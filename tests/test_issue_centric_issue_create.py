from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_next_prompt  # noqa: E402
import issue_centric_contract  # noqa: E402
import issue_centric_issue_create  # noqa: E402
import issue_centric_transport  # noqa: E402
from _bridge_common import BridgeStop  # noqa: E402


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build_decision(body_text: str) -> issue_centric_contract.IssueCentricDecision:
    return issue_centric_contract.IssueCentricDecision(
        action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
        target_issue=None,
        close_current_issue=False,
        create_followup_issue=False,
        summary="Create a GitHub issue from the decoded body.",
        issue_body_base64=b64(body_text),
        codex_body_base64=None,
        review_base64=None,
        raw_json="{}",
        raw_segment="segment",
    )


def build_issue_create_reply(body_text: str) -> str:
    return "\n".join(
        [
            "あなた:",
            "request body",
            "ChatGPT:",
            issue_centric_contract.ISSUE_BODY_START,
            b64(body_text),
            issue_centric_contract.ISSUE_BODY_END,
            issue_centric_contract.DECISION_JSON_START,
            json.dumps(
                {
                    "action": "issue_create",
                    "target_issue": "none",
                    "close_current_issue": False,
                    "create_followup_issue": False,
                    "summary": "Create the next issue.",
                },
                ensure_ascii=True,
            ),
            issue_centric_contract.DECISION_JSON_END,
        ]
    )


class TempLogWriter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.counter = 0

    def __call__(self, prefix: str, content: str, suffix: str = "md") -> Path:
        self.counter += 1
        path = self.root / f"{self.counter:02d}_{prefix}.{suffix}"
        path.write_text(content, encoding="utf-8")
        return path


