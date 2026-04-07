from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_ISSUE_URL_RE = re.compile(r"https://github\.com/([^/\s]+/[^/\s]+)/issues/(\d+)")
_ISSUE_REF_RE = re.compile(r"^(?:[^/\s]+/[^/\s]+#|#)?(\d+)$")


@dataclass(frozen=True)
class IssueCentricNextRequestContext:
    target_issue: str
    target_issue_source: str
    next_request_hint: str
    principal_issue_kind: str
    used_normalized_summary: bool
    fallback_reason: str
    summary_path: str


@dataclass(frozen=True)
class IssueCentricRouteSelection:
    route_selected: str
    target_issue: str
    target_issue_source: str
    next_request_hint: str
    principal_issue_kind: str
    used_normalized_summary: bool
    fallback_reason: str
    summary_path: str


@dataclass(frozen=True)
class IssueCentricRecoveryContext:
    recovery_status: str
    recovery_source: str
    route_selected: str
    target_issue: str
    target_issue_source: str
    next_request_hint: str
    principal_issue: str
    principal_issue_kind: str
    used_normalized_summary: bool
    fallback_reason: str
    summary_path: str
    dispatch_result_path: str


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


def load_issue_centric_dispatch_result(
    state: Mapping[str, Any],
    *,
    repo_root: Path,
) -> dict[str, Any] | None:
    raw_path = str(state.get("last_issue_centric_dispatch_result", "")).strip()
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


def resolve_issue_centric_next_request_context(
    state: Mapping[str, Any],
    *,
    repo_root: Path,
) -> IssueCentricNextRequestContext | None:
    summary_path = str(state.get("last_issue_centric_normalized_summary", "")).strip()
    summary = load_issue_centric_normalized_summary(state, repo_root=repo_root)
    if summary is not None:
        principal_kind = str(summary.get("principal_issue_kind", "")).strip()
        next_request_hint = str(summary.get("next_request_hint", "")).strip()
        principal = summary.get("principal_issue_candidate")
        if (
            isinstance(principal, Mapping)
            and principal_kind not in {"", "unresolved"}
            and next_request_hint != "issue_resolution_unclear"
            and _summary_matches_state(summary, state)
        ):
            target_issue = (
                str(principal.get("url", "")).strip()
                or str(principal.get("ref", "")).strip()
            )
            if target_issue:
                return IssueCentricNextRequestContext(
                    target_issue=target_issue,
                    target_issue_source="normalized_summary",
                    next_request_hint=next_request_hint,
                    principal_issue_kind=principal_kind,
                    used_normalized_summary=True,
                    fallback_reason="",
                    summary_path=summary_path,
                )

    fallback_target = _resolve_next_request_target_from_state(state)
    if fallback_target:
        return IssueCentricNextRequestContext(
            target_issue=fallback_target,
            target_issue_source="existing_state_fallback",
            next_request_hint=str(state.get("last_issue_centric_next_request_hint", "")).strip()
            or "issue_resolution_unclear",
            principal_issue_kind=str(state.get("last_issue_centric_principal_issue_kind", "")).strip()
            or "fallback",
            used_normalized_summary=False,
            fallback_reason=_fallback_reason_for_summary(summary, summary_path),
            summary_path=summary_path,
        )
    return None


