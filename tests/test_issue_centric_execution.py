from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import issue_centric_contract  # noqa: E402
import issue_centric_execution  # noqa: E402
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


def build_decision(
    *,
    action: issue_centric_contract.IssueCentricAction,
    target_issue: str | None = None,
    close_current_issue: bool = False,
    create_followup_issue: bool = False,
    issue_text: str | None = None,
    review_text: str | None = None,
    followup_text: str | None = None,
) -> issue_centric_contract.IssueCentricDecision:
    return issue_centric_contract.IssueCentricDecision(
        action=action,
        target_issue=target_issue,
        close_current_issue=close_current_issue,
        create_followup_issue=create_followup_issue,
        summary="Dispatcher test decision",
        issue_body_base64=(b64(issue_text) if issue_text is not None else None),
        codex_body_base64=None,
        review_base64=(b64(review_text) if review_text is not None else None),
        followup_issue_body_base64=(b64(followup_text) if followup_text is not None else None),
        raw_json="{}",
        raw_segment="segment",
    )


def materialized_from_decision(
    decision: issue_centric_contract.IssueCentricDecision,
    *,
    root: Path,
) -> SimpleNamespace:
    prepared = issue_centric_transport.decode_issue_centric_decision(decision)
    metadata = root / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    artifact = root / "artifact.md"
    artifact.write_text("artifact", encoding="utf-8")
    return SimpleNamespace(
        prepared=prepared,
        metadata_log_path=metadata,
        artifact_log_path=artifact,
        safe_stop_reason="prepared",
    )


def fake_issue(number: int, *, state: str = "open") -> SimpleNamespace:
    return SimpleNamespace(
        number=number,
        url=f"https://github.com/example/repo/issues/{number}",
        title=f"Issue {number}",
        repository="example/repo",
        node_id=f"ISSUE_{number}",
        state=state,
    )


def fake_comment(comment_id: int, issue_number: int) -> SimpleNamespace:
    return SimpleNamespace(
        comment_id=comment_id,
        url=f"https://github.com/example/repo/issues/{issue_number}#issuecomment-{comment_id}",
        issue_number=issue_number,
    )


