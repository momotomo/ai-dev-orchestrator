"""safari_tab_probe.py — Read-only probe: enumerate all Safari windows/tabs.

Purpose
-------
Diagnose whether the bridge can read ChatGPT conversation content from a
*non-current* tab so we can eventually reduce the dependency on Safari's
front-window current tab.

This script is **read-only**.  It never modifies Safari state, never touches
bridge runtime state, and never writes to bridge/state.json.

Usage
-----
    python3 scripts/safari_tab_probe.py [--json] [--out PATH] [--body-limit N]

Options
    --json          Output results as JSON instead of human-readable text.
    --out PATH      Write output to PATH (default: print to stdout).
                    Extension is respected: .json forces JSON mode.
    --body-limit N  Character limit for the body preview (default: 500).
                    Pass 0 to skip body fetch entirely.

Output
------
For each tab:
  - window_index       (1-based)
  - tab_index          (1-based)
  - title
  - url
  - is_front_window    True if this is the frontmost window
  - is_current_tab     True if this is the current tab of its window
  - is_chatgpt         True if URL contains "chatgpt.com"
  - is_conversation    True if URL matches ChatGPT /c/ conversation pattern
  - conversation_id    extracted conversation UUID (empty if not a conversation)
  - body_fetch_status  "ok" | "skipped" | error message
  - body_preview       first --body-limit chars of document.body.innerText

Non-current tab JavaScript errors are reported in body_fetch_status rather
than being suppressed, so the caller can diagnose Safari's access policy.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# AppleScript helpers (self-contained; no dependency on _bridge_common)
# ---------------------------------------------------------------------------

APPLE_EVENT_TIMEOUT = 15  # seconds per osascript call

# AppleScript to enumerate all windows and tabs.
# Returns lines of the form: WINDOW\t<winIdx>\t<tabIdx>\t<isFront>\t<isCurrent>\t<url>\t<title>
# NOTE: inside "tell application Safari", the keyword `tab` refers to a Safari Tab
# object, not the ASCII tab character.  Use `ASCII character 9` instead.
_ENUMERATE_TABS_SCRIPT = """\
on run
    set sep to ASCII character 9
    set out to ""
    tell application "Safari"
        if not running then error "safari_not_running"
        set winList to every window
        if (count of winList) is 0 then error "no_windows"
        set frontWin to front window
        set frontIdx to index of frontWin
        set winIdx to 0
        repeat with w in winList
            set winIdx to winIdx + 1
            set isFront to (index of w) is frontIdx
            set tabIdx to 0
            set currentTabIdx to index of current tab of w
            repeat with t in every tab of w
                set tabIdx to tabIdx + 1
                set isCurrent to (tabIdx is currentTabIdx)
                set u to URL of t
                set nm to name of t
                if isFront then
                    set frontStr to "1"
                else
                    set frontStr to "0"
                end if
                if isCurrent then
                    set curStr to "1"
                else
                    set curStr to "0"
                end if
                set out to out & "TAB" & sep & winIdx & sep & tabIdx & sep & frontStr & sep & curStr & sep & u & sep & nm & linefeed
            end repeat
        end repeat
    end tell
    return out
end run
"""

# AppleScript template to run JavaScript in a specific window/tab.
# Argv: [0]=window_index  [1]=tab_index  [2]=javascript_code
_JS_IN_TAB_SCRIPT = """\
on run argv
    set winIdx to (item 1 of argv) as integer
    set tabIdx to (item 2 of argv) as integer
    set jsCode to item 3 of argv
    tell application "Safari"
        set w to window winIdx
        set t to tab tabIdx of w
        return do JavaScript jsCode in t
    end tell