def select_issue_centric_next_request_route(
    state: Mapping[str, Any],
    *,
    repo_root: Path,
) -> IssueCentricRouteSelection:
    summary_path = str(state.get("last_issue_centric_normalized_summary", "")).strip()
    summary = load_issue_centric_normalized_summary(state, repo_root=repo_root)
    try:
        context = resolve_issue_centric_next_request_context(state, repo_root=repo_root)
    except Exception as exc:
        return IssueCentricRouteSelection(
            route_selected="fallback_legacy",
            target_issue="",
            target_issue_source="resolver_exception",
            next_request_hint="issue_resolution_unclear",
            principal_issue_kind="unresolved",
            used_normalized_summary=False,
            fallback_reason=f"resolver_error:{exc.__class__.__name__}",
            summary_path=summary_path,
        )

    if context is None:
        return IssueCentricRouteSelection(
            route_selected="fallback_legacy",
            target_issue="",
            target_issue_source="legacy_unresolved",
            next_request_hint="issue_resolution_unclear",
            principal_issue_kind="unresolved",
            used_normalized_summary=False,
            fallback_reason=_fallback_reason_for_summary(summary, summary_path)
            or "legacy_resolver_required",
            summary_path=summary_path,
        )

    if _should_prefer_issue_centric_route(summary, context):
        return IssueCentricRouteSelection(
            route_selected="issue_centric",
            target_issue=context.target_issue,
            target_issue_source=context.target_issue_source,
            next_request_hint=context.next_request_hint,
            principal_issue_kind=context.principal_issue_kind,
            used_normalized_summary=context.used_normalized_summary,
            fallback_reason="",
            summary_path=context.summary_path,
        )

    return IssueCentricRouteSelection(
        route_selected="fallback_legacy",
        target_issue=context.target_issue,
        target_issue_source=context.target_issue_source,
        next_request_hint=context.next_request_hint,
        principal_issue_kind=context.principal_issue_kind,
        used_normalized_summary=context.used_normalized_summary,
        fallback_reason=context.fallback_reason
        or _fallback_reason_for_summary(summary, summary_path)
        or "legacy_resolver_required",
        summary_path=context.summary_path,
    )


def recover_issue_centric_next_request_context(
    state: Mapping[str, Any],
    *,
    repo_root: Path,
) -> IssueCentricRecoveryContext | None:
    if not _has_issue_centric_recovery_candidate_state(state):
        return None

    summary_path = str(state.get("last_issue_centric_normalized_summary", "")).strip()
    dispatch_result_path = str(state.get("last_issue_centric_dispatch_result", "")).strip()
    summary = load_issue_centric_normalized_summary(state, repo_root=repo_root)
    dispatch_result = load_issue_centric_dispatch_result(state, repo_root=repo_root)
    route = select_issue_centric_next_request_route(state, repo_root=repo_root)
    principal_issue = _recover_principal_issue(summary, state)

    if dispatch_result_path and dispatch_result is None:
        return IssueCentricRecoveryContext(
            recovery_status="issue_centric_recovery_fallback",
            recovery_source="state_fallback_only",
            route_selected="fallback_legacy",
            target_issue=route.target_issue,
            target_issue_source=route.target_issue_source,
            next_request_hint=route.next_request_hint,
            principal_issue=principal_issue,
            principal_issue_kind=route.principal_issue_kind,
            used_normalized_summary=False,
            fallback_reason="dispatch_result_missing_or_unreadable",
            summary_path=summary_path,
            dispatch_result_path=dispatch_result_path,
        )

    if dispatch_result is not None and _dispatch_result_is_fatal(dispatch_result):
        return IssueCentricRecoveryContext(
            recovery_status="issue_centric_recovery_fallback",
            recovery_source="state_fallback_only",
            route_selected="fallback_legacy",
            target_issue=route.target_issue,
            target_issue_source=route.target_issue_source,
            next_request_hint=route.next_request_hint,
            principal_issue=principal_issue,
            principal_issue_kind=route.principal_issue_kind,
            used_normalized_summary=False,
            fallback_reason="dispatch_result_failed_execution",
            summary_path=summary_path,
            dispatch_result_path=dispatch_result_path,
        )

    if (
        summary is not None
        and route.route_selected == "issue_centric"
        and route.target_issue
        and principal_issue
        and route.next_request_hint != "issue_resolution_unclear"
        and _summary_has_supporting_state(summary, state)
    ):
        recovery_source = "normalized_summary_then_state"
        if dispatch_result is not None:
            recovery_source = "normalized_summary_then_dispatch_then_state"
        return IssueCentricRecoveryContext(
            recovery_status="issue_centric_recovered",
            recovery_source=recovery_source,
            route_selected=route.route_selected,
            target_issue=route.target_issue,
            target_issue_source=route.target_issue_source,
            next_request_hint=route.next_request_hint,
            principal_issue=principal_issue,
            principal_issue_kind=route.principal_issue_kind,
            used_normalized_summary=route.used_normalized_summary,
            fallback_reason="",
            summary_path=summary_path,
            dispatch_result_path=dispatch_result_path,
        )

    return IssueCentricRecoveryContext(
        recovery_status="issue_centric_recovery_fallback",
        recovery_source="state_fallback_only",
        route_selected="fallback_legacy",
        target_issue=route.target_issue,
        target_issue_source=route.target_issue_source,
        next_request_hint=route.next_request_hint,
        principal_issue=principal_issue,
        principal_issue_kind=route.principal_issue_kind,
        used_normalized_summary=False,
        fallback_reason=(
            route.fallback_reason
            or _recovery_fallback_reason_for_summary(summary, state)
            or "issue_centric_recovery_unresolved"
        ),
        summary_path=summary_path,
        dispatch_result_path=dispatch_result_path,
    )


