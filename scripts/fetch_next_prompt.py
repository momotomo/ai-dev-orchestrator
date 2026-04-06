#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _bridge_common import (
    BridgeError,
    BridgeStop,
    build_human_review_auto_continue_request,
    clear_chat_rotation_fields,
    clear_error_fields,
    clear_pending_request_fields,
    promote_pending_request,
    extract_last_chatgpt_reply,
    guarded_main,
    load_state,
    log_text,
    mark_next_request_requires_rotation,
    next_request_rotation_reason,
    read_pending_request_text,
    read_text,
    repo_relative,
    runtime_prompt_path,
    save_state,
    send_to_chatgpt,
    stage_prepared_request,
    stable_text_hash,
    should_rotate_before_next_chat_request,
    wait_for_prompt_reply_text,
    write_text,
)
from issue_centric_contract import (
    IssueCentricContractError,
    maybe_parse_issue_centric_reply,
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
        raise BridgeStop(
            "issue-centric contract reply を検出しました。"
            " parser / dispatcher の前段では action・target_issue・body blocks の抽出と検証までは完了していますが、"
            " issue create / close 実行、BODY 利用本実装、state machine 切替はまだ未実装です。"
            f" raw dump: {repo_relative(raw_log)} decision log: {repo_relative(decision_log)}"
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
