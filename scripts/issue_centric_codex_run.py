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
    IssueCentricGitHubError,
    ResolvedGitHubIssue,
    create_github_issue_comment,
    resolve_github_repository,
    resolve_github_token,
    resolve_target_issue,
)
from issue_centric_transport import PreparedIssueCentricDecision


class IssueCentricCodexRunError(ValueError):
    """Raised when a prepared codex_run action cannot be executed safely."""


@dataclass(frozen=True)
class CodexRunExecutionPayload:
    repo: str
    target_issue: str
    request: str
    trigger_comment: str


@dataclass(frozen=True)
class CodexRunExecutionResult:
    status: str
    resolved_issue: ResolvedGitHubIssue | None
    created_comment: CreatedGitHubComment | None
    payload: CodexRunExecutionPayload | None
    payload_log_path: Path | None
    execution_log_path: Path
    launch_status: str
    launch_note: str
    safe_stop_reason: str


def execute_codex_run_action(
    prepared: PreparedIssueCentricDecision,
    *,
    project_config: Mapping[str, Any],
    repo_path: Path,
    source_decision_log: str,
    source_metadata_log: str,
    source_artifact_path: str,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    comment_creator: Callable[[str, int, str, str], CreatedGitHubComment] | None = None,
    env: Mapping[str, str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> CodexRunExecutionResult:
    if prepared.decision.action is not IssueCentricAction.CODEX_RUN:
        raise IssueCentricCodexRunError("codex_run execution only accepts action=codex_run.")
    if prepared.codex_body is None:
        raise IssueCentricCodexRunError("No decoded CODEX_BODY is available for codex_run.")
    if not prepared.decision.target_issue:
        raise IssueCentricCodexRunError("codex_run requires a target issue.")

    now = (now_fn or _utcnow)()
    resolved_issue: ResolvedGitHubIssue | None = None
    created_comment: CreatedGitHubComment | None = None
    payload: CodexRunExecutionPayload | None = None
    payload_log_path: Path | None = None
    repository = ""
    token_source = ""

    try:
        repository = resolve_github_repository(project_config=project_config, repo_path=str(repo_path))
        resolved_issue = resolve_target_issue(
            prepared.decision.target_issue,
            default_repository=repository,
        )
        token, token_source = resolve_github_token(env=env)
        creator = comment_creator or create_github_issue_comment
        created_comment = creator(
            resolved_issue.repository,
            resolved_issue.issue_number,
            prepared.codex_body.decoded_text,
            token,
        )
        payload = CodexRunExecutionPayload(
            repo=str(repo_path),
            target_issue=resolved_issue.issue_url,
            request=prepared.codex_body.decoded_text,
            trigger_comment=created_comment.url,
        )
        payload_log_path = log_writer(
            "prepared_issue_centric_codex_run_payload",
            json.dumps(
                {
                    "repo": payload.repo,
                    "target_issue": payload.target_issue,
                    "request_chars": len(payload.request),
                    "trigger_comment": payload.trigger_comment,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            "json",
        )
        launch_status = "not_implemented"
        launch_note = (
            "Trigger comment is registered. Issue-centric Codex launch wiring is intentionally not connected in this slice."
        )
        safe_stop_reason = (
            f"codex_run trigger comment was registered on issue #{resolved_issue.issue_number}. "
            "The assembled repo / target_issue / request / trigger_comment payload is ready, but issue-centric Codex launch remains unimplemented."
        )
        execution_status = "completed"
    except (IssueCentricCodexRunError, IssueCentricGitHubError) as exc:
        launch_status = "not_attempted"
        launch_note = "Execution stopped before issue-centric Codex launch."
        safe_stop_reason = f"codex_run execution stopped before launch. {exc}"
        execution_status = "blocked"
    except Exception as exc:
        launch_status = "not_attempted"
        launch_note = "Execution stopped after a GitHub mutation failure."
        safe_stop_reason = f"codex_run execution stopped after a GitHub mutation failure. {exc}"
        execution_status = "blocked"

    execution_log = {
        "action": "codex_run",
        "status": execution_status,
        "executed_at": now.isoformat(),
        "source_decision_log": source_decision_log,
        "source_metadata_log": source_metadata_log,
        "source_prepared_artifact": source_artifact_path,
        "repository": repository,
        "token_source": token_source,
        "target_issue": (
            {
                "input": prepared.decision.target_issue,
                "resolved_url": resolved_issue.issue_url,
                "resolved_repository": resolved_issue.repository,
                "resolved_number": resolved_issue.issue_number,
            }
            if resolved_issue is not None
            else None
        ),
        "trigger_comment": (
            {
                "id": created_comment.comment_id,
                "url": created_comment.url,
                "issue_number": created_comment.issue_number,
            }
            if created_comment is not None
            else None
        ),
        "assembled_execution_payload": (
            {
                "repo": payload.repo,
                "target_issue": payload.target_issue,
                "request_chars": len(payload.request),
                "trigger_comment": payload.trigger_comment,
                "payload_log": repo_relative(payload_log_path) if payload_log_path is not None else "",
            }
            if payload is not None
            else None
        ),
        "close_current_issue": prepared.decision.close_current_issue,
        "create_followup_issue": prepared.decision.create_followup_issue,
        "launch": {
            "status": launch_status,
            "note": launch_note,
        },
        "safe_stop_reason": safe_stop_reason,
    }
    execution_log_path = log_writer(
        f"issue_centric_codex_run_{execution_status}",
        json.dumps(execution_log, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    return CodexRunExecutionResult(
        status=execution_status,
        resolved_issue=resolved_issue,
        created_comment=created_comment,
        payload=payload,
        payload_log_path=payload_log_path,
        execution_log_path=execution_log_path,
        launch_status=launch_status,
        launch_note=launch_note,
        safe_stop_reason=safe_stop_reason,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
