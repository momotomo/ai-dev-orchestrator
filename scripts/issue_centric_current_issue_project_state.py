#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from issue_centric_github import (
    GitHubIssueSnapshot,
    IssueCentricGitHubError,
    ResolvedGitHubIssue,
    ResolvedGitHubProjectItem,
    ResolvedGitHubProjectState,
    fetch_github_issue,
    resolve_github_project_item_for_issue,
    resolve_github_project_state,
    resolve_github_repository,
    resolve_github_token,
    resolve_target_issue,
    set_github_project_item_state,
)
from issue_centric_transport import PreparedIssueCentricDecision


class IssueCentricCurrentIssueProjectStateError(ValueError):
    """Raised when current issue Project State sync cannot proceed safely."""


@dataclass(frozen=True)
class CurrentIssueProjectStateSyncResult:
    status: str
    sync_status: str
    lifecycle_stage: str
    resolved_issue: ResolvedGitHubIssue | None
    issue_snapshot: GitHubIssueSnapshot | None
    execution_log_path: Path
    project_url: str
    project_item_id: str
    project_state_field_name: str
    project_state_value_name: str
    safe_stop_reason: str


def execute_current_issue_project_state_sync(
    prepared: PreparedIssueCentricDecision,
    *,
    lifecycle_stage: str,
    prior_state: Mapping[str, Any],
    project_config: Mapping[str, Any],
    repo_path: Path,
    source_decision_log: str,
    source_metadata_log: str,
    source_action_execution_log: str,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    issue_fetcher: Callable[[str, int, str], GitHubIssueSnapshot] | None = None,
    project_state_resolver: Callable[[str, str, str, str], ResolvedGitHubProjectState] | None = None,
    project_item_resolver: Callable[[str, str, str], ResolvedGitHubProjectItem] | None = None,
    project_state_setter: Callable[[str, str, str, str, str], None] | None = None,
    env: Mapping[str, str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> CurrentIssueProjectStateSyncResult:
    now = (now_fn or _utcnow)()
    resolved_issue: ResolvedGitHubIssue | None = None
    issue_snapshot: GitHubIssueSnapshot | None = None
    resolved_project: ResolvedGitHubProjectState | None = None
    resolved_project_item: ResolvedGitHubProjectItem | None = None
    repository = ""
    token_source = ""
    project_url = str(project_config.get("github_project_url", "")).strip()
    project_item_id = ""
    project_state_field_name = ""
    project_state_value_name = ""

    try:
        if not project_url:
            sync_status = "not_requested_no_project"
            safe_stop_reason = "No GitHub Project is configured for current-issue lifecycle state sync."
            execution_status = "not_requested"
        else:
            repository = resolve_github_repository(project_config=project_config, repo_path=str(repo_path))
            token, token_source = resolve_github_token(env=env)
            state_field_name = str(project_config.get("github_project_state_field_name", "State")).strip() or "State"
            state_value_name = _resolve_lifecycle_state_value(project_config, lifecycle_stage)
            resolved_issue = resolve_current_issue_for_project_state_sync(
                prepared,
                prior_state=prior_state,
                default_repository=repository,
            )
            if resolved_issue.repository != repository:
                raise IssueCentricCurrentIssueProjectStateError(
                    "current issue lifecycle Project sync resolved to a different repository than the configured bridge repository."
                )
            fetcher = issue_fetcher or fetch_github_issue
            issue_snapshot = fetcher(resolved_issue.repository, resolved_issue.issue_number, token)
            if not issue_snapshot.node_id:
                raise IssueCentricCurrentIssueProjectStateError(
                    "current issue lifecycle Project sync requires issue node_id, but the GitHub issue fetch did not return one."
                )

            resolver = project_state_resolver or resolve_github_project_state
            resolved_project = resolver(
                project_url,
                state_field_name=state_field_name,
                state_option_name=state_value_name,
                token=token,
            )
            project_state_field_name = resolved_project.state_field_name
            project_state_value_name = resolved_project.state_option_name

            resolved_project_item = _ensure_project_item_for_issue(
                resolved_project=resolved_project,
                issue_snapshot=issue_snapshot,
                prior_state=prior_state,
                project_url=project_url,
                project_item_resolver=project_item_resolver,
                token=token,
            )
            project_item_id = resolved_project_item.item_id

            _sync_project_state_field(
                resolved_project=resolved_project,
                project_item_id=project_item_id,
                project_state_setter=project_state_setter,
                token=token,
            )
            sync_status = "project_state_synced"
            safe_stop_reason = (
                f"Synced current issue #{issue_snapshot.number} to Project `{resolved_project.project_title}` with "
                f"{resolved_project.state_field_name}={resolved_project.state_option_name} after the {lifecycle_stage} step."
            )
            execution_status = "completed"
    except (IssueCentricCurrentIssueProjectStateError, IssueCentricGitHubError) as exc:
        sync_status = "project_state_sync_failed"
        safe_stop_reason = f"current issue lifecycle Project State sync stopped before completion. {exc}"
        execution_status = "blocked"
    except Exception as exc:
        sync_status = "project_state_sync_failed"
        safe_stop_reason = f"current issue lifecycle Project State sync stopped after a GitHub mutation failure. {exc}"
        execution_status = "blocked"

    execution_log = {
        "lifecycle_stage": lifecycle_stage,
        "status": execution_status,
        "sync_status": sync_status,
        "executed_at": now.isoformat(),
        "source_decision_log": source_decision_log,
        "source_metadata_log": source_metadata_log,
        "source_action_execution_log": source_action_execution_log,
        "resolved_repository": repository,
        "token_source": token_source,
        "project_url": project_url,
        "current_issue": (
            {
                "source_ref": resolved_issue.source_ref,
                "url": resolved_issue.issue_url,
                "number": resolved_issue.issue_number,
                "repository": resolved_issue.repository,
            }
            if resolved_issue is not None
            else None
        ),
        "issue_snapshot": (
            {
                "number": issue_snapshot.number,
                "url": issue_snapshot.url,
                "title": issue_snapshot.title,
                "repository": issue_snapshot.repository,
                "state": issue_snapshot.state,
                "node_id": issue_snapshot.node_id,
            }
            if issue_snapshot is not None
            else None
        ),
        "project_item_id": project_item_id,
        "project_state_field_name": project_state_field_name,
        "project_state_value_name": project_state_value_name,
        "safe_stop_reason": safe_stop_reason,
    }
    execution_log_path = log_writer(
        f"issue_centric_current_issue_project_state_{lifecycle_stage}_{execution_status}",
        json.dumps(execution_log, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    return CurrentIssueProjectStateSyncResult(
        status=execution_status,
        sync_status=sync_status,
        lifecycle_stage=lifecycle_stage,
        resolved_issue=resolved_issue,
        issue_snapshot=issue_snapshot,
        execution_log_path=execution_log_path,
        project_url=project_url,
        project_item_id=project_item_id,
        project_state_field_name=project_state_field_name,
        project_state_value_name=project_state_value_name,
        safe_stop_reason=safe_stop_reason,
    )


def resolve_current_issue_for_project_state_sync(
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
        raise IssueCentricCurrentIssueProjectStateError(
            "current issue lifecycle Project sync could not resolve the current issue from target_issue or existing issue-centric state."
        )

    for other in (resolved_from_state, resolved_from_state_target):
        if other is None:
            continue
        if (other.repository, other.issue_number) != (chosen.repository, chosen.issue_number):
            raise IssueCentricCurrentIssueProjectStateError(
                "current issue lifecycle Project sync target does not match the current issue tracked by the bridge state."
            )
    return chosen


def _ensure_project_item_for_issue(
    *,
    resolved_project: ResolvedGitHubProjectState,
    issue_snapshot: GitHubIssueSnapshot,
    prior_state: Mapping[str, Any],
    project_url: str,
    project_item_resolver: Callable[[str, str, str], ResolvedGitHubProjectItem] | None,
    token: str,
) -> ResolvedGitHubProjectItem:
    """Return a project item for *issue_snapshot*, using the cached item_id when available.

    Cache hit: if the state already holds a ``last_issue_centric_current_project_item_id``
    for the same project URL, the cached id is promoted to a ``ResolvedGitHubProjectItem``
    without hitting the GitHub API.

    Cache miss: the resolver is called (default: ``resolve_github_project_item_for_issue``).
    Raises ``IssueCentricGitHubError`` / ``IssueCentricCurrentIssueProjectStateError`` on
    failure — the caller is responsible for mapping those to the appropriate sync_status.
    """
    cached_project_url = str(prior_state.get("last_issue_centric_current_project_url", "")).strip()
    cached_project_item_id = str(prior_state.get("last_issue_centric_current_project_item_id", "")).strip()
    if cached_project_url == project_url and cached_project_item_id:
        return ResolvedGitHubProjectItem(
            item_id=cached_project_item_id,
            project_id=resolved_project.project_id,
            issue_node_id=issue_snapshot.node_id,
            issue_number=issue_snapshot.number,
            repository=issue_snapshot.repository,
        )
    resolver = project_item_resolver or resolve_github_project_item_for_issue
    return resolver(
        project_id=resolved_project.project_id,
        issue_node_id=issue_snapshot.node_id,
        token=token,
    )


def _sync_project_state_field(
    *,
    resolved_project: ResolvedGitHubProjectState,
    project_item_id: str,
    project_state_setter: Callable[[str, str, str, str, str], None] | None,
    token: str,
) -> None:
    """Update the project state field for *project_item_id*.

    Calls ``set_github_project_item_state`` (or the injected *project_state_setter*).
    Raises ``IssueCentricGitHubError`` on failure — the caller maps that to
    ``project_state_sync_failed``.
    """
    setter = project_state_setter or set_github_project_item_state
    setter(
        project_id=resolved_project.project_id,
        item_id=project_item_id,
        field_id=resolved_project.state_field_id,
        option_id=resolved_project.state_option_id,
        token=token,
    )


def _resolve_lifecycle_state_value(project_config: Mapping[str, Any], lifecycle_stage: str) -> str:
    mapping = {
        "in_progress": ("github_project_in_progress_state", "in_progress"),
        "review": ("github_project_review_state", "review"),
        "done": ("github_project_done_state", "done"),
    }
    if lifecycle_stage not in mapping:
        raise IssueCentricCurrentIssueProjectStateError(
            f"unsupported current issue lifecycle stage for Project sync: {lifecycle_stage}"
        )
    key, default_value = mapping[lifecycle_stage]
    configured_value = project_config.get(key)
    if configured_value is None:
        value = default_value
    else:
        value = str(configured_value).strip()
    if not value:
        raise IssueCentricCurrentIssueProjectStateError(
            f"{key} must not be empty when github_project_url is configured and current issue lifecycle sync is requested."
        )
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
