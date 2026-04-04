#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
BRIDGE_DIR = ROOT_DIR / "bridge"
INBOX_DIR = BRIDGE_DIR / "inbox"
OUTBOX_DIR = BRIDGE_DIR / "outbox"
HISTORY_DIR = BRIDGE_DIR / "history"
LOGS_DIR = ROOT_DIR / "logs"
STATE_PATH = BRIDGE_DIR / "state.json"
STOP_PATH = BRIDGE_DIR / "STOP"
BROWSER_CONFIG_PATH = BRIDGE_DIR / "browser_config.json"
PROJECT_CONFIG_PATH = BRIDGE_DIR / "project_config.json"
PROMPT_REPLY_START = "===CHATGPT_PROMPT_REPLY==="
PROMPT_REPLY_END = "===END_REPLY==="
NO_CODEX_REPLY_START = "===CHATGPT_NO_CODEX==="
NO_CODEX_REPLY_END = "===END_NO_CODEX==="
HANDOFF_REPLY_START = "===CHATGPT_CHAT_HANDOFF==="
HANDOFF_REPLY_END = "===END_CHAT_HANDOFF==="
BRIDGE_SUMMARY_START = "===BRIDGE_SUMMARY==="
BRIDGE_SUMMARY_END = "===END_BRIDGE_SUMMARY==="
CHATGPT_REQUEST_START = "===CHATGPT_REQUEST==="
CHATGPT_REQUEST_END = "===END_CHATGPT_REQUEST==="
PLACEHOLDER_REPORT_HEADER = "# Codex Report Outbox"
OUTBOX_PLACEHOLDER_TEXT = """# Codex Report Outbox

このファイルは Codex 実行完了時に上書きします。
運用時はここに最新の完了報告が入ります。
""".strip()

DEFAULT_STATE: dict[str, Any] = {
    "mode": "idle",
    "need_chatgpt_prompt": True,
    "need_codex_run": False,
    "need_chatgpt_next": False,
    "last_prompt_file": "",
    "last_report_file": "",
    "pending_request_hash": "",
    "pending_request_source": "",
    "pending_request_log": "",
    "pending_handoff_hash": "",
    "pending_handoff_source": "",
    "pending_handoff_log": "",
    "last_processed_request_hash": "",
    "last_processed_reply_hash": "",
    "current_chat_session": "",
    "human_review_auto_continue_count": 0,
    "pause": False,
    "error": False,
    "error_message": "",
    "cycle": 0,
}

DEFAULT_BROWSER_CONFIG: dict[str, Any] = {
    "app_name": "Safari",
    "chat_url_prefix": "https://chatgpt.com/",
    "conversation_url_keywords": ["/c/"],
    "chat_hint": "",
    "require_chat_hint": False,
    "fetch_timeout_seconds": 1800,
    "reply_timeout_seconds": 1800,
    "poll_interval_seconds": 2,
    "apple_event_timeout_retry_count": 1,
    "apple_event_timeout_retry_delay_seconds": 2,
    "runner_heartbeat_seconds": 10,
    "extended_fetch_timeout_seconds": 600,
    "project_page_url": "",
}

DEFAULT_PROJECT_CONFIG: dict[str, Any] = {
    "project_name": ROOT_DIR.name,
    "bridge_runtime_root": ".",
    "worker_repo_path": ".",
    "worker_repo_marker_mode": "strict",
    "worker_repo_markers": [],
    "codex_bin": "codex",
    "codex_model": "",
    "codex_sandbox": "",
    "codex_timeout_seconds": 7200,
    "report_request_next_todo": "前回 report を踏まえて、次の 1 フェーズ分の Codex 用 prompt を作成してください。",
    "report_request_open_questions": "未解決事項があれば安全側で補ってください。",
}

REPO_LIKE_MARKERS = [
    ".git",
    ".github",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
]

PROJECT_CONFIG_WARNING_KEY = "_project_config_warnings"
WORKER_REPO_MARKER_MODES = {"strict", "warning"}

COMPOSER_SELECTORS = [
    "#prompt-textarea",
    "textarea[data-testid='prompt-textarea']",
    "textarea",
    "[contenteditable='true'][data-lexical-editor='true']",
    "[contenteditable='true'][translate='no']",
    "[contenteditable='true']",
]
PROJECT_CHAT_COMPOSER_HINTS = (
    "このプロジェクト内の新しいチャット",
    "このプロジェクト内で新しいチャット",
    "プロジェクト内の新しいチャット",
)

SEND_BUTTON_SELECTORS = [
    "button[data-testid='send-button']",
    "button[aria-label='Send prompt']",
    "button[aria-label='Send message']",
    "button[aria-label='Send']",
]

APPLE_EVENT_TIMEOUT_SECONDS = 15
APPLE_EVENT_TIMEOUT_MARKERS = (
    "AppleEvent timeout",
    "AppleEventがタイムアウト",
    "AppleEvent timed out",
)


class BridgeError(Exception):
    """Raised when a bridge operation fails and should mark state.error=true."""


class BridgeStop(Exception):
    """Raised when the bridge should stop without marking an operational error."""


@dataclass
class SafariChatPage:
    config: Mapping[str, Any]
    front_tab: dict[str, str]

    def wait_for_timeout(self, milliseconds: int) -> None:
        time.sleep(milliseconds / 1000.0)

    def evaluate(self, script: str) -> str:
        self.assert_same_front_tab()
        return _run_safari_javascript(script)

    def assert_same_front_tab(self) -> None:
        current_tab = frontmost_safari_tab_info(self.config)
        if _same_tab(self.front_tab, current_tab):
            return

        dump_note = ""
        target_prefix = str(self.config.get("chat_url_prefix", DEFAULT_BROWSER_CONFIG["chat_url_prefix"]))
        if _chat_domain_matches(current_tab.get("url", ""), target_prefix):
            dump_text = _body_text_unchecked()
            if dump_text:
                dump_path = log_text("raw_chatgpt_prompt_dump", dump_text, suffix="txt")
                dump_note = f" raw dump: {repo_relative(dump_path)}"

        raise BridgeError(
            "Safari の現在タブが切り替わりました。対象チャットを再表示してから再実行してください。"
            f" 現在: {current_tab.get('title', '')} {current_tab.get('url', '')}{dump_note}"
        )


@dataclass(frozen=True)
class BridgeStatusView:
    label: str
    detail: str


@dataclass(frozen=True)
class BridgeHandoffView:
    title: str
    detail: str


@dataclass(frozen=True)
class BridgeResumePromptView:
    title: str
    detail: str
    example: str


@dataclass(frozen=True)
class ChatGPTReplyDecision:
    kind: str
    body: str
    note: str
    raw_block: str


@dataclass(frozen=True)
class CodexProgressSnapshot:
    status: str
    excerpt: str
    progress_line: str
    last_message_path: str
    stdout_log_path: str
    stderr_log_path: str
    stdout_tail: str
    stderr_tail: str


@dataclass(frozen=True)
class ChatGPTWaitEvent:
    name: str
    latest_text: str


def build_chatgpt_reply_contract_section() -> str:
    return "\n".join(
        [
            "## bridge reply contract",
            "",
            "bridge が返答を機械処理するため、前置きや補足説明を付けず、次のどちらか 1 つのブロックだけを返してください。",
            "",
            "Codex に渡す 1 フェーズ prompt がある場合:",
            "===CHATGPT_PROMPT_REPLY===",
            "[Codex 用 1 フェーズ prompt 本文]",
            "===END_REPLY===",
            "",
            "今回は Codex に渡さない場合:",
            "===CHATGPT_NO_CODEX===",
            "completed | human_review | need_info",
            "[必要なら短い理由]",
            "===END_NO_CODEX===",
            "",
            "`CHATGPT_NO_CODEX` の先頭行は completed / human_review / need_info のいずれかにしてください。",
        ]
    ).strip()


def present_bridge_status(
    state: Mapping[str, Any],
    *,
    blocked: bool = False,
    stale_codex_running: bool = False,
) -> BridgeStatusView:
    mode = str(state.get("mode", "idle"))
    need_chatgpt_prompt = bool(state.get("need_chatgpt_prompt"))
    need_chatgpt_next = bool(state.get("need_chatgpt_next"))
    need_codex_run = bool(state.get("need_codex_run"))
    pending_handoff_log = str(state.get("pending_handoff_log", "")).strip()
    chatgpt_decision = str(state.get("chatgpt_decision", "")).strip()
    chatgpt_decision_note = str(state.get("chatgpt_decision_note", "")).strip()

    if bool(state.get("error")):
        return BridgeStatusView("異常", "handoff と summary を見て、必要なら error_message を確認してから再開します。")

    if blocked or stale_codex_running or runtime_stop_path().exists() or bool(state.get("pause")):
        return BridgeStatusView("人確認待ち", "handoff と summary の note を確認してから再開します。")

    if mode == "awaiting_user" or chatgpt_decision in {"human_review", "need_info"}:
        detail = chatgpt_decision_note or "ChatGPT が Codex 不要と判断しました。人が次の判断を行います。"
        return BridgeStatusView("人確認待ち", detail)

    if mode == "idle" and need_chatgpt_prompt:
        return BridgeStatusView("初回依頼文の入力待ち", "最初に ChatGPT へ送る本文を入力します。")

    if mode in {"waiting_prompt_reply", "extended_wait", "await_late_completion"}:
        return BridgeStatusView("ChatGPT返答待ち", "返答から次の Codex 用 prompt を回収します。")

    if mode == "ready_for_codex" and need_codex_run:
        return BridgeStatusView("Codex実行待ち", "bridge が Codex worker を 1 回起動します。")

    if mode == "ready_for_codex":
        return BridgeStatusView("人確認待ち", "Codex 実行条件を確認してください。")

    if mode == "codex_running":
        return BridgeStatusView("Codex実行中", "Codex worker の完了報告を待っています。")

    if mode == "codex_done":
        return BridgeStatusView("完了報告整理中", "完了報告を archive して次 request へ進めます。")

    if mode == "idle" and need_chatgpt_next and pending_handoff_log:
        return BridgeStatusView("ChatGPTへ依頼中", "handoff は回収済みです。project 内の新しいチャット送信を再試行します。")

    if mode == "idle" and need_chatgpt_next:
        return BridgeStatusView("ChatGPTへ依頼中", "完了報告をもとに次の依頼を送ります。")

    if mode == "completed":
        detail = chatgpt_decision_note or "追加の操作は不要です。"
        return BridgeStatusView("完了", detail)

    if mode == "idle" and not need_chatgpt_prompt and not need_chatgpt_next and not need_codex_run:
        return BridgeStatusView("完了", "追加の操作は不要です。")

    return BridgeStatusView("人確認待ち", "内部状態の詳細を確認してから再開します。")


