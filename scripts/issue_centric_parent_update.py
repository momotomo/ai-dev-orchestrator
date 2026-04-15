#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from issue_centric_github import (
    CreatedGitHubComment,
    IssueCentricGitHubError,
    ResolvedGitHubIssue,
    create_github_issue_comment,
    resolve_github_token,
    resolve_target_issue,
)


class IssueCentricParentUpdateError(ValueError):
    """Raised when a narrow parent-issue update cannot proceed safely."""


@dataclass(frozen=True)
class ParentIssueUpdateResult:
    status: str
    update_status: str
    resolved_parent_issue: ResolvedGitHubIssue | None
    created_comment: CreatedGitHubComment | None
    closed_issue_url: str
    execution_log_path: Path
    safe_stop_reason: str


_PARENT_REF_RE = re.compile(r"(?im)^\s*Parent:\s*([^\s]+)\s*$")


def execute_parent_issue_update_after_close(
    *,
    close_execution: object,
    prior_state: Mapping[str, Any],
    source_decision_log: str,
    source_metadata_log: str,
    source_action_execution_log: str,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    comment_creator: Callable[[str, int, str, str], CreatedGitHubComment] | None = None,
    env: Mapping[str, str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> ParentIssueUpdateResult:
    now = (now_fn or _utcnow)()
    resolved_parent_issue: ResolvedGitHubIssue | None = None
    created_comment: CreatedGitHubComment | None = None
    closed_issue_url = ""
    token_source = ""

    try:
        if str(getattr(close_execution, "status", "")).strip() != "completed":
            raise IssueCentricParentUpdateError(
                "parent issue update runs only after close_current_issue completed successfully."
            )

        resolved_issue = getattr(close_execution, "resolved_issue", None)
        if resolved_issue is None:
            raise IssueCentricParentUpdateError(
                "parent issue update could not resolve the closed current issue."
            )

        closed_issue_url = str(getattr(resolved_issue, "issue_url", "") or "").strip()
        issue_snapshot = getattr(close_execution, "issue_after", None) or getattr(close_execution, "issue_before", None)
        issue_body = str(getattr(issue_snapshot, "body", "") or "").strip()
        parent_ref = _extract_parent_issue_ref(issue_body)
        if not parent_ref:
            update_status = "not_requested_missing_parent_ref"
            safe_stop_reason = (
                "parent issue update was not requested because the closed issue body did not contain a Parent: issue ref."
            )
            execution_status = "not_requested"
        else:
            resolved_parent_issue = resolve_target_issue(
                parent_ref,
                default_repository=str(getattr(resolved_issue, "repository", "")).strip(),
            )
            if (
                resolved_parent_issue.repository == getattr(resolved_issue, "repository", "")
                and resolved_parent_issue.issue_number == getattr(resolved_issue, "issue_number", -1)
            ):
                raise IssueCentricParentUpdateError(
                    "parent issue update resolved to the same issue that was just closed."
                )
            if _already_recorded(
                prior_state=prior_state,
                resolved_parent_issue=resolved_parent_issue,
                closed_issue_url=closed_issue_url,
            ):
                update_status = "already_recorded"
                safe_stop_reason = (
                    f"parent issue update for #{resolved_parent_issue.issue_number} was already recorded for {closed_issue_url}."
                )
                execution_status = "not_requested"
            else:
                token, token_source = resolve_github_token(env=env)
                creator = comment_creator or create_github_issue_comment
                comment_body = _render_parent_update_comment(
                    issue_snapshot=issue_snapshot,
                    resolved_issue=resolved_issue,
                )
                created_comment = creator(
                    resolved_parent_issue.repository,
                    resolved_parent_issue.issue_number,
                    comment_body,
                    token,
                )
                update_status = "comment_created"
                safe_stop_reason = (
                    f"parent issue #{resolved_parent_issue.issue_number} received a completion comment after issue "
                    f"#{resolved_issue.issue_number} closed."
                )
                execution_status = "completed"
    except (IssueCentricParentUpdateError, IssueCentricGitHubError) as exc:
        update_status = "blocked"
        safe_stop_reason = f"parent issue update stopped before mutation completed. {exc}"
        execution_status = "blocked"
    except Exception as exc:
        update_status = "failed_after_mutation_attempt"
        safe_stop_reason = f"parent issue update stopped after a GitHub mutation failure. {exc}"
        execution_status = "blocked"

    execution_log = {
        "status": execution_status,
        "update_status": update_status,
        "executed_at": now.isoformat(),
        "source_decision_log": source_decision_log,
        "source_metadata_log": source_metadata_log,
        "source_action_execution_log": source_action_execution_log,
        "token_source": token_source,
        "resolved_parent_issue": (
            {
                "source_ref": resolved_parent_issue.source_ref,
                "url": resolved_parent_issue.issue_url,
                "number": resolved_parent_issue.issue_number,
                "repository": resolved_parent_issue.repository,
            }
            if resolved_parent_issue is not None
            else None
        ),
        "created_comment": (
            {
                "id": created_comment.comment_id,
                "url": created_comment.url,
                "issue_number": created_comment.issue_number,
                "repository": created_comment.repository,
            }
            if created_comment is not None
            else None
        ),
        "closed_issue_url": closed_issue_url,
        "safe_stop_reason": safe_stop_reason,
    }
    execution_log_path = log_writer(
        f"issue_centric_parent_update_{execution_status}",
        json.dumps(execution_log, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    return ParentIssueUpdateResult(
        status=execution_status,
        update_status=update_status,
        resolved_parent_issue=resolved_parent_issue,
        created_comment=created_comment,
        closed_issue_url=closed_issue_url,
        execution_log_path=execution_log_path,
        safe_stop_reason=safe_stop_reason,
    )


def _extract_parent_issue_ref(issue_body: str) -> str:
    match = _PARENT_REF_RE.search(issue_body)
    if not match:
        return ""
    return match.group(1).strip()


def _render_parent_update_comment(*, issue_snapshot: object, resolved_issue: object) -> str:
    child_title = str(getattr(issue_snapshot, "title", "") or "").strip()
    child_number = int(getattr(resolved_issue, "issue_number", 0) or 0)
    child_url = str(getattr(resolved_issue, "issue_url", "") or "").strip()
    label = f"#{child_number} {child_title}".strip()
    return (
        f"`{label}` を完了として扱いました。\n"
        f"- child issue close: {child_url}\n"
        "- close_current_issue 後の narrow parent update を反映\n"
    )


def _already_recorded(
    *,
    prior_state: Mapping[str, Any],
    resolved_parent_issue: ResolvedGitHubIssue,
    closed_issue_url: str,
) -> bool:
    return (
        str(prior_state.get("last_issue_centric_parent_update_status", "")).strip() == "comment_created"
        and str(prior_state.get("last_issue_centric_parent_update_issue", "")).strip()
        == resolved_parent_issue.issue_url
        and str(prior_state.get("last_issue_centric_parent_update_closed_issue", "")).strip() == closed_issue_url
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
