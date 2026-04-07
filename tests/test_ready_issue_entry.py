from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import bridge_orchestrator  # noqa: E402
import request_next_prompt  # noqa: E402


class RequestNextPromptTests(unittest.TestCase):
    def test_build_initial_request_prefers_ready_issue_reference(self) -> None:
        args = argparse.Namespace(
            ready_issue_ref="#20 runtime entry",
            request_body="",
            project_path="/tmp/repo",
        )
        request_text, request_hash, request_source = request_next_prompt.build_initial_request(args)
        self.assertIn("current ready issue: #20 runtime entry", request_text)
        self.assertIn("ready issue を今回の実行単位正本として使う", request_text)
        self.assertTrue(request_hash)
        self.assertTrue(request_source.startswith("ready_issue:"))

    def test_build_initial_request_uses_override_source_for_request_body(self) -> None:
        args = argparse.Namespace(
            ready_issue_ref="",
            request_body="Target repo: /tmp/repo\nOverride reason: recovery",
            project_path="/tmp/repo",
        )
        request_text, _, request_source = request_next_prompt.build_initial_request(args)
        self.assertIn("Override reason: recovery", request_text)
        self.assertTrue(request_source.startswith("override:"))

    def test_build_initial_request_rejects_ambiguous_entry_flags(self) -> None:
        args = argparse.Namespace(
            ready_issue_ref="#20 runtime entry",
            request_body="override text",
            project_path="/tmp/repo",
        )
        with self.assertRaises(request_next_prompt.BridgeError):
            request_next_prompt.build_initial_request(args)

    def test_log_prefixes_follow_request_source_kind(self) -> None:
        self.assertEqual(
            request_next_prompt.request_log_prefixes("ready_issue:abc"),
            ("prepared_prompt_request_from_ready_issue", "sent_prompt_request_from_ready_issue"),
        )
        self.assertEqual(
            request_next_prompt.request_log_prefixes("override:abc"),
            ("prepared_prompt_request_from_override", "sent_prompt_request_from_override"),
        )
        self.assertEqual(
            request_next_prompt.request_log_prefixes("initial:abc"),
            ("prepared_prompt_request_from_override", "sent_prompt_request_from_override"),
        )

    def test_load_retryable_initial_request_accepts_ready_issue_source(self) -> None:
        state = {
            "pending_request_source": "",
            "prepared_request_status": "retry_send",
            "prepared_request_source": "ready_issue:abc",
            "prepared_request_hash": "hash123",
            "prepared_request_log": "logs/request.md",
        }
        with patch.object(request_next_prompt, "read_prepared_request_text", return_value="request text"):
            retryable = request_next_prompt.load_retryable_initial_request(state)
        self.assertEqual(retryable, ("request text", "hash123", "ready_issue:abc"))

    def test_load_retryable_initial_request_accepts_prepared_ready_issue_source(self) -> None:
        state = {
            "pending_request_source": "",
            "prepared_request_status": "prepared",
            "prepared_request_source": "ready_issue:abc",
            "prepared_request_hash": "hash123",
            "prepared_request_log": "logs/request.md",
        }
        with patch.object(request_next_prompt, "read_prepared_request_text", return_value="request text"):
            retryable = request_next_prompt.load_retryable_initial_request(state)
        self.assertEqual(retryable, ("request text", "hash123", "ready_issue:abc"))


class OrchestratorArgForwardingTests(unittest.TestCase):
    def test_build_initial_request_argv_forwards_ready_issue_and_override(self) -> None:
        args = argparse.Namespace(
            worker_repo_path="/tmp/repo",
            ready_issue_ref="#20 runtime entry",
            request_body="override text",
        )
        argv = bridge_orchestrator.build_initial_request_argv(args)
        self.assertEqual(
            argv,
            [
                "--project-path",
                "/tmp/repo",
                "--ready-issue-ref",
                "#20 runtime entry",
                "--request-body",
                "override text",
            ],
        )

    def test_run_prefers_fetch_for_issue_centric_pending_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "snapshot.json"
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
            state = {
                "mode": "idle",
                "need_chatgpt_next": True,
                "last_issue_centric_runtime_snapshot": str(snapshot_path),
                "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                "last_issue_centric_pending_generation_id": "summary:logs/summary.json",
                "pending_request_hash": "hash123",
                "pending_request_source": "report:1",
                "pending_request_log": "logs/request.md",
            }
            with (
                patch.object(bridge_orchestrator, "load_project_config", return_value={}),
                patch.object(bridge_orchestrator, "print_project_config_warnings"),
                patch.object(bridge_orchestrator.fetch_next_prompt, "run", return_value=0) as fetch_run,
                patch.object(bridge_orchestrator.request_prompt_from_report, "run", return_value=0) as report_run,
            ):
                rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        fetch_run.assert_called_once()
        report_run.assert_not_called()

    def test_run_uses_issue_centric_preferred_route_for_next_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "snapshot.json"
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
            state = {
                "mode": "idle",
                "need_chatgpt_next": True,
                "last_issue_centric_runtime_snapshot": str(snapshot_path),
                "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
            }
            out = io.StringIO()
            with (
                patch.object(bridge_orchestrator, "load_project_config", return_value={}),
                patch.object(bridge_orchestrator, "print_project_config_warnings"),
                patch.object(bridge_orchestrator.request_prompt_from_report, "run", return_value=0) as report_run,
                redirect_stdout(out),
            ):
                rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        report_run.assert_called_once()
        self.assertIn("issue-centric preferred route", out.getvalue())
        self.assertIn("https://github.com/example/repo/issues/51", out.getvalue())

    def test_run_uses_legacy_fallback_for_next_request_when_issue_centric_is_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "snapshot.json"
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
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "mode": "idle",
                "need_chatgpt_next": True,
                "last_issue_centric_runtime_snapshot": str(snapshot_path),
                "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                "last_issue_centric_invalidated_generation_id": "summary:logs/summary.json",
                "last_issue_centric_invalidation_status": "issue_centric_invalidated",
                "last_issue_centric_invalidation_reason": "legacy_fallback_selected",
            }
            out = io.StringIO()
            with (
                patch.object(bridge_orchestrator, "load_project_config", return_value={}),
                patch.object(bridge_orchestrator, "print_project_config_warnings"),
                patch.object(bridge_orchestrator.request_prompt_from_report, "run", return_value=0) as report_run,
                redirect_stdout(out),
            ):
                rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        report_run.assert_called_once()
        self.assertIn("legacy fallback", out.getvalue())
        self.assertIn("legacy_fallback_selected", out.getvalue())


if __name__ == "__main__":
    unittest.main()
