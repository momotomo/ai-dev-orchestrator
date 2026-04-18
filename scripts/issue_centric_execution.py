from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from dataclasses import asdict

from _bridge_common import bridge_lifecycle_sync_suffix
from issue_centric_normalized_summary import (
    build_issue_centric_normalized_summary,
    build_issue_centric_runtime_snapshot,
    resolve_issue_centric_runtime_mode,
)


@dataclass(frozen=True)
class IssueCentricExecutionStep:
    name: str
    status: str
    log_path: str
    note: str


@dataclass(frozen=True)
class IssueCentricDispatchResult:
    matrix_path: str
    final_status: str
    steps: tuple[IssueCentricExecutionStep, ...]
    summary_log_path: Path
    final_state: dict[str, object]
    stop_message: str


def dispatch_issue_centric_execution(
    *,
    contract_decision: object,
    materialized: object,
    prior_state: Mapping[str, Any],
    mutable_state: dict[str, object],
    project_config: Mapping[str, Any],
    repo_path: Path,
    source_raw_log: str,
    source_decision_log: str,
    source_metadata_log: str,
    source_artifact_path: str,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    load_state_fn: Callable[[], dict[str, object]],
    save_state_fn: Callable[[dict[str, object]], None],
    execute_issue_create_action_fn: Callable[..., object],
    execute_codex_run_action_fn: Callable[..., object],
    launch_issue_centric_codex_run_fn: Callable[..., object],
    execute_human_review_action_fn: Callable[..., object],
    execute_close_current_issue_fn: Callable[..., object],
    execute_followup_issue_action_fn: Callable[..., object],
    execute_current_issue_project_state_sync_fn: Callable[..., object],
    launch_runner: Callable[[dict[str, object], list[str] | None], int],
    execute_parent_issue_update_fn: Callable[..., object] | None = None,
) -> IssueCentricDispatchResult:
    steps: list[IssueCentricExecutionStep] = []
    decision_action = contract_decision.action.value

    if contract_decision.create_followup_issue and decision_action not in {
        "no_action",
        "human_review_needed",
        "issue_create",
        "codex_run",
    }:
        unsupported_reason = (
            "create_followup_issue execution is currently implemented only for action=no_action, action=issue_create, action=codex_run, and the narrow human_review_needed combo. "
            f"The combination action={decision_action} + create_followup_issue=true is blocked in this slice."
        )
        mutable_state.update(
            {
                "last_issue_centric_followup_status": "blocked_unsupported_action_combo",
                "last_issue_centric_stop_reason": unsupported_reason,
                "chatgpt_decision_note": unsupported_reason,
            }
        )
        steps.append(
            IssueCentricExecutionStep(
                name="unsupported_followup_combo",
                status="blocked",
                log_path="",
                note=unsupported_reason,
            )
        )
        return _finalize_dispatch(
            matrix_path="blocked_followup_combo",
            final_status="blocked",
            steps=steps,
            mutable_state=mutable_state,
            log_writer=log_writer,
            repo_relative=repo_relative,
            stop_message=(
                "issue-centric contract reply を検出しましたが、create_followup_issue の narrow execution は action=no_action と human_review_needed review combo にだけ対応しています。"
                " issue_create / codex_run combo は narrow happy path のみ対応しています。"
                f" decision log: {source_decision_log}"
                f" metadata: {source_metadata_log}"
                + unsupported_reason
            ),
        )

    if decision_action == "issue_create":
        if contract_decision.create_followup_issue and materialized.prepared.primary_body is None:
            blocked_reason = (
                "issue_create + create_followup_issue requires a prepared CHATGPT_ISSUE_BODY or CHATGPT_FOLLOWUP_ISSUE_BODY artifact before any issue mutation can run."
            )
            mutable_state.update(
                {
                    "last_issue_centric_execution_status": "blocked_missing_primary_issue_artifact",
                    "last_issue_centric_stop_reason": blocked_reason,
                    "chatgpt_decision_note": blocked_reason,
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="issue_create",
                    status="blocked_missing_primary_issue_artifact",
                    log_path="",
                    note=blocked_reason,
                )
            )
            return _finalize_dispatch(
                matrix_path="blocked_issue_create_followup_missing_primary",
                final_status="blocked",
                steps=steps,
                mutable_state=mutable_state,
                log_writer=log_writer,
                repo_relative=repo_relative,
                stop_message=(
                    "issue-centric contract reply を検出しましたが、issue_create + create_followup_issue の narrow combo に必要な primary issue artifact が不足しているため停止しました。"
                    f" decision log: {source_decision_log}"
                    f" metadata: {source_metadata_log}"
                ),
            )
        if contract_decision.create_followup_issue and materialized.prepared.followup_issue_body is None:
            blocked_reason = (
                "issue_create + create_followup_issue requires a prepared CHATGPT_FOLLOWUP_ISSUE_BODY artifact before follow-up issue creation can run."
            )
            mutable_state.update(
                {
                    "last_issue_centric_followup_status": "blocked_missing_followup_artifact",
                    "last_issue_centric_stop_reason": blocked_reason,
                    "chatgpt_decision_note": blocked_reason,
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="followup_issue_create",
                    status="blocked_missing_followup_artifact",
                    log_path="",
                    note=blocked_reason,
                )
            )
            return _finalize_dispatch(
                matrix_path="blocked_issue_create_followup_missing_followup",
                final_status="blocked",
                steps=steps,
                mutable_state=mutable_state,
                log_writer=log_writer,
                repo_relative=repo_relative,
                stop_message=(
                    "issue-centric contract reply を検出しましたが、issue_create + create_followup_issue の narrow combo に必要な follow-up artifact が不足しているため停止しました。"
                    f" decision log: {source_decision_log}"
                    f" metadata: {source_metadata_log}"
                ),
            )
        execution = execute_issue_create_action_fn(
            materialized.prepared,
            project_config=project_config,
            repo_path=repo_path,
            source_decision_log=source_decision_log,
            source_metadata_log=source_metadata_log,
            source_artifact_path=source_artifact_path,
            log_writer=log_writer,
            repo_relative=repo_relative,
            allow_followup_combo=contract_decision.create_followup_issue,
        )
        _apply_issue_create_execution_state(
            mutable_state,
            execution=execution,
            repo_relative=repo_relative,
        )
        steps.append(
            IssueCentricExecutionStep(
                name="issue_create",
                status=execution.status,
                log_path=repo_relative(execution.execution_log_path),
                note=execution.safe_stop_reason,
            )
        )
        close_note = ""
        final_status = execution.status
        followup_note = ""
        followup_execution = None
        close_execution = None
        done_sync_execution = None
        # When issue_body is None, we used followup_issue_body as the primary body via the
        # primary_body fallback.  Running execute_followup_issue_action_fn would create a
        # second issue from the same followup_issue_body — skip it to avoid duplicate creation.
        if (
            contract_decision.create_followup_issue
            and execution.status == "completed"
            and materialized.prepared.issue_body is not None
        ):
            followup_execution = execute_followup_issue_action_fn(
                materialized.prepared,
                prior_state=prior_state,
                project_config=project_config,
                repo_path=repo_path,
                source_decision_log=source_decision_log,
                source_metadata_log=source_metadata_log,
                source_artifact_path=source_artifact_path,
                log_writer=log_writer,
                repo_relative=repo_relative,
                allow_issue_create_combo=True,
            )
            _apply_followup_execution_state(
                mutable_state,
                followup_execution=followup_execution,
                repo_relative=repo_relative,
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="followup_issue_create",
                    status=followup_execution.status,
                    log_path=repo_relative(followup_execution.execution_log_path),
                    note=followup_execution.safe_stop_reason,
                )
            )
            if followup_execution.created_issue is not None:
                followup_note = (
                    f" follow-up issue: #{followup_execution.created_issue.number} "
                    f"{followup_execution.created_issue.url}"
                )
            if followup_execution.status != "completed":
                final_status = "partial"
            if contract_decision.close_current_issue and followup_execution.status == "completed":
                close_execution = execute_close_current_issue_fn(
                    materialized.prepared,
                    prior_state=prior_state,
                    project_config=project_config,
                    repo_path=repo_path,
                    source_decision_log=source_decision_log,
                    source_metadata_log=source_metadata_log,
                    source_action_execution_log=repo_relative(followup_execution.execution_log_path),
                    log_writer=log_writer,
                    repo_relative=repo_relative,
                    allow_issue_create_followup_close=True,
                )
                _apply_close_execution_state(
                    mutable_state,
                    close_execution=close_execution,
                    repo_relative=repo_relative,
                )
                steps.append(
                    IssueCentricExecutionStep(
                        name="close_current_issue",
                        status=close_execution.status,
                        log_path=repo_relative(close_execution.execution_log_path),
                        note=close_execution.safe_stop_reason,
                    )
                )
                close_note = f" close log: {repo_relative(close_execution.execution_log_path)}"
                if close_execution.status != "completed":
                    final_status = "partial"
                else:
                    done_sync_execution = _run_current_issue_project_state_sync(
                        lifecycle_stage="done",
                        prepared=materialized.prepared,
                        prior_state=mutable_state,
                        target_state=mutable_state,
                        project_config=project_config,
                        repo_path=repo_path,
                        source_decision_log=source_decision_log,
                        source_metadata_log=source_metadata_log,
                        source_action_execution_log=repo_relative(close_execution.execution_log_path),
                        step_name="current_issue_project_state_sync_done",
                        steps=steps,
                        log_writer=log_writer,
                        repo_relative=repo_relative,
                        execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
                    )
                    if done_sync_execution.status not in {"completed", "not_requested"}:
                        final_status = "partial"
            elif contract_decision.close_current_issue:
                mutable_state.update(
                    {
                        "last_issue_centric_close_status": "not_attempted_followup_blocked",
                        "last_issue_centric_close_order": "after_issue_create_followup",
                    }
                )
                steps.append(
                    IssueCentricExecutionStep(
                        name="close_current_issue",
                        status="not_attempted_followup_blocked",
                        log_path="",
                        note="close_current_issue runs only after primary issue create, follow-up issue create, and any required Project sync complete.",
                    )
                )
                final_status = "partial"
            if not contract_decision.close_current_issue and followup_execution.status == "completed":
                followup_sync_execution = _run_current_issue_project_state_sync(
                    lifecycle_stage="followup_created",
                    prepared=materialized.prepared,
                    prior_state=mutable_state,
                    target_state=mutable_state,
                    project_config=project_config,
                    repo_path=repo_path,
                    source_decision_log=source_decision_log,
                    source_metadata_log=source_metadata_log,
                    source_action_execution_log=repo_relative(followup_execution.execution_log_path),
                    step_name="current_issue_project_state_sync_followup_created",
                    steps=steps,
                    log_writer=log_writer,
                    repo_relative=repo_relative,
                    execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
                )
                if followup_sync_execution.status not in {"completed", "not_requested"}:
                    final_status = "partial"
        if contract_decision.close_current_issue and execution.status == "completed":
            if not contract_decision.create_followup_issue:
                close_execution = execute_close_current_issue_fn(
                    materialized.prepared,
                    prior_state=prior_state,
                    project_config=project_config,
                    repo_path=repo_path,
                    source_decision_log=source_decision_log,
                    source_metadata_log=source_metadata_log,
                    source_action_execution_log=repo_relative(execution.execution_log_path),
                    log_writer=log_writer,
                    repo_relative=repo_relative,
                )
                _apply_close_execution_state(
                    mutable_state,
                    close_execution=close_execution,
                    repo_relative=repo_relative,
                )
                steps.append(
                    IssueCentricExecutionStep(
                        name="close_current_issue",
                        status=close_execution.status,
                        log_path=repo_relative(close_execution.execution_log_path),
                        note=close_execution.safe_stop_reason,
                    )
                )
                close_note = f" close log: {repo_relative(close_execution.execution_log_path)}"
                if close_execution.status != "completed":
                    final_status = "partial"
                else:
                    done_sync_execution = _run_current_issue_project_state_sync(
                        lifecycle_stage="done",
                        prepared=materialized.prepared,
                        prior_state=mutable_state,
                        target_state=mutable_state,
                        project_config=project_config,
                        repo_path=repo_path,
                        source_decision_log=source_decision_log,
                        source_metadata_log=source_metadata_log,
                        source_action_execution_log=repo_relative(close_execution.execution_log_path),
                        step_name="current_issue_project_state_sync_done",
                        steps=steps,
                        log_writer=log_writer,
                        repo_relative=repo_relative,
                        execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
                    )
                    if done_sync_execution.status not in {"completed", "not_requested"}:
                        final_status = "partial"
        elif contract_decision.close_current_issue:
            mutable_state.update(
                {
                    "last_issue_centric_close_status": "not_attempted_primary_action_blocked",
                    "last_issue_centric_close_order": (
                        "after_issue_create_followup"
                        if contract_decision.create_followup_issue
                        else "after_issue_create"
                    ),
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="close_current_issue",
                    status="not_attempted_primary_action_blocked",
                    log_path="",
                    note=(
                        "close_current_issue runs only after primary issue create, follow-up issue create, and any required Project sync complete."
                        if contract_decision.create_followup_issue
                        else "close_current_issue runs only after issue create + project sync completes."
                    ),
                )
            )
            final_status = "partial"
        issue_note = ""
        if execution.created_issue is not None:
            issue_note = f" created primary issue: #{execution.created_issue.number} {execution.created_issue.url}"
        if execution.status != "completed":
            stop_label = (
                "issue-centric contract reply を検出しましたが、issue_create primary execution を完了できず停止しました。"
            )
        elif contract_decision.create_followup_issue and followup_execution is not None:
            if (
                contract_decision.close_current_issue
                and close_execution is not None
                and close_execution.status == "completed"
            ):
                stop_label = (
                    "issue-centric contract reply を検出し、issue_create の primary issue create / narrow follow-up issue create / narrow close まで実行しました。"
                )
            elif followup_execution.status == "completed":
                stop_label = (
                    "issue-centric contract reply を検出し、issue_create の primary issue create と narrow follow-up issue create まで実行しました。"
                )
            else:
                stop_label = (
                    "issue-centric contract reply を検出し、issue_create の primary issue create までは完了しましたが、その後の narrow follow-up issue create で停止しました。"
                )
        elif execution.status == "completed" and contract_decision.close_current_issue:
            stop_label = (
                "issue-centric contract reply を検出し、issue_create の primary issue create と narrow close まで実行しました。"
            )
        else:
            stop_label = (
                "issue-centric contract reply を検出し、issue_create の最小 execution slice まで実行しました。"
            )
        return _finalize_dispatch(
            matrix_path=(
                "issue_create_followup_then_close"
                if contract_decision.create_followup_issue and contract_decision.close_current_issue
                else (
                    "issue_create_followup"
                    if contract_decision.create_followup_issue
                    else (
                        "issue_create_then_close"
                        if contract_decision.close_current_issue
                        else "issue_create"
                    )
                )
            ),
            final_status=final_status,
            steps=steps,
            mutable_state=mutable_state,
            log_writer=log_writer,
            repo_relative=repo_relative,
            stop_message=(
                stop_label
                + f" decision log: {source_decision_log}"
                + f" metadata: {source_metadata_log}"
                + (f" artifact: {source_artifact_path}" if source_artifact_path else "")
                + f" execution: {repo_relative(execution.execution_log_path)}"
                + issue_note
                + (f" project item: {execution.project_item_id}" if execution.project_item_id else "")
                + followup_note
                + close_note
                + (
                    " codex_run + create_followup_issue / other action Project sync / Codex dispatch はまだ未実装です。"
                    if contract_decision.create_followup_issue
                    else " create_followup_issue mutation / other action Project sync / Codex dispatch はまだ未実装です。"
                )
            ),
        )

    if (
        decision_action == "codex_run"
        and contract_decision.close_current_issue
        and not contract_decision.create_followup_issue
    ):
        close_execution = execute_close_current_issue_fn(
            materialized.prepared,
            prior_state=prior_state,
            project_config=project_config,
            repo_path=repo_path,
            source_decision_log=source_decision_log,
            source_metadata_log=source_metadata_log,
            source_action_execution_log="",
            log_writer=log_writer,
            repo_relative=repo_relative,
        )
        _apply_close_execution_state(
            mutable_state,
            close_execution=close_execution,
            repo_relative=repo_relative,
        )
        steps.append(
            IssueCentricExecutionStep(
                name="close_current_issue",
                status=close_execution.status,
                log_path=repo_relative(close_execution.execution_log_path),
                note=close_execution.safe_stop_reason,
            )
        )
        return _finalize_dispatch(
            matrix_path="blocked_codex_run_close",
            final_status="blocked",
            steps=steps,
            mutable_state=mutable_state,
            log_writer=log_writer,
            repo_relative=repo_relative,
            stop_message=(
                "issue-centric contract reply を検出しましたが、codex_run + close_current_issue はこの slice では安全に実行できないため停止しました。"
                f" decision log: {source_decision_log}"
                f" metadata: {source_metadata_log}"
                + (f" artifact: {source_artifact_path}" if source_artifact_path else "")
                + f" close: {repo_relative(close_execution.execution_log_path)}"
            ),
        )

    if decision_action == "codex_run":
        if contract_decision.create_followup_issue and materialized.prepared.codex_body is None:
            blocked_reason = (
                "codex_run + create_followup_issue requires a prepared CHATGPT_CODEX_BODY artifact before trigger comment mutation can run."
            )
            mutable_state.update(
                {
                    "last_issue_centric_execution_status": "blocked_missing_codex_artifact",
                    "last_issue_centric_stop_reason": blocked_reason,
                    "chatgpt_decision_note": blocked_reason,
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="codex_trigger_comment",
                    status="blocked_missing_codex_artifact",
                    log_path="",
                    note=blocked_reason,
                )
            )
            return _finalize_dispatch(
                matrix_path="blocked_codex_run_followup_missing_codex",
                final_status="blocked",
                steps=steps,
                mutable_state=mutable_state,
                log_writer=log_writer,
                repo_relative=repo_relative,
                stop_message=(
                    "issue-centric contract reply を検出しましたが、codex_run + create_followup_issue の narrow combo に必要な Codex artifact が不足しているため停止しました。"
                    f" decision log: {source_decision_log}"
                    f" metadata: {source_metadata_log}"
                ),
            )
        if contract_decision.create_followup_issue and materialized.prepared.followup_issue_body is None:
            blocked_reason = (
                "codex_run + create_followup_issue requires a prepared CHATGPT_FOLLOWUP_ISSUE_BODY artifact before follow-up issue creation can run."
            )
            mutable_state.update(
                {
                    "last_issue_centric_followup_status": "blocked_missing_followup_artifact",
                    "last_issue_centric_stop_reason": blocked_reason,
                    "chatgpt_decision_note": blocked_reason,
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="followup_issue_create",
                    status="blocked_missing_followup_artifact",
                    log_path="",
                    note=blocked_reason,
                )
            )
            return _finalize_dispatch(
                matrix_path="blocked_codex_run_followup_missing_followup",
                final_status="blocked",
                steps=steps,
                mutable_state=mutable_state,
                log_writer=log_writer,
                repo_relative=repo_relative,
                stop_message=(
                    "issue-centric contract reply を検出しましたが、codex_run + create_followup_issue の narrow combo に必要な follow-up artifact が不足しているため停止しました。"
                    f" decision log: {source_decision_log}"
                    f" metadata: {source_metadata_log}"
                ),
            )
        execution = execute_codex_run_action_fn(
            materialized.prepared,
            project_config=project_config,
            repo_path=repo_path,
            source_decision_log=source_decision_log,
            source_metadata_log=source_metadata_log,
            source_artifact_path=source_artifact_path,
            log_writer=log_writer,
            repo_relative=repo_relative,
        )
        _apply_codex_execution_state(
            mutable_state,
            execution=execution,
            repo_relative=repo_relative,
        )
        steps.append(
            IssueCentricExecutionStep(
                name="codex_trigger_comment",
                status=execution.status,
                log_path=repo_relative(execution.execution_log_path),
                note=execution.safe_stop_reason,
            )
        )
        if contract_decision.close_current_issue and execution.status != "completed":
            post_trigger_block_reason = (
                "close_current_issue runs only after trigger comment registration, Codex launch / continuation handoff, and follow-up issue create complete."
            )
            mutable_state.update(
                {
                    "last_issue_centric_close_status": "not_attempted_trigger_blocked",
                    "last_issue_centric_close_order": "after_codex_run_followup",
                    "last_issue_centric_stop_reason": execution.safe_stop_reason,
                    "chatgpt_decision_note": execution.safe_stop_reason,
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="close_current_issue",
                    status="not_attempted_trigger_blocked",
                    log_path="",
                    note=post_trigger_block_reason,
                )
            )
        if execution.status != "completed":
            trigger_note = ""
            if execution.created_comment is not None:
                trigger_note = f" trigger comment: {execution.created_comment.url}"
            return _finalize_dispatch(
                matrix_path="codex_run_trigger_only",
                final_status=execution.status,
                steps=steps,
                mutable_state=mutable_state,
                log_writer=log_writer,
                repo_relative=repo_relative,
                stop_message=(
                    "issue-centric contract reply を検出し、codex_run の trigger comment execution まで実行しました。"
                    f" decision log: {source_decision_log}"
                    f" metadata: {source_metadata_log}"
                    + (f" artifact: {source_artifact_path}" if source_artifact_path else "")
                    + f" execution: {repo_relative(execution.execution_log_path)}"
                    + (
                        f" payload: {repo_relative(execution.payload_log_path)}"
                        if execution.payload_log_path is not None
                        else ""
                    )
                    + trigger_note
                    + (
                        " issue-centric Codex launch はまだ進めませんでした。"
                        if not contract_decision.create_followup_issue
                        else " issue-centric Codex launch とその後の follow-up issue create / close には進めませんでした。"
                    )
                ),
            )

        try:
            launch_result = launch_issue_centric_codex_run_fn(
                materialized.prepared,
                execution,
                state=mutable_state,
                project_config=project_config,
                log_writer=log_writer,
                repo_relative=repo_relative,
                launch_runner=launch_runner,
                load_state_fn=load_state_fn,
                save_state_fn=save_state_fn,
            )
        except Exception:
            partial_state = dict(load_state_fn())
            partial_state.setdefault(
                "last_issue_centric_execution_status",
                execution.status,
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="codex_launch_and_continuation",
                    status="blocked",
                    log_path="",
                    note="launch_issue_centric_codex_run raised before dispatcher could summarize the path.",
                )
            )
            dispatch_result = _finalize_dispatch(
                matrix_path="codex_run_launch_failed",
                final_status="blocked",
                steps=steps,
                mutable_state=partial_state,
                log_writer=log_writer,
                repo_relative=repo_relative,
                stop_message=partial_state.get("chatgpt_decision_note", "") or str(partial_state.get("last_issue_centric_stop_reason", "")),
            )
            save_state_fn(dispatch_result.final_state)
            raise

        post_launch_state = dict(load_state_fn())
        post_launch_state.update(
            {
                "last_issue_centric_launch_status": launch_result.launch_status,
                "last_issue_centric_launch_entrypoint": launch_result.launch_entrypoint,
                "last_issue_centric_launch_prompt_log": (
                    repo_relative(launch_result.prompt_log_path)
                    if launch_result.prompt_log_path is not None
                    else ""
                ),
                "last_issue_centric_launch_log": repo_relative(launch_result.launch_log_path),
                "last_issue_centric_continuation_status": launch_result.continuation_status,
                "last_issue_centric_continuation_log": repo_relative(launch_result.continuation_log_path),
                "last_issue_centric_report_status": launch_result.report_status,
                "last_issue_centric_report_file": launch_result.report_file,
                "last_issue_centric_stop_reason": launch_result.safe_stop_reason,
                "chatgpt_decision_note": launch_result.safe_stop_reason,
            }
        )
        steps.append(
            IssueCentricExecutionStep(
                name="codex_launch_and_continuation",
                status=launch_result.status,
                log_path=repo_relative(launch_result.launch_log_path),
                note=launch_result.safe_stop_reason,
            )
        )
        followup_note = ""
        close_note = ""
        followup_execution = None
        close_execution = None
        lifecycle_sync_execution = None
        done_sync_execution = None
        final_status = launch_result.status
        post_followup_state = post_launch_state
        if contract_decision.close_current_issue and launch_result.status != "completed":
            post_launch_state.update(
                {
                    "last_issue_centric_close_status": "not_attempted_continuation_blocked",
                    "last_issue_centric_close_order": "after_codex_run_followup",
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="close_current_issue",
                    status="not_attempted_continuation_blocked",
                    log_path="",
                    note="close_current_issue runs only after launch / continuation and follow-up issue create complete.",
                )
            )
        if launch_result.status == "completed":
            lifecycle_sync_execution = _run_current_issue_project_state_sync(
                lifecycle_stage="in_progress",
                prepared=materialized.prepared,
                prior_state=prior_state,
                target_state=post_launch_state,
                project_config=project_config,
                repo_path=repo_path,
                source_decision_log=source_decision_log,
                source_metadata_log=source_metadata_log,
                source_action_execution_log=repo_relative(launch_result.continuation_log_path),
                step_name="current_issue_project_state_sync_in_progress",
                steps=steps,
                log_writer=log_writer,
                repo_relative=repo_relative,
                execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
            )
            if lifecycle_sync_execution.status not in {"completed", "not_requested"}:
                final_status = "partial"
        if contract_decision.create_followup_issue and launch_result.status == "completed":
            followup_execution = execute_followup_issue_action_fn(
                materialized.prepared,
                prior_state=post_launch_state,
                project_config=project_config,
                repo_path=repo_path,
                source_decision_log=source_decision_log,
                source_metadata_log=source_metadata_log,
                source_artifact_path=source_artifact_path,
                log_writer=log_writer,
                repo_relative=repo_relative,
                allow_codex_run_combo=True,
            )
            _apply_followup_execution_state(
                post_launch_state,
                followup_execution=followup_execution,
                repo_relative=repo_relative,
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="followup_issue_create",
                    status=followup_execution.status,
                    log_path=repo_relative(followup_execution.execution_log_path),
                    note=followup_execution.safe_stop_reason,
                )
            )
            if followup_execution.created_issue is not None:
                followup_note = (
                    f" follow-up issue: #{followup_execution.created_issue.number} "
                    f"{followup_execution.created_issue.url}"
                )
            if followup_execution.status != "completed":
                final_status = "partial"
            if contract_decision.close_current_issue and followup_execution.status == "completed":
                close_execution = execute_close_current_issue_fn(
                    materialized.prepared,
                    prior_state=post_followup_state,
                    project_config=project_config,
                    repo_path=repo_path,
                    source_decision_log=source_decision_log,
                    source_metadata_log=source_metadata_log,
                    source_action_execution_log=repo_relative(followup_execution.execution_log_path),
                    log_writer=log_writer,
                    repo_relative=repo_relative,
                    allow_codex_run_followup_close=True,
                )
                _apply_close_execution_state(
                    post_followup_state,
                    close_execution=close_execution,
                    repo_relative=repo_relative,
                )
                steps.append(
                    IssueCentricExecutionStep(
                        name="close_current_issue",
                        status=close_execution.status,
                        log_path=repo_relative(close_execution.execution_log_path),
                        note=close_execution.safe_stop_reason,
                    )
                )
                close_note = f" close: {repo_relative(close_execution.execution_log_path)}"
                if close_execution.status != "completed":
                    final_status = "partial"
                else:
                    done_sync_execution = _run_current_issue_project_state_sync(
                        lifecycle_stage="done",
                        prepared=materialized.prepared,
                        prior_state=post_followup_state,
                        target_state=post_followup_state,
                        project_config=project_config,
                        repo_path=repo_path,
                        source_decision_log=source_decision_log,
                        source_metadata_log=source_metadata_log,
                        source_action_execution_log=repo_relative(close_execution.execution_log_path),
                        step_name="current_issue_project_state_sync_done",
                        steps=steps,
                        log_writer=log_writer,
                        repo_relative=repo_relative,
                        execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
                    )
                    if done_sync_execution.status not in {"completed", "not_requested"}:
                        final_status = "partial"
            elif contract_decision.close_current_issue:
                post_followup_state.update(
                    {
                        "last_issue_centric_close_status": "not_attempted_followup_blocked",
                        "last_issue_centric_close_order": "after_codex_run_followup",
                    }
                )
                steps.append(
                    IssueCentricExecutionStep(
                        name="close_current_issue",
                        status="not_attempted_followup_blocked",
                        log_path="",
                        note="close_current_issue runs only after codex launch / continuation and follow-up issue create complete.",
                    )
                )
                final_status = "partial"
        trigger_note = ""
        if execution.created_comment is not None:
            trigger_note = f" trigger comment: {execution.created_comment.url}"
        if launch_result.status != "completed":
            stop_label = "issue-centric contract reply を検出し、codex_run launch 後の continuation handoff で停止しました。"
        elif contract_decision.create_followup_issue and followup_execution is not None:
            if (
                contract_decision.close_current_issue
                and close_execution is not None
                and close_execution.status == "completed"
            ):
                stop_label = (
                    "issue-centric contract reply を検出し、codex_run の trigger comment / launch / continuation / narrow follow-up issue create / narrow close まで実行しました。"
                )
            elif followup_execution.status == "completed":
                stop_label = (
                    "issue-centric contract reply を検出し、codex_run の trigger comment / launch / continuation と narrow follow-up issue create まで実行しました。"
                )
            else:
                stop_label = (
                    "issue-centric contract reply を検出し、codex_run の trigger comment / launch / continuation までは完了しましたが、その後の narrow follow-up issue create で停止しました。"
                )
        else:
            stop_label = (
                "issue-centric contract reply を検出し、codex_run を既存 Codex launch 入口へ narrow 接続しました。"
            )
        return _finalize_dispatch(
            matrix_path=(
                "codex_run_followup_then_close"
                if contract_decision.create_followup_issue and contract_decision.close_current_issue
                else (
                    "codex_run_followup"
                    if contract_decision.create_followup_issue
                    else "codex_run_launch_and_continuation"
                )
            ),
            final_status=final_status,
            steps=steps,
            mutable_state=post_followup_state,
            log_writer=log_writer,
            repo_relative=repo_relative,
            stop_message=(
                stop_label
                + f" decision log: {source_decision_log}"
                f" metadata: {source_metadata_log}"
                + (f" artifact: {source_artifact_path}" if source_artifact_path else "")
                + f" execution: {repo_relative(execution.execution_log_path)}"
                + (
                    f" payload: {repo_relative(execution.payload_log_path)}"
                    if execution.payload_log_path is not None
                    else ""
                )
                + f" prompt: {repo_relative(launch_result.prompt_log_path)}"
                + f" launch: {repo_relative(launch_result.launch_log_path)}"
                + f" continuation: {repo_relative(launch_result.continuation_log_path)}"
                + trigger_note
                + followup_note
                + close_note
                + f" final mode: {launch_result.final_mode or 'unknown'}"
                + f" continuation status: {launch_result.continuation_status}"
                + (
                    " post-codex review automation はまだ未実装です。"
                    if contract_decision.create_followup_issue and contract_decision.close_current_issue
                    else (
                        " close_current_issue for codex_run / post-codex review automation はまだ未実装です。"
                        if contract_decision.create_followup_issue
                        else " close_current_issue for codex_run / follow-up mutation / post-codex review automation はまだ未実装です。"
                    )
                )
            ),
        )

    if decision_action == "human_review_needed":
        if contract_decision.create_followup_issue and materialized.prepared.review_body is None:
            blocked_reason = (
                "human_review_needed + create_followup_issue requires a prepared CHATGPT_REVIEW artifact before any review mutation can run."
            )
            mutable_state.update(
                {
                    "last_issue_centric_review_status": "blocked_missing_review_artifact",
                    "last_issue_centric_stop_reason": blocked_reason,
                    "chatgpt_decision_note": blocked_reason,
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="human_review_comment",
                    status="blocked_missing_review_artifact",
                    log_path="",
                    note=blocked_reason,
                )
            )
            return _finalize_dispatch(
                matrix_path="blocked_human_review_followup_missing_review",
                final_status="blocked",
                steps=steps,
                mutable_state=mutable_state,
                log_writer=log_writer,
                repo_relative=repo_relative,
                stop_message=(
                    "issue-centric contract reply を検出しましたが、human_review_needed + create_followup_issue の narrow combo に必要な review artifact が不足しているため停止しました。"
                    f" decision log: {source_decision_log}"
                    f" metadata: {source_metadata_log}"
                ),
            )
        if contract_decision.create_followup_issue and materialized.prepared.followup_issue_body is None:
            blocked_reason = (
                "human_review_needed + create_followup_issue requires a prepared CHATGPT_FOLLOWUP_ISSUE_BODY artifact before review can safely proceed."
            )
            mutable_state.update(
                {
                    "last_issue_centric_followup_status": "blocked_missing_followup_artifact",
                    "last_issue_centric_stop_reason": blocked_reason,
                    "chatgpt_decision_note": blocked_reason,
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="followup_issue_create",
                    status="blocked_missing_followup_artifact",
                    log_path="",
                    note=blocked_reason,
                )
            )
            return _finalize_dispatch(
                matrix_path="blocked_human_review_followup_missing_followup",
                final_status="blocked",
                steps=steps,
                mutable_state=mutable_state,
                log_writer=log_writer,
                repo_relative=repo_relative,
                stop_message=(
                    "issue-centric contract reply を検出しましたが、human_review_needed + create_followup_issue の narrow combo に必要な follow-up artifact が不足しているため停止しました。"
                    f" decision log: {source_decision_log}"
                    f" metadata: {source_metadata_log}"
                ),
            )
        review_execution = execute_human_review_action_fn(
            materialized.prepared,
            prior_state=prior_state,
            project_config=project_config,
            repo_path=repo_path,
            source_decision_log=source_decision_log,
            source_metadata_log=source_metadata_log,
            source_artifact_path=source_artifact_path,
            log_writer=log_writer,
            repo_relative=repo_relative,
            allow_followup_combo=contract_decision.create_followup_issue,
        )
        _apply_review_execution_state(
            mutable_state,
            review_execution=review_execution,
            repo_relative=repo_relative,
        )
        steps.append(
            IssueCentricExecutionStep(
                name="human_review_comment",
                status=review_execution.status,
                log_path=repo_relative(review_execution.execution_log_path),
                note=review_execution.safe_stop_reason,
            )
        )
        close_note = ""
        final_status = review_execution.status
        followup_note = ""
        followup_execution = None
        close_execution = None
        review_sync_execution = None
        done_sync_execution = None
        post_review_state = mutable_state
        if review_execution.status == "completed":
            review_sync_execution = _run_current_issue_project_state_sync(
                lifecycle_stage="review",
                prepared=materialized.prepared,
                prior_state=prior_state,
                target_state=post_review_state,
                project_config=project_config,
                repo_path=repo_path,
                source_decision_log=source_decision_log,
                source_metadata_log=source_metadata_log,
                source_action_execution_log=repo_relative(review_execution.execution_log_path),
                step_name="current_issue_project_state_sync_review",
                steps=steps,
                log_writer=log_writer,
                repo_relative=repo_relative,
                execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
            )
            if review_sync_execution.status not in {"completed", "not_requested"}:
                final_status = "partial"
        if contract_decision.create_followup_issue and review_execution.status == "completed":
            followup_execution = execute_followup_issue_action_fn(
                materialized.prepared,
                prior_state=post_review_state,
                project_config=project_config,
                repo_path=repo_path,
                source_decision_log=source_decision_log,
                source_metadata_log=source_metadata_log,
                source_artifact_path=source_artifact_path,
                log_writer=log_writer,
                repo_relative=repo_relative,
                allow_human_review_combo=True,
            )
            _apply_followup_execution_state(
                mutable_state,
                followup_execution=followup_execution,
                repo_relative=repo_relative,
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="followup_issue_create",
                    status=followup_execution.status,
                    log_path=repo_relative(followup_execution.execution_log_path),
                    note=followup_execution.safe_stop_reason,
                )
            )
            if followup_execution.created_issue is not None:
                followup_note = (
                    f" follow-up issue: #{followup_execution.created_issue.number} "
                    f"{followup_execution.created_issue.url}"
                )
            if followup_execution.status != "completed":
                final_status = "partial"
            if contract_decision.close_current_issue and followup_execution.status == "completed":
                close_execution = execute_close_current_issue_fn(
                    materialized.prepared,
                    prior_state=prior_state,
                    project_config=project_config,
                    repo_path=repo_path,
                    source_decision_log=source_decision_log,
                    source_metadata_log=source_metadata_log,
                    source_action_execution_log=repo_relative(followup_execution.execution_log_path),
                    log_writer=log_writer,
                    repo_relative=repo_relative,
                    allow_human_review_close=True,
                    allow_human_review_followup_close=True,
                )
                _apply_close_execution_state(
                    mutable_state,
                    close_execution=close_execution,
                    repo_relative=repo_relative,
                )
                steps.append(
                    IssueCentricExecutionStep(
                        name="close_current_issue",
                        status=close_execution.status,
                        log_path=repo_relative(close_execution.execution_log_path),
                        note=close_execution.safe_stop_reason,
                    )
                )
                close_note = f" close: {repo_relative(close_execution.execution_log_path)}"
                if close_execution.status != "completed":
                    final_status = "partial"
                else:
                    done_sync_execution = _run_current_issue_project_state_sync(
                        lifecycle_stage="done",
                        prepared=materialized.prepared,
                        prior_state=mutable_state,
                        target_state=mutable_state,
                        project_config=project_config,
                        repo_path=repo_path,
                        source_decision_log=source_decision_log,
                        source_metadata_log=source_metadata_log,
                        source_action_execution_log=repo_relative(close_execution.execution_log_path),
                        step_name="current_issue_project_state_sync_done",
                        steps=steps,
                        log_writer=log_writer,
                        repo_relative=repo_relative,
                        execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
                    )
                    if done_sync_execution.status not in {"completed", "not_requested"}:
                        final_status = "partial"
            elif contract_decision.close_current_issue:
                mutable_state.update(
                    {
                        "last_issue_centric_close_status": "not_attempted_followup_blocked",
                        "last_issue_centric_close_order": "after_human_review_followup",
                    }
                )
                steps.append(
                    IssueCentricExecutionStep(
                        name="close_current_issue",
                        status="not_attempted_followup_blocked",
                        log_path="",
                        note="close_current_issue runs only after the review-followup path and any required Project sync complete.",
                    )
                )
                final_status = "partial"
            if not contract_decision.close_current_issue and followup_execution.status == "completed":
                followup_sync_execution = _run_current_issue_project_state_sync(
                    lifecycle_stage="followup_created",
                    prepared=materialized.prepared,
                    prior_state=mutable_state,
                    target_state=mutable_state,
                    project_config=project_config,
                    repo_path=repo_path,
                    source_decision_log=source_decision_log,
                    source_metadata_log=source_metadata_log,
                    source_action_execution_log=repo_relative(followup_execution.execution_log_path),
                    step_name="current_issue_project_state_sync_followup_created",
                    steps=steps,
                    log_writer=log_writer,
                    repo_relative=repo_relative,
                    execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
                )
                if followup_sync_execution.status not in {"completed", "not_requested"}:
                    final_status = "partial"
        elif contract_decision.close_current_issue and review_execution.status == "completed":
            close_execution = execute_close_current_issue_fn(
                materialized.prepared,
                prior_state=prior_state,
                project_config=project_config,
                repo_path=repo_path,
                source_decision_log=source_decision_log,
                source_metadata_log=source_metadata_log,
                source_action_execution_log=repo_relative(review_execution.execution_log_path),
                log_writer=log_writer,
                repo_relative=repo_relative,
                allow_human_review_close=True,
            )
            _apply_close_execution_state(
                mutable_state,
                close_execution=close_execution,
                repo_relative=repo_relative,
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="close_current_issue",
                    status=close_execution.status,
                    log_path=repo_relative(close_execution.execution_log_path),
                    note=close_execution.safe_stop_reason,
                )
            )
            close_note = f" close: {repo_relative(close_execution.execution_log_path)}"
            if close_execution.status != "completed":
                final_status = "partial"
            else:
                done_sync_execution = _run_current_issue_project_state_sync(
                    lifecycle_stage="done",
                    prepared=materialized.prepared,
                    prior_state=mutable_state,
                    target_state=mutable_state,
                    project_config=project_config,
                    repo_path=repo_path,
                    source_decision_log=source_decision_log,
                    source_metadata_log=source_metadata_log,
                    source_action_execution_log=repo_relative(close_execution.execution_log_path),
                    step_name="current_issue_project_state_sync_done",
                    steps=steps,
                    log_writer=log_writer,
                    repo_relative=repo_relative,
                    execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
                )
                if done_sync_execution.status not in {"completed", "not_requested"}:
                    final_status = "partial"
        elif contract_decision.close_current_issue:
            mutable_state.update(
                {
                    "last_issue_centric_close_status": "not_attempted_review_blocked",
                    "last_issue_centric_close_order": "after_human_review",
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="close_current_issue",
                    status="not_attempted_review_blocked",
                    log_path="",
                    note="close_current_issue runs only after review succeeds in this slice.",
                )
            )
            final_status = "partial"
        review_note = ""
        if review_execution.created_comment is not None:
            review_note = f" review comment: {review_execution.created_comment.url}"
        if review_execution.status != "completed":
            stop_label = (
                "issue-centric contract reply を検出しましたが、human_review_needed review execution を完了できず停止しました。"
            )
        elif contract_decision.create_followup_issue and followup_execution is not None:
            if (
                contract_decision.close_current_issue
                and close_execution is not None
                and close_execution.status == "completed"
            ):
                stop_label = (
                    "issue-centric contract reply を検出し、human_review_needed の review comment mutation / narrow follow-up issue create / narrow post-review close まで実行しました。"
                )
            elif followup_execution.status == "completed":
                stop_label = (
                    "issue-centric contract reply を検出し、human_review_needed の review comment mutation と narrow follow-up issue create まで実行しました。"
                )
            else:
                stop_label = (
                    "issue-centric contract reply を検出し、human_review_needed の review comment mutation までは完了しましたが、その後の narrow follow-up issue create で停止しました。"
                )
        elif review_execution.status == "completed" and contract_decision.close_current_issue:
            stop_label = (
                "issue-centric contract reply を検出し、human_review_needed の review comment mutation と narrow post-review close まで実行しました。"
            )
        elif review_execution.status == "completed":
            stop_label = (
                "issue-centric contract reply を検出し、human_review_needed の最小 review comment mutation まで実行しました。"
            )
        return _finalize_dispatch(
            matrix_path=(
                "human_review_followup_then_close"
                if contract_decision.create_followup_issue and contract_decision.close_current_issue
                else (
                    "human_review_followup"
                    if contract_decision.create_followup_issue
                    else (
                        "human_review_then_close"
                        if contract_decision.close_current_issue
                        else "human_review"
                    )
                )
            ),
            final_status=final_status,
            steps=steps,
            mutable_state=mutable_state,
            log_writer=log_writer,
            repo_relative=repo_relative,
            stop_message=(
                stop_label
                + f" decision log: {source_decision_log}"
                + f" metadata: {source_metadata_log}"
                + (f" artifact: {source_artifact_path}" if source_artifact_path else "")
                + f" review: {repo_relative(review_execution.execution_log_path)}"
                + review_note
                + followup_note
                + close_note
                + (
                    " issue_create + create_followup_issue / codex_run + create_followup_issue / Projects update の他 action 反映 はまだ未実装です。"
                    if contract_decision.create_followup_issue
                    else " human_review_needed + create_followup_issue / Projects update はまだ未実装です。"
                )
            ),
        )

    if decision_action == "no_action" and contract_decision.create_followup_issue:
        followup_execution = execute_followup_issue_action_fn(
            materialized.prepared,
            prior_state=prior_state,
            project_config=project_config,
            repo_path=repo_path,
            source_decision_log=source_decision_log,
            source_metadata_log=source_metadata_log,
            source_artifact_path=source_artifact_path,
            log_writer=log_writer,
            repo_relative=repo_relative,
        )
        _apply_followup_execution_state(
            mutable_state,
            followup_execution=followup_execution,
            repo_relative=repo_relative,
        )
        steps.append(
            IssueCentricExecutionStep(
                name="followup_issue_create",
                status=followup_execution.status,
                log_path=repo_relative(followup_execution.execution_log_path),
                note=followup_execution.safe_stop_reason,
            )
        )
        close_note = ""
        final_status = followup_execution.status
        done_sync_execution = None
        if contract_decision.close_current_issue and followup_execution.status == "completed":
            close_execution = execute_close_current_issue_fn(
                materialized.prepared,
                prior_state=prior_state,
                project_config=project_config,
                repo_path=repo_path,
                source_decision_log=source_decision_log,
                source_metadata_log=source_metadata_log,
                source_action_execution_log=repo_relative(followup_execution.execution_log_path),
                log_writer=log_writer,
                repo_relative=repo_relative,
            )
            _apply_close_execution_state(
                mutable_state,
                close_execution=close_execution,
                repo_relative=repo_relative,
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="close_current_issue",
                    status=close_execution.status,
                    log_path=repo_relative(close_execution.execution_log_path),
                    note=close_execution.safe_stop_reason,
                )
            )
            close_note = f" close: {repo_relative(close_execution.execution_log_path)}"
            if close_execution.status != "completed":
                final_status = "partial"
            else:
                done_sync_execution = _run_current_issue_project_state_sync(
                    lifecycle_stage="done",
                    prepared=materialized.prepared,
                    prior_state=mutable_state,
                    target_state=mutable_state,
                    project_config=project_config,
                    repo_path=repo_path,
                    source_decision_log=source_decision_log,
                    source_metadata_log=source_metadata_log,
                    source_action_execution_log=repo_relative(close_execution.execution_log_path),
                    step_name="current_issue_project_state_sync_done",
                    steps=steps,
                    log_writer=log_writer,
                    repo_relative=repo_relative,
                    execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
                )
                if done_sync_execution.status not in {"completed", "not_requested"}:
                    final_status = "partial"
        elif contract_decision.close_current_issue:
            mutable_state.update(
                {
                    "last_issue_centric_close_status": "not_attempted_followup_blocked",
                    "last_issue_centric_close_order": "after_followup_issue_create",
                }
            )
            steps.append(
                IssueCentricExecutionStep(
                    name="close_current_issue",
                    status="not_attempted_followup_blocked",
                    log_path="",
                    note="close_current_issue runs only after follow-up issue create + project sync completes.",
                )
            )
            final_status = "partial"
        if not contract_decision.close_current_issue and followup_execution.status == "completed":
            followup_sync_execution = _run_current_issue_project_state_sync(
                lifecycle_stage="followup_created",
                prepared=materialized.prepared,
                prior_state=mutable_state,
                target_state=mutable_state,
                project_config=project_config,
                repo_path=repo_path,
                source_decision_log=source_decision_log,
                source_metadata_log=source_metadata_log,
                source_action_execution_log=repo_relative(followup_execution.execution_log_path),
                step_name="current_issue_project_state_sync_followup_created",
                steps=steps,
                log_writer=log_writer,
                repo_relative=repo_relative,
                execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
            )
            if followup_sync_execution.status not in {"completed", "not_requested"}:
                final_status = "partial"
        followup_note = ""
        if followup_execution.created_issue is not None:
            followup_note = (
                f" created follow-up issue: #{followup_execution.created_issue.number} "
                f"{followup_execution.created_issue.url}"
            )
        return _finalize_dispatch(
            matrix_path=(
                "no_action_followup_then_close"
                if contract_decision.close_current_issue
                else "no_action_followup"
            ),
            final_status=final_status,
            steps=steps,
            mutable_state=mutable_state,
            log_writer=log_writer,
            repo_relative=repo_relative,
            stop_message=(
                "issue-centric contract reply を検出し、no_action + create_followup_issue の narrow execution slice まで実行しました。"
                f" decision log: {source_decision_log}"
                f" metadata: {source_metadata_log}"
                + (f" artifact: {source_artifact_path}" if source_artifact_path else "")
                + f" execution: {repo_relative(followup_execution.execution_log_path)}"
                + followup_note
                + (
                    f" project item: {followup_execution.project_item_id}"
                    if followup_execution.project_item_id
                    else ""
                )
                + close_note
                + " create_followup_issue の一般化 / 他 action との組み合わせ / Projects update の全面対応 はまだ未実装です。"
            ),
        )

    if contract_decision.close_current_issue and decision_action == "no_action":
        close_execution = execute_close_current_issue_fn(
            materialized.prepared,
            prior_state=prior_state,
            project_config=project_config,
            repo_path=repo_path,
            source_decision_log=source_decision_log,
            source_metadata_log=source_metadata_log,
            source_action_execution_log="",
            log_writer=log_writer,
            repo_relative=repo_relative,
        )
        _apply_close_execution_state(
            mutable_state,
            close_execution=close_execution,
            repo_relative=repo_relative,
        )
        steps.append(
            IssueCentricExecutionStep(
                name="close_current_issue",
                status=close_execution.status,
                log_path=repo_relative(close_execution.execution_log_path),
                note=close_execution.safe_stop_reason,
            )
        )
        final_status = close_execution.status
        parent_update_note = ""
        if close_execution.status == "completed":
            done_sync_execution = _run_current_issue_project_state_sync(
                lifecycle_stage="done",
                prepared=materialized.prepared,
                prior_state=mutable_state,
                target_state=mutable_state,
                project_config=project_config,
                repo_path=repo_path,
                source_decision_log=source_decision_log,
                source_metadata_log=source_metadata_log,
                source_action_execution_log=repo_relative(close_execution.execution_log_path),
                step_name="current_issue_project_state_sync_done",
                steps=steps,
                log_writer=log_writer,
                repo_relative=repo_relative,
                execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync_fn,
            )
            if done_sync_execution.status not in {"completed", "not_requested"}:
                final_status = "partial"
            if execute_parent_issue_update_fn is not None:
                parent_update_execution = execute_parent_issue_update_fn(
                    close_execution=close_execution,
                    prior_state=mutable_state,
                    source_decision_log=source_decision_log,
                    source_metadata_log=source_metadata_log,
                    source_action_execution_log=repo_relative(close_execution.execution_log_path),
                    log_writer=log_writer,
                    repo_relative=repo_relative,
                )
                _apply_parent_issue_update_state(
                    mutable_state,
                    parent_update_execution=parent_update_execution,
                    repo_relative=repo_relative,
                )
                steps.append(
                    IssueCentricExecutionStep(
                        name="parent_issue_update_after_close",
                        status=parent_update_execution.status,
                        log_path=repo_relative(parent_update_execution.execution_log_path),
                        note=parent_update_execution.safe_stop_reason,
                    )
                )
                if parent_update_execution.status not in {"completed", "not_requested"}:
                    final_status = "partial"
                parent_comment = getattr(parent_update_execution, "created_comment", None)
                if parent_comment is not None:
                    parent_update_note = f" parent update: {getattr(parent_comment, 'url', '')}"
        return _finalize_dispatch(
            matrix_path="no_action_close",
            final_status=final_status,
            steps=steps,
            mutable_state=mutable_state,
            log_writer=log_writer,
            repo_relative=repo_relative,
            stop_message=(
                "issue-centric contract reply を検出し、close_current_issue の最小 mutation slice まで実行しました。"
                f" decision log: {source_decision_log}"
                f" metadata: {source_metadata_log}"
                + f" close: {repo_relative(close_execution.execution_log_path)}"
                + parent_update_note
                + (f" action: {decision_action}" if decision_action != "no_action" else "")
                + " create_followup_issue mutation / review automation / Projects update はまだ未実装です。"
            ),
        )

    steps.append(
        IssueCentricExecutionStep(
            name="prepared_artifact_only",
            status="prepared_only",
            log_path=source_artifact_path,
            note=materialized.safe_stop_reason,
        )
    )
    return _finalize_dispatch(
        matrix_path="prepared_artifact_only",
        final_status="prepared_only",
        steps=steps,
        mutable_state=mutable_state,
        log_writer=log_writer,
        repo_relative=repo_relative,
        stop_message=(
            "issue-centric contract reply を検出し、BODY base64 transport の prepared artifact まで作成しました。"
            " issue create / codex_run / human_review_needed / close_current_issue の narrow execution 以外、GitHub mutation の広い接続、state machine 切替はまだ未実装です。"
            f" raw dump: {source_raw_log}"
            f" decision log: {source_decision_log}"
            f" metadata: {source_metadata_log}"
            + (f" artifact: {source_artifact_path}" if source_artifact_path else "")
        ),
    )


