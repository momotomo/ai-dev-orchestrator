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
    codex_text: str | None = None,
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
        codex_body_base64=(b64(codex_text) if codex_text is not None else None),
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


class IssueCentricMutationSpineDispatchTests(unittest.TestCase):
    """#43: dispatcher-level tests for narrow issue_create and close_current_issue paths.

    These tests verify that the validated contract decision from #42's
    parse_issue_centric_reply is the authoritative entry point for both mutation
    paths.  They cover cases not exercised by the existing dispatcher suite:
    - standalone issue_create (no followup, no close)
    - issue_create with no decoded body at the dispatcher-check level
    - standalone no_action + close_current_issue (explicit no-target-resolved failure)
    - end-to-end integration: raw JSON → parse → materialize → dispatch for both paths
    """

    def _base_state(self) -> dict[str, object]:
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

    def _no_project_sync_fn(self, root: Path):
        def fn(*args, **kwargs):
            p = root / "no-project-sync.json"
            p.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                status="not_requested",
                sync_status="not_requested_no_project",
                lifecycle_stage=kwargs.get("lifecycle_stage", ""),
                resolved_issue=None,
                issue_snapshot=None,
                execution_log_path=p,
                project_url="",
                project_item_id="",
                project_state_field_name="",
                project_state_value_name="",
                safe_stop_reason="No GitHub Project is configured.",
            )

        return fn

    def _dispatch(
        self,
        *,
        decision: issue_centric_contract.IssueCentricDecision,
        root: Path,
        prior_resolved: str = "https://github.com/example/repo/issues/20",
        execute_issue_create_action_fn=None,
        execute_close_current_issue_fn=None,
    ) -> issue_centric_execution.IssueCentricDispatchResult:
        materialized = materialized_from_decision(decision, root=root)
        state = self._base_state()
        state["last_issue_centric_resolved_issue"] = prior_resolved

        def _abort(name: str):
            def fn(*args, **kwargs):
                raise AssertionError(f"{name} should not be called in this test")

            return fn

        return issue_centric_execution.dispatch_issue_centric_execution(
            contract_decision=decision,
            materialized=materialized,
            prior_state={"last_issue_centric_resolved_issue": prior_resolved},
            mutable_state=state,
            project_config={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
            repo_path=REPO_ROOT,
            source_raw_log="logs/raw.txt",
            source_decision_log="logs/decision.md",
            source_metadata_log="logs/metadata.json",
            source_artifact_path="logs/artifact.md",
            log_writer=TempLogWriter(root),
            repo_relative=lambda p: str(p),
            load_state_fn=lambda: dict(state),
            save_state_fn=lambda s: None,
            execute_issue_create_action_fn=execute_issue_create_action_fn or _abort("issue_create"),
            execute_codex_run_action_fn=_abort("codex_run"),
            launch_issue_centric_codex_run_fn=_abort("launch"),
            execute_human_review_action_fn=_abort("human_review"),
            execute_close_current_issue_fn=execute_close_current_issue_fn or _abort("close"),
            execute_followup_issue_action_fn=_abort("followup"),
            execute_current_issue_project_state_sync_fn=self._no_project_sync_fn(root),
            launch_runner=lambda s, argv=None: 0,
        )

    # --- standalone issue_create (no followup, no close) ---

    def test_dispatcher_standalone_issue_create_happy_path(self) -> None:
        """action=issue_create alone routes to execute_issue_create once, matrix_path=issue_create."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                target_issue="#20",
                issue_text="# Ready: new slice\n\nBody text.\n",
            )
            calls: list[str] = []

            def fake_create(*args, **kwargs):
                calls.append("issue_create")
                p = root / "create.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    execution_log_path=p,
                    created_issue=fake_issue(55),
                    project_sync_status="issue_only_fallback",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="created",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_issue_create_action_fn=fake_create,
            )

            self.assertEqual(calls, ["issue_create"])
            self.assertEqual(result.matrix_path, "issue_create")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual([s.name for s in result.steps], ["issue_create"])
            self.assertEqual(result.final_state["last_issue_centric_created_issue_number"], "55")

    def test_dispatcher_issue_create_blocked_when_executor_returns_blocked(self) -> None:
        """When execute_issue_create returns status=blocked, dispatcher records partial/blocked in state."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                target_issue="#20",
                issue_text="# Issue\n\nBody.\n",
            )

            def fake_create_blocked(*args, **kwargs):
                p = root / "create_blocked.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    execution_log_path=p,
                    created_issue=None,
                    project_sync_status="blocked_project_preflight",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="token resolution failed",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_issue_create_action_fn=fake_create_blocked,
            )

            self.assertEqual(result.matrix_path, "issue_create")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual(result.final_state["last_issue_centric_execution_status"], "blocked")
            self.assertEqual(result.final_state["last_issue_centric_created_issue_number"], "")

    def test_dispatcher_issue_create_missing_followup_artifact_is_blocked_at_dispatcher(self) -> None:
        """action=issue_create + create_followup_issue=True, primary issue body present but
        followup body absent → blocked before executor (defensive dispatcher guard).

        This path is not reachable through parse_issue_centric_reply because the contract
        validator enforces the same constraint. This test exercises the dispatcher's own defensive
        check by constructing the materialized state directly.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Build a decision that has issue_body but explicitly NO followup body.
            # We bypass decode_issue_centric_decision to avoid the contract validator.
            import issue_centric_close_current_issue  # noqa: F401
            import issue_centric_transport

            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                target_issue="#20",
                create_followup_issue=True,
                issue_text="# Primary\n\nBody.\n",
                # followup_text intentionally omitted
            )
            # Construct prepared directly to sidestep the full decode validator
            import issue_centric_transport as _transport
            decoded_body = _transport.IssueCentricDecodedBody(
                kind=_transport.IssueCentricArtifactKind.ISSUE_BODY,
                block_name="CHATGPT_ISSUE_BODY",
                raw_base64=b64("# Primary\n\nBody.\n"),
                normalized_base64=b64("# Primary\n\nBody.\n"),
                decoded_text="# Primary\n\nBody.\n",
            )
            prepared = _transport.PreparedIssueCentricDecision(
                decision=decision,
                issue_body=decoded_body,
                codex_body=None,
                review_body=None,
                followup_issue_body=None,  # absent — dispatcher should block
            )
            metadata = root / "metadata.json"
            metadata.write_text("{}", encoding="utf-8")
            artifact = root / "artifact.md"
            artifact.write_text("artifact", encoding="utf-8")
            mat = SimpleNamespace(
                prepared=prepared,
                metadata_log_path=metadata,
                artifact_log_path=artifact,
                safe_stop_reason="prepared",
            )
            state = self._base_state()
            result = issue_centric_execution.dispatch_issue_centric_execution(
                contract_decision=decision,
                materialized=mat,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                mutable_state=state,
                project_config={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
                repo_path=REPO_ROOT,
                source_raw_log="logs/raw.txt",
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/artifact.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda p: str(p),
                load_state_fn=lambda: dict(state),
                save_state_fn=lambda s: None,
                execute_issue_create_action_fn=lambda *a, **kw: (_ for _ in ()).throw(
                    AssertionError("executor should not be called")
                ),
                execute_codex_run_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("codex_run")),
                launch_issue_centric_codex_run_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("launch")),
                execute_human_review_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("review")),
                execute_close_current_issue_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("close")),
                execute_followup_issue_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("followup")),
                execute_current_issue_project_state_sync_fn=self._no_project_sync_fn(root),
                launch_runner=lambda s, argv=None: 0,
            )

            self.assertEqual(result.final_status, "blocked")
            self.assertIn("blocked", result.matrix_path)

    # --- end-to-end: parse_issue_centric_reply → materialize → dispatch ---

    def test_e2e_parse_to_dispatch_issue_create(self) -> None:
        """End-to-end: raw JSON from parse_issue_centric_reply enters issue_create path in dispatcher."""
        issue_text = "# Ready: e2e slice\n\nBody line.\n"
        raw_reply = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                issue_centric_contract.ISSUE_BODY_START,
                b64(issue_text),
                issue_centric_contract.ISSUE_BODY_END,
                issue_centric_contract.DECISION_JSON_START,
                json.dumps(
                    {
                        "action": "issue_create",
                        "target_issue": "#20",
                        "close_current_issue": False,
                        "create_followup_issue": False,
                        "summary": "e2e test",
                    }
                ),
                issue_centric_contract.DECISION_JSON_END,
            ]
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw_reply, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.ISSUE_CREATE)
        self.assertEqual(decision.target_issue, "#20")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: list[str] = []

            def fake_create(*args, **kwargs):
                calls.append("issue_create")
                p = root / "create.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    execution_log_path=p,
                    created_issue=fake_issue(99),
                    project_sync_status="issue_only_fallback",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="created",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_issue_create_action_fn=fake_create,
            )

            self.assertEqual(calls, ["issue_create"])
            self.assertEqual(result.matrix_path, "issue_create")
            self.assertEqual(result.final_state["last_issue_centric_created_issue_number"], "99")

    def test_e2e_parse_to_dispatch_no_action_close(self) -> None:
        """End-to-end: raw JSON no_action + close_current_issue=true enters close path in dispatcher."""
        raw_reply = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                issue_centric_contract.DECISION_JSON_START,
                json.dumps(
                    {
                        "action": "no_action",
                        "target_issue": "#20",
                        "close_current_issue": True,
                        "create_followup_issue": False,
                        "summary": "e2e close test",
                    }
                ),
                issue_centric_contract.DECISION_JSON_END,
            ]
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw_reply, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.NO_ACTION)
        self.assertTrue(decision.close_current_issue)
        self.assertEqual(decision.target_issue, "#20")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: list[str] = []

            def fake_close(*args, **kwargs):
                calls.append("close")
                p = root / "close.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_no_action",
                    execution_log_path=p,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed #20",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["close"])
            self.assertEqual(result.matrix_path, "no_action_close")
            self.assertEqual(result.final_state["last_issue_centric_closed_issue_number"], "20")


class IssueCentricFollowupDispatchIntegrationTests(unittest.TestCase):
    """#44: dispatcher-level tests for the create_followup_issue execution path.

    These tests confirm that the validated contract decision from
    parse_issue_centric_reply is the authoritative entry point for the follow-up
    issue execution path, using the same spine-test pattern as #43.

    Coverage:
    - no_action + create_followup_issue positive path (standalone, no close)
    - no_action + create_followup_issue + close_current_issue positive path
    - blocked executor: follow-up blocked → close not attempted
    - missing follow-up artifact routed to executor → executor returns blocked
    - end-to-end: raw JSON → parse → materialize → dispatch follow-up path
    """

    def _base_state(self) -> dict[str, object]:
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

    def _no_project_sync_fn(self, root: Path):
        def fn(*args, **kwargs):
            p = root / "no-project-sync.json"
            p.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                status="not_requested",
                sync_status="not_requested_no_project",
                lifecycle_stage=kwargs.get("lifecycle_stage", ""),
                resolved_issue=None,
                issue_snapshot=None,
                execution_log_path=p,
                project_url="",
                project_item_id="",
                project_state_field_name="",
                project_state_value_name="",
                safe_stop_reason="No GitHub Project is configured.",
            )

        return fn

    def _dispatch(
        self,
        *,
        decision: issue_centric_contract.IssueCentricDecision,
        root: Path,
        prior_resolved: str = "https://github.com/example/repo/issues/20",
        execute_followup_issue_action_fn=None,
        execute_close_current_issue_fn=None,
    ) -> issue_centric_execution.IssueCentricDispatchResult:
        materialized = materialized_from_decision(decision, root=root)
        state = self._base_state()
        state["last_issue_centric_resolved_issue"] = prior_resolved

        def _abort(name: str):
            def fn(*args, **kwargs):
                raise AssertionError(f"{name} should not be called in this test")

            return fn

        return issue_centric_execution.dispatch_issue_centric_execution(
            contract_decision=decision,
            materialized=materialized,
            prior_state={"last_issue_centric_resolved_issue": prior_resolved},
            mutable_state=state,
            project_config={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
            repo_path=REPO_ROOT,
            source_raw_log="logs/raw.txt",
            source_decision_log="logs/decision.md",
            source_metadata_log="logs/metadata.json",
            source_artifact_path="logs/artifact.md",
            log_writer=TempLogWriter(root),
            repo_relative=lambda p: str(p),
            load_state_fn=lambda: dict(state),
            save_state_fn=lambda s: None,
            execute_issue_create_action_fn=_abort("issue_create"),
            execute_codex_run_action_fn=_abort("codex_run"),
            launch_issue_centric_codex_run_fn=_abort("launch"),
            execute_human_review_action_fn=_abort("human_review"),
            execute_close_current_issue_fn=execute_close_current_issue_fn or _abort("close"),
            execute_followup_issue_action_fn=execute_followup_issue_action_fn or _abort("followup"),
            execute_current_issue_project_state_sync_fn=self._no_project_sync_fn(root),
            launch_runner=lambda s, argv=None: 0,
        )

    def _fake_followup_completed(self, root: Path, issue_number: int = 72):
        def fn(*args, **kwargs):
            p = root / "followup.json"
            p.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                status="completed",
                followup_status="completed",
                parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                created_issue=fake_issue(issue_number),
                execution_log_path=p,
                project_sync_status="issue_only_fallback",
                project_url="",
                project_item_id="",
                project_state_field_name="",
                project_state_value_name="",
                safe_stop_reason=f"created follow-up issue #{issue_number}",
            )

        return fn

    def _fake_followup_blocked(self, root: Path):
        def fn(*args, **kwargs):
            p = root / "followup_blocked.json"
            p.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                status="blocked",
                followup_status="blocked_missing_followup_artifact",
                parent_issue=None,
                created_issue=None,
                execution_log_path=p,
                project_sync_status="",
                project_url="",
                project_item_id="",
                project_state_field_name="",
                project_state_value_name="",
                safe_stop_reason="create_followup_issue=true requires a decoded CHATGPT_FOLLOWUP_ISSUE_BODY artifact.",
            )

        return fn

    # --- no_action + create_followup_issue positive path (no close) ---

    def test_dispatcher_no_action_followup_standalone_happy_path(self) -> None:
        """no_action + create_followup_issue=True (no close) routes to followup executor once.

        Confirms that the validated contract decision from parse_issue_centric_reply
        is the authoritative entry point for the follow-up issue execution path.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                target_issue="#20",
                create_followup_issue=True,
                followup_text="# Follow-up\n\nNext bounded slice.\n",
            )
            calls: list[str] = []

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                return self._fake_followup_completed(root, issue_number=72)(*args, **kwargs)

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_followup_issue_action_fn=fake_followup,
            )

            self.assertEqual(calls, ["followup"])
            self.assertEqual(result.matrix_path, "no_action_followup")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual([s.name for s in result.steps], ["followup_issue_create"])
            self.assertEqual(result.final_state["last_issue_centric_followup_issue_number"], "72")

    # --- no_action + create_followup_issue + close positive path ---

    def test_dispatcher_no_action_followup_then_close_positive_path(self) -> None:
        """no_action + create_followup_issue=True + close=True → followup then close, both succeed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                target_issue="#20",
                create_followup_issue=True,
                close_current_issue=True,
                followup_text="# Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                return self._fake_followup_completed(root, issue_number=80)(*args, **kwargs)

            def fake_close(*args, **kwargs):
                calls.append("close")
                p = root / "close.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_no_action",
                    execution_log_path=p,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed #20",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_followup_issue_action_fn=fake_followup,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["followup", "close"])
            self.assertEqual(result.matrix_path, "no_action_followup_then_close")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(
                [s.name for s in result.steps],
                ["followup_issue_create", "close_current_issue"],
            )
            self.assertEqual(result.final_state["last_issue_centric_followup_issue_number"], "80")
            self.assertEqual(result.final_state["last_issue_centric_closed_issue_number"], "20")

    # --- executor returns blocked ---

    def test_dispatcher_no_action_followup_blocked_when_executor_returns_blocked(self) -> None:
        """When the followup executor returns blocked, final_status=blocked and close is not attempted."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                target_issue="#20",
                create_followup_issue=True,
                followup_text="# Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                return self._fake_followup_blocked(root)(*args, **kwargs)

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_followup_issue_action_fn=fake_followup,
            )

            self.assertEqual(calls, ["followup"])
            self.assertEqual(result.matrix_path, "no_action_followup")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual(result.final_state["last_issue_centric_followup_status"], "blocked_missing_followup_artifact")
            self.assertEqual(result.final_state["last_issue_centric_followup_issue_number"], "")

    # --- close not attempted when followup blocked ---

    def test_dispatcher_no_action_followup_does_not_close_when_followup_blocked(self) -> None:
        """With close=True, if followup is blocked the close step is recorded as not_attempted."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                target_issue="#20",
                create_followup_issue=True,
                close_current_issue=True,
                followup_text="# Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                return self._fake_followup_blocked(root)(*args, **kwargs)

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_followup_issue_action_fn=fake_followup,
                execute_close_current_issue_fn=lambda *a, **kw: (_ for _ in ()).throw(
                    AssertionError("close should not be called when followup is blocked")
                ),
            )

            self.assertEqual(calls, ["followup"])
            self.assertEqual(result.matrix_path, "no_action_followup_then_close")
            self.assertEqual(result.final_status, "partial")
            self.assertEqual(result.final_state["last_issue_centric_close_status"], "not_attempted_followup_blocked")
            close_steps = [s for s in result.steps if s.name == "close_current_issue"]
            self.assertEqual(len(close_steps), 1)
            self.assertEqual(close_steps[0].status, "not_attempted_followup_blocked")

    # --- missing artifact routed to executor (no dispatcher-level guard for no_action path) ---

    def test_dispatcher_no_action_followup_missing_artifact_routes_to_executor(self) -> None:
        """For no_action path, a missing followup_issue_body has no dispatcher-level guard.

        Unlike the issue_create path (which has an explicit dispatcher guard before the executor),
        the no_action followup path routes the call to the executor unconditionally.  The executor
        itself returns blocked when the artifact is absent.

        This state is not reachable through parse_issue_centric_reply because the contract
        validator enforces the same constraint.  We bypass the validator by constructing
        PreparedIssueCentricDecision directly, the same pattern used in #43's
        test_dispatcher_issue_create_missing_followup_artifact_is_blocked_at_dispatcher.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # Build decision with create_followup_issue=True but no followup body.
            # Use NO_ACTION so the issue_create dispatcher guard does not apply.
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                target_issue="#20",
                create_followup_issue=True,
                followup_text="placeholder",  # needed for contract validation pass
            )
            # Override prepared to have followup_issue_body=None, bypassing the validator
            prepared_with_missing_artifact = issue_centric_transport.PreparedIssueCentricDecision(
                decision=decision,
                issue_body=None,
                codex_body=None,
                review_body=None,
                followup_issue_body=None,  # absent — should route to executor, not dispatcher guard
            )
            metadata = root / "metadata.json"
            metadata.write_text("{}", encoding="utf-8")
            artifact = root / "artifact.md"
            artifact.write_text("artifact", encoding="utf-8")
            mat = SimpleNamespace(
                prepared=prepared_with_missing_artifact,
                metadata_log_path=metadata,
                artifact_log_path=artifact,
                safe_stop_reason="prepared",
            )

            executor_called = [False]

            def fake_followup_missing_artifact(*args, **kwargs):
                executor_called[0] = True
                p = root / "followup_no_artifact.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    followup_status="blocked_missing_followup_artifact",
                    parent_issue=None,
                    created_issue=None,
                    execution_log_path=p,
                    project_sync_status="",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="create_followup_issue=true requires a decoded CHATGPT_FOLLOWUP_ISSUE_BODY artifact.",
                )

            state = self._base_state()
            result = issue_centric_execution.dispatch_issue_centric_execution(
                contract_decision=decision,
                materialized=mat,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                mutable_state=state,
                project_config={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
                repo_path=REPO_ROOT,
                source_raw_log="logs/raw.txt",
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/artifact.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda p: str(p),
                load_state_fn=lambda: dict(state),
                save_state_fn=lambda s: None,
                execute_issue_create_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("issue_create")),
                execute_codex_run_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("codex_run")),
                launch_issue_centric_codex_run_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("launch")),
                execute_human_review_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("review")),
                execute_close_current_issue_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("close")),
                execute_followup_issue_action_fn=fake_followup_missing_artifact,
                execute_current_issue_project_state_sync_fn=self._no_project_sync_fn(root),
                launch_runner=lambda s, argv=None: 0,
            )

            # Executor IS called for no_action path (no dispatcher-level guard unlike issue_create path)
            self.assertTrue(executor_called[0], "executor should be called even when artifact is absent for no_action path")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual(result.final_state["last_issue_centric_followup_status"], "blocked_missing_followup_artifact")

    # --- end-to-end: parse_issue_centric_reply → decode → dispatch ---

    def test_e2e_parse_to_dispatch_no_action_followup(self) -> None:
        """End-to-end: raw JSON with CHATGPT_FOLLOWUP_ISSUE_BODY enters the follow-up dispatch path.

        Verifies that the decoded follow-up artifact from parse_issue_centric_reply is
        correctly routed through the dispatcher into the follow-up issue execution path.
        """
        followup_text = "# Follow-up: next slice\n\nBody of follow-up issue.\n"
        raw_reply = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                issue_centric_contract.FOLLOWUP_ISSUE_BODY_START,
                b64(followup_text),
                issue_centric_contract.FOLLOWUP_ISSUE_BODY_END,
                issue_centric_contract.DECISION_JSON_START,
                json.dumps(
                    {
                        "action": "no_action",
                        "target_issue": "#20",
                        "close_current_issue": False,
                        "create_followup_issue": True,
                        "summary": "e2e followup dispatch test",
                    }
                ),
                issue_centric_contract.DECISION_JSON_END,
            ]
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw_reply, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.NO_ACTION)
        self.assertTrue(decision.create_followup_issue)
        self.assertIsNotNone(decision.followup_issue_body_base64)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            followup_artifact_text: list[str] = []
            calls: list[str] = []

            def fake_followup(prepared, **kwargs):
                calls.append("followup")
                # Confirm the decoded artifact text matches the raw input
                if prepared.followup_issue_body is not None:
                    followup_artifact_text.append(prepared.followup_issue_body.decoded_text)
                p = root / "followup_e2e.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    followup_status="completed",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=fake_issue(101),
                    execution_log_path=p,
                    project_sync_status="issue_only_fallback",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="e2e follow-up created",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_followup_issue_action_fn=fake_followup,
            )

            self.assertEqual(calls, ["followup"])
            self.assertEqual(result.matrix_path, "no_action_followup")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_followup_issue_number"], "101")
            # Confirm decoded artifact body was passed through correctly
            self.assertEqual(len(followup_artifact_text), 1)
            self.assertEqual(followup_artifact_text[0], followup_text)


