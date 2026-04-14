from __future__ import annotations

import base64
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

import fetch_next_prompt  # noqa: E402
import issue_centric_close_current_issue  # noqa: E402
import issue_centric_contract  # noqa: E402
import issue_centric_followup_issue  # noqa: E402
import issue_centric_github  # noqa: E402
import issue_centric_issue_create  # noqa: E402
import issue_centric_transport  # noqa: E402
from _bridge_common import BridgeStop  # noqa: E402


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build_decision(
    body_text: str,
    *,
    target_issue: str | None = None,
    close_current_issue: bool = False,
    create_followup_issue: bool = False,
    followup_text: str | None = None,
) -> issue_centric_contract.IssueCentricDecision:
    return issue_centric_contract.IssueCentricDecision(
        action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
        target_issue=target_issue,
        close_current_issue=close_current_issue,
        create_followup_issue=create_followup_issue,
        summary="Create a GitHub issue from the decoded body.",
        issue_body_base64=b64(body_text),
        codex_body_base64=None,
        review_base64=None,
        followup_issue_body_base64=(b64(followup_text) if followup_text is not None else None),
        raw_json="{}",
        raw_segment="segment",
    )


def build_issue_create_reply(
    body_text: str,
    *,
    target_issue: str | None = "none",
    close_current_issue: bool = False,
    create_followup_issue: bool = False,
    followup_text: str | None = None,
) -> str:
    parts = [
        "あなた:",
        "request body",
        "ChatGPT:",
        issue_centric_contract.ISSUE_BODY_START,
        b64(body_text),
        issue_centric_contract.ISSUE_BODY_END,
    ]
    if followup_text is not None:
        parts.extend(
            [
                issue_centric_contract.FOLLOWUP_ISSUE_BODY_START,
                b64(followup_text),
                issue_centric_contract.FOLLOWUP_ISSUE_BODY_END,
            ]
        )
    parts.extend(
        [
            issue_centric_contract.DECISION_JSON_START,
            json.dumps(
                {
                    "action": "issue_create",
                    "target_issue": target_issue if target_issue is not None else "none",
                    "close_current_issue": close_current_issue,
                    "create_followup_issue": create_followup_issue,
                    "summary": "Create the next issue.",
                },
                ensure_ascii=True,
            ),
            issue_centric_contract.DECISION_JSON_END,
            issue_centric_contract.REPLY_COMPLETE_TAG,
        ]
    )
    return "\n".join(parts)


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
                node_id="ISSUE_node_51",
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
                followup_issue_body_base64=None,
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
            followup_issue_body=None,
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

    def test_project_requirement_blocks_before_issue_create_when_state_config_is_missing(self) -> None:
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
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=fake_creator,
                project_state_resolver=lambda project_url, state_field_name, state_option_name, token: (_ for _ in ()).throw(
                    AssertionError("resolver should not be called")
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.project_sync_status, "blocked_project_preflight")
            self.assertFalse(called)
            execution = json.loads(result.execution_log_path.read_text(encoding="utf-8"))
            self.assertIsNone(execution["created_issue"])

    def test_project_issue_create_places_item_and_sets_state(self) -> None:
        prepared = self.prepared("# Ready: title\n\nBody paragraph.\n")
        create_calls: list[tuple[str, str, str, str]] = []
        item_calls: list[tuple[str, str, str]] = []
        state_calls: list[tuple[str, str, str, str, str]] = []

        def fake_creator(repository: str, title: str, body: str, token: str) -> issue_centric_issue_create.CreatedGitHubIssue:
            create_calls.append((repository, title, body, token))
            return issue_centric_issue_create.CreatedGitHubIssue(
                number=52,
                url="https://github.com/example/repo/issues/52",
                title=title,
                repository=repository,
                node_id="ISSUE_node_52",
            )

        def fake_resolver(project_url: str, state_field_name: str, state_option_name: str, token: str) -> issue_centric_github.ResolvedGitHubProjectState:
            return issue_centric_github.ResolvedGitHubProjectState(
                project_id="PVT_proj_1",
                project_url=project_url,
                project_title="Issue Centric",
                owner_login="example",
                owner_kind="users",
                state_field_id="PVTSSF_field_1",
                state_field_name=state_field_name,
                state_option_id="PVTSSO_ready",
                state_option_name=state_option_name,
            )

        def fake_item_creator(project_id: str, issue_node_id: str, token: str) -> issue_centric_github.CreatedGitHubProjectItem:
            item_calls.append((project_id, issue_node_id, token))
            return issue_centric_github.CreatedGitHubProjectItem(item_id="PVT_item_1", project_id=project_id)

        def fake_state_setter(project_id: str, item_id: str, field_id: str, option_id: str, token: str) -> None:
            state_calls.append((project_id, item_id, field_id, option_id, token))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_issue_create.execute_issue_create_action(
                prepared,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "ready",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=fake_creator,
                project_state_resolver=fake_resolver,
                project_item_creator=fake_item_creator,
                project_state_setter=fake_state_setter,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.project_sync_status, "project_state_synced")
            self.assertEqual(result.project_item_id, "PVT_item_1")
            self.assertEqual(result.project_state_field_name, "State")
            self.assertEqual(result.project_state_value_name, "ready")
            self.assertEqual(create_calls[0], ("example/repo", "Ready: title", "Body paragraph.\n", "token-123"))
            self.assertEqual(item_calls[0], ("PVT_proj_1", "ISSUE_node_52", "token-123"))
            self.assertEqual(state_calls[0], ("PVT_proj_1", "PVT_item_1", "PVTSSF_field_1", "PVTSSO_ready", "token-123"))

    def test_project_item_create_failure_is_recorded_as_partial_success(self) -> None:
        prepared = self.prepared("# Ready: title\n\nBody paragraph.\n")

        def fake_creator(repository: str, title: str, body: str, token: str) -> issue_centric_issue_create.CreatedGitHubIssue:
            return issue_centric_issue_create.CreatedGitHubIssue(
                number=53,
                url="https://github.com/example/repo/issues/53",
                title=title,
                repository=repository,
                node_id="ISSUE_node_53",
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_issue_create.execute_issue_create_action(
                prepared,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "ready",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=fake_creator,
                project_state_resolver=lambda project_url, state_field_name, state_option_name, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_proj_1",
                    project_url=project_url,
                    project_title="Issue Centric",
                    owner_login="example",
                    owner_kind="users",
                    state_field_id="PVTSSF_field_1",
                    state_field_name=state_field_name,
                    state_option_id="PVTSSO_ready",
                    state_option_name=state_option_name,
                ),
                project_item_creator=lambda project_id, issue_node_id, token: (_ for _ in ()).throw(
                    issue_centric_github.IssueCentricGitHubError("item failed")
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.project_sync_status, "issue_created_project_item_failed")
            self.assertEqual(result.created_issue.number, 53)
            self.assertIn("item failed", result.safe_stop_reason)

    def test_project_preflight_success_but_issue_create_failure_is_recorded(self) -> None:
        prepared = self.prepared("# Ready: title\n\nBody paragraph.\n")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_issue_create.execute_issue_create_action(
                prepared,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "ready",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=lambda repository, title, body, token: (_ for _ in ()).throw(
                    issue_centric_github.IssueCentricGitHubError("issue create failed")
                ),
                project_state_resolver=lambda project_url, state_field_name, state_option_name, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_proj_1",
                    project_url=project_url,
                    project_title="Issue Centric",
                    owner_login="example",
                    owner_kind="users",
                    state_field_id="PVTSSF_field_1",
                    state_field_name=state_field_name,
                    state_option_id="PVTSSO_ready",
                    state_option_name=state_option_name,
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.project_sync_status, "issue_create_failed_before_project_item")
            self.assertIsNone(result.created_issue)
            self.assertIn("issue create failed", result.safe_stop_reason)

    def test_project_state_set_failure_is_recorded_as_partial_success(self) -> None:
        prepared = self.prepared("# Ready: title\n\nBody paragraph.\n")

        def fake_creator(repository: str, title: str, body: str, token: str) -> issue_centric_issue_create.CreatedGitHubIssue:
            return issue_centric_issue_create.CreatedGitHubIssue(
                number=54,
                url="https://github.com/example/repo/issues/54",
                title=title,
                repository=repository,
                node_id="ISSUE_node_54",
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_issue_create.execute_issue_create_action(
                prepared,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "ready",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=fake_creator,
                project_state_resolver=lambda project_url, state_field_name, state_option_name, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_proj_1",
                    project_url=project_url,
                    project_title="Issue Centric",
                    owner_login="example",
                    owner_kind="users",
                    state_field_id="PVTSSF_field_1",
                    state_field_name=state_field_name,
                    state_option_id="PVTSSO_ready",
                    state_option_name=state_option_name,
                ),
                project_item_creator=lambda project_id, issue_node_id, token: issue_centric_github.CreatedGitHubProjectItem(
                    item_id="PVT_item_54",
                    project_id=project_id,
                ),
                project_state_setter=lambda project_id, item_id, field_id, option_id, token: (_ for _ in ()).throw(
                    issue_centric_github.IssueCentricGitHubError("state failed")
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.project_sync_status, "issue_created_project_state_failed")
            self.assertEqual(result.project_item_id, "PVT_item_54")
            self.assertIn("state failed", result.safe_stop_reason)

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
            project_url="",
            project_sync_status="issue_only_fallback",
            project_sync_note="No project configured.",
            project_item_id="",
            project_state_field_name="",
            project_state_value_name="",
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
                patch.object(fetch_next_prompt, "wait_for_plan_a_or_prompt_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_issue_create_action", return_value=fake_result) as exec_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "created primary issue: #77"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(exec_mock.call_count, 1)
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_execution_status"], "completed")
            self.assertEqual(saved["last_issue_centric_created_issue_number"], "77")
            self.assertEqual(saved["last_issue_centric_created_issue_url"], "https://github.com/example/repo/issues/77")
            self.assertEqual(saved["last_issue_centric_project_sync_status"], "issue_only_fallback")
            self.assertEqual(saved["last_issue_centric_project_item_id"], "")

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
            project_url="https://github.com/users/example/projects/1",
            project_sync_status="blocked_project_preflight",
            project_sync_note="Project config present.",
            project_item_id="",
            project_state_field_name="State",
            project_state_value_name="ready",
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
                patch.object(fetch_next_prompt, "wait_for_plan_a_or_prompt_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "https://github.com/users/example/projects/1", "github_project_state_field_name": "State", "github_project_default_issue_state": "ready", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_issue_create_action", return_value=fake_result),
                patch.object(fetch_next_prompt, "execute_close_current_issue") as close_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "issue_create primary execution を完了できず停止しました"):
                    fetch_next_prompt.run(dict(state), [])

            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_execution_status"], "blocked")
            self.assertEqual(saved["last_issue_centric_created_issue_number"], "")
            self.assertEqual(saved["last_issue_centric_project_sync_status"], "blocked_project_preflight")
            self.assertEqual(close_mock.call_count, 0)

    def test_fetch_next_prompt_records_project_item_and_state_when_issue_create_syncs_project(self) -> None:
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
                number=88,
                url="https://github.com/example/repo/issues/88",
                title="Ready: title",
                repository="example/repo",
                node_id="ISSUE_node_88",
            ),
            draft_log_path=REPO_ROOT / "logs" / "draft.md",
            execution_log_path=REPO_ROOT / "logs" / "execution.json",
            project_url="https://github.com/users/example/projects/1",
            project_sync_status="project_state_synced",
            project_sync_note="Project synced.",
            project_item_id="PVT_item_88",
            project_state_field_name="State",
            project_state_value_name="ready",
            safe_stop_reason="issue_create completed with project sync.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_plan_a_or_prompt_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "https://github.com/users/example/projects/1", "github_project_state_field_name": "State", "github_project_default_issue_state": "ready", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_issue_create_action", return_value=fake_result),
            ):
                with self.assertRaisesRegex(BridgeStop, "project item: PVT_item_88"):
                    fetch_next_prompt.run(dict(state), [])

            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_project_sync_status"], "project_state_synced")
            self.assertEqual(saved["last_issue_centric_project_url"], "https://github.com/users/example/projects/1")
            self.assertEqual(saved["last_issue_centric_project_item_id"], "PVT_item_88")
            self.assertEqual(saved["last_issue_centric_project_state_field"], "State")
            self.assertEqual(saved["last_issue_centric_project_state_value"], "ready")

    def test_fetch_next_prompt_executes_issue_create_followup_then_close_combo(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "review:#20",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
            "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
            "last_issue_centric_target_issue": "#20",
        }
        raw = build_issue_create_reply(
            "# Primary issue\n\nPrimary body.\n",
            target_issue="#20",
            close_current_issue=True,
            create_followup_issue=True,
            followup_text="# Follow-up issue\n\nFollow-up body.\n",
        )
        saved_states: list[dict[str, object]] = []

        primary_result = issue_centric_issue_create.IssueCreateExecutionResult(
            status="completed",
            draft=issue_centric_issue_create.IssueCreateDraft(
                title="Primary issue",
                body="Primary body.\n",
                title_line="# Primary issue",
                source_artifact_path="logs/prepared_issue_body.md",
            ),
            created_issue=issue_centric_issue_create.CreatedGitHubIssue(
                number=90,
                url="https://github.com/example/repo/issues/90",
                title="Primary issue",
                repository="example/repo",
                node_id="ISSUE_node_90",
            ),
            draft_log_path=REPO_ROOT / "logs" / "primary-draft.md",
            execution_log_path=REPO_ROOT / "logs" / "primary-execution.json",
            project_url="https://github.com/users/example/projects/1",
            project_sync_status="project_state_synced",
            project_sync_note="Primary synced.",
            project_item_id="PVT_item_90",
            project_state_field_name="State",
            project_state_value_name="ready",
            safe_stop_reason="primary issue create completed.",
        )
        followup_result = issue_centric_followup_issue.FollowupIssueExecutionResult(
            status="completed",
            followup_status="completed",
            parent_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            draft=issue_centric_issue_create.IssueCreateDraft(
                title="Follow-up issue",
                body="Follow-up body.\n",
                title_line="# Follow-up issue",
                source_artifact_path="logs/prepared_followup_issue_body.md",
            ),
            created_issue=issue_centric_github.CreatedGitHubIssue(
                number=91,
                url="https://github.com/example/repo/issues/91",
                title="Follow-up issue",
                repository="example/repo",
                node_id="ISSUE_node_91",
            ),
            issue_create_execution_log_path=REPO_ROOT / "logs" / "followup-inner.json",
            execution_log_path=REPO_ROOT / "logs" / "followup-execution.json",
            project_url="https://github.com/users/example/projects/1",
            project_sync_status="project_state_synced",
            project_sync_note="Follow-up synced.",
            project_item_id="PVT_item_91",
            project_state_field_name="State",
            project_state_value_name="ready",
            close_policy="after_issue_create_followup_success_then_close",
            safe_stop_reason="follow-up issue create completed.",
        )
        close_result = issue_centric_close_current_issue.IssueCloseExecutionResult(
            status="completed",
            close_status="closed",
            close_order="after_issue_create_followup",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            issue_before=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Current issue",
                repository="example/repo",
                state="open",
            ),
            issue_after=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Current issue",
                repository="example/repo",
                state="closed",
            ),
            execution_log_path=REPO_ROOT / "logs" / "close-execution.json",
            safe_stop_reason="closed current issue after primary and follow-up success.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_plan_a_or_prompt_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(
                    fetch_next_prompt,
                    "load_project_config",
                    return_value={
                        "github_repository": "example/repo",
                        "github_project_url": "https://github.com/users/example/projects/1",
                        "github_project_state_field_name": "State",
                        "github_project_default_issue_state": "ready",
                        "worker_repo_path": ".",
                    },
                ),
                patch.object(fetch_next_prompt, "execute_issue_create_action", return_value=primary_result) as primary_mock,
                patch.object(fetch_next_prompt, "execute_followup_issue_action", return_value=followup_result) as followup_mock,
                patch.object(fetch_next_prompt, "execute_close_current_issue", return_value=close_result) as close_mock,
                patch.object(
                    fetch_next_prompt,
                    "execute_current_issue_project_state_sync",
                    return_value=SimpleNamespace(
                        status="completed",
                        sync_status="project_state_synced",
                        lifecycle_stage="done",
                        resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                            repository="example/repo",
                            issue_number=20,
                            issue_url="https://github.com/example/repo/issues/20",
                            source_ref="#20",
                        ),
                        issue_snapshot=issue_centric_github.GitHubIssueSnapshot(
                            number=20,
                            url="https://github.com/example/repo/issues/20",
                            title="Current issue",
                            repository="example/repo",
                            state="closed",
                            node_id="ISSUE_node_20",
                        ),
                        execution_log_path=temp_root / "lifecycle-sync.json",
                        project_url="https://github.com/users/example/projects/1",
                        project_item_id="ITEM_20",
                        project_state_field_name="State",
                        project_state_value_name="done",
                        safe_stop_reason="current issue synced to done",
                    ),
                ) as lifecycle_sync_mock,
            ):
                with self.assertRaisesRegex(
                    BridgeStop,
                    "primary issue create / narrow follow-up issue create / narrow close",
                ):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(primary_mock.call_count, 1)
            self.assertEqual(followup_mock.call_count, 1)
            self.assertEqual(close_mock.call_count, 1)
            self.assertEqual(lifecycle_sync_mock.call_count, 1)
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_primary_issue_number"], "90")
            self.assertEqual(saved["last_issue_centric_followup_issue_number"], "91")
            self.assertEqual(saved["last_issue_centric_close_order"], "after_issue_create_followup")
            self.assertEqual(saved["last_issue_centric_lifecycle_sync_state_value"], "done")


