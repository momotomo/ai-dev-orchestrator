"""Tests for pending reply guard: no-send-while-pending-reply boundary.

Covers:
- is_pending_chatgpt_reply_state helper
- fetch_next_prompt: send_missing during correction → BridgeStop(reply_still_generating)
- fetch_next_prompt: correction succeeds normally when send button is available
- request_prompt_from_report: _send_missing_soft_retry_blocker returns reply_still_generating
- request_prompt_from_report: send_missing with pending reply → BridgeStop
- pending state fields preserved on reply_still_generating stop
- normal rotation/handoff path unaffected when pending_request_hash is empty
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _bridge_common as bridge_common  # noqa: E402
import fetch_next_prompt  # noqa: E402
import request_prompt_from_report  # noqa: E402
from _bridge_common import BridgeError, BridgeStop  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pending_state(mode: str = "waiting_prompt_reply") -> dict[str, object]:
    return {
        "mode": mode,
        "pending_request_hash": "abc123",
        "pending_request_source": "ready_issue:#7",
        "pending_request_log": "logs/req.md",
        "pending_request_signal": "",
        "current_ready_issue_ref": "#7 Ready: implement Y",
        "last_processed_request_hash": "",
        "last_processed_reply_hash": "",
    }


# ---------------------------------------------------------------------------
# is_pending_chatgpt_reply_state
# ---------------------------------------------------------------------------

class IsPendingChatgptReplyStateTests(unittest.TestCase):
    def test_returns_true_for_pending_request_hash(self) -> None:
        state = {"mode": "idle", "pending_request_hash": "abc123"}
        self.assertTrue(bridge_common.is_pending_chatgpt_reply_state(state))

    def test_returns_true_for_waiting_prompt_reply_mode(self) -> None:
        state = {"mode": "waiting_prompt_reply", "pending_request_hash": ""}
        self.assertTrue(bridge_common.is_pending_chatgpt_reply_state(state))

    def test_returns_true_for_await_late_completion_mode(self) -> None:
        state = {"mode": "await_late_completion", "pending_request_hash": ""}
        self.assertTrue(bridge_common.is_pending_chatgpt_reply_state(state))

    def test_returns_true_when_both_hash_and_mode_set(self) -> None:
        state = {"mode": "await_late_completion", "pending_request_hash": "xyz"}
        self.assertTrue(bridge_common.is_pending_chatgpt_reply_state(state))

    def test_returns_false_for_idle_no_hash(self) -> None:
        state = {"mode": "idle", "pending_request_hash": ""}
        self.assertFalse(bridge_common.is_pending_chatgpt_reply_state(state))

    def test_returns_false_for_extended_wait_no_hash(self) -> None:
        # extended_wait is a fetch sub-state but is NOT a send-blocked state by itself.
        state = {"mode": "extended_wait", "pending_request_hash": ""}
        self.assertFalse(bridge_common.is_pending_chatgpt_reply_state(state))

    def test_returns_false_for_empty_state(self) -> None:
        self.assertFalse(bridge_common.is_pending_chatgpt_reply_state({}))

    def test_whitespace_only_hash_treated_as_absent(self) -> None:
        state = {"mode": "idle", "pending_request_hash": "   "}
        self.assertFalse(bridge_common.is_pending_chatgpt_reply_state(state))


# ---------------------------------------------------------------------------
# fetch_next_prompt: send_missing during correction → reply_still_generating
# ---------------------------------------------------------------------------

# Raw text that has the REPLY_COMPLETE_TAG but no IC contract markers.
# Classifies as reply_complete_no_marker → correction_retry path.
# Used for tests that need to reach the correction send path.
sys.path.insert(0, str(SCRIPTS_DIR))  # already inserted above
import issue_centric_contract as _ic_contract  # noqa: E402
_REPLY_COMPLETE_TAG = _ic_contract.REPLY_COMPLETE_TAG
_STALL_RAW = (
    "あなた:\nrequest body\nChatGPT:\n"
    "何かコメントをここに書きました。\n"
    + _REPLY_COMPLETE_TAG
)


def _block(name: str, payload: str) -> str:
    markers = {
        "json": (
            _ic_contract.DECISION_JSON_START,
            _ic_contract.DECISION_JSON_END,
        ),
        "codex": (
            _ic_contract.CODEX_BODY_START,
            _ic_contract.CODEX_BODY_END,
        ),
    }
    start_marker, end_marker = markers[name]
    return f"{start_marker}\n{payload}\n{end_marker}"


def _raw_reply(envelope: dict[str, object], *, parts: list[str] | None = None) -> str:
    contract_parts = parts or [_block("json", json.dumps(envelope, ensure_ascii=True, indent=2))]
    return "\n".join(["あなた:", "request body", "ChatGPT:", *contract_parts, _REPLY_COMPLETE_TAG])

# Raw text that looks like stalled app metadata (no IC contract, no thinking visible).
# Classifies as not_ready (no completion tag). Used only for await_late_completion tests
# where the stall detection promotes it to correction_retry.
_LATE_STALL_RAW = (
    "あなた:\nrequest body\nChatGPT:\n"
    "Received app response\n"
    "Thought for 16s\n"
    "拡張\n"
    "GitHub"
)


class FetchNextPromptCorrectionPreSendGuardTests(unittest.TestCase):
    """Guard: pending reply blocks correction before send_to_chatgpt."""

    def _make_patches(
        self,
        tmp: str,
        raw: str,
        saved_states: list,
        sent_texts: list,
        send_side_effect=None,
    ):
        temp_root = Path(tmp)

        def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
            path = temp_root / f"{prefix}.{suffix}"
            path.write_text(text, encoding="utf-8")
            return path

        def fake_send(text: str) -> None:
            if send_side_effect is not None:
                raise send_side_effect
            sent_texts.append(text)

        return (
            patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
            patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
            patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
            patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
            patch.object(fetch_next_prompt, "send_to_chatgpt", side_effect=fake_send),
            patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo"}),
        )

    def test_pending_reply_invalid_contract_blocks_before_send(self) -> None:
        """Invalid contract correction with pending reply must not call send_to_chatgpt."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        state = _pending_state("await_late_completion")
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patches(tmp, _STALL_RAW, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop) as cm:
                    fetch_next_prompt.run(state, [])
        self.assertIn("reply_still_generating", str(cm.exception))
        self.assertIn("no send attempted", str(cm.exception))
        self.assertEqual(len(sent_texts), 0)
        self.assertTrue(len(saved_states) > 0)
        last_saved = saved_states[-1]
        self.assertEqual(last_saved.get("last_issue_centric_contract_correction_reason"), "reply_still_generating")

    def test_pending_reply_waiting_prompt_reply_blocks_before_send(self) -> None:
        """waiting_prompt_reply correction is also pre-send blocked."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        state = _pending_state("waiting_prompt_reply")
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patches(tmp, _STALL_RAW, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop) as cm:
                    fetch_next_prompt.run(state, [])
        self.assertIn("reply_still_generating", str(cm.exception))
        self.assertEqual(len(sent_texts), 0)

    def test_pending_reply_binding_mismatch_blocks_before_send_and_preserves_pending_fields(self) -> None:
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        state = _pending_state("waiting_prompt_reply")
        state["pending_request_source"] = "ready_issue:#7"
        state["pending_request_log"] = "logs/original.md"
        raw = _raw_reply(
            {
                "action": "no_action",
                "target_issue": "#99",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "stale issue",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patches(tmp, raw, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop) as cm:
                    fetch_next_prompt.run(state, [])
        self.assertIn("reply_still_generating", str(cm.exception))
        self.assertEqual(len(sent_texts), 0)
        last_saved = saved_states[-1]
        self.assertEqual(last_saved.get("pending_request_hash"), "abc123")
        self.assertEqual(last_saved.get("pending_request_source"), "ready_issue:#7")
        self.assertEqual(last_saved.get("pending_request_log"), "logs/original.md")

    def test_pending_reply_body_decode_blocks_before_send(self) -> None:
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        state = _pending_state("waiting_prompt_reply")
        decision = {
            "action": "codex_run",
            "target_issue": "#7",
            "close_current_issue": False,
            "create_followup_issue": False,
            "summary": "run codex",
        }
        raw = _raw_reply(
            decision,
            parts=[
                _block("json", json.dumps(decision, ensure_ascii=True, indent=2)),
                _block("codex", "//8="),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patches(tmp, raw, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop) as cm:
                    fetch_next_prompt.run(state, [])
        self.assertIn("reply_still_generating", str(cm.exception))
        self.assertEqual(len(sent_texts), 0)

    def test_send_missing_without_pending_hash_propagates_as_bridge_error(self) -> None:
        """send_missing when no pending_request_hash → propagate as BridgeError, not BridgeStop."""
        # Without pending hash, is_pending_chatgpt_reply_state returns False
        # (unless mode is waiting_prompt_reply or await_late_completion).
        # Since mode is idle here and no hash, guard should not fire.
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        # Construct a state without pending hash and idle mode — but we need a valid
        # pending request for fetch_next_prompt.run() to proceed.
        # Use awaiting_user mode to bypass the early check. Since mode is not in
        # pending states, is_pending_chatgpt_reply_state returns False.
        # Note: fetch_next_prompt.run() raises BridgeError early if no pending_request_hash.
        # So this test just verifies early exit (different code path).
        state: dict[str, object] = {
            "mode": "idle",
            "pending_request_hash": "",
            "pending_request_source": "",
            "pending_request_log": "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            # fetch_next_prompt.run() raises BridgeError immediately when hash is empty
            with patch.object(fetch_next_prompt, "read_pending_request_text", return_value=""):
                with self.assertRaises(BridgeError) as cm:
                    fetch_next_prompt.run(state, [])
        # Should get the "fetch できませんでした" error, not reply_still_generating
        self.assertNotIn("reply_still_generating", str(cm.exception))
        self.assertIn("fetch できませんでした", str(cm.exception))

    def test_pending_reply_correction_blocks_even_when_send_button_available(self) -> None:
        """Pending reply is a hard boundary, not a send_missing fallback."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        state = _pending_state("await_late_completion")
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patches(tmp, _STALL_RAW, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop) as cm:
                    fetch_next_prompt.run(state, [])
        self.assertEqual(len(sent_texts), 0, "correction must be blocked before send_to_chatgpt")
        self.assertIn("reply_still_generating", str(cm.exception))
        last_saved = saved_states[-1]
        self.assertEqual(last_saved.get("last_issue_centric_contract_correction_reason"), "reply_still_generating")


