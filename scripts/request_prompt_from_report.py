#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from _bridge_common import (
    BRIDGE_DIR,
    build_chatgpt_handoff_request,
    build_chatgpt_request,
    clear_error_fields,
    clear_pending_handoff_fields,
    clear_pending_request_fields,
    extract_last_chatgpt_handoff,
    guarded_main,
    load_project_config,
    log_text,
    present_resume_prompt,
    read_pending_handoff_text,
    read_last_report_text,
    repo_relative,
    rotate_chat_with_handoff,
    send_to_chatgpt,
    save_state,
    stable_text_hash,
    wait_for_handoff_reply_text,
)

DEFAULT_NEXT_TODO = "前回 report を踏まえて、次の 1 フェーズ分の Codex 用 prompt を作成してください。"
DEFAULT_OPEN_QUESTIONS = "未解決事項があれば安全側で補ってください。"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    project_config = load_project_config()
    parser = argparse.ArgumentParser(description="Safari の現在 ChatGPT タブへ完了報告ベースの prompt request を送信します。")
    parser.add_argument(
        "--next-todo",
        default=str(project_config.get("report_request_next_todo", DEFAULT_NEXT_TODO)),
        help="次にやりたいこと",
    )
    parser.add_argument(
        "--open-questions",
        default=str(project_config.get("report_request_open_questions", DEFAULT_OPEN_QUESTIONS)),
        help="未解決事項",
    )
    parser.add_argument("--current-status", default="", help="CURRENT_STATUS の上書き")
    parser.add_argument("--resume-note", default="", help="human_review / need_info 再開時に添える補足入力")
    return parser.parse_args(argv)


def prompt_resume_note(state: dict[str, object]) -> str:
    resume_prompt = present_resume_prompt(state)
    print(resume_prompt.title, flush=True)
    print(resume_prompt.detail, flush=True)
    print("この入力は初回 request を上書きせず、次の ChatGPT request に添える補足だけとして使います。", flush=True)
    print("以下はそのまま使える短い例です。必要な行だけ書き換えてください。", flush=True)
    print("", flush=True)
    print(resume_prompt.example, flush=True)
    print("", flush=True)
    print("入力終了は Ctrl-D、または空行を 2 回です。空入力では送信しません。", flush=True)

    lines: list[str] = []
    empty_streak = 0
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            empty_streak += 1
            if empty_streak >= 2:
                break
        else:
            empty_streak = 0
        lines.append(line)
    return "\n".join(lines).strip()


def resolve_resume_note(state: dict[str, object], args: argparse.Namespace) -> str:
    if args.resume_note.strip():
        return args.resume_note.strip()

    if str(state.get("mode", "")).strip() != "awaiting_user":
        return ""

    if sys.stdin is not None and not sys.stdin.isatty():
        return sys.stdin.read().strip()

    return prompt_resume_note(state)


def build_report_request_source(state: dict[str, object], resume_note: str) -> str:
    last_report_file = str(state.get("last_report_file", "")).strip() or "unknown-report"
    if str(state.get("mode", "")).strip() == "awaiting_user":
        decision = str(state.get("chatgpt_decision", "")).strip() or "resume"
        resume_hash = stable_text_hash(resume_note.strip() or "no-note")
        return f"handoff:{decision}:{last_report_file}:{resume_hash}"
    return f"report:{last_report_file}"


def log_wait_event(event: object) -> None:
    event_name = str(getattr(event, "name", "")).strip()
    latest_text = str(getattr(event, "latest_text", "") or "")
    if not event_name:
        return
    stage_log = log_text(event_name, latest_text, suffix="txt")
    print(f"{event_name}: {stage_log}")


