"""Tests for start_bridge.reset_stale_fallback_for_fresh_start().

Covers the fix for: rehearsal runtime state (mode=awaiting_user + stale
last_issue_centric_* fields) blocking fresh-start on a new target repo.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _bridge_common  # noqa: E402
import start_bridge  # noqa: E402


def _make_bridge_env(root: Path) -> dict:
    """Create minimal bridge directory structure and return a fake config."""
    (root / "bridge" / "inbox").mkdir(parents=True)
    (root / "bridge" / "outbox").mkdir(parents=True)
    (root / "bridge" / "history").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)
    return {"bridge_runtime_root": str(root)}


def _stale_rehearsal_state(**overrides: object) -> dict:
    """Return a state that mimics rehearsal completion leftover (awaiting_user + stale fields)."""
    state = _bridge_common.DEFAULT_STATE.copy()
    state.update(
        mode="awaiting_user",
        need_chatgpt_prompt=False,
        need_chatgpt_next=False,
        last_issue_centric_target_issue="#9",
        last_issue_centric_close_status="closed",
        last_issue_centric_stop_reason=(
            "parent issue #1 received a completion comment after issue #9 closed."
        ),
        last_issue_centric_runtime_mode="issue_centric_degraded_fallback",
        last_issue_centric_generation_lifecycle="issue_centric_invalidated",
        error=False,
        pause=False,
    )
    state.update(overrides)
    return state


def _args(status: bool = False, doctor: bool = False, clear_error: bool = False) -> argparse.Namespace:
    return argparse.Namespace(status=status, doctor=doctor, clear_error=clear_error)


class StaleFallbackDetectionTests(unittest.TestCase):
    """_is_safe_stale_fallback_state() correctly identifies the problematic state."""

    def test_stale_awaiting_user_with_no_pending_is_safe(self) -> None:
        state = _stale_rehearsal_state()
        self.assertTrue(start_bridge._is_safe_stale_fallback_state(state))

    def test_active_codex_mode_is_not_safe(self) -> None:
        for active_mode in ("ready_for_codex", "codex_running", "codex_done"):
            state = _stale_rehearsal_state(mode=active_mode)
            self.assertFalse(
                start_bridge._is_safe_stale_fallback_state(state),
                f"mode={active_mode} should not be safe to reset",
            )

    def test_error_state_is_not_safe(self) -> None:
        state = _stale_rehearsal_state(error=True, error_message="some error")
        self.assertFalse(start_bridge._is_safe_stale_fallback_state(state))

    def test_pause_state_is_not_safe(self) -> None:
        state = _stale_rehearsal_state(pause=True)
        self.assertFalse(start_bridge._is_safe_stale_fallback_state(state))

    def test_pending_request_hash_is_not_safe(self) -> None:
        state = _stale_rehearsal_state(pending_request_hash="abc123")
        self.assertFalse(start_bridge._is_safe_stale_fallback_state(state))

    def test_pending_handoff_hash_is_not_safe(self) -> None:
        state = _stale_rehearsal_state(pending_handoff_hash="def456")
        self.assertFalse(start_bridge._is_safe_stale_fallback_state(state))

    def test_pending_generation_id_is_not_safe(self) -> None:
        state = _stale_rehearsal_state(last_issue_centric_pending_generation_id="gen-001")
        self.assertFalse(start_bridge._is_safe_stale_fallback_state(state))

    def test_prepared_generation_id_is_not_safe(self) -> None:
        state = _stale_rehearsal_state(last_issue_centric_prepared_generation_id="gen-002")
        self.assertFalse(start_bridge._is_safe_stale_fallback_state(state))

    def test_clean_idle_state_is_not_reset(self) -> None:
        """DEFAULT_STATE (idle + need_chatgpt_prompt=True) should not be treated as stale."""
        state = _bridge_common.DEFAULT_STATE.copy()
        # DEFAULT_STATE already has need_chatgpt_prompt=True → action = request_next_prompt, not no_action
        self.assertFalse(start_bridge._is_safe_stale_fallback_state(state))


class ResetStaleFallbackFreshStartTests(unittest.TestCase):
    """reset_stale_fallback_for_fresh_start() transitions stale state to idle."""

    def _write_state(self, root: Path, state: dict) -> Path:
        state_path = root / "bridge" / "state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return state_path

    def test_stale_rehearsal_state_is_reset_to_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_bridge_env(root)
            state_path = self._write_state(root, _stale_rehearsal_state())

            original_runtime_root = _bridge_common.bridge_runtime_root
            _bridge_common._RUNTIME_ROOT_OVERRIDE = root  # type: ignore[attr-defined]
            try:
                import importlib
                importlib.reload(_bridge_common)
                # Re-patch after reload
                state_path = root / "bridge" / "state.json"
                state_path.write_text(json.dumps(_stale_rehearsal_state()), encoding="utf-8")

                # Directly test via patching bridge_runtime_root
                with unittest.mock.patch.object(
                    _bridge_common,
                    "bridge_runtime_root",
                    return_value=root,
                ):
                    result = start_bridge.reset_stale_fallback_for_fresh_start(_args())
                    self.assertTrue(result)
                    after = json.loads(state_path.read_text(encoding="utf-8"))
                    self.assertEqual(after["mode"], "idle")
                    self.assertTrue(after["need_chatgpt_prompt"])
            finally:
                importlib.reload(_bridge_common)

    def test_skipped_for_status_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_bridge_env(root)
            state_path = self._write_state(root, _stale_rehearsal_state())
            import unittest.mock
            with unittest.mock.patch.object(
                _bridge_common,
                "bridge_runtime_root",
                return_value=root,
            ):
                result = start_bridge.reset_stale_fallback_for_fresh_start(_args(status=True))
                self.assertFalse(result)
                after = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(after["mode"], "awaiting_user")

    def test_skipped_for_doctor_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_bridge_env(root)
            state_path = self._write_state(root, _stale_rehearsal_state())
            import unittest.mock
            with unittest.mock.patch.object(
                _bridge_common,
                "bridge_runtime_root",
                return_value=root,
            ):
                result = start_bridge.reset_stale_fallback_for_fresh_start(_args(doctor=True))
                self.assertFalse(result)
                after = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(after["mode"], "awaiting_user")

    def test_genuine_pending_request_not_reset(self) -> None:
        """State with a genuine pending request should not be reset."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_bridge_env(root)
            state = _stale_rehearsal_state(
                mode="waiting_prompt_reply",
                pending_request_hash="abc123",
                pending_request_log="some pending request",
            )
            state_path = self._write_state(root, state)
            import unittest.mock
            with unittest.mock.patch.object(
                _bridge_common,
                "bridge_runtime_root",
                return_value=root,
            ):
                result = start_bridge.reset_stale_fallback_for_fresh_start(_args())
                self.assertFalse(result)
                after = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(after["mode"], "waiting_prompt_reply")


