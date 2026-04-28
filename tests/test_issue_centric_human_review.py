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
import issue_centric_contract  # noqa: E402
import issue_centric_followup_issue  # noqa: E402
import issue_centric_github  # noqa: E402
import issue_centric_human_review  # noqa: E402
import issue_centric_issue_create  # noqa: E402
import issue_centric_transport  # noqa: E402
from _bridge_common import BridgeStop  # noqa: E402


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build_decision(
    *,
    target_issue: str | None,
    review_text: str | None,
    close_current_issue: bool = False,
    create_followup_issue: bool = False,
    followup_text: str | None = None,
) -> issue_centric_contract.IssueCentricDecision:
    return issue_centric_contract.IssueCentricDecision(
        action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
        target_issue=target_issue,
        close_current_issue=close_current_issue,
        create_followup_issue=create_followup_issue,
        summary="Post review guidance.",
        issue_body_base64=None,
        codex_body_base64=None,
        review_base64=(b64(review_text) if review_text is not None else None),
        followup_issue_body_base64=(b64(followup_text) if followup_text is not None else None),
        raw_json="{}",
        raw_segment="segment",
    )


def build_reply(
    *,
    target_issue: str | None,
    review_text: str | None,
    close_current_issue: bool = False,
    create_followup_issue: bool = False,
    followup_text: str | None = None,
) -> str:
    parts = [
        "あなた:",
        "request body",
        "ChatGPT:",
    ]
    if review_text is not None:
        parts.extend(
            [
                issue_centric_contract.REVIEW_BODY_START,
                b64(review_text),
                issue_centric_contract.REVIEW_BODY_END,
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
    parts.extend(
        [
            issue_centric_contract.DECISION_JSON_START,
            json.dumps(
                {
                    "action": "human_review_needed",
                    "target_issue": target_issue if target_issue is not None else "none",
                    "close_current_issue": close_current_issue,
                    "create_followup_issue": create_followup_issue,
                    "summary": "Post review guidance.",
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


class HumanReviewExecutionTests(unittest.TestCase):
    def prepared(
        self,
        *,
        target_issue: str | None,
        review_text: str | None,
        close_current_issue: bool = False,
        create_followup_issue: bool = False,
        followup_text: str | None = None,
    ) -> issue_centric_transport.PreparedIssueCentricDecision:
        return issue_centric_transport.decode_issue_centric_decision(
            build_decision(
                target_issue=target_issue,
                review_text=review_text,
                close_current_issue=close_current_issue,
                create_followup_issue=create_followup_issue,
                followup_text=followup_text,
            )
        )

    def test_executes_review_comment_on_target_issue(self) -> None:
        prepared = self.prepared(target_issue="#20", review_text="## Review\n\n- Looks good\n")
        comment_calls: list[tuple[str, int, str, str]] = []

        def fake_comment_creator(repository: str, issue_number: int, body: str, token: str) -> issue_centric_github.CreatedGitHubComment:
            comment_calls.append((repository, issue_number, body, token))
            return issue_centric_github.CreatedGitHubComment(
                comment_id=801,
                url="https://github.com/example/repo/issues/20#issuecomment-801",
                body=body,
                repository=repository,
                issue_number=issue_number,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_human_review.execute_human_review_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_review.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Ready issue",
                    repository=repository,
                    state="open",
                ),
                comment_creator=fake_comment_creator,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.review_status, "completed")
            self.assertEqual(result.created_comment.comment_id, 801)
            self.assertEqual(
                comment_calls[0],
                ("example/repo", 20, "## Review\n\n- Looks good\n", "token-123"),
            )
            execution = json.loads(result.execution_log_path.read_text(encoding="utf-8"))
            self.assertEqual(execution["review_comment"]["id"], 801)
            self.assertEqual(execution["resolved_issue"]["number"], 20)

    def test_missing_review_body_blocks_before_mutation(self) -> None:
        prepared = self.prepared(target_issue="#20", review_text=None)
        called = False

        def fake_comment_creator(repository: str, issue_number: int, body: str, token: str) -> issue_centric_github.CreatedGitHubComment:
            nonlocal called
            called = True
            raise AssertionError("should not be called")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_human_review.execute_human_review_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                comment_creator=fake_comment_creator,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertFalse(called)
            self.assertIn("requires CHATGPT_REVIEW", result.safe_stop_reason)

    def test_target_mismatch_blocks_before_comment(self) -> None:
        prepared = self.prepared(target_issue="#20", review_text="Review\n")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_human_review.execute_human_review_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/21"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/review.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertIn("does not match", result.safe_stop_reason)

    def test_closed_issue_blocks_review_comment(self) -> None:
        prepared = self.prepared(target_issue="#20", review_text="Review\n")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_human_review.execute_human_review_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/review.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Ready issue",
                    repository=repository,
                    state="closed",
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertIn("already closed", result.safe_stop_reason)

    def test_comment_mutation_failure_is_recorded(self) -> None:
        prepared = self.prepared(target_issue="#20", review_text="Review\n")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_human_review.execute_human_review_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/review.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Ready issue",
                    repository=repository,
                    state="open",
                ),
                comment_creator=lambda repository, issue_number, body, token: (_ for _ in ()).throw(
                    issue_centric_github.IssueCentricGitHubError("comment failed")
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertIn("comment failed", result.safe_stop_reason)

    def test_create_followup_flag_blocks_human_review_slice(self) -> None:
        prepared = issue_centric_transport.PreparedIssueCentricDecision(
            decision=issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                target_issue="#20",
                close_current_issue=False,
                create_followup_issue=True,
                summary="Blocked combo.",
                issue_body_base64=None,
                codex_body_base64=None,
                review_base64=b64("Review\n"),
                followup_issue_body_base64=None,
                raw_json="{}",
                raw_segment="segment",
            ),
            issue_body=None,
            codex_body=None,
            review_body=issue_centric_transport.IssueCentricDecodedBody(
                kind=issue_centric_transport.IssueCentricArtifactKind.REVIEW,
                block_name="CHATGPT_REVIEW",
                raw_base64=b64("Review\n"),
                normalized_base64=b64("Review\n"),
                decoded_text="Review\n",
            ),
            followup_issue_body=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_human_review.execute_human_review_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/review.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertIn("create_followup_issue", result.safe_stop_reason)

    def test_human_review_can_run_when_followup_combo_is_explicitly_enabled(self) -> None:
        prepared = self.prepared(
            target_issue="#20",
            review_text="## Review\n\n- Split follow-up\n",
            create_followup_issue=True,
            followup_text="# Follow-up\n\nBody\n",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_human_review.execute_human_review_action(
                prepared,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/review.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                allow_followup_combo=True,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=20,
                    url="https://github.com/example/repo/issues/20",
                    title="Ready issue",
                    repository=repository,
                    state="open",
                ),
                comment_creator=lambda repository, issue_number, body, token: issue_centric_github.CreatedGitHubComment(
                    comment_id=901,
                    url="https://github.com/example/repo/issues/20#issuecomment-901",
                    body=body,
                    repository=repository,
                    issue_number=issue_number,
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.close_policy, "after_review_followup_if_review_succeeds")
            self.assertEqual(result.created_comment.comment_id, 901)


class FetchNextPromptHumanReviewIntegrationTests(unittest.TestCase):
    def test_fetch_next_prompt_executes_human_review_and_records_comment(self) -> None:
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
        raw = build_reply(target_issue="#20", review_text="## Review\n\n- OK\n")
        saved_states: list[dict[str, object]] = []

        fake_review = issue_centric_human_review.HumanReviewExecutionResult(
            status="completed",
            review_status="completed",
            close_policy="review_only",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            issue_before=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Ready issue",
                repository="example/repo",
                state="open",
            ),
            created_comment=issue_centric_github.CreatedGitHubComment(
                comment_id=801,
                url="https://github.com/example/repo/issues/20#issuecomment-801",
                body="## Review\n\n- OK\n",
                repository="example/repo",
                issue_number=20,
            ),
            execution_log_path=REPO_ROOT / "logs" / "review.json",
            safe_stop_reason="human_review_needed posted a review comment.",
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
                patch.object(fetch_next_prompt, "execute_human_review_action", return_value=fake_review) as review_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "human_review_needed の最小 review comment mutation まで実行しました"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(review_mock.call_count, 1)
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_review_status"], "completed")
            self.assertEqual(saved["last_issue_centric_review_comment_id"], "801")
            self.assertEqual(saved["last_issue_centric_review_comment_url"], "https://github.com/example/repo/issues/20#issuecomment-801")
            self.assertEqual(saved["last_issue_centric_resolved_issue"], "https://github.com/example/repo/issues/20")

    def test_fetch_next_prompt_executes_post_review_close_when_enabled(self) -> None:
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
        raw = build_reply(target_issue="#20", review_text="## Review\n\n- OK\n", close_current_issue=True)
        saved_states: list[dict[str, object]] = []

        fake_review = issue_centric_human_review.HumanReviewExecutionResult(
            status="completed",
            review_status="completed",
            close_policy="after_review_close_if_review_succeeds",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            issue_before=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Ready issue",
                repository="example/repo",
                state="open",
            ),
            created_comment=issue_centric_github.CreatedGitHubComment(
                comment_id=801,
                url="https://github.com/example/repo/issues/20#issuecomment-801",
                body="## Review\n\n- OK\n",
                repository="example/repo",
                issue_number=20,
            ),
            execution_log_path=REPO_ROOT / "logs" / "review.json",
            safe_stop_reason="human_review_needed posted a review comment.",
        )
        fake_close = issue_centric_close_current_issue.IssueCloseExecutionResult(
            status="completed",
            close_status="closed",
            close_order="after_human_review",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            issue_before=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Ready issue",
                repository="example/repo",
                state="open",
            ),
            issue_after=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Ready issue",
                repository="example/repo",
                state="closed",
            ),
            execution_log_path=REPO_ROOT / "logs" / "close.json",
            safe_stop_reason="close_current_issue closed issue #20 after the review comment was posted.",
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
                patch.object(fetch_next_prompt, "execute_human_review_action", return_value=fake_review),
                patch.object(fetch_next_prompt, "execute_close_current_issue", return_value=fake_close) as close_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "review comment mutation と narrow post-review close まで実行しました"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(close_mock.call_count, 1)
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_review_status"], "completed")
            self.assertEqual(saved["last_issue_centric_close_status"], "closed")
            self.assertEqual(saved["last_issue_centric_close_order"], "after_human_review")
            self.assertEqual(saved["last_issue_centric_closed_issue_number"], "20")

    def test_fetch_next_prompt_keeps_review_success_when_post_review_close_blocks(self) -> None:
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
        raw = build_reply(target_issue="#20", review_text="## Review\n\n- OK\n", close_current_issue=True)
        saved_states: list[dict[str, object]] = []

        fake_review = issue_centric_human_review.HumanReviewExecutionResult(
            status="completed",
            review_status="completed",
            close_policy="after_review_close_if_review_succeeds",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            issue_before=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Ready issue",
                repository="example/repo",
                state="open",
            ),
            created_comment=issue_centric_github.CreatedGitHubComment(
                comment_id=801,
                url="https://github.com/example/repo/issues/20#issuecomment-801",
                body="## Review\n\n- OK\n",
                repository="example/repo",
                issue_number=20,
            ),
            execution_log_path=REPO_ROOT / "logs" / "review.json",
            safe_stop_reason="human_review_needed posted a review comment.",
        )
        fake_close = issue_centric_close_current_issue.IssueCloseExecutionResult(
            status="blocked",
            close_status="blocked",
            close_order="after_human_review",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            issue_before=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Ready issue",
                repository="example/repo",
                state="open",
            ),
            issue_after=None,
            execution_log_path=REPO_ROOT / "logs" / "close_blocked.json",
            safe_stop_reason="close_current_issue stopped before mutation completed. target mismatch",
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
                patch.object(fetch_next_prompt, "execute_human_review_action", return_value=fake_review),
                patch.object(fetch_next_prompt, "execute_close_current_issue", return_value=fake_close),
            ):
                with self.assertRaisesRegex(BridgeStop, "review comment mutation と narrow post-review close まで実行しました"):
                    fetch_next_prompt.run(dict(state), [])

            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_review_status"], "completed")
            self.assertEqual(saved["last_issue_centric_close_status"], "blocked")
            self.assertEqual(saved["last_issue_centric_close_order"], "after_human_review")

    def test_fetch_next_prompt_executes_review_followup_then_close_combo(self) -> None:
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
        raw = build_reply(
            target_issue="#20",
            review_text="## Review\n\n- OK\n",
            close_current_issue=True,
            create_followup_issue=True,
            followup_text="# Follow-up\n\nBody\n",
        )
        saved_states: list[dict[str, object]] = []

        fake_review = issue_centric_human_review.HumanReviewExecutionResult(
            status="completed",
            review_status="completed",
            close_policy="after_review_followup_then_close_if_followup_succeeds",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            issue_before=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Ready issue",
                repository="example/repo",
                state="open",
            ),
            created_comment=issue_centric_github.CreatedGitHubComment(
                comment_id=1001,
                url="https://github.com/example/repo/issues/20#issuecomment-1001",
                body="## Review\n\n- OK\n",
                repository="example/repo",
                issue_number=20,
            ),
            execution_log_path=REPO_ROOT / "logs" / "review.json",
            safe_stop_reason="review posted",
        )
        fake_followup = issue_centric_github.CreatedGitHubIssue(
            number=74,
            url="https://github.com/example/repo/issues/74",
            title="Follow-up",
            repository="example/repo",
            node_id="ISSUE_node_74",
        )
        fake_followup_result = issue_centric_followup_issue.FollowupIssueExecutionResult(
            status="completed",
            followup_status="completed",
            parent_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            draft=issue_centric_issue_create.IssueCreateDraft(
                title="Follow-up",
                body="Body\n",
                title_line="# Follow-up",
                source_artifact_path="logs/followup.md",
            ),
            created_issue=fake_followup,
            issue_create_execution_log_path=REPO_ROOT / "logs" / "followup_inner.json",
            execution_log_path=REPO_ROOT / "logs" / "followup.json",
            project_url="",
            project_sync_status="issue_only_fallback",
            project_sync_note="No project configured.",
            project_item_id="",
            project_state_field_name="",
            project_state_value_name="",
            close_policy="after_review_followup_success_then_close",
            safe_stop_reason="follow-up created",
        )
        fake_close = issue_centric_close_current_issue.IssueCloseExecutionResult(
            status="completed",
            close_status="closed",
            close_order="after_human_review_followup",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            issue_before=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Ready issue",
                repository="example/repo",
                state="open",
            ),
            issue_after=issue_centric_github.GitHubIssueSnapshot(
                number=20,
                url="https://github.com/example/repo/issues/20",
                title="Ready issue",
                repository="example/repo",
                state="closed",
            ),
            execution_log_path=REPO_ROOT / "logs" / "close.json",
            safe_stop_reason="closed after review and follow-up",
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
                patch.object(fetch_next_prompt, "execute_human_review_action", return_value=fake_review) as review_mock,
                patch.object(fetch_next_prompt, "execute_followup_issue_action", return_value=fake_followup_result) as followup_mock,
                patch.object(fetch_next_prompt, "execute_close_current_issue", return_value=fake_close) as close_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "review comment mutation / narrow follow-up issue create / narrow post-review close"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(review_mock.call_count, 1)
            self.assertEqual(followup_mock.call_count, 1)
            self.assertEqual(close_mock.call_count, 1)
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_review_status"], "completed")
            self.assertEqual(saved["last_issue_centric_followup_status"], "completed")
            self.assertEqual(saved["last_issue_centric_close_status"], "closed")
            self.assertEqual(saved["last_issue_centric_close_order"], "after_human_review_followup")


class GitHubFacingReviewCommentLifecycleSyncSurfacingTests(unittest.TestCase):
    """Phase 1B (#63): lifecycle sync outcomes are visible in review comment body and safe_stop_reason.

    Covers execute_human_review_action() – review_text passed to comment_creator and
    safe_stop_reason on the returned HumanReviewExecutionResult.
    """

    def _run_review(
        self, prior_state: dict
    ) -> "tuple[issue_centric_human_review.HumanReviewExecutionResult, list[str]]":
        prepared = issue_centric_transport.decode_issue_centric_decision(
            build_decision(target_issue="#20", review_text="## Review\n\n- Looks good\n")
        )
        captured_bodies: list[str] = []

        def fake_comment_creator(
            repository: str, issue_number: int, body: str, token: str
        ) -> issue_centric_github.CreatedGitHubComment:
            captured_bodies.append(body)
            return issue_centric_github.CreatedGitHubComment(
                comment_id=801,
                url="https://github.com/example/repo/issues/20#issuecomment-801",
                body=body,
                repository=repository,
                issue_number=issue_number,
            )

        full_state = dict(
            prior_state,
            last_issue_centric_resolved_issue="https://github.com/example/repo/issues/20",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_human_review.execute_human_review_action(
                prepared,
                prior_state=full_state,
                project_config={"github_repository": "example/repo", "github_project_url": ""},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_review.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                issue_fetcher=lambda repository, issue_number, token: issue_centric_github.GitHubIssueSnapshot(
                    number=issue_number,
                    url=f"https://github.com/{repository}/issues/{issue_number}",
                    title="Ready issue",
                    repository=repository,
                    state="open",
                ),
                comment_creator=fake_comment_creator,
                env={"GITHUB_TOKEN": "token-123"},
            )
        return result, captured_bodies

    # --- review comment body ---

    def test_review_comment_body_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        result, bodies = self._run_review(state)
        self.assertEqual(result.status, "completed")
        self.assertGreater(len(bodies), 0)
        self.assertIn("lifecycle_sync", bodies[0])
        self.assertIn("signal=synced", bodies[0])
        self.assertIn("stage=done", bodies[0])

    def test_review_comment_body_shows_lifecycle_sync_skipped_no_project(self) -> None:
        state = {
            "last_issue_centric_lifecycle_sync_status": "not_requested_no_project",
        }
        result, bodies = self._run_review(state)
        self.assertEqual(result.status, "completed")
        self.assertGreater(len(bodies), 0)
        self.assertIn("lifecycle_sync", bodies[0])
        self.assertIn("signal=skipped_no_project", bodies[0])

    def test_review_comment_body_shows_lifecycle_sync_failed(self) -> None:
        state = {
            "last_issue_centric_lifecycle_sync_status": "blocked_project_preflight",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        result, bodies = self._run_review(state)
        self.assertEqual(result.status, "completed")
        self.assertGreater(len(bodies), 0)
        self.assertIn("lifecycle_sync", bodies[0])
        self.assertIn("signal=sync_failed", bodies[0])
        self.assertIn("reason=blocked_project_preflight", bodies[0])

    def test_review_comment_body_no_lifecycle_sync_when_no_sync_data(self) -> None:
        state: dict = {}
        result, bodies = self._run_review(state)
        self.assertEqual(result.status, "completed")
        self.assertGreater(len(bodies), 0)
        self.assertNotIn("lifecycle_sync", bodies[0])

    # --- safe_stop_reason ---

    def test_safe_stop_reason_shows_lifecycle_sync_synced(self) -> None:
        state = {
            "last_issue_centric_lifecycle_sync_status": "project_state_synced",
            "last_issue_centric_lifecycle_sync_stage": "done",
        }
        result, _ = self._run_review(state)
        self.assertEqual(result.status, "completed")
        self.assertIn("lifecycle_sync", result.safe_stop_reason)
        self.assertIn("signal=synced", result.safe_stop_reason)

    def test_safe_stop_reason_no_lifecycle_sync_when_no_sync_data(self) -> None:
        state: dict = {}
        result, _ = self._run_review(state)
        self.assertEqual(result.status, "completed")
        self.assertNotIn("lifecycle_sync", result.safe_stop_reason)


if __name__ == "__main__":
    unittest.main()
