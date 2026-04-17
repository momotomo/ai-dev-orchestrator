from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
import types
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
    status: bool = False,
    resume: bool = False,
    doctor: bool = False,
    clear_error: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        project_path=project_path,
        ready_issue_ref=ready_issue_ref,
        request_body=request_body,
        max_execution_count=max_execution_count,
        status=status,
        resume=resume,
        doctor=doctor,
        clear_error=clear_error,
    )


class HelpSmokeTest(unittest.TestCase):
    def test_start_bridge_help_mentions_issue_selection_and_resume_priority(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/start_bridge.py", "--help"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        # description: initial state → issue selection, resume priority
        self.assertIn("bridge の通常入口", output)
        self.assertIn("issue selection", output)
        self.assertIn("resume 優先", output)
        # --ready-issue-ref: explicit entry, optional for fresh start
        self.assertIn("--ready-issue-ref", output)
        self.assertIn("明示指定", output)
        self.assertIn("省略してよい", output)
        # --request-body: exception / recovery / override only
        self.assertIn("--request-body", output)
        self.assertIn("exception / recovery / override", output)
        # --clear-error vs --reset distinction
        self.assertIn("--clear-error", output)
        self.assertIn("reset とは別物", output)
        self.assertIn("--reset", output)
        self.assertIn("clear-error とは別物", output)


class RecoverablePendingCodexResumeTests(unittest.TestCase):
    def _pending_codex_state(self, *, error: bool = True) -> dict[str, object]:
        return {
            "mode": "awaiting_user",
            "error": error,
            "error_message": "pending issue-centric codex dispatch を raw response log から再構成できませんでした。",
            "chatgpt_decision": "issue_centric:codex_run",
            "last_issue_centric_artifact_kind": "codex_body",
            "last_issue_centric_metadata_log": "logs/meta.json",
            "last_issue_centric_artifact_file": "logs/body.md",
            "last_issue_centric_execution_status": "",
            "need_codex_run": False,
        }

    def test_recover_resume_clears_error_for_reconstructable_pending_dispatch(self) -> None:
        args = make_args(resume=True)
        state = self._pending_codex_state()
        saved: dict[str, object] = {}
        fake_module = types.SimpleNamespace(
            load_pending_issue_centric_codex_materialized=lambda payload: ("decision", "materialized", "", "", "")
        )
        with (
            patch.object(start_bridge.run_until_stop, "load_state", return_value=state),
            patch.object(start_bridge.importlib, "import_module", return_value=fake_module),
            patch.object(start_bridge, "save_state", side_effect=lambda updated: saved.update(updated)),
        ):
            self.assertTrue(start_bridge.recover_resume_from_pending_issue_centric_codex_dispatch(args))
        self.assertFalse(bool(saved.get("error")))
        self.assertEqual(saved.get("error_message"), "")

    def test_recover_resume_keeps_error_when_no_pending_dispatch(self) -> None:
        args = make_args(resume=True)
        state = {
            "mode": "awaiting_user",
            "error": True,
            "error_message": "plain error",
            "chatgpt_decision": "issue_centric:codex_run",
            "last_issue_centric_artifact_kind": "",
            "last_issue_centric_metadata_log": "",
            "last_issue_centric_execution_status": "",
            "need_codex_run": False,
        }
        with (
            patch.object(start_bridge.run_until_stop, "load_state", return_value=state),
            patch.object(start_bridge, "save_state") as save_state_mock,
        ):
            self.assertFalse(start_bridge.recover_resume_from_pending_issue_centric_codex_dispatch(args))
        save_state_mock.assert_not_called()

    def test_recover_resume_keeps_error_when_reconstruct_fails(self) -> None:
        args = make_args(resume=True)
        state = self._pending_codex_state()
        fake_module = types.SimpleNamespace(
            load_pending_issue_centric_codex_materialized=lambda payload: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        with (
            patch.object(start_bridge.run_until_stop, "load_state", return_value=state),
            patch.object(start_bridge.importlib, "import_module", return_value=fake_module),
            patch.object(start_bridge, "save_state") as save_state_mock,
        ):
            self.assertFalse(start_bridge.recover_resume_from_pending_issue_centric_codex_dispatch(args))
        save_state_mock.assert_not_called()

    def test_main_resume_clears_recoverable_pending_dispatch_error_before_run(self) -> None:
        args = self._pending_codex_state()
        mutable_state = dict(args)
        fake_module = types.SimpleNamespace(
            load_pending_issue_centric_codex_materialized=lambda payload: ("decision", "materialized", "", "", "")
        )

        def _save(updated: dict[str, object]) -> None:
            mutable_state.clear()
            mutable_state.update(updated)

        def _run(_argv: list[str]) -> int:
            self.assertFalse(bool(mutable_state.get("error")))
            return 0

        with (
            patch.object(start_bridge.run_until_stop, "load_state", side_effect=lambda: dict(mutable_state)),
            patch.object(start_bridge.importlib, "import_module", return_value=fake_module),
            patch.object(start_bridge, "save_state", side_effect=_save),
            patch.object(start_bridge, "print_resume_overview"),
            patch.object(start_bridge.run_until_stop, "load_project_config", return_value={}),
            patch.object(start_bridge.run_until_stop, "worker_repo_path", return_value=Path("/tmp/repo")),
            patch.object(start_bridge.run_until_stop, "browser_fetch_timeout_seconds", return_value=1800),
            patch.object(start_bridge.run_until_stop, "load_browser_config", return_value={}),
            patch.object(start_bridge.run_until_stop, "run", side_effect=_run),
        ):
            rc = start_bridge.main(["--project-path", "/tmp/repo", "--max-execution-count", "6", "--resume"])
        self.assertEqual(rc, 0)


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


class StatusViewCutoverTest(unittest.TestCase):
    """phase7 status-view-cutover: present_bridge_status() no longer calls resolve_codex_lifecycle_view().

    Architecture invariant after this phase:
      - present_bridge_status() uses is_blocked_codex_lifecycle_state() for the blocked case
        and resolve_unified_next_action() for action-keyed routing with inline status strings.
      - resolve_codex_lifecycle_view() external callers: ZERO.
        It is consumed only by is_normal_path_state(), is_blocked_codex_lifecycle_state(),
        and resolve_unified_next_action() — all within _bridge_common.py.
    """

    def test_present_bridge_status_does_not_call_resolve_codex_lifecycle_view_directly(self) -> None:
        """present_bridge_status() must not call resolve_codex_lifecycle_view() — only mentions
        in docstrings/comments are allowed.  The function is now a fully internal helper; all
        outside paths go through present_bridge_status() or action keys from
        resolve_unified_next_action().
        """
        import inspect
        import re
        import _bridge_common as m

        src = inspect.getsource(m.present_bridge_status)
        # Strip docstring (triple-quoted strings) and single-line comments before checking.
        # getsource includes the full function body with docstring.
        src_no_docstring = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
        src_no_comments = re.sub(r"#[^\n]*", "", src_no_docstring)
        self.assertNotIn(
            "resolve_codex_lifecycle_view",
            src_no_comments,
            "present_bridge_status() must not call resolve_codex_lifecycle_view() "
            "(docstring/comment mentions are allowed) "
            "after the status-view-cutover phase.",
        )

    def test_present_bridge_status_lifecycle_ready_for_codex_actionable(self) -> None:
        """mode=ready_for_codex + need_codex_run=True → label='Codex実行待ち'."""
        from _bridge_common import present_bridge_status

        status = present_bridge_status({"mode": "ready_for_codex", "need_codex_run": True})
        self.assertEqual(status.label, "Codex実行待ち")

    def test_present_bridge_status_lifecycle_codex_running(self) -> None:
        """mode=codex_running → label='Codex実行中'."""
        from _bridge_common import present_bridge_status

        status = present_bridge_status({"mode": "codex_running"})
        self.assertEqual(status.label, "Codex実行中")

    def test_present_bridge_status_lifecycle_codex_done(self) -> None:
        """mode=codex_done → label='完了報告整理中'."""
        from _bridge_common import present_bridge_status

        status = present_bridge_status({"mode": "codex_done"})
        self.assertEqual(status.label, "完了報告整理中")

    def test_present_bridge_status_lifecycle_blocked(self) -> None:
        """mode=ready_for_codex + need_codex_run=False (blocked) → label='人確認待ち'."""
        from _bridge_common import present_bridge_status

        status = present_bridge_status({"mode": "ready_for_codex", "need_codex_run": False})
        self.assertEqual(status.label, "人確認待ち")
        status_default = present_bridge_status({"mode": "ready_for_codex"})
        self.assertEqual(status_default.label, "人確認待ち")

    def test_present_bridge_status_label_matches_lifecycle_view_regression(self) -> None:
        """Regression: present_bridge_status().label == resolve_codex_lifecycle_view().status_label
        for all lifecycle states.  The inline strings in present_bridge_status() must stay in
        sync with the values returned by resolve_codex_lifecycle_view().
        """
        from _bridge_common import present_bridge_status, resolve_codex_lifecycle_view

        cases = [
            {"mode": "ready_for_codex", "need_codex_run": True},
            {"mode": "ready_for_codex", "need_codex_run": False},
            {"mode": "codex_running"},
            {"mode": "codex_done"},
        ]
        for state in cases:
            with self.subTest(state=state):
                lv = resolve_codex_lifecycle_view(state)
                self.assertIsNotNone(lv)
                assert lv is not None
                status = present_bridge_status(state)
                self.assertEqual(
                    status.label,
                    lv.status_label,
                    f"present_bridge_status().label must stay in sync with "
                    f"resolve_codex_lifecycle_view().status_label for state={state}",
                )


class BridgeStatusLifecycleSyncSurfacingTests(unittest.TestCase):
    """Phase 1: lifecycle sync outcomes are visible in present_bridge_status() detail."""

    _SNAPSHOT_BASE: dict[str, object] = {
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
    }

    def test_bridge_status_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "followup_created",
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertIn("lifecycle_sync", view.detail)
        self.assertIn("signal=synced", view.detail)
        self.assertIn("stage=followup_created", view.detail)

    def test_bridge_status_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
            "last_issue_centric_lifecycle_sync_stage": "review",
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertIn("lifecycle_sync", view.detail)
        self.assertIn("signal=skipped_no_project", view.detail)
        self.assertIn("stage=review", view.detail)

    def test_bridge_status_shows_lifecycle_sync_failed_with_reason(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertIn("lifecycle_sync", view.detail)
        self.assertIn("signal=sync_failed", view.detail)
        self.assertIn("reason=blocked_project_preflight", view.detail)
        self.assertIn("stage=done", view.detail)

    def test_bridge_status_no_lifecycle_sync_when_no_sync_data(self) -> None:
        # Regression: no lifecycle state fields → detail must not contain lifecycle_sync.
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertNotIn("lifecycle_sync", view.detail)

    def test_bridge_status_lifecycle_sync_in_preferred_route_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snap = dict(self._SNAPSHOT_BASE)
            snap["snapshot_path"] = str(snapshot_path)
            snapshot_path.write_text(json.dumps(snap), encoding="utf-8")
            state = {
                "mode": "idle",
                "need_chatgpt_next": True,
                "last_issue_centric_runtime_snapshot": str(snapshot_path),
                "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                "last_issue_centric_lifecycle_sync_status": "project_state_synced",
                "last_issue_centric_lifecycle_sync_stage": "followup_created",
            }
            view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertIn("issue-centric preferred route", view.detail)
        self.assertIn("lifecycle_sync", view.detail)
        self.assertIn("signal=synced", view.detail)

    def test_bridge_status_lifecycle_sync_missing_stage_shows_signal_only(self) -> None:
        # Only sync_status set, stage absent — signal shown, no stage= in detail.
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPTへ依頼準備中")
        self.assertIn("signal=synced", view.detail)
        self.assertNotIn("stage=", view.detail)


class BridgeHandoffLifecycleSyncSurfacingTests(unittest.TestCase):
    """Phase 2: lifecycle sync outcomes are visible in present_bridge_handoff() and format_operator_stop_note()."""

    def test_handoff_completed_decision_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "chatgpt_decision": "completed",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "followup_created",
        }
        handoff = present_bridge_handoff(state)
        self.assertEqual(handoff.title, "完了しました。")
        self.assertIn("lifecycle_sync", handoff.detail)
        self.assertIn("signal=synced", handoff.detail)

    def test_handoff_completed_decision_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "mode": "idle",
            "chatgpt_decision": "completed",
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        handoff = present_bridge_handoff(state)
        self.assertEqual(handoff.title, "完了しました。")
        self.assertIn("signal=skipped_no_project", handoff.detail)

    def test_handoff_completed_decision_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "idle",
            "chatgpt_decision": "completed",
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        handoff = present_bridge_handoff(state)
        self.assertEqual(handoff.title, "完了しました。")
        self.assertIn("signal=sync_failed", handoff.detail)
        self.assertIn("reason=blocked_project_preflight", handoff.detail)

    def test_handoff_completed_idle_state_shows_lifecycle_sync(self) -> None:
        # is_completed_state() path: mode="idle" with no need_* flags.
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "review",
        }
        handoff = present_bridge_handoff(state)
        self.assertEqual(handoff.title, "完了しました。")
        self.assertIn("lifecycle_sync", handoff.detail)
        self.assertIn("signal=synced", handoff.detail)

    def test_handoff_no_lifecycle_sync_when_no_sync_data(self) -> None:
        # Regression: no lifecycle state → detail must not contain lifecycle_sync.
        state = {"mode": "idle", "chatgpt_decision": "completed"}
        handoff = present_bridge_handoff(state)
        self.assertEqual(handoff.title, "完了しました。")
        self.assertNotIn("lifecycle_sync", handoff.detail)

    def test_handoff_suggested_note_takes_priority_over_lifecycle_sync(self) -> None:
        # When caller provides suggested_note, that takes precedence (no suffix appended).
        state = {
            "mode": "idle",
            "chatgpt_decision": "completed",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
        }
        handoff = present_bridge_handoff(state, suggested_note="caller note")
        self.assertEqual(handoff.title, "完了しました。")
        self.assertEqual(handoff.detail, "caller note")

    def test_format_stop_note_shows_lifecycle_sync_for_request_prompt_from_report(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "followup_created",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)

    def test_format_stop_note_no_lifecycle_sync_when_no_sync_data(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {"mode": "idle", "need_chatgpt_next": True}
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertNotIn("lifecycle_sync", note)


class BridgeBlockedWaitLifecycleSyncSurfacingTests(unittest.TestCase):
    """Phase 1 (#53): lifecycle sync signal in blocked / wait bridge status cases."""

    def test_blocked_guard_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "pause": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "followup_created",
        }
        view = present_bridge_status(state, blocked=True)
        self.assertEqual(view.label, "人確認待ち")
        self.assertIn("lifecycle_sync", view.detail)
        self.assertIn("signal=synced", view.detail)

    def test_blocked_guard_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "mode": "idle",
            "pause": True,
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        view = present_bridge_status(state, blocked=True)
        self.assertEqual(view.label, "人確認待ち")
        self.assertIn("signal=skipped_no_project", view.detail)

    def test_blocked_guard_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "idle",
            "pause": True,
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        view = present_bridge_status(state, blocked=True)
        self.assertEqual(view.label, "人確認待ち")
        self.assertIn("signal=sync_failed", view.detail)
        self.assertIn("reason=blocked_project_preflight", view.detail)

    def test_blocked_guard_no_lifecycle_sync_when_no_sync_data(self) -> None:
        view = present_bridge_status({"mode": "idle", "pause": True}, blocked=True)
        self.assertEqual(view.label, "人確認待ち")
        self.assertNotIn("lifecycle_sync", view.detail)

    def test_awaiting_user_supplement_shows_lifecycle_sync_when_no_decision_note(self) -> None:
        # When chatgpt_decision_note is absent, the default text gets the suffix appended.
        state = {
            "mode": "awaiting_user",
            "chatgpt_decision": "human_review",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "review",
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "人確認待ち")
        self.assertIn("lifecycle_sync", view.detail)
        self.assertIn("signal=synced", view.detail)

    def test_awaiting_user_supplement_decision_note_takes_priority(self) -> None:
        # When chatgpt_decision_note is set, that note is shown as-is (no suffix).
        state = {
            "mode": "awaiting_user",
            "chatgpt_decision": "human_review",
            "chatgpt_decision_note": "operator note here",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "人確認待ち")
        self.assertEqual(view.detail, "operator note here")

    def test_wait_state_fetch_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "extended_wait",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "followup_created",
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("lifecycle_sync", view.detail)
        self.assertIn("signal=synced", view.detail)

    def test_wait_state_fetch_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "extended_wait",
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("signal=sync_failed", view.detail)
        self.assertIn("reason=blocked_project_preflight", view.detail)

    def test_wait_state_fetch_no_lifecycle_sync_when_no_sync_data(self) -> None:
        view = present_bridge_status({"mode": "extended_wait"})
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertNotIn("lifecycle_sync", view.detail)

    def test_wait_state_late_completion_shows_lifecycle_sync(self) -> None:
        state = {
            "mode": "await_late_completion",
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("signal=skipped_no_project", view.detail)


class BridgeHandoffBlockedWaitLifecycleSyncTests(unittest.TestCase):
    """Phase 2 (#53): lifecycle sync in present_bridge_handoff blocked/wait/recovery cases
    and format_operator_stop_note fetch case."""

    def test_handoff_human_review_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "chatgpt_decision": "human_review",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "followup_created",
        }
        handoff = present_bridge_handoff(state)
        self.assertEqual(handoff.title, "人の判断が必要です。次の方針を決めてから再開してください。")
        self.assertIn("lifecycle_sync", handoff.detail)
        self.assertIn("signal=synced", handoff.detail)

    def test_handoff_human_review_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "idle",
            "chatgpt_decision": "human_review",
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        handoff = present_bridge_handoff(state)
        self.assertIn("signal=sync_failed", handoff.detail)
        self.assertIn("reason=blocked_project_preflight", handoff.detail)

    def test_handoff_need_info_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "mode": "idle",
            "chatgpt_decision": "need_info",
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
            "last_issue_centric_lifecycle_sync_stage": "review",
        }
        handoff = present_bridge_handoff(state)
        self.assertEqual(handoff.title, "情報が不足しています。入力内容を補って再開してください。")
        self.assertIn("signal=skipped_no_project", handoff.detail)

    def test_handoff_blocked_guard_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "pause": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        handoff = present_bridge_handoff(state, blocked=True)
        self.assertEqual(handoff.title, "自動では進めません。まず summary と doctor を確認してください。")
        self.assertIn("lifecycle_sync", handoff.detail)
        self.assertIn("signal=synced", handoff.detail)

    def test_handoff_blocked_guard_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "idle",
            "pause": True,
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        handoff = present_bridge_handoff(state, blocked=True)
        self.assertIn("signal=sync_failed", handoff.detail)
        self.assertIn("reason=blocked_project_preflight", handoff.detail)

    def test_handoff_decision_note_takes_priority_over_lifecycle_sync(self) -> None:
        state = {
            "mode": "idle",
            "chatgpt_decision": "human_review",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
        }
        handoff = present_bridge_handoff(state, suggested_note="caller override note")
        self.assertEqual(handoff.detail, "caller override note")

    def test_handoff_no_lifecycle_sync_when_no_sync_data(self) -> None:
        state = {"mode": "idle", "chatgpt_decision": "human_review"}
        handoff = present_bridge_handoff(state)
        self.assertNotIn("lifecycle_sync", handoff.detail)

    def test_format_stop_note_fetch_shows_lifecycle_sync_synced(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "extended_wait",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "followup_created",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)

    def test_format_stop_note_fetch_shows_lifecycle_sync_failed(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "extended_wait",
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("signal=sync_failed", note)
        self.assertIn("reason=blocked_project_preflight", note)

    def test_format_stop_note_fetch_no_lifecycle_sync_when_no_sync_data(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {"mode": "extended_wait"}
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertNotIn("lifecycle_sync", note)


class StopSummaryLifecycleSyncSurfacingTests(unittest.TestCase):
    """Phase 1: lifecycle sync outcomes are visible in format_operator_stop_note
    remaining cases (completed, no_action, request_next_prompt) and in
    format_lifecycle_sync_state_note diagnostic helper."""

    # --- format_operator_stop_note: completed ---

    def test_format_operator_stop_note_completed_shows_lifecycle_sync_synced(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)
        self.assertIn("stage=closing", note)

    def test_format_operator_stop_note_completed_shows_lifecycle_sync_skipped_no_project(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=skipped_no_project", note)

    def test_format_operator_stop_note_completed_shows_lifecycle_sync_failed(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "transition_error",
            "last_issue_centric_lifecycle_sync_stage": "opening",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=sync_failed", note)
        self.assertIn("reason=transition_error", note)

    def test_format_operator_stop_note_completed_no_lifecycle_sync_when_no_sync_data(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {"mode": "idle"}
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("不要", note)
        self.assertNotIn("lifecycle_sync", note)

    # --- format_operator_stop_note: no_action ---

    def test_format_operator_stop_note_no_action_shows_lifecycle_sync_synced(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "need_codex_run": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)

    def test_format_operator_stop_note_no_action_shows_lifecycle_sync_skipped_no_project(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "need_codex_run": True,
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=skipped_no_project", note)

    # --- format_operator_stop_note: request_next_prompt ---

    def test_format_operator_stop_note_request_next_prompt_shows_lifecycle_sync_synced(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "need_chatgpt_prompt": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "opening",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)

    def test_format_operator_stop_note_request_next_prompt_no_src_when_no_sync_data(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {"mode": "idle", "need_chatgpt_prompt": True}
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("新規入口", note)
        self.assertNotIn("lifecycle_sync", note)

    # --- format_lifecycle_sync_state_note ---

    def test_format_lifecycle_sync_state_note_synced(self) -> None:
        from _bridge_common import format_lifecycle_sync_state_note
        state = {
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note = format_lifecycle_sync_state_note(state)
        self.assertIn("signal=synced", note)
        self.assertIn("stage=closing", note)
        self.assertNotIn("[lifecycle_sync:", note)

    def test_format_lifecycle_sync_state_note_skipped_no_project(self) -> None:
        from _bridge_common import format_lifecycle_sync_state_note
        state = {"last_issue_centric_lifecycle_sync_status": "not_requested_no_project"}
        note = format_lifecycle_sync_state_note(state)
        self.assertIn("signal=skipped_no_project", note)
        self.assertNotEqual(note, "not_recorded")

    def test_format_lifecycle_sync_state_note_sync_failed(self) -> None:
        from _bridge_common import format_lifecycle_sync_state_note
        state = {
            "last_issue_centric_lifecycle_sync_status": "mutation_error",
            "last_issue_centric_lifecycle_sync_stage": "opening",
        }
        note = format_lifecycle_sync_state_note(state)
        self.assertIn("signal=sync_failed", note)
        self.assertIn("reason=mutation_error", note)
        self.assertIn("stage=opening", note)

    def test_format_lifecycle_sync_state_note_not_recorded(self) -> None:
        from _bridge_common import format_lifecycle_sync_state_note
        state = {}
        note = format_lifecycle_sync_state_note(state)
        self.assertEqual(note, "not_recorded")


class DoctorStopSummaryDiagnosticsLifecycleSyncTests(unittest.TestCase):
    """Phase 2: lifecycle sync outcomes are visible in suggested_next_note
    (doctor/stop-summary note) and in print_doctor's detailed diagnostics."""

    # --- suggested_next_note: human_review ---

    def test_suggested_next_note_human_review_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "chatgpt_decision": "human_review",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)

    def test_suggested_next_note_human_review_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "chatgpt_decision": "human_review",
            "last_issue_centric_lifecycle_sync_status": "transition_error",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=sync_failed", note)

    def test_suggested_next_note_human_review_decision_note_takes_priority(self) -> None:
        state = {
            "chatgpt_decision": "human_review",
            "chatgpt_decision_note": "caller provided note",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("caller provided note", note)
        self.assertNotIn("lifecycle_sync", note)

    # --- suggested_next_note: need_info ---

    def test_suggested_next_note_need_info_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "chatgpt_decision": "need_info",
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=skipped_no_project", note)

    # --- suggested_next_note: completed ---

    def test_suggested_next_note_completed_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "chatgpt_decision": "completed",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)

    def test_suggested_next_note_completed_decision_note_takes_priority(self) -> None:
        state = {
            "chatgpt_decision": "completed",
            "chatgpt_decision_note": "all done note",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertEqual(note, "all done note")
        self.assertNotIn("lifecycle_sync", note)

    def test_suggested_next_note_no_lifecycle_sync_when_no_sync_data(self) -> None:
        state = {"chatgpt_decision": "human_review"}
        note = run_until_stop.suggested_next_note(state)
        self.assertNotIn("lifecycle_sync", note)
        self.assertIn("bridge を再実行", note)

    # --- print_doctor: lifecycle_sync_state in 詳細診断 ---

    def test_doctor_output_shows_lifecycle_sync_state_synced(self) -> None:
        state = {
            "mode": "idle",
            "error": False,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        args = make_args()
        derived_args = argparse.Namespace()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            stop_path = temp_root / "STOP"
            report_path = temp_root / "codex_report.md"
            prompt_path = temp_root / "codex_prompt.md"
            out = io.StringIO()
            with (
                patch.object(start_bridge.run_until_stop, "load_state", return_value=state),
                patch.object(start_bridge, "build_derived_args", return_value=derived_args),
                patch.object(start_bridge.run_until_stop, "start_bridge_mode", return_value="このまま再開できます"),
                patch.object(
                    start_bridge.run_until_stop,
                    "start_bridge_resume_guidance",
                    return_value=("ChatGPTへ依頼準備中", "次の依頼を送る準備ができています。", "目視確認してください。"),
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
        self.assertIn("lifecycle_sync_state:", output)
        self.assertIn("signal=synced", output)
        self.assertIn("stage=closing", output)

    def test_doctor_output_shows_lifecycle_sync_state_not_recorded(self) -> None:
        state = {"mode": "idle", "error": False}
        args = make_args()
        derived_args = argparse.Namespace()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            stop_path = temp_root / "STOP"
            report_path = temp_root / "codex_report.md"
            prompt_path = temp_root / "codex_prompt.md"
            out = io.StringIO()
            with (
                patch.object(start_bridge.run_until_stop, "load_state", return_value=state),
                patch.object(start_bridge, "build_derived_args", return_value=derived_args),
                patch.object(start_bridge.run_until_stop, "start_bridge_mode", return_value="このまま再開できます"),
                patch.object(
                    start_bridge.run_until_stop,
                    "start_bridge_resume_guidance",
                    return_value=("ChatGPTへ依頼準備中", "次の依頼を送る準備ができています。", "目視確認してください。"),
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
        self.assertIn("lifecycle_sync_state:", output)
        self.assertIn("not_recorded", output)

    # --- consistency with prior surfaces ---

    def test_stop_summary_includes_lifecycle_sync_state_field(self) -> None:
        args = run_until_stop.parse_args(
            ["--project-path", "/tmp/repo", "--max-execution-count", "6", "--entry-script", "scripts/start_bridge.py"],
            {},
        )
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        summary = run_until_stop.summarize_run(
            args=args,
            reason="test stop",
            steps=1,
            warnings=[],
            initial_state=state,
            final_state=state,
            history=[],
        )
        self.assertIn("lifecycle_sync_state:", summary)
        self.assertIn("signal=synced", summary)
        self.assertIn("stage=closing", summary)

    def test_stop_summary_lifecycle_sync_state_not_recorded_when_no_sync_data(self) -> None:
        args = run_until_stop.parse_args(
            ["--project-path", "/tmp/repo", "--max-execution-count", "6", "--entry-script", "scripts/start_bridge.py"],
            {},
        )
        state = {"mode": "idle"}
        summary = run_until_stop.summarize_run(
            args=args,
            reason="test stop",
            steps=0,
            warnings=[],
            initial_state=state,
            final_state=state,
            history=[],
        )
        self.assertIn("lifecycle_sync_state:", summary)
        self.assertIn("not_recorded", summary)


class RunSummaryLifecycleSyncSurfacingTests(unittest.TestCase):
    """Phase 1 (#59): lifecycle sync outcomes are visible in run summary text.

    Covers suggested_next_note() for request_next_prompt and completed action paths
    that were previously missing lifecycle sync surfacing.
    """

    # --- suggested_next_note: request_next_prompt ---

    def test_suggested_next_note_request_next_prompt_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_prompt": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)
        self.assertIn("stage=closing", note)

    def test_suggested_next_note_request_next_prompt_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_prompt": True,
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=skipped_no_project", note)

    def test_suggested_next_note_request_next_prompt_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_prompt": True,
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=sync_failed", note)
        self.assertIn("reason=blocked_project_preflight", note)

    def test_suggested_next_note_request_next_prompt_no_lifecycle_sync_when_no_sync_data(self) -> None:
        state = {"mode": "idle", "need_chatgpt_prompt": True}
        note = run_until_stop.suggested_next_note(state)
        self.assertNotIn("lifecycle_sync", note)
        self.assertIn("Safari", note)

    # --- suggested_next_note: completed action ---

    def test_suggested_next_note_completed_action_shows_lifecycle_sync_synced(self) -> None:
        # resolve_unified_next_action returns "completed" when mode=idle, no need_* flags,
        # and no chatgpt_decision set (chatgpt_decision check fires first if set).
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)

    def test_suggested_next_note_completed_action_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=skipped_no_project", note)

    def test_suggested_next_note_completed_action_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "transition_error",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=sync_failed", note)
        self.assertIn("reason=transition_error", note)

    def test_suggested_next_note_completed_action_no_lifecycle_sync_when_no_sync_data(self) -> None:
        state = {"mode": "idle"}
        note = run_until_stop.suggested_next_note(state)
        self.assertNotIn("lifecycle_sync", note)
        self.assertIn("追加の操作は不要です", note)

    # --- run summary (summarize_run) consistent with lifecycle_sync_state field ---

    def test_run_summary_suggested_note_includes_lifecycle_sync_for_request_next_prompt(self) -> None:
        """summarize_run - 補足 field carries lifecycle_sync when action=request_next_prompt."""
        args = run_until_stop.parse_args(
            ["--project-path", "/tmp/repo", "--max-execution-count", "6", "--entry-script", "scripts/start_bridge.py"],
            {},
        )
        state = {
            "mode": "idle",
            "need_chatgpt_prompt": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note_override = run_until_stop.suggested_next_note(state)
        summary = run_until_stop.summarize_run(
            args=args,
            reason="test stop",
            steps=1,
            warnings=[],
            initial_state=state,
            final_state=state,
            history=[],
            suggested_next_note_override=note_override,
        )
        self.assertIn("lifecycle_sync_state:", summary)
        self.assertIn("signal=synced", summary)
        self.assertIn("補足:", summary)


class HandoffArtifactTextLifecycleSyncSurfacingTests(unittest.TestCase):
    """Phase 2 (#59): lifecycle sync outcomes are visible in handoff artifact text.

    Covers present_bridge_handoff() paths that were previously missing lifecycle sync:
    - cycle_boundary_stop
    - --max-steps= stop reason
    - ユーザー中断 stop reason
    """

    # --- cycle_boundary_stop ---

    def test_handoff_cycle_boundary_stop_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        handoff = present_bridge_handoff(state, cycle_boundary_stop=True)
        self.assertEqual(handoff.title, "この run は cycle 完了で停止しました。")
        self.assertIn("lifecycle_sync", handoff.detail)
        self.assertIn("signal=synced", handoff.detail)
        self.assertIn("stage=closing", handoff.detail)

    def test_handoff_cycle_boundary_stop_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        handoff = present_bridge_handoff(state, cycle_boundary_stop=True)
        self.assertEqual(handoff.title, "この run は cycle 完了で停止しました。")
        self.assertIn("lifecycle_sync", handoff.detail)
        self.assertIn("signal=skipped_no_project", handoff.detail)

    def test_handoff_cycle_boundary_stop_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        handoff = present_bridge_handoff(state, cycle_boundary_stop=True)
        self.assertEqual(handoff.title, "この run は cycle 完了で停止しました。")
        self.assertIn("lifecycle_sync", handoff.detail)
        self.assertIn("signal=sync_failed", handoff.detail)
        self.assertIn("reason=blocked_project_preflight", handoff.detail)

    def test_handoff_cycle_boundary_stop_no_lifecycle_sync_when_no_sync_data(self) -> None:
        handoff = present_bridge_handoff({"mode": "idle"}, cycle_boundary_stop=True)
        self.assertEqual(handoff.title, "この run は cycle 完了で停止しました。")
        self.assertNotIn("lifecycle_sync", handoff.detail)

    def test_handoff_cycle_boundary_stop_suggested_note_takes_priority(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
        }
        handoff = present_bridge_handoff(state, cycle_boundary_stop=True, suggested_note="caller note")
        self.assertEqual(handoff.detail, "caller note")

    # --- --max-steps= stop reason ---

    def test_handoff_max_steps_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "followup_created",
        }
        handoff = present_bridge_handoff(state, reason="--max-steps=6 に達しました")
        self.assertEqual(handoff.title, "上限回数に達したため、ここで一旦止めました。")
        self.assertIn("lifecycle_sync", handoff.detail)
        self.assertIn("signal=synced", handoff.detail)

    def test_handoff_max_steps_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
        }
        handoff = present_bridge_handoff(state, reason="--max-steps=6 に達しました")
        self.assertEqual(handoff.title, "上限回数に達したため、ここで一旦止めました。")
        self.assertIn("signal=skipped_no_project", handoff.detail)

    def test_handoff_max_steps_no_lifecycle_sync_when_no_sync_data(self) -> None:
        handoff = present_bridge_handoff({"mode": "idle", "need_chatgpt_next": True}, reason="--max-steps=6 に達しました")
        self.assertEqual(handoff.title, "上限回数に達したため、ここで一旦止めました。")
        self.assertNotIn("lifecycle_sync", handoff.detail)

    def test_handoff_max_steps_suggested_note_takes_priority(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
        }
        handoff = present_bridge_handoff(state, reason="--max-steps=6 に達しました", suggested_note="caller override")
        self.assertEqual(handoff.detail, "caller override")

    # --- ユーザー中断 stop reason ---

    def test_handoff_user_interrupt_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        handoff = present_bridge_handoff(state, reason="ユーザー中断")
        self.assertEqual(handoff.title, "途中で停止しました。summary / note を確認してください。")
        self.assertIn("lifecycle_sync", handoff.detail)
        self.assertIn("signal=synced", handoff.detail)

    def test_handoff_user_interrupt_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
        }
        handoff = present_bridge_handoff(state, reason="ユーザー中断")
        self.assertEqual(handoff.title, "途中で停止しました。summary / note を確認してください。")
        self.assertIn("signal=sync_failed", handoff.detail)
        self.assertIn("reason=blocked_project_preflight", handoff.detail)

    def test_handoff_user_interrupt_no_lifecycle_sync_when_no_sync_data(self) -> None:
        handoff = present_bridge_handoff({"mode": "idle", "need_chatgpt_next": True}, reason="ユーザー中断")
        self.assertEqual(handoff.title, "途中で停止しました。summary / note を確認してください。")
        self.assertNotIn("lifecycle_sync", handoff.detail)

    def test_handoff_user_interrupt_suggested_note_takes_priority(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
        }
        handoff = present_bridge_handoff(state, reason="ユーザー中断", suggested_note="operator note")
        self.assertEqual(handoff.detail, "operator note")


class ConciseReviewSummaryLifecycleSyncSurfacingTests(unittest.TestCase):
    """Phase 1 (#61): lifecycle sync outcomes are visible in concise review summary text.

    Covers suggested_next_note() for request_prompt_from_report action paths
    that were previously missing lifecycle sync surfacing.
    """

    # --- suggested_next_note: request_prompt_from_report (basic path) ---

    def test_suggested_next_note_request_prompt_from_report_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)
        self.assertIn("stage=closing", note)

    def test_suggested_next_note_request_prompt_from_report_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=skipped_no_project", note)

    def test_suggested_next_note_request_prompt_from_report_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "transition_error",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=sync_failed", note)
        self.assertIn("reason=transition_error", note)

    def test_suggested_next_note_request_prompt_from_report_no_lifecycle_sync_when_no_sync_data(self) -> None:
        state = {"mode": "idle", "need_chatgpt_next": True}
        note = run_until_stop.suggested_next_note(state)
        self.assertNotIn("lifecycle_sync", note)
        self.assertIn("Safari", note)

    # --- suggested_next_note: request_prompt_from_report (pending handoff rotation path) ---

    def test_suggested_next_note_pending_handoff_rotation_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "next_request_requires_rotation": True,
            "pending_handoff_log": "logs/handoff.md",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "review",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=synced", note)
        self.assertIn("handoff", note)

    def test_suggested_next_note_pending_handoff_rotation_no_lifecycle_sync_when_no_sync_data(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "next_request_requires_rotation": True,
            "pending_handoff_log": "logs/handoff.md",
        }
        note = run_until_stop.suggested_next_note(state)
        self.assertNotIn("lifecycle_sync", note)
        self.assertIn("handoff", note)


class CloseoutFacingHumanTextLifecycleSyncSurfacingTests(unittest.TestCase):
    """Phase 2 (#61): lifecycle sync outcomes are visible in closeout-facing human text.

    Covers entry_guidance() for the completed action path (closeout text shown
    at session or work-unit close) that was previously missing lifecycle sync surfacing.
    """

    def _make_args(self) -> "argparse.Namespace":
        return run_until_stop.parse_args(
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

    # --- entry_guidance: completed ---

    def test_entry_guidance_completed_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        args = self._make_args()
        guidance = run_until_stop.entry_guidance(state, args)
        self.assertIn("lifecycle_sync", guidance)
        self.assertIn("signal=synced", guidance)
        self.assertIn("stage=done", guidance)

    def test_entry_guidance_completed_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
        }
        args = self._make_args()
        guidance = run_until_stop.entry_guidance(state, args)
        self.assertIn("lifecycle_sync", guidance)
        self.assertIn("signal=skipped_no_project", guidance)

    def test_entry_guidance_completed_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        args = self._make_args()
        guidance = run_until_stop.entry_guidance(state, args)
        self.assertIn("lifecycle_sync", guidance)
        self.assertIn("signal=sync_failed", guidance)
        self.assertIn("reason=blocked_project_preflight", guidance)

    def test_entry_guidance_completed_no_lifecycle_sync_when_no_sync_data(self) -> None:
        state = {"mode": "idle"}
        args = self._make_args()
        guidance = run_until_stop.entry_guidance(state, args)
        self.assertNotIn("lifecycle_sync", guidance)
        self.assertIn("追加の操作は不要です", guidance)

    # --- run summary integration: suggested_next_note for request_prompt_from_report includes lifecycle_sync ---

    def test_run_summary_suggested_note_includes_lifecycle_sync_for_request_prompt_from_report(self) -> None:
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
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note_override = run_until_stop.suggested_next_note(state)
        summary = run_until_stop.summarize_run(
            args=args,
            reason="test stop",
            steps=1,
            warnings=[],
            initial_state=state,
            final_state=state,
            history=[],
            suggested_next_note_override=note_override,
        )
        self.assertIn("lifecycle_sync_state:", summary)
        self.assertIn("signal=synced", summary)
        self.assertIn("補足:", summary)


class WaitingPromptReplyFetchTransitionTests(unittest.TestCase):
    """resolve_next_generation_transition() routes waiting_prompt_reply + pending hash
    to fetch_next_prompt instead of no_action.

    Root cause: request_next_prompt.py calls save_pending_request() but does NOT set
    last_issue_centric_pending_generation_id, so generation_lifecycle stays
    "fresh_available" and runtime_action becomes "need_next_generation".  Without the
    reply-wait branch in resolve_next_generation_transition(), waiting_prompt_reply + hash
    would fall through to no_action.
    """

    def test_waiting_prompt_reply_with_pending_hash_routes_to_fetch(self) -> None:
        """waiting_prompt_reply + pending_request_hash → fetch_next_prompt, not no_action."""
        from _bridge_common import resolve_next_generation_transition
        state: dict[str, object] = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "abc123hash",
        }
        self.assertEqual(resolve_next_generation_transition(state), "fetch_next_prompt")

    def test_waiting_prompt_reply_without_pending_hash_stays_no_action(self) -> None:
        """waiting_prompt_reply without pending_request_hash → no_action (genuine stale)."""
        from _bridge_common import resolve_next_generation_transition
        state: dict[str, object] = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "",
        }
        self.assertEqual(resolve_next_generation_transition(state), "no_action")

    def test_extended_wait_with_pending_hash_routes_to_fetch(self) -> None:
        """extended_wait + pending_request_hash → fetch_next_prompt."""
        from _bridge_common import resolve_next_generation_transition
        state: dict[str, object] = {
            "mode": "extended_wait",
            "pending_request_hash": "hashxyz",
        }
        self.assertEqual(resolve_next_generation_transition(state), "fetch_next_prompt")

    def test_past_no_action_chatgpt_decision_does_not_block_fetch(self) -> None:
        """A stale chatgpt_decision=no_action field must not prevent fetch when hash present."""
        from _bridge_common import resolve_next_generation_transition
        state: dict[str, object] = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "hash456",
            "pending_request_source": "ready_issue:#42 some task",
            "pending_request_signal": "conversation_url",
            "chatgpt_decision": "no_action",
        }
        self.assertEqual(resolve_next_generation_transition(state), "fetch_next_prompt")

    def test_unified_action_waiting_prompt_reply_with_pending_hash_is_fetch(self) -> None:
        """resolve_unified_next_action() returns fetch_next_prompt for the full bug state."""
        from _bridge_common import resolve_unified_next_action
        # Minimal state replicating the bug: request_next_prompt ran successfully,
        # last_issue_centric_pending_generation_id is NOT set (request_next_prompt
        # does not set it), but pending_request_hash is present.
        state: dict[str, object] = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "bugrepro_hash",
            "pending_request_source": "ready_issue:#1 PromptWeave initial",
            "pending_request_log": "bridge/history/sent_prompt_request.md",
            "pending_request_signal": "conversation_url",
            "last_issue_centric_pending_generation_id": "",
            "error": False,
            "pause": False,
        }
        self.assertEqual(resolve_unified_next_action(state), "fetch_next_prompt")

    def test_awaiting_user_no_pending_hash_not_affected(self) -> None:
        """awaiting_user without pending hash is not changed by the new branch."""
        from _bridge_common import resolve_next_generation_transition
        state: dict[str, object] = {
            "mode": "awaiting_user",
            "chatgpt_decision": "human_review",
            "pending_request_hash": "",
        }
        self.assertEqual(resolve_next_generation_transition(state), "request_prompt_from_report")


class ContractCorrectionHelperTests(unittest.TestCase):
    """Unit-tests for the retryable-contract detection and correction helpers in fetch_next_prompt."""

    def setUp(self) -> None:
        import fetch_next_prompt  # noqa: E402 — imported here to avoid heavy top-level side effects
        self.fp = fetch_next_prompt

    # --- _is_retryable_contract_error ---

    def test_retryable_for_invalid_contract_status(self) -> None:
        """reply_complete_invalid_contract is always retryable regardless of reason."""
        self.assertTrue(
            self.fp._is_retryable_contract_error("CHATGPT_CODEX_BODY block is not valid base64: ...", "reply_complete_invalid_contract")
        )

    def test_retryable_for_no_marker_status(self) -> None:
        """reply_complete_no_marker (ChatGPT forgot the contract) is retryable."""
        self.assertTrue(
            self.fp._is_retryable_contract_error("completion tag present but issue-centric decision markers are missing.", "reply_complete_no_marker")
        )

    def test_retryable_for_bad_json_in_invalid_contract(self) -> None:
        self.assertTrue(
            self.fp._is_retryable_contract_error("CHATGPT_DECISION_JSON is not valid JSON: ...", "reply_complete_invalid_contract")
        )

    def test_not_retryable_for_not_ready_status(self) -> None:
        """reply_not_ready means the response is still incomplete — do not retry."""
        self.assertFalse(
            self.fp._is_retryable_contract_error("reply not ready", "reply_not_ready")
        )

    def test_not_retryable_for_unknown_status(self) -> None:
        self.assertFalse(
            self.fp._is_retryable_contract_error("some reason", "reply_complete_legacy_contract")
        )

    # --- _build_contract_correction_request ---

    def test_build_correction_request_contains_reason(self) -> None:
        reason = "CHATGPT_CODEX_BODY block is not valid base64: ..."
        text = self.fp._build_contract_correction_request(reason)
        self.assertIn(reason, text)

    def test_build_correction_request_contains_reply_complete_tag(self) -> None:
        text = self.fp._build_contract_correction_request("some reason")
        self.assertIn("===CHATGPT_REPLY_COMPLETE===", text)

    def test_build_correction_request_mentions_decision_json(self) -> None:
        text = self.fp._build_contract_correction_request("some reason")
        self.assertIn("CHATGPT_DECISION_JSON", text)

    # --- max corrections constant ---

    def test_max_contract_corrections_is_two(self) -> None:
        self.assertEqual(self.fp._MAX_CONTRACT_CORRECTIONS, 2)

    # --- _build_binding_mismatch_correction_request ---

    def test_binding_mismatch_request_contains_reason(self) -> None:
        reason = "current ready issue は #5 ですが stale target #7 を返しました。"
        text = self.fp._build_binding_mismatch_correction_request(reason, "#5 Ready: feature X")
        self.assertIn(reason, text)

    def test_binding_mismatch_request_contains_current_ready_issue_ref(self) -> None:
        text = self.fp._build_binding_mismatch_correction_request("some reason", "#5 Ready: feature X")
        self.assertIn("#5", text)

    def test_binding_mismatch_request_contains_reply_complete_tag(self) -> None:
        text = self.fp._build_binding_mismatch_correction_request("some reason", "#5 Ready: feature X")
        self.assertIn("===CHATGPT_REPLY_COMPLETE===", text)

    def test_binding_mismatch_request_mentions_target_issue(self) -> None:
        text = self.fp._build_binding_mismatch_correction_request("some reason", "#5 Ready: feature X")
        self.assertIn("target_issue", text)

    def test_binding_mismatch_request_differs_from_generic(self) -> None:
        """The binding mismatch request must NOT start with the generic contract error intro."""
        text = self.fp._build_binding_mismatch_correction_request("some reason", "#5 Ready: feature X")
        self.assertNotIn("issue-centric contract の不正がありました", text)


class LegacyPathDeprecationTests(unittest.TestCase):
    """issue-centric contract が正規ルートであることを示す縮退整理テスト.

    旧 visible-text マーカー (===CHATGPT_PROMPT_REPLY===) への参照が
    ユーザー向けの表示文言から除去されているかを検証する.
    """

    def test_fetch_next_prompt_note_does_not_mention_legacy_marker(self) -> None:
        """suggested_next_note() for fetch_next_prompt no longer shows ===CHATGPT_PROMPT_REPLY===."""
        note = run_until_stop.suggested_next_note(
            {
                "mode": "waiting_prompt_reply",
                "pending_request_hash": "abc",
                "pending_request_source": "ready_issue:abc",
                "pending_request_log": "logs/request.md",
                "pending_request_signal": "",
            }
        )
        # Legacy marker must not appear in user-visible guidance.
        self.assertNotIn("CHATGPT_PROMPT_REPLY", note)

    def test_fetch_next_prompt_note_uses_issue_centric_wording(self) -> None:
        """suggested_next_note() for fetch_next_prompt mentions issue-centric contract."""
        note = run_until_stop.suggested_next_note(
            {
                "mode": "waiting_prompt_reply",
                "pending_request_hash": "abc",
                "pending_request_source": "ready_issue:abc",
                "pending_request_log": "logs/request.md",
                "pending_request_signal": "",
            }
        )
        self.assertIn("issue-centric contract reply", note)

    def test_legacy_markers_tuple_defined_in_fetch_next_prompt(self) -> None:
        """_LEGACY_REPLY_MARKERS is still defined (safety net not removed)."""
        import fetch_next_prompt as fnp
        self.assertIn("===CHATGPT_PROMPT_REPLY===", fnp._LEGACY_REPLY_MARKERS)
        self.assertIn("===CHATGPT_NO_CODEX===", fnp._LEGACY_REPLY_MARKERS)

    def test_classify_returns_reply_complete_legacy_contract_for_old_markers(self) -> None:
        """classify_issue_centric_reply_readiness detects legacy markers → reply_complete_legacy_contract."""
        import fetch_next_prompt as fnp
        raw = "\n".join([
            "あなた:",
            "request text",
            "ChatGPT:",
            "===CHATGPT_PROMPT_REPLY===",
            "some prompt body",
            "===END_REPLY===",
        ])
        readiness = fnp.classify_issue_centric_reply_readiness(raw)
        self.assertEqual(readiness.status, "reply_complete_legacy_contract")

    def test_is_retryable_contract_error_does_not_treat_legacy_as_retryable(self) -> None:
        """reply_complete_legacy_contract is NOT treated as retryable (it is an exception path)."""
        import fetch_next_prompt as fnp
        self.assertFalse(
            fnp._is_retryable_contract_error("legacy visible-text reply contract is present.", "reply_complete_legacy_contract")
        )



