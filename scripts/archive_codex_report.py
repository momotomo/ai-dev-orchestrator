#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from _bridge_common import (
    OUTBOX_PLACEHOLDER_TEXT,
    BridgeError,
    clear_error_fields,
    guarded_main,
    log_text,
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
    archive_log = ""
    if (
        str(state.get("last_issue_centric_action", "")).strip() == "codex_run"
        and str(state.get("last_issue_centric_launch_status", "")).strip()
    ):
        archive_log = repo_relative(
            log_text(
                "issue_centric_codex_report_archived",
                "\n".join(
                    [
                        f"target_issue: {state.get('last_issue_centric_resolved_issue', '')}",
                        f"trigger_comment: {state.get('last_issue_centric_trigger_comment_url', '')}",
                        f"archived_report: {repo_relative(archive_path)}",
                        f"source_launch_log: {state.get('last_issue_centric_launch_log', '')}",
                        f"source_continuation_log: {state.get('last_issue_centric_continuation_log', '')}",
                    ]
                ),
                suffix="md",
            )
        )
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
    if archive_log:
        mutable_state.update(
            {
                "last_issue_centric_continuation_status": "archived_for_next_request",
                "last_issue_centric_continuation_log": archive_log,
                "last_issue_centric_report_status": "archived",
                "last_issue_centric_report_file": repo_relative(archive_path),
                "last_issue_centric_stop_reason": (
                    "Issue-centric Codex report was archived and the existing next-request preparation path can continue."
                ),
            }
        )
    save_state(mutable_state)
    print(f"archived: {archive_path}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(run))