def render_issue_centric_next_request_section(
    context: IssueCentricNextRequestContext | IssueCentricRouteSelection | IssueCentricRecoveryContext | None,
    *,
    repo_label: str,
) -> str:
    if context is None:
        return ""
    recovery_status = str(getattr(context, "recovery_status", "") or "").strip()
    recovery_source = str(getattr(context, "recovery_source", "") or "").strip()
    route_selected = str(getattr(context, "route_selected", "") or "").strip()
    target_issue = str(getattr(context, "target_issue", "") or "").strip()
    target_issue_source = str(getattr(context, "target_issue_source", "") or "").strip()
    principal_issue = str(getattr(context, "principal_issue", "") or "").strip()
    principal_issue_kind = str(getattr(context, "principal_issue_kind", "") or "").strip()
    next_request_hint = str(getattr(context, "next_request_hint", "") or "").strip()
    summary_path = str(getattr(context, "summary_path", "") or "").strip()
    dispatch_result_path = str(getattr(context, "dispatch_result_path", "") or "").strip()
    fallback_reason = str(getattr(context, "fallback_reason", "") or "").strip()
    lines = [
        "## issue_centric_next_request",
        "",
        f"- repo: {repo_label}",
    ]
    if recovery_status:
        lines.append(f"- recovery_status: {recovery_status}")
    if recovery_source:
        lines.append(f"- recovery_source: {recovery_source}")
    if route_selected:
        lines.append(f"- next_request_route: {route_selected}")
    if target_issue:
        lines.append(f"- target_issue: {target_issue}")
    if target_issue_source:
        lines.append(f"- target_issue_source: {target_issue_source}")
    if principal_issue:
        lines.append(f"- principal_issue: {principal_issue}")
    if principal_issue_kind:
        lines.append(f"- principal_issue_kind: {principal_issue_kind}")
    if next_request_hint:
        lines.append(f"- next_request_hint: {next_request_hint}")
    if summary_path:
        lines.append(f"- normalized_summary: {summary_path}")
    if dispatch_result_path:
        lines.append(f"- dispatch_result: {dispatch_result_path}")
    if fallback_reason:
        lines.append(f"- fallback_reason: {fallback_reason}")
    return "\n".join(lines).strip() + "\n"


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


def _resolve_next_request_target_from_state(state: Mapping[str, Any]) -> str:
    preferred = [
        str(state.get("last_issue_centric_next_request_target", "")).strip(),
        str(state.get("last_issue_centric_principal_issue", "")).strip(),
        str(state.get("last_issue_centric_followup_issue_url", "")).strip(),
        str(state.get("last_issue_centric_primary_issue_url", "")).strip(),
        str(state.get("last_issue_centric_resolved_issue", "")).strip(),
        str(state.get("last_issue_centric_target_issue", "")).strip(),
    ]
    for candidate in preferred:
        if candidate and candidate != "none":
            return candidate
    return ""


def _fallback_reason_for_summary(summary: Mapping[str, Any] | None, summary_path: str) -> str:
    if summary is None:
        return "normalized_summary_missing_or_unreadable" if summary_path else "normalized_summary_missing"
    if str(summary.get("next_request_hint", "")).strip() == "issue_resolution_unclear":
        return "normalized_summary_requested_fallback"
    if str(summary.get("final_status", "")).strip() == "failed":
        return "normalized_summary_failed_execution"
    return "normalized_summary_inconsistent_with_state"


def _has_issue_centric_recovery_candidate_state(state: Mapping[str, Any]) -> bool:
    keys = (
        "last_issue_centric_action",
        "last_issue_centric_normalized_summary",
        "last_issue_centric_dispatch_result",
        "last_issue_centric_principal_issue",
        "last_issue_centric_next_request_target",
        "last_issue_centric_resolved_issue",
        "last_issue_centric_target_issue",
    )
    return any(str(state.get(key, "")).strip() for key in keys)