class IssueCentricCodexRunHumanReviewDispatchTests(unittest.TestCase):
    """#45: dispatcher-level tests for codex_run and human_review_needed paths.

    Confirms that the validated contract decision from parse_issue_centric_reply is
    the authoritative entry point for codex_run and human_review_needed, using the
    same spine-test pattern as #43, #44.

    Coverage:
    codex_run:
    - trigger-only positive path (executor returns blocked → codex_run_trigger_only)
    - trigger + launch + continuation positive path (codex_run_launch_and_continuation)
    - codex_run + close (no followup) → blocked_codex_run_close
    - codex_run + create_followup_issue missing codex artifact → blocked at dispatcher
    - end-to-end raw JSON → parse → materialize → dispatch (trigger-only)

    human_review_needed:
    - standalone positive path (human_review)
    - executor returns blocked
    - end-to-end raw JSON → parse → materialize → dispatch
    """

    def _base_state(self) -> dict[str, object]:
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

    def _no_project_sync_fn(self, root: Path):
        def fn(*args, **kwargs):
            p = root / "no-project-sync.json"
            if not p.exists():
                p.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                status="not_requested",
                sync_status="not_requested_no_project",
                lifecycle_stage=kwargs.get("lifecycle_stage", ""),
                resolved_issue=None,
                issue_snapshot=None,
                execution_log_path=p,
                project_url="",
                project_item_id="",
                project_state_field_name="",
                project_state_value_name="",
                safe_stop_reason="No GitHub Project is configured.",
            )

        return fn

    def _dispatch(
        self,
        *,
        decision: issue_centric_contract.IssueCentricDecision,
        root: Path,
        prior_resolved: str = "https://github.com/example/repo/issues/20",
        execute_codex_run_action_fn=None,
        launch_issue_centric_codex_run_fn=None,
        execute_human_review_action_fn=None,
        execute_close_current_issue_fn=None,
    ) -> issue_centric_execution.IssueCentricDispatchResult:
        materialized = materialized_from_decision(decision, root=root)
        state = self._base_state()
        state["last_issue_centric_resolved_issue"] = prior_resolved
        saved: list[dict] = []

        def _abort(name: str):
            def fn(*args, **kwargs):
                raise AssertionError(f"{name} should not be called in this test")

            return fn

        return issue_centric_execution.dispatch_issue_centric_execution(
            contract_decision=decision,
            materialized=materialized,
            prior_state={"last_issue_centric_resolved_issue": prior_resolved},
            mutable_state=state,
            project_config={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
            repo_path=REPO_ROOT,
            source_raw_log="logs/raw.txt",
            source_decision_log="logs/decision.md",
            source_metadata_log="logs/metadata.json",
            source_artifact_path="logs/artifact.md",
            log_writer=TempLogWriter(root),
            repo_relative=lambda p: str(p),
            load_state_fn=lambda: dict(saved[-1]) if saved else dict(state),
            save_state_fn=lambda s: saved.append(dict(s)),
            execute_issue_create_action_fn=_abort("issue_create"),
            execute_codex_run_action_fn=execute_codex_run_action_fn or _abort("codex_run"),
            launch_issue_centric_codex_run_fn=launch_issue_centric_codex_run_fn or _abort("launch"),
            execute_human_review_action_fn=execute_human_review_action_fn or _abort("human_review"),
            execute_close_current_issue_fn=execute_close_current_issue_fn or _abort("close"),
            execute_followup_issue_action_fn=_abort("followup"),
            execute_current_issue_project_state_sync_fn=self._no_project_sync_fn(root),
            launch_runner=lambda s, argv=None: 0,
        )

    # ---- codex_run: executor returns blocked → codex_run_trigger_only ----

    def test_dispatcher_codex_run_trigger_only_when_executor_blocked(self) -> None:
        """codex_run executor returns blocked → matrix_path=codex_run_trigger_only, blocked."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                codex_text="Implement the issue.\n",
            )
            calls: list[str] = []

            def fake_codex_run_blocked(*args, **kwargs):
                calls.append("codex_run")
                p = root / "codex_run_blocked.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    launch_status="blocked",
                    resolved_issue=None,
                    created_comment=None,
                    payload_log_path=None,
                    execution_log_path=p,
                    safe_stop_reason="target issue resolution failed",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_codex_run_action_fn=fake_codex_run_blocked,
            )

            self.assertEqual(calls, ["codex_run"])
            self.assertEqual(result.matrix_path, "codex_run_trigger_only")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual([s.name for s in result.steps], ["codex_trigger_comment"])
            self.assertEqual(result.final_state["last_issue_centric_execution_status"], "blocked")

    # ---- codex_run: trigger + launch + continuation happy path ----

    def test_dispatcher_codex_run_launch_and_continuation_happy_path(self) -> None:
        """Standalone codex_run (no followup, no close): trigger + launch → codex_run_launch_and_continuation."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                codex_text="Implement the issue.\n",
            )
            calls: list[str] = []

            def fake_codex_run(*args, **kwargs):
                calls.append("codex_run")
                p = root / "codex_run.json"
                p.write_text("{}", encoding="utf-8")
                payload = root / "payload.json"
                payload.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    launch_status="waiting_launch",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(1001, 20),
                    payload_log_path=payload,
                    execution_log_path=p,
                    safe_stop_reason="trigger comment registered",
                )

            def fake_launch(*args, **kwargs):
                calls.append("launch")
                launch_log = root / "launch.json"
                launch_log.write_text("{}", encoding="utf-8")
                cont_log = root / "continuation.json"
                cont_log.write_text("{}", encoding="utf-8")
                prompt_log = root / "prompt.json"
                prompt_log.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    launch_status="launched",
                    launch_entrypoint="codex_runner",
                    launch_log_path=launch_log,
                    continuation_status="completed",
                    continuation_log_path=cont_log,
                    prompt_log_path=prompt_log,
                    report_status="ready",
                    report_file="report.md",
                    safe_stop_reason="codex ran and continuation completed",
                    final_mode="codex_running",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_codex_run_action_fn=fake_codex_run,
                launch_issue_centric_codex_run_fn=fake_launch,
            )

            self.assertEqual(calls, ["codex_run", "launch"])
            self.assertEqual(result.matrix_path, "codex_run_launch_and_continuation")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(
                [s.name for s in result.steps],
                ["codex_trigger_comment", "codex_launch_and_continuation"],
            )
            self.assertEqual(result.final_state["last_issue_centric_execution_status"], "completed")
            self.assertEqual(result.final_state["last_issue_centric_trigger_comment_id"], "1001")

    # ---- codex_run: close without followup is blocked at dispatcher ----

    def test_dispatcher_codex_run_close_blocked_without_followup(self) -> None:
        """codex_run + close_current_issue (no followup) → blocked_codex_run_close.

        The dispatcher passes the close call to execute_close_current_issue_fn
        but records the result as blocked regardless of what the executor returns.
        This confirms the currently intended behavior.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=True,
                codex_text="Implement the issue.\n",
            )
            calls: list[str] = []

            def fake_close(*args, **kwargs):
                calls.append("close")
                p = root / "close.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    close_status="blocked",
                    close_order="blocked_codex_run",
                    execution_log_path=p,
                    issue_before=None,
                    issue_after=None,
                    safe_stop_reason="action=codex_run cannot execute close_current_issue in this slice.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["close"])
            self.assertEqual(result.matrix_path, "blocked_codex_run_close")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual([s.name for s in result.steps], ["close_current_issue"])
            self.assertIn("codex_run + close_current_issue", result.stop_message)

    # ---- codex_run: create_followup_issue missing codex artifact blocked at dispatcher ----

    def test_dispatcher_codex_run_missing_codex_artifact_blocked_at_dispatcher(self) -> None:
        """codex_run + create_followup_issue + codex_body=None → blocked_codex_run_followup_missing_codex.

        This state is not reachable through parse_issue_centric_reply (validator requires
        codex_body for codex_run). We bypass the validator using PreparedIssueCentricDecision
        directly, following the same approach as test_dispatcher_issue_create_missing_followup*.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Build a decision with create_followup_issue + codex_body present (passes validator)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                create_followup_issue=True,
                codex_text="Implement the issue.\n",
                followup_text="# Follow-up\n\nBody.\n",
            )
            # Override prepared to have codex_body=None, bypassing the validator
            prepared_no_codex = issue_centric_transport.PreparedIssueCentricDecision(
                decision=decision,
                issue_body=None,
                codex_body=None,  # absent — dispatcher should block before executor
                review_body=None,
                followup_issue_body=issue_centric_transport.IssueCentricDecodedBody(
                    kind=issue_centric_transport.IssueCentricArtifactKind.FOLLOWUP_ISSUE_BODY,
                    block_name="CHATGPT_FOLLOWUP_ISSUE_BODY",
                    raw_base64=b64("# Follow-up\n\nBody.\n"),
                    normalized_base64=b64("# Follow-up\n\nBody.\n"),
                    decoded_text="# Follow-up\n\nBody.\n",
                ),
            )
            metadata = root / "metadata.json"
            metadata.write_text("{}", encoding="utf-8")
            artifact = root / "artifact.md"
            artifact.write_text("artifact", encoding="utf-8")
            mat = SimpleNamespace(
                prepared=prepared_no_codex,
                metadata_log_path=metadata,
                artifact_log_path=artifact,
                safe_stop_reason="prepared",
            )
            state = self._base_state()
            result = issue_centric_execution.dispatch_issue_centric_execution(
                contract_decision=decision,
                materialized=mat,
                prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
                mutable_state=state,
                project_config={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
                repo_path=REPO_ROOT,
                source_raw_log="logs/raw.txt",
                source_decision_log="logs/decision.md",
                source_metadata_log="logs/metadata.json",
                source_artifact_path="logs/artifact.md",
                log_writer=TempLogWriter(root),
                repo_relative=lambda p: str(p),
                load_state_fn=lambda: dict(state),
                save_state_fn=lambda s: None,
                execute_issue_create_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("issue_create")),
                execute_codex_run_action_fn=lambda *a, **kw: (_ for _ in ()).throw(
                    AssertionError("executor should not be called when codex artifact is absent")
                ),
                launch_issue_centric_codex_run_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("launch")),
                execute_human_review_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("review")),
                execute_close_current_issue_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("close")),
                execute_followup_issue_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("followup")),
                execute_current_issue_project_state_sync_fn=self._no_project_sync_fn(root),
                launch_runner=lambda s, argv=None: 0,
            )

            self.assertEqual(result.matrix_path, "blocked_codex_run_followup_missing_codex")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual(result.final_state["last_issue_centric_execution_status"], "blocked_missing_codex_artifact")

    # ---- codex_run: end-to-end raw JSON → parse → dispatch ----

    def test_e2e_parse_to_dispatch_codex_run(self) -> None:
        """End-to-end: raw reply with CHATGPT_CODEX_BODY enters codex_run trigger path.

        Executor returns blocked so the test remains fast (no real Codex launch needed).
        Confirms the decoded codex artifact is passed through the dispatcher correctly.
        """
        codex_text = "Implement the feature described in issue #20.\n"
        raw_reply = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                issue_centric_contract.CODEX_BODY_START,
                b64(codex_text),
                issue_centric_contract.CODEX_BODY_END,
                issue_centric_contract.DECISION_JSON_START,
                json.dumps(
                    {
                        "action": "codex_run",
                        "target_issue": "#20",
                        "close_current_issue": False,
                        "create_followup_issue": False,
                        "summary": "e2e codex_run dispatch test",
                    }
                ),
                issue_centric_contract.DECISION_JSON_END,
            ]
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw_reply, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.CODEX_RUN)
        self.assertEqual(decision.target_issue, "#20")
        self.assertIsNotNone(decision.codex_body_base64)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_artifact_text: list[str] = []
            calls: list[str] = []

            def fake_codex_run(prepared, **kwargs):
                calls.append("codex_run")
                if prepared.codex_body is not None:
                    codex_artifact_text.append(prepared.codex_body.decoded_text)
                p = root / "codex_run_e2e.json"
                p.write_text("{}", encoding="utf-8")
                # Return blocked so the test doesn't need a launch mock
                return SimpleNamespace(
                    status="blocked",
                    launch_status="blocked",
                    resolved_issue=None,
                    created_comment=None,
                    payload_log_path=None,
                    execution_log_path=p,
                    safe_stop_reason="stopped at trigger for e2e test",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_codex_run_action_fn=fake_codex_run,
            )

            self.assertEqual(calls, ["codex_run"])
            self.assertEqual(result.matrix_path, "codex_run_trigger_only")
            self.assertEqual(result.final_status, "blocked")
            # Confirm the decoded codex body was passed through correctly
            self.assertEqual(len(codex_artifact_text), 1)
            self.assertEqual(codex_artifact_text[0], codex_text)

    # ---- human_review_needed: standalone positive path ----

    def test_dispatcher_human_review_standalone_happy_path(self) -> None:
        """Standalone human_review_needed (no followup, no close) → human_review, completed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                target_issue="#20",
                review_text="## Review\n\n- Everything looks good.\n",
            )
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("human_review")
                p = root / "review.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_close_if_review_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(4001, 20),
                    execution_log_path=p,
                    safe_stop_reason="review comment posted",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_human_review_action_fn=fake_review,
            )

            self.assertEqual(calls, ["human_review"])
            self.assertEqual(result.matrix_path, "human_review")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual([s.name for s in result.steps], ["human_review_comment"])
            self.assertEqual(result.final_state["last_issue_centric_review_status"], "completed")
            self.assertEqual(result.final_state["last_issue_centric_review_comment_id"], "4001")

    # ---- human_review_needed: executor returns blocked ----

    def test_dispatcher_human_review_blocked_when_executor_returns_blocked(self) -> None:
        """When the review executor returns blocked, final_status=blocked."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                target_issue="#20",
                review_text="## Review\n\n- Blocked.\n",
            )
            calls: list[str] = []

            def fake_review_blocked(*args, **kwargs):
                calls.append("human_review")
                p = root / "review_blocked.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    review_status="blocked_resolve_failed",
                    close_policy="no_close",
                    resolved_issue=None,
                    created_comment=None,
                    execution_log_path=p,
                    safe_stop_reason="target issue resolution failed",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_human_review_action_fn=fake_review_blocked,
            )

            self.assertEqual(calls, ["human_review"])
            self.assertEqual(result.matrix_path, "human_review")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual(result.final_state["last_issue_centric_review_status"], "blocked_resolve_failed")
            self.assertEqual(result.final_state["last_issue_centric_review_comment_id"], "")

    # ---- human_review_needed: end-to-end raw JSON → parse → dispatch ----

    def test_e2e_parse_to_dispatch_human_review_needed(self) -> None:
        """End-to-end: raw reply with CHATGPT_REVIEW enters human_review_needed path."""
        review_text = "## Review Comment\n\n- Implementation looks correct.\n"
        raw_reply = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                issue_centric_contract.REVIEW_BODY_START,
                b64(review_text),
                issue_centric_contract.REVIEW_BODY_END,
                issue_centric_contract.DECISION_JSON_START,
                json.dumps(
                    {
                        "action": "human_review_needed",
                        "target_issue": "#20",
                        "close_current_issue": False,
                        "create_followup_issue": False,
                        "summary": "e2e human_review_needed dispatch test",
                    }
                ),
                issue_centric_contract.DECISION_JSON_END,
            ]
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw_reply, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED)
        self.assertEqual(decision.target_issue, "#20")
        self.assertIsNotNone(decision.review_base64)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_artifact_text: list[str] = []
            calls: list[str] = []

            def fake_review(prepared, **kwargs):
                calls.append("human_review")
                if prepared.review_body is not None:
                    review_artifact_text.append(prepared.review_body.decoded_text)
                p = root / "review_e2e.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_close_if_review_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(5001, 20),
                    execution_log_path=p,
                    safe_stop_reason="e2e review posted",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_human_review_action_fn=fake_review,
            )

            self.assertEqual(calls, ["human_review"])
            self.assertEqual(result.matrix_path, "human_review")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_review_comment_id"], "5001")
            # Confirm decoded review body was passed through correctly
            self.assertEqual(len(review_artifact_text), 1)
            self.assertEqual(review_artifact_text[0], review_text)


