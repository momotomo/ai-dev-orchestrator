#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping


class IssueCentricGitHubError(ValueError):
    """Raised when a narrow issue-centric GitHub operation cannot proceed safely."""


@dataclass(frozen=True)
class CreatedGitHubIssue:
    number: int
    url: str
    title: str
    repository: str


@dataclass(frozen=True)
class CreatedGitHubComment:
    comment_id: int
    url: str
    body: str
    repository: str
    issue_number: int


@dataclass(frozen=True)
class ResolvedGitHubIssue:
    repository: str
    issue_number: int
    issue_url: str
    source_ref: str


def resolve_github_repository(
    *,
    project_config: Mapping[str, Any],
    repo_path: str,
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
        raise IssueCentricGitHubError(
            "GitHub repository could not be resolved. Set bridge/project_config.json `github_repository` or configure an origin remote."
        ) from exc

    remote_url = completed.stdout.strip()
    if not remote_url:
        raise IssueCentricGitHubError(
            "GitHub repository could not be resolved because the origin remote is empty."
        )
    match = re.match(r"^(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?$", remote_url)
    if not match:
        raise IssueCentricGitHubError(
            f"Origin remote is not a supported GitHub remote URL: {remote_url}"
        )
    return match.group(1)


def resolve_github_token(*, env: Mapping[str, str] | None = None) -> tuple[str, str]:
    env_map = env or os.environ
    for name in ("AIDO_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = str(env_map.get(name, "")).strip()
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

    raise IssueCentricGitHubError(
        "GitHub token is unavailable. Set AIDO_GITHUB_TOKEN, GITHUB_TOKEN, or GH_TOKEN, or make `gh auth token` available."
    )


def resolve_target_issue(
    raw_target_issue: str,
    *,
    default_repository: str,
) -> ResolvedGitHubIssue:
    raw = raw_target_issue.strip()
    if not raw:
        raise IssueCentricGitHubError("target issue reference is empty.")

    url_match = re.match(r"^https://github\.com/([^/]+/[^/]+)/issues/([0-9]+)$", raw)
    if url_match:
        repository = url_match.group(1)
        number = int(url_match.group(2))
        return ResolvedGitHubIssue(
            repository=repository,
            issue_number=number,
            issue_url=raw,
            source_ref=raw,
        )

    repo_ref_match = re.match(r"^([^/\s]+/[^#\s]+)#([0-9]+)$", raw)
    if repo_ref_match:
        repository = repo_ref_match.group(1)
        number = int(repo_ref_match.group(2))
        return ResolvedGitHubIssue(
            repository=repository,
            issue_number=number,
            issue_url=f"https://github.com/{repository}/issues/{number}",
            source_ref=raw,
        )

    hash_match = re.match(r"^#?([0-9]+)$", raw)
    if hash_match:
        number = int(hash_match.group(1))
        repository = default_repository.strip()
        if not repository:
            raise IssueCentricGitHubError(
                "target issue reference uses only a number, but the default GitHub repository is unavailable."
            )
        return ResolvedGitHubIssue(
            repository=repository,
            issue_number=number,
            issue_url=f"https://github.com/{repository}/issues/{number}",
            source_ref=raw,
        )

    raise IssueCentricGitHubError(
        f"target issue reference is unsupported: {raw}. Use #123, 123, owner/repo#123, or a full issue URL."
    )


def create_github_issue(repository: str, title: str, body: str, token: str) -> CreatedGitHubIssue:
    payload_obj = _github_api_request(
        method="POST",
        url=f"https://api.github.com/repos/{repository}/issues",
        token=token,
        payload={"title": title, "body": body},
        context="GitHub issue create",
    )
    number = payload_obj.get("number")
    html_url = payload_obj.get("html_url")
    returned_title = payload_obj.get("title")
    if not isinstance(number, int) or not isinstance(html_url, str) or not isinstance(returned_title, str):
        raise IssueCentricGitHubError(
            "GitHub issue create response is missing number / html_url / title."
        )
    return CreatedGitHubIssue(
        number=number,
        url=html_url,
        title=returned_title,
        repository=repository,
    )


def create_github_issue_comment(
    repository: str,
    issue_number: int,
    body: str,
    token: str,
) -> CreatedGitHubComment:
    payload_obj = _github_api_request(
        method="POST",
        url=f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments",
        token=token,
        payload={"body": body},
        context="GitHub issue comment create",
    )
    comment_id = payload_obj.get("id")
    html_url = payload_obj.get("html_url")
    returned_body = payload_obj.get("body")
    if not isinstance(comment_id, int) or not isinstance(html_url, str) or not isinstance(returned_body, str):
        raise IssueCentricGitHubError(
            "GitHub issue comment create response is missing id / html_url / body."
        )
    return CreatedGitHubComment(
        comment_id=comment_id,
        url=html_url,
        body=returned_body,
        repository=repository,
        issue_number=issue_number,
    )


def _github_api_request(
    *,
    method: str,
    url: str,
    token: str,
    payload: dict[str, object],
    context: str,
) -> dict[str, object]:
    encoded = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method=method,
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
        raise IssueCentricGitHubError(
            f"{context} failed with HTTP {exc.code}: {detail or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise IssueCentricGitHubError(f"{context} failed: {exc.reason}") from exc

    try:
        payload_obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IssueCentricGitHubError(f"{context} returned invalid JSON.") from exc
    if not isinstance(payload_obj, dict):
        raise IssueCentricGitHubError(f"{context} returned a non-object JSON payload.")
    return payload_obj
