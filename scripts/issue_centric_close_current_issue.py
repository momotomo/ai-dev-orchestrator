#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from issue_centric_contract import IssueCentricAction
from issue_centric_github import (
    GitHubIssueSnapshot,
    IssueCentricGitHubError,
    ResolvedGitHubIssue,
    close_github_issue,
    fetch_github_issue,
    resolve_github_repository,
    resolve_github_token,
    resolve_target_issue,
)
from issue_centric_transport import PreparedIssueCentricDecision


class IssueCentricCloseCurrentIssueError(ValueError):
    """Raised when close_current_issue cannot be executed safely."""


@dataclass(frozen=True)
class IssueCloseExecutionResult:
    status: str
    close_status: str
    close_order: str
    resolved_issue: ResolvedGitHubIssue | None
    issue_before: GitHubIssueSnapshot | None
    issue_after: GitHubIssueSnapshot | None
    execution_log_path: Path
    safe_stop_reason: str


def execute_close_current_issue(
    prepared: PreparedIssueCentricDecision,
    *,
    prior_state: Mapping[str, Any],
    project_config: Mapping[str, Any],
    repo_path: Path,
    source_decision_log: str,
    source_metadata_log: str,
    source_action_execution_log: str,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    issue_fetcher: Callable[[str, int, str], GitHubIssueSnapshot] | None = None,
    issue_closer: Callable[[str, int, str], GitHubIssueSnapshot] | None = None,
    allow_human_review_close: bool = False,
    allow_human_review_followup_close: bool = False,
    env: Mapping[str, str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> IssueCloseExecutionResult:
    if not prepared.decision.close_current_issue:
        raise IssueCentricCloseCurrentIssueError("close_current_issue execution requires close_current_issue=true.")

    now = (now_fn or _utcnow)()
    resolved_issue: ResolvedGitHubIssue | None = None
    issue_before: GitHubIssueSnapshot | None = None
    issue_after: GitHubIssueSnapshot | None = None
    repository = ""
    token_source = ""
    close_order = _determine_close_order(
        prepared.decision.action,
        allow_human_review_close=allow_human_review_close,
        allow_human_review_followup_close=allow_human_review_followup_close,
    )

    try:
        repository = resolve_github_repository(project_config=project_config, repo_path=str(repo_path))
        configured_project_url = str(project_config.get("github_project_url", "")).strip()
        if configured_project_url:
            raise IssueCentricCloseCurrentIssueError(
                "github_project_url is configured, but Project state sync for close_current_issue is not implemented in this slice."
            )

        resolved_issue = resolve_close_target_issue(
            prepared,
            prior_state=prior_state,
            default_repository=repository,
            allow_human_review_close=allow_human_review_close,
            allow_human_review_followup_close=allow_human_review_followup_close,
        )
        if resolved_issue.repository != repository:
            raise IssueCentricCloseCurrentIssueError(
                "close_current_issue resolved to a different repository than the configured bridge repository."
            )

        token, token_source = resolve_github_token(env=env)
        fetcher = issue_fetcher or fetch_github_issue
        closer = issue_closer or close_github_issue
        issue_before = fetcher(resolved_issue.repository, resolved_issue.issue_number, token)
        if issue_before.state.lower() == "closed":
            close_status = "already_closed"
            safe_stop_reason = (
                f"close_current_issue resolved issue #{issue_before.number}, but it was already closed. "
                "No additional GitHub mutation was needed."
            )
            execution_status = "completed"
        else:
            issue_after = closer(resolved_issue.repository, resolved_issue.issue_number, token)
            if issue_after.state.lower() != "closed":
                raise IssueCentricCloseCurrentIssueError(
                    "GitHub issue close returned successfully but the issue state is not `closed`."
                )
            close_status = "closed"
            if (
                prepared.decision.action is IssueCentricAction.HUMAN_REVIEW_NEEDED
                and allow_human_review_close
                and allow_human_review_followup_close
                and prepared.decision.create_followup_issue
            ):
                safe_stop_reason = (
                    f"close_current_issue closed issue #{issue_after.number} after the review comment and follow-up issue path succeeded."
                )
            elif prepared.decision.action is IssueCentricAction.HUMAN_REVIEW_NEEDED and allow_human_review_close:
                safe_stop_reason = (
                    f"close_current_issue closed issue #{issue_after.number} after the review comment was posted."
                )
            else:
                safe_stop_reason = (
                    f"close_current_issue closed issue #{issue_after.number} after the primary action completed."
                )
            execution_status = "completed"
    except (IssueCentricCloseCurrentIssueError, IssueCentricGitHubError) as exc:
        close_status = "blocked"
        safe_stop_reason = f"close_current_issue stopped before mutation completed. {exc}"
        execution_status = "blocked"
    except Exception as exc:
        close_status = "failed_after_mutation_attempt"
        safe_stop_reason = f"close_current_issue stopped after a GitHub mutation failure. {exc}"
        execution_status = "blocked"

    execution_log = {
        "action": prepared.decision.action.value,
        "close_current_issue": True,
        "status": execution_status,
        "close_status": close_status,
        "close_order": close_order,
        "executed_at": now.isoformat(),
        "source_decision_log": source_decision_log,
        "source_metadata_log": source_metadata_log,
        "source_action_execution_log": source_action_execution_log,
        "allow_human_review_close": allow_human_review_close,
        "allow_human_review_followup_close": allow_human_review_followup_close,
        "resolved_repository": repository,
        "token_source": token_source,
        "resolved_issue": (
            {
                "source_ref": resolved_issue.source_ref,
                "url": resolved_issue.issue_url,
                "number": resolved_issue.issue_number,
                "repository": resolved_issue.repository,
            }
            if resolved_issue is not None
            else None
        ),
        "issue_before": (
            {
                "number": issue_before.number,
                "url": issue_before.url,
                "title": issue_before.title,
                "state": issue_before.state,
            }
            if issue_before is not None
            else None
        ),
        "issue_after": (
            {
                "number": issue_after.number,
                "url": issue_after.url,
                "title": issue_after.title,
                "state": issue_after.state,
            }
            if issue_after is not None
            else None
        ),
        "decision_target_issue": prepared.decision.target_issue or "none",
        "prior_state_issue": str(prior_state.get("last_issue_centric_resolved_issue", "")).strip(),
        "safe_stop_reason": safe_stop_reason,
    }
    execution_log_path = log_writer(
        f"issue_centric_close_current_issue_{execution_status}",
        json.dumps(execution_log, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    return IssueCloseExecutionResult(
        status=execution_status,
        close_status=close_status,
        close_order=close_order,
        resolved_issue=resolved_issue,
        issue_before=issue_before,
        issue_after=issue_after,
        execution_log_path=execution_log_path,
        safe_stop_reason=safe_stop_reason,
    )


def resolve_close_target_issue(
    prepared: PreparedIssueCentricDecision,
    *,
    prior_state: Mapping[str, Any],
    default_repository: str,
    allow_human_review_close: bool = False,
    allow_human_review_followup_close: bool = False,
) -> ResolvedGitHubIssue:
    decision_target = str(prepared.decision.target_issue or "").strip()
    state_resolved = str(prior_state.get("last_issue_centric_resolved_issue", "")).strip()
    state_target = str(prior_state.get("last_issue_centric_target_issue", "")).strip()

    resolved_from_decision = (
        resolve_target_issue(decision_target, default_repository=default_repository)
        if decision_target and decision_target != "none"
        else None
    )
    resolved_from_state = (
        resolve_target_issue(state_resolved, default_repository=default_repository)
        if state_resolved
        else None
    )
    resolved_from_state_target = (
        resolve_target_issue(state_target, default_repository=default_repository)
        if state_target and state_target != "none"
        else None
    )

    if prepared.decision.action is IssueCentricAction.CODEX_RUN:
        raise IssueCentricCloseCurrentIssueError(
            "action=codex_run cannot execute close_current_issue in this slice because the current execution unit remains active."
        )
    if prepared.decision.action is IssueCentricAction.HUMAN_REVIEW_NEEDED:
        if prepared.decision.create_followup_issue and not allow_human_review_followup_close:
            raise IssueCentricCloseCurrentIssueError(
                "action=human_review_needed + create_followup_issue cannot execute close_current_issue in this slice."
            )
        if not allow_human_review_close:
            raise IssueCentricCloseCurrentIssueError(
                "action=human_review_needed cannot execute close_current_issue in this slice because human review is still required."
            )

    chosen = resolved_from_decision or resolved_from_state or resolved_from_state_target
    if chosen is None:
        raise IssueCentricCloseCurrentIssueError(
            "close_current_issue could not resolve the current issue from target_issue or existing issue-centric state."
        )

    for other in (resolved_from_state, resolved_from_state_target):
        if other is None:
            continue
        if (other.repository, other.issue_number) != (chosen.repository, chosen.issue_number):
            raise IssueCentricCloseCurrentIssueError(
                "close_current_issue target does not match the current issue tracked by the bridge state."
            )
    return chosen


def _determine_close_order(
    action: IssueCentricAction,
    *,
    allow_human_review_close: bool = False,
    allow_human_review_followup_close: bool = False,
) -> str:
    if action is IssueCentricAction.ISSUE_CREATE:
        return "after_issue_create"
    if action is IssueCentricAction.NO_ACTION:
        return "after_no_action"
    if action is IssueCentricAction.HUMAN_REVIEW_NEEDED:
        if allow_human_review_followup_close:
            return "after_human_review_followup"
        return "after_human_review" if allow_human_review_close else "blocked_human_review_needed"
    if action is IssueCentricAction.CODEX_RUN:
        return "blocked_codex_run"
    return "not_supported"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