def present_bridge_handoff(
    state: Mapping[str, Any],
    *,
    reason: str = "",
    suggested_note: str = "",
    blocked: bool = False,
    stale_codex_running: bool = False,
    cycle_boundary_stop: bool = False,
) -> BridgeHandoffView:
    mode = str(state.get("mode", "idle"))
    need_chatgpt_prompt = bool(state.get("need_chatgpt_prompt"))
    need_chatgpt_next = bool(state.get("need_chatgpt_next"))
    need_codex_run = bool(state.get("need_codex_run"))
    chatgpt_decision = str(state.get("chatgpt_decision", "")).strip()
    error_message = str(state.get("error_message", "")).strip()
    normalized_reason = reason.strip()

    if bool(state.get("error")):
        detail = suggested_note or error_message or "summary と error_message を確認してください。"
        return BridgeHandoffView("異常終了です。詳細ログを確認してください。", detail)

    if cycle_boundary_stop:
        detail = suggested_note or "この run は現在の cycle 完了までで止めました。次 cycle は次回実行で進めます。"
        return BridgeHandoffView("この run は cycle 完了で停止しました。", detail)

    if chatgpt_decision == "completed" or mode == "completed":
        detail = suggested_note or "summary を確認し、必要なら report を見れば十分です。"
        return BridgeHandoffView("完了しました。", detail)

    if chatgpt_decision == "human_review":
        detail = suggested_note or "summary / note を確認して人が次の判断を行ってください。"
        return BridgeHandoffView("人の判断が必要です。summary / note を確認してください。", detail)

    if chatgpt_decision == "need_info":
        detail = suggested_note or "不足している情報を補ってから再開してください。"
        return BridgeHandoffView("情報が不足しています。入力内容を補って再開してください。", detail)

    if blocked or stale_codex_running or runtime_stop_path().exists() or bool(state.get("pause")):
        detail = suggested_note or "自動継続しません。summary / note を確認してください。"
        return BridgeHandoffView("自動継続しません。summary / note を確認してください。", detail)

    if normalized_reason.startswith("--max-steps="):
        detail = suggested_note or "summary を見て、続けるなら suggested_next_command を再実行してください。"
        return BridgeHandoffView("上限回数に達したため一旦停止しました。", detail)

    if normalized_reason.startswith("ユーザー中断"):
        detail = suggested_note or "summary を見て、再開するかここで止めるかを決めてください。"
        return BridgeHandoffView("途中で停止しました。summary / note を確認してください。", detail)

    if mode == "idle" and not need_chatgpt_prompt and not need_chatgpt_next and not need_codex_run:
        detail = suggested_note or "追加の操作は不要です。"
        return BridgeHandoffView("完了しました。", detail)

    status = present_bridge_status(state, blocked=blocked, stale_codex_running=stale_codex_running)
    detail = suggested_note or status.detail
    if status.label == "人確認待ち":
        return BridgeHandoffView("人の確認が必要です。summary / note を確認してください。", detail)
    if status.label == "完了":
        return BridgeHandoffView("完了しました。", detail)
    return BridgeHandoffView(f"{status.label}です。", detail)


def present_resume_prompt(state: Mapping[str, Any]) -> BridgeResumePromptView:
    decision = str(state.get("chatgpt_decision", "")).strip()
    note = str(state.get("chatgpt_decision_note", "")).strip()

    if decision == "human_review":
        return BridgeResumePromptView(
            title="人の判断内容を入力してください。",
            detail=note or "次の ChatGPT request に添える判断結果や方針だけを短く入力します。",
            example="\n".join(
                [
                    "判断結果: sample browser の軽い UI polish に留める",
                    "制約: schema / resolver / preview / playback / export は変えない",
                ]
            ),
        )

    if decision == "need_info":
        return BridgeResumePromptView(
            title="不足情報を入力してください。",
            detail=note or "次の ChatGPT request に添える不足情報だけを短く入力します。",
            example="\n".join(
                [
                    "不足情報: 対象画面は sample browser panel",
                    "補足: shared row は変更しない",
                ]
            ),
        )

    return BridgeResumePromptView(
        title="再開用の補足を入力してください。",
        detail=note or "次の ChatGPT request に添える補足だけを短く入力します。",
        example="補足: 今回の方針や不足情報を 2 行程度で書く",
    )


def ensure_runtime_dirs() -> None:
    runtime_inbox_dir().mkdir(parents=True, exist_ok=True)
    runtime_outbox_dir().mkdir(parents=True, exist_ok=True)
    runtime_history_dir().mkdir(parents=True, exist_ok=True)
    runtime_logs_dir().mkdir(parents=True, exist_ok=True)


def load_state() -> dict[str, Any]:
    ensure_runtime_dirs()
    state_path = runtime_state_path()
    if not state_path.exists():
        save_state(DEFAULT_STATE.copy())
        return DEFAULT_STATE.copy()

    with state_path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)

    state = DEFAULT_STATE.copy()
    state.update(loaded)
    return state


