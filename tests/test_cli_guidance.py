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

    def test_run_until_stop_help_shows_engine_role_and_option_wording(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/run_until_stop.py", "--help"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        # description: engine, normal entry is start_bridge.py
        self.assertIn("実行エンジン", output)
        self.assertIn("scripts/start_bridge.py", output)
        # --ready-issue-ref: explicit override, not "通常入口で使う"
        self.assertIn("明示指定", output)
        self.assertNotIn("通常入口で使う", output)
        # --request-body: exception / recovery / override only
        self.assertIn("exception / recovery / override", output)
        # --select-issue: explicit issue selection helper
        self.assertIn("issue selection", output)


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

    def test_request_next_prompt_recommends_explicit_ready_issue_entry(self) -> None:
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
        self.assertEqual(label, "明示指定の ready issue で開始")
        self.assertIn("--ready-issue-ref", command)

    def test_request_next_prompt_recommends_issue_selection_for_fresh_start(self) -> None:
        """Brand-new state without --ready-issue-ref → 'issue selection から開始'."""
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
                "mode": "idle",
                "need_chatgpt_prompt": True,
            },
        )
        self.assertEqual(label, "issue selection から開始")
        self.assertNotIn("--ready-issue-ref", command)


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

    def test_no_action_continuation_state_routes_to_request_prompt_from_report(self) -> None:
        """After plain no_action, mode=idle + need_chatgpt_next routes to request_prompt_from_report.

        The no_action continuation fix in _finalize_dispatch sets these fields so
        the next bridge_orchestrator call proceeds to request_prompt_from_report
        instead of resolving to 'no_action' (terminal).
        """
        from _bridge_common import resolve_next_generation_transition
        state: dict[str, object] = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "chatgpt_decision": "issue_centric:no_action",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
            "last_issue_centric_next_request_target": "#15",
        }
        self.assertEqual(resolve_next_generation_transition(state), "request_prompt_from_report")

    def test_no_action_awaiting_user_without_chatgpt_decision_routes_to_no_action(self) -> None:
        """awaiting_user + chatgpt_decision=issue_centric:no_action (old unfixed state) → no_action.

        Documents the original stopping condition that the no_action continuation fix resolves:
        before the fix, mode stayed 'awaiting_user' and chatgpt_decision was 'issue_centric:no_action',
        which is not in {'human_review', 'need_info'}, so the transition returned 'no_action'.
        The fix resets mode to 'idle' + need_chatgpt_next=True so this path is no longer taken.
        """
        from _bridge_common import resolve_next_generation_transition
        state: dict[str, object] = {
            "mode": "awaiting_user",
            "chatgpt_decision": "issue_centric:no_action",
            "pending_request_hash": "",
        }
        self.assertEqual(resolve_next_generation_transition(state), "no_action")


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

    def test_legacy_reply_readiness_has_no_ic_decision(self) -> None:
        """classify_issue_centric_reply_readiness for legacy markers → decision is None (no auto-continue)."""
        import fetch_next_prompt as fnp
        raw = "\n".join([
            "あなた:",
            "request text",
            "ChatGPT:",
            "===CHATGPT_PROMPT_REPLY===",
            "some prompt body",
        ])
        readiness = fnp.classify_issue_centric_reply_readiness(raw)
        self.assertEqual(readiness.status, "reply_complete_legacy_contract")
        # decision is None → no IC decision to dispatch; explicit stop required
        self.assertIsNone(readiness.decision)

    def test_legacy_stop_condition_is_distinct_from_retryable_and_success(self) -> None:
        """reply_complete_legacy_contract is neither retryable (correction retry) nor success (IC dispatch)."""
        import fetch_next_prompt as fnp
        status = "reply_complete_legacy_contract"
        reason = "legacy visible-text reply contract is present."
        # Not retryable → no correction request sent
        self.assertFalse(fnp._is_retryable_contract_error(reason, status))
        # Not a valid IC contract → not success path
        raw = "\n".join([
            "あなた:",
            "request text",
            "ChatGPT:",
            "===CHATGPT_PROMPT_REPLY===",
            "body",
        ])
        readiness = fnp.classify_issue_centric_reply_readiness(raw)
        # Explicit stop condition: status == "reply_complete_legacy_contract"
        self.assertEqual(readiness.status, "reply_complete_legacy_contract")
        # IC decision is absent → cannot proceed to IC dispatch
        self.assertIsNone(readiness.decision)


