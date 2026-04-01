#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from _bridge_common import (
    OUTBOX_PLACEHOLDER_TEXT,
    BridgeError,
    clear_error_fields,
    guarded_main,
    now_stamp,
    ready_codex_report_text,
    repo_relative,
    runtime_history_dir,
    runtime_report_path,
    save_state,
    write_text,
)


def build_archive_path(cycle: int) -> Path:
    return runtime_history_dir() / f"codex_report_cycle_{cycle:04d}_{now_stamp()}.md"


def run(state: dict[str, object]) -> int:
    outbox_path = runtime_report_path()
    report_text = ready_codex_report_text(outbox_path)
    if not report_text:
        raise BridgeError("退避対象の bridge/outbox/codex_report.md が見つかりませんでした。")

    next_cycle = int(state.get("cycle", 0)) + 1
    archive_path = build_archive_path(next_cycle)
    write_text(archive_path, report_text + "\n")
    write_text(outbox_path, OUTBOX_PLACEHOLDER_TEXT + "\n")

    mutable_state = clear_error_fields(dict(state))
    mutable_state.update(
        {
            "mode": "idle",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": True,
            "need_codex_run": False,
            "last_report_file": repo_relative(archive_path),
            "cycle": next_cycle,
        }
    )
    save_state(mutable_state)
    print(f"archived: {archive_path}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(run))
