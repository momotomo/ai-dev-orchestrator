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
from issue_centric_current_issue_project_state import execute_current_issue_project_state_sync
from issue_centric_human_review import execute_human_review_action
from issue_centric_contract import (
    IssueCentricContractError,
    maybe_parse_issue_centric_reply,
)
from issue_centric_codex_run import execute_codex_run_action
from issue_centric_execution import dispatch_issue_centric_execution
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
                "last_issue_centric_dispatch_result": "",
                "last_issue_centric_normalized_summary": "",
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
                "last_issue_centric_primary_issue_number": "",
                "last_issue_centric_primary_issue_url": "",
                "last_issue_centric_primary_issue_title": "",
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
                "last_issue_centric_close_order": "",
                "last_issue_centric_review_status": "",
                "last_issue_centric_review_log": "",
                "last_issue_centric_review_comment_id": "",
                "last_issue_centric_review_comment_url": "",
                "last_issue_centric_review_close_policy": "",
                "last_issue_centric_stop_reason": materialized.safe_stop_reason,
            }
        )
        project_config = load_project_config()
        dispatch_result = dispatch_issue_centric_execution(
            contract_decision=contract_decision,
            materialized=materialized,
            prior_state=state,
            mutable_state=mutable_state,
            project_config=project_config,
            repo_path=project_repo_path(project_config),
            source_raw_log=repo_relative(raw_log),
            source_decision_log=repo_relative(decision_log),
            source_metadata_log=repo_relative(materialized.metadata_log_path),
            source_artifact_path=(
                repo_relative(materialized.artifact_log_path)
                if materialized.artifact_log_path is not None
                else ""
            ),
            log_writer=log_text,
            repo_relative=repo_relative,
            load_state_fn=load_state,
            save_state_fn=save_state,
            execute_issue_create_action_fn=execute_issue_create_action,
            execute_codex_run_action_fn=execute_codex_run_action,
            launch_issue_centric_codex_run_fn=launch_issue_centric_codex_run,
            execute_human_review_action_fn=execute_human_review_action,
            execute_close_current_issue_fn=execute_close_current_issue,
            execute_followup_issue_action_fn=execute_followup_issue_action,
            execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync,
            launch_runner=launch_codex_once.run,
        )
        save_state(dispatch_result.final_state)
        raise BridgeStop(dispatch_result.stop_message)
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
