#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT_DIR = Path(__file__).resolve().parents[1]
BRIDGE_DIR = ROOT_DIR / "bridge"
INBOX_DIR = BRIDGE_DIR / "inbox"
OUTBOX_DIR = BRIDGE_DIR / "outbox"
HISTORY_DIR = BRIDGE_DIR / "history"
LOGS_DIR = ROOT_DIR / "logs"
STATE_PATH = BRIDGE_DIR / "state.json"
STOP_PATH = BRIDGE_DIR / "STOP"
CHATGPT_APP_NAME = "ChatGPT"
PROMPT_REPLY_START = "===CHATGPT_PROMPT_REPLY==="
PROMPT_REPLY_END = "===END_REPLY==="

DEFAULT_STATE: dict[str, Any] = {
    "mode": "idle",
    "need_chatgpt_prompt": True,
    "need_codex_run": False,
    "need_chatgpt_next": False,
    "last_prompt_file": "",
    "last_report_file": "",
    "pause": False,
    "error": False,
    "error_message": "",
    "cycle": 0,
}

PLACEHOLDER_REPORT_HEADER = "# Codex Report Outbox"


class BridgeError(Exception):
    """Raised when a bridge operation fails and should mark state.error=true."""


class BridgeStop(Exception):
    """Raised when the bridge should stop without marking an operational error."""


def ensure_runtime_dirs() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> dict[str, Any]:
    ensure_runtime_dirs()
    if not STATE_PATH.exists():
        save_state(DEFAULT_STATE.copy())
        return DEFAULT_STATE.copy()

    with STATE_PATH.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)

    state = DEFAULT_STATE.copy()
    state.update(loaded)
    return state


def save_state(state: Mapping[str, Any]) -> None:
    ensure_runtime_dirs()
    normalized = DEFAULT_STATE.copy()
    normalized.update(state)
    STATE_PATH.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def update_state(**changes: Any) -> dict[str, Any]:
    state = load_state()
    state.update(changes)
    save_state(state)
    return state


def mark_error(message: str) -> None:
    state = load_state()
    state["error"] = True
    state["error_message"] = message
    save_state(state)


def clear_error_fields(state: dict[str, Any]) -> dict[str, Any]:
    state["error"] = False
    state["error_message"] = ""
    return state


def check_stop_conditions(state: Mapping[str, Any] | None = None) -> None:
    current_state = dict(state or load_state())
    if STOP_PATH.exists():
        raise BridgeStop("bridge/STOP が存在するため停止しました。")
    if current_state.get("pause"):
        raise BridgeStop("state.pause=true のため停止しました。")
    if current_state.get("error"):
        message = current_state.get("error_message") or "state.error=true"
        raise BridgeStop(f"state.error=true のため停止しました: {message}")


def guarded_main(task: Callable[[dict[str, Any]], int]) -> int:
    try:
        state = load_state()
        check_stop_conditions(state)
        return task(state)
    except BridgeStop as exc:
        print(f"[stop] {exc}")
        return 0
    except Exception as exc:  # pragma: no cover - top-level safety net
        mark_error(str(exc))
        print(f"[error] {exc}", file=sys.stderr)
        return 1


def now_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT_DIR).as_posix()


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def log_text(prefix: str, content: str, suffix: str = "md") -> Path:
    ensure_runtime_dirs()
    log_path = LOGS_DIR / f"{now_stamp()}_{prefix}.{suffix}"
    write_text(log_path, content)
    return log_path


def state_snapshot(state: Mapping[str, Any]) -> str:
    fields = [
        f"- mode: {state.get('mode', '')}",
        f"- cycle: {state.get('cycle', 0)}",
        f"- need_chatgpt_prompt: {state.get('need_chatgpt_prompt', False)}",
        f"- need_codex_run: {state.get('need_codex_run', False)}",
        f"- need_chatgpt_next: {state.get('need_chatgpt_next', False)}",
        f"- last_prompt_file: {state.get('last_prompt_file', '')}",
        f"- last_report_file: {state.get('last_report_file', '')}",
        f"- pause: {state.get('pause', False)}",
        f"- error: {state.get('error', False)}",
    ]
    if state.get("error_message"):
        fields.append(f"- error_message: {state['error_message']}")
    return "\n".join(fields)