class IssueCentricComboMatrixDispatchTests(unittest.TestCase):
    """#46: combo-matrix and close-policy coverage across dispatcher paths.

    Confirms under-specified cross-path combinations and state field recording
    that lacked explicit coverage after #45:

    - codex_run + close (no followup): blocked_codex_run_close state fields recorded
    - human_review_needed + create_followup_issue + missing review body: blocked at dispatcher
    - human_review_needed + create_followup_issue + missing followup body: blocked at dispatcher
    - codex_run + create_followup_issue + close (tri-flag): trigger-only matrix path when executor blocked
    - no_action + close_current_issue: routes to no_action_close
    - close_policy field recorded in state after human_review
    """

    def _base_state(self) -> dict[str, object]:
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

    def _no_project_sync_fn(self, root: Path):
        def fn(*args, **kwargs):
            p = root / "no-project-sync.json"
            if not p.exists():
                p.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                status="not_requested",
                sync_status="not_requested_no_project",
                lifecycle_stage=kwargs.get("lifecycle_stage", ""),
                resolved_issue=None,
                issue_snapshot=None,
                execution_log_path=p,
                project_url="",
                project_item_id="",
                project_state_field_name="",
                project_state_value_name="",
                safe_stop_reason="No GitHub Project is configured.",
            )

        return fn

    def _dispatch(
        self,
        *,
        decision: issue_centric_contract.IssueCentricDecision,
        root: Path,
        prior_resolved: str = "https://github.com/example/repo/issues/20",
        execute_codex_run_action_fn=None,
        launch_issue_centric_codex_run_fn=None,
        execute_human_review_action_fn=None,
        execute_close_current_issue_fn=None,
        execute_parent_issue_update_fn=None,
        execute_followup_issue_action_fn=None,
        materialized_override=None,
    ) -> issue_centric_execution.IssueCentricDispatchResult:
        if materialized_override is not None:
            mat = materialized_override
        else:
            mat = materialized_from_decision(decision, root=root)
        state = self._base_state()
        state["last_issue_centric_resolved_issue"] = prior_resolved
        saved: list[dict] = []

        def _abort(name: str):
            def fn(*args, **kwargs):
                raise AssertionError(f"{name} should not be called in this test")

            return fn

        return issue_centric_execution.dispatch_issue_centric_execution(
            contract_decision=decision,
            materialized=mat,
            prior_state={"last_issue_centric_resolved_issue": prior_resolved},
            mutable_state=state,
            project_config={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
            repo_path=REPO_ROOT,
            source_raw_log="logs/raw.txt",
            source_decision_log="logs/decision.md",
            source_metadata_log="logs/metadata.json",
            source_artifact_path="logs/artifact.md",
            log_writer=TempLogWriter(root),
            repo_relative=lambda p: str(p),
            load_state_fn=lambda: dict(saved[-1]) if saved else dict(state),
            save_state_fn=lambda s: saved.append(dict(s)),
            execute_issue_create_action_fn=_abort("issue_create"),
            execute_codex_run_action_fn=execute_codex_run_action_fn or _abort("codex_run"),
            launch_issue_centric_codex_run_fn=launch_issue_centric_codex_run_fn or _abort("launch"),
            execute_human_review_action_fn=execute_human_review_action_fn or _abort("human_review"),
            execute_close_current_issue_fn=execute_close_current_issue_fn or _abort("close"),
            execute_followup_issue_action_fn=execute_followup_issue_action_fn or _abort("followup"),
            execute_current_issue_project_state_sync_fn=self._no_project_sync_fn(root),
            launch_runner=lambda s, argv=None: 0,
            execute_parent_issue_update_fn=execute_parent_issue_update_fn,
        )

    # ---- codex_run + close (no followup): state fields recorded ----

    def test_combo_codex_run_close_state_fields_recorded(self) -> None:
        """codex_run + close_current_issue (no followup) → blocked_codex_run_close.

        The dispatcher calls execute_close_current_issue_fn and applies its result to
        mutable_state before finalizing as blocked. Confirms close_status and close_order
        fields are recorded from the executor response.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=True,
                codex_text="Implement the issue.\n",
            )

            def fake_close(*args, **kwargs):
                p = root / "close_combo.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    close_status="blocked_codex_run",
                    close_order="blocked_codex_run",
                    execution_log_path=p,
                    issue_before=None,
                    issue_after=None,
                    safe_stop_reason="codex_run cannot close in this slice.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(result.matrix_path, "blocked_codex_run_close")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual(result.final_state["last_issue_centric_close_status"], "blocked_codex_run")
            self.assertEqual(result.final_state["last_issue_centric_close_order"], "blocked_codex_run")

    # ---- human_review_needed + create_followup_issue: missing review body guard ----

    def test_combo_human_review_followup_missing_review_blocked_at_dispatcher(self) -> None:
        """human_review_needed + create_followup_issue + review_body=None → blocked before executor.

        The dispatcher guards that a prepared CHATGPT_REVIEW artifact is present before
        entering the human_review + followup combo path. With review_text=None (no review
        artifact in the reply) the dispatcher must block without calling the executor.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # human_review_needed + create_followup_issue=True — review artifact absent (review_text=None)
            # followup artifact present (required by contract when create_followup_issue=True)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                create_followup_issue=True,
                review_text=None,
                followup_text="## Follow-up\n\nBody.\n",
            )

            result = self._dispatch(
                decision=decision,
                root=root,
                # no executor fns provided — dispatcher must block before reaching any executor
            )

            self.assertEqual(result.matrix_path, "blocked_human_review_followup_missing_review")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual(
                result.final_state["last_issue_centric_review_status"],
                "blocked_missing_review_artifact",
            )

    # ---- human_review_needed + create_followup_issue: missing followup body guard ----

    def test_combo_human_review_followup_missing_followup_body_blocked_at_dispatcher(self) -> None:
        """human_review_needed + create_followup_issue + followup_body=None → blocked before executor.

        When the review artifact is present but the follow-up body is absent (bypassing the
        contract validator via PreparedIssueCentricDecision), the dispatcher blocks before
        calling any executor fn.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Build a valid decision that would normally pass the validator
            # (review + followup both present) then override prepared to have followup_body=None
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                create_followup_issue=True,
                review_text="## Review notes\n\nLooks good.\n",
                followup_text="## Follow-up\n\nBody.\n",
            )
            review_decoded = issue_centric_transport.IssueCentricDecodedBody(
                kind=issue_centric_transport.IssueCentricArtifactKind.REVIEW,
                block_name="CHATGPT_REVIEW",
                raw_base64=b64("## Review notes\n\nLooks good.\n"),
                normalized_base64=b64("## Review notes\n\nLooks good.\n"),
                decoded_text="## Review notes\n\nLooks good.\n",
            )
            prepared_no_followup = issue_centric_transport.PreparedIssueCentricDecision(
                decision=decision,
                issue_body=None,
                codex_body=None,
                review_body=review_decoded,
                followup_issue_body=None,  # absent — dispatcher must block before executor
            )
            metadata = root / "metadata.json"
            metadata.write_text("{}", encoding="utf-8")
            artifact = root / "artifact.md"
            artifact.write_text("artifact", encoding="utf-8")
            mat = SimpleNamespace(
                prepared=prepared_no_followup,
                metadata_log_path=metadata,
                artifact_log_path=artifact,
                safe_stop_reason="prepared",
            )

            result = self._dispatch(
                decision=decision,
                root=root,
                materialized_override=mat,
                # no executor fns — dispatcher must block before reaching any executor
            )

            self.assertEqual(result.matrix_path, "blocked_human_review_followup_missing_followup")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual(
                result.final_state["last_issue_centric_followup_status"],
                "blocked_missing_followup_artifact",
            )

    # ---- codex_run + create_followup_issue + close tri-flag: trigger-only when executor blocked ----

    def test_combo_codex_run_followup_close_tri_flag_trigger_only_when_blocked(self) -> None:
        """codex_run + create_followup_issue + close_current_issue + executor blocked → codex_run_trigger_only.

        With all three flags set but the codex trigger executor returning blocked (no Codex
        launch), the dispatcher must route to codex_run_trigger_only and record
        close_status as not_attempted_trigger_blocked.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                codex_text="Implement the issue.\n",
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_codex_blocked(*args, **kwargs):
                calls.append("codex_run")
                p = root / "codex_trigger_blocked.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    launch_status="blocked",
                    resolved_issue=None,
                    execution_log_path=p,
                    payload_log_path=None,
                    created_comment=None,
                    safe_stop_reason="trigger blocked for test.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_codex_run_action_fn=fake_codex_blocked,
            )

            self.assertEqual(calls, ["codex_run"])
            self.assertEqual(result.matrix_path, "codex_run_trigger_only")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual(
                result.final_state["last_issue_centric_close_status"],
                "not_attempted_trigger_blocked",
            )
            self.assertEqual(
                result.final_state["last_issue_centric_close_order"],
                "after_codex_run_followup",
            )

    # ---- no_action + close_current_issue: routes to no_action_close ----

    def test_combo_no_action_close_standalone_routes_to_no_action_close(self) -> None:
        """no_action + close_current_issue → matrix_path=no_action_close.

        The dispatcher must call execute_close_current_issue_fn and route the result
        to the no_action_close path without entering any other executor.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                close_current_issue=True,
            )
            calls: list[str] = []

            def fake_close(*args, **kwargs):
                calls.append("close")
                p = root / "close_no_action.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="completed",
                    close_order="after_no_action",
                    execution_log_path=p,
                    issue_before=fake_issue(20, state="open"),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed issue #20.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["close"])
            self.assertEqual(result.matrix_path, "no_action_close")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_close_status"], "completed")
            self.assertEqual(result.final_state["last_issue_centric_closed_issue_number"], "20")

    def test_combo_no_action_close_runs_parent_update_after_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                close_current_issue=True,
            )
            calls: list[str] = []

            def fake_close(*args, **kwargs):
                calls.append("close")
                p = root / "close_no_action_parent.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="completed",
                    close_order="after_no_action",
                    execution_log_path=p,
                    resolved_issue=SimpleNamespace(
                        issue_number=20,
                        issue_url="https://github.com/example/repo/issues/20",
                    ),
                    issue_before=SimpleNamespace(
                        number=20,
                        url="https://github.com/example/repo/issues/20",
                        title="Ready child",
                        state="open",
                        body="Parent: #1\n",
                    ),
                    issue_after=SimpleNamespace(
                        number=20,
                        url="https://github.com/example/repo/issues/20",
                        title="Ready child",
                        state="closed",
                        body="Parent: #1\n",
                    ),
                    safe_stop_reason="closed issue #20.",
                )

            def fake_parent_update(*args, **kwargs):
                calls.append("parent")
                p = root / "parent_update.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    update_status="comment_created",
                    resolved_parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/1"),
                    created_comment=SimpleNamespace(
                        comment_id=901,
                        url="https://github.com/example/repo/issues/1#issuecomment-901",
                    ),
                    closed_issue_url="https://github.com/example/repo/issues/20",
                    execution_log_path=p,
                    safe_stop_reason="updated parent #1.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_close_current_issue_fn=fake_close,
                execute_parent_issue_update_fn=fake_parent_update,
            )

            self.assertEqual(calls, ["close", "parent"])
            self.assertEqual(result.matrix_path, "no_action_close")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_parent_update_status"], "comment_created")
            self.assertEqual(
                result.final_state["last_issue_centric_parent_update_issue"],
                "https://github.com/example/repo/issues/1",
            )
            self.assertEqual(
                result.final_state["last_issue_centric_parent_update_comment_url"],
                "https://github.com/example/repo/issues/1#issuecomment-901",
            )

    # ---- close_policy field recorded in state after human_review ----

    def test_close_policy_field_recorded_after_human_review(self) -> None:
        """human_review completed → last_issue_centric_review_close_policy recorded in state.

        Confirms that _apply_review_execution_state writes the close_policy value from the
        executor response into mutable_state, making it available for downstream logic.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                target_issue="#20",
                review_text="## Review\n\nApproved.\n",
            )
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("human_review")
                p = root / "review_close_policy.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_close_if_review_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(9001, 20),
                    execution_log_path=p,
                    safe_stop_reason="review comment posted.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_human_review_action_fn=fake_review,
            )

            self.assertEqual(calls, ["human_review"])
            self.assertEqual(result.matrix_path, "human_review")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(
                result.final_state["last_issue_centric_review_close_policy"],
                "after_review_close_if_review_succeeds",
            )
            self.assertEqual(result.final_state["last_issue_centric_review_comment_id"], "9001")


