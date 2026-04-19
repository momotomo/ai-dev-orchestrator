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
import issue_centric_close_current_issue  # noqa: E402
import issue_centric_codex_launch  # noqa: E402
import issue_centric_codex_run  # noqa: E402
import issue_centric_contract  # noqa: E402
import issue_centric_followup_issue  # noqa: E402
import issue_centric_github  # noqa: E402
import issue_centric_issue_create  # noqa: E402
import issue_centric_transport  # noqa: E402
from _bridge_common import BridgeStop  # noqa: E402


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build_decision(
    *,
    target_issue: str | None,
    followup_text: str | None,
    close_current_issue: bool = False,
) -> issue_centric_contract.IssueCentricDecision:
    return issue_centric_contract.IssueCentricDecision(
        action=issue_centric_contract.IssueCentricAction.NO_ACTION,
        target_issue=target_issue,
        close_current_issue=close_current_issue,
        create_followup_issue=True,
        summary="Create one follow-up issue.",
        issue_body_base64=None,
        codex_body_base64=None,
        review_base64=None,
        followup_issue_body_base64=(b64(followup_text) if followup_text is not None else None),
        raw_json="{}",
        raw_segment="segment",
    )


def build_raw_reply(
    *,
    action: str,
    target_issue: str | None,
    close_current_issue: bool,
    create_followup_issue: bool,
    followup_text: str | None = None,
    issue_text: str | None = None,
    codex_text: str | None = None,
) -> str:
    parts = [
        "あなた:",
        "request body",
        "ChatGPT:",
    ]
    if issue_text is not None:
        parts.extend(
            [
                issue_centric_contract.ISSUE_BODY_START,
                b64(issue_text),
                issue_centric_contract.ISSUE_BODY_END,
            ]
        )
    if followup_text is not None:
        parts.extend(
            [
                issue_centric_contract.FOLLOWUP_ISSUE_BODY_START,
                b64(followup_text),
                issue_centric_contract.FOLLOWUP_ISSUE_BODY_END,
            ]
        )
    if codex_text is not None:
        parts.extend(
            [
                issue_centric_contract.CODEX_BODY_START,
                b64(codex_text),
                issue_centric_contract.CODEX_BODY_END,
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
                    "create_followup_issue": create_followup_issue,
                    "summary": "Create one follow-up issue.",
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


class FollowupIssueExecutionTests(unittest.TestCase):
    def prepared(
        self,
        *,
        target_issue: str | None = "#20",
        followup_text: str | None = "# Follow-up title\n\nBody paragraph.\n",
        close_current_issue: bool = False,
    ) -> issue_centric_transport.PreparedIssueCentricDecision:
        return issue_centric_transport.decode_issue_centric_decision(
            build_decision(
                target_issue=target_issue,
                followup_text=followup_text,
                close_current_issue=close_current_issue,
            )
        )

    def test_execute_followup_issue_creates_issue_and_records_parent_relation(self) -> None:
        prepared = self.prepared()

        def fake_creator(repository: str, title: str, body: str, token: str) -> issue_centric_github.CreatedGitHubIssue:
            return issue_centric_github.CreatedGitHubIssue(
                number=71,
                url="https://github.com/example/repo/issues/71",
                title=title,
                repository=repository,
                node_id="ISSUE_node_71",
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=fake_creator,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.followup_status, "completed")
            self.assertEqual(result.created_issue.number, 71)
            self.assertEqual(result.parent_issue.issue_number, 20)
            execution = json.loads(result.execution_log_path.read_text(encoding="utf-8"))
            self.assertEqual(execution["current_issue"]["number"], 20)
            self.assertEqual(execution["created_followup_issue"]["number"], 71)

    def test_followup_issue_create_reuses_project_sync_when_configured(self) -> None:
        prepared = self.prepared()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "planned",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=lambda repository, title, body, token: issue_centric_github.CreatedGitHubIssue(
                    number=72,
                    url="https://github.com/example/repo/issues/72",
                    title=title,
                    repository=repository,
                    node_id="ISSUE_node_72",
                ),
                project_state_resolver=lambda project_url, state_field_name, state_option_name, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_proj",
                    project_url=project_url,
                    project_title="Issue Backlog",
                    owner_login="example",
                    owner_kind="user",
                    state_field_id="FIELD_state",
                    state_field_name=state_field_name,
                    state_option_id="OPT_planned",
                    state_option_name=state_option_name,
                ),
                project_item_creator=lambda project_id, issue_node_id, token: issue_centric_github.CreatedGitHubProjectItem(
                    item_id="ITEM_72",
                    project_id=project_id,
                ),
                project_state_setter=lambda project_id, item_id, field_id, option_id, token: None,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.project_sync_status, "project_state_synced")
            self.assertEqual(result.project_item_id, "ITEM_72")

    def test_followup_issue_blocks_without_decoded_body(self) -> None:
        prepared = issue_centric_transport.PreparedIssueCentricDecision(
            decision=build_decision(
                target_issue="#20",
                followup_text="# Follow-up title\n\nBody paragraph.\n",
                close_current_issue=False,
            ),
            issue_body=None,
            codex_body=None,
            review_body=None,
            followup_issue_body=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                env={"GITHUB_TOKEN": "token-123"},
            )
            self.assertEqual(result.status, "blocked")
            self.assertIn("requires a decoded CHATGPT_FOLLOWUP_ISSUE_BODY", result.safe_stop_reason)

    def test_followup_issue_partial_project_failure_is_recorded(self) -> None:
        prepared = self.prepared()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "planned",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=lambda repository, title, body, token: issue_centric_github.CreatedGitHubIssue(
                    number=73,
                    url="https://github.com/example/repo/issues/73",
                    title=title,
                    repository=repository,
                    node_id="ISSUE_node_73",
                ),
                project_state_resolver=lambda project_url, state_field_name, state_option_name, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_proj",
                    project_url=project_url,
                    project_title="Issue Backlog",
                    owner_login="example",
                    owner_kind="user",
                    state_field_id="FIELD_state",
                    state_field_name=state_field_name,
                    state_option_id="OPT_planned",
                    state_option_name=state_option_name,
                ),
                project_item_creator=lambda project_id, issue_node_id, token: (_ for _ in ()).throw(
                    issue_centric_github.IssueCentricGitHubError("item create failed")
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )
            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.project_sync_status, "issue_created_project_item_failed")
            self.assertEqual(result.created_issue.number, 73)

    def test_followup_issue_can_run_for_human_review_combo_when_opted_in(self) -> None:
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
            target_issue="#20",
            close_current_issue=False,
            create_followup_issue=True,
            summary="Create follow-up after review.",
            issue_body_base64=None,
            codex_body_base64=None,
            review_base64=b64("## Review\n\n- Split follow-up\n"),
            followup_issue_body_base64=b64("# Follow-up title\n\nBody paragraph.\n"),
            raw_json="{}",
            raw_segment="segment",
        )
        prepared = issue_centric_transport.decode_issue_centric_decision(decision)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                allow_human_review_combo=True,
                issue_creator=lambda repository, title, body, token: issue_centric_github.CreatedGitHubIssue(
                    number=75,
                    url="https://github.com/example/repo/issues/75",
                    title=title,
                    repository=repository,
                    node_id="ISSUE_node_75",
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.followup_status, "completed")
            self.assertEqual(result.close_policy, "after_review_followup_success_only")
            self.assertEqual(result.created_issue.number, 75)

    def test_followup_issue_can_run_for_issue_create_combo_when_opted_in(self) -> None:
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
            target_issue="#20",
            close_current_issue=False,
            create_followup_issue=True,
            summary="Create primary and follow-up issues.",
            issue_body_base64=b64("# Primary issue\n\nPrimary body.\n"),
            codex_body_base64=None,
            review_base64=None,
            followup_issue_body_base64=b64("# Follow-up title\n\nBody paragraph.\n"),
            raw_json="{}",
            raw_segment="segment",
        )
        prepared = issue_centric_transport.decode_issue_centric_decision(decision)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                allow_issue_create_combo=True,
                issue_creator=lambda repository, title, body, token: issue_centric_github.CreatedGitHubIssue(
                    number=76,
                    url="https://github.com/example/repo/issues/76",
                    title=title,
                    repository=repository,
                    node_id="ISSUE_node_76",
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.followup_status, "completed")
            self.assertEqual(result.close_policy, "after_issue_create_followup_success_only")
            self.assertEqual(result.created_issue.number, 76)

    def test_followup_issue_can_run_for_codex_run_combo_when_opted_in(self) -> None:
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
            target_issue="#20",
            close_current_issue=False,
            create_followup_issue=True,
            summary="Run codex and create follow-up.",
            issue_body_base64=None,
            codex_body_base64=b64("Implement the issue.\n"),
            review_base64=None,
            followup_issue_body_base64=b64("# Follow-up title\n\nBody paragraph.\n"),
            raw_json="{}",
            raw_segment="segment",
        )
        prepared = issue_centric_transport.decode_issue_centric_decision(decision)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                allow_codex_run_combo=True,
                issue_creator=lambda repository, title, body, token: issue_centric_github.CreatedGitHubIssue(
                    number=77,
                    url="https://github.com/example/repo/issues/77",
                    title=title,
                    repository=repository,
                    node_id="ISSUE_node_77",
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.followup_status, "completed")
            self.assertEqual(result.close_policy, "after_codex_followup_success_only")
            self.assertEqual(result.created_issue.number, 77)

    def test_followup_issue_sets_close_policy_for_codex_run_followup_close_combo(self) -> None:
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
            target_issue="#20",
            close_current_issue=True,
            create_followup_issue=True,
            summary="Run codex, create follow-up, and then close current issue.",
            issue_body_base64=None,
            codex_body_base64=b64("Implement the issue.\n"),
            review_base64=None,
            followup_issue_body_base64=b64("# Follow-up title\n\nBody paragraph.\n"),
            raw_json="{}",
            raw_segment="segment",
        )
        prepared = issue_centric_transport.decode_issue_centric_decision(decision)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                allow_codex_run_combo=True,
                issue_creator=lambda repository, title, body, token: issue_centric_github.CreatedGitHubIssue(
                    number=78,
                    url="https://github.com/example/repo/issues/78",
                    title=title,
                    repository=repository,
                    node_id="ISSUE_node_78",
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.followup_status, "completed")
            self.assertEqual(result.close_policy, "after_codex_followup_success_then_close")
            self.assertEqual(result.created_issue.number, 78)


