#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.error import URLError
from urllib.request import urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
BRIDGE_DIR = ROOT_DIR / "bridge"
INBOX_DIR = BRIDGE_DIR / "inbox"
OUTBOX_DIR = BRIDGE_DIR / "outbox"
HISTORY_DIR = BRIDGE_DIR / "history"
LOGS_DIR = ROOT_DIR / "logs"
STATE_PATH = BRIDGE_DIR / "state.json"
STOP_PATH = BRIDGE_DIR / "STOP"
BROWSER_CONFIG_PATH = BRIDGE_DIR / "browser_config.json"
PROMPT_REPLY_START = "===CHATGPT_PROMPT_REPLY==="
PROMPT_REPLY_END = "===END_REPLY==="
PLACEHOLDER_REPORT_HEADER = "# Codex Report Outbox"

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

DEFAULT_BROWSER_CONFIG: dict[str, Any] = {
    "chat_url_prefix": "https://chatgpt.com/",
    "cdp_endpoint": "http://127.0.0.1:9222",
    "chat_hint": "",
    "require_chat_hint": False,
    "reply_timeout_seconds": 90,
    "poll_interval_seconds": 2,
}

COMPOSER_SELECTORS = [
    "#prompt-textarea",
    "textarea[data-testid='prompt-textarea']",
    "textarea",
    "[contenteditable='true'][data-lexical-editor='true']",
    "[contenteditable='true'][translate='no']",
    "[contenteditable='true']",
]

SEND_BUTTON_SELECTORS = [
    "button[data-testid='send-button']",
    "button[aria-label='Send prompt']",
    "button[aria-label='Send message']",
    "button[aria-label='Send']",
]


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


def log_page_dump(page: Any, prefix: str = "raw_chatgpt_prompt_dump") -> Path | None:
    text = _body_text(page)
    if not text:
        return None
    return log_text(prefix, text, suffix="txt")


def load_browser_config() -> dict[str, Any]:
    config = DEFAULT_BROWSER_CONFIG.copy()
    if BROWSER_CONFIG_PATH.exists():
        loaded = json.loads(BROWSER_CONFIG_PATH.read_text(encoding="utf-8"))
        config.update(loaded)
    if "chat_url" in config and "chat_url_prefix" not in config:
        config["chat_url_prefix"] = config["chat_url"]
    return config


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


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise BridgeError(
            "playwright が見つかりません。requirements.txt をインストールしてください。"
        ) from exc
    return sync_playwright


def _run_osascript(lines: list[str]) -> subprocess.CompletedProcess[str]:
    command = ["osascript"]
    for line in lines:
        command.extend(["-e", line])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


def _frontmost_app_name() -> str:
    result = _run_osascript(
        ['tell application "System Events" to get name of first application process whose frontmost is true']
    )
    if result.returncode != 0:
        raise BridgeError(f"前面アプリを取得できませんでした: {result.stderr.strip()}")
    return result.stdout.strip()


def _chat_domain_matches(url: str, target_prefix: str) -> bool:
    return "chatgpt.com" in url or url.startswith(target_prefix) or "chat.openai.com" in url


def frontmost_chrome_tab_info(config: Mapping[str, Any]) -> dict[str, str]:
    if _frontmost_app_name() != "Google Chrome":
        raise BridgeError("Google Chrome が前面ではありません。対象チャットを前面表示してください。")

    result = _run_osascript(
        [
            'tell application "Google Chrome"',
            'if (count of windows) is 0 then error "no chrome window"',
            "set activeUrl to URL of active tab of front window",
            "set activeTitle to title of active tab of front window",
            'return activeUrl & linefeed & activeTitle',
            "end tell",
        ]
    )
    if result.returncode != 0:
        raise BridgeError(f"前面 Chrome タブ情報を取得できませんでした: {result.stderr.strip()}")

    parts = result.stdout.splitlines()
    url = parts[0].strip() if parts else ""
    title = parts[1].strip() if len(parts) > 1 else ""
    if not url:
        raise BridgeError("前面 Chrome タブの URL を取得できませんでした。")

    target_prefix = str(config.get("chat_url_prefix", DEFAULT_BROWSER_CONFIG["chat_url_prefix"]))
    if not _chat_domain_matches(url, target_prefix):
        raise BridgeError(f"前面 Chrome タブが ChatGPT ではありません: {title or '(no title)'} {url}")

    return {
        "url": url,
        "title": title,
    }


def _cdp_is_ready(config: Mapping[str, Any]) -> bool:
    endpoint = str(config.get("cdp_endpoint", DEFAULT_BROWSER_CONFIG["cdp_endpoint"])).rstrip("/") + "/json/version"
    try:
        with urlopen(endpoint, timeout=2) as response:
            return response.status == 200
    except URLError:
        return False
    except Exception:
        return False