def _stale_ready_issue_pending_state(**overrides: object) -> dict:
    """Return a state mimicking an invalid-contract recovery leftover.

    mode=awaiting_user + pending_request_source=ready_issue: (stale, no_action).
    This is the scenario after --reset (clear-error) when a ready_issue: run failed
    mid-cycle and left awaiting_user + pending_request_* without clearing them.
    """
    state = _bridge_common.DEFAULT_STATE.copy()
    state.update(
        mode="awaiting_user",
        need_chatgpt_prompt=False,
        need_chatgpt_next=False,
        pending_request_hash="abc123hash",
        pending_request_source="ready_issue:#42 some task",
        pending_request_log="logs/20260416_130535_sent_prompt_request_from_ready_issue.md",
        last_issue_centric_pending_generation_id="gen-ready-042",
        error=False,
        pause=False,
    )
    state.update(overrides)
    return state


class StaleReadyIssuePendingDetectionTests(unittest.TestCase):
    """_is_stale_ready_issue_awaiting_state() correctly identifies the stale state."""

    def test_stale_ready_issue_pending_is_detected(self) -> None:
        state = _stale_ready_issue_pending_state()
        self.assertTrue(start_bridge._is_stale_ready_issue_awaiting_state(state))

    def test_wrong_mode_not_detected(self) -> None:
        """mode=waiting_prompt_reply with ready_issue: source is NOT stale (genuine pending)."""
        state = _stale_ready_issue_pending_state(mode="waiting_prompt_reply")
        self.assertFalse(start_bridge._is_stale_ready_issue_awaiting_state(state))

    def test_non_ready_issue_source_not_detected(self) -> None:
        """pending_request_source=report: is not the initial request; skip."""
        state = _stale_ready_issue_pending_state(
            pending_request_source="report:some_report"
        )
        self.assertFalse(start_bridge._is_stale_ready_issue_awaiting_state(state))

    def test_error_state_not_detected(self) -> None:
        state = _stale_ready_issue_pending_state(error=True, error_message="some error")
        self.assertFalse(start_bridge._is_stale_ready_issue_awaiting_state(state))

    def test_pause_state_not_detected(self) -> None:
        state = _stale_ready_issue_pending_state(pause=True)
        self.assertFalse(start_bridge._is_stale_ready_issue_awaiting_state(state))

    def test_genuine_codex_dispatch_not_detected(self) -> None:
        """chatgpt_decision=issue_centric:codex_run is a genuine pending; not stale."""
        state = _stale_ready_issue_pending_state(
            chatgpt_decision="issue_centric:codex_run",
            last_issue_centric_artifact_kind="codex_body",
            last_issue_centric_metadata_log="logs/some_metadata.json",
        )
        # resolve_unified_next_action returns dispatch_issue_centric_codex_run, not no_action
        self.assertFalse(start_bridge._is_stale_ready_issue_awaiting_state(state))


