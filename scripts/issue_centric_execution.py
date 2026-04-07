from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


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
    launch_runner: Callable[[dict[str, object], list[str] | None], int],
) -> IssueCentricDispatchResult:
    steps: list[IssueCentricExecutionStep] = []
    decision_action = contract_decision.action.value

    if contract_decision.create_followup_issue and decision_action not in {
        "no_action",
        "human_review_needed",
        "issue_create",
    }:
        unsupported_reason = (
            "create_followup_issue execution is currently implemented only for action=no_action, action=issue_create, and the narrow human_review_needed combo. "
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
                " issue_create combo は narrow happy path のみ対応しています。"
                f" decision log: {source_decision_log}"
                f" metadata: {source_metadata_log}"
                + unsupported_reason
            ),
        )

    if decision_action == "issue_create":
        if contract_decision.create_followup_issue and materialized.prepared.issue_body is None:
            blocked_reason = (
                "issue_create + create_followup_issue requires a prepared CHATGPT_ISSUE_BODY artifact before any issue mutation can run."
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
        if contract_decision.create_followup_issue and execution.status == "completed":
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

    if decision_action == "codex_run" and contract_decision.close_current_issue:
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
                    + " issue-centric Codex launch はまだ進めませんでした。"
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
        trigger_note = ""
        if execution.created_comment is not None:
            trigger_note = f" trigger comment: {execution.created_comment.url}"
        stop_label = (
            "issue-centric contract reply を検出し、codex_run を既存 Codex launch 入口へ narrow 接続しました。"
            if launch_result.status == "completed"
            else "issue-centric contract reply を検出し、codex_run launch 後の continuation handoff で停止しました。"
        )
        return _finalize_dispatch(
            matrix_path="codex_run_launch_and_continuation",
            final_status=launch_result.status,
            steps=steps,
            mutable_state=post_launch_state,
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
                + f" final mode: {launch_result.final_mode or 'unknown'}"
                + f" continuation status: {launch_result.continuation_status}"
                + " close_current_issue for codex_run / follow-up mutation / post-codex review automation はまだ未実装です。"
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
        if contract_decision.create_followup_issue and review_execution.status == "completed":
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
        return _finalize_dispatch(
            matrix_path="no_action_close",
            final_status=close_execution.status,
            steps=steps,
            mutable_state=mutable_state,
            log_writer=log_writer,
            repo_relative=repo_relative,
            stop_message=(
                "issue-centric contract reply を検出し、close_current_issue の最小 mutation slice まで実行しました。"
                f" decision log: {source_decision_log}"
                f" metadata: {source_metadata_log}"
                + f" close: {repo_relative(close_execution.execution_log_path)}"
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


def _finalize_dispatch(
    *,
    matrix_path: str,
    final_status: str,
    steps: Sequence[IssueCentricExecutionStep],
    mutable_state: dict[str, object],
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    stop_message: str,
) -> IssueCentricDispatchResult:
    stop_reason = str(mutable_state.get("last_issue_centric_stop_reason", "")).strip()
    summary = {
        "matrix_path": matrix_path,
        "action": str(mutable_state.get("last_issue_centric_action", "")).strip(),
        "target_issue": str(mutable_state.get("last_issue_centric_target_issue", "")).strip(),
        "close_current_issue": bool(str(mutable_state.get("last_issue_centric_close_order", "")).strip()),
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
    mutable_state.update(
        {
            "last_issue_centric_dispatch_result": repo_relative(summary_log_path),
            "last_issue_centric_stop_reason": stop_reason,
            "chatgpt_decision_note": stop_reason,
        }
    )
    return IssueCentricDispatchResult(
        matrix_path=matrix_path,
        final_status=final_status,
        steps=tuple(steps),
        summary_log_path=summary_log_path,
        final_state=mutable_state,
        stop_message=stop_message,
    )