class FetchNextPromptFollowupTests(unittest.TestCase):
    def test_fetch_executes_no_action_followup_then_close(self) -> None:
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
        raw = build_raw_reply(
            action="no_action",
            target_issue="#20",
            close_current_issue=True,
            create_followup_issue=True,
            followup_text="# Follow-up title\n\nBody paragraph.\n",
        )

        saved_states: list[dict[str, object]] = []
        close_called: list[str] = []
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
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": ""}),
                patch.object(fetch_next_prompt, "project_repo_path", return_value=REPO_ROOT),
                patch.object(
                    fetch_next_prompt,
                    "execute_followup_issue_action",
                    return_value=issue_centric_followup_issue.FollowupIssueExecutionResult(
                        status="completed",
                        followup_status="completed",
                        parent_issue=issue_centric_github.ResolvedGitHubIssue(
                            repository="example/repo",
                            issue_number=20,
                            issue_url="https://github.com/example/repo/issues/20",
                            source_ref="#20",
                        ),
                        draft=issue_centric_issue_create.IssueCreateDraft(
                            title="Follow-up title",
                            body="Body paragraph.\n",
                            title_line="# Follow-up title",
                            source_artifact_path="logs/prepared_followup_issue_body.md",
                        ),
                        created_issue=issue_centric_github.CreatedGitHubIssue(
                            number=74,
                            url="https://github.com/example/repo/issues/74",
                            title="Follow-up title",
                            repository="example/repo",
                            node_id="ISSUE_node_74",
                        ),
                        issue_create_execution_log_path=temp_root / "inner.json",
                        execution_log_path=temp_root / "followup.json",
                        project_url="",
                        project_sync_status="issue_only_fallback",
                        project_sync_note="Created without Project placement.",
                        project_item_id="",
                        project_state_field_name="",
                        project_state_value_name="",
                        close_policy="after_followup_success_only",
                        safe_stop_reason="Created follow-up issue #74.",
                    ),
                ),
                patch.object(
                    fetch_next_prompt,
                    "execute_close_current_issue",
                    side_effect=lambda *args, **kwargs: close_called.append("called") or issue_centric_close_current_issue.IssueCloseExecutionResult(
                        status="completed",
                        close_status="closed",
                        close_order="after_followup_issue_create",
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
                        execution_log_path=temp_root / "close.json",
                        safe_stop_reason="Closed current issue after follow-up success.",
                    ),
                ),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
            ):
                with self.assertRaisesRegex(BridgeStop, "no_action \\+ create_followup_issue"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(close_called, ["called"])
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_followup_status"], "completed")
            self.assertEqual(saved["last_issue_centric_created_issue_number"], "74")
            self.assertEqual(saved["last_issue_centric_close_status"], "closed")

    def test_fetch_stages_codex_followup_close_combo_for_later_dispatch(self) -> None:
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
        raw = build_raw_reply(
            action="codex_run",
            target_issue="#20",
            close_current_issue=True,
            create_followup_issue=True,
            codex_text="Implement the issue.\n",
            followup_text="# Follow-up title\n\nBody\n",
        )

        saved_states: list[dict[str, object]] = []
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
                patch.object(
                    fetch_next_prompt,
                    "load_project_config",
                    return_value={
                        "github_repository": "example/repo",
                        "github_project_url": "",
                        "worker_repo_path": ".",
                    },
                ),
                patch.object(
                    fetch_next_prompt,
                    "load_state",
                    return_value={
                        **state,
                        "mode": "codex_running",
                        "last_issue_centric_execution_status": "completed",
                        "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                        "last_issue_centric_target_issue": "#20",
                    },
                ),
                patch.object(
                    fetch_next_prompt,
                    "execute_codex_run_action",
                    return_value=issue_centric_codex_run.CodexRunExecutionResult(
                        status="completed",
                        resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                            repository="example/repo",
                            issue_number=20,
                            issue_url="https://github.com/example/repo/issues/20",
                            source_ref="#20",
                        ),
                        created_comment=issue_centric_github.CreatedGitHubComment(
                            comment_id=820,
                            url="https://github.com/example/repo/issues/20#issuecomment-820",
                            body="Implement the issue.\n",
                            repository="example/repo",
                            issue_number=20,
                        ),
                        payload=issue_centric_codex_run.CodexRunExecutionPayload(
                            repo="/tmp/repo",
                            target_issue="https://github.com/example/repo/issues/20",
                            request="Implement the issue.\n",
                            trigger_comment="https://github.com/example/repo/issues/20#issuecomment-820",
                        ),
                        payload_log_path=temp_root / "payload.json",
                        execution_log_path=temp_root / "codex.json",
                        launch_status="not_implemented",
                        launch_note="Not implemented.",
                        safe_stop_reason="codex_run completed through trigger comment creation.",
                    ),
                ) as codex_mock,
                patch.object(
                    fetch_next_prompt,
                    "launch_issue_centric_codex_run",
                    return_value=issue_centric_codex_launch.IssueCentricCodexLaunchResult(
                        status="completed",
                        launch_status="launched",
                        launch_entrypoint="launch_codex_once.run",
                        prompt_path=temp_root / "codex_prompt.md",
                        prompt_log_path=temp_root / "prompt-log.md",
                        launch_log_path=temp_root / "launch.json",
                        continuation_status="delegated_to_existing_codex_wait",
                        continuation_log_path=temp_root / "continuation.json",
                        report_status="waiting_for_report",
                        report_file="",
                        final_mode="codex_running",
                        safe_stop_reason="codex_run trigger comment and launch completed.",
                    ),
                ) as launch_mock,
                patch.object(
                    fetch_next_prompt,
                    "execute_followup_issue_action",
                    return_value=issue_centric_followup_issue.FollowupIssueExecutionResult(
                        status="completed",
                        followup_status="completed",
                        parent_issue=issue_centric_github.ResolvedGitHubIssue(
                            repository="example/repo",
                            issue_number=20,
                            issue_url="https://github.com/example/repo/issues/20",
                            source_ref="#20",
                        ),
                        draft=issue_centric_issue_create.IssueCreateDraft(
                            title="Follow-up title",
                            body="Body\n",
                            title_line="# Follow-up title",
                            source_artifact_path="logs/prepared_followup_issue_body.md",
                        ),
                        created_issue=issue_centric_github.CreatedGitHubIssue(
                            number=84,
                            url="https://github.com/example/repo/issues/84",
                            title="Follow-up title",
                            repository="example/repo",
                            node_id="ISSUE_node_84",
                        ),
                        issue_create_execution_log_path=temp_root / "followup-inner.json",
                        execution_log_path=temp_root / "followup.json",
                        project_url="",
                        project_sync_status="issue_only_fallback",
                        project_sync_note="No project configured.",
                        project_item_id="",
                        project_state_field_name="",
                        project_state_value_name="",
                        close_policy="after_codex_followup_success_then_close",
                        safe_stop_reason="Created follow-up issue after codex continuation.",
                    ),
                ) as followup_mock,
                patch.object(
                    fetch_next_prompt,
                    "execute_close_current_issue",
                    return_value=issue_centric_close_current_issue.IssueCloseExecutionResult(
                        status="completed",
                        close_status="closed",
                        close_order="after_codex_run_followup",
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
                        execution_log_path=temp_root / "close.json",
                        safe_stop_reason="Closed current issue after codex continuation and follow-up.",
                    ),
                ) as close_mock,
            ):
                with self.assertRaisesRegex(
                    BridgeStop,
                    "prepared Codex body は次の bridge 手で codex_run dispatch に渡します",
                ):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(close_mock.call_count, 0)
            self.assertEqual(codex_mock.call_count, 0)
            self.assertEqual(launch_mock.call_count, 0)
            self.assertEqual(followup_mock.call_count, 0)
            saved = saved_states[-1]
            self.assertEqual(saved["chatgpt_decision"], "issue_centric:codex_run")
            self.assertEqual(saved["last_issue_centric_artifact_kind"], "codex_body")
            self.assertTrue(saved["last_issue_centric_metadata_log"])
            self.assertTrue(saved["last_issue_centric_artifact_file"])
            self.assertEqual(saved["last_issue_centric_launch_status"], "")
            self.assertEqual(saved["last_issue_centric_followup_issue_number"], "")
            self.assertEqual(saved["last_issue_centric_close_order"], "")

    def test_fetch_does_not_close_when_followup_is_blocked(self) -> None:
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
        raw = build_raw_reply(
            action="no_action",
            target_issue="#20",
            close_current_issue=True,
            create_followup_issue=True,
            followup_text="# Follow-up title\n\nBody paragraph.\n",
        )

        saved_states: list[dict[str, object]] = []
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
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": ""}),
                patch.object(fetch_next_prompt, "project_repo_path", return_value=REPO_ROOT),
                patch.object(
                    fetch_next_prompt,
                    "execute_followup_issue_action",
                    return_value=issue_centric_followup_issue.FollowupIssueExecutionResult(
                        status="blocked",
                        followup_status="blocked_project_preflight",
                        parent_issue=issue_centric_github.ResolvedGitHubIssue(
                            repository="example/repo",
                            issue_number=20,
                            issue_url="https://github.com/example/repo/issues/20",
                            source_ref="#20",
                        ),
                        draft=None,
                        created_issue=None,
                        issue_create_execution_log_path=None,
                        execution_log_path=temp_root / "followup-blocked.json",
                        project_url="https://github.com/users/example/projects/1",
                        project_sync_status="blocked_project_preflight",
                        project_sync_note="Project config is incomplete.",
                        project_item_id="",
                        project_state_field_name="",
                        project_state_value_name="",
                        close_policy="after_followup_success_only",
                        safe_stop_reason="Follow-up issue creation was blocked before mutation.",
                    ),
                ),
                patch.object(fetch_next_prompt, "execute_close_current_issue", side_effect=AssertionError("close should not run")),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
            ):
                with self.assertRaisesRegex(BridgeStop, "no_action \\+ create_followup_issue"):
                    fetch_next_prompt.run(dict(state), [])

            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_followup_status"], "blocked_project_preflight")
            self.assertEqual(saved["last_issue_centric_close_status"], "not_attempted_followup_blocked")