def _apply_issue_create_execution_state(
    target_state: dict[str, object],
    *,
    execution: object,
    repo_relative: Callable[[Path], str],
) -> None:
    """Apply issue_create execution result to mutable state.

    **Next-cycle contract** — the following state fields are written for the
    next request cycle:

    * ``last_issue_centric_created_issue_*`` — the newly created primary issue;
      ``_finalize_dispatch`` promotes this to ``last_issue_centric_principal_issue``
      via the normalized summary's ``principal_issue_candidate``.
    * ``last_issue_centric_primary_issue_*`` — same issue, stored under the
      ``primary_issue`` namespace for follow-up combo flows.
    * ``last_issue_centric_project_sync_status / url / item_id / *_field / *_value``
      — project-board state for the created issue.

    The next request cycle reads ``last_issue_centric_principal_issue`` (written
    by ``_finalize_dispatch``) as its primary target.  No ``resolved_issue`` is
    written here — that field is reserved for ``codex_run`` / review paths.
    """
    target_state.update(
        {
            "last_issue_centric_execution_status": execution.status,
            "last_issue_centric_execution_log": repo_relative(execution.execution_log_path),
            "last_issue_centric_created_issue_number": (
                str(execution.created_issue.number) if execution.created_issue is not None else ""
            ),
            "last_issue_centric_created_issue_url": (
                execution.created_issue.url if execution.created_issue is not None else ""
            ),
            "last_issue_centric_created_issue_title": (
                execution.created_issue.title if execution.created_issue is not None else ""
            ),
            "last_issue_centric_project_sync_status": execution.project_sync_status,
            "last_issue_centric_project_url": execution.project_url,
            "last_issue_centric_project_item_id": execution.project_item_id,
            "last_issue_centric_project_state_field": execution.project_state_field_name,
            "last_issue_centric_project_state_value": execution.project_state_value_name,
            "last_issue_centric_primary_issue_number": (
                str(execution.created_issue.number) if execution.created_issue is not None else ""
            ),
            "last_issue_centric_primary_issue_url": (
                execution.created_issue.url if execution.created_issue is not None else ""
            ),
            "last_issue_centric_primary_issue_title": (
                execution.created_issue.title if execution.created_issue is not None else ""
            ),
            "last_issue_centric_primary_project_sync_status": execution.project_sync_status,
            "last_issue_centric_primary_project_url": execution.project_url,
            "last_issue_centric_primary_project_item_id": execution.project_item_id,
            "last_issue_centric_primary_project_state_field": execution.project_state_field_name,
            "last_issue_centric_primary_project_state_value": execution.project_state_value_name,
            "last_issue_centric_stop_reason": execution.safe_stop_reason,
            "chatgpt_decision_note": execution.safe_stop_reason,
        }
    )