class IssueCentricExecutionDispatcherTests(unittest.TestCase):
    def base_state(self) -> dict[str, object]:
        return {
            "last_issue_centric_action": "",
            "last_issue_centric_target_issue": "",
            "last_issue_centric_stop_reason": "",
            "chatgpt_decision_note": "",
            "last_issue_centric_dispatch_result": "",
            "last_issue_centric_normalized_summary": "",
            "last_issue_centric_runtime_snapshot": "",
            "last_issue_centric_snapshot_status": "",
            "last_issue_centric_runtime_generation_id": "",
            "last_issue_centric_generation_lifecycle": "",
            "last_issue_centric_generation_lifecycle_reason": "",
            "last_issue_centric_generation_lifecycle_source": "",
            "last_issue_centric_prepared_generation_id": "",
            "last_issue_centric_pending_generation_id": "",
            "last_issue_centric_principal_issue": "",
            "last_issue_centric_principal_issue_kind": "",
            "last_issue_centric_next_request_hint": "",
            "last_issue_centric_next_request_target": "",
            "last_issue_centric_next_request_target_source": "",
            "last_issue_centric_next_request_fallback_reason": "",
            "last_issue_centric_route_selected": "",
            "last_issue_centric_route_fallback_reason": "",
            "last_issue_centric_recovery_status": "",
            "last_issue_centric_recovery_source": "",
            "last_issue_centric_recovery_fallback_reason": "",
            "last_issue_centric_runtime_mode": "",
            "last_issue_centric_runtime_mode_reason": "",
            "last_issue_centric_runtime_mode_source": "",
            "last_issue_centric_freshness_status": "",
            "last_issue_centric_freshness_reason": "",
            "last_issue_centric_freshness_source": "",
            "last_issue_centric_invalidation_status": "",
            "last_issue_centric_invalidation_reason": "",
            "last_issue_centric_invalidated_generation_id": "",
            "last_issue_centric_consumed_generation_id": "",
            "last_issue_centric_close_order": "",
            "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
            "last_issue_centric_execution_status": "",
            "last_issue_centric_execution_log": "",
            "last_issue_centric_created_issue_number": "",
            "last_issue_centric_created_issue_url": "",
            "last_issue_centric_created_issue_title": "",
            "last_issue_centric_primary_issue_number": "",
            "last_issue_centric_primary_issue_url": "",
            "last_issue_centric_primary_issue_title": "",
            "last_issue_centric_project_sync_status": "",
            "last_issue_centric_project_url": "",
            "last_issue_centric_project_item_id": "",
            "last_issue_centric_project_state_field": "",
            "last_issue_centric_project_state_value": "",
            "last_issue_centric_primary_project_sync_status": "",
            "last_issue_centric_primary_project_url": "",
            "last_issue_centric_primary_project_item_id": "",
            "last_issue_centric_primary_project_state_field": "",
            "last_issue_centric_primary_project_state_value": "",
            "last_issue_centric_followup_status": "",
            "last_issue_centric_followup_log": "",
            "last_issue_centric_followup_parent_issue": "",
            "last_issue_centric_followup_issue_number": "",
            "last_issue_centric_followup_issue_url": "",
            "last_issue_centric_followup_issue_title": "",
            "last_issue_centric_followup_project_sync_status": "",
            "last_issue_centric_followup_project_url": "",
            "last_issue_centric_followup_project_item_id": "",
            "last_issue_centric_followup_project_state_field": "",
            "last_issue_centric_followup_project_state_value": "",
            "last_issue_centric_current_project_item_id": "",
            "last_issue_centric_current_project_url": "",
            "last_issue_centric_lifecycle_sync_status": "",
            "last_issue_centric_lifecycle_sync_log": "",
            "last_issue_centric_lifecycle_sync_issue": "",
            "last_issue_centric_lifecycle_sync_stage": "",
            "last_issue_centric_lifecycle_sync_project_url": "",
            "last_issue_centric_lifecycle_sync_project_item_id": "",
            "last_issue_centric_lifecycle_sync_state_field": "",
            "last_issue_centric_lifecycle_sync_state_value": "",
            "last_issue_centric_close_status": "",
            "last_issue_centric_close_log": "",
            "last_issue_centric_closed_issue_number": "",
            "last_issue_centric_closed_issue_url": "",
            "last_issue_centric_closed_issue_title": "",
            "last_issue_centric_review_status": "",
            "last_issue_centric_review_log": "",
            "last_issue_centric_review_comment_id": "",
            "last_issue_centric_review_comment_url": "",
            "last_issue_centric_review_close_policy": "",
        }

    def dispatch(
        self,
        *,
        decision: issue_centric_contract.IssueCentricDecision,
        materialized: SimpleNamespace,
        root: Path,
        mutable_state: dict[str, object] | None = None,
        project_config: dict[str, object] | None = None,
        execute_issue_create_action_fn=None,
        execute_codex_run_action_fn=None,
        launch_issue_centric_codex_run_fn=None,
        execute_human_review_action_fn=None,
        execute_close_current_issue_fn=None,
        execute_followup_issue_action_fn=None,
        execute_current_issue_project_state_sync_fn=None,
    ) -> issue_centric_execution.IssueCentricDispatchResult:
        saved_states: list[dict[str, object]] = []
        log_writer = TempLogWriter(root)
        state = self.base_state() if mutable_state is None else mutable_state
        state.update(
            {
                "last_issue_centric_action": decision.action.value,
                "last_issue_centric_target_issue": decision.target_issue or "none",
            }
        )
        return issue_centric_execution.dispatch_issue_centric_execution(
            contract_decision=decision,
            materialized=materialized,
            prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
            mutable_state=state,
            project_config=project_config
            or {"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
            repo_path=REPO_ROOT,
            source_raw_log="logs/raw.txt",
            source_decision_log="logs/decision.md",
            source_metadata_log="logs/metadata.json",
            source_artifact_path="logs/artifact.md",
            log_writer=log_writer,
            repo_relative=lambda path: str(path),
            load_state_fn=lambda: dict(saved_states[-1]) if saved_states else dict(state),
            save_state_fn=lambda s: saved_states.append(dict(s)),
            execute_issue_create_action_fn=execute_issue_create_action_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("issue_create should not run"))),
            execute_codex_run_action_fn=execute_codex_run_action_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("codex_run should not run"))),
            launch_issue_centric_codex_run_fn=launch_issue_centric_codex_run_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("launch should not run"))),
            execute_human_review_action_fn=execute_human_review_action_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("review should not run"))),
            execute_close_current_issue_fn=execute_close_current_issue_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("close should not run"))),
            execute_followup_issue_action_fn=execute_followup_issue_action_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("followup should not run"))),
            execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn
            or (
                lambda *args, **kwargs: SimpleNamespace(
                    status="not_requested",
                    sync_status="not_requested_no_project",
                    lifecycle_stage=kwargs.get("lifecycle_stage", ""),
                    resolved_issue=None,
                    issue_snapshot=None,
                    execution_log_path=root / "no-project-sync.json",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="No GitHub Project is configured for current-issue lifecycle state sync.",
                )
            ),
            launch_runner=lambda state, argv=None: 0,
        )

    def test_dispatcher_blocks_codex_run_close_before_trigger_comment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=False,
                summary="Run codex and close current issue.",
                issue_body_base64=None,
                codex_body_base64=b64("Implement the issue.\n"),
                review_base64=None,
                followup_issue_body_base64=None,
                raw_json="{}",
                raw_segment="segment",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    close_status="blocked",
                    close_order="blocked_codex_run",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    issue_before=None,
                    issue_after=None,
                    execution_log_path=log_path,
                    safe_stop_reason="action=codex_run cannot execute close_current_issue in this slice.",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["close"])
            self.assertEqual(result.matrix_path, "blocked_codex_run_close")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual(result.final_state["last_issue_centric_close_status"], "blocked")
            self.assertEqual(result.final_state["last_issue_centric_close_order"], "blocked_codex_run")
            self.assertEqual(result.final_state["last_issue_centric_execution_status"], "")
            self.assertIn("codex_run + close_current_issue", result.stop_message)

    def test_dispatcher_runs_codex_followup_then_close_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                summary="Run codex and create follow-up.",
                issue_body_base64=None,
                codex_body_base64=b64("Implement the issue.\n"),
                review_base64=None,
                followup_issue_body_base64=b64("# Follow-up\n\nBody\n"),
                raw_json="{}",
                raw_segment="segment",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_codex_run(*args, **kwargs):
                calls.append("trigger")
                log_path = root / "codex-trigger.json"
                log_path.write_text("{}", encoding="utf-8")
                payload_path = root / "payload.json"
                payload_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(703, 20),
                    payload=SimpleNamespace(
                        repo=str(REPO_ROOT),
                        target_issue="https://github.com/example/repo/issues/20",
                        request="Implement the issue.\n",
                        trigger_comment="https://github.com/example/repo/issues/20#issuecomment-703",
                    ),
                    payload_log_path=payload_path,
                    execution_log_path=log_path,
                    launch_status="not_implemented",
                    launch_note="not implemented",
                    safe_stop_reason="trigger comment created",
                )

            def fake_launch(*args, **kwargs):
                calls.append("launch")
                prompt_log = root / "prompt.md"
                prompt_log.write_text("prompt", encoding="utf-8")
                launch_log = root / "launch.json"
                launch_log.write_text("{}", encoding="utf-8")
                cont_log = root / "continuation.json"
                cont_log.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    launch_status="launched",
                    launch_entrypoint="launch_codex_once.run",
                    prompt_log_path=prompt_log,
                    launch_log_path=launch_log,
                    continuation_status="delegated_to_existing_codex_wait",
                    continuation_log_path=cont_log,
                    report_status="waiting_for_report",
                    report_file="",
                    final_mode="codex_running",
                    safe_stop_reason="launch and continuation delegated",
                )

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    followup_status="completed",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=fake_issue(81),
                    execution_log_path=log_path,
                    project_sync_status="project_state_synced",
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="ITEM_81",
                    project_state_field_name="State",
                    project_state_value_name="ready",
                    safe_stop_reason="follow-up created after codex handoff",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_codex_run_followup",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed current issue after codex continuation and follow-up",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_codex_run_action_fn=fake_codex_run,
                launch_issue_centric_codex_run_fn=fake_launch,
                execute_followup_issue_action_fn=fake_followup,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["trigger", "launch", "followup", "close"])
            self.assertEqual(result.matrix_path, "codex_run_followup_then_close")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(
                [step.name for step in result.steps],
                [
                    "codex_trigger_comment",
                    "codex_launch_and_continuation",
                    "followup_issue_create",
                    "close_current_issue",
                ],
            )
            self.assertEqual(result.final_state["last_issue_centric_followup_issue_number"], "81")
            self.assertEqual(result.final_state["last_issue_centric_close_order"], "after_codex_run_followup")
            self.assertEqual(
                result.final_state["last_issue_centric_principal_issue"],
                "https://github.com/example/repo/issues/81",
            )
            self.assertEqual(result.final_state["last_issue_centric_principal_issue_kind"], "followup_issue")
            self.assertEqual(result.final_state["last_issue_centric_next_request_hint"], "continue_on_followup_issue")
            self.assertTrue(str(result.final_state["last_issue_centric_normalized_summary"]).endswith(".json"))
            self.assertTrue(str(result.final_state["last_issue_centric_runtime_snapshot"]).endswith(".json"))
            self.assertEqual(result.final_state["last_issue_centric_snapshot_status"], "issue_centric_snapshot_ready")
            self.assertEqual(result.final_state["last_issue_centric_runtime_mode"], "issue_centric_ready")
            self.assertEqual(result.final_state["last_issue_centric_runtime_mode_reason"], "issue_centric_snapshot_ready")
            self.assertTrue(str(result.final_state["last_issue_centric_runtime_generation_id"]).startswith("summary:"))
            self.assertEqual(result.final_state["last_issue_centric_generation_lifecycle"], "fresh_available")
            self.assertEqual(result.final_state["last_issue_centric_freshness_status"], "issue_centric_fresh")

    def test_dispatcher_runs_codex_followup_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=False,
                create_followup_issue=True,
                summary="Run codex and create follow-up.",
                issue_body_base64=None,
                codex_body_base64=b64("Implement the issue.\n"),
                review_base64=None,
                followup_issue_body_base64=b64("# Follow-up\n\nBody\n"),
                raw_json="{}",
                raw_segment="segment",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_codex_run(*args, **kwargs):
                calls.append("trigger")
                log_path = root / "codex-trigger.json"
                log_path.write_text("{}", encoding="utf-8")
                payload_path = root / "payload.json"
                payload_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(701, 20),
                    payload=SimpleNamespace(
                        repo=str(REPO_ROOT),
                        target_issue="https://github.com/example/repo/issues/20",
                        request="Implement the issue.\n",
                        trigger_comment="https://github.com/example/repo/issues/20#issuecomment-701",
                    ),
                    payload_log_path=payload_path,
                    execution_log_path=log_path,
                    launch_status="not_implemented",
                    launch_note="not implemented",
                    safe_stop_reason="trigger comment created",
                )

            def fake_launch(*args, **kwargs):
                calls.append("launch")
                prompt_log = root / "prompt.md"
                prompt_log.write_text("prompt", encoding="utf-8")
                launch_log = root / "launch.json"
                launch_log.write_text("{}", encoding="utf-8")
                cont_log = root / "continuation.json"
                cont_log.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    launch_status="launched",
                    launch_entrypoint="launch_codex_once.run",
                    prompt_log_path=prompt_log,
                    launch_log_path=launch_log,
                    continuation_status="delegated_to_existing_codex_wait",
                    continuation_log_path=cont_log,
                    report_status="waiting_for_report",
                    report_file="",
                    final_mode="codex_running",
                    safe_stop_reason="launch and continuation delegated",
                )

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    followup_status="completed",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=fake_issue(81),
                    execution_log_path=log_path,
                    project_sync_status="project_state_synced",
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="ITEM_81",
                    project_state_field_name="State",
                    project_state_value_name="ready",
                    safe_stop_reason="follow-up created after codex handoff",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_codex_run_action_fn=fake_codex_run,
                launch_issue_centric_codex_run_fn=fake_launch,
                execute_followup_issue_action_fn=fake_followup,
            )

            self.assertEqual(calls, ["trigger", "launch", "followup"])
            self.assertEqual(result.matrix_path, "codex_run_followup")
            self.assertEqual(
                [step.name for step in result.steps],
                ["codex_trigger_comment", "codex_launch_and_continuation", "followup_issue_create"],
            )
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_followup_status"], "completed")
            self.assertEqual(result.final_state["last_issue_centric_followup_issue_number"], "81")

    def test_dispatcher_syncs_codex_run_current_issue_to_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=False,
                create_followup_issue=False,
                summary="Run codex and sync current issue state.",
                issue_body_base64=None,
                codex_body_base64=b64("Implement the issue.\n"),
                review_base64=None,
                followup_issue_body_base64=None,
                raw_json="{}",
                raw_segment="segment",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_codex_run(*args, **kwargs):
                calls.append("trigger")
                log_path = root / "codex-trigger.json"
                log_path.write_text("{}", encoding="utf-8")
                payload_path = root / "payload.json"
                payload_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(706, 20),
                    payload=SimpleNamespace(
                        repo=str(REPO_ROOT),
                        target_issue="https://github.com/example/repo/issues/20",
                        request="Implement the issue.\n",
                        trigger_comment="https://github.com/example/repo/issues/20#issuecomment-706",
                    ),
                    payload_log_path=payload_path,
                    execution_log_path=log_path,
                    launch_status="not_implemented",
                    launch_note="not implemented",
                    safe_stop_reason="trigger comment created",
                )

            def fake_launch(*args, **kwargs):
                calls.append("launch")
                prompt_log = root / "prompt.md"
                prompt_log.write_text("prompt", encoding="utf-8")
                launch_log = root / "launch.json"
                launch_log.write_text("{}", encoding="utf-8")
                cont_log = root / "continuation.json"
                cont_log.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    launch_status="launched",
                    launch_entrypoint="launch_codex_once.run",
                    prompt_log_path=prompt_log,
                    launch_log_path=launch_log,
                    continuation_status="delegated_to_existing_codex_wait",
                    continuation_log_path=cont_log,
                    report_status="waiting_for_report",
                    report_file="",
                    final_mode="codex_running",
                    safe_stop_reason="launch and continuation delegated",
                )

            def fake_sync(*args, **kwargs):
                calls.append(f"sync:{kwargs['lifecycle_stage']}")
                log_path = root / "project-sync.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    sync_status="project_state_synced",
                    lifecycle_stage=kwargs["lifecycle_stage"],
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    issue_snapshot=SimpleNamespace(number=20, url="https://github.com/example/repo/issues/20", title="Current issue"),
                    execution_log_path=log_path,
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="ITEM_20",
                    project_state_field_name="State",
                    project_state_value_name="in_progress",
                    safe_stop_reason="current issue synced to in_progress",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_in_progress_state": "in_progress",
                    "worker_repo_path": ".",
                },
                execute_codex_run_action_fn=fake_codex_run,
                launch_issue_centric_codex_run_fn=fake_launch,
                execute_current_issue_project_state_sync_fn=fake_sync,
            )

            self.assertEqual(calls, ["trigger", "launch", "sync:in_progress"])
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "project_state_synced")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_state_value"], "in_progress")
            self.assertEqual(result.final_state["last_issue_centric_principal_issue_kind"], "current_issue")
            self.assertEqual(result.final_state["last_issue_centric_next_request_hint"], "continue_on_current_issue")

    def test_dispatcher_keeps_codex_success_when_codex_followup_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=False,
                create_followup_issue=True,
                summary="Run codex and create follow-up.",
                issue_body_base64=None,
                codex_body_base64=b64("Implement the issue.\n"),
                review_base64=None,
                followup_issue_body_base64=b64("# Follow-up\n\nBody\n"),
                raw_json="{}",
                raw_segment="segment",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_codex_run(*args, **kwargs):
                calls.append("trigger")
                log_path = root / "codex-trigger.json"
                log_path.write_text("{}", encoding="utf-8")
                payload_path = root / "payload.json"
                payload_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(702, 20),
                    payload=SimpleNamespace(
                        repo=str(REPO_ROOT),
                        target_issue="https://github.com/example/repo/issues/20",
                        request="Implement the issue.\n",
                        trigger_comment="https://github.com/example/repo/issues/20#issuecomment-702",
                    ),
                    payload_log_path=payload_path,
                    execution_log_path=log_path,
                    launch_status="not_implemented",
                    launch_note="not implemented",
                    safe_stop_reason="trigger comment created",
                )

            def fake_launch(*args, **kwargs):
                calls.append("launch")
                prompt_log = root / "prompt.md"
                prompt_log.write_text("prompt", encoding="utf-8")
                launch_log = root / "launch.json"
                launch_log.write_text("{}", encoding="utf-8")
                cont_log = root / "continuation.json"
                cont_log.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    launch_status="launched",
                    launch_entrypoint="launch_codex_once.run",
                    prompt_log_path=prompt_log,
                    launch_log_path=launch_log,
                    continuation_status="delegated_to_existing_codex_wait",
                    continuation_log_path=cont_log,
                    report_status="waiting_for_report",
                    report_file="",
                    final_mode="codex_running",
                    safe_stop_reason="launch and continuation delegated",
                )

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    followup_status="blocked_project_preflight",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=None,
                    execution_log_path=log_path,
                    project_sync_status="blocked_project_preflight",
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="",
                    project_state_field_name="State",
                    project_state_value_name="ready",
                    safe_stop_reason="follow-up blocked",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_codex_run_action_fn=fake_codex_run,
                launch_issue_centric_codex_run_fn=fake_launch,
                execute_followup_issue_action_fn=fake_followup,
            )

            self.assertEqual(calls, ["trigger", "launch", "followup"])
            self.assertEqual(result.matrix_path, "codex_run_followup")
            self.assertEqual(result.final_status, "partial")
            self.assertEqual(result.final_state["last_issue_centric_launch_status"], "launched")
            self.assertEqual(result.final_state["last_issue_centric_followup_status"], "blocked_project_preflight")

    def test_dispatcher_does_not_close_when_codex_followup_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                summary="Run codex, create follow-up, and close current issue.",
                issue_body_base64=None,
                codex_body_base64=b64("Implement the issue.\n"),
                review_base64=None,
                followup_issue_body_base64=b64("# Follow-up\n\nBody\n"),
                raw_json="{}",
                raw_segment="segment",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_codex_run(*args, **kwargs):
                calls.append("trigger")
                log_path = root / "codex-trigger.json"
                log_path.write_text("{}", encoding="utf-8")
                payload_path = root / "payload.json"
                payload_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(704, 20),
                    payload=SimpleNamespace(
                        repo=str(REPO_ROOT),
                        target_issue="https://github.com/example/repo/issues/20",
                        request="Implement the issue.\n",
                        trigger_comment="https://github.com/example/repo/issues/20#issuecomment-704",
                    ),
                    payload_log_path=payload_path,
                    execution_log_path=log_path,
                    launch_status="not_implemented",
                    launch_note="not implemented",
                    safe_stop_reason="trigger comment created",
                )

            def fake_launch(*args, **kwargs):
                calls.append("launch")
                prompt_log = root / "prompt.md"
                prompt_log.write_text("prompt", encoding="utf-8")
                launch_log = root / "launch.json"
                launch_log.write_text("{}", encoding="utf-8")
                cont_log = root / "continuation.json"
                cont_log.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    launch_status="launched",
                    launch_entrypoint="launch_codex_once.run",
                    prompt_log_path=prompt_log,
                    launch_log_path=launch_log,
                    continuation_status="delegated_to_existing_codex_wait",
                    continuation_log_path=cont_log,
                    report_status="waiting_for_report",
                    report_file="",
                    final_mode="codex_running",
                    safe_stop_reason="launch and continuation delegated",
                )

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    followup_status="blocked_project_preflight",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=None,
                    execution_log_path=log_path,
                    project_sync_status="blocked_project_preflight",
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="",
                    project_state_field_name="State",
                    project_state_value_name="ready",
                    safe_stop_reason="follow-up blocked",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_codex_run_action_fn=fake_codex_run,
                launch_issue_centric_codex_run_fn=fake_launch,
                execute_followup_issue_action_fn=fake_followup,
            )

            self.assertEqual(calls, ["trigger", "launch", "followup"])
            self.assertEqual(result.matrix_path, "codex_run_followup_then_close")
            self.assertEqual(result.final_status, "partial")
            self.assertEqual(result.final_state["last_issue_centric_launch_status"], "launched")
            self.assertEqual(result.final_state["last_issue_centric_followup_status"], "blocked_project_preflight")
            self.assertEqual(result.final_state["last_issue_centric_close_status"], "not_attempted_followup_blocked")

    def test_dispatcher_keeps_codex_and_followup_success_when_codex_followup_close_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                summary="Run codex, create follow-up, and close current issue.",
                issue_body_base64=None,
                codex_body_base64=b64("Implement the issue.\n"),
                review_base64=None,
                followup_issue_body_base64=b64("# Follow-up\n\nBody\n"),
                raw_json="{}",
                raw_segment="segment",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_codex_run(*args, **kwargs):
                calls.append("trigger")
                log_path = root / "codex-trigger.json"
                log_path.write_text("{}", encoding="utf-8")
                payload_path = root / "payload.json"
                payload_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(705, 20),
                    payload=SimpleNamespace(
                        repo=str(REPO_ROOT),
                        target_issue="https://github.com/example/repo/issues/20",
                        request="Implement the issue.\n",
                        trigger_comment="https://github.com/example/repo/issues/20#issuecomment-705",
                    ),
                    payload_log_path=payload_path,
                    execution_log_path=log_path,
                    launch_status="not_implemented",
                    launch_note="not implemented",
                    safe_stop_reason="trigger comment created",
                )

            def fake_launch(*args, **kwargs):
                calls.append("launch")
                prompt_log = root / "prompt.md"
                prompt_log.write_text("prompt", encoding="utf-8")
                launch_log = root / "launch.json"
                launch_log.write_text("{}", encoding="utf-8")
                cont_log = root / "continuation.json"
                cont_log.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    launch_status="launched",
                    launch_entrypoint="launch_codex_once.run",
                    prompt_log_path=prompt_log,
                    launch_log_path=launch_log,
                    continuation_status="delegated_to_existing_codex_wait",
                    continuation_log_path=cont_log,
                    report_status="waiting_for_report",
                    report_file="",
                    final_mode="codex_running",
                    safe_stop_reason="launch and continuation delegated",
                )

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    followup_status="completed",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=fake_issue(83),
                    execution_log_path=log_path,
                    project_sync_status="project_state_synced",
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="ITEM_83",
                    project_state_field_name="State",
                    project_state_value_name="ready",
                    safe_stop_reason="follow-up created after codex handoff",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    close_status="failed_after_mutation_attempt",
                    close_order="after_codex_run_followup",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=None,
                    safe_stop_reason="close failed after reviewable codex/followup success",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_codex_run_action_fn=fake_codex_run,
                launch_issue_centric_codex_run_fn=fake_launch,
                execute_followup_issue_action_fn=fake_followup,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["trigger", "launch", "followup", "close"])
            self.assertEqual(result.matrix_path, "codex_run_followup_then_close")
            self.assertEqual(result.final_status, "partial")
            self.assertEqual(result.final_state["last_issue_centric_followup_issue_number"], "83")
            self.assertEqual(result.final_state["last_issue_centric_close_order"], "after_codex_run_followup")

    def test_dispatcher_syncs_done_after_codex_followup_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = issue_centric_contract.IssueCentricDecision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                summary="Run codex, create follow-up, close current issue, and sync lifecycle state.",
                issue_body_base64=None,
                codex_body_base64=b64("Implement the issue.\n"),
                review_base64=None,
                followup_issue_body_base64=b64("# Follow-up\n\nBody\n"),
                raw_json="{}",
                raw_segment="segment",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_codex_run(*args, **kwargs):
                calls.append("trigger")
                log_path = root / "codex-trigger.json"
                log_path.write_text("{}", encoding="utf-8")
                payload_path = root / "payload.json"
                payload_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(706, 20),
                    payload=SimpleNamespace(
                        repo=str(REPO_ROOT),
                        target_issue="https://github.com/example/repo/issues/20",
                        request="Implement the issue.\n",
                        trigger_comment="https://github.com/example/repo/issues/20#issuecomment-706",
                    ),
                    payload_log_path=payload_path,
                    execution_log_path=log_path,
                    launch_status="not_implemented",
                    launch_note="not implemented",
                    safe_stop_reason="trigger comment created",
                )

            def fake_launch(*args, **kwargs):
                calls.append("launch")
                prompt_log = root / "prompt.md"
                prompt_log.write_text("prompt", encoding="utf-8")
                launch_log = root / "launch.json"
                launch_log.write_text("{}", encoding="utf-8")
                cont_log = root / "continuation.json"
                cont_log.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    launch_status="launched",
                    launch_entrypoint="launch_codex_once.run",
                    prompt_log_path=prompt_log,
                    launch_log_path=launch_log,
                    continuation_status="delegated_to_existing_codex_wait",
                    continuation_log_path=cont_log,
                    report_status="waiting_for_report",
                    report_file="",
                    final_mode="codex_running",
                    safe_stop_reason="launch and continuation delegated",
                )

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    followup_status="completed",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=fake_issue(85),
                    execution_log_path=log_path,
                    project_sync_status="project_state_synced",
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="ITEM_85",
                    project_state_field_name="State",
                    project_state_value_name="ready",
                    safe_stop_reason="follow-up created after codex handoff",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_codex_run_followup",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed current issue after codex continuation and follow-up",
                )

            def fake_sync(*args, **kwargs):
                calls.append(f"sync:{kwargs['lifecycle_stage']}")
                stage = kwargs["lifecycle_stage"]
                log_path = root / f"sync-{stage}.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    sync_status="project_state_synced",
                    lifecycle_stage=stage,
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    issue_snapshot=SimpleNamespace(number=20, url="https://github.com/example/repo/issues/20", title="Current issue"),
                    execution_log_path=log_path,
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="ITEM_20",
                    project_state_field_name="State",
                    project_state_value_name="done" if stage == "done" else "in_progress",
                    safe_stop_reason=f"current issue synced to {stage}",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_in_progress_state": "in_progress",
                    "github_project_done_state": "done",
                    "worker_repo_path": ".",
                },
                execute_codex_run_action_fn=fake_codex_run,
                launch_issue_centric_codex_run_fn=fake_launch,
                execute_followup_issue_action_fn=fake_followup,
                execute_close_current_issue_fn=fake_close,
                execute_current_issue_project_state_sync_fn=fake_sync,
            )

            self.assertEqual(calls, ["trigger", "launch", "sync:in_progress", "followup", "close", "sync:done"])
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_stage"], "done")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_state_value"], "done")

    def test_dispatcher_runs_issue_create_followup_then_close_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                issue_text="# Primary issue\n\nPrimary body\n",
                followup_text="# Follow-up issue\n\nFollow-up body\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_issue_create(*args, **kwargs):
                calls.append("issue_create")
                log_path = root / "issue_create.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    execution_log_path=log_path,
                    created_issue=fake_issue(71),
                    project_sync_status="project_state_synced",
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="ITEM_primary",
                    project_state_field_name="State",
                    project_state_value_name="ready",
                    safe_stop_reason="created primary issue",
                )

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    followup_status="completed",
                    execution_log_path=log_path,
                    created_issue=fake_issue(72),
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    project_sync_status="project_state_synced",
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="ITEM_followup",
                    project_state_field_name="State",
                    project_state_value_name="ready",
                    safe_stop_reason="created follow-up issue",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_issue_create_followup",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed current issue after primary and follow-up",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_issue_create_action_fn=fake_issue_create,
                execute_followup_issue_action_fn=fake_followup,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["issue_create", "followup", "close"])
            self.assertEqual(result.matrix_path, "issue_create_followup_then_close")
            self.assertEqual(
                [step.name for step in result.steps],
                ["issue_create", "followup_issue_create", "close_current_issue"],
            )
            self.assertEqual(result.final_state["last_issue_centric_primary_issue_number"], "71")
            self.assertEqual(result.final_state["last_issue_centric_followup_issue_number"], "72")
            self.assertEqual(result.final_state["last_issue_centric_close_order"], "after_issue_create_followup")

    def test_dispatcher_keeps_primary_success_when_issue_create_followup_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                issue_text="# Primary issue\n\nPrimary body\n",
                followup_text="# Follow-up issue\n\nFollow-up body\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_issue_create(*args, **kwargs):
                calls.append("issue_create")
                log_path = root / "issue_create.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    execution_log_path=log_path,
                    created_issue=fake_issue(71),
                    project_sync_status="issue_only_fallback",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="created primary issue",
                )

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    followup_status="blocked_project_preflight",
                    execution_log_path=log_path,
                    created_issue=None,
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    project_sync_status="blocked_project_preflight",
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="",
                    project_state_field_name="State",
                    project_state_value_name="ready",
                    safe_stop_reason="follow-up blocked before mutation",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_issue_create_action_fn=fake_issue_create,
                execute_followup_issue_action_fn=fake_followup,
            )

            self.assertEqual(calls, ["issue_create", "followup"])
            self.assertEqual(result.matrix_path, "issue_create_followup_then_close")
            self.assertEqual(result.final_status, "partial")
            self.assertEqual(result.final_state["last_issue_centric_primary_issue_number"], "71")
            self.assertEqual(result.final_state["last_issue_centric_followup_status"], "blocked_project_preflight")
            self.assertEqual(result.final_state["last_issue_centric_close_status"], "not_attempted_followup_blocked")

    def test_dispatcher_runs_issue_create_then_close_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                target_issue="#20",
                close_current_issue=True,
                issue_text="# New issue\n\nBody\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_issue_create(*args, **kwargs):
                calls.append("issue_create")
                log_path = root / "issue_create.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    execution_log_path=log_path,
                    created_issue=fake_issue(71),
                    project_sync_status="issue_only_fallback",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="created issue",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_issue_create",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed current issue",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_issue_create_action_fn=fake_issue_create,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["issue_create", "close"])
            self.assertEqual(result.matrix_path, "issue_create_then_close")
            self.assertEqual([step.name for step in result.steps], ["issue_create", "close_current_issue"])
            self.assertEqual(result.final_state["last_issue_centric_closed_issue_number"], "20")

    def test_dispatcher_runs_human_review_then_close_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                target_issue="#20",
                close_current_issue=True,
                review_text="## Review\n\n- OK\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("review")
                log_path = root / "review.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_close_if_review_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(3001, 20),
                    execution_log_path=log_path,
                    safe_stop_reason="review posted",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_human_review",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed after review",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_human_review_action_fn=fake_review,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["review", "close"])
            self.assertEqual(result.matrix_path, "human_review_then_close")
            self.assertEqual(result.final_state["last_issue_centric_review_comment_id"], "3001")
            self.assertEqual(result.final_state["last_issue_centric_close_order"], "after_human_review")

    def test_dispatcher_syncs_current_issue_to_review_after_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                target_issue="#20",
                review_text="## Review\n\n- OK\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("review")
                log_path = root / "review.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="review_only",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(3201, 20),
                    execution_log_path=log_path,
                    safe_stop_reason="review posted",
                )

            def fake_sync(*args, **kwargs):
                calls.append(f"sync:{kwargs['lifecycle_stage']}")
                log_path = root / "review-sync.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    sync_status="project_state_synced",
                    lifecycle_stage=kwargs["lifecycle_stage"],
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    issue_snapshot=SimpleNamespace(number=20, url="https://github.com/example/repo/issues/20", title="Current issue"),
                    execution_log_path=log_path,
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="ITEM_20",
                    project_state_field_name="State",
                    project_state_value_name="review",
                    safe_stop_reason="current issue synced to review",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_review_state": "review",
                    "worker_repo_path": ".",
                },
                execute_human_review_action_fn=fake_review,
                execute_current_issue_project_state_sync_fn=fake_sync,
            )

            self.assertEqual(calls, ["review", "sync:review"])
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_stage"], "review")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_state_value"], "review")

    def test_dispatcher_runs_human_review_followup_then_close_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                review_text="## Review\n\n- Split follow-up\n",
                followup_text="# Follow-up\n\nBody\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("review")
                log_path = root / "review.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_followup_then_close_if_followup_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(3101, 20),
                    execution_log_path=log_path,
                    safe_stop_reason="review posted",
                )

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    followup_status="completed",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=fake_issue(72),
                    execution_log_path=log_path,
                    project_sync_status="issue_only_fallback",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="follow-up created",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_human_review_followup",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed after review and follow-up",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_human_review_action_fn=fake_review,
                execute_followup_issue_action_fn=fake_followup,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["review", "followup", "close"])
            self.assertEqual(result.matrix_path, "human_review_followup_then_close")
            self.assertEqual(
                [step.name for step in result.steps],
                ["human_review_comment", "followup_issue_create", "close_current_issue"],
            )
            self.assertEqual(result.final_state["last_issue_centric_review_comment_id"], "3101")
            self.assertEqual(result.final_state["last_issue_centric_followup_status"], "completed")
            self.assertEqual(result.final_state["last_issue_centric_close_order"], "after_human_review_followup")

    def test_dispatcher_runs_no_action_followup_then_close_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                followup_text="# Follow-up\n\nBody\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    followup_status="completed",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=fake_issue(72),
                    execution_log_path=log_path,
                    project_sync_status="issue_only_fallback",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="follow-up created",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_no_action",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed current issue",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_followup_issue_action_fn=fake_followup,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["followup", "close"])
            self.assertEqual(result.matrix_path, "no_action_followup_then_close")
            self.assertEqual([step.name for step in result.steps], ["followup_issue_create", "close_current_issue"])

    def test_dispatcher_syncs_done_after_no_action_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                target_issue="#20",
                close_current_issue=True,
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_no_action",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed current issue",
                )

            def fake_sync(*args, **kwargs):
                calls.append(f"sync:{kwargs['lifecycle_stage']}")
                log_path = root / "done-sync.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    sync_status="project_state_synced",
                    lifecycle_stage=kwargs["lifecycle_stage"],
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    issue_snapshot=SimpleNamespace(number=20, url="https://github.com/example/repo/issues/20", title="Current issue"),
                    execution_log_path=log_path,
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="ITEM_20",
                    project_state_field_name="State",
                    project_state_value_name="done",
                    safe_stop_reason="current issue synced to done",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                project_config={
                    "github_repository": "example/repo",
                    "github_project_url": "https://github.com/users/example/projects/1",
                    "github_project_state_field_name": "State",
                    "github_project_done_state": "done",
                    "worker_repo_path": ".",
                },
                execute_close_current_issue_fn=fake_close,
                execute_current_issue_project_state_sync_fn=fake_sync,
            )

            self.assertEqual(calls, ["close", "sync:done"])
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_stage"], "done")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_state_value"], "done")

    def test_dispatcher_keeps_review_success_when_review_followup_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                review_text="## Review\n\n- Split follow-up\n",
                followup_text="# Follow-up\n\nBody\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("review")
                log_path = root / "review.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_followup_then_close_if_followup_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(3102, 20),
                    execution_log_path=log_path,
                    safe_stop_reason="review posted",
                )

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    followup_status="blocked_project_preflight",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=None,
                    execution_log_path=log_path,
                    project_sync_status="blocked_project_preflight",
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="",
                    project_state_field_name="State",
                    project_state_value_name="planned",
                    safe_stop_reason="follow-up blocked",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_human_review_action_fn=fake_review,
                execute_followup_issue_action_fn=fake_followup,
                execute_close_current_issue_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("close should not run")),
            )

            self.assertEqual(calls, ["review", "followup"])
            self.assertEqual(result.matrix_path, "human_review_followup_then_close")
            self.assertEqual(result.final_status, "partial")
            self.assertEqual(result.final_state["last_issue_centric_review_status"], "completed")
            self.assertEqual(result.final_state["last_issue_centric_followup_status"], "blocked_project_preflight")
            self.assertEqual(result.final_state["last_issue_centric_close_status"], "not_attempted_followup_blocked")


if __name__ == "__main__":
    unittest.main()