def save_state(state: Mapping[str, Any]) -> None:
    ensure_runtime_dirs()
    normalized = DEFAULT_STATE.copy()
    normalized.update(state)
    runtime_state_path().write_text(
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
    if runtime_stop_path().exists():
        raise BridgeStop("bridge/STOP が存在するため停止しました。")
    if current_state.get("pause"):
        raise BridgeStop("state.pause=true のため停止しました。")
    if current_state.get("error"):
        message = current_state.get("error_message") or "state.error=true"
        raise BridgeStop(f"state.error=true のため停止しました: {message}")


def guarded_main(
    task: Callable[[dict[str, Any]], int],
    *,
    recover_state: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> int:
    try:
        state = load_state()
        if recover_state is not None:
            state = recover_state(state)
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
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return str(resolved)


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def log_text(prefix: str, content: str, suffix: str = "md") -> Path:
    ensure_runtime_dirs()
    log_path = runtime_logs_dir() / f"{now_stamp()}_{prefix}.{suffix}"
    write_text(log_path, content)
    return log_path


def read_latest_prompt_request_text() -> str:
    ensure_runtime_dirs()
    candidates = sorted(runtime_logs_dir().glob("*sent_prompt_request*.md"))
    if not candidates:
        return ""
    return read_text(candidates[-1]).strip()


def stable_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pending_request_log_path(state: Mapping[str, Any]) -> Path | None:
    raw_path = str(state.get("pending_request_log", "")).strip()
    if not raw_path:
        return None
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (bridge_runtime_root() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def read_pending_request_text(state: Mapping[str, Any]) -> str:
    log_path = pending_request_log_path(state)
    if log_path and log_path.exists():
        return read_text(log_path).strip()
    return read_latest_prompt_request_text()


def pending_handoff_log_path(state: Mapping[str, Any]) -> Path | None:
    raw_path = str(state.get("pending_handoff_log", "")).strip()
    if not raw_path:
        return None
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (bridge_runtime_root() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def read_pending_handoff_text(state: Mapping[str, Any]) -> str:
    log_path = pending_handoff_log_path(state)
    if log_path and log_path.exists():
        return read_text(log_path).strip()
    return ""


def clear_pending_request_fields(state: dict[str, Any]) -> dict[str, Any]:
    state["pending_request_hash"] = ""
    state["pending_request_source"] = ""
    state["pending_request_log"] = ""
    return state


def clear_pending_handoff_fields(state: dict[str, Any]) -> dict[str, Any]:
    state["pending_handoff_hash"] = ""
    state["pending_handoff_source"] = ""
    state["pending_handoff_log"] = ""
    return state


def load_browser_config() -> dict[str, Any]:
    config = DEFAULT_BROWSER_CONFIG.copy()
    loaded: dict[str, Any] = {}
    if BROWSER_CONFIG_PATH.exists():
        loaded = json.loads(BROWSER_CONFIG_PATH.read_text(encoding="utf-8"))
        config.update(loaded)

    keywords = config.get("conversation_url_keywords", DEFAULT_BROWSER_CONFIG["conversation_url_keywords"])
    if isinstance(keywords, str):
        config["conversation_url_keywords"] = [keywords]
    elif isinstance(keywords, list):
        config["conversation_url_keywords"] = [str(keyword) for keyword in keywords if str(keyword)]
    else:
        config["conversation_url_keywords"] = list(DEFAULT_BROWSER_CONFIG["conversation_url_keywords"])

    if "fetch_timeout_seconds" in loaded and "reply_timeout_seconds" not in loaded:
        config["reply_timeout_seconds"] = config["fetch_timeout_seconds"]
    elif "reply_timeout_seconds" in loaded and "fetch_timeout_seconds" not in loaded:
        config["fetch_timeout_seconds"] = config["reply_timeout_seconds"]
    elif "fetch_timeout_seconds" not in config and "reply_timeout_seconds" in config:
        config["fetch_timeout_seconds"] = config["reply_timeout_seconds"]
    if "reply_timeout_seconds" not in config and "fetch_timeout_seconds" in config:
        config["reply_timeout_seconds"] = config["fetch_timeout_seconds"]

    config["fetch_timeout_seconds"] = _coerce_browser_int(
        config.get("fetch_timeout_seconds", DEFAULT_BROWSER_CONFIG["fetch_timeout_seconds"]),
        default=int(DEFAULT_BROWSER_CONFIG["fetch_timeout_seconds"]),
        minimum=1,
    )
    config["reply_timeout_seconds"] = _coerce_browser_int(
        config.get("reply_timeout_seconds", config["fetch_timeout_seconds"]),
        default=int(config["fetch_timeout_seconds"]),
        minimum=1,
    )
    config["poll_interval_seconds"] = _coerce_browser_float(
        config.get("poll_interval_seconds", DEFAULT_BROWSER_CONFIG["poll_interval_seconds"]),
        default=float(DEFAULT_BROWSER_CONFIG["poll_interval_seconds"]),
        minimum=0.1,
    )
    config["apple_event_timeout_retry_count"] = _coerce_browser_int(
        config.get("apple_event_timeout_retry_count", DEFAULT_BROWSER_CONFIG["apple_event_timeout_retry_count"]),
        default=int(DEFAULT_BROWSER_CONFIG["apple_event_timeout_retry_count"]),
        minimum=0,
    )
    config["apple_event_timeout_retry_delay_seconds"] = _coerce_browser_float(
        config.get(
            "apple_event_timeout_retry_delay_seconds",
            DEFAULT_BROWSER_CONFIG["apple_event_timeout_retry_delay_seconds"],
        ),
        default=float(DEFAULT_BROWSER_CONFIG["apple_event_timeout_retry_delay_seconds"]),
        minimum=0.0,
    )
    config["runner_heartbeat_seconds"] = _coerce_browser_float(
        config.get("runner_heartbeat_seconds", DEFAULT_BROWSER_CONFIG["runner_heartbeat_seconds"]),
        default=float(DEFAULT_BROWSER_CONFIG["runner_heartbeat_seconds"]),
        minimum=0.1,
    )
    config["extended_fetch_timeout_seconds"] = _coerce_browser_int(
        config.get("extended_fetch_timeout_seconds", DEFAULT_BROWSER_CONFIG["extended_fetch_timeout_seconds"]),
        default=int(DEFAULT_BROWSER_CONFIG["extended_fetch_timeout_seconds"]),
        minimum=0,
    )
    project_page_url = str(config.get("project_page_url", DEFAULT_BROWSER_CONFIG["project_page_url"])).strip()
    config["project_page_url"] = project_page_url

    if "chat_url" in config and "chat_url_prefix" not in config:
        config["chat_url_prefix"] = config["chat_url"]
    return config


def _coerce_browser_int(raw_value: Any, *, default: int, minimum: int) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    if value < minimum:
        return default
    return value


def _coerce_browser_float(raw_value: Any, *, default: float, minimum: float) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    if value < minimum:
        return default
    return value


def browser_fetch_timeout_seconds(config: Mapping[str, Any] | None = None) -> int:
    loaded = dict(config or load_browser_config())
    return int(
        loaded.get(
            "fetch_timeout_seconds",
            loaded.get("reply_timeout_seconds", DEFAULT_BROWSER_CONFIG["fetch_timeout_seconds"]),
        )
    )


def browser_runner_heartbeat_seconds(config: Mapping[str, Any] | None = None) -> float:
    loaded = dict(config or load_browser_config())
    return float(loaded.get("runner_heartbeat_seconds", DEFAULT_BROWSER_CONFIG["runner_heartbeat_seconds"]))


def browser_extended_fetch_timeout_seconds(config: Mapping[str, Any] | None = None) -> int:
    loaded = dict(config or load_browser_config())
    return int(loaded.get("extended_fetch_timeout_seconds", DEFAULT_BROWSER_CONFIG["extended_fetch_timeout_seconds"]))


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BridgeError(
            f"{repo_relative(path)} の JSON を読めませんでした。"
            f" {label} を正しい JSON オブジェクトへ修正してください: {exc.msg}"
        ) from exc

    if not isinstance(loaded, dict):
        raise BridgeError(
            f"{repo_relative(path)} は JSON オブジェクトである必要があります。"
            f" {label} のトップレベルを {{...}} 形式に修正してください。"
        )
    return loaded


def _require_project_config_text(
    config: dict[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
) -> None:
    value = config.get(key, DEFAULT_PROJECT_CONFIG[key])
    if not isinstance(value, str):
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `{key}` は文字列で指定してください。"
        )

    normalized = value.strip()
    if not allow_empty and not normalized:
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `{key}` が空です。"
            " 対象案件向けの値を入れてください。"
        )
    config[key] = normalized


def _add_project_config_warning(config: dict[str, Any], message: str) -> None:
    warnings = config.setdefault(PROJECT_CONFIG_WARNING_KEY, [])
    if isinstance(warnings, list):
        warnings.append(message)


def project_config_warnings(config: Mapping[str, Any]) -> list[str]:
    warnings = config.get(PROJECT_CONFIG_WARNING_KEY, [])
    if not isinstance(warnings, list):
        return []
    return [str(message) for message in warnings if str(message).strip()]


def print_project_config_warnings(config: Mapping[str, Any]) -> None:
    if os.environ.get("BRIDGE_SUPPRESS_PROJECT_WARNINGS") == "1":
        return
    for message in project_config_warnings(config):
        print(f"[warning] {message}")


def _resolve_project_path(
    raw_value: Any,
    *,
    key_name: str,
    relative_base: Path,
    empty_hint: str,
) -> Path:
    if not isinstance(raw_value, str):
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `{key_name}` は文字列で指定してください。"
        )

    normalized = raw_value.strip()
    if not normalized:
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `{key_name}` が空です。"
            f" {empty_hint}"
        )

    repo_path = Path(normalized).expanduser()
    if not repo_path.is_absolute():
        repo_path = (relative_base / repo_path).resolve()
    else:
        repo_path = repo_path.resolve()
    return repo_path


def _raw_bridge_runtime_root_value(config: Mapping[str, Any]) -> Any:
    if "bridge_runtime_root" in config:
        return config["bridge_runtime_root"]
    if "repo_path" in config:
        return config["repo_path"]
    return DEFAULT_PROJECT_CONFIG["bridge_runtime_root"]


def _validate_bridge_runtime_root(config: dict[str, Any]) -> None:
    runtime_root = _resolve_project_path(
        _raw_bridge_runtime_root_value(config),
        key_name="bridge_runtime_root",
        relative_base=ROOT_DIR,
        empty_hint="現在の bridge runtime を使うなら `.` を指定してください。",
    )

    if not runtime_root.exists():
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `bridge_runtime_root` が存在しません: {runtime_root}"
            " 現在の bridge runtime root を指定してください。"
        )
    if not runtime_root.is_dir():
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `bridge_runtime_root` はディレクトリである必要があります: {runtime_root}"
        )

    expected_bridge_dir = (runtime_root / "bridge").resolve()
    if expected_bridge_dir != BRIDGE_DIR.resolve():
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `bridge_runtime_root`={runtime_root} は現在の bridge 配置と一致しません。"
            f" 期待値: {ROOT_DIR}"
            " 現在の実装では bridge runtime はこの workspace に固定です。"
            " 同居運用なら `bridge_runtime_root` を `.` にし、別 repo を worker にしたい場合は `worker_repo_path` 側だけを変更してください。"
        )

    config["bridge_runtime_root"] = str(runtime_root)
    config["repo_path"] = str(runtime_root)


def _validate_worker_repo_marker_mode(config: dict[str, Any]) -> None:
    raw_value = config.get("worker_repo_marker_mode", DEFAULT_PROJECT_CONFIG["worker_repo_marker_mode"])
    if not isinstance(raw_value, str):
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `worker_repo_marker_mode` は文字列で指定してください。"
        )

    normalized = raw_value.strip().lower()
    if normalized not in WORKER_REPO_MARKER_MODES:
        allowed = ", ".join(sorted(WORKER_REPO_MARKER_MODES))
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `worker_repo_marker_mode` は {allowed} のいずれかで指定してください。"
        )
    config["worker_repo_marker_mode"] = normalized


def _validate_worker_repo_markers(config: dict[str, Any]) -> None:
    raw_value = config.get("worker_repo_markers", DEFAULT_PROJECT_CONFIG["worker_repo_markers"])
    if raw_value is None:
        config["worker_repo_markers"] = []
        return
    if not isinstance(raw_value, list):
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `worker_repo_markers` は配列で指定してください。"
        )

    normalized_markers: list[str] = []
    for index, marker in enumerate(raw_value):
        if not isinstance(marker, str):
            raise BridgeError(
                f"{repo_relative(PROJECT_CONFIG_PATH)} の `worker_repo_markers[{index}]` は文字列で指定してください。"
            )
        normalized = marker.strip()
        if not normalized:
            raise BridgeError(
                f"{repo_relative(PROJECT_CONFIG_PATH)} の `worker_repo_markers[{index}]` が空です。"
                " file / dir 名を 1 つずつ入れてください。"
            )
        if normalized not in normalized_markers:
            normalized_markers.append(normalized)

    config["worker_repo_markers"] = normalized_markers


def _configured_worker_repo_markers(config: Mapping[str, Any]) -> list[str]:
    configured = config.get("worker_repo_markers", [])
    if not isinstance(configured, list):
        return list(REPO_LIKE_MARKERS)

    merged_markers = list(REPO_LIKE_MARKERS)
    for marker in configured:
        normalized = str(marker).strip()
        if normalized and normalized not in merged_markers:
            merged_markers.append(normalized)
    return merged_markers


