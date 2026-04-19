#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from issue_centric_contract import build_issue_centric_reply_contract_section as _build_ic_reply_contract_section
from issue_centric_normalized_summary import (
    IssueCentricNextRequestContext,
    IssueCentricRecoveryContext,
    IssueCentricRouteSelection,
    IssueCentricRuntimeMode,
    IssueCentricRuntimeSnapshot,
    load_issue_centric_normalized_summary,
    resolve_issue_centric_state_bridge,
    resolve_issue_centric_runtime_snapshot,
    resolve_issue_centric_runtime_mode,
    render_issue_centric_next_request_section,
    render_issue_centric_summary_for_request,
)

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
    "prepared_request_hash": "",
    "prepared_request_source": "",
    "prepared_request_log": "",
    "prepared_request_status": "",
    "pending_request_hash": "",
    "pending_request_source": "",
    "pending_request_log": "",
    "pending_request_signal": "",
    "github_source_attach_status": "",
    "github_source_attach_boundary": "",
    "github_source_attach_detail": "",
    "github_source_attach_context": "",
    "github_source_attach_log": "",
    "request_send_continued_without_github_source": False,
    "pending_handoff_hash": "",
    "pending_handoff_source": "",
    "pending_handoff_log": "",
    "next_request_requires_rotation": False,
    "next_request_rotation_reason": "",
    "rotate_after_cycle": False,
    "rotate_after_cycle_reason": "",
    "last_processed_request_hash": "",
    "last_processed_reply_hash": "",
    "current_chat_session": "",
    "human_review_auto_continue_count": 0,
    "last_issue_centric_action": "",
    "last_issue_centric_target_issue": "",
    "last_issue_centric_decision_log": "",
    "last_issue_centric_metadata_log": "",
    "last_issue_centric_artifact_file": "",
    "last_issue_centric_dispatch_result": "",
    "last_issue_centric_normalized_summary": "",
    "last_issue_centric_principal_issue": "",
    "last_issue_centric_principal_issue_kind": "",
    "last_issue_centric_next_request_hint": "",
    "last_issue_centric_next_request_target": "",
    "last_issue_centric_next_request_target_source": "",
    "last_issue_centric_next_request_fallback_reason": "",
    "last_issue_centric_route_selected": "",
    "last_issue_centric_route_fallback_reason": "",
    "last_issue_centric_recovery_status": "",
    "last_issue_centric_recovery_source": "",
    "last_issue_centric_recovery_fallback_reason": "",
    "last_issue_centric_runtime_snapshot": "",
    "last_issue_centric_snapshot_status": "",
    "last_issue_centric_runtime_generation_id": "",
    "last_issue_centric_generation_lifecycle": "",
    "last_issue_centric_generation_lifecycle_reason": "",
    "last_issue_centric_generation_lifecycle_source": "",
    "last_issue_centric_prepared_generation_id": "",
    "last_issue_centric_pending_generation_id": "",
    "last_issue_centric_runtime_mode": "",
    "last_issue_centric_runtime_mode_reason": "",
    "last_issue_centric_runtime_mode_source": "",
    "last_issue_centric_state_view": "",
    "last_issue_centric_state_view_reason": "",
    "last_issue_centric_state_view_source": "",
    "last_issue_centric_wait_kind": "",
    "last_issue_centric_wait_reason": "",
    "last_issue_centric_freshness_status": "",
    "last_issue_centric_freshness_reason": "",
    "last_issue_centric_freshness_source": "",
    "last_issue_centric_invalidation_status": "",
    "last_issue_centric_invalidation_reason": "",
    "last_issue_centric_invalidated_generation_id": "",
    "last_issue_centric_consumed_generation_id": "",
    "last_issue_centric_artifact_kind": "",
    "last_issue_centric_execution_status": "",
    "last_issue_centric_execution_log": "",
    "last_issue_centric_created_issue_number": "",
    "last_issue_centric_created_issue_url": "",
    "last_issue_centric_created_issue_title": "",
    "last_issue_centric_primary_issue_number": "",
    "last_issue_centric_primary_issue_url": "",
    "last_issue_centric_primary_issue_title": "",
    "last_issue_centric_resolved_issue": "",
    "last_issue_centric_trigger_comment_id": "",
    "last_issue_centric_trigger_comment_url": "",
    "last_issue_centric_execution_payload_log": "",
    "last_issue_centric_launch_status": "",
    "last_issue_centric_launch_entrypoint": "",
    "last_issue_centric_launch_prompt_log": "",
    "last_issue_centric_launch_log": "",
    "last_issue_centric_continuation_status": "",
    "last_issue_centric_continuation_log": "",
    "last_issue_centric_report_status": "",
    "last_issue_centric_report_file": "",
    "last_issue_centric_project_sync_status": "",
    "last_issue_centric_project_url": "",
    "last_issue_centric_project_item_id": "",
    "last_issue_centric_project_state_field": "",
    "last_issue_centric_project_state_value": "",
    "last_issue_centric_primary_project_sync_status": "",
    "last_issue_centric_primary_project_url": "",
    "last_issue_centric_primary_project_item_id": "",
    "last_issue_centric_primary_project_state_field": "",
    "last_issue_centric_primary_project_state_value": "",
    "last_issue_centric_followup_status": "",
    "last_issue_centric_followup_log": "",
    "last_issue_centric_followup_parent_issue": "",
    "last_issue_centric_followup_issue_number": "",
    "last_issue_centric_followup_issue_url": "",
    "last_issue_centric_followup_issue_title": "",
    "last_issue_centric_followup_project_sync_status": "",
    "last_issue_centric_followup_project_url": "",
    "last_issue_centric_followup_project_item_id": "",
    "last_issue_centric_followup_project_state_field": "",
    "last_issue_centric_followup_project_state_value": "",
    "last_issue_centric_current_project_item_id": "",
    "last_issue_centric_current_project_url": "",
    "last_issue_centric_lifecycle_sync_status": "",
    "last_issue_centric_lifecycle_sync_log": "",
    "last_issue_centric_lifecycle_sync_issue": "",
    "last_issue_centric_lifecycle_sync_stage": "",
    "last_issue_centric_lifecycle_sync_project_url": "",
    "last_issue_centric_lifecycle_sync_project_item_id": "",
    "last_issue_centric_lifecycle_sync_state_field": "",
    "last_issue_centric_lifecycle_sync_state_value": "",
    "last_issue_centric_close_status": "",
    "last_issue_centric_close_log": "",
    "last_issue_centric_closed_issue_number": "",
    "last_issue_centric_closed_issue_url": "",
    "last_issue_centric_closed_issue_title": "",
    "last_issue_centric_close_order": "",
    "last_issue_centric_parent_update_status": "",
    "last_issue_centric_parent_update_log": "",
    "last_issue_centric_parent_update_issue": "",
    "last_issue_centric_parent_update_comment_id": "",
    "last_issue_centric_parent_update_comment_url": "",
    "last_issue_centric_parent_update_closed_issue": "",
    "last_issue_centric_review_status": "",
    "last_issue_centric_review_log": "",
    "last_issue_centric_review_comment_id": "",
    "last_issue_centric_review_comment_url": "",
    "last_issue_centric_review_close_policy": "",
    "last_issue_centric_stop_reason": "",
    "last_project_sync_alert_status": "",
    "last_project_sync_alert_hash": "",
    "last_project_sync_alert_file": "",
    "last_project_sync_alert_delivery_status": "",
    "last_project_sync_alert_delivery_hash": "",
    "last_project_sync_alert_delivery_attempted_at": "",
    "last_project_sync_alert_delivery_error": "",
    "last_project_sync_alert_delivery_url": "",
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
    "require_github_source": False,
}

DEFAULT_PROJECT_CONFIG: dict[str, Any] = {
    "project_name": ROOT_DIR.name,
    "bridge_runtime_root": ".",
    "worker_repo_path": ".",
    "worker_repo_marker_mode": "strict",
    "worker_repo_markers": [],
    "github_repository": "",
    "github_project_url": "",
    "execution_agent": "codex",
    "agent_model": "",
    "github_copilot_bin": "gh",
    "codex_bin": "codex",
    "codex_model": "",
    "codex_sandbox": "",
    "codex_timeout_seconds": 7200,
    "report_request_next_todo": "前回 report を踏まえて、次の 1 フェーズ分の Codex 用 prompt を作成してください。",
    "report_request_open_questions": "未解決事項があれば安全側で補ってください。",
    "project_sync_alert_webhook_url": "",
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
    "内の新しいチャット",
    "このプロジェクト内の新しいチャット",
    "このプロジェクト内で新しいチャット",
    "プロジェクト内の新しいチャット",
)
PROJECT_CHAT_PLUS_BUTTON_LABELS = (
    "+",
    "＋",
    "ファイルの追加など",
    "Add files and more",
)
PROJECT_CHAT_PLUS_BUTTON_SELECTORS = (
    "#composer-plus-btn",
    "[data-testid='composer-plus-btn']",
)
PROJECT_CHAT_MORE_LABELS = (
    "さらに表示",
    "More",
    "See more",
)
PROJECT_CHAT_ADD_SOURCE_LABELS = (
    "情報源を追加する",
    "Add sources",
)
PROJECT_CHAT_GITHUB_LABELS = (
    "GitHub",
)
PROJECT_CHAT_GITHUB_PILL_REMOVE_LABELS = (
    "GitHub：クリックして削除",
    "GitHub: click to remove",
)
PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_MS = 250
PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_ATTEMPTS = 5
PROJECT_CHAT_MORE_OPEN_STRATEGIES = (
    "hover",
    "focus",
    "keyboard_enter",
    "keyboard_space",
    "keyboard_arrow_right",
    "click",
)
PROJECT_CHAT_GITHUB_ATTACH_PHASE_TIMEOUT_SECONDS = 20.0
PROJECT_NAME_HEADING_SELECTORS = [
    "main h1",
    "main h2",
    "header h1",
    "header h2",
    "[role='heading'][aria-level='1']",
    "[role='heading'][aria-level='2']",
]

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
    require_conversation: bool = True
    surface_label: str = "対象チャット"
    allow_conversation_transition: bool = False

    def wait_for_timeout(self, milliseconds: int) -> None:
        time.sleep(milliseconds / 1000.0)

    def evaluate(self, script: str) -> str:
        self.assert_same_front_tab()
        return _run_safari_javascript(script)

    def evaluate_unchecked(self, script: str) -> str:
        return _run_safari_javascript(script)

    def assert_same_front_tab(self) -> None:
        current_tab = frontmost_safari_tab_info(
            self.config,
            require_conversation=self.require_conversation and not self.allow_conversation_transition,
        )
        if _same_tab(self.front_tab, current_tab):
            return

        target_prefix = str(self.config.get("chat_url_prefix", DEFAULT_BROWSER_CONFIG["chat_url_prefix"]))
        if (
            self.allow_conversation_transition
            and _chat_domain_matches(current_tab.get("url", ""), target_prefix)
            and _conversation_url_matches(current_tab.get("url", ""), self.config)
        ):
            self.front_tab = dict(current_tab)
            return

        dump_note = ""
        if _chat_domain_matches(current_tab.get("url", ""), target_prefix):
            dump_text = _body_text_unchecked()
            if dump_text:
                dump_path = log_text("raw_chatgpt_prompt_dump", dump_text, suffix="txt")
                dump_note = f" raw dump: {repo_relative(dump_path)}"

        raise BridgeError(
            "Safari の現在タブが切り替わりました。"
            f"{self.surface_label} を再表示してから再実行してください。"
            f" 現在: {current_tab.get('title', '')} {current_tab.get('url', '')}{dump_note}"
        )


@dataclass(frozen=True)
class BridgeStatusView:
    label: str
    detail: str


@dataclass(frozen=True)
class CodexLifecycleView:
    """Unified view of the Codex lifecycle compatibility state.

    This is the single place for ready_for_codex / codex_running / codex_done
    classification logic.  It centralises the action key, display wording, and
    blocked flag that were previously scattered across resolve_unified_next_action(),
    present_bridge_status(), and bridge_orchestrator.run().

    Fields:
        action      -- canonical action key used by resolve_unified_next_action() and
                       bridge_orchestrator dispatch ("launch_codex_once" /
                       "wait_for_codex_report" / "archive_codex_report" /
                       "check_codex_condition" when blocked).
        status_label -- human-facing label for BridgeStatusView.
        status_detail -- human-facing detail for BridgeStatusView.
        is_blocked   -- True when operator confirmation is needed before the
                        Codex step can proceed (ready_for_codex without
                        need_codex_run); callers should not dispatch in this case.
    """

    action: str
    status_label: str
    status_detail: str
    is_blocked: bool

    def to_status_view(self) -> "BridgeStatusView":
        """Return a BridgeStatusView built from this lifecycle view."""
        return BridgeStatusView(self.status_label, self.status_detail)


@dataclass(frozen=True)
class IssueCentricRouteChoice:
    route_selected: str
    route_reason: str
    route_reason_source: str
    runtime_mode: str
    generation_lifecycle: str
    target_issue: str
    target_issue_source: str
    next_request_hint: str
    preferred_loop_action: str
    preferred_loop_reason: str


@dataclass(frozen=True)
class RuntimeDispatchPlan:
    """Consolidated action-view of what the runtime should do next.

    Produced by resolve_runtime_dispatch_plan().  Runners read this instead of
    calling resolve_runtime_next_action() + transition helpers individually.

    Fields:
        runtime_action:  high-level action key from resolve_runtime_next_action()
                         ("prepared_request" | "pending_reply" | "need_next_generation"
                          | "fallback_legacy")
        next_action:     concrete action key after transition resolution
                         ("request_next_prompt" | "request_prompt_from_report" |
                          "fetch_next_prompt" | "completed" | "no_action" | …)
        note:            human-facing dispatch note from format_next_action_note()
        route_choice:    IssueCentricRouteChoice used for wording context
        runtime_action_reason: reason string from resolve_runtime_next_action()
        is_terminal:     True when next_action is "completed" or "no_action" —
                         no further dispatching is needed
        is_fallback:     True when runtime_action is "fallback_legacy"
    """

    runtime_action: str
    next_action: str
    note: str
    route_choice: "IssueCentricRouteChoice"
    runtime_action_reason: str
    is_terminal: bool
    is_fallback: bool


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
    details: Mapping[str, Any] | None = None


def _bridge_lifecycle_sync_suffix(state: Mapping[str, Any]) -> str:
    """Return a compact bracketed lifecycle sync suffix for bridge status/handoff detail text.

    Returns '[lifecycle_sync: stage=X signal=synced]' or '' when no sync data is present.
    Three signals: synced | skipped_no_project | sync_failed (+ reason for failures).
    Consistent with the signal model introduced in issue #50.
    """
    sync_status = str(state.get("last_issue_centric_lifecycle_sync_status", "")).strip()
    sync_stage = str(state.get("last_issue_centric_lifecycle_sync_stage", "")).strip()
    if not sync_status and not sync_stage:
        return ""
    if sync_status == "project_state_synced":
        signal = "synced"
    elif sync_status == "not_requested_no_project":
        signal = "skipped_no_project"
    elif sync_status:
        signal = "sync_failed"
    else:
        return ""
    parts: list[str] = []
    if sync_stage:
        parts.append(f"stage={sync_stage}")
    parts.append(f"signal={signal}")
    if signal == "sync_failed" and sync_status:
        parts.append(f"reason={sync_status}")
    return f" [lifecycle_sync: {' '.join(parts)}]"


def bridge_lifecycle_sync_suffix(state: Mapping[str, Any]) -> str:
    """Public wrapper around _bridge_lifecycle_sync_suffix for cross-module use.

    Returns '[lifecycle_sync: stage=X signal=synced/skipped_no_project/sync_failed]' or ''.
    Consistent with the signal model introduced in issue #50.
    """
    return _bridge_lifecycle_sync_suffix(state)


# ---------------------------------------------------------------------------
# Project sync warning helpers (Phase 56)
# ---------------------------------------------------------------------------
# Covers the three project-sync families.  Lifecycle sync is already surfaced
# via _bridge_lifecycle_sync_suffix; the helpers below extend coverage to the
# primary (issue-create) and followup families, and provide a unified detection
# helper used by stop summaries and operator-facing notes.

_PROJECT_SYNC_FAILED = "project_state_sync_failed"

# Primary and followup families.  Lifecycle is handled separately by
# _bridge_lifecycle_sync_suffix to avoid duplicate reporting in inline suffixes.
_PROJECT_SYNC_IC_FAMILIES: dict[str, str] = {
    "primary": "last_issue_centric_primary_project_sync_status",
    "followup": "last_issue_centric_followup_project_sync_status",
}


def _detect_project_sync_warning(state: Mapping[str, Any]) -> bool:
    """Return True if any of the three project sync families recorded project_state_sync_failed.

    Checks primary, followup, AND lifecycle sync families so callers can use a
    single boolean gate for 'any project sync failed' decisions.
    """
    all_keys = list(_PROJECT_SYNC_IC_FAMILIES.values()) + [
        "last_issue_centric_lifecycle_sync_status",
    ]
    return any(str(state.get(k, "")).strip() == _PROJECT_SYNC_FAILED for k in all_keys)


def _resolve_project_sync_warning_family(state: Mapping[str, Any]) -> list[str]:
    """Return the family names (primary, followup, lifecycle) that have project_state_sync_failed."""
    all_families: dict[str, str] = {
        **_PROJECT_SYNC_IC_FAMILIES,
        "lifecycle": "last_issue_centric_lifecycle_sync_status",
    }
    return [
        name
        for name, key in all_families.items()
        if str(state.get(key, "")).strip() == _PROJECT_SYNC_FAILED
    ]


def _build_project_sync_warning_note(state: Mapping[str, Any]) -> str:
    """Return an inline warning note when primary or followup project sync failed.

    Covers only primary and followup families — lifecycle sync is already surfaced
    via _bridge_lifecycle_sync_suffix and is intentionally excluded here to avoid
    duplicate reporting in status/handoff text.

    Returns empty string when no primary/followup project sync failure is present.

    Severity: 'success with warning'.  The main action (issue create / followup)
    completed; only Project state sync failed.  This is NOT a hard runtime error.
    Operator guidance: check GitHub Project config / state field / token / permissions.
    """
    failed = [
        name
        for name, key in _PROJECT_SYNC_IC_FAMILIES.items()
        if str(state.get(key, "")).strip() == _PROJECT_SYNC_FAILED
    ]
    if not failed:
        return ""
    families_str = "+".join(failed)
    return (
        f" [project_sync: warning family={families_str}"
        " 主処理は完了しましたが Project state sync に失敗しました。"
        " GitHub Project 設定・state field・token・permissions を確認してください。]"
    )


def bridge_project_sync_warning_suffix(state: Mapping[str, Any]) -> str:
    """Public wrapper for primary/followup project sync warning note.

    Returns a compact bracketed warning suitable for inline use in status/handoff
    text, or empty string when no primary/followup project sync failure is present.
    Lifecycle sync failures are covered by bridge_lifecycle_sync_suffix instead.
    """
    return _build_project_sync_warning_note(state)


def format_project_sync_warning_note(state: Mapping[str, Any]) -> str:
    """Return a diagnostic note covering all three project-sync families for stop summaries.

    Unlike bridge_project_sync_warning_suffix (primary+followup only), this function
    covers primary, followup, AND lifecycle so stop summaries give a complete picture.

    Returns e.g. 'family=primary+lifecycle failed=project_state_sync_failed' or
    'none' when no project sync failure is present.
    """
    failed_families = _resolve_project_sync_warning_family(state)
    if not failed_families:
        return "none"
    families_str = "+".join(failed_families)
    return f"family={families_str} failed={_PROJECT_SYNC_FAILED}"


# ---------------------------------------------------------------------------
# project_state_sync_failed — alert signal / payload / dedupe (Phase 58)
# ---------------------------------------------------------------------------

#: Alert payload artifact — single latest-wins file in bridge/ root.
#: Operator can inspect this file to see the last undelivered alert candidate.
ALERT_PAYLOAD_PATH = BRIDGE_DIR / "project_sync_alert.json"

#: State-key map for each alert family (primary / followup / lifecycle).
_ALERT_FAMILY_STATE_KEYS: dict[str, dict[str, str]] = {
    "primary": {
        "project_url": "last_issue_centric_primary_project_url",
        "project_item_id": "last_issue_centric_primary_project_item_id",
        "project_state_field": "last_issue_centric_primary_project_state_field",
        "project_state_value": "last_issue_centric_primary_project_state_value",
    },
    "followup": {
        "project_url": "last_issue_centric_followup_project_url",
        "project_item_id": "last_issue_centric_followup_project_item_id",
        "project_state_field": "last_issue_centric_followup_project_state_field",
        "project_state_value": "last_issue_centric_followup_project_state_value",
    },
    "lifecycle": {
        "project_url": "last_issue_centric_lifecycle_sync_project_url",
        "project_item_id": "last_issue_centric_lifecycle_sync_project_item_id",
        "project_state_field": "last_issue_centric_lifecycle_sync_state_field",
        "project_state_value": "last_issue_centric_lifecycle_sync_state_value",
    },
}


@dataclass
class _ProjectSyncAlertCandidate:
    """Internal representation of a project_state_sync_failed alert candidate.

    Built by _build_project_sync_alert_candidate() when project sync failure is
    detected.  Used to generate the alert payload artifact and dedupe hash.

    Severity: 'success with warning' (NOT a hard runtime error).
    Only project_state_sync_failed triggers an alert candidate.
    not_requested_no_project / issue_only_fallback are excluded by the detection gate.
    """

    families: list[str]
    sync_status: str
    issue_ref: str
    principal_issue_ref: str
    next_request_target: str
    project_url: str
    project_item_id: str
    project_state_field: str
    project_state_value: str
    runtime_mode: str
    runtime_action: str
    detected_at: str
    alert_hash: str
    source_note: str


def _detect_project_sync_alert_candidate(state: Mapping[str, Any]) -> bool:
    """Return True if any project sync family has project_state_sync_failed.

    This is the sole gate for alert candidate detection.  Only
    project_state_sync_failed qualifies; not_requested_no_project and
    issue_only_fallback do not.  Hard errors (error=True) are a separate
    signal and do not affect this gate.
    """
    return _detect_project_sync_warning(state)


def _build_project_sync_alert_candidate(
    state: Mapping[str, Any],
) -> _ProjectSyncAlertCandidate | None:
    """Build an alert candidate from state, or return None if no failure is detected.

    Returns None when:
    - No project sync family has project_state_sync_failed
    - not_requested_no_project / issue_only_fallback are present (already excluded
      by _detect_project_sync_alert_candidate which checks only project_state_sync_failed)
    """
    if not _detect_project_sync_alert_candidate(state):
        return None

    failed_families = _resolve_project_sync_warning_family(state)
    first_family = failed_families[0] if failed_families else ""
    family_keys = _ALERT_FAMILY_STATE_KEYS.get(first_family, {})
    project_url = str(state.get(family_keys.get("project_url", ""), "")).strip()
    project_item_id = str(state.get(family_keys.get("project_item_id", ""), "")).strip()
    project_state_field = str(state.get(family_keys.get("project_state_field", ""), "")).strip()
    project_state_value = str(state.get(family_keys.get("project_state_value", ""), "")).strip()
    issue_ref = str(state.get("last_issue_centric_target_issue", "")).strip()
    principal_issue_ref = str(state.get("last_issue_centric_principal_issue", "")).strip()
    next_request_target = str(state.get("last_issue_centric_next_request_target", "")).strip()
    runtime_mode = str(state.get("last_issue_centric_runtime_mode", "")).strip()
    runtime_action = str(state.get("last_issue_centric_action", "")).strip()
    detected_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    # Stable hash: key identity fields only (not detected_at) for reliable deduplication.
    hash_parts = "|".join([
        "+".join(sorted(failed_families)),
        _PROJECT_SYNC_FAILED,
        issue_ref,
        project_url,
        project_item_id,
        project_state_value,
    ])
    alert_hash = hashlib.sha256(hash_parts.encode("utf-8")).hexdigest()
    source_note = format_project_sync_warning_note(state)
    return _ProjectSyncAlertCandidate(
        families=failed_families,
        sync_status=_PROJECT_SYNC_FAILED,
        issue_ref=issue_ref,
        principal_issue_ref=principal_issue_ref,
        next_request_target=next_request_target,
        project_url=project_url,
        project_item_id=project_item_id,
        project_state_field=project_state_field,
        project_state_value=project_state_value,
        runtime_mode=runtime_mode,
        runtime_action=runtime_action,
        detected_at=detected_at,
        alert_hash=alert_hash,
        source_note=source_note,
    )


def _build_project_sync_alert_payload(
    candidate: _ProjectSyncAlertCandidate,
) -> dict[str, Any]:
    """Serialize an alert candidate to a JSON-serializable dict."""
    return {
        "family": "+".join(candidate.families),
        "sync_status": candidate.sync_status,
        "issue_ref": candidate.issue_ref,
        "principal_issue_ref": candidate.principal_issue_ref,
        "next_request_target": candidate.next_request_target,
        "project_url": candidate.project_url,
        "project_item_id": candidate.project_item_id,
        "project_state_field": candidate.project_state_field,
        "project_state_value": candidate.project_state_value,
        "runtime_mode": candidate.runtime_mode,
        "runtime_action": candidate.runtime_action,
        "detected_at": candidate.detected_at,
        "hash": candidate.alert_hash,
        "source_note": candidate.source_note,
    }


