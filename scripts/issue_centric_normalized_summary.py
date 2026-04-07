from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping


_ISSUE_URL_RE = re.compile(r"https://github\.com/([^/\s]+/[^/\s]+)/issues/(\d+)")
_ISSUE_REF_RE = re.compile(r"^(?:[^/\s]+/[^/\s]+#|#)?(\d+)$")


def build_issue_centric_normalized_summary(
    *,
    matrix_path: str,
    final_status: str,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    action = str(state.get("last_issue_centric_action", "")).strip()
    current_issue = _issue_from_ref(
        str(state.get("last_issue_centric_resolved_issue", "")).strip()
        or str(state.get("last_issue_centric_target_issue", "")).strip()
    )
    created_primary_issue = _issue_from_parts(
        number=state.get("last_issue_centric_primary_issue_number", ""),
        url=state.get("last_issue_centric_primary_issue_url", ""),
        title=state.get("last_issue_centric_primary_issue_title", ""),
    )
    created_followup_issue = _issue_from_parts(
        number=state.get("last_issue_centric_followup_issue_number", ""),
        url=state.get("last_issue_centric_followup_issue_url", ""),
        title=state.get("last_issue_centric_followup_issue_title", ""),
    )
    closed_issue = _issue_from_parts(
        number=state.get("last_issue_centric_closed_issue_number", ""),
        url=state.get("last_issue_centric_closed_issue_url", ""),
        title=state.get("last_issue_centric_closed_issue_title", ""),
    )

    codex_target_issue = current_issue if action == "codex_run" else None
    review_target_issue = current_issue if action == "human_review_needed" else None
    project_lifecycle_sync = {
        "status": str(state.get("last_issue_centric_lifecycle_sync_status", "")).strip(),
        "stage": str(state.get("last_issue_centric_lifecycle_sync_stage", "")).strip(),
        "project_url": str(state.get("last_issue_centric_lifecycle_sync_project_url", "")).strip(),
        "project_item_id": str(state.get("last_issue_centric_lifecycle_sync_project_item_id", "")).strip(),
        "state_field": str(state.get("last_issue_centric_lifecycle_sync_state_field", "")).strip(),
        "state_value": str(state.get("last_issue_centric_lifecycle_sync_state_value", "")).strip(),
        "log": str(state.get("last_issue_centric_lifecycle_sync_log", "")).strip(),
    }

    principal_issue_candidate, principal_issue_kind = _choose_principal_issue_candidate(
        action=action,
        current_issue=current_issue,
        created_primary_issue=created_primary_issue,
        created_followup_issue=created_followup_issue,
        closed_issue=closed_issue,
    )
    next_request_hint = _choose_next_request_hint(
        action=action,
        current_issue=current_issue,
        created_primary_issue=created_primary_issue,
        created_followup_issue=created_followup_issue,
        closed_issue=closed_issue,
        principal_issue_kind=principal_issue_kind,
    )

    stop_reason = str(state.get("last_issue_centric_stop_reason", "")).strip()
    blocked_reason = stop_reason if final_status == "blocked" else ""
    partial_reason = stop_reason if final_status == "partial" else ""

    return {
        "action": action,
        "matrix_path": matrix_path,
        "final_status": final_status,
        "current_issue": current_issue,
        "principal_issue_candidate": principal_issue_candidate,
        "principal_issue_kind": principal_issue_kind,
        "created_primary_issue": created_primary_issue,
        "created_followup_issue": created_followup_issue,
        "closed_issue": closed_issue,
        "codex_target_issue": codex_target_issue,
        "review_target_issue": review_target_issue,
        "project_lifecycle_sync": project_lifecycle_sync,
        "blocked_reason": blocked_reason,
        "partial_reason": partial_reason,
        "next_request_hint": next_request_hint,
    }


def load_issue_centric_normalized_summary(
    state: Mapping[str, Any],
    *,
    repo_root: Path,
) -> dict[str, Any] | None:
    raw_path = str(state.get("last_issue_centric_normalized_summary", "")).strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = repo_root / raw_path
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def render_issue_centric_summary_for_request(summary: Mapping[str, Any]) -> str:
    lines = [
        "- issue_centric_action: " + str(summary.get("action", "")).strip(),
        "- issue_centric_final_status: " + str(summary.get("final_status", "")).strip(),
        "- issue_centric_principal_issue_kind: " + str(summary.get("principal_issue_kind", "")).strip(),
        "- issue_centric_next_request_hint: " + str(summary.get("next_request_hint", "")).strip(),
    ]
    principal = _issue_label(summary.get("principal_issue_candidate"))
    if principal:
        lines.append(f"- issue_centric_principal_issue: {principal}")
    current_issue = _issue_label(summary.get("current_issue"))
    if current_issue:
        lines.append(f"- issue_centric_current_issue: {current_issue}")
    created_primary = _issue_label(summary.get("created_primary_issue"))
    if created_primary:
        lines.append(f"- issue_centric_created_primary_issue: {created_primary}")
    created_followup = _issue_label(summary.get("created_followup_issue"))
    if created_followup:
        lines.append(f"- issue_centric_created_followup_issue: {created_followup}")
    closed_issue = _issue_label(summary.get("closed_issue"))
    if closed_issue:
        lines.append(f"- issue_centric_closed_issue: {closed_issue}")
    lifecycle_sync = summary.get("project_lifecycle_sync")
    if isinstance(lifecycle_sync, Mapping):
        lifecycle_status = str(lifecycle_sync.get("status", "")).strip()
        lifecycle_stage = str(lifecycle_sync.get("stage", "")).strip()
        if lifecycle_status or lifecycle_stage:
            lines.append(
                f"- issue_centric_project_lifecycle_sync: stage={lifecycle_stage or 'unknown'} status={lifecycle_status or 'unknown'}"
            )
    blocked_reason = str(summary.get("blocked_reason", "")).strip()
    partial_reason = str(summary.get("partial_reason", "")).strip()
    if blocked_reason:
        lines.append(f"- issue_centric_blocked_reason: {blocked_reason}")
    if partial_reason:
        lines.append(f"- issue_centric_partial_reason: {partial_reason}")
    return "\n".join(line for line in lines if line.strip())


def _choose_principal_issue_candidate(
    *,
    action: str,
    current_issue: dict[str, Any] | None,
    created_primary_issue: dict[str, Any] | None,
    created_followup_issue: dict[str, Any] | None,
    closed_issue: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str]:
    current_closed = _same_issue(current_issue, closed_issue)

    if created_followup_issue is not None and (current_closed or action == "no_action"):
        return created_followup_issue, "followup_issue"
    if action == "issue_create" and created_primary_issue is not None:
        return created_primary_issue, "primary_issue"
    if action == "human_review_needed" and current_issue is not None and not current_closed:
        return current_issue, "current_issue"
    if action == "codex_run" and current_issue is not None and not current_closed:
        return current_issue, "current_issue"
    if current_issue is not None and not current_closed and created_followup_issue is None:
        return current_issue, "current_issue"
    if created_followup_issue is not None:
        return created_followup_issue, "followup_issue"
    return None, "unresolved"


def _choose_next_request_hint(
    *,
    action: str,
    current_issue: dict[str, Any] | None,
    created_primary_issue: dict[str, Any] | None,
    created_followup_issue: dict[str, Any] | None,
    closed_issue: dict[str, Any] | None,
    principal_issue_kind: str,
) -> str:
    current_closed = _same_issue(current_issue, closed_issue)

    if principal_issue_kind == "followup_issue":
        return "continue_on_followup_issue"
    if principal_issue_kind == "primary_issue":
        return "continue_on_primary_issue"
    if action == "human_review_needed" and current_issue is not None and not current_closed:
        return "review_current_issue"
    if principal_issue_kind == "current_issue":
        return "continue_on_current_issue"
    if created_primary_issue is not None:
        return "continue_on_primary_issue"
    if created_followup_issue is not None:
        return "continue_on_followup_issue"
    return "issue_resolution_unclear"


def _issue_from_parts(*, number: Any, url: Any, title: Any) -> dict[str, Any] | None:
    number_text = str(number).strip()
    url_text = str(url).strip()
    title_text = str(title).strip()
    if not (number_text or url_text or title_text):
        return None
    if not number_text and url_text:
        number_text = _extract_issue_number(url_text)
    return {
        "number": number_text,
        "url": url_text,
        "title": title_text,
        "ref": f"#{number_text}" if number_text else url_text,
    }


def _issue_from_ref(ref: str) -> dict[str, Any] | None:
    ref = str(ref).strip()
    if not ref or ref == "none":
        return None
    if match := _ISSUE_URL_RE.search(ref):
        repository, number = match.groups()
        return {
            "number": number,
            "url": match.group(0),
            "title": "",
            "ref": f"{repository}#{number}",
        }
    if match := _ISSUE_REF_RE.match(ref):
        number = match.group(1)
        return {
            "number": number,
            "url": "",
            "title": "",
            "ref": f"#{number}",
        }
    return {
        "number": "",
        "url": "",
        "title": "",
        "ref": ref,
    }


def _extract_issue_number(ref: str) -> str:
    if match := _ISSUE_URL_RE.search(ref):
        return match.group(2)
    if match := _ISSUE_REF_RE.match(ref.strip()):
        return match.group(1)
    return ""


def _same_issue(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    if left is None or right is None:
        return False
    left_number = str(left.get("number", "")).strip()
    right_number = str(right.get("number", "")).strip()
    left_url = str(left.get("url", "")).strip()
    right_url = str(right.get("url", "")).strip()
    if left_number and right_number:
        return left_number == right_number
    if left_url and right_url:
        return left_url == right_url
    return False


def _issue_label(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    number = str(value.get("number", "")).strip()
    url = str(value.get("url", "")).strip()
    title = str(value.get("title", "")).strip()
    ref = str(value.get("ref", "")).strip()
    core = ref or (f"#{number}" if number else "")
    if url:
        core = f"{core} {url}".strip()
    if title:
        core = f"{core} ({title})".strip()
    return core.strip()
