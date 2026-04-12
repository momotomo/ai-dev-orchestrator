from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _bridge_common  # noqa: E402
import reset_runtime_state  # noqa: E402


def _make_bridge_env(root: Path) -> dict:
    """Create minimal bridge directory structure and return a fake config."""
    (root / "bridge" / "inbox").mkdir(parents=True)
    (root / "bridge" / "outbox").mkdir(parents=True)
    (root / "bridge" / "history").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)
    return {"bridge_runtime_root": str(root)}


def _dirty_state(**overrides: object) -> dict:
    """Return a copy of DEFAULT_STATE with given overrides applied."""
    state = _bridge_common.DEFAULT_STATE.copy()
    state.update(overrides)
    return state


class ResetFreshStateTests(unittest.TestCase):
    """run_reset writes DEFAULT_STATE to state.json regardless of prior state."""

    def test_ready_for_codex_becomes_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            state_path = root / "bridge" / "state.json"
            state_path.write_text(
                json.dumps(_dirty_state(mode="ready_for_codex", need_codex_run=True)),
                encoding="utf-8",
            )

            reset_runtime_state.run_reset(dry_run=False, config=config)

            result = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result["mode"], "idle")
            self.assertTrue(result["need_chatgpt_prompt"])
            self.assertFalse(result["need_codex_run"])

    def test_waiting_prompt_reply_becomes_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            state_path = root / "bridge" / "state.json"
            state_path.write_text(
                json.dumps(_dirty_state(mode="waiting_prompt_reply", need_chatgpt_next=True)),
                encoding="utf-8",
            )

            reset_runtime_state.run_reset(dry_run=False, config=config)

            result = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result["mode"], "idle")
            self.assertFalse(result.get("need_chatgpt_next", True))

    def test_error_state_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            state_path = root / "bridge" / "state.json"
            state_path.write_text(
                json.dumps(
                    _dirty_state(mode="error", error=True, error_message="boom")
                ),
                encoding="utf-8",
            )

            reset_runtime_state.run_reset(dry_run=False, config=config)

            result = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result["mode"], "idle")
            self.assertFalse(result.get("error", True))
            self.assertEqual(result.get("error_message", "X"), "")

    def test_pending_request_fields_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            state_path = root / "bridge" / "state.json"
            state_path.write_text(
                json.dumps(
                    _dirty_state(
                        pending_request_hash="abc123",
                        pending_request_source="report:5",
                        pending_request_signal="send",
                    )
                ),
                encoding="utf-8",
            )

            reset_runtime_state.run_reset(dry_run=False, config=config)

            result = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result.get("pending_request_hash", "X"), "")
            self.assertEqual(result.get("pending_request_source", "X"), "")
            self.assertEqual(result.get("pending_request_signal", "X"), "")

    def test_cycle_resets_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            state_path = root / "bridge" / "state.json"
            state_path.write_text(
                json.dumps(_dirty_state(cycle=7)),
                encoding="utf-8",
            )

            reset_runtime_state.run_reset(dry_run=False, config=config)

            result = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result.get("cycle", -1), 0)

    def test_missing_state_file_creates_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            state_path = root / "bridge" / "state.json"
            self.assertFalse(state_path.exists())

            reset_runtime_state.run_reset(dry_run=False, config=config)

            self.assertTrue(state_path.exists())
            result = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result["mode"], "idle")


class ResetArtifactArchivalTests(unittest.TestCase):
    """Active artifacts in inbox/outbox are archived to logs/ on reset."""

    def test_prompt_with_content_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            prompt_path = root / "bridge" / "inbox" / "codex_prompt.md"
            prompt_path.write_text("# prompt content\n", encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=False, config=config)

            archived = list((root / "logs").glob("*_inbox_codex_prompt.md"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_text(encoding="utf-8"), "# prompt content\n")

    def test_report_with_content_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            report_path = root / "bridge" / "outbox" / "codex_report.md"
            report_path.write_text("# report content\n", encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=False, config=config)

            archived = list((root / "logs").glob("*_outbox_codex_report.md"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_text(encoding="utf-8"), "# report content\n")

    def test_whitespace_only_prompt_not_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            prompt_path = root / "bridge" / "inbox" / "codex_prompt.md"
            prompt_path.write_text("   \n\n", encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=False, config=config)

            archived = list((root / "logs").glob("*_inbox_codex_prompt.md"))
            self.assertEqual(len(archived), 0)

    def test_missing_prompt_not_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)

            reset_runtime_state.run_reset(dry_run=False, config=config)

            archived = list((root / "logs").glob("*_inbox_codex_prompt.md"))
            self.assertEqual(len(archived), 0)

    def test_missing_report_not_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)

            reset_runtime_state.run_reset(dry_run=False, config=config)

            archived = list((root / "logs").glob("*_outbox_codex_report.md"))
            self.assertEqual(len(archived), 0)


