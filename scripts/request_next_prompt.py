#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from _bridge_common import BRIDGE_DIR, build_chatgpt_request, clear_error_fields, guarded_main, log_text, send_to_chatgpt, save_state

DEFAULT_NEXT_TODO = "今回着手すべき 1 フェーズ分の Codex 用プロンプトを、差分中心・節約版で作成してください。"
DEFAULT_OPEN_QUESTIONS = "特になし。必要なら安全側の前提を置いてください。"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ChatGPT に次の Codex 用プロンプト要求を送信します。")
    parser.add_argument("--next-todo", default=DEFAULT_NEXT_TODO, help="次にやりたいこと")
    parser.add_argument("--open-questions", default=DEFAULT_OPEN_QUESTIONS, help="未解決事項")
    parser.add_argument("--current-status", default="", help="CURRENT_STATUS の上書き")
    return parser.parse_args()


def run(state: dict[str, object]) -> int:
    args = parse_args()
    template_path = BRIDGE_DIR / "chatgpt_prompt_request_template.md"
    request_text = build_chatgpt_request(
        state=state,
        template_path=template_path,
        next_todo=args.next_todo,
        open_questions=args.open_questions,
        current_status=args.current_status or None,
    )

    send_to_chatgpt(request_text)
    request_log = log_text("sent_prompt_request", request_text)

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
    sys.exit(guarded_main(run))