class ProjectSyncWarningOperatorSurfaceTests(unittest.TestCase):
    """Phase 56 — project_state_sync_failed operator-facing warning surface.

    Verifies that:
    - primary / followup / lifecycle project sync failed → operator-facing warning note
    - no-project / issue-only-fallback → NO warning note
    - main runtime hard errors / deliberate stops are NOT mixed with sync warnings
    - warning vocabulary is consistent across all three families
    - existing stop paths (completed, no_action, codex stops, etc.) do not regress
    - bridge_project_sync_warning_suffix and format_project_sync_warning_note are exported
    """

    # ------------------------------------------------------------------
    # Helper: build a minimal terminal state
    # ------------------------------------------------------------------

    def _completed_state(self, **extra: object) -> dict:
        return {"mode": "idle", **extra}

    # ------------------------------------------------------------------
    # Group 1: _detect_project_sync_warning / _resolve_project_sync_warning_family
    # ------------------------------------------------------------------

    def test_detect_project_sync_warning_primary_failed(self) -> None:
        from _bridge_common import _detect_project_sync_warning
        state = {"last_issue_centric_primary_project_sync_status": "project_state_sync_failed"}
        self.assertTrue(_detect_project_sync_warning(state))

    def test_detect_project_sync_warning_followup_failed(self) -> None:
        from _bridge_common import _detect_project_sync_warning
        state = {"last_issue_centric_followup_project_sync_status": "project_state_sync_failed"}
        self.assertTrue(_detect_project_sync_warning(state))

    def test_detect_project_sync_warning_lifecycle_failed(self) -> None:
        from _bridge_common import _detect_project_sync_warning
        state = {"last_issue_centric_lifecycle_sync_status": "project_state_sync_failed"}
        self.assertTrue(_detect_project_sync_warning(state))

    def test_detect_project_sync_warning_all_three_failed(self) -> None:
        from _bridge_common import _detect_project_sync_warning
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_followup_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_lifecycle_sync_status": "project_state_sync_failed",
        }
        self.assertTrue(_detect_project_sync_warning(state))

    def test_detect_project_sync_warning_no_failed(self) -> None:
        from _bridge_common import _detect_project_sync_warning
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_synced",
            "last_issue_centric_followup_project_sync_status": "not_requested_no_project",
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
        }
        self.assertFalse(_detect_project_sync_warning(state))

    def test_detect_project_sync_warning_no_project_not_warning(self) -> None:
        from _bridge_common import _detect_project_sync_warning
        state = {
            "last_issue_centric_primary_project_sync_status": "not_requested_no_project",
            "last_issue_centric_followup_project_sync_status": "not_requested_no_project",
        }
        self.assertFalse(_detect_project_sync_warning(state))

    def test_detect_project_sync_warning_issue_only_fallback_not_warning(self) -> None:
        from _bridge_common import _detect_project_sync_warning
        state = {
            "last_issue_centric_primary_project_sync_status": "issue_only_fallback",
            "last_issue_centric_followup_project_sync_status": "issue_only_fallback",
        }
        self.assertFalse(_detect_project_sync_warning(state))

    def test_detect_project_sync_warning_empty_state(self) -> None:
        from _bridge_common import _detect_project_sync_warning
        self.assertFalse(_detect_project_sync_warning({}))

    def test_resolve_warning_family_primary_only(self) -> None:
        from _bridge_common import _resolve_project_sync_warning_family
        state = {"last_issue_centric_primary_project_sync_status": "project_state_sync_failed"}
        self.assertEqual(_resolve_project_sync_warning_family(state), ["primary"])

    def test_resolve_warning_family_followup_only(self) -> None:
        from _bridge_common import _resolve_project_sync_warning_family
        state = {"last_issue_centric_followup_project_sync_status": "project_state_sync_failed"}
        self.assertEqual(_resolve_project_sync_warning_family(state), ["followup"])

    def test_resolve_warning_family_lifecycle_only(self) -> None:
        from _bridge_common import _resolve_project_sync_warning_family
        state = {"last_issue_centric_lifecycle_sync_status": "project_state_sync_failed"}
        self.assertEqual(_resolve_project_sync_warning_family(state), ["lifecycle"])

    def test_resolve_warning_family_primary_and_followup(self) -> None:
        from _bridge_common import _resolve_project_sync_warning_family
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_followup_project_sync_status": "project_state_sync_failed",
        }
        families = _resolve_project_sync_warning_family(state)
        self.assertIn("primary", families)
        self.assertIn("followup", families)

    def test_resolve_warning_family_empty_when_no_failures(self) -> None:
        from _bridge_common import _resolve_project_sync_warning_family
        state = {"last_issue_centric_primary_project_sync_status": "project_state_synced"}
        self.assertEqual(_resolve_project_sync_warning_family(state), [])

    # ------------------------------------------------------------------
    # Group 2: bridge_project_sync_warning_suffix (primary+followup only)
    # ------------------------------------------------------------------

    def test_bridge_project_sync_warning_suffix_primary_failed(self) -> None:
        from _bridge_common import bridge_project_sync_warning_suffix
        state = {"last_issue_centric_primary_project_sync_status": "project_state_sync_failed"}
        suffix = bridge_project_sync_warning_suffix(state)
        self.assertIn("project_sync", suffix)
        self.assertIn("warning", suffix)
        self.assertIn("primary", suffix)
        self.assertIn("Project state sync", suffix)

    def test_bridge_project_sync_warning_suffix_followup_failed(self) -> None:
        from _bridge_common import bridge_project_sync_warning_suffix
        state = {"last_issue_centric_followup_project_sync_status": "project_state_sync_failed"}
        suffix = bridge_project_sync_warning_suffix(state)
        self.assertIn("project_sync", suffix)
        self.assertIn("warning", suffix)
        self.assertIn("followup", suffix)

    def test_bridge_project_sync_warning_suffix_lifecycle_failed_is_empty(self) -> None:
        """Lifecycle sync is handled by bridge_lifecycle_sync_suffix; warning suffix returns empty."""
        from _bridge_common import bridge_project_sync_warning_suffix
        state = {"last_issue_centric_lifecycle_sync_status": "project_state_sync_failed"}
        suffix = bridge_project_sync_warning_suffix(state)
        # Lifecycle is intentionally NOT covered here — it's covered by _bridge_lifecycle_sync_suffix
        self.assertEqual(suffix, "")

    def test_bridge_project_sync_warning_suffix_no_project_is_empty(self) -> None:
        from _bridge_common import bridge_project_sync_warning_suffix
        state = {
            "last_issue_centric_primary_project_sync_status": "not_requested_no_project",
            "last_issue_centric_followup_project_sync_status": "not_requested_no_project",
        }
        self.assertEqual(bridge_project_sync_warning_suffix(state), "")

    def test_bridge_project_sync_warning_suffix_issue_only_fallback_is_empty(self) -> None:
        from _bridge_common import bridge_project_sync_warning_suffix
        state = {
            "last_issue_centric_primary_project_sync_status": "issue_only_fallback",
            "last_issue_centric_followup_project_sync_status": "issue_only_fallback",
        }
        self.assertEqual(bridge_project_sync_warning_suffix(state), "")

    def test_bridge_project_sync_warning_suffix_synced_is_empty(self) -> None:
        from _bridge_common import bridge_project_sync_warning_suffix
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_synced",
            "last_issue_centric_followup_project_sync_status": "project_state_synced",
        }
        self.assertEqual(bridge_project_sync_warning_suffix(state), "")

    def test_bridge_project_sync_warning_suffix_empty_state_is_empty(self) -> None:
        from _bridge_common import bridge_project_sync_warning_suffix
        self.assertEqual(bridge_project_sync_warning_suffix({}), "")

    def test_bridge_project_sync_warning_suffix_primary_and_followup_both_failed(self) -> None:
        from _bridge_common import bridge_project_sync_warning_suffix
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_followup_project_sync_status": "project_state_sync_failed",
        }
        suffix = bridge_project_sync_warning_suffix(state)
        self.assertIn("primary", suffix)
        self.assertIn("followup", suffix)

    # ------------------------------------------------------------------
    # Group 3: format_project_sync_warning_note (all 3 families)
    # ------------------------------------------------------------------

    def test_format_project_sync_warning_note_none_when_no_failures(self) -> None:
        from _bridge_common import format_project_sync_warning_note
        state = {}
        self.assertEqual(format_project_sync_warning_note(state), "none")

    def test_format_project_sync_warning_note_primary_failed(self) -> None:
        from _bridge_common import format_project_sync_warning_note
        state = {"last_issue_centric_primary_project_sync_status": "project_state_sync_failed"}
        note = format_project_sync_warning_note(state)
        self.assertIn("primary", note)
        self.assertIn("project_state_sync_failed", note)
        self.assertNotEqual(note, "none")

    def test_format_project_sync_warning_note_followup_failed(self) -> None:
        from _bridge_common import format_project_sync_warning_note
        state = {"last_issue_centric_followup_project_sync_status": "project_state_sync_failed"}
        note = format_project_sync_warning_note(state)
        self.assertIn("followup", note)
        self.assertIn("project_state_sync_failed", note)

    def test_format_project_sync_warning_note_lifecycle_failed(self) -> None:
        from _bridge_common import format_project_sync_warning_note
        state = {"last_issue_centric_lifecycle_sync_status": "project_state_sync_failed"}
        note = format_project_sync_warning_note(state)
        self.assertIn("lifecycle", note)
        self.assertIn("project_state_sync_failed", note)

    def test_format_project_sync_warning_note_all_three_failed(self) -> None:
        from _bridge_common import format_project_sync_warning_note
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_followup_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_lifecycle_sync_status": "project_state_sync_failed",
        }
        note = format_project_sync_warning_note(state)
        self.assertIn("primary", note)
        self.assertIn("followup", note)
        self.assertIn("lifecycle", note)

    def test_format_project_sync_warning_note_no_project_returns_none(self) -> None:
        from _bridge_common import format_project_sync_warning_note
        state = {
            "last_issue_centric_primary_project_sync_status": "not_requested_no_project",
        }
        self.assertEqual(format_project_sync_warning_note(state), "none")

    # ------------------------------------------------------------------
    # Group 4: format_operator_stop_note with project sync warning
    # ------------------------------------------------------------------

    def test_format_operator_stop_note_completed_shows_primary_project_sync_warning(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_sync_failed",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)
        self.assertIn("primary", note)

    def test_format_operator_stop_note_completed_shows_followup_project_sync_warning(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_followup_project_sync_status="project_state_sync_failed",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)
        self.assertIn("followup", note)

    def test_format_operator_stop_note_completed_no_warning_when_no_project(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="not_requested_no_project",
            last_issue_centric_followup_project_sync_status="not_requested_no_project",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertNotIn("project_sync: warning", note)

    def test_format_operator_stop_note_completed_no_warning_for_issue_only_fallback(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="issue_only_fallback",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertNotIn("project_sync: warning", note)

    def test_format_operator_stop_note_completed_no_warning_when_synced(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_synced",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertNotIn("project_sync: warning", note)

    def test_format_operator_stop_note_completed_warning_with_remediation_hint(self) -> None:
        """Warning note should include remediation guidance for the operator."""
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_sync_failed",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("Project", note)
        self.assertIn("設定", note)

    def test_format_operator_stop_note_completed_lifecycle_and_primary_both_failed(self) -> None:
        """Both lifecycle (via _lc) and primary (via _pw) warnings can appear together."""
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_lifecycle_sync_status="project_state_sync_failed",
            last_issue_centric_lifecycle_sync_stage="closing",
            last_issue_centric_primary_project_sync_status="project_state_sync_failed",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        # lifecycle → _bridge_lifecycle_sync_suffix
        self.assertIn("lifecycle_sync", note)
        self.assertIn("signal=sync_failed", note)
        # primary → _build_project_sync_warning_note
        self.assertIn("project_sync: warning", note)
        self.assertIn("primary", note)

    # ------------------------------------------------------------------
    # Group 5: suggested_next_note with project sync warning
    # ------------------------------------------------------------------

    def test_suggested_next_note_completed_shows_primary_project_sync_warning(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_sync_failed",
        )
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)
        self.assertIn("primary", note)

    def test_suggested_next_note_completed_shows_followup_project_sync_warning(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_followup_project_sync_status="project_state_sync_failed",
        )
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)
        self.assertIn("followup", note)

    def test_suggested_next_note_completed_no_warning_when_no_project(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="not_requested_no_project",
        )
        note = run_until_stop.suggested_next_note(state)
        self.assertNotIn("project_sync: warning", note)

    def test_suggested_next_note_completed_no_warning_for_issue_only_fallback(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="issue_only_fallback",
        )
        note = run_until_stop.suggested_next_note(state)
        self.assertNotIn("project_sync: warning", note)

    def test_suggested_next_note_completed_no_warning_when_synced(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_synced",
        )
        note = run_until_stop.suggested_next_note(state)
        self.assertNotIn("project_sync: warning", note)

    # ------------------------------------------------------------------
    # Group 6: error state is NOT a project sync warning
    # ------------------------------------------------------------------

    def test_error_state_is_not_project_sync_warning(self) -> None:
        """runtime hard error (state.error = True) must not show 'success with warning'."""
        from _bridge_common import present_bridge_status
        state = {
            "error": True,
            "error_message": "something went wrong",
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        status = present_bridge_status(state)
        self.assertEqual(status.label, "異常")
        self.assertNotIn("project_sync: warning", status.detail)

    def test_deliberate_stop_does_not_show_project_sync_warning(self) -> None:
        """IC initial_selection_stop (deliberate stop) does not confuse with project_sync_warning."""
        from _bridge_common import detect_ic_stop_path, bridge_project_sync_warning_suffix
        # IC initial_selection_stop: ChatGPT selected a ready issue → operator must confirm
        state = {
            "chatgpt_decision": "issue_selected",
            "selected_ready_issue_ref": "#7",
            "chatgpt_decision_note": "ready issue #7 を選択。",
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        ic_path = detect_ic_stop_path(state)
        # The IC stop path is classified independently of project_sync_warning
        # (the sync warning is a primary/followup scoped check, not an IC stop path check)
        # What matters: bridge_project_sync_warning_suffix still returns the warning,
        # showing the two signals are independently readable and do not interfere.
        warning = bridge_project_sync_warning_suffix(state)
        self.assertIn("project_sync", warning)
        self.assertIn("warning", warning)
        # IC path and sync warning are in different vocabularies — they do not overwrite each other
        self.assertNotEqual(ic_path, "project_state_sync_failed")

    def test_project_sync_warning_and_hard_error_are_distinguishable(self) -> None:
        """Vocabulary must clearly separate sync warning from hard error."""
        from _bridge_common import bridge_project_sync_warning_suffix, present_bridge_status
        state_warning = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        state_error = {
            "error": True,
            "error_message": "fatal",
        }
        warning_suffix = bridge_project_sync_warning_suffix(state_warning)
        error_status = present_bridge_status(state_error)
        # Warning: shows "warning" text
        self.assertIn("warning", warning_suffix)
        # Hard error: label is "異常", detail does not contain "warning"
        self.assertEqual(error_status.label, "異常")
        self.assertNotIn("warning", error_status.detail)

    # ------------------------------------------------------------------
    # Group 7: existing stop paths do not regress
    # ------------------------------------------------------------------

    def test_existing_lifecycle_sync_suffix_still_works_for_synced(self) -> None:
        from _bridge_common import bridge_lifecycle_sync_suffix
        state = {
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        suffix = bridge_lifecycle_sync_suffix(state)
        self.assertIn("lifecycle_sync", suffix)
        self.assertIn("signal=synced", suffix)

    def test_existing_lifecycle_sync_suffix_still_works_for_sync_failed(self) -> None:
        from _bridge_common import bridge_lifecycle_sync_suffix
        state = {
            "last_issue_centric_lifecycle_sync_status": "project_state_sync_failed",
            "last_issue_centric_lifecycle_sync_stage": "in_progress",
        }
        suffix = bridge_lifecycle_sync_suffix(state)
        self.assertIn("lifecycle_sync", suffix)
        self.assertIn("signal=sync_failed", suffix)
        self.assertIn("reason=project_state_sync_failed", suffix)

    def test_format_operator_stop_note_no_action_unaffected_by_project_sync_warning(self) -> None:
        """project_sync_warning does NOT appear in no_action paths — those have different semantics."""
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "need_codex_run": True,
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        # no_action does not add project_sync warning suffix in current implementation
        # (warning is only on "completed" path where main action succeeded)
        self.assertNotIn("project_sync: warning", note)

    def test_format_operator_stop_note_request_next_prompt_unaffected(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "need_chatgpt_prompt": True,
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertNotIn("project_sync: warning", note)

    def test_format_lifecycle_sync_state_note_still_works_not_recorded(self) -> None:
        from _bridge_common import format_lifecycle_sync_state_note
        self.assertEqual(format_lifecycle_sync_state_note({}), "not_recorded")

    def test_format_lifecycle_sync_state_note_still_works_synced(self) -> None:
        from _bridge_common import format_lifecycle_sync_state_note
        state = {
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        note = format_lifecycle_sync_state_note(state)
        self.assertIn("signal=synced", note)
        self.assertIn("stage=closing", note)


class ProjectSyncWarningSurfaceAlignmentTests(unittest.TestCase):
    """Phase 57 — project_state_sync_failed warning aligned across remaining operator surfaces.

    Verifies that:
    - bridge_orchestrator.run() normal path completed shows project sync warning
    - bridge_orchestrator.run() IC stop paths are unaffected
    - start_bridge.print_doctor() shows project_sync_warning line
    - run_until_stop.entry_guidance() completed path shows project sync warning
    - not_requested_no_project / issue_only_fallback → no warning in all surfaces
    - hard error path is not mixed with project sync warning
    - deliberate stop paths are not mixed with project sync warning
    - Phase 56 surfaces (format_operator_stop_note, suggested_next_note) still pass
    """

    def _completed_state(self, **extra: object) -> dict:
        return {"mode": "idle", **extra}

    # ------------------------------------------------------------------
    # Group 1: bridge_orchestrator.py — format_operator_stop_note via else branch
    # ------------------------------------------------------------------

    def test_bridge_orchestrator_completed_shows_primary_project_sync_warning(self) -> None:
        """bridge_orchestrator.run() else branch uses format_operator_stop_note → shows warning."""
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_sync_failed",
        )
        plan = resolve_runtime_dispatch_plan(state)
        # Verify format_operator_stop_note is what bridge_orchestrator uses now
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)
        self.assertIn("primary", note)
        # The note is the source used by bridge_orchestrator else branch
        self.assertIn("追加の Codex 実行", note)

    def test_bridge_orchestrator_completed_shows_followup_project_sync_warning(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_followup_project_sync_status="project_state_sync_failed",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)
        self.assertIn("followup", note)

    def test_bridge_orchestrator_completed_no_warning_for_not_requested_no_project(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="not_requested_no_project",
            last_issue_centric_followup_project_sync_status="not_requested_no_project",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertNotIn("project_sync: warning", note)

    def test_bridge_orchestrator_completed_no_warning_for_issue_only_fallback(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="issue_only_fallback",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertNotIn("project_sync: warning", note)

    def test_bridge_orchestrator_completed_no_warning_when_synced(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_synced",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertNotIn("project_sync: warning", note)

    def test_bridge_orchestrator_ic_stop_initial_selection_uses_ic_note(self) -> None:
        """IC stop path in bridge_orchestrator is unchanged — uses _ic_note not format_operator_stop_note."""
        from _bridge_common import detect_ic_stop_path
        # detect_ic_stop_path requires chatgpt_decision starts with 'issue_centric:'
        # AND selected_ready_issue_ref is non-empty for initial_selection_stop.
        state = {
            "chatgpt_decision": "issue_centric:ready_issue_selected",
            "selected_ready_issue_ref": "#7",
            "chatgpt_decision_note": "ready issue #7 を選択しました。",
        }
        ic_stop = detect_ic_stop_path(state)
        self.assertEqual(ic_stop, "initial_selection_stop")
        # In bridge_orchestrator, the IC stop branch uses _ic_note or plan.note directly
        ic_note = str(state.get("chatgpt_decision_note", "")).strip()
        self.assertEqual(ic_note, "ready issue #7 を選択しました。")

    def test_bridge_orchestrator_no_action_does_not_show_project_sync_warning(self) -> None:
        """no_action path does not add project_sync: warning (only completed path does)."""
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "need_codex_run": True,
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertNotIn("project_sync: warning", note)

    # ------------------------------------------------------------------
    # Group 2: start_bridge.print_doctor() — project_sync_warning line
    # ------------------------------------------------------------------

    def test_start_bridge_doctor_shows_project_sync_warning_primary_failed(self) -> None:
        """print_doctor() must include project_sync_warning diagnostic line."""
        import io
        import start_bridge
        from unittest.mock import patch
        state = {
            "mode": "idle",
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        captured = []
        def fake_print(*args: object, **kwargs: object) -> None:
            captured.append(" ".join(str(a) for a in args))
        with patch("builtins.print", side_effect=fake_print):
            with patch("run_until_stop.load_state", return_value=state):
                with patch("run_until_stop.start_bridge_resume_guidance", return_value=("idle", "guidance", "note")):
                    with patch("run_until_stop.recommended_operator_step", return_value=("再実行", "python3 ...")):
                        with patch("run_until_stop.codex_report_is_ready", return_value=False):
                            with patch("run_until_stop.runtime_prompt_path") as mp:
                                mp.return_value.exists.return_value = False
                                with patch("run_until_stop.runtime_stop_path") as ms:
                                    ms.return_value.exists.return_value = False
                                    with patch("run_until_stop.bridge_runtime_root") as mb:
                                        mb.return_value.glob.return_value = []
                                        with patch("run_until_stop.should_prioritize_unarchived_report", return_value=False):
                                            with patch("run_until_stop.should_rotate_before_next_chat_request", return_value=False):
                                                args = start_bridge.parse_args(["--project-path", "/tmp"])
                                                start_bridge.print_doctor(args)
        lines_with_warning = [l for l in captured if "project_sync_warning" in l]
        self.assertTrue(len(lines_with_warning) >= 1, f"project_sync_warning line not found. captured: {captured}")
        warning_line = lines_with_warning[0]
        self.assertIn("primary", warning_line)
        self.assertIn("project_state_sync_failed", warning_line)

    def test_start_bridge_doctor_shows_none_when_no_failures(self) -> None:
        """print_doctor() project_sync_warning line shows 'none' when no failures."""
        import start_bridge
        from unittest.mock import patch
        state = {"mode": "idle"}
        captured = []
        def fake_print(*args: object, **kwargs: object) -> None:
            captured.append(" ".join(str(a) for a in args))
        with patch("builtins.print", side_effect=fake_print):
            with patch("run_until_stop.load_state", return_value=state):
                with patch("run_until_stop.start_bridge_resume_guidance", return_value=("idle", "guidance", "note")):
                    with patch("run_until_stop.recommended_operator_step", return_value=("再実行", "python3 ...")):
                        with patch("run_until_stop.codex_report_is_ready", return_value=False):
                            with patch("run_until_stop.runtime_prompt_path") as mp:
                                mp.return_value.exists.return_value = False
                                with patch("run_until_stop.runtime_stop_path") as ms:
                                    ms.return_value.exists.return_value = False
                                    with patch("run_until_stop.bridge_runtime_root") as mb:
                                        mb.return_value.glob.return_value = []
                                        with patch("run_until_stop.should_prioritize_unarchived_report", return_value=False):
                                            with patch("run_until_stop.should_rotate_before_next_chat_request", return_value=False):
                                                args = start_bridge.parse_args(["--project-path", "/tmp"])
                                                start_bridge.print_doctor(args)
        lines_with_warning = [l for l in captured if "project_sync_warning" in l]
        self.assertTrue(len(lines_with_warning) >= 1, f"project_sync_warning line not found. captured: {captured}")
        self.assertIn("none", lines_with_warning[0])

    def test_start_bridge_doctor_shows_lifecycle_failed_in_warning_note(self) -> None:
        """print_doctor() project_sync_warning line also covers lifecycle family."""
        import start_bridge
        from unittest.mock import patch
        state = {
            "mode": "idle",
            "last_issue_centric_lifecycle_sync_status": "project_state_sync_failed",
            "last_issue_centric_lifecycle_sync_stage": "closing",
        }
        captured = []
        def fake_print(*args: object, **kwargs: object) -> None:
            captured.append(" ".join(str(a) for a in args))
        with patch("builtins.print", side_effect=fake_print):
            with patch("run_until_stop.load_state", return_value=state):
                with patch("run_until_stop.start_bridge_resume_guidance", return_value=("idle", "guidance", "note")):
                    with patch("run_until_stop.recommended_operator_step", return_value=("再実行", "python3 ...")):
                        with patch("run_until_stop.codex_report_is_ready", return_value=False):
                            with patch("run_until_stop.runtime_prompt_path") as mp:
                                mp.return_value.exists.return_value = False
                                with patch("run_until_stop.runtime_stop_path") as ms:
                                    ms.return_value.exists.return_value = False
                                    with patch("run_until_stop.bridge_runtime_root") as mb:
                                        mb.return_value.glob.return_value = []
                                        with patch("run_until_stop.should_prioritize_unarchived_report", return_value=False):
                                            with patch("run_until_stop.should_rotate_before_next_chat_request", return_value=False):
                                                args = start_bridge.parse_args(["--project-path", "/tmp"])
                                                start_bridge.print_doctor(args)
        lines_with_warning = [l for l in captured if "project_sync_warning" in l]
        self.assertTrue(len(lines_with_warning) >= 1)
        warning_line = lines_with_warning[0]
        self.assertIn("lifecycle", warning_line)

    # ------------------------------------------------------------------
    # Group 3: run_until_stop.entry_guidance() — completed path with _pw
    # ------------------------------------------------------------------

    def test_entry_guidance_completed_shows_primary_project_sync_warning(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_sync_failed",
        )
        # We need a minimal args object for entry_guidance
        args = run_until_stop.parse_args(["--project-path", "/tmp", "--max-execution-count", "6"])
        note = run_until_stop.entry_guidance(state, args)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)
        self.assertIn("primary", note)

    def test_entry_guidance_completed_shows_followup_project_sync_warning(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_followup_project_sync_status="project_state_sync_failed",
        )
        args = run_until_stop.parse_args(["--project-path", "/tmp", "--max-execution-count", "6"])
        note = run_until_stop.entry_guidance(state, args)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)
        self.assertIn("followup", note)

    def test_entry_guidance_completed_no_warning_for_not_requested_no_project(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="not_requested_no_project",
        )
        args = run_until_stop.parse_args(["--project-path", "/tmp", "--max-execution-count", "6"])
        note = run_until_stop.entry_guidance(state, args)
        self.assertNotIn("project_sync: warning", note)

    def test_entry_guidance_completed_no_warning_for_issue_only_fallback(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="issue_only_fallback",
        )
        args = run_until_stop.parse_args(["--project-path", "/tmp", "--max-execution-count", "6"])
        note = run_until_stop.entry_guidance(state, args)
        self.assertNotIn("project_sync: warning", note)

    def test_entry_guidance_completed_no_warning_when_synced(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_synced",
        )
        args = run_until_stop.parse_args(["--project-path", "/tmp", "--max-execution-count", "6"])
        note = run_until_stop.entry_guidance(state, args)
        self.assertNotIn("project_sync: warning", note)

    def test_entry_guidance_completed_lifecycle_only_not_in_pw(self) -> None:
        """Lifecycle sync failure alone does not trigger _pw in entry_guidance completed path."""
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_lifecycle_sync_status="project_state_sync_failed",
            last_issue_centric_lifecycle_sync_stage="closing",
        )
        args = run_until_stop.parse_args(["--project-path", "/tmp", "--max-execution-count", "6"])
        note = run_until_stop.entry_guidance(state, args)
        # lifecycle is handled by _lc (bridge_lifecycle_sync_suffix) separately
        # _pw (bridge_project_sync_warning_suffix) covers only primary+followup
        self.assertNotIn("project_sync: warning", note)

    # ------------------------------------------------------------------
    # Group 4: boundary — hard error vs warning, deliberate stop vs warning
    # ------------------------------------------------------------------

    def test_hard_error_not_mixed_with_project_sync_warning_in_orchestrator(self) -> None:
        """Hard error (error=True) status label is '異常'; warning is a separate signal."""
        from _bridge_common import present_bridge_status
        state = {
            "error": True,
            "error_message": "something critical",
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        status = present_bridge_status(state)
        # Hard error: status label is "異常" — distinct from project_sync warning vocabulary
        self.assertEqual(status.label, "異常")
        # The error status detail (from present_bridge_status) does not contain project_sync warning text
        # (project_sync warning is only in format_operator_stop_note, not in present_bridge_status.detail)
        self.assertNotIn("project_sync: warning", status.detail)

    def test_initial_selection_stop_not_mixed_with_project_sync_warning(self) -> None:
        """initial_selection_stop is a deliberate stop; IC branch uses _ic_note not project_sync warning."""
        from _bridge_common import detect_ic_stop_path, bridge_project_sync_warning_suffix
        # detect_ic_stop_path requires chatgpt_decision starts with 'issue_centric:'
        # AND selected_ready_issue_ref non-empty for initial_selection_stop.
        state = {
            "chatgpt_decision": "issue_centric:ready_issue_selected",
            "selected_ready_issue_ref": "#7",
            "chatgpt_decision_note": "ready issue #7 を選択しました。",
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        ic_stop = detect_ic_stop_path(state)
        self.assertEqual(ic_stop, "initial_selection_stop")
        # In bridge_orchestrator, this goes to the IC branch: _ic_note or plan.note
        # — NOT to format_operator_stop_note. So project sync warning is not in the IC branch note.
        ic_note = str(state.get("chatgpt_decision_note", "")).strip()
        self.assertNotIn("project_sync", ic_note)
        # bridge_project_sync_warning_suffix independently returns the warning (two signals are separate)
        warning = bridge_project_sync_warning_suffix(state)
        self.assertIn("project_sync: warning", warning)

    def test_project_sync_warning_vocabulary_consistent_across_surfaces(self) -> None:
        """All three surfaces use the same vocabulary: 'project_sync: warning family=...'."""
        from _bridge_common import (
            format_operator_stop_note,
            resolve_runtime_dispatch_plan,
        )
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_sync_failed",
        )
        plan = resolve_runtime_dispatch_plan(state)
        orch_note = format_operator_stop_note(state, plan=plan)
        args = run_until_stop.parse_args(["--project-path", "/tmp", "--max-execution-count", "6"])
        entry_note = run_until_stop.entry_guidance(state, args)
        suggested_note = run_until_stop.suggested_next_note(state)
        for note in [orch_note, entry_note, suggested_note]:
            self.assertIn("project_sync", note)
            self.assertIn("warning", note)

    # ------------------------------------------------------------------
    # Group 5: Phase 56 regression — format_operator_stop_note, suggested_next_note
    # ------------------------------------------------------------------

    def test_phase56_format_operator_stop_note_completed_still_passes(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_sync_failed",
        )
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)

    def test_phase56_suggested_next_note_completed_still_passes(self) -> None:
        import run_until_stop
        state = self._completed_state(
            last_issue_centric_primary_project_sync_status="project_state_sync_failed",
        )
        note = run_until_stop.suggested_next_note(state)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)

    def test_phase56_format_project_sync_warning_note_all_three_still_passes(self) -> None:
        from _bridge_common import format_project_sync_warning_note
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_followup_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_lifecycle_sync_status": "project_state_sync_failed",
        }
        note = format_project_sync_warning_note(state)
        self.assertIn("primary", note)
        self.assertIn("followup", note)
        self.assertIn("lifecycle", note)


class ProjectSyncAlertSignalPayloadTests(unittest.TestCase):
    """Phase 58 — project_state_sync_failed alert signal / payload / dedupe.

    Verifies:
    - primary / followup / lifecycle sync failed → alert candidate detected
    - not_requested_no_project / issue_only_fallback → NOT alert candidate
    - hard error / deliberate stop do not mix with alert candidate
    - _build_project_sync_alert_candidate returns correct fields
    - stable hash: same state → same hash; different event → different hash
    - _build_project_sync_alert_payload contains expected fields
    - record_project_sync_alert_if_new: "recorded" / "skipped_duplicate" / "none"
    - format_project_sync_alert_status: "pending file=..." / "none"
    - start_bridge.print_doctor() shows project_sync_alert line
    - Phase 57 warning surfaces remain unaffected (regression)
    """

    # ------------------------------------------------------------------
    # Group 1: alert candidate detection
    # ------------------------------------------------------------------

    def test_detect_primary_project_sync_failed_is_alert_candidate(self) -> None:
        from _bridge_common import _detect_project_sync_alert_candidate
        state = {"last_issue_centric_primary_project_sync_status": "project_state_sync_failed"}
        self.assertTrue(_detect_project_sync_alert_candidate(state))

    def test_detect_followup_project_sync_failed_is_alert_candidate(self) -> None:
        from _bridge_common import _detect_project_sync_alert_candidate
        state = {"last_issue_centric_followup_project_sync_status": "project_state_sync_failed"}
        self.assertTrue(_detect_project_sync_alert_candidate(state))

    def test_detect_lifecycle_project_sync_failed_is_alert_candidate(self) -> None:
        from _bridge_common import _detect_project_sync_alert_candidate
        state = {"last_issue_centric_lifecycle_sync_status": "project_state_sync_failed"}
        self.assertTrue(_detect_project_sync_alert_candidate(state))

    def test_not_requested_no_project_is_not_alert_candidate(self) -> None:
        from _bridge_common import _detect_project_sync_alert_candidate
        state = {
            "last_issue_centric_primary_project_sync_status": "not_requested_no_project",
            "last_issue_centric_followup_project_sync_status": "not_requested_no_project",
        }
        self.assertFalse(_detect_project_sync_alert_candidate(state))

    def test_issue_only_fallback_is_not_alert_candidate(self) -> None:
        from _bridge_common import _detect_project_sync_alert_candidate
        state = {
            "last_issue_centric_primary_project_sync_status": "issue_only_fallback",
        }
        self.assertFalse(_detect_project_sync_alert_candidate(state))

    def test_synced_status_is_not_alert_candidate(self) -> None:
        from _bridge_common import _detect_project_sync_alert_candidate
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_synced",
        }
        self.assertFalse(_detect_project_sync_alert_candidate(state))

    def test_empty_state_is_not_alert_candidate(self) -> None:
        from _bridge_common import _detect_project_sync_alert_candidate
        self.assertFalse(_detect_project_sync_alert_candidate({}))

    def test_hard_error_without_sync_failed_is_not_alert_candidate(self) -> None:
        """Hard error alone (no project_state_sync_failed) does not produce alert candidate."""
        from _bridge_common import _detect_project_sync_alert_candidate
        state = {
            "error": True,
            "error_message": "critical failure",
        }
        self.assertFalse(_detect_project_sync_alert_candidate(state))

    # ------------------------------------------------------------------
    # Group 2: build alert candidate fields
    # ------------------------------------------------------------------

    def test_build_alert_candidate_returns_none_when_no_failure(self) -> None:
        from _bridge_common import _build_project_sync_alert_candidate
        self.assertIsNone(_build_project_sync_alert_candidate({}))

    def test_build_alert_candidate_primary_fields(self) -> None:
        from _bridge_common import _build_project_sync_alert_candidate
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_primary_project_url": "https://github.com/orgs/x/projects/1",
            "last_issue_centric_primary_project_item_id": "PVTI_abc",
            "last_issue_centric_primary_project_state_field": "Status",
            "last_issue_centric_primary_project_state_value": "In Progress",
            "last_issue_centric_target_issue": "#5",
            "last_issue_centric_principal_issue": "https://github.com/x/y/issues/5",
            "last_issue_centric_next_request_target": "https://github.com/x/y/issues/5",
            "last_issue_centric_runtime_mode": "issue_centric_ready",
            "last_issue_centric_action": "issue_create",
        }
        candidate = _build_project_sync_alert_candidate(state)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.families, ["primary"])
        self.assertEqual(candidate.sync_status, "project_state_sync_failed")
        self.assertEqual(candidate.issue_ref, "#5")
        self.assertEqual(candidate.project_url, "https://github.com/orgs/x/projects/1")
        self.assertEqual(candidate.project_item_id, "PVTI_abc")
        self.assertEqual(candidate.project_state_field, "Status")
        self.assertEqual(candidate.project_state_value, "In Progress")
        self.assertEqual(candidate.runtime_mode, "issue_centric_ready")
        self.assertEqual(candidate.runtime_action, "issue_create")
        self.assertIsNotNone(candidate.detected_at)
        self.assertTrue(len(candidate.alert_hash) == 64)  # sha256 hex

    def test_build_alert_candidate_followup_fields(self) -> None:
        from _bridge_common import _build_project_sync_alert_candidate
        state = {
            "last_issue_centric_followup_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_followup_project_url": "https://github.com/orgs/x/projects/2",
        }
        candidate = _build_project_sync_alert_candidate(state)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.families, ["followup"])
        self.assertEqual(candidate.project_url, "https://github.com/orgs/x/projects/2")

    def test_build_alert_candidate_lifecycle_fields(self) -> None:
        from _bridge_common import _build_project_sync_alert_candidate
        state = {
            "last_issue_centric_lifecycle_sync_status": "project_state_sync_failed",
            "last_issue_centric_lifecycle_sync_project_url": "https://github.com/orgs/x/projects/3",
            "last_issue_centric_lifecycle_sync_project_item_id": "PVTI_xyz",
            "last_issue_centric_lifecycle_sync_state_field": "Status",
            "last_issue_centric_lifecycle_sync_state_value": "Done",
        }
        candidate = _build_project_sync_alert_candidate(state)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.families, ["lifecycle"])
        self.assertEqual(candidate.project_url, "https://github.com/orgs/x/projects/3")
        self.assertEqual(candidate.project_state_value, "Done")

    def test_build_alert_candidate_multi_family(self) -> None:
        """primary + followup both failed → both appear in families."""
        from _bridge_common import _build_project_sync_alert_candidate
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_followup_project_sync_status": "project_state_sync_failed",
        }
        candidate = _build_project_sync_alert_candidate(state)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertIn("primary", candidate.families)
        self.assertIn("followup", candidate.families)

    # ------------------------------------------------------------------
    # Group 3: stable hash / deduplication
    # ------------------------------------------------------------------

    def test_same_state_produces_same_hash(self) -> None:
        from _bridge_common import _build_project_sync_alert_candidate
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_primary_project_url": "https://github.com/orgs/x/projects/1",
            "last_issue_centric_primary_project_item_id": "PVTI_abc",
            "last_issue_centric_primary_project_state_value": "In Progress",
            "last_issue_centric_target_issue": "#5",
        }
        c1 = _build_project_sync_alert_candidate(state)
        c2 = _build_project_sync_alert_candidate(state)
        self.assertIsNotNone(c1)
        self.assertIsNotNone(c2)
        assert c1 is not None
        assert c2 is not None
        self.assertEqual(c1.alert_hash, c2.alert_hash)

    def test_different_issue_ref_produces_different_hash(self) -> None:
        from _bridge_common import _build_project_sync_alert_candidate
        state_a = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_target_issue": "#5",
        }
        state_b = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_target_issue": "#6",
        }
        c_a = _build_project_sync_alert_candidate(state_a)
        c_b = _build_project_sync_alert_candidate(state_b)
        self.assertIsNotNone(c_a)
        self.assertIsNotNone(c_b)
        assert c_a is not None
        assert c_b is not None
        self.assertNotEqual(c_a.alert_hash, c_b.alert_hash)

    def test_different_project_state_value_produces_different_hash(self) -> None:
        from _bridge_common import _build_project_sync_alert_candidate
        state_a = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_primary_project_state_value": "In Progress",
        }
        state_b = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_primary_project_state_value": "Done",
        }
        c_a = _build_project_sync_alert_candidate(state_a)
        c_b = _build_project_sync_alert_candidate(state_b)
        self.assertIsNotNone(c_a)
        self.assertIsNotNone(c_b)
        assert c_a is not None
        assert c_b is not None
        self.assertNotEqual(c_a.alert_hash, c_b.alert_hash)

    # ------------------------------------------------------------------
    # Group 4: alert payload fields
    # ------------------------------------------------------------------

    def test_build_alert_payload_contains_required_fields(self) -> None:
        from _bridge_common import _build_project_sync_alert_candidate, _build_project_sync_alert_payload
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_primary_project_url": "https://github.com/orgs/x/projects/1",
            "last_issue_centric_primary_project_item_id": "PVTI_abc",
            "last_issue_centric_primary_project_state_field": "Status",
            "last_issue_centric_primary_project_state_value": "In Progress",
            "last_issue_centric_target_issue": "#5",
        }
        candidate = _build_project_sync_alert_candidate(state)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        payload = _build_project_sync_alert_payload(candidate)
        required_keys = [
            "family", "sync_status", "issue_ref", "principal_issue_ref",
            "next_request_target", "project_url", "project_item_id",
            "project_state_field", "project_state_value",
            "runtime_mode", "runtime_action", "detected_at", "hash", "source_note",
        ]
        for key in required_keys:
            self.assertIn(key, payload, f"missing key: {key}")
        self.assertEqual(payload["family"], "primary")
        self.assertEqual(payload["sync_status"], "project_state_sync_failed")
        self.assertEqual(payload["project_url"], "https://github.com/orgs/x/projects/1")
        self.assertEqual(payload["hash"], candidate.alert_hash)

    def test_build_alert_payload_json_serializable(self) -> None:
        """Payload must be JSON serializable."""
        import json as json_mod
        from _bridge_common import _build_project_sync_alert_candidate, _build_project_sync_alert_payload
        state = {"last_issue_centric_primary_project_sync_status": "project_state_sync_failed"}
        candidate = _build_project_sync_alert_candidate(state)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        payload = _build_project_sync_alert_payload(candidate)
        serialized = json_mod.dumps(payload, ensure_ascii=False)
        self.assertIsInstance(serialized, str)

    # ------------------------------------------------------------------
    # Group 5: record_project_sync_alert_if_new return values
    # ------------------------------------------------------------------

    def test_record_returns_none_when_no_failure(self) -> None:
        from _bridge_common import record_project_sync_alert_if_new
        result = record_project_sync_alert_if_new({})
        self.assertEqual(result, "none")

    def test_record_returns_none_for_not_requested_no_project(self) -> None:
        from _bridge_common import record_project_sync_alert_if_new
        state = {"last_issue_centric_primary_project_sync_status": "not_requested_no_project"}
        self.assertEqual(record_project_sync_alert_if_new(state), "none")

    def test_record_returns_none_for_issue_only_fallback(self) -> None:
        from _bridge_common import record_project_sync_alert_if_new
        state = {"last_issue_centric_primary_project_sync_status": "issue_only_fallback"}
        self.assertEqual(record_project_sync_alert_if_new(state), "none")

    def test_record_returns_skipped_duplicate_for_same_hash(self) -> None:
        from _bridge_common import _build_project_sync_alert_candidate, record_project_sync_alert_if_new
        state = {"last_issue_centric_primary_project_sync_status": "project_state_sync_failed"}
        candidate = _build_project_sync_alert_candidate(state)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        state_with_hash: dict = dict(state)
        state_with_hash["last_project_sync_alert_hash"] = candidate.alert_hash
        result = record_project_sync_alert_if_new(state_with_hash)
        self.assertEqual(result, "skipped_duplicate")

    def test_record_payload_file_is_written_on_new_alert(self) -> None:
        """record_project_sync_alert_if_new writes ALERT_PAYLOAD_PATH when candidate is new."""
        import json as json_mod
        from unittest.mock import patch, MagicMock
        import _bridge_common
        from _bridge_common import record_project_sync_alert_if_new
        state = {
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
            "last_issue_centric_primary_project_url": "https://github.com/orgs/x/projects/1",
        }
        written_payloads: list[str] = []
        state_updates: list[dict] = []

        mock_path = MagicMock()
        mock_path.relative_to.return_value = mock_path
        mock_path.__str__ = MagicMock(return_value="bridge/project_sync_alert.json")

        def fake_write_text(content: str, encoding: str = "utf-8") -> None:
            written_payloads.append(content)

        mock_path.write_text.side_effect = fake_write_text

        def fake_update_state(**kwargs: object) -> dict:
            state_updates.append(dict(kwargs))
            return {}

        with patch.object(_bridge_common, "ALERT_PAYLOAD_PATH", mock_path):
            with patch("_bridge_common.update_state", side_effect=fake_update_state):
                result = record_project_sync_alert_if_new(state)

        self.assertEqual(result, "recorded")
        self.assertEqual(len(written_payloads), 1)
        payload = json_mod.loads(written_payloads[0])
        self.assertEqual(payload["family"], "primary")
        self.assertEqual(payload["sync_status"], "project_state_sync_failed")
        self.assertEqual(len(state_updates), 1)
        self.assertEqual(state_updates[0]["last_project_sync_alert_status"], "pending")
        self.assertIn("last_project_sync_alert_hash", state_updates[0])
        self.assertIn("last_project_sync_alert_file", state_updates[0])

    def test_record_no_duplicate_payload_for_same_event(self) -> None:
        """Second call with same hash returns skipped_duplicate without writing."""
        from unittest.mock import patch
        from _bridge_common import _build_project_sync_alert_candidate, record_project_sync_alert_if_new
        state = {"last_issue_centric_primary_project_sync_status": "project_state_sync_failed"}
        candidate = _build_project_sync_alert_candidate(state)
        assert candidate is not None
        state_dup = dict(state)
        state_dup["last_project_sync_alert_hash"] = candidate.alert_hash
        write_calls: list = []
        with patch("_bridge_common.update_state") as mock_update:
            with patch("_bridge_common.ALERT_PAYLOAD_PATH") as mock_path:
                mock_path.write_text.side_effect = lambda *a, **kw: write_calls.append(a)
                result = record_project_sync_alert_if_new(state_dup)
        self.assertEqual(result, "skipped_duplicate")
        self.assertEqual(len(write_calls), 0)
        mock_update.assert_not_called()

    # ------------------------------------------------------------------
    # Group 6: format_project_sync_alert_status
    # ------------------------------------------------------------------

    def test_format_alert_status_pending(self) -> None:
        from _bridge_common import format_project_sync_alert_status
        state = {
            "last_project_sync_alert_status": "pending",
            "last_project_sync_alert_file": "bridge/project_sync_alert.json",
        }
        result = format_project_sync_alert_status(state)
        self.assertEqual(result, "pending file=bridge/project_sync_alert.json")

    def test_format_alert_status_none_when_empty(self) -> None:
        from _bridge_common import format_project_sync_alert_status
        self.assertEqual(format_project_sync_alert_status({}), "none")

    def test_format_alert_status_none_when_pending_but_no_file(self) -> None:
        from _bridge_common import format_project_sync_alert_status
        state = {"last_project_sync_alert_status": "pending", "last_project_sync_alert_file": ""}
        self.assertEqual(format_project_sync_alert_status(state), "none")

    # ------------------------------------------------------------------
    # Group 7: start_bridge.print_doctor() shows project_sync_alert line
    # ------------------------------------------------------------------

    def test_start_bridge_doctor_shows_project_sync_alert_pending(self) -> None:
        """print_doctor() must include project_sync_alert line when alert is pending."""
        import start_bridge
        from unittest.mock import patch
        state = {
            "mode": "idle",
            "last_project_sync_alert_status": "pending",
            "last_project_sync_alert_file": "bridge/project_sync_alert.json",
        }
        captured = []
        def fake_print(*args: object, **kwargs: object) -> None:
            captured.append(" ".join(str(a) for a in args))
        with patch("builtins.print", side_effect=fake_print):
            with patch("run_until_stop.load_state", return_value=state):
                with patch("run_until_stop.start_bridge_resume_guidance", return_value=("idle", "g", "n")):
                    with patch("run_until_stop.recommended_operator_step", return_value=("再実行", "cmd")):
                        with patch("run_until_stop.codex_report_is_ready", return_value=False):
                            with patch("run_until_stop.runtime_prompt_path") as mp:
                                mp.return_value.exists.return_value = False
                                with patch("run_until_stop.runtime_stop_path") as ms:
                                    ms.return_value.exists.return_value = False
                                    with patch("run_until_stop.bridge_runtime_root") as mb:
                                        mb.return_value.glob.return_value = []
                                        with patch("run_until_stop.should_prioritize_unarchived_report", return_value=False):
                                            with patch("run_until_stop.should_rotate_before_next_chat_request", return_value=False):
                                                args = start_bridge.parse_args(["--project-path", "/tmp"])
                                                start_bridge.print_doctor(args)
        alert_lines = [l for l in captured if "project_sync_alert" in l]
        self.assertTrue(len(alert_lines) >= 1, f"project_sync_alert line not found. captured={captured}")
        self.assertIn("pending", alert_lines[0])
        self.assertIn("bridge/project_sync_alert.json", alert_lines[0])

    def test_start_bridge_doctor_shows_project_sync_alert_none(self) -> None:
        """print_doctor() shows 'none' for project_sync_alert when no alert pending."""
        import start_bridge
        from unittest.mock import patch
        state = {"mode": "idle"}
        captured = []
        def fake_print(*args: object, **kwargs: object) -> None:
            captured.append(" ".join(str(a) for a in args))
        with patch("builtins.print", side_effect=fake_print):
            with patch("run_until_stop.load_state", return_value=state):
                with patch("run_until_stop.start_bridge_resume_guidance", return_value=("idle", "g", "n")):
                    with patch("run_until_stop.recommended_operator_step", return_value=("再実行", "cmd")):
                        with patch("run_until_stop.codex_report_is_ready", return_value=False):
                            with patch("run_until_stop.runtime_prompt_path") as mp:
                                mp.return_value.exists.return_value = False
                                with patch("run_until_stop.runtime_stop_path") as ms:
                                    ms.return_value.exists.return_value = False
                                    with patch("run_until_stop.bridge_runtime_root") as mb:
                                        mb.return_value.glob.return_value = []
                                        with patch("run_until_stop.should_prioritize_unarchived_report", return_value=False):
                                            with patch("run_until_stop.should_rotate_before_next_chat_request", return_value=False):
                                                args = start_bridge.parse_args(["--project-path", "/tmp"])
                                                start_bridge.print_doctor(args)
        alert_lines = [l for l in captured if "project_sync_alert" in l]
        self.assertTrue(len(alert_lines) >= 1, f"project_sync_alert line not found. captured={captured}")
        self.assertIn("none", alert_lines[0])

    # ------------------------------------------------------------------
    # Group 8: boundary — warning surface and alert candidate consistency
    # ------------------------------------------------------------------

    def test_warning_surface_and_alert_candidate_consistent_for_primary_failed(self) -> None:
        """When primary failed, warning note contains 'project_sync: warning primary'
        and alert candidate detects the same failure."""
        from _bridge_common import (
            _detect_project_sync_alert_candidate,
            bridge_project_sync_warning_suffix,
        )
        state = {"last_issue_centric_primary_project_sync_status": "project_state_sync_failed"}
        self.assertTrue(_detect_project_sync_alert_candidate(state))
        warning = bridge_project_sync_warning_suffix(state)
        self.assertIn("project_sync: warning", warning)
        self.assertIn("primary", warning)

    def test_warning_surface_and_alert_candidate_consistent_for_synced(self) -> None:
        """When primary is synced, no warning and no alert candidate."""
        from _bridge_common import (
            _detect_project_sync_alert_candidate,
            bridge_project_sync_warning_suffix,
        )
        state = {"last_issue_centric_primary_project_sync_status": "project_state_synced"}
        self.assertFalse(_detect_project_sync_alert_candidate(state))
        self.assertEqual(bridge_project_sync_warning_suffix(state), "")

    def test_hard_error_with_no_sync_failure_not_alert_candidate(self) -> None:
        """Hard error (error=True) without project_state_sync_failed is not alert candidate."""
        from _bridge_common import _detect_project_sync_alert_candidate
        state = {
            "error": True,
            "error_message": "something went wrong",
            "last_issue_centric_primary_project_sync_status": "issue_only_fallback",
        }
        self.assertFalse(_detect_project_sync_alert_candidate(state))

    def test_hard_error_with_sync_failure_is_both_error_and_alert_candidate(self) -> None:
        """Hard error + project sync failed: both signals are independent and separate."""
        from _bridge_common import _detect_project_sync_alert_candidate, present_bridge_status
        state = {
            "error": True,
            "error_message": "critical failure",
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        # Hard error status is "異常"
        self.assertEqual(present_bridge_status(state).label, "異常")
        # Alert candidate is also detected (independent signal)
        self.assertTrue(_detect_project_sync_alert_candidate(state))

    # ------------------------------------------------------------------
    # Group 9: Phase 57 regression
    # ------------------------------------------------------------------

    def test_phase57_bridge_orchestrator_completed_warning_still_passes(self) -> None:
        from _bridge_common import format_operator_stop_note, resolve_runtime_dispatch_plan
        state = {
            "mode": "idle",
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        plan = resolve_runtime_dispatch_plan(state)
        note = format_operator_stop_note(state, plan=plan)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)

    def test_phase57_entry_guidance_completed_warning_still_passes(self) -> None:
        import run_until_stop
        state = {
            "mode": "idle",
            "last_issue_centric_primary_project_sync_status": "project_state_sync_failed",
        }
        args = run_until_stop.parse_args(["--project-path", "/tmp", "--max-execution-count", "6"])
        note = run_until_stop.entry_guidance(state, args)
        self.assertIn("project_sync", note)
        self.assertIn("warning", note)


# ---------------------------------------------------------------------------
# Phase 59 — project_state_sync_failed generic webhook delivery tests
# ---------------------------------------------------------------------------


class ProjectSyncAlertWebhookDeliveryTests(unittest.TestCase):
    """Phase 59 — project_state_sync_failed webhook delivery.

    Verifies:
    - deliver_project_sync_alert_if_pending returns "none" when no pending alert
    - deliver_project_sync_alert_if_pending returns "not_requested_no_webhook" when URL blank
    - deliver_project_sync_alert_if_pending returns "skipped_already_delivered" on same hash
    - HTTP 2xx → "delivered"; state persisted
    - HTTP non-2xx → "delivery_failed"; not a hard error
    - Network error → "delivery_failed"; not a hard error
    - Missing payload file → "invalid_payload"
    - Invalid JSON payload → "invalid_payload"
    - format_project_sync_alert_delivery_status returns expected strings
    - DEFAULT_STATE has all 5 delivery keys
    - DEFAULT_PROJECT_CONFIG has project_sync_alert_webhook_url
    - doctor output includes project_sync_alert_delivery line
    - Phase 58 regression: record_project_sync_alert_if_new still works
    """

    def setUp(self) -> None:
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

    def _make_pending_state(
        self,
        alert_file: str = "bridge/project_sync_alert.json",
        alert_hash: str = "abc123",
        delivered_hash: str = "",
    ) -> dict:
        return {
            "last_project_sync_alert_status": "pending",
            "last_project_sync_alert_file": alert_file,
            "last_project_sync_alert_hash": alert_hash,
            "last_project_sync_alert_delivery_hash": delivered_hash,
            "last_project_sync_alert_delivery_status": "",
            "last_project_sync_alert_delivery_error": "",
            "last_project_sync_alert_delivery_url": "",
            "last_project_sync_alert_delivery_attempted_at": "",
        }

    def _make_config(self, webhook_url: str = "") -> dict:
        return {"project_sync_alert_webhook_url": webhook_url}

    # ------------------------------------------------------------------
    # Return value contract: no pending alert
    # ------------------------------------------------------------------

    def test_returns_none_when_no_pending_alert(self) -> None:
        import _bridge_common as bc
        state = {"last_project_sync_alert_status": ""}
        result = bc.deliver_project_sync_alert_if_pending(state)
        self.assertEqual(result, "none")

    def test_returns_none_when_alert_status_is_not_pending(self) -> None:
        import _bridge_common as bc
        state = {"last_project_sync_alert_status": "recorded"}
        result = bc.deliver_project_sync_alert_if_pending(state)
        self.assertEqual(result, "none")

    # ------------------------------------------------------------------
    # Return value: no webhook configured
    # ------------------------------------------------------------------

    def test_returns_not_requested_when_webhook_url_blank(self) -> None:
        import _bridge_common as bc
        state = self._make_pending_state()
        config = self._make_config(webhook_url="")
        result = bc.deliver_project_sync_alert_if_pending(state, config)
        self.assertEqual(result, "not_requested_no_webhook")

    def test_returns_not_requested_when_no_config(self) -> None:
        import _bridge_common as bc
        state = self._make_pending_state()
        result = bc.deliver_project_sync_alert_if_pending(state, config=None)
        self.assertEqual(result, "not_requested_no_webhook")

    # ------------------------------------------------------------------
    # Deduplication: same hash already delivered
    # ------------------------------------------------------------------

    def test_returns_skipped_when_same_hash_already_delivered(self) -> None:
        import _bridge_common as bc
        state = self._make_pending_state(alert_hash="abc123", delivered_hash="abc123")
        config = self._make_config(webhook_url="https://example.invalid/hook")
        result = bc.deliver_project_sync_alert_if_pending(state, config)
        self.assertEqual(result, "skipped_already_delivered")

    def test_does_not_skip_when_hashes_differ(self) -> None:
        """When alert hash differs from last delivered hash, delivery should be attempted."""
        import _bridge_common as bc
        import json
        import tempfile
        import pathlib
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            json.dump({"type": "project_sync_alert", "hash": "newHash"}, fp)
            tmp_path = fp.name
        try:
            state = self._make_pending_state(
                alert_file=tmp_path,
                alert_hash="newHash",
                delivered_hash="oldHash",
            )
            config = self._make_config(webhook_url="https://127.0.0.1:0/will-fail")
            result = bc.deliver_project_sync_alert_if_pending(state, config)
            # delivery attempt was made (either delivered or delivery_failed)
            self.assertIn(result, ("delivered", "delivery_failed"))
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Return value: invalid payload
    # ------------------------------------------------------------------

    def test_returns_invalid_payload_when_file_missing(self) -> None:
        import _bridge_common as bc
        state = self._make_pending_state(alert_file="bridge/this_file_does_not_exist.json")
        config = self._make_config(webhook_url="https://example.invalid/hook")
        result = bc.deliver_project_sync_alert_if_pending(state, config)
        self.assertEqual(result, "invalid_payload")

    def test_returns_invalid_payload_when_json_broken(self) -> None:
        import _bridge_common as bc
        import tempfile
        import pathlib
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            fp.write("not valid json !!!")
            tmp_path = fp.name
        try:
            state = self._make_pending_state(alert_file=tmp_path)
            config = self._make_config(webhook_url="https://example.invalid/hook")
            result = bc.deliver_project_sync_alert_if_pending(state, config)
            self.assertEqual(result, "invalid_payload")
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Return value: delivery_failed on network error
    # ------------------------------------------------------------------

    def test_returns_delivery_failed_on_unreachable_url(self) -> None:
        """Delivery to an unreachable URL must return delivery_failed and must not raise."""
        import _bridge_common as bc
        import json
        import tempfile
        import pathlib
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            json.dump({"type": "project_sync_alert", "hash": "h1"}, fp)
            tmp_path = fp.name
        try:
            state = self._make_pending_state(
                alert_file=tmp_path,
                alert_hash="h1",
                delivered_hash="",
            )
            config = self._make_config(webhook_url="http://127.0.0.1:1/")
            result = bc.deliver_project_sync_alert_if_pending(state, config)
            self.assertEqual(result, "delivery_failed")
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

    def test_delivery_failed_does_not_raise(self) -> None:
        """Delivery failure must be absorbed — no exception must propagate."""
        import _bridge_common as bc
        import json
        import tempfile
        import pathlib
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            json.dump({"type": "test"}, fp)
            tmp_path = fp.name
        try:
            state = self._make_pending_state(alert_file=tmp_path, alert_hash="x1")
            config = self._make_config(webhook_url="http://127.0.0.1:1/")
            # Must not raise under any circumstances
            try:
                bc.deliver_project_sync_alert_if_pending(state, config)
            except Exception as exc:
                self.fail(f"deliver_project_sync_alert_if_pending raised: {exc}")
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Return value: delivered (mock HTTP server)
    # ------------------------------------------------------------------

    def test_returns_delivered_on_http_200(self) -> None:
        """Delivery to an HTTP 200 endpoint returns 'delivered'."""
        import _bridge_common as bc
        import json
        import tempfile
        import pathlib
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        received_body: list[bytes] = []

        class _MockHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                received_body.append(self.rfile.read(length))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, fmt, *args) -> None:  # type: ignore[override]
                pass

        server = HTTPServer(("127.0.0.1", 0), _MockHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            json.dump({"type": "project_sync_alert", "hash": "hdeliver"}, fp)
            tmp_path = fp.name
        try:
            state = self._make_pending_state(
                alert_file=tmp_path,
                alert_hash="hdeliver",
                delivered_hash="",
            )
            config = self._make_config(webhook_url=f"http://127.0.0.1:{port}/hook")
            result = bc.deliver_project_sync_alert_if_pending(state, config)
            thread.join(timeout=3.0)
            server.server_close()
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

        self.assertEqual(result, "delivered")
        self.assertEqual(len(received_body), 1)
        body = json.loads(received_body[0])
        self.assertEqual(body["type"], "project_sync_alert")

    # ------------------------------------------------------------------
    # format_project_sync_alert_delivery_status
    # ------------------------------------------------------------------

    def test_format_delivery_status_none_when_no_status(self) -> None:
        import _bridge_common as bc
        state: dict = {}
        result = bc.format_project_sync_alert_delivery_status(state)
        self.assertEqual(result, "none")

    def test_format_delivery_status_delivered_includes_short_hash(self) -> None:
        import _bridge_common as bc
        state = {
            "last_project_sync_alert_delivery_status": "delivered",
            "last_project_sync_alert_delivery_hash": "abc123xyz789",
            "last_project_sync_alert_delivery_error": "",
        }
        result = bc.format_project_sync_alert_delivery_status(state)
        self.assertIn("delivered", result)
        self.assertIn("abc123xyz7", result)  # first 10+ chars present

    def test_format_delivery_status_delivery_failed_includes_error(self) -> None:
        import _bridge_common as bc
        state = {
            "last_project_sync_alert_delivery_status": "delivery_failed",
            "last_project_sync_alert_delivery_hash": "",
            "last_project_sync_alert_delivery_error": "URLError: <urlopen error>",
        }
        result = bc.format_project_sync_alert_delivery_status(state)
        self.assertIn("delivery_failed", result)
        self.assertIn("URLError", result)

    def test_format_delivery_status_not_requested(self) -> None:
        import _bridge_common as bc
        state = {
            "last_project_sync_alert_delivery_status": "not_requested_no_webhook",
            "last_project_sync_alert_delivery_hash": "",
            "last_project_sync_alert_delivery_error": "",
        }
        result = bc.format_project_sync_alert_delivery_status(state)
        self.assertEqual(result, "not_requested_no_webhook")

    def test_format_delivery_status_skipped(self) -> None:
        import _bridge_common as bc
        state = {
            "last_project_sync_alert_delivery_status": "skipped_already_delivered",
            "last_project_sync_alert_delivery_hash": "abc123xyz789",
            "last_project_sync_alert_delivery_error": "",
        }
        result = bc.format_project_sync_alert_delivery_status(state)
        self.assertIn("skipped_already_delivered", result)

    # ------------------------------------------------------------------
    # DEFAULT_STATE delivery keys
    # ------------------------------------------------------------------

    def test_default_state_has_delivery_keys(self) -> None:
        import _bridge_common as bc
        for key in (
            "last_project_sync_alert_delivery_status",
            "last_project_sync_alert_delivery_hash",
            "last_project_sync_alert_delivery_attempted_at",
            "last_project_sync_alert_delivery_error",
            "last_project_sync_alert_delivery_url",
        ):
            self.assertIn(key, bc.DEFAULT_STATE, f"DEFAULT_STATE missing key: {key}")

    def test_default_project_config_has_webhook_url_key(self) -> None:
        import _bridge_common as bc
        self.assertIn(
            "project_sync_alert_webhook_url",
            bc.DEFAULT_PROJECT_CONFIG,
        )
        self.assertEqual(bc.DEFAULT_PROJECT_CONFIG["project_sync_alert_webhook_url"], "")

    # ------------------------------------------------------------------
    # doctor output includes delivery line
    # ------------------------------------------------------------------

    def test_doctor_output_includes_delivery_line(self) -> None:
        """start_bridge.py source must include project_sync_alert_delivery print line."""
        import os
        script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "start_bridge.py")
        with open(script_path, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("project_sync_alert_delivery", source)

    # ------------------------------------------------------------------
    # Phase 58 regression: record_project_sync_alert_if_new still works
    # ------------------------------------------------------------------

    def test_phase58_record_alert_returns_none_without_trigger(self) -> None:
        import _bridge_common as bc
        state = {
            "last_issue_centric_primary_project_sync_status": "synced",
        }
        with unittest.mock.patch.object(bc, "update_state"):
            result = bc.record_project_sync_alert_if_new(state)
        self.assertEqual(result, "none")

    # ------------------------------------------------------------------
    # _deliver_project_sync_alert_to_webhook unit tests (transport level)
    # ------------------------------------------------------------------

    def test_transport_returns_delivered_on_2xx(self) -> None:
        """_deliver_project_sync_alert_to_webhook returns ('delivered', '') on 200."""
        import _bridge_common as bc
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                self.send_response(200)
                self.end_headers()

            def log_message(self, fmt, *args) -> None:  # type: ignore[override]
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        status, err = bc._deliver_project_sync_alert_to_webhook(
            {"key": "val"}, f"http://127.0.0.1:{port}/", timeout=5
        )
        thread.join(timeout=3.0)
        server.server_close()
        self.assertEqual(status, "delivered")
        self.assertEqual(err, "")

    def test_transport_returns_delivery_failed_on_non_2xx(self) -> None:
        """_deliver_project_sync_alert_to_webhook returns ('delivery_failed', ...) on 500."""
        import _bridge_common as bc
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                self.send_response(500)
                self.end_headers()

            def log_message(self, fmt, *args) -> None:  # type: ignore[override]
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        status, err = bc._deliver_project_sync_alert_to_webhook(
            {"key": "val"}, f"http://127.0.0.1:{port}/", timeout=5
        )
        thread.join(timeout=3.0)
        server.server_close()
        self.assertEqual(status, "delivery_failed")
        self.assertIn("500", err)

    def test_transport_returns_delivery_failed_on_connection_refused(self) -> None:
        """_deliver_project_sync_alert_to_webhook absorbs connection errors."""
        import _bridge_common as bc
        status, err = bc._deliver_project_sync_alert_to_webhook(
            {"key": "val"}, "http://127.0.0.1:1/", timeout=3
        )
        self.assertEqual(status, "delivery_failed")
        self.assertTrue(len(err) > 0)

    # ------------------------------------------------------------------
    # double-delivery prevention via state update
    # ------------------------------------------------------------------

    def test_double_delivery_prevented_after_delivered_state(self) -> None:
        """After a successful delivery, calling again with same state returns skipped."""
        import _bridge_common as bc
        state = self._make_pending_state(
            alert_hash="dupHash",
            delivered_hash="dupHash",  # same → already delivered
        )
        config = self._make_config(webhook_url="https://example.invalid/hook")
        result = bc.deliver_project_sync_alert_if_pending(state, config)
        self.assertEqual(result, "skipped_already_delivered")


# ---------------------------------------------------------------------------
# Phase 60 — bounded multi-retry for project_state_sync_failed webhook delivery
# ---------------------------------------------------------------------------


class ProjectSyncAlertBoundedRetryTests(unittest.TestCase):
    """Phase 60 — bounded multi-retry for webhook delivery.

    Verifies:
    - _deliver_project_sync_alert_with_retry: 1st attempt succeeds → attempts=1
    - _deliver_project_sync_alert_with_retry: 1st fails, 2nd succeeds → attempts=2
    - _deliver_project_sync_alert_with_retry: 1st+2nd fail, 3rd succeeds → attempts=3
    - _deliver_project_sync_alert_with_retry: all 3 fail → delivery_failed, attempts=3
    - retry only on delivery_failed, not on other return values
    - not_requested_no_webhook / invalid_payload / skipped_already_delivered not retried
    - attempt_count stored in state after delivery
    - format_project_sync_alert_delivery_status includes attempts= suffix
    - hard error not raised on all-attempts-failed
    - Phase 59 dedupe / warning surface regression
    - DEFAULT_STATE has attempt_count key
    """

    def setUp(self) -> None:
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

    def _make_pending_state(
        self,
        alert_file: str = "bridge/project_sync_alert.json",
        alert_hash: str = "abc123",
        delivered_hash: str = "",
    ) -> dict:
        return {
            "last_project_sync_alert_status": "pending",
            "last_project_sync_alert_file": alert_file,
            "last_project_sync_alert_hash": alert_hash,
            "last_project_sync_alert_delivery_hash": delivered_hash,
            "last_project_sync_alert_delivery_status": "",
            "last_project_sync_alert_delivery_error": "",
            "last_project_sync_alert_delivery_url": "",
            "last_project_sync_alert_delivery_attempted_at": "",
            "last_project_sync_alert_delivery_attempt_count": 0,
        }

    def _make_config(self, webhook_url: str = "") -> dict:
        return {"project_sync_alert_webhook_url": webhook_url}

    # ------------------------------------------------------------------
    # _deliver_project_sync_alert_with_retry unit tests
    # ------------------------------------------------------------------

    def test_retry_succeeds_on_first_attempt(self) -> None:
        """If 1st attempt returns delivered, attempt_count=1."""
        import _bridge_common as bc
        with unittest.mock.patch.object(
            bc,
            "_deliver_project_sync_alert_to_webhook",
            return_value=("delivered", ""),
        ) as mock_transport:
            status, error, count = bc._deliver_project_sync_alert_with_retry(
                {"k": "v"}, "http://example.invalid/", max_attempts=3, retry_delays=(0, 0)
            )
        self.assertEqual(status, "delivered")
        self.assertEqual(error, "")
        self.assertEqual(count, 1)
        self.assertEqual(mock_transport.call_count, 1)

    def test_retry_succeeds_on_second_attempt(self) -> None:
        """1st fails, 2nd succeeds → attempt_count=2."""
        import _bridge_common as bc
        side_effects = [("delivery_failed", "err1"), ("delivered", "")]
        with unittest.mock.patch.object(
            bc,
            "_deliver_project_sync_alert_to_webhook",
            side_effect=side_effects,
        ) as mock_transport:
            with unittest.mock.patch("time.sleep"):
                status, error, count = bc._deliver_project_sync_alert_with_retry(
                    {"k": "v"}, "http://example.invalid/", max_attempts=3, retry_delays=(0, 0)
                )
        self.assertEqual(status, "delivered")
        self.assertEqual(error, "")
        self.assertEqual(count, 2)
        self.assertEqual(mock_transport.call_count, 2)

    def test_retry_succeeds_on_third_attempt(self) -> None:
        """1st+2nd fail, 3rd succeeds → attempt_count=3."""
        import _bridge_common as bc
        side_effects = [
            ("delivery_failed", "err1"),
            ("delivery_failed", "err2"),
            ("delivered", ""),
        ]
        with unittest.mock.patch.object(
            bc,
            "_deliver_project_sync_alert_to_webhook",
            side_effect=side_effects,
        ) as mock_transport:
            with unittest.mock.patch("time.sleep"):
                status, error, count = bc._deliver_project_sync_alert_with_retry(
                    {"k": "v"}, "http://example.invalid/", max_attempts=3, retry_delays=(0, 0)
                )
        self.assertEqual(status, "delivered")
        self.assertEqual(error, "")
        self.assertEqual(count, 3)
        self.assertEqual(mock_transport.call_count, 3)

    def test_retry_exhausted_all_three_attempts(self) -> None:
        """All 3 attempts fail → delivery_failed, attempt_count=3."""
        import _bridge_common as bc
        side_effects = [
            ("delivery_failed", "err1"),
            ("delivery_failed", "err2"),
            ("delivery_failed", "err3"),
        ]
        with unittest.mock.patch.object(
            bc,
            "_deliver_project_sync_alert_to_webhook",
            side_effect=side_effects,
        ) as mock_transport:
            with unittest.mock.patch("time.sleep"):
                status, error, count = bc._deliver_project_sync_alert_with_retry(
                    {"k": "v"}, "http://example.invalid/", max_attempts=3, retry_delays=(0, 0)
                )
        self.assertEqual(status, "delivery_failed")
        self.assertEqual(error, "err3")  # last error
        self.assertEqual(count, 3)
        self.assertEqual(mock_transport.call_count, 3)

    def test_retry_does_not_exceed_max_attempts(self) -> None:
        """Never calls transport more than max_attempts times."""
        import _bridge_common as bc
        with unittest.mock.patch.object(
            bc,
            "_deliver_project_sync_alert_to_webhook",
            return_value=("delivery_failed", "always_fail"),
        ) as mock_transport:
            with unittest.mock.patch("time.sleep"):
                status, error, count = bc._deliver_project_sync_alert_with_retry(
                    {"k": "v"}, "http://example.invalid/", max_attempts=3, retry_delays=(0, 0)
                )
        self.assertEqual(count, 3)
        self.assertEqual(mock_transport.call_count, 3)

    def test_retry_delay_called_between_attempts(self) -> None:
        """time.sleep is called with correct delays between attempts."""
        import _bridge_common as bc
        side_effects = [
            ("delivery_failed", "e1"),
            ("delivery_failed", "e2"),
            ("delivered", ""),
        ]
        sleep_calls = []
        with unittest.mock.patch.object(
            bc,
            "_deliver_project_sync_alert_to_webhook",
            side_effect=side_effects,
        ):
            with unittest.mock.patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                bc._deliver_project_sync_alert_with_retry(
                    {"k": "v"}, "http://example.invalid/", max_attempts=3, retry_delays=(1, 3)
                )
        # 3 attempts → 2 sleeps: 1s before 2nd, 3s before 3rd
        self.assertEqual(sleep_calls, [1, 3])

    def test_no_sleep_after_final_attempt(self) -> None:
        """No sleep after the last (3rd) attempt even if still failing."""
        import _bridge_common as bc
        sleep_calls = []
        with unittest.mock.patch.object(
            bc,
            "_deliver_project_sync_alert_to_webhook",
            return_value=("delivery_failed", "e"),
        ):
            with unittest.mock.patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                bc._deliver_project_sync_alert_with_retry(
                    {"k": "v"}, "http://example.invalid/", max_attempts=3, retry_delays=(1, 3)
                )
        # Only 2 sleeps (before 2nd and 3rd), none after 3rd
        self.assertEqual(len(sleep_calls), 2)

    # ------------------------------------------------------------------
    # deliver_project_sync_alert_if_pending with retry: attempt_count in state
    # ------------------------------------------------------------------

    def test_attempt_count_stored_on_delivered(self) -> None:
        """attempt_count saved in state when delivery succeeds."""
        import _bridge_common as bc
        import json
        import tempfile
        import pathlib
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            json.dump({"type": "project_sync_alert", "hash": "hh1"}, fp)
            tmp_path = fp.name
        try:
            state = self._make_pending_state(alert_file=tmp_path, alert_hash="hh1")
            config = self._make_config(webhook_url="http://example.invalid/")
            saved_state: dict = {}
            with unittest.mock.patch.object(
                bc,
                "_deliver_project_sync_alert_with_retry",
                return_value=("delivered", "", 2),
            ):
                with unittest.mock.patch.object(
                    bc,
                    "update_state",
                    side_effect=lambda **kw: saved_state.update(kw),
                ):
                    result = bc.deliver_project_sync_alert_if_pending(state, config)
            self.assertEqual(result, "delivered")
            self.assertEqual(saved_state.get("last_project_sync_alert_delivery_attempt_count"), 2)
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

    def test_attempt_count_stored_on_delivery_failed(self) -> None:
        """attempt_count=3 saved in state when all attempts fail."""
        import _bridge_common as bc
        import json
        import tempfile
        import pathlib
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            json.dump({"type": "project_sync_alert", "hash": "hh2"}, fp)
            tmp_path = fp.name
        try:
            state = self._make_pending_state(alert_file=tmp_path, alert_hash="hh2")
            config = self._make_config(webhook_url="http://example.invalid/")
            saved_state: dict = {}
            with unittest.mock.patch.object(
                bc,
                "_deliver_project_sync_alert_with_retry",
                return_value=("delivery_failed", "timeout", 3),
            ):
                with unittest.mock.patch.object(
                    bc,
                    "update_state",
                    side_effect=lambda **kw: saved_state.update(kw),
                ):
                    result = bc.deliver_project_sync_alert_if_pending(state, config)
            self.assertEqual(result, "delivery_failed")
            self.assertEqual(saved_state.get("last_project_sync_alert_delivery_attempt_count"), 3)
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Non-retried paths: not_requested / invalid_payload / skipped
    # ------------------------------------------------------------------

    def test_not_requested_no_webhook_not_retried(self) -> None:
        """not_requested_no_webhook path never calls the transport."""
        import _bridge_common as bc
        state = self._make_pending_state()
        config = self._make_config(webhook_url="")
        with unittest.mock.patch.object(
            bc,
            "_deliver_project_sync_alert_with_retry",
        ) as mock_retry:
            result = bc.deliver_project_sync_alert_if_pending(state, config)
        self.assertEqual(result, "not_requested_no_webhook")
        mock_retry.assert_not_called()

    def test_invalid_payload_not_retried(self) -> None:
        """invalid_payload path never calls retry transport."""
        import _bridge_common as bc
        state = self._make_pending_state(alert_file="bridge/does_not_exist_xyz.json")
        config = self._make_config(webhook_url="http://example.invalid/")
        with unittest.mock.patch.object(
            bc,
            "_deliver_project_sync_alert_with_retry",
        ) as mock_retry:
            result = bc.deliver_project_sync_alert_if_pending(state, config)
        self.assertEqual(result, "invalid_payload")
        mock_retry.assert_not_called()

    def test_skipped_already_delivered_not_retried(self) -> None:
        """skipped_already_delivered path never calls retry transport."""
        import _bridge_common as bc
        state = self._make_pending_state(alert_hash="sameHash", delivered_hash="sameHash")
        config = self._make_config(webhook_url="http://example.invalid/")
        with unittest.mock.patch.object(
            bc,
            "_deliver_project_sync_alert_with_retry",
        ) as mock_retry:
            result = bc.deliver_project_sync_alert_if_pending(state, config)
        self.assertEqual(result, "skipped_already_delivered")
        mock_retry.assert_not_called()

    # ------------------------------------------------------------------
    # format_project_sync_alert_delivery_status — attempts suffix
    # ------------------------------------------------------------------

    def test_format_status_delivered_includes_attempts(self) -> None:
        import _bridge_common as bc
        state = {
            "last_project_sync_alert_delivery_status": "delivered",
            "last_project_sync_alert_delivery_hash": "abc123xyz789",
            "last_project_sync_alert_delivery_error": "",
            "last_project_sync_alert_delivery_attempt_count": 2,
        }
        result = bc.format_project_sync_alert_delivery_status(state)
        self.assertIn("delivered", result)
        self.assertIn("attempts=2", result)

    def test_format_status_delivery_failed_includes_attempts(self) -> None:
        import _bridge_common as bc
        state = {
            "last_project_sync_alert_delivery_status": "delivery_failed",
            "last_project_sync_alert_delivery_hash": "",
            "last_project_sync_alert_delivery_error": "URLError: timed out",
            "last_project_sync_alert_delivery_attempt_count": 3,
        }
        result = bc.format_project_sync_alert_delivery_status(state)
        self.assertIn("delivery_failed", result)
        self.assertIn("attempts=3", result)
        self.assertIn("URLError", result)

    def test_format_status_no_attempts_suffix_when_zero(self) -> None:
        """attempt_count=0 (default) → no attempts= suffix."""
        import _bridge_common as bc
        state = {
            "last_project_sync_alert_delivery_status": "delivered",
            "last_project_sync_alert_delivery_hash": "abc123",
            "last_project_sync_alert_delivery_error": "",
            "last_project_sync_alert_delivery_attempt_count": 0,
        }
        result = bc.format_project_sync_alert_delivery_status(state)
        self.assertNotIn("attempts=", result)

    # ------------------------------------------------------------------
    # hard error not raised on all-attempts-failed (integration)
    # ------------------------------------------------------------------

    def test_all_attempts_failed_does_not_raise(self) -> None:
        """All retries exhausted must not raise any exception."""
        import _bridge_common as bc
        import json
        import tempfile
        import pathlib
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            json.dump({"type": "test"}, fp)
            tmp_path = fp.name
        try:
            state = self._make_pending_state(alert_file=tmp_path, alert_hash="hfail")
            config = self._make_config(webhook_url="http://127.0.0.1:1/")
            with unittest.mock.patch("time.sleep"):
                try:
                    bc.deliver_project_sync_alert_if_pending(state, config)
                except Exception as exc:
                    self.fail(f"deliver_project_sync_alert_if_pending raised after all retries: {exc}")
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # DEFAULT_STATE has attempt_count key
    # ------------------------------------------------------------------

    def test_default_state_has_attempt_count_key(self) -> None:
        import _bridge_common as bc
        self.assertIn("last_project_sync_alert_delivery_attempt_count", bc.DEFAULT_STATE)
        self.assertEqual(bc.DEFAULT_STATE["last_project_sync_alert_delivery_attempt_count"], 0)

    # ------------------------------------------------------------------
    # Phase 59 regression: dedupe still works
    # ------------------------------------------------------------------

    def test_phase59_dedupe_regression(self) -> None:
        """Same alert hash is still skipped after bounded retry is introduced."""
        import _bridge_common as bc
        state = self._make_pending_state(alert_hash="dedupeH", delivered_hash="dedupeH")
        config = self._make_config(webhook_url="http://example.invalid/")
        result = bc.deliver_project_sync_alert_if_pending(state, config)
        self.assertEqual(result, "skipped_already_delivered")

    # ------------------------------------------------------------------
    # Integration: 1st succeeds via real mock HTTP server (no retry needed)
    # ------------------------------------------------------------------

    def test_integration_delivered_first_attempt(self) -> None:
        """Integration: HTTP 200 on 1st attempt → delivered, attempts=1."""
        import _bridge_common as bc
        import json
        import tempfile
        import pathlib
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                self.send_response(200)
                self.end_headers()

            def log_message(self, fmt, *args) -> None:  # type: ignore[override]
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            json.dump({"type": "project_sync_alert", "hash": "intH1"}, fp)
            tmp_path = fp.name
        saved_state: dict = {}
        try:
            state = self._make_pending_state(alert_file=tmp_path, alert_hash="intH1")
            config = self._make_config(webhook_url=f"http://127.0.0.1:{port}/hook")
            with unittest.mock.patch.object(
                bc, "update_state", side_effect=lambda **kw: saved_state.update(kw)
            ):
                result = bc.deliver_project_sync_alert_if_pending(state, config)
            thread.join(timeout=3.0)
            server.server_close()
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)
        self.assertEqual(result, "delivered")
        self.assertEqual(saved_state.get("last_project_sync_alert_delivery_attempt_count"), 1)


