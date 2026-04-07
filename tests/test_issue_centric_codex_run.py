from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _bridge_common  # noqa: E402
import archive_codex_report  # noqa: E402
import bridge_orchestrator  # noqa: E402
import fetch_next_prompt  # noqa: E402
import issue_centric_codex_launch  # noqa: E402
import issue_centric_codex_run  # noqa: E402
import issue_centric_followup_issue  # noqa: E402
import issue_centric_contract  # noqa: E402
import issue_centric_github  # noqa: E402
import issue_centric_issue_create  # noqa: E402
import issue_centric_transport  # noqa: E402
from _bridge_common import BridgeError, BridgeStop  # noqa: E402


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build_codex_decision(
    target_issue: str,
    body_text: str,
    *,
    close_current_issue: bool = False,
    create_followup_issue: bool = False,
    followup_text: str | None = None,
) -> issue_centric_contract.IssueCentricDecision:
    return issue_centric_contract.IssueCentricDecision(
        action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
        target_issue=target_issue,
        close_current_issue=close_current_issue,
        create_followup_issue=create_followup_issue,
        summary="Run Codex for the target issue.",
        issue_body_base64=None,
        codex_body_base64=b64(body_text),
        review_base64=None,
        followup_issue_body_base64=(b64(followup_text) if followup_text is not None else None),
        raw_json="{}",
        raw_segment="segment",
    )


def build_codex_run_reply(
    target_issue: str,
    body_text: str,
    *,
    close_current_issue: bool = False,
    create_followup_issue: bool = False,
    followup_text: str | None = None,
) -> str:
    parts = [
        "あなた:",
        "request body",
        "ChatGPT:",
        issue_centric_contract.CODEX_BODY_START,
        b64(body_text),
        issue_centric_contract.CODEX_BODY_END,
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
                    "action": "codex_run",
                    "target_issue": target_issue,
                    "close_current_issue": close_current_issue,
                    "create_followup_issue": create_followup_issue,
                    "summary": "Run Codex for the existing issue.",
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
                followup_issue_body_base64=None,
                raw_json="{}",
                raw_segment="segment",
            ),
            issue_body=None,
            codex_body=None,
            review_body=None,
            followup_issue_body=None,
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
            report_path = temp_root / "codex_report.md"
            report_path.write_text("# Report\n\nbody\n", encoding="utf-8")

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
                runtime_report_path_fn=lambda: report_path,
                write_text_fn=lambda path, text: path.write_text(text, encoding="utf-8"),
                save_state_fn=fake_save_state,
                load_state_fn=fake_load_state,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.launch_status, "launched")
            self.assertEqual(result.launch_entrypoint, "launch_codex_once.run")
            self.assertEqual(result.continuation_status, "report_ready_for_archive")
            self.assertEqual(result.report_status, "ready_for_archive")
            self.assertEqual(result.report_file, str(report_path.resolve()))
            self.assertEqual(result.final_mode, "codex_done")
            self.assertEqual(len(launch_calls), 1)
            prompt_text = prompt_path.read_text(encoding="utf-8")
            self.assertIn("# Issue-Centric Codex Prompt", prompt_text)
            self.assertIn("target issue: https://github.com/example/repo/issues/20", prompt_text)
            self.assertIn("trigger comment: https://github.com/example/repo/issues/20#issuecomment-701", prompt_text)
            self.assertIn("Run this body.", prompt_text)
            self.assertIn("## Report Handoff", prompt_text)
            self.assertEqual(saved_states[-1]["last_issue_centric_launch_status"], "launched")
            self.assertEqual(saved_states[-1]["last_issue_centric_launch_entrypoint"], "launch_codex_once.run")
            self.assertEqual(saved_states[-1]["last_issue_centric_continuation_status"], "report_ready_for_archive")
            self.assertEqual(saved_states[-1]["last_issue_centric_report_status"], "ready_for_archive")
            self.assertTrue(str(saved_states[-1]["last_issue_centric_launch_prompt_log"]).endswith(".md"))
            self.assertTrue(str(saved_states[-1]["last_issue_centric_launch_log"]).endswith(".json"))
            self.assertTrue(str(saved_states[-1]["last_issue_centric_continuation_log"]).endswith(".json"))

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
            self.assertEqual(saved_states[-1]["last_issue_centric_continuation_status"], "launch_failed_after_trigger_comment")
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

    def test_launch_can_delegate_to_existing_codex_wait_flow(self) -> None:
        prepared = self.prepared("#20", "Run this body.\n")
        execution = self.execution_result()
        saved_states: list[dict[str, object]] = []

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            prompt_path = temp_root / "codex_prompt.md"
            report_path = temp_root / "codex_report.md"

            def fake_save_state(state: dict[str, object]) -> None:
                saved_states.append(dict(state))

            def fake_load_state() -> dict[str, object]:
                latest = dict(saved_states[-1]) if saved_states else {}
                latest.update({"mode": "codex_running", "need_codex_run": True})
                return latest

            result = issue_centric_codex_launch.launch_issue_centric_codex_run(
                prepared,
                execution,
                state={"mode": "awaiting_user", "need_codex_run": False},
                project_config={"worker_repo_path": "."},
                log_writer=TempLogWriter(temp_root),
                repo_relative=lambda path: path.name,
                launch_runner=lambda state, argv: 0,
                runtime_prompt_path_fn=lambda _config=None: prompt_path,
                runtime_report_path_fn=lambda: report_path,
                write_text_fn=lambda path, text: path.write_text(text, encoding="utf-8"),
                save_state_fn=fake_save_state,
                load_state_fn=fake_load_state,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.continuation_status, "delegated_to_existing_codex_wait")
            self.assertEqual(result.report_status, "waiting_for_report")
            self.assertEqual(result.final_mode, "codex_running")


