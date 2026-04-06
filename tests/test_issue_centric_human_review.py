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
import issue_centric_github  # noqa: E402
import issue_centric_human_review  # noqa: E402
import issue_centric_transport  # noqa: E402
from _bridge_common import BridgeStop  # noqa: E402


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build_decision(
    *,
    target_issue: str | None,
    review_text: str | None,
    close_current_issue: bool = False,
) -> issue_centric_contract.IssueCentricDecision:
    return issue_centric_contract.IssueCentricDecision(
        action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
        target_issue=target_issue,
        close_current_issue=close_current_issue,
        create_followup_issue=False,
        summary="Post review guidance.",
        issue_body_base64=None,
        codex_body_base64=None,
        review_base64=(b64(review_text) if review_text is not None else None),
        raw_json="{}",
        raw_segment="segment",
    )


def build_reply(
    *,
    target_issue: str | None,
    review_text: str | None,
    close_current_issue: bool = False,
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
    parts.extend(
        [
            issue_centric_contract.DECISION_JSON_START,
            json.dumps(
                {
                    "action": "human_review_needed",
                    "target_issue": target_issue if target_issue is not None else "none",
                    "close_current_issue": close_current_issue,
                    "create_followup_issue": False,
                    "summary": "Post review guidance.",
                },
                ensure_ascii=True,
            ),
            issue_centric_contract.DECISION_JSON_END,
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
    ) -> issue_centric_transport.PreparedIssueCentricDecision:
        return issue_centric_transport.decode_issue_centric_decision(
            build_decision(
                target_issue=target_issue,
                review_text=review_text,
                close_current_issue=close_current_issue,
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
                patch.object(fetch_next_prompt, "wait_for_prompt_reply_text", return_value=raw),
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

    def test_fetch_next_prompt_records_review_then_close_block_policy(self) -> None:
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
            close_policy="review_then_close_unimplemented",
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
                patch.object(fetch_next_prompt, "wait_for_prompt_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_human_review_action", return_value=fake_review),
                patch.object(fetch_next_prompt, "execute_close_current_issue") as close_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "close_current_issue は review 後にのみ検討し、この slice では実行していません"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(close_mock.call_count, 0)
            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_review_status"], "completed")
            self.assertEqual(saved["last_issue_centric_close_status"], "blocked_review_then_close_unimplemented")
            self.assertEqual(saved["last_issue_centric_close_order"], "after_review_blocked")


if __name__ == "__main__":
    unittest.main()
