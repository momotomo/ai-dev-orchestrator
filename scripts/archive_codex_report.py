#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from _bridge_common import HISTORY_DIR, OUTBOX_DIR, PLACEHOLDER_REPORT_HEADER, BridgeError, clear_error_fields, guarded_main, now_stamp, read_text, repo_relative, save_state, write_text

OUTBOX_PLACEHOLDER = """# Codex Report Outbox

このファイルは Codex 実行完了時に上書きします。
運用時はここに最新の完了報告が入ります。
"""


def build_archive_path(cycle: int) -> Path:
    return HISTORY_DIR / f"codex_report_cycle_{cycle:04d}_{now_stamp()}.md"


def run(state: dict[str, object]) -> int:
    outbox_path = OUTBOX_DIR / "codex_report.md"
    report_text = read_text(outbox_path).strip()
    if not report_text or report_text.startswith(PLACEHOLDER_REPORT_HEADER):
        raise BridgeError("退避対象の bridge/outbox/codex_report.md が見つかりませんでした。")

    next_cycle = int(state.get("cycle", 0)) + 1
    archive_path = build_archive_path(next_cycle)
    write_text(archive_path, report_text + "\n")
    write_text(outbox_path, OUTBOX_PLACEHOLDER)

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