def record_project_sync_alert_if_new(state: Mapping[str, Any]) -> str:
    """Record a project sync alert payload if a new (non-duplicate) candidate is found.

    Deduplication: if the current alert hash equals last_project_sync_alert_hash in
    state, no new payload is written.  A new failed event (different issue / project /
    state_value) produces a different hash and triggers a new payload.

    Returns:
        "recorded"          — new alert payload was saved; state updated
        "skipped_duplicate" — same hash as last alert; no action taken
        "none"              — no alert candidate detected (no project_state_sync_failed)

    Side effects when returning "recorded":
        - Writes JSON payload to ALERT_PAYLOAD_PATH (bridge/project_sync_alert.json)
        - Calls update_state() to persist last_project_sync_alert_status="pending",
          last_project_sync_alert_hash, and last_project_sync_alert_file in state.json
    """
    candidate = _build_project_sync_alert_candidate(state)
    if candidate is None:
        return "none"
    last_hash = str(state.get("last_project_sync_alert_hash", "")).strip()
    if last_hash == candidate.alert_hash:
        return "skipped_duplicate"
    payload = _build_project_sync_alert_payload(candidate)
    ALERT_PAYLOAD_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    update_state(
        last_project_sync_alert_status="pending",
        last_project_sync_alert_hash=candidate.alert_hash,
        last_project_sync_alert_file=str(ALERT_PAYLOAD_PATH.relative_to(ROOT_DIR)),
    )
    return "recorded"


def format_project_sync_alert_status(state: Mapping[str, Any]) -> str:
    """Return a short alert status string for doctor / summary display.

    Returns 'pending file=bridge/project_sync_alert.json' when an undelivered
    alert payload is present in state, or 'none' when no alert is pending.
    """
    status = str(state.get("last_project_sync_alert_status", "")).strip()
    alert_file = str(state.get("last_project_sync_alert_file", "")).strip()
    if status == "pending" and alert_file:
        return f"pending file={alert_file}"
    return "none"


def format_project_sync_alert_delivery_status(state: Mapping[str, Any]) -> str:
    """Return a short delivery status string for doctor / summary display.

    Returns e.g. 'delivered hash=...' / 'delivery_failed error=...' / 'not_requested_no_webhook' / 'none'.
    """
    delivery_status = str(state.get("last_project_sync_alert_delivery_status", "")).strip()
    delivery_hash = str(state.get("last_project_sync_alert_delivery_hash", "")).strip()
    delivery_error = str(state.get("last_project_sync_alert_delivery_error", "")).strip()
    if not delivery_status:
        return "none"
    if delivery_status == "delivered" and delivery_hash:
        return f"delivered hash={delivery_hash[:12]}"
    if delivery_status == "delivery_failed":
        error_summary = delivery_error[:80] if delivery_error else "unknown"
        return f"delivery_failed error={error_summary}"
    if delivery_status == "not_requested_no_webhook":
        return "not_requested_no_webhook"
    if delivery_status == "skipped_already_delivered" and delivery_hash:
        return f"skipped_already_delivered hash={delivery_hash[:12]}"
    return delivery_status


# ---------------------------------------------------------------------------
# project_state_sync_failed — generic webhook delivery (Phase 59)
# ---------------------------------------------------------------------------

#: delivery success return codes (HTTP 2xx).
_WEBHOOK_SUCCESS_CODES = frozenset(range(200, 300))

#: HTTP request timeout for webhook delivery (seconds).  Kept short so that a
#: slow or unreachable webhook endpoint never blocks the normal operator flow.
_WEBHOOK_TIMEOUT_SECONDS = 10