def _apply_codex_execution_state(
    target_state: dict[str, object],
    *,
    execution: object,
    repo_relative: Callable[[Path], str],
) -> None:
    """Apply codex_run execution result to mutable state.

    **Next-cycle contract** — the following state fields are written for the
    next request cycle:

    * ``last_issue_centric_resolved_issue`` — the issue closed/resolved by
      this Codex run.  Used as the 3rd-priority fallback in the target
      resolution chain when ``next_request_target`` and ``principal_issue``
      are both absent.
    * ``last_issue_centric_trigger_comment_*`` — the trigger comment posted
      to start the Codex run.
    * ``last_issue_centric_launch_status`` — whether the Codex run was
      successfully launched.

    ``principal_issue`` is **not** written here; it is derived by
    ``_finalize_dispatch`` from the normalized summary.
    """
    target_state.update(
        {
            "last_issue_centric_execution_status": execution.status,
            "last_issue_centric_execution_log": repo_relative(execution.execution_log_path),
            "last_issue_centric_resolved_issue": (
                execution.resolved_issue.issue_url if execution.resolved_issue is not None else ""
            ),
            "last_issue_centric_trigger_comment_id": (
                str(execution.created_comment.comment_id)
                if execution.created_comment is not None
                else ""
            ),
            "last_issue_centric_trigger_comment_url": (
                execution.created_comment.url if execution.created_comment is not None else ""
            ),
            "last_issue_centric_execution_payload_log": (
                repo_relative(execution.payload_log_path)
                if execution.payload_log_path is not None
                else ""
            ),
            "last_issue_centric_launch_status": execution.launch_status,
            "last_issue_centric_stop_reason": execution.safe_stop_reason,
            "chatgpt_decision_note": execution.safe_stop_reason,
        }
    )


