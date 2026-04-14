#!/usr/bin/env python3
from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


DECISION_JSON_START = "===CHATGPT_DECISION_JSON==="
DECISION_JSON_END = "===END_DECISION_JSON==="
ISSUE_BODY_START = "===CHATGPT_ISSUE_BODY==="
ISSUE_BODY_END = "===END_ISSUE_BODY==="
CODEX_BODY_START = "===CHATGPT_CODEX_BODY==="
CODEX_BODY_END = "===END_CODEX_BODY==="
REVIEW_BODY_START = "===CHATGPT_REVIEW==="
REVIEW_BODY_END = "===END_REVIEW==="
FOLLOWUP_ISSUE_BODY_START = "===CHATGPT_FOLLOWUP_ISSUE_BODY==="
FOLLOWUP_ISSUE_BODY_END = "===END_FOLLOWUP_ISSUE_BODY==="

# Mandatory terminal tag that signals the reply is fully complete.
# bridge only starts parse/validate after this tag is present.
REPLY_COMPLETE_TAG = "===CHATGPT_REPLY_COMPLETE==="

CHATGPT_TURN_MARKER = "ChatGPT:"
USER_TURN_MARKER = "あなた:"

# Accepted target_issue formats (mirrors resolve_target_issue in issue_centric_github.py):
#   bare number ("42"), hash-prefixed ("#42"),
#   cross-repo reference ("owner/repo#42"),
#   full GitHub issue URL ("https://github.com/owner/repo/issues/42").
_TARGET_ISSUE_URL_RE = re.compile(
    r"^https://github\.com/[^/\s]+/[^/\s]+/issues/[0-9]+$"
)
_TARGET_ISSUE_REF_RE = re.compile(r"^(?:[^/\s]+/[^/#\s]+#|#)?([0-9]+)$")


class IssueCentricContractError(ValueError):
    """Raised when a ChatGPT issue-centric contract reply is invalid."""


class IssueCentricContractNotFound(IssueCentricContractError):
    """Raised when the new issue-centric contract is not present in a reply."""


class IssueCentricAction(str, Enum):
    ISSUE_CREATE = "issue_create"
    CODEX_RUN = "codex_run"
    NO_ACTION = "no_action"
    HUMAN_REVIEW_NEEDED = "human_review_needed"


@dataclass(frozen=True)
class ExtractedIssueCentricReply:
    envelope: Mapping[str, Any]
    raw_json: str
    target_issue_raw: str
    issue_body_base64: str | None
    codex_body_base64: str | None
    review_base64: str | None
    followup_issue_body_base64: str | None
    raw_segment: str


@dataclass(frozen=True)
class IssueCentricDecision:
    action: IssueCentricAction
    target_issue: str | None
    close_current_issue: bool
    create_followup_issue: bool
    summary: str
    issue_body_base64: str | None
    codex_body_base64: str | None
    review_base64: str | None
    followup_issue_body_base64: str | None
    raw_json: str
    raw_segment: str

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "target_issue": self.target_issue or "none",
            "close_current_issue": self.close_current_issue,
            "create_followup_issue": self.create_followup_issue,
            "summary": self.summary,
            "issue_body_base64": self.issue_body_base64,
            "codex_body_base64": self.codex_body_base64,
            "review_base64": self.review_base64,
            "followup_issue_body_base64": self.followup_issue_body_base64,
        }

    def render_debug_markdown(self) -> str:
        blocks = {
            "CHATGPT_ISSUE_BODY": self.issue_body_base64,
            "CHATGPT_CODEX_BODY": self.codex_body_base64,
            "CHATGPT_REVIEW": self.review_base64,
            "CHATGPT_FOLLOWUP_ISSUE_BODY": self.followup_issue_body_base64,
        }
        lines = [
            "# Issue-Centric Contract Decision",
            "",
            f"- action: `{self.action.value}`",
            f"- target_issue: `{self.target_issue or 'none'}`",
            f"- close_current_issue: `{str(self.close_current_issue).lower()}`",
            f"- create_followup_issue: `{str(self.create_followup_issue).lower()}`",
            f"- summary: {self.summary}",
            "",
            "## Body Blocks",
            "",
        ]
        for name, payload in blocks.items():
            if payload is None:
                lines.append(f"- {name}: none")
            else:
                lines.append(f"- {name}: present ({len(payload)} chars)")
        lines.extend(
            [
                "",
                "## Raw JSON",
                "",
                "```json",
                self.raw_json.strip(),
                "```",
            ]
        )
        return "\n".join(lines).strip() + "\n"


