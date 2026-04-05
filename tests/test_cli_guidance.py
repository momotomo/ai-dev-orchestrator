from __future__ import annotations

import argparse
import io
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


def make_args(project_path: str = "/tmp/repo", max_execution_count: int = 6) -> argparse.Namespace:
    return argparse.Namespace(
        project_path=project_path,
        max_execution_count=max_execution_count,
        status=False,
        resume=False,
        doctor=False,
        clear_error=False,
    )


class HelpSmokeTest(unittest.TestCase):
    def test_start_bridge_help_mentions_user_authored_first_request(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/start_bridge.py", "--help"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout
        self.assertIn("bridge の通常入口", output)
        self.assertIn("初回 request の本文は自分で書きます", output)
        self.assertIn("reply contract だけを追加します", output)


class HumanFacingStatusTests(unittest.TestCase):
    def test_first_request_waiting_uses_source_of_truth_wording(self) -> None:
        view = present_bridge_status({"mode": "idle", "need_chatgpt_prompt": True})
        self.assertEqual(view.label, "初回依頼文の入力待ち")
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

    def test_handoff_view_uses_next_step_style_for_running_codex(self) -> None:
        handoff = present_bridge_handoff({"mode": "codex_running", "need_codex_run": True})
        self.assertEqual(handoff.title, "Codex の完了を待っています。")


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


if __name__ == "__main__":
    unittest.main()
