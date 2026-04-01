#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _bridge_common import clear_error_fields, extract_last_chatgpt_reply, guarded_main, log_text, read_latest_prompt_request_text, read_text, repo_relative, runtime_prompt_path, save_state, wait_for_prompt_reply_text, write_text


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
    request_text = read_latest_prompt_request_text()
    if args.raw_file:
        raw_text = read_text(Path(args.raw_file)).strip()
        if not raw_text:
            raise ValueError(f"raw file を読めませんでした: {args.raw_file}")
    else:
        raw_text = wait_for_prompt_reply_text(timeout_seconds=args.timeout_seconds or None)
    raw_log = log_text("raw_chatgpt_prompt_dump", raw_text, suffix="txt")
    decision = extract_last_chatgpt_reply(raw_text, after_text=request_text or None)
    mutable_state = clear_error_fields(dict(state))

    if decision.kind == "codex_prompt":
        prompt_log = log_text("extracted_codex_prompt", decision.body)
        prompt_path = runtime_prompt_path()
        write_text(prompt_path, decision.body)
        mutable_state.update(
            {
                "mode": "ready_for_codex",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": False,
                "need_codex_run": True,
                "chatgpt_decision": "",
                "chatgpt_decision_note": "",
                "last_prompt_file": repo_relative(prompt_path),
            }
        )
        save_state(mutable_state)
        print(f"raw dump: {raw_log}")
        print(f"prompt log: {prompt_log}")
        print(f"saved prompt: {prompt_path}")
        return 0

    decision_log = log_text("extracted_no_codex_reply", decision.raw_block or decision.note, suffix="md")
    mutable_state.update(
        {
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "chatgpt_decision": decision.kind,
            "chatgpt_decision_note": decision.note,
            "last_prompt_file": "",
        }
    )
    if decision.kind == "completed":
        mutable_state["mode"] = "completed"
    else:
        mutable_state["mode"] = "awaiting_user"
    save_state(mutable_state)
    print(f"raw dump: {raw_log}")
    print(f"decision log: {decision_log}")
    print(f"ChatGPT は Codex 不要と判断しました: {decision.kind}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