class ResetStalePendingReadyIssueFreshStartTests(unittest.TestCase):
    """reset_stale_fallback_for_fresh_start() clears stale ready_issue: pending state."""

    def _write_state(self, root: Path, state: dict) -> Path:
        state_path = root / "bridge" / "state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return state_path

    def test_stale_ready_issue_pending_is_reset_to_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_bridge_env(root)
            state_path = self._write_state(root, _stale_ready_issue_pending_state())
            import unittest.mock
            with unittest.mock.patch.object(
                _bridge_common,
                "bridge_runtime_root",
                return_value=root,
            ):
                result = start_bridge.reset_stale_fallback_for_fresh_start(_args())
                self.assertTrue(result)
                after = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(after["mode"], "idle")
                self.assertTrue(after["need_chatgpt_prompt"])

    def test_stale_ready_issue_pending_fields_cleared(self) -> None:
        """After reset, pending_request_* and pending_generation_id are cleared."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_bridge_env(root)
            state_path = self._write_state(root, _stale_ready_issue_pending_state())
            import unittest.mock
            with unittest.mock.patch.object(
                _bridge_common,
                "bridge_runtime_root",
                return_value=root,
            ):
                start_bridge.reset_stale_fallback_for_fresh_start(_args())
                after = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(after["pending_request_hash"], "")
                self.assertEqual(after["pending_request_source"], "")
                self.assertEqual(after["pending_request_log"], "")
                self.assertEqual(after["pending_request_signal"], "")
                self.assertEqual(after["last_issue_centric_pending_generation_id"], "")

    def test_skipped_for_clear_error_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_bridge_env(root)
            state_path = self._write_state(root, _stale_ready_issue_pending_state())
            import unittest.mock
            with unittest.mock.patch.object(
                _bridge_common,
                "bridge_runtime_root",
                return_value=root,
            ):
                result = start_bridge.reset_stale_fallback_for_fresh_start(
                    _args(clear_error=True)
                )
                self.assertFalse(result)
                after = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(after["mode"], "awaiting_user")

    def test_genuine_waiting_prompt_reply_not_reset(self) -> None:
        """mode=waiting_prompt_reply + ready_issue: is a genuine pending; must not reset."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_bridge_env(root)
            state = _stale_ready_issue_pending_state(mode="waiting_prompt_reply")
            state_path = self._write_state(root, state)
            import unittest.mock
            with unittest.mock.patch.object(
                _bridge_common,
                "bridge_runtime_root",
                return_value=root,
            ):
                result = start_bridge.reset_stale_fallback_for_fresh_start(_args())
                self.assertFalse(result)
                after = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(after["mode"], "waiting_prompt_reply")


