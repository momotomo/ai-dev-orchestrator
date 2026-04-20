from __future__ import annotations

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
import issue_centric_close_current_issue  # noqa: E402
import issue_centric_contract  # noqa: E402
import issue_centric_github  # noqa: E402
import issue_centric_issue_create  # noqa: E402
import issue_centric_parent_update  # noqa: E402
import issue_centric_transport  # noqa: E402
from _bridge_common import BridgeStop  # noqa: E402


def b64(text: str) -> str:
    import base64

    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build_decision(
    *,
    action: issue_centric_contract.IssueCentricAction,
    target_issue: str | None,
    close_current_issue: bool,
) -> issue_centric_contract.IssueCentricDecision:
    return issue_centric_contract.IssueCentricDecision(
        action=action,
        target_issue=target_issue,
        close_current_issue=close_current_issue,
        create_followup_issue=False,
        summary="Close the current issue if allowed.",
        issue_body_base64=(b64("# Next issue\n\nBody\n") if action is issue_centric_contract.IssueCentricAction.ISSUE_CREATE else None),
        codex_body_base64=(b64("Run body\n") if action is issue_centric_contract.IssueCentricAction.CODEX_RUN else None),
        review_base64=None,
        followup_issue_body_base64=None,
        raw_json="{}",
        raw_segment="segment",
    )