def _validate_worker_repo_path(config: dict[str, Any]) -> None:
    runtime_root = Path(str(config["bridge_runtime_root"])).resolve()
    raw_value = config.get("worker_repo_path", DEFAULT_PROJECT_CONFIG["worker_repo_path"])
    worker_path = _resolve_project_path(
        raw_value,
        key_name="worker_repo_path",
        relative_base=runtime_root,
        empty_hint="同居運用なら `.` を、別 repo を指定するならその path を入れてください。",
    )

    if not worker_path.exists():
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `worker_repo_path` が存在しません: {worker_path}"
            " Codex が作業する対象 repo root を指定してください。"
        )
    if not worker_path.is_dir():
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `worker_repo_path` はディレクトリである必要があります: {worker_path}"
        )

    if worker_path != runtime_root:
        visible_entries = [entry for entry in worker_path.iterdir() if entry.name != ".DS_Store"]
        if not visible_entries:
            raise BridgeError(
                f"{repo_relative(PROJECT_CONFIG_PATH)} の `worker_repo_path` が空ディレクトリを指しています: {worker_path}"
                " 実際に Codex が作業する対象 repo root を指定してください。"
            )

        configured_markers = _configured_worker_repo_markers(config)
        matched_markers = [marker for marker in configured_markers if (worker_path / marker).exists()]
        if not matched_markers:
            marker_text = ", ".join(configured_markers)
            message = (
                f"{repo_relative(PROJECT_CONFIG_PATH)} の `worker_repo_path` 直下に repo root らしい印が見つかりません: {worker_path}"
                f" 確認している印: {marker_text}"
                " 対象 repo root を指定しているか確認してください。"
            )
            if str(config.get("worker_repo_marker_mode", "strict")) == "warning":
                _add_project_config_warning(
                    config,
                    message + " marker が弱い正当な repo の場合だけ `worker_repo_marker_mode=warning` のまま続行してください。",
                )
            else:
                raise BridgeError(
                    message
                    + " marker が弱い正当な repo を扱う場合だけ `worker_repo_marker_mode=warning` を検討してください。"
                )

    config["worker_repo_path"] = str(worker_path)


def _validate_project_timeout(config: dict[str, Any]) -> None:
    raw_value = config.get("codex_timeout_seconds", DEFAULT_PROJECT_CONFIG["codex_timeout_seconds"])
    try:
        timeout = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `codex_timeout_seconds` は正の整数で指定してください。"
        ) from exc

    if timeout <= 0:
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `codex_timeout_seconds` は 1 以上で指定してください。"
        )
    config["codex_timeout_seconds"] = timeout


def load_project_config() -> dict[str, Any]:
    config = DEFAULT_PROJECT_CONFIG.copy()
    if PROJECT_CONFIG_PATH.exists():
        loaded = _load_json_object(PROJECT_CONFIG_PATH, label="project config")
        config.update(loaded)
    config[PROJECT_CONFIG_WARNING_KEY] = []

    _require_project_config_text(config, "project_name")
    _validate_bridge_runtime_root(config)
    _validate_worker_repo_marker_mode(config)
    _validate_worker_repo_markers(config)
    _validate_worker_repo_path(config)
    _require_project_config_text(config, "codex_bin")
    _require_project_config_text(config, "codex_model", allow_empty=True)
    _require_project_config_text(config, "codex_sandbox", allow_empty=True)
    _validate_project_timeout(config)
    _require_project_config_text(config, "report_request_next_todo")
    _require_project_config_text(config, "report_request_open_questions")

    return config


def bridge_runtime_root(config: Mapping[str, Any] | None = None) -> Path:
    loaded = dict(config or load_project_config())
    return Path(str(loaded.get("bridge_runtime_root", ROOT_DIR))).expanduser().resolve()


def runtime_bridge_dir(config: Mapping[str, Any] | None = None) -> Path:
    return bridge_runtime_root(config) / "bridge"


def runtime_inbox_dir(config: Mapping[str, Any] | None = None) -> Path:
    return runtime_bridge_dir(config) / "inbox"


def runtime_outbox_dir(config: Mapping[str, Any] | None = None) -> Path:
    return runtime_bridge_dir(config) / "outbox"


def runtime_history_dir(config: Mapping[str, Any] | None = None) -> Path:
    return runtime_bridge_dir(config) / "history"


def runtime_logs_dir(config: Mapping[str, Any] | None = None) -> Path:
    return bridge_runtime_root(config) / "logs"


def runtime_state_path(config: Mapping[str, Any] | None = None) -> Path:
    return runtime_bridge_dir(config) / "state.json"


def runtime_stop_path(config: Mapping[str, Any] | None = None) -> Path:
    return runtime_bridge_dir(config) / "STOP"


def runtime_prompt_path(config: Mapping[str, Any] | None = None) -> Path:
    return runtime_inbox_dir(config) / "codex_prompt.md"


def runtime_report_path(config: Mapping[str, Any] | None = None) -> Path:
    return runtime_outbox_dir(config) / "codex_report.md"


def worker_repo_path(config: Mapping[str, Any] | None = None) -> Path:
    loaded = dict(config or load_project_config())
    return Path(str(loaded.get("worker_repo_path", bridge_runtime_root(loaded)))).expanduser().resolve()


def project_repo_path(config: Mapping[str, Any] | None = None) -> Path:
    return worker_repo_path(config)


def state_snapshot(state: Mapping[str, Any]) -> str:
    fields = [
        f"- mode: {state.get('mode', '')}",
        f"- cycle: {state.get('cycle', 0)}",
        f"- need_chatgpt_prompt: {state.get('need_chatgpt_prompt', False)}",
        f"- need_codex_run: {state.get('need_codex_run', False)}",
        f"- need_chatgpt_next: {state.get('need_chatgpt_next', False)}",
        f"- last_prompt_file: {state.get('last_prompt_file', '')}",
        f"- last_report_file: {state.get('last_report_file', '')}",
        f"- pending_request_hash: {state.get('pending_request_hash', '')}",
        f"- pending_request_source: {state.get('pending_request_source', '')}",
        f"- pending_request_log: {state.get('pending_request_log', '')}",
        f"- pending_handoff_hash: {state.get('pending_handoff_hash', '')}",
        f"- pending_handoff_source: {state.get('pending_handoff_source', '')}",
        f"- pending_handoff_log: {state.get('pending_handoff_log', '')}",
        f"- last_processed_request_hash: {state.get('last_processed_request_hash', '')}",
        f"- last_processed_reply_hash: {state.get('last_processed_reply_hash', '')}",
        f"- current_chat_session: {state.get('current_chat_session', '')}",
        f"- human_review_auto_continue_count: {state.get('human_review_auto_continue_count', 0)}",
        f"- pause: {state.get('pause', False)}",
        f"- error: {state.get('error', False)}",
    ]
    if state.get("chatgpt_decision"):
        fields.append(f"- chatgpt_decision: {state['chatgpt_decision']}")
    if state.get("chatgpt_decision_note"):
        fields.append(f"- chatgpt_decision_note: {state['chatgpt_decision_note']}")
    if state.get("error_message"):
        fields.append(f"- error_message: {state['error_message']}")
    return "\n".join(fields)


def render_template(template_text: str, values: Mapping[str, str]) -> str:
    rendered = template_text
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def read_last_report_text(state: Mapping[str, Any]) -> str:
    ready_outbox_text = ready_codex_report_text(runtime_report_path())
    if ready_outbox_text:
        return ready_outbox_text

    last_report_file = str(state.get("last_report_file") or "").strip()
    if last_report_file:
        candidate = bridge_runtime_root() / last_report_file
        if candidate.exists():
            return read_text(candidate).strip()

    return "（前回の完了報告はまだありません）"


def normalize_codex_report_text(report_text: str) -> str:
    text = report_text.strip()
    if not text:
        return ""
    if text == OUTBOX_PLACEHOLDER_TEXT:
        return ""
    if text.startswith(OUTBOX_PLACEHOLDER_TEXT):
        return text[len(OUTBOX_PLACEHOLDER_TEXT) :].strip()
    if text.startswith(PLACEHOLDER_REPORT_HEADER):
        placeholder_lines = OUTBOX_PLACEHOLDER_TEXT.splitlines()
        text_lines = text.splitlines()
        if text_lines[: len(placeholder_lines)] == placeholder_lines:
            return "\n".join(text_lines[len(placeholder_lines) :]).strip()
    return text


def ready_codex_report_text(report_path: Path | None = None) -> str:
    candidate = report_path or runtime_report_path()
    return normalize_codex_report_text(read_text(candidate))


def codex_report_is_ready(report_path: Path | None = None) -> bool:
    return bool(ready_codex_report_text(report_path))