class ResolveStartResumeEntryActionTests(unittest.TestCase):
    """resolve_start_resume_entry_action() returns the correct action string for each state."""

    def _initial_state(self) -> dict:
        return _bridge_common.DEFAULT_STATE.copy()

    def test_error_state_returns_blocked_error(self) -> None:
        state = self._initial_state()
        state["error"] = True
        result = _bridge_common.resolve_start_resume_entry_action(state)
        self.assertEqual(result, "blocked_error")

    def test_pause_state_returns_blocked_pause(self) -> None:
        state = self._initial_state()
        state["pause"] = True
        result = _bridge_common.resolve_start_resume_entry_action(state)
        self.assertEqual(result, "blocked_pause")

    def test_error_takes_priority_over_pause(self) -> None:
        state = self._initial_state()
        state["error"] = True
        state["pause"] = True
        result = _bridge_common.resolve_start_resume_entry_action(state)
        self.assertEqual(result, "blocked_error")

    def test_pending_request_hash_returns_resume_pending_reply(self) -> None:
        state = self._initial_state()
        state["pending_request_hash"] = "abc123"
        state["pending_request_source"] = "ready_issue:#5"
        state["mode"] = "waiting_prompt_reply"
        result = _bridge_common.resolve_start_resume_entry_action(state)
        self.assertEqual(result, "resume_pending_reply")

    def test_pending_handoff_hash_returns_resume_pending_handoff(self) -> None:
        state = self._initial_state()
        state["pending_handoff_hash"] = "handoff-hash-1"
        result = _bridge_common.resolve_start_resume_entry_action(state)
        self.assertEqual(result, "resume_pending_handoff")

    def test_prepared_request_returns_resume_prepared_request(self) -> None:
        state = self._initial_state()
        state["prepared_request_hash"] = "prep-hash"
        state["prepared_request_source"] = "report:cycle-3"
        state["prepared_request_status"] = "prepared"
        result = _bridge_common.resolve_start_resume_entry_action(state)
        self.assertEqual(result, "resume_prepared_request")

    def test_prepared_request_only_hash_no_source_does_not_match(self) -> None:
        # prepared_request_hash alone without source should NOT trigger resume_prepared_request
        state = self._initial_state()
        state["prepared_request_hash"] = "prep-hash"
        state["prepared_request_source"] = ""
        result = _bridge_common.resolve_start_resume_entry_action(state)
        self.assertNotEqual(result, "resume_prepared_request")

    def test_pending_codex_dispatch_returns_resume_issue_centric_codex_dispatch(self) -> None:
        state = self._initial_state()
        state["mode"] = "awaiting_user"
        state["chatgpt_decision"] = "issue_centric:codex_run"
        state["last_issue_centric_artifact_kind"] = "codex_body"
        state["last_issue_centric_metadata_log"] = "some-metadata"
        state["need_codex_run"] = False
        state["last_issue_centric_execution_status"] = ""
        result = _bridge_common.resolve_start_resume_entry_action(state)
        self.assertEqual(result, "resume_issue_centric_codex_dispatch")

    def test_initial_state_returns_fresh_start_issue_selection(self) -> None:
        state = _bridge_common.DEFAULT_STATE.copy()
        result = _bridge_common.resolve_start_resume_entry_action(state)
        self.assertEqual(result, "fresh_start_issue_selection")

    def test_active_non_initial_state_returns_empty_string(self) -> None:
        # A state that's running (mode=awaiting_user) but doesn't fit any above category
        state = _stale_rehearsal_state()
        # Override to ensure it won't match any specific resume condition
        state["error"] = False
        state["pause"] = False
        state["pending_request_hash"] = ""
        state["pending_handoff_hash"] = ""
        state["prepared_request_hash"] = ""
        state["prepared_request_source"] = ""
        state["chatgpt_decision"] = "issue_centric:close_issue"  # not codex_run
        result = _bridge_common.resolve_start_resume_entry_action(state)
        self.assertEqual(result, "")

    def test_fresh_start_issue_selection_template_has_repo_placeholder(self) -> None:
        template = _bridge_common.FRESH_START_ISSUE_SELECTION_TEMPLATE
        formatted = template.format(repo="owner/repo-name")
        self.assertIn("owner/repo-name", formatted)
        self.assertIn("Issue", formatted)
        self.assertIn("issue-centric contract", formatted)


class IsInitialBridgeStateTests(unittest.TestCase):
    """is_initial_bridge_state() returns True only for factory-default states."""

    def test_default_state_is_initial(self) -> None:
        state = _bridge_common.DEFAULT_STATE.copy()
        self.assertTrue(_bridge_common.is_initial_bridge_state(state))

    def test_non_idle_mode_is_not_initial(self) -> None:
        state = _bridge_common.DEFAULT_STATE.copy()
        state["mode"] = "awaiting_user"
        self.assertFalse(_bridge_common.is_initial_bridge_state(state))

    def test_error_true_is_not_initial(self) -> None:
        state = _bridge_common.DEFAULT_STATE.copy()
        state["error"] = True
        self.assertFalse(_bridge_common.is_initial_bridge_state(state))

    def test_pending_request_hash_is_not_initial(self) -> None:
        state = _bridge_common.DEFAULT_STATE.copy()
        state["pending_request_hash"] = "some-hash"
        self.assertFalse(_bridge_common.is_initial_bridge_state(state))

    def test_pending_generation_id_is_not_initial(self) -> None:
        state = _bridge_common.DEFAULT_STATE.copy()
        state["last_issue_centric_pending_generation_id"] = "gen-1"
        self.assertFalse(_bridge_common.is_initial_bridge_state(state))


class StartBridgeResetFlagTests(unittest.TestCase):
    """--reset flag is separate from --clear-error and resets state to factory default."""

    def test_reset_flag_is_distinct_from_clear_error(self) -> None:
        # --clear-error should set clear_error=True, reset=False
        args = start_bridge.parse_args(["--clear-error"])
        self.assertTrue(args.clear_error)
        self.assertFalse(getattr(args, "reset", False))

    def test_reset_flag_sets_reset_true(self) -> None:
        args = start_bridge.parse_args(["--reset"])
        self.assertTrue(getattr(args, "reset", False))
        self.assertFalse(args.clear_error)

    def test_reset_is_not_alias_of_clear_error(self) -> None:
        # Passing --reset must NOT set clear_error
        args = start_bridge.parse_args(["--reset"])
        self.assertFalse(args.clear_error)


if __name__ == "__main__":
    unittest.main()