class IssueCreateProjectSyncSignalTests(unittest.TestCase):
    """Tests for issue_create_project_sync_signal and issue_create_project_sync_suffix (issue #65)."""

    def test_signal_synced_for_project_state_synced(self) -> None:
        self.assertEqual(
            issue_centric_issue_create.issue_create_project_sync_signal("project_state_synced"),
            "synced",
        )

    def test_signal_skipped_no_project_for_issue_only_fallback(self) -> None:
        self.assertEqual(
            issue_centric_issue_create.issue_create_project_sync_signal("issue_only_fallback"),
            "skipped_no_project",
        )

    def test_signal_skipped_no_project_for_not_requested(self) -> None:
        self.assertEqual(
            issue_centric_issue_create.issue_create_project_sync_signal("not_requested"),
            "skipped_no_project",
        )

    def test_signal_sync_failed_for_item_create_failed(self) -> None:
        self.assertEqual(
            issue_centric_issue_create.issue_create_project_sync_signal("issue_created_project_item_failed"),
            "sync_failed",
        )

    def test_signal_sync_failed_for_state_set_failed(self) -> None:
        self.assertEqual(
            issue_centric_issue_create.issue_create_project_sync_signal("issue_created_project_state_failed"),
            "sync_failed",
        )

    def test_signal_sync_failed_for_blocked_project_preflight(self) -> None:
        self.assertEqual(
            issue_centric_issue_create.issue_create_project_sync_signal("blocked_project_preflight"),
            "sync_failed",
        )

    def test_suffix_format_synced(self) -> None:
        self.assertEqual(
            issue_centric_issue_create.issue_create_project_sync_suffix("project_state_synced"),
            " [project_sync: signal=synced]",
        )

    def test_suffix_format_skipped_no_project(self) -> None:
        self.assertEqual(
            issue_centric_issue_create.issue_create_project_sync_suffix("issue_only_fallback"),
            " [project_sync: signal=skipped_no_project]",
        )

    def test_suffix_format_sync_failed_includes_reason(self) -> None:
        suffix = issue_centric_issue_create.issue_create_project_sync_suffix("issue_created_project_item_failed")
        self.assertIn("signal=sync_failed", suffix)
        self.assertIn("reason=issue_created_project_item_failed", suffix)

    def test_safe_stop_reason_contains_synced_suffix_when_project_synced(self) -> None:
        prepared = issue_centric_transport.decode_issue_centric_decision(
            build_decision("# Ready: title\n\nBody paragraph.\n")
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_issue_create.execute_issue_create_action(
                prepared,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "ready",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=lambda repo, title, body, token: issue_centric_issue_create.CreatedGitHubIssue(
                    number=101, url="https://github.com/example/repo/issues/101",
                    title=title, repository=repo, node_id="NODE_101",
                ),
                project_state_resolver=lambda url, field, state, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_p", project_url=url, project_title="Backlog",
                    owner_login="example", owner_kind="users",
                    state_field_id="FIELD_s", state_field_name=field,
                    state_option_id="OPT_r", state_option_name=state,
                ),
                project_item_creator=lambda pid, nid, token: issue_centric_github.CreatedGitHubProjectItem(
                    item_id="ITEM_101", project_id=pid
                ),
                project_state_setter=lambda pid, iid, fid, oid, token: None,
                env={"GITHUB_TOKEN": "token-x"},
            )

        self.assertEqual(result.project_sync_status, "project_state_synced")
        self.assertIn("[project_sync: signal=synced]", result.safe_stop_reason)

    def test_safe_stop_reason_contains_skipped_no_project_when_no_project_configured(self) -> None:
        prepared = issue_centric_transport.decode_issue_centric_decision(
            build_decision("# Ready: title\n\nBody paragraph.\n")
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
                issue_creator=lambda repo, title, body, token: issue_centric_issue_create.CreatedGitHubIssue(
                    number=102, url="https://github.com/example/repo/issues/102",
                    title=title, repository=repo, node_id="NODE_102",
                ),
                env={"GITHUB_TOKEN": "token-x"},
            )

        self.assertEqual(result.project_sync_status, "issue_only_fallback")
        self.assertIn("[project_sync: signal=skipped_no_project]", result.safe_stop_reason)

    def test_safe_stop_reason_contains_sync_failed_when_project_item_create_fails(self) -> None:
        prepared = issue_centric_transport.decode_issue_centric_decision(
            build_decision("# Ready: title\n\nBody paragraph.\n")
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_issue_create.execute_issue_create_action(
                prepared,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "ready",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=lambda repo, title, body, token: issue_centric_issue_create.CreatedGitHubIssue(
                    number=103, url="https://github.com/example/repo/issues/103",
                    title=title, repository=repo, node_id="NODE_103",
                ),
                project_state_resolver=lambda url, field, state, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_p", project_url=url, project_title="Backlog",
                    owner_login="example", owner_kind="users",
                    state_field_id="FIELD_s", state_field_name=field,
                    state_option_id="OPT_r", state_option_name=state,
                ),
                project_item_creator=lambda pid, nid, token: (_ for _ in ()).throw(
                    issue_centric_github.IssueCentricGitHubError("item failed")
                ),
                env={"GITHUB_TOKEN": "token-x"},
            )

        self.assertEqual(result.project_sync_status, "issue_created_project_item_failed")
        self.assertIn("signal=sync_failed", result.safe_stop_reason)
        self.assertIn("reason=issue_created_project_item_failed", result.safe_stop_reason)

    def test_no_sync_suffix_when_issue_create_blocked_before_project_check(self) -> None:
        """Regression: safe_stop_reason must not have a sync suffix when status is not_requested."""
        prepared = issue_centric_transport.decode_issue_centric_decision(
            build_decision("# Ready: title\n\nBody paragraph.\n")
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
                issue_creator=lambda repo, title, body, token: (_ for _ in ()).throw(
                    issue_centric_issue_create.IssueCentricIssueCreateError("boom")
                ),
                env={"GITHUB_TOKEN": "token-x"},
            )

        # project_sync_status == "not_requested" (initial) — no suffix expected
        self.assertEqual(result.project_sync_status, "not_requested")
        self.assertNotIn("[project_sync:", result.safe_stop_reason)


if __name__ == "__main__":
    unittest.main()