def run_resume_request(
    state: dict[str, object],
    args: argparse.Namespace,
    last_report: str,
    resume_note: str,
) -> int:
    template_path = BRIDGE_DIR / "chatgpt_prompt_request_template.md"
    request_text = build_chatgpt_request(
        state=state,
        template_path=template_path,
        next_todo=args.next_todo,
        open_questions=args.open_questions,
        current_status=args.current_status or None,
        last_report=last_report,
        resume_note=resume_note or None,
    )
    request_hash = stable_text_hash(request_text)
    request_source = build_report_request_source(state, resume_note)

    if (
        str(state.get("mode", "")).strip() == "waiting_prompt_reply"
        and str(state.get("pending_request_source", "")).strip() == request_source
    ):
        print("request: 同じ report からの request は送信済みのため再送しませんでした。")
        if str(state.get("pending_request_log", "")).strip():
            print(f"pending: {state.get('pending_request_log', '')}")
        return 0

    request_log = log_text("sent_prompt_request_from_report", request_text)

    mutable_state = clear_error_fields(dict(state))
    clear_pending_handoff_fields(mutable_state)
    clear_pending_request_fields(mutable_state)
    mutable_state.update(
        {
            "mode": "waiting_prompt_reply",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "chatgpt_decision": "",
            "chatgpt_decision_note": "",
            "human_review_auto_continue_count": 0,
            "pending_request_hash": request_hash,
            "pending_request_source": request_source,
            "pending_request_log": repo_relative(request_log),
        }
    )
    save_state(mutable_state)

    send_to_chatgpt(request_text)
    print(f"sent: {request_log}")
    return 0


def run_rotated_report_request(
    state: dict[str, object],
    args: argparse.Namespace,
    last_report: str,
) -> int:
    request_source = build_report_request_source(state, "")
    pending_handoff_text = ""
    pending_handoff_source = str(state.get("pending_handoff_source", "")).strip()
    if pending_handoff_source == request_source:
        pending_handoff_text = read_pending_handoff_text(state)

    if pending_handoff_text:
        handoff_text = pending_handoff_text
        handoff_received_log = state.get("pending_handoff_log", "") or ""
        print("handoff: 回収済み handoff を再利用して新チャット送信を再試行します。")
    else:
        handoff_request_text = build_chatgpt_handoff_request(
            state=state,
            last_report=last_report,
            next_todo=args.next_todo,
            open_questions=args.open_questions,
            current_status=args.current_status or None,
        )
        handoff_request_log = log_text("handoff_requested", handoff_request_text)
        send_to_chatgpt(handoff_request_text)
        print(f"handoff requested: {handoff_request_log}")

        raw_text = wait_for_handoff_reply_text(
            request_text=handoff_request_text,
            stage_callback=log_wait_event,
        )
        handoff_text = extract_last_chatgpt_handoff(raw_text, after_text=handoff_request_text)
        handoff_received_log = log_text("handoff_received", handoff_text)
        handoff_state = clear_error_fields(dict(state))
        clear_pending_request_fields(handoff_state)
        handoff_state.update(
            {
                "mode": "idle",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": True,
                "need_codex_run": False,
                "pending_handoff_hash": stable_text_hash(handoff_text),
                "pending_handoff_source": request_source,
                "pending_handoff_log": repo_relative(handoff_received_log),
            }
        )
        save_state(handoff_state)

    rotated_chat = rotate_chat_with_handoff(handoff_text)
    chat_rotated_log = log_text(
        "chat_rotated",
        "\n".join(
            [
                f"url: {rotated_chat.get('url', '')}",
                f"title: {rotated_chat.get('title', '')}",
            ]
        ),
    )
    request_log = log_text("sent_prompt_request_from_report", handoff_text)
    request_hash = stable_text_hash(handoff_text)

    mutable_state = clear_error_fields(dict(state))
    clear_pending_request_fields(mutable_state)
    clear_pending_handoff_fields(mutable_state)
    mutable_state.update(
        {
            "mode": "waiting_prompt_reply",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "chatgpt_decision": "",
            "chatgpt_decision_note": "",
            "human_review_auto_continue_count": 0,
            "pending_request_hash": request_hash,
            "pending_request_source": request_source,
            "pending_request_log": repo_relative(request_log),
            "current_chat_session": rotated_chat.get("url", ""),
        }
    )
    save_state(mutable_state)

    if handoff_received_log:
        print(f"handoff received: {handoff_received_log}")
    print(f"chat rotated: {chat_rotated_log}")
    print(f"sent: {request_log}")
    return 0


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    resume_note = resolve_resume_note(state, args)
    if str(state.get("mode", "")).strip() == "awaiting_user" and not resume_note.strip():
        print("再開用の補足入力が空のため送信しませんでした。必要な補足を入力して再実行してください。")
        return 0
    last_report = read_last_report_text(state)
    if str(state.get("mode", "")).strip() == "awaiting_user":
        return run_resume_request(state, args, last_report, resume_note)
    return run_rotated_report_request(state, args, last_report)


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