def render_template(template_text: str, values: Mapping[str, str]) -> str:
    rendered = template_text
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def read_last_report_text(state: Mapping[str, Any]) -> str:
    outbox_path = OUTBOX_DIR / "codex_report.md"
    outbox_text = read_text(outbox_path).strip()
    if outbox_text and not outbox_text.startswith(PLACEHOLDER_REPORT_HEADER):
        return outbox_text

    last_report_file = str(state.get("last_report_file") or "").strip()
    if last_report_file:
        candidate = ROOT_DIR / last_report_file
        if candidate.exists():
            return read_text(candidate).strip()

    return "（前回の完了報告はまだありません）"


def normalize_prompt_body(raw_body: str) -> str:
    body = raw_body.strip()
    lines = body.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip().lower().lstrip("#").strip() == "codex prompt":
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    body = "\n".join(lines).strip()
    if not body:
        raise BridgeError("抽出した Codex Prompt 本文が空でした。")
    return body + "\n"


def extract_last_prompt_reply(raw_text: str) -> str:
    pattern = re.compile(
        rf"{re.escape(PROMPT_REPLY_START)}(.*?){re.escape(PROMPT_REPLY_END)}",
        re.DOTALL,
    )
    matches = pattern.findall(raw_text)
    if not matches:
        raise BridgeError("CHATGPT_PROMPT_REPLY ブロックを抽出できませんでした。")
    return normalize_prompt_body(matches[-1])


def _import_pyautogui():
    try:
        import pyautogui  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise BridgeError("pyautogui が見つかりません。requirements.txt をインストールしてください。") from exc
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1
    return pyautogui


def _import_pyperclip():
    try:
        import pyperclip  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise BridgeError("pyperclip が見つかりません。requirements.txt をインストールしてください。") from exc
    return pyperclip


def _try_log_window_titles() -> None:
    try:
        import pygetwindow as gw  # type: ignore

        titles = [window.title for window in gw.getAllWindows() if window.title]
        if titles:
            log_text("chatgpt_window_titles", "\n".join(sorted(set(titles))), suffix="txt")
    except Exception:
        return


def activate_chatgpt_app() -> None:
    _try_log_window_titles()
    applescript = f'tell application "{CHATGPT_APP_NAME}" to activate'
    result = subprocess.run(
        ["osascript", "-e", applescript],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        fallback = subprocess.run(
            ["open", "-a", CHATGPT_APP_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        if fallback.returncode != 0:
            raise BridgeError(
                "ChatGPT アプリを前面化できませんでした。"
                f" osascript={result.stderr.strip()} open={fallback.stderr.strip()}"
            )
    time.sleep(1.0)


def paste_into_chatgpt(text: str, press_enter: bool) -> None:
    pyautogui = _import_pyautogui()
    pyperclip = _import_pyperclip()
    activate_chatgpt_app()

    previous_clipboard = pyperclip.paste()
    pyperclip.copy(text)
    time.sleep(0.2)
    pyautogui.hotkey("command", "v")
    time.sleep(0.3)
    if press_enter:
        pyautogui.press("enter")
        time.sleep(0.3)
    pyperclip.copy(previous_clipboard)


def send_to_chatgpt(text: str) -> None:
    paste_into_chatgpt(text, press_enter=True)


def copy_chatgpt_conversation() -> str:
    pyautogui = _import_pyautogui()
    pyperclip = _import_pyperclip()
    activate_chatgpt_app()

    previous_clipboard = pyperclip.paste()
    pyautogui.hotkey("command", "a")
    time.sleep(0.3)
    pyautogui.hotkey("command", "c")
    time.sleep(0.5)
    copied = pyperclip.paste()
    pyperclip.copy(previous_clipboard)
    if not copied.strip():
        raise BridgeError(
            "会話全文のコピー結果が空でした。ChatGPT の会話領域を前面にして再実行してください。"
        )
    return copied


def build_chatgpt_request(
    *,
    state: Mapping[str, Any],
    template_path: Path,
    next_todo: str,
    open_questions: str,
    current_status: str | None = None,
    last_report: str | None = None,
) -> str:
    template_text = read_text(template_path).strip()
    if not template_text:
        raise BridgeError(f"テンプレートを読めませんでした: {repo_relative(template_path)}")

    values = {
        "CURRENT_STATUS": current_status or state_snapshot(state),
        "LAST_REPORT": last_report or read_last_report_text(state),
        "NEXT_TODO": next_todo,
        "OPEN_QUESTIONS": open_questions,
    }
    return render_template(template_text, values).strip() + "\n"