def _apply_close_execution_state(
    target_state: dict[str, object],
    *,
    close_execution: object,
    repo_relative: Callable[[Path], str],
) -> None:
    target_state.update(
        {
            "last_issue_centric_close_status": close_execution.close_status,
            "last_issue_centric_close_log": repo_relative(close_execution.execution_log_path),
            "last_issue_centric_closed_issue_number": (
                str(close_execution.issue_after.number)
                if close_execution.issue_after is not None
                else (
                    str(close_execution.issue_before.number)
                    if close_execution.issue_before is not None
                    else ""
                )
            ),
            "last_issue_centric_closed_issue_url": (
                close_execution.issue_after.url
                if close_execution.issue_after is not None
                else (
                    close_execution.issue_before.url
                    if close_execution.issue_before is not None
                    else ""
                )
            ),
            "last_issue_centric_closed_issue_title": (
                close_execution.issue_after.title
                if close_execution.issue_after is not None
                else (
                    close_execution.issue_before.title
                    if close_execution.issue_before is not None
                    else ""
                )
            ),
            "last_issue_centric_close_order": close_execution.close_order,
            "last_issue_centric_stop_reason": close_execution.safe_stop_reason,
            "chatgpt_decision_note": close_execution.safe_stop_reason,
        }
    )


def _apply_review_execution_state(
    target_state: dict[str, object],
    *,
    review_execution: object,
    repo_relative: Callable[[Path], str],
) -> None:
    """Apply human_review_needed execution result to mutable state.

    **Next-cycle contract** — the following state fields are written for the
    next request cycle:

    * ``last_issue_centric_resolved_issue`` — carries the review target
      (i.e. the issue awaiting human review).  Used as the 3rd-priority
      fallback in the target resolution chain.  Falls back to the previous
      ``last_issue_centric_resolved_issue`` value when the review execution
      did not produce a new resolved issue.
    * ``last_issue_centric_review_*`` — review status, log, comment, and
      close policy for the review action.

    ``principal_issue`` is determined by ``_finalize_dispatch`` via the
    normalized summary; this helper does not write it.
    """
    target_state.update(
        {
            "last_issue_centric_review_status": review_execution.review_status,
            "last_issue_centric_review_log": repo_relative(review_execution.execution_log_path),
            "last_issue_centric_review_comment_id": (
                str(review_execution.created_comment.comment_id)
                if review_execution.created_comment is not None
                else ""
            ),
            "last_issue_centric_review_comment_url": (
                review_execution.created_comment.url
                if review_execution.created_comment is not None
                else ""
            ),
            "last_issue_centric_review_close_policy": review_execution.close_policy,
            "last_issue_centric_resolved_issue": (
                review_execution.resolved_issue.issue_url
                if review_execution.resolved_issue is not None
                else str(target_state.get("last_issue_centric_resolved_issue", "")).strip()
            ),
            "last_issue_centric_stop_reason": review_execution.safe_stop_reason,
            "chatgpt_decision_note": review_execution.safe_stop_reason,
        }
    )


