#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _bridge_common import INBOX_DIR, clear_error_fields, copy_chatgpt_conversation, extract_last_prompt_reply, guarded_main, log_text, read_text, repo_relative, save_state, write_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ChatGPT 返答から最後の Codex Prompt を抽出します。")
    parser.add_argument(
        "--raw-file",
        default="",
        help="診断や再現テスト用に、会話全文 dump ファイルを直接読む",
    )
    return parser.parse_args()


def run(state: dict[str, object]) -> int:
    args = parse_args()
    if args.raw_file:
        raw_text = read_text(Path(args.raw_file)).strip()
        if not raw_text:
            raise ValueError(f"raw file を読めませんでした: {args.raw_file}")
    else:
        raw_text = copy_chatgpt_conversation()
    raw_log = log_text("raw_chatgpt_prompt_dump", raw_text, suffix="txt")
    prompt_body = extract_last_prompt_reply(raw_text)
    prompt_log = log_text("extracted_codex_prompt", prompt_body)

    prompt_path = INBOX_DIR / "codex_prompt.md"
    write_text(prompt_path, prompt_body)

    mutable_state = clear_error_fields(dict(state))
    mutable_state.update(
        {
            "mode": "ready_for_codex",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": True,
            "last_prompt_file": repo_relative(prompt_path),
        }
    )
    save_state(mutable_state)
    print(f"raw dump: {raw_log}")
    print(f"prompt log: {prompt_log}")
    print(f"saved prompt: {prompt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(run))