end run
"""

# Minimal JavaScript: first N chars of document.body.innerText.
# Passed as a string argument to avoid escaping issues.
_BODY_JS_TEMPLATE = "(document.body.innerText || '').substring(0, {limit})"


def _run_osascript_script(
    script_text: str,
    args: list[str] | None = None,
    *,
    timeout: int = APPLE_EVENT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    command = ["osascript", "-"]
    if args:
        command.extend(args)
    return subprocess.run(
        command,
        input=script_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_CHATGPT_DOMAIN_RE = re.compile(r"chatgpt\.com|chat\.openai\.com", re.IGNORECASE)
_CONVERSATION_RE = re.compile(r"/c/([0-9a-f-]{8,})", re.IGNORECASE)


def _is_chatgpt(url: str) -> bool:
    return bool(_CHATGPT_DOMAIN_RE.search(url))


def _conversation_id(url: str) -> str:
    m = _CONVERSATION_RE.search(url)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Core probe logic
# ---------------------------------------------------------------------------

def enumerate_tabs() -> list[dict[str, Any]]:
    """Return a list of tab dicts from all Safari windows."""
    try:
        result = _run_osascript_script(_ENUMERATE_TABS_SCRIPT)
    except subprocess.TimeoutExpired:
        return [{"error": "AppleScript timed out enumerating tabs"}]

    if result.returncode != 0:
        msg = result.stderr.strip()
        if "safari_not_running" in msg:
            return [{"error": "Safari is not running"}]
        if "no_windows" in msg:
            return [{"error": "Safari has no open windows"}]
        return [{"error": f"AppleScript error: {msg}"}]

    tabs: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("TAB"):
            continue
        parts = line.split("\t")
        # Expected: TAB, winIdx, tabIdx, isFront, isCurrent, url, title
        if len(parts) < 7:
            continue
        _, win_idx_s, tab_idx_s, is_front_s, is_current_s, url, *title_parts = parts
        title = "\t".join(title_parts)
        try:
            win_idx = int(win_idx_s)
            tab_idx = int(tab_idx_s)
        except ValueError:
            continue
        conv_id = _conversation_id(url)
        tabs.append(
            {
                "window_index": win_idx,
                "tab_index": tab_idx,
                "title": title,
                "url": url,
                "is_front_window": is_front_s == "1",
                "is_current_tab": is_current_s == "1",
                "is_chatgpt": _is_chatgpt(url),
                "is_conversation": bool(conv_id),
                "conversation_id": conv_id,
                "body_fetch_status": "pending",
                "body_preview": "",
            }
        )
    return tabs


def fetch_body_for_tab(tab: dict[str, Any], limit: int) -> None:
    """Attempt to fetch body text for *tab* in-place; fills body_fetch_status / body_preview."""
    win_idx = tab["window_index"]
    tab_idx = tab["tab_index"]
    js_code = _BODY_JS_TEMPLATE.format(limit=limit)
    try:
        result = _run_osascript_script(
            _JS_IN_TAB_SCRIPT,
            [str(win_idx), str(tab_idx), js_code],
        )
    except subprocess.TimeoutExpired:
        tab["body_fetch_status"] = "error: AppleScript timed out"
        return

    if result.returncode != 0:
        stderr = result.stderr.strip()
        tab["body_fetch_status"] = f"error: {stderr}"
    else:
        body = result.stdout.rstrip("\n")
        tab["body_fetch_status"] = "ok"
        tab["body_preview"] = body


def run_probe(body_limit: int = 500) -> list[dict[str, Any]]:
    """Enumerate all Safari tabs and probe body text for ChatGPT conversation tabs."""
    tabs = enumerate_tabs()
    if not tabs:
        return tabs
    # If the first entry has an 'error' key, enumeration failed; return as-is.
    if "error" in tabs[0]:
        return tabs

    for tab in tabs:
        if body_limit == 0:
            tab["body_fetch_status"] = "skipped"
            continue
        # Probe every ChatGPT conversation tab; skip others.
        if tab.get("is_conversation"):
            fetch_body_for_tab(tab, body_limit)
        else:
            tab["body_fetch_status"] = "skipped"

    return tabs


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _format_human(tabs: list[dict[str, Any]]) -> str:
    if not tabs:
        return "(no tabs found)\n"
    if "error" in tabs[0]:
        return f"ERROR: {tabs[0]['error']}\n"

    lines: list[str] = []
    for t in tabs:
        flags: list[str] = []
        if t.get("is_front_window"):
            flags.append("FRONT_WINDOW")
        if t.get("is_current_tab"):
            flags.append("CURRENT_TAB")
        if t.get("is_chatgpt"):
            flags.append("CHATGPT")
        if t.get("is_conversation"):
            flags.append("CONVERSATION")
        flag_str = " ".join(flags) if flags else "-"
        lines.append(
            f"[W{t['window_index']}:T{t['tab_index']}] {flag_str}"
        )
        lines.append(f"  title : {t.get('title', '')}")
        lines.append(f"  url   : {t.get('url', '')}")
        if t.get("is_conversation"):
            lines.append(f"  conv  : {t.get('conversation_id', '')}")
        status = t.get("body_fetch_status", "")
        lines.append(f"  body  : [{status}]")
        preview = t.get("body_preview", "")
        if preview:
            wrapped = textwrap.indent(
                textwrap.shorten(preview, width=200, placeholder="…"),
                "    ",
            )
            lines.append(wrapped)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only probe: enumerate Safari windows/tabs and check ChatGPT readability.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output results as JSON.",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="Write output to file instead of stdout.",
    )
    parser.add_argument(
        "--body-limit",
        type=int,
        default=500,
        metavar="N",
        help="Character limit for body preview (0 = skip body fetch). Default: 500.",
    )
    args = parser.parse_args(argv)

    force_json = args.json_output
    if args.out and args.out.endswith(".json"):
        force_json = True

    tabs = run_probe(body_limit=args.body_limit)

    if force_json:
        output = json.dumps(tabs, ensure_ascii=False, indent=2) + "\n"
    else:
        output = _format_human(tabs)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"probe output written to: {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