def _recover_principal_issue(
    summary: Mapping[str, Any] | None,
    state: Mapping[str, Any],
) -> str:
    if isinstance(summary, Mapping):
        principal = summary.get("principal_issue_candidate")
        if isinstance(principal, Mapping):
            recovered = (
                str(principal.get("url", "")).strip()
                or str(principal.get("ref", "")).strip()
            )
            if recovered:
                return recovered
    return (
        str(state.get("last_issue_centric_principal_issue", "")).strip()
        or str(state.get("last_issue_centric_next_request_target", "")).strip()
        or _resolve_next_request_target_from_state(state)
    )


def _dispatch_result_is_fatal(dispatch_result: Mapping[str, Any]) -> bool:
    return str(dispatch_result.get("final_status", "")).strip() == "failed"


def _should_prefer_issue_centric_route(
    summary: Mapping[str, Any] | None,
    context: IssueCentricNextRequestContext,
) -> bool:
    if summary is None:
        return False
    if context.target_issue_source != "normalized_summary":
        return False
    if context.fallback_reason:
        return False
    if context.next_request_hint == "issue_resolution_unclear":
        return False
    if str(summary.get("final_status", "")).strip() == "failed":
        return False
    return True


def _recovery_fallback_reason_for_summary(
    summary: Mapping[str, Any] | None,
    state: Mapping[str, Any],
) -> str:
    if summary is not None and not _summary_has_supporting_state(summary, state):
        return "normalized_summary_state_missing"
    return ""


def _summary_matches_state(summary: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    principal = summary.get("principal_issue_candidate")
    if not isinstance(principal, Mapping):
        return False
    principal_url = str(principal.get("url", "")).strip()
    principal_number = str(principal.get("number", "")).strip()
    principal_kind = str(summary.get("principal_issue_kind", "")).strip()

    if principal_kind == "followup_issue":
        return _matches_issue(
            principal_url,
            principal_number,
            str(state.get("last_issue_centric_followup_issue_url", "")).strip(),
            str(state.get("last_issue_centric_followup_issue_number", "")).strip(),
        )
    if principal_kind == "primary_issue":
        return _matches_issue(
            principal_url,
            principal_number,
            str(state.get("last_issue_centric_primary_issue_url", "")).strip(),
            str(state.get("last_issue_centric_primary_issue_number", "")).strip(),
        )
    if principal_kind == "current_issue":
        return _matches_issue(
            principal_url,
            principal_number,
            str(state.get("last_issue_centric_resolved_issue", "")).strip()
            or str(state.get("last_issue_centric_target_issue", "")).strip(),
            _extract_issue_number(
                str(state.get("last_issue_centric_resolved_issue", "")).strip()
                or str(state.get("last_issue_centric_target_issue", "")).strip()
            ),
        )
    return False


def _summary_has_supporting_state(summary: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    principal_kind = str(summary.get("principal_issue_kind", "")).strip()
    if principal_kind == "followup_issue":
        return _any_state_value(
            state,
            "last_issue_centric_followup_issue_url",
            "last_issue_centric_followup_issue_number",
            "last_issue_centric_principal_issue",
            "last_issue_centric_next_request_target",
        )
    if principal_kind == "primary_issue":
        return _any_state_value(
            state,
            "last_issue_centric_primary_issue_url",
            "last_issue_centric_primary_issue_number",
            "last_issue_centric_principal_issue",
            "last_issue_centric_next_request_target",
        )
    if principal_kind == "current_issue":
        return _any_state_value(
            state,
            "last_issue_centric_resolved_issue",
            "last_issue_centric_target_issue",
            "last_issue_centric_principal_issue",
            "last_issue_centric_next_request_target",
        )
    return False


def _matches_issue(
    candidate_url: str,
    candidate_number: str,
    state_url_or_ref: str,
    state_number: str,
) -> bool:
    if not state_url_or_ref and not state_number:
        return True
    if candidate_number and state_number and candidate_number == state_number:
        return True
    if candidate_url and state_url_or_ref and candidate_url == state_url_or_ref:
        return True
    return False


def _any_state_value(state: Mapping[str, Any], *keys: str) -> bool:
    return any(str(state.get(key, "")).strip() for key in keys)
