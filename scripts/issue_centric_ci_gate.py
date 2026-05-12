#!/usr/bin/env python3
"""CI gate for the issue-centric runtime.

Checks GitHub Actions CI run status before allowing the runtime to proceed
with sending the next ChatGPT request after a post-push Codex report.

CI gate verdicts:
  "waiting_ci"   — CI is queued or in_progress; caller should poll before ChatGPT send
  "success"      — CI completed successfully; proceed to close/next issue
  "failure"      — CI failed or was cancelled; create fix continuation
  "skipped"      — No CI run found; proceed normally
  "indeterminate"— CI check could not be completed; hold for human review

State fields managed (all stored in bridge/state.json):
  ci_gate_status         — current gate verdict
  ci_gate_run_id         — GitHub Actions run ID being watched
  ci_gate_commit_sha     — head commit SHA of the watched run
  ci_gate_checked_at     — ISO-8601 timestamp of the last check
  ci_gate_attempt_count  — number of check attempts since gate was entered
  ci_gate_current_issue  — issue ref that triggered the gate
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

# Maximum number of CI-check attempts before declaring "indeterminate".
# Raised to accommodate in-process synchronous polling (up to 1800s / 15s = 120 polls).
CI_GATE_MAX_ATTEMPT_COUNT = 200

# GitHub Actions run status values that mean "still running".
CI_PENDING_STATUSES: frozenset[str] = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})

# GitHub Actions run conclusion values that mean "success".
CI_SUCCESS_CONCLUSIONS: frozenset[str] = frozenset({"success", "skipped", "neutral"})

# GitHub Actions run conclusion values that mean "failed".
CI_FAILURE_CONCLUSIONS: frozenset[str] = frozenset(
    {"failure", "cancelled", "timed_out", "action_required", "stale"}
)

# Regex: match GitHub Actions run URLs in report text.
# Captures (owner/repo, run_id).
_CI_RUN_URL_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)/actions/runs/([0-9]+)"
)

# Regex: match 40-char hex commit SHAs (git full SHA).
_COMMIT_SHA_RE = re.compile(r"\b([0-9a-fA-F]{40})\b")

GITHUB_API_BASE = "https://api.github.com"

# Timeout for GitHub API requests (seconds).
_API_TIMEOUT = 15


class CIGateError(Exception):
    """Raised when a CI gate operation cannot proceed safely."""


@dataclass(frozen=True)
class CIRunStatus:
    """Snapshot of a GitHub Actions workflow run."""

    run_id: str
    repository: str
    status: str             # queued | in_progress | completed | …
    conclusion: str | None  # success | failure | cancelled | … (None while running)
    html_url: str
    head_sha: str
    created_at: str
    updated_at: str
    name: str               # workflow name

    def is_pending(self) -> bool:
        return self.status in CI_PENDING_STATUSES

    def is_success(self) -> bool:
        return self.status == "completed" and self.conclusion in CI_SUCCESS_CONCLUSIONS

    def is_failure(self) -> bool:
        return self.status == "completed" and self.conclusion in CI_FAILURE_CONCLUSIONS


@dataclass(frozen=True)
class CIGateResult:
    """Result of a single CI gate evaluation."""

    verdict: str              # "waiting_ci" | "success" | "failure" | "skipped" | "indeterminate"
    run_id: str               # resolved run ID (empty when skipped)
    commit_sha: str           # resolved commit SHA (empty when skipped)
    checked_at: str           # ISO-8601 timestamp
    attempt_count: int        # total attempts so far (including this one)
    run_status: CIRunStatus | None  # None when skipped / indeterminate without a run
    note: str
    failure_detail: str = ""  # non-empty only when verdict=="failure"


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------


def extract_ci_run_ids_from_text(text: str) -> list[tuple[str, str]]:
    """Return (repository, run_id) pairs found in *text*.

    Scans for GitHub Actions run URLs.  The last match is considered the
    most recent run when the caller picks one to check.
    """
    return _CI_RUN_URL_RE.findall(text)


def extract_commit_shas_from_text(text: str) -> list[str]:
    """Return 40-char hex SHA strings found in *text*."""
    return _COMMIT_SHA_RE.findall(text)


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------


def _github_api_get(endpoint: str, token: str) -> Any:
    """Perform a GitHub REST API GET request and return parsed JSON.

    *endpoint* should start with '/' or be a full path fragment without the
    base URL, e.g. ``"repos/owner/repo/actions/runs/12345"``.

    Raises:
        CIGateError  — for HTTP errors or malformed responses.
        urllib.error.URLError — for low-level network errors (callers should
                                convert to CIGateError or handle explicitly).
    """
    url = f"{GITHUB_API_BASE}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CIGateError(
            f"GitHub API returned HTTP {exc.code} for {url}: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise CIGateError(f"Network error fetching {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise CIGateError(f"Malformed JSON from {url}") from exc


def fetch_ci_run_by_id(repository: str, run_id: str, token: str) -> CIRunStatus:
    """Fetch a specific GitHub Actions workflow run by *run_id*.

    Raises:
        CIGateError — when the API call fails or the run cannot be parsed.
    """
    data = _github_api_get(f"repos/{repository}/actions/runs/{run_id}", token)
    return _parse_run(data, repository)


def fetch_latest_ci_run(
    repository: str,
    token: str,
    *,
    branch: str = "",
    commit_sha: str = "",
    per_page: int = 5,
) -> CIRunStatus | None:
    """Return the most-recent GitHub Actions workflow run for *repository*.

    Optionally filter by *branch* or *commit_sha*.  Returns ``None`` when no
    runs exist.

    Raises:
        CIGateError — when the API call fails.
    """
    params: dict[str, str] = {"per_page": str(per_page)}
    if branch:
        params["branch"] = branch
    if commit_sha:
        params["head_sha"] = commit_sha
    query = urllib.parse.urlencode(params)
    data = _github_api_get(f"repos/{repository}/actions/runs?{query}", token)
    runs = data.get("workflow_runs", [])
    if not runs:
        return None
    return _parse_run(runs[0], repository)


def _parse_run(data: dict[str, Any], repository: str) -> CIRunStatus:
    return CIRunStatus(
        run_id=str(data["id"]),
        repository=repository,
        status=str(data.get("status", "")),
        conclusion=data.get("conclusion"),  # None while still running
        html_url=str(data.get("html_url", "")),
        head_sha=str(data.get("head_sha", "")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        name=str(data.get("name", "")),
    )


def fetch_ci_run_failed_jobs_summary(
    repository: str,
    run_id: str,
    token: str,
) -> str:
    """Return a brief text summary of failed jobs in a CI run.

    Returns empty string when the API call fails or there are no failed jobs.
    Never raises.
    """
    try:
        data = _github_api_get(
            f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest",
            token,
        )
    except CIGateError:
        return ""

    jobs = data.get("jobs", [])
    failed_jobs = [j for j in jobs if j.get("conclusion") in CI_FAILURE_CONCLUSIONS]
    if not failed_jobs:
        return ""

    parts: list[str] = []
    for job in failed_jobs[:3]:  # cap at 3 for brevity
        job_name = str(job.get("name", "unknown"))
        conclusion = str(job.get("conclusion", ""))
        steps = job.get("steps", [])
        failed_steps = [
            str(s.get("name", ""))
            for s in steps
            if s.get("conclusion") in CI_FAILURE_CONCLUSIONS
        ]
        step_str = ", ".join(failed_steps[:3]) if failed_steps else "unknown step"
        parts.append(
            f"job={job_name!r} conclusion={conclusion} failed_steps=[{step_str}]"
        )
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Core gate evaluation
# ---------------------------------------------------------------------------


def evaluate_ci_gate(
    *,
    report_text: str,
    repository: str,
    token: str,
    prior_state: Mapping[str, Any],
    branch: str = "",
) -> CIGateResult:
    """Evaluate the CI gate and return a verdict.

    Resolution priority (first match wins):
      1. ``ci_gate_run_id`` from *prior_state* — recheck the same run when
         the gate was already entered (``waiting_ci`` cycle continuation).
      2. GitHub Actions run URLs extracted from *report_text*.
      3. Commit SHAs extracted from *report_text* → look up run by ``head_sha``.
      4. Latest run for *repository*/*branch* (fallback).
      5. No run found → ``"skipped"``.

    Attempt count is incremented on every call. When it exceeds
    ``CI_GATE_MAX_ATTEMPT_COUNT`` the verdict is ``"indeterminate"`` so that
    the operator is notified instead of polling forever.
    """
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prior_attempt_count = int(prior_state.get("ci_gate_attempt_count", 0))
    attempt_count = prior_attempt_count + 1

    prior_run_id = str(prior_state.get("ci_gate_run_id", "")).strip()
    prior_commit_sha = str(prior_state.get("ci_gate_commit_sha", "")).strip()

    # Hard limit: bounded polling.
    if attempt_count > CI_GATE_MAX_ATTEMPT_COUNT:
        return CIGateResult(
            verdict="indeterminate",
            run_id=prior_run_id,
            commit_sha=prior_commit_sha,
            checked_at=checked_at,
            attempt_count=attempt_count,
            run_status=None,
            note=(
                f"CI gate exceeded maximum attempt count ({CI_GATE_MAX_ATTEMPT_COUNT}). "
                "Human review required."
            ),
        )

    run_id = ""
    commit_sha = ""
    run_status: CIRunStatus | None = None

    # 1. Recheck a saved run from a prior waiting_ci cycle.
    if prior_run_id:
        run_id = prior_run_id
        commit_sha = prior_commit_sha
        try:
            run_status = fetch_ci_run_by_id(repository, run_id, token)
        except CIGateError as exc:
            return CIGateResult(
                verdict="indeterminate",
                run_id=run_id,
                commit_sha=commit_sha,
                checked_at=checked_at,
                attempt_count=attempt_count,
                run_status=None,
                note=f"Failed to re-fetch CI run {run_id}: {exc}",
            )
    else:
        # 2. Extract run ID from report text.
        run_ids_from_text = extract_ci_run_ids_from_text(report_text)
        if run_ids_from_text:
            # Use the last (most recent) URL found.
            repo_from_text, run_id_from_text = run_ids_from_text[-1]
            eff_repo = repository or repo_from_text
            try:
                run_status = fetch_ci_run_by_id(eff_repo, run_id_from_text, token)
                run_id = run_id_from_text
                commit_sha = run_status.head_sha
                repository = eff_repo
            except CIGateError:
                run_id = ""
                run_status = None

        # 3. Extract commit SHA from report text and look up run.
        if not run_status:
            shas = extract_commit_shas_from_text(report_text)
            if shas:
                commit_sha = shas[-1]  # use last SHA
                try:
                    candidate = fetch_latest_ci_run(
                        repository, token,
                        commit_sha=commit_sha,
                        branch=branch,
                    )
                    if candidate is not None:
                        run_status = candidate
                        run_id = run_status.run_id
                except CIGateError:
                    pass

        # 4. Fall back to latest run for the repository.
        if not run_status:
            try:
                candidate = fetch_latest_ci_run(
                    repository, token, branch=branch
                )
                if candidate is not None:
                    run_status = candidate
                    run_id = run_status.run_id
                    commit_sha = run_status.head_sha
            except CIGateError:
                pass

    # 5. No CI run found → skip the gate.
    if run_status is None:
        return CIGateResult(
            verdict="skipped",
            run_id="",
            commit_sha="",
            checked_at=checked_at,
            attempt_count=attempt_count,
            run_status=None,
            note="No CI run found; skipping CI gate.",
        )

    # Determine verdict.
    if run_status.is_pending():
        return CIGateResult(
            verdict="waiting_ci",
            run_id=run_id,
            commit_sha=commit_sha,
            checked_at=checked_at,
            attempt_count=attempt_count,
            run_status=run_status,
            note=(
                f"CI run {run_id} is {run_status.status!r}. "
                "Waiting for completion before sending next ChatGPT request."
            ),
        )

    if run_status.is_success():
        return CIGateResult(
            verdict="success",
            run_id=run_id,
            commit_sha=commit_sha,
            checked_at=checked_at,
            attempt_count=attempt_count,
            run_status=run_status,
            note=(
                f"CI run {run_id} completed successfully "
                f"(conclusion={run_status.conclusion!r}). Proceeding."
            ),
        )

    if run_status.is_failure():
        failure_detail = fetch_ci_run_failed_jobs_summary(repository, run_id, token)
        return CIGateResult(
            verdict="failure",
            run_id=run_id,
            commit_sha=commit_sha,
            checked_at=checked_at,
            attempt_count=attempt_count,
            run_status=run_status,
            note=(
                f"CI run {run_id} failed "
                f"(conclusion={run_status.conclusion!r}). "
                "CI failure fix continuation will be created."
            ),
            failure_detail=failure_detail,
        )

    # Unknown status / conclusion.
    return CIGateResult(
        verdict="indeterminate",
        run_id=run_id,
        commit_sha=commit_sha,
        checked_at=checked_at,
        attempt_count=attempt_count,
        run_status=run_status,
        note=(
            f"CI run {run_id} has unrecognised "
            f"status={run_status.status!r} conclusion={run_status.conclusion!r}. "
            "Human review required."
        ),
    )


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def apply_ci_gate_state(
    mutable_state: dict[str, Any],
    result: CIGateResult,
    *,
    current_issue: str = "",
) -> None:
    """Write CI gate fields from *result* into *mutable_state* in-place."""
    mutable_state["ci_gate_status"] = result.verdict
    mutable_state["ci_gate_run_id"] = result.run_id
    mutable_state["ci_gate_commit_sha"] = result.commit_sha
    mutable_state["ci_gate_checked_at"] = result.checked_at
    mutable_state["ci_gate_attempt_count"] = result.attempt_count
    if current_issue:
        mutable_state["ci_gate_current_issue"] = current_issue


def clear_ci_gate_state(mutable_state: dict[str, Any]) -> None:
    """Reset all CI gate fields in *mutable_state* to their default values."""
    mutable_state["ci_gate_status"] = ""
    mutable_state["ci_gate_run_id"] = ""
    mutable_state["ci_gate_commit_sha"] = ""
    mutable_state["ci_gate_checked_at"] = ""
    mutable_state["ci_gate_attempt_count"] = 0
    mutable_state["ci_gate_current_issue"] = ""


def is_waiting_ci(state: Mapping[str, Any]) -> bool:
    """Return True when the runtime is in ``waiting_ci`` state."""
    return str(state.get("ci_gate_status", "")).strip() == "waiting_ci"


# ---------------------------------------------------------------------------
# CI failure continuation body builder
# ---------------------------------------------------------------------------


def build_ci_failure_continuation_body(
    *,
    issue_ref: str,
    run_id: str,
    run_url: str,
    conclusion: str,
    failure_detail: str,
    repository: str,
    workflow_name: str = "",
) -> str:
    """Build a Codex continuation body focused on fixing CI failures.

    The returned string is a Markdown prompt suitable for use as a Codex
    prompt body in a CI-failure fix continuation.
    """
    lines = [
        f"# CI Failure Fix — {issue_ref}",
        "",
        "## Context",
        "",
        (
            f"The CI run for **{issue_ref}** failed and must be fixed "
            "before this issue can be closed."
        ),
        "",
        "## CI Run",
        "",
        f"- **Repository**: `{repository}`",
        f"- **Run ID**: `{run_id}`",
    ]
    if run_url:
        lines.append(f"- **URL**: {run_url}")
    if workflow_name:
        lines.append(f"- **Workflow**: `{workflow_name}`")
    lines += [
        f"- **Conclusion**: `{conclusion}`",
        "",
    ]
    if failure_detail:
        lines += [
            "## Failure Summary",
            "",
            failure_detail,
            "",
        ]
    lines += [
        "## Task",
        "",
        (
            "Investigate and fix the CI failures listed above. "
            "Focus on the failing jobs and steps. "
            "Fix only the CI failure caused by this issue, and do not broaden unrelated code. "
            "Rerun focused checks locally when possible. "
            "**Do NOT close this issue until CI passes.**"
        ),
        "",
        "When the fix is pushed, confirm that CI passes before reporting completion.",
    ]
    return "\n".join(lines)