def _connect_browser(playwright: Any, config: Mapping[str, Any]) -> Any:
    if not _cdp_is_ready(config):
        raise BridgeError(
            "Playwright が接続できる Chrome が見つかりませんでした。"
            " 事前準備として remote debugging 有効の Chrome を起動し、対象チャットを前面表示してください。"
        )

    endpoint = str(config.get("cdp_endpoint", DEFAULT_BROWSER_CONFIG["cdp_endpoint"]))
    browser = playwright.chromium.connect_over_cdp(endpoint)
    if not browser.contexts:
        browser.close()
        raise BridgeError("CDP 接続後に Chrome context を取得できませんでした。")
    return browser


def _normalized_url(url: str) -> str:
    return url.rstrip("/")


def _mark_last_visible_element(page: Any, selectors: list[str], attribute: str) -> bool:
    script = """
    ({selectors, attribute}) => {
      for (const node of document.querySelectorAll(`[${attribute}]`)) {
        node.removeAttribute(attribute);
      }
      const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (!style) return false;
        const rect = el.getBoundingClientRect();
        return style.display !== "none" &&
          style.visibility !== "hidden" &&
          rect.width > 0 &&
          rect.height > 0;
      };
      for (const selector of selectors) {
        const nodes = Array.from(document.querySelectorAll(selector));
        for (let index = nodes.length - 1; index >= 0; index -= 1) {
          const node = nodes[index];
          if (isVisible(node)) {
            node.setAttribute(attribute, "1");
            return true;
          }
        }
      }
      return false;
    }
    """
    return bool(page.evaluate(script, {"selectors": selectors, "attribute": attribute}))


def _body_text(page: Any) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000).strip()
    except Exception:
        return ""


def _page_matches_front_tab(page: Any, front_tab: Mapping[str, str], target_prefix: str) -> bool:
    if not _chat_domain_matches(page.url, target_prefix):
        return False
    if _normalized_url(page.url) == _normalized_url(str(front_tab.get("url", ""))):
        return True
    try:
        title = page.title()
    except Exception:
        title = ""
    return bool(title and title == front_tab.get("title"))


def _page_hint_matches(page: Any, front_tab: Mapping[str, str], config: Mapping[str, Any]) -> bool:
    hint = str(config.get("chat_hint", "")).strip()
    if not hint:
        return not bool(config.get("require_chat_hint"))
    hint_lower = hint.lower()
    title = str(front_tab.get("title", "")).lower()
    url = str(front_tab.get("url", "")).lower()
    if hint_lower in title or hint_lower in url:
        return True
    return hint_lower in _body_text(page).lower()


def _ensure_target_chat(page: Any, front_tab: Mapping[str, str], config: Mapping[str, Any]) -> None:
    if _page_hint_matches(page, front_tab, config):
        return

    dump_path = log_page_dump(page)
    dump_note = f" raw dump: {repo_relative(dump_path)}" if dump_path else ""
    hint = str(config.get("chat_hint", "")).strip()
    if hint:
        raise BridgeError(
            f"前面の ChatGPT タブから chat_hint='{hint}' を確認できませんでした。"
            f" 対象チャットが違う可能性があるため停止しました: {front_tab.get('title', '')} {front_tab.get('url', '')}"
            f"{dump_note}"
        )
    raise BridgeError(
        "前面の ChatGPT タブを識別できませんでした。"
        f" browser_config.json の chat_hint を設定して対象チャットを絞り込んでください。{dump_note}"
    )


def _resolve_chatgpt_page(context: Any, config: Mapping[str, Any]) -> tuple[Any, dict[str, str]]:
    front_tab = frontmost_chrome_tab_info(config)
    target_prefix = str(config.get("chat_url_prefix", DEFAULT_BROWSER_CONFIG["chat_url_prefix"]))
    candidates = [page for page in context.pages if _page_matches_front_tab(page, front_tab, target_prefix)]
    if not candidates:
        raise BridgeError(
            f"前面の ChatGPT タブを CDP から特定できませんでした。"
            f" URL={front_tab.get('url', '')} title={front_tab.get('title', '')}"
        )
    if len(candidates) > 1:
        raise BridgeError(
            f"前面の ChatGPT タブ候補が複数見つかりました。chat_hint を設定して絞り込んでください。"
            f" URL={front_tab.get('url', '')}"
        )

    page = candidates[0]
    page.bring_to_front()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(600)
    _ensure_target_chat(page, front_tab, config)
    return page, front_tab


def _find_composer(page: Any) -> Any | None:
    if not _mark_last_visible_element(page, COMPOSER_SELECTORS, "data-bridge-composer"):
        return None
    return page.locator("[data-bridge-composer='1']").first


