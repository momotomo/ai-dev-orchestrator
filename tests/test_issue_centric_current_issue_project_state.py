from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import issue_centric_contract  # noqa: E402
import issue_centric_current_issue_project_state  # noqa: E402
import issue_centric_github  # noqa: E402
import issue_centric_transport  # noqa: E402


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class TempLogWriter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.counter = 0

    def __call__(self, prefix: str, content: str, suffix: str = "md") -> Path:
        self.counter += 1
        path = self.root / f"{self.counter:02d}_{prefix}.{suffix}"
        path.write_text(content, encoding="utf-8")
        return path


class CurrentIssueProjectStateSyncTests(unittest.TestCase):
    def prepared(
        self,
        *,
        action: issue_centric_contract.IssueCentricAction,
        target_issue: str = "#20",
        close_current_issue: bool = False,
        create_followup_issue: bool = False,
        codex_text: str | None = None,
        review_text: str | None = None,
    ) -> issue_centric_transport.PreparedIssueCentricDecision:
        return issue_centric_transport.decode_issue_centric_decision(
            issue_centric_contract.IssueCentricDecision(
                action=action,
                target_issue=target_issue,
                close_current_issue=close_current_issue,
                create_followup_issue=create_followup_issue,
                summary="Sync current issue lifecycle state.",
                issue_body_base64=None,
                codex_body_base64=(b64(codex_text) if codex_text is not None else None),
                review_base64=(b64(review_text) if review_text is not None else None),
                followup_issue_body_base64=None,
                raw_json="{}",
                raw_segment="segment",
            )
        )

    def test_returns_not_requested_when_no_project_is_configured(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
            codex_text="Implement the issue.\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_current_issue_project_state.execute_current_issue_project_state_sync(
                prepared,
                lifecycle_stage="in_progress",
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/codex.json",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
            )

            self.assertEqual(result.status, "not_requested")
            self.assertEqual(result.sync_status, "not_requested_no_project")

    def test_syncs_current_issue_into_configured_project_state(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
            codex_text="Implement the issue.\n",
        )
        state_calls: list[tuple[str, str, str, str, str]] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_current_issue_project_state.execute_current_issue_project_state_sync(
                prepared,
                lifecycle_stage="in_progress",
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_in_progress_state": "in_progress",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/codex.json",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Current issue",
                    repository=repository,
                    state="open",
                    node_id="ISSUE_node_20",
                ),
                project_state_resolver=lambda project_url, state_field_name, state_option_name, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_proj_1",
                    project_url=project_url,
                    project_title="Issue Centric",
                    owner_login="example",
                    owner_kind="users",
                    state_field_id="PVTSSF_field_1",
                    state_field_name=state_field_name,
                    state_option_id="PVTSSO_in_progress",
                    state_option_name=state_option_name,
                ),
                project_item_resolver=lambda project_id, issue_node_id, token: issue_centric_github.ResolvedGitHubProjectItem(
                    item_id="PVT_item_20",
                    project_id=project_id,
                    issue_node_id=issue_node_id,
                    issue_number=20,
                    repository="example/repo",
                ),
                project_state_setter=lambda project_id, item_id, field_id, option_id, token: state_calls.append(
                    (project_id, item_id, field_id, option_id, token)
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.sync_status, "project_state_synced")
            self.assertEqual(result.project_item_id, "PVT_item_20")
            self.assertEqual(result.project_state_value_name, "in_progress")
            self.assertEqual(
                state_calls[0],
                ("PVT_proj_1", "PVT_item_20", "PVTSSF_field_1", "PVTSSO_in_progress", "token-123"),
            )

    def test_missing_lifecycle_config_blocks_before_project_mutation(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
            review_text="## Review\n\n- OK\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_current_issue_project_state.execute_current_issue_project_state_sync(
                prepared,
                lifecycle_stage="review",
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_review_state": "",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/review.json",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.sync_status, "project_state_sync_failed")
            self.assertIn("github_project_review_state must not be empty", result.safe_stop_reason)

    def test_missing_project_item_is_recorded_as_blocked(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
            codex_text="Implement the issue.\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_current_issue_project_state.execute_current_issue_project_state_sync(
                prepared,
                lifecycle_stage="in_progress",
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_in_progress_state": "in_progress",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/codex.json",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Current issue",
                    repository=repository,
                    state="open",
                    node_id="ISSUE_node_20",
                ),
                project_state_resolver=lambda project_url, state_field_name, state_option_name, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_proj_1",
                    project_url=project_url,
                    project_title="Issue Centric",
                    owner_login="example",
                    owner_kind="users",
                    state_field_id="PVTSSF_field_1",
                    state_field_name=state_field_name,
                    state_option_id="PVTSSO_in_progress",
                    state_option_name=state_option_name,
                ),
                project_item_resolver=lambda project_id, issue_node_id, token: (_ for _ in ()).throw(
                    issue_centric_github.IssueCentricGitHubError("project item was not found")
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.sync_status, "project_state_sync_failed")
            self.assertIn("project item was not found", result.safe_stop_reason)

    def test_state_mutation_failure_is_recorded_without_hiding_main_stage(self) -> None:
        prepared = self.prepared(
            action=issue_centric_contract.IssueCentricAction.NO_ACTION,
            close_current_issue=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_current_issue_project_state.execute_current_issue_project_state_sync(
                prepared,
                lifecycle_stage="done",
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_done_state": "done",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_action_execution_log="logs/close.json",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Current issue",
                    repository=repository,
                    state="closed",
                    node_id="ISSUE_node_20",
                ),
                project_state_resolver=lambda project_url, state_field_name, state_option_name, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_proj_1",
                    project_url=project_url,
                    project_title="Issue Centric",
                    owner_login="example",
                    owner_kind="users",
                    state_field_id="PVTSSF_field_1",
                    state_field_name=state_field_name,
                    state_option_id="PVTSSO_done",
                    state_option_name=state_option_name,
                ),
                project_item_resolver=lambda project_id, issue_node_id, token: issue_centric_github.ResolvedGitHubProjectItem(
                    item_id="PVT_item_20",
                    project_id=project_id,
                    issue_node_id=issue_node_id,
                    issue_number=20,
                    repository="example/repo",
                ),
                project_state_setter=lambda project_id, item_id, field_id, option_id, token: (_ for _ in ()).throw(
                    issue_centric_github.IssueCentricGitHubError("state mutation failed")
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.sync_status, "project_state_sync_failed")
            self.assertIn("state mutation failed", result.safe_stop_reason)
            execution = json.loads(result.execution_log_path.read_text(encoding="utf-8"))
            self.assertEqual(execution["lifecycle_stage"], "done")


if __name__ == "__main__":
    unittest.main()
