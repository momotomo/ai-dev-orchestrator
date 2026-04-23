#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import launch_codex_once
from _bridge_common import (
    BridgeError,
    BridgeStop,
    clear_chat_rotation_fields,
    clear_error_fields,
    clear_pending_request_fields,
    guarded_main,
    load_project_config,
    load_state,
    log_text,
    mark_next_request_requires_rotation,
    next_request_rotation_reason,
    project_repo_path,
    read_pending_request_text,
    read_text,
    repo_relative,
    runtime_prompt_path,
    save_state,
    send_to_chatgpt,
    stable_text_hash,
    should_rotate_before_next_chat_request,
    wait_for_issue_centric_reply_text,
    write_text,
)
from issue_centric_codex_launch import launch_issue_centric_codex_run
from issue_centric_close_current_issue import execute_close_current_issue
from issue_centric_current_issue_project_state import execute_current_issue_project_state_sync
from issue_centric_parent_update import execute_parent_issue_update_after_close
from issue_centric_human_review import execute_human_review_action
from issue_centric_contract import (
    CHATGPT_TURN_MARKER,
    CODEX_BODY_END,
    CODEX_BODY_START,
    DECISION_JSON_END,
    DECISION_JSON_START,
    FOLLOWUP_ISSUE_BODY_END,
    FOLLOWUP_ISSUE_BODY_START,
    IssueCentricAction,
    IssueCentricContractError,
    IssueCentricContractNotFound,
    IssueCentricDecision,
    ISSUE_BODY_END,
    ISSUE_BODY_START,
    REPLY_COMPLETE_TAG,
    REVIEW_BODY_END,
    REVIEW_BODY_START,
    USER_TURN_MARKER,
    parse_issue_centric_reply,
)
from issue_centric_codex_run import execute_codex_run_action
from issue_centric_execution import dispatch_issue_centric_execution
from issue_centric_followup_issue import execute_followup_issue_action
from issue_centric_github import IssueCentricGitHubError, resolve_target_issue
from issue_centric_issue_create import execute_issue_create_action
from issue_centric_transport import (
    IssueCentricBodyDecodeError,
    IssueCentricTransportError,
    materialize_issue_centric_decision,
)


