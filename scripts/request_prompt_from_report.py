#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from _bridge_common import BRIDGE_DIR, build_chatgpt_request, clear_error_fields, guarded_main, load_project_config, log_text, read_last_report_text, send_to_chatgpt, save_state

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
    return parser.parse_args(argv)


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    template_path = BRIDGE_DIR / "chatgpt_prompt_request_template.md"
    last_report = read_last_report_text(state)
    request_text = build_chatgpt_request(
        state=state,
        template_path=template_path,
        next_todo=args.next_todo,
        open_questions=args.open_questions,
        current_status=args.current_status or None,
        last_report=last_report,
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
        }
    )
    save_state(mutable_state)
    print(f"sent: {request_log}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
