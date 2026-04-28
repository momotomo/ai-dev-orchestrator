"""Tests for delivery-signal issue-centric view mapping.

Verifies that is_issue_centric_delivery_pending_state() helper returns correct
(bool, target_issue) pairs, and that present_bridge_status() /
suggested_next_note() surface target_issue context when the helper returns True.

Five scenarios:
1. ic+submitted_unconfirmed → enriched wording with target_issue
2. ic+extended_wait        → enriched wording with target_issue
3. ic+await_late_completion → enriched wording with target_issue
4. ic+no_signal            → conventional delivery path, no enrichment
5. legacy+submitted_unconfirmed → legacy (no issue-centric) wording unchanged
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _bridge_common import (  # noqa: E402
    ic_delivery_pending_detail,
    is_issue_centric_delivery_pending_state,
    present_bridge_status,
)
from issue_centric_normalized_summary import IssueCentricRuntimeMode  # noqa: E402


def make_ic_ready_mode(target_issue: str = "#28") -> IssueCentricRuntimeMode:
    """Return a minimal IssueCentricRuntimeMode that represents issue_centric_ready."""
    return IssueCentricRuntimeMode(
        runtime_mode="issue_centric_ready",
        runtime_mode_reason="issue_centric_snapshot_ready",
        runtime_mode_source="runtime_snapshot",
        generation_lifecycle="issue_centric_active",
        generation_lifecycle_reason="issue_centric_snapshot_ready",
        generation_lifecycle_source="runtime_snapshot",
        freshness_status="issue_centric_fresh",
        freshness_reason="issue_centric_snapshot_ready",
        freshness_source="runtime_snapshot",
        invalidation_status="",
        invalidation_reason="",
        snapshot_status="issue_centric_snapshot_ready",
        snapshot_source="execution_finalize",
        generation_id="summary:logs/summary.json",
        route_selected="issue_centric",
        recovery_status="",
        recovery_source="",
        fallback_reason="",
        principal_issue="https://github.com/example/repo/issues/28",
        principal_issue_kind="primary_issue",
        target_issue=target_issue,
        target_issue_source="normalized_summary",
        next_request_hint="continue_on_primary_issue",
        normalized_summary_path="",
        dispatch_result_path="",
        snapshot_path="",
    )


def make_ic_degraded_mode() -> IssueCentricRuntimeMode:
    """Return a minimal IssueCentricRuntimeMode that represents issue_centric_degraded_fallback."""
    return IssueCentricRuntimeMode(
        runtime_mode="issue_centric_degraded_fallback",
        runtime_mode_reason="route_selected_fallback",
        runtime_mode_source="runtime_snapshot",
        generation_lifecycle="issue_centric_invalidated",
        generation_lifecycle_reason="route_selected_fallback",
        generation_lifecycle_source="runtime_snapshot",
        freshness_status="issue_centric_stale",
        freshness_reason="route_selected_fallback",
        freshness_source="runtime_snapshot",
        invalidation_status="issue_centric_invalidated",
        invalidation_reason="route_selected_fallback",
        snapshot_status="issue_centric_snapshot_ready",
        snapshot_source="execution_finalize",
        generation_id="summary:logs/summary.json",
        route_selected="fallback_legacy",
        recovery_status="",
        recovery_source="",
        fallback_reason="legacy_fallback_selected",
        principal_issue="https://github.com/example/repo/issues/28",
        principal_issue_kind="primary_issue",
        target_issue="#28",
        target_issue_source="normalized_summary",
        next_request_hint="continue_on_primary_issue",
        normalized_summary_path="",
        dispatch_result_path="",
        snapshot_path="",
    )


_IC_STATE_BASE: dict = {
    "mode": "waiting_prompt_reply",
    "pending_request_signal": "",
}


class IsIcentricDeliveryPendingStateHelperTests(unittest.TestCase):
    """Unit tests for is_issue_centric_delivery_pending_state()."""

    def test_submitted_unconfirmed_with_ic_ready_returns_true_and_issue(self) -> None:
        """submitted_unconfirmed + issue_centric_ready → (True, target_issue)."""
        state = {**_IC_STATE_BASE, "pending_request_signal": "submitted_unconfirmed"}
        ic_mode = make_ic_ready_mode("#28")
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=ic_mode):
            result, target = is_issue_centric_delivery_pending_state(state)
        self.assertTrue(result)
        self.assertEqual(target, "#28")

    def test_extended_wait_with_ic_ready_returns_true_and_issue(self) -> None:
        """mode==extended_wait + issue_centric_ready → (True, target_issue)."""
        state = {**_IC_STATE_BASE, "mode": "extended_wait", "pending_request_signal": ""}
        ic_mode = make_ic_ready_mode("#28")
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=ic_mode):
            result, target = is_issue_centric_delivery_pending_state(state)
        self.assertTrue(result)
        self.assertEqual(target, "#28")

    def test_await_late_completion_with_ic_ready_returns_true_and_issue(self) -> None:
        """mode==await_late_completion + issue_centric_ready → (True, target_issue)."""
        state = {**_IC_STATE_BASE, "mode": "await_late_completion", "pending_request_signal": ""}
        ic_mode = make_ic_ready_mode("#28")
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=ic_mode):
            result, target = is_issue_centric_delivery_pending_state(state)
        self.assertTrue(result)
        self.assertEqual(target, "#28")

    def test_no_signal_returns_false_empty(self) -> None:
        """No delivery-pending signal present → (False, '') regardless of runtime mode."""
        state = {**_IC_STATE_BASE, "mode": "waiting_prompt_reply", "pending_request_signal": ""}
        ic_mode = make_ic_ready_mode("#28")
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=ic_mode):
            result, target = is_issue_centric_delivery_pending_state(state)
        self.assertFalse(result)
        self.assertEqual(target, "")

    def test_submitted_unconfirmed_without_snapshot_returns_false(self) -> None:
        """submitted_unconfirmed + no IC snapshot (legacy path) → (False, '')."""
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "submitted_unconfirmed",
        }
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=None):
            result, target = is_issue_centric_delivery_pending_state(state)
        self.assertFalse(result)
        self.assertEqual(target, "")

    def test_submitted_unconfirmed_with_degraded_mode_returns_false(self) -> None:
        """submitted_unconfirmed + issue_centric_degraded_fallback → (False, '').

        Only issue_centric_ready triggers enrichment; degraded / unavailable do not.
        """
        state = {**_IC_STATE_BASE, "pending_request_signal": "submitted_unconfirmed"}
        degraded = make_ic_degraded_mode()
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=degraded):
            result, target = is_issue_centric_delivery_pending_state(state)
        self.assertFalse(result)
        self.assertEqual(target, "")


class IcentricDeliveryStatusViewTests(unittest.TestCase):
    """Tests that present_bridge_status() surfaces target_issue in delivery-pending messages."""

    def test_submitted_unconfirmed_ic_ready_includes_target_issue(self) -> None:
        """submitted_unconfirmed + IC ready → detail includes target_issue."""
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "submitted_unconfirmed",
        }
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(True, "#28"),
        ):
            view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("#28", view.detail)
        self.assertIn("delivery pending", view.detail)

    def test_extended_wait_ic_ready_includes_target_issue(self) -> None:
        """extended_wait + IC ready → detail includes target_issue."""
        state = {"mode": "extended_wait"}
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(True, "#28"),
        ):
            view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("#28", view.detail)
        self.assertIn("delivery pending", view.detail)

    def test_await_late_completion_ic_ready_includes_target_issue(self) -> None:
        """await_late_completion + IC ready → detail includes target_issue."""
        state = {"mode": "await_late_completion"}
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(True, "#28"),
        ):
            view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("#28", view.detail)
        self.assertIn("delivery pending", view.detail)

    def test_submitted_unconfirmed_legacy_no_snapshot_uses_legacy_wording(self) -> None:
        """submitted_unconfirmed + legacy (no snapshot) → legacy wording unchanged."""
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "submitted_unconfirmed",
        }
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(False, ""),
        ):
            view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("再送せず", view.detail)
        self.assertNotIn("delivery pending", view.detail)

    def test_waiting_prompt_reply_no_signal_uses_default_wording(self) -> None:
        """fetch path + no delivery-pending signal → default fetch wording."""
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "",
        }
        view = present_bridge_status(state)
        self.assertEqual(view.label, "ChatGPT返答待ち")
        self.assertIn("回収", view.detail)
        self.assertNotIn("delivery pending", view.detail)


class IcentricDeliverySuggestedNoteTests(unittest.TestCase):
    """Tests that suggested_next_note() surfaces target_issue for delivery-pending states."""

    def test_submitted_unconfirmed_ic_ready_includes_target_issue(self) -> None:
        """submitted_unconfirmed + IC ready → suggested note includes target_issue."""
        import run_until_stop

        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "submitted_unconfirmed",
        }
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(True, "#28"),
        ):
            note = run_until_stop.suggested_next_note(state)
        self.assertIn("#28", note)
        self.assertIn("delivery pending", note)

    def test_submitted_unconfirmed_legacy_no_snapshot_uses_legacy_wording(self) -> None:
        """submitted_unconfirmed + legacy → note without delivery pending label."""
        import run_until_stop

        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "submitted_unconfirmed",
        }
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(False, ""),
        ):
            note = run_until_stop.suggested_next_note(state)
        self.assertIn("再送せず", note)
        self.assertNotIn("delivery pending", note)


class IcentricHandoffTitleEnrichmentTests(unittest.TestCase):
    """Tests that present_bridge_handoff() enriches title with target_issue for delivery-pending states."""

    def test_submitted_unconfirmed_ic_ready_enriches_handoff_title(self) -> None:
        """submitted_unconfirmed + IC ready → handoff title includes target_issue."""
        from _bridge_common import present_bridge_handoff

        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "submitted_unconfirmed",
        }
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(True, "#29"),
        ):
            view = present_bridge_handoff(state)
        self.assertIn("#29", view.title)
        self.assertIn("delivery pending", view.title)

    def test_extended_wait_ic_ready_enriches_handoff_title(self) -> None:
        """extended_wait + IC ready → handoff title includes target_issue."""
        from _bridge_common import present_bridge_handoff

        state = {"mode": "extended_wait"}
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(True, "#29"),
        ):
            view = present_bridge_handoff(state)
        self.assertIn("#29", view.title)
        self.assertIn("delivery pending", view.title)

    def test_await_late_completion_ic_ready_enriches_handoff_title(self) -> None:
        """await_late_completion + IC ready → handoff title includes target_issue."""
        from _bridge_common import present_bridge_handoff

        state = {"mode": "await_late_completion"}
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(True, "#29"),
        ):
            view = present_bridge_handoff(state)
        self.assertIn("#29", view.title)
        self.assertIn("delivery pending", view.title)

    def test_submitted_unconfirmed_legacy_handoff_title_unchanged(self) -> None:
        """submitted_unconfirmed + legacy → handoff title is conventional."""
        from _bridge_common import present_bridge_handoff

        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "submitted_unconfirmed",
        }
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(False, ""),
        ):
            view = present_bridge_handoff(state)
        self.assertIn("ChatGPT の返答を待っています", view.title)
        self.assertNotIn("delivery pending", view.title)

    def test_no_delivery_pending_handoff_title_conventional(self) -> None:
        """No delivery-pending signal → conventional handoff title."""
        from _bridge_common import present_bridge_handoff

        state = {"mode": "waiting_prompt_reply"}
        view = present_bridge_handoff(state)
        self.assertEqual(view.title, "ChatGPT の返答を待っています。")


class IcentricErrorPathNoteEnrichmentTests(unittest.TestCase):
    """Tests for run_until_stop.py error-path submitted_unconfirmed note enrichment."""

    def test_submitted_unconfirmed_ic_ready_error_note_includes_target_issue(self) -> None:
        """submitted_unconfirmed + IC ready + error state → error note includes target_issue."""
        import run_until_stop

        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "submitted_unconfirmed",
            "error": True,
            "error_message": "",
        }
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(True, "#29"),
        ):
            result = run_until_stop.blocked_next_guidance(state)
        self.assertIsNotNone(result)
        assert result is not None
        _step, note = result
        self.assertIn("#29", note)
        self.assertIn("delivery pending", note)

    def test_submitted_unconfirmed_legacy_error_note_unchanged(self) -> None:
        """submitted_unconfirmed + legacy + error state → error note without delivery pending."""
        import run_until_stop

        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "submitted_unconfirmed",
            "error": True,
            "error_message": "",
        }
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(False, ""),
        ):
            result = run_until_stop.blocked_next_guidance(state)
        self.assertIsNotNone(result)
        assert result is not None
        _step, note = result
        self.assertIn("reply 回収側", note)
        self.assertNotIn("delivery pending", note)


class IcDeliveryPendingDetailHelperTests(unittest.TestCase):
    """Unit tests for the ic_delivery_pending_detail() shared helper."""

    def test_ic_ready_prefixes_with_issue(self) -> None:
        """IC ready → detail is prefixed with 'issue-centric delivery pending ({issue}): '."""
        state = {"mode": "waiting_prompt_reply", "pending_request_signal": "submitted_unconfirmed"}
        ic_mode = make_ic_ready_mode("#29")
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=ic_mode):
            result = ic_delivery_pending_detail("base text here", state)
        self.assertEqual(result, "issue-centric delivery pending (#29): base text here")

    def test_legacy_path_returns_ic_base_unchanged(self) -> None:
        """Legacy path (no IC snapshot) → ic_base_text returned unchanged."""
        state = {"mode": "extended_wait"}
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=None):
            result = ic_delivery_pending_detail("base text legacy", state)
        self.assertEqual(result, "base text legacy")

    def test_legacy_path_returns_legacy_base_when_provided(self) -> None:
        """Legacy path + legacy_base_text provided → legacy_base_text returned."""
        state = {"pending_request_signal": "submitted_unconfirmed"}
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=None):
            result = ic_delivery_pending_detail(
                "ic text",
                state,
                legacy_base_text="legacy text",
            )
        self.assertEqual(result, "legacy text")

    def test_ic_ready_ignores_legacy_base_text(self) -> None:
        """IC ready + legacy_base_text provided → prefixed ic_base_text returned (legacy ignored)."""
        state = {"pending_request_signal": "submitted_unconfirmed"}
        ic_mode = make_ic_ready_mode("#29")
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=ic_mode):
            result = ic_delivery_pending_detail(
                "ic text",
                state,
                legacy_base_text="legacy text",
            )
        self.assertEqual(result, "issue-centric delivery pending (#29): ic text")

    def test_no_delivery_pending_signal_no_enrichment(self) -> None:
        """No delivery-pending substate active → base text unchanged even if IC is ready."""
        state = {"mode": "waiting_prompt_reply", "pending_request_signal": ""}
        ic_mode = make_ic_ready_mode("#29")
        with patch("_bridge_common.resolve_issue_centric_runtime_mode", return_value=ic_mode):
            result = ic_delivery_pending_detail("no pending text", state)
        self.assertEqual(result, "no pending text")

    def test_multi_surface_reuse_status_and_note(
        self,
    ) -> None:
        """Verify shared helper is used by both present_bridge_status and suggested_next_note.

        When is_issue_centric_delivery_pending_state reports IC delivery pending,
        both surfaces include the target issue in their output, confirming the
        shared ic_delivery_pending_detail helper path is active in both code paths.
        """
        import run_until_stop

        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_signal": "submitted_unconfirmed",
        }
        with patch(
            "_bridge_common.is_issue_centric_delivery_pending_state",
            return_value=(True, "#99"),
        ):
            status_view = present_bridge_status(state)
            note = run_until_stop.suggested_next_note(state)
        self.assertIn("#99", status_view.detail)
        self.assertIn("delivery pending", status_view.detail)
        self.assertIn("#99", note)
        self.assertIn("delivery pending", note)


if __name__ == "__main__":
    unittest.main()
