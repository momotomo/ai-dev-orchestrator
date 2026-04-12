#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from issue_centric_contract import IssueCentricAction
from issue_centric_github import (
    CreatedGitHubProjectItem,
    CreatedGitHubIssue,
    IssueCentricGitHubError,
    ResolvedGitHubProjectState,
    add_issue_to_github_project,
    create_github_issue,
    resolve_github_project_state,
    resolve_github_repository,
    resolve_github_token,
    set_github_project_item_state,
)
from issue_centric_transport import PreparedIssueCentricDecision


class IssueCentricIssueCreateError(ValueError):
    """Raised when a prepared issue_create action cannot be executed safely."""


@dataclass(frozen=True)
class IssueCreateDraft:
    title: str
    body: str
    title_line: str
    source_artifact_path: str


@dataclass(frozen=True)
class IssueCreateExecutionResult:
    status: str
    draft: IssueCreateDraft | None
    created_issue: CreatedGitHubIssue | None
    draft_log_path: Path | None
    execution_log_path: Path
    project_url: str
    project_sync_status: str
    project_sync_note: str
    project_item_id: str
    project_state_field_name: str
    project_state_value_name: str
    safe_stop_reason: str


def materialize_issue_draft_text(
    decoded_text: str,
    *,
    source_artifact_path: str,
) -> IssueCreateDraft:
    lines = decoded_text.splitlines(keepends=True)
    title_index = -1
    title_line = ""
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        title_index = index
        title_line = line.rstrip("\r\n")
        break
    if title_index == -1 or not title_line.startswith("# "):
        raise IssueCentricIssueCreateError(
            "Issue draft must start with a level-1 heading (`# Title`) on the first non-empty line."
        )

    title = title_line[2:].strip()
    if not title:
        raise IssueCentricIssueCreateError("Issue draft title must not be empty.")

    body = "".join(lines[title_index + 1 :]).lstrip("\r\n")
    if not body.strip():
        raise IssueCentricIssueCreateError("Issue draft body must not be empty after the H1 title line.")

    return IssueCreateDraft(
        title=title,
        body=body,
        title_line=title_line,
        source_artifact_path=source_artifact_path,
    )


