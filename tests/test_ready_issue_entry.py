from __future__ import annotations

import argparse
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