def _deliver_project_sync_alert_to_webhook(
    payload: dict[str, Any],
    webhook_url: str,
    *,
    timeout: int = _WEBHOOK_TIMEOUT_SECONDS,
) -> tuple[str, str]:
    """POST the alert payload to webhook_url as JSON.

    Returns:
        ("delivered", "")               — HTTP 2xx response
        ("delivery_failed", error_str)  — HTTP non-2xx or network error

    This function never raises; all errors are caught and returned as the
    second element of the tuple so callers can surface them without raising.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = resp.status
            if status_code in _WEBHOOK_SUCCESS_CODES:
                return ("delivered", "")
            return ("delivery_failed", f"HTTP {status_code}")
    except urllib.error.HTTPError as exc:
        return ("delivery_failed", f"HTTPError {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        return ("delivery_failed", f"URLError: {exc.reason}")
    except Exception as exc:  # noqa: BLE001
        return ("delivery_failed", str(exc)[:200])


def deliver_project_sync_alert_if_pending(
    state: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> str:
    """Deliver a pending project sync alert to the configured webhook URL.

    Deduplication: if last_project_sync_alert_delivery_hash equals the current
    last_project_sync_alert_hash, delivery is skipped (already delivered).

    Returns:
        "none"                      — no pending alert (last_project_sync_alert_status != "pending")
        "not_requested_no_webhook"  — alert pending but webhook URL not configured
        "delivered"                 — payload posted successfully (HTTP 2xx)
        "delivery_failed"           — HTTP error / network error (NOT a hard runtime error)
        "skipped_already_delivered" — same alert hash was already delivered
        "invalid_payload"           — payload file missing or not valid JSON

    Side effects when returning "delivered" or "delivery_failed":
        - Calls update_state() to persist delivery status, hash, timestamp, error.
        - delivery failure does NOT set state["error"] = True.

    Design constraint:
        Webhook delivery failure is NOT a hard runtime error.
        The operator learns of the failure via state / doctor output, but the main
        processing result (success with warning) is preserved.
    """
    alert_status = str(state.get("last_project_sync_alert_status", "")).strip()
    if alert_status != "pending":
        return "none"

    alert_hash = str(state.get("last_project_sync_alert_hash", "")).strip()
    delivered_hash = str(state.get("last_project_sync_alert_delivery_hash", "")).strip()
    if alert_hash and alert_hash == delivered_hash:
        return "skipped_already_delivered"

    _cfg = config or {}
    webhook_url = str(_cfg.get("project_sync_alert_webhook_url", "")).strip()
    if not webhook_url:
        return "not_requested_no_webhook"

    alert_file_rel = str(state.get("last_project_sync_alert_file", "")).strip()
    alert_file = ROOT_DIR / alert_file_rel if alert_file_rel else ALERT_PAYLOAD_PATH
    if not alert_file.exists():
        return "invalid_payload"
    try:
        payload_text = alert_file.read_text(encoding="utf-8")
        payload = json.loads(payload_text)
    except (OSError, json.JSONDecodeError):
        return "invalid_payload"
    if not isinstance(payload, dict):
        return "invalid_payload"

    delivery_status, error_detail = _deliver_project_sync_alert_to_webhook(payload, webhook_url)
    attempted_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    update_state(
        last_project_sync_alert_delivery_status=delivery_status,
        last_project_sync_alert_delivery_hash=alert_hash,
        last_project_sync_alert_delivery_attempted_at=attempted_at,
        last_project_sync_alert_delivery_error=error_detail,
        last_project_sync_alert_delivery_url=webhook_url,
    )
    return delivery_status


def format_lifecycle_sync_state_note(state: Mapping[str, Any]) -> str:
    """Return a short lifecycle sync diagnostic note for stop summaries and doctor output.

    Returns e.g. 'stage=closing signal=synced' or 'not_recorded' when no sync data.
    Consistent with the signal model introduced in issue #50.
    Three signals: synced | skipped_no_project | sync_failed (+ reason for failures).
    """
    sync_status = str(state.get("last_issue_centric_lifecycle_sync_status", "")).strip()
    sync_stage = str(state.get("last_issue_centric_lifecycle_sync_stage", "")).strip()
    if not sync_status and not sync_stage:
        return "not_recorded"
    if sync_status == "project_state_synced":
        signal = "synced"
    elif sync_status == "not_requested_no_project":
        signal = "skipped_no_project"
    elif sync_status:
        signal = "sync_failed"
    else:
        return "not_recorded"
    parts: list[str] = []
    if sync_stage:
        parts.append(f"stage={sync_stage}")
    parts.append(f"signal={signal}")
    if signal == "sync_failed" and sync_status:
        parts.append(f"reason={sync_status}")
    return " ".join(parts)


def present_bridge_status(
    state: Mapping[str, Any],
    *,
    blocked: bool = False,
    stale_codex_running: bool = False,
) -> BridgeStatusView:
    """Return a human-facing status view for the current runtime state.

    Routing priority (post full-cutover):
      1. Pre-dispatch-plan early-exit guards (error / blocked / Codex dispatch)
      2. Codex lifecycle compatibility branch — action-view based, not direct lifecycle view
      3. Awaiting user supplement sub-case — human input required before dispatch resumes
      4. Normal path — resolve_runtime_dispatch_plan() is the primary routing authority;
         route by plan.runtime_action / plan.next_action / plan.is_fallback

    mode is NOT read directly at any point.  Lifecycle states (step 2) are classified via
    is_blocked_codex_lifecycle_state() and resolve_unified_next_action(); resolve_codex_lifecycle_view()
    is not called here.  Status labels and details for lifecycle states are expressed inline.
    """
    # --- Pre-dispatch-plan early-exit guards ---
    if bool(state.get("error")):
        return BridgeStatusView("異常", "まず stop summary と doctor を見て、必要なら詳しい error を確認してから再開します。")

    if blocked or stale_codex_running or runtime_stop_path().exists() or bool(state.get("pause")):
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        return BridgeStatusView("人確認待ち", f"まず stop summary の next step と note を確認してから再開します。{_lc_suffix}")

    if has_pending_issue_centric_codex_dispatch(state):
        return BridgeStatusView(
            "Codex実行待ち",
            "issue-centric で prepared Codex body を保持しています。次の bridge 手で codex_run dispatch を進めます。",
        )

    # --- Codex lifecycle compatibility branch (action-view based, no direct lifecycle view call) ---
    # is_blocked_codex_lifecycle_state() covers the blocked lifecycle case (ready_for_codex
    # without need_codex_run).  resolve_unified_next_action() covers non-blocked lifecycle
    # states.  Status labels and details are expressed inline so resolve_codex_lifecycle_view()
    # is not required at this call site.
    # Full cutover target: once lifecycle states are action-view equivalents in the state
    # machine, remove these arms and the is_blocked_codex_lifecycle_state() guard.
    if is_blocked_codex_lifecycle_state(state):
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        return BridgeStatusView("人確認待ち", f"Codex 実行条件を確認してください。{_lc_suffix}")

    if not is_normal_path_state(state):
        # Non-blocked lifecycle state: route by action key.
        action = resolve_unified_next_action(state)
        if action == "launch_codex_once":
            return BridgeStatusView("Codex実行待ち", "次の prompt はそろっています。bridge が Codex worker を 1 回起動します。")
        if action == "wait_for_codex_report":
            _lc_suffix = _bridge_lifecycle_sync_suffix(state)
            return BridgeStatusView("Codex実行中", f"Codex worker の完了報告を待っています。{_lc_suffix}")
        if action == "archive_codex_report":
            return BridgeStatusView("完了報告整理中", "完了報告を整理して、次の ChatGPT 依頼へつなぎます。")

    # --- Awaiting user supplement sub-case ---
    # next_action for these states is "request_prompt_from_report", but status is "人確認待ち"
    # because human input is required before dispatch can resume.
    chatgpt_decision = str(state.get("chatgpt_decision", "")).strip()
    chatgpt_decision_note = str(state.get("chatgpt_decision_note", "")).strip()

    # IC initial_selection_stop: ChatGPT selected a ready issue for the operator to confirm
    # with --ready-issue-ref.  Use a specific status label rather than the generic "人確認待ち"
    # so that the operator immediately understands the required next step.
    _ic_stop = detect_ic_stop_path(state)
    if _ic_stop == "initial_selection_stop":
        _selected_ref = str(state.get("selected_ready_issue_ref", "")).strip()
        _ic_note = chatgpt_decision_note or f"--ready-issue-ref {_selected_ref} を指定して bridge を再実行してください。"
        return BridgeStatusView("ready issue選定済み", _ic_note)

    if is_awaiting_user_supplement(state) or chatgpt_decision in {"human_review", "need_info"}:
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        _base = chatgpt_decision_note or "ChatGPT がここで人の補足を求めています。必要な判断や情報を入れてから続けます。"
        detail = f"{_base}{_lc_suffix}" if not chatgpt_decision_note else _base
        return BridgeStatusView("人確認待ち", detail)

    # --- Normal path: dispatch plan is the primary routing authority ---
    # For all states reaching this point, is_normal_path_state(state) is True.
    # mode is not read directly; plan.runtime_action / plan.next_action / plan.is_fallback
    # are the canonical routing subjects.
    plan = resolve_runtime_dispatch_plan(state)

    # Prepared request (fresh_prepared): reuse without rebuilding regardless of send sub-route.
    if plan.runtime_action == "prepared_request":
        return BridgeStatusView(
            "ChatGPT送信待ち",
            "issue-centric prepared request があり、再生成せずそのまま送信できます。",
        )

    if plan.next_action == "fetch_next_prompt":
        # issue-centric pending reply: distinct from legacy fetch substates.
        if plan.runtime_action == "pending_reply":
            _lc_suffix = _bridge_lifecycle_sync_suffix(state)
            return BridgeStatusView(
                "ChatGPT返答待ち",
                f"issue-centric pending generation に対する reply 回収を待っています。{_lc_suffix}",
            )
        pending_request_signal = str(state.get("pending_request_signal", "")).strip()
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        if pending_request_signal == "submitted_unconfirmed":
            _base = ic_delivery_pending_detail(
                "handoff 送信は通った可能性が高いため、同じ handoff は再送せず返答を待っています。",
                state,
                legacy_base_text="新しいチャットへの送信は通った可能性が高いため、同じ handoff は再送せず返答を待っています。",
            )
            return BridgeStatusView("ChatGPT返答待ち", f"{_base}{_lc_suffix}")
        if is_fetch_extended_wait_state(state):
            _base = ic_delivery_pending_detail("返答が重いため、追加待機しながら回収を続けています。", state)
            return BridgeStatusView("ChatGPT返答待ち", f"{_base}{_lc_suffix}")
        if is_fetch_late_completion_state(state):
            _base = ic_delivery_pending_detail("返答が書き切られるまで監視し、その後で回収します。", state)
            return BridgeStatusView("ChatGPT返答待ち", f"{_base}{_lc_suffix}")
        return BridgeStatusView("ChatGPT返答待ち", f"返答から次の Codex 用 prompt を回収します。{_lc_suffix}")

    if plan.next_action == "request_next_prompt":
        return BridgeStatusView(
            "ready issue参照で開始待ち",
            "通常は current ready issue の参照で始めます。free-form 初回本文は override 用で、bridge は reply contract だけを足します。",
        )

    if plan.next_action == "request_prompt_from_report":
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        pending_handoff_log = str(state.get("pending_handoff_log", "")).strip()
        if pending_handoff_log and should_rotate_before_next_chat_request(state):
            return BridgeStatusView(
                "ChatGPTへ依頼準備中",
                f"次の依頼を送る前に新しいチャットへ切り替えます。再実行で入力確認と送信確認を再試行します。{_lc_suffix}",
            )
        if plan.is_fallback:
            # Safety fallback (legacy) route is active: degraded / unavailable / invalidated.
            route_choice = plan.route_choice
            route_reason = (route_choice.route_reason if route_choice else "") or "fallback required"
            gen_lifecycle = (route_choice.generation_lifecycle if route_choice else "")
            if "invalidated" in gen_lifecycle:
                return BridgeStatusView(
                    "ChatGPTへ依頼準備中",
                    f"issue-centric generation は invalidated のため、safety fallback (legacy) route で次の依頼を準備します。理由: {route_reason}。{_lc_suffix}",
                )
            return BridgeStatusView(
                "ChatGPTへ依頼準備中",
                f"safety fallback (legacy) route で次の依頼を準備します。理由: {route_reason}。{_lc_suffix}",
            )
        # issue-centric preferred route.
        target_issue = (plan.route_choice.target_issue if plan.route_choice else "") or ""
        if target_issue:
            return BridgeStatusView(
                "ChatGPTへ依頼準備中",
                f"issue-centric preferred route で次の依頼を準備します。target_issue は {target_issue} です。{_lc_suffix}",
            )
        return BridgeStatusView("ChatGPTへ依頼準備中", f"issue-centric preferred route で次の依頼を準備します。{_lc_suffix}")

    if plan.is_terminal:
        detail = chatgpt_decision_note or "追加の操作は不要です。"
        return BridgeStatusView("完了", detail)

    return BridgeStatusView("人確認待ち", "まず stop summary と doctor を確認してから再開します。")


def present_bridge_handoff(
    state: Mapping[str, Any],
    *,
    reason: str = "",
    suggested_note: str = "",
    blocked: bool = False,
    stale_codex_running: bool = False,
    cycle_boundary_stop: bool = False,
) -> BridgeHandoffView:
    """Return a human-facing handoff view for the current runtime state.

    Early-exit guards fire before the status-driven fallthrough at the end.
    is_completed_state() replaces raw mode / need_* reads for the completion check
    so that mode is not consulted as an independent routing subject in this function.
    """
    chatgpt_decision = str(state.get("chatgpt_decision", "")).strip()
    error_message = str(state.get("error_message", "")).strip()
    normalized_reason = reason.strip()

    if bool(state.get("error")):
        detail = suggested_note or error_message or "stop summary と doctor を確認してください。"
        return BridgeHandoffView("異常で止まりました。まず doctor と summary を確認してください。", detail)

    if cycle_boundary_stop:
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        detail = suggested_note or f"この run は現在の cycle 完了までで止めました。次 cycle は次回実行で進めます。{_lc_suffix}"
        return BridgeHandoffView("この run は cycle 完了で停止しました。", detail)

    # Explicit completion signals: chatgpt_decision == "completed" or mode == "completed".
    # is_completed_state() is NOT used here intentionally: it also matches
    # mode == "idle" and not need_*, which must fire AFTER the blocked guard below.
    if chatgpt_decision == "completed" or str(state.get("mode", "")) == "completed":
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        detail = suggested_note or f"summary を確認し、必要なら report を見れば十分です。{_lc_suffix}"
        return BridgeHandoffView("完了しました。", detail)

    if chatgpt_decision == "human_review":
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        detail = suggested_note or f"stop summary の案内に沿って、次の判断や補足を入れてください。{_lc_suffix}"
        return BridgeHandoffView("人の判断が必要です。次の方針を決めてから再開してください。", detail)

    if chatgpt_decision == "need_info":
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        detail = suggested_note or f"不足している情報を補ってから再開してください。{_lc_suffix}"
        return BridgeHandoffView("情報が不足しています。入力内容を補って再開してください。", detail)

    # IC human_review_needed: ChatGPT returned a human_review_needed decision in IC context.
    # Provides a more specific title than the generic status-driven "人の確認が必要です。" fallthrough.
    if chatgpt_decision.startswith("issue_centric:") and "human_review_needed" in chatgpt_decision:
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        _ic_note = str(state.get("chatgpt_decision_note", "")).strip()
        detail = suggested_note or _ic_note or f"stop summary の案内に沿って、次の判断や補足を入れてください。{_lc_suffix}"
        return BridgeHandoffView("人の判断が必要です。補足を入れて bridge を再実行してください。", detail)

    if blocked or stale_codex_running or runtime_stop_path().exists() or bool(state.get("pause")):
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        detail = suggested_note or f"自動では進めません。stop summary の案内に沿って確認してください。{_lc_suffix}"
        return BridgeHandoffView("自動では進めません。まず summary と doctor を確認してください。", detail)

    if normalized_reason.startswith("--max-steps="):
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        detail = suggested_note or f"続けるなら summary のおすすめ 1 コマンドをそのまま使ってください。{_lc_suffix}"
        return BridgeHandoffView("上限回数に達したため、ここで一旦止めました。", detail)

    if normalized_reason.startswith("ユーザー中断"):
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        detail = suggested_note or f"再開するか、このまま止めるかを summary を見て決めてください。{_lc_suffix}"
        return BridgeHandoffView("途中で停止しました。summary / note を確認してください。", detail)

    # Normal path: effectively-idle completed state check.
    # blocked guard has already fired above, so is_completed_state() is safe here.
    if is_completed_state(state):
        _lc_suffix = _bridge_lifecycle_sync_suffix(state)
        detail = suggested_note or f"追加の操作は不要です。{_lc_suffix}"
        return BridgeHandoffView("完了しました。", detail)

    # Issue-centric initial_selection_stop: ChatGPT selected a ready issue.
    # Fires after all error/blocked/completed guards so it only matches the
    # deliberate stop path where selected_ready_issue_ref was written by
    # _apply_ic_fetch_stop_state().
    _selected_ref = str(state.get("selected_ready_issue_ref", "")).strip()
    if _selected_ref and chatgpt_decision.startswith("issue_centric:"):
        _ic_note = str(state.get("chatgpt_decision_note", "")).strip()
        detail = suggested_note or _ic_note or f"--ready-issue-ref {_selected_ref} を指定して bridge を再実行してください。"
        return BridgeHandoffView(
            f"ChatGPT が ready issue を選定しました。--ready-issue-ref {_selected_ref} で bridge を再実行してください。",
            detail,
        )

    status = present_bridge_status(state, blocked=blocked, stale_codex_running=stale_codex_running)
    detail = suggested_note or status.detail
    if status.label == "人確認待ち":
        return BridgeHandoffView("人の確認が必要です。summary と doctor を確認してください。", detail)
    if status.label == "完了":
        return BridgeHandoffView("完了しました。", detail)
    if status.label == "ready issue選定済み":
        _sel = str(state.get("selected_ready_issue_ref", "")).strip()
        _title = f"ChatGPT が ready issue を選定しました。--ready-issue-ref {_sel} で bridge を再実行してください。" if _sel else "ChatGPT が ready issue を選定しました。--ready-issue-ref を指定して bridge を再実行してください。"
        return BridgeHandoffView(_title, detail)
    if status.label == "ready issue参照で開始待ち":
        return BridgeHandoffView("current ready issue の参照で開始してください。", detail)
    if status.label == "ChatGPTへ依頼準備中":
        return BridgeHandoffView("ChatGPT への次の依頼を送る準備ができています。", detail)
    if status.label == "ChatGPT返答待ち":
        _ic_pending, _ic_issue = is_issue_centric_delivery_pending_state(state)
        if _ic_pending and _ic_issue:
            return BridgeHandoffView(
                f"ChatGPT の返答を待っています ({_ic_issue} の delivery pending)。",
                detail,
            )
        return BridgeHandoffView("ChatGPT の返答を待っています。", detail)
    if status.label == "Codex実行待ち":
        return BridgeHandoffView("Codex を起動する準備ができています。", detail)
    if status.label == "Codex実行中":
        return BridgeHandoffView("Codex の完了を待っています。", detail)
    if status.label == "完了報告整理中":
        return BridgeHandoffView("完了報告を整理して次へ進めます。", detail)
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
    _apply_issue_centric_state_bridge_fields(normalized)
    runtime_state_path().write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _apply_issue_centric_state_bridge_fields(state: dict[str, Any]) -> None:
    bridge = resolve_issue_centric_state_bridge(state, repo_root=ROOT_DIR)
    if bridge is None:
        state["last_issue_centric_state_view"] = ""
        state["last_issue_centric_state_view_reason"] = ""
        state["last_issue_centric_state_view_source"] = ""
        state["last_issue_centric_wait_kind"] = ""
        state["last_issue_centric_wait_reason"] = ""
        return
    state["last_issue_centric_state_view"] = bridge.state_view
    state["last_issue_centric_state_view_reason"] = bridge.state_view_reason
    state["last_issue_centric_state_view_source"] = bridge.state_view_source
    state["last_issue_centric_wait_kind"] = bridge.wait_kind
    state["last_issue_centric_wait_reason"] = bridge.wait_reason


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
    return ""


def prepared_request_log_path(state: Mapping[str, Any]) -> Path | None:
    raw_path = str(state.get("prepared_request_log", "")).strip()
    if not raw_path:
        return None
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (bridge_runtime_root() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def read_prepared_request_text(state: Mapping[str, Any]) -> str:
    log_path = prepared_request_log_path(state)
    if log_path and log_path.exists():
        return read_text(log_path).strip()
    return ""


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
    state["pending_request_signal"] = ""
    state["github_source_attach_status"] = ""
    state["github_source_attach_boundary"] = ""
    state["github_source_attach_detail"] = ""
    state["github_source_attach_context"] = ""
    state["github_source_attach_log"] = ""
    state["request_send_continued_without_github_source"] = False
    return state


def clear_prepared_request_fields(state: dict[str, Any]) -> dict[str, Any]:
    state["prepared_request_hash"] = ""
    state["prepared_request_source"] = ""
    state["prepared_request_log"] = ""
    state["prepared_request_status"] = ""
    return state


def has_pending_issue_centric_codex_dispatch(state: Mapping[str, Any]) -> bool:
    return (
        str(state.get("mode", "")).strip() == "awaiting_user"
        and str(state.get("chatgpt_decision", "")).strip() == "issue_centric:codex_run"
        and str(state.get("last_issue_centric_artifact_kind", "")).strip() == "codex_body"
        and bool(str(state.get("last_issue_centric_metadata_log", "")).strip())
        and not bool(state.get("need_codex_run"))
        and not bool(str(state.get("last_issue_centric_execution_status", "")).strip())
    )


def detect_ic_stop_path(state: Mapping[str, Any]) -> str:
    """Return the IC stop path for operator-facing surfaces, or '' if not applicable.

    Used by present_bridge_status(), format_operator_stop_note(), and
    run_until_stop.py surfaces to produce stop-path-specific guidance rather
    than falling through to generic plan-based messages.

    Returns one of:
      "codex_run_stop"         — pending issue-centric codex dispatch (prepared body waiting)
      "initial_selection_stop" — ChatGPT selected a ready issue; operator must re-run
                                 with --ready-issue-ref
      "human_review_needed"    — IC human_review_needed decision; operator must resume
                                 with supplement input
      ""                       — not an IC awaiting-user stop path

    Evaluation order: codex_run_stop is checked first (uses has_pending_issue_centric_codex_dispatch),
    then chatgpt_decision prefix check, then sub-classification by selected_ready_issue_ref /
    "human_review_needed" substring.
    """
    if has_pending_issue_centric_codex_dispatch(state):
        return "codex_run_stop"
    chatgpt_decision = str(state.get("chatgpt_decision", "")).strip()
    if not chatgpt_decision.startswith("issue_centric:"):
        return ""
    if str(state.get("selected_ready_issue_ref", "")).strip():
        return "initial_selection_stop"
    if "human_review_needed" in chatgpt_decision:
        return "human_review_needed"
    return ""


def is_initial_bridge_state(state: Mapping[str, Any]) -> bool:
    """Return True when state is at the factory-default (just-reset / brand-new) state.

    The canonical initial state has:
      - mode == "idle"
      - need_chatgpt_prompt == True
      - no pending/prepared request hashes
      - no issue-centric artefacts
      - error=False, pause=False
    """
    if str(state.get("mode", "")).strip() != "idle":
        return False
    if not bool(state.get("need_chatgpt_prompt", True)):
        return False
    if bool(state.get("error")):
        return False
    if bool(state.get("pause")):
        return False
    if str(state.get("pending_request_hash", "")).strip():
        return False
    if str(state.get("pending_handoff_hash", "")).strip():
        return False
    if str(state.get("prepared_request_hash", "")).strip():
        return False
    if str(state.get("last_issue_centric_pending_generation_id", "")).strip():
        return False
    if str(state.get("last_issue_centric_prepared_generation_id", "")).strip():
        return False
    return True


# --- Start / resume entry action ---
#
# Returns one of the seven canonical start-entry action strings:
#
#   blocked_error                    — state.error=true, must clear before resuming
#   blocked_pause                    — state.pause=true, must unpause before resuming
#   resume_pending_reply             — a sent request is waiting for a ChatGPT reply
#   resume_pending_handoff           — a rotation handoff is pending confirmation
#   resume_prepared_request          — a prepared-but-not-yet-sent request is ready
#   resume_issue_centric_codex_dispatch — a codex dispatch is waiting to be launched
#   fresh_start_issue_selection      — state is at the initial default, start from scratch
#
# If none of the seven conditions match, returns "" so the caller can delegate to
# the existing routing (resolve_unified_next_action / run_until_stop).
_START_ENTRY_ACTIONS = frozenset(
    {
        "blocked_error",
        "blocked_pause",
        "resume_pending_reply",
        "resume_pending_handoff",
        "resume_prepared_request",
        "resume_issue_centric_codex_dispatch",
        "fresh_start_issue_selection",
    }
)

FRESH_START_ISSUE_SELECTION_TEMPLATE = (
    "この repo（{repo}）は Issue 前提で進めます。\n"
    "まず、今回進める Issue を確認してください。\n"
    "必要なら、親 Issue のまま進めず child issue を切る判断をして構いません。\n"
    "返答は issue-centric contract に従ってください。\n"
)


def resolve_start_resume_entry_action(state: Mapping[str, Any]) -> str:
    """Determine the start / resume entry action based on state.

    Evaluation order (fixed):
      1. error=true          → "blocked_error"
      2. pause=true          → "blocked_pause"
      3. pending_request_*   → "resume_pending_reply"
      4. pending_handoff_*   → "resume_pending_handoff"
      5. prepared_request_*  → "resume_prepared_request"
      6. pending codex dispatch → "resume_issue_centric_codex_dispatch"
      7. initial state       → "fresh_start_issue_selection"
      8. otherwise           → "" (delegate to existing routing)

    Returns a string from _START_ENTRY_ACTIONS or "" for delegation.
    """
    if bool(state.get("error")):
        return "blocked_error"
    if bool(state.get("pause")):
        return "blocked_pause"
    if str(state.get("pending_request_hash", "")).strip():
        return "resume_pending_reply"
    if str(state.get("pending_handoff_hash", "")).strip():
        return "resume_pending_handoff"
    prepared_hash = str(state.get("prepared_request_hash", "")).strip()
    prepared_source = str(state.get("prepared_request_source", "")).strip()
    if prepared_hash and prepared_source:
        return "resume_prepared_request"
    if has_pending_issue_centric_codex_dispatch(state):
        return "resume_issue_centric_codex_dispatch"
    if is_initial_bridge_state(state):
        return "fresh_start_issue_selection"
    return ""


def prepared_request_action(state: Mapping[str, Any]) -> str:
    prepared_source = str(state.get("prepared_request_source", "")).strip()
    if prepared_source.startswith(("report:", "handoff:", "human_review_continue:")):
        return "request_prompt_from_report"
    if prepared_source.startswith(("ready_issue:", "override:", "initial:")):
        return "request_next_prompt"
    return ""


def can_reuse_prepared_request(state: Mapping[str, Any]) -> bool:
    prepared_hash = str(state.get("prepared_request_hash", "")).strip()
    prepared_source = str(state.get("prepared_request_source", "")).strip()
    prepared_log = str(state.get("prepared_request_log", "")).strip()
    prepared_status = str(state.get("prepared_request_status", "")).strip()
    if not (prepared_hash and prepared_source and prepared_log):
        return False
    if prepared_status == "retry_send":
        return True
    if prepared_status != "prepared":
        return False
    generation_id = str(state.get("last_issue_centric_runtime_generation_id", "")).strip()
    if not generation_id:
        return True
    return str(state.get("last_issue_centric_generation_lifecycle", "")).strip() == "fresh_prepared"


def resolve_issue_centric_route_choice(state: Mapping[str, Any]) -> IssueCentricRouteChoice:
    """Return a routing-context snapshot for the current runtime state.

    Internal helper consumed by resolve_runtime_dispatch_plan() to populate
    route_choice in the returned RuntimeDispatchPlan.  Not the primary routing
    authority — callers should use resolve_runtime_dispatch_plan() instead.
    """
    runtime_mode = resolve_issue_centric_runtime_mode(state, repo_root=ROOT_DIR)
    if runtime_mode is None:
        return IssueCentricRouteChoice(
            route_selected="fallback_legacy",
            route_reason="issue_centric_runtime_mode_missing",
            route_reason_source="runtime_mode_missing",
            runtime_mode="",
            generation_lifecycle="",
            target_issue="",
            target_issue_source="legacy_unresolved",
            next_request_hint="",
            preferred_loop_action="",
            preferred_loop_reason="",
        )

    route_selected = "fallback_legacy"
    route_reason = runtime_mode.fallback_reason or runtime_mode.runtime_mode_reason or "issue_centric_fallback_required"
    route_reason_source = runtime_mode.runtime_mode_source or "runtime_mode"
    preferred_loop_action = ""
    preferred_loop_reason = ""
    mode = str(state.get("mode", "")).strip()
    generation_lifecycle = str(runtime_mode.generation_lifecycle).strip()

    if runtime_mode.runtime_mode == "issue_centric_ready":
        route_selected = "issue_centric"
        route_reason = runtime_mode.runtime_mode_reason or "issue_centric_ready"
        if generation_lifecycle == "fresh_pending":
            if str(state.get("pending_request_source", "")).strip() and mode in {
                "idle",
                "waiting_prompt_reply",
                "extended_wait",
                "await_late_completion",
            }:
                preferred_loop_action = "fetch_next_prompt"
                preferred_loop_reason = "issue_centric_fresh_pending"
        elif generation_lifecycle == "fresh_prepared":
            if mode in {"idle", "awaiting_user"} and can_reuse_prepared_request(state):
                preferred_loop_action = prepared_request_action(state)
                preferred_loop_reason = "issue_centric_fresh_prepared"

    return IssueCentricRouteChoice(
        route_selected=route_selected,
        route_reason=route_reason,
        route_reason_source=route_reason_source,
        runtime_mode=runtime_mode.runtime_mode,
        generation_lifecycle=generation_lifecycle,
        target_issue=runtime_mode.target_issue,
        target_issue_source=runtime_mode.target_issue_source,
        next_request_hint=runtime_mode.next_request_hint,
        preferred_loop_action=preferred_loop_action,
        preferred_loop_reason=preferred_loop_reason,
    )



def resolve_runtime_next_action(state: Mapping[str, Any]) -> tuple[str, str]:
    """Return (action_key, reason) with the issue-centric state view as primary authority.

    Internal dispatch step consumed by resolve_runtime_dispatch_plan().  Callers
    outside that function should use resolve_runtime_dispatch_plan() instead.

    action_key is one of:
        "prepared_request"     - fresh_prepared generation; reuse and send without rebuilding
        "pending_reply"        - fresh_pending generation; wait for reply recovery
        "need_next_generation" - issue_centric_ready but generation is consumed or unset;
                                 resolve_next_generation_transition() selects the concrete builder
        "fallback_legacy"      - degraded / unavailable / invalidated; safety fallback (legacy)
                                 route is active; resolve_fallback_legacy_transition() handles it

    Decision order follows ISSUE_CENTRIC_RUNTIME_CONTRACT:
        1. runtime snapshot
        2. runtime readiness / health gate  (runtime_mode)
        3. generation lifecycle
        4. freshness / invalidation (folded into runtime_mode by resolve_issue_centric_runtime_mode)
        5. legacy mode only when the layers above require fallback (fallback_legacy)
    """
    runtime_mode = resolve_issue_centric_runtime_mode(state, repo_root=ROOT_DIR)
    if runtime_mode is None:
        return "fallback_legacy", "issue_centric_runtime_mode_missing"

    mode_value = runtime_mode.runtime_mode
    generation_lifecycle = runtime_mode.generation_lifecycle
    fallback_reason = (
        runtime_mode.fallback_reason
        or runtime_mode.runtime_mode_reason
        or ""
    )

    # Health gate: degraded or unavailable → legacy fallback
    if mode_value in {"issue_centric_degraded_fallback", "issue_centric_unavailable"}:
        return "fallback_legacy", fallback_reason or mode_value

    if mode_value != "issue_centric_ready":
        return "fallback_legacy", fallback_reason or f"issue_centric_mode_{mode_value}"

    # Generation lifecycle drives the concrete next action
    if generation_lifecycle == "fresh_pending":
        return "pending_reply", runtime_mode.generation_lifecycle_reason or "issue_centric_fresh_pending"
    if generation_lifecycle == "fresh_prepared":
        return "prepared_request", runtime_mode.generation_lifecycle_reason or "issue_centric_fresh_prepared"
    if generation_lifecycle == "issue_centric_invalidated":
        return "fallback_legacy", runtime_mode.generation_lifecycle_reason or "issue_centric_invalidated"

    # consumed / unset / other → prepare next generation
    return (
        "need_next_generation",
        runtime_mode.generation_lifecycle_reason or f"issue_centric_lifecycle_{generation_lifecycle or 'unset'}",
    )


def is_completed_state(state: Mapping[str, Any]) -> bool:
    """Return True when the state represents a fully completed session.

    Extracted from run_until_stop.py so all transition helpers can use it.
    """
    mode = str(state.get("mode", "idle"))
    if mode == "completed":
        return True
    return (
        mode == "idle"
        and not bool(state.get("need_chatgpt_prompt"))
        and not bool(state.get("need_chatgpt_next"))
        and not bool(state.get("need_codex_run"))
    )


def is_awaiting_user_supplement(state: Mapping[str, Any]) -> bool:
    """Return True when the runtime is paused for operator-supplied supplement input.

    mode == "awaiting_user" is the compatibility signal for this sub-state.
    This helper centralises that read so operator-facing callers do not scatter
    direct mode reads when distinguishing the user-supplement sub-case from the
    standard report-request path.
    The next_action in this state is "request_prompt_from_report"; the helper
    narrows it to the branch that needs additional user input before proceeding.
    """
    return str(state.get("mode", "")).strip() == "awaiting_user"


def is_fetch_extended_wait_state(state: Mapping[str, Any]) -> bool:
    """Return True when fetch_next_prompt is in extended-wait substate.

    mode == "extended_wait" is the compatibility signal for this sub-state of
    the fetch_next_prompt action, set by fetch_next_prompt.py when the ChatGPT
    tab enters an extended-wait phase.  This helper centralises that read so
    operator-facing callers avoid scattering direct mode reads.

    Callers only see this state while action == "fetch_next_prompt" is in flight;
    the dispatch plan next_action remains "fetch_next_prompt" throughout.
    """
    return str(state.get("mode", "")).strip() == "extended_wait"


def is_fetch_late_completion_state(state: Mapping[str, Any]) -> bool:
    """Return True when fetch_next_prompt is in late-completion substate.

    mode == "await_late_completion" is the compatibility signal for this
    sub-state of the fetch_next_prompt action, set by fetch_next_prompt.py when
    the ChatGPT tab reaches the late-completion detection phase.  This helper
    centralises that read so operator-facing callers avoid scattering direct
    mode reads.

    Callers only see this state while action == "fetch_next_prompt" is in flight;
    the dispatch plan next_action remains "fetch_next_prompt" throughout.
    """
    return str(state.get("mode", "")).strip() == "await_late_completion"


def is_issue_centric_delivery_pending_state(
    state: Mapping[str, Any],
) -> tuple[bool, str]:
    """Return (True, target_issue) when issued-centric runtime has a delivery-pending substate.

    A delivery-pending substate is any of the three late-completion / handoff substates
    of fetch_next_prompt that indicate the reply has not yet been collected:
    - ``pending_request_signal == "submitted_unconfirmed"``
    - ``mode == "extended_wait"`` (is_fetch_extended_wait_state)
    - ``mode == "await_late_completion"`` (is_fetch_late_completion_state)

    Additionally the issue-centric runtime must be healthy (runtime_mode ==
    ``"issue_centric_ready"``) with a known target_issue.  Legacy / fallback paths
    always receive ``(False, "")``.

    The cheap signal/mode check runs first; the more expensive
    resolve_issue_centric_runtime_mode() call is only made when we already know
    a delivery-pending substate is active, so the hot path (no pending state) is
    inexpensive.

    Returns:
        ``(True, target_issue)``  when delivery-pending AND issue-centric healthy
        ``(False, "")``           otherwise (legacy fallback path or no delivery pending)
    """
    signal = str(state.get("pending_request_signal", "")).strip()
    is_delivery_pending = (
        signal == "submitted_unconfirmed"
        or is_fetch_extended_wait_state(state)
        or is_fetch_late_completion_state(state)
    )
    if not is_delivery_pending:
        return False, ""

    runtime_mode = resolve_issue_centric_runtime_mode(state, repo_root=ROOT_DIR)
    if runtime_mode is None:
        return False, ""
    if runtime_mode.runtime_mode != "issue_centric_ready":
        return False, ""

    target_issue = str(runtime_mode.target_issue or "").strip()
    return True, target_issue


def ic_delivery_pending_detail(
    ic_base_text: str,
    state: Mapping[str, Any],
    *,
    legacy_base_text: str | None = None,
) -> str:
    """Return a delivery-pending aware detail string for human-facing views.

    Single assembly point for the IC delivery-pending detail prefix across all
    human-facing surfaces.  When is_issue_centric_delivery_pending_state() reports
    an active IC delivery-pending substate, returns:

        f"issue-centric delivery pending ({target_issue}): {ic_base_text}"

    When delivery-pending is not active or the runtime is on the legacy/fallback path:
    - returns ``legacy_base_text`` when explicitly provided (preserves legacy wording
      that differs from ``ic_base_text``),
    - otherwise returns ``ic_base_text`` unchanged.

    This keeps the IC-aware prefix in one place so present_bridge_status(),
    suggested_next_note(), and the error-path note in run_until_stop all produce
    consistent, issue-targeted wording without repeating the inline pattern.
    """
    _ic_pending, _ic_issue = is_issue_centric_delivery_pending_state(state)
    if _ic_pending and _ic_issue:
        return f"issue-centric delivery pending ({_ic_issue}): {ic_base_text}"
    if legacy_base_text is not None:
        return legacy_base_text
    return ic_base_text


def is_normal_path_state(state: Mapping[str, Any]) -> bool:
    """Return True when the runtime is in the normal path where dispatch plan is primary.

    Normal path = NOT in Codex lifecycle compatibility branch AND no pending early-exit
    conditions (unarchived report, pending Codex dispatch) that are handled before
    the dispatch plan is consulted.

    In normal-path states, resolve_runtime_dispatch_plan(state) is the authoritative
    routing source.  Legacy request-centric helpers activate only when the returned
    plan has is_fallback=True (i.e., degraded / unavailable / invalidated).

    This is the full cutover boundary: callers that need to know whether dispatch
    plan or compatibility branches own the next step should call this helper.
    Uses resolve_codex_lifecycle_view() internally so it stays in sync with the
    single Codex lifecycle classification authority.
    """
    # Delegate to the single Codex lifecycle authority (mode classification
    # is enclosed inside resolve_codex_lifecycle_view()).
    if resolve_codex_lifecycle_view(state) is not None:
        return False
    # Early-exit conditions that bypass the dispatch plan in resolve_unified_next_action().
    if has_pending_issue_centric_codex_dispatch(state):
        return False
    # Unarchived report is handled before dispatch plan in the normal flow,
    # but it is itself an action-view action (archive_codex_report).
    # Return True so dispatch-plan-aware callers see this as normal path.
    return True


def is_blocked_codex_lifecycle_state(state: Mapping[str, Any]) -> bool:
    """Return True when the state is in a blocked Codex lifecycle branch.

    A blocked lifecycle state (currently: ready_for_codex without need_codex_run=True)
    requires operator confirmation before any dispatch can proceed.  It is distinct from
    normal-path states (where the dispatch plan is primary) and from actionable lifecycle
    states (where resolve_unified_next_action() returns a concrete action key).

    This helper exists so that orchestrator callers can detect blocked lifecycle without
    importing resolve_codex_lifecycle_view() directly.  It encapsulates the full lifecycle
    classification check (including the is_blocked flag) in a single boolean query.

    Returns False for all non-lifecycle states and for non-blocked lifecycle states.
    """
    view = resolve_codex_lifecycle_view(state)
    return view is not None and view.is_blocked


def resolve_codex_lifecycle_view(state: Mapping[str, Any]) -> "CodexLifecycleView | None":
    """Return the unified CodexLifecycleView for the current state, or None.

    This is the sole authority for Codex lifecycle compatibility classification.
    The three lifecycle modes (ready_for_codex, codex_running, codex_done) are
    named only here; no external constant or helper exposes them.  Callers
    receive a view object and never need to inspect raw mode values directly.

    Returns None when the state is not in the Codex lifecycle compatibility branch.

    External callers: NONE.  This helper is consumed only internally by:
      - is_normal_path_state() (routing gate)
      - is_blocked_codex_lifecycle_state() (blocked lifecycle flag)
      - resolve_unified_next_action() (action authority)
    All external code obtains lifecycle status via present_bridge_status() or
    action keys via resolve_unified_next_action().

    Full cutover target: once lifecycle states are action-view equivalents in the
    state machine, this helper and its 3 internal callers can be removed together.
    """
    mode = str(state.get("mode", "")).strip()
    if mode not in {"ready_for_codex", "codex_running", "codex_done"}:
        return None
    need_codex_run = bool(state.get("need_codex_run"))
    if mode == "ready_for_codex" and need_codex_run:
        return CodexLifecycleView(
            action="launch_codex_once",
            status_label="Codex実行待ち",
            status_detail="次の prompt はそろっています。bridge が Codex worker を 1 回起動します。",
            is_blocked=False,
        )
    if mode == "ready_for_codex":
        return CodexLifecycleView(
            action="check_codex_condition",
            status_label="人確認待ち",
            status_detail="Codex 実行条件を確認してください。",
            is_blocked=True,
        )
    if mode == "codex_running":
        return CodexLifecycleView(
            action="wait_for_codex_report",
            status_label="Codex実行中",
            status_detail="Codex worker の完了報告を待っています。",
            is_blocked=False,
        )
    if mode == "codex_done":
        return CodexLifecycleView(
            action="archive_codex_report",
            status_label="完了報告整理中",
            status_detail="完了報告を整理して、次の ChatGPT 依頼へつなぎます。",
            is_blocked=False,
        )
    return None  # unreachable: lifecycle modes exhausted


def resolve_next_generation_transition(state: Mapping[str, Any]) -> str:
    """Residual compatibility helper — called only from resolve_runtime_dispatch_plan().

    Fires when runtime_action == 'need_next_generation' (issue-centric is ready but the
    current generation is consumed or unset).  Uses mode as a compatibility display signal
    to select the concrete request builder; this is NOT a primary routing API.

    Callers outside resolve_runtime_dispatch_plan() should use that function instead.

    Returns one of:
        "request_next_prompt"        - idle initial request path
        "request_prompt_from_report" - awaiting_user or next-phase report path
        "fetch_next_prompt"          - reply-wait mode with pending request hash
        "completed"                  - session already finished
        "no_action"                  - no matching condition
    """
    mode = str(state.get("mode", "idle")).strip()
    if mode == "idle" and bool(state.get("need_chatgpt_prompt")):
        return "request_next_prompt"
    if mode == "awaiting_user" and str(state.get("chatgpt_decision", "")).strip() in {"human_review", "need_info"}:
        return "request_prompt_from_report"
    if mode == "idle" and bool(state.get("need_chatgpt_next")):
        return "request_prompt_from_report"
    # Reply-wait modes: if a pending request hash is present, prioritize fetching
    # over generation lifecycle.  request_next_prompt.py sets pending_request_hash
    # via save_pending_request() but does not set last_issue_centric_pending_generation_id,
    # so generation_lifecycle stays "fresh_available" and runtime_action becomes
    # "need_next_generation".  Without this branch, waiting_prompt_reply + pending hash
    # would fall through to no_action here instead of going to fetch_next_prompt.
    if mode in {"waiting_prompt_reply", "extended_wait", "await_late_completion"} and str(
        state.get("pending_request_hash", "")
    ).strip():
        return "fetch_next_prompt"
    if is_completed_state(state):
        return "completed"
    return "no_action"


def resolve_fallback_legacy_transition(state: Mapping[str, Any]) -> str:
    """Safety fallback (legacy) helper — called only from resolve_runtime_dispatch_plan().

    Fires when runtime_action == 'fallback_legacy' (plan.is_fallback == True), i.e., when
    the issue-centric runtime is degraded / unavailable / invalidated.  This is a
    mode-driven chain retained for compatibility with the legacy request-centric path.

    Do NOT call this directly from normal-path routing.  Normal-path states go through
    resolve_runtime_dispatch_plan(), which is the primary routing authority and calls
    this helper only when the safety fallback route is required.

    Codex lifecycle states (ready_for_codex, codex_running, codex_done) are NOT
    handled here.  Callers guard lifecycle states before reaching resolve_runtime_dispatch_plan():
      - resolve_unified_next_action() handles them as a higher-priority branch
      - bridge_orchestrator.run() uses is_blocked_codex_lifecycle_state() + resolve_unified_next_action()
      - summarize_run() in run_until_stop.py guards via is_normal_path_state() +
        has_pending_issue_centric_codex_dispatch() (no direct lifecycle view import)
    Passing a Codex lifecycle state here is a caller bug.

    Returns one of:
        "request_next_prompt"        - idle initial request path
        "fetch_next_prompt"          - reply waiting modes
        "request_prompt_from_report" - awaiting_user / next-phase report path
        "completed"                  - session already finished
        "no_action"                  - no matching condition (including lifecycle states
                                       that should not reach this function)
    """
    mode = str(state.get("mode", "idle")).strip()
    if mode == "idle" and bool(state.get("need_chatgpt_prompt")):
        return "request_next_prompt"
    if mode in {"waiting_prompt_reply", "extended_wait", "await_late_completion"}:
        return "fetch_next_prompt"
    if mode == "awaiting_user" and str(state.get("chatgpt_decision", "")).strip() in {"human_review", "need_info"}:
        return "request_prompt_from_report"
    if mode == "idle" and bool(state.get("need_chatgpt_next")):
        return "request_prompt_from_report"
    if is_completed_state(state):
        return "completed"
    return "no_action"


def resolve_prepared_request_transition(state: Mapping[str, Any]) -> str:
    """Return action key for the prepared_request path (fresh_prepared generation).

    When a prepared request is fresh and its builder can be determined, returns
    the concrete send action so the runner can reuse the prepared request without
    rebuilding it.  Falls back to "need_next_generation" only when the builder
    cannot be determined.

    Returns one of:
        "request_next_prompt"        - prepared source is ready_issue: / override: / initial:
        "request_prompt_from_report" - prepared source is report: / handoff: / human_review_continue:
        "need_next_generation"       - builder could not be determined; caller should treat as
                                       need_next_generation and select builder from mode instead
    """
    action = prepared_request_action(state)
    if action:
        return action
    return "need_next_generation"


def format_next_action_note(
    state: Mapping[str, Any],
    *,
    next_action: str,
    runtime_action: str = "",
    runtime_action_reason: str = "",
    route_choice: "IssueCentricRouteChoice | None" = None,
) -> str:
    """Return the human-facing dispatch note for a resolved runtime action.

    Callers prepend the status label and a separator:

        note = format_next_action_note(state, next_action=..., ...)
        print(f"{status.label}です。{note}")

    next_action is one of the action keys produced by the shared spine helpers:
        "pending_reply"              - runtime_action pass-through for fresh_pending
        "request_next_prompt"        - initial request path
        "request_prompt_from_report" - report / handoff / next-phase request path
        "fetch_next_prompt"          - reply-wait fetch path
        "completed"                  - session finished; no dispatch needed
        "no_action" / other          - no matching action; generic fallback note

    runtime_action (optional): distinguishes prepared_request context so the note
        says "prepared request を再生成せず、そのまま送信します。" instead of the
        normal generation note.
    runtime_action_reason (optional): included in lifecycle annotation notes.
    route_choice (optional): provides routing context for fetch / report notes.
    """
    if next_action == "pending_reply":
        return (
            "issue-centric preferred route で pending generation の reply 回収を優先します。"
            f" lifecycle={runtime_action_reason or 'issue_centric_fresh_pending'}"
        )
    if runtime_action == "prepared_request" and next_action in {"request_next_prompt", "request_prompt_from_report"}:
        return (
            "issue-centric preferred route で prepared request を再生成せず、そのまま送信します。"
            f" lifecycle={runtime_action_reason or 'issue_centric_fresh_prepared'}"
        )
    if next_action == "request_next_prompt":
        return (
            "通常は current ready issue の参照から最初の request を組み立てます。"
            " free-form 初回本文は override 用にだけ残しています。"
        )
    if next_action == "fetch_next_prompt":
        if route_choice is not None and route_choice.route_selected == "fallback_legacy":
            return (
                "issue-centric preferred route を今回使えないため、safety fallback (legacy) route で ChatGPT 返答を回収します。"
                f" 理由: {route_choice.route_reason or 'legacy fallback required'}."
            )
        return "ChatGPT 返答から次の prompt または停止判断を回収します。"
    if next_action == "request_prompt_from_report":
        # Use is_awaiting_user_supplement() instead of a raw mode read.
        if is_awaiting_user_supplement(state):
            return "次の ChatGPT request に添える補足入力を受けて再開します。"
        if route_choice is not None and route_choice.route_selected == "issue_centric":
            return (
                "issue-centric preferred route で、次の ChatGPT request を準備して送ります。"
                f" target_issue={route_choice.target_issue or 'unresolved'}."
            )
        if str(state.get("pending_handoff_log", "")).strip() and should_rotate_before_next_chat_request(state):
            return "次の ChatGPT request を送る前に、回収済み handoff の composer 入力確認と新チャット送信確認を再試行します。"
        route_reason = route_choice.route_reason if route_choice is not None else ""
        return (
            "issue-centric preferred route を今回使えないため、safety fallback (legacy) route で同じチャットへ次フェーズ要求を送ります。"
            f" 理由: {route_reason or 'legacy fallback required'}."
        )
    if next_action == "completed":
        return "追加の操作は不要です。"
    return "今回の 1 手はありません。必要なら state.json の詳細を確認してください。"


def resolve_runtime_dispatch_plan(state: Mapping[str, Any]) -> RuntimeDispatchPlan:
    """Return a consolidated action-view of what the runtime should do next.

    **This is the primary routing authority for all normal-path states.**
    Call is_normal_path_state(state) first if you need to confirm whether the
    runtime is in a normal-path state vs a Codex lifecycle compatibility branch.

    Returns a RuntimeDispatchPlan with:
      - runtime_action  (high-level state from resolve_runtime_next_action)
      - next_action     (concrete action key after transition helpers)
      - note            (human-facing wording from format_next_action_note)
      - route_choice    (routing context for further use)
      - is_terminal     (True for completed / no_action — no dispatch needed)
      - is_fallback     (True when runtime is degraded / unavailable / invalidated
                         and safety fallback (legacy) route is active)

    Decision order follows ISSUE_CENTRIC_RUNTIME_CONTRACT:
        runtime snapshot → health gate → generation lifecycle → transition helpers

    Callers that handled early-exit branches (unarchived report, pending Codex
    dispatch, Codex lifecycle mode) before reaching this function will never see
    those action keys in next_action.

    Safety fallback (legacy route) activates only when is_fallback=True in the
    returned plan — i.e., when the issue-centric runtime is degraded, unavailable,
    or invalidated.  In all other cases, the issue-centric preferred route owns
    the next action.
    """
    runtime_action, runtime_action_reason = resolve_runtime_next_action(state)
    route_choice = resolve_issue_centric_route_choice(state)

    # Resolve concrete next_action from transition helpers.
    if runtime_action == "pending_reply":
        next_action: str = "fetch_next_prompt"
    elif runtime_action == "prepared_request":
        next_action = resolve_prepared_request_transition(state)
        if next_action == "need_next_generation":
            # builder could not be determined; fall through to need_next_generation
            next_action = resolve_next_generation_transition(state)
    elif runtime_action == "need_next_generation":
        next_action = resolve_next_generation_transition(state)
    else:
        # fallback_legacy: degraded / unavailable / invalidated.
        next_action = resolve_fallback_legacy_transition(state)

    note = format_next_action_note(
        state,
        next_action=next_action,
        runtime_action=runtime_action,
        runtime_action_reason=runtime_action_reason,
        route_choice=route_choice,
    )
    is_terminal = next_action in {"completed", "no_action"}
    is_fallback = runtime_action == "fallback_legacy"

    return RuntimeDispatchPlan(
        runtime_action=runtime_action,
        next_action=next_action,
        note=note,
        route_choice=route_choice,
        runtime_action_reason=runtime_action_reason,
        is_terminal=is_terminal,
        is_fallback=is_fallback,
    )


def resolve_unified_next_action(state: Mapping[str, Any]) -> str:
    """Return the canonical next-action key for any runtime state.

    This is the single authoritative answer to "what action should the runtime
    take next?" covering ALL state classes:

      1. Unarchived report present (early exit):
           -> "archive_codex_report"
      2. Pending issue-centric Codex dispatch (early exit):
           -> "dispatch_issue_centric_codex_run"
      3. Codex lifecycle compatibility state (mode-driven, not dispatch-plan):
           -> lifecycle_view.action  (one of launch_codex_once /
              wait_for_codex_report / archive_codex_report)
           -> Falls through to step 4 when lifecycle_view.is_blocked (operator
              confirmation required before Codex can proceed).
      4. Normal path (is_normal_path_state is True):
           -> resolve_runtime_dispatch_plan(state).next_action

    This function is the action-view bridge between Codex lifecycle compatibility
    and the normal dispatch plan path.  Both paths answer the same question and
    return the same vocabulary of action keys.

    Callers that need richer context (note, is_fallback, route_choice, is_blocked
    wording) should call resolve_runtime_dispatch_plan() directly.
    For lifecycle state status wording, use present_bridge_status().
    """
    if should_prioritize_unarchived_report(state):
        return "archive_codex_report"

    if has_pending_issue_centric_codex_dispatch(state):
        return "dispatch_issue_centric_codex_run"

    lifecycle_view = resolve_codex_lifecycle_view(state)
    if lifecycle_view is not None and not lifecycle_view.is_blocked:
        return lifecycle_view.action

    # Normal path: dispatch plan is the sole routing authority.
    plan = resolve_runtime_dispatch_plan(state)
    return plan.next_action


def format_operator_stop_note(state: Mapping[str, Any], *, plan: RuntimeDispatchPlan) -> str:
    """Return a concise operator-facing stop note derived from the dispatch plan.

    Use this in stop summaries and guidance banners where plan.note (the dispatch
    wording) is too terse or too dispatch-specific for human readers.

    Primary vocabulary: plan.next_action, plan.runtime_action, plan.is_fallback,
    plan.is_terminal.
    mode is only consulted via is_awaiting_user_supplement() rather than read
    directly, keeping mode as a compatibility signal in the background.
    """
    # IC stop paths: surface chatgpt_decision_note rather than generic plan-based messages.
    # detect_ic_stop_path() checks in order: codex_run_stop → initial_selection_stop → human_review_needed.
    # codex_run_stop is already handled before summarize_run() calls this (plan.next_action would
    # be "dispatch_issue_centric_codex_run"), so it won't appear here; but the guard is harmless.
    _ic_path = detect_ic_stop_path(state)
    if _ic_path in {"initial_selection_stop", "human_review_needed"}:
        _ic_note = str(state.get("chatgpt_decision_note", "")).strip()
        if _ic_note:
            return _ic_note
    if plan.next_action == "completed":
        _lc = _bridge_lifecycle_sync_suffix(state)
        _pw = _build_project_sync_warning_note(state)
        return f"追加の Codex 実行・ChatGPT 依頼は不要です。{_lc}{_pw}"
    if plan.next_action == "no_action":
        _lc = _bridge_lifecycle_sync_suffix(state)
        return f"次の 1 手が見つかりません。state と doctor を確認してください。{_lc}"
    if plan.next_action == "request_next_prompt":
        _lc = _bridge_lifecycle_sync_suffix(state)
        return f"次の ChatGPT 依頼を送る新規入口へ進めます。{_lc}"
    if plan.next_action == "request_prompt_from_report":
        _lc = _bridge_lifecycle_sync_suffix(state)
        if is_awaiting_user_supplement(state):
            return f"補足入力を受けて次の ChatGPT 依頼へ進めます。{_lc}"
        if plan.is_fallback:
            return f"safety fallback (legacy) route で次の ChatGPT 依頼を送ります。{_lc}"
        return f"issue-centric route で次の ChatGPT 依頼を送ります。{_lc}"
    if plan.next_action == "fetch_next_prompt":
        _lc = _bridge_lifecycle_sync_suffix(state)
        if plan.is_fallback:
            return f"safety fallback (legacy) route で ChatGPT 返答を回収します。{_lc}"
        return f"ChatGPT 返答を回収します。{_lc}"
    # For less common next_action values, delegate to the dispatch note.
    return plan.note


def stage_prepared_request(
    state: dict[str, Any],
    *,
    request_hash: str,
    request_source: str,
    request_log: str,
    status: str = "prepared",
) -> dict[str, Any]:
    clear_pending_request_fields(state)
    clear_prepared_request_fields(state)
    state["prepared_request_hash"] = request_hash
    state["prepared_request_source"] = request_source
    state["prepared_request_log"] = request_log
    state["prepared_request_status"] = status
    return state


def promote_pending_request(
    state: dict[str, Any],
    *,
    request_hash: str,
    request_source: str,
    request_log: str,
    request_signal: str = "",
) -> dict[str, Any]:
    clear_pending_request_fields(state)
    clear_prepared_request_fields(state)
    state.update(
        {
            "mode": "waiting_prompt_reply",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "pending_request_hash": request_hash,
            "pending_request_source": request_source,
            "pending_request_log": request_log,
            "pending_request_signal": request_signal,
        }
    )
    return state


def clear_pending_handoff_fields(state: dict[str, Any]) -> dict[str, Any]:
    state["pending_handoff_hash"] = ""
    state["pending_handoff_source"] = ""
    state["pending_handoff_log"] = ""
    return state


def clear_chat_rotation_fields(state: dict[str, Any]) -> dict[str, Any]:
    state["next_request_requires_rotation"] = False
    state["next_request_rotation_reason"] = ""
    state["rotate_after_cycle"] = False
    state["rotate_after_cycle_reason"] = ""
    return state


def mark_next_request_requires_rotation(state: dict[str, Any], reason: str) -> dict[str, Any]:
    normalized_reason = reason.strip()
    state["next_request_requires_rotation"] = True
    state["next_request_rotation_reason"] = normalized_reason
    state["rotate_after_cycle"] = True
    state["rotate_after_cycle_reason"] = normalized_reason
    return state


def next_request_rotation_reason(state: Mapping[str, Any]) -> str:
    reason = str(state.get("next_request_rotation_reason", "")).strip()
    if reason:
        return reason
    return str(state.get("rotate_after_cycle_reason", "")).strip()


def should_rotate_before_next_chat_request(state: Mapping[str, Any]) -> bool:
    if "next_request_requires_rotation" in state:
        return bool(state.get("next_request_requires_rotation"))
    return bool(state.get("rotate_after_cycle"))


def should_request_chat_rotation(state: Mapping[str, Any]) -> bool:
    return should_rotate_before_next_chat_request(state)


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


_VALID_EXECUTION_AGENTS = {"codex", "github_copilot"}


def _validate_execution_agent(config: dict[str, Any]) -> None:
    raw = config.get("execution_agent", "")
    if not isinstance(raw, str):
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `execution_agent` は文字列で指定してください。"
        )
    normalized = raw.strip()
    if not normalized:
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `execution_agent` が空です。"
            f" 有効な値: {sorted(_VALID_EXECUTION_AGENTS)}"
        )
    if normalized not in _VALID_EXECUTION_AGENTS:
        raise BridgeError(
            f"{repo_relative(PROJECT_CONFIG_PATH)} の `execution_agent` に無効な値が指定されています: {normalized!r}。"
            f" 有効な値: {sorted(_VALID_EXECUTION_AGENTS)}"
        )
    config["execution_agent"] = normalized


def resolve_execution_agent(project_config: Mapping[str, Any]) -> str:
    """Return the execution agent name from project_config.

    Valid values: ``"codex"`` / ``"github_copilot"``.
    Raises :class:`BridgeError` for missing, empty, or unrecognised values.
    """
    raw = project_config.get("execution_agent", "")
    if not isinstance(raw, str):
        raise BridgeError(
            "`execution_agent` は文字列で指定してください。"
            f" 有効な値: {sorted(_VALID_EXECUTION_AGENTS)}"
        )
    normalized = raw.strip()
    if not normalized:
        raise BridgeError(
            "`execution_agent` が未設定です。"
            f" 有効な値: {sorted(_VALID_EXECUTION_AGENTS)}"
        )
    if normalized not in _VALID_EXECUTION_AGENTS:
        raise BridgeError(
            f"`execution_agent` に無効な値が指定されています: {normalized!r}。"
            f" 有効な値: {sorted(_VALID_EXECUTION_AGENTS)}"
        )
    return normalized



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
    _validate_execution_agent(config)
    _require_project_config_text(config, "agent_model", allow_empty=True)
    _require_project_config_text(config, "github_copilot_bin")
    _require_project_config_text(config, "codex_bin")
    _require_project_config_text(config, "codex_model", allow_empty=True)
    _require_project_config_text(config, "codex_sandbox", allow_empty=True)
    _require_project_config_text(config, "github_repository", allow_empty=True)
    _require_project_config_text(config, "github_project_url", allow_empty=True)
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
        f"- prepared_request_hash: {state.get('prepared_request_hash', '')}",
        f"- prepared_request_source: {state.get('prepared_request_source', '')}",
        f"- prepared_request_log: {state.get('prepared_request_log', '')}",
        f"- prepared_request_status: {state.get('prepared_request_status', '')}",
        f"- pending_request_hash: {state.get('pending_request_hash', '')}",
        f"- pending_request_source: {state.get('pending_request_source', '')}",
        f"- pending_request_log: {state.get('pending_request_log', '')}",
        f"- pending_request_signal: {state.get('pending_request_signal', '')}",
        f"- pending_handoff_hash: {state.get('pending_handoff_hash', '')}",
        f"- pending_handoff_source: {state.get('pending_handoff_source', '')}",
        f"- pending_handoff_log: {state.get('pending_handoff_log', '')}",
        f"- next_request_requires_rotation: {should_rotate_before_next_chat_request(state)}",
        f"- next_request_rotation_reason: {next_request_rotation_reason(state)}",
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
    if state.get("last_issue_centric_action"):
        fields.append(f"- last_issue_centric_action: {state['last_issue_centric_action']}")
    if state.get("last_issue_centric_target_issue"):
        fields.append(f"- last_issue_centric_target_issue: {state['last_issue_centric_target_issue']}")
    if state.get("last_issue_centric_decision_log"):
        fields.append(f"- last_issue_centric_decision_log: {state['last_issue_centric_decision_log']}")
    if state.get("last_issue_centric_metadata_log"):
        fields.append(f"- last_issue_centric_metadata_log: {state['last_issue_centric_metadata_log']}")
    if state.get("last_issue_centric_artifact_file"):
        fields.append(f"- last_issue_centric_artifact_file: {state['last_issue_centric_artifact_file']}")
    if state.get("last_issue_centric_normalized_summary"):
        fields.append(f"- last_issue_centric_normalized_summary: {state['last_issue_centric_normalized_summary']}")
    if state.get("last_issue_centric_principal_issue"):
        fields.append(f"- last_issue_centric_principal_issue: {state['last_issue_centric_principal_issue']}")
    if state.get("last_issue_centric_principal_issue_kind"):
        fields.append(
            f"- last_issue_centric_principal_issue_kind: {state['last_issue_centric_principal_issue_kind']}"
        )
    if state.get("last_issue_centric_next_request_hint"):
        fields.append(f"- last_issue_centric_next_request_hint: {state['last_issue_centric_next_request_hint']}")
    if state.get("last_issue_centric_next_request_target"):
        fields.append(f"- last_issue_centric_next_request_target: {state['last_issue_centric_next_request_target']}")
    if state.get("last_issue_centric_next_request_target_source"):
        fields.append(
            f"- last_issue_centric_next_request_target_source: {state['last_issue_centric_next_request_target_source']}"
        )
    if state.get("last_issue_centric_next_request_fallback_reason"):
        fields.append(
            f"- last_issue_centric_next_request_fallback_reason: {state['last_issue_centric_next_request_fallback_reason']}"
        )
    if state.get("last_issue_centric_route_selected"):
        fields.append(f"- last_issue_centric_route_selected: {state['last_issue_centric_route_selected']}")
    if state.get("last_issue_centric_route_fallback_reason"):
        fields.append(
            f"- last_issue_centric_route_fallback_reason: {state['last_issue_centric_route_fallback_reason']}"
        )
    if state.get("last_issue_centric_recovery_status"):
        fields.append(f"- last_issue_centric_recovery_status: {state['last_issue_centric_recovery_status']}")
    if state.get("last_issue_centric_recovery_source"):
        fields.append(f"- last_issue_centric_recovery_source: {state['last_issue_centric_recovery_source']}")
    if state.get("last_issue_centric_recovery_fallback_reason"):
        fields.append(
            f"- last_issue_centric_recovery_fallback_reason: {state['last_issue_centric_recovery_fallback_reason']}"
        )
    if state.get("last_issue_centric_runtime_snapshot"):
        fields.append(f"- last_issue_centric_runtime_snapshot: {state['last_issue_centric_runtime_snapshot']}")
    if state.get("last_issue_centric_snapshot_status"):
        fields.append(f"- last_issue_centric_snapshot_status: {state['last_issue_centric_snapshot_status']}")
    if state.get("last_issue_centric_runtime_generation_id"):
        fields.append(
            f"- last_issue_centric_runtime_generation_id: {state['last_issue_centric_runtime_generation_id']}"
        )
    if state.get("last_issue_centric_generation_lifecycle"):
        fields.append(f"- last_issue_centric_generation_lifecycle: {state['last_issue_centric_generation_lifecycle']}")
    if state.get("last_issue_centric_generation_lifecycle_reason"):
        fields.append(
            f"- last_issue_centric_generation_lifecycle_reason: {state['last_issue_centric_generation_lifecycle_reason']}"
        )
    if state.get("last_issue_centric_generation_lifecycle_source"):
        fields.append(
            f"- last_issue_centric_generation_lifecycle_source: {state['last_issue_centric_generation_lifecycle_source']}"
        )
    if state.get("last_issue_centric_prepared_generation_id"):
        fields.append(
            f"- last_issue_centric_prepared_generation_id: {state['last_issue_centric_prepared_generation_id']}"
        )
    if state.get("last_issue_centric_pending_generation_id"):
        fields.append(
            f"- last_issue_centric_pending_generation_id: {state['last_issue_centric_pending_generation_id']}"
        )
    if state.get("last_issue_centric_runtime_mode"):
        fields.append(f"- last_issue_centric_runtime_mode: {state['last_issue_centric_runtime_mode']}")
    if state.get("last_issue_centric_runtime_mode_reason"):
        fields.append(f"- last_issue_centric_runtime_mode_reason: {state['last_issue_centric_runtime_mode_reason']}")
    if state.get("last_issue_centric_runtime_mode_source"):
        fields.append(f"- last_issue_centric_runtime_mode_source: {state['last_issue_centric_runtime_mode_source']}")
    if state.get("last_issue_centric_state_view"):
        fields.append(f"- last_issue_centric_state_view: {state['last_issue_centric_state_view']}")
    if state.get("last_issue_centric_state_view_reason"):
        fields.append(f"- last_issue_centric_state_view_reason: {state['last_issue_centric_state_view_reason']}")
    if state.get("last_issue_centric_state_view_source"):
        fields.append(f"- last_issue_centric_state_view_source: {state['last_issue_centric_state_view_source']}")
    if state.get("last_issue_centric_wait_kind"):
        fields.append(f"- last_issue_centric_wait_kind: {state['last_issue_centric_wait_kind']}")
    if state.get("last_issue_centric_wait_reason"):
        fields.append(f"- last_issue_centric_wait_reason: {state['last_issue_centric_wait_reason']}")
    if state.get("last_issue_centric_freshness_status"):
        fields.append(f"- last_issue_centric_freshness_status: {state['last_issue_centric_freshness_status']}")
    if state.get("last_issue_centric_freshness_reason"):
        fields.append(f"- last_issue_centric_freshness_reason: {state['last_issue_centric_freshness_reason']}")
    if state.get("last_issue_centric_freshness_source"):
        fields.append(f"- last_issue_centric_freshness_source: {state['last_issue_centric_freshness_source']}")
    if state.get("last_issue_centric_invalidation_status"):
        fields.append(
            f"- last_issue_centric_invalidation_status: {state['last_issue_centric_invalidation_status']}"
        )
    if state.get("last_issue_centric_invalidation_reason"):
        fields.append(
            f"- last_issue_centric_invalidation_reason: {state['last_issue_centric_invalidation_reason']}"
        )
    if state.get("last_issue_centric_invalidated_generation_id"):
        fields.append(
            f"- last_issue_centric_invalidated_generation_id: {state['last_issue_centric_invalidated_generation_id']}"
        )
    if state.get("last_issue_centric_consumed_generation_id"):
        fields.append(
            f"- last_issue_centric_consumed_generation_id: {state['last_issue_centric_consumed_generation_id']}"
        )
    if state.get("last_issue_centric_artifact_kind"):
        fields.append(f"- last_issue_centric_artifact_kind: {state['last_issue_centric_artifact_kind']}")
    if state.get("last_issue_centric_execution_status"):
        fields.append(f"- last_issue_centric_execution_status: {state['last_issue_centric_execution_status']}")
    if state.get("last_issue_centric_execution_log"):
        fields.append(f"- last_issue_centric_execution_log: {state['last_issue_centric_execution_log']}")
    if state.get("last_issue_centric_created_issue_number"):
        fields.append(f"- last_issue_centric_created_issue_number: {state['last_issue_centric_created_issue_number']}")
    if state.get("last_issue_centric_created_issue_url"):
        fields.append(f"- last_issue_centric_created_issue_url: {state['last_issue_centric_created_issue_url']}")
    if state.get("last_issue_centric_created_issue_title"):
        fields.append(f"- last_issue_centric_created_issue_title: {state['last_issue_centric_created_issue_title']}")
    if state.get("last_issue_centric_primary_issue_number"):
        fields.append(f"- last_issue_centric_primary_issue_number: {state['last_issue_centric_primary_issue_number']}")
    if state.get("last_issue_centric_primary_issue_url"):
        fields.append(f"- last_issue_centric_primary_issue_url: {state['last_issue_centric_primary_issue_url']}")
    if state.get("last_issue_centric_primary_issue_title"):
        fields.append(f"- last_issue_centric_primary_issue_title: {state['last_issue_centric_primary_issue_title']}")
    if state.get("last_issue_centric_resolved_issue"):
        fields.append(f"- last_issue_centric_resolved_issue: {state['last_issue_centric_resolved_issue']}")
    if state.get("last_issue_centric_trigger_comment_id"):
        fields.append(f"- last_issue_centric_trigger_comment_id: {state['last_issue_centric_trigger_comment_id']}")
    if state.get("last_issue_centric_trigger_comment_url"):
        fields.append(f"- last_issue_centric_trigger_comment_url: {state['last_issue_centric_trigger_comment_url']}")
    if state.get("last_issue_centric_execution_payload_log"):
        fields.append(f"- last_issue_centric_execution_payload_log: {state['last_issue_centric_execution_payload_log']}")
    if state.get("last_issue_centric_launch_status"):
        fields.append(f"- last_issue_centric_launch_status: {state['last_issue_centric_launch_status']}")
    if state.get("last_issue_centric_launch_entrypoint"):
        fields.append(f"- last_issue_centric_launch_entrypoint: {state['last_issue_centric_launch_entrypoint']}")
    if state.get("last_issue_centric_launch_prompt_log"):
        fields.append(f"- last_issue_centric_launch_prompt_log: {state['last_issue_centric_launch_prompt_log']}")
    if state.get("last_issue_centric_launch_log"):
        fields.append(f"- last_issue_centric_launch_log: {state['last_issue_centric_launch_log']}")
    if state.get("last_issue_centric_continuation_status"):
        fields.append(f"- last_issue_centric_continuation_status: {state['last_issue_centric_continuation_status']}")
    if state.get("last_issue_centric_continuation_log"):
        fields.append(f"- last_issue_centric_continuation_log: {state['last_issue_centric_continuation_log']}")
    if state.get("last_issue_centric_report_status"):
        fields.append(f"- last_issue_centric_report_status: {state['last_issue_centric_report_status']}")
    if state.get("last_issue_centric_report_file"):
        fields.append(f"- last_issue_centric_report_file: {state['last_issue_centric_report_file']}")
    if state.get("last_issue_centric_project_sync_status"):
        fields.append(f"- last_issue_centric_project_sync_status: {state['last_issue_centric_project_sync_status']}")
    if state.get("last_issue_centric_project_url"):
        fields.append(f"- last_issue_centric_project_url: {state['last_issue_centric_project_url']}")
    if state.get("last_issue_centric_project_item_id"):
        fields.append(f"- last_issue_centric_project_item_id: {state['last_issue_centric_project_item_id']}")
    if state.get("last_issue_centric_project_state_field"):
        fields.append(f"- last_issue_centric_project_state_field: {state['last_issue_centric_project_state_field']}")
    if state.get("last_issue_centric_project_state_value"):
        fields.append(f"- last_issue_centric_project_state_value: {state['last_issue_centric_project_state_value']}")
    if state.get("last_issue_centric_primary_project_sync_status"):
        fields.append(f"- last_issue_centric_primary_project_sync_status: {state['last_issue_centric_primary_project_sync_status']}")
    if state.get("last_issue_centric_primary_project_url"):
        fields.append(f"- last_issue_centric_primary_project_url: {state['last_issue_centric_primary_project_url']}")
    if state.get("last_issue_centric_primary_project_item_id"):
        fields.append(f"- last_issue_centric_primary_project_item_id: {state['last_issue_centric_primary_project_item_id']}")
    if state.get("last_issue_centric_primary_project_state_field"):
        fields.append(f"- last_issue_centric_primary_project_state_field: {state['last_issue_centric_primary_project_state_field']}")
    if state.get("last_issue_centric_primary_project_state_value"):
        fields.append(f"- last_issue_centric_primary_project_state_value: {state['last_issue_centric_primary_project_state_value']}")
    if state.get("last_issue_centric_followup_issue_number"):
        fields.append(f"- last_issue_centric_followup_issue_number: {state['last_issue_centric_followup_issue_number']}")
    if state.get("last_issue_centric_followup_issue_url"):
        fields.append(f"- last_issue_centric_followup_issue_url: {state['last_issue_centric_followup_issue_url']}")
    if state.get("last_issue_centric_followup_issue_title"):
        fields.append(f"- last_issue_centric_followup_issue_title: {state['last_issue_centric_followup_issue_title']}")
    if state.get("last_issue_centric_followup_project_sync_status"):
        fields.append(f"- last_issue_centric_followup_project_sync_status: {state['last_issue_centric_followup_project_sync_status']}")
    if state.get("last_issue_centric_followup_project_url"):
        fields.append(f"- last_issue_centric_followup_project_url: {state['last_issue_centric_followup_project_url']}")
    if state.get("last_issue_centric_followup_project_item_id"):
        fields.append(f"- last_issue_centric_followup_project_item_id: {state['last_issue_centric_followup_project_item_id']}")
    if state.get("last_issue_centric_followup_project_state_field"):
        fields.append(f"- last_issue_centric_followup_project_state_field: {state['last_issue_centric_followup_project_state_field']}")
    if state.get("last_issue_centric_followup_project_state_value"):
        fields.append(f"- last_issue_centric_followup_project_state_value: {state['last_issue_centric_followup_project_state_value']}")
    if state.get("last_issue_centric_current_project_item_id"):
        fields.append(f"- last_issue_centric_current_project_item_id: {state['last_issue_centric_current_project_item_id']}")
    if state.get("last_issue_centric_current_project_url"):
        fields.append(f"- last_issue_centric_current_project_url: {state['last_issue_centric_current_project_url']}")
    if state.get("last_issue_centric_lifecycle_sync_status"):
        fields.append(f"- last_issue_centric_lifecycle_sync_status: {state['last_issue_centric_lifecycle_sync_status']}")
    if state.get("last_issue_centric_lifecycle_sync_log"):
        fields.append(f"- last_issue_centric_lifecycle_sync_log: {state['last_issue_centric_lifecycle_sync_log']}")
    if state.get("last_issue_centric_lifecycle_sync_issue"):
        fields.append(f"- last_issue_centric_lifecycle_sync_issue: {state['last_issue_centric_lifecycle_sync_issue']}")
    if state.get("last_issue_centric_lifecycle_sync_stage"):
        fields.append(f"- last_issue_centric_lifecycle_sync_stage: {state['last_issue_centric_lifecycle_sync_stage']}")
    if state.get("last_issue_centric_lifecycle_sync_project_url"):
        fields.append(f"- last_issue_centric_lifecycle_sync_project_url: {state['last_issue_centric_lifecycle_sync_project_url']}")
    if state.get("last_issue_centric_lifecycle_sync_project_item_id"):
        fields.append(f"- last_issue_centric_lifecycle_sync_project_item_id: {state['last_issue_centric_lifecycle_sync_project_item_id']}")
    if state.get("last_issue_centric_lifecycle_sync_state_field"):
        fields.append(f"- last_issue_centric_lifecycle_sync_state_field: {state['last_issue_centric_lifecycle_sync_state_field']}")
    if state.get("last_issue_centric_lifecycle_sync_state_value"):
        fields.append(f"- last_issue_centric_lifecycle_sync_state_value: {state['last_issue_centric_lifecycle_sync_state_value']}")
    if state.get("last_issue_centric_close_status"):
        fields.append(f"- last_issue_centric_close_status: {state['last_issue_centric_close_status']}")
    if state.get("last_issue_centric_close_log"):
        fields.append(f"- last_issue_centric_close_log: {state['last_issue_centric_close_log']}")
    if state.get("last_issue_centric_closed_issue_number"):
        fields.append(f"- last_issue_centric_closed_issue_number: {state['last_issue_centric_closed_issue_number']}")
    if state.get("last_issue_centric_closed_issue_url"):
        fields.append(f"- last_issue_centric_closed_issue_url: {state['last_issue_centric_closed_issue_url']}")
    if state.get("last_issue_centric_closed_issue_title"):
        fields.append(f"- last_issue_centric_closed_issue_title: {state['last_issue_centric_closed_issue_title']}")
    if state.get("last_issue_centric_close_order"):
        fields.append(f"- last_issue_centric_close_order: {state['last_issue_centric_close_order']}")
    if state.get("last_issue_centric_review_status"):
        fields.append(f"- last_issue_centric_review_status: {state['last_issue_centric_review_status']}")
    if state.get("last_issue_centric_review_log"):
        fields.append(f"- last_issue_centric_review_log: {state['last_issue_centric_review_log']}")
    if state.get("last_issue_centric_review_comment_id"):
        fields.append(f"- last_issue_centric_review_comment_id: {state['last_issue_centric_review_comment_id']}")
    if state.get("last_issue_centric_review_comment_url"):
        fields.append(f"- last_issue_centric_review_comment_url: {state['last_issue_centric_review_comment_url']}")
    if state.get("last_issue_centric_review_close_policy"):
        fields.append(f"- last_issue_centric_review_close_policy: {state['last_issue_centric_review_close_policy']}")
    if state.get("last_issue_centric_stop_reason"):
        fields.append(f"- last_issue_centric_stop_reason: {state['last_issue_centric_stop_reason']}")
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

    def candidate_paths_from_log(log_candidate: Path) -> list[Path]:
        try:
            raw_text = read_text(log_candidate)
        except UnicodeDecodeError:
            print(
                "[note] report recovery skipped unreadable historical log (non-UTF-8):"
                f" {repo_relative(log_candidate)}",
                flush=True,
            )
            return []
        return _candidate_report_paths_from_text(raw_text)

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
        for candidate in candidate_paths_from_log(log_candidate):
            add_candidate(candidate)

    if search_recent_logs:
        for log_candidate in _recent_codex_log_paths():
            if newer_than is not None and log_candidate.stat().st_mtime < newer_than:
                continue
            for candidate in candidate_paths_from_log(log_candidate):
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

    should_promote = should_prioritize_unarchived_report(current_state)
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
    if should_prioritize_unarchived_report(current_state):
        return current_state, False
    if not should_rotate_before_next_chat_request(current_state):
        return current_state, False
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


def should_prioritize_unarchived_report(state: Mapping[str, Any]) -> bool:
    if not codex_report_is_ready(runtime_report_path()):
        return False

    mode = str(state.get("mode", "")).strip()
    if mode in {"waiting_prompt_reply", "extended_wait", "await_late_completion"}:
        return False

    if bool(state.get("error")):
        return True
    if mode in {"ready_for_codex", "codex_running", "codex_done"}:
        return True
    if mode == "idle":
        return True
    if mode == "awaiting_user":
        return True
    return False


def recover_prepared_request_state(state: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    current_state = dict(state)
    pending_source = str(current_state.get("pending_request_source", "")).strip()
    prepared_hash = str(current_state.get("prepared_request_hash", "")).strip()
    prepared_source = str(current_state.get("prepared_request_source", "")).strip()
    prepared_log = str(current_state.get("prepared_request_log", "")).strip()
    prepared_status = str(current_state.get("prepared_request_status", "")).strip()
    prepared_generation_id = str(current_state.get("last_issue_centric_prepared_generation_id", "")).strip()

    if pending_source:
        return current_state, False
    if not (prepared_hash and prepared_source and prepared_log):
        return current_state, False
    if prepared_status != "prepared":
        return current_state, False

    updated = clear_error_fields(dict(current_state))
    if prepared_generation_id:
        updated.update(
            {
                "last_issue_centric_generation_lifecycle": "fresh_prepared",
                "last_issue_centric_generation_lifecycle_reason": "prepared_request_recovered_without_send",
                "last_issue_centric_generation_lifecycle_source": "recover_prepared_request_state",
                "last_issue_centric_pending_generation_id": "",
                "last_issue_centric_prepared_generation_id": prepared_generation_id,
                "last_issue_centric_freshness_status": "issue_centric_fresh",
                "last_issue_centric_freshness_reason": "prepared_request_bound_to_generation",
                "last_issue_centric_freshness_source": "prepared_request_state",
                "last_issue_centric_consumed_generation_id": "",
            }
        )
    if updated != current_state:
        save_state(updated)
    return updated, True


def is_retryable_pending_handoff_error(state: Mapping[str, Any]) -> bool:
    if not should_rotate_before_next_chat_request(state):
        return False
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


def _project_page_url_matches(url: str, config: Mapping[str, Any]) -> bool:
    normalized = url.rstrip("/")
    explicit = str(config.get("project_page_url", "")).strip().rstrip("/")
    if explicit and normalized == explicit:
        return True
    return normalized.endswith("/project")


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


def _build_composer_lookup_script(*, preferred_hint: str | None = None, project_page_mode: bool = False) -> str:
    return f"""
  const composerLookup = (() => {{
  const selectors = {json.dumps(COMPOSER_SELECTORS, ensure_ascii=False)};
  const preferredHint = {json.dumps((preferred_hint or "").strip(), ensure_ascii=False)};
  const projectPageMode = {json.dumps(project_page_mode)};
  const projectComposerHints = {json.dumps(PROJECT_CHAT_COMPOSER_HINTS, ensure_ascii=False)};
  const projectNameSelectors = {json.dumps(PROJECT_NAME_HEADING_SELECTORS, ensure_ascii=False)};
  const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
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
  const collectProjectNames = () => {{
    const results = [];
    const seen = new Set();
    const push = (value, source) => {{
      const text = normalize(value);
      if (!text || seen.has(text)) return;
      seen.add(text);
      results.push({{text, source}});
    }};
    push((document.title || "").replace(/^ChatGPT\\s*[-:|｜]\\s*/, ""), "document.title");
    for (const selector of projectNameSelectors) {{
      const nodes = Array.from(document.querySelectorAll(selector));
      for (const node of nodes) {{
        if (!isVisible(node)) continue;
        const text = normalize(node.innerText || node.textContent || "");
        if (text) push(text, selector);
      }}
    }}
    return results;
  }};
  const projectNames = collectProjectNames();
  const buildCandidate = (node) => {{
    const container = node.closest?.("form, section, main, div") || null;
    const values = [
      {{label: "placeholder", text: normalize(node.getAttribute?.("placeholder") || "")}},
      {{label: "aria-label", text: normalize(node.getAttribute?.("aria-label") || "")}},
      {{label: "data-placeholder", text: normalize(node.getAttribute?.("data-placeholder") || "")}},
      {{label: "container", text: normalize((container?.innerText || "").slice(0, 240))}},
    ].filter((entry) => entry.text);
    let matchKind = "";
    let matchedHint = "";
    let matchedPreferredHint = false;
    let projectHintDetected = false;
    let matchedProjectName = "";
    const includesText = (needle) => values.some((entry) => entry.text.includes(needle));
    const normalizedPreferredHint = normalize(preferredHint);
    if (normalizedPreferredHint && includesText(normalizedPreferredHint)) {{
      matchKind = "preferred_hint";
      matchedHint = normalizedPreferredHint;
      matchedPreferredHint = true;
    }}
    if (!matchKind && projectPageMode) {{
      for (const projectName of projectNames) {{
        const specificHint = normalize(`${{projectName.text}} 内の新しいチャット`);
        if (specificHint && includesText(specificHint)) {{
          matchKind = "project_title";
          matchedHint = specificHint;
          matchedProjectName = projectName.text;
          break;
        }}
      }}
      for (const hint of projectComposerHints) {{
        const normalizedHint = normalize(hint);
        if (normalizedHint && includesText(normalizedHint)) {{
          projectHintDetected = true;
          if (!matchKind) {{
            matchKind = "project_hint";
            matchedHint = normalizedHint;
          }}
          break;
        }}
      }}
      if (!matchKind && container && container.closest?.("main")) {{
        matchKind = "project_structure";
        matchedHint = "main_structure";
      }}
    }}
    return {{
      values,
      matchKind,
      matchedHint,
      matchedPreferredHint,
      matchedProjectName,
      projectHintDetected,
      rectTop: Math.round(node.getBoundingClientRect().top),
      tagName: (node.tagName || "").toLowerCase(),
      inMain: !!container?.closest?.("main"),
    }};
  }};
  let composer = null;
  let composerMeta = null;
  let fallbackComposer = null;
  let fallbackMeta = null;
  const candidateHints = [];
  const scoreFor = (meta) => {{
    if (meta.matchKind === "preferred_hint") return 500;
    if (meta.matchKind === "project_title") return 400;
    if (meta.matchKind === "project_hint") return 300;
    if (meta.matchKind === "project_structure") return 200;
    return 0;
  }};
  let bestScore = -1;
  for (const selector of selectors) {{
    const nodes = Array.from(document.querySelectorAll(selector));
    for (let index = nodes.length - 1; index >= 0; index -= 1) {{
      const node = nodes[index];
      if (isVisible(node)) {{
        const meta = buildCandidate(node);
        const summary = meta.values.map((entry) => `${{entry.label}}=${{entry.text}}`).join(" | ");
        candidateHints.push(summary);
        if (!fallbackComposer) {{
          fallbackComposer = node;
          fallbackMeta = meta;
        }}
        const score = scoreFor(meta);
        if (score > bestScore || (score === bestScore && meta.rectTop < (composerMeta?.rectTop ?? 999999))) {{
          if (score > 0) {{
            composer = node;
            composerMeta = meta;
            bestScore = score;
          }}
        }}
      }}
    }}
  }}
  if (!composer && !projectPageMode && fallbackComposer) {{
    composer = fallbackComposer;
    composerMeta = fallbackMeta;
  }}
  if (!composer && projectPageMode && fallbackComposer && candidateHints.length === 1) {{
    composer = fallbackComposer;
    composerMeta = {{
      ...fallbackMeta,
      matchKind: fallbackMeta?.matchKind || "single_visible_composer",
      matchedHint: fallbackMeta?.matchedHint || "single_visible_composer",
    }};
  }}
  const tagName = (composer?.tagName || "").toLowerCase();
  let currentText = "";
  if (tagName === "textarea" && composer) {{
    currentText = (composer.value || "").trim();
  }} else if (composer && composer.isContentEditable) {{
    currentText = (composer.innerText || composer.textContent || "").trim();
  }}
  const payload = {{
    found: !!composer,
    matchedPreferredHint: !!composerMeta?.matchedPreferredHint,
    matchKind: composerMeta?.matchKind || "",
    matchedHint: composerMeta?.matchedHint || "",
    matchedProjectName: composerMeta?.matchedProjectName || "",
    projectHintDetected: !!composerMeta?.projectHintDetected || candidateHints.some((text) => text.includes("内の新しいチャット")),
    projectName: projectNames[0]?.text || "",
    projectNameSource: projectNames[0]?.source || "",
    candidateHints: candidateHints.slice(0, 5),
    visibleComposerCount: candidateHints.length,
    tagName,
    inMain: !!composerMeta?.inMain,
    isContentEditable: !!composer?.isContentEditable,
    currentText,
  }};
  return {{composer, payload}};
  }})();
  const composer = composerLookup.composer;
  const payload = composerLookup.payload;
