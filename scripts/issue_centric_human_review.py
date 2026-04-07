#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from issue_centric_contract import IssueCentricAction
from issue_centric_github import (
    CreatedGitHubComment,
    GitHubIssueSnapshot,
    IssueCentricGitHubError,
    ResolvedGitHubIssue,
    create_github_issue_comment,
    fetch_github_issue,
    resolve_github_repository,
    resolve_github_token,
    resolve_target_issue,
)
from issue_centric_transport import PreparedIssueCentricDecision


class IssueCentricHumanReviewError(ValueError):
    """Raised when a prepared human_review_needed action cannot be executed safely."""


@dataclass(frozen=True)
class HumanReviewExecutionResult:
    status: str
    review_status: str
    close_policy: str
    resolved_issue: ResolvedGitHubIssue | None
    issue_before: GitHubIssueSnapshot | None
    created_comment: CreatedGitHubComment | None
    execution_log_path: Path
    safe_stop_reason: str


def execute_human_review_action(
    prepared: PreparedIssueCentricDecision,
    *,
    prior_state: Mapping[str, Any],
    project_config: Mapping[str, Any],
    repo_path: Path,
    source_decision_log: str,
    source_metadata_log: str,
    source_artifact_path: str,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    issue_fetcher: Callable[[str, int, str], GitHubIssueSnapshot] | None = None,
    comment_creator: Callable[[str, int, str, str], CreatedGitHubComment] | None = None,
    allow_followup_combo: bool = False,
    env: Mapping[str, str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> HumanReviewExecutionResult:
    if prepared.decision.action is not IssueCentricAction.HUMAN_REVIEW_NEEDED:
        raise IssueCentricHumanReviewError("human_review_needed execution only accepts action=human_review_needed.")

    now = (now_fn or _utcnow)()
    resolved_issue: ResolvedGitHubIssue | None = None
    issue_before: GitHubIssueSnapshot | None = None
    created_comment: CreatedGitHubComment | None = None
    repository = ""
    token_source = ""

    if prepared.decision.create_followup_issue and prepared.decision.close_current_issue:
        close_policy = "after_review_followup_then_close_if_followup_succeeds"
    elif prepared.decision.create_followup_issue:
        close_policy = "after_review_followup_if_review_succeeds"
    elif prepared.decision.close_current_issue:
        close_policy = "after_review_close_if_review_succeeds"
    else:
        close_policy = "review_only"

    try:
        if prepared.decision.create_followup_issue and not allow_followup_combo:
            raise IssueCentricHumanReviewError(
                "human_review_needed + create_followup_issue is not supported in this slice."
            )
        if prepared.review_body is None:
            raise IssueCentricHumanReviewError(
                "human_review_needed requires CHATGPT_REVIEW in this execution slice."
            )
        review_text = prepared.review_body.decoded_text
        if not review_text.strip():
            raise IssueCentricHumanReviewError(
                "human_review_needed review body must not be empty."
            )

        repository = resolve_github_repository(project_config=project_config, repo_path=str(repo_path))
        resolved_issue = resolve_review_target_issue(
            prepared,
            prior_state=prior_state,
            default_repository=repository,
        )
        if resolved_issue.repository != repository:
            raise IssueCentricHumanReviewError(
                "human_review_needed resolved to a different repository than the configured bridge repository."
            )

        configured_project_url = str(project_config.get("github_project_url", "")).strip()
        if configured_project_url:
            raise IssueCentricHumanReviewError(
                "github_project_url is configured, but Project review state sync is not implemented in this slice."
            )

        token, token_source = resolve_github_token(env=env)
        fetcher = issue_fetcher or fetch_github_issue
        creator = comment_creator or create_github_issue_comment
        issue_before = fetcher(resolved_issue.repository, resolved_issue.issue_number, token)
        if issue_before.state.lower() == "closed":
            raise IssueCentricHumanReviewError(
                "human_review_needed cannot post a review comment to an already closed issue in this slice."
            )

        created_comment = creator(
            resolved_issue.repository,
            resolved_issue.issue_number,
            review_text,
            token,
        )
        review_status = "completed"
        safe_stop_reason = (
            f"human_review_needed posted a review comment on issue #{resolved_issue.issue_number}."
        )
        if prepared.decision.create_followup_issue and prepared.decision.close_current_issue:
            safe_stop_reason += (
                " create_followup_issue=true may now be evaluated immediately after review; "
                "close_current_issue may run only if that follow-up path succeeds in this slice."
            )
        elif prepared.decision.create_followup_issue:
            safe_stop_reason += (
                " create_followup_issue=true may now be evaluated immediately after review in this slice."
            )
        elif prepared.decision.close_current_issue:
            safe_stop_reason += (
                " close_current_issue=true may now be evaluated immediately after review in this slice."
            )
        execution_status = "completed"
    except (IssueCentricHumanReviewError, IssueCentricGitHubError) as exc:
        review_status = "blocked"
        safe_stop_reason = f"human_review_needed execution stopped before review comment mutation completed. {exc}"
        execution_status = "blocked"
    except Exception as exc:
        review_status = "blocked_after_mutation_attempt"
        safe_stop_reason = f"human_review_needed execution stopped after a GitHub mutation failure. {exc}"
        execution_status = "blocked"

    execution_log = {
        "action": "human_review_needed",
        "status": execution_status,
        "review_status": review_status,
        "close_policy": close_policy,
        "executed_at": now.isoformat(),
        "source_decision_log": source_decision_log,
        "source_metadata_log": source_metadata_log,
        "source_prepared_artifact": source_artifact_path,
        "decision_target_issue": prepared.decision.target_issue or "none",
        "close_current_issue": prepared.decision.close_current_issue,
        "create_followup_issue": prepared.decision.create_followup_issue,
        "allow_followup_combo": allow_followup_combo,
        "repository": repository,
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
        "review_comment": (
            {
                "id": created_comment.comment_id,
                "url": created_comment.url,
                "issue_number": created_comment.issue_number,
            }
            if created_comment is not None
            else None
        ),
        "safe_stop_reason": safe_stop_reason,
    }
    execution_log_path = log_writer(
        f"issue_centric_human_review_{execution_status}",
        json.dumps(execution_log, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    return HumanReviewExecutionResult(
        status=execution_status,
        review_status=review_status,
        close_policy=close_policy,
        resolved_issue=resolved_issue,
        issue_before=issue_before,
        created_comment=created_comment,
        execution_log_path=execution_log_path,
        safe_stop_reason=safe_stop_reason,
    )


def resolve_review_target_issue(
    prepared: PreparedIssueCentricDecision,
    *,
    prior_state: Mapping[str, Any],
    default_repository: str,
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

    chosen = resolved_from_decision or resolved_from_state or resolved_from_state_target
    if chosen is None:
        raise IssueCentricHumanReviewError(
            "human_review_needed could not resolve the review target issue from target_issue or existing issue-centric state."
        )

    for other in (resolved_from_state, resolved_from_state_target):
        if other is None:
            continue
        if (other.repository, other.issue_number) != (chosen.repository, chosen.issue_number):
            raise IssueCentricHumanReviewError(
                "human_review_needed target does not match the current issue tracked by the bridge state."
            )
    return chosen


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
