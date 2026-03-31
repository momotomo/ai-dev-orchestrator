#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from _bridge_common import BRIDGE_DIR, build_chatgpt_request, clear_error_fields, guarded_main, load_project_config, log_text, present_resume_prompt, read_last_report_text, send_to_chatgpt, save_state

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


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    resume_note = resolve_resume_note(state, args)
    if str(state.get("mode", "")).strip() == "awaiting_user" and not resume_note.strip():
        print("再開用の補足入力が空のため送信しませんでした。必要な補足を入力して再実行してください。")
        return 0
    template_path = BRIDGE_DIR / "chatgpt_prompt_request_template.md"
    last_report = read_last_report_text(state)
    request_text = build_chatgpt_request(
        state=state,
        template_path=template_path,
        next_todo=args.next_todo,
        open_questions=args.open_questions,
        current_status=args.current_status or None,
        last_report=last_report,
        resume_note=resume_note or None,
    )

    send_to_chatgpt(request_text)
    request_log = log_text("sent_prompt_request_from_report", request_text)

    mutable_state = clear_error_fields(dict(state))
    mutable_state.update(
        {
            "mode": "waiting_prompt_reply",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "chatgpt_decision": "",
            "chatgpt_decision_note": "",
        }
    )
    save_state(mutable_state)
    print(f"sent: {request_log}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