"""


def _build_composer_state_script(preferred_hint: str | None = None, *, project_page_mode: bool = False) -> str:
    return f"""
(() => {{
  {_build_composer_lookup_script(preferred_hint=preferred_hint, project_page_mode=project_page_mode)}
  return JSON.stringify(payload);
}})();
"""


def _build_fill_composer_script(text: str, preferred_hint: str | None = None, *, project_page_mode: bool = False) -> str:
    return f"""
(() => {{
  const text = {json.dumps(text, ensure_ascii=False)};
  {_build_composer_lookup_script(preferred_hint=preferred_hint, project_page_mode=project_page_mode)}
  if (!composer) return JSON.stringify({{
    ok: false,
    reason: "composer_missing",
    projectName: payload.projectName || "",
    projectNameSource: payload.projectNameSource || "",
    matchKind: payload.matchKind || "",
    matchedHint: payload.matchedHint || "",
    projectHintDetected: !!payload.projectHintDetected,
    candidateHints: payload.candidateHints || [],
  }});
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


def _build_post_send_state_script(expected_excerpt: str, preferred_hint: str | None = None, *, project_page_mode: bool = False) -> str:
    return f"""
(() => {{
  const expectedExcerpt = {json.dumps(expected_excerpt, ensure_ascii=False)};
  {_build_composer_lookup_script(preferred_hint=preferred_hint, project_page_mode=project_page_mode)}
  const readComposerText = (composer) => {{
    if (!composer) return "";
    const tagName = (composer.tagName || "").toLowerCase();
    if (tagName === "textarea") return (composer.value || "").trim();
    if (composer.isContentEditable) return (composer.innerText || composer.textContent || "").trim();
    return "";
  }};
  const composerText = readComposerText(composer);
  const bodyText = normalize(
    document.querySelector("main")?.innerText ||
    document.querySelector("article")?.innerText ||
    document.body?.innerText ||
    ""
  );
  const normalizedExcerpt = normalize(expectedExcerpt);
  return JSON.stringify({{
    composerFound: !!composer,
    composerText,
    composerEmpty: normalize(composerText) === "",
    bodyContainsExpected: normalizedExcerpt ? bodyText.includes(normalizedExcerpt) : false,
    url: window.location.href,
    matchKind: payload.matchKind || "",
    matchedHint: payload.matchedHint || "",
    projectName: payload.projectName || "",
    candidateHints: payload.candidateHints || [],
  }});
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


def _evaluate_json_unchecked(page: SafariChatPage, script: str, failure_label: str) -> dict[str, Any]:
    raw = page.evaluate_unchecked(script).strip()
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


def _find_composer_state(
    page: SafariChatPage,
    *,
    preferred_hint: str | None = None,
    project_page_mode: bool = False,
) -> dict[str, Any]:
    return _evaluate_json(
        page,
        _build_composer_state_script(preferred_hint=preferred_hint, project_page_mode=project_page_mode),
        "ChatGPT 入力欄の確認に失敗しました",
    )


def _normalize_dom_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _expected_excerpt(text: str, *, max_chars: int = 160) -> str:
    return _normalize_dom_text(text)[:max_chars]


def _composer_text_matches(actual_text: str, expected_text: str) -> bool:
    actual = _normalize_dom_text(actual_text)
    expected = _normalize_dom_text(expected_text)
    if not expected:
        return True
    if actual == expected:
        return True
    excerpt = expected[: min(len(expected), 160)]
    return bool(excerpt) and excerpt in actual


def _composer_candidate_summary(payload: Mapping[str, Any]) -> str:
    candidates = [str(item).strip() for item in payload.get("candidateHints", []) if str(item).strip()]
    if not candidates:
        return "candidate_hints=none"
    return "candidate_hints=" + " || ".join(candidates[:3])


def _log_composer_probe(prefix: str, payload: Mapping[str, Any]) -> Path:
    snapshot = {
        "found": bool(payload.get("found")),
        "projectName": str(payload.get("projectName", "")),
        "projectNameSource": str(payload.get("projectNameSource", "")),
        "matchKind": str(payload.get("matchKind", "")),
        "matchedHint": str(payload.get("matchedHint", "")),
        "matchedPreferredHint": bool(payload.get("matchedPreferredHint")),
        "projectHintDetected": bool(payload.get("projectHintDetected")),
        "visibleComposerCount": int(payload.get("visibleComposerCount", 0) or 0),
        "candidateHints": list(payload.get("candidateHints", [])),
    }
    return log_text(prefix, json.dumps(snapshot, ensure_ascii=False, indent=2))


@dataclass(frozen=True)
class ProjectPageGithubSourcePreflightResult:
    status: str
    boundary: str
    detail: str


@dataclass(frozen=True)
class ProjectPageGithubSourceAttachOutcome:
    status: str
    boundary: str
    detail: str
    context: str
    attempted: bool
    continued_without_github_source: bool
    probe_log: str = ""
    raw_dump: str = ""
    final_confirmation_kind: str = ""


class ProjectPageGithubSourcePreflightError(BridgeError):
    def __init__(
        self,
        message: str,
        *,
        result: ProjectPageGithubSourcePreflightResult,
        payload: Mapping[str, Any],
        probe_path: Path | None,
        dump_path: Path | None,
    ) -> None:
        super().__init__(message)
        self.result = result
        self.payload = dict(payload)
        self.probe_path = probe_path
        self.dump_path = dump_path


def classify_project_page_github_source_preflight(
    payload: Mapping[str, Any],
) -> ProjectPageGithubSourcePreflightResult:
    connector_submenu_detected = _connector_submenu_detected(payload)
    connector_labels = _connector_submenu_labels(payload)
    submenu_classifier_reason = str(payload.get("submenuClassifierReason", "")).strip()
    final_confirmation_kind = str(payload.get("finalAttachConfirmationKind", "")).strip()
    if not bool(payload.get("composerFound")):
        return ProjectPageGithubSourcePreflightResult(
            status="probe_failed",
            boundary="composer_missing",
            detail="brand-new chat composer を特定できませんでした。",
        )
    if not bool(payload.get("plusFound")):
        return ProjectPageGithubSourcePreflightResult(
            status="probe_failed",
            boundary="composer_plus_missing",
            detail="composer の `＋` ボタンが見つかりませんでした。",
        )
    if not bool(payload.get("plusClicked")):
        return ProjectPageGithubSourcePreflightResult(
            status="probe_failed",
            boundary="composer_plus_click_failed",
            detail="composer の `＋` ボタンをクリックできませんでした。",
        )
    if not bool(payload.get("menuOpened")):
        return ProjectPageGithubSourcePreflightResult(
            status="probe_failed",
            boundary="composer_menu_not_open",
            detail="composer の `＋` を押した後にメニューを確認できませんでした。",
        )
    if not bool(payload.get("moreFound")):
        return ProjectPageGithubSourcePreflightResult(
            status="probe_failed",
            boundary="composer_more_missing",
            detail="composer のメニューは見えましたが `さらに表示` が見つかりませんでした。",
        )
    if not bool(payload.get("moreActionPerformed") or payload.get("moreClicked")):
        return ProjectPageGithubSourcePreflightResult(
            status="probe_failed",
            boundary="composer_more_click_failed",
            detail="`さらに表示` を開く操作を実行できませんでした。",
        )
    if not connector_submenu_detected:
        detail = "`さらに表示` を押した後の connector submenu を確認できませんでした。"
        if submenu_classifier_reason:
            detail += f" classifier={submenu_classifier_reason}."
        return ProjectPageGithubSourcePreflightResult(
            status="probe_failed",
            boundary="composer_more_submenu_not_open",
            detail=detail,
        )
    if not bool(payload.get("githubFound")):
        detail = "connector submenu までは到達しましたが、`GitHub` は見つかりませんでした。"
        if connector_labels:
            detail += f" connector_labels={','.join(connector_labels[:5])}."
        return ProjectPageGithubSourcePreflightResult(
            status="unavailable",
            boundary="github_item_missing",
            detail=detail,
        )
    if not bool(payload.get("githubClicked")):
        return ProjectPageGithubSourcePreflightResult(
            status="probe_failed",
            boundary="github_click_failed",
            detail="`GitHub` は見つかりましたがクリックできませんでした。",
        )
    if bool(payload.get("githubPillConfirmed")):
        detail = "`GitHub` pill が composer 上に追加されたことを確認できました。"
        if final_confirmation_kind == "github_pill_remove_button":
            detail = "`GitHub：クリックして削除` remove button で composer 上の GitHub pill を確認できました。"
        return ProjectPageGithubSourcePreflightResult(
            status="available",
            boundary="github_pill_confirmed",
            detail=detail,
        )
    return ProjectPageGithubSourcePreflightResult(
        status="probe_failed",
        boundary="github_click_unconfirmed",
        detail="`GitHub` menu item は押しましたが、composer 上の GitHub pill を確認できませんでした。",
    )


def _build_project_page_github_source_probe_script(
    *,
    preferred_hint: str | None = None,
    action: str = "probe",
) -> str:
    return f"""
(() => {{
  {_build_composer_lookup_script(preferred_hint=preferred_hint, project_page_mode=True)}
  const action = {json.dumps(action)};
  const plusLabels = {json.dumps(list(PROJECT_CHAT_PLUS_BUTTON_LABELS), ensure_ascii=False)};
  const plusSelectors = {json.dumps(list(PROJECT_CHAT_PLUS_BUTTON_SELECTORS), ensure_ascii=False)};
  const moreLabels = {json.dumps(list(PROJECT_CHAT_MORE_LABELS), ensure_ascii=False)};
  const addSourceLabels = {json.dumps(list(PROJECT_CHAT_ADD_SOURCE_LABELS), ensure_ascii=False)};
  const githubLabels = {json.dumps(list(PROJECT_CHAT_GITHUB_LABELS), ensure_ascii=False)};
  const githubPillRemoveLabels = {json.dumps(list(PROJECT_CHAT_GITHUB_PILL_REMOVE_LABELS), ensure_ascii=False)};
  const connectorTargetLabels = [...addSourceLabels, ...githubLabels];
  const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
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
  const composerRect = composer?.getBoundingClientRect?.() || null;
  const composerScope = composer?.closest?.("form") || composer?.closest?.("section") || composer?.closest?.("main") || document.querySelector("main") || document.body;
  const interactiveSelector = "button,[role='button'],[role='menuitem'],[role='menuitemradio'],[role='option'],a";
  const menuRoles = new Set(["menuitem", "menuitemradio", "option"]);
  const dedupe = (values) => {{
    const out = [];
    const seen = new Set();
    for (const value of values) {{
      const normalized = normalize(value);
      if (!normalized || seen.has(normalized)) continue;
      seen.add(normalized);
      out.push(normalized);
    }}
    return out;
  }};
  const labelsFor = (node) => dedupe([
    node?.innerText,
    node?.textContent,
    node?.getAttribute?.("aria-label"),
    node?.getAttribute?.("title"),
    node?.getAttribute?.("data-testid"),
  ]);
  const toEntry = (node) => {{
    const labels = labelsFor(node);
    return {{
      node,
      labels,
      text: labels[0] || "",
      ariaLabel: normalize(node?.getAttribute?.("aria-label") || ""),
      title: normalize(node?.getAttribute?.("title") || ""),
      role: normalize(node?.getAttribute?.("role") || ""),
      parentRole: normalize(node?.parentElement?.getAttribute?.("role") || ""),
      testId: normalize(node?.getAttribute?.("data-testid") || ""),
      selected: normalize(node?.getAttribute?.("aria-selected") || ""),
      checked: normalize(node?.getAttribute?.("aria-checked") || ""),
      pressed: normalize(node?.getAttribute?.("aria-pressed") || ""),
      current: normalize(node?.getAttribute?.("aria-current") || ""),
      state: normalize(node?.getAttribute?.("data-state") || ""),
      rectTop: Math.round(node?.getBoundingClientRect?.().top || 0),
      rectLeft: Math.round(node?.getBoundingClientRect?.().left || 0),
      controls: normalize(node?.getAttribute?.("aria-controls") || ""),
      hasSubmenu: node?.hasAttribute?.("data-has-submenu") || normalize(node?.getAttribute?.("aria-haspopup") || "") === "menu",
    }};
  }};
  const collectEntries = (root) => Array.from(root.querySelectorAll(interactiveSelector)).filter(isVisible).map(toEntry);
  const scopedEntries = () => collectEntries(composerScope);
  const globalEntries = () => collectEntries(document);
  const distanceScore = (entry) => {{
    if (!composerRect) return 999999;
    const dx = Math.abs((entry.rectLeft || 0) - composerRect.left);
    const dy = Math.abs((entry.rectTop || 0) - composerRect.top);
    return dx + dy;
  }};
  const uniqueEntriesByNode = (entries) => {{
    const out = [];
    const seen = new Set();
    for (const entry of entries) {{
      const node = entry?.node;
      if (!node || seen.has(node)) continue;
      seen.add(node);
      out.push(entry);
    }}
    return out;
  }};
  const matchingEntries = (labels, entries) => uniqueEntriesByNode(
    entries
      .filter((entry) => labels.some((label) => entry.labels.includes(label)))
      .sort((left, right) => distanceScore(left) - distanceScore(right))
  );
  const pickEntry = (labels, entries, options = {{}}) => {{
    const preferScoped = !!options.preferScoped;
    if (preferScoped) {{
      const scoped = matchingEntries(labels, scopedEntries())[0] || null;
      if (scoped) return scoped;
    }}
    return matchingEntries(labels, entries)[0] || null;
  }};
  const isTargetEntry = (entry, labels) => labels.some((label) => entry.labels.includes(label));
  const nearComposer = (entry) => {{
    if (!composerRect) return true;
    return Math.abs((entry.rectTop || 0) - composerRect.top) <= 520;
  }};
  const contextualMenuEntries = (entries) => entries
    .filter((entry) => nearComposer(entry) && (
      menuRoles.has(entry.role) ||
      menuRoles.has(entry.parentRole) ||
      isTargetEntry(entry, moreLabels)
    ))
    .sort((left, right) => distanceScore(left) - distanceScore(right));
  const renderEntries = (entries) => entries.map((entry) => {{
    return {{
      text: entry.text,
      ariaLabel: entry.ariaLabel,
      title: entry.title,
      role: entry.role,
      parentRole: entry.parentRole,
      testId: entry.testId,
      selected: entry.selected,
      checked: entry.checked,
      pressed: entry.pressed,
      current: entry.current,
      state: entry.state,
      controls: entry.controls,
      hasSubmenu: entry.hasSubmenu,
    }};
  }});
  const renderLabels = (entries) => dedupe(entries.flatMap((entry) => entry.labels));
  const nonMenuVisibleLabels = (entries) => renderLabels(
    entries.filter((entry) => !menuRoles.has(entry.role) && !menuRoles.has(entry.parentRole))
  );
  const isInsideMenu = (node) => {{
    const directRole = normalize(node?.getAttribute?.("role") || "");
    if (menuRoles.has(directRole)) return true;
    return !!node?.closest?.("[role='menu']");
  }};
  const exactVisibleSelectorEntries = (selectors, root) => {{
    const entries = [];
    if (!root) return entries;
    for (const selector of selectors) {{
      try {{
        const node = root.querySelector(selector);
        if (node && isVisible(node)) entries.push(toEntry(node));
      }} catch (error) {{
        // ignore invalid selector while probing
      }}
    }}
    return uniqueEntriesByNode(entries);
  }};
  const pickPreferredPlusEntry = (entries) => {{
    const scopedPreferred = exactVisibleSelectorEntries(plusSelectors, composerScope);
    if (scopedPreferred[0]) return scopedPreferred[0];
    const globalPreferred = exactVisibleSelectorEntries(plusSelectors, document);
    if (globalPreferred[0]) return globalPreferred[0];
    const scopedAriaPreferred = uniqueEntriesByNode(
      scopedEntries().filter((entry) => plusLabels.includes(entry.ariaLabel)).sort((left, right) => distanceScore(left) - distanceScore(right))
    );
    if (scopedAriaPreferred[0]) return scopedAriaPreferred[0];
    return pickEntry(plusLabels, entries, {{preferScoped: true}});
  }};
  const scoreMoreEntry = (entry) => {{
    const hasControlsScore = entry.controls ? 0 : 1000;
    const hasSubmenuScore = entry.hasSubmenu ? 0 : 100;
    return hasControlsScore + hasSubmenuScore + distanceScore(entry);
  }};
  const pickPreferredMoreCandidates = (entries) => uniqueEntriesByNode(
    matchingEntries(moreLabels, entries).sort((left, right) => scoreMoreEntry(left) - scoreMoreEntry(right))
  );
  const readControlledSubmenuState = (entry) => {{
    const controlledId = normalize(entry?.controls || entry?.node?.getAttribute?.("aria-controls") || "");
    if (!controlledId) {{
      return {{
        controlledId: "",
        entries: [],
        labels: [],
        connectorEntries: [],
        sourceAddEntry: null,
        githubEntry: null,
      }};
    }}
    const root = document.getElementById(controlledId);
    if (!root || !isVisible(root)) {{
      return {{
        controlledId,
        entries: [],
        labels: [],
        connectorEntries: [],
        sourceAddEntry: null,
        githubEntry: null,
      }};
    }}
    const entries = collectEntries(root);
    const connectorEntries = entries.filter((candidate) => isTargetEntry(candidate, connectorTargetLabels));
    const sourceAddEntry = connectorEntries.find((candidate) => isTargetEntry(candidate, addSourceLabels)) || null;
    const githubEntry = connectorEntries.find((candidate) => isTargetEntry(candidate, githubLabels)) || null;
    return {{
      controlledId,
      entries,
      labels: renderLabels(entries),
      connectorEntries,
      sourceAddEntry,
      githubEntry,
    }};
  }};
  const clickEntry = (entry) => {{
    if (!entry?.node) return false;
    entry.node.scrollIntoView?.({{block: "center"}});
    entry.node.dispatchEvent?.(new MouseEvent("pointerdown", {{bubbles: true}}));
    entry.node.dispatchEvent?.(new MouseEvent("mousedown", {{bubbles: true}}));
    entry.node.click?.();
    entry.node.dispatchEvent?.(new MouseEvent("mouseup", {{bubbles: true}}));
    entry.node.dispatchEvent?.(new MouseEvent("pointerup", {{bubbles: true}}));
    return true;
  }};
  const focusEntry = (entry) => {{
    if (!entry?.node) return false;
    entry.node.scrollIntoView?.({{block: "center"}});
    entry.node.focus?.();
    entry.node.dispatchEvent?.(new FocusEvent("focus", {{bubbles: true}}));
    entry.node.dispatchEvent?.(new FocusEvent("focusin", {{bubbles: true}}));
    return document.activeElement === entry.node || entry.node.contains?.(document.activeElement);
  }};
  const hoverEntry = (entry) => {{
    if (!entry?.node) return false;
    entry.node.scrollIntoView?.({{block: "center"}});
    for (const type of ["pointerover", "mouseover", "mouseenter", "pointermove", "mousemove"]) {{
      entry.node.dispatchEvent?.(new MouseEvent(type, {{bubbles: true}}));
    }}
    return true;
  }};
  const keyboardEntry = (entry, key) => {{
    if (!entry?.node) return false;
    entry.node.scrollIntoView?.({{block: "center"}});
    entry.node.focus?.();
    const code = key === " " ? "Space" : key;
    for (const type of ["keydown", "keyup"]) {{
      entry.node.dispatchEvent?.(new KeyboardEvent(type, {{key, code, bubbles: true}}));
    }}
    return true;
  }};
  const performMoreStrategy = (entry, strategy) => {{
    if (!entry?.node) return false;
    if (strategy === "click") return clickEntry(entry);
    if (strategy === "focus") return focusEntry(entry);
    if (strategy === "hover") return hoverEntry(entry);
    if (strategy === "keyboard_enter") return keyboardEntry(entry, "Enter");
    if (strategy === "keyboard_space") return keyboardEntry(entry, " ");
    if (strategy === "keyboard_arrow_right") return keyboardEntry(entry, "ArrowRight");
    return false;
  }};
  const findComposerGithubPillState = () => {{
    const root = composerScope || composer || document.body;
    const removeButtons = Array.from(root.querySelectorAll("button,[role='button']")).filter((node) => {{
      if (!isVisible(node) || isInsideMenu(node)) return false;
      return githubPillRemoveLabels.includes(normalize(node.getAttribute("aria-label") || ""));
    }});
    const pillNodes = Array.from(root.querySelectorAll("button,[role='button'],span,div")).filter((node) => {{
      if (!isVisible(node) || isInsideMenu(node)) return false;
      if (node === composer || node.contains?.(composer)) return false;
      return normalize(node.textContent || "") === "GitHub";
    }});
    const githubPillRemoveButtonFound = removeButtons.length > 0;
    const githubPillVisible = githubPillRemoveButtonFound || pillNodes.length > 0;
    return {{
      githubPillConfirmed: githubPillVisible,
      githubPillRemoveButtonFound,
      githubPillVisible,
      githubPillLabels: dedupe([
        ...removeButtons.map((node) => normalize(node.getAttribute("aria-label") || "")),
        ...pillNodes.map((node) => normalize(node.textContent || "")),
      ]),
      finalAttachConfirmationKind: githubPillRemoveButtonFound
        ? "github_pill_remove_button"
        : (pillNodes.length > 0 ? "github_pill_visible" : ""),
    }};
  }};
  const beforeEntries = globalEntries();
  const plusEntry = pickPreferredPlusEntry(beforeEntries);
  const menuEntriesBefore = contextualMenuEntries(beforeEntries);
  const menuMoreCandidatesBefore = pickPreferredMoreCandidates(menuEntriesBefore);
  const globalMoreCandidatesBefore = pickPreferredMoreCandidates(beforeEntries);
  const allMoreCandidatesBefore = uniqueEntriesByNode([...menuMoreCandidatesBefore, ...globalMoreCandidatesBefore]);
  const moreEntryBefore = allMoreCandidatesBefore[0] || null;
  const chosenMoreCandidateIndex = moreEntryBefore
    ? allMoreCandidatesBefore.findIndex((entry) => entry.node === moreEntryBefore.node)
    : -1;
  const submenuStateBefore = readControlledSubmenuState(moreEntryBefore);
  const sourceAddEntryBefore = submenuStateBefore.sourceAddEntry;
  const githubEntryBefore = submenuStateBefore.githubEntry;
  const plusClicked = action === "click_plus" ? clickEntry(plusEntry) : false;
  const moreStrategy = action.startsWith("open_more_") ? action.slice("open_more_".length) : "";
  const moreActionPerformed = moreStrategy ? performMoreStrategy(moreEntryBefore, moreStrategy) : false;
  const githubClicked = action === "click_github" ? clickEntry(githubEntryBefore) : false;
  const afterEntries = globalEntries();
  const menuEntriesAfter = contextualMenuEntries(afterEntries);
  const menuEntries = menuEntriesAfter.length ? menuEntriesAfter : menuEntriesBefore;
  const menuMoreCandidatesAfter = pickPreferredMoreCandidates(menuEntriesAfter);
  const globalMoreCandidatesAfter = pickPreferredMoreCandidates(afterEntries);
  const allMoreCandidatesAfter = uniqueEntriesByNode([...menuMoreCandidatesAfter, ...globalMoreCandidatesAfter]);
  const moreEntry = allMoreCandidatesAfter[0] || moreEntryBefore;
  const submenuStateAfter = readControlledSubmenuState(moreEntry || moreEntryBefore);
  const sourceAddEntry = submenuStateAfter.sourceAddEntry || sourceAddEntryBefore;
  const githubEntry = submenuStateAfter.githubEntry || githubEntryBefore;
  const connectorEntries = uniqueEntriesByNode([
    ...(submenuStateAfter.connectorEntries || []),
    ...(submenuStateBefore.connectorEntries || []),
  ]);
  const submenuLabels = renderLabels(submenuStateAfter.entries || submenuStateBefore.entries || []);
  const connectorLikeLabelsSeen = dedupe([
    ...renderLabels(connectorEntries),
    ...renderLabels((submenuStateAfter.entries || []).filter((entry) => isTargetEntry(entry, connectorTargetLabels))),
    ...renderLabels((submenuStateBefore.entries || []).filter((entry) => isTargetEntry(entry, connectorTargetLabels))),
  ]);
  const connectorSubmenuDetected = !!sourceAddEntry || !!githubEntry || connectorLikeLabelsSeen.length > 0;
  const submenuClassifier = connectorSubmenuDetected
    ? "connector_submenu"
    : (submenuLabels.length > 0
        ? "controlled_submenu_without_connector_labels"
        : "no_submenu");
  const submenuClassifierReason = connectorSubmenuDetected
    ? "controlled_submenu_connector_labels_seen"
    : (submenuLabels.length > 0
        ? "controlled_submenu_without_connector_labels"
        : "no_submenu_candidates");
  const submenuCandidateGroups = [
    {{group: "menuish", labels: renderLabels(menuEntriesAfter).slice(0, 40)}},
    {{group: "controlled_submenu", labels: submenuLabels.slice(0, 40)}},
    {{group: "connector_like", labels: connectorLikeLabelsSeen.slice(0, 40)}},
  ];
  const genericOverlayLabelsSeen = nonMenuVisibleLabels(afterEntries)
    .filter((label) => !connectorLikeLabelsSeen.includes(label) && !plusLabels.includes(label));
  const composerScopeText = normalize((composerScope?.innerText || "").slice(0, 1200));
  const pillState = findComposerGithubPillState();
  const githubFoundContext = githubEntry ? "controlled_submenu" : "";
  return JSON.stringify({{
    composerFound: !!composer,
    projectName: payload.projectName || "",
    matchKind: payload.matchKind || "",
    matchedHint: payload.matchedHint || "",
    action,
    plusFound: !!plusEntry,
    plusLabel: plusEntry?.text || "",
    plusAriaLabel: plusEntry?.ariaLabel || "",
    plusTitle: plusEntry?.title || "",
    plusClicked,
    plusCandidates: renderLabels(beforeEntries.filter((entry) => isTargetEntry(entry, plusLabels))).slice(0, 20),
    beforeVisibleLabels: renderLabels(beforeEntries).slice(0, 60),
    afterVisibleLabels: renderLabels(afterEntries).slice(0, 60),
    menuOpened: !!moreEntry || menuEntries.length > 0,
    menuItems: renderEntries(menuEntries).slice(0, 20),
    menuCandidateLabels: renderLabels(menuEntries).slice(0, 20),
    moreFound: !!moreEntry,
    moreLabel: moreEntry?.text || "",
    moreAriaLabel: moreEntry?.ariaLabel || "",
    moreClicked: action === "open_more_click" ? moreActionPerformed : false,
    moreActionPerformed,
    moreOpenStrategyAttempted: moreStrategy,
    moreTargetText: moreEntry?.text || "",
    moreTargetAriaLabel: moreEntry?.ariaLabel || "",
    moreControlledId: submenuStateAfter.controlledId || submenuStateBefore.controlledId || "",
    chosenMoreCandidateIndex,
    chosenMoreCandidateLabels: moreEntryBefore?.labels || [],
    allMoreCandidates: renderEntries(allMoreCandidatesBefore).slice(0, 10),
    submenuOpened: connectorSubmenuDetected,
    submenuItems: renderEntries(connectorEntries).slice(0, 20),
    submenuCandidateLabels: connectorLikeLabelsSeen.slice(0, 20),
    submenuClassifier,
    submenuClassifierReason,
    connectorSubmenuDetected,
    submenuCandidateGroups,
    connectorLikeLabelsSeen: connectorLikeLabelsSeen.slice(0, 40),
    genericOverlayLabelsSeen: genericOverlayLabelsSeen.slice(0, 40),
    submenuProbeVisibleLabels: renderLabels(afterEntries).slice(0, 60),
    submenuProbeMenuishLabels: renderLabels(menuEntriesAfter).slice(0, 40),
    submenuProbeOverlayLabels: submenuLabels.slice(0, 40),
    sourceAddFound: !!sourceAddEntry,
    githubFound: !!githubEntry,
    githubLabel: githubEntry?.text || "",
    githubAriaLabel: githubEntry?.ariaLabel || "",
    githubClicked,
    githubPillConfirmed: pillState.githubPillConfirmed,
    githubPillRemoveButtonFound: pillState.githubPillRemoveButtonFound,
    githubPillVisible: pillState.githubPillVisible,
    githubPillLabels: pillState.githubPillLabels.slice(0, 10),
    githubSelectedLike: pillState.githubPillConfirmed,
    githubClickConfirmed: githubClicked,
    githubFoundContext,
    finalAttachConfirmationKind: pillState.finalAttachConfirmationKind,
    visibleLabels: renderLabels(afterEntries).slice(0, 60),
    composerScopeText,
  }});
}})();
"""


def _probe_project_page_github_source(
    page: SafariChatPage,
    *,
    preferred_hint: str | None = None,
    action: str = "probe",
    assert_front_tab: bool = True,
) -> dict[str, Any]:
    evaluator = _evaluate_json if assert_front_tab else _evaluate_json_unchecked
    return evaluator(
        page,
        _build_project_page_github_source_probe_script(
            preferred_hint=preferred_hint,
            action=action,
        ),
        "GitHub source preflight probe に失敗しました",
    )


def _merge_project_page_github_source_payload(
    base: dict[str, Any],
    update: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, bool):
            merged[key] = bool(merged.get(key)) or value
            continue
        if isinstance(value, str):
            if value:
                merged[key] = value
            else:
                merged.setdefault(key, "")
            continue
        if isinstance(value, list):
            if value:
                merged[key] = value
            else:
                merged.setdefault(key, [])
            continue
        if value is not None:
            merged[key] = value
    return merged


def _collect_project_page_probe_labels(payload: Mapping[str, Any], *keys: str) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value).strip()
        if not text or text in seen:
            return
        seen.add(text)
        labels.append(text)

    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    add(item.get("text", ""))
                    add(item.get("ariaLabel", ""))
                    add(item.get("title", ""))
                    add(item.get("testId", ""))
                else:
                    add(item)
        elif isinstance(value, Mapping):
            add(value.get("text", ""))
            add(value.get("ariaLabel", ""))
            add(value.get("title", ""))
            add(value.get("testId", ""))
        else:
            add(value)
    return labels


def _connector_submenu_labels(payload: Mapping[str, Any]) -> list[str]:
    connector_targets = set(PROJECT_CHAT_ADD_SOURCE_LABELS) | set(PROJECT_CHAT_GITHUB_LABELS)
    labels = _collect_project_page_probe_labels(
        payload,
        "connectorLikeLabelsSeen",
        "submenuItems",
        "submenuCandidateLabels",
        "submenuProbeOverlayLabels",
    )
    return [label for label in labels if label in connector_targets]


def _connector_submenu_detected(payload: Mapping[str, Any]) -> bool:
    if "connectorSubmenuDetected" in payload:
        return bool(payload.get("connectorSubmenuDetected"))
    if bool(payload.get("sourceAddFound")) or bool(payload.get("githubFound")):
        return True
    return bool(_connector_submenu_labels(payload))


def _wait_for_project_page_github_source_probe(
    *,
    probe: Callable[[], dict[str, Any]],
    wait_before_first_probe: bool,
    stop_when: Callable[[Mapping[str, Any]], bool],
    seen_keys: Sequence[str],
    wait: Callable[[int], None],
    attempts: int = PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_ATTEMPTS,
    settle_ms: int = PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_MS,
    deadline: float | None = None,
) -> tuple[dict[str, Any], int, int, list[str], bool]:
    last_payload: dict[str, Any] = {}
    seen_labels: list[str] = []
    seen_set: set[str] = set()
    attempt_count = 0
    started = time.monotonic()
    timed_out = False

    for attempt in range(1, attempts + 1):
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            break
        if attempt > 1 or wait_before_first_probe:
            wait(settle_ms)
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            break
        payload = probe()
        last_payload = payload
        attempt_count = attempt
        for label in _collect_project_page_probe_labels(payload, *seen_keys):
            if label in seen_set:
                continue
            seen_set.add(label)
            seen_labels.append(label)
        if stop_when(payload):
            break

    elapsed_ms = int(round((time.monotonic() - started) * 1000))
    return last_payload, attempt_count, elapsed_ms, seen_labels, timed_out


def _github_attach_phase_boundary_check(
    page: SafariChatPage,
    payload: dict[str, Any],
    *,
    phase_name: str,
) -> None:
    checks = list(payload.get("phaseBoundaryChecks", []))
    checks.append(phase_name)
    payload["phaseBoundaryChecks"] = checks
    if not hasattr(page, "assert_same_front_tab"):
        return
    try:
        page.assert_same_front_tab()
    except BridgeError as exc:
        payload["phaseFailureBoundary"] = "github_attach_phase_tab_drift"
        payload["phaseFailurePhase"] = phase_name
        raise BridgeError(
            "GitHub attach phase boundary check に失敗しました。"
            f" boundary=github_attach_phase_tab_drift"
            f" phase={phase_name}"
            f" detail={exc}"
        ) from exc


def _raise_project_page_github_attach_timeout(
    page: SafariChatPage,
    payload: dict[str, Any],
    *,
    phase_name: str,
    started_at: float,
) -> None:
    payload["phaseFailureBoundary"] = "github_attach_phase_timeout"
    payload["phaseFailurePhase"] = phase_name
    payload["phaseTimeoutMs"] = int(round((time.monotonic() - started_at) * 1000))
    result = ProjectPageGithubSourcePreflightResult(
        status="probe_failed",
        boundary="github_attach_phase_timeout",
        detail=f"GitHub attach operation が phase={phase_name} でタイムアウトしました。",
    )
    _raise_project_page_github_source_preflight_error(page, result, payload)


def _log_project_page_github_source_probe(prefix: str, payload: Mapping[str, Any]) -> Path:
    snapshot = {
        "attachOperationStarted": bool(payload.get("attachOperationStarted")),
        "attachOperationCompleted": bool(payload.get("attachOperationCompleted")),
        "phaseBoundaryChecks": list(payload.get("phaseBoundaryChecks", [])),
        "uncheckedProbeCount": int(payload.get("uncheckedProbeCount", 0) or 0),
        "phaseTimeoutMs": int(payload.get("phaseTimeoutMs", 0) or 0),
        "phaseFailureBoundary": str(payload.get("phaseFailureBoundary", "")),
        "phaseFailurePhase": str(payload.get("phaseFailurePhase", "")),
        "composerFound": bool(payload.get("composerFound")),
        "projectName": str(payload.get("projectName", "")),
        "matchKind": str(payload.get("matchKind", "")),
        "matchedHint": str(payload.get("matchedHint", "")),
        "action": str(payload.get("action", "")),
        "plusFound": bool(payload.get("plusFound")),
        "plusLabel": str(payload.get("plusLabel", "")),
        "plusAriaLabel": str(payload.get("plusAriaLabel", "")),
        "plusTitle": str(payload.get("plusTitle", "")),
        "plusClicked": bool(payload.get("plusClicked")),
        "plusCandidates": list(payload.get("plusCandidates", [])),
        "plusCandidatesBefore": list(payload.get("plusCandidatesBefore", [])),
        "plusCandidatesAfter": list(payload.get("plusCandidatesAfter", [])),
        "menuOpened": bool(payload.get("menuOpened")),
        "menuItems": list(payload.get("menuItems", [])),
        "menuCandidateLabels": list(payload.get("menuCandidateLabels", [])),
        "menuWaitAttempts": int(payload.get("menuWaitAttempts", 0) or 0),
        "menuWaitElapsedMs": int(payload.get("menuWaitElapsedMs", 0) or 0),
        "menuWaitSeenItems": list(payload.get("menuWaitSeenItems", [])),
        "moreFound": bool(payload.get("moreFound")),
        "moreLabel": str(payload.get("moreLabel", "")),
        "moreAriaLabel": str(payload.get("moreAriaLabel", "")),
        "moreClicked": bool(payload.get("moreClicked")),
        "moreActionPerformed": bool(payload.get("moreActionPerformed")),
        "moreOpenStrategiesTried": list(payload.get("moreOpenStrategiesTried", [])),
        "moreOpenStrategySucceeded": str(payload.get("moreOpenStrategySucceeded", "")),
        "moreOpenStrategySeenLabels": list(payload.get("moreOpenStrategySeenLabels", [])),
        "moreTargetText": str(payload.get("moreTargetText", "")),
        "moreTargetAriaLabel": str(payload.get("moreTargetAriaLabel", "")),
        "chosenMoreCandidateIndex": int(payload.get("chosenMoreCandidateIndex", -1) or -1),
        "chosenMoreCandidateLabels": list(payload.get("chosenMoreCandidateLabels", [])),
        "allMoreCandidates": list(payload.get("allMoreCandidates", [])),
        "moreCandidatesBefore": list(payload.get("moreCandidatesBefore", [])),
        "moreCandidatesAfter": list(payload.get("moreCandidatesAfter", [])),
        "submenuOpened": bool(payload.get("submenuOpened")),
        "submenuItems": list(payload.get("submenuItems", [])),
        "submenuCandidateLabels": list(payload.get("submenuCandidateLabels", [])),
        "submenuClassifier": str(payload.get("submenuClassifier", "")),
        "submenuClassifierReason": str(payload.get("submenuClassifierReason", "")),
        "connectorSubmenuDetected": bool(payload.get("connectorSubmenuDetected")),
        "submenuCandidateGroups": list(payload.get("submenuCandidateGroups", [])),
        "connectorLikeLabelsSeen": list(payload.get("connectorLikeLabelsSeen", [])),
        "genericOverlayLabelsSeen": list(payload.get("genericOverlayLabelsSeen", [])),
        "submenuProbeVisibleLabels": list(payload.get("submenuProbeVisibleLabels", [])),
        "submenuProbeMenuishLabels": list(payload.get("submenuProbeMenuishLabels", [])),
        "submenuProbeOverlayLabels": list(payload.get("submenuProbeOverlayLabels", [])),
        "submenuWaitAttempts": int(payload.get("submenuWaitAttempts", 0) or 0),
        "submenuWaitElapsedMs": int(payload.get("submenuWaitElapsedMs", 0) or 0),
        "submenuWaitSeenItems": list(payload.get("submenuWaitSeenItems", [])),
        "sourceAddFound": bool(payload.get("sourceAddFound")),
        "githubFound": bool(payload.get("githubFound")),
        "githubLabel": str(payload.get("githubLabel", "")),
        "githubAriaLabel": str(payload.get("githubAriaLabel", "")),
        "githubClicked": bool(payload.get("githubClicked")),
        "githubPillConfirmed": bool(payload.get("githubPillConfirmed")),
        "githubPillRemoveButtonFound": bool(payload.get("githubPillRemoveButtonFound")),
        "githubPillVisible": bool(payload.get("githubPillVisible")),
        "githubPillLabels": list(payload.get("githubPillLabels", [])),
        "githubSelectedLike": bool(payload.get("githubSelectedLike")),
        "githubClickConfirmed": bool(payload.get("githubClickConfirmed")),
        "githubFoundContext": str(payload.get("githubFoundContext", "")),
        "moreControlledId": str(payload.get("moreControlledId", "")),
        "githubCandidatesBefore": list(payload.get("githubCandidatesBefore", [])),
        "githubCandidatesAfter": list(payload.get("githubCandidatesAfter", [])),
        "githubConfirmAttempts": int(payload.get("githubConfirmAttempts", 0) or 0),
        "githubConfirmElapsedMs": int(payload.get("githubConfirmElapsedMs", 0) or 0),
        "githubConfirmSeenItems": list(payload.get("githubConfirmSeenItems", [])),
        "githubConfirmationKind": str(payload.get("githubConfirmationKind", "")),
        "finalAttachConfirmationKind": str(payload.get("finalAttachConfirmationKind", "")),
        "visibleLabels": list(payload.get("visibleLabels", [])),
    }
    return log_text(prefix, json.dumps(snapshot, ensure_ascii=False, indent=2))


def _raise_project_page_github_source_preflight_error(
    page: SafariChatPage,
    result: ProjectPageGithubSourcePreflightResult,
    payload: Mapping[str, Any],
) -> None:
    probe_path = _log_project_page_github_source_probe("project_page_github_source_probe", payload)
    dump_path = log_page_dump(page, prefix="project_page_github_source_dump")
    dump_note = f" raw dump: {repo_relative(dump_path)}" if dump_path else ""
    raise ProjectPageGithubSourcePreflightError(
        "GitHub source preflight に失敗しました。"
        f" status={result.status}"
        f" boundary={result.boundary}"
        f" detail={result.detail}"
        f" probe: {repo_relative(probe_path)}"
        f"{dump_note}",
        result=result,
        payload=payload,
        probe_path=probe_path,
        dump_path=dump_path,
    )


def ensure_project_page_github_source_ready(
    page: SafariChatPage,
    *,
    preferred_hint: str | None = None,
) -> dict[str, Any]:
    operation_started = time.monotonic()
    operation_deadline = operation_started + PROJECT_CHAT_GITHUB_ATTACH_PHASE_TIMEOUT_SECONDS
    payload: dict[str, Any] = {
        "attachOperationStarted": True,
        "attachOperationCompleted": False,
        "phaseBoundaryChecks": [],
        "uncheckedProbeCount": 0,
        "phaseTimeoutMs": int(PROJECT_CHAT_GITHUB_ATTACH_PHASE_TIMEOUT_SECONDS * 1000),
        "phaseFailureBoundary": "",
        "phaseFailurePhase": "",
        "moreOpenStrategiesTried": [],
        "moreOpenStrategySucceeded": "",
        "moreOpenStrategySeenLabels": [],
        "finalAttachConfirmationKind": "",
    }

    _github_attach_phase_boundary_check(page, payload, phase_name="github_attach_start")

    def run_unchecked_probe(*, action: str = "probe") -> dict[str, Any]:
        payload["uncheckedProbeCount"] = int(payload.get("uncheckedProbeCount", 0)) + 1
        return _probe_project_page_github_source(
            page,
            preferred_hint=preferred_hint,
            action=action,
            assert_front_tab=False,
        )

    payload = _merge_project_page_github_source_payload(payload, run_unchecked_probe(action="probe"))
    result = classify_project_page_github_source_preflight(payload)
    if result.boundary in {"composer_missing", "composer_plus_missing"}:
        _raise_project_page_github_source_preflight_error(page, result, payload)

    plus_click_payload = run_unchecked_probe(action="click_plus")
    payload = _merge_project_page_github_source_payload(payload, plus_click_payload)
    payload["plusCandidatesBefore"] = list(plus_click_payload.get("beforeVisibleLabels", []))
    payload["plusCandidatesAfter"] = list(plus_click_payload.get("afterVisibleLabels", []))
    result = classify_project_page_github_source_preflight(payload)
    if result.boundary in {"composer_missing", "composer_plus_missing", "composer_plus_click_failed"}:
        _raise_project_page_github_source_preflight_error(page, result, payload)

    menu_payload, menu_attempts, menu_elapsed_ms, menu_seen, menu_timed_out = _wait_for_project_page_github_source_probe(
        probe=lambda: run_unchecked_probe(action="probe"),
        wait_before_first_probe=True,
        stop_when=lambda current: bool(current.get("menuOpened")) or bool(current.get("moreFound")),
        seen_keys=("menuItems", "menuCandidateLabels", "visibleLabels", "moreLabel", "moreAriaLabel"),
        wait=page.wait_for_timeout,
        deadline=operation_deadline,
    )
    payload = _merge_project_page_github_source_payload(payload, menu_payload)
    payload["menuWaitAttempts"] = menu_attempts
    payload["menuWaitElapsedMs"] = menu_elapsed_ms
    payload["menuWaitSeenItems"] = menu_seen
    if menu_timed_out:
        _raise_project_page_github_attach_timeout(
            page,
            payload,
            phase_name="github_attach_menu_wait",
            started_at=operation_started,
        )
    result = classify_project_page_github_source_preflight(payload)
    if result.boundary == "composer_menu_not_open":
        _raise_project_page_github_source_preflight_error(page, result, payload)

    payload["moreOpenStrategiesTried"] = []
    payload["moreOpenStrategySeenLabels"] = []
    strategy_succeeded = ""
    strategy_seen_logs: list[dict[str, Any]] = []

    for strategy in PROJECT_CHAT_MORE_OPEN_STRATEGIES:
        if time.monotonic() >= operation_deadline:
            _raise_project_page_github_attach_timeout(
                page,
                payload,
                phase_name="github_attach_more_open",
                started_at=operation_started,
            )
        more_open_payload = run_unchecked_probe(action=f"open_more_{strategy}")
        payload = _merge_project_page_github_source_payload(payload, more_open_payload)
        payload["moreCandidatesBefore"] = list(more_open_payload.get("beforeVisibleLabels", []))
        payload["moreCandidatesAfter"] = list(more_open_payload.get("afterVisibleLabels", []))
        tried = list(payload.get("moreOpenStrategiesTried", []))
        tried.append(strategy)
        payload["moreOpenStrategiesTried"] = tried
        result = classify_project_page_github_source_preflight(payload)
        if result.boundary == "composer_more_missing":
            _raise_project_page_github_source_preflight_error(page, result, payload)
        if not bool(payload.get("moreActionPerformed") or payload.get("moreClicked")):
            continue

        submenu_payload, submenu_attempts, submenu_elapsed_ms, submenu_seen, submenu_timed_out = _wait_for_project_page_github_source_probe(
            probe=lambda: run_unchecked_probe(action="probe"),
            wait_before_first_probe=True,
            stop_when=lambda current: bool(current.get("submenuOpened"))
            or bool(current.get("sourceAddFound"))
            or bool(current.get("githubFound")),
            seen_keys=(
                "submenuItems",
                "submenuCandidateLabels",
                "submenuProbeVisibleLabels",
                "submenuProbeMenuishLabels",
                "submenuProbeOverlayLabels",
                "visibleLabels",
                "githubLabel",
                "githubAriaLabel",
            ),
            wait=page.wait_for_timeout,
            deadline=operation_deadline,
        )
        payload = _merge_project_page_github_source_payload(payload, submenu_payload)
        payload["submenuWaitAttempts"] = submenu_attempts
        payload["submenuWaitElapsedMs"] = submenu_elapsed_ms
        payload["submenuWaitSeenItems"] = submenu_seen
        strategy_seen_logs.append({"strategy": strategy, "seenLabels": submenu_seen})
        payload["moreOpenStrategySeenLabels"] = strategy_seen_logs
        if submenu_timed_out:
            _raise_project_page_github_attach_timeout(
                page,
                payload,
                phase_name="github_attach_submenu_wait",
                started_at=operation_started,
            )
        result = classify_project_page_github_source_preflight(payload)
        if result.boundary not in {"composer_more_submenu_not_open", "github_item_missing"}:
            strategy_succeeded = strategy
            break

    payload["moreOpenStrategySucceeded"] = strategy_succeeded
    result = classify_project_page_github_source_preflight(payload)
    if result.boundary in {
        "composer_more_click_failed",
        "composer_more_submenu_not_open",
        "github_item_missing",
    }:
        _raise_project_page_github_source_preflight_error(page, result, payload)

    github_click_payload = run_unchecked_probe(action="click_github")
    payload = _merge_project_page_github_source_payload(payload, github_click_payload)
    payload["githubCandidatesBefore"] = list(github_click_payload.get("beforeVisibleLabels", []))
    payload["githubCandidatesAfter"] = list(github_click_payload.get("afterVisibleLabels", []))
    result = classify_project_page_github_source_preflight(payload)
    if result.boundary == "github_click_failed":
        _raise_project_page_github_source_preflight_error(page, result, payload)

    confirm_payload, confirm_attempts, confirm_elapsed_ms, confirm_seen, confirm_timed_out = _wait_for_project_page_github_source_probe(
        probe=lambda: run_unchecked_probe(action="probe"),
        wait_before_first_probe=True,
        stop_when=lambda current: bool(current.get("githubPillConfirmed")),
        seen_keys=(
            "submenuItems",
            "submenuCandidateLabels",
            "visibleLabels",
            "githubLabel",
            "githubAriaLabel",
            "githubPillLabels",
        ),
        wait=page.wait_for_timeout,
        deadline=operation_deadline,
    )
    payload = _merge_project_page_github_source_payload(payload, confirm_payload)
    payload["githubConfirmAttempts"] = confirm_attempts
    payload["githubConfirmElapsedMs"] = confirm_elapsed_ms
    payload["githubConfirmSeenItems"] = confirm_seen
    payload["githubConfirmationKind"] = str(payload.get("finalAttachConfirmationKind", ""))
    if confirm_timed_out:
        _raise_project_page_github_attach_timeout(
            page,
            payload,
            phase_name="github_attach_confirm",
            started_at=operation_started,
        )
    result = classify_project_page_github_source_preflight(payload)
    if result.status != "available":
        _raise_project_page_github_source_preflight_error(page, result, payload)
    _github_attach_phase_boundary_check(page, payload, phase_name="github_attach_complete")
    payload["attachOperationCompleted"] = True
    return payload


def github_source_attach_required(config: Mapping[str, Any]) -> bool:
    return bool(config.get("require_github_source"))


def _project_page_github_source_attach_prefix(send_context: str) -> str:
    if send_context == "rotation_handoff":
        return "project_page_github_source_attach_rotation"
    return "project_page_github_source_attach_initial"


def _log_project_page_github_source_attach_outcome(
    send_context: str,
    outcome: ProjectPageGithubSourceAttachOutcome,
) -> Path:
    snapshot = {
        "github_source_attach_status": outcome.status,
        "github_source_attach_boundary": outcome.boundary,
        "github_source_attach_detail": outcome.detail,
        "github_source_attach_context": outcome.context,
        "github_source_attach_attempted": outcome.attempted,
        "request_send_continued_without_github_source": outcome.continued_without_github_source,
        "github_source_attach_probe_log": outcome.probe_log,
        "github_source_attach_raw_dump": outcome.raw_dump,
        "finalAttachConfirmationKind": outcome.final_confirmation_kind,
    }
    return log_text(
        _project_page_github_source_attach_prefix(send_context),
        json.dumps(snapshot, ensure_ascii=False, indent=2),
    )


def ensure_project_page_github_source_ready_for_send(
    page: SafariChatPage,
    config: Mapping[str, Any],
    *,
    preferred_hint: str | None = None,
    send_context: str,
) -> ProjectPageGithubSourceAttachOutcome:
    require_github_source = github_source_attach_required(config)
    try:
        payload = ensure_project_page_github_source_ready(page, preferred_hint=preferred_hint)
    except ProjectPageGithubSourcePreflightError as exc:
        if require_github_source:
            raise
        return ProjectPageGithubSourceAttachOutcome(
            status=exc.result.status,
            boundary=exc.result.boundary,
            detail=exc.result.detail,
            context=send_context,
            attempted=True,
            continued_without_github_source=True,
            probe_log=repo_relative(exc.probe_path) if exc.probe_path else "",
            raw_dump=repo_relative(exc.dump_path) if exc.dump_path else "",
            final_confirmation_kind=str(exc.payload.get("finalAttachConfirmationKind", "")),
        )

    result = classify_project_page_github_source_preflight(payload)
    if bool(payload.get("githubPillConfirmed")):
        status = "attached"
    else:
        status = "unconfirmed"
    return ProjectPageGithubSourceAttachOutcome(
        status=status,
        boundary=result.boundary,
        detail=result.detail,
        context=send_context,
        attempted=True,
        continued_without_github_source=(status != "attached"),
        final_confirmation_kind=str(payload.get("finalAttachConfirmationKind", "")),
    )


def _read_post_send_state(
    config: Mapping[str, Any],
    *,
    expected_text: str,
    preferred_hint: str | None = None,
    project_page_mode: bool = False,
) -> tuple[dict[str, str], dict[str, Any]]:
    tab_info = frontmost_safari_tab_info(config, require_conversation=False)
    raw = _run_safari_javascript(
        _build_post_send_state_script(
            _expected_excerpt(expected_text),
            preferred_hint=preferred_hint,
            project_page_mode=project_page_mode,
        )
    ).strip()
    if not raw:
        raise BridgeError("新チャット送信後の状態確認に失敗しました: Safari から空の応答が返りました。")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError("新チャット送信後の状態確認に失敗しました: JSON として読めませんでした。") from exc
    return tab_info, payload


def _is_transient_post_send_probe_error(message: str) -> bool:
    markers = (
        "新チャット送信後の状態確認に失敗しました: Safari から空の応答が返りました。",
        "新チャット送信後の状態確認に失敗しました: JSON として読めませんでした。",
    )
    return any(marker in message for marker in markers)


def ensure_chatgpt_ready(
    page: SafariChatPage,
    config: Mapping[str, Any],
    *,
    allow_manual_login: bool = False,
    preferred_hint: str | None = None,
    project_page_mode: bool = False,
) -> dict[str, Any]:
    deadline = time.time() + 20
    state = _find_composer_state(page, preferred_hint=preferred_hint, project_page_mode=project_page_mode)
    while not state.get("found") and time.time() < deadline:
        page.wait_for_timeout(1000)
        state = _find_composer_state(page, preferred_hint=preferred_hint, project_page_mode=project_page_mode)

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
    if project_page_mode:
        probe_path = _log_composer_probe("project_chat_composer_probe", state)
        probe_note = f" composer probe: {repo_relative(probe_path)}"
        candidate_note = f" {_composer_candidate_summary(state)}"
        if not str(state.get("projectName", "")).strip():
            raise BridgeError(
                "project ページは見えていますが、ChatGPT project 名を取得できませんでした。"
                f"{candidate_note}{dump_note}{probe_note}"
            )
        if not bool(state.get("projectHintDetected")):
            raise BridgeError(
                "project ページは見えていますが、『＜project名＞ 内の新しいチャット』系の hint を確認できませんでした。"
                f" project_name={state.get('projectName', '')}{candidate_note}{dump_note}{probe_note}"
            )
        raise BridgeError(
            "project ページ上の新規 composer 構造を特定できませんでした。"
            f" project_name={state.get('projectName', '')}{candidate_note}{dump_note}{probe_note}"
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
    surface_label: str | None = None,
    allow_conversation_transition: bool = False,
) -> Iterator[tuple[None, SafariChatPage, dict[str, Any], dict[str, str]]]:
    del reset_chat
    config = load_browser_config()
    front_tab = frontmost_safari_tab_info(config, require_conversation=require_conversation)
    page = SafariChatPage(
        config=config,
        front_tab=front_tab,
        require_conversation=require_conversation,
        surface_label=surface_label or ("対象チャット" if require_conversation else "project ページ"),
        allow_conversation_transition=allow_conversation_transition,
    )
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
    project_page_mode: bool = False,
) -> dict[str, Any]:
    initial_state = ensure_chatgpt_ready(
        page,
        config,
        allow_manual_login=allow_manual_login,
        preferred_hint=preferred_hint,
        project_page_mode=project_page_mode,
    )
    if preferred_hint and not bool(initial_state.get("matchKind")):
        dump_path = log_page_dump(page)
        dump_note = f" raw dump: {repo_relative(dump_path)}" if dump_path else ""
        probe_path = _log_composer_probe("project_chat_composer_probe", initial_state)
        probe_note = f" composer probe: {repo_relative(probe_path)}"
        raise BridgeError(
            "指定した project page composer を特定できませんでした。"
            f" hint='{preferred_hint}' match_kind='{initial_state.get('matchKind', '')}'"
            f" matched_hint='{initial_state.get('matchedHint', '')}'"
            f" {_composer_candidate_summary(initial_state)}{dump_note}{probe_note}"
        )
    result = _evaluate_json(
        page,
        _build_fill_composer_script(text, preferred_hint=preferred_hint, project_page_mode=project_page_mode),
        "ChatGPT 入力欄への書き込みに失敗しました",
    )
    if result.get("ok"):
        page.wait_for_timeout(300)
        state = _find_composer_state(page, preferred_hint=preferred_hint, project_page_mode=project_page_mode)
        actual_text = str(state.get("currentText", "") or "")
        if not _composer_text_matches(actual_text, text):
            dump_path = log_page_dump(page)
            dump_note = f" raw dump: {repo_relative(dump_path)}" if dump_path else ""
            probe_path = _log_composer_probe("project_chat_composer_probe", state)
            probe_note = f" composer probe: {repo_relative(probe_path)}"
            raise BridgeError(
                "ChatGPT 入力欄へ本文を書き込んだ後の確認に失敗しました。"
                " composer に期待した handoff 本文が入っていません。"
                f" {_composer_candidate_summary(state)}{dump_note}{probe_note}"
            )
        return state
    probe_path = _log_composer_probe("project_chat_composer_probe", result)
    probe_note = f" composer probe: {repo_relative(probe_path)}"
    raise BridgeError(
        "ChatGPT の入力欄に文字を設定できませんでした:"
        f" {result.get('reason', 'unknown')}"
        f" project_name={result.get('projectName', '')}"
        f" matched_hint={result.get('matchedHint', '')}"
        f" {_composer_candidate_summary(result)}{probe_note}"
    )


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


def _attach_outcome_fields(outcome: ProjectPageGithubSourceAttachOutcome | None) -> dict[str, Any]:
    if outcome is None:
        return {
            "github_source_attach_status": "skipped",
            "github_source_attach_boundary": "",
            "github_source_attach_detail": "",
            "github_source_attach_context": "",
            "github_source_attach_log": "",
            "request_send_continued_without_github_source": False,
        }
    return {
        "github_source_attach_status": outcome.status,
        "github_source_attach_boundary": outcome.boundary,
        "github_source_attach_detail": outcome.detail,
        "github_source_attach_context": outcome.context,
        "github_source_attach_log": "",
        "request_send_continued_without_github_source": outcome.continued_without_github_source,
    }


def send_initial_request_to_chatgpt(text: str) -> dict[str, Any]:
    config = load_browser_config()
    front_tab = frontmost_safari_tab_info(config, require_conversation=False)
    current_url = front_tab.get("url", "")
    if _conversation_url_matches(current_url, config):
        send_to_chatgpt(text)
        return {
            "url": current_url,
            "title": front_tab.get("title", ""),
            "signal": "conversation_url",
            "match_kind": "",
            "matched_hint": "",
            "project_name": "",
            **_attach_outcome_fields(None),
        }
    if _project_page_url_matches(current_url, config):
        last_error = ""
        for hint in PROJECT_CHAT_COMPOSER_HINTS + ("",):
            try:
                return send_to_chatgpt_in_current_surface(
                    text,
                    require_conversation=False,
                    require_target_chat=False,
                    preferred_hint=hint or None,
                    project_page_mode=True,
                    send_context="initial_request",
                )
            except BridgeError as exc:
                last_error = str(exc)
        raise BridgeError(
            "Safari の現在 ChatGPT タブは project ページですが、初回 request を送る composer を特定できませんでした。"
            f" {last_error}"
        )
    raise BridgeError(
        "Safari の現在 ChatGPT タブが対象会話でも project ページでもありません。"
        f" 対象チャットまたは project ページを開いてください: {front_tab.get('title', '')} {current_url}"
    )


def send_to_chatgpt_in_current_surface(
    text: str,
    *,
    require_conversation: bool = False,
    require_target_chat: bool = False,
    preferred_hint: str | None = None,
    project_page_mode: bool = False,
    send_context: str = "initial_request",
) -> dict[str, Any]:
    surface_label = "project ページ" if project_page_mode else ("対象チャット" if require_conversation else "ChatGPT ページ")
    with open_chatgpt_page(
        reset_chat=False,
        require_conversation=require_conversation,
        require_target_chat=require_target_chat,
        surface_label=surface_label,
    ) as (_, page, config, _):
        attach_outcome: ProjectPageGithubSourceAttachOutcome | None = None
        if project_page_mode:
            attach_outcome = ensure_project_page_github_source_ready_for_send(
                page,
                config,
                preferred_hint=preferred_hint,
                send_context=send_context,
            )
            attach_log = _log_project_page_github_source_attach_outcome(send_context, attach_outcome)
            if attach_outcome.continued_without_github_source:
                print(
                    "note: GitHub source attach は未確認のままです。"
                    f" status={attach_outcome.status}"
                    f" boundary={attach_outcome.boundary}"
                    " best-effort で request 送信を続けます。"
                )
            attach_fields = _attach_outcome_fields(attach_outcome)
            attach_fields["github_source_attach_log"] = repo_relative(attach_log)
        else:
            attach_fields = _attach_outcome_fields(None)
        composer_state = fill_chatgpt_composer(
            page,
            text,
            config,
            preferred_hint=preferred_hint,
            project_page_mode=project_page_mode,
        )
        submit_chatgpt_message(page)
        deadline = time.time() + 15
        last_tab: dict[str, str] | None = None
        last_payload: dict[str, Any] | None = None
        last_probe_error = ""
        while time.time() < deadline:
            try:
                tab_info, payload = _read_post_send_state(
                    config,
                    expected_text=text,
                    preferred_hint=preferred_hint,
                    project_page_mode=project_page_mode,
                )
            except BridgeError as exc:
                if project_page_mode and _is_transient_post_send_probe_error(str(exc)):
                    last_probe_error = str(exc)
                    time.sleep(0.5)
                    continue
                raise
            last_tab = tab_info
            last_payload = payload
            if _conversation_url_matches(tab_info.get("url", ""), config):
                return {
                    "url": tab_info.get("url", ""),
                    "title": tab_info.get("title", ""),
                    "signal": "conversation_url",
                    "match_kind": str(composer_state.get("matchKind", "")),
                    "matched_hint": str(composer_state.get("matchedHint", "")),
                    "project_name": str(composer_state.get("projectName", "")),
                    **attach_fields,
                }
            if bool(payload.get("bodyContainsExpected")):
                return {
                    "url": tab_info.get("url", ""),
                    "title": tab_info.get("title", ""),
                    "signal": "message_visible",
                    "match_kind": str(composer_state.get("matchKind", "")),
                    "matched_hint": str(composer_state.get("matchedHint", "")),
                    "project_name": str(composer_state.get("projectName", "")),
                    **attach_fields,
                }
            if bool(payload.get("composerEmpty")):
                return {
                    "url": tab_info.get("url", ""),
                    "title": tab_info.get("title", ""),
                    "signal": "composer_cleared",
                    "match_kind": str(composer_state.get("matchKind", "")),
                    "matched_hint": str(composer_state.get("matchedHint", "")),
                    "project_name": str(composer_state.get("projectName", "")),
                    **attach_fields,
                }
            time.sleep(0.5)

        if project_page_mode and last_probe_error:
            fallback_tab = last_tab or dict(page.front_tab)
            return {
                "url": fallback_tab.get("url", ""),
                "title": fallback_tab.get("title", ""),
                "signal": "submitted_unconfirmed",
                "match_kind": str(composer_state.get("matchKind", "")),
                "matched_hint": str(composer_state.get("matchedHint", "")),
                "project_name": str(composer_state.get("projectName", "")),
                "warning": last_probe_error,
                **attach_fields,
            }

        dump_text = _body_text_unchecked()
        dump_note = ""
        if dump_text:
            dump_path = log_text("chat_rotation_failure_dump", dump_text, suffix="txt")
            dump_note = f" raw dump: {repo_relative(dump_path)}"
        raise BridgeError(
            "project ページへ handoff を送った後の確認に失敗しました。"
            " composer が空に戻る、本文が会話へ現れる、会話 URL へ切り替わる、のいずれも確認できませんでした。"
            f" last_url: {last_tab.get('url', '') if last_tab else ''}"
            f" composer_empty: {bool(last_payload.get('composerEmpty')) if last_payload else False}"
            f" body_contains_expected: {bool(last_payload.get('bodyContainsExpected')) if last_payload else False}"
            f" match_kind: {composer_state.get('matchKind', '')}"
            f" matched_hint: {composer_state.get('matchedHint', '')}"
            f" project_name: {composer_state.get('projectName', '')}"
            f"{dump_note}"
        )


def draft_message_in_chatgpt(text: str) -> None:
    with open_chatgpt_page(reset_chat=False, surface_label="対象チャット") as (_, page, config, _):
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
    last_signal = ""
    last_warning = ""
    last_send_result: dict[str, Any] = {}
    for attempt in range(1, 3):
        navigate_current_chatgpt_tab(project_page_url)
        try:
            sent = False
            for hint in PROJECT_CHAT_COMPOSER_HINTS + ("",):
                try:
                    send_result = send_to_chatgpt_in_current_surface(
                        handoff_text,
                        require_conversation=False,
                        require_target_chat=False,
                        preferred_hint=hint or None,
                        project_page_mode=True,
                        send_context="rotation_handoff",
                    )
                    last_send_result = dict(send_result)
                    last_signal = str(send_result.get("signal", "")).strip()
                    last_warning = str(send_result.get("warning", "")).strip()
                    sent = True
                    break
                except BridgeError as exc:
                    last_error = str(exc)
            if not sent:
                if attempt < 2:
                    time.sleep(1.0)
                    continue
                raise BridgeError(
                    "project ページの『＜project名＞ 内の新しいチャット』入力欄へ handoff を送れませんでした。"
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
                info = dict(info)
                if last_signal:
                    info["signal"] = last_signal
                if last_warning:
                    info["warning"] = last_warning
                info.update(_attach_outcome_fields(None))
                for key, value in last_send_result.items():
                    if key.startswith("github_source_attach_") or key == "request_send_continued_without_github_source":
                        info[key] = value
                return info
            time.sleep(0.5)

        if last_signal == "submitted_unconfirmed":
            try:
                info = frontmost_safari_tab_info(config, require_conversation=False)
            except BridgeError:
                info = {"url": project_page_url, "title": ""}
            info = dict(info)
            info["signal"] = last_signal
            if last_warning:
                info["warning"] = last_warning
            info.update(_attach_outcome_fields(None))
            for key, value in last_send_result.items():
                if key.startswith("github_source_attach_") or key == "request_send_continued_without_github_source":
                    info[key] = value
            return info

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
        f" signal: {last_signal or 'unknown'} {last_error}".rstrip()
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
    allow_project_page_wait: bool = False,
) -> str:
    with open_chatgpt_page(
        reset_chat=False,
        require_conversation=not allow_project_page_wait,
        require_target_chat=not allow_project_page_wait,
        surface_label="project ページ / 新チャット待機" if allow_project_page_wait else None,
        allow_conversation_transition=allow_project_page_wait,
    ) as (_, page, config, front_tab):
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
        last_reply_readiness_signature = ""
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
            except BridgeError as exc:
                readiness_status = str(getattr(exc, "reply_readiness_status", "")).strip()
                if readiness_status:
                    readiness_details = {
                        "reply_readiness_status": readiness_status,
                        "reply_readiness_reason": str(
                            getattr(exc, "reply_readiness_reason", "")
                        ).strip(),
                        "assistant_text_present": bool(
                            getattr(exc, "assistant_text_present", False)
                        ),
                        "assistant_final_content_present": bool(
                            getattr(exc, "assistant_final_content_present", False)
                        ),
                        "assistant_meta_only": bool(
                            getattr(exc, "assistant_meta_only", False)
                        ),
                        "thinking_visible": bool(getattr(exc, "thinking_visible", False)),
                        "decision_marker_present": bool(
                            getattr(exc, "decision_marker_present", False)
                        ),
                        "body_block_start_present": bool(
                            getattr(exc, "body_block_start_present", False)
                        ),
                        "body_block_end_present": bool(
                            getattr(exc, "body_block_end_present", False)
                        ),
                        "partial_body_block_detected": bool(
                            getattr(exc, "partial_body_block_detected", False)
                        ),
                        "open_body_blocks": list(
                            getattr(exc, "open_body_blocks", []) or []
                        ),
                        "contract_parse_attempted": bool(
                            getattr(exc, "contract_parse_attempted", False)
                        ),
                        "reply_complete_tag_present": bool(
                            getattr(exc, "reply_complete_tag_present", False)
                        ),
                    }
                    signature = "|".join(
                        [
                            readiness_details["reply_readiness_status"],
                            readiness_details["reply_readiness_reason"],
                            str(readiness_details["assistant_text_present"]),
                            str(readiness_details["assistant_final_content_present"]),
                            str(readiness_details["assistant_meta_only"]),
                            str(readiness_details["thinking_visible"]),
                            str(readiness_details["decision_marker_present"]),
                            str(readiness_details["body_block_start_present"]),
                            str(readiness_details["body_block_end_present"]),
                            str(readiness_details["partial_body_block_detected"]),
                            str(readiness_details["open_body_blocks"]),
                            str(readiness_details["contract_parse_attempted"]),
                            str(readiness_details["reply_complete_tag_present"]),
                        ]
                    )
                    if (
                        stage_callback is not None
                        and signature
                        and signature != last_reply_readiness_signature
                    ):
                        stage_callback(
                            ChatGPTWaitEvent(
                                "reply_not_ready",
                                latest_text,
                                readiness_details,
                            )
                        )
                        last_reply_readiness_signature = signature
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


def wait_for_issue_centric_reply_text(
    *,
    plan_a_extractor: Callable[[str, str | None], Any],
    timeout_seconds: int | None = None,
    request_text: str | None = None,
    stage_callback: Callable[[ChatGPTWaitEvent], None] | None = None,
    allow_project_page_wait: bool = False,
) -> str:
    """Wait for a ChatGPT reply that satisfies the issue-centric (Plan A) contract extractor.

    ``plan_a_extractor`` is the sole extraction path (IC-only).  Legacy
    visible-text reply extraction (===CHATGPT_PROMPT_REPLY=== / ===CHATGPT_NO_CODEX===)
    is no longer a success path.  Detection of legacy replies is handled by the
    caller (fetch_next_prompt.run classifies legacy as an explicit BridgeStop).
    Polling continues until plan_a_extractor succeeds or the timeout is reached.
    """
    return _wait_for_chatgpt_reply_text(
        timeout_seconds=timeout_seconds,
        request_text=request_text,
        extractor=plan_a_extractor,
        stage_callback=stage_callback,
        allow_project_page_wait=allow_project_page_wait,
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
    issue_centric_next_request_section: str | None = None,
) -> str:
    summary = compact_last_report_text(last_report)
    contract = _build_ic_reply_contract_section()
    status_view = present_bridge_status(state)
    status_text = current_status or build_issue_centric_request_status(
        state,
        fallback_text=f"{status_view.label}: {status_view.detail}",
    )
    next_request_section = issue_centric_next_request_section
    if next_request_section is None:
        _, next_request_section = prepare_issue_centric_runtime_mode(state)
    return (
        "次チャットへそのまま貼る完成済みの最初のメッセージだけを書いてください。\n"
        "これは要約メモではありません。新しいチャットの最初の 1 通として、そのまま送れる本文だけを返してください。\n"
        "前置き、見出し、箇条書き、補足説明、冗長な要約は禁止です。\n"
        "今回必要な差分だけを短く含めてください。\n"
        "本文の流れは project 前提 / 現在進捗 / 直前完了 / next request / bridge reply contract の順に固定してください。\n\n"
        "返答は前置きなしで次のブロックだけにしてください。\n\n"
        f"{HANDOFF_REPLY_START}\n"
        "[新しいチャットの最初のメッセージ本文だけ]\n"
        f"{HANDOFF_REPLY_END}\n\n"
        "## current_status\n"
        f"{status_text}\n\n"
        "## last_report\n"
        f"{summary}\n\n"
        "## next_request\n"
        f"- next_todo: {next_todo}\n"
        f"- open_questions: {open_questions}\n\n"
        f"{next_request_section}"
        "## bridge_reply_contract\n"
        f"{contract}\n"
    ).strip() + "\n"


_DEFAULT_REQUEST_GUIDANCE = (
    "次の Codex 用 1 フェーズ prompt だけを返してください。\n"
    "共通ルールは固定 docs 側にあるので、今回差分だけに集中してください。\n"
    "Git ルールや worker 共通ルールを prompt 本文へ長く重複記載せず、必要なら `追加確認 docs` だけを短く足してください。"
)

_LIFECYCLE_ONLY_REQUEST_GUIDANCE = (
    "今回は新しい Codex 用 prompt を作りません。lifecycle automation だけを issue-centric contract で判断してください。\n"
    "返答は action=no_action を基本とし、issue を閉じるなら close_current_issue=true を添えてください。\n"
    "CHATGPT_CODEX_BODY は返さないでください。"
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
    issue_centric_next_request_section: str | None = None,
    request_guidance: str | None = None,
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

    next_request_section = issue_centric_next_request_section
    if next_request_section is None:
        _, next_request_section = prepare_issue_centric_runtime_mode(state)
    values = {
        "CURRENT_STATUS": current_status or build_issue_centric_request_status(state),
        "LAST_REPORT": compact_last_report_text(last_report or read_last_report_text(state)),
        "NEXT_TODO": next_todo,
        "OPEN_QUESTIONS": open_questions,
        "ISSUE_CENTRIC_NEXT_REQUEST_SECTION": next_request_section or "",
        "RESUME_CONTEXT_SECTION": resume_section,
        "REQUEST_GUIDANCE": (request_guidance if request_guidance is not None else _DEFAULT_REQUEST_GUIDANCE),
    }
    result = render_template(template_text, values).strip() + "\n"
    result = result.rstrip("\n") + "\n\n" + _build_ic_reply_contract_section() + "\n"
    return result


def build_issue_centric_request_status(
    state: Mapping[str, Any],
    *,
    fallback_text: str | None = None,
) -> str:
    base = fallback_text or state_snapshot(state)
    state_bridge = resolve_issue_centric_state_bridge(state, repo_root=ROOT_DIR)
    if state_bridge is not None:
        bridge_lines = [
            "- issue_centric_state_view: " + str(state_bridge.state_view).strip(),
            "- issue_centric_state_view_reason: " + str(state_bridge.state_view_reason).strip(),
            "- issue_centric_state_view_source: " + str(state_bridge.state_view_source).strip(),
            "- issue_centric_wait_kind: " + str(state_bridge.wait_kind).strip(),
            "- issue_centric_wait_reason: " + str(state_bridge.wait_reason).strip(),
        ]
        rendered_bridge = "\n".join(line for line in bridge_lines if not line.endswith(": "))
        if rendered_bridge:
            base = f"{base}\n\n## issue_centric_state_bridge\n{rendered_bridge}".strip()
    runtime_mode = resolve_issue_centric_runtime_mode(state, repo_root=ROOT_DIR)
    if runtime_mode is not None:
        mode_lines = [
            "- issue_centric_runtime_mode: " + str(runtime_mode.runtime_mode).strip(),
            "- issue_centric_runtime_mode_reason: " + str(runtime_mode.runtime_mode_reason).strip(),
            "- issue_centric_runtime_mode_source: " + str(runtime_mode.runtime_mode_source).strip(),
            "- issue_centric_generation_lifecycle: " + str(runtime_mode.generation_lifecycle).strip(),
            "- issue_centric_generation_lifecycle_reason: " + str(runtime_mode.generation_lifecycle_reason).strip(),
            "- issue_centric_generation_lifecycle_source: " + str(runtime_mode.generation_lifecycle_source).strip(),
            "- issue_centric_freshness_status: " + str(runtime_mode.freshness_status).strip(),
            "- issue_centric_freshness_reason: " + str(runtime_mode.freshness_reason).strip(),
            "- issue_centric_freshness_source: " + str(runtime_mode.freshness_source).strip(),
            "- issue_centric_invalidation_status: " + str(runtime_mode.invalidation_status).strip(),
            "- issue_centric_invalidation_reason: " + str(runtime_mode.invalidation_reason).strip(),
            "- issue_centric_runtime_generation_id: " + str(runtime_mode.generation_id).strip(),
            "- issue_centric_next_request_target: " + str(runtime_mode.target_issue).strip(),
        ]
        rendered_mode = "\n".join(line for line in mode_lines if not line.endswith(": "))
        if rendered_mode:
            base = f"{base}\n\n## issue_centric_runtime_mode\n{rendered_mode}".strip()
    runtime_snapshot = resolve_issue_centric_runtime_snapshot(state, repo_root=ROOT_DIR)
    if runtime_snapshot is not None:
        snapshot_lines = [
            "- issue_centric_snapshot_status: " + str(runtime_snapshot.snapshot_status).strip(),
            "- issue_centric_snapshot_source: " + str(runtime_snapshot.snapshot_source).strip(),
            "- issue_centric_route_selected: " + str(runtime_snapshot.route_selected).strip(),
            "- issue_centric_principal_issue: " + str(runtime_snapshot.principal_issue).strip(),
            "- issue_centric_next_request_target: " + str(runtime_snapshot.target_issue).strip(),
        ]
        rendered_snapshot = "\n".join(line for line in snapshot_lines if not line.endswith(": "))
        if rendered_snapshot:
            base = f"{base}\n\n## issue_centric_runtime_snapshot\n{rendered_snapshot}".strip()
    summary = load_issue_centric_normalized_summary(state, repo_root=ROOT_DIR)
    if summary is None:
        return base
    rendered = render_issue_centric_summary_for_request(summary)
    if not rendered.strip():
        return base
    return f"{base}\n\n## issue_centric_summary\n{rendered}".strip()


def prepare_issue_centric_next_request_context(
    state: Mapping[str, Any],
) -> tuple[IssueCentricNextRequestContext | None, str]:
    runtime_mode, section = prepare_issue_centric_runtime_mode(state)
    if runtime_mode is None:
        return None, section
    return (
        IssueCentricNextRequestContext(
            target_issue=runtime_mode.target_issue,
            target_issue_source=runtime_mode.target_issue_source,
            next_request_hint=runtime_mode.next_request_hint,
            principal_issue_kind=runtime_mode.principal_issue_kind,
            used_normalized_summary=runtime_mode.runtime_mode == "issue_centric_ready",
            fallback_reason=runtime_mode.fallback_reason or runtime_mode.runtime_mode_reason,
            summary_path=runtime_mode.normalized_summary_path,
        ),
        section,
    )


def prepare_issue_centric_next_request_route_selection(
    state: Mapping[str, Any],
) -> tuple[IssueCentricRouteSelection | None, str]:
    runtime_mode, section = prepare_issue_centric_runtime_mode(state)
    if runtime_mode is None:
        return None, section
    return (
        IssueCentricRouteSelection(
            route_selected="issue_centric" if runtime_mode.runtime_mode == "issue_centric_ready" else "fallback_legacy",
            target_issue=runtime_mode.target_issue,
            target_issue_source=runtime_mode.target_issue_source,
            next_request_hint=runtime_mode.next_request_hint,
            principal_issue_kind=runtime_mode.principal_issue_kind,
            used_normalized_summary=runtime_mode.runtime_mode == "issue_centric_ready",
            fallback_reason=runtime_mode.fallback_reason or runtime_mode.runtime_mode_reason,
            summary_path=runtime_mode.normalized_summary_path,
        ),
        section,
    )


def prepare_issue_centric_next_request_recovery(
    state: Mapping[str, Any],
) -> tuple[IssueCentricRecoveryContext | None, str]:
    snapshot, section = prepare_issue_centric_runtime_snapshot(state)
    if snapshot is None:
        return None, section
    return (
        IssueCentricRecoveryContext(
            recovery_status=snapshot.recovery_status,
            recovery_source=snapshot.recovery_source,
            route_selected=snapshot.route_selected,
            target_issue=snapshot.target_issue,
            target_issue_source=snapshot.target_issue_source,
            next_request_hint=snapshot.next_request_hint,
            principal_issue=snapshot.principal_issue,
            principal_issue_kind=snapshot.principal_issue_kind,
            used_normalized_summary=snapshot.route_selected == "issue_centric",
            fallback_reason=snapshot.fallback_reason,
            summary_path=snapshot.normalized_summary_path,
            dispatch_result_path=snapshot.dispatch_result_path,
        ),
        section,
    )


def prepare_issue_centric_runtime_snapshot(
    state: Mapping[str, Any],
) -> tuple[IssueCentricRuntimeSnapshot | None, str]:
    config = load_project_config()
    repo_label = str(config.get("github_repository", "")).strip() or str(project_repo_path(config))
    snapshot = resolve_issue_centric_runtime_snapshot(state, repo_root=ROOT_DIR)
    if snapshot is None:
        return None, ""
    section = render_issue_centric_next_request_section(snapshot, repo_label=repo_label)
    return snapshot, section


def prepare_issue_centric_runtime_mode(
    state: Mapping[str, Any],
) -> tuple[IssueCentricRuntimeMode | None, str]:
    config = load_project_config()
    repo_label = str(config.get("github_repository", "")).strip() or str(project_repo_path(config))
    runtime_mode = resolve_issue_centric_runtime_mode(state, repo_root=ROOT_DIR)
    if runtime_mode is None:
        return None, ""
    section = render_issue_centric_next_request_section(runtime_mode, repo_label=repo_label)
    return runtime_mode, section


def build_pinned_ready_issue_ic_section(ready_issue_ref: str) -> str:
    """Build a minimal issue-centric next-request section from a pinned ready issue ref.

    Used in ``request_prompt_from_report.run_resume_request`` when the current pending
    request was a ``ready_issue:`` entry (fresh start with explicit issue input) to
    prevent stale ``last_issue_centric_target_issue`` context (from a previous run) from
    being used as the target issue in the continuation request.

    The returned section includes only the pinned target_issue and the minimum context
    needed for ChatGPT to identify the current work item.  Route is treated as
    ``issue_centric`` by the caller so the IC reply contract is also appended.
    """
    config = load_project_config()
    repo_label = str(config.get("github_repository", "")).strip() or str(project_repo_path(config))
    ctx = IssueCentricNextRequestContext(
        target_issue=ready_issue_ref,
        target_issue_source="pinned_ready_issue",
        next_request_hint="ready_issue_active",
        principal_issue_kind="current_issue",
        used_normalized_summary=False,
        fallback_reason="",
        summary_path="",
    )
    return render_issue_centric_next_request_section(ctx, repo_label=repo_label)