def build_issue_centric_reply_contract_section() -> str:
    actions = " / ".join(f"`{action.value}`" for action in IssueCentricAction)
    return "\n".join(
        [
            "## issue-centric reply contract",
            "",
            "bridge が返答を機械処理するため、前置きや補足説明、コードフェンスを付けず、issue-centric contract only で返答してください。",
            "legacy visible-text fallback は使わないでください。",
            "",
            f"返答は必ず先頭に `{DECISION_JSON_START}` と `{DECISION_JSON_END}` で囲った JSON を置いてください。",
            f"body が必要な場合だけ `{ISSUE_BODY_START} ... {ISSUE_BODY_END}`、`{CODEX_BODY_START} ... {CODEX_BODY_END}`、`{REVIEW_BODY_START} ... {REVIEW_BODY_END}`、`{FOLLOWUP_ISSUE_BODY_START} ... {FOLLOWUP_ISSUE_BODY_END}` のいずれかを続けてください。",
            "各 body block の payload は BASE64 で返してください。",
            f"`action` は {actions} のいずれかだけを使ってください。",
            "`summary` は短くしてください。",
            f"返答の最終行に必ず `{REPLY_COMPLETE_TAG}` を置いてください。bridge はこのタグが来るまで completion 扱いしません。",
        ]
    ).strip()


@dataclass(frozen=True)
class _LocatedMatch:
    start: int
    end: int
    assistant_start: int
    raw_body: str


def _search_start_index(raw_text: str, after_text: str | None = None) -> int:
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


def _select_assistant_match(
    raw_text: str,
    start_marker: str,
    end_marker: str,
    *,
    after_text: str | None = None,
) -> _LocatedMatch:
    search_start = _search_start_index(raw_text, after_text)
    pattern = re.compile(
        rf"{re.escape(start_marker)}(.*?){re.escape(end_marker)}",
        re.DOTALL,
    )
    assistant_matches: list[_LocatedMatch] = []
    fallback_matches: list[_LocatedMatch] = []
    for match in pattern.finditer(raw_text, search_start):
        assistant_index = raw_text.rfind(CHATGPT_TURN_MARKER, search_start, match.start())
        user_index = raw_text.rfind(USER_TURN_MARKER, search_start, match.start())
        located = _LocatedMatch(
            start=match.start(),
            end=match.end(),
            assistant_start=assistant_index if assistant_index != -1 else search_start,
            raw_body=match.group(1),
        )
        fallback_matches.append(located)
        if assistant_index > user_index:
            assistant_matches.append(located)

    matches = assistant_matches if search_start > 0 else (assistant_matches or fallback_matches)
    if not matches:
        raise IssueCentricContractNotFound(
            "直近の request 以降に issue-centric contract reply を抽出できませんでした。"
        )
    return sorted(matches, key=lambda item: item.start)[-1]


def _slice_assistant_segment(raw_text: str, selected: _LocatedMatch) -> str:
    next_user_turn = raw_text.find(USER_TURN_MARKER, selected.end)
    segment_end = next_user_turn if next_user_turn != -1 else len(raw_text)
    return raw_text[selected.assistant_start:segment_end]


def _extract_single_block(
    segment: str,
    *,
    name: str,
    start_marker: str,
    end_marker: str,
    required: bool = False,
    normalize_base64: bool = False,
) -> str | None:
    start_count = segment.count(start_marker)
    end_count = segment.count(end_marker)
    if start_count != end_count:
        raise IssueCentricContractError(f"{name} block marker pairing is broken.")
    if start_count == 0:
        if required:
            raise IssueCentricContractError(f"{name} block is required but missing.")
        return None
    if start_count > 1:
        raise IssueCentricContractError(f"{name} block must not appear more than once.")

    pattern = re.compile(
        rf"{re.escape(start_marker)}(.*?){re.escape(end_marker)}",
        re.DOTALL,
    )
    match = pattern.search(segment)
    if match is None:
        raise IssueCentricContractError(f"{name} block could not be extracted.")
    raw_body = match.group(1)
    if not raw_body.strip():
        raise IssueCentricContractError(f"{name} block is present but empty.")
    if not normalize_base64:
        return raw_body.strip()
    return _normalize_base64_payload(raw_body, name=name)


