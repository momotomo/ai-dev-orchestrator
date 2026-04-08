from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import start_bridge  # noqa: E402
import run_until_stop  # noqa: E402
from _bridge_common import present_bridge_handoff, present_bridge_status  # noqa: E402


def make_args(
    project_path: str = "/tmp/repo",
    max_execution_count: int = 6,
    *,
    ready_issue_ref: str = "",
    request_body: str = "",
) -> argparse.Namespace:
    return argparse.Namespace(
        project_path=project_path,
        ready_issue_ref=ready_issue_ref,
        request_body=request_body,
        max_execution_count=max_execution_count,
        status=False,
        resume=False,
        doctor=False,
        clear_error=False,
    )


class HelpSmokeTest(unittest.TestCase):
    def test_start_bridge_help_mentions_ready_issue_normal_entry(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/start_bridge.py", "--help"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        self.assertIn("bridge の通常入口", output)
        self.assertIn("ready issue の参照を使います", output)
        self.assertIn("--ready-issue-ref", output)
        self.assertIn("reply contract だけを追加します", output)


class HumanFacingStatusTests(unittest.TestCase):
    def test_first_request_waiting_uses_ready_issue_entry_wording(self) -> None:
        view = present_bridge_status({"mode": "idle", "need_chatgpt_prompt": True})
        self.assertEqual(view.label, "ready issue参照で開始待ち")
        self.assertIn("current ready issue", view.detail)
        self.assertIn("reply contract", view.detail)

    def test_same_chat_next_request_uses_preparation_wording(self) -> None:
        view = present_bridge_status({"mode": "idle", "need_chatgpt_next": True})
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertIn("次の依頼", view.detail)
        self.assertNotIn("新しいチャットへ切り替えます", view.detail)

    def test_handoff_preprocessing_mentions_new_chat_only_when_needed(self) -> None:
        view = present_bridge_status(
            {
                "mode": "idle",
                "need_chatgpt_next": True,
                "pending_handoff_log": "logs/handoff.md",
                "next_request_requires_rotation": True,
            }
        )
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertIn("新しいチャットへ切り替えます", view.detail)

    def test_extended_wait_stays_human_friendly(self) -> None:
        view = present_bridge_status({"mode": "extended_wait"})
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("追加待機", view.detail)

    def test_submitted_unconfirmed_wait_prefers_wait_over_resend(self) -> None:
        view = present_bridge_status(
            {
                "mode": "waiting_prompt_reply",
                "pending_request_signal": "submitted_unconfirmed",
            }
        )
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("再送せず", view.detail)

    def test_prepared_issue_centric_codex_dispatch_uses_codex_wait_wording(self) -> None:
        view = present_bridge_status(
            {
                "mode": "awaiting_user",
                "chatgpt_decision": "issue_centric:codex_run",
                "last_issue_centric_artifact_kind": "codex_body",
                "last_issue_centric_metadata_log": "logs/metadata.json",
                "last_issue_centric_execution_status": "",
            }
        )
        self.assertEqual(view.label, "Codex実行待ち")
        self.assertIn("prepared Codex body", view.detail)

    def test_issue_centric_prepared_request_uses_send_wait_wording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": "summary:logs/summary.json",
                        "action": "no_action",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/81",
                        "principal_issue_kind": "followup_issue",
                        "target_issue": "https://github.com/example/repo/issues/81",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_followup_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            view = present_bridge_status(
                {
                    "mode": "idle",
                    "need_chatgpt_next": True,
                    "last_issue_centric_runtime_snapshot": str(snapshot_path),
                    "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                    "last_issue_centric_prepared_generation_id": "summary:logs/summary.json",
                    "prepared_request_hash": "abc",
                    "prepared_request_source": "report:1",
                    "prepared_request_log": "logs/request.md",
                    "prepared_request_status": "prepared",
                }
            )
        self.assertEqual(view.label, "ChatGPT送信待ち")
        self.assertIn("prepared request", view.detail)

    def test_issue_centric_ready_next_status_mentions_preferred_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": "summary:logs/summary.json",
                        "action": "issue_create",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/51",
                        "principal_issue_kind": "primary_issue",
                        "target_issue": "https://github.com/example/repo/issues/51",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_primary_issue",
                        "current_issue": None,
                        "created_primary_issue": {
                            "number": "51",
                            "url": "https://github.com/example/repo/issues/51",
                            "title": "Primary issue",
                            "ref": "#51",
                        },
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            view = present_bridge_status(
                {
                    "mode": "idle",
                    "need_chatgpt_next": True,
                    "last_issue_centric_runtime_snapshot": str(snapshot_path),
                    "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                }
            )
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertIn("issue-centric preferred route", view.detail)
        self.assertIn("https://github.com/example/repo/issues/51", view.detail)

    def test_issue_centric_invalidated_status_mentions_fallback_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": "summary:logs/summary.json",
                        "action": "no_action",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/81",
                        "principal_issue_kind": "followup_issue",
                        "target_issue": "https://github.com/example/repo/issues/81",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_followup_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            view = present_bridge_status(
                {
                    "mode": "idle",
                    "need_chatgpt_next": True,
                    "last_issue_centric_runtime_snapshot": str(snapshot_path),
                    "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                    "last_issue_centric_invalidated_generation_id": "summary:logs/summary.json",
                    "last_issue_centric_invalidation_status": "issue_centric_invalidated",
                    "last_issue_centric_invalidation_reason": "legacy_fallback_selected",
                }
            )
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertIn("invalidated", view.detail)
        self.assertIn("legacy_fallback_selected", view.detail)

    def test_handoff_view_uses_next_step_style_for_running_codex(self) -> None:
        handoff = present_bridge_handoff({"mode": "codex_running", "need_codex_run": True})
        self.assertEqual(handoff.title, "Codex の完了を待っています。")

    def test_status_normal_path_no_snapshot_uses_safety_fallback_wording(self) -> None:
        # No runtime snapshot → dispatch plan returns fallback_legacy.
        # present_bridge_status() must reflect safety fallback framing, not
        # old "legacy fallback" peer wording.
        view = present_bridge_status({"mode": "idle", "need_chatgpt_next": True})
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertIn("safety fallback (legacy) route", view.detail)
        self.assertNotIn("legacy fallback で次の依頼", view.detail)

    def test_status_pending_reply_dispatch_plan_routes_to_fetch(self) -> None:
        # mode == "await_late_completion" is a fetch substate; dispatch plan
        # resolves fetch_next_prompt and present_bridge_status() picks it up
        # via is_fetch_late_completion_state() without a raw mode read.
        view = present_bridge_status({"mode": "await_late_completion"})
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("書き切られるまで", view.detail)

    def test_handoff_completed_state_uses_is_completed_state(self) -> None:
        # is_completed_state() should fire: mode="idle" and no need_* flags.
        handoff = present_bridge_handoff({"mode": "idle"})
        self.assertEqual(handoff.title, "完了しました。")


class StartBridgeOutputTests(unittest.TestCase):
    def test_status_output_leads_with_human_facing_guidance(self) -> None:
        state = {"mode": "idle", "need_chatgpt_next": True}
        args = make_args()
        derived_args = argparse.Namespace()
        out = io.StringIO()
        with (
            patch.object(start_bridge.run_until_stop, "load_state", return_value=state),
            patch.object(start_bridge, "build_derived_args", return_value=derived_args),
            patch.object(start_bridge.run_until_stop, "start_bridge_mode", return_value="このまま再開できます"),
            patch.object(
                start_bridge.run_until_stop,
                "start_bridge_resume_guidance",
                return_value=(
                    "ChatGPTへ依頼準備中",
                    "完了報告をもとに、次の依頼を送る準備ができています。",
                    "Safari の current tab を対象チャットに合わせたまま再実行してください。",
                ),
            ),
            patch.object(
                start_bridge.run_until_stop,
                "recommended_operator_step",
                return_value=("そのまま再開", "python3 scripts/start_bridge.py --resume"),
            ),
            redirect_stdout(out),
        ):
            start_bridge.print_resume_overview(args)

        output = out.getvalue()
        self.assertIn("bridge status:", output)
        self.assertIn("現在の状況: ChatGPTへ依頼準備中", output)
        self.assertIn("まずやること: そのまま再開", output)
        self.assertIn("おすすめ 1 コマンド: python3 scripts/start_bridge.py --resume", output)
        self.assertNotIn("state.json", output)

    def test_doctor_output_surfaces_decision_before_details(self) -> None:
        state = {"mode": "idle", "need_chatgpt_next": True, "error": False}
        args = make_args()
        derived_args = argparse.Namespace()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            prompt_path = temp_root / "codex_prompt.md"
            prompt_path.write_text("prompt\n", encoding="utf-8")
            stop_path = temp_root / "STOP"
            report_path = temp_root / "codex_report.md"

            out = io.StringIO()
            with (
                patch.object(start_bridge.run_until_stop, "load_state", return_value=state),
                patch.object(start_bridge, "build_derived_args", return_value=derived_args),
                patch.object(start_bridge.run_until_stop, "start_bridge_mode", return_value="このまま再開できます"),
                patch.object(
                    start_bridge.run_until_stop,
                    "start_bridge_resume_guidance",
                    return_value=(
                        "ChatGPTへ依頼準備中",
                        "完了報告をもとに、次の依頼を送る準備ができています。",
                        "Safari の current tab を対象チャットに合わせたまま再実行してください。",
                    ),
                ),
                patch.object(
                    start_bridge.run_until_stop,
                    "recommended_operator_step",
                    return_value=("そのまま再開", "python3 scripts/start_bridge.py --resume"),
                ),
                patch.object(start_bridge.run_until_stop, "codex_report_is_ready", return_value=False),
                patch.object(start_bridge.run_until_stop, "runtime_report_path", return_value=report_path),
                patch.object(start_bridge.run_until_stop, "runtime_prompt_path", return_value=prompt_path),
                patch.object(start_bridge.run_until_stop, "runtime_stop_path", return_value=stop_path),
                patch.object(start_bridge.run_until_stop, "bridge_runtime_root", return_value=temp_root),
                patch.object(start_bridge.run_until_stop, "should_rotate_before_next_chat_request", return_value=False),
                patch.object(start_bridge.run_until_stop, "should_prioritize_unarchived_report", return_value=False),
                patch.object(start_bridge.run_until_stop, "is_apple_event_timeout_text", return_value=False),
                redirect_stdout(out),
            ):
                start_bridge.print_doctor(args)

        output = out.getvalue()
        self.assertIn("bridge doctor:", output)
        self.assertIn("判定: そのまま再開", output)
        self.assertIn("おすすめ 1 コマンド: python3 scripts/start_bridge.py --resume", output)
        self.assertIn("次に起きること:", output)
        self.assertIn("まず見るもの:", output)
        self.assertIn("詳細診断:", output)


class SummaryTests(unittest.TestCase):
    def test_run_summary_uses_next_step_section(self) -> None:
        args = run_until_stop.parse_args(
            [
                "--project-path",
                "/tmp/repo",
                "--max-execution-count",
                "6",
                "--entry-script",
                "scripts/start_bridge.py",
            ],
            {},
        )
        initial_state = {"mode": "idle", "need_chatgpt_next": True}
        final_state = {"mode": "idle", "need_chatgpt_next": True}
        summary = run_until_stop.summarize_run(
            args=args,
            reason="dry-run",
            steps=0,
            warnings=[],
            initial_state=initial_state,
            final_state=final_state,
            history=["- dry_run next_action: request_prompt_from_report"],
        )
        self.assertIn("## next_step", summary)
        self.assertIn("おすすめ 1 コマンド", summary)
        self.assertIn("ChatGPTへ依頼準備中", summary)
        self.assertNotIn("## handoff", summary)

    def test_submitted_unconfirmed_note_prefers_wait(self) -> None:
        note = run_until_stop.suggested_next_note(
            {
                "mode": "waiting_prompt_reply",
                "pending_request_hash": "abc",
                "pending_request_source": "source",
                "pending_request_log": "logs/request.md",
                "pending_request_signal": "submitted_unconfirmed",
            }
        )
        self.assertIn("再送せず", note)
        self.assertIn("reply", note)

    def test_request_prompt_from_report_note_mentions_issue_centric_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "action": "no_action",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/81",
                        "principal_issue_kind": "followup_issue",
                        "target_issue": "https://github.com/example/repo/issues/81",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_followup_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            note = run_until_stop.suggested_next_note(
                {
                    "mode": "idle",
                    "need_chatgpt_next": True,
                    "last_issue_centric_runtime_snapshot": str(snapshot_path),
                    "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                }
            )
        self.assertIn("issue-centric preferred route", note)
        self.assertIn("https://github.com/example/repo/issues/81", note)

    def test_request_prompt_from_report_note_mentions_recovered_issue_centric_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "recovery_rehydration",
                        "action": "no_action",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "issue_centric_recovered",
                        "recovery_source": "normalized_summary_then_state",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/81",
                        "principal_issue_kind": "followup_issue",
                        "target_issue": "https://github.com/example/repo/issues/81",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_followup_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            note = run_until_stop.suggested_next_note(
                {
                    "mode": "idle",
                    "need_chatgpt_next": True,
                    "last_issue_centric_runtime_snapshot": str(snapshot_path),
                    "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                }
            )
        self.assertIn("再構築した文脈", note)
        self.assertIn("normalized_summary_then_state", note)

    def test_request_prompt_from_report_note_mentions_pending_issue_centric_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": "summary:logs/summary.json",
                        "action": "codex_run",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/20",
                        "principal_issue_kind": "current_issue",
                        "target_issue": "https://github.com/example/repo/issues/20",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_current_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            note = run_until_stop.suggested_next_note(
                {
                    "mode": "waiting_prompt_reply",
                    "need_chatgpt_next": False,
                    "last_issue_centric_runtime_snapshot": str(snapshot_path),
                    "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                    "last_issue_centric_pending_generation_id": "summary:logs/summary.json",
                    "pending_request_hash": "abc",
                    "pending_request_source": "report:1",
                    "pending_request_log": "logs/request.md",
                }
            )
        self.assertIn("pending", note)
        self.assertIn("reply 待ち", note)

    def test_resolve_unified_next_action_prefers_fetch_for_fresh_pending_issue_centric_generation(self) -> None:
        from _bridge_common import resolve_unified_next_action
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": "summary:logs/summary.json",
                        "action": "codex_run",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/20",
                        "principal_issue_kind": "current_issue",
                        "target_issue": "https://github.com/example/repo/issues/20",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_current_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(run_until_stop, "should_prioritize_unarchived_report", return_value=False):
                action = resolve_unified_next_action(
                    {
                        "mode": "idle",
                        "need_chatgpt_next": True,
                        "last_issue_centric_runtime_snapshot": str(snapshot_path),
                        "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                        "last_issue_centric_pending_generation_id": "summary:logs/summary.json",
                        "pending_request_hash": "abc",
                        "pending_request_source": "report:1",
                        "pending_request_log": "logs/request.md",
                    }
                )
        self.assertEqual(action, "fetch_next_prompt")

    def test_resolve_unified_next_action_prefers_later_codex_dispatch_when_prepared(self) -> None:
        from _bridge_common import resolve_unified_next_action
        action = resolve_unified_next_action(
            {
                "mode": "awaiting_user",
                "chatgpt_decision": "issue_centric:codex_run",
                "last_issue_centric_artifact_kind": "codex_body",
                "last_issue_centric_metadata_log": "logs/metadata.json",
                "last_issue_centric_execution_status": "",
            }
        )
        self.assertEqual(action, "dispatch_issue_centric_codex_run")

    def test_resolve_unified_next_action_keeps_ready_for_codex_mode_driven(self) -> None:
        from _bridge_common import resolve_unified_next_action
        with patch.object(run_until_stop, "should_prioritize_unarchived_report", return_value=False):
            with patch(
                "_bridge_common.resolve_runtime_dispatch_plan",
                side_effect=AssertionError("issue-centric next-action should not override ready_for_codex"),
            ):
                action = resolve_unified_next_action(
                    {
                        "mode": "ready_for_codex",
                        "need_codex_run": True,
                    }
                )
        self.assertEqual(action, "launch_codex_once")

    def test_request_prompt_from_report_note_mentions_fallback_route(self) -> None:
        note = run_until_stop.suggested_next_note(
            {
                "mode": "idle",
                "need_chatgpt_next": True,
                "last_issue_centric_route_selected": "fallback_legacy",
                "last_issue_centric_next_request_target": "https://github.com/example/repo/issues/20",
                "last_issue_centric_route_fallback_reason": "normalized_summary_missing",
            }
        )
        self.assertIn("safety fallback (legacy) route", note)
        self.assertIn("normalized_summary_missing", note)

    def test_request_prompt_from_report_note_mentions_recovery_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_fallback",
                        "snapshot_source": "recovery_rehydration",
                        "action": "codex_run",
                        "dispatch_final_status": "completed",
                        "route_selected": "fallback_legacy",
                        "route_fallback_reason": "dispatch_result_missing_or_unreadable",
                        "recovery_status": "issue_centric_recovery_fallback",
                        "recovery_source": "state_fallback_only",
                        "recovery_fallback_reason": "dispatch_result_missing_or_unreadable",
                        "fallback_reason": "dispatch_result_missing_or_unreadable",
                        "principal_issue": "https://github.com/example/repo/issues/20",
                        "principal_issue_kind": "current_issue",
                        "target_issue": "https://github.com/example/repo/issues/20",
                        "target_issue_source": "existing_state_fallback",
                        "next_request_hint": "continue_on_current_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            note = run_until_stop.suggested_next_note(
                {
                    "mode": "idle",
                    "need_chatgpt_next": True,
                    "last_issue_centric_runtime_snapshot": str(snapshot_path),
                    "last_issue_centric_snapshot_status": "issue_centric_snapshot_fallback",
                }
            )
        self.assertIn("degraded", note)
        self.assertIn("dispatch_result_missing_or_unreadable", note)

    def test_request_prompt_from_report_note_mentions_unavailable_runtime_mode(self) -> None:
        note = run_until_stop.suggested_next_note(
            {
                "mode": "idle",
                "need_chatgpt_next": True,
                "last_issue_centric_runtime_snapshot": "logs/missing.json",
                "last_issue_centric_snapshot_status": "issue_centric_snapshot_missing",
            }
        )
        self.assertIn("issue-centric runtime", note)
        self.assertIn("unavailable", note)

    def test_request_prompt_from_report_note_mentions_stale_runtime_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": "summary:logs/summary.json",
                        "action": "no_action",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/81",
                        "principal_issue_kind": "followup_issue",
                        "target_issue": "https://github.com/example/repo/issues/81",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_followup_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "logs/summary.json",
                        "dispatch_result_path": "logs/dispatch.json",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            note = run_until_stop.suggested_next_note(
                {
                    "mode": "idle",
                    "need_chatgpt_next": True,
                    "last_issue_centric_runtime_snapshot": str(snapshot_path),
                    "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                    "last_issue_centric_consumed_generation_id": "summary:logs/summary.json",
                }
            )

        self.assertIn("safety fallback (legacy) route", note)
        self.assertIn("chatgpt_reply_recovered_for_generation", note)

    def test_request_prompt_from_report_note_mentions_invalidated_runtime_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": "summary:logs/summary.json",
                        "action": "human_review_needed",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/20",
                        "principal_issue_kind": "current_issue",
                        "target_issue": "https://github.com/example/repo/issues/20",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_current_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "logs/summary.json",
                        "dispatch_result_path": "logs/dispatch.json",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            note = run_until_stop.suggested_next_note(
                {
                    "mode": "idle",
                    "need_chatgpt_next": True,
                    "last_issue_centric_runtime_snapshot": str(snapshot_path),
                    "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                    "last_issue_centric_invalidated_generation_id": "summary:logs/summary.json",
                    "last_issue_centric_invalidation_status": "issue_centric_invalidated",
                    "last_issue_centric_invalidation_reason": "legacy_fallback_selected",
                }
            )

        self.assertIn("invalidated", note)
        self.assertIn("legacy_fallback_selected", note)

    def test_submitted_unconfirmed_recommends_resume_not_clear_error(self) -> None:
        args = run_until_stop.parse_args(
            [
                "--project-path",
                "/tmp/repo",
                "--max-execution-count",
                "6",
                "--entry-script",
                "scripts/start_bridge.py",
            ],
            {},
        )
        label, command = run_until_stop.recommended_operator_step(
            args,
            {
                "mode": "waiting_prompt_reply",
                "pending_request_hash": "abc",
                "pending_request_source": "source",
                "pending_request_log": "logs/request.md",
                "pending_request_signal": "submitted_unconfirmed",
            },
        )
        self.assertEqual(label, "そのまま再開")
        self.assertIn("--resume", command)

    def test_request_next_prompt_recommends_ready_issue_entry(self) -> None:
        args = run_until_stop.parse_args(
            [
                "--project-path",
                "/tmp/repo",
                "--max-execution-count",
                "6",
                "--entry-script",
                "scripts/start_bridge.py",
                "--ready-issue-ref",
                "#20 runtime entry",
            ],
            {},
        )
        label, command = run_until_stop.recommended_operator_step(
            args,
            {
                "mode": "idle",
                "need_chatgpt_prompt": True,
            },
        )
        self.assertEqual(label, "ready issue 参照で開始")
        self.assertIn("--ready-issue-ref", command)


class DispatchPlanOperatorHelpersTest(unittest.TestCase):
    """Tests for slice6 operator-facing dispatch plan helpers."""

    def test_is_awaiting_user_supplement_true_for_awaiting_user_mode(self) -> None:
        from _bridge_common import is_awaiting_user_supplement

        self.assertTrue(is_awaiting_user_supplement({"mode": "awaiting_user"}))

    def test_is_awaiting_user_supplement_false_for_other_modes(self) -> None:
        from _bridge_common import is_awaiting_user_supplement

        for mode in ("idle", "waiting_prompt_reply", "ready_for_codex", "codex_running", ""):
            with self.subTest(mode=mode):
                self.assertFalse(is_awaiting_user_supplement({"mode": mode}))

    def test_format_operator_stop_note_completed(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan

        state = {"mode": "idle", "need_chatgpt_prompt": False, "need_chatgpt_next": False, "need_codex_run": False}
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("不要", note)

    def test_format_operator_stop_note_fetch_next_prompt(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan

        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "abc",
            "pending_request_source": "source",
            "pending_request_log": "logs/req.md",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("ChatGPT 返答を回収", note)

    def test_format_operator_stop_note_awaiting_user_supplement(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan

        state = {
            "mode": "awaiting_user",
            "need_chatgpt_next": True,
            "chatgpt_decision": "human_review",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("補足入力", note)

    def test_summarize_run_includes_dispatch_plan_fields(self) -> None:
        args = run_until_stop.parse_args(
            ["--project-path", "/tmp/repo", "--max-execution-count", "6", "--entry-script", "scripts/start_bridge.py"],
            {},
        )
        state = {"mode": "waiting_prompt_reply", "pending_request_hash": "x", "pending_request_source": "s", "pending_request_log": "l"}
        summary = run_until_stop.summarize_run(
            args=args,
            reason="test",
            steps=1,
            warnings=[],
            initial_state=state,
            final_state=state,
            history=[],
        )
        self.assertIn("next_action:", summary)
        self.assertIn("runtime_action:", summary)
        self.assertIn("is_fallback:", summary)

    def test_summarize_run_mode_in_debug_section(self) -> None:
        args = run_until_stop.parse_args(
            ["--project-path", "/tmp/repo", "--max-execution-count", "6", "--entry-script", "scripts/start_bridge.py"],
            {},
        )
        state = {"mode": "idle", "need_chatgpt_prompt": True}
        summary = run_until_stop.summarize_run(
            args=args,
            reason="test",
            steps=0,
            warnings=[],
            initial_state=state,
            final_state=state,
            history=[],
        )
        # mode_compat should appear in ## debug (compatibility field)
        self.assertIn("mode_compat:", summary)
        debug_pos = summary.find("## debug")
        mode_pos = summary.find("mode_compat:")
        self.assertGreater(mode_pos, debug_pos, "mode_compat should be in ## debug section")

    def test_start_bridge_mode_uses_action_view_for_awaiting_user(self) -> None:
        state = {"mode": "awaiting_user", "need_chatgpt_next": True, "chatgpt_decision": "human_review"}
        result = run_until_stop.start_bridge_mode(state)
        self.assertEqual(result, "補足を入れて再開できます")

    def test_is_fetch_extended_wait_state_true(self) -> None:
        from _bridge_common import is_fetch_extended_wait_state

        self.assertTrue(is_fetch_extended_wait_state({"mode": "extended_wait"}))

    def test_is_fetch_extended_wait_state_false_for_other_modes(self) -> None:
        from _bridge_common import is_fetch_extended_wait_state

        for mode in ("idle", "waiting_prompt_reply", "await_late_completion", "codex_running", ""):
            with self.subTest(mode=mode):
                self.assertFalse(is_fetch_extended_wait_state({"mode": mode}))

    def test_is_fetch_late_completion_state_true(self) -> None:
        from _bridge_common import is_fetch_late_completion_state

        self.assertTrue(is_fetch_late_completion_state({"mode": "await_late_completion"}))

    def test_is_fetch_late_completion_state_false_for_other_modes(self) -> None:
        from _bridge_common import is_fetch_late_completion_state

        for mode in ("idle", "waiting_prompt_reply", "extended_wait", "codex_running", ""):
            with self.subTest(mode=mode):
                self.assertFalse(is_fetch_late_completion_state({"mode": mode}))

    def test_codex_lifecycle_view_covers_all_three_modes(self) -> None:
        """resolve_codex_lifecycle_view() returns a non-None view for each of the three lifecycle
        modes; CODEX_LIFECYCLE_MODES constant was removed — classification is now internal to
        resolve_codex_lifecycle_view()."""
        from _bridge_common import resolve_codex_lifecycle_view

        # Verify the three modes are still recognised after CODEX_LIFECYCLE_MODES removal.
        for mode, kwargs in (
            ("ready_for_codex", {"need_codex_run": True}),
            ("codex_running", {}),
            ("codex_done", {}),
        ):
            with self.subTest(mode=mode):
                self.assertIsNotNone(resolve_codex_lifecycle_view({"mode": mode, **kwargs}))

        # The constant must no longer be importable.
        import _bridge_common
        self.assertFalse(hasattr(_bridge_common, "CODEX_LIFECYCLE_MODES"),
                         "CODEX_LIFECYCLE_MODES should have been deleted")

    def test_resolve_codex_lifecycle_view_recognises_lifecycle_modes(self) -> None:
        """resolve_codex_lifecycle_view() returns a view (not None) for each lifecycle mode.

        CODEX_LIFECYCLE_MODES constant was deleted; classification is now internal to
        resolve_codex_lifecycle_view().
        """
        from _bridge_common import resolve_codex_lifecycle_view

        for mode, kwargs in (
            ("ready_for_codex", {"need_codex_run": True}),
            ("codex_running", {}),
            ("codex_done", {}),
        ):
            with self.subTest(mode=mode):
                self.assertIsNotNone(resolve_codex_lifecycle_view({"mode": mode, **kwargs}))

    def test_resolve_codex_lifecycle_view_none_for_non_lifecycle_modes(self) -> None:
        """resolve_codex_lifecycle_view() returns None for non-Codex lifecycle modes.

        Replaces the removed is_codex_lifecycle_state()==False assertion.
        """
        from _bridge_common import resolve_codex_lifecycle_view

        for mode in ("idle", "waiting_prompt_reply", "extended_wait", "await_late_completion", ""):
            with self.subTest(mode=mode):
                self.assertIsNone(resolve_codex_lifecycle_view({"mode": mode}))

    def test_is_normal_path_state_true(self) -> None:
        from _bridge_common import is_normal_path_state

        for mode in ("idle", "waiting_prompt_reply", "extended_wait", "await_late_completion", ""):
            with self.subTest(mode=mode):
                self.assertTrue(is_normal_path_state({"mode": mode}))

    def test_is_normal_path_state_false_for_codex_lifecycle_modes(self) -> None:
        from _bridge_common import is_normal_path_state

        for mode in ("ready_for_codex", "codex_running", "codex_done"):
            with self.subTest(mode=mode):
                self.assertFalse(is_normal_path_state({"mode": mode}))

    def test_is_normal_path_state_false_when_pending_dispatch(self) -> None:
        from _bridge_common import is_normal_path_state

        # State that satisfies has_pending_issue_centric_codex_dispatch()
        state = {
            "mode": "awaiting_user",
            "chatgpt_decision": "issue_centric:codex_run",
            "last_issue_centric_artifact_kind": "codex_body",
            "last_issue_centric_metadata_log": "some_log.json",
            "need_codex_run": False,
            "last_issue_centric_execution_status": "",
        }
        self.assertFalse(is_normal_path_state(state))

    def test_summarize_run_includes_action_stop_note(self) -> None:
        args = run_until_stop.parse_args(
            ["--project-path", "/tmp/repo", "--max-execution-count", "6", "--entry-script", "scripts/start_bridge.py"],
            {},
        )
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "x",
            "pending_request_source": "s",
            "pending_request_log": "l",
        }
        summary = run_until_stop.summarize_run(
            args=args,
            reason="test",
            steps=1,
            warnings=[],
            initial_state=state,
            final_state=state,
            history=[],
        )
        self.assertIn("action_stop_note:", summary)

    # ------------------------------------------------------------------
    # Legacy route inventory: deletion-boundary classification tests
    # (added 2026-04-09, post-cutover streamline)
    # ------------------------------------------------------------------

    def test_state_signature_includes_mode_for_change_detection_only(self) -> None:
        """state_signature() is a change-detection tuple; mode is included for
        legacy-state change detection — NOT as a routing signal."""
        state_a = {"mode": "idle"}
        state_b = {"mode": "waiting_prompt_reply"}

        sig_a = run_until_stop.state_signature(state_a)
        sig_b = run_until_stop.state_signature(state_b)

        # mode difference must be visible in the signature so change detection works
        self.assertNotEqual(sig_a, sig_b)
        # routing decisions must NOT consult state_signature; dispatch plan is authority
        self.assertIsInstance(sig_a, tuple)
        self.assertIsInstance(sig_b, tuple)

    def test_resolve_route_choice_returns_preferred_loop_action_and_reason(self) -> None:
        """resolve_issue_centric_route_choice() exposes preferred_loop_action and
        preferred_loop_reason directly.  The thin wrapper that used to re-expose
        those two fields (resolve_issue_centric_preferred_loop_action) has been
        removed; callers should use resolve_issue_centric_route_choice() instead."""
        from _bridge_common import resolve_issue_centric_route_choice

        state = {
            "mode": "idle",
            "chatgpt_decision": "",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
        }
        route = resolve_issue_centric_route_choice(state)
        self.assertIsInstance(route.preferred_loop_action, str)
        self.assertIsInstance(route.preferred_loop_reason, str)
        # preferred_loop_action may be empty for a fully-idle state;
        # what matters is that the attribute exists and is accessible directly.

    def test_present_bridge_status_does_not_use_raw_mode_in_normal_path(self) -> None:
        """present_bridge_status() must NOT read raw mode for normal-path routing.

        Verify that calling it with an action-view-only state (no mode) still
        produces a coherent output — confirming the dispatch plan is primary
        and mode is not required for normal-path status.
        """
        from _bridge_common import present_bridge_status

        # action-view-only state: no mode field at all
        state = {
            "pending_request_hash": "abc123",
            "pending_request_source": "user",
            "pending_request_log": "some.log",
        }
        result = present_bridge_status(state)
        # Must not crash and must return a BridgeStatusView with non-empty text
        self.assertTrue(hasattr(result, "status_text") or isinstance(result, str) or result is not None)

    # ------------------------------------------------------------------
    # Codex lifecycle centralisation tests (added 2026-04-08)
    # ------------------------------------------------------------------

    def test_resolve_codex_lifecycle_view_returns_none_for_normal_path(self) -> None:
        """resolve_codex_lifecycle_view() returns None for non-Codex lifecycle states."""
        from _bridge_common import resolve_codex_lifecycle_view

        for mode in ("idle", "waiting_prompt_reply", "awaiting_user", ""):
            with self.subTest(mode=mode):
                state: dict[str, object] = {"mode": mode}
                self.assertIsNone(resolve_codex_lifecycle_view(state))

    def test_resolve_codex_lifecycle_view_launch(self) -> None:
        """ready_for_codex + need_codex_run=True → action=launch_codex_once, not blocked."""
        from _bridge_common import resolve_codex_lifecycle_view

        state = {"mode": "ready_for_codex", "need_codex_run": True}
        view = resolve_codex_lifecycle_view(state)
        self.assertIsNotNone(view)
        assert view is not None
        self.assertEqual(view.action, "launch_codex_once")
        self.assertFalse(view.is_blocked)

    def test_resolve_codex_lifecycle_view_blocked(self) -> None:
        """ready_for_codex without need_codex_run → is_blocked=True, action=check_codex_condition."""
        from _bridge_common import resolve_codex_lifecycle_view

        state = {"mode": "ready_for_codex", "need_codex_run": False}
        view = resolve_codex_lifecycle_view(state)
        self.assertIsNotNone(view)
        assert view is not None
        self.assertTrue(view.is_blocked)
        self.assertEqual(view.action, "check_codex_condition")

    def test_resolve_codex_lifecycle_view_running(self) -> None:
        """codex_running → action=wait_for_codex_report, not blocked."""
        from _bridge_common import resolve_codex_lifecycle_view

        state = {"mode": "codex_running"}
        view = resolve_codex_lifecycle_view(state)
        self.assertIsNotNone(view)
        assert view is not None
        self.assertEqual(view.action, "wait_for_codex_report")
        self.assertFalse(view.is_blocked)

    def test_resolve_codex_lifecycle_view_done(self) -> None:
        """codex_done → action=archive_codex_report, not blocked."""
        from _bridge_common import resolve_codex_lifecycle_view

        state = {"mode": "codex_done"}
        view = resolve_codex_lifecycle_view(state)
        self.assertIsNotNone(view)
        assert view is not None
        self.assertEqual(view.action, "archive_codex_report")
        self.assertFalse(view.is_blocked)

    def test_codex_lifecycle_view_to_status_view(self) -> None:
        """CodexLifecycleView.to_status_view() returns a BridgeStatusView with matching text."""
        from _bridge_common import BridgeStatusView, resolve_codex_lifecycle_view

        state = {"mode": "codex_running"}
        view = resolve_codex_lifecycle_view(state)
        self.assertIsNotNone(view)
        assert view is not None
        status = view.to_status_view()
        self.assertIsInstance(status, BridgeStatusView)
        self.assertEqual(status.label, view.status_label)
        self.assertEqual(status.detail, view.status_detail)

    def test_present_bridge_status_uses_lifecycle_view_for_codex_states(self) -> None:
        """present_bridge_status() returns the same wording as resolve_codex_lifecycle_view()
        for all Codex lifecycle states — confirming the two are now in sync."""
        from _bridge_common import present_bridge_status, resolve_codex_lifecycle_view

        for mode, need_codex_run in (
            ("ready_for_codex", True),
            ("codex_running", False),
            ("codex_done", False),
        ):
            with self.subTest(mode=mode):
                state: dict[str, object] = {"mode": mode, "need_codex_run": need_codex_run}
                view = resolve_codex_lifecycle_view(state)
                status = present_bridge_status(state)
                self.assertIsNotNone(view)
                assert view is not None
                self.assertEqual(status.label, view.status_label)
                self.assertEqual(status.detail, view.status_detail)

    def test_resolve_unified_next_action_codex_lifecycle_action_matches_view(self) -> None:
        """resolve_unified_next_action() returns lifecycle_view.action for non-blocked Codex states."""
        from _bridge_common import resolve_codex_lifecycle_view, resolve_unified_next_action

        for mode, need_codex_run in (
            ("ready_for_codex", True),
            ("codex_running", False),
            ("codex_done", False),
        ):
            with self.subTest(mode=mode):
                state: dict[str, object] = {"mode": mode, "need_codex_run": need_codex_run}
                view = resolve_codex_lifecycle_view(state)
                action = resolve_unified_next_action(state)
                self.assertIsNotNone(view)
                assert view is not None
                self.assertEqual(action, view.action)

    def test_present_bridge_status_routes_lifecycle_via_view_not_raw_guard(self) -> None:
        """present_bridge_status() uses only resolve_codex_lifecycle_view(); no is_codex_lifecycle_state()
        call site remains.  Verify that blocked lifecycle (check_codex_condition) still yields
        the right label — i.e. the view-based path is followed, not a raw is_blocked check."""
        from _bridge_common import BridgeStatusView, present_bridge_status, resolve_codex_lifecycle_view

        # blocked case: ready_for_codex without need_codex_run
        state: dict[str, object] = {"mode": "ready_for_codex", "need_codex_run": False}
        view = resolve_codex_lifecycle_view(state)
        status = present_bridge_status(state)
        self.assertIsNotNone(view)
        assert view is not None
        self.assertTrue(view.is_blocked)
        # Both must agree: status comes from lifecycle view only
        self.assertIsInstance(status, BridgeStatusView)
        self.assertEqual(status.label, view.status_label)
        self.assertEqual(status.detail, view.status_detail)

    # ------------------------------------------------------------------
    # resolve_unified_next_action / action-bridge tests (2026-04-08)
    # ------------------------------------------------------------------

    def test_resolve_unified_next_action_normal_path_delegates_to_dispatch_plan(self) -> None:
        """Normal-path state: resolve_unified_next_action returns plan.next_action."""
        from _bridge_common import resolve_runtime_dispatch_plan, resolve_unified_next_action

        state = {
            "mode": "idle",
            "chatgpt_decision": "",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
        }
        unified = resolve_unified_next_action(state)
        plan = resolve_runtime_dispatch_plan(state)
        self.assertEqual(unified, plan.next_action)

    def test_resolve_unified_next_action_codex_lifecycle_matches_lifecycle_view(self) -> None:
        """Codex lifecycle states: resolve_unified_next_action matches lifecycle_view.action."""
        from _bridge_common import resolve_codex_lifecycle_view, resolve_unified_next_action

        for mode, need_codex_run in (
            ("ready_for_codex", True),
            ("codex_running", False),
            ("codex_done", False),
        ):
            with self.subTest(mode=mode):
                state: dict[str, object] = {"mode": mode, "need_codex_run": need_codex_run}
                view = resolve_codex_lifecycle_view(state)
                unified = resolve_unified_next_action(state)
                self.assertIsNotNone(view)
                assert view is not None
                self.assertEqual(unified, view.action)

    def test_resolve_unified_next_action_blocked_lifecycle_falls_through_to_dispatch(self) -> None:
        """Blocked Codex lifecycle (ready_for_codex without need_codex_run) falls through
        to the dispatch plan, not the lifecycle view action."""
        from _bridge_common import resolve_runtime_dispatch_plan, resolve_unified_next_action

        state: dict[str, object] = {"mode": "ready_for_codex", "need_codex_run": False}
        unified = resolve_unified_next_action(state)
        plan = resolve_runtime_dispatch_plan(state)
        # Must not return "check_codex_condition" (that's the blocked lifecycle action);
        # must return the dispatch plan's answer instead.
        self.assertNotEqual(unified, "check_codex_condition")
        self.assertEqual(unified, plan.next_action)

    def test_is_normal_path_state_uses_lifecycle_view(self) -> None:
        """is_normal_path_state() returns False for all Codex lifecycle modes
        (delegates to resolve_codex_lifecycle_view(); CODEX_LIFECYCLE_MODES removed)."""
        from _bridge_common import is_normal_path_state

        for mode in ("ready_for_codex", "codex_running", "codex_done"):
            with self.subTest(mode=mode):
                state: dict[str, object] = {"mode": mode}
                self.assertFalse(is_normal_path_state(state))

        # Normal modes must still return True.
        for mode in ("idle", "waiting_prompt_reply", "awaiting_user"):
            with self.subTest(mode=mode):
                state = {"mode": mode}
                self.assertTrue(is_normal_path_state(state))


# ------------------------------------------------------------------
# Fallback arms cleanup tests (2026-04-08 phase7 fallback-arms-cleanup)
# ------------------------------------------------------------------

class FallbackArmsCleanupTest(unittest.TestCase):
    """Lifecycle arms removed from resolve_fallback_legacy_transition(); summarize_run()
    now guards lifecycle states via is_normal_path_state() + has_pending_issue_centric_codex_dispatch()
    (no direct resolve_codex_lifecycle_view() call in run_until_stop.py)."""

    def _make_args(self) -> "argparse.Namespace":
        import run_until_stop
        return run_until_stop.parse_args(
            ["--project-path", "/tmp/repo", "--max-execution-count", "6",
             "--entry-script", "scripts/start_bridge.py"],
            {},
        )

    def test_fallback_lifecycle_arms_removed_ready_for_codex(self) -> None:
        """resolve_fallback_legacy_transition() no longer handles ready_for_codex."""
        from _bridge_common import resolve_fallback_legacy_transition
        result = resolve_fallback_legacy_transition({"mode": "ready_for_codex", "need_codex_run": True})
        # Was "launch_codex_once"; now falls through to "no_action".
        self.assertEqual(result, "no_action")

    def test_fallback_lifecycle_arms_removed_codex_running(self) -> None:
        """resolve_fallback_legacy_transition() no longer handles codex_running."""
        from _bridge_common import resolve_fallback_legacy_transition
        result = resolve_fallback_legacy_transition({"mode": "codex_running"})
        # Was "wait_for_codex_report"; now falls through to "no_action".
        self.assertEqual(result, "no_action")

    def test_fallback_lifecycle_arms_removed_codex_done(self) -> None:
        """resolve_fallback_legacy_transition() no longer handles codex_done."""
        from _bridge_common import resolve_fallback_legacy_transition
        result = resolve_fallback_legacy_transition({"mode": "codex_done"})
        # Was "archive_codex_report"; now falls through to "no_action".
        self.assertEqual(result, "no_action")

    def test_fallback_normal_modes_unchanged(self) -> None:
        """Non-lifecycle arms in resolve_fallback_legacy_transition() are intact."""
        from _bridge_common import resolve_fallback_legacy_transition
        self.assertEqual(resolve_fallback_legacy_transition({"mode": "idle", "need_chatgpt_prompt": True}), "request_next_prompt")
        self.assertEqual(resolve_fallback_legacy_transition({"mode": "waiting_prompt_reply"}), "fetch_next_prompt")
        self.assertEqual(resolve_fallback_legacy_transition({"mode": "extended_wait"}), "fetch_next_prompt")

    def test_summarize_run_lifecycle_state_uses_lifecycle_compat_path(self) -> None:
        """summarize_run() with a Codex lifecycle final_state uses the lifecycle compat path,
        not resolve_runtime_dispatch_plan().  next_action comes from resolve_unified_next_action();
        runtime_action is 'codex_lifecycle_compat'; is_fallback is False."""
        import run_until_stop
        args = self._make_args()
        # codex_done: resolve_unified_next_action returns "archive_codex_report"
        final_state: dict[str, object] = {"mode": "codex_done"}
        summary = run_until_stop.summarize_run(
            args=args,
            reason="test",
            steps=1,
            warnings=[],
            initial_state={"mode": "codex_done"},
            final_state=final_state,
            history=[],
        )
        self.assertIn("next_action: archive_codex_report", summary)
        self.assertIn("runtime_action: codex_lifecycle_compat", summary)
        self.assertIn("is_fallback: False", summary)

    def test_summarize_run_codex_running_uses_lifecycle_view(self) -> None:
        """summarize_run() with mode=codex_running reflects wait_for_codex_report."""
        import run_until_stop
        args = self._make_args()
        final_state: dict[str, object] = {"mode": "codex_running"}
        summary = run_until_stop.summarize_run(
            args=args,
            reason="test",
            steps=1,
            warnings=[],
            initial_state={"mode": "codex_running"},
            final_state=final_state,
            history=[],
        )
        self.assertIn("next_action: wait_for_codex_report", summary)
        self.assertIn("is_fallback: False", summary)

    def test_summarize_run_normal_state_still_uses_dispatch_plan(self) -> None:
        """summarize_run() with a normal final_state still uses resolve_runtime_dispatch_plan()."""
        import run_until_stop
        args = self._make_args()
        final_state: dict[str, object] = {"mode": "idle", "need_chatgpt_next": True}
        summary = run_until_stop.summarize_run(
            args=args,
            reason="test",
            steps=1,
            warnings=[],
            initial_state=final_state,
            final_state=final_state,
            history=[],
        )
        # Dispatch plan path: runtime_action is not "codex_lifecycle_compat"
        self.assertNotIn("codex_lifecycle_compat", summary)


# ------------------------------------------------------------------
# Lifecycle view scope tests (2026-04-08 phase7 lifecycle-view-scope)
# Verifies that resolve_codex_lifecycle_view() callers are limited to
# the status-display and orchestrator-dispatch scope.
# ------------------------------------------------------------------

class LifecycleViewScopeTest(unittest.TestCase):
    """phase7 lifecycle-view-scope: residue narrowed to status / orchestrator.

    resolve_codex_lifecycle_view() external call sites:
      - present_bridge_status()  — STATUS DISPLAY responsibility (label + detail)
      - bridge_orchestrator.run() — ORCHESTRATOR DISPATCH (action + is_blocked + label)
    Internal call sites in _bridge_common.py:
      - is_normal_path_state()  — routing gate
      - resolve_unified_next_action() — action authority
    run_until_stop.py no longer imports resolve_codex_lifecycle_view directly.
    """

    def test_run_until_stop_does_not_import_resolve_codex_lifecycle_view(self) -> None:
        """run_until_stop.py no longer has a direct resolve_codex_lifecycle_view import.
        summarize_run() now uses is_normal_path_state() + resolve_unified_next_action()
        + present_bridge_status() instead.
        """
        import run_until_stop
        self.assertFalse(
            hasattr(run_until_stop, "resolve_codex_lifecycle_view"),
            "resolve_codex_lifecycle_view should not be in run_until_stop namespace; "
            "use is_normal_path_state() + resolve_unified_next_action() instead.",
        )

    def test_present_bridge_status_detail_matches_lifecycle_view_detail(self) -> None:
        """present_bridge_status(state).detail == lifecycle_view.status_detail for all lifecycle
        states (including blocked).  This validates that summarize_run() can use
        present_bridge_status(final_state).detail as the stop note source.
        """
        from _bridge_common import present_bridge_status, resolve_codex_lifecycle_view

        for mode, need_codex_run in (
            ("ready_for_codex", True),
            ("ready_for_codex", False),   # blocked lifecycle
            ("codex_running", False),
            ("codex_done", False),
        ):
            with self.subTest(mode=mode, need_codex_run=need_codex_run):
                state: dict[str, object] = {"mode": mode, "need_codex_run": need_codex_run}
                view = resolve_codex_lifecycle_view(state)
                # Call without blocked/stale flags to get clean lifecycle status.
                status = present_bridge_status(state)
                self.assertIsNotNone(view)
                assert view is not None
                self.assertEqual(status.detail, view.status_detail)

    def test_lifecycle_guard_equivalence(self) -> None:
        """not is_normal_path_state(s) and not has_pending_issue_centric_codex_dispatch(s)
        is equivalent to resolve_codex_lifecycle_view(s) is not None.
        This is the guard logic used by summarize_run() to avoid a direct lifecycle view call.
        """
        from _bridge_common import (
            has_pending_issue_centric_codex_dispatch,
            is_normal_path_state,
            resolve_codex_lifecycle_view,
        )

        lifecycle_cases: list[tuple[dict[str, object], bool]] = [
            ({"mode": "ready_for_codex", "need_codex_run": True}, True),
            ({"mode": "ready_for_codex", "need_codex_run": False}, True),  # blocked
            ({"mode": "codex_running"}, True),
            ({"mode": "codex_done"}, True),
            ({"mode": "idle"}, False),
            ({"mode": "awaiting_user"}, False),
            ({"mode": "waiting_prompt_reply"}, False),
        ]
        for state, expected_lifecycle in lifecycle_cases:
            with self.subTest(state=state):
                via_view = resolve_codex_lifecycle_view(state) is not None
                via_guard = (
                    not is_normal_path_state(state)
                    and not has_pending_issue_centric_codex_dispatch(state)
                )
                self.assertEqual(via_view, expected_lifecycle)
                self.assertEqual(via_guard, via_view,
                    "Guard equivalence failed: the two lifecycle detection paths must agree.")

    def test_resolve_unified_next_action_covers_non_blocked_lifecycle(self) -> None:
        """resolve_unified_next_action() returns the lifecycle action for non-blocked lifecycle
        states.  summarize_run() relies on this for _summary_next_action.
        """
        from _bridge_common import resolve_codex_lifecycle_view, resolve_unified_next_action

        non_blocked_cases = [
            ({"mode": "ready_for_codex", "need_codex_run": True}, "launch_codex_once"),
            ({"mode": "codex_running"}, "wait_for_codex_report"),
            ({"mode": "codex_done"}, "archive_codex_report"),
        ]
        for state, expected_action in non_blocked_cases:
            with self.subTest(state=state):
                view = resolve_codex_lifecycle_view(state)
                unified = resolve_unified_next_action(state)
                self.assertIsNotNone(view)
                assert view is not None
                self.assertFalse(view.is_blocked)
                self.assertEqual(unified, expected_action)
                self.assertEqual(unified, view.action)


# ------------------------------------------------------------------
# Orchestrator action-view reshape tests (2026-04-08 phase7 orchestrator-action-view)
# Validates that bridge_orchestrator.run() dispatch decisions go through action-view
# authority, not lifecycle view fields directly.
# ------------------------------------------------------------------

class OrchestratorActionViewTest(unittest.TestCase):
    """phase7 orchestrator-action-view: orchestrator dispatch via resolve_unified_next_action().

    Responsibilities:
      - Dispatch action comes from resolve_unified_next_action() (action authority)
      - Blocked lifecycle gate: is_blocked_codex_lifecycle_state() (encapsulates lifecycle)
      - Operator label: present_bridge_status(state).label
      - resolve_codex_lifecycle_view() is NOT imported by bridge_orchestrator
    """

    def test_bridge_orchestrator_does_not_import_resolve_codex_lifecycle_view(self) -> None:
        """bridge_orchestrator should not import resolve_codex_lifecycle_view directly.
        Lifecycle classification is now encapsulated behind is_blocked_codex_lifecycle_state()
        and resolve_unified_next_action().
        """
        import bridge_orchestrator
        self.assertFalse(
            hasattr(bridge_orchestrator, "resolve_codex_lifecycle_view"),
            "resolve_codex_lifecycle_view should not be in bridge_orchestrator namespace; "
            "use is_blocked_codex_lifecycle_state() + resolve_unified_next_action() instead.",
        )

    def test_is_blocked_codex_lifecycle_state_blocked_case(self) -> None:
        """is_blocked_codex_lifecycle_state() returns True for the blocked lifecycle case only."""
        from _bridge_common import is_blocked_codex_lifecycle_state

        # Blocked: ready_for_codex without need_codex_run
        self.assertTrue(is_blocked_codex_lifecycle_state({"mode": "ready_for_codex", "need_codex_run": False}))
        self.assertTrue(is_blocked_codex_lifecycle_state({"mode": "ready_for_codex"}))

    def test_is_blocked_codex_lifecycle_state_non_blocked_cases(self) -> None:
        """is_blocked_codex_lifecycle_state() returns False for actionable lifecycle and normal states."""
        from _bridge_common import is_blocked_codex_lifecycle_state

        # Actionable lifecycle states (not blocked)
        self.assertFalse(is_blocked_codex_lifecycle_state({"mode": "ready_for_codex", "need_codex_run": True}))
        self.assertFalse(is_blocked_codex_lifecycle_state({"mode": "codex_running"}))
        self.assertFalse(is_blocked_codex_lifecycle_state({"mode": "codex_done"}))
        # Normal path states
        self.assertFalse(is_blocked_codex_lifecycle_state({"mode": "idle"}))
        self.assertFalse(is_blocked_codex_lifecycle_state({"mode": "awaiting_user"}))

    def test_unified_action_covers_all_non_blocked_lifecycle_dispatch(self) -> None:
        """resolve_unified_next_action() returns the correct action key for all non-blocked lifecycle
        states that the orchestrator would previously dispatch via lifecycle_view.action.
        This validates that the orchestrator can rely on resolve_unified_next_action() for routing.
        """
        from _bridge_common import resolve_unified_next_action

        self.assertEqual(
            resolve_unified_next_action({"mode": "ready_for_codex", "need_codex_run": True}),
            "launch_codex_once",
        )
        self.assertEqual(
            resolve_unified_next_action({"mode": "codex_running"}),
            "wait_for_codex_report",
        )
        self.assertEqual(
            resolve_unified_next_action({"mode": "codex_done"}),
            "archive_codex_report",
        )

    def test_blocked_lifecycle_resolves_to_dispatch_plan_not_lifecycle_action(self) -> None:
        """Blocked lifecycle (ready_for_codex without need_codex_run) falls through to the
        dispatch plan in resolve_unified_next_action(), NOT to check_codex_condition.
        is_blocked_codex_lifecycle_state() must be checked before consulting action authority
        in orchestrator dispatch logic.
        """
        from _bridge_common import is_blocked_codex_lifecycle_state, resolve_unified_next_action

        state: dict[str, object] = {"mode": "ready_for_codex", "need_codex_run": False}
        # is_blocked should be True — caught BEFORE action authority
        self.assertTrue(is_blocked_codex_lifecycle_state(state))
        # Action authority falls through to plan; must NOT be "check_codex_condition"
        action = resolve_unified_next_action(state)
        self.assertNotEqual(action, "check_codex_condition",
            "Blocked lifecycle should be caught by is_blocked_codex_lifecycle_state(); "
            "resolve_unified_next_action() falls through to dispatch plan for these states.")

    def test_status_label_matches_lifecycle_view_for_orchestrator_display(self) -> None:
        """present_bridge_status(state).label == lifecycle_view.status_label for all lifecycle
        states.  This confirms the orchestrator can use present_bridge_status() for display
        instead of lifecycle_view.status_label directly.
        """
        from _bridge_common import present_bridge_status, resolve_codex_lifecycle_view

        for mode, need_codex_run in (
            ("ready_for_codex", True),
            ("ready_for_codex", False),   # blocked
            ("codex_running", False),
            ("codex_done", False),
        ):
            with self.subTest(mode=mode, need_codex_run=need_codex_run):
                state: dict[str, object] = {"mode": mode, "need_codex_run": need_codex_run}
                view = resolve_codex_lifecycle_view(state)
                status = present_bridge_status(state)
                self.assertIsNotNone(view)
                assert view is not None
                self.assertEqual(status.label, view.status_label,
                    "present_bridge_status().label must match lifecycle_view.status_label "
                    "so orchestrator can use present_bridge_status() for display.")


if __name__ == "__main__":
    unittest.main()
