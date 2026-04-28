from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _bridge_common  # noqa: E402


class RecoverCodexReportTests(unittest.TestCase):
    def test_utf8_log_path_still_recovers_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_report = root / "bridge" / "outbox" / "codex_report.md"
            target_report.parent.mkdir(parents=True, exist_ok=True)
            recovered_report = root / "candidate" / "codex_report.md"
            recovered_report.parent.mkdir(parents=True, exist_ok=True)
            recovered_report.write_text("# Report\n\nRecovered\n", encoding="utf-8")
            utf8_log = root / "logs" / "utf8.log"
            utf8_log.parent.mkdir(parents=True, exist_ok=True)
            utf8_log.write_text(f"report: {recovered_report}\n", encoding="utf-8")

            recovered_from = _bridge_common.recover_codex_report(
                target_report,
                log_paths=[utf8_log],
                search_recent_logs=False,
            )

            self.assertEqual(recovered_from, recovered_report.resolve())
            self.assertEqual(target_report.read_text(encoding="utf-8"), "# Report\n\nRecovered\n")

    def test_non_utf8_log_is_skipped_with_compact_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_report = root / "bridge" / "outbox" / "codex_report.md"
            target_report.parent.mkdir(parents=True, exist_ok=True)
            bad_log = root / "logs" / "bad.log"
            bad_log.parent.mkdir(parents=True, exist_ok=True)
            bad_log.write_bytes(b"\xff\xfe\xfdlegacy")

            out = io.StringIO()
            with redirect_stdout(out):
                recovered_from = _bridge_common.recover_codex_report(
                    target_report,
                    log_paths=[bad_log],
                    search_recent_logs=False,
                )

            self.assertIsNone(recovered_from)
            self.assertFalse(target_report.exists())
            self.assertIn("report recovery skipped unreadable historical log", out.getvalue())
            self.assertIn("bad.log", out.getvalue())

    def test_recover_report_ready_state_does_not_crash_on_non_utf8_recent_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "bridge" / "inbox" / "codex_prompt.md"
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text("", encoding="utf-8")
            report_path = root / "bridge" / "outbox" / "codex_report.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            bad_log = root / "logs" / "recent-bad.log"
            bad_log.parent.mkdir(parents=True, exist_ok=True)
            bad_log.write_bytes(b"\xff\xfe\xfdrecent")
            state = {
                "mode": "idle",
                "need_chatgpt_prompt": True,
                "need_codex_run": False,
                "need_chatgpt_next": False,
            }

            out = io.StringIO()
            with (
                patch.object(_bridge_common, "runtime_prompt_path", return_value=prompt_path),
                patch.object(_bridge_common, "runtime_report_path", return_value=report_path),
                patch.object(_bridge_common, "_recent_codex_log_paths", return_value=[bad_log]),
                redirect_stdout(out),
            ):
                updated_state, recovered_from = _bridge_common.recover_report_ready_state(state)

            self.assertEqual(updated_state, state)
            self.assertIsNone(recovered_from)
            self.assertIn("recent-bad.log", out.getvalue())


if __name__ == "__main__":
    unittest.main()
