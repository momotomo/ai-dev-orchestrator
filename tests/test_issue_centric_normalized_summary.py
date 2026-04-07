from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import issue_centric_normalized_summary  # noqa: E402
from _bridge_common import build_chatgpt_request  # noqa: E402


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
            template_path.write_text("STATE\n{CURRENT_STATUS}\n", encoding="utf-8")

            request = build_chatgpt_request(
                state={
                    "mode": "idle",
                    "need_chatgpt_prompt": False,
                    "need_chatgpt_next": True,
                    "need_codex_run": False,
                    "last_issue_centric_normalized_summary": str(summary_path),
                },
                template_path=template_path,
                next_todo="next",
                open_questions="none",
                last_report="===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
            )

            self.assertIn("## issue_centric_summary", request)
            self.assertIn("issue_centric_principal_issue_kind: followup_issue", request)
            self.assertIn("issue_centric_next_request_hint: continue_on_followup_issue", request)
            self.assertIn("issue_centric_principal_issue: #81 https://github.com/example/repo/issues/81 (Follow-up issue)", request)


if __name__ == "__main__":
    unittest.main()
