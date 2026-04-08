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
                patch.object(bridge_orchestrator, "should_prioritize_unarchived_report", return_value=False),
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
                patch.object(bridge_orchestrator, "should_prioritize_unarchived_report", return_value=False),
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
                patch.object(bridge_orchestrator, "should_prioritize_unarchived_report", return_value=False),
                patch.object(bridge_orchestrator.request_prompt_from_report, "run", return_value=0) as report_run,
                redirect_stdout(out),
            ):
                rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        report_run.assert_called_once()
        self.assertIn("safety fallback (legacy) route", out.getvalue())
        self.assertIn("legacy_fallback_selected", out.getvalue())

    def test_run_keeps_codex_running_mode_driven_before_issue_centric_routing(self) -> None:
        out = io.StringIO()
        with (
            patch.object(bridge_orchestrator, "load_project_config", return_value={}),
            patch.object(bridge_orchestrator, "print_project_config_warnings"),
            patch.object(bridge_orchestrator, "should_prioritize_unarchived_report", return_value=False),
            patch.object(bridge_orchestrator, "maybe_promote_codex_done", return_value=False),
            patch.object(
                bridge_orchestrator,
                "resolve_runtime_dispatch_plan",
                side_effect=AssertionError("issue-centric next-action should not override codex_running"),
            ),
            redirect_stdout(out),
        ):
            rc = bridge_orchestrator.run({"mode": "codex_running", "need_codex_run": True}, [])

        self.assertEqual(rc, 0)
        self.assertIn("Codex worker の完了待ち", out.getvalue())


class IssueAwareProvenanceTest(unittest.TestCase):
    """#26: build_report_request_source() issue-aware provenance.

    Invariants:
      - With last_issue_centric_principal_issue → report:{file}:issue:{num}
      - Without (absent / empty / falsy) → report:{file} (unchanged)
      - awaiting_user mode still produces handoff:... regardless of principal_issue
      - pending_request_source idempotency guard treats extended source as stable key
    """

    def _src(self, state: dict, resume_note: str = "") -> str:
        from request_prompt_from_report import build_report_request_source
        return build_report_request_source(state, resume_note)

    def test_with_principal_issue_returns_extended_source(self) -> None:
        state = {"last_report_file": "codex_report_20260408_123456.md",
                 "last_issue_centric_principal_issue": "42"}
        self.assertEqual(self._src(state), "report:codex_report_20260408_123456.md:issue:42")

    def test_without_principal_issue_returns_plain_source(self) -> None:
        state = {"last_report_file": "codex_report_20260408_123456.md"}
        self.assertEqual(self._src(state), "report:codex_report_20260408_123456.md")

    def test_empty_principal_issue_returns_plain_source(self) -> None:
        state = {"last_report_file": "codex_report_20260408_123456.md",
                 "last_issue_centric_principal_issue": ""}
        self.assertEqual(self._src(state), "report:codex_report_20260408_123456.md")

    def test_whitespace_only_principal_issue_returns_plain_source(self) -> None:
        state = {"last_report_file": "codex_report_20260408_123456.md",
                 "last_issue_centric_principal_issue": "  "}
        self.assertEqual(self._src(state), "report:codex_report_20260408_123456.md")

    def test_awaiting_user_mode_produces_handoff_regardless_of_principal_issue(self) -> None:
        """awaiting_user path must not be affected by principal_issue addition."""
        from request_prompt_from_report import build_report_request_source
        state = {
            "last_report_file": "report.md",
            "mode": "awaiting_user",
            "chatgpt_decision": "resume",
            "last_issue_centric_principal_issue": "99",
        }
        src = build_report_request_source(state, "some note")
        self.assertTrue(src.startswith("handoff:"), f"Expected handoff: prefix, got {src!r}")
        self.assertNotIn(":issue:", src)

    def test_missing_report_file_uses_unknown_report_fallback(self) -> None:
        state = {"last_issue_centric_principal_issue": "7"}
        self.assertEqual(self._src(state), "report:unknown-report:issue:7")

    def test_idempotency_guard_extended_source_prevents_duplicate_send(self) -> None:
        """pending_request_source == extended source → duplicate send is blocked.

        Simulates the guard check in request_prompt_from_report.main():
          if mode == 'waiting_prompt_reply' and pending_request_source == request_source: skip
        The guard must work unchanged because it is a plain string equality check.
        """
        from request_prompt_from_report import build_report_request_source
        state = {
            "last_report_file": "codex_report_20260408_123456.md",
            "mode": "waiting_prompt_reply",
            "last_issue_centric_principal_issue": "42",
            "pending_request_source": "report:codex_report_20260408_123456.md:issue:42",
        }
        request_source = build_report_request_source(state, "")
        # Guard condition: same report + same issue → same key → duplicate send blocked
        self.assertEqual(
            state["pending_request_source"],
            request_source,
            "Extended source must be a stable key so idempotency guard prevents duplicate send.",
        )

    def test_idempotency_guard_different_issue_does_not_block(self) -> None:
        """A different principal_issue produces a different source key → send is NOT blocked."""
        from request_prompt_from_report import build_report_request_source
        state = {
            "last_report_file": "codex_report_20260408_123456.md",
            "mode": "waiting_prompt_reply",
            "last_issue_centric_principal_issue": "99",
            "pending_request_source": "report:codex_report_20260408_123456.md:issue:42",
        }
        request_source = build_report_request_source(state, "")
        self.assertNotEqual(
            state["pending_request_source"],
            request_source,
            "Different issue number must produce a different source key.",
        )


if __name__ == "__main__":
    unittest.main()