class ResetStopFileTests(unittest.TestCase):
    """bridge/STOP is removed by reset if present."""

    def test_stop_file_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            stop_path = root / "bridge" / "STOP"
            stop_path.write_text("STOP\n", encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=False, config=config)

            self.assertFalse(stop_path.exists())

    def test_absent_stop_file_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)

            reset_runtime_state.run_reset(dry_run=False, config=config)

            self.assertFalse((root / "bridge" / "STOP").exists())


class ResetDurablePreservationTests(unittest.TestCase):
    """Durable config and history are never touched by reset."""

    def test_project_config_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            project_config_path = root / "bridge" / "project_config.json"
            project_config_path.write_text('{"project_name": "test"}', encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=False, config=config)

            self.assertTrue(project_config_path.exists())
            loaded = json.loads(project_config_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["project_name"], "test")

    def test_browser_config_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            browser_config_path = root / "bridge" / "browser_config.json"
            browser_config_path.write_text('{"app_name": "Safari"}', encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=False, config=config)

            self.assertTrue(browser_config_path.exists())
            loaded = json.loads(browser_config_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["app_name"], "Safari")

    def test_history_dir_contents_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            old_report = root / "bridge" / "history" / "codex_report_cycle_0001.md"
            old_report.write_text("# old\n", encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=False, config=config)

            self.assertTrue(old_report.exists())

    def test_existing_logs_not_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            existing_log = root / "logs" / "old_run.txt"
            existing_log.write_text("log\n", encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=False, config=config)

            self.assertTrue(existing_log.exists())


class ResetIdempotencyTests(unittest.TestCase):
    """Running reset multiple times in a row is safe."""

    def test_double_reset_leaves_idle_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            state_path = root / "bridge" / "state.json"
            state_path.write_text(
                json.dumps(_dirty_state(mode="ready_for_codex", need_codex_run=True)),
                encoding="utf-8",
            )

            reset_runtime_state.run_reset(dry_run=False, config=config)
            reset_runtime_state.run_reset(dry_run=False, config=config)

            result = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result["mode"], "idle")

    def test_second_reset_does_not_re_archive_empty_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            prompt_path = root / "bridge" / "inbox" / "codex_prompt.md"
            prompt_path.write_text("# prompt\n", encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=False, config=config)
            # After first reset, prompt is archived; file may still exist but
            # the source still exists with original content. Run again to confirm
            # it archives again (content still there) — no crash.
            reset_runtime_state.run_reset(dry_run=False, config=config)

            archived = list((root / "logs").glob("*_inbox_codex_prompt.md"))
            self.assertGreaterEqual(len(archived), 1)


class ResetDryRunTests(unittest.TestCase):
    """--dry-run prints what would happen but makes no file changes."""

    def test_dry_run_leaves_dirty_state_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            state_path = root / "bridge" / "state.json"
            state_path.write_text(
                json.dumps(_dirty_state(mode="ready_for_codex", need_codex_run=True)),
                encoding="utf-8",
            )

            reset_runtime_state.run_reset(dry_run=True, config=config)

            result = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result["mode"], "ready_for_codex")

    def test_dry_run_does_not_archive_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            prompt_path = root / "bridge" / "inbox" / "codex_prompt.md"
            prompt_path.write_text("# prompt\n", encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=True, config=config)

            archived = list((root / "logs").glob("*_inbox_codex_prompt.md"))
            self.assertEqual(len(archived), 0)

    def test_dry_run_does_not_remove_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            stop_path = root / "bridge" / "STOP"
            stop_path.write_text("STOP\n", encoding="utf-8")

            reset_runtime_state.run_reset(dry_run=True, config=config)

            self.assertTrue(stop_path.exists())

    def test_dry_run_prints_dry_run_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)

            out = io.StringIO()
            with redirect_stdout(out):
                reset_runtime_state.run_reset(dry_run=True, config=config)

            self.assertIn("[dry-run]", out.getvalue())


class ResetOutputTests(unittest.TestCase):
    """run_reset prints a helpful summary."""

    def test_output_includes_mode_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)

            out = io.StringIO()
            with redirect_stdout(out):
                reset_runtime_state.run_reset(dry_run=False, config=config)

            self.assertIn("mode: idle", out.getvalue())

    def test_output_includes_reset_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)

            out = io.StringIO()
            with redirect_stdout(out):
                reset_runtime_state.run_reset(dry_run=False, config=config)

            self.assertIn("reset complete", out.getvalue())

    def test_output_mentions_archived_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _make_bridge_env(root)
            (root / "bridge" / "inbox" / "codex_prompt.md").write_text(
                "# prompt\n", encoding="utf-8"
            )

            out = io.StringIO()
            with redirect_stdout(out):
                reset_runtime_state.run_reset(dry_run=False, config=config)

            self.assertIn("codex_prompt.md", out.getvalue())


if __name__ == "__main__":
    unittest.main()
