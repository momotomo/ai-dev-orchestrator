#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from issue_centric_contract import IssueCentricAction
from issue_centric_github import (
    CreatedGitHubIssue,
    IssueCentricGitHubError,
    ResolvedGitHubIssue,
    resolve_github_repository,
    resolve_target_issue,
)
from issue_centric_issue_create import (
    IssueCreateDraft,
    IssueCreateExecutionResult,
    IssueCentricIssueCreateError,
    execute_issue_create_draft,
    materialize_issue_draft_text,
)
from issue_centric_transport import PreparedIssueCentricDecision


class IssueCentricFollowupIssueError(ValueError):
    """Raised when a no_action follow-up issue cannot be executed safely."""


@dataclass(frozen=True)
class FollowupIssueExecutionResult:
    status: str
    followup_status: str
    parent_issue: ResolvedGitHubIssue | None
    draft: IssueCreateDraft | None
    created_issue: CreatedGitHubIssue | None
    issue_create_execution_log_path: Path | None
    execution_log_path: Path
    project_url: str
    project_sync_status: str
    project_sync_note: str
    project_item_id: str
    project_state_field_name: str
    project_state_value_name: str
    close_policy: str
    safe_stop_reason: str


def execute_followup_issue_action(
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
    issue_creator: Callable[[str, str, str, str], CreatedGitHubIssue] | None = None,
    project_state_resolver: Callable[[str, str, str, str], object] | None = None,
    project_item_creator: Callable[[str, str, str], object] | None = None,
    project_state_setter: Callable[[str, str, str, str, str], None] | None = None,
    allow_human_review_combo: bool = False,
    allow_issue_create_combo: bool = False,
    allow_codex_run_combo: bool = False,
    env: Mapping[str, str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> FollowupIssueExecutionResult:
    is_human_review_combo = (
        prepared.decision.action is IssueCentricAction.HUMAN_REVIEW_NEEDED
        and allow_human_review_combo
    )
    is_issue_create_combo = (
        prepared.decision.action is IssueCentricAction.ISSUE_CREATE
        and allow_issue_create_combo
    )
    is_codex_run_combo = (
        prepared.decision.action is IssueCentricAction.CODEX_RUN
        and allow_codex_run_combo
    )
    if (
        prepared.decision.action is not IssueCentricAction.NO_ACTION
        and not is_human_review_combo
        and not is_issue_create_combo
        and not is_codex_run_combo
    ):
        raise IssueCentricFollowupIssueError(
            "follow-up issue execution only accepts action=no_action unless the narrow human_review_needed, issue_create, or codex_run combo is explicitly enabled."
        )
    if not prepared.decision.create_followup_issue:
        raise IssueCentricFollowupIssueError(
            "follow-up issue execution requires create_followup_issue=true."
        )

    now = (now_fn or _utcnow)()
    repository = ""
    parent_issue: ResolvedGitHubIssue | None = None
    draft: IssueCreateDraft | None = None
    issue_create_result: IssueCreateExecutionResult | None = None
    if is_codex_run_combo and prepared.decision.close_current_issue:
        close_policy = "after_codex_followup_success_then_close"
    elif is_codex_run_combo:
        close_policy = "after_codex_followup_success_only"
    elif is_issue_create_combo and prepared.decision.close_current_issue:
        close_policy = "after_issue_create_followup_success_then_close"
    elif is_issue_create_combo:
        close_policy = "after_issue_create_followup_success_only"
    elif is_human_review_combo and prepared.decision.close_current_issue:
        close_policy = "after_review_followup_success_then_close"
    elif is_human_review_combo:
        close_policy = "after_review_followup_success_only"
    elif prepared.decision.close_current_issue:
        close_policy = "after_followup_success_only"
    else:
        close_policy = "followup_only"

    try:
        if prepared.followup_issue_body is None:
            raise IssueCentricFollowupIssueError(
                "create_followup_issue=true requires a decoded CHATGPT_FOLLOWUP_ISSUE_BODY artifact."
            )
        repository = resolve_github_repository(project_config=project_config, repo_path=str(repo_path))
        parent_issue = resolve_followup_parent_issue(
            prepared,
            prior_state=prior_state,
            default_repository=repository,
        )
        if parent_issue.repository != repository:
            raise IssueCentricFollowupIssueError(
                "follow-up issue target resolves to a different repository than the configured bridge repository."
            )

        draft = materialize_issue_draft_text(
            prepared.followup_issue_body.decoded_text,
            source_artifact_path=source_artifact_path,
        )
        issue_create_result = execute_issue_create_draft(
            draft,
            action_label=(
                "codex_run_followup_issue_create"
                if is_codex_run_combo
                else (
                    "issue_create_followup_issue_create"
                    if is_issue_create_combo
                    else (
                        "review_followup_issue_create"
                        if is_human_review_combo
                        else "followup_issue_create"
                    )
                )
            ),
            close_current_issue=prepared.decision.close_current_issue,
            create_followup_issue=True,
            project_config=project_config,
            repo_path=repo_path,
            source_decision_log=source_decision_log,
            source_metadata_log=source_metadata_log,
            log_writer=log_writer,
            repo_relative=repo_relative,
            issue_creator=issue_creator,
            project_state_resolver=project_state_resolver,
            project_item_creator=project_item_creator,
            project_state_setter=project_state_setter,
            allow_followup_combo=is_issue_create_combo or is_codex_run_combo,
            env=env,
            now_fn=lambda: now,
        )
        if issue_create_result.status == "completed":
            followup_status = "completed"
            if is_codex_run_combo:
                safe_stop_reason = (
                    f"Created follow-up issue #{issue_create_result.created_issue.number} after the issue-centric Codex launch / continuation path succeeded. "
                    "close_current_issue may run only after this follow-up creation path succeeds."
                )
            elif is_issue_create_combo:
                safe_stop_reason = (
                    f"Created follow-up issue #{issue_create_result.created_issue.number} after the primary issue create step succeeded. "
                    "close_current_issue may run only after both primary and follow-up issue creation paths succeed."
                )
            elif is_human_review_combo:
                safe_stop_reason = (
                    f"Created follow-up issue #{issue_create_result.created_issue.number} for current issue "
                    f"#{parent_issue.issue_number} after the review comment step succeeded. "
                    "close_current_issue may run only after this follow-up creation path succeeds."
                )
            else:
                safe_stop_reason = (
                    f"Created follow-up issue #{issue_create_result.created_issue.number} for current issue "
                    f"#{parent_issue.issue_number}. close_current_issue may run only after this follow-up creation path succeeds."
                )
            execution_status = "completed"
        else:
            followup_status = issue_create_result.project_sync_status or "blocked"
            safe_stop_reason = (
                "follow-up issue execution stopped before the new execution unit was fully established. "
                f"{issue_create_result.safe_stop_reason}"
            )
            execution_status = "blocked"
    except (IssueCentricFollowupIssueError, IssueCentricIssueCreateError, IssueCentricGitHubError) as exc:
        followup_status = "blocked"
        safe_stop_reason = f"follow-up issue execution stopped before mutation completed. {exc}"
        execution_status = "blocked"
    except Exception as exc:
        followup_status = "failed_after_mutation_attempt"
        safe_stop_reason = f"follow-up issue execution stopped after a GitHub mutation failure. {exc}"
        execution_status = "blocked"

    execution_log = {
        "action": prepared.decision.action.value,
        "followup_execution": "followup_issue_create",
        "status": execution_status,
        "followup_status": followup_status,
        "executed_at": now.isoformat(),
        "source_decision_log": source_decision_log,
        "source_metadata_log": source_metadata_log,
        "source_prepared_artifact": source_artifact_path,
        "current_issue": (
            {
                "source_ref": parent_issue.source_ref,
                "url": parent_issue.issue_url,
                "number": parent_issue.issue_number,
                "repository": parent_issue.repository,
            }
            if parent_issue is not None
            else None
        ),
        "draft": (
            {
                "title": draft.title,
                "body_chars": len(draft.body),
                "title_source_line": draft.title_line,
            }
            if draft is not None
            else None
        ),
        "created_followup_issue": (
            {
                "number": issue_create_result.created_issue.number,
                "url": issue_create_result.created_issue.url,
                "title": issue_create_result.created_issue.title,
                "repository": issue_create_result.created_issue.repository,
            }
            if issue_create_result is not None and issue_create_result.created_issue is not None
            else None
        ),
        "project_sync": (
            {
                "status": issue_create_result.project_sync_status,
                "note": issue_create_result.project_sync_note,
                "project_url": issue_create_result.project_url,
                "project_item_id": issue_create_result.project_item_id,
                "state_field_name": issue_create_result.project_state_field_name,
                "state_value_name": issue_create_result.project_state_value_name,
            }
            if issue_create_result is not None
            else None
        ),
        "close_current_issue": prepared.decision.close_current_issue,
        "close_policy": close_policy,
        "allow_human_review_combo": allow_human_review_combo,
        "allow_issue_create_combo": allow_issue_create_combo,
        "allow_codex_run_combo": allow_codex_run_combo,
        "inner_issue_create_log": (
            repo_relative(issue_create_result.execution_log_path)
            if issue_create_result is not None
            else ""
        ),
        "safe_stop_reason": safe_stop_reason,
    }
    execution_log_path = log_writer(
        f"issue_centric_followup_issue_{execution_status}",
        json.dumps(execution_log, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    return FollowupIssueExecutionResult(
        status=execution_status,
        followup_status=followup_status,
        parent_issue=parent_issue,
        draft=draft,
        created_issue=(issue_create_result.created_issue if issue_create_result is not None else None),
        issue_create_execution_log_path=(
            issue_create_result.execution_log_path if issue_create_result is not None else None
        ),
        execution_log_path=execution_log_path,
        project_url=issue_create_result.project_url if issue_create_result is not None else "",
        project_sync_status=issue_create_result.project_sync_status if issue_create_result is not None else "",
        project_sync_note=issue_create_result.project_sync_note if issue_create_result is not None else "",
        project_item_id=issue_create_result.project_item_id if issue_create_result is not None else "",
        project_state_field_name=(
            issue_create_result.project_state_field_name if issue_create_result is not None else ""
        ),
        project_state_value_name=(
            issue_create_result.project_state_value_name if issue_create_result is not None else ""
        ),
        close_policy=close_policy,
        safe_stop_reason=safe_stop_reason,
    )


def resolve_followup_parent_issue(
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
        raise IssueCentricFollowupIssueError(
            "follow-up issue execution could not resolve the current issue from target_issue or existing issue-centric state."
        )

    for other in (resolved_from_state, resolved_from_state_target):
        if other is None:
            continue
        if (other.repository, other.issue_number) != (chosen.repository, chosen.issue_number):
            raise IssueCentricFollowupIssueError(
                "follow-up issue target does not match the current issue tracked by the bridge state."
            )
    return chosen


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