def _normalize_base64_payload(raw_body: str, *, name: str) -> str:
    normalized = "".join(line.strip() for line in raw_body.splitlines() if line.strip())
    if not normalized:
        raise IssueCentricContractError(f"{name} block is present but empty.")

    padded = normalized + ("=" * ((4 - len(normalized) % 4) % 4))
    try:
        base64.b64decode(padded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise IssueCentricContractError(f"{name} block is not valid base64: {exc}") from exc
    return normalized


def _require_bool(envelope: Mapping[str, Any], field: str) -> bool:
    value = envelope.get(field)
    if type(value) is not bool:
        raise IssueCentricContractError(f"{field} must be a boolean.")
    return value


def _require_optional_bool(envelope: Mapping[str, Any], field: str, *, default: bool = False) -> bool:
    value = envelope.get(field, default)
    if type(value) is not bool:
        raise IssueCentricContractError(f"{field} must be a boolean.")
    return value


def _require_string(envelope: Mapping[str, Any], field: str) -> str:
    value = envelope.get(field)
    if not isinstance(value, str):
        raise IssueCentricContractError(f"{field} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise IssueCentricContractError(f"{field} must not be empty.")
    return normalized


def _require_target_issue_scalar(envelope: Mapping[str, Any], field: str) -> str:
    value = envelope.get(field)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise IssueCentricContractError(f"{field} must not be empty.")
        return normalized
    if type(value) is int:
        return str(value)
    raise IssueCentricContractError(f"{field} must be a string or integer.")


def _normalize_target_issue(raw_target_issue: str) -> str | None:
    stripped = raw_target_issue.strip()
    if stripped.lower() == "none":
        return None
    if _TARGET_ISSUE_URL_RE.match(stripped) or _TARGET_ISSUE_REF_RE.match(stripped):
        return stripped
    raise IssueCentricContractError(
        f"target_issue has an invalid format: {stripped!r}. "
        'Accepted formats: bare number ("42"), hash-prefixed ("#42"), '
        'cross-repo reference ("owner/repo#42"), '
        'or full GitHub issue URL ("https://github.com/owner/repo/issues/42").'
    )


_CONTRACT_MARKERS = (
    DECISION_JSON_START,
    DECISION_JSON_END,
    ISSUE_BODY_START,
    ISSUE_BODY_END,
    CODEX_BODY_START,
    CODEX_BODY_END,
    REVIEW_BODY_START,
    REVIEW_BODY_END,
    FOLLOWUP_ISSUE_BODY_START,
    FOLLOWUP_ISSUE_BODY_END,
)


def contains_issue_centric_contract_marker(
    raw_text: str,
    *,
    after_text: str | None = None,
) -> bool:
    search_start = _search_start_index(raw_text, after_text)
    segment = raw_text[search_start:]
    return any(marker in segment for marker in _CONTRACT_MARKERS)


def extract_issue_centric_reply(
    raw_text: str,
    *,
    after_text: str | None = None,
) -> ExtractedIssueCentricReply:
    selected = _select_assistant_match(
        raw_text,
        DECISION_JSON_START,
        DECISION_JSON_END,
        after_text=after_text,
    )
    segment = _slice_assistant_segment(raw_text, selected)
    raw_json = _extract_single_block(
        segment,
        name="CHATGPT_DECISION_JSON",
        start_marker=DECISION_JSON_START,
        end_marker=DECISION_JSON_END,
        required=True,
    )
    try:
        envelope = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise IssueCentricContractError(f"CHATGPT_DECISION_JSON is not valid JSON: {exc}") from exc
    if not isinstance(envelope, dict):
        raise IssueCentricContractError("CHATGPT_DECISION_JSON must decode to a JSON object.")

    issue_body = _extract_single_block(
        segment,
        name="CHATGPT_ISSUE_BODY",
        start_marker=ISSUE_BODY_START,
        end_marker=ISSUE_BODY_END,
        normalize_base64=True,
    )
    codex_body = _extract_single_block(
        segment,
        name="CHATGPT_CODEX_BODY",
        start_marker=CODEX_BODY_START,
        end_marker=CODEX_BODY_END,
        normalize_base64=True,
    )
    review_body = _extract_single_block(
        segment,
        name="CHATGPT_REVIEW",
        start_marker=REVIEW_BODY_START,
        end_marker=REVIEW_BODY_END,
        normalize_base64=True,
    )
    followup_issue_body = _extract_single_block(
        segment,
        name="CHATGPT_FOLLOWUP_ISSUE_BODY",
        start_marker=FOLLOWUP_ISSUE_BODY_START,
        end_marker=FOLLOWUP_ISSUE_BODY_END,
        normalize_base64=True,
    )

    raw_target_issue = _require_target_issue_scalar(envelope, "target_issue")
    return ExtractedIssueCentricReply(
        envelope=envelope,
        raw_json=raw_json,
        target_issue_raw=raw_target_issue,
        issue_body_base64=issue_body,
        codex_body_base64=codex_body,
        review_base64=review_body,
        followup_issue_body_base64=followup_issue_body,
        raw_segment=segment.strip() + "\n",
    )


def normalize_issue_centric_reply(extracted: ExtractedIssueCentricReply) -> IssueCentricDecision:
    envelope = extracted.envelope
    raw_action = _require_string(envelope, "action")
    try:
        action = IssueCentricAction(raw_action)
    except ValueError as exc:
        raise IssueCentricContractError(f"action is unknown: {raw_action}") from exc

    decision = IssueCentricDecision(
        action=action,
        target_issue=_normalize_target_issue(extracted.target_issue_raw),
        close_current_issue=_require_optional_bool(envelope, "close_current_issue"),
        create_followup_issue=_require_optional_bool(envelope, "create_followup_issue"),
        summary=_require_string(envelope, "summary"),
        issue_body_base64=extracted.issue_body_base64,
        codex_body_base64=extracted.codex_body_base64,
        review_base64=extracted.review_base64,
        followup_issue_body_base64=extracted.followup_issue_body_base64,
        raw_json=extracted.raw_json,
        raw_segment=extracted.raw_segment,
    )
    _validate_decision(decision)
    return decision


def validate_issue_centric_decision(decision: IssueCentricDecision) -> IssueCentricDecision:
    _validate_decision(decision)
    return decision


def parse_issue_centric_reply(
    raw_text: str,
    *,
    after_text: str | None = None,
) -> IssueCentricDecision:
    return normalize_issue_centric_reply(extract_issue_centric_reply(raw_text, after_text=after_text))


def maybe_parse_issue_centric_reply(
    raw_text: str,
    *,
    after_text: str | None = None,
) -> IssueCentricDecision | None:
    try:
        return parse_issue_centric_reply(raw_text, after_text=after_text)
    except IssueCentricContractNotFound:
        return None


def _validate_decision(decision: IssueCentricDecision) -> None:
    if decision.target_issue is None and decision.action is IssueCentricAction.CODEX_RUN:
        raise IssueCentricContractError("target_issue=none cannot be combined with action=codex_run.")
    if decision.create_followup_issue and decision.followup_issue_body_base64 is None:
        raise IssueCentricContractError("create_followup_issue=true requires CHATGPT_FOLLOWUP_ISSUE_BODY.")
    if not decision.create_followup_issue and decision.followup_issue_body_base64 is not None:
        raise IssueCentricContractError("CHATGPT_FOLLOWUP_ISSUE_BODY is allowed only when create_followup_issue=true.")

    if decision.action is IssueCentricAction.ISSUE_CREATE:
        if decision.issue_body_base64 is None:
            raise IssueCentricContractError("issue_create requires CHATGPT_ISSUE_BODY.")
        if decision.codex_body_base64 is not None:
            raise IssueCentricContractError("issue_create must not include CHATGPT_CODEX_BODY.")
        if decision.review_base64 is not None:
            raise IssueCentricContractError("issue_create must not include CHATGPT_REVIEW.")
        return

    if decision.action is IssueCentricAction.CODEX_RUN:
        if decision.codex_body_base64 is None:
            raise IssueCentricContractError("codex_run requires CHATGPT_CODEX_BODY.")
        if decision.issue_body_base64 is not None:
            raise IssueCentricContractError("codex_run must not include CHATGPT_ISSUE_BODY.")
        if decision.review_base64 is not None:
            raise IssueCentricContractError("codex_run must not include CHATGPT_REVIEW.")
        return

    if decision.action is IssueCentricAction.NO_ACTION:
        if any(
            payload is not None
            for payload in (
                decision.issue_body_base64,
                decision.codex_body_base64,
                decision.review_base64,
            )
        ):
            raise IssueCentricContractError("no_action must not include body blocks.")
        return

    if decision.action is IssueCentricAction.HUMAN_REVIEW_NEEDED:
        if decision.issue_body_base64 is not None or decision.codex_body_base64 is not None:
            raise IssueCentricContractError(
                "human_review_needed may include CHATGPT_REVIEW only."
            )
        return