def _apply_followup_execution_state(
    target_state: dict[str, object],
    *,
    followup_execution: object,
    repo_relative: Callable[[Path], str],
) -> None:
    """Apply follow-up issue creation result to mutable state.

    **Next-cycle contract** — the following state fields are written for the
    next request cycle:

    * ``last_issue_centric_created_issue_*`` — the newly created follow-up
      issue.  This is distinct from the primary issue created in
      ``issue_create`` flows.
    * ``last_issue_centric_followup_issue_*`` — follow-up issue URL/number/
      title stored under the ``followup_issue`` namespace.
    * ``last_issue_centric_followup_parent_issue`` — the parent issue whose
      work the follow-up continues.  The normalized summary uses this to
      decide whether ``next_request_target`` should point to the follow-up
      or to the parent.
    * ``last_issue_centric_project_sync_*`` — project-board state for the
      follow-up issue.

    After a follow-up combo, ``_finalize_dispatch`` typically promotes the
    follow-up issue to ``last_issue_centric_principal_issue`` so that the
    next request cycle targets it.
    """
    target_state.update(
        {
            "last_issue_centric_execution_status": followup_execution.status,
            "last_issue_centric_execution_log": repo_relative(followup_execution.execution_log_path),
            "last_issue_centric_created_issue_number": (
                str(followup_execution.created_issue.number)
                if followup_execution.created_issue is not None
                else ""
            ),
            "last_issue_centric_created_issue_url": (
                followup_execution.created_issue.url
                if followup_execution.created_issue is not None
                else ""
            ),
            "last_issue_centric_created_issue_title": (
                followup_execution.created_issue.title
                if followup_execution.created_issue is not None
                else ""
            ),
            "last_issue_centric_followup_status": followup_execution.followup_status,
            "last_issue_centric_followup_log": repo_relative(followup_execution.execution_log_path),
            "last_issue_centric_followup_parent_issue": (
                followup_execution.parent_issue.issue_url
                if followup_execution.parent_issue is not None
                else ""
            ),
            "last_issue_centric_followup_issue_number": (
                str(followup_execution.created_issue.number)
                if followup_execution.created_issue is not None
                else ""
            ),
            "last_issue_centric_followup_issue_url": (
                followup_execution.created_issue.url
                if followup_execution.created_issue is not None
                else ""
            ),
            "last_issue_centric_followup_issue_title": (
                followup_execution.created_issue.title
                if followup_execution.created_issue is not None
                else ""
            ),
            "last_issue_centric_project_sync_status": followup_execution.project_sync_status,
            "last_issue_centric_project_url": followup_execution.project_url,
            "last_issue_centric_project_item_id": followup_execution.project_item_id,
            "last_issue_centric_project_state_field": followup_execution.project_state_field_name,
            "last_issue_centric_project_state_value": followup_execution.project_state_value_name,
            "last_issue_centric_followup_project_sync_status": followup_execution.project_sync_status,
            "last_issue_centric_followup_project_url": followup_execution.project_url,
            "last_issue_centric_followup_project_item_id": followup_execution.project_item_id,
            "last_issue_centric_followup_project_state_field": followup_execution.project_state_field_name,
            "last_issue_centric_followup_project_state_value": followup_execution.project_state_value_name,
            "last_issue_centric_stop_reason": followup_execution.safe_stop_reason,
            "chatgpt_decision_note": followup_execution.safe_stop_reason,
        }
    )


