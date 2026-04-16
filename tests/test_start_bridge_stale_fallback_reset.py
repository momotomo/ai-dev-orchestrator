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


if __name__ == "__main__":
    unittest.main()