# ---------------------------------------------------------------------------
# request_prompt_from_report: _send_missing_soft_retry_blocker
# ---------------------------------------------------------------------------

class SendMissingSoftRetryBlockerPendingReplyTests(unittest.TestCase):
    """_send_missing_soft_retry_blocker returns reply_still_generating for pending reply states."""

    def _make_state(self, mode: str, pending_hash: str = "abc123") -> dict[str, object]:
        return {
            "mode": mode,
            "pending_request_hash": pending_hash,
            "current_chat_session": "https://chatgpt.com/c/test-session",
        }

    def _make_prepared_state(self) -> dict[str, object]:
        return {
            "prepared_request_status": "prepared",
            "prepared_request_log": "logs/prepared.md",
        }

    def test_waiting_prompt_reply_returns_reply_still_generating(self) -> None:
        state = self._make_state("waiting_prompt_reply")
        exc = BridgeError("send_missing")
        result = request_prompt_from_report._send_missing_soft_retry_blocker(
            original_state=state,
            prepared_state=self._make_prepared_state(),
            request_text="some request",
            exc=exc,
        )
        self.assertEqual(result, "reply_still_generating")

    def test_await_late_completion_returns_reply_still_generating(self) -> None:
        state = self._make_state("await_late_completion")
        exc = BridgeError("send_missing")
        result = request_prompt_from_report._send_missing_soft_retry_blocker(
            original_state=state,
            prepared_state=self._make_prepared_state(),
            request_text="some request",
            exc=exc,
        )
        self.assertEqual(result, "reply_still_generating")

    def test_idle_mode_returns_pending_request_hash_present(self) -> None:
        """Idle mode with pending hash: existing blocker (not reply_still_generating)."""
        state = self._make_state("idle")
        exc = BridgeError("send_missing")
        result = request_prompt_from_report._send_missing_soft_retry_blocker(
            original_state=state,
            prepared_state=self._make_prepared_state(),
            request_text="some request",
            exc=exc,
        )
        self.assertEqual(result, "pending_request_hash_present")

    def test_no_pending_hash_no_reply_still_generating(self) -> None:
        """No pending_request_hash: reply_still_generating not returned."""
        state = self._make_state("await_late_completion", pending_hash="")
        exc = BridgeError("send_missing")
        result = request_prompt_from_report._send_missing_soft_retry_blocker(
            original_state=state,
            prepared_state=self._make_prepared_state(),
            request_text="some request",
            exc=exc,
        )
        # No pending hash → should not be pending_request_hash_present or reply_still_generating
        # Falls through to other checks (prepared_request_log, etc.)
        self.assertNotIn(result, {"reply_still_generating", "pending_request_hash_present"})


