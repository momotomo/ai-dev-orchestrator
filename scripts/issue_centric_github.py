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
    node_id: str = ""


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


@dataclass(frozen=True)
class GitHubIssueSnapshot:
    number: int
    url: str
    title: str
    repository: str
    state: str
    node_id: str = ""


@dataclass(frozen=True)
class ResolvedGitHubProjectState:
    project_id: str
    project_url: str
    project_title: str
    owner_login: str
    owner_kind: str
    state_field_id: str
    state_field_name: str
    state_option_id: str
    state_option_name: str


@dataclass(frozen=True)
class CreatedGitHubProjectItem:
    item_id: str
    project_id: str


@dataclass(frozen=True)
class ResolvedGitHubProjectItem:
    item_id: str
    project_id: str
    issue_node_id: str
    issue_number: int
    repository: str


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
    node_id = payload_obj.get("node_id")
    if (
        not isinstance(number, int)
        or not isinstance(html_url, str)
        or not isinstance(returned_title, str)
        or not isinstance(node_id, str)
    ):
        raise IssueCentricGitHubError(
            "GitHub issue create response is missing number / html_url / title / node_id."
        )
    return CreatedGitHubIssue(
        number=number,
        url=html_url,
        title=returned_title,
        repository=repository,
        node_id=node_id,
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


def fetch_github_issue(
    repository: str,
    issue_number: int,
    token: str,
) -> GitHubIssueSnapshot:
    payload_obj = _github_api_request(
        method="GET",
        url=f"https://api.github.com/repos/{repository}/issues/{issue_number}",
        token=token,
        payload=None,
        context="GitHub issue fetch",
    )
    return _parse_issue_snapshot(
        payload_obj,
        repository=repository,
        context="GitHub issue fetch",
    )


def close_github_issue(
    repository: str,
    issue_number: int,
    token: str,
) -> GitHubIssueSnapshot:
    payload_obj = _github_api_request(
        method="PATCH",
        url=f"https://api.github.com/repos/{repository}/issues/{issue_number}",
        token=token,
        payload={"state": "closed"},
        context="GitHub issue close",
    )
    return _parse_issue_snapshot(
        payload_obj,
        repository=repository,
        context="GitHub issue close",
    )


def resolve_github_project_state(
    project_url: str,
    *,
    state_field_name: str,
    state_option_name: str,
    token: str,
) -> ResolvedGitHubProjectState:
    owner_kind, owner_login, project_number = _parse_project_url(project_url)
    query = _project_query_for_owner_kind(owner_kind)
    payload_obj = _github_graphql_request(
        token=token,
        query=query,
        variables={"login": owner_login, "number": project_number},
        context="GitHub Project resolve",
    )
    owner_data = payload_obj.get("user" if owner_kind == "users" else "organization")
    if not isinstance(owner_data, dict):
        raise IssueCentricGitHubError(
            f"GitHub Project resolve did not return a valid {owner_kind.rstrip('s')} object."
        )
    project = owner_data.get("projectV2")
    if not isinstance(project, dict):
        raise IssueCentricGitHubError(
            "GitHub Project could not be resolved from github_project_url."
        )

    project_id = project.get("id")
    project_title = project.get("title")
    fields = (((project.get("fields") or {}).get("nodes")) if isinstance(project.get("fields"), dict) else None)
    if not isinstance(project_id, str) or not isinstance(project_title, str) or not isinstance(fields, list):
        raise IssueCentricGitHubError(
            "GitHub Project resolve response is missing project id / title / fields."
        )

    target_field = None
    wanted_field = state_field_name.strip()
    wanted_option = state_option_name.strip()
    if not wanted_field:
        raise IssueCentricGitHubError("github_project_state_field_name must not be empty when github_project_url is configured.")
    if not wanted_option:
        raise IssueCentricGitHubError("github_project_default_issue_state must not be empty when github_project_url is configured.")

    for field in fields:
        if not isinstance(field, dict):
            continue
        if field.get("__typename") != "ProjectV2SingleSelectField":
            continue
        name = field.get("name")
        if isinstance(name, str) and name.casefold() == wanted_field.casefold():
            target_field = field
            break
    if target_field is None:
        raise IssueCentricGitHubError(
            f"GitHub Project State field `{wanted_field}` could not be resolved from {project_url}."
        )

    field_id = target_field.get("id")
    actual_field_name = target_field.get("name")
    options = target_field.get("options")
    if not isinstance(field_id, str) or not isinstance(actual_field_name, str) or not isinstance(options, list):
        raise IssueCentricGitHubError(
            "GitHub Project State field response is missing id / name / options."
        )

    target_option = None
    for option in options:
        if not isinstance(option, dict):
            continue
        name = option.get("name")
        if isinstance(name, str) and name.casefold() == wanted_option.casefold():
            target_option = option
            break
    if target_option is None:
        raise IssueCentricGitHubError(
            f"GitHub Project State option `{wanted_option}` could not be resolved from field `{actual_field_name}`."
        )

    option_id = target_option.get("id")
    actual_option_name = target_option.get("name")
    if not isinstance(option_id, str) or not isinstance(actual_option_name, str):
        raise IssueCentricGitHubError(
            "GitHub Project State option response is missing id / name."
        )

    return ResolvedGitHubProjectState(
        project_id=project_id,
        project_url=project_url,
        project_title=project_title,
        owner_login=owner_login,
        owner_kind=owner_kind,
        state_field_id=field_id,
        state_field_name=actual_field_name,
        state_option_id=option_id,
        state_option_name=actual_option_name,
    )


def add_issue_to_github_project(
    project_id: str,
    issue_node_id: str,
    *,
    token: str,
) -> CreatedGitHubProjectItem:
    payload_obj = _github_graphql_request(
        token=token,
        query=(
            "mutation($projectId: ID!, $contentId: ID!) {"
            "  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {"
            "    item { id }"
            "  }"
            "}"
        ),
        variables={"projectId": project_id, "contentId": issue_node_id},
        context="GitHub Project item create",
    )
    mutation = payload_obj.get("addProjectV2ItemById")
    item = mutation.get("item") if isinstance(mutation, dict) else None
    item_id = item.get("id") if isinstance(item, dict) else None
    if not isinstance(item_id, str):
        raise IssueCentricGitHubError(
            "GitHub Project item create response is missing item id."
        )
    return CreatedGitHubProjectItem(item_id=item_id, project_id=project_id)


def set_github_project_item_state(
    *,
    project_id: str,
    item_id: str,
    field_id: str,
    option_id: str,
    token: str,
) -> None:
    payload_obj = _github_graphql_request(
        token=token,
        query=(
            "mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {"
            "  updateProjectV2ItemFieldValue("
            "    input: {"
            "      projectId: $projectId,"
            "      itemId: $itemId,"
            "      fieldId: $fieldId,"
            "      value: {singleSelectOptionId: $optionId}"
            "    }"
            "  ) {"
            "    projectV2Item { id }"
            "  }"
            "}"
        ),
        variables={
            "projectId": project_id,
            "itemId": item_id,
            "fieldId": field_id,
            "optionId": option_id,
        },
        context="GitHub Project State set",
    )
    mutation = payload_obj.get("updateProjectV2ItemFieldValue")
    item = mutation.get("projectV2Item") if isinstance(mutation, dict) else None
    returned_item_id = item.get("id") if isinstance(item, dict) else None
    if not isinstance(returned_item_id, str):
        raise IssueCentricGitHubError(
            "GitHub Project State set response is missing project item id."
        )


def resolve_github_project_item_for_issue(
    *,
    project_id: str,
    issue_node_id: str,
    token: str,
) -> ResolvedGitHubProjectItem:
    after: str | None = None
    while True:
        payload_obj = _github_graphql_request(
            token=token,
            query=(
                "query($projectId: ID!, $after: String) {"
                "  node(id: $projectId) {"
                "    ... on ProjectV2 {"
                "      items(first: 100, after: $after) {"
                "        pageInfo { hasNextPage endCursor }"
                "        nodes {"
                "          id"
                "          content {"
                "            __typename"
                "            ... on Issue {"
                "              id"
                "              number"
                "              repository { nameWithOwner }"
                "            }"
                "          }"
                "        }"
                "      }"
                "    }"
                "  }"
                "}"
            ),
            variables={"projectId": project_id, "after": after},
            context="GitHub Project item resolve",
        )
        node = payload_obj.get("node")
        if not isinstance(node, dict):
            raise IssueCentricGitHubError(
                "GitHub Project item resolve response is missing the project node."
            )
        items = node.get("items")
        if not isinstance(items, dict):
            raise IssueCentricGitHubError(
                "GitHub Project item resolve response is missing project items."
            )
        item_nodes = items.get("nodes")
        page_info = items.get("pageInfo")
        if not isinstance(item_nodes, list) or not isinstance(page_info, dict):
            raise IssueCentricGitHubError(
                "GitHub Project item resolve response is missing item nodes or pageInfo."
            )
        for item in item_nodes:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            content = item.get("content")
            if not isinstance(item_id, str) or not isinstance(content, dict):
                continue
            if content.get("__typename") != "Issue":
                continue
            content_id = content.get("id")
            issue_number = content.get("number")
            repository = (
                (content.get("repository") or {}).get("nameWithOwner")
                if isinstance(content.get("repository"), dict)
                else None
            )
            if (
                isinstance(content_id, str)
                and content_id == issue_node_id
                and isinstance(issue_number, int)
                and isinstance(repository, str)
            ):
                return ResolvedGitHubProjectItem(
                    item_id=item_id,
                    project_id=project_id,
                    issue_node_id=content_id,
                    issue_number=issue_number,
                    repository=repository,
                )
        has_next_page = bool(page_info.get("hasNextPage"))
        end_cursor = page_info.get("endCursor")
        if not has_next_page:
            break
        if end_cursor is not None and not isinstance(end_cursor, str):
            raise IssueCentricGitHubError(
                "GitHub Project item resolve response returned an invalid endCursor."
            )
        after = end_cursor

    raise IssueCentricGitHubError(
        "GitHub Project item for the current issue could not be resolved from the configured Project."
    )


def _github_api_request(
    *,
    method: str,
    url: str,
    token: str,
    payload: dict[str, object] | None,
    context: str,
) -> dict[str, object]:
    encoded = json.dumps(payload).encode("utf-8") if payload is not None else None
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


def _github_graphql_request(
    *,
    token: str,
    query: str,
    variables: dict[str, object],
    context: str,
) -> dict[str, object]:
    payload = {"query": query, "variables": variables}
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps(payload).encode("utf-8"),
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
    errors = payload_obj.get("errors")
    if isinstance(errors, list) and errors:
        messages = []
        for error in errors:
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                messages.append(error["message"])
        raise IssueCentricGitHubError(
            f"{context} failed: {'; '.join(messages) or 'unknown GraphQL error'}"
        )
    data = payload_obj.get("data")
    if not isinstance(data, dict):
        raise IssueCentricGitHubError(f"{context} returned no GraphQL data object.")
    return data


def _parse_issue_snapshot(
    payload_obj: Mapping[str, Any],
    *,
    repository: str,
    context: str,
) -> GitHubIssueSnapshot:
    number = payload_obj.get("number")
    html_url = payload_obj.get("html_url")
    returned_title = payload_obj.get("title")
    returned_state = payload_obj.get("state")
    if (
        not isinstance(number, int)
        or not isinstance(html_url, str)
        or not isinstance(returned_title, str)
        or not isinstance(returned_state, str)
    ):
        raise IssueCentricGitHubError(
            f"{context} response is missing number / html_url / title / state."
        )
    return GitHubIssueSnapshot(
        number=number,
        url=html_url,
        title=returned_title,
        repository=repository,
        state=returned_state,
        node_id=str(payload_obj.get("node_id", "") or ""),
    )


def _parse_project_url(project_url: str) -> tuple[str, str, int]:
    raw = project_url.strip()
    match = re.match(r"^https://github\.com/(users|orgs)/([^/]+)/projects/([0-9]+)$", raw)
    if not match:
        raise IssueCentricGitHubError(
            "github_project_url must look like https://github.com/users/<owner>/projects/<number> or https://github.com/orgs/<owner>/projects/<number>."
        )
    owner_kind = match.group(1)
    owner_login = match.group(2)
    project_number = int(match.group(3))
    return owner_kind, owner_login, project_number


def _project_query_for_owner_kind(owner_kind: str) -> str:
    if owner_kind == "users":
        owner_root = "user"
    elif owner_kind == "orgs":
        owner_root = "organization"
    else:
        raise IssueCentricGitHubError(f"Unsupported project owner kind: {owner_kind}")
    return (
        f"query($login: String!, $number: Int!) {{"
        f"  {owner_root}(login: $login) {{"
        f"    projectV2(number: $number) {{"
        f"      id"
        f"      title"
        f"      fields(first: 50) {{"
        f"        nodes {{"
        f"          __typename"
        f"          ... on ProjectV2SingleSelectField {{"
        f"            id"
        f"            name"
        f"            options {{ id name }}"
        f"          }}"
        f"        }}"
        f"      }}"
        f"    }}"
        f"  }}"
        f"}}"
    )
