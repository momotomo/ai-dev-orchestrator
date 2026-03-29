#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from _bridge_common import copy_chatgpt_conversation, guarded_main, log_text, paste_into_chatgpt

DEFAULT_TEST_MESSAGE = """これは ChatGPT + Codex + Python ブリッジの手動疎通テストです。
次の形式で短く返答してください。

===CHATGPT_PROMPT_REPLY===

Codex Prompt

manual bridge test succeeded

===END_REPLY===
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ChatGPT デスクトップとの手動疎通を確認します。")
    parser.add_argument(
        "--message",
        default=DEFAULT_TEST_MESSAGE,
        help="ChatGPT へ貼り付けるテストメッセージ",
    )
    return parser.parse_args()


def run(_: dict[str, object]) -> int:
    args = parse_args()
    request_log = log_text("manual_bridge_test_request", args.message)
    paste_into_chatgpt(args.message, press_enter=False)

    print("ChatGPT アプリを前面化し、テストメッセージを貼り付けました。")
    print("内容を確認して手動送信し、返信が出たら会話領域を選べる状態で Enter を押してください。")
    input()

    copied = copy_chatgpt_conversation()
    raw_log = log_text("manual_bridge_test_raw", copied, suffix="txt")
    print(f"request log: {request_log}")
    print(f"raw log: {raw_log}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(run))