def build_reply(
    *,
    action: str,
    target_issue: str | None,
    close_current_issue: bool,
    include_issue_body: bool = False,
) -> str:
    parts = [
        "あなた:",
        "request body",
        "ChatGPT:",
    ]
    if include_issue_body:
        parts.extend(
            [
                issue_centric_contract.ISSUE_BODY_START,
                b64("# Next issue\n\nBody\n"),
                issue_centric_contract.ISSUE_BODY_END,
            ]
        )
    parts.extend(
        [
            issue_centric_contract.DECISION_JSON_START,
            json.dumps(
                {
                    "action": action,
                    "target_issue": target_issue if target_issue is not None else "none",
                    "close_current_issue": close_current_issue,
                    "create_followup_issue": False,
                    "summary": "Close the current issue if allowed.",
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


class CloseCurrentIssueExecutionTests(unittest.TestCase):
    def prepared(
        self,
        *,
        action: issue_centric_contract.IssueCentricAction,
        target_issue: str | None,
        close_current_issue: bool = True,
    ) -> issue_centric_transport.PreparedIssueCentricDecision:
        return issue_centric_transport.decode_issue_centric_decision(
            build_decision(
                action=action,
                target_issue=target_issue,
                close_current_issue=close_current_issue,
            )
        )

    def test_close_current_issue_executes_close_mutation(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.NO_ACTION,
            target_issue="#20",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Ready issue",
                    repository=repository,
                    state="open",
                ),
                issue_closer=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Ready issue",
                    repository=repository,
                    state="closed",
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.close_status, "closed")
            self.assertEqual(result.close_order, "after_no_action")
            self.assertEqual(result.issue_after.title, "Ready issue")
            execution_log = json.loads(result.execution_log_path.read_text(encoding="utf-8"))
            self.assertEqual(execution_log["issue_after"]["state"], "closed")

    def test_close_current_issue_blocks_on_target_mismatch(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.NO_ACTION,
            target_issue="#20",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/21"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.close_status, "blocked")
            self.assertIn("does not match", result.safe_stop_reason)

    def test_close_current_issue_treats_already_closed_as_completed_noop(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.NO_ACTION,
            target_issue="#20",
        )
        closed_called = False

        def fake_closer(repository: str, issue_number: int, token: str) -> issue_centric_github.GitHubIssueSnapshot:
            nonlocal closed_called
            closed_called = True
            raise AssertionError("should not close")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Ready issue",
                    repository=repository,
                    state="closed",
                ),
                issue_closer=fake_closer,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.close_status, "already_closed")
            self.assertFalse(closed_called)

    def test_close_current_issue_records_mutation_failure_as_blocked(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.NO_ACTION,
            target_issue="#20",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Ready issue",
                    repository=repository,
                    state="open",
                ),
                issue_closer=lambda repository, issue_number, token: (_ for _ in ()).throw(
                    issue_centric_github.IssueCentricGitHubError("close failed")
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.close_status, "blocked")
            self.assertIn("close failed", result.safe_stop_reason)

    def test_close_current_issue_blocks_codex_run_combination(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
            target_issue="#20",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.close_order, "blocked_codex_run")
            self.assertIn("action=codex_run", result.safe_stop_reason)

    def test_close_current_issue_allows_human_review_when_opted_in(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
            target_issue="#20",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/review.json",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Reviewed issue",
                    repository=repository,
                    state="open",
                ),
                issue_closer=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Reviewed issue",
                    repository=repository,
                    state="closed",
                ),
                allow_human_review_close=True,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.close_status, "closed")
            self.assertEqual(result.close_order, "after_human_review")
            self.assertIn("after the review comment was posted", result.safe_stop_reason)

    def test_close_current_issue_allows_human_review_followup_when_opted_in(self) -> None:
        prepared = issue_centric_transport.decode_issue_centric_decision(
            issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                summary="Close after review and follow-up.",
                issue_body_base64=None,
                codex_body_base64=None,
                review_base64=b64("## Review\n\n- OK\n"),
                followup_issue_body_base64=b64("# Follow-up\n\nBody\n"),
                raw_json="{}",
                raw_segment="segment",
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/review_followup.json",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Reviewed issue",
                    repository=repository,
                    state="open",
                ),
                issue_closer=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Reviewed issue",
                    repository=repository,
                    state="closed",
                ),
                allow_human_review_close=True,
                allow_human_review_followup_close=True,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.close_status, "closed")
            self.assertEqual(result.close_order, "after_human_review_followup")
            self.assertIn("review comment and follow-up issue path", result.safe_stop_reason)

    def test_close_current_issue_allows_issue_create_followup_when_opted_in(self) -> None:
        prepared = issue_centric_transport.decode_issue_centric_decision(
            issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                summary="Close after primary and follow-up issue create.",
                issue_body_base64=b64("# Primary issue\n\nBody\n"),
                codex_body_base64=None,
                review_base64=None,
                followup_issue_body_base64=b64("# Follow-up\n\nBody\n"),
                raw_json="{}",
                raw_segment="segment",
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/issue_create_followup.json",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Current issue",
                    repository=repository,
                    state="open",
                ),
                issue_closer=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Current issue",
                    repository=repository,
                    state="closed",
                ),
                allow_issue_create_followup_close=True,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.close_status, "closed")
            self.assertEqual(result.close_order, "after_issue_create_followup")
            self.assertIn("primary issue and follow-up issue paths", result.safe_stop_reason)

    def test_close_current_issue_allows_codex_run_followup_when_opted_in(self) -> None:
        prepared = issue_centric_transport.decode_issue_centric_decision(
            issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                summary="Close after codex launch / continuation and follow-up issue create.",
                issue_body_base64=None,
                codex_body_base64=b64("Implement the issue.\n"),
                review_base64=None,
                followup_issue_body_base64=b64("# Follow-up\n\nBody\n"),
                raw_json="{}",
                raw_segment="segment",
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/codex_followup.json",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Current issue",
                    repository=repository,
                    state="open",
                ),
                issue_closer=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Current issue",
                    repository=repository,
                    state="closed",
                ),
                allow_codex_run_followup_close=True,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.close_status, "closed")
            self.assertEqual(result.close_order, "after_codex_run_followup")
            self.assertIn("Codex launch / continuation path and follow-up issue path", result.safe_stop_reason)


    def test_close_current_issue_blocks_when_no_target_can_be_resolved(self) -> None:
        """close_current_issue is blocked when target_issue=none and state has no backfill (#43)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepared = self.prepared(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                target_issue=None,  # no explicit target
                close_current_issue=True,
            )
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={
                    # no last_issue_centric_resolved_issue
                    # no last_issue_centric_target_issue
                },
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=root,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="",
                log_writer=TempLogWriter(root),
                repo_relative=lambda p: str(p),
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.close_status, "blocked")
        self.assertIn("could not resolve", result.safe_stop_reason)

    def test_close_current_issue_with_project_url_configured_still_closes_issue(self) -> None:
        """github_project_url configured must NOT block the issue close mutation.

        Previously, execute_close_current_issue() raised an error when
        github_project_url was set ('not implemented in this slice').
        Project state sync for 'done' is handled by issue_centric_execution.py
        separately after the close completes; the close function itself only
        needs to perform the GitHub issue close.
        """
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.NO_ACTION,
            target_issue="#2",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/2"},
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/orgs/example/projects/1",
                    "github_project_state_field_name": "Status",
                    "github_project_done_state": "Done",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="PromptDraft normalized update flow",
                    repository=repository,
                    state="open",
                ),
                issue_closer=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="PromptDraft normalized update flow",
                    repository=repository,
                    state="closed",
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

        # The close must complete successfully even with github_project_url configured.
        # Project 'done' sync is handled by issue_centric_execution.py after this returns.
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.close_status, "closed")
        self.assertEqual(result.close_order, "after_no_action")
        self.assertIsNotNone(result.issue_after)
        self.assertEqual(result.issue_after.state, "closed")

    def test_close_current_issue_without_project_url_still_succeeds(self) -> None:
        """Regression: github_project_url empty still results in completed close."""
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.NO_ACTION,
            target_issue="#5",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_close_current_issue.execute_close_current_issue(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/5"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Some issue",
                    repository=repository,
                    state="open",
                ),
                issue_closer=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Some issue",
                    repository=repository,
                    state="closed",
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.close_status, "closed")


class FetchNextPromptCloseIntegrationTests(unittest.TestCase):
    def test_issue_create_can_close_current_issue_after_creation(self) -> None:
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
        raw = build_reply(action="issue_create", target_issue="#20", close_current_issue=True, include_issue_body=True)
        saved_states: list[dict[str, object]] = []

        fake_issue_create = issue_centric_issue_create.IssueCreateExecutionResult(
            status="completed",
            draft=issue_centric_issue_create.IssueCreateDraft(
                title="Next issue",
                body="Body\n",
                title_line="# Next issue",
                source_artifact_path="logs/prepared_issue_body.md",
            ),
            created_issue=issue_centric_issue_create.CreatedGitHubIssue(
                number=77,
                url="https://github.com/example/repo/issues/77",
                title="Next issue",
                repository="example/repo",
            ),
            draft_log_path=REPO_ROOT / "logs" / "draft.md",
            execution_log_path=REPO_ROOT / "logs" / "issue_create.json",
            project_url="",
            project_sync_status="issue_only_fallback",
            project_sync_note="No project configured.",
            project_item_id="",
            project_state_field_name="",
            project_state_value_name="",
            safe_stop_reason="issue_create completed.",
        )
        fake_close = issue_centric_close_current_issue.IssueCloseExecutionResult(
            status="completed",
            close_status="closed",
            close_order="after_issue_create",
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
            execution_log_path=REPO_ROOT / "logs" / "close.json",
            safe_stop_reason="close_current_issue closed #20.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_issue_create_action", return_value=fake_issue_create) as issue_create_mock,
                patch.object(fetch_next_prompt, "execute_close_current_issue", return_value=fake_close) as close_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "close log: .*close.json"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(issue_create_mock.call_count, 1)
            self.assertEqual(close_mock.call_count, 1)
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_created_issue_number"], "77")
            self.assertEqual(saved["last_issue_centric_close_status"], "closed")
            self.assertEqual(saved["last_issue_centric_closed_issue_number"], "20")
            self.assertEqual(saved["last_issue_centric_close_order"], "after_issue_create")

    def test_no_action_can_close_current_issue(self) -> None:
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
        raw = build_reply(action="no_action", target_issue="#20", close_current_issue=True, include_issue_body=False)
        saved_states: list[dict[str, object]] = []
        fake_close = issue_centric_close_current_issue.IssueCloseExecutionResult(
            status="completed",
            close_status="closed",
            close_order="after_no_action",
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
            execution_log_path=REPO_ROOT / "logs" / "close.json",
            safe_stop_reason="close_current_issue closed #20.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_close_current_issue", return_value=fake_close) as close_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "close_current_issue の最小 mutation slice まで実行しました"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(close_mock.call_count, 1)
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_close_status"], "closed")
            self.assertEqual(saved["last_issue_centric_closed_issue_url"], "https://github.com/example/repo/issues/20")

    def test_no_action_close_runs_parent_update_in_fetch_path(self) -> None:
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
        raw = build_reply(action="no_action", target_issue="#20", close_current_issue=True, include_issue_body=False)
        saved_states: list[dict[str, object]] = []
        fake_close = issue_centric_close_current_issue.IssueCloseExecutionResult(
            status="completed",
            close_status="closed",
            close_order="after_no_action",
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
                body="Parent: #1",
            ),
            issue_after=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Current issue",
                repository="example/repo",
                state="closed",
                body="Parent: #1",
            ),
            execution_log_path=REPO_ROOT / "logs" / "close.json",
            safe_stop_reason="close_current_issue closed #20.",
        )
        fake_parent_update = issue_centric_parent_update.ParentIssueUpdateResult(
            status="completed",
            update_status="comment_created",
            resolved_parent_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=1,
                issue_url="https://github.com/example/repo/issues/1",
                source_ref="#1",
            ),
            created_comment=issue_centric_github.CreatedGitHubComment(
                comment_id=901,
                url="https://github.com/example/repo/issues/1#issuecomment-901",
                issue_number=1,
                repository="example/repo",
                body="parent updated",
            ),
            closed_issue_url="https://github.com/example/repo/issues/20",
            execution_log_path=REPO_ROOT / "logs" / "parent_update.json",
            safe_stop_reason="parent issue #1 received a completion comment after issue #20 closed.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_close_current_issue", return_value=fake_close) as close_mock,
                patch.object(fetch_next_prompt, "execute_parent_issue_update_after_close", return_value=fake_parent_update) as parent_update_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "parent update: https://github.com/example/repo/issues/1#issuecomment-901"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(close_mock.call_count, 1)
            self.assertEqual(parent_update_mock.call_count, 1)
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_close_status"], "closed")
            self.assertEqual(saved["last_issue_centric_parent_update_status"], "comment_created")
            self.assertEqual(saved["last_issue_centric_parent_update_issue"], "https://github.com/example/repo/issues/1")
            self.assertEqual(
                saved["last_issue_centric_parent_update_comment_url"],
                "https://github.com/example/repo/issues/1#issuecomment-901",
            )

    def test_codex_run_with_close_current_issue_is_staged_for_later_dispatch(self) -> None:
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
        raw = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                issue_centric_contract.CODEX_BODY_START,
                b64("Run it\n"),
                issue_centric_contract.CODEX_BODY_END,
                issue_centric_contract.DECISION_JSON_START,
                json.dumps(
                    {
                        "action": "codex_run",
                        "target_issue": "#20",
                        "close_current_issue": True,
                        "create_followup_issue": False,
                        "summary": "This should block before codex execution.",
                    },
                    ensure_ascii=True,
                ),
                issue_centric_contract.DECISION_JSON_END,
                issue_centric_contract.REPLY_COMPLETE_TAG,
            ]
        )
        saved_states: list[dict[str, object]] = []
        fake_close = issue_centric_close_current_issue.IssueCloseExecutionResult(
            status="blocked",
            close_status="blocked",
            close_order="blocked_codex_run",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            issue_before=None,
            issue_after=None,
            execution_log_path=REPO_ROOT / "logs" / "close.json",
            safe_stop_reason="action=codex_run cannot execute close_current_issue in this slice.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_close_current_issue", return_value=fake_close) as close_mock,
                patch.object(fetch_next_prompt, "execute_codex_run_action") as codex_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "prepared Codex body は次の bridge 手で codex_run dispatch に渡します"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(close_mock.call_count, 0)
            self.assertEqual(codex_mock.call_count, 0)
            saved = saved_states[0]
            self.assertEqual(saved["chatgpt_decision"], "issue_centric:codex_run")
            self.assertEqual(saved["last_issue_centric_artifact_kind"], "codex_body")
            self.assertTrue(saved["last_issue_centric_metadata_log"])
            self.assertTrue(saved["last_issue_centric_artifact_file"])
            self.assertEqual(saved["last_issue_centric_execution_status"], "")
            self.assertEqual(saved["last_issue_centric_close_status"], "")
            self.assertEqual(saved["last_issue_centric_close_order"], "")


if __name__ == "__main__":
    unittest.main()
