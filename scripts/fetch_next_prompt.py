#!/usr/bin/env python3
from __future__ import annotations

import sys

from _bridge_common import INBOX_DIR, clear_error_fields, copy_chatgpt_conversation, extract_last_prompt_reply, guarded_main, log_text, repo_relative, save_state, write_text


def run(state: dict[str, object]) -> int:
    raw_text = copy_chatgpt_conversation()
    raw_log = log_text("chatgpt_raw_dump", raw_text, suffix="txt")
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