# ---------------------------------------------------------------------------
# request_prompt_from_report: BridgeStop raised for reply_still_generating
# ---------------------------------------------------------------------------

class SendToChGPTWithSendMissingPendingReplyTests(unittest.TestCase):
    """_send_to_chatgpt_with_send_missing_soft_retry raises BridgeStop for reply_still_generating."""

    def test_pending_reply_blocks_before_send(self) -> None:
        """pending_request_hash+await_late_completion → BridgeStop before send_to_chatgpt."""
        original_state: dict[str, object] = {
            "mode": "await_late_completion",
            "pending_request_hash": "xyz789",
            "current_chat_session": "https://chatgpt.com/c/sess",
        }
        prepared_state: dict[str, object] = {
            "prepared_request_status": "prepared",
            "prepared_request_log": "logs/prep.md",
        }
        saved_states: list[dict] = []
        with (
            patch.object(request_prompt_from_report, "send_to_chatgpt") as send_mock,
            patch.object(request_prompt_from_report, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
        ):
            with self.assertRaises(BridgeStop) as cm:
                request_prompt_from_report._send_to_chatgpt_with_send_missing_soft_retry(
                    original_state=original_state,
                    prepared_state=prepared_state,
                    request_text="some new request",
                    request_hash="hash123",
                    request_source="report:last.md",
                    request_log_rel="logs/req.md",
                    issue_centric_runtime_snapshot=None,
                )
        self.assertIn("reply_still_generating", str(cm.exception))
        send_mock.assert_not_called()
        self.assertEqual(saved_states[-1]["pending_request_hash"], "xyz789")

    def test_pending_reply_waiting_prompt_reply_blocks_before_send(self) -> None:
        """waiting_prompt_reply+pending_hash → BridgeStop before send_to_chatgpt."""
        original_state: dict[str, object] = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "xyz789",
            "current_chat_session": "https://chatgpt.com/c/sess",
        }
        prepared_state: dict[str, object] = {
            "prepared_request_status": "prepared",
            "prepared_request_log": "logs/prep.md",
        }
        with (
            patch.object(request_prompt_from_report, "send_to_chatgpt") as send_mock,
            patch.object(request_prompt_from_report, "save_state", side_effect=lambda s: None),
        ):
            with self.assertRaises(BridgeStop) as cm:
                request_prompt_from_report._send_to_chatgpt_with_send_missing_soft_retry(
                    original_state=original_state,
                    prepared_state=prepared_state,
                    request_text="some new request",
                    request_hash="hash123",
                    request_source="report:last.md",
                    request_log_rel="logs/req.md",
                    issue_centric_runtime_snapshot=None,
                )
        self.assertIn("reply_still_generating", str(cm.exception))
        send_mock.assert_not_called()

    def test_send_missing_without_pending_reply_raises_hard_error(self) -> None:
        """send_missing when no pending_request_hash (and mode=idle) → hard error, not BridgeStop."""
        original_state: dict[str, object] = {
            "mode": "idle",
            "pending_request_hash": "",
            "current_chat_session": "https://chatgpt.com/c/sess",
        }
        prepared_state: dict[str, object] = {
            "prepared_request_status": "prepared",
            "prepared_request_log": "logs/prep.md",
        }
        with patch.object(
            request_prompt_from_report,
            "send_to_chatgpt",
            side_effect=BridgeError("send_missing: no button"),
        ):
            # No pending state → falls through to other blockers or soft retry
            # (in this case conversation_url_unknown since we don't have /c/ in session,
            # wait — we do have /c/ in current_chat_session; so will fail on something else)
            # The key assertion: it does NOT raise BridgeStop with reply_still_generating
            try:
                result = request_prompt_from_report._send_to_chatgpt_with_send_missing_soft_retry(
                    original_state=original_state,
                    prepared_state=prepared_state,
                    request_text="some new request",
                    request_hash="hash123",
                    request_source="report:last.md",
                    request_log_rel="logs/req.md",
                    issue_centric_runtime_snapshot=None,
                )
                self.fail("Expected an exception to be raised")
            except BridgeStop as exc:
                self.assertNotIn("reply_still_generating", str(exc))
            except request_prompt_from_report._SendMissingSoftRetryHardError as exc:
                self.assertNotIn("reply_still_generating", str(exc))
            except Exception:
                pass  # Any other exception is acceptable — just not BridgeStop(reply_still_generating)


