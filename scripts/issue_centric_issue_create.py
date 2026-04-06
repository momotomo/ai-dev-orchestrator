#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from issue_centric_contract import IssueCentricAction
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
class CreatedGitHubIssue:
    number: int
    url: str
    title: str
    repository: str


@dataclass(frozen=True)
class IssueCreateExecutionResult:
    status: str
    draft: IssueCreateDraft | None
    created_issue: CreatedGitHubIssue | None
    draft_log_path: Path | None
    execution_log_path: Path
    project_sync_status: str
    project_sync_note: str
    safe_stop_reason: str


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
    env: Mapping[str, str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> IssueCreateExecutionResult:
    if prepared.decision.action is not IssueCentricAction.ISSUE_CREATE:
        raise IssueCentricIssueCreateError("issue_create execution only accepts action=issue_create.")

    env_map = env or os.environ
    now = (now_fn or _utcnow)()
    draft: IssueCreateDraft | None = None
    draft_log_path: Path | None = None
    created_issue: CreatedGitHubIssue | None = None
    project_sync_status = "not_requested"
    project_sync_note = "No GitHub Project requirement was configured."
    repository = ""

    try:
        draft = materialize_issue_create_draft(prepared, source_artifact_path=source_artifact_path)
        draft_log_path = log_writer(
            "prepared_issue_centric_issue_draft",
            _render_issue_draft_markdown(draft),
            "md",
        )

        configured_project_url = str(project_config.get("github_project_url", "")).strip()
        if configured_project_url:
            project_sync_status = "blocked_project_required_unimplemented"
            project_sync_note = (
                "github_project_url is configured, but Project placement is not implemented in this slice."
            )
            raise IssueCentricIssueCreateError(project_sync_note)

        repository = resolve_github_repository(project_config=project_config, repo_path=repo_path)
        token, token_source = resolve_github_token(env=env_map)
        creator = issue_creator or create_github_issue
        created_issue = creator(repository, draft.title, draft.body, token)
        project_sync_status = "issue_only_fallback"
        project_sync_note = "Created the issue without Project placement because no GitHub Project was configured."
        safe_stop_reason = (
            f"issue_create is implemented through GitHub issue creation. Created issue #{created_issue.number}. "
            "Project placement, close_current_issue execution, create_followup_issue mutation, and Codex dispatch remain unimplemented."
        )
        execution_status = "completed"
    except IssueCentricIssueCreateError as exc:
        token_source = ""
        safe_stop_reason = (
            "issue_create execution stopped before full handoff completion. "
            f"{exc}"
        )
        execution_status = "blocked"
    except Exception as exc:
        token_source = ""
        safe_stop_reason = (
            "issue_create execution stopped after a GitHub mutation failure. "
            f"{exc}"
        )
        execution_status = "blocked"

    execution_log = {
        "action": "issue_create",
        "status": execution_status,
        "executed_at": now.isoformat(),
        "source_decision_log": source_decision_log,
        "source_metadata_log": source_metadata_log,
        "source_prepared_artifact": source_artifact_path,
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
            }
            if created_issue is not None
            else None
        ),
        "close_current_issue": prepared.decision.close_current_issue,
        "create_followup_issue": prepared.decision.create_followup_issue,
        "project_sync": {
            "status": project_sync_status,
            "note": project_sync_note,
        },
        "next_step": (
            "Implement Project placement / state sync / follow-up and close execution after this slice."
        ),
        "safe_stop_reason": safe_stop_reason,
    }
    execution_log_path = log_writer(
        f"issue_centric_issue_create_{execution_status}",
        json.dumps(execution_log, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    return IssueCreateExecutionResult(
        status=execution_status,
        draft=draft,
        created_issue=created_issue,
        draft_log_path=draft_log_path,
        execution_log_path=execution_log_path,
        project_sync_status=project_sync_status,
        project_sync_note=project_sync_note,
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

    lines = prepared.issue_body.decoded_text.splitlines(keepends=True)
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


def resolve_github_repository(
    *,
    project_config: Mapping[str, Any],
    repo_path: Path,
) -> str:
    configured = str(project_config.get("github_repository", "")).strip()
    if configured:
        return configured

    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise IssueCentricIssueCreateError(
            "GitHub repository could not be resolved. Set bridge/project_config.json `github_repository` or configure an origin remote."
        ) from exc

    remote_url = completed.stdout.strip()
    if not remote_url:
        raise IssueCentricIssueCreateError(
            "GitHub repository could not be resolved because the origin remote is empty."
        )
    match = re.match(r"^(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?$", remote_url)
    if not match:
        raise IssueCentricIssueCreateError(
            f"Origin remote is not a supported GitHub remote URL: {remote_url}"
        )
    return match.group(1)


def resolve_github_token(*, env: Mapping[str, str]) -> tuple[str, str]:
    for name in ("AIDO_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = str(env.get(name, "")).strip()
        if value:
            return value, name

    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        completed = None

    if completed is not None:
        token = completed.stdout.strip()
        if token:
            return token, "gh auth token"

    raise IssueCentricIssueCreateError(
        "GitHub token is unavailable. Set AIDO_GITHUB_TOKEN, GITHUB_TOKEN, or GH_TOKEN, or make `gh auth token` available."
    )


def create_github_issue(repository: str, title: str, body: str, token: str) -> CreatedGitHubIssue:
    payload = json.dumps({"title": title, "body": body}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/issues",
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ai-dev-orchestrator-bridge",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise IssueCentricIssueCreateError(
            f"GitHub issue create failed with HTTP {exc.code}: {detail or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise IssueCentricIssueCreateError(f"GitHub issue create failed: {exc.reason}") from exc

    try:
        payload_obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IssueCentricIssueCreateError("GitHub issue create returned invalid JSON.") from exc

    number = payload_obj.get("number")
    html_url = payload_obj.get("html_url")
    returned_title = payload_obj.get("title")
    if not isinstance(number, int) or not isinstance(html_url, str) or not isinstance(returned_title, str):
        raise IssueCentricIssueCreateError(
            "GitHub issue create response is missing number / html_url / title."
        )

    return CreatedGitHubIssue(
        number=number,
        url=html_url,
        title=returned_title,
        repository=repository,
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