def execute_issue_create_action(
    prepared: PreparedIssueCentricDecision,
    *,
    project_config: Mapping[str, Any],
    repo_path: Path,
    source_decision_log: str,
    source_metadata_log: str,
    source_artifact_path: str,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    issue_creator: Callable[[str, str, str, str], CreatedGitHubIssue] | None = None,
    project_state_resolver: Callable[[str, str, str, str], ResolvedGitHubProjectState] | None = None,
    project_item_creator: Callable[[str, str, str], CreatedGitHubProjectItem] | None = None,
    project_state_setter: Callable[[str, str, str, str, str], None] | None = None,
    allow_followup_combo: bool = False,
    env: Mapping[str, str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> IssueCreateExecutionResult:
    if prepared.decision.action is not IssueCentricAction.ISSUE_CREATE:
        raise IssueCentricIssueCreateError("issue_create execution only accepts action=issue_create.")

    if prepared.issue_body is None:
        raise IssueCentricIssueCreateError("No decoded issue body is available for issue_create.")
    draft = materialize_issue_draft_text(
        prepared.issue_body.decoded_text,
        source_artifact_path=source_artifact_path,
    )
    return execute_issue_create_draft(
        draft,
        action_label="issue_create",
        close_current_issue=prepared.decision.close_current_issue,
        create_followup_issue=prepared.decision.create_followup_issue,
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
        allow_followup_combo=allow_followup_combo,
        env=env,
        now_fn=now_fn,
    )


def execute_issue_create_draft(
    draft: IssueCreateDraft,
    *,
    action_label: str,
    close_current_issue: bool,
    create_followup_issue: bool,
    project_config: Mapping[str, Any],
    repo_path: Path,
    source_decision_log: str,
    source_metadata_log: str,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    issue_creator: Callable[[str, str, str, str], CreatedGitHubIssue] | None = None,
    project_state_resolver: Callable[[str, str, str, str], ResolvedGitHubProjectState] | None = None,
    project_item_creator: Callable[[str, str, str], CreatedGitHubProjectItem] | None = None,
    project_state_setter: Callable[[str, str, str, str, str], None] | None = None,
    allow_followup_combo: bool = False,
    env: Mapping[str, str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> IssueCreateExecutionResult:

    now = (now_fn or _utcnow)()
    draft_log_path: Path | None = None
    created_issue: CreatedGitHubIssue | None = None
    resolved_project: ResolvedGitHubProjectState | None = None
    created_project_item: CreatedGitHubProjectItem | None = None
    project_sync_status = "not_requested"
    project_sync_note = "No GitHub Project requirement was configured."
    repository = ""
    project_url = ""
    token_source = ""

    try:
        draft_log_path = log_writer(
            f"prepared_{action_label}_draft",
            _render_issue_draft_markdown(draft),
            "md",
        )

        repository = resolve_github_repository(project_config=project_config, repo_path=str(repo_path))
        token, token_source = resolve_github_token(env=env)
        configured_project_url = str(project_config.get("github_project_url", "")).strip()
        project_state_field_name = str(project_config.get("github_project_state_field_name", "State")).strip() or "State"
        project_default_issue_state = str(project_config.get("github_project_default_issue_state", "")).strip()
        if configured_project_url:
            project_url = configured_project_url
            if not project_state_field_name:
                raise IssueCentricIssueCreateError(
                    "github_project_state_field_name must not be empty when github_project_url is configured."
                )
            if not project_default_issue_state:
                raise IssueCentricIssueCreateError(
                    "github_project_default_issue_state must not be empty when github_project_url is configured."
                )
            resolver = project_state_resolver or resolve_github_project_state
            resolved_project = resolver(
                configured_project_url,
                project_state_field_name,
                project_default_issue_state,
                token,
            )

        creator = issue_creator or create_github_issue
        created_issue = creator(repository, draft.title, draft.body, token)

        if resolved_project is None:
            project_sync_status = "issue_only_fallback"
            project_sync_note = "Created the issue without Project placement because no GitHub Project was configured."
            if allow_followup_combo:
                safe_stop_reason = (
                    f"issue_create is implemented through GitHub issue creation. Created primary issue #{created_issue.number}. "
                    "The narrow follow-up issue create path may run only after this primary issue create step succeeds. "
                    "close_current_issue may run only after both issue-create paths succeed."
                )
            else:
                safe_stop_reason = (
                    f"issue_create is implemented through GitHub issue creation. Created issue #{created_issue.number}. "
                    "Broader create_followup_issue execution, other action Project sync, and Codex dispatch remain unimplemented. "
                    "close_current_issue may run as a separate follow-up mutation in the bridge."
                )
            execution_status = "completed"
        else:
            if not created_issue.node_id:
                raise IssueCentricIssueCreateError(
                    "GitHub issue create did not return node_id required for Project placement."
                )
            item_creator = project_item_creator or add_issue_to_github_project
            try:
                created_project_item = item_creator(
                    resolved_project.project_id,
                    created_issue.node_id,
                    token,
                )
            except (IssueCentricIssueCreateError, IssueCentricGitHubError) as exc:
                project_sync_status = "issue_created_project_item_failed"
                project_sync_note = f"Created issue #{created_issue.number}, but Project item create failed. {exc}"
                safe_stop_reason = project_sync_note
                execution_status = "blocked"
            else:
                state_setter = project_state_setter or set_github_project_item_state
                try:
                    state_setter(
                        resolved_project.project_id,
                        created_project_item.item_id,
                        resolved_project.state_field_id,
                        resolved_project.state_option_id,
                        token,
                    )
                except (IssueCentricIssueCreateError, IssueCentricGitHubError) as exc:
                    project_sync_status = "issue_created_project_state_failed"
                    project_sync_note = (
                        f"Created issue #{created_issue.number} and added Project item {created_project_item.item_id}, "
                        f"but State set failed. {exc}"
                    )
                    safe_stop_reason = project_sync_note
                    execution_status = "blocked"
                else:
                    project_sync_status = "project_state_synced"
                    project_sync_note = (
                        f"Created issue #{created_issue.number}, added it to Project `{resolved_project.project_title}`, "
                        f"and set {resolved_project.state_field_name}={resolved_project.state_option_name}."
                    )
                    if allow_followup_combo:
                        safe_stop_reason = (
                            project_sync_note
                            + " The narrow follow-up issue create path may run only after this Project placement succeeds."
                        )
                    else:
                        safe_stop_reason = (
                            project_sync_note
                            + " close_current_issue may run only after this Project placement succeeds."
                        )
                    execution_status = "completed"
    except (IssueCentricIssueCreateError, IssueCentricGitHubError) as exc:
        configured_project_url = str(project_config.get("github_project_url", "")).strip()
        if configured_project_url:
            project_url = configured_project_url
            if resolved_project is None:
                project_sync_status = "blocked_project_preflight"
                project_sync_note = f"Project placement preflight failed before issue creation. {exc}"
            elif created_issue is None:
                project_sync_status = "issue_create_failed_before_project_item"
                project_sync_note = (
                    "Project preflight succeeded, but GitHub issue creation failed before Project item placement. "
                    f"{exc}"
                )
        safe_stop_reason = (
            "issue_create execution stopped before full handoff completion. "
            f"{exc}"
        )
        execution_status = "blocked"
    except Exception as exc:
        configured_project_url = str(project_config.get("github_project_url", "")).strip()
        if configured_project_url:
            project_url = configured_project_url
            if resolved_project is None:
                project_sync_status = "blocked_project_preflight"
                project_sync_note = f"Project placement preflight failed before issue creation. {exc}"
            elif created_issue is None:
                project_sync_status = "issue_create_failed_before_project_item"
                project_sync_note = (
                    "Project preflight succeeded, but GitHub issue creation failed before Project item placement. "
                    f"{exc}"
                )
        safe_stop_reason = (
            "issue_create execution stopped after a GitHub mutation failure. "
            f"{exc}"
        )
        execution_status = "blocked"

    # Surface project sync signal in issue_create action human-facing text.
    # Consistent with issue #50 signal model: synced | skipped_no_project | sync_failed.
    if project_sync_status != "not_requested":
        safe_stop_reason = safe_stop_reason + issue_create_project_sync_suffix(project_sync_status)

    execution_log = {
        "action": "issue_create",
        "execution_action": action_label,
        "status": execution_status,
        "executed_at": now.isoformat(),
        "source_decision_log": source_decision_log,
        "source_metadata_log": source_metadata_log,
        "source_prepared_artifact": draft.source_artifact_path,
        "draft": (
            {
                "title": draft.title,
                "body_chars": len(draft.body),
                "title_source_line": draft.title_line,
                "draft_log": repo_relative(draft_log_path) if draft_log_path is not None else "",
            }
            if draft is not None
            else None
        ),
        "repository": repository,
        "token_source": token_source,
        "created_issue": (
            {
                "number": created_issue.number,
                "url": created_issue.url,
                "title": created_issue.title,
                "repository": created_issue.repository,
                "node_id": created_issue.node_id,
            }
            if created_issue is not None
            else None
        ),
        "close_current_issue": close_current_issue,
        "create_followup_issue": create_followup_issue,
        "project_sync": {
            "project_url": project_url,
            "status": project_sync_status,
            "note": project_sync_note,
            "project_title": resolved_project.project_title if resolved_project is not None else "",
            "project_id": resolved_project.project_id if resolved_project is not None else "",
            "item_id": created_project_item.item_id if created_project_item is not None else "",
            "state_field_name": resolved_project.state_field_name if resolved_project is not None else "",
            "state_option_name": resolved_project.state_option_name if resolved_project is not None else "",
        },
        "next_step": (
            "Implement broader create_followup_issue handling, other action Project sync, and broader runtime cutover after this slice."
        ),
        "safe_stop_reason": safe_stop_reason,
    }
    execution_log_path = log_writer(
        f"{action_label}_{execution_status}",
        json.dumps(execution_log, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    return IssueCreateExecutionResult(
        status=execution_status,
        draft=draft,
        created_issue=created_issue,
        draft_log_path=draft_log_path,
        execution_log_path=execution_log_path,
        project_url=project_url,
        project_sync_status=project_sync_status,
        project_sync_note=project_sync_note,
        project_item_id=created_project_item.item_id if created_project_item is not None else "",
        project_state_field_name=resolved_project.state_field_name if resolved_project is not None else "",
        project_state_value_name=resolved_project.state_option_name if resolved_project is not None else "",
        safe_stop_reason=safe_stop_reason,
    )


def materialize_issue_create_draft(
    prepared: PreparedIssueCentricDecision,
    *,
    source_artifact_path: str,
) -> IssueCreateDraft:
    if prepared.decision.action is not IssueCentricAction.ISSUE_CREATE:
        raise IssueCentricIssueCreateError("issue draft materialization only supports action=issue_create.")
    if prepared.issue_body is None:
        raise IssueCentricIssueCreateError("No decoded issue body is available for issue_create.")
    return materialize_issue_draft_text(
        prepared.issue_body.decoded_text,
        source_artifact_path=source_artifact_path,
    )
def _render_issue_draft_markdown(draft: IssueCreateDraft) -> str:
    return (
        "# Issue-Centric GitHub Issue Draft\n\n"
        f"- title: {draft.title}\n"
        f"- source_artifact: {draft.source_artifact_path}\n"
        f"- title_rule: first non-empty `# ` heading line\n\n"
        "## Body\n\n"
        f"{draft.body}"
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def issue_create_project_sync_signal(project_sync_status: str) -> str:
    """Map issue_create project_sync_status to the standard three-signal vocabulary.

    Returns 'synced', 'skipped_no_project', or 'sync_failed'.
    Consistent with the signal model introduced in issue #50.
    Three signals: synced | skipped_no_project | sync_failed.
    """
    if project_sync_status == "project_state_synced":
        return "synced"
    if project_sync_status in ("issue_only_fallback", "not_requested"):
        return "skipped_no_project"
    return "sync_failed"


def issue_create_project_sync_suffix(project_sync_status: str) -> str:
    """Return a compact bracketed project sync suffix for issue_create action human-facing text.

    Returns e.g. ' [project_sync: signal=synced]' or ' [project_sync: signal=skipped_no_project]'
    or ' [project_sync: signal=sync_failed reason=...]'.
    Consistent with the signal model introduced in issue #50.
    """
    signal = issue_create_project_sync_signal(project_sync_status)
    if signal == "sync_failed":
        return f" [project_sync: signal=sync_failed reason={project_sync_status}]"
    return f" [project_sync: signal={signal}]"
