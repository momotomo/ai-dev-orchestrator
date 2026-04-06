#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from _bridge_common import _run_osascript_script, _run_safari_javascript, guarded_main, log_text, repo_relative


@dataclass(frozen=True)
class MarkdownSignalSummary:
    sha256: str
    has_heading_marker: bool
    has_exact_dash_list: bool
    has_markdown_list: bool
    has_inline_code: bool
    has_fenced_code: bool
    has_blank_line_pair: bool


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Safari 上の既存 ChatGPT assistant reply を read-only で観測し、"
            "visible text と UI copy path の markdown fidelity を比較します。"
        )
    )
    parser.add_argument(
        "--conversation-url",
        default="",
        help="probe 対象の既存 ChatGPT conversation URL。指定時は一時 Safari window を開く",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=3,
        help="copy path を繰り返し読む回数。既定は 3",
    )
    parser.add_argument(
        "--copy-button-retries",
        type=int,
        default=5,
        help="copy button が一時的に消えている時の再試行回数。既定は 5",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=float,
        default=0.5,
        help="copy button 再試行の待機秒数。既定は 0.5",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=1.0,
        help="copy click 後に clipboard を読むまでの待機秒数。既定は 1.0",
    )
    return parser.parse_args(argv)


def analyze_markdown_text(text: str) -> MarkdownSignalSummary:
    return MarkdownSignalSummary(
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        has_heading_marker=bool(re.search(r"(?m)^##\s+\S", text)),
        has_exact_dash_list=bool(re.search(r"(?m)^-\s+\S", text)),
        has_markdown_list=bool(re.search(r"(?m)^[-*]\s+\S", text)),
        has_inline_code=bool(re.search(r"`[^`\n]+`", text)),
        has_fenced_code=("```" in text and bool(re.search(r"```[A-Za-z0-9_+-]*\n", text))),
        has_blank_line_pair="\n\n" in text,
    )


def _read_clipboard_bytes() -> bytes:
    return subprocess.run(["pbpaste"], capture_output=True, check=False).stdout


def _write_clipboard_bytes(content: bytes) -> None:
    subprocess.run(["pbcopy"], input=content, check=False)


def _json_eval(script: str) -> dict[str, object]:
    raw = _run_safari_javascript(script).strip()
    if not raw:
        return {}
    return json.loads(raw)


def read_latest_assistant_visible_text() -> str:
    payload = _json_eval(
        r"""
(() => {
  const turn = Array.from(document.querySelectorAll('[data-testid^="conversation-turn-"]'))
    .filter((node) => (node.innerText || "").includes("ChatGPT:"))
    .pop();
  if (!turn) return JSON.stringify({text: ""});
  return JSON.stringify({text: turn.innerText || turn.textContent || ""});
})();
"""
    )
    return str(payload.get("text", "") or "")


def click_latest_response_copy_button_with_retry(retries: int, retry_delay_seconds: float) -> dict[str, object]:
    script = r"""
(() => {
  const buttons = Array.from(document.querySelectorAll('button[data-testid="copy-turn-action-button"]'));
  const target = buttons.filter((btn) => (btn.getAttribute("aria-label") || "").includes("回答をコピーする")).pop();
  if (!target) return JSON.stringify({ok: false, reason: "missing"});
  target.scrollIntoView({block: "center"});
  target.click();
  return JSON.stringify({ok: true});
})();
"""
    last_result: dict[str, object] = {"ok": False, "reason": "missing"}
    attempts = 0
    for attempts in range(1, retries + 1):
        last_result = _json_eval(script)
        if bool(last_result.get("ok")):
            break
        time.sleep(retry_delay_seconds)
    return {
        "attempts": attempts,
        "result": last_result,
    }


@contextmanager
def temporary_safari_window(conversation_url: str) -> Iterator[None]:
    if not conversation_url:
        yield
        return

    applescript = """
on run argv
    set targetUrl to item 1 of argv
    tell application "Safari"
        activate
        set newDoc to make new document with properties {URL:targetUrl}
        return URL of current tab of front window
    end tell
end run
"""
    close_script = """
tell application "Safari"
    if (count of windows) > 1 then close front window
end tell
"""
    _run_osascript_script(applescript, [conversation_url])
    time.sleep(5)
    try:
        yield
    finally:
        _run_osascript_script(close_script)


def run(_: dict[str, object], argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    original_clipboard = _read_clipboard_bytes()

    try:
        with temporary_safari_window(args.conversation_url):
            visible_text = read_latest_assistant_visible_text()
            visible_log = log_text("markdown_fidelity_visible", visible_text, suffix="txt")
            visible_summary = analyze_markdown_text(visible_text)

            copy_runs: list[dict[str, object]] = []
            for run_index in range(args.repeat):
                _write_clipboard_bytes(b"__CODEX_SENTINEL__")
                action = click_latest_response_copy_button_with_retry(
                    args.copy_button_retries,
                    args.retry_delay_seconds,
                )
                time.sleep(args.settle_seconds)
                copied_text = _read_clipboard_bytes().decode("utf-8", errors="replace")
                copied_log = log_text(
                    f"markdown_fidelity_copied_run_{run_index + 1}",
                    copied_text,
                    suffix="md",
                )
                copied_summary = analyze_markdown_text(copied_text)
                copy_runs.append(
                    {
                        "run": run_index + 1,
                        "copy_action": action,
                        "clipboard_changed": copied_text != "__CODEX_SENTINEL__",
                        "log": repo_relative(copied_log),
                        "text": copied_text,
                        "summary": copied_summary.__dict__,
                    }
                )

            print(
                json.dumps(
                    {
                        "visible": {
                            "log": repo_relative(visible_log),
                            "text": visible_text,
                            "summary": visible_summary.__dict__,
                        },
                        "copy_runs": copy_runs,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0
    finally:
        _write_clipboard_bytes(original_clipboard)


if __name__ == "__main__":
    sys.exit(guarded_main(run))