class IssueCentricLifecycleSyncIntegrationTests(unittest.TestCase):
    """#48: lifecycle sync and project-state automation after issue-centric execution.

    Tests the followup_created lifecycle sync stage that was wired into:
    - no_action + create_followup_issue (no close)
    - human_review_needed + create_followup_issue (no close)
    - issue_create + create_followup_issue (no close)

    Also covers:
    - success path with project sync available
    - no-project safe fallback (sync returns not_requested, main path stays completed)
    - sync failure recording (final_status downgrades to partial)
    - review-completed path state fields
    - close-completed path state fields
    - regression: paths with close still reach done sync first
    """

    def _base_state(self) -> dict[str, object]:
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

    def _make_sync_fn(self, root: Path, calls: list[str], *, status: str = "completed") -> object:
        """Return a project-state sync fn that records its lifecycle_stage call and returns status."""

        def fn(*args, **kwargs):
            stage = kwargs.get("lifecycle_stage", "unknown")
            calls.append(f"sync:{stage}")
            p = root / f"sync_{stage}.json"
            if not p.exists():
                p.write_text("{}", encoding="utf-8")
            if status == "completed":
                return SimpleNamespace(
                    status="completed",
                    sync_status="project_state_synced",
                    lifecycle_stage=stage,
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    issue_snapshot=None,
                    execution_log_path=p,
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="PVTI_example",
                    project_state_field_name="Status",
                    project_state_value_name=stage,
                    safe_stop_reason=f"synced to {stage}",
                )
            elif status == "not_requested":
                return SimpleNamespace(
                    status="not_requested",
                    sync_status="not_requested_no_project",
                    lifecycle_stage=stage,
                    resolved_issue=None,
                    issue_snapshot=None,
                    execution_log_path=p,
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="No GitHub Project is configured.",
                )
            else:  # "blocked" / any failure status
                return SimpleNamespace(
                    status="blocked",
                    sync_status="blocked_project_preflight",
                    lifecycle_stage=stage,
                    resolved_issue=None,
                    issue_snapshot=None,
                    execution_log_path=p,
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason=f"project sync failed for stage {stage}",
                )

        return fn

    def _make_followup_fn(self, root: Path, calls: list[str]) -> object:
        """Return a followup executor that creates a fake follow-up issue."""

        def fn(*args, **kwargs):
            calls.append("followup")
            p = root / "followup.json"
            p.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                status="completed",
                followup_status="completed",
                execution_log_path=p,
                parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                project_item_id="",
                project_url="",
                project_sync_status="",
                project_state_field_name="",
                project_state_value_name="",
                created_issue=fake_issue(99),
                safe_stop_reason="created follow-up issue #99",
            )

        return fn

    def _dispatch(
        self,
        *,
        decision: issue_centric_contract.IssueCentricDecision,
        root: Path,
        prior_resolved: str = "https://github.com/example/repo/issues/20",
        execute_issue_create_action_fn=None,
        execute_human_review_action_fn=None,
        execute_close_current_issue_fn=None,
        execute_followup_issue_action_fn=None,
        execute_current_issue_project_state_sync_fn=None,
    ) -> issue_centric_execution.IssueCentricDispatchResult:
        mat = materialized_from_decision(decision, root=root)
        state = self._base_state()
        state["last_issue_centric_resolved_issue"] = prior_resolved
        saved: list[dict] = []

        def _abort(name: str):
            def fn(*args, **kwargs):
                raise AssertionError(f"{name} should not be called in this test")

            return fn

        def _no_project_sync(*args, **kwargs):
            stage = kwargs.get("lifecycle_stage", "unknown")
            p = root / f"no_sync_{stage}.json"
            if not p.exists():
                p.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                status="not_requested",
                sync_status="not_requested_no_project",
                lifecycle_stage=stage,
                resolved_issue=None,
                issue_snapshot=None,
                execution_log_path=p,
                project_url="",
                project_item_id="",
                project_state_field_name="",
                project_state_value_name="",
                safe_stop_reason="No GitHub Project is configured.",
            )

        return issue_centric_execution.dispatch_issue_centric_execution(
            contract_decision=decision,
            materialized=mat,
            prior_state={"last_issue_centric_resolved_issue": prior_resolved},
            mutable_state=state,
            project_config={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
            repo_path=REPO_ROOT,
            source_raw_log="logs/raw.txt",
            source_decision_log="logs/decision.md",
            source_metadata_log="logs/metadata.json",
            source_artifact_path="logs/artifact.md",
            log_writer=TempLogWriter(root),
            repo_relative=lambda p: str(p),
            load_state_fn=lambda: dict(saved[-1]) if saved else dict(state),
            save_state_fn=lambda s: saved.append(dict(s)),
            execute_issue_create_action_fn=execute_issue_create_action_fn or _abort("issue_create"),
            execute_codex_run_action_fn=_abort("codex_run"),
            launch_issue_centric_codex_run_fn=_abort("launch"),
            execute_human_review_action_fn=execute_human_review_action_fn or _abort("human_review"),
            execute_close_current_issue_fn=execute_close_current_issue_fn or _abort("close"),
            execute_followup_issue_action_fn=execute_followup_issue_action_fn or _abort("followup"),
            execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn or _no_project_sync,
            launch_runner=lambda s, argv=None: 0,
        )

    # ---- no_action + followup: followup_created lifecycle sync ----

    def test_no_action_followup_calls_followup_created_sync_on_success(self) -> None:
        """no_action + create_followup_issue → follows followup_created lifecycle sync step."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                create_followup_issue=True,
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []
            result = self._dispatch(
                decision=decision,
                root=root,
                execute_followup_issue_action_fn=self._make_followup_fn(root, calls),
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls),
            )

            self.assertIn("followup", calls)
            self.assertIn("sync:followup_created", calls)
            self.assertEqual(calls, ["followup", "sync:followup_created"])
            self.assertEqual(result.matrix_path, "no_action_followup")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_stage"], "followup_created")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "project_state_synced")
            self.assertIn("current_issue_project_state_sync_followup_created", [s.name for s in result.steps])

    def test_no_action_followup_sync_not_requested_safe_fallback(self) -> None:
        """no_action + followup: when no project configured, sync returns not_requested and final_status stays completed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                create_followup_issue=True,
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []
            result = self._dispatch(
                decision=decision,
                root=root,
                execute_followup_issue_action_fn=self._make_followup_fn(root, calls),
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls, status="not_requested"),
            )

            self.assertEqual(calls, ["followup", "sync:followup_created"])
            self.assertEqual(result.matrix_path, "no_action_followup")
            # not_requested must NOT degrade the main execution
            self.assertEqual(result.final_status, "completed")
            # lifecycle sync state not touched when not_requested (early return in _apply_current_issue_project_state_sync_state)
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "")

    def test_no_action_followup_sync_failure_records_partial(self) -> None:
        """no_action + followup: sync failure → final_status=partial, sync status recorded."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                create_followup_issue=True,
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []
            result = self._dispatch(
                decision=decision,
                root=root,
                execute_followup_issue_action_fn=self._make_followup_fn(root, calls),
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls, status="blocked"),
            )

            self.assertEqual(calls, ["followup", "sync:followup_created"])
            self.assertEqual(result.matrix_path, "no_action_followup")
            # sync failure must degrade final_status
            self.assertEqual(result.final_status, "partial")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "blocked_project_preflight")

    def test_no_action_followup_then_close_calls_done_sync_not_followup_created(self) -> None:
        """no_action + followup + close: only done sync is called (not followup_created).

        Regression: when close_current_issue=True, the followup_created sync must NOT fire.
        The close path already calls done sync.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                create_followup_issue=True,
                close_current_issue=True,
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_close(*args, **kwargs):
                calls.append("close")
                p = root / "close.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="completed",
                    close_order="after_no_action_followup",
                    execution_log_path=p,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed issue #20.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_followup_issue_action_fn=self._make_followup_fn(root, calls),
                execute_close_current_issue_fn=fake_close,
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls),
            )

            self.assertIn("close", calls)
            self.assertIn("sync:done", calls)
            # followup_created sync must NOT be called when close is present
            self.assertNotIn("sync:followup_created", calls)
            self.assertEqual(result.matrix_path, "no_action_followup_then_close")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_stage"], "done")

    # ---- human_review + followup: followup_created lifecycle sync ----

    def test_human_review_followup_calls_review_then_followup_created_sync(self) -> None:
        """human_review_needed + create_followup_issue → review sync then followup_created sync."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                create_followup_issue=True,
                review_text="## Review\n\nApproved.\n",
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("review")
                p = root / "review.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_close_if_review_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(7001, 20),
                    execution_log_path=p,
                    safe_stop_reason="review posted.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_human_review_action_fn=fake_review,
                execute_followup_issue_action_fn=self._make_followup_fn(root, calls),
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls),
            )

            self.assertEqual(calls, ["review", "sync:review", "followup", "sync:followup_created"])
            self.assertEqual(result.matrix_path, "human_review_followup")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_stage"], "followup_created")
            self.assertIn("current_issue_project_state_sync_followup_created", [s.name for s in result.steps])

    def test_human_review_followup_sync_not_requested_safe_fallback(self) -> None:
        """human_review + followup: no-project fallback keeps final_status=completed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                create_followup_issue=True,
                review_text="## Review\n\nApproved.\n",
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("review")
                p = root / "review.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_close_if_review_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(7002, 20),
                    execution_log_path=p,
                    safe_stop_reason="review posted.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_human_review_action_fn=fake_review,
                execute_followup_issue_action_fn=self._make_followup_fn(root, calls),
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls, status="not_requested"),
            )

            # Both review and followup syncs are called but return not_requested
            self.assertEqual(calls, ["review", "sync:review", "followup", "sync:followup_created"])
            self.assertEqual(result.final_status, "completed")
            # not_requested does not write to lifecycle sync state
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "")

    def test_human_review_followup_sync_failure_records_partial(self) -> None:
        """human_review + followup: sync failure at followup_created → final_status=partial."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                create_followup_issue=True,
                review_text="## Review\n\nApproved.\n",
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("review")
                p = root / "review.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_close_if_review_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(7003, 20),
                    execution_log_path=p,
                    safe_stop_reason="review posted.",
                )

            call_count = [0]

            def sync_fn_mixed(*args, **kwargs):
                stage = kwargs.get("lifecycle_stage", "unknown")
                call_count[0] += 1
                calls.append(f"sync:{stage}")
                p = root / f"sync_{stage}_{call_count[0]}.json"
                p.write_text("{}", encoding="utf-8")
                if stage == "review":
                    return SimpleNamespace(
                        status="completed",
                        sync_status="project_state_synced",
                        lifecycle_stage=stage,
                        resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                        issue_snapshot=None,
                        execution_log_path=p,
                        project_url="https://github.com/users/example/projects/1",
                        project_item_id="PVTI_example",
                        project_state_field_name="Status",
                        project_state_value_name=stage,
                        safe_stop_reason=f"synced to {stage}",
                    )
                else:
                    return SimpleNamespace(
                        status="blocked",
                        sync_status="blocked_project_preflight",
                        lifecycle_stage=stage,
                        resolved_issue=None,
                        issue_snapshot=None,
                        execution_log_path=p,
                        project_url="",
                        project_item_id="",
                        project_state_field_name="",
                        project_state_value_name="",
                        safe_stop_reason=f"sync blocked for {stage}",
                    )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_human_review_action_fn=fake_review,
                execute_followup_issue_action_fn=self._make_followup_fn(root, calls),
                execute_current_issue_project_state_sync_fn=sync_fn_mixed,
            )

            self.assertEqual(calls, ["review", "sync:review", "followup", "sync:followup_created"])
            self.assertEqual(result.matrix_path, "human_review_followup")
            # followup sync failed → partial
            self.assertEqual(result.final_status, "partial")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "blocked_project_preflight")

    # ---- issue_create + followup: followup_created lifecycle sync ----

    def test_issue_create_followup_calls_followup_created_sync(self) -> None:
        """issue_create + create_followup_issue (no close) → followup_created sync called."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                create_followup_issue=True,
                issue_text="## New Issue\n\nBody.\n",
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_issue_create(*args, **kwargs):
                calls.append("issue_create")
                p = root / "issue_create.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    execution_log_path=p,
                    project_item_id="PVTI_new",
                    project_sync_status="project_state_synced",
                    project_url="https://github.com/users/example/projects/1",
                    project_state_field_name="Status",
                    project_state_value_name="ready",
                    created_issue=fake_issue(101),
                    safe_stop_reason="created issue #101",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_issue_create_action_fn=fake_issue_create,
                execute_followup_issue_action_fn=self._make_followup_fn(root, calls),
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls),
            )

            self.assertIn("issue_create", calls)
            self.assertIn("followup", calls)
            self.assertIn("sync:followup_created", calls)
            self.assertEqual(calls, ["issue_create", "followup", "sync:followup_created"])
            self.assertEqual(result.matrix_path, "issue_create_followup")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_stage"], "followup_created")

    def test_issue_create_followup_sync_not_requested_safe_fallback(self) -> None:
        """issue_create + followup: no-project fallback keeps final_status=completed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                create_followup_issue=True,
                issue_text="## New Issue\n\nBody.\n",
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_issue_create(*args, **kwargs):
                calls.append("issue_create")
                p = root / "issue_create.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    execution_log_path=p,
                    project_item_id="",
                    project_sync_status="",
                    project_url="",
                    project_state_field_name="",
                    project_state_value_name="",
                    created_issue=fake_issue(102),
                    safe_stop_reason="created issue #102",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_issue_create_action_fn=fake_issue_create,
                execute_followup_issue_action_fn=self._make_followup_fn(root, calls),
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls, status="not_requested"),
            )

            self.assertEqual(calls, ["issue_create", "followup", "sync:followup_created"])
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "")

    def test_issue_create_followup_sync_failure_records_partial(self) -> None:
        """issue_create + followup: sync failure at followup_created → final_status=partial."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                create_followup_issue=True,
                issue_text="## New Issue\n\nBody.\n",
                followup_text="## Follow-up\n\nBody.\n",
            )
            calls: list[str] = []

            def fake_issue_create(*args, **kwargs):
                calls.append("issue_create")
                p = root / "issue_create.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    execution_log_path=p,
                    project_item_id="",
                    project_sync_status="",
                    project_url="",
                    project_state_field_name="",
                    project_state_value_name="",
                    created_issue=fake_issue(103),
                    safe_stop_reason="created issue #103",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_issue_create_action_fn=fake_issue_create,
                execute_followup_issue_action_fn=self._make_followup_fn(root, calls),
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls, status="blocked"),
            )

            self.assertEqual(calls, ["issue_create", "followup", "sync:followup_created"])
            self.assertEqual(result.matrix_path, "issue_create_followup")
            self.assertEqual(result.final_status, "partial")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "blocked_project_preflight")

    # ---- review-completed path: state fields explicitly verified ----

    def test_review_completed_lifecycle_sync_state_fields(self) -> None:
        """human_review completed (no followup, no close) → review sync state fields recorded."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                review_text="## Review\n\nApproved.\n",
            )
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("review")
                p = root / "review.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_close_if_review_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(8001, 20),
                    execution_log_path=p,
                    safe_stop_reason="review posted.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_human_review_action_fn=fake_review,
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls),
            )

            self.assertEqual(calls, ["review", "sync:review"])
            self.assertEqual(result.matrix_path, "human_review")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_stage"], "review")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "project_state_synced")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_state_value"], "review")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_project_item_id"], "PVTI_example")
            self.assertIn("current_issue_project_state_sync_review", [s.name for s in result.steps])

    def test_review_completed_sync_not_requested_no_project(self) -> None:
        """human_review completed: no-project sync keeps final_status=completed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                review_text="## Review\n\nApproved.\n",
            )
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("review")
                p = root / "review.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="",
                    resolved_issue=None,
                    created_comment=fake_comment(8002, 20),
                    execution_log_path=p,
                    safe_stop_reason="review posted.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_human_review_action_fn=fake_review,
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls, status="not_requested"),
            )

            self.assertEqual(calls, ["review", "sync:review"])
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "")

    # ---- close-completed path: state fields explicitly verified ----

    def test_close_completed_lifecycle_sync_state_fields(self) -> None:
        """no_action + close_current_issue: done sync state fields recorded."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                close_current_issue=True,
            )
            calls: list[str] = []

            def fake_close(*args, **kwargs):
                calls.append("close")
                p = root / "close.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="completed",
                    close_order="after_no_action",
                    execution_log_path=p,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed issue #20.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_close_current_issue_fn=fake_close,
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls),
            )

            self.assertEqual(calls, ["close", "sync:done"])
            self.assertEqual(result.matrix_path, "no_action_close")
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_stage"], "done")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "project_state_synced")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_state_value"], "done")
            self.assertIn("current_issue_project_state_sync_done", [s.name for s in result.steps])

    def test_close_completed_sync_not_requested_no_project(self) -> None:
        """close completed: no-project sync keeps final_status=completed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                close_current_issue=True,
            )
            calls: list[str] = []

            def fake_close(*args, **kwargs):
                calls.append("close")
                p = root / "close.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="completed",
                    close_order="after_no_action",
                    execution_log_path=p,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed issue #20.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_close_current_issue_fn=fake_close,
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls, status="not_requested"),
            )

            self.assertEqual(calls, ["close", "sync:done"])
            self.assertEqual(result.final_status, "completed")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "")

    def test_close_completed_sync_failure_records_partial(self) -> None:
        """close completed: sync failure → final_status=partial, sync failure recorded."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                close_current_issue=True,
            )
            calls: list[str] = []

            def fake_close(*args, **kwargs):
                calls.append("close")
                p = root / "close.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="completed",
                    close_order="after_no_action",
                    execution_log_path=p,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed issue #20.",
                )

            result = self._dispatch(
                decision=decision,
                root=root,
                execute_close_current_issue_fn=fake_close,
                execute_current_issue_project_state_sync_fn=self._make_sync_fn(root, calls, status="blocked"),
            )

            self.assertEqual(calls, ["close", "sync:done"])
            self.assertEqual(result.final_status, "partial")
            self.assertEqual(result.final_state["last_issue_centric_lifecycle_sync_status"], "blocked_project_preflight")


class DispatchSummaryLifecycleSyncSignalSurfacingTests(unittest.TestCase):
    """Phase 2 of #57: dispatch summary JSON must include current_issue_lifecycle_sync_signal."""

    def _close_fn(self, root: Path) -> object:
        def fn(*args, **kwargs):
            p = root / "close.json"
            p.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                status="completed",
                close_status="completed",
                close_order="after_no_action",
                execution_log_path=p,
                issue_before=SimpleNamespace(
                    number=30,
                    url="https://github.com/example/repo/issues/30",
                    title="Issue 30",
                    repository="example/repo",
                    node_id="ISSUE_30",
                    state="open",
                ),
                issue_after=SimpleNamespace(
                    number=30,
                    url="https://github.com/example/repo/issues/30",
                    title="Issue 30",
                    repository="example/repo",
                    node_id="ISSUE_30",
                    state="closed",
                ),
                safe_stop_reason="closed issue #30.",
            )
        return fn

    def _dispatch_no_action_close(
        self,
        root: Path,
        sync_fn,
    ) -> "issue_centric_execution.IssueCentricDispatchResult":
        decision = build_decision(
            action=issue_centric_contract.IssueCentricAction.NO_ACTION,
            close_current_issue=True,
        )
        mat = materialized_from_decision(decision, root=root)
        state: dict[str, object] = {
            "last_issue_centric_action": "no_action",
            "last_issue_centric_target_issue": "https://github.com/example/repo/issues/30",
            "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/30",
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
        saved: list[dict] = []
        log_writer = TempLogWriter(root)
        return issue_centric_execution.dispatch_issue_centric_execution(
            contract_decision=decision,
            materialized=mat,
            prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/30"},
            mutable_state=state,
            project_config={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
            repo_path=REPO_ROOT,
            source_raw_log="logs/raw.txt",
            source_decision_log="logs/decision.md",
            source_metadata_log="logs/metadata.json",
            source_artifact_path="logs/artifact.md",
            log_writer=log_writer,
            repo_relative=lambda p: str(p),
            load_state_fn=lambda: dict(saved[-1]) if saved else dict(state),
            save_state_fn=lambda s: saved.append(dict(s)),
            execute_issue_create_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("issue_create should not run")),
            execute_codex_run_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("codex_run should not run")),
            launch_issue_centric_codex_run_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("launch should not run")),
            execute_human_review_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("review should not run")),
            execute_close_current_issue_fn=self._close_fn(root),
            execute_followup_issue_action_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("followup should not run")),
            execute_current_issue_project_state_sync_fn=sync_fn,
            launch_runner=lambda s, argv=None: 0,
        )

    def _read_dispatch_summary(self, result: "issue_centric_execution.IssueCentricDispatchResult") -> dict:
        return json.loads(result.summary_log_path.read_text(encoding="utf-8"))

    def test_dispatch_summary_lifecycle_sync_signal_synced(self) -> None:
        """dispatch summary JSON: project_state_synced → current_issue_lifecycle_sync_signal=synced."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def sync_fn(*args, **kwargs):
                stage = kwargs.get("lifecycle_stage", "no_action")
                p = root / f"sync_{stage}.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    sync_status="project_state_synced",
                    lifecycle_stage=stage,
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/30"),
                    issue_snapshot=None,
                    execution_log_path=p,
                    project_url="https://github.com/users/example/projects/1",
                    project_item_id="PVTI_example",
                    project_state_field_name="Status",
                    project_state_value_name=stage,
                    safe_stop_reason=f"synced to {stage}",
                )

            result = self._dispatch_no_action_close(root, sync_fn)
            summary = self._read_dispatch_summary(result)

            self.assertEqual(summary["current_issue_lifecycle_sync_signal"], "synced")
            self.assertEqual(summary["current_issue_lifecycle_sync_status"], "project_state_synced")

    def test_dispatch_summary_lifecycle_sync_signal_sync_failed(self) -> None:
        """dispatch summary JSON: blocked sync → current_issue_lifecycle_sync_signal=sync_failed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def sync_fn(*args, **kwargs):
                stage = kwargs.get("lifecycle_stage", "no_action")
                p = root / f"sync_{stage}.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="blocked",
                    sync_status="blocked_project_preflight",
                    lifecycle_stage=stage,
                    resolved_issue=None,
                    issue_snapshot=None,
                    execution_log_path=p,
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason=f"project sync failed for stage {stage}",
                )

            result = self._dispatch_no_action_close(root, sync_fn)
            summary = self._read_dispatch_summary(result)

            self.assertEqual(summary["current_issue_lifecycle_sync_signal"], "sync_failed")
            self.assertEqual(summary["current_issue_lifecycle_sync_status"], "blocked_project_preflight")

    def test_dispatch_summary_lifecycle_sync_signal_empty_when_not_triggered(self) -> None:
        """dispatch summary JSON: not_requested (no project) → current_issue_lifecycle_sync_signal=''."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def sync_fn(*args, **kwargs):
                stage = kwargs.get("lifecycle_stage", "no_action")
                p = root / f"no_sync_{stage}.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="not_requested",
                    sync_status="not_requested_no_project",
                    lifecycle_stage=stage,
                    resolved_issue=None,
                    issue_snapshot=None,
                    execution_log_path=p,
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="No GitHub Project is configured.",
                )

            result = self._dispatch_no_action_close(root, sync_fn)
            summary = self._read_dispatch_summary(result)

            self.assertEqual(summary["current_issue_lifecycle_sync_signal"], "")
            self.assertEqual(summary["current_issue_lifecycle_sync_status"], "")


class DispatchStopMessageLifecycleSyncSurfacingTests(unittest.TestCase):
    """Phase 2 (#63): dispatch stop_message must include lifecycle sync suffix.

    Covers IssueCentricDispatchResult.stop_message via _finalize_dispatch(), which is the
    closeout-facing human text consumed by BridgeStop in fetch_next_prompt.run().
    """

    def _make_sync_fn(self, root: Path, sync_status: str, lifecycle_stage: str = "done"):
        # status="not_requested" → _apply_current_issue_project_state_sync_state is skipped,
        # so lifecycle fields in mutable_state stay empty.
        # Use status="completed" for synced, status="blocked" for sync_failed.
        if sync_status == "project_state_synced":
            exec_status = "completed"
        elif sync_status == "":
            exec_status = "not_requested"
        else:
            exec_status = "blocked"

        def fn(*args, **kwargs):
            p = root / f"sync_{sync_status or 'empty'}.json"
            p.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                status=exec_status,
                sync_status=sync_status,
                lifecycle_stage=lifecycle_stage,
                resolved_issue=None,
                issue_snapshot=None,
                execution_log_path=p,
                project_url="",
                project_item_id="",
                project_state_field_name="",
                project_state_value_name="",
                safe_stop_reason="lifecycle sync ok." if sync_status == "project_state_synced" else "sync note.",
            )
        return fn

    def _dispatch(self, root: Path, sync_fn) -> "issue_centric_execution.IssueCentricDispatchResult":
        helper = DispatchSummaryLifecycleSyncSignalSurfacingTests()
        return helper._dispatch_no_action_close(root, sync_fn)

    def test_stop_message_shows_lifecycle_sync_synced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._dispatch(root, self._make_sync_fn(root, "project_state_synced", "done"))
            self.assertIn("lifecycle_sync", result.stop_message)
            self.assertIn("signal=synced", result.stop_message)
            self.assertIn("stage=done", result.stop_message)

    def test_stop_message_shows_lifecycle_sync_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._dispatch(root, self._make_sync_fn(root, "blocked_project_preflight", "done"))
            self.assertIn("lifecycle_sync", result.stop_message)
            self.assertIn("signal=sync_failed", result.stop_message)
            self.assertIn("reason=blocked_project_preflight", result.stop_message)

    def test_stop_message_no_lifecycle_sync_when_no_sync_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def empty_sync_fn(*args, **kwargs):
                p = root / "no_sync.json"
                p.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="not_requested",
                    sync_status="",
                    lifecycle_stage="",
                    resolved_issue=None,
                    issue_snapshot=None,
                    execution_log_path=p,
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="",
                )

            result = self._dispatch(root, empty_sync_fn)
            self.assertNotIn("lifecycle_sync", result.stop_message)


if __name__ == "__main__":
    unittest.main()