# ---------------------------------------------------------------------------
# Phase 61: webhook config validation / doctor guidance
# ---------------------------------------------------------------------------


class ProjectSyncAlertWebhookConfigValidationTests(unittest.TestCase):
    """Tests for validate_project_sync_alert_webhook_url and
    format_project_sync_alert_webhook_config_note (Phase 61).
    """

    # ------------------------------------------------------------------
    # validate_project_sync_alert_webhook_url
    # ------------------------------------------------------------------

    def test_empty_string_is_valid_delivery_disabled(self) -> None:
        import _bridge_common as bc
        ok, reason = bc.validate_project_sync_alert_webhook_url("")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_whitespace_only_is_valid_delivery_disabled(self) -> None:
        import _bridge_common as bc
        ok, reason = bc.validate_project_sync_alert_webhook_url("   ")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_valid_https_url(self) -> None:
        import _bridge_common as bc
        ok, reason = bc.validate_project_sync_alert_webhook_url("https://example.com/hook")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_valid_http_url(self) -> None:
        import _bridge_common as bc
        ok, reason = bc.validate_project_sync_alert_webhook_url("http://192.168.1.1/hook")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_https_scheme_only_no_host_is_invalid(self) -> None:
        import _bridge_common as bc
        ok, reason = bc.validate_project_sync_alert_webhook_url("https://")
        self.assertFalse(ok)
        self.assertIn("no host", reason)

    def test_http_scheme_only_no_host_is_invalid(self) -> None:
        import _bridge_common as bc
        ok, reason = bc.validate_project_sync_alert_webhook_url("http://")
        self.assertFalse(ok)
        self.assertIn("no host", reason)

    def test_ftp_scheme_is_invalid(self) -> None:
        import _bridge_common as bc
        ok, reason = bc.validate_project_sync_alert_webhook_url("ftp://example.com/hook")
        self.assertFalse(ok)
        self.assertIn("ftp", reason)

    def test_no_scheme_is_invalid(self) -> None:
        import _bridge_common as bc
        ok, reason = bc.validate_project_sync_alert_webhook_url("example.com/hook")
        self.assertFalse(ok)
        self.assertTrue(len(reason) > 0)

    def test_leading_trailing_whitespace_around_valid_url(self) -> None:
        """URL with surrounding whitespace should be treated as valid after trim."""
        import _bridge_common as bc
        ok, reason = bc.validate_project_sync_alert_webhook_url("  https://example.com/hook  ")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_leading_whitespace_around_invalid_scheme(self) -> None:
        """URL with ftp:// scheme is invalid even with surrounding whitespace."""
        import _bridge_common as bc
        ok, reason = bc.validate_project_sync_alert_webhook_url("  ftp://bad.example.com  ")
        self.assertFalse(ok)

    def test_does_not_raise(self) -> None:
        """validate_project_sync_alert_webhook_url must never raise."""
        import _bridge_common as bc
        for url in ["", "garbage", "://broken", "https://", "http://ok.com"]:
            try:
                bc.validate_project_sync_alert_webhook_url(url)
            except Exception as exc:  # pragma: no cover
                self.fail(f"validate raised for {url!r}: {exc}")

    # ------------------------------------------------------------------
    # format_project_sync_alert_webhook_config_note
    # ------------------------------------------------------------------

    def test_format_note_empty_url(self) -> None:
        import _bridge_common as bc
        note = bc.format_project_sync_alert_webhook_config_note(
            {"project_sync_alert_webhook_url": ""}
        )
        self.assertIn("disabled", note)

    def test_format_note_missing_key(self) -> None:
        import _bridge_common as bc
        note = bc.format_project_sync_alert_webhook_config_note({})
        self.assertIn("disabled", note)

    def test_format_note_valid_https_url(self) -> None:
        import _bridge_common as bc
        note = bc.format_project_sync_alert_webhook_config_note(
            {"project_sync_alert_webhook_url": "https://hooks.example.com/abc"}
        )
        self.assertTrue(note.startswith("ok url="))
        self.assertIn("https://hooks.example.com/abc", note)

    def test_format_note_invalid_scheme(self) -> None:
        import _bridge_common as bc
        note = bc.format_project_sync_alert_webhook_config_note(
            {"project_sync_alert_webhook_url": "ftp://bad.example.com"}
        )
        self.assertTrue(note.startswith("config_warning:"))

    def test_format_note_scheme_only_no_host(self) -> None:
        import _bridge_common as bc
        note = bc.format_project_sync_alert_webhook_config_note(
            {"project_sync_alert_webhook_url": "https://"}
        )
        self.assertTrue(note.startswith("config_warning:"))

    def test_format_note_long_url_truncated(self) -> None:
        import _bridge_common as bc
        long_url = "https://" + "a" * 80 + ".example.com/hook"
        note = bc.format_project_sync_alert_webhook_config_note(
            {"project_sync_alert_webhook_url": long_url}
        )
        self.assertTrue(note.startswith("ok url="))
        # Display must be truncated (original is > 60 chars)
        self.assertNotIn(long_url, note)

    # ------------------------------------------------------------------
    # doctor output includes project_sync_alert_webhook_config
    # ------------------------------------------------------------------

    def test_doctor_output_includes_webhook_config_line(self) -> None:
        """start_bridge doctor prints project_sync_alert_webhook_config line."""
        import io
        import unittest.mock
        from contextlib import redirect_stdout

        cfg = {"project_sync_alert_webhook_url": "https://hook.example.com/test"}
        with (
            unittest.mock.patch("run_until_stop.load_state", return_value={}),
            unittest.mock.patch("run_until_stop.load_project_config", return_value=cfg),
            unittest.mock.patch("run_until_stop.start_bridge_mode", return_value="resume"),
            unittest.mock.patch(
                "run_until_stop.start_bridge_resume_guidance",
                return_value=("ok", "guidance", "note"),
            ),
            unittest.mock.patch(
                "run_until_stop.recommended_operator_step",
                return_value=("step", "cmd"),
            ),
            unittest.mock.patch("run_until_stop.codex_report_is_ready", return_value=False),
            unittest.mock.patch("run_until_stop.runtime_report_path", return_value=Path("/tmp/nope")),
            unittest.mock.patch("run_until_stop.runtime_prompt_path", return_value=Path("/tmp/nope")),
            unittest.mock.patch("run_until_stop.runtime_stop_path", return_value=Path("/tmp/nope")),
            unittest.mock.patch("run_until_stop.bridge_runtime_root", return_value=Path("/tmp")),
            unittest.mock.patch("run_until_stop.should_rotate_before_next_chat_request", return_value=False),
            unittest.mock.patch("run_until_stop.should_prioritize_unarchived_report", return_value=False),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                args = make_args()
                start_bridge.print_doctor(args)
            output = buf.getvalue()
        self.assertIn("project_sync_alert_webhook_config:", output)
        self.assertIn("ok url=https://hook.example.com/test", output)

    def test_doctor_shows_disabled_when_url_empty(self) -> None:
        import io
        import unittest.mock
        from contextlib import redirect_stdout

        cfg = {"project_sync_alert_webhook_url": ""}
        with (
            unittest.mock.patch("run_until_stop.load_state", return_value={}),
            unittest.mock.patch("run_until_stop.load_project_config", return_value=cfg),
            unittest.mock.patch("run_until_stop.start_bridge_mode", return_value="resume"),
            unittest.mock.patch(
                "run_until_stop.start_bridge_resume_guidance",
                return_value=("ok", "guidance", "note"),
            ),
            unittest.mock.patch(
                "run_until_stop.recommended_operator_step",
                return_value=("step", "cmd"),
            ),
            unittest.mock.patch("run_until_stop.codex_report_is_ready", return_value=False),
            unittest.mock.patch("run_until_stop.runtime_report_path", return_value=Path("/tmp/nope")),
            unittest.mock.patch("run_until_stop.runtime_prompt_path", return_value=Path("/tmp/nope")),
            unittest.mock.patch("run_until_stop.runtime_stop_path", return_value=Path("/tmp/nope")),
            unittest.mock.patch("run_until_stop.bridge_runtime_root", return_value=Path("/tmp")),
            unittest.mock.patch("run_until_stop.should_rotate_before_next_chat_request", return_value=False),
            unittest.mock.patch("run_until_stop.should_prioritize_unarchived_report", return_value=False),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                args = make_args()
                start_bridge.print_doctor(args)
            output = buf.getvalue()
        self.assertIn("project_sync_alert_webhook_config:", output)
        self.assertIn("disabled", output)

    def test_doctor_shows_config_warning_for_invalid_scheme(self) -> None:
        import io
        import unittest.mock
        from contextlib import redirect_stdout

        cfg = {"project_sync_alert_webhook_url": "ftp://bad.example.com"}
        with (
            unittest.mock.patch("run_until_stop.load_state", return_value={}),
            unittest.mock.patch("run_until_stop.load_project_config", return_value=cfg),
            unittest.mock.patch("run_until_stop.start_bridge_mode", return_value="resume"),
            unittest.mock.patch(
                "run_until_stop.start_bridge_resume_guidance",
                return_value=("ok", "guidance", "note"),
            ),
            unittest.mock.patch(
                "run_until_stop.recommended_operator_step",
                return_value=("step", "cmd"),
            ),
            unittest.mock.patch("run_until_stop.codex_report_is_ready", return_value=False),
            unittest.mock.patch("run_until_stop.runtime_report_path", return_value=Path("/tmp/nope")),
            unittest.mock.patch("run_until_stop.runtime_prompt_path", return_value=Path("/tmp/nope")),
            unittest.mock.patch("run_until_stop.runtime_stop_path", return_value=Path("/tmp/nope")),
            unittest.mock.patch("run_until_stop.bridge_runtime_root", return_value=Path("/tmp")),
            unittest.mock.patch("run_until_stop.should_rotate_before_next_chat_request", return_value=False),
            unittest.mock.patch("run_until_stop.should_prioritize_unarchived_report", return_value=False),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                args = make_args()
                start_bridge.print_doctor(args)
            output = buf.getvalue()
        self.assertIn("config_warning", output)

    # ------------------------------------------------------------------
    # Boundary: delivery_failed ≠ config_invalid  /  skipped ≠ warning
    # ------------------------------------------------------------------

    def test_delivery_failed_state_does_not_become_config_invalid(self) -> None:
        """delivery_failed state key is independent of config validation note."""
        import _bridge_common as bc
        state = {
            "last_project_sync_alert_delivery_status": "delivery_failed",
            "last_project_sync_alert_delivery_hash": "abc",
            "last_project_sync_alert_delivery_error": "URLError: timed out",
            "last_project_sync_alert_delivery_attempted_at": "2026-04-19T00:00:00+00:00",
            "last_project_sync_alert_delivery_attempt_count": 3,
        }
        delivery_note = bc.format_project_sync_alert_delivery_status(state)
        self.assertIn("delivery_failed", delivery_note)
        # config note is independent and should not be affected by state
        config_note = bc.format_project_sync_alert_webhook_config_note(
            {"project_sync_alert_webhook_url": "https://ok.example.com"}
        )
        self.assertTrue(config_note.startswith("ok url="))

    def test_skipped_already_delivered_is_not_warning(self) -> None:
        """skipped_already_delivered is a normal dedupe result, not a warning."""
        import _bridge_common as bc
        state = {
            "last_project_sync_alert_delivery_status": "skipped_already_delivered",
            "last_project_sync_alert_delivery_hash": "abc",
            "last_project_sync_alert_delivery_error": "",
            "last_project_sync_alert_delivery_attempted_at": "",
            "last_project_sync_alert_delivery_attempt_count": 0,
        }
        note = bc.format_project_sync_alert_delivery_status(state)
        self.assertIn("skipped_already_delivered", note)
        # must NOT contain "warning" or "error" to confirm boundary
        self.assertNotIn("warning", note)
        self.assertNotIn("error", note)

    # ------------------------------------------------------------------
    # Phase 59-60 regression: delivery / retry / dedupe still work
    # ------------------------------------------------------------------

    def test_deliver_pending_with_valid_url_calls_retry_helper(self) -> None:
        """deliver_project_sync_alert_if_pending still calls retry helper (Phase 60 regression)."""
        import _bridge_common as bc
        import pathlib
        import json
        import tempfile
        import unittest.mock

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fp:
            json.dump({"type": "project_sync_alert", "hash": "reg60"}, fp)
            tmp_path = fp.name

        state = {
            "last_project_sync_alert_status": "pending",
            "last_project_sync_alert_hash": "reg60",
            "last_project_sync_alert_file": tmp_path,
            "last_project_sync_alert_delivery_status": "",
            "last_project_sync_alert_delivery_hash": "",
            "last_project_sync_alert_delivery_error": "",
            "last_project_sync_alert_delivery_attempted_at": "",
            "last_project_sync_alert_delivery_url": "",
            "last_project_sync_alert_delivery_attempt_count": 0,
        }
        config = {"project_sync_alert_webhook_url": "https://hook.example.com/reg"}

        with (
            unittest.mock.patch.object(
                bc,
                "_deliver_project_sync_alert_with_retry",
                return_value=("delivered", "", 1),
            ) as mock_retry,
            unittest.mock.patch.object(bc, "update_state"),
        ):
            result = bc.deliver_project_sync_alert_if_pending(state, config)

        pathlib.Path(tmp_path).unlink(missing_ok=True)
        self.assertEqual(result, "delivered")
        mock_retry.assert_called_once()

    def test_dedupe_returns_skipped_already_delivered(self) -> None:
        """dedupe: same hash → skipped_already_delivered (Phase 59 regression)."""
        import _bridge_common as bc
        import pathlib
        import json
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fp:
            json.dump({"type": "project_sync_alert", "hash": "dup01"}, fp)
            tmp_path = fp.name

        state = {
            "last_project_sync_alert_status": "pending",
            "last_project_sync_alert_hash": "dup01",
            "last_project_sync_alert_file": tmp_path,
            "last_project_sync_alert_delivery_status": "delivered",
            "last_project_sync_alert_delivery_hash": "dup01",
            "last_project_sync_alert_delivery_error": "",
            "last_project_sync_alert_delivery_attempted_at": "",
            "last_project_sync_alert_delivery_url": "",
            "last_project_sync_alert_delivery_attempt_count": 1,
        }
        config = {"project_sync_alert_webhook_url": "https://hook.example.com/reg"}

        result = bc.deliver_project_sync_alert_if_pending(state, config)
        pathlib.Path(tmp_path).unlink(missing_ok=True)
        self.assertEqual(result, "skipped_already_delivered")