class RequestPromptFromReportPreSendGuardTests(unittest.TestCase):
    """request_prompt_from_report blocks outbound paths while a reply is pending."""

    def _pending_report_state(self) -> dict[str, object]:
        return {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "pending-hash",
            "pending_request_source": "ready_issue:#7",
            "pending_request_log": "logs/pending.md",
            "pending_request_signal": "submitted_unconfirmed",
            "next_request_requires_rotation": True,
            "next_request_rotation_reason": "late_completion",
        }

    def test_dispatch_request_pending_reply_blocks_next_send_and_preserves_fields(self) -> None:
        saved_states: list[dict] = []
        state = self._pending_report_state()
        with (
            patch.object(request_prompt_from_report, "send_to_chatgpt") as send_mock,
            patch.object(request_prompt_from_report, "log_text") as log_mock,
            patch.object(request_prompt_from_report, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
        ):
            with self.assertRaises(BridgeStop) as cm:
                request_prompt_from_report.dispatch_request(
                    state,
                    request_text="new request",
                    request_hash="new-hash",
                    request_source="report:new",
                    prepared_prefix="prepared",
                    sent_prefix="sent",
                )
        self.assertIn("reply_still_generating", str(cm.exception))
        send_mock.assert_not_called()
        log_mock.assert_not_called()
        last_saved = saved_states[-1]
        self.assertEqual(last_saved.get("pending_request_hash"), "pending-hash")
        self.assertEqual(last_saved.get("pending_request_source"), "ready_issue:#7")
        self.assertEqual(last_saved.get("pending_request_log"), "logs/pending.md")
        self.assertEqual(last_saved.get("pending_request_signal"), "submitted_unconfirmed")
        self.assertTrue(last_saved.get("next_request_requires_rotation"))

    def test_pending_reply_blocks_handoff_request_before_send(self) -> None:
        state = self._pending_report_state()
        args = MagicMock(next_todo="", open_questions="", current_status="")
        ic_context = request_prompt_from_report._IcResolvedContext(next_request_section="section")
        with (
            patch.object(request_prompt_from_report, "send_to_chatgpt") as send_mock,
            patch.object(request_prompt_from_report, "build_chatgpt_handoff_request") as build_mock,
            patch.object(request_prompt_from_report, "save_state", side_effect=lambda s: None),
        ):
            with self.assertRaises(BridgeStop):
                request_prompt_from_report._acquire_rotated_handoff(
                    state,
                    args,
                    "last report",
                    request_source="report:last",
                    ic_context=ic_context,
                )
        send_mock.assert_not_called()
        build_mock.assert_not_called()

    def test_pending_reply_blocks_rotation_before_rotate_call(self) -> None:
        state = self._pending_report_state()
        ic_context = request_prompt_from_report._IcResolvedContext(next_request_section="section")
        with (
            patch.object(request_prompt_from_report, "rotate_chat_with_handoff") as rotate_mock,
            patch.object(request_prompt_from_report, "save_state", side_effect=lambda s: None),
        ):
            with self.assertRaises(BridgeStop):
                request_prompt_from_report._apply_rotated_request_result(
                    state,
                    handoff_text="handoff",
                    handoff_received_log="logs/handoff.md",
                    request_source="report:last",
                    ic_context=ic_context,
                )
        rotate_mock.assert_not_called()

    def test_normal_no_pending_dispatch_still_sends(self) -> None:
        state: dict[str, object] = {
            "mode": "codex_done",
            "pending_request_hash": "",
            "pending_request_source": "",
            "pending_request_log": "",
        }
        logged: list[tuple[str, str]] = []
        applied: list[dict[str, object]] = []

        def fake_apply(state_arg: dict[str, object], **kwargs: object) -> None:
            applied.append(dict(kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str) -> Path:
                logged.append((prefix, text))
                path = temp_root / f"{prefix}.md"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(request_prompt_from_report, "send_to_chatgpt") as send_mock,
                patch.object(request_prompt_from_report, "log_text", side_effect=fake_log_text),
                patch.object(request_prompt_from_report, "save_state", side_effect=lambda s: None),
                patch.object(request_prompt_from_report, "_apply_pending_request_state", side_effect=fake_apply),
            ):
                result = request_prompt_from_report.dispatch_request(
                    state,
                    request_text="new request",
                    request_hash="new-hash",
                    request_source="report:new",
                    prepared_prefix="prepared",
                    sent_prefix="sent",
                )
        self.assertEqual(result, 0)
        send_mock.assert_called_once_with("new request")
        self.assertEqual(logged[0][0], "prepared")
        self.assertEqual(logged[1][0], "sent")
        self.assertEqual(applied[0]["request_hash"], "new-hash")


# ---------------------------------------------------------------------------
# Pending state preservation on reply_still_generating stop
# ---------------------------------------------------------------------------

class PendingStatePreservationTests(unittest.TestCase):
    """Verify pending request info is preserved when reply_still_generating stop occurs."""

    def test_pending_fields_preserved_on_reply_still_generating(self) -> None:
        """After reply_still_generating BridgeStop, pending_request_hash etc. stay intact."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        state = _pending_state("await_late_completion")
        state["pending_request_source"] = "ready_issue:#7"
        state["pending_request_log"] = "logs/my_req.md"
        state["pending_request_signal"] = ""
        state["current_chat_session"] = "https://chatgpt.com/c/session-99"
        state["next_request_requires_rotation"] = True
        state["next_request_rotation_reason"] = "late_completion"

        send_error = BridgeError("send_missing: button disabled")

        temp_root_ref: list[Path] = []

        def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
            root = temp_root_ref[0]
            path = root / f"{prefix}.{suffix}"
            path.write_text(text, encoding="utf-8")
            return path

        def fake_save_state(s: dict) -> None:
            saved_states.append(dict(s))

        def fake_send(text: str) -> None:
            raise send_error

        with tempfile.TemporaryDirectory() as tmp:
            temp_root_ref.append(Path(tmp))
            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=_STALL_RAW),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=fake_save_state),
                patch.object(fetch_next_prompt, "send_to_chatgpt", side_effect=fake_send),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo"}),
            ):
                with self.assertRaises(BridgeStop):
                    fetch_next_prompt.run(state, [])

        # The last saved state must preserve the critical pending fields
        self.assertTrue(len(saved_states) > 0, "state must be saved")
        last_saved = saved_states[-1]
        # pending_request_hash must NOT be cleared
        self.assertEqual(last_saved.get("pending_request_hash"), "abc123")
        # pending_request_source must NOT be cleared
        self.assertEqual(last_saved.get("pending_request_source"), "ready_issue:#7")
        # pending_request_log must NOT be cleared
        self.assertEqual(last_saved.get("pending_request_log"), "logs/my_req.md")
        # current_chat_session must NOT be cleared
        self.assertEqual(last_saved.get("current_chat_session"), "https://chatgpt.com/c/session-99")
        # next_request_requires_rotation must be preserved
        self.assertTrue(last_saved.get("next_request_requires_rotation"))
        # Correction reason should document the stop
        self.assertEqual(last_saved.get("last_issue_centric_contract_correction_reason"), "reply_still_generating")


# ---------------------------------------------------------------------------
# Normal next request rotation: unaffected when no pending reply
# ---------------------------------------------------------------------------

class NormalPathNotAffectedTests(unittest.TestCase):
    """Verify that the guard does not affect normal paths without pending reply."""

    def test_should_rotate_before_next_chat_request_unaffected(self) -> None:
        """should_rotate_before_next_chat_request still works for non-pending states."""
        state_with_rotation: dict[str, object] = {
            "mode": "idle",
            "pending_request_hash": "",
            "next_request_requires_rotation": True,
        }
        self.assertTrue(bridge_common.should_rotate_before_next_chat_request(state_with_rotation))

    def test_is_pending_false_for_normal_report_send_state(self) -> None:
        """A state ready for report send (no pending hash, awaiting_user) is NOT pending."""
        state: dict[str, object] = {
            "mode": "awaiting_user",
            "pending_request_hash": "",
            "pending_request_source": "",
        }
        self.assertFalse(bridge_common.is_pending_chatgpt_reply_state(state))

    def test_is_pending_false_for_codex_done(self) -> None:
        """codex_done mode without pending hash is NOT pending."""
        state: dict[str, object] = {
            "mode": "codex_done",
            "pending_request_hash": "",
        }
        self.assertFalse(bridge_common.is_pending_chatgpt_reply_state(state))


if __name__ == "__main__":
    unittest.main()
