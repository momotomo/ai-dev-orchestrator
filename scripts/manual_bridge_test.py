#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from _bridge_common import fill_chatgpt_composer, guarded_main, log_text, open_chatgpt_page, read_chatgpt_conversation_dom

DEFAULT_TEST_MESSAGE = """これは ChatGPT + Codex + Python ブリッジの手動疎通テストです。
次の形式で短く返答してください。

===CHATGPT_PROMPT_REPLY===

Codex Prompt

manual bridge test succeeded

===END_REPLY===
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safari の現在タブ上の ChatGPT との手動疎通を確認します。")
    parser.add_argument(
        "--message",
        default=DEFAULT_TEST_MESSAGE,
        help="ChatGPT へ貼り付けるテストメッセージ",
    )
    return parser.parse_args()


def run(_: dict[str, object]) -> int:
    args = parse_args()
    request_log = log_text("manual_bridge_test_request", args.message)

    with open_chatgpt_page(reset_chat=False) as (_, page, config, front_tab):
        fill_chatgpt_composer(page, args.message, config, allow_manual_login=False)
        print(f"Safari の現在タブを使います: {front_tab['title']} {front_tab['url']}")
        print("Safari の現在 ChatGPT タブにテストメッセージを下書きしました。")
        print("内容を確認して手動送信し、返信が表示されたら Enter を押します。")
        input()

        conversation_text = read_chatgpt_conversation_dom(page)
        raw_log = log_text("manual_bridge_test_raw", conversation_text, suffix="txt")

    print(f"request log: {request_log}")
    print(f"raw log: {raw_log}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(run))
