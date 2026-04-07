from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import issue_centric_normalized_summary  # noqa: E402
import _bridge_common  # noqa: E402
import request_prompt_from_report  # noqa: E402
from _bridge_common import (  # noqa: E402
    build_chatgpt_handoff_request,
    build_chatgpt_request,
    build_issue_centric_request_status,
    recover_prepared_request_state,
)


class IssueCentricNormalizedSummaryTests(unittest.TestCase):
    def test_issue_create_prefers_primary_issue_when_followup_is_absent(self) -> None:
        summary = issue_centric_normalized_summary.build_issue_centric_normalized_summary(
            matrix_path="issue_create",
            final_status="completed",
            state={
                "last_issue_centric_action": "issue_create",
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                "last_issue_centric_primary_issue_number": "51",
                "last_issue_centric_primary_issue_url": "https://github.com/example/repo/issues/51",
                "last_issue_centric_primary_issue_title": "Primary issue",
            },
        )

        self.assertEqual(summary["principal_issue_kind"], "primary_issue")
        self.assertEqual(summary["principal_issue_candidate"]["number"], "51")
        self.assertEqual(summary["next_request_hint"], "continue_on_primary_issue")

    def test_no_action_followup_prefers_followup_issue(self) -> None:
        summary = issue_centric_normalized_summary.build_issue_centric_normalized_summary(
            matrix_path="no_action_followup",
            final_status="completed",
            state={
                "last_issue_centric_action": "no_action",
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                "last_issue_centric_followup_issue_number": "81",
                "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                "last_issue_centric_followup_issue_title": "Follow-up issue",
            },
        )

        self.assertEqual(summary["principal_issue_kind"], "followup_issue")
        self.assertEqual(summary["principal_issue_candidate"]["number"], "81")
        self.assertEqual(summary["next_request_hint"], "continue_on_followup_issue")

    def test_human_review_close_keeps_review_target_and_closed_issue(self) -> None:
        summary = issue_centric_normalized_summary.build_issue_centric_normalized_summary(
            matrix_path="human_review_then_close",
            final_status="completed",
            state={
                "last_issue_centric_action": "human_review_needed",
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                "last_issue_centric_closed_issue_number": "20",
                "last_issue_centric_closed_issue_url": "https://github.com/example/repo/issues/20",
                "last_issue_centric_closed_issue_title": "Current issue",
            },
        )

        self.assertEqual(summary["review_target_issue"]["number"], "20")
        self.assertEqual(summary["closed_issue"]["number"], "20")
        self.assertEqual(summary["next_request_hint"], "issue_resolution_unclear")

    def test_codex_followup_close_prefers_followup_and_keeps_codex_target(self) -> None:
        summary = issue_centric_normalized_summary.build_issue_centric_normalized_summary(
            matrix_path="codex_run_followup_then_close",
            final_status="completed",
            state={
                "last_issue_centric_action": "codex_run",
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                "last_issue_centric_followup_issue_number": "81",
                "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                "last_issue_centric_followup_issue_title": "Follow-up issue",
                "last_issue_centric_closed_issue_number": "20",
                "last_issue_centric_closed_issue_url": "https://github.com/example/repo/issues/20",
                "last_issue_centric_closed_issue_title": "Current issue",
                "last_issue_centric_lifecycle_sync_status": "project_state_synced",
                "last_issue_centric_lifecycle_sync_stage": "done",
            },
        )

        self.assertEqual(summary["principal_issue_kind"], "followup_issue")
        self.assertEqual(summary["principal_issue_candidate"]["number"], "81")
        self.assertEqual(summary["codex_target_issue"]["number"], "20")
        self.assertEqual(summary["closed_issue"]["number"], "20")
        self.assertEqual(summary["project_lifecycle_sync"]["stage"], "done")

    def test_partial_reason_is_retained_when_main_action_partially_succeeds(self) -> None:
        summary = issue_centric_normalized_summary.build_issue_centric_normalized_summary(
            matrix_path="codex_run",
            final_status="partial",
            state={
                "last_issue_centric_action": "codex_run",
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                "last_issue_centric_stop_reason": "main action succeeded but lifecycle sync failed",
                "last_issue_centric_lifecycle_sync_status": "blocked_project_state_sync",
                "last_issue_centric_lifecycle_sync_stage": "in_progress",
            },
        )

        self.assertEqual(summary["partial_reason"], "main action succeeded but lifecycle sync failed")
        self.assertEqual(summary["principal_issue_kind"], "current_issue")

    def test_request_builder_includes_saved_issue_centric_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "codex_run",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "current_issue": {
                            "number": "20",
                            "url": "https://github.com/example/repo/issues/20",
                            "title": "",
                            "ref": "example/repo#20",
                        },
                        "created_followup_issue": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "closed_issue": {
                            "number": "20",
                            "url": "https://github.com/example/repo/issues/20",
                            "title": "Current issue",
                            "ref": "#20",
                        },
                        "project_lifecycle_sync": {
                            "status": "project_state_synced",
                            "stage": "done",
                        },
                        "blocked_reason": "",
                        "partial_reason": "",
                        "next_request_hint": "continue_on_followup_issue",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            template_path = root / "request_template.md"
            template_path.write_text(
                "STATE\n{CURRENT_STATUS}\n\n{ISSUE_CENTRIC_NEXT_REQUEST_SECTION}\n",
                encoding="utf-8",
            )

            request = build_chatgpt_request(
                state={
                    "mode": "idle",
                    "need_chatgpt_prompt": False,
                    "need_chatgpt_next": True,
                    "need_codex_run": False,
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_followup_issue_number": "81",
                    "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                },
                template_path=template_path,
                next_todo="next",
                open_questions="none",
                last_report="===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
            )

            self.assertIn("## issue_centric_summary", request)
            self.assertIn("- runtime_mode: issue_centric_ready", request)
            self.assertIn("## issue_centric_runtime_snapshot", request)
            self.assertIn("issue_centric_snapshot_status: issue_centric_snapshot_ready", request)
            self.assertIn("issue_centric_principal_issue_kind: followup_issue", request)
            self.assertIn("issue_centric_next_request_hint: continue_on_followup_issue", request)
            self.assertIn("issue_centric_principal_issue: #81 https://github.com/example/repo/issues/81 (Follow-up issue)", request)
            self.assertIn("## issue_centric_next_request", request)
            self.assertIn("- next_request_route: issue_centric", request)
            self.assertIn("- target_issue: https://github.com/example/repo/issues/81", request)
            self.assertIn("- target_issue_source: normalized_summary", request)
            self.assertIn("## issue_centric_state_bridge", request)
            self.assertIn("issue_centric_state_view", request)

    def test_request_builder_falls_back_when_snapshot_generation_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "no_action",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "created_followup_issue": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )
            template_path = root / "request_template.md"
            template_path.write_text(
                "STATE\n{CURRENT_STATUS}\n\n{ISSUE_CENTRIC_NEXT_REQUEST_SECTION}\n",
                encoding="utf-8",
            )

            request = build_chatgpt_request(
                state={
                    "mode": "idle",
                    "need_chatgpt_prompt": False,
                    "need_chatgpt_next": True,
                    "need_codex_run": False,
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_followup_issue_number": "81",
                    "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                    "last_issue_centric_consumed_generation_id": f"summary:{summary_path}",
                },
                template_path=template_path,
                next_todo="next",
                open_questions="none",
                last_report="===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
            )

            self.assertIn("- runtime_mode: issue_centric_degraded_fallback", request)
            self.assertIn("- freshness_status: issue_centric_stale", request)
            self.assertIn("- next_request_route: fallback_legacy", request)

    def test_route_selector_prefers_issue_centric_when_summary_is_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "no_action",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "created_followup_issue": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )

            selection = issue_centric_normalized_summary.select_issue_centric_next_request_route(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_followup_issue_number": "81",
                    "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                },
                repo_root=REPO_ROOT,
            )

            self.assertEqual(selection.route_selected, "issue_centric")
            self.assertEqual(selection.target_issue, "https://github.com/example/repo/issues/81")
            self.assertEqual(selection.target_issue_source, "normalized_summary")
            self.assertEqual(selection.fallback_reason, "")

    def test_runtime_mode_is_ready_when_snapshot_and_route_are_coherent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "issue_create",
                        "final_status": "completed",
                        "principal_issue_kind": "primary_issue",
                        "principal_issue_candidate": {
                            "number": "51",
                            "url": "https://github.com/example/repo/issues/51",
                            "title": "Primary issue",
                            "ref": "#51",
                        },
                        "next_request_hint": "continue_on_primary_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "completed", "matrix_path": "issue_create"}),
                encoding="utf-8",
            )

            runtime_mode = issue_centric_normalized_summary.resolve_issue_centric_runtime_mode(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_primary_issue_number": "51",
                    "last_issue_centric_primary_issue_url": "https://github.com/example/repo/issues/51",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(runtime_mode)
            assert runtime_mode is not None
            self.assertEqual(runtime_mode.runtime_mode, "issue_centric_ready")
            self.assertEqual(runtime_mode.target_issue, "https://github.com/example/repo/issues/51")

    def test_generation_lifecycle_is_fresh_prepared_when_prepared_request_matches_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "issue_create",
                        "final_status": "completed",
                        "principal_issue_kind": "primary_issue",
                        "principal_issue_candidate": {
                            "number": "51",
                            "url": "https://github.com/example/repo/issues/51",
                            "title": "Primary issue",
                            "ref": "#51",
                        },
                        "next_request_hint": "continue_on_primary_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "completed", "matrix_path": "issue_create"}),
                encoding="utf-8",
            )
            generation_id = f"summary:{summary_path}"

            lifecycle = issue_centric_normalized_summary.resolve_issue_centric_generation_lifecycle(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_primary_issue_number": "51",
                    "last_issue_centric_primary_issue_url": "https://github.com/example/repo/issues/51",
                    "last_issue_centric_prepared_generation_id": generation_id,
                    "prepared_request_hash": "abc",
                    "prepared_request_source": "report:1",
                    "prepared_request_log": "logs/request.md",
                    "prepared_request_status": "prepared",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(lifecycle)
            assert lifecycle is not None
            self.assertEqual(lifecycle.generation_lifecycle, "fresh_prepared")
            self.assertEqual(lifecycle.generation_id, generation_id)

    def test_generation_lifecycle_is_fresh_pending_when_pending_request_matches_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "no_action",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "created_followup_issue": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "completed", "matrix_path": "no_action_followup"}),
                encoding="utf-8",
            )
            generation_id = f"summary:{summary_path}"

            lifecycle = issue_centric_normalized_summary.resolve_issue_centric_generation_lifecycle(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_followup_issue_number": "81",
                    "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                    "last_issue_centric_pending_generation_id": generation_id,
                    "pending_request_hash": "abc",
                    "pending_request_source": "report:1",
                    "pending_request_log": "logs/request.md",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(lifecycle)
            assert lifecycle is not None
            self.assertEqual(lifecycle.generation_lifecycle, "fresh_pending")
            self.assertEqual(lifecycle.generation_id, generation_id)

    def test_generation_lifecycle_is_consumed_after_reply_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "codex_run",
                        "final_status": "completed",
                        "principal_issue_kind": "current_issue",
                        "principal_issue_candidate": {
                            "number": "20",
                            "url": "https://github.com/example/repo/issues/20",
                            "title": "",
                            "ref": "#20",
                        },
                        "next_request_hint": "continue_on_current_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "completed", "matrix_path": "codex_run"}),
                encoding="utf-8",
            )
            generation_id = f"summary:{summary_path}"

            lifecycle = issue_centric_normalized_summary.resolve_issue_centric_generation_lifecycle(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                    "last_issue_centric_consumed_generation_id": generation_id,
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(lifecycle)
            assert lifecycle is not None
            self.assertEqual(lifecycle.generation_lifecycle, "issue_centric_consumed")
            self.assertEqual(lifecycle.generation_lifecycle_reason, "chatgpt_reply_recovered_for_generation")

    def test_recover_prepared_request_state_keeps_issue_centric_generation_prepared(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "prepared_request_hash": "hash123",
            "prepared_request_source": "report:1",
            "prepared_request_log": "logs/request.md",
            "prepared_request_status": "prepared",
            "last_issue_centric_prepared_generation_id": "summary:logs/summary.json",
        }
        saved_states: list[dict[str, object]] = []

        with patch.object(_bridge_common, "save_state", side_effect=lambda payload: saved_states.append(dict(payload))):
            updated, recovered = recover_prepared_request_state(state)

        self.assertTrue(recovered)
        self.assertEqual(updated["prepared_request_status"], "prepared")
        self.assertEqual(updated.get("pending_request_source", ""), "")
        self.assertEqual(updated["last_issue_centric_generation_lifecycle"], "fresh_prepared")
        self.assertEqual(updated["last_issue_centric_generation_lifecycle_reason"], "prepared_request_recovered_without_send")
        self.assertEqual(updated["last_issue_centric_prepared_generation_id"], "summary:logs/summary.json")
        self.assertEqual(updated["last_issue_centric_pending_generation_id"], "")

    def test_runtime_mode_is_degraded_when_resolution_is_unclear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "human_review_needed",
                        "final_status": "completed",
                        "principal_issue_kind": "current_issue",
                        "principal_issue_candidate": {
                            "number": "20",
                            "url": "https://github.com/example/repo/issues/20",
                            "title": "",
                            "ref": "example/repo#20",
                        },
                        "next_request_hint": "issue_resolution_unclear",
                    }
                ),
                encoding="utf-8",
            )

            runtime_mode = issue_centric_normalized_summary.resolve_issue_centric_runtime_mode(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(runtime_mode)
            assert runtime_mode is not None
            self.assertEqual(runtime_mode.runtime_mode, "issue_centric_degraded_fallback")
            self.assertEqual(runtime_mode.runtime_mode_reason, "issue_resolution_unclear")

    def test_runtime_mode_is_degraded_when_snapshot_generation_was_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "no_action",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "created_followup_issue": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "completed", "matrix_path": "no_action_followup"}),
                encoding="utf-8",
            )

            runtime_mode = issue_centric_normalized_summary.resolve_issue_centric_runtime_mode(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_followup_issue_number": "81",
                    "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                    "last_issue_centric_consumed_generation_id": f"summary:{summary_path}",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(runtime_mode)
            assert runtime_mode is not None
            self.assertEqual(runtime_mode.runtime_mode, "issue_centric_degraded_fallback")
            self.assertEqual(runtime_mode.freshness_status, "issue_centric_stale")
            self.assertEqual(runtime_mode.runtime_mode_reason, "chatgpt_reply_recovered_for_generation")

    def test_runtime_mode_is_degraded_when_snapshot_generation_was_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "human_review_needed",
                        "final_status": "completed",
                        "principal_issue_kind": "current_issue",
                        "principal_issue_candidate": {
                            "number": "20",
                            "url": "https://github.com/example/repo/issues/20",
                            "title": "",
                            "ref": "#20",
                        },
                        "next_request_hint": "continue_on_current_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "completed", "matrix_path": "human_review_needed"}),
                encoding="utf-8",
            )

            runtime_mode = issue_centric_normalized_summary.resolve_issue_centric_runtime_mode(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                    "last_issue_centric_invalidated_generation_id": f"summary:{summary_path}",
                    "last_issue_centric_invalidation_status": "issue_centric_invalidated",
                    "last_issue_centric_invalidation_reason": "legacy_fallback_selected",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(runtime_mode)
            assert runtime_mode is not None
            self.assertEqual(runtime_mode.runtime_mode, "issue_centric_degraded_fallback")
            self.assertEqual(runtime_mode.freshness_status, "issue_centric_invalidated")
            self.assertEqual(runtime_mode.invalidation_reason, "legacy_fallback_selected")

    def test_runtime_mode_is_unavailable_when_snapshot_sources_are_missing(self) -> None:
        runtime_mode = issue_centric_normalized_summary.resolve_issue_centric_runtime_mode(
            {
                "last_issue_centric_runtime_snapshot": "logs/missing.json",
            },
            repo_root=REPO_ROOT,
        )

        self.assertIsNotNone(runtime_mode)
        assert runtime_mode is not None
        self.assertEqual(runtime_mode.runtime_mode, "issue_centric_unavailable")
        self.assertEqual(runtime_mode.runtime_mode_reason, "runtime_snapshot_missing_or_unreadable")

    def test_state_bridge_marks_prepared_generation_as_send_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "issue_create",
                        "final_status": "completed",
                        "principal_issue_kind": "primary_issue",
                        "principal_issue_candidate": {
                            "number": "51",
                            "url": "https://github.com/example/repo/issues/51",
                            "title": "Primary issue",
                            "ref": "#51",
                        },
                        "next_request_hint": "continue_on_primary_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "completed", "matrix_path": "issue_create"}),
                encoding="utf-8",
            )
            bridge = issue_centric_normalized_summary.resolve_issue_centric_state_bridge(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_primary_issue_number": "51",
                    "last_issue_centric_primary_issue_url": "https://github.com/example/repo/issues/51",
                    "last_issue_centric_prepared_generation_id": f"summary:{summary_path}",
                    "prepared_request_hash": "abc",
                    "prepared_request_source": "report:1",
                    "prepared_request_log": "logs/request.md",
                    "prepared_request_status": "prepared",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(bridge)
            assert bridge is not None
            self.assertEqual(bridge.state_view, "issue_centric_prepared_request")
            self.assertEqual(bridge.wait_kind, "send_prepared_request")

    def test_state_bridge_marks_invalidated_generation_as_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "human_review_needed",
                        "final_status": "completed",
                        "principal_issue_kind": "current_issue",
                        "principal_issue_candidate": {
                            "number": "20",
                            "url": "https://github.com/example/repo/issues/20",
                            "title": "",
                            "ref": "#20",
                        },
                        "next_request_hint": "continue_on_current_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "completed", "matrix_path": "human_review_needed"}),
                encoding="utf-8",
            )
            bridge = issue_centric_normalized_summary.resolve_issue_centric_state_bridge(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                    "last_issue_centric_invalidated_generation_id": f"summary:{summary_path}",
                    "last_issue_centric_invalidation_status": "issue_centric_invalidated",
                    "last_issue_centric_invalidation_reason": "legacy_fallback_selected",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(bridge)
            assert bridge is not None
            self.assertEqual(bridge.state_view, "issue_centric_invalidated")
            self.assertEqual(bridge.wait_kind, "legacy_fallback")
            self.assertEqual(bridge.wait_reason, "legacy_fallback_selected")

    def test_recovery_prefers_issue_centric_when_summary_and_dispatch_are_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "codex_run",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "created_followup_issue": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "completed", "matrix_path": "codex_run_followup"}),
                encoding="utf-8",
            )

            recovery = issue_centric_normalized_summary.recover_issue_centric_next_request_context(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_followup_issue_number": "81",
                    "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(recovery)
            self.assertEqual(recovery.recovery_status, "issue_centric_recovered")
            self.assertEqual(recovery.route_selected, "issue_centric")
            self.assertEqual(recovery.target_issue, "https://github.com/example/repo/issues/81")
            self.assertEqual(
                recovery.recovery_source,
                "normalized_summary_then_dispatch_then_state",
            )

    def test_recovery_falls_back_when_summary_exists_but_state_support_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "no_action",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )

            recovery = issue_centric_normalized_summary.recover_issue_centric_next_request_context(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(recovery)
            self.assertEqual(recovery.recovery_status, "issue_centric_recovery_fallback")
            self.assertEqual(recovery.route_selected, "fallback_legacy")
            self.assertEqual(recovery.fallback_reason, "normalized_summary_state_missing")

    def test_route_selector_falls_back_when_summary_is_missing(self) -> None:
        selection = issue_centric_normalized_summary.select_issue_centric_next_request_route(
            {
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                "last_issue_centric_next_request_hint": "continue_on_current_issue",
            },
            repo_root=REPO_ROOT,
        )

        self.assertEqual(selection.route_selected, "fallback_legacy")
        self.assertEqual(selection.target_issue, "https://github.com/example/repo/issues/20")
        self.assertEqual(selection.fallback_reason, "normalized_summary_missing")

    def test_recovery_falls_back_when_summary_is_missing(self) -> None:
        recovery = issue_centric_normalized_summary.recover_issue_centric_next_request_context(
            {
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                "last_issue_centric_next_request_hint": "continue_on_current_issue",
                "last_issue_centric_action": "human_review_needed",
            },
            repo_root=REPO_ROOT,
        )

        self.assertIsNotNone(recovery)
        self.assertEqual(recovery.recovery_status, "issue_centric_recovery_fallback")
        self.assertEqual(recovery.route_selected, "fallback_legacy")
        self.assertEqual(recovery.target_issue, "https://github.com/example/repo/issues/20")
        self.assertEqual(recovery.fallback_reason, "normalized_summary_missing")

    def test_resolver_falls_back_to_existing_state_when_summary_is_missing(self) -> None:
        context = issue_centric_normalized_summary.resolve_issue_centric_next_request_context(
            {
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                "last_issue_centric_next_request_hint": "continue_on_current_issue",
            },
            repo_root=REPO_ROOT,
        )

        self.assertIsNotNone(context)
        self.assertEqual(context.target_issue, "https://github.com/example/repo/issues/20")
        self.assertEqual(context.target_issue_source, "existing_state_fallback")
        self.assertEqual(context.fallback_reason, "normalized_summary_missing")

    def test_resolver_falls_back_when_summary_is_unclear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "principal_issue_kind": "unresolved",
                        "principal_issue_candidate": None,
                        "next_request_hint": "issue_resolution_unclear",
                    }
                ),
                encoding="utf-8",
            )
            context = issue_centric_normalized_summary.resolve_issue_centric_next_request_context(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(context)
            self.assertEqual(context.target_issue, "https://github.com/example/repo/issues/20")
            self.assertEqual(context.target_issue_source, "existing_state_fallback")
            self.assertEqual(context.fallback_reason, "normalized_summary_requested_fallback")

    def test_resolver_falls_back_when_summary_conflicts_with_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "88",
                            "url": "https://github.com/example/repo/issues/88",
                            "title": "Conflicting issue",
                            "ref": "#88",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )
            context = issue_centric_normalized_summary.resolve_issue_centric_next_request_context(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_followup_issue_number": "81",
                    "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                    "last_issue_centric_principal_issue": "https://github.com/example/repo/issues/81",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(context)
            self.assertEqual(context.target_issue, "https://github.com/example/repo/issues/81")
            self.assertEqual(context.target_issue_source, "existing_state_fallback")
            self.assertEqual(context.fallback_reason, "normalized_summary_inconsistent_with_state")

    def test_route_selector_falls_back_when_summary_conflicts_with_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "no_action",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "88",
                            "url": "https://github.com/example/repo/issues/88",
                            "title": "Conflicting issue",
                            "ref": "#88",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )

            selection = issue_centric_normalized_summary.select_issue_centric_next_request_route(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_followup_issue_number": "81",
                    "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                },
                repo_root=REPO_ROOT,
            )

            self.assertEqual(selection.route_selected, "fallback_legacy")
            self.assertEqual(selection.target_issue, "https://github.com/example/repo/issues/81")
            self.assertEqual(selection.fallback_reason, "normalized_summary_inconsistent_with_state")

    def test_route_selector_falls_back_when_resolver_raises(self) -> None:
        with patch.object(
            issue_centric_normalized_summary,
            "resolve_issue_centric_next_request_context",
            side_effect=RuntimeError("boom"),
        ):
            selection = issue_centric_normalized_summary.select_issue_centric_next_request_route(
                {},
                repo_root=REPO_ROOT,
            )

        self.assertEqual(selection.route_selected, "fallback_legacy")
        self.assertEqual(selection.target_issue_source, "resolver_exception")
        self.assertEqual(selection.fallback_reason, "resolver_error:RuntimeError")

    def test_recovery_falls_back_when_dispatch_result_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "issue_create",
                        "final_status": "completed",
                        "principal_issue_kind": "primary_issue",
                        "principal_issue_candidate": {
                            "number": "51",
                            "url": "https://github.com/example/repo/issues/51",
                            "title": "Primary issue",
                            "ref": "#51",
                        },
                        "next_request_hint": "continue_on_primary_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text("{not-json", encoding="utf-8")

            recovery = issue_centric_normalized_summary.recover_issue_centric_next_request_context(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_primary_issue_number": "51",
                    "last_issue_centric_primary_issue_url": "https://github.com/example/repo/issues/51",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(recovery)
            self.assertEqual(recovery.recovery_status, "issue_centric_recovery_fallback")
            self.assertEqual(recovery.route_selected, "fallback_legacy")
            self.assertEqual(recovery.fallback_reason, "dispatch_result_missing_or_unreadable")

    def test_recovery_falls_back_when_summary_is_unreadable_but_state_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text("{broken", encoding="utf-8")

            recovery = issue_centric_normalized_summary.recover_issue_centric_next_request_context(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                    "last_issue_centric_action": "human_review_needed",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(recovery)
            self.assertEqual(recovery.recovery_status, "issue_centric_recovery_fallback")
            self.assertEqual(recovery.route_selected, "fallback_legacy")
            self.assertEqual(recovery.fallback_reason, "normalized_summary_missing_or_unreadable")

    def test_recovery_falls_back_when_dispatch_result_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "codex_run",
                        "final_status": "completed",
                        "principal_issue_kind": "current_issue",
                        "principal_issue_candidate": {
                            "number": "20",
                            "url": "https://github.com/example/repo/issues/20",
                            "title": "",
                            "ref": "#20",
                        },
                        "next_request_hint": "continue_on_current_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "failed", "matrix_path": "codex_run"}),
                encoding="utf-8",
            )

            recovery = issue_centric_normalized_summary.recover_issue_centric_next_request_context(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(recovery)
            self.assertEqual(recovery.recovery_status, "issue_centric_recovery_fallback")
            self.assertEqual(recovery.fallback_reason, "dispatch_result_failed_execution")

    def test_runtime_snapshot_is_built_from_summary_and_recovery_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            dispatch_path = root / "dispatch.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "codex_run",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "created_followup_issue": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "closed_issue": {
                            "number": "20",
                            "url": "https://github.com/example/repo/issues/20",
                            "title": "Current issue",
                            "ref": "#20",
                        },
                        "codex_target_issue": {
                            "number": "20",
                            "url": "https://github.com/example/repo/issues/20",
                            "title": "",
                            "ref": "example/repo#20",
                        },
                        "project_lifecycle_sync": {
                            "status": "project_state_synced",
                            "stage": "done",
                            "state_value": "Done",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )
            dispatch_path.write_text(
                json.dumps({"final_status": "completed", "matrix_path": "codex_run_followup_then_close"}),
                encoding="utf-8",
            )

            snapshot = issue_centric_normalized_summary.build_issue_centric_runtime_snapshot(
                {
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_dispatch_result": str(dispatch_path),
                    "last_issue_centric_followup_issue_number": "81",
                    "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                    "last_issue_centric_closed_issue_number": "20",
                    "last_issue_centric_closed_issue_url": "https://github.com/example/repo/issues/20",
                },
                repo_root=REPO_ROOT,
                snapshot_source="execution_finalize",
            )

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot.snapshot_status, "issue_centric_snapshot_ready")
            self.assertEqual(snapshot.route_selected, "issue_centric")
            self.assertEqual(snapshot.principal_issue_kind, "followup_issue")
            self.assertEqual(snapshot.target_issue, "https://github.com/example/repo/issues/81")
            self.assertEqual(snapshot.project_lifecycle_sync["stage"], "done")

    def test_runtime_snapshot_resolution_prefers_saved_snapshot_when_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
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
                        "created_followup_issue": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
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

            snapshot = issue_centric_normalized_summary.resolve_issue_centric_runtime_snapshot(
                {
                    "last_issue_centric_runtime_snapshot": str(snapshot_path),
                    "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                    "last_issue_centric_principal_issue": "https://github.com/example/repo/issues/81",
                    "last_issue_centric_next_request_target": "https://github.com/example/repo/issues/81",
                },
                repo_root=REPO_ROOT,
            )

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot.snapshot_source, "execution_finalize")
            self.assertEqual(snapshot.target_issue, "https://github.com/example/repo/issues/81")

    def test_request_status_prefers_runtime_snapshot_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "issue_create",
                        "final_status": "completed",
                        "principal_issue_kind": "primary_issue",
                        "principal_issue_candidate": {
                            "number": "51",
                            "url": "https://github.com/example/repo/issues/51",
                            "title": "Primary issue",
                            "ref": "#51",
                        },
                        "next_request_hint": "continue_on_primary_issue",
                    }
                ),
                encoding="utf-8",
            )

            rendered = build_issue_centric_request_status(
                {
                    "mode": "idle",
                    "need_chatgpt_next": True,
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_primary_issue_number": "51",
                    "last_issue_centric_primary_issue_url": "https://github.com/example/repo/issues/51",
                }
            )

            self.assertIn("## issue_centric_runtime_snapshot", rendered)
            self.assertIn("issue_centric_snapshot_status: issue_centric_snapshot_ready", rendered)
            self.assertIn("issue_centric_next_request_target: https://github.com/example/repo/issues/51", rendered)

    def test_handoff_builder_uses_summary_based_target_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "issue_create",
                        "final_status": "completed",
                        "principal_issue_kind": "primary_issue",
                        "principal_issue_candidate": {
                            "number": "51",
                            "url": "https://github.com/example/repo/issues/51",
                            "title": "Primary issue",
                            "ref": "#51",
                        },
                        "next_request_hint": "continue_on_primary_issue",
                    }
                ),
                encoding="utf-8",
            )
            handoff = build_chatgpt_handoff_request(
                state={
                    "last_issue_centric_normalized_summary": str(summary_path),
                    "last_issue_centric_primary_issue_number": "51",
                    "last_issue_centric_primary_issue_url": "https://github.com/example/repo/issues/51",
                },
                last_report="===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
                next_todo="next",
                open_questions="none",
            )

            self.assertIn("## issue_centric_next_request", handoff)
            self.assertIn("- recovery_status: issue_centric_recovered", handoff)
            self.assertIn("- next_request_route: issue_centric", handoff)
            self.assertIn("- target_issue: https://github.com/example/repo/issues/51", handoff)
            self.assertIn("- next_request_hint: continue_on_primary_issue", handoff)

    def test_request_prompt_from_report_updates_state_with_resolved_next_request_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "no_action",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )
            saved_states: list[dict[str, object]] = []

            def fake_log_text(prefix: str, content: str, suffix: str = "md") -> Path:
                path = root / f"{prefix}.{suffix}"
                path.write_text(content, encoding="utf-8")
                return path

            args = SimpleNamespace(
                next_todo="next",
                open_questions="none",
                current_status="",
                resume_note="",
            )
            with (
                patch.object(request_prompt_from_report, "log_text", side_effect=fake_log_text),
                patch.object(request_prompt_from_report, "save_state", side_effect=lambda state: saved_states.append(dict(state))),
                patch.object(request_prompt_from_report, "send_to_chatgpt", return_value=None),
            ):
                rc = request_prompt_from_report.run_resume_request(
                    {
                        "mode": "idle",
                        "last_issue_centric_normalized_summary": str(summary_path),
                        "last_issue_centric_followup_issue_number": "81",
                        "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                    },
                    args,
                    "===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
                    "",
                )

            self.assertEqual(rc, 0)
            self.assertEqual(saved_states[-1]["last_issue_centric_next_request_target"], "https://github.com/example/repo/issues/81")
            self.assertEqual(saved_states[-1]["last_issue_centric_next_request_target_source"], "normalized_summary")
            self.assertEqual(saved_states[-1]["last_issue_centric_route_selected"], "issue_centric")
            self.assertEqual(saved_states[-1]["last_issue_centric_route_fallback_reason"], "")
            self.assertEqual(saved_states[-1]["last_issue_centric_recovery_status"], "issue_centric_recovered")
            self.assertEqual(
                saved_states[-1]["last_issue_centric_recovery_source"],
                "normalized_summary_then_state",
            )
            self.assertEqual(saved_states[-1]["last_issue_centric_runtime_mode"], "issue_centric_ready")
            self.assertEqual(saved_states[-1]["last_issue_centric_runtime_mode_reason"], "issue_centric_snapshot_ready")
            self.assertEqual(saved_states[-1]["last_issue_centric_generation_lifecycle"], "fresh_pending")
            self.assertEqual(saved_states[-1]["last_issue_centric_freshness_status"], "issue_centric_fresh")
            self.assertEqual(saved_states[-1]["last_issue_centric_freshness_reason"], "pending_request_bound_to_generation")
            self.assertTrue(str(saved_states[-1]["last_issue_centric_pending_generation_id"]).startswith("summary:"))
            self.assertEqual(saved_states[-1]["last_issue_centric_consumed_generation_id"], "")
            self.assertTrue(str(saved_states[-1]["last_issue_centric_runtime_snapshot"]).endswith(".json"))
            self.assertEqual(saved_states[-1]["last_issue_centric_snapshot_status"], "issue_centric_snapshot_ready")

    def test_run_resume_request_reuses_prepared_issue_centric_request_when_fresh_prepared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "no_action",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )
            saved_states: list[dict[str, object]] = []

            def fake_log_text(prefix: str, content: str, suffix: str = "md") -> Path:
                path = root / f"{prefix}.{suffix}"
                path.write_text(content, encoding="utf-8")
                return path

            args = SimpleNamespace(
                next_todo="next",
                open_questions="none",
                current_status="",
                resume_note="",
            )
            state = {
                "mode": "idle",
                "need_chatgpt_next": True,
                "prepared_request_hash": "hash123",
                "prepared_request_source": "report:1",
                "prepared_request_log": "logs/prepared.md",
                "prepared_request_status": "prepared",
                "last_issue_centric_normalized_summary": str(summary_path),
                "last_issue_centric_followup_issue_number": "81",
                "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                "last_issue_centric_runtime_generation_id": f"summary:{summary_path}",
                "last_issue_centric_generation_lifecycle": "fresh_prepared",
            }
            with (
                patch.object(request_prompt_from_report, "log_text", side_effect=fake_log_text),
                patch.object(request_prompt_from_report, "read_prepared_request_text", return_value="prepared request body"),
                patch.object(request_prompt_from_report, "save_state", side_effect=lambda payload: saved_states.append(dict(payload))),
                patch.object(request_prompt_from_report, "send_to_chatgpt", return_value=None) as send_mock,
                patch.object(request_prompt_from_report, "build_chatgpt_request", side_effect=AssertionError("should not rebuild request")),
            ):
                rc = request_prompt_from_report.run_resume_request(
                    state,
                    args,
                    "===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
                    "",
                )

            self.assertEqual(rc, 0)
            send_mock.assert_called_once_with("prepared request body")
            self.assertEqual(saved_states[-1]["last_issue_centric_generation_lifecycle"], "fresh_pending")
            self.assertTrue(str(saved_states[-1]["last_issue_centric_pending_generation_id"]).startswith("summary:"))

    def test_save_state_persists_issue_centric_state_bridge_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            snapshot_path = root / "snapshot.json"
            state_path = root / "state.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "no_action",
                        "final_status": "completed",
                        "principal_issue_kind": "followup_issue",
                        "principal_issue_candidate": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "created_followup_issue": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "next_request_hint": "continue_on_followup_issue",
                    }
                ),
                encoding="utf-8",
            )
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": f"summary:{summary_path}",
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
                        "created_followup_issue": {
                            "number": "81",
                            "url": "https://github.com/example/repo/issues/81",
                            "title": "Follow-up issue",
                            "ref": "#81",
                        },
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": str(summary_path),
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(_bridge_common, "ensure_runtime_dirs"),
                patch.object(_bridge_common, "runtime_state_path", return_value=state_path),
            ):
                _bridge_common.save_state(
                    {
                        "last_issue_centric_normalized_summary": str(summary_path),
                        "last_issue_centric_runtime_snapshot": str(snapshot_path),
                        "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                        "last_issue_centric_followup_issue_number": "81",
                        "last_issue_centric_followup_issue_url": "https://github.com/example/repo/issues/81",
                        "last_issue_centric_prepared_generation_id": f"summary:{summary_path}",
                        "prepared_request_hash": "abc",
                        "prepared_request_source": "report:1",
                        "prepared_request_log": "logs/request.md",
                        "prepared_request_status": "prepared",
                    }
                )
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["last_issue_centric_state_view"], "issue_centric_prepared_request")
            self.assertEqual(saved["last_issue_centric_wait_kind"], "send_prepared_request")


if __name__ == "__main__":
    unittest.main()
