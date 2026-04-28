#!/usr/bin/env python3
"""Reset the runtime to a guaranteed fresh-start state for clean rehearsal runs.

Use this when bridge/state.json reflects an in-progress or interrupted flow
(e.g. mode=ready_for_codex, mode=waiting_prompt_reply, pending/error states)
and you need a true blank-slate restart that will not accidentally resume the
old flow.

What is reset:
  • bridge/state.json              → overwritten with DEFAULT_STATE (mode=idle)
  • bridge/inbox/codex_prompt.md  → archived to logs/ then removed if it has
                                    content; removed (no archive) if empty
  • bridge/outbox/codex_report.md → archived to logs/ then removed if it has
                                    content; removed (no archive) if empty
  • bridge/STOP                   → removed if present

What is preserved (durable config and history):
  • bridge/project_config.json
  • bridge/browser_config.json
  • bridge/history/
  • logs/   (archived files are added, existing ones are never removed)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from _bridge_common import (  # noqa: E402
    DEFAULT_STATE,
    load_project_config,
    now_stamp,
    runtime_logs_dir,
    runtime_prompt_path,
    runtime_report_path,
    runtime_state_path,
    runtime_stop_path,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "runtime を確実に fresh-start 状態にリセットします。\n"
            "bridge/state.json を DEFAULT_STATE (mode=idle) に上書きし、\n"
            "inbox/outbox のアクティブアーティファクトを logs/ にアーカイブします。\n\n"
            "durable config (project_config.json, browser_config.json) と\n"
            "history/ / logs/ ディレクトリは保持されます。"
        ),
        epilog=(
            "使用例:\n"
            "  python3 scripts/reset_runtime_state.py\n"
            "  python3 scripts/reset_runtime_state.py --dry-run\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際の変更を行わず、何が変更されるかだけを表示する",
    )
    return parser.parse_args(argv)


def _archive_and_clear_artifact(
    src: Path,
    label: str,
    logs_dir: Path,
    stamp: str,
    dry_run: bool,
) -> str | None:
    """Archive src to logs/ if it has content, then remove the source file.

    - If src does not exist: no-op, returns None.
    - If src has meaningful content: archives to logs/, removes src, returns
      the archive path string.
    - If src is empty/whitespace-only: removes src (no archive), returns None.
    - In dry-run mode: no files are modified or removed.
    """
    if not src.exists():
        return None
    content = src.read_text(encoding="utf-8")
    if not content.strip():
        if dry_run:
            print(f"  [dry-run] would remove (empty) {src}", flush=True)
        else:
            src.unlink()
        return None
    archive_path = logs_dir / f"{stamp}_{label}_{src.name}"
    if dry_run:
        print(f"  [dry-run] would archive {src} → {archive_path}", flush=True)
        print(f"  [dry-run] would remove {src}", flush=True)
        return str(archive_path)
    logs_dir.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(content, encoding="utf-8")
    src.unlink()
    return str(archive_path)


def _remove_if_exists(path: Path, dry_run: bool) -> bool:
    """Remove path if it exists. Returns True if removed (or would be removed)."""
    if not path.exists():
        return False
    if dry_run:
        print(f"  [dry-run] would remove {path}", flush=True)
        return True
    path.unlink()
    return True


def run_reset(
    dry_run: bool = False,
    config: dict | None = None,
) -> None:
    """Reset bridge runtime to DEFAULT_STATE and archive active artifacts.

    Args:
        dry_run: If True, print what would change but make no modifications.
        config: Optional pre-loaded project config dict. If None, loads via
                load_project_config().
    """
    resolved_config = config if config is not None else load_project_config()
    stamp = now_stamp()
    state_path = runtime_state_path(resolved_config)
    prompt_path = runtime_prompt_path(resolved_config)
    report_path = runtime_report_path(resolved_config)
    stop_path = runtime_stop_path(resolved_config)
    logs_dir = runtime_logs_dir(resolved_config)

    # --- Before-state summary ---
    before_mode = "(missing)"
    if state_path.exists():
        try:
            before_mode = str(
                json.loads(state_path.read_text(encoding="utf-8")).get("mode", "")
            )
        except Exception:
            before_mode = "(unreadable)"

    print("reset_runtime_state: fresh-start reset", flush=True)
    print(f"  state_path: {state_path}", flush=True)
    print(f"  before: mode={before_mode!r}", flush=True)
    print("", flush=True)

    # --- Archive active artifacts and clear source files ---
    archived: list[str] = []
    for path, label in [(prompt_path, "inbox"), (report_path, "outbox")]:
        result = _archive_and_clear_artifact(path, label, logs_dir, stamp, dry_run)
        if result:
            archived.append(f"  archived: {path.name} → {result}")

    # --- Remove STOP if present ---
    stop_removed = _remove_if_exists(stop_path, dry_run)

    # --- Write fresh DEFAULT_STATE ---
    if dry_run:
        print(f"  [dry-run] would write DEFAULT_STATE to {state_path}", flush=True)
    else:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fresh_state = DEFAULT_STATE.copy()
        state_path.write_text(
            json.dumps(fresh_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # --- Print summary ---
    if archived:
        print("archived active artifacts:", flush=True)
        for line in archived:
            print(line, flush=True)
        print("", flush=True)
    if stop_removed:
        print("removed: bridge/STOP", flush=True)
        print("", flush=True)

    print("result:", flush=True)
    print("  mode: idle", flush=True)
    print("  need_chatgpt_prompt: True", flush=True)
    print("  need_codex_run: False", flush=True)
    if dry_run:
        print("  [dry-run] no changes were written", flush=True)
    else:
        print("reset complete: runtime is now in fresh-start state", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_reset(dry_run=args.dry_run)
        return 0
    except Exception as exc:
        print(f"reset_runtime_state error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