def _apply_current_issue_project_state_sync_state(
    target_state: dict[str, object],
    *,
    sync_execution: object,
    repo_relative: Callable[[Path], str],
) -> None:
    if sync_execution.status == "not_requested":
        return
    current_item_id = str(target_state.get("last_issue_centric_current_project_item_id", "")).strip()
    current_project_url = str(target_state.get("last_issue_centric_current_project_url", "")).strip()
    target_state.update(
        {
            "last_issue_centric_current_project_item_id": sync_execution.project_item_id or current_item_id,
            "last_issue_centric_current_project_url": sync_execution.project_url or current_project_url,
            "last_issue_centric_lifecycle_sync_status": sync_execution.sync_status,
            "last_issue_centric_lifecycle_sync_log": repo_relative(sync_execution.execution_log_path),
            "last_issue_centric_lifecycle_sync_issue": (
                sync_execution.resolved_issue.issue_url
                if sync_execution.resolved_issue is not None
                else str(target_state.get("last_issue_centric_lifecycle_sync_issue", "")).strip()
            ),
            "last_issue_centric_lifecycle_sync_stage": sync_execution.lifecycle_stage,
            "last_issue_centric_lifecycle_sync_project_url": sync_execution.project_url,
            "last_issue_centric_lifecycle_sync_project_item_id": sync_execution.project_item_id,
            "last_issue_centric_lifecycle_sync_state_field": sync_execution.project_state_field_name,
            "last_issue_centric_lifecycle_sync_state_value": sync_execution.project_state_value_name,
            "last_issue_centric_stop_reason": sync_execution.safe_stop_reason,
            "chatgpt_decision_note": sync_execution.safe_stop_reason,
        }
    )


