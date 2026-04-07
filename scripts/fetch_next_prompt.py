#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import launch_codex_once
from _bridge_common import (
    BridgeError,
    BridgeStop,
    build_human_review_auto_continue_request,
    clear_chat_rotation_fields,
    clear_error_fields,
    clear_pending_request_fields,
    extract_last_chatgpt_reply,
    guarded_main,
    load_project_config,
    load_state,
    log_text,
    mark_next_request_requires_rotation,
    next_request_rotation_reason,
    project_repo_path,
    read_pending_request_text,
    read_text,
    repo_relative,
    runtime_prompt_path,
    save_state,
    send_to_chatgpt,
    stage_prepared_request,
    stable_text_hash,
    should_rotate_before_next_chat_request,
    promote_pending_request,
    wait_for_prompt_reply_text,
    write_text,
)
from issue_centric_codex_launch import launch_issue_centric_codex_run
from issue_centric_close_current_issue import execute_close_current_issue
from issue_centric_human_review import execute_human_review_action
from issue_centric_contract import (
    IssueCentricContractError,
    maybe_parse_issue_centric_reply,
)
from issue_centric_codex_run import execute_codex_run_action
from issue_centric_followup_issue import execute_followup_issue_action
from issue_centric_issue_create import execute_issue_create_action
from issue_centric_transport import (
    IssueCentricTransportError,
    materialize_issue_centric_decision,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safari の現在 ChatGPT タブから最後の ChatGPT 返答ブロックを抽出します。")
    parser.add_argument(
        "--raw-file",
        default="",
        help="診断や再現テスト用に、会話全文 dump ファイルを直接読む",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Safari から返答を待つ最大秒数。0 の場合は browser_config.json を使う",
    )
    return parser.parse_args(argv)


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pending_request_hash = str(state.get("pending_request_hash", "")).strip()
    pending_request_source = str(state.get("pending_request_source", "")).strip()
    pending_request_signal = str(state.get("pending_request_signal", "")).strip()
    request_text = read_pending_request_text(state)
    if not (pending_request_hash and pending_request_source and request_text):
        raise BridgeError(
            "送信済みの ChatGPT request を確認できないため fetch できませんでした。"
            " request 送信から再開してください。"
        )

    rotation_requested = should_rotate_before_next_chat_request(state)
    rotation_reason = next_request_rotation_reason(state)
    if str(state.get("mode", "")).strip() == "await_late_completion":
        rotation_requested = True
        rotation_reason = rotation_reason or "late_completion"

    def handle_wait_event(event: object) -> None:
        nonlocal rotation_requested, rotation_reason
        event_name = str(getattr(event, "name", "")).strip()
        latest_text = str(getattr(event, "latest_text", "") or "")
        mutable_state = clear_error_fields(dict(load_state()))
        if event_name == "timeout_first":
            mutable_state["mode"] = "extended_wait"
        elif event_name == "late_completion_mode":
            mutable_state["mode"] = "await_late_completion"
            rotation_requested = True
            rotation_reason = "late_completion"
            mark_next_request_requires_rotation(mutable_state, rotation_reason)
        save_state(mutable_state)
        stage_log = log_text(event_name, latest_text, suffix="txt")
        print(f"{event_name}: {stage_log}")

    if args.raw_file:
        raw_text = read_text(Path(args.raw_file)).strip()
        if not raw_text:
            raise ValueError(f"raw file を読めませんでした: {args.raw_file}")
    else:
        raw_text = wait_for_prompt_reply_text(
            timeout_seconds=args.timeout_seconds or None,
            request_text=request_text or None,
            stage_callback=handle_wait_event,
            allow_project_page_wait=(pending_request_signal == "submitted_unconfirmed"),
        )
    raw_log = log_text("raw_chatgpt_prompt_dump", raw_text, suffix="txt")
    try:
        contract_decision = maybe_parse_issue_centric_reply(raw_text, after_text=request_text or None)
    except IssueCentricContractError as exc:
        raise BridgeError(f"issue-centric contract reply が不正でした: {exc}") from exc
    if contract_decision is not None:
        def apply_close_execution_state(
            target_state: dict[str, object],
            *,
            close_execution: object,
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

        def apply_review_execution_state(
            target_state: dict[str, object],
            *,
            review_execution: object,
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

        def apply_followup_execution_state(
            target_state: dict[str, object],
            *,
            followup_execution: object,
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
                    "last_issue_centric_project_sync_status": followup_execution.project_sync_status,
                    "last_issue_centric_project_url": followup_execution.project_url,
                    "last_issue_centric_project_item_id": followup_execution.project_item_id,
                    "last_issue_centric_project_state_field": followup_execution.project_state_field_name,
                    "last_issue_centric_project_state_value": followup_execution.project_state_value_name,
                    "last_issue_centric_stop_reason": followup_execution.safe_stop_reason,
                    "chatgpt_decision_note": followup_execution.safe_stop_reason,
                }
            )

        decision_log = log_text(
            "extracted_issue_centric_contract",
            contract_decision.render_debug_markdown(),
            suffix="md",
        )
        try:
            materialized = materialize_issue_centric_decision(
                contract_decision,
                log_writer=log_text,
                repo_relative=repo_relative,
                raw_log_path=raw_log,
                decision_log_path=decision_log,
            )
        except IssueCentricTransportError as exc:
            raise BridgeError(f"issue-centric contract transport を準備できませんでした: {exc}") from exc

        reply_hash = stable_text_hash(contract_decision.raw_segment.strip())
        mutable_state = clear_error_fields(dict(state))
        clear_pending_request_fields(mutable_state)
        mutable_state.update(
            {
                "mode": "awaiting_user",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": False,
                "need_codex_run": False,
                "last_prompt_file": "",
                "last_processed_request_hash": pending_request_hash
                or str(state.get("last_processed_request_hash", "")).strip(),
                "last_processed_reply_hash": reply_hash,
                "chatgpt_decision": f"issue_centric:{contract_decision.action.value}",
                "chatgpt_decision_note": materialized.safe_stop_reason,
                "last_issue_centric_action": contract_decision.action.value,
                "last_issue_centric_target_issue": contract_decision.target_issue or "none",
                "last_issue_centric_decision_log": repo_relative(decision_log),
                "last_issue_centric_metadata_log": repo_relative(materialized.metadata_log_path),
                "last_issue_centric_artifact_file": (
                    repo_relative(materialized.artifact_log_path)
                    if materialized.artifact_log_path is not None
                    else ""
                ),
                "last_issue_centric_artifact_kind": (
                    materialized.prepared.primary_body.kind.value
                    if materialized.prepared.primary_body is not None
                    else ""
                ),
                "last_issue_centric_execution_status": "",
                "last_issue_centric_execution_log": "",
                "last_issue_centric_created_issue_number": "",
                "last_issue_centric_created_issue_url": "",
                "last_issue_centric_created_issue_title": "",
                "last_issue_centric_resolved_issue": "",
                "last_issue_centric_trigger_comment_id": "",
                "last_issue_centric_trigger_comment_url": "",
                "last_issue_centric_execution_payload_log": "",
                "last_issue_centric_launch_status": "",
                "last_issue_centric_launch_entrypoint": "",
                "last_issue_centric_launch_prompt_log": "",
                "last_issue_centric_launch_log": "",
                "last_issue_centric_continuation_status": "",
                "last_issue_centric_continuation_log": "",
                "last_issue_centric_report_status": "",
                "last_issue_centric_report_file": "",
                "last_issue_centric_project_sync_status": "",
                "last_issue_centric_project_url": "",
                "last_issue_centric_project_item_id": "",
                "last_issue_centric_project_state_field": "",
                "last_issue_centric_project_state_value": "",
                "last_issue_centric_followup_status": "",
                "last_issue_centric_followup_log": "",
                "last_issue_centric_followup_parent_issue": "",
                "last_issue_centric_close_status": "",
                "last_issue_centric_close_log": "",
                "last_issue_centric_closed_issue_number": "",
                "last_issue_centric_closed_issue_url": "",
                "last_issue_centric_closed_issue_title": "",
                "last_issue_centric_close_order": "",
                "last_issue_centric_review_status": "",
                "last_issue_centric_review_log": "",
                "last_issue_centric_review_comment_id": "",
                "last_issue_centric_review_comment_url": "",
                "last_issue_centric_review_close_policy": "",
                "last_issue_centric_stop_reason": materialized.safe_stop_reason,
            }
        )

        if contract_decision.create_followup_issue and contract_decision.action.value != "no_action":
            unsupported_reason = (
                "create_followup_issue execution is currently implemented only for action=no_action. "
                f"The combination action={contract_decision.action.value} + create_followup_issue=true is blocked in this slice."
            )
            mutable_state.update(
                {
                    "last_issue_centric_followup_status": "blocked_unsupported_action_combo",
                    "last_issue_centric_stop_reason": unsupported_reason,
                    "chatgpt_decision_note": unsupported_reason,
                }
            )
            save_state(mutable_state)
            raise BridgeStop(
                "issue-centric contract reply を検出しましたが、create_followup_issue の narrow execution は action=no_action にだけ対応しています。"
                f" decision log: {repo_relative(decision_log)}"
                f" metadata: {repo_relative(materialized.metadata_log_path)}"
                + unsupported_reason
            )

        if contract_decision.action.value == "issue_create":
            project_config = load_project_config()
            execution = execute_issue_create_action(
                materialized.prepared,
                project_config=project_config,
                repo_path=project_repo_path(project_config),
                source_decision_log=repo_relative(decision_log),
                source_metadata_log=repo_relative(materialized.metadata_log_path),
                source_artifact_path=(
                    repo_relative(materialized.artifact_log_path)
                    if materialized.artifact_log_path is not None
                    else ""
                ),
                log_writer=log_text,
                repo_relative=repo_relative,
            )
            mutable_state.update(
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
                    "last_issue_centric_stop_reason": execution.safe_stop_reason,
                    "chatgpt_decision_note": execution.safe_stop_reason,
                }
            )
            close_note = ""
            if contract_decision.close_current_issue and execution.status == "completed":
                close_execution = execute_close_current_issue(
                    materialized.prepared,
                    prior_state=state,
                    project_config=project_config,
                    repo_path=project_repo_path(project_config),
                    source_decision_log=repo_relative(decision_log),
                    source_metadata_log=repo_relative(materialized.metadata_log_path),
                    source_action_execution_log=repo_relative(execution.execution_log_path),
                    log_writer=log_text,
                    repo_relative=repo_relative,
                )
                apply_close_execution_state(mutable_state, close_execution=close_execution)
                close_note = f" close log: {repo_relative(close_execution.execution_log_path)}"
            elif contract_decision.close_current_issue:
                mutable_state.update(
                    {
                        "last_issue_centric_close_status": "not_attempted_primary_action_blocked",
                        "last_issue_centric_close_order": "after_issue_create",
                    }
                )
            save_state(mutable_state)
            issue_note = ""
            if execution.created_issue is not None:
                issue_note = (
                    f" created issue: #{execution.created_issue.number} "
                    f"{execution.created_issue.url}"
                )
            raise BridgeStop(
                "issue-centric contract reply を検出し、issue_create の最小 execution slice まで実行しました。"
                f" decision log: {repo_relative(decision_log)}"
                f" metadata: {repo_relative(materialized.metadata_log_path)}"
                + (
                    f" artifact: {repo_relative(materialized.artifact_log_path)}"
                    if materialized.artifact_log_path is not None
                    else ""
                )
                + f" execution: {repo_relative(execution.execution_log_path)}"
                + issue_note
                + (
                    f" project item: {execution.project_item_id}"
                    if execution.project_item_id
                    else ""
                )
                + close_note
                + " create_followup_issue mutation / other action Project sync / Codex dispatch はまだ未実装です。"
            )

        if contract_decision.action.value == "codex_run" and contract_decision.close_current_issue:
            project_config = load_project_config()
            close_execution = execute_close_current_issue(
                materialized.prepared,
                prior_state=state,
                project_config=project_config,
                repo_path=project_repo_path(project_config),
                source_decision_log=repo_relative(decision_log),
                source_metadata_log=repo_relative(materialized.metadata_log_path),
                source_action_execution_log="",
                log_writer=log_text,
                repo_relative=repo_relative,
            )
            apply_close_execution_state(mutable_state, close_execution=close_execution)
            save_state(mutable_state)
            raise BridgeStop(
                "issue-centric contract reply を検出しましたが、codex_run + close_current_issue はこの slice では安全に実行できないため停止しました。"
                f" decision log: {repo_relative(decision_log)}"
                f" metadata: {repo_relative(materialized.metadata_log_path)}"
                + (
                    f" artifact: {repo_relative(materialized.artifact_log_path)}"
                    if materialized.artifact_log_path is not None
                    else ""
                )
                + f" close: {repo_relative(close_execution.execution_log_path)}"
            )

        if contract_decision.action.value == "codex_run":
            project_config = load_project_config()
            execution = execute_codex_run_action(
                materialized.prepared,
                project_config=project_config,
                repo_path=project_repo_path(project_config),
                source_decision_log=repo_relative(decision_log),
                source_metadata_log=repo_relative(materialized.metadata_log_path),
                source_artifact_path=(
                    repo_relative(materialized.artifact_log_path)
                    if materialized.artifact_log_path is not None
                    else ""
                ),
                log_writer=log_text,
                repo_relative=repo_relative,
            )
            mutable_state.update(
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
            if execution.status != "completed":
                save_state(mutable_state)
                trigger_note = ""
                if execution.created_comment is not None:
                    trigger_note = f" trigger comment: {execution.created_comment.url}"
                raise BridgeStop(
                    "issue-centric contract reply を検出し、codex_run の trigger comment execution まで実行しました。"
                    f" decision log: {repo_relative(decision_log)}"
                    f" metadata: {repo_relative(materialized.metadata_log_path)}"
                    + (
                        f" artifact: {repo_relative(materialized.artifact_log_path)}"
                        if materialized.artifact_log_path is not None
                        else ""
                    )
                    + f" execution: {repo_relative(execution.execution_log_path)}"
                    + (
                        f" payload: {repo_relative(execution.payload_log_path)}"
                        if execution.payload_log_path is not None
                        else ""
                    )
                    + trigger_note
                    + " issue-centric Codex launch はまだ進めませんでした。"
                )

            launch_result = launch_issue_centric_codex_run(
                materialized.prepared,
                execution,
                state=mutable_state,
                project_config=project_config,
                log_writer=log_text,
                repo_relative=repo_relative,
                launch_runner=launch_codex_once.run,
            )
            post_launch_state = dict(load_state())
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
            save_state(post_launch_state)
            trigger_note = ""
            if execution.created_comment is not None:
                trigger_note = f" trigger comment: {execution.created_comment.url}"
            stop_label = (
                "issue-centric contract reply を検出し、codex_run を既存 Codex launch 入口へ narrow 接続しました。"
                if launch_result.status == "completed"
                else "issue-centric contract reply を検出し、codex_run launch 後の continuation handoff で停止しました。"
            )
            raise BridgeStop(
                stop_label
                + f" decision log: {repo_relative(decision_log)}"
                f" metadata: {repo_relative(materialized.metadata_log_path)}"
                + (
                    f" artifact: {repo_relative(materialized.artifact_log_path)}"
                    if materialized.artifact_log_path is not None
                    else ""
                )
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
            )

        if contract_decision.action.value == "human_review_needed":
            project_config = load_project_config()
            review_execution = execute_human_review_action(
                materialized.prepared,
                prior_state=state,
                project_config=project_config,
                repo_path=project_repo_path(project_config),
                source_decision_log=repo_relative(decision_log),
                source_metadata_log=repo_relative(materialized.metadata_log_path),
                source_artifact_path=(
                    repo_relative(materialized.artifact_log_path)
                    if materialized.artifact_log_path is not None
                    else ""
                ),
                log_writer=log_text,
                repo_relative=repo_relative,
            )
            apply_review_execution_state(mutable_state, review_execution=review_execution)
            if contract_decision.close_current_issue and review_execution.status == "completed":
                mutable_state.update(
                    {
                        "last_issue_centric_close_status": "blocked_review_then_close_unimplemented",
                        "last_issue_centric_close_order": "after_review_blocked",
                        "last_issue_centric_stop_reason": (
                            review_execution.safe_stop_reason
                            + " close_current_issue=true was left for a later slice after review comment posting."
                        ),
                        "chatgpt_decision_note": (
                            review_execution.safe_stop_reason
                            + " close_current_issue=true was left for a later slice after review comment posting."
                        ),
                    }
                )
            save_state(mutable_state)
            review_note = ""
            if review_execution.created_comment is not None:
                review_note = f" review comment: {review_execution.created_comment.url}"
            close_note = ""
            if contract_decision.close_current_issue:
                close_note = " close_current_issue は review 後にのみ検討し、この slice では実行していません。"
            stop_label = (
                "issue-centric contract reply を検出し、human_review_needed の最小 review comment mutation まで実行しました。"
                if review_execution.status == "completed"
                else "issue-centric contract reply を検出しましたが、human_review_needed review execution を完了できず停止しました。"
            )
            raise BridgeStop(
                stop_label
                + f" decision log: {repo_relative(decision_log)}"
                + f" metadata: {repo_relative(materialized.metadata_log_path)}"
                + (
                    f" artifact: {repo_relative(materialized.artifact_log_path)}"
                    if materialized.artifact_log_path is not None
                    else ""
                )
                + f" review: {repo_relative(review_execution.execution_log_path)}"
                + review_note
                + close_note
                + " create_followup_issue mutation / Projects update はまだ未実装です。"
            )

        if contract_decision.action.value == "no_action" and contract_decision.create_followup_issue:
            project_config = load_project_config()
            followup_execution = execute_followup_issue_action(
                materialized.prepared,
                prior_state=state,
                project_config=project_config,
                repo_path=project_repo_path(project_config),
                source_decision_log=repo_relative(decision_log),
                source_metadata_log=repo_relative(materialized.metadata_log_path),
                source_artifact_path=(
                    repo_relative(materialized.artifact_log_path)
                    if materialized.artifact_log_path is not None
                    else ""
                ),
                log_writer=log_text,
                repo_relative=repo_relative,
            )
            apply_followup_execution_state(mutable_state, followup_execution=followup_execution)
            close_note = ""
            if contract_decision.close_current_issue and followup_execution.status == "completed":
                close_execution = execute_close_current_issue(
                    materialized.prepared,
                    prior_state=state,
                    project_config=project_config,
                    repo_path=project_repo_path(project_config),
                    source_decision_log=repo_relative(decision_log),
                    source_metadata_log=repo_relative(materialized.metadata_log_path),
                    source_action_execution_log=repo_relative(followup_execution.execution_log_path),
                    log_writer=log_text,
                    repo_relative=repo_relative,
                )
                apply_close_execution_state(mutable_state, close_execution=close_execution)
                close_note = f" close: {repo_relative(close_execution.execution_log_path)}"
            elif contract_decision.close_current_issue:
                mutable_state.update(
                    {
                        "last_issue_centric_close_status": "not_attempted_followup_blocked",
                        "last_issue_centric_close_order": "after_followup_issue_create",
                    }
                )
            save_state(mutable_state)
            followup_note = ""
            if followup_execution.created_issue is not None:
                followup_note = (
                    f" created follow-up issue: #{followup_execution.created_issue.number} "
                    f"{followup_execution.created_issue.url}"
                )
            raise BridgeStop(
                "issue-centric contract reply を検出し、no_action + create_followup_issue の narrow execution slice まで実行しました。"
                f" decision log: {repo_relative(decision_log)}"
                f" metadata: {repo_relative(materialized.metadata_log_path)}"
                + (
                    f" artifact: {repo_relative(materialized.artifact_log_path)}"
                    if materialized.artifact_log_path is not None
                    else ""
                )
                + f" execution: {repo_relative(followup_execution.execution_log_path)}"
                + followup_note
                + (
                    f" project item: {followup_execution.project_item_id}"
                    if followup_execution.project_item_id
                    else ""
                )
                + close_note
                + " create_followup_issue の一般化 / 他 action との組み合わせ / Projects update の全面対応 はまだ未実装です。"
            )

        if contract_decision.close_current_issue and contract_decision.action.value == "no_action":
            project_config = load_project_config()
            close_execution = execute_close_current_issue(
                materialized.prepared,
                prior_state=state,
                project_config=project_config,
                repo_path=project_repo_path(project_config),
                source_decision_log=repo_relative(decision_log),
                source_metadata_log=repo_relative(materialized.metadata_log_path),
                source_action_execution_log="",
                log_writer=log_text,
                repo_relative=repo_relative,
            )
            apply_close_execution_state(mutable_state, close_execution=close_execution)
            save_state(mutable_state)
            raise BridgeStop(
                "issue-centric contract reply を検出し、close_current_issue の最小 mutation slice まで実行しました。"
                f" decision log: {repo_relative(decision_log)}"
                f" metadata: {repo_relative(materialized.metadata_log_path)}"
                + f" close: {repo_relative(close_execution.execution_log_path)}"
                + (
                    f" action: {contract_decision.action.value}"
                    if contract_decision.action.value != "no_action"
                    else ""
                )
                + " create_followup_issue mutation / review automation / Projects update はまだ未実装です。"
            )

        save_state(mutable_state)
        raise BridgeStop(
            "issue-centric contract reply を検出し、BODY base64 transport の prepared artifact まで作成しました。"
            " issue create / codex_run / human_review_needed / close_current_issue の narrow execution 以外、GitHub mutation の広い接続、state machine 切替はまだ未実装です。"
            f" raw dump: {repo_relative(raw_log)}"
            f" decision log: {repo_relative(decision_log)}"
            f" metadata: {repo_relative(materialized.metadata_log_path)}"
            + (
                f" artifact: {repo_relative(materialized.artifact_log_path)}"
                if materialized.artifact_log_path is not None
                else ""
            )
        )
    decision = extract_last_chatgpt_reply(raw_text, after_text=request_text or None)
    reply_body = decision.body if decision.kind == "codex_prompt" else (decision.raw_block or decision.note)
    reply_hash = stable_text_hash(f"{decision.kind}\n{reply_body.strip()}")
    already_processed = (
        bool(pending_request_hash)
        and pending_request_hash == str(state.get("last_processed_request_hash", "")).strip()
        and reply_hash == str(state.get("last_processed_reply_hash", "")).strip()
    )
    mutable_state = clear_error_fields(dict(state))
    clear_pending_request_fields(mutable_state)
    mutable_state["last_processed_request_hash"] = pending_request_hash or str(state.get("last_processed_request_hash", "")).strip()
    mutable_state["last_processed_reply_hash"] = reply_hash

    if decision.kind == "codex_prompt":
        prompt_path = runtime_prompt_path()
        prompt_log = None
        current_prompt = read_text(prompt_path).strip()
        if not already_processed or current_prompt != decision.body.strip():
            prompt_log = log_text("extracted_codex_prompt", decision.body)
            write_text(prompt_path, decision.body)
        mutable_state.update(
            {
                "mode": "ready_for_codex",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": False,
                "need_codex_run": True,
                "human_review_auto_continue_count": 0,
                "chatgpt_decision": "",
                "chatgpt_decision_note": "",
                "last_prompt_file": repo_relative(prompt_path),
            }
        )
        if rotation_requested:
            mark_next_request_requires_rotation(mutable_state, rotation_reason or "late_completion")
        else:
            clear_chat_rotation_fields(mutable_state)
        save_state(mutable_state)
        print(f"raw dump: {raw_log}")
        if prompt_log is not None:
            print(f"prompt log: {prompt_log}")
        elif already_processed:
            print("prompt: 同じ request / reply はすでに処理済みのため再採用しませんでした")
        print(f"saved prompt: {prompt_path}")
        return 0

    auto_continue_count = int(state.get("human_review_auto_continue_count", 0) or 0)
    if decision.kind == "human_review" and auto_continue_count < 1:
        continue_text = build_human_review_auto_continue_request()
        request_hash = stable_text_hash(continue_text)
        request_source = (
            f"human_review_continue:{pending_request_hash or stable_text_hash(request_text or '')}:{auto_continue_count + 1}"
        )
        prepared_log = log_text("prepared_human_review_auto_continue", continue_text)
        prepared_log_rel = repo_relative(prepared_log)
        prepared_state = clear_error_fields(dict(mutable_state))
        stage_prepared_request(
            prepared_state,
            request_hash=request_hash,
            request_source=request_source,
            request_log=prepared_log_rel,
        )
        prepared_state.update(
            {
                    "mode": "awaiting_user",
                    "need_chatgpt_prompt": False,
                    "need_chatgpt_next": False,
                    "need_codex_run": False,
                    "human_review_auto_continue_count": auto_continue_count + 1,
                    "chatgpt_decision": "human_review",
                    "chatgpt_decision_note": decision.note,
                    "last_prompt_file": "",
                }
            )
        if rotation_requested:
            mark_next_request_requires_rotation(prepared_state, rotation_reason or "late_completion")
        else:
            clear_chat_rotation_fields(prepared_state)
        save_state(prepared_state)
        try:
            send_to_chatgpt(continue_text)
        except Exception:
            retry_state = clear_error_fields(dict(mutable_state))
            stage_prepared_request(
                retry_state,
                request_hash=request_hash,
                request_source=request_source,
                request_log=prepared_log_rel,
                status="retry_send",
            )
            retry_state.update(
                {
                    "mode": "awaiting_user",
                    "need_chatgpt_prompt": False,
                    "need_chatgpt_next": False,
                    "need_codex_run": False,
                    "human_review_auto_continue_count": auto_continue_count,
                    "chatgpt_decision": "human_review",
                    "chatgpt_decision_note": decision.note,
                    "last_prompt_file": "",
                }
            )
            if rotation_requested:
                mark_next_request_requires_rotation(retry_state, rotation_reason or "late_completion")
            else:
                clear_chat_rotation_fields(retry_state)
            save_state(retry_state)
            raise
        continue_log = log_text("human_review_auto_continue", continue_text)
        waiting_state = clear_error_fields(dict(mutable_state))
        promote_pending_request(
            waiting_state,
            request_hash=request_hash,
            request_source=request_source,
            request_log=repo_relative(continue_log),
        )
        waiting_state.update(
            {
                "human_review_auto_continue_count": auto_continue_count + 1,
                "chatgpt_decision": "",
                "chatgpt_decision_note": "",
            }
        )
        if rotation_requested:
            mark_next_request_requires_rotation(waiting_state, rotation_reason or "late_completion")
        else:
            clear_chat_rotation_fields(waiting_state)
        save_state(waiting_state)
        print(f"raw dump: {raw_log}")
        print(f"auto-continue: {continue_log}")
        print("ChatGPT の human_review は 1 回だけ自動継続しました。")
        return 0

    decision_log = None
    if not already_processed:
        decision_log = log_text("extracted_no_codex_reply", decision.raw_block or decision.note, suffix="md")
    decision_note = decision.note
    if decision.kind == "human_review" and auto_continue_count >= 1:
        suffix = "human_review が 2 回続いたため、人確認待ちへ切り替えました。"
        decision_note = f"{decision.note}\n{suffix}".strip() if decision.note else suffix
    mutable_state.update(
        {
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "human_review_auto_continue_count": 0,
            "chatgpt_decision": decision.kind,
            "chatgpt_decision_note": decision_note,
            "last_prompt_file": "",
        }
    )
    clear_chat_rotation_fields(mutable_state)
    if decision.kind == "completed":
        mutable_state["mode"] = "completed"
    else:
        mutable_state["mode"] = "awaiting_user"
    save_state(mutable_state)
    print(f"raw dump: {raw_log}")
    if decision_log is not None:
        print(f"decision log: {decision_log}")
    elif already_processed:
        print("decision: 同じ request / reply はすでに処理済みのため再採用しませんでした")
    print(f"ChatGPT は Codex 不要と判断しました: {decision.kind}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