class FollowupIssueProjectSyncSignalTests(unittest.TestCase):
    """Tests for lifecycle sync signal surfacing in follow-up action human-facing text (issue #65)."""

    def _base_prepared(
        self, *, target_issue: str = "#20", followup_text: str = "# Follow-up title\n\nBody paragraph.\n"
    ) -> issue_centric_transport.PreparedIssueCentricDecision:
        return issue_centric_transport.decode_issue_centric_decision(
            build_decision(target_issue=target_issue, followup_text=followup_text)
        )

    def test_followup_safe_stop_reason_contains_synced_when_project_synced(self) -> None:
        prepared = self._base_prepared()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "planned",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=lambda repo, title, body, token: issue_centric_github.CreatedGitHubIssue(
                    number=111, url="https://github.com/example/repo/issues/111",
                    title=title, repository=repo, node_id="NODE_111",
                ),
                project_state_resolver=lambda url, state_field_name, state_option_name, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_p", project_url=url, project_title="Backlog",
                    owner_login="example", owner_kind="users",
                    state_field_id="FIELD_s", state_field_name=state_field_name,
                    state_option_id="OPT_r", state_option_name=state_option_name,
                ),
                project_item_creator=lambda pid, nid, token: issue_centric_github.CreatedGitHubProjectItem(
                    item_id="ITEM_111", project_id=pid
                ),
                project_state_setter=lambda pid, iid, fid, oid, token: None,
                env={"GITHUB_TOKEN": "token-x"},
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.project_sync_status, "project_state_synced")
        self.assertIn("[project_sync: signal=synced]", result.safe_stop_reason)

    def test_followup_safe_stop_reason_contains_skipped_no_project_when_no_project(self) -> None:
        prepared = self._base_prepared()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=lambda repo, title, body, token: issue_centric_github.CreatedGitHubIssue(
                    number=112, url="https://github.com/example/repo/issues/112",
                    title=title, repository=repo, node_id="NODE_112",
                ),
                env={"GITHUB_TOKEN": "token-x"},
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.project_sync_status, "not_requested_no_project")
        self.assertIn("[project_sync: signal=skipped_no_project]", result.safe_stop_reason)

    def test_followup_safe_stop_reason_contains_sync_failed_when_project_item_fails(self) -> None:
        prepared = self._base_prepared()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_default_issue_state": "planned",
                },
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_creator=lambda repo, title, body, token: issue_centric_github.CreatedGitHubIssue(
                    number=113, url="https://github.com/example/repo/issues/113",
                    title=title, repository=repo, node_id="NODE_113",
                ),
                project_state_resolver=lambda url, state_field_name, state_option_name, token: issue_centric_github.ResolvedGitHubProjectState(
                    project_id="PVT_p", project_url=url, project_title="Backlog",
                    owner_login="example", owner_kind="users",
                    state_field_id="FIELD_s", state_field_name=state_field_name,
                    state_option_id="OPT_r", state_option_name=state_option_name,
                ),
                project_item_creator=lambda pid, nid, token: (_ for _ in ()).throw(
                    issue_centric_github.IssueCentricGitHubError("item create failed")
                ),
                env={"GITHUB_TOKEN": "token-x"},
            )

        # status is blocked (project item creation failed), inner safe_stop_reason has sync_failed
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.project_sync_status, "issue_created_project_item_failed")
        # inner issue_create result safe_stop_reason is embedded in the followup safe_stop_reason
        self.assertIn("sync_failed", result.safe_stop_reason)

    def test_followup_no_sync_signal_regression_for_blocked_before_create(self) -> None:
        """Regression: blocked-before-create followup should not have a spurious sync signal."""
        prepared = issue_centric_transport.PreparedIssueCentricDecision(
            decision=build_decision(
                target_issue="#20",
                followup_text="# Follow-up title\n\nBody paragraph.\n",
            ),
            issue_body=None,
            codex_body=None,
            review_body=None,
            followup_issue_body=None,  # missing decoded body
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_followup_issue.execute_followup_issue_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_followup_issue_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                env={"GITHUB_TOKEN": "token-x"},
            )

        self.assertEqual(result.status, "blocked")
        # No issue was created, no project sync occurred — safe_stop_reason should not contain sync suffix
        self.assertNotIn("[project_sync:", result.safe_stop_reason)


if __name__ == "__main__":
    unittest.main()