def _apply_parent_issue_update_state(
    target_state: dict[str, object],
    *,
    parent_update_execution: object,
    repo_relative: Callable[[Path], str],
) -> None:
    target_state.update(
        {
            "last_issue_centric_parent_update_status": str(
                getattr(parent_update_execution, "update_status", "")
            ).strip(),
            "last_issue_centric_parent_update_log": repo_relative(
                getattr(parent_update_execution, "execution_log_path")
            ),
            "last_issue_centric_parent_update_issue": (
                getattr(getattr(parent_update_execution, "resolved_parent_issue", None), "issue_url", "") or ""
            ),
            "last_issue_centric_parent_update_comment_id": str(
                getattr(getattr(parent_update_execution, "created_comment", None), "comment_id", "") or ""
            ).strip(),
            "last_issue_centric_parent_update_comment_url": (
                getattr(getattr(parent_update_execution, "created_comment", None), "url", "") or ""
            ),
            "last_issue_centric_parent_update_closed_issue": str(
                getattr(parent_update_execution, "closed_issue_url", "") or ""
            ).strip(),
            "last_issue_centric_stop_reason": getattr(parent_update_execution, "safe_stop_reason", ""),
            "chatgpt_decision_note": getattr(parent_update_execution, "safe_stop_reason", ""),
        }
    )


def _run_current_issue_project_state_sync(
    *,
    lifecycle_stage: str,
    prepared: object,
    prior_state: Mapping[str, Any],
    target_state: dict[str, object],
    project_config: Mapping[str, Any],
    repo_path: Path,
    source_decision_log: str,
    source_metadata_log: str,
    source_action_execution_log: str,
    step_name: str,
    steps: list[IssueCentricExecutionStep],
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    execute_current_issue_project_state_sync_fn: Callable[..., object],
) -> object:
    sync_execution = execute_current_issue_project_state_sync_fn(
        prepared,
        lifecycle_stage=lifecycle_stage,
        prior_state=target_state,
        project_config=project_config,
        repo_path=repo_path,
        source_decision_log=source_decision_log,
        source_metadata_log=source_metadata_log,
        source_action_execution_log=source_action_execution_log,
        log_writer=log_writer,
        repo_relative=repo_relative,
    )
    if sync_execution.status != "not_requested":
        _apply_current_issue_project_state_sync_state(
            target_state,
            sync_execution=sync_execution,
            repo_relative=repo_relative,
        )
        steps.append(
            IssueCentricExecutionStep(
                name=step_name,
                status=sync_execution.status,
                log_path=repo_relative(sync_execution.execution_log_path),
                note=sync_execution.safe_stop_reason,
            )
        )
    return sync_execution


@dataclass(frozen=True)
class _IcContinuationPayload:
    """Normalized next-cycle continuation fields collected from execution state.

    Built by :func:`_build_ic_continuation_payload` after all
    ``_apply_*_execution_state`` helpers and ``_finalize_dispatch`` have
    written their ``last_issue_centric_*`` fields.  Provides a single named
    view of everything the next request cycle will receive, making
    action-specific provenance explicit and testable without coupling callers
    to raw key strings.

    **Shared with** :class:`request_prompt_from_report._IcNextCycleContext` —
    the fields below that are also present in ``_IcNextCycleContext`` map to
    the same ``last_issue_centric_*`` state keys, so the priority chain
    ``resolved_next_request_target`` on the request side reads exactly what
    execution writes here.

    **Action-specific provenance**:

    * ``issue_create``  → ``created_issue_url`` / ``created_issue_number``
      are set; ``principal_issue`` is promoted from the created issue by
      ``_finalize_dispatch`` via the normalized summary.
    * ``codex_run``     → ``resolved_issue`` carries the closed/resolved
      issue URL; ``principal_issue`` is the issue that was being worked on.
    * ``human_review_needed`` → ``resolved_issue`` carries the review target.
    * follow-up combo  → ``followup_issue_url`` / ``followup_issue_number``
      are set; ``next_request_target`` is expected to point to the follow-up.
    * ``close_current_issue`` → ``close_order`` is non-empty.
    * ``no_action``    → only ``next_request_hint`` / ``next_request_target``
      carry useful signal; action-specific issue fields are typically empty.
    """

    # --- from _finalize_dispatch / normalized_summary ---
    principal_issue: str          # last_issue_centric_principal_issue
    principal_issue_kind: str     # last_issue_centric_principal_issue_kind
    next_request_hint: str        # last_issue_centric_next_request_hint
    # --- from _finalize_dispatch / runtime_snapshot ---
    next_request_target: str           # last_issue_centric_next_request_target
    next_request_target_source: str    # last_issue_centric_next_request_target_source
    # --- from dispatch entry (set before _apply_*) ---
    action: str                   # last_issue_centric_action
    target_issue: str             # last_issue_centric_target_issue
    # --- from codex_run / review _apply_* helpers ---
    resolved_issue: str           # last_issue_centric_resolved_issue
    # --- from issue_create / followup _apply_* helpers ---
    created_issue_number: str     # last_issue_centric_created_issue_number
    created_issue_url: str        # last_issue_centric_created_issue_url
    # --- from followup _apply_* helper only ---
    followup_issue_number: str    # last_issue_centric_followup_issue_number
    followup_issue_url: str       # last_issue_centric_followup_issue_url
    followup_parent_issue: str    # last_issue_centric_followup_parent_issue
    # --- from close _apply_* helper ---
    close_order: str              # last_issue_centric_close_order
    # --- general ---
    execution_status: str         # last_issue_centric_execution_status
    stop_reason: str              # last_issue_centric_stop_reason


def _build_ic_continuation_payload(state: dict[str, object]) -> _IcContinuationPayload:
    """Build an :class:`_IcContinuationPayload` from execution state.

    Should be called *after* all ``_apply_*_execution_state`` helpers and
    ``_finalize_dispatch`` have written their ``last_issue_centric_*`` fields.
    All values are stripped strings; missing keys yield empty strings.
    """
    return _IcContinuationPayload(
        principal_issue=str(state.get("last_issue_centric_principal_issue", "")).strip(),
        principal_issue_kind=str(state.get("last_issue_centric_principal_issue_kind", "")).strip(),
        next_request_hint=str(state.get("last_issue_centric_next_request_hint", "")).strip(),
        next_request_target=str(state.get("last_issue_centric_next_request_target", "")).strip(),
        next_request_target_source=str(state.get("last_issue_centric_next_request_target_source", "")).strip(),
        action=str(state.get("last_issue_centric_action", "")).strip(),
        target_issue=str(state.get("last_issue_centric_target_issue", "")).strip(),
        resolved_issue=str(state.get("last_issue_centric_resolved_issue", "")).strip(),
        created_issue_number=str(state.get("last_issue_centric_created_issue_number", "")).strip(),
        created_issue_url=str(state.get("last_issue_centric_created_issue_url", "")).strip(),
        followup_issue_number=str(state.get("last_issue_centric_followup_issue_number", "")).strip(),
        followup_issue_url=str(state.get("last_issue_centric_followup_issue_url", "")).strip(),
        followup_parent_issue=str(state.get("last_issue_centric_followup_parent_issue", "")).strip(),
        close_order=str(state.get("last_issue_centric_close_order", "")).strip(),
        execution_status=str(state.get("last_issue_centric_execution_status", "")).strip(),
        stop_reason=str(state.get("last_issue_centric_stop_reason", "")).strip(),
    )


def _apply_ic_continuation_fields(
    target_state: dict[str, object],
    *,
    normalized_summary: Mapping[str, object],
) -> None:
    """Write the next-cycle continuation fields derived from the normalized summary.

    Extracts ``principal_issue``, ``principal_issue_kind``, and
    ``next_request_hint`` from *normalized_summary* and writes them to
    *target_state* as ``last_issue_centric_*`` keys.

    These three fields form the **continuation contract from the normalized
    summary** — they are the bridge between the execution result and what the
    next request cycle can read via
    :func:`request_prompt_from_report._read_ic_next_cycle_context`.
    """
    principal_issue_candidate = normalized_summary.get("principal_issue_candidate")
    principal_issue_ref = ""
    principal_issue_kind = str(normalized_summary.get("principal_issue_kind", "")).strip()
    if isinstance(principal_issue_candidate, Mapping):
        principal_issue_ref = (
            str(principal_issue_candidate.get("url", "")).strip()
            or str(principal_issue_candidate.get("ref", "")).strip()
        )
    target_state.update(
        {
            "last_issue_centric_principal_issue": principal_issue_ref,
            "last_issue_centric_principal_issue_kind": principal_issue_kind,
            "last_issue_centric_next_request_hint": str(
                normalized_summary.get("next_request_hint", "")
            ).strip(),
        }
    )