def _normalize_recovery_path(path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = (ROOT_DIR / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _candidate_report_paths_from_text(raw_text: str) -> list[Path]:
    candidates: list[Path] = []
    for match in re.findall(r"(/[^\s)\]]*codex_report\.md)", raw_text):
        candidate = _normalize_recovery_path(match.rstrip(".,"))
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _recent_codex_log_paths(limit: int = 12) -> list[Path]:
    patterns = [
        "*codex_last_message.txt",
        "*codex_launch_stdout.txt",
        "*codex_launch_stderr.txt",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(runtime_logs_dir().glob(pattern))
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _collapse_single_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clip_text(text: str, *, max_chars: int = 160) -> str:
    normalized = _collapse_single_line(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _tail_lines_text(path: Path, *, line_count: int = 4, max_chars: int = 240) -> str:
    text = read_text(path).strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return _clip_text(" / ".join(lines[-line_count:]), max_chars=max_chars)


def summarize_codex_progress_text(raw_text: str) -> tuple[str, str]:
    excerpt = _clip_text(raw_text, max_chars=160)
    if not excerpt:
        return "進捗待ち", ""

    lowered = excerpt.lower()
    if any(token in lowered for token in ("codex_runner_rules", "git_worker_rules", "prompt_compaction_rules", "rules file")):
        return "rules 読み込み中", excerpt
    if any(token in lowered for token in ("codex_prompt.md", "prompt file", "phase", "task", "requirements")):
        return "prompt 確認中", excerpt
    if any(token in lowered for token in ("pnpm test", "pytest", "vitest", "typecheck", "lint", "build", "test")):
        return "テスト中", excerpt
    if any(token in lowered for token in ("codex_report", "report", "outbox", "archive")):
        return "report 書き込み待ち", excerpt
    if any(token in lowered for token in ("apply_patch", "update file", "add file", "write file", "edit", "implement", "patch")):
        return "実装中", excerpt
    if any(token in lowered for token in ("git diff", "git status", "inspect", "read ", "open ", "review")):
        return "確認中", excerpt
    return "実装中", excerpt


def latest_codex_progress_snapshot(*, since: float | None = None) -> CodexProgressSnapshot | None:
    logs_dir = runtime_logs_dir()
    last_message_candidates = sorted(
        logs_dir.glob("*_codex_last_message.txt"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for last_message_path in last_message_candidates:
        if since is not None and last_message_path.stat().st_mtime < since:
            continue
        prefix = last_message_path.name[: -len("_codex_last_message.txt")]
        stdout_log_path = logs_dir / f"{prefix}_codex_launch_stdout.txt"
        stderr_log_path = logs_dir / f"{prefix}_codex_launch_stderr.txt"
        raw_text = read_text(last_message_path).strip()
        status, excerpt = summarize_codex_progress_text(raw_text)
        if not excerpt:
            stdout_tail = _tail_lines_text(stdout_log_path)
            stderr_tail = _tail_lines_text(stderr_log_path)
            fallback_excerpt = stdout_tail or stderr_tail
            if not fallback_excerpt:
                continue
            excerpt = fallback_excerpt
            if stderr_tail:
                status = "異常候補"
            elif stdout_tail:
                status = "実装中"
        progress_line = status if not excerpt else f"{status}: {excerpt}"
        return CodexProgressSnapshot(
            status=status,
            excerpt=excerpt,
            progress_line=progress_line,
            last_message_path=repo_relative(last_message_path),
            stdout_log_path=repo_relative(stdout_log_path),
            stderr_log_path=repo_relative(stderr_log_path),
            stdout_tail=_tail_lines_text(stdout_log_path),
            stderr_tail=_tail_lines_text(stderr_log_path),
        )
    return None


def recover_codex_report(
    report_path: Path | None = None,
    *,
    candidate_paths: Sequence[Path | str] | None = None,
    log_paths: Sequence[Path | str] | None = None,
    search_recent_logs: bool = False,
    newer_than: float | None = None,
) -> Path | None:
    target_path = _normalize_recovery_path(report_path or runtime_report_path())
    if codex_report_is_ready(target_path):
        return None

    ordered_candidates: list[Path] = []

    def add_candidate(path: Path | str) -> None:
        candidate = _normalize_recovery_path(path)
        if candidate == target_path or candidate in ordered_candidates:
            return
        ordered_candidates.append(candidate)

    for candidate in candidate_paths or []:
        add_candidate(candidate)

    for log_path in log_paths or []:
        log_candidate = _normalize_recovery_path(log_path)
        if not log_candidate.exists():
            continue
        if newer_than is not None and log_candidate.stat().st_mtime < newer_than:
            continue
        for candidate in _candidate_report_paths_from_text(read_text(log_candidate)):
            add_candidate(candidate)

    if search_recent_logs:
        for log_candidate in _recent_codex_log_paths():
            if newer_than is not None and log_candidate.stat().st_mtime < newer_than:
                continue
            for candidate in _candidate_report_paths_from_text(read_text(log_candidate)):
                add_candidate(candidate)

    for candidate in ordered_candidates:
        if newer_than is not None and candidate.exists() and candidate.stat().st_mtime < newer_than:
            continue
        report_text = ready_codex_report_text(candidate)
        if not report_text:
            continue
        write_text(target_path, report_text.rstrip() + "\n")
        return candidate

    return None


def recover_report_ready_state(
    state: Mapping[str, Any],
    *,
    prompt_path: Path | None = None,
    search_recent_logs: bool = True,
) -> tuple[dict[str, Any], Path | None]:
    current_state = dict(state)
    prompt_candidate = _normalize_recovery_path(prompt_path or runtime_prompt_path())
    prompt_mtime = prompt_candidate.stat().st_mtime if prompt_candidate.exists() else None
    recovered_report = recover_codex_report(
        runtime_report_path(),
        search_recent_logs=search_recent_logs,
        newer_than=prompt_mtime,
    )
    if not codex_report_is_ready(runtime_report_path()):
        return current_state, recovered_report

    mode = str(current_state.get("mode", "")).strip()
    should_promote = bool(current_state.get("error")) or mode in {"ready_for_codex", "codex_running", "codex_done"}
    if not should_promote:
        return current_state, recovered_report

    updated = clear_error_fields(dict(current_state))
    updated.update(
        {
            "mode": "codex_done",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
        }
    )
    save_state(updated)
    return updated, recovered_report


def recover_pending_handoff_state(state: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    current_state = dict(state)
    if not is_retryable_pending_handoff_error(current_state):
        return current_state, False
    if not read_pending_handoff_text(current_state):
        return current_state, False

    updated = clear_error_fields(dict(current_state))
    updated.update(
        {
            "mode": "idle",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": True,
            "need_codex_run": False,
        }
    )
    save_state(updated)
    return updated, True


def is_retryable_pending_handoff_error(state: Mapping[str, Any]) -> bool:
    if not bool(state.get("error")):
        return False
    if not bool(state.get("need_chatgpt_next")):
        return False
    error_message = str(state.get("error_message", "")).strip()
    retryable_markers = (
        "project ページ",
        "新チャット送信後",
        "新しいチャット",
        "handoff",
        "対象チャットを特定",
    )
    if error_message and not any(marker in error_message for marker in retryable_markers):
        return False
    return True


def _extract_marked_block(report_text: str, start_marker: str, end_marker: str) -> str | None:
    start_index = report_text.find(start_marker)
    if start_index == -1:
        return None
    end_index = report_text.find(end_marker, start_index + len(start_marker))
    if end_index == -1:
        return None
    return report_text[start_index : end_index + len(end_marker)].strip()


def compact_last_report_text(report_text: str) -> str:
    summary_block = _extract_marked_block(report_text, BRIDGE_SUMMARY_START, BRIDGE_SUMMARY_END)
    request_block = _extract_marked_block(report_text, CHATGPT_REQUEST_START, CHATGPT_REQUEST_END)
    blocks = [block for block in [summary_block, request_block] if block]
    if blocks:
        return "\n\n".join(blocks)
    return report_text.strip()


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


def normalize_no_codex_reason(raw_reason: str) -> str:
    normalized = raw_reason.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized.startswith("status:"):
        normalized = normalized.split(":", 1)[1].strip()
    aliases = {
        "completed": "completed",
        "complete": "completed",
        "done": "completed",
        "完了": "completed",
        "human_review": "human_review",
        "manual_review": "human_review",
        "review": "human_review",
        "人確認待ち": "human_review",
        "need_info": "need_info",
        "need_information": "need_info",
        "more_info": "need_info",
        "追加情報待ち": "need_info",
        "情報待ち": "need_info",
    }
    reason = aliases.get(normalized, normalized)
    if reason not in {"completed", "human_review", "need_info"}:
        raise BridgeError(
            "CHATGPT_NO_CODEX ブロックの先頭は completed / human_review / need_info のいずれかにしてください。"
        )
    return reason


def parse_no_codex_block(raw_body: str) -> tuple[str, str]:
    lines = [line.strip() for line in raw_body.strip().splitlines()]
    non_empty = [line for line in lines if line]
    if not non_empty:
        raise BridgeError("CHATGPT_NO_CODEX ブロックが空でした。")
    reason = normalize_no_codex_reason(non_empty[0])
    note = "\n".join(non_empty[1:]).strip()
    return reason, note


def _search_start_index(raw_text: str, after_text: str | None = None) -> int:
    search_start = 0
    if after_text:
        anchor = raw_text.rfind(after_text)
        if anchor != -1:
            search_start = anchor + len(after_text)
    if search_start == 0:
        last_user_turn = raw_text.rfind("あなた:")
        if last_user_turn != -1:
            search_start = last_user_turn
    return search_start


def extract_last_chatgpt_handoff(raw_text: str, *, after_text: str | None = None) -> str:
    search_start = _search_start_index(raw_text, after_text)
    pattern = re.compile(
        rf"{re.escape(HANDOFF_REPLY_START)}(.*?){re.escape(HANDOFF_REPLY_END)}",
        re.DOTALL,
    )
    matches: list[tuple[int, str]] = []
    assistant_matches: list[tuple[int, str]] = []
    for match in pattern.finditer(raw_text, search_start):
        body = match.group(1).strip()
        entry = (match.start(), body)
        matches.append(entry)
        assistant_index = raw_text.rfind("ChatGPT:", search_start, match.start())
        user_index = raw_text.rfind("あなた:", search_start, match.start())
        if assistant_index > user_index:
            assistant_matches.append(entry)

    selected = assistant_matches if search_start > 0 else (assistant_matches or matches)
    if not selected:
        raise BridgeError("直近の request 以降に有効な CHATGPT_CHAT_HANDOFF ブロックを抽出できませんでした。")

    handoff = sorted(selected, key=lambda item: item[0])[-1][1].strip()
    if not handoff:
        raise BridgeError("CHATGPT_CHAT_HANDOFF ブロックが空でした。")
    return handoff + "\n"


def extract_last_chatgpt_reply(raw_text: str, *, after_text: str | None = None) -> ChatGPTReplyDecision:
    search_start = _search_start_index(raw_text, after_text)

    candidate_specs = [
        ("codex_prompt", PROMPT_REPLY_START, PROMPT_REPLY_END),
        ("no_codex", NO_CODEX_REPLY_START, NO_CODEX_REPLY_END),
    ]
    assistant_matches: list[tuple[int, str, str]] = []
    fallback_matches: list[tuple[int, str, str]] = []

    for kind, start_marker, end_marker in candidate_specs:
        pattern = re.compile(
            rf"{re.escape(start_marker)}(.*?){re.escape(end_marker)}",
            re.DOTALL,
        )
        for match in pattern.finditer(raw_text, search_start):
            entry = (match.start(), kind, match.group(1))
            fallback_matches.append(entry)
            assistant_index = raw_text.rfind("ChatGPT:", search_start, match.start())
            user_index = raw_text.rfind("あなた:", search_start, match.start())
            if assistant_index > user_index:
                assistant_matches.append(entry)

    matches = assistant_matches if search_start > 0 else (assistant_matches or fallback_matches)
    if not matches:
        if after_text:
            raise BridgeError("直近の prompt request 以降に有効な ChatGPT 返答ブロックを抽出できませんでした。")
        raise BridgeError("直近のユーザー発話以降に有効な ChatGPT 返答ブロックを抽出できませんでした。")

    _, kind, raw_body = sorted(matches, key=lambda item: item[0])[-1]
    if kind == "codex_prompt":
        body = normalize_prompt_body(raw_body)
        return ChatGPTReplyDecision("codex_prompt", body, "", body)

    reason, note = parse_no_codex_block(raw_body)
    return ChatGPTReplyDecision(reason, "", note, raw_body.strip() + "\n")


def extract_last_prompt_reply(raw_text: str, *, after_text: str | None = None) -> str:
    decision = extract_last_chatgpt_reply(raw_text, after_text=after_text)
    if decision.kind != "codex_prompt":
        raise BridgeError("CHATGPT_PROMPT_REPLY ではなく CHATGPT_NO_CODEX が返りました。")
    return decision.body


def _apple_event_timeout_message(target: str) -> str:
    return (
        f"{target} が AppleEvent timeout で止まりました。"
        " Safari の現在タブが応答していないか、対象チャットが開かれていないか、"
        " `Allow JavaScript from Apple Events` が無効か、macOS の Automation 許可が未確定の可能性があります。"
        " 初回は許可ダイアログで許可し、Safari の対象チャットを前面表示したまま再実行してください。"
    )


def is_apple_event_timeout_text(message: str) -> bool:
    normalized = str(message)
    return any(marker in normalized for marker in APPLE_EVENT_TIMEOUT_MARKERS)


def safari_timeout_checklist_text() -> str:
    return (
        "Safari の current tab、対象チャット表示、`Allow JavaScript from Apple Events`、"
        "macOS Automation を確認してください。"
    )


def _run_osascript(
    lines: list[str],
    *,
    timeout_seconds: int = APPLE_EVENT_TIMEOUT_SECONDS,
    timeout_label: str = "AppleScript 実行",
) -> subprocess.CompletedProcess[str]:
    command = ["osascript"]
    for line in lines:
        command.extend(["-e", line])
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(_apple_event_timeout_message(timeout_label)) from exc


def _run_osascript_script(
    script_text: str,
    args: Sequence[str] | None = None,
    *,
    timeout_seconds: int = APPLE_EVENT_TIMEOUT_SECONDS,
    timeout_label: str = "AppleScript 実行",
) -> subprocess.CompletedProcess[str]:
    command = ["osascript", "-"]
    if args:
        command.extend(args)
    try:
        return subprocess.run(
            command,
            input=script_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(_apple_event_timeout_message(timeout_label)) from exc


def _chat_domain_matches(url: str, target_prefix: str) -> bool:
    return "chatgpt.com" in url or url.startswith(target_prefix) or "chat.openai.com" in url


def _conversation_url_matches(url: str, config: Mapping[str, Any]) -> bool:
    keywords = list(config.get("conversation_url_keywords", []))
    if not keywords:
        return True
    return any(keyword in url for keyword in keywords)


def _normalized_url(url: str) -> str:
    return url.rstrip("/")


def _same_tab(expected: Mapping[str, str], current: Mapping[str, str]) -> bool:
    expected_url = _normalized_url(str(expected.get("url", "")))
    current_url = _normalized_url(str(current.get("url", "")))
    if expected_url and current_url:
        return expected_url == current_url
    return str(expected.get("title", "")) == str(current.get("title", ""))


def _safari_js_error_message(stderr: str) -> str:
    message = stderr.strip()
    if "-1712" in message or "AppleEventがタイムアウト" in message or "AppleEvent timed out" in message:
        return _apple_event_timeout_message("Safari JavaScript 実行")
    if "JavaScript from Apple Events" in message or ("JavaScript" in message and "Apple Events" in message):
        return (
            "Safari で Apple Events からの JavaScript 実行が許可されていません。"
            " Safari の Develop メニューで 'Allow JavaScript from Apple Events' を有効にしてください。"
        )
    return f"Safari の JavaScript 実行に失敗しました: {message}"


def _run_safari_javascript(script: str) -> str:
    applescript = """
on run argv
    if (count of argv) is 0 then error "missing javascript"
    set jsCode to item 1 of argv
    tell application "Safari"
        if not running then error "safari not running"
        if (count of windows) is 0 then error "no safari window"
        return do JavaScript jsCode in current tab of front window
    end tell
end run
"""
    result = _run_osascript_script(
        applescript,
        [script],
        timeout_label="Safari JavaScript 実行",
    )
    if result.returncode != 0:
        raise BridgeError(_safari_js_error_message(result.stderr))
    return result.stdout.rstrip("\n")


def frontmost_safari_tab_info(
    config: Mapping[str, Any],
    *,
    require_conversation: bool = True,
) -> dict[str, str]:
    app_name = str(config.get("app_name", DEFAULT_BROWSER_CONFIG["app_name"]))
    result = _run_osascript(
        [
            f'tell application "{app_name}"',
            'if not running then error "browser not running"',
            'if (count of windows) is 0 then error "no browser window"',
            "set activeUrl to URL of current tab of front window",
            "set activeName to name of current tab of front window",
            'return activeUrl & linefeed & activeName',
            "end tell",
        ],
        timeout_label="Safari の現在タブ情報取得",
    )
    if result.returncode != 0:
        message = result.stderr.strip()
        if "browser not running" in message:
            raise BridgeError("Safari が起動していません。対象チャットを開いてください。")
        if "no browser window" in message:
            raise BridgeError("Safari のウィンドウが見つかりませんでした。対象チャットを開いてください。")
        if "-1712" in message or "AppleEventがタイムアウト" in message or "AppleEvent timed out" in message:
            raise BridgeError(_apple_event_timeout_message("Safari の現在タブ情報取得"))
        raise BridgeError(f"Safari の現在タブ情報を取得できませんでした: {message}")

    parts = result.stdout.splitlines()
    url = parts[0].strip() if parts else ""
    title = parts[1].strip() if len(parts) > 1 else ""
    if not url:
        raise BridgeError("Safari の現在タブ URL を取得できませんでした。")

    target_prefix = str(config.get("chat_url_prefix", DEFAULT_BROWSER_CONFIG["chat_url_prefix"]))
    if not _chat_domain_matches(url, target_prefix):
        raise BridgeError(f"Safari の現在タブが ChatGPT ではありません: {title or '(no title)'} {url}")
    if require_conversation and not _conversation_url_matches(url, config):
        raise BridgeError(
            "Safari の現在タブが ChatGPT の対象会話ではありません。"
            f" 対象チャットを開いてください: {title or '(no title)'} {url}"
        )

    return {
        "url": url,
        "title": title,
    }


def _build_visible_text_script(selectors: Sequence[str]) -> str:
    return f"""
(() => {{
  const selectors = {json.dumps(list(selectors), ensure_ascii=False)};
  const isVisible = (el) => {{
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    const rect = el.getBoundingClientRect();
    return style.display !== "none" &&
      style.visibility !== "hidden" &&
      rect.width > 0 &&
      rect.height > 0;
  }};
  for (const selector of selectors) {{
    const nodes = Array.from(document.querySelectorAll(selector));
    for (const node of nodes) {{
      if (!isVisible(node)) continue;
      const text = (node.innerText || node.textContent || "").trim();
      if (text) return text;
    }}
  }}
  return "";
}})();
"""


def _build_composer_state_script(preferred_hint: str | None = None) -> str:
    return f"""
(() => {{
  const selectors = {json.dumps(COMPOSER_SELECTORS, ensure_ascii=False)};
  const preferredHint = {json.dumps((preferred_hint or "").strip(), ensure_ascii=False)};
  const isVisible = (el) => {{
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    const rect = el.getBoundingClientRect();
    return style.display !== "none" &&
      style.visibility !== "hidden" &&
      rect.width > 0 &&
      rect.height > 0;
  }};
  const matchesHint = (node) => {{
    if (!preferredHint) return false;
    const values = [
      node.getAttribute?.("placeholder") || "",
      node.getAttribute?.("aria-label") || "",
      node.getAttribute?.("data-placeholder") || "",
      node.closest?.("form, section, main, div")?.innerText || "",
    ];
    return values.some((value) => value && value.includes(preferredHint));
  }};
  let composer = null;
  let fallbackComposer = null;
  for (const selector of selectors) {{
    const nodes = Array.from(document.querySelectorAll(selector));
    for (let index = nodes.length - 1; index >= 0; index -= 1) {{
      const node = nodes[index];
      if (isVisible(node)) {{
        if (!fallbackComposer) fallbackComposer = node;
        if (matchesHint(node)) {{
          composer = node;
          break;
        }}
      }}
    }}
    if (composer) break;
  }}
  if (!composer) composer = fallbackComposer;
  if (!composer) return JSON.stringify({{found: false}});
  return JSON.stringify({{
    found: true,
    tagName: (composer.tagName || "").toLowerCase(),
    isContentEditable: !!composer.isContentEditable
  }});
}})();
"""


def _build_fill_composer_script(text: str, preferred_hint: str | None = None) -> str:
    return f"""
(() => {{
  const selectors = {json.dumps(COMPOSER_SELECTORS, ensure_ascii=False)};
  const text = {json.dumps(text, ensure_ascii=False)};
  const preferredHint = {json.dumps((preferred_hint or "").strip(), ensure_ascii=False)};
  const isVisible = (el) => {{
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    const rect = el.getBoundingClientRect();
    return style.display !== "none" &&
      style.visibility !== "hidden" &&
      rect.width > 0 &&
      rect.height > 0;
  }};
  const matchesHint = (node) => {{
    if (!preferredHint) return false;
    const values = [
      node.getAttribute?.("placeholder") || "",
      node.getAttribute?.("aria-label") || "",
      node.getAttribute?.("data-placeholder") || "",
      node.closest?.("form, section, main, div")?.innerText || "",
    ];
    return values.some((value) => value && value.includes(preferredHint));
  }};
  let composer = null;
  let fallbackComposer = null;
  for (const selector of selectors) {{
    const nodes = Array.from(document.querySelectorAll(selector));
    for (let index = nodes.length - 1; index >= 0; index -= 1) {{
      const node = nodes[index];
      if (isVisible(node)) {{
        if (!fallbackComposer) fallbackComposer = node;
        if (matchesHint(node)) {{
          composer = node;
          break;
        }}
      }}
    }}
    if (composer) break;
  }}
  if (!composer) composer = fallbackComposer;
  if (!composer) return JSON.stringify({{ok: false, reason: "composer_missing"}});
  composer.scrollIntoView({{block: "center"}});
  composer.focus();
  const tagName = (composer.tagName || "").toLowerCase();
  if (tagName === "textarea") {{
    const descriptor = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value");
    if (descriptor && descriptor.set) {{
      descriptor.set.call(composer, text);
    }} else {{
      composer.value = text;
    }}
    composer.dispatchEvent(new Event("input", {{bubbles: true}}));
    composer.dispatchEvent(new Event("change", {{bubbles: true}}));
    return JSON.stringify({{ok: true, mode: "textarea"}});
  }}
  if (composer.isContentEditable) {{
    composer.innerHTML = "";
    const lines = text.split("\\n");
    lines.forEach((line, index) => {{
      if (index > 0) composer.appendChild(document.createElement("br"));
      composer.appendChild(document.createTextNode(line));
    }});
    try {{
      composer.dispatchEvent(new InputEvent("input", {{
        bubbles: true,
        cancelable: true,
        data: text,
        inputType: "insertText"
      }}));
    }} catch (error) {{
      composer.dispatchEvent(new Event("input", {{bubbles: true}}));
    }}
    composer.dispatchEvent(new Event("change", {{bubbles: true}}));
    return JSON.stringify({{ok: true, mode: "contenteditable"}});
  }}
  return JSON.stringify({{ok: false, reason: "composer_unsupported"}});
}})();
"""


def _build_submit_script() -> str:
    return f"""
(() => {{
  const selectors = {json.dumps(SEND_BUTTON_SELECTORS, ensure_ascii=False)};
  const isVisible = (el) => {{
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    const rect = el.getBoundingClientRect();
    return style.display !== "none" &&
      style.visibility !== "hidden" &&
      rect.width > 0 &&
      rect.height > 0;
  }};
  for (const selector of selectors) {{
    const nodes = Array.from(document.querySelectorAll(selector));
    for (let index = nodes.length - 1; index >= 0; index -= 1) {{
      const button = nodes[index];
      if (!isVisible(button)) continue;
      if (button.disabled || button.getAttribute("aria-disabled") === "true") {{
        return JSON.stringify({{ok: false, reason: "send_disabled"}});
      }}
      button.click();
      return JSON.stringify({{ok: true, method: "button"}});
    }}
  }}
  return JSON.stringify({{ok: false, reason: "send_missing"}});
}})();
"""


def _evaluate_json(page: SafariChatPage, script: str, failure_label: str) -> dict[str, Any]:
    raw = page.evaluate(script).strip()
    if not raw:
        raise BridgeError(f"{failure_label}: Safari から空の応答が返りました。")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"{failure_label}: JSON として読めませんでした。") from exc


def _body_text(page: SafariChatPage) -> str:
    return page.evaluate(_build_visible_text_script(["body"])).strip()


def _body_text_unchecked() -> str:
    try:
        return _run_safari_javascript(_build_visible_text_script(["body"])).strip()
    except Exception:
        return ""


def log_page_dump(page: SafariChatPage, prefix: str = "raw_chatgpt_prompt_dump") -> Path | None:
    text = _body_text(page)
    if not text:
        return None
    return log_text(prefix, text, suffix="txt")


def _page_hint_matches(page: SafariChatPage, front_tab: Mapping[str, str], config: Mapping[str, Any]) -> bool:
    hint = str(config.get("chat_hint", "")).strip()
    if not hint:
        return not bool(config.get("require_chat_hint"))

    hint_lower = hint.lower()
    title = str(front_tab.get("title", "")).lower()
    url = str(front_tab.get("url", "")).lower()
    if hint_lower in title or hint_lower in url:
        return True
    return hint_lower in _body_text(page).lower()


def _ensure_target_chat(page: SafariChatPage, front_tab: Mapping[str, str], config: Mapping[str, Any]) -> None:
    if _page_hint_matches(page, front_tab, config):
        return

    dump_path = log_page_dump(page)
    dump_note = f" raw dump: {repo_relative(dump_path)}" if dump_path else ""
    hint = str(config.get("chat_hint", "")).strip()
    if hint:
        raise BridgeError(
            f"Safari の現在 ChatGPT タブから chat_hint='{hint}' を確認できませんでした。"
            f" 対象チャットが違う可能性があるため停止しました: {front_tab.get('title', '')} {front_tab.get('url', '')}"
            f"{dump_note}"
        )
    raise BridgeError(
        "Safari の現在 ChatGPT タブを識別できませんでした。"
        f" browser_config.json の chat_hint を設定して対象チャットを絞り込んでください。{dump_note}"
    )


def _find_composer_state(page: SafariChatPage, *, preferred_hint: str | None = None) -> dict[str, Any]:
    return _evaluate_json(
        page,
        _build_composer_state_script(preferred_hint=preferred_hint),
        "ChatGPT 入力欄の確認に失敗しました",
    )


def ensure_chatgpt_ready(
    page: SafariChatPage,
    config: Mapping[str, Any],
    *,
    allow_manual_login: bool = False,
    preferred_hint: str | None = None,
) -> dict[str, Any]:
    deadline = time.time() + 20
    state = _find_composer_state(page, preferred_hint=preferred_hint)
    while not state.get("found") and time.time() < deadline:
        page.wait_for_timeout(1000)
        state = _find_composer_state(page, preferred_hint=preferred_hint)

    if state.get("found"):
        return state

    body_text = _body_text(page)
    dump_path = log_page_dump(page) if body_text else None
    dump_note = f" raw dump: {repo_relative(dump_path)}" if dump_path else ""
    login_hint = "Log in" in body_text or "ログイン" in body_text or "Sign up" in body_text
    if login_hint or allow_manual_login:
        raise BridgeError(
            "ChatGPT の入力欄が見つかりませんでした。"
            " 事前準備として Safari でログイン済みの対象チャットを現在タブに表示してください。"
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
def open_chatgpt_page(
    *,
    reset_chat: bool = False,
    require_conversation: bool = True,
    require_target_chat: bool = True,
) -> Iterator[tuple[None, SafariChatPage, dict[str, Any], dict[str, str]]]:
    del reset_chat
    config = load_browser_config()
    front_tab = frontmost_safari_tab_info(config, require_conversation=require_conversation)
    page = SafariChatPage(config=config, front_tab=front_tab)
    if require_target_chat:
        _ensure_target_chat(page, front_tab, config)
    yield None, page, config, front_tab


def fill_chatgpt_composer(
    page: SafariChatPage,
    text: str,
    config: Mapping[str, Any],
    *,
    allow_manual_login: bool = False,
    preferred_hint: str | None = None,
) -> None:
    ensure_chatgpt_ready(page, config, allow_manual_login=allow_manual_login, preferred_hint=preferred_hint)
    result = _evaluate_json(
        page,
        _build_fill_composer_script(text, preferred_hint=preferred_hint),
        "ChatGPT 入力欄への書き込みに失敗しました",
    )
    if result.get("ok"):
        page.wait_for_timeout(300)
        return
    raise BridgeError(f"ChatGPT の入力欄に文字を設定できませんでした: {result.get('reason', 'unknown')}")


def submit_chatgpt_message(page: SafariChatPage) -> None:
    deadline = time.time() + 5
    last_reason = "send_missing"
    while time.time() < deadline:
        result = _evaluate_json(page, _build_submit_script(), "送信ボタン操作に失敗しました")
        if result.get("ok"):
            return
        last_reason = str(result.get("reason", last_reason))
        page.wait_for_timeout(500)

    dump_path = log_page_dump(page)
    dump_note = f" raw dump: {repo_relative(dump_path)}" if dump_path else ""
    raise BridgeError(f"Safari 上の送信ボタンを押せませんでした: {last_reason}{dump_note}")


def send_to_chatgpt(text: str) -> None:
    with open_chatgpt_page(reset_chat=False) as (_, page, config, _):
        fill_chatgpt_composer(page, text, config)
        submit_chatgpt_message(page)


def send_to_chatgpt_in_current_surface(
    text: str,
    *,
    require_conversation: bool = False,
    require_target_chat: bool = False,
    preferred_hint: str | None = None,
) -> dict[str, str]:
    with open_chatgpt_page(
        reset_chat=False,
        require_conversation=require_conversation,
        require_target_chat=require_target_chat,
    ) as (_, page, config, _):
        fill_chatgpt_composer(page, text, config, preferred_hint=preferred_hint)
        submit_chatgpt_message(page)
        page.wait_for_timeout(500)
        return frontmost_safari_tab_info(config, require_conversation=require_conversation)


def draft_message_in_chatgpt(text: str) -> None:
    with open_chatgpt_page(reset_chat=False) as (_, page, config, _):
        fill_chatgpt_composer(page, text, config, allow_manual_login=True)


def derive_chatgpt_project_page_url(conversation_url: str, config: Mapping[str, Any]) -> str:
    explicit = str(config.get("project_page_url", "")).strip()
    if explicit:
        return explicit
    if "/c/" not in conversation_url:
        raise BridgeError(
            "現在の ChatGPT URL から project ページを導出できませんでした。"
            " browser_config.json の project_page_url を設定してください。"
        )
    return conversation_url.split("/c/", 1)[0].rstrip("/")


def navigate_current_chatgpt_tab(url: str, *, timeout_seconds: int = 30) -> dict[str, str]:
    applescript = """
on run argv
    if (count of argv) is 0 then error "missing url"
    set targetUrl to item 1 of argv
    tell application "Safari"
        if not running then error "browser not running"
        if (count of windows) is 0 then error "no browser window"
        set URL of current tab of front window to targetUrl
    end tell
    return targetUrl
end run
"""
    result = _run_osascript_script(
        applescript,
        [url],
        timeout_label="Safari の project ページ遷移",
    )
    if result.returncode != 0:
        raise BridgeError(f"Safari の project ページ遷移に失敗しました: {result.stderr.strip()}")

    config = load_browser_config()
    deadline = time.time() + timeout_seconds
    last_info: dict[str, str] | None = None
    while time.time() < deadline:
        info = frontmost_safari_tab_info(config, require_conversation=False)
        last_info = info
        if _normalized_url(info.get("url", "")) == _normalized_url(url):
            return info
        time.sleep(0.5)
    raise BridgeError(
        "project ページへ遷移できませんでした。"
        f" 期待: {url} 実際: {last_info.get('url', '') if last_info else ''}"
    )


def rotate_chat_with_handoff(handoff_text: str) -> dict[str, str]:
    config = load_browser_config()
    current_tab = frontmost_safari_tab_info(config)
    project_page_url = derive_chatgpt_project_page_url(current_tab.get("url", ""), config)
    last_error = ""
    last_info: dict[str, str] | None = None
    for attempt in range(1, 3):
        navigate_current_chatgpt_tab(project_page_url)
        try:
            sent = False
            for hint in PROJECT_CHAT_COMPOSER_HINTS + ("",):
                try:
                    send_to_chatgpt_in_current_surface(
                        handoff_text,
                        require_conversation=False,
                        require_target_chat=False,
                        preferred_hint=hint or None,
                    )
                    sent = True
                    break
                except BridgeError as exc:
                    last_error = str(exc)
            if not sent:
                if attempt < 2:
                    time.sleep(1.0)
                    continue
                raise BridgeError(
                    "project ページの『このプロジェクト内の新しいチャット』入力欄へ handoff を送れませんでした。"
                    f" {last_error}"
                )
        except BridgeError as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(1.0)
                continue
            raise

        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                info = frontmost_safari_tab_info(config, require_conversation=True)
            except BridgeError as exc:
                last_error = str(exc)
                time.sleep(0.5)
                continue
            last_info = info
            if _conversation_url_matches(info.get("url", ""), config):
                return info
            time.sleep(0.5)

        if attempt < 2:
            time.sleep(1.0)

    dump_note = ""
    dump_text = _body_text_unchecked()
    if dump_text:
        dump_path = log_text("chat_rotation_failure_dump", dump_text, suffix="txt")
        dump_note = f" raw dump: {repo_relative(dump_path)}"
    raise BridgeError(
        "新チャット送信後に対象チャットを特定できませんでした。"
        f" 現在: {last_info.get('url', '') if last_info else project_page_url}"
        f" {last_error}".rstrip()
        + dump_note
    )


def read_chatgpt_conversation_dom(page: SafariChatPage) -> str:
    text = page.evaluate(_build_visible_text_script(["main", "article", "body"])).strip()
    if text:
        return text
    raise BridgeError("Safari 上の ChatGPT 会話テキストを取得できませんでした。会話領域が見えているか確認してください。")


def read_chatgpt_conversation() -> str:
    with open_chatgpt_page(reset_chat=False) as (_, page, _, _):
        return read_chatgpt_conversation_dom(page)


def _wait_for_chatgpt_reply_text(
    *,
    timeout_seconds: int | None = None,
    request_text: str | None = None,
    extractor: Callable[[str, str | None], Any],
    stage_callback: Callable[[ChatGPTWaitEvent], None] | None = None,
) -> str:
    with open_chatgpt_page(reset_chat=False) as (_, page, config, front_tab):
        timeout = int(timeout_seconds or browser_fetch_timeout_seconds(config))
        extended_timeout = int(browser_extended_fetch_timeout_seconds(config))
        poll_seconds = float(config.get("poll_interval_seconds", 2))
        retry_count = int(config.get("apple_event_timeout_retry_count", DEFAULT_BROWSER_CONFIG["apple_event_timeout_retry_count"]))
        retry_delay_seconds = float(
            config.get(
                "apple_event_timeout_retry_delay_seconds",
                DEFAULT_BROWSER_CONFIG["apple_event_timeout_retry_delay_seconds"],
            )
        )
        request_text = request_text if request_text is not None else read_latest_prompt_request_text()
        first_deadline = time.time() + timeout
        extended_deadline = first_deadline + extended_timeout
        latest_text = ""
        timeout_attempts = 0
        stage = "initial"
        while True:
            try:
                latest_text = read_chatgpt_conversation_dom(page)
            except BridgeError as exc:
                if is_apple_event_timeout_text(str(exc)):
                    timeout_attempts += 1
                    total_attempts = retry_count + 1
                    if timeout_attempts <= retry_count:
                        print(
                            f"[retry] fetch_next_prompt で Safari timeout を検知しました。"
                            f" retry {timeout_attempts}/{retry_count} を {retry_delay_seconds:.1f}s 後に行います。",
                            flush=True,
                        )
                        if retry_delay_seconds > 0:
                            page.wait_for_timeout(int(retry_delay_seconds * 1000))
                        continue
                    raise BridgeError(
                        f"{exc} fetch_next_prompt では Safari timeout を {timeout_attempts}/{total_attempts} 回確認しました。"
                        f" {safari_timeout_checklist_text()}"
                    ) from exc
                raise
            try:
                extractor(latest_text, request_text or None)
                if timeout_attempts > 0:
                    print(
                        f"[retry] fetch_next_prompt は Safari timeout 後の再試行で回復しました。"
                        f" timeout={timeout_attempts} 回",
                        flush=True,
                    )
                return latest_text
            except BridgeError:
                pass

            now = time.time()
            if stage == "initial" and now >= first_deadline:
                stage = "extended"
                if stage_callback is not None:
                    stage_callback(ChatGPTWaitEvent("timeout_first", latest_text))
            elif stage == "extended" and now >= extended_deadline:
                stage = "late_completion"
                if stage_callback is not None:
                    stage_callback(ChatGPTWaitEvent("timeout_extended", latest_text))
                    stage_callback(ChatGPTWaitEvent("late_completion_mode", latest_text))

            page.wait_for_timeout(int(poll_seconds * 1000))


def wait_for_prompt_reply_text(
    timeout_seconds: int | None = None,
    request_text: str | None = None,
    stage_callback: Callable[[ChatGPTWaitEvent], None] | None = None,
) -> str:
    return _wait_for_chatgpt_reply_text(
        timeout_seconds=timeout_seconds,
        request_text=request_text,
        extractor=lambda raw_text, after_text: extract_last_chatgpt_reply(raw_text, after_text=after_text),
        stage_callback=stage_callback,
    )


def wait_for_handoff_reply_text(
    *,
    timeout_seconds: int | None = None,
    request_text: str | None = None,
    stage_callback: Callable[[ChatGPTWaitEvent], None] | None = None,
) -> str:
    return _wait_for_chatgpt_reply_text(
        timeout_seconds=timeout_seconds,
        request_text=request_text,
        extractor=lambda raw_text, after_text: extract_last_chatgpt_handoff(raw_text, after_text=after_text),
        stage_callback=stage_callback,
    )


def build_chatgpt_handoff_request(
    *,
    state: Mapping[str, Any],
    last_report: str,
    next_todo: str,
    open_questions: str,
    current_status: str | None = None,
) -> str:
    summary = compact_last_report_text(last_report)
    contract = build_chatgpt_reply_contract_section()
    status_text = current_status or state_snapshot(state)
    return (
        "次チャット用の引き継ぎを書いてください。\n"
        "この文章はそのまま新しいチャットの最初のメッセージとして使います。\n\n"
        "要件:\n"
        "- 冗長な説明をしない\n"
        "- 現在のプロジェクト前提を入れる\n"
        "- 現在の進捗を短く入れる\n"
        "- 直前の完了内容を短く入れる\n"
        "- 次の Codex 用 prompt request を入れる\n"
        "- bridge reply contract を含める\n\n"
        "返答は前置きなしで次のブロックだけにしてください。\n\n"
        f"{HANDOFF_REPLY_START}\n"
        "[新しいチャットの最初のメッセージ本文]\n"
        f"{HANDOFF_REPLY_END}\n\n"
        "## current_status\n"
        f"{status_text}\n\n"
        "## last_report\n"
        f"{summary}\n\n"
        "## next_request\n"
        f"- next_todo: {next_todo}\n"
        f"- open_questions: {open_questions}\n\n"
        "## bridge_reply_contract\n"
        f"{contract}\n"
    ).strip() + "\n"


def build_human_review_auto_continue_request() -> str:
    contract = build_chatgpt_reply_contract_section()
    return (
        "レビュー要求のみでは停止しない運用です。\n"
        "human_review では止めず継続してください。\n"
        "次の Codex 用 1 フェーズ prompt を reply contract に従って返してください。\n\n"
        f"{contract}\n"
    )


def build_chatgpt_request(
    *,
    state: Mapping[str, Any],
    template_path: Path,
    next_todo: str,
    open_questions: str,
    current_status: str | None = None,
    last_report: str | None = None,
    resume_note: str | None = None,
) -> str:
    template_text = read_text(template_path).strip()
    if not template_text:
        raise BridgeError(f"テンプレートを読めませんでした: {repo_relative(template_path)}")

    resume_text = (resume_note or "").strip()
    resume_section = ""
    if resume_text:
        decision = str(state.get("chatgpt_decision", "")).strip() or "resume"
        decision_note = str(state.get("chatgpt_decision_note", "")).strip()
        lines = [
            "## handoff",
            "",
            f"- reason: {decision}",
        ]
        if decision_note:
            lines.append(f"- previous_note: {decision_note}")
        lines.extend(
            [
                "- user_input:",
                resume_text,
            ]
        )
        resume_section = "\n".join(lines).strip() + "\n"

    values = {
        "CURRENT_STATUS": current_status or state_snapshot(state),
        "LAST_REPORT": compact_last_report_text(last_report or read_last_report_text(state)),
        "NEXT_TODO": next_todo,
        "OPEN_QUESTIONS": open_questions,
        "RESUME_CONTEXT_SECTION": resume_section,
    }
    return render_template(template_text, values).strip() + "\n"
