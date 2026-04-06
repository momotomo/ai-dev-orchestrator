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
import issue_centric_codex_launch  # noqa: E402
import issue_centric_codex_run  # noqa: E402
import issue_centric_contract  # noqa: E402
import issue_centric_github  # noqa: E402
import issue_centric_transport  # noqa: E402
from _bridge_common import BridgeError, BridgeStop  # noqa: E402


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build_codex_decision(target_issue: str, body_text: str) -> issue_centric_contract.IssueCentricDecision:
    return issue_centric_contract.IssueCentricDecision(
        action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
        target_issue=target_issue,
        close_current_issue=False,
        create_followup_issue=False,
        summary="Run Codex for the target issue.",
        issue_body_base64=None,
        codex_body_base64=b64(body_text),
        review_base64=None,
        raw_json="{}",
        raw_segment="segment",
    )


def build_codex_run_reply(target_issue: str, body_text: str) -> str:
    return "\n".join(
        [
            "あなた:",
            "request body",
            "ChatGPT:",
            issue_centric_contract.CODEX_BODY_START,
            b64(body_text),
            issue_centric_contract.CODEX_BODY_END,
            issue_centric_contract.DECISION_JSON_START,
            json.dumps(
                {
                    "action": "codex_run",
                    "target_issue": target_issue,
                    "close_current_issue": False,
                    "create_followup_issue": False,
                    "summary": "Run Codex for the existing issue.",
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


class CodexRunExecutionTests(unittest.TestCase):
    def prepared(self, target_issue: str, body_text: str) -> issue_centric_transport.PreparedIssueCentricDecision:
        return issue_centric_transport.decode_issue_centric_decision(
            build_codex_decision(target_issue, body_text)
        )

    def test_resolves_issue_number_and_creates_trigger_comment(self) -> None:
        prepared = self.prepared("#20", "Run this body.\n")
        calls: list[tuple[str, int, str, str]] = []

        def fake_comment_creator(repository: str, issue_number: int, body: str, token: str) -> issue_centric_github.CreatedGitHubComment:
            calls.append((repository, issue_number, body, token))
            return issue_centric_github.CreatedGitHubComment(
                comment_id=701,
                url="https://github.com/example/repo/issues/20#issuecomment-701",
                body=body,
                repository=repository,
                issue_number=issue_number,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_codex_run.execute_codex_run_action(
                prepared,
                project_config={"github_repository": "example/repo"},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_codex_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                comment_creator=fake_comment_creator,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.resolved_issue.issue_url, "https://github.com/example/repo/issues/20")
            self.assertEqual(result.created_comment.comment_id, 701)
            self.assertEqual(result.launch_status, "not_implemented")
            self.assertEqual(
                calls[0],
                ("example/repo", 20, "Run this body.\n", "token-123"),
            )
            payload = json.loads(result.payload_log_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["target_issue"], "https://github.com/example/repo/issues/20")
            self.assertEqual(
                payload["trigger_comment"],
                "https://github.com/example/repo/issues/20#issuecomment-701",
            )

    def test_resolves_full_issue_url(self) -> None:
        prepared = self.prepared("https://github.com/example/repo/issues/33", "Run this body.\n")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_codex_run.execute_codex_run_action(
                prepared,
                project_config={"github_repository": "ignored/repo"},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_codex_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                comment_creator=lambda repository, issue_number, body, token: issue_centric_github.CreatedGitHubComment(
                    comment_id=99,
                    url="https://github.com/example/repo/issues/33#issuecomment-99",
                    body=body,
                    repository=repository,
                    issue_number=issue_number,
                ),
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.resolved_issue.repository, "example/repo")
            self.assertEqual(result.resolved_issue.issue_number, 33)

    def test_non_codex_run_action_does_not_enter_execution(self) -> None:
        prepared = issue_centric_transport.PreparedIssueCentricDecision(
            decision=issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                target_issue=None,
                close_current_issue=False,
                create_followup_issue=False,
                summary="No action.",
                issue_body_base64=None,
                codex_body_base64=None,
                review_base64=None,
                raw_json="{}",
                raw_segment="segment",
            ),
            issue_body=None,
            codex_body=None,
            review_body=None,
        )
        with self.assertRaisesRegex(
            issue_centric_codex_run.IssueCentricCodexRunError,
            "action=codex_run",
        ):
            issue_centric_codex_run.execute_codex_run_action(
                prepared,
                project_config={"github_repository": "example/repo"},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_codex_body.md",
                log_writer=TempLogWriter(REPO_ROOT / "logs"),
                repo_relative=lambda path: path.name,
            )

    def test_invalid_target_issue_stops_before_mutation(self) -> None:
        prepared = self.prepared("not-an-issue", "Run this body.\n")
        called = False

        def fake_comment_creator(repository: str, issue_number: int, body: str, token: str) -> issue_centric_github.CreatedGitHubComment:
            nonlocal called
            called = True
            raise AssertionError("should not be called")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_codex_run.execute_codex_run_action(
                prepared,
                project_config={"github_repository": "example/repo"},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_codex_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                comment_creator=fake_comment_creator,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertFalse(called)
            self.assertIn("unsupported", result.safe_stop_reason)

    def test_mutation_failure_is_recorded_as_blocked(self) -> None:
        prepared = self.prepared("#20", "Run this body.\n")

        def fake_comment_creator(repository: str, issue_number: int, body: str, token: str) -> issue_centric_github.CreatedGitHubComment:
            raise issue_centric_github.IssueCentricGitHubError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = issue_centric_codex_run.execute_codex_run_action(
                prepared,
                project_config={"github_repository": "example/repo"},
                repo_path=REPO_ROOT,
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/prepared_codex_body.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
                comment_creator=fake_comment_creator,
                env={"GITHUB_TOKEN": "token-123"},
            )

            self.assertEqual(result.status, "blocked")
            self.assertIsNone(result.created_comment)
            self.assertIn("boom", result.safe_stop_reason)


class CodexRunLaunchTests(unittest.TestCase):
    def prepared(self, target_issue: str, body_text: str) -> issue_centric_transport.PreparedIssueCentricDecision:
        return issue_centric_transport.decode_issue_centric_decision(
            build_codex_decision(target_issue, body_text)
        )

    def execution_result(self, *, target_issue: str = "https://github.com/example/repo/issues/20") -> issue_centric_codex_run.CodexRunExecutionResult:
        return issue_centric_codex_run.CodexRunExecutionResult(
            status="completed",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url=target_issue,
                source_ref="#20",
            ),
            created_comment=issue_centric_github.CreatedGitHubComment(
                comment_id=701,
                url="https://github.com/example/repo/issues/20#issuecomment-701",
                body="Run this body.\n",
                repository="example/repo",
                issue_number=20,
            ),
            payload=issue_centric_codex_run.CodexRunExecutionPayload(
                repo=str(REPO_ROOT),
                target_issue=target_issue,
                request="Run this body.\n",
                trigger_comment="https://github.com/example/repo/issues/20#issuecomment-701",
            ),
            payload_log_path=REPO_ROOT / "logs" / "payload.json",
            execution_log_path=REPO_ROOT / "logs" / "execution.json",
            launch_status="not_implemented",
            launch_note="Not implemented.",
            safe_stop_reason="codex_run completed through trigger comment creation.",
        )

    def test_builds_prompt_and_launches_via_existing_entrypoint(self) -> None:
        prepared = self.prepared("#20", "Run this body.\n")
        execution = self.execution_result()
        saved_states: list[dict[str, object]] = []

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            prompt_path = temp_root / "codex_prompt.md"

            def fake_save_state(state: dict[str, object]) -> None:
                saved_states.append(dict(state))

            def fake_load_state() -> dict[str, object]:
                if saved_states:
                    latest = dict(saved_states[-1])
                else:
                    latest = {}
                latest.update({"mode": "codex_done", "need_codex_run": False})
                return latest

            launch_calls: list[tuple[str, str]] = []

            def fake_launch_runner(state: dict[str, object], argv: list[str] | None) -> int:
                launch_calls.append((str(state.get("mode", "")), str(state.get("last_prompt_file", ""))))
                self.assertEqual(argv, [])
                self.assertEqual(state["mode"], "ready_for_codex")
                self.assertTrue(state["need_codex_run"])
                return 0

            result = issue_centric_codex_launch.launch_issue_centric_codex_run(
                prepared,
                execution,
                state={
                    "mode": "awaiting_user",
                    "need_chatgpt_prompt": False,
                    "need_chatgpt_next": False,
                    "need_codex_run": False,
                    "last_issue_centric_trigger_comment_url": execution.created_comment.url,
                },
                project_config={"worker_repo_path": "."},
                log_writer=TempLogWriter(temp_root),
                repo_relative=lambda path: path.name,
                launch_runner=fake_launch_runner,
                runtime_prompt_path_fn=lambda _config=None: prompt_path,
                write_text_fn=lambda path, text: path.write_text(text, encoding="utf-8"),
                save_state_fn=fake_save_state,
                load_state_fn=fake_load_state,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.launch_status, "launched")
            self.assertEqual(result.launch_entrypoint, "launch_codex_once.run")
            self.assertEqual(result.final_mode, "codex_done")
            self.assertEqual(len(launch_calls), 1)
            prompt_text = prompt_path.read_text(encoding="utf-8")
            self.assertIn("# Issue-Centric Codex Prompt", prompt_text)
            self.assertIn("target issue: https://github.com/example/repo/issues/20", prompt_text)
            self.assertIn("trigger comment: https://github.com/example/repo/issues/20#issuecomment-701", prompt_text)
            self.assertIn("Run this body.", prompt_text)
            self.assertEqual(saved_states[-1]["last_issue_centric_launch_status"], "launched")
            self.assertEqual(saved_states[-1]["last_issue_centric_launch_entrypoint"], "launch_codex_once.run")
            self.assertTrue(str(saved_states[-1]["last_issue_centric_launch_prompt_log"]).endswith(".md"))
            self.assertTrue(str(saved_states[-1]["last_issue_centric_launch_log"]).endswith(".json"))

    def test_launch_failure_preserves_trigger_comment_and_marks_blocked(self) -> None:
        prepared = self.prepared("#20", "Run this body.\n")
        execution = self.execution_result()
        saved_states: list[dict[str, object]] = []

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            prompt_path = temp_root / "codex_prompt.md"

            def fake_save_state(state: dict[str, object]) -> None:
                saved_states.append(dict(state))

            def fake_load_state() -> dict[str, object]:
                return dict(saved_states[-1]) if saved_states else {}

            def failing_launch_runner(state: dict[str, object], argv: list[str] | None) -> int:
                del state, argv
                raise BridgeError("launch boom")

            with self.assertRaisesRegex(BridgeError, "Trigger comment registration succeeded"):
                issue_centric_codex_launch.launch_issue_centric_codex_run(
                    prepared,
                    execution,
                    state={
                        "mode": "awaiting_user",
                        "need_chatgpt_prompt": False,
                        "need_chatgpt_next": False,
                        "need_codex_run": False,
                        "last_issue_centric_trigger_comment_url": execution.created_comment.url,
                    },
                    project_config={"worker_repo_path": "."},
                    log_writer=TempLogWriter(temp_root),
                    repo_relative=lambda path: path.name,
                    launch_runner=failing_launch_runner,
                    runtime_prompt_path_fn=lambda _config=None: prompt_path,
                    write_text_fn=lambda path, text: path.write_text(text, encoding="utf-8"),
                    save_state_fn=fake_save_state,
                    load_state_fn=fake_load_state,
                )

            self.assertEqual(saved_states[-1]["last_issue_centric_launch_status"], "failed_after_trigger_comment")
            self.assertEqual(saved_states[-1]["last_issue_centric_launch_entrypoint"], "launch_codex_once.run")
            self.assertIn("launch boom", str(saved_states[-1]["last_issue_centric_stop_reason"]))

    def test_launch_requires_assembled_payload(self) -> None:
        prepared = self.prepared("#20", "Run this body.\n")
        execution = issue_centric_codex_run.CodexRunExecutionResult(
            status="completed",
            resolved_issue=None,
            created_comment=None,
            payload=None,
            payload_log_path=None,
            execution_log_path=REPO_ROOT / "logs" / "execution.json",
            launch_status="not_implemented",
            launch_note="Not implemented.",
            safe_stop_reason="codex_run completed through trigger comment creation.",
        )

        with self.assertRaisesRegex(
            issue_centric_codex_launch.IssueCentricCodexLaunchError,
            "assembled payload",
        ):
            issue_centric_codex_launch.launch_issue_centric_codex_run(
                prepared,
                execution,
                state={},
                project_config={"worker_repo_path": "."},
                log_writer=TempLogWriter(REPO_ROOT / "logs"),
                repo_relative=lambda path: path.name,
            )


class FetchNextPromptCodexRunIntegrationTests(unittest.TestCase):
    def test_fetch_next_prompt_executes_codex_run_and_launches_existing_entrypoint(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "ready_issue:#20",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }
        raw = build_codex_run_reply("#20", "Run this body.\n")
        saved_states: list[dict[str, object]] = []

        fake_result = issue_centric_codex_run.CodexRunExecutionResult(
            status="completed",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            created_comment=issue_centric_github.CreatedGitHubComment(
                comment_id=701,
                url="https://github.com/example/repo/issues/20#issuecomment-701",
                body="Run this body.\n",
                repository="example/repo",
                issue_number=20,
            ),
            payload=issue_centric_codex_run.CodexRunExecutionPayload(
                repo="/tmp/repo",
                target_issue="https://github.com/example/repo/issues/20",
                request="Run this body.\n",
                trigger_comment="https://github.com/example/repo/issues/20#issuecomment-701",
            ),
            payload_log_path=REPO_ROOT / "logs" / "payload.json",
            execution_log_path=REPO_ROOT / "logs" / "execution.json",
            launch_status="not_implemented",
            launch_note="Not implemented.",
            safe_stop_reason="codex_run completed through trigger comment creation.",
        )
        fake_launch_result = issue_centric_codex_launch.IssueCentricCodexLaunchResult(
            status="completed",
            launch_status="launched",
            launch_entrypoint="launch_codex_once.run",
            prompt_path=REPO_ROOT / "bridge" / "inbox" / "codex_prompt.md",
            prompt_log_path=REPO_ROOT / "logs" / "issue_centric_codex_prompt.md",
            launch_log_path=REPO_ROOT / "logs" / "issue_centric_launch.json",
            final_mode="codex_done",
            safe_stop_reason="codex_run trigger comment and launch completed.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            persisted_after_launch = {
                **state,
                "mode": "codex_done",
                "last_issue_centric_execution_status": "completed",
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                "last_issue_centric_trigger_comment_id": "701",
                "last_issue_centric_trigger_comment_url": "https://github.com/example/repo/issues/20#issuecomment-701",
                "last_issue_centric_execution_payload_log": "logs/payload.json",
            }

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_prompt_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_state", return_value=dict(persisted_after_launch)),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."}),
                patch.object(fetch_next_prompt, "execute_codex_run_action", return_value=fake_result) as exec_mock,
                patch.object(fetch_next_prompt, "launch_issue_centric_codex_run", return_value=fake_launch_result) as launch_mock,
            ):
                with self.assertRaisesRegex(BridgeStop, "narrow 接続しました"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(exec_mock.call_count, 1)
            self.assertEqual(launch_mock.call_count, 1)
            saved = saved_states[-1]
            self.assertEqual(saved["last_issue_centric_execution_status"], "completed")
            self.assertEqual(saved["last_issue_centric_resolved_issue"], "https://github.com/example/repo/issues/20")
            self.assertEqual(saved["last_issue_centric_trigger_comment_id"], "701")
            self.assertEqual(saved["last_issue_centric_trigger_comment_url"], "https://github.com/example/repo/issues/20#issuecomment-701")
            self.assertEqual(saved["last_issue_centric_execution_payload_log"], "logs/payload.json")
            self.assertEqual(saved["last_issue_centric_launch_status"], "launched")
            self.assertEqual(saved["last_issue_centric_launch_entrypoint"], "launch_codex_once.run")
            self.assertEqual(saved["last_issue_centric_launch_log"], "logs/issue_centric_launch.json")

    def test_fetch_next_prompt_records_blocked_codex_run_reason(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "ready_issue:#20",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }
        raw = build_codex_run_reply("bad-ref", "Run this body.\n")
        saved_states: list[dict[str, object]] = []

        fake_result = issue_centric_codex_run.CodexRunExecutionResult(
            status="blocked",
            resolved_issue=None,
            created_comment=None,
            payload=None,
            payload_log_path=None,
            execution_log_path=REPO_ROOT / "logs" / "execution.json",
            launch_status="not_attempted",
            launch_note="Not attempted.",
            safe_stop_reason="codex_run blocked before launch.",
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
                patch.object(fetch_next_prompt, "execute_codex_run_action", return_value=fake_result),
            ):
                with self.assertRaisesRegex(BridgeStop, "trigger comment execution まで実行しました"):
                    fetch_next_prompt.run(dict(state), [])

            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_execution_status"], "blocked")
            self.assertEqual(saved["last_issue_centric_trigger_comment_id"], "")
            self.assertEqual(saved["last_issue_centric_launch_status"], "not_attempted")

    def test_fetch_next_prompt_surfaces_launch_failure_after_trigger_comment(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "ready_issue:#20",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }
        raw = build_codex_run_reply("#20", "Run this body.\n")
        saved_states: list[dict[str, object]] = []

        fake_result = issue_centric_codex_run.CodexRunExecutionResult(
            status="completed",
            resolved_issue=issue_centric_github.ResolvedGitHubIssue(
                repository="example/repo",
                issue_number=20,
                issue_url="https://github.com/example/repo/issues/20",
                source_ref="#20",
            ),
            created_comment=issue_centric_github.CreatedGitHubComment(
                comment_id=701,
                url="https://github.com/example/repo/issues/20#issuecomment-701",
                body="Run this body.\n",
                repository="example/repo",
                issue_number=20,
            ),
            payload=issue_centric_codex_run.CodexRunExecutionPayload(
                repo="/tmp/repo",
                target_issue="https://github.com/example/repo/issues/20",
                request="Run this body.\n",
                trigger_comment="https://github.com/example/repo/issues/20#issuecomment-701",
            ),
            payload_log_path=REPO_ROOT / "logs" / "payload.json",
            execution_log_path=REPO_ROOT / "logs" / "execution.json",
            launch_status="not_implemented",
            launch_note="Not implemented.",
            safe_stop_reason="codex_run completed through trigger comment creation.",
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
                patch.object(fetch_next_prompt, "execute_codex_run_action", return_value=fake_result),
                patch.object(fetch_next_prompt, "launch_issue_centric_codex_run", side_effect=BridgeError("launch failed")),
            ):
                with self.assertRaisesRegex(BridgeError, "launch failed"):
                    fetch_next_prompt.run(dict(state), [])


if __name__ == "__main__":
    unittest.main()