class IssueCreateExecutionTests(unittest.TestCase):
    def prepared(self, body_text: str) -> issue_centric_transport.PreparedIssueCentricDecision:
        return issue_centric_transport.decode_issue_centric_decision(build_decision(body_text))

    def test_materializes_title_and_body_from_prepared_artifact(self) -> None:
        draft = issue_centric_issue_create.materialize_issue_create_draft(
            self.prepared("# Ready: title\n\nBody paragraph.\n- item\n"),
            source_artifact_path="logs/issue-body.md",
        )
        self.assertEqual(draft.title, "Ready: title")
        self.assertEqual(draft.body, "Body paragraph.\n- item\n")

    def test_title_extraction_requires_h1_on_first_non_empty_line(self) -> None:
        with self.assertRaisesRegex(
            issue_centric_issue_create.IssueCentricIssueCreateError,
            "level-1 heading",
        ):
            issue_centric_issue_create.materialize_issue_create_draft(
                self.prepared("Body first\n# Late title\n"),
                source_artifact_path="logs/issue-body.md",
            )

    def test_empty_body_after_h1_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            issue_centric_issue_create.IssueCentricIssueCreateError,
            "body must not be empty",
        ):
            issue_centric_issue_create.materialize_issue_create_draft(
                self.prepared("# Title only\n\n"),
                source_artifact_path="logs/issue-body.md",
            )

    def test_execute_issue_create_creates_github_issue_and_logs_stateful_result(self) -> None:
        prepared = self.prepared("# Ready: title\n\nBody paragraph.\n")
        created_calls: list[tuple[str, str, str, str]] = []

        def fake_creator(repository: str, title: str, body: str, token: str) -> issue_centric_issue_create.CreatedGitHubIssue:
            created_calls.append((repository, title, body, token))
            return issue_centric_issue_create.CreatedGitHubIssue(
                number=51,
                url="https://github.com/example/repo/issues/51",
                title=title,
                repository=repository,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_issue_create.execute_issue_create_action(
                prepared,
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=fake_creator,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.created_issue.number, 51)
            self.assertEqual(result.project_sync_status, "issue_only_fallback")
            self.assertEqual(created_calls[0], ("example/repo", "Ready: title", "Body paragraph.\n", "token-123"))
            self.assertTrue(result.execution_log_path.exists())
            execution = json.loads(result.execution_log_path.read_text(encoding="utf-8"))
            self.assertEqual(execution["created_issue"]["number"], 51)
            self.assertEqual(execution["draft"]["title"], "Ready: title")
            self.assertEqual(execution["source_prepared_artifact"], "logs/prepared_issue_body.md")

    def test_non_issue_create_action_does_not_enter_execution(self) -> None:
        prepared = issue_centric_transport.PreparedIssueCentricDecision(
            decision=issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=False,
                create_followup_issue=False,
                summary="Run Codex.",
                issue_body_base64=None,
                codex_body_base64=b64("body"),
                review_base64=None,
                raw_json="{}",
                raw_segment="segment",
            ),
            issue_body=None,
            codex_body=issue_centric_transport.IssueCentricDecodedBody(
                kind=issue_centric_transport.IssueCentricArtifactKind.CODEX_BODY,
                block_name="CHATGPT_CODEX_BODY",
                raw_base64=b64("body"),
                normalized_base64=b64("body"),
                decoded_text="body",
            ),
            review_body=None,
        )
        with self.assertRaisesRegex(
            issue_centric_issue_create.IssueCentricIssueCreateError,
            "action=issue_create",
        ):
            issue_centric_issue_create.execute_issue_create_action(
                prepared,
                project_config={"github_repository": "example/repo"},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/body.md",
                log_writer=TempLogWriter(REPO_ROOT / "logs"),
                repo_relative=lambda path: path.name,
            )

    def test_project_requirement_blocks_before_issue_create(self) -> None:
        prepared = self.prepared("# Ready: title\n\nBody paragraph.\n")
        called = False

        def fake_creator(repository: str, title: str, body: str, token: str) -> issue_centric_issue_create.CreatedGitHubIssue:
            nonlocal called
            called = True
            raise AssertionError("should not be called")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_issue_create.execute_issue_create_action(
                prepared,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=fake_creator,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.project_sync_status, "blocked_project_required_unimplemented")
            self.assertFalse(called)
            execution = json.loads(result.execution_log_path.read_text(encoding="utf-8"))
            self.assertIsNone(execution["created_issue"])

    def test_github_mutation_failure_is_recorded_as_blocked(self) -> None:
        prepared = self.prepared("# Ready: title\n\nBody paragraph.\n")

        def fake_creator(repository: str, title: str, body: str, token: str) -> issue_centric_issue_create.CreatedGitHubIssue:
            raise issue_centric_issue_create.IssueCentricIssueCreateError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_issue_create.execute_issue_create_action(
                prepared,
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=fake_creator,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertIn("boom", result.safe_stop_reason)


class FetchNextPromptIssueCreateIntegrationTests(unittest.TestCase):
    def test_fetch_next_prompt_executes_issue_create_and_records_created_issue(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "ready_issue:#24",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }
        raw = build_issue_create_reply("# Ready: title\n\nBody paragraph.\n")
        saved_states: list[dict[str, object]] = []

        fake_result = issue_centric_issue_create.IssueCreateExecutionResult(
            status="completed",
            draft=issue_centric_issue_create.IssueCreateDraft(
                title="Ready: title",
                body="Body paragraph.\n",
                title_line="# Ready: title",
                source_artifact_path="logs/prepared_issue_body.md",
            ),
            created_issue=issue_centric_issue_create.CreatedGitHubIssue(
                number=77,
                url="https://github.com/example/repo/issues/77",
                title="Ready: title",
                repository="example/repo",
            ),
            draft_log_path=REPO_ROOT / "logs" / "draft.md",
            execution_log_path=REPO_ROOT / "logs" / "execution.json",
            project_sync_status="issue_only_fallback",
            project_sync_note="No project configured.",
            safe_stop_reason="issue_create completed.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_prompt_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_issue_create_action", return_value=fake_result) as exec_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "created issue: #77"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(exec_mock.call_count, 1)
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_execution_status"], "completed")
            self.assertEqual(saved["last_issue_centric_created_issue_number"], "77")
            self.assertEqual(saved["last_issue_centric_created_issue_url"], "https://github.com/example/repo/issues/77")
            self.assertEqual(saved["last_issue_centric_project_sync_status"], "issue_only_fallback")

    def test_fetch_next_prompt_records_blocked_issue_create_reason(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "ready_issue:#24",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }
        raw = build_issue_create_reply("# Ready: title\n\nBody paragraph.\n")
        saved_states: list[dict[str, object]] = []

        fake_result = issue_centric_issue_create.IssueCreateExecutionResult(
            status="blocked",
            draft=issue_centric_issue_create.IssueCreateDraft(
                title="Ready: title",
                body="Body paragraph.\n",
                title_line="# Ready: title",
                source_artifact_path="logs/prepared_issue_body.md",
            ),
            created_issue=None,
            draft_log_path=REPO_ROOT / "logs" / "draft.md",
            execution_log_path=REPO_ROOT / "logs" / "execution.json",
            project_sync_status="blocked_project_required_unimplemented",
            project_sync_note="Project config present.",
            safe_stop_reason="issue_create blocked before mutation.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_prompt_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "https://github.com/users/example/projects/1", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_issue_create_action", return_value=fake_result),
            ):
                with self.assertRaisesRegex(BridgeStop, "issue_create の最小 execution slice まで実行しました"):
                    fetch_next_prompt.run(dict(state), [])

            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_execution_status"], "blocked")
            self.assertEqual(saved["last_issue_centric_created_issue_number"], "")
            self.assertEqual(saved["last_issue_centric_project_sync_status"], "blocked_project_required_unimplemented")


if __name__ == "__main__":
    unittest.main()