class PreparedCodexDispatchResumeTests(unittest.TestCase):
    def build_pending_state(
        self,
        root: Path,
        raw_reply: str,
        *,
        artifact_text: str,
    ) -> tuple[dict[str, object], Path, Path]:
        raw_log = root / "raw_reply.txt"
        raw_log.write_text(raw_reply, encoding="utf-8")
        artifact_log = root / "prepared_issue_centric_codex_body.md"
        artifact_log.write_text(artifact_text, encoding="utf-8")
        decision_log = root / "decision.md"
        decision_log.write_text("# decision\n", encoding="utf-8")
        metadata_log = root / "metadata.json"
        metadata_log.write_text(
            json.dumps(
                {
                    "raw_response_log": str(raw_log),
                    "prepared_artifact": {
                        "kind": "codex_body",
                        "path": str(artifact_log),
                    },
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        return (
            {
                "mode": "awaiting_user",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": False,
                "need_codex_run": False,
                "chatgpt_decision": "issue_centric:codex_run",
                "chatgpt_decision_note": "prepared for later dispatch",
                "last_issue_centric_action": "codex_run",
                "last_issue_centric_target_issue": "#20",
                "last_issue_centric_artifact_kind": "codex_body",
                "last_issue_centric_artifact_file": str(artifact_log),
                "last_issue_centric_metadata_log": str(metadata_log),
                "last_issue_centric_decision_log": str(decision_log),
                "last_issue_centric_execution_status": "",
            },
            raw_log,
            artifact_log,
        )

    def test_bridge_orchestrator_dispatches_prepared_codex_run_from_saved_logs(self) -> None:
        raw = build_codex_run_reply("#20", "Run this body.\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, _, _ = self.build_pending_state(root, raw, artifact_text="Run this body.\n")
            out = io.StringIO()

            with (
                patch.object(bridge_orchestrator, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."}),
                patch.object(bridge_orchestrator, "print_project_config_warnings"),
                patch.object(bridge_orchestrator, "dispatch_pending_issue_centric_codex_run", return_value=0) as dispatch_mock,
                redirect_stdout(out),
            ):
                rc = bridge_orchestrator.run(dict(state), [])

        self.assertEqual(rc, 0)
        dispatch_mock.assert_called_once()
        self.assertIn("prepared Codex body", out.getvalue())

    def test_bridge_orchestrator_reconstructs_followup_body_for_later_codex_dispatch(self) -> None:
        raw = build_codex_run_reply(
            "#20",
            "Run this body.\n",
            create_followup_issue=True,
            followup_text="# Follow-up issue\n\nFollow-up body.\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, _, _ = self.build_pending_state(root, raw, artifact_text="Run this body.\n")

            def fake_dispatch(**kwargs):
                self.assertTrue(kwargs["contract_decision"].create_followup_issue)
                self.assertEqual(
                    kwargs["materialized"].prepared.followup_issue_body.decoded_text,
                    "# Follow-up issue\n\nFollow-up body.\n",
                )
                return SimpleNamespace(
                    final_state={**state, "mode": "codex_running"},
                    stop_message="followup dispatch done",
                )

            with (
                patch.object(bridge_orchestrator, "load_project_config", return_value={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."}),
                patch.object(bridge_orchestrator, "print_project_config_warnings"),
                patch.object(bridge_orchestrator, "dispatch_issue_centric_execution", side_effect=fake_dispatch) as dispatch_mock,
                patch.object(bridge_orchestrator, "save_state"),
            ):
                rc = bridge_orchestrator.run(dict(state), [])

        self.assertEqual(rc, 0)
        dispatch_mock.assert_called_once()


class IssueCentricContinuationArchiveTests(unittest.TestCase):
    def test_archive_marks_issue_centric_report_as_ready_for_next_request(self) -> None:
        state = {
            "mode": "codex_done",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "cycle": 3,
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
            "last_issue_centric_trigger_comment_url": "https://github.com/example/repo/issues/20#issuecomment-701",
            "last_issue_centric_launch_status": "launched",
            "last_issue_centric_launch_log": "logs/launch.json",
            "last_issue_centric_continuation_log": "logs/continuation.json",
        }
        saved_states: list[dict[str, object]] = []

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            outbox_path = temp_root / "codex_report.md"
            history_dir = temp_root / "history"
            outbox_path.write_text("# Report\n\nbody\n", encoding="utf-8")

            def fake_log_text(prefix: str, content: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(content, encoding="utf-8")
                return path

            with (
                patch.object(archive_codex_report, "runtime_report_path", return_value=outbox_path),
                patch.object(archive_codex_report, "runtime_history_dir", return_value=history_dir),
                patch.object(archive_codex_report, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(archive_codex_report, "log_text", side_effect=fake_log_text),
            ):
                rc = archive_codex_report.run(dict(state))

            self.assertEqual(rc, 0)
            saved = saved_states[-1]
            self.assertEqual(saved["mode"], "idle")
            self.assertTrue(saved["need_chatgpt_next"])
            self.assertEqual(saved["last_issue_centric_continuation_status"], "archived_for_next_request")
            self.assertEqual(saved["last_issue_centric_report_status"], "archived")
            self.assertIn("codex_report_cycle_0004_", saved["last_issue_centric_report_file"])
            self.assertTrue(str(saved["last_issue_centric_continuation_log"]).endswith(".md"))

    def test_next_request_builder_keeps_issue_centric_target_issue_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            template_path = temp_root / "request_template.md"
            template_path.write_text("STATE\n{CURRENT_STATUS}\n", encoding="utf-8")

            request = _bridge_common.build_chatgpt_request(
                state={
                    "mode": "idle",
                    "need_chatgpt_prompt": False,
                    "need_chatgpt_next": True,
                    "need_codex_run": False,
                    "last_issue_centric_action": "codex_run",
                    "last_issue_centric_target_issue": "#20",
                    "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
                    "last_issue_centric_trigger_comment_url": "https://github.com/example/repo/issues/20#issuecomment-701",
                    "last_issue_centric_continuation_status": "archived_for_next_request",
                    "last_issue_centric_report_status": "archived",
                    "last_issue_centric_report_file": "bridge/history/codex_report_cycle_0004_sample.md",
                },
                template_path=template_path,
                next_todo="next",
                open_questions="none",
                last_report="===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
            )

            self.assertIn("last_issue_centric_target_issue: #20", request)
            self.assertIn("last_issue_centric_trigger_comment_url: https://github.com/example/repo/issues/20#issuecomment-701", request)
            self.assertIn("last_issue_centric_continuation_status: archived_for_next_request", request)
            self.assertIn("last_issue_centric_report_status: archived", request)


if __name__ == "__main__":
    unittest.main()