def _build_ic_continuation_payload_from_normalized(
    normalized_summary: Mapping[str, object],
    state: dict[str, object],
) -> _IcContinuationPayload:
    """Build an :class:`_IcContinuationPayload` from normalized summary + execution state.

    This is the **writer constructor** — called during ``_finalize_dispatch``
    after all ``_apply_*_execution_state`` helpers have run.  It assembles the
    next-cycle continuation contract from two sources:

    * *normalized_summary* — provides ``principal_issue``,
      ``principal_issue_kind``, and ``next_request_hint`` (derived from the
      normalized view of the execution result).
    * *state* — provides the action-specific fields (``resolved_issue``,
      ``created_issue_*``, ``followup_*``, ``close_order``, etc.) that were
      already written by the ``_apply_*_execution_state`` helpers.

    ``next_request_target`` and ``next_request_target_source`` are set to
    ``""`` here — they are filled in by the ``runtime_snapshot`` step later
    in ``_finalize_dispatch``.

    Contrast with :func:`_build_ic_continuation_payload` (the **reader**),
    which reconstructs a payload snapshot from already-saved state after the
    full execution including the runtime_snapshot step.
    """
    principal_issue_candidate = normalized_summary.get("principal_issue_candidate")
    principal_issue_ref = ""
    if isinstance(principal_issue_candidate, Mapping):
        principal_issue_ref = (
            str(principal_issue_candidate.get("url", "")).strip()
            or str(principal_issue_candidate.get("ref", "")).strip()
        )
    return _IcContinuationPayload(
        principal_issue=principal_issue_ref,
        principal_issue_kind=str(normalized_summary.get("principal_issue_kind", "")).strip(),
        next_request_hint=str(normalized_summary.get("next_request_hint", "")).strip(),
        next_request_target="",
        next_request_target_source="",
        action=str(state.get("last_issue_centric_action", "")).strip(),
        target_issue=str(state.get("last_issue_centric_target_issue", "")).strip(),
        resolved_issue=str(state.get("last_issue_centric_resolved_issue", "")).strip(),
        created_issue_number=str(state.get("last_issue_centric_created_issue_number", "")).strip(),
        created_issue_url=str(state.get("last_issue_centric_created_issue_url", "")).strip(),
        followup_issue_number=str(state.get("last_issue_centric_followup_issue_number", "")).strip(),
        followup_issue_url=str(state.get("last_issue_centric_followup_issue_url", "")).strip(),
        followup_parent_issue=str(state.get("last_issue_centric_followup_parent_issue", "")).strip(),
        close_order=str(state.get("last_issue_centric_close_order", "")).strip(),
        execution_status=str(state.get("last_issue_centric_execution_status", "")).strip(),
        stop_reason=str(state.get("last_issue_centric_stop_reason", "")).strip(),
    )


def _apply_ic_continuation_payload_to_state(
    target_state: dict[str, object],
    payload: _IcContinuationPayload,
) -> None:
    """Apply all :class:`_IcContinuationPayload` fields to *target_state*.

    This is the **writer applier** — writes the typed continuation contract to
    ``last_issue_centric_*`` state keys so the next request cycle can read
    them via :func:`request_prompt_from_report._read_ic_next_cycle_context`.

    Written field groups:

    * **Normalized-summary fields** (new values derived during
      ``_finalize_dispatch``): ``principal_issue``, ``principal_issue_kind``,
      ``next_request_hint``.
    * **Action-specific fields** (idempotent writes — already in state from
      the ``_apply_*_execution_state`` helpers): ``resolved_issue``,
      ``created_issue_*``, ``followup_*``, ``close_order``, ``action``,
      ``target_issue``.
    * **General** (idempotent): ``execution_status``, ``stop_reason``.

    ``next_request_target`` and ``next_request_target_source`` are **not**
    written here — they are set by the ``runtime_snapshot`` step in
    ``_finalize_dispatch`` and are absent from the writer payload by design.
    """
    target_state.update(
        {
            "last_issue_centric_principal_issue": payload.principal_issue,
            "last_issue_centric_principal_issue_kind": payload.principal_issue_kind,
            "last_issue_centric_next_request_hint": payload.next_request_hint,
            "last_issue_centric_action": payload.action,
            "last_issue_centric_target_issue": payload.target_issue,
            "last_issue_centric_resolved_issue": payload.resolved_issue,
            "last_issue_centric_created_issue_number": payload.created_issue_number,
            "last_issue_centric_created_issue_url": payload.created_issue_url,
            "last_issue_centric_followup_issue_number": payload.followup_issue_number,
            "last_issue_centric_followup_issue_url": payload.followup_issue_url,
            "last_issue_centric_followup_parent_issue": payload.followup_parent_issue,
            "last_issue_centric_close_order": payload.close_order,
            "last_issue_centric_execution_status": payload.execution_status,
            "last_issue_centric_stop_reason": payload.stop_reason,
        }
    )


def _finalize_dispatch(
    *,
    matrix_path: str,
    final_status: str,
    steps: Sequence[IssueCentricExecutionStep],
    mutable_state: dict[str, object],
    repo_root: Path | None = None,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    stop_message: str,
) -> IssueCentricDispatchResult:
    repo_root = repo_root or Path.cwd()
    stop_reason = str(mutable_state.get("last_issue_centric_stop_reason", "")).strip()
    normalized_summary = build_issue_centric_normalized_summary(
        matrix_path=matrix_path,
        final_status=final_status,
        state=mutable_state,
    )
    _normalized_lifecycle_sync = normalized_summary.get("project_lifecycle_sync")
    _lifecycle_sync_signal = (
        str(_normalized_lifecycle_sync.get("signal", "")).strip()
        if isinstance(_normalized_lifecycle_sync, Mapping)
        else ""
    )
    summary = {
        "matrix_path": matrix_path,
        "action": str(mutable_state.get("last_issue_centric_action", "")).strip(),
        "target_issue": str(mutable_state.get("last_issue_centric_target_issue", "")).strip(),
        "close_current_issue": bool(str(mutable_state.get("last_issue_centric_close_order", "")).strip()),
        "current_issue_lifecycle_sync_status": str(
            mutable_state.get("last_issue_centric_lifecycle_sync_status", "")
        ).strip(),
        "current_issue_lifecycle_sync_stage": str(
            mutable_state.get("last_issue_centric_lifecycle_sync_stage", "")
        ).strip(),
        "current_issue_lifecycle_sync_signal": _lifecycle_sync_signal,
        "final_status": final_status,
        "final_stop_reason": stop_reason,
        "step_sequence": [step.name for step in steps],
        "steps": [
            {
                "name": step.name,
                "status": step.status,
                "log_path": step.log_path,
                "note": step.note,
            }
            for step in steps
        ],
    }
    summary_log_path = log_writer(
        f"issue_centric_dispatch_{final_status}",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    normalized_summary_log_path = log_writer(
        f"issue_centric_normalized_summary_{final_status}",
        json.dumps(normalized_summary, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    principal_issue = normalized_summary.get("principal_issue_candidate")
    principal_issue_ref = ""
    principal_issue_kind = str(normalized_summary.get("principal_issue_kind", "")).strip()
    if isinstance(principal_issue, Mapping):
        principal_issue_ref = (
            str(principal_issue.get("url", "")).strip()
            or str(principal_issue.get("ref", "")).strip()
        )
    _continuation = _build_ic_continuation_payload_from_normalized(normalized_summary, mutable_state)
    _apply_ic_continuation_payload_to_state(mutable_state, _continuation)
    mutable_state.update(
        {
            "last_issue_centric_dispatch_result": repo_relative(summary_log_path),
            "last_issue_centric_normalized_summary": repo_relative(normalized_summary_log_path),
            "chatgpt_decision_note": stop_reason,
        }
    )
    runtime_snapshot = build_issue_centric_runtime_snapshot(
        mutable_state,
        repo_root=repo_root,
        snapshot_source="execution_finalize",
    )
    if runtime_snapshot is not None:
        runtime_snapshot_log_path = log_writer(
            f"issue_centric_runtime_snapshot_{final_status}",
            json.dumps(asdict(runtime_snapshot), ensure_ascii=False, indent=2) + "\n",
            "json",
        )
        mutable_state.update(
            {
                "last_issue_centric_runtime_snapshot": repo_relative(runtime_snapshot_log_path),
                "last_issue_centric_snapshot_status": runtime_snapshot.snapshot_status,
                "last_issue_centric_runtime_generation_id": runtime_snapshot.generation_id,
                "last_issue_centric_route_selected": runtime_snapshot.route_selected,
                "last_issue_centric_route_fallback_reason": runtime_snapshot.route_fallback_reason
                or runtime_snapshot.fallback_reason,
                "last_issue_centric_recovery_status": runtime_snapshot.recovery_status,
                "last_issue_centric_recovery_source": runtime_snapshot.recovery_source,
                "last_issue_centric_recovery_fallback_reason": runtime_snapshot.recovery_fallback_reason
                or runtime_snapshot.fallback_reason,
                "last_issue_centric_next_request_target": runtime_snapshot.target_issue,
                "last_issue_centric_next_request_target_source": runtime_snapshot.target_issue_source,
                "last_issue_centric_next_request_fallback_reason": runtime_snapshot.fallback_reason,
            }
        )
        runtime_mode = resolve_issue_centric_runtime_mode(mutable_state, repo_root=repo_root)
        if runtime_mode is not None:
            mutable_state.update(
                {
                    "last_issue_centric_runtime_mode": runtime_mode.runtime_mode,
                    "last_issue_centric_runtime_mode_reason": runtime_mode.runtime_mode_reason,
                    "last_issue_centric_runtime_mode_source": runtime_mode.runtime_mode_source,
                    "last_issue_centric_generation_lifecycle": runtime_mode.generation_lifecycle,
                    "last_issue_centric_generation_lifecycle_reason": runtime_mode.generation_lifecycle_reason,
                    "last_issue_centric_generation_lifecycle_source": runtime_mode.generation_lifecycle_source,
                    "last_issue_centric_prepared_generation_id": "",
                    "last_issue_centric_pending_generation_id": "",
                    "last_issue_centric_freshness_status": runtime_mode.freshness_status,
                    "last_issue_centric_freshness_reason": runtime_mode.freshness_reason,
                    "last_issue_centric_freshness_source": runtime_mode.freshness_source,
                    "last_issue_centric_invalidation_status": runtime_mode.invalidation_status,
                    "last_issue_centric_invalidation_reason": runtime_mode.invalidation_reason,
                    "last_issue_centric_invalidated_generation_id": (
                        runtime_mode.generation_id
                        if runtime_mode.invalidation_status
                        else ""
                    ),
                    "last_issue_centric_consumed_generation_id": "",
                }
            )
    _lc = bridge_lifecycle_sync_suffix(mutable_state)
    return IssueCentricDispatchResult(
        matrix_path=matrix_path,
        final_status=final_status,
        steps=tuple(steps),
        summary_log_path=summary_log_path,
        final_state=mutable_state,
        stop_message=stop_message + _lc,
    )