class IssueCentricReplyInvalid(Exception):
    def __init__(
        self,
        detail: str,
        *,
        raw_text: str,
        readiness: "IssueCentricReplyReadiness | None" = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.raw_text = raw_text
        self.readiness = readiness


@dataclass(frozen=True)
class IssueCentricReplyReadiness:
    status: str
    reason: str
    assistant_text_present: bool
    thinking_visible: bool
    decision_marker_present: bool
    contract_parse_attempted: bool
    # True only when the assistant area contains final reply content
    # (not just meta-only UI labels).
    assistant_final_content_present: bool = False
    # True when assistant area has text but it is all meta-only UI labels.
    assistant_meta_only: bool = False
    # True when at least one body block start marker is visible.
    body_block_start_present: bool = False
    # True when at least one body block end marker is visible.
    body_block_end_present: bool = False
    # True when a body block start marker is present but its end marker is not
    # yet present — the reply is still being generated.
    partial_body_block_detected: bool = False
    # Names of open (start-only) body blocks, e.g. ["===CHATGPT_CODEX_BODY==="]
    open_body_blocks: tuple[str, ...] = ()
    # True when the terminal completion tag (===CHATGPT_REPLY_COMPLETE===) is
    # present in the assistant segment.  Only when this is True does the bridge
    # proceed to parse / validate the issue-centric contract.
    reply_complete_tag_present: bool = False
    # Source route used by reply detection.  "assistant_segment" when the
    # normal role-marker segment (ChatGPT: … あなた:) was used;
    # "raw_text_contract_fallback" when the role marker was absent and a
    # complete contract was found by scanning raw_text directly.
    reply_source: str = "assistant_segment"
    decision: IssueCentricDecision | None = None


class IssueCentricReplyNotReady(BridgeError):
    def __init__(self, readiness: IssueCentricReplyReadiness) -> None:
        super().__init__(readiness.reason)
        self.reply_readiness_status = readiness.status
        self.reply_readiness_reason = readiness.reason
        self.assistant_text_present = readiness.assistant_text_present
        self.thinking_visible = readiness.thinking_visible
        self.decision_marker_present = readiness.decision_marker_present
        self.contract_parse_attempted = readiness.contract_parse_attempted
        self.assistant_final_content_present = readiness.assistant_final_content_present
        self.assistant_meta_only = readiness.assistant_meta_only
        self.body_block_start_present = readiness.body_block_start_present
        self.body_block_end_present = readiness.body_block_end_present
        self.partial_body_block_detected = readiness.partial_body_block_detected
        self.open_body_blocks = readiness.open_body_blocks
        self.reply_complete_tag_present = readiness.reply_complete_tag_present
        self.reply_source = readiness.reply_source


import re as _re

# Markers that indicate the assistant area shows only in-progress UI metadata
# (thinking spinners, source pills, connector labels) and not final reply content.
_THINKING_MARKERS = (
    "思考中",
    "じっくり思考",
    "Thinking",
    "Reasoning",
)
# Additional UI-only labels that mean the reply is not yet final.
# These are matched as whole-line exact strings (after strip) or via regex.
_META_ONLY_EXACT = frozenset(
    [
        "GitHub",
        "Deep research",
        "ウェブ検索",
        "思考中",
        "じっくり思考",
        "Thinking",
        "Reasoning",
        "ChatGPT",
        # Tool-call status labels shown while ChatGPT is executing an app/plugin.
        "Running app request",
        "Running app response",
        "Received app response",
    ]
)
# Regex patterns matched against individual stripped lines.
_META_ONLY_PATTERNS = (
    _re.compile(r"^Thought for \d+ seconds?$"),
    _re.compile(r"^Thought for \d+s$"),
    _re.compile(r"^Searched \d+ sites?$"),
    _re.compile(r"^読み込み中"),
    _re.compile(r"^Loading"),
    # Catch future variants of tool-call status labels.
    _re.compile(r"^Running app "),
    _re.compile(r"^Received app "),
)


def _line_is_meta_only(line: str) -> bool:
    """Return True if a stripped non-empty line is a UI metadata label only."""
    if line in _META_ONLY_EXACT:
        return True
    if any(marker in line for marker in _THINKING_MARKERS):
        return True
    return any(pat.search(line) for pat in _META_ONLY_PATTERNS)


_NON_FINAL_ASSISTANT_LINE_MARKERS = (
    "ChatGPT の回答は必ずしも正しいとは限りません。",
    "重要な情報は確認するようにしてください。",
    "cookie の設定を参照してください。",
    "ChatGPT can make mistakes.",
    "Check important info.",
)

# Body block start/end pairs.  Used to detect partial (open) blocks that
# indicate the reply is still being generated.
_BODY_BLOCK_PAIRS: tuple[tuple[str, str], ...] = (
    ("===CHATGPT_CODEX_BODY===", "===END_CODEX_BODY==="),
    ("===CHATGPT_ISSUE_BODY===", "===END_ISSUE_BODY==="),
    ("===CHATGPT_REVIEW===", "===END_REVIEW==="),
    ("===CHATGPT_FOLLOWUP_ISSUE_BODY===", "===END_FOLLOWUP_ISSUE_BODY==="),
)


def _detect_partial_body_blocks(
    segment: str,
) -> tuple[list[str], list[str]]:
    """Return (open_blocks, closed_blocks).

    open_blocks: body blocks where start marker is present but end marker is
        not yet present — the reply is still being generated.
    closed_blocks: body blocks where both start and end markers are present.
    """
    open_blocks: list[str] = []
    closed_blocks: list[str] = []
    for start, end in _BODY_BLOCK_PAIRS:
        if start in segment:
            if end in segment:
                closed_blocks.append(start)
            else:
                open_blocks.append(start)
    return open_blocks, closed_blocks
# Legacy visible-text reply markers — detect-only, explicit-stop.
# These markers belong to the old visible-text reply contract that predates the
# issue-centric contract.  They are kept here so that an accidental / stale
# old-format ChatGPT reply is detected and triggers an explicit BridgeStop
# rather than being silently misclassified.  Fetch does NOT succeed via this
# path; canonical replies must use the issue-centric contract only.
# New requests always include `build_issue_centric_reply_contract_section()`,
# so ChatGPT should never produce these markers in normal operation.
_LEGACY_REPLY_MARKERS = (
    "===CHATGPT_PROMPT_REPLY===",
    "===CHATGPT_NO_CODEX===",
)


def _reply_search_start_index(raw_text: str, after_text: str | None = None) -> int:
    search_start = 0
    if after_text:
        anchor = raw_text.rfind(after_text)
        if anchor != -1:
            search_start = anchor + len(after_text)
    if search_start == 0:
        last_user_turn = raw_text.rfind(USER_TURN_MARKER)
        if last_user_turn != -1:
            search_start = last_user_turn
    return search_start


def _assistant_segment_after_text(raw_text: str, after_text: str | None = None) -> str:
    search_start = _reply_search_start_index(raw_text, after_text)
    assistant_start = raw_text.find(CHATGPT_TURN_MARKER, search_start)
    if assistant_start == -1:
        assistant_start = raw_text.rfind(CHATGPT_TURN_MARKER)
    if assistant_start == -1:
        return ""
    next_user_turn = raw_text.find(USER_TURN_MARKER, assistant_start + len(CHATGPT_TURN_MARKER))
    segment_end = next_user_turn if next_user_turn != -1 else len(raw_text)
    return raw_text[assistant_start:segment_end]


def _assistant_lines_for_readiness(raw_text: str, after_text: str | None = None) -> list[str]:
    segment = _assistant_segment_after_text(raw_text, after_text)
    if not segment:
        return []
    body = segment
    if body.startswith(CHATGPT_TURN_MARKER):
        body = body[len(CHATGPT_TURN_MARKER) :]
    filtered_lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(marker in line for marker in _NON_FINAL_ASSISTANT_LINE_MARKERS):
            continue
        filtered_lines.append(line)
    return filtered_lines


def _split_meta_content_lines(
    lines: list[str],
) -> tuple[list[str], list[str]]:
    """Split lines into (meta_only_lines, final_content_lines).

    meta_only_lines: lines that are UI metadata labels only (thinking spinners,
        source pills, connector names – not real reply content).
    final_content_lines: lines that contain actual reply content.
    """
    meta: list[str] = []
    content: list[str] = []
    for line in lines:
        if _line_is_meta_only(line):
            meta.append(line)
        else:
            content.append(line)
    return meta, content


def _find_last_complete_ic_contract_in_raw(raw_text: str, *, search_start: int = 0) -> str | None:
    """Return the last complete IC contract slice starting at or after search_start.

    A contract is considered complete when:
    - DECISION_JSON_START … DECISION_JSON_END block is present
    - REPLY_COMPLETE_TAG appears after DECISION_JSON_END

    search_start limits the scan to raw_text[search_start:] so that stale
    contracts from previous chat turns are not misidentified as the current
    reply.  Callers should pass _reply_search_start_index(raw_text, after_text)
    to ensure only the pending-request window is searched.

    Returns a slice of raw_text starting at the last qualifying
    DECISION_JSON_START and ending at the next USER_TURN_MARKER or end-of-text.
    Returns None if no complete contract is found within the window.
    """
    last_valid_start = -1
    search_from = search_start
    while True:
        pos = raw_text.find(DECISION_JSON_START, search_from)
        if pos == -1:
            break
        end_pos = raw_text.find(DECISION_JSON_END, pos + len(DECISION_JSON_START))
        if end_pos != -1:
            after_end = end_pos + len(DECISION_JSON_END)
            if REPLY_COMPLETE_TAG in raw_text[after_end:]:
                last_valid_start = pos
        search_from = pos + 1

    if last_valid_start == -1:
        return None

    # Slice from last_valid_start to the next USER_TURN_MARKER or end of text.
    next_user = raw_text.find(USER_TURN_MARKER, last_valid_start)
    slice_end = next_user if next_user != -1 else len(raw_text)
    return raw_text[last_valid_start:slice_end]


def classify_issue_centric_reply_readiness(
    raw_text: str,
    *,
    after_text: str | None = None,
) -> IssueCentricReplyReadiness:
    assistant_segment = _assistant_segment_after_text(raw_text, after_text)
    reply_source = "assistant_segment"

    # ── Raw-text fallback: role marker absent but contract present in raw_text ──
    # When the browser DOM does not emit the "ChatGPT:" role label, the
    # assistant-segment route returns an empty string.  Before declaring
    # not-ready, scan raw_text directly for the last complete IC contract
    # candidate within the pending-request window.
    # The window is determined by _reply_search_start_index (same logic as the
    # normal assistant-segment route): after_text anchor → last USER_TURN_MARKER
    # → 0.  This prevents stale contracts from previous chat turns from being
    # misidentified as the current reply.
    if not assistant_segment:
        _fallback_start = _reply_search_start_index(raw_text, after_text)
        _fallback = _find_last_complete_ic_contract_in_raw(
            raw_text, search_start=_fallback_start
        )
        if _fallback is not None:
            assistant_segment = _fallback
            reply_source = "raw_text_contract_fallback"

    # Derive assistant lines.  For the raw-text fallback the segment does not
    # start with the role marker, so we compute lines directly from it instead
    # of re-calling _assistant_lines_for_readiness (which calls
    # _assistant_segment_after_text internally and would return empty again).
    if reply_source == "raw_text_contract_fallback":
        assistant_lines: list[str] = [
            line.strip()
            for raw_line in assistant_segment.splitlines()
            if (line := raw_line.strip())
            and not any(m in line for m in _NON_FINAL_ASSISTANT_LINE_MARKERS)
        ]
    else:
        assistant_lines = _assistant_lines_for_readiness(raw_text, after_text)

    assistant_text_present = bool(assistant_lines)
    meta_lines, content_lines = _split_meta_content_lines(assistant_lines)
    assistant_final_content_present = bool(content_lines)
    assistant_meta_only = assistant_text_present and not assistant_final_content_present
    thinking_visible = any(
        any(marker in line for marker in _THINKING_MARKERS) for line in assistant_lines
    )
    decision_marker_present = "===CHATGPT_DECISION_JSON===" in assistant_segment
    legacy_marker_present = any(marker in assistant_segment for marker in _LEGACY_REPLY_MARKERS)
    reply_complete_tag_present = REPLY_COMPLETE_TAG in assistant_segment

    # Always compute body block state so diagnostic fields are accurate even
    # when returning early via the terminal tag gate.
    open_blocks, closed_blocks = _detect_partial_body_blocks(assistant_segment)
    partial_body_block = bool(open_blocks)
    body_block_start_present = bool(open_blocks) or bool(closed_blocks)
    body_block_end_present = bool(closed_blocks)

    # ── Gate 1: no text at all ──────────────────────────────────────────────
    if not assistant_text_present:
        return IssueCentricReplyReadiness(
            status="reply_not_ready",
            reason="assistant final reply is not visible yet.",
            assistant_text_present=False,
            thinking_visible=False,
            decision_marker_present=False,
            contract_parse_attempted=False,
            assistant_final_content_present=False,
            assistant_meta_only=False,
            reply_complete_tag_present=False,
            reply_source=reply_source,
        )

    # ── Gate 1b: legacy contract detection — detect-only, explicit-stop ───
    # Legacy replies (===CHATGPT_PROMPT_REPLY=== / ===CHATGPT_NO_CODEX===) do
    # not carry the issue-centric terminal tag.  Detect them early so that
    # run() can raise BridgeStop immediately; fetch does NOT continue through
    # a legacy success path.  The canonical reply format is the issue-centric
    # contract only; this gate is retained as a backward-compat safety net.
    if legacy_marker_present:
        return IssueCentricReplyReadiness(
            status="reply_complete_legacy_contract",
            reason="legacy visible-text reply contract is present.",
            assistant_text_present=True,
            thinking_visible=thinking_visible,
            decision_marker_present=False,
            contract_parse_attempted=False,
            assistant_final_content_present=assistant_final_content_present,
            assistant_meta_only=assistant_meta_only,
            reply_complete_tag_present=reply_complete_tag_present,
            reply_source=reply_source,
        )

    # ── Gate 2 (PRIMARY): terminal tag absent → always not-ready ───────────
    # This covers every in-progress state: thinking spinners, source pills,
    # partial body blocks, plain assistant text without the contract, etc.
    # Nothing below this gate is reachable until REPLY_COMPLETE_TAG appears.
    if not reply_complete_tag_present:
        if assistant_meta_only:
            reason_suffix = " (assistant area shows only UI metadata labels)"
        elif partial_body_block:
            reason_suffix = f" (partial body block: {open_blocks})"
        elif not assistant_final_content_present:
            reason_suffix = " (no final content lines visible)"
        else:
            reason_suffix = ""
        return IssueCentricReplyReadiness(
            status="reply_not_ready",
            reason=f"completion tag {REPLY_COMPLETE_TAG!r} is not yet present." + reason_suffix,
            assistant_text_present=True,
            thinking_visible=thinking_visible,
            decision_marker_present=decision_marker_present,
            contract_parse_attempted=False,
            assistant_final_content_present=assistant_final_content_present,
            assistant_meta_only=assistant_meta_only,
            body_block_start_present=body_block_start_present,
            body_block_end_present=body_block_end_present,
            partial_body_block_detected=partial_body_block,
            open_body_blocks=tuple(open_blocks),
            reply_complete_tag_present=False,
            reply_source=reply_source,
        )

    # ── Terminal tag is present — proceed to parse / validate ───────────────

    if not decision_marker_present:
        return IssueCentricReplyReadiness(
            status="reply_complete_no_marker",
            reason="completion tag present but issue-centric decision markers are missing.",
            assistant_text_present=True,
            thinking_visible=thinking_visible,
            decision_marker_present=False,
            contract_parse_attempted=False,
            assistant_final_content_present=assistant_final_content_present,
            assistant_meta_only=assistant_meta_only,
            reply_complete_tag_present=True,
            reply_source=reply_source,
        )

    try:
        decision = parse_issue_centric_reply(assistant_segment)
    except IssueCentricContractNotFound as exc:
        return IssueCentricReplyReadiness(
            status="reply_complete_invalid_contract",
            reason=str(exc),
            assistant_text_present=True,
            thinking_visible=thinking_visible,
            decision_marker_present=True,
            contract_parse_attempted=True,
            assistant_final_content_present=assistant_final_content_present,
            assistant_meta_only=assistant_meta_only,
            body_block_start_present=body_block_start_present,
            body_block_end_present=body_block_end_present,
            partial_body_block_detected=False,
            reply_complete_tag_present=True,
            reply_source=reply_source,
        )
    except IssueCentricContractError as exc:
        return IssueCentricReplyReadiness(
            status="reply_complete_invalid_contract",
            reason=str(exc),
            assistant_text_present=True,
            thinking_visible=thinking_visible,
            decision_marker_present=True,
            contract_parse_attempted=True,
            assistant_final_content_present=assistant_final_content_present,
            assistant_meta_only=assistant_meta_only,
            body_block_start_present=body_block_start_present,
            body_block_end_present=body_block_end_present,
            partial_body_block_detected=False,
            reply_complete_tag_present=True,
            reply_source=reply_source,
        )

    return IssueCentricReplyReadiness(
        status="reply_complete_valid_contract",
        reason="issue-centric contract parsed successfully.",
        assistant_text_present=True,
        thinking_visible=thinking_visible,
        decision_marker_present=True,
        contract_parse_attempted=True,
        assistant_final_content_present=assistant_final_content_present,
        assistant_meta_only=assistant_meta_only,
        body_block_start_present=body_block_start_present,
        body_block_end_present=body_block_end_present,
        partial_body_block_detected=False,
        reply_complete_tag_present=True,
        reply_source=reply_source,
        decision=decision,
    )


def parse_issue_centric_reply_for_fetch(
    raw_text: str,
    *,
    after_text: str | None = None,
) -> object:
    readiness = classify_issue_centric_reply_readiness(raw_text, after_text=after_text)
    if readiness.status == "reply_not_ready":
        raise IssueCentricReplyNotReady(readiness)
    if readiness.status == "reply_complete_no_marker":
        raise IssueCentricReplyInvalid(
            readiness.reason,
            raw_text=raw_text,
            readiness=readiness,
        )
    # Note: reply_complete_legacy_contract is not gated here.  run() detects
    # legacy replies via readiness.status BEFORE calling this function, so
    # parse_issue_centric_reply_for_fetch() is not reached for legacy replies
    # during normal flow.  If called directly with a legacy-classified raw_text
    # (e.g. in tests), execution falls through to the `decision is None` check
    # below and raises BridgeError.
    if readiness.status == "reply_complete_invalid_contract":
        raise IssueCentricReplyInvalid(
            readiness.reason,
            raw_text=raw_text,
            readiness=readiness,
        )
    if readiness.decision is None:
        raise BridgeError("issue-centric contract reply が見つかりませんでした")
    return readiness.decision


# Maximum number of automatic contract correction requests per pending request.
_MAX_CONTRACT_CORRECTIONS = 2


def _is_retryable_contract_error(reason: str, status: str) -> bool:
    """Return True when the invalid-contract reason is one ChatGPT can fix by re-emitting.

    The following are considered retryable (ChatGPT formatting / content errors):
      - reply_complete_invalid_contract: the completion tag is present but contract
        parse failed for any reason (base64, JSON, block markers, field types, …)
      - reply_complete_no_marker: the completion tag is present but the decision
        markers are entirely missing (ChatGPT forgot to include the contract)

    The following are NOT retryable:
      - reply_not_ready: response is still incomplete, just wait
      - transport / execution / controller code errors
    """
    return status in {"reply_complete_invalid_contract", "reply_complete_no_marker"}


# ---------------------------------------------------------------------------
# Structured correction request helpers (Phase 26)
# ---------------------------------------------------------------------------
# Shared English/ASCII-first BODY regeneration guidance.  Included in ALL
# correction requests so ChatGPT regenerates BODY content in plain ASCII
# Markdown instead of Japanese / mixed-encoding prose.  Also carries the
# UTF-8 requirement so Phase-25 tests still pass.
_ENGLISH_BODY_GUIDANCE = (
    "BODY regeneration requirements:\n"
    "  - Regenerate BODY content as English, ASCII-first Markdown — write fresh from scratch\n"
    "  - Do NOT repair or reuse the previous base64 payload\n"
    "  - Do not include Japanese prose unless strictly unavoidable for the issue content\n"
    "  - Avoid full-width punctuation (。、「」), non-UTF-8 byte sequences, smart quotes,\n"
    "    and decorative symbols; keep text strictly valid UTF-8\n"
    "  - Prefer plain ASCII bullets (-), plain code fences (```), simple path notation\n"
    "  - Keep the BODY compact and bounded to the current issue scope\n"
    "  - Base64-encode the final plain text with correct padding\n"
)

_BODY_TAG_LIST = (
    f"  {ISSUE_BODY_START} … {ISSUE_BODY_END}\n"
    f"  {CODEX_BODY_START} … {CODEX_BODY_END}\n"
    f"  {REVIEW_BODY_START} … {REVIEW_BODY_END}\n"
    f"  {FOLLOWUP_ISSUE_BODY_START} … {FOLLOWUP_ISSUE_BODY_END}\n"
)

_DECISION_JSON_UNCHANGED_INSTRUCTION = (
    "- CHATGPT_DECISION_JSON の中身（action / target_issue / flags / summary）は一切変えないこと\n"
    f"- {DECISION_JSON_START} ～ {DECISION_JSON_END} マーカーを正確に配置すること\n"
)


def _classify_correction_reason(reason: str) -> str:
    """Classify a contract correction reason string into a diagnostic category.

    Returns one of: 'base64', 'utf8', 'marker', 'generic'.
    """
    r = reason.lower()
    if "not valid base64" in r or (
        "base64" in r and any(k in r for k in ("invalid", "excess", "padding", "incorrect"))
    ):
        return "base64"
    if "not valid utf-8" in r or "utf-8 markdown" in r or "decoded bytes are not valid" in r:
        return "utf8"
    if "marker" in r or "block marker" in r or "tag pairing" in r:
        return "marker"
    return "generic"


def _build_generic_correction_request(reason: str) -> str:
    """Generic correction request for JSON / unknown contract errors."""
    return (
        "前回の返答に issue-centric contract の不正がありました。\n"
        f"エラー詳細: {reason}\n\n"
        "canonical 形式で contract のみを再出力してください。余計な説明・謝罪・コメントは付けないこと。\n\n"
        + _DECISION_JSON_UNCHANGED_INSTRUCTION
        + "- BODY block が必要な場合のみ、以下の canonical tag と valid base64 payload（padding 含む）で出力すること:\n"
        + _BODY_TAG_LIST
        + _ENGLISH_BODY_GUIDANCE
    )


def _build_marker_correction_request(reason: str) -> str:
    """Structured correction request for BODY block marker pairing errors."""
    return (
        "[BODY block marker error]\n"
        "前回の返答に BODY block のマーカー不正（start/end ペアの不一致）がありました。\n"
        f"Error detail: {reason}\n\n"
        "Expected canonical tag pairs:\n"
        + _BODY_TAG_LIST
        + "\n"
        + _DECISION_JSON_UNCHANGED_INSTRUCTION
        + "Next action:\n"
        "  - Do NOT reuse or repair the previous BODY block content\n"
        "  - Re-emit each needed BODY block using only the exact canonical start/end tag pairs above\n"
        + _ENGLISH_BODY_GUIDANCE
    )


def _build_base64_correction_request(reason: str) -> str:
    """Structured correction request for BODY payload base64 encoding errors."""
    return (
        "[BODY base64 encoding error]\n"
        "前回の返答の BODY payload が valid base64 ではありませんでした。\n"
        f"Detail: {reason}\n\n"
        + _DECISION_JSON_UNCHANGED_INSTRUCTION
        + "Next action:\n"
        "  - Do NOT repair or modify the previous base64 payload — 変えないこと\n"
        "  - Write the BODY as fresh plain text, then re-encode as valid base64 with correct padding\n"
        + _ENGLISH_BODY_GUIDANCE
        + "Canonical tag pairs for BODY blocks:\n"
        + _BODY_TAG_LIST
    )


def _build_utf8_correction_request(reason: str) -> str:
    """Structured correction request for BODY payload UTF-8 decode errors."""
    return (
        "[BODY UTF-8 decode error]\n"
        "前回の返答の BODY payload は valid base64 ですが decoded bytes が valid UTF-8 ではありませんでした。\n"
        f"Detail: {reason}\n\n"
        + _DECISION_JSON_UNCHANGED_INSTRUCTION
        + "Next action:\n"
        "  - Do NOT repair or modify the previous base64 payload\n"
        "  - Write the BODY as fresh plain text, then base64-encode with correct padding\n"
        + _ENGLISH_BODY_GUIDANCE
        + "Canonical tag pairs for BODY blocks:\n"
        + _BODY_TAG_LIST
    )


def _build_contract_correction_request(reason: str) -> str:
    """Build a structured correction request dispatched by error category.

    Covers all retryable invalid-contract cases: malformed base64, invalid JSON,
    missing or broken block markers, field type errors, unknown action, etc.
    Uses only current canonical envelope tags — no legacy aliases, no completion tags.
    """
    category = _classify_correction_reason(reason)
    if category == "base64":
        return _build_base64_correction_request(reason)
    if category == "utf8":
        return _build_utf8_correction_request(reason)
    if category == "marker":
        return _build_marker_correction_request(reason)
    return _build_generic_correction_request(reason)


def _build_binding_mismatch_correction_request(reason: str, current_ready_issue_ref: str) -> str:
    """Build a correction request when target_issue does not match the current ready issue.

    Unlike the generic correction request, this explicitly tells ChatGPT which
    target_issue to use and forbids changing anything else.
    Uses only current canonical envelope tags — no legacy aliases, no completion tags.
    """
    target_issue_ref = current_ready_issue_ref.split(maxsplit=1)[0].strip()
    return (
        "前回の返答の target_issue が現在の ready issue と一致していませんでした。\n"
        f"エラー詳細: {reason}\n\n"
        "以下の点を修正して canonical 形式で contract のみを再出力してください。余計な説明・謝罪・コメントは付けないこと。\n\n"
        f"- `target_issue` は必ず `{target_issue_ref}` に合わせること\n"
        "- target_issue 以外の CHATGPT_DECISION_JSON フィールド（action / flags / summary）は変更しないこと\n"
        + _DECISION_JSON_UNCHANGED_INSTRUCTION
        + "- BODY block が必要な場合のみ、以下の canonical tag と valid base64 payload（padding 含む）で出力すること:\n"
        + _BODY_TAG_LIST
        + _ENGLISH_BODY_GUIDANCE
    )


def _guard_correction_resend(
    state: dict[str, object],
    raw_text: str,
    request_text: str | None,
) -> None:
    """Raise BridgeStop if no new assistant turn has appeared since the last correction was sent.

    When a correction request has been sent, the assistant segment (the ChatGPT
    turn visible in the pending-request window) must change before another
    correction is allowed.  If the segment hash matches the hash recorded at
    the time of the last correction send, ChatGPT has not yet responded to the
    previous correction and a second send would be premature.

    Only active when ``last_issue_centric_correction_send_hash`` is non-empty in
    state (i.e. at least one correction has already been sent this cycle).
    """
    sent_hash = str(state.get("last_issue_centric_correction_send_hash", "")).strip()
    if not sent_hash:
        return
    current_seg = _assistant_segment_after_text(raw_text, after_text=request_text or None)
    current_hash = stable_text_hash(current_seg.strip())
    if current_hash == sent_hash:
        raise BridgeStop(
            "問題: correction request を送信済みですが、ChatGPT からの新しい assistant turn がまだ確認できません。\n"
            "対応: ChatGPT が前回の correction request に返答するまで待ってから fetch を再実行してください。"
        )


def _build_not_ready_bridge_error(readiness: IssueCentricReplyReadiness) -> BridgeError:
    """Build a BridgeError for the not_ready route with structured operator diagnostics.

    Surfaces readiness.reason, open_body_blocks, and other diagnostic fields so
    the operator can see exactly why the reply is not yet ready.
    """
    lines = [
        "ChatGPT reply はまだ未完成です。もう一度 fetch を待ってください。",
        f"  reason: {readiness.reason}",
        f"  reply_complete_tag_present: {readiness.reply_complete_tag_present}",
    ]
    if readiness.assistant_meta_only:
        lines.append("  assistant_meta_only: true")
    if readiness.open_body_blocks:
        for block in readiness.open_body_blocks:
            lines.append(f"  open_body_block: {block}")
    if readiness.partial_body_block_detected:
        lines.append("  partial_body_block_detected: true")
    return BridgeError("\n".join(lines))


def stop_for_invalid_issue_centric_contract(
    state: dict[str, object],
    *,
    raw_text: str,
    detail: str,
    pending_request_source: str,
    raw_log_path: Path | None = None,
    readiness: IssueCentricReplyReadiness | None = None,
    correction_count: int = 0,
) -> None:
    raw_log = raw_log_path or log_text("raw_chatgpt_prompt_dump", raw_text, suffix="txt")
    readiness_lines: list[str] = []
    if readiness is not None:
        readiness_lines.extend(
            [
                f"- reply_readiness_status: {readiness.status}",
                f"- reply_readiness_reason: {readiness.reason}",
                f"- assistant_text_present: {readiness.assistant_text_present}",
                f"- assistant_final_content_present: {readiness.assistant_final_content_present}",
                f"- assistant_meta_only: {readiness.assistant_meta_only}",
                f"- thinking_visible: {readiness.thinking_visible}",
                f"- decision_marker_present: {readiness.decision_marker_present}",
                f"- reply_complete_tag_present: {readiness.reply_complete_tag_present}",
                f"- body_block_start_present: {readiness.body_block_start_present}",
                f"- body_block_end_present: {readiness.body_block_end_present}",
                f"- partial_body_block_detected: {readiness.partial_body_block_detected}",
                f"- open_body_blocks: {list(readiness.open_body_blocks)}",
                f"- contract_parse_attempted: {readiness.contract_parse_attempted}",
            ]
        )
    invalid_summary = "\n".join(
        [
            "# Invalid Issue-Centric Contract",
            "",
            f"- error: {detail}",
            f"- raw_dump: {repo_relative(raw_log)}",
            f"- pending_request_source: {pending_request_source or 'unknown'}",
            *readiness_lines,
        ]
    ).strip() + "\n"
    invalid_log = log_text("invalid_issue_centric_contract", invalid_summary, suffix="md")
    failed_state = dict(state)
    if correction_count > 0:
        action_guidance = (
            f"ChatGPT に自動修正依頼を {correction_count} 回試しましたが修正できませんでした。"
            " 内容を確認して再実行してください。"
        )
    else:
        action_guidance = "ChatGPT の返答を確認して再実行してください。"
    user_message = (
        f"問題: issue-centric contract reply が不正でした。\n"
        f"対応: {action_guidance}\n"
        f"詳細: raw dump: {repo_relative(raw_log)}"
        f" / invalid log: {repo_relative(invalid_log)}"
        f" / error: {detail}"
    )
    failed_state.update(
        {
            "mode": "awaiting_user",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "error": True,
            "error_message": user_message,
            "chatgpt_decision": "issue_centric_invalid_contract",
            "chatgpt_decision_note": detail,
            "last_issue_centric_decision_log": repo_relative(invalid_log),
            "last_issue_centric_metadata_log": "",
            "last_issue_centric_artifact_file": "",
            "reply_readiness_status": readiness.status if readiness is not None else "",
            "reply_readiness_reason": readiness.reason if readiness is not None else "",
            "assistant_text_present": readiness.assistant_text_present if readiness is not None else False,
            "assistant_final_content_present": readiness.assistant_final_content_present if readiness is not None else False,
            "assistant_meta_only": readiness.assistant_meta_only if readiness is not None else False,
            "thinking_visible": readiness.thinking_visible if readiness is not None else False,
            "decision_marker_present": readiness.decision_marker_present if readiness is not None else False,
            "body_block_start_present": readiness.body_block_start_present if readiness is not None else False,
            "body_block_end_present": readiness.body_block_end_present if readiness is not None else False,
            "partial_body_block_detected": readiness.partial_body_block_detected if readiness is not None else False,
            "open_body_blocks": list(readiness.open_body_blocks) if readiness is not None else [],
            "contract_parse_attempted": readiness.contract_parse_attempted if readiness is not None else False,
            "reply_complete_tag_present": readiness.reply_complete_tag_present if readiness is not None else False,
        }
    )
    save_state(failed_state)
    raise BridgeError(user_message)


# ---------------------------------------------------------------------------
# Plan A / legacy fallback route decision
# ---------------------------------------------------------------------------
# After classify_issue_centric_reply_readiness() returns, _resolve_ic_reply_route_decision()
# derives an explicit routing intent from the readiness status.
#
# The key distinction this helper makes:
#   "Plan A marker present but broken" (plan_a_present=True, route=stop_broken)
#   vs "Plan A marker absent" (plan_a_present=False, route=correction_retry)
#
# This ensures:
#   - Broken Plan A NEVER falls to a legacy success path (route is always stop_broken)
#   - Plan A absent routes to correction_retry (not legacy fallback)
#   - Legacy markers always produce legacy_stop regardless of Plan A state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _IcReplyRouteDecision:
    """Explicit routing intent derived from IssueCentricReplyReadiness.

    Separates "Plan A marker present but broken" from "Plan A marker absent"
    so that run() can apply the correct stop / retry policy without any risk
    of inadvertently falling to a legacy success path.

    Fields
    ------
    plan_a_present:   True when ===CHATGPT_DECISION_JSON=== was found in the
                      assistant segment (decision_marker_present was True).
    plan_a_parseable: True only when classify returned "reply_complete_valid_contract".
    legacy_present:   True when legacy reply markers (===CHATGPT_PROMPT_REPLY===
                      / ===CHATGPT_NO_CODEX===) were detected.

    route values
    ------------
    "ic_proceed"      — Plan A present and parsed OK; proceed to materialize + dispatch.
    "stop_broken"     — Plan A marker present but parse / validate failed.
                        Correction retry may be attempted before the final stop,
                        but the reply NEVER falls to a legacy success path.
    "legacy_stop"     — Legacy markers detected; explicit stop, no retry.
    "correction_retry"— Plan A marker absent, no legacy markers detected.
                        Correction request to ChatGPT to re-emit the full contract.
    "not_ready"       — Reply not yet complete; continue polling.
    """

    plan_a_present: bool
    plan_a_parseable: bool
    legacy_present: bool
    route: str  # "ic_proceed" | "stop_broken" | "legacy_stop" | "correction_retry" | "not_ready"


def _resolve_ic_reply_route_decision(
    readiness: IssueCentricReplyReadiness,
) -> _IcReplyRouteDecision:
    """Derive an explicit route decision from a readiness classification result.

    Invariants enforced by this function:
    - plan_a_present=True  → route is "ic_proceed" or "stop_broken" only.
                             Never "legacy_stop" or "correction_retry".
    - legacy_present=True  → route is "legacy_stop" always.
    - not_ready status     → route is "not_ready" always.
    - no marker, no legacy → route is "correction_retry" (Plan A absent).
    """
    plan_a_present = readiness.decision_marker_present
    plan_a_parseable = readiness.status == "reply_complete_valid_contract"
    legacy_present = readiness.status == "reply_complete_legacy_contract"

    if readiness.status == "reply_not_ready":
        return _IcReplyRouteDecision(
            plan_a_present=plan_a_present,
            plan_a_parseable=False,
            legacy_present=False,
            route="not_ready",
        )

    if legacy_present:
        return _IcReplyRouteDecision(
            plan_a_present=False,
            plan_a_parseable=False,
            legacy_present=True,
            route="legacy_stop",
        )

    if plan_a_parseable:
        return _IcReplyRouteDecision(
            plan_a_present=True,
            plan_a_parseable=True,
            legacy_present=False,
            route="ic_proceed",
        )

    if plan_a_present:
        # Decision marker was present but parse / validate failed (invalid_contract).
        # Explicit stop path — correction retry may be applied before the final stop,
        # but this NEVER falls to a legacy success path.
        return _IcReplyRouteDecision(
            plan_a_present=True,
            plan_a_parseable=False,
            legacy_present=False,
            route="stop_broken",
        )

    # Plan A marker absent (no_marker), no legacy detected → correction retry candidate.
    return _IcReplyRouteDecision(
        plan_a_present=False,
        plan_a_parseable=False,
        legacy_present=False,
        route="correction_retry",
    )


def _validate_ready_issue_target_binding(
    decision: IssueCentricDecision,
    *,
    state: dict[str, object],
    pending_request_source: str,
) -> str | None:
    if not pending_request_source.startswith("ready_issue:"):
        return None
    raw_ready_issue_ref = str(state.get("current_ready_issue_ref", "")).strip()
    if not raw_ready_issue_ref:
        return None
    expected_issue_ref = raw_ready_issue_ref.split(maxsplit=1)[0].strip()
    if not expected_issue_ref:
        return "current_ready_issue_ref から ready issue ref を抽出できませんでした。"
    raw_target_issue = str(decision.target_issue or "").strip()
    if not raw_target_issue:
        return (
            f"current ready issue は {raw_ready_issue_ref} に固定されていますが、"
            "contract reply が target_issue=none を返しました。"
        )

    project_config = load_project_config()
    default_repository = str(project_config.get("github_repository", "")).strip()
    try:
        expected_issue = resolve_target_issue(expected_issue_ref, default_repository=default_repository)
        actual_issue = resolve_target_issue(raw_target_issue, default_repository=default_repository)
    except IssueCentricGitHubError as exc:
        return f"ready issue binding validation failed: {exc}"

    if (
        expected_issue.repository != actual_issue.repository
        or expected_issue.issue_number != actual_issue.issue_number
    ):
        return (
            f"current ready issue は {raw_ready_issue_ref} ですが、"
            f"contract reply が stale target_issue {raw_target_issue} を返しました。"
            " ready issue request では current ready issue と一致する target_issue だけを受け入れます。"
        )
    return None


# ---------------------------------------------------------------------------
# Fetch handoff state family
# ---------------------------------------------------------------------------
# When fetch_next_prompt.run() receives a valid issue-centric contract reply,
# the state family passed to dispatch_issue_centric_execution is assembled in
# three steps:
#
#   1. _build_ic_fetch_handoff_state()   — capture what fetch learned from the
#      ChatGPT reply into an immutable snapshot
#   2. _apply_ic_continuation_reset()   — clear all prior-cycle continuation /
#      execution fields so stale values do not contaminate the fresh dispatch
#   3. _apply_ic_fetch_handoff_state()  — write the fetch-known values into
#      mutable_state (action, target, artifact metadata, readiness, correction
#      reset)
#
# The split makes the responsibility boundary explicit:
#   * continuation fields (produced by execution) → cleared by reset helper
#   * fetch-derived fields (from contract + materialized) → set by apply helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _IcFetchHandoffState:
    """Immutable snapshot of what fetch learned from a successful ChatGPT reply.

    Built after contract parse + artifact materialization.  Applied to
    mutable_state *before* dispatch_issue_centric_execution is called (or
    before the codex_run / initial_selection BridgeStop is raised).

    These fields represent only what the *fetch step* knows.  The continuation
    contract fields produced by *execution* are separately cleared by
    _apply_ic_continuation_reset() and then filled in by _finalize_dispatch().
    """

    # ── Decision identity ──────────────────────────────────────────────────
    action: str
    target_issue: str
    # ── Artifact metadata ──────────────────────────────────────────────────
    artifact_kind: str
    artifact_file: str
    metadata_log: str
    decision_log: str
    stop_reason: str
    # ── Reply identity ─────────────────────────────────────────────────────
    reply_hash: str
    processed_request_hash: str
    # ── Readiness diagnostics ──────────────────────────────────────────────
    readiness_status: str
    readiness_reason: str
    assistant_text_present: bool
    thinking_visible: bool
    decision_marker_present: bool
    contract_parse_attempted: bool


# All issue-centric state keys that track *prior-cycle* execution results and
# must be cleared at the start of every fresh fetch→dispatch cycle.  Keeping
# this list explicit makes it auditable and ensures nothing from the previous
# cycle leaks into the new one.
_IC_CONTINUATION_RESET_FIELDS: tuple[str, ...] = (
    # dispatch / summary
    "last_issue_centric_dispatch_result",
    "last_issue_centric_normalized_summary",
    # continuation contract
    "last_issue_centric_principal_issue",
    "last_issue_centric_principal_issue_kind",
    "last_issue_centric_next_request_hint",
    "last_issue_centric_next_request_target",
    "last_issue_centric_next_request_target_source",
    "last_issue_centric_next_request_fallback_reason",
    # route / recovery
    "last_issue_centric_route_selected",
    "last_issue_centric_route_fallback_reason",
    "last_issue_centric_recovery_status",
    "last_issue_centric_recovery_source",
    "last_issue_centric_recovery_fallback_reason",
    # runtime snapshot / generation
    "last_issue_centric_runtime_snapshot",
    "last_issue_centric_snapshot_status",
    "last_issue_centric_runtime_generation_id",
    "last_issue_centric_generation_lifecycle",
    "last_issue_centric_generation_lifecycle_reason",
    "last_issue_centric_generation_lifecycle_source",
    "last_issue_centric_prepared_generation_id",
    "last_issue_centric_pending_generation_id",
    # runtime mode / freshness
    "last_issue_centric_runtime_mode",
    "last_issue_centric_runtime_mode_reason",
    "last_issue_centric_runtime_mode_source",
    "last_issue_centric_freshness_status",
    "last_issue_centric_freshness_reason",
    "last_issue_centric_freshness_source",
    "last_issue_centric_invalidation_status",
    "last_issue_centric_invalidation_reason",
    "last_issue_centric_invalidated_generation_id",
    "last_issue_centric_consumed_generation_id",
    # execution
    "last_issue_centric_execution_status",
    "last_issue_centric_execution_log",
    "last_issue_centric_created_issue_number",
    "last_issue_centric_created_issue_url",
    "last_issue_centric_created_issue_title",
    "last_issue_centric_primary_issue_number",
    "last_issue_centric_primary_issue_url",
    "last_issue_centric_primary_issue_title",
    "last_issue_centric_resolved_issue",
    "last_issue_centric_trigger_comment_id",
    "last_issue_centric_trigger_comment_url",
    "last_issue_centric_execution_payload_log",
    # launch
    "last_issue_centric_launch_status",
    "last_issue_centric_launch_entrypoint",
    "last_issue_centric_launch_prompt_log",
    "last_issue_centric_launch_log",
    # continuation / report
    "last_issue_centric_continuation_status",
    "last_issue_centric_continuation_log",
    "last_issue_centric_report_status",
    "last_issue_centric_report_file",
    # project sync (current issue)
    "last_issue_centric_project_sync_status",
    "last_issue_centric_project_url",
    "last_issue_centric_project_item_id",
    "last_issue_centric_project_state_field",
    "last_issue_centric_project_state_value",
    # project sync (primary issue)
    "last_issue_centric_primary_project_sync_status",
    "last_issue_centric_primary_project_url",
    "last_issue_centric_primary_project_item_id",
    "last_issue_centric_primary_project_state_field",
    "last_issue_centric_primary_project_state_value",
    # followup
    "last_issue_centric_followup_status",
    "last_issue_centric_followup_log",
    "last_issue_centric_followup_parent_issue",
    "last_issue_centric_followup_issue_number",
    "last_issue_centric_followup_issue_url",
    "last_issue_centric_followup_issue_title",
    "last_issue_centric_followup_project_sync_status",
    "last_issue_centric_followup_project_url",
    "last_issue_centric_followup_project_item_id",
    "last_issue_centric_followup_project_state_field",
    "last_issue_centric_followup_project_state_value",
    # current project
    "last_issue_centric_current_project_item_id",
    "last_issue_centric_current_project_url",
    # lifecycle sync
    "last_issue_centric_lifecycle_sync_status",
    "last_issue_centric_lifecycle_sync_log",
    "last_issue_centric_lifecycle_sync_issue",
    "last_issue_centric_lifecycle_sync_stage",
    "last_issue_centric_lifecycle_sync_project_url",
    "last_issue_centric_lifecycle_sync_project_item_id",
    "last_issue_centric_lifecycle_sync_state_field",
    "last_issue_centric_lifecycle_sync_state_value",
    # close
    "last_issue_centric_close_status",
    "last_issue_centric_close_log",
    "last_issue_centric_closed_issue_number",
    "last_issue_centric_closed_issue_url",
    "last_issue_centric_closed_issue_title",
    "last_issue_centric_close_order",
    # parent update
    "last_issue_centric_parent_update_status",
    "last_issue_centric_parent_update_log",
    "last_issue_centric_parent_update_issue",
    "last_issue_centric_parent_update_comment_id",
    "last_issue_centric_parent_update_comment_url",
    "last_issue_centric_parent_update_closed_issue",
    # review
    "last_issue_centric_review_status",
    "last_issue_centric_review_log",
    "last_issue_centric_review_comment_id",
    "last_issue_centric_review_comment_url",
    "last_issue_centric_review_close_policy",
)


def _build_ic_fetch_handoff_state(
    contract_decision: IssueCentricDecision,
    materialized: object,
    *,
    readiness: IssueCentricReplyReadiness,
    decision_log: Path,
    pending_request_hash: str,
    prior_state: dict[str, object],
) -> _IcFetchHandoffState:
    """Build the fetch handoff state from a successfully parsed contract reply.

    Captures the immutable snapshot of what fetch learned: action, target,
    artifact metadata, readiness diagnostics, and reply hash.  This snapshot
    is later applied to mutable_state by _apply_ic_fetch_handoff_state().
    """
    reply_hash = stable_text_hash(contract_decision.raw_segment.strip())
    artifact_log_path = getattr(materialized, "artifact_log_path", None)
    prepared = getattr(materialized, "prepared", None)
    primary_body = getattr(prepared, "primary_body", None) if prepared is not None else None
    return _IcFetchHandoffState(
        action=contract_decision.action.value,
        target_issue=contract_decision.target_issue or "none",
        artifact_kind=(primary_body.kind.value if primary_body is not None else ""),
        artifact_file=(
            repo_relative(artifact_log_path) if artifact_log_path is not None else ""
        ),
        metadata_log=repo_relative(getattr(materialized, "metadata_log_path")),
        decision_log=repo_relative(decision_log),
        stop_reason=getattr(materialized, "safe_stop_reason", ""),
        reply_hash=reply_hash,
        processed_request_hash=(
            pending_request_hash
            or str(prior_state.get("last_processed_request_hash", "")).strip()
        ),
        readiness_status=readiness.status,
        readiness_reason=readiness.reason,
        assistant_text_present=readiness.assistant_text_present,
        thinking_visible=readiness.thinking_visible,
        decision_marker_present=readiness.decision_marker_present,
        contract_parse_attempted=readiness.contract_parse_attempted,
    )


def _apply_ic_continuation_reset(mutable_state: dict[str, object]) -> None:
    """Clear all prior-cycle issue-centric continuation / execution fields.

    Must be called *before* _apply_ic_fetch_handoff_state() so that stale
    values from the previous execution do not contaminate the fresh dispatch.
    """
    mutable_state.update({k: "" for k in _IC_CONTINUATION_RESET_FIELDS})


def _apply_ic_fetch_handoff_state(
    mutable_state: dict[str, object],
    handoff: _IcFetchHandoffState,
) -> None:
    """Apply the fetch handoff state to mutable_state.

    Writes action, target_issue, artifact metadata, stop reason, and readiness
    diagnostics.  Also resets the contract correction retry counter because a
    valid contract was successfully recovered.

    Call after _apply_ic_continuation_reset() — only the fetch-known values
    will overwrite the cleared fields; the rest remain "" until execution fills
    them in via _finalize_dispatch().
    """
    mutable_state.update(
        {
            # ── Decision identity ──────────────────────────────────────────
            "last_issue_centric_action": handoff.action,
            "last_issue_centric_target_issue": handoff.target_issue,
            # ── Artifact / log paths ───────────────────────────────────────
            "last_issue_centric_decision_log": handoff.decision_log,
            "last_issue_centric_metadata_log": handoff.metadata_log,
            "last_issue_centric_artifact_file": handoff.artifact_file,
            "last_issue_centric_artifact_kind": handoff.artifact_kind,
            # ── Stop reason ────────────────────────────────────────────────
            "last_issue_centric_stop_reason": handoff.stop_reason,
            # ── Readiness diagnostics ──────────────────────────────────────
            "reply_readiness_status": handoff.readiness_status,
            "reply_readiness_reason": handoff.readiness_reason,
            "assistant_text_present": handoff.assistant_text_present,
            "thinking_visible": handoff.thinking_visible,
            "decision_marker_present": handoff.decision_marker_present,
            "contract_parse_attempted": handoff.contract_parse_attempted,
            # ── Correction retry reset ─────────────────────────────────────
            # A valid contract was recovered — clear any prior correction loop.
            "last_issue_centric_contract_correction_count": 0,
            "last_issue_centric_contract_correction_log": "",
            "last_issue_centric_contract_correction_reason": "",
            # Clear the assistant segment hash guard — the correction cycle is done.
            "last_issue_centric_correction_send_hash": "",
        }
    )


# ---------------------------------------------------------------------------
# Fetch outcome routing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _IcFetchOutcome:
    """Routing decision produced after the fetch handoff state is built.

    path:
      "dispatch"               — proceed to dispatch_issue_centric_execution()
      "codex_run_stop"         — stop before dispatch; artifact ready for codex_run
      "initial_selection_stop" — stop; ChatGPT returned a ready-issue selection

    stop_message is populated for all non-dispatch paths.
    selected_issue_ref is populated for initial_selection_stop only.
    """

    path: str  # "dispatch" | "codex_run_stop" | "initial_selection_stop"
    stop_message: str
    selected_issue_ref: str


def _resolve_ic_fetch_outcome(
    contract_decision: object,
    pending_request_source: str,
    *,
    raw_log_rel: str,
    decision_log_rel: str,
    metadata_log_rel: str,
    artifact_log_rel: str,
) -> _IcFetchOutcome:
    """Resolve the fetch outcome routing path after the handoff state is built.

    Reads the contract decision action and the pending request source to
    determine whether to proceed to dispatch or stop before it.

    Returns _IcFetchOutcome describing the routing decision and, for stop
    paths, the BridgeStop message and any additional state values.

    Stop paths:
      CODEX_RUN              — artifact is prepared; stop so operator can launch codex_run
      initial_selection stop — source starts with "initial_selection:" and ChatGPT
                               returned NO_ACTION with a target_issue selection
    Dispatch path:
      All other combinations — proceed to dispatch_issue_centric_execution()
    """
    if contract_decision.action is IssueCentricAction.CODEX_RUN:
        artifact_part = f" artifact: {artifact_log_rel}" if artifact_log_rel else ""
        stop_message = (
            "issue-centric contract reply を検出し、BODY base64 transport の prepared artifact まで作成しました。"
            " prepared Codex body は次の bridge 手で codex_run dispatch に渡します。"
            f" raw dump: {raw_log_rel}"
            f" decision log: {decision_log_rel}"
            f" metadata: {metadata_log_rel}"
            + artifact_part
        )
        return _IcFetchOutcome(
            path="codex_run_stop",
            stop_message=stop_message,
            selected_issue_ref="",
        )

    if (
        pending_request_source.startswith("initial_selection:")
        and contract_decision.action is IssueCentricAction.NO_ACTION
        and contract_decision.target_issue
    ):
        stop_message = (
            f"initial_selection: ChatGPT が ready issue を選定しました: {contract_decision.target_issue}."
            f" summary: {contract_decision.summary!r}"
            " 次は --ready-issue-ref でその issue を指定して実行を開始してください。"
            f" raw dump: {raw_log_rel}"
            f" decision log: {decision_log_rel}"
        )
        return _IcFetchOutcome(
            path="initial_selection_stop",
            stop_message=stop_message,
            selected_issue_ref=contract_decision.target_issue,
        )

    return _IcFetchOutcome(path="dispatch", stop_message="", selected_issue_ref="")


def _apply_ic_fetch_stop_state(
    mutable_state: dict[str, object],
    outcome: _IcFetchOutcome,
) -> None:
    """Apply outcome-specific state mutations for stop-before-dispatch paths.

    For codex_run_stop:        no additional mutations (handoff state is sufficient).
    For initial_selection_stop: writes selected_ready_issue_ref from outcome.
    """
    if outcome.path == "initial_selection_stop" and outcome.selected_issue_ref:
        mutable_state["selected_ready_issue_ref"] = outcome.selected_issue_ref


# ---------------------------------------------------------------------------
# Operator-facing lifecycle status helpers
# ---------------------------------------------------------------------------
# These helpers translate internal issue-centric state values into operator-
# readable decision notes.  The notes are stored in chatgpt_decision_note and
# surfaced by run_until_stop.suggested_next_note() and the bridge summary.
#
# Responsibilities:
#   _build_ic_operator_decision_note() — produce a user-facing note from an
#     action value and optional context (target_issue, selected_issue_ref,
#     outcome path).  Returns a short Japanese sentence suitable for display
#     in the bridge summary / operator screen.
#
# The caller (run()) replaces the raw safe_stop_reason with this note so that
# the operator always sees an action-appropriate next-step description rather
# than a low-level technical stop reason.
# ---------------------------------------------------------------------------


def _build_ic_operator_decision_note(
    action: str,
    outcome_path: str,
    *,
    target_issue: str = "",
    selected_issue_ref: str = "",
    safe_stop_reason: str = "",
) -> str:
    """Return a Japanese operator-facing note for the given fetch outcome.

    This note is stored in ``chatgpt_decision_note`` and read by
    ``run_until_stop.suggested_next_note()`` to produce the operator guidance
    shown after each bridge cycle.

    Rules:
    - codex_run_stop  → prepared artifact / next bridge step guidance
    - initial_selection_stop → ready issue selection guidance
    - dispatch + codex_run   → continuation note (execution will overwrite later)
    - dispatch + no_action / human_review_needed / issue_create → appropriate guidance
    - fallback: safe_stop_reason if non-empty, otherwise generic note
    """
    if outcome_path == "codex_run_stop":
        issue_part = f" ({target_issue})" if target_issue and target_issue != "none" else ""
        return (
            f"ChatGPT が Codex 実行指示{issue_part}を返しました。"
            " prepared Codex body は保存済みです。"
            " bridge を再実行すると issue-centric codex_run dispatch を進めます。"
        )
    if outcome_path == "initial_selection_stop":
        ref = selected_issue_ref or target_issue
        ref_part = f" {ref}" if ref and ref != "none" else ""
        return (
            f"ChatGPT が ready issue{ref_part} を選定しました。"
            " --ready-issue-ref でその issue を指定して bridge を再実行してください。"
        )
    # dispatch paths — execution will overwrite chatgpt_decision_note via
    # _finalize_dispatch; this note acts as a pre-dispatch placeholder.
    if action == "codex_run":
        issue_part = f" ({target_issue})" if target_issue and target_issue != "none" else ""
        return (
            f"ChatGPT が Codex 実行指示{issue_part}を返しました。dispatch を継続します。"
        )
    if action == "issue_create":
        issue_part = f" ({target_issue})" if target_issue and target_issue != "none" else ""
        return f"ChatGPT が issue 作成指示{issue_part}を返しました。dispatch を継続します。"
    if action == "human_review_needed":
        issue_part = f" ({target_issue})" if target_issue and target_issue != "none" else ""
        return (
            f"ChatGPT が人レビュー待ち{issue_part}を返しました。"
            " bridge を再実行すると補足入力を受けて次 request を送ります。"
        )
    if action == "no_action":
        return (
            "ChatGPT が no_action を返しました。dispatch を継続します。"
        )
    # Generic fallback — preserve safe_stop_reason when available.
    if safe_stop_reason:
        return safe_stop_reason
    return f"ChatGPT が {action} を返しました。dispatch を継続します。"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safari の現在 ChatGPT タブから最後の ChatGPT 返答ブロックを抽出します。")
    parser.add_argument(
        "--raw-file",
        default="",
        help="診断や再現テスト用に、会話全文 dump ファイルを直接読む",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Safari から返答を待つ最大秒数。0 の場合は browser_config.json を使う",
    )
    return parser.parse_args(argv)


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pending_request_hash = str(state.get("pending_request_hash", "")).strip()
    pending_request_source = str(state.get("pending_request_source", "")).strip()
    pending_request_signal = str(state.get("pending_request_signal", "")).strip()
    pending_generation_id = str(state.get("last_issue_centric_pending_generation_id", "")).strip()
    request_text = read_pending_request_text(state)
    if not (pending_request_hash and pending_request_source and request_text):
        raise BridgeError(
            "送信済みの ChatGPT request を確認できないため fetch できませんでした。"
            " request 送信から再開してください。"
        )

    rotation_requested = should_rotate_before_next_chat_request(state)
    rotation_reason = next_request_rotation_reason(state)
    if str(state.get("mode", "")).strip() == "await_late_completion":
        rotation_requested = True
        rotation_reason = rotation_reason or "late_completion"

    def handle_wait_event(event: object) -> None:
        nonlocal rotation_requested, rotation_reason
        event_name = str(getattr(event, "name", "")).strip()
        latest_text = str(getattr(event, "latest_text", "") or "")
        event_details = dict(getattr(event, "details", {}) or {})
        mutable_state = clear_error_fields(dict(load_state()))
        if event_name == "timeout_first":
            mutable_state["mode"] = "extended_wait"
        elif event_name == "late_completion_mode":
            mutable_state["mode"] = "await_late_completion"
            rotation_requested = True
            rotation_reason = "late_completion"
            mark_next_request_requires_rotation(mutable_state, rotation_reason)
        elif event_name == "reply_not_ready":
            mutable_state.update(
                {
                    "mode": "waiting_prompt_reply",
                    "reply_readiness_status": str(event_details.get("reply_readiness_status", "")).strip(),
                    "reply_readiness_reason": str(event_details.get("reply_readiness_reason", "")).strip(),
                    "assistant_text_present": bool(event_details.get("assistant_text_present", False)),
                    "assistant_final_content_present": bool(event_details.get("assistant_final_content_present", False)),
                    "assistant_meta_only": bool(event_details.get("assistant_meta_only", False)),
                    "thinking_visible": bool(event_details.get("thinking_visible", False)),
                    "decision_marker_present": bool(event_details.get("decision_marker_present", False)),
                    "reply_complete_tag_present": bool(event_details.get("reply_complete_tag_present", False)),
                    "body_block_start_present": bool(event_details.get("body_block_start_present", False)),
                    "body_block_end_present": bool(event_details.get("body_block_end_present", False)),
                    "partial_body_block_detected": bool(event_details.get("partial_body_block_detected", False)),
                    "open_body_blocks": list(event_details.get("open_body_blocks", [])),
                    "contract_parse_attempted": bool(event_details.get("contract_parse_attempted", False)),
                }
            )
        save_state(mutable_state)
        stage_log = log_text(event_name, latest_text, suffix="txt")
        print(f"{event_name}: {stage_log}")

    if args.raw_file:
        raw_text = read_text(Path(args.raw_file)).strip()
        if not raw_text:
            raise ValueError(f"raw file を読めませんでした: {args.raw_file}")
    else:
        def _plan_a_extractor(raw: str, after: str | None) -> None:
            parse_issue_centric_reply_for_fetch(raw, after_text=after)

        try:
            raw_text = wait_for_issue_centric_reply_text(
                plan_a_extractor=_plan_a_extractor,
                timeout_seconds=args.timeout_seconds or None,
                request_text=request_text or None,
                stage_callback=handle_wait_event,
                allow_project_page_wait=(pending_request_signal == "submitted_unconfirmed"),
            )
        except IssueCentricReplyInvalid as exc:
            # Set raw_text so the common correction retry logic below handles this
            # error the same way as the normal wait-success route.  The readiness
            # classification is repeated on the same raw_text, yielding the same
            # retryable status and falling into the unified correction retry path.
            raw_text = exc.raw_text
    raw_log = log_text("raw_chatgpt_prompt_dump", raw_text, suffix="txt")
    readiness = classify_issue_centric_reply_readiness(raw_text, after_text=request_text or None)
    # Derive an explicit route decision so routing intent is auditable and
    # tested.  The key invariant: plan_a_present=True never routes to a legacy
    # success path; stop_broken and legacy_stop are always explicit stops.
    route_decision = _resolve_ic_reply_route_decision(readiness)
    if route_decision.route == "not_ready":
        raise _build_not_ready_bridge_error(readiness)

    # --- retryable invalid contract handling ---
    # stop_broken (Plan A marker present but parse failed) and correction_retry
    # (Plan A marker absent) are both retryable: ChatGPT produced a complete
    # response but with bad formatting / missing contract.
    if route_decision.route in ("stop_broken", "correction_retry"):
        correction_count = int(state.get("last_issue_centric_contract_correction_count") or 0)
        # Guard: don't re-send correction if no new assistant turn has appeared
        # since the previous correction was sent.
        _guard_correction_resend(state, raw_text, request_text or None)
        if correction_count < _MAX_CONTRACT_CORRECTIONS:
            correction_text = _build_contract_correction_request(readiness.reason)
            correction_log = log_text("contract_correction_request", correction_text, suffix="md")
            send_to_chatgpt(correction_text)
            correction_state = clear_error_fields(dict(state))
            # Preserve pending_request_hash / source / log so the next fetch picks up the reply.
            correction_state["last_issue_centric_contract_correction_count"] = correction_count + 1
            correction_state["last_issue_centric_contract_correction_log"] = repo_relative(correction_log)
            correction_state["last_issue_centric_contract_correction_reason"] = readiness.reason
            # Record assistant segment hash so the next fetch can detect a new turn.
            correction_state["last_issue_centric_correction_send_hash"] = stable_text_hash(
                _assistant_segment_after_text(raw_text, after_text=request_text or None).strip()
            )
            correction_state["mode"] = "waiting_prompt_reply"
            save_state(correction_state)
            raise BridgeStop(
                f"問題: issue-centric contract reply が不正でした（{correction_count + 1} 回目）。\n"
                f"対応: 同じチャットに修正依頼を再送しました。返答後に fetch を再実行してください。\n"
                f"詳細: correction log: {repo_relative(correction_log)}"
                f" / reason: {readiness.reason}"
            )
        stop_for_invalid_issue_centric_contract(
            dict(state),
            raw_text=raw_text,
            detail=readiness.reason,
            pending_request_source=pending_request_source,
            raw_log_path=raw_log,
            readiness=readiness,
            correction_count=correction_count,
        )

    # ── Legacy visible-text reply → explicit stop (detect-only safety net) ───
    # When legacy markers (===CHATGPT_PROMPT_REPLY=== / ===CHATGPT_NO_CODEX===)
    # are detected, the reply is not a valid issue-centric contract.
    # Stop immediately so the operator can request a proper IC-format reply.
    # There is no legacy success path; this block is a backward-compat safety
    # net for accidental / stale old-format replies only.  Normal operation
    # should never reach here because outbound requests always carry the
    # IC-only reply contract section.
    # route_decision.route == "legacy_stop" guarantees legacy_present=True and
    # plan_a_present=False — Plan A never falls through to this branch.
    if route_decision.route == "legacy_stop":
        legacy_summary = "\n".join(
            [
                "# Legacy Visible-Text Reply Detected",
                "",
                f"- reply_readiness_status: {readiness.status}",
                f"- reply_readiness_reason: {readiness.reason}",
                f"- raw_dump: {repo_relative(Path(raw_log))}",
                f"- pending_request_source: {pending_request_source or 'unknown'}",
            ]
        ).strip() + "\n"
        legacy_log = log_text("legacy_reply_detected", legacy_summary, suffix="md")
        user_message = (
            "問題: legacy visible-text reply (===CHATGPT_PROMPT_REPLY=== / ===CHATGPT_NO_CODEX===) が返ってきました。\n"
            "対応: issue-centric contract 形式 (===CHATGPT_DECISION_JSON=== ～ ===CHATGPT_REPLY_COMPLETE===) で返答するよう"
            " ChatGPT に依頼してください。correction retry ではなくプロンプト / contract 側の確認が必要です。\n"
            f"詳細: raw log: {repo_relative(Path(raw_log))} / legacy log: {repo_relative(Path(legacy_log))}"
        )
        legacy_state = clear_error_fields(dict(state))
        legacy_state.update(
            {
                "mode": "awaiting_user",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": False,
                "need_codex_run": False,
                "error": True,
                "error_message": user_message,
                "chatgpt_decision": "legacy_contract_detected",
                "chatgpt_decision_note": "legacy visible-text reply detected; issue-centric contract required",
                "reply_readiness_status": readiness.status,
                "reply_readiness_reason": readiness.reason,
            }
        )
        save_state(legacy_state)
        raise BridgeStop(user_message)

    contract_decision = readiness.decision
    if contract_decision is not None:
        ready_issue_binding_error = _validate_ready_issue_target_binding(
            contract_decision,
            state=state,
            pending_request_source=pending_request_source,
        )
        if ready_issue_binding_error:
            binding_correction_count = int(state.get("last_issue_centric_contract_correction_count") or 0)
            # Guard: don't re-send correction if no new assistant turn has appeared
            # since the previous correction was sent.
            _guard_correction_resend(state, raw_text, request_text or None)
            if binding_correction_count < _MAX_CONTRACT_CORRECTIONS:
                current_ready_issue_ref = str(state.get("current_ready_issue_ref", "")).strip()
                correction_text = _build_binding_mismatch_correction_request(
                    ready_issue_binding_error, current_ready_issue_ref
                )
                correction_log = log_text("contract_correction_request", correction_text, suffix="md")
                send_to_chatgpt(correction_text)
                correction_state = clear_error_fields(dict(state))
                correction_state["last_issue_centric_contract_correction_count"] = binding_correction_count + 1
                correction_state["last_issue_centric_contract_correction_log"] = repo_relative(correction_log)
                correction_state["last_issue_centric_contract_correction_reason"] = ready_issue_binding_error
                # Record assistant segment hash so the next fetch can detect a new turn.
                correction_state["last_issue_centric_correction_send_hash"] = stable_text_hash(
                    _assistant_segment_after_text(raw_text, after_text=request_text or None).strip()
                )
                correction_state["mode"] = "waiting_prompt_reply"
                save_state(correction_state)
                raise BridgeStop(
                    f"問題: ready issue binding が不正でした（{binding_correction_count + 1} 回目）。\n"
                    f"対応: 同じチャットに修正依頼を再送しました。返答後に fetch を再実行してください。\n"
                    f"詳細: correction log: {repo_relative(correction_log)}"
                    f" / reason: {ready_issue_binding_error}"
                )
            stop_for_invalid_issue_centric_contract(
                dict(state),
                raw_text=raw_text,
                detail=ready_issue_binding_error,
                pending_request_source=pending_request_source,
                raw_log_path=raw_log,
                readiness=readiness,
                correction_count=binding_correction_count,
            )
        decision_log = log_text(
            "extracted_issue_centric_contract",
            contract_decision.render_debug_markdown(),
            suffix="md",
        )
        try:
            materialized = materialize_issue_centric_decision(
                contract_decision,
                log_writer=log_text,
                repo_relative=repo_relative,
                raw_log_path=raw_log,
                decision_log_path=decision_log,
            )
        except IssueCentricBodyDecodeError as exc:
            # BODY payload was valid base64 but decoded bytes are not valid UTF-8.
            # This is retryable: ask ChatGPT to re-emit the contract with a valid payload.
            body_decode_reason = str(exc)
            body_correction_count = int(state.get("last_issue_centric_contract_correction_count") or 0)
            # Guard: don't re-send correction if no new assistant turn has appeared
            # since the previous correction was sent.
            _guard_correction_resend(state, raw_text, request_text or None)
            if body_correction_count < _MAX_CONTRACT_CORRECTIONS:
                correction_text = _build_contract_correction_request(body_decode_reason)
                correction_log = log_text("contract_correction_request", correction_text, suffix="md")
                send_to_chatgpt(correction_text)
                correction_state = clear_error_fields(dict(state))
                correction_state["last_issue_centric_contract_correction_count"] = body_correction_count + 1
                correction_state["last_issue_centric_contract_correction_log"] = repo_relative(correction_log)
                correction_state["last_issue_centric_contract_correction_reason"] = body_decode_reason
                # Record assistant segment hash so the next fetch can detect a new turn.
                correction_state["last_issue_centric_correction_send_hash"] = stable_text_hash(
                    _assistant_segment_after_text(raw_text, after_text=request_text or None).strip()
                )
                correction_state["mode"] = "waiting_prompt_reply"
                save_state(correction_state)
                raise BridgeStop(
                    f"問題: BODY block payload が valid base64 ですが decoded text が valid UTF-8 ではありませんでした"
                    f"（{body_correction_count + 1} 回目）。\n"
                    f"対応: 同じチャットに修正依頼を再送しました。返答後に fetch を再実行してください。\n"
                    f"詳細: correction log: {repo_relative(correction_log)}"
                    f" / reason: {body_decode_reason}"
                ) from exc
            stop_for_invalid_issue_centric_contract(
                dict(state),
                raw_text=raw_text,
                detail=body_decode_reason,
                pending_request_source=pending_request_source,
                raw_log_path=raw_log,
                readiness=readiness,
                correction_count=body_correction_count,
            )
        except IssueCentricTransportError as exc:
            raise BridgeError(f"issue-centric contract transport を準備できませんでした: {exc}") from exc

        # ── Build fetch handoff snapshot ────────────────────────────────────
        handoff = _build_ic_fetch_handoff_state(
            contract_decision,
            materialized,
            readiness=readiness,
            decision_log=decision_log,
            pending_request_hash=pending_request_hash,
            prior_state=state,
        )
        mutable_state = clear_error_fields(dict(state))
        clear_pending_request_fields(mutable_state)
        # Preserve rotation flag set during the wait (e.g. late_completion_mode).
        # handle_wait_event saves it to disk but not to the in-memory state dict;
        # rotation_requested (nonlocal) carries the correct value from either the
        # original state or the callback.
        if rotation_requested:
            mark_next_request_requires_rotation(mutable_state, rotation_reason)
        if pending_generation_id:
            mutable_state.update(
                {
                    "last_issue_centric_generation_lifecycle": "issue_centric_consumed",
                    "last_issue_centric_generation_lifecycle_reason": "chatgpt_reply_recovered_for_generation",
                    "last_issue_centric_generation_lifecycle_source": "fetch_next_prompt",
                    "last_issue_centric_pending_generation_id": "",
                    "last_issue_centric_prepared_generation_id": "",
                    "last_issue_centric_consumed_generation_id": pending_generation_id,
                    "last_issue_centric_route_selected": "fallback_legacy",
                    "last_issue_centric_route_fallback_reason": "chatgpt_reply_recovered_for_generation",
                    "last_issue_centric_runtime_mode": "issue_centric_degraded_fallback",
                    "last_issue_centric_runtime_mode_reason": "chatgpt_reply_recovered_for_generation",
                    "last_issue_centric_runtime_mode_source": "fetch_next_prompt",
                    "last_issue_centric_freshness_status": "issue_centric_stale",
                    "last_issue_centric_freshness_reason": "chatgpt_reply_recovered_for_generation",
                    "last_issue_centric_freshness_source": "reply_recovery_state",
                }
            )
        # ── Base fetch state ────────────────────────────────────────────────
        mutable_state.update(
            {
                "mode": "awaiting_user",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": False,
                "need_codex_run": False,
                "last_prompt_file": "",
                "last_processed_request_hash": handoff.processed_request_hash,
                "last_processed_reply_hash": handoff.reply_hash,
                "chatgpt_decision": f"issue_centric:{handoff.action}",
                "chatgpt_decision_note": handoff.stop_reason,
            }
        )
        # ── Issue-centric state reset + fetch handoff ───────────────────────
        # 1. Clear all prior-cycle continuation / execution fields.
        _apply_ic_continuation_reset(mutable_state)
        # 2. Write what this fetch cycle learned from the ChatGPT reply.
        #    After this call mutable_state is the complete pre-dispatch handoff.
        _apply_ic_fetch_handoff_state(mutable_state, handoff)
        # ── Outcome routing: resolve and execute ────────────────────────────
        outcome = _resolve_ic_fetch_outcome(
            contract_decision,
            pending_request_source,
            raw_log_rel=repo_relative(raw_log),
            decision_log_rel=repo_relative(decision_log),
            metadata_log_rel=repo_relative(materialized.metadata_log_path),
            artifact_log_rel=(
                repo_relative(materialized.artifact_log_path)
                if materialized.artifact_log_path is not None
                else ""
            ),
        )
        # ── Operator-facing decision note ───────────────────────────────────
        # Replace the raw safe_stop_reason with an action-appropriate note so
        # the operator always sees a meaningful next-step description.
        mutable_state["chatgpt_decision_note"] = _build_ic_operator_decision_note(
            handoff.action,
            outcome.path,
            target_issue=handoff.target_issue,
            selected_issue_ref=outcome.selected_issue_ref,
            safe_stop_reason=handoff.stop_reason,
        )
        if outcome.path != "dispatch":
            _apply_ic_fetch_stop_state(mutable_state, outcome)
            save_state(mutable_state)
            raise BridgeStop(outcome.stop_message)
        project_config = load_project_config()
        dispatch_result = dispatch_issue_centric_execution(
            contract_decision=contract_decision,
            materialized=materialized,
            prior_state=state,
            mutable_state=mutable_state,
            project_config=project_config,
            repo_path=project_repo_path(project_config),
            source_raw_log=repo_relative(raw_log),
            source_decision_log=repo_relative(decision_log),
            source_metadata_log=repo_relative(materialized.metadata_log_path),
            source_artifact_path=(
                repo_relative(materialized.artifact_log_path)
                if materialized.artifact_log_path is not None
                else ""
            ),
            log_writer=log_text,
            repo_relative=repo_relative,
            load_state_fn=load_state,
            save_state_fn=save_state,
            execute_issue_create_action_fn=execute_issue_create_action,
            execute_codex_run_action_fn=execute_codex_run_action,
            launch_issue_centric_codex_run_fn=launch_issue_centric_codex_run,
            execute_human_review_action_fn=execute_human_review_action,
            execute_close_current_issue_fn=execute_close_current_issue,
            execute_parent_issue_update_fn=execute_parent_issue_update_after_close,
            execute_followup_issue_action_fn=execute_followup_issue_action,
            execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync,
            launch_runner=launch_codex_once.run,
        )
        save_state(dispatch_result.final_state)
        raise BridgeStop(dispatch_result.stop_message)
    # Safety guard: contract_decision should always be non-None here because
    # all statuses that leave decision=None (legacy, not-ready, retryable errors)
    # are handled by earlier explicit-stop blocks.  If we reach this point with
    # decision=None it indicates an unhandled classifier state.
    raise BridgeError(
        "issue-centric contract reply が見つかりませんでした（未対応の classifier 状態）"
    )


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
