from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import issue_centric_github
from issue_centric_parent_update import execute_parent_issue_update_after_close


class ParentIssueUpdateAfterCloseTests(unittest.TestCase):
    def _log_writer(self, root: Path):
        def write(stem: str, content: str, extension: str) -> Path:
            path = root / f"{stem}.{extension}"
            path.write_text(content, encoding="utf-8")
            return path

        return write

    def test_execute_parent_issue_update_after_close_creates_comment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: list[tuple[str, int, str]] = []

            def fake_comment_creator(repository: str, issue_number: int, body: str, token: str):
                calls.append((repository, issue_number, body))
                return issue_centric_github.CreatedGitHubComment(
                    comment_id=901,
                    url=f"https://github.com/{repository}/issues/{issue_number}#issuecomment-901",
                    body=body,
                    repository=repository,
                    issue_number=issue_number,
                )

            close_execution = SimpleNamespace(
                status="completed",
                close_status="closed",
                resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                    repository="example/repo",
                    issue_number=7,
                    issue_url="https://github.com/example/repo/issues/7",
                    source_ref="#7",
                ),
                issue_before=issue_centric_github.GitHubIssueSnapshot(
                    number=7,
                    url="https://github.com/example/repo/issues/7",
                    title="Ready child",
                    repository="example/repo",
                    state="open",
                    body="Parent: #1\n",
                ),
                issue_after=issue_centric_github.GitHubIssueSnapshot(
                    number=7,
                    url="https://github.com/example/repo/issues/7",
                    title="Ready child",
                    repository="example/repo",
                    state="closed",
                    body="Parent: #1\n",
                ),
            )

            result = execute_parent_issue_update_after_close(
                close_execution=close_execution,
                prior_state={},
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/close.json",
                log_writer=self._log_writer(root),
                repo_relative=lambda path: path.name,
                comment_creator=fake_comment_creator,
                env={"GITHUB_TOKEN": "test-token"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.update_status, "comment_created")
            self.assertEqual(result.resolved_parent_issue.issue_number, 1)
            self.assertEqual(result.created_comment.issue_number, 1)
            self.assertEqual(calls[0][0], "example/repo")
            self.assertEqual(calls[0][1], 1)
            self.assertIn("`#7 Ready child`", calls[0][2])

    def test_execute_parent_issue_update_after_close_skips_when_already_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            close_execution = SimpleNamespace(
                status="completed",
                close_status="closed",
                resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                    repository="example/repo",
                    issue_number=7,
                    issue_url="https://github.com/example/repo/issues/7",
                    source_ref="#7",
                ),
                issue_before=issue_centric_github.GitHubIssueSnapshot(
                    number=7,
                    url="https://github.com/example/repo/issues/7",
                    title="Ready child",
                    repository="example/repo",
                    state="open",
                    body="Parent: #1\n",
                ),
                issue_after=issue_centric_github.GitHubIssueSnapshot(
                    number=7,
                    url="https://github.com/example/repo/issues/7",
                    title="Ready child",
                    repository="example/repo",
                    state="closed",
                    body="Parent: #1\n",
                ),
            )

            result = execute_parent_issue_update_after_close(
                close_execution=close_execution,
                prior_state={
                    "last_issue_centric_parent_update_status": "comment_created",
                    "last_issue_centric_parent_update_issue": "https://github.com/example/repo/issues/1",
                    "last_issue_centric_parent_update_closed_issue": "https://github.com/example/repo/issues/7",
                },
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/close.json",
                log_writer=self._log_writer(root),
                repo_relative=lambda path: path.name,
                comment_creator=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not comment")),
                env={"GITHUB_TOKEN": "test-token"},
            )

            self.assertEqual(result.status, "not_requested")
            self.assertEqual(result.update_status, "already_recorded")