def ensure_chatgpt_ready(page: Any, config: Mapping[str, Any], *, allow_manual_login: bool = False) -> Any:
    deadline = time.time() + 20
    composer = _find_composer(page)
    while composer is None and time.time() < deadline:
        page.wait_for_timeout(1000)
        composer = _find_composer(page)

    if composer is not None:
        return composer

    body_text = _body_text(page)
    dump_path = log_page_dump(page) if body_text else None
    dump_note = f" raw dump: {repo_relative(dump_path)}" if dump_path else ""
    login_hint = "Log in" in body_text or "ログイン" in body_text or "Sign up" in body_text
    if login_hint or allow_manual_login:
        raise BridgeError(
            "ChatGPT の入力欄が見つかりませんでした。"
            " 事前準備として Chrome でログイン済みの対象チャットを前面表示してください。"
            f"{dump_note}"
        )
    waiting_hint = "しばらくお待ちください" in body_text or "Please stand by" in body_text
    if waiting_hint:
        raise BridgeError(
            "ChatGPT ページの読み込みが完了していません。対象チャットが表示されてから再実行してください。"
            f"{dump_note}"
        )
    raise BridgeError(
        "ChatGPT の入力欄が見つかりませんでした。対象チャットが前面表示されているか確認してください。"
        f"{dump_note}"
    )


@contextmanager
def open_chatgpt_page(*, reset_chat: bool = False) -> Iterator[tuple[Any, Any, dict[str, Any], dict[str, str]]]:
    del reset_chat
    config = load_browser_config()
    sync_playwright = _import_playwright()

    with sync_playwright() as playwright:
        browser = _connect_browser(playwright, config)
        try:
            context = browser.contexts[0]
            page, front_tab = _resolve_chatgpt_page(context, config)
            yield context, page, config, front_tab
        finally:
            browser.close()


def fill_chatgpt_composer(page: Any, text: str, config: Mapping[str, Any], *, allow_manual_login: bool = False) -> None:
    composer = ensure_chatgpt_ready(page, config, allow_manual_login=allow_manual_login)
    composer.scroll_into_view_if_needed()
    composer.click()
    composer.focus()
    page.wait_for_timeout(200)
    tag_name = composer.evaluate("el => el.tagName.toLowerCase()")
    is_contenteditable = bool(composer.evaluate("el => !!el.isContentEditable"))
    if tag_name == "textarea":
        composer.fill(text)
        return
    if is_contenteditable:
        page.keyboard.press("Meta+A")
        page.keyboard.insert_text(text)
        return
    raise BridgeError("ChatGPT の入力欄に文字を設定できませんでした。")


def submit_chatgpt_message(page: Any) -> None:
    page.wait_for_timeout(300)
    if _mark_last_visible_element(page, SEND_BUTTON_SELECTORS, "data-bridge-send"):
        button = page.locator("[data-bridge-send='1']").first
        try:
            if button.is_enabled():
                button.click()
                return
        except Exception:
            pass
    page.keyboard.press("Enter")


def send_to_chatgpt(text: str) -> None:
    with open_chatgpt_page(reset_chat=False) as (_, page, config, _):
        fill_chatgpt_composer(page, text, config)
        submit_chatgpt_message(page)


def draft_message_in_chatgpt(text: str) -> None:
    with open_chatgpt_page(reset_chat=False) as (_, page, config, _):
        fill_chatgpt_composer(page, text, config, allow_manual_login=True)


def read_chatgpt_conversation_dom(page: Any) -> str:
    selectors = ["main", "article", "body"]
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.count() == 0:
                continue
            text = locator.first.inner_text(timeout=3000).strip()
        except Exception:
            continue
        if text:
            return text
    raise BridgeError("Chrome 上の ChatGPT 会話テキストを取得できませんでした。会話領域が見えているか確認してください。")


def read_chatgpt_conversation() -> str:
    with open_chatgpt_page(reset_chat=False) as (_, page, _, _):
        return read_chatgpt_conversation_dom(page)


def wait_for_prompt_reply_text(timeout_seconds: int | None = None) -> str:
    with open_chatgpt_page(reset_chat=False) as (_, page, config, front_tab):
        timeout = int(timeout_seconds or config.get("reply_timeout_seconds", 90))
        poll_seconds = float(config.get("poll_interval_seconds", 2))
        deadline = time.time() + timeout
        latest_text = ""
        while time.time() < deadline:
            latest_text = read_chatgpt_conversation_dom(page)
            if PROMPT_REPLY_START in latest_text and PROMPT_REPLY_END in latest_text:
                return latest_text
            page.wait_for_timeout(int(poll_seconds * 1000))
        dump_note = ""
        if latest_text.strip():
            dump_path = log_text("raw_chatgpt_prompt_dump", latest_text, suffix="txt")
            dump_note = f" raw dump: {repo_relative(dump_path)}"
        raise BridgeError(
            "制限時間内に CHATGPT_PROMPT_REPLY ブロックを確認できませんでした。"
            f" 対象チャットを確認してください: {front_tab.get('title', '')} {front_tab.get('url', '')}"
            f"{dump_note}"
        )


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
