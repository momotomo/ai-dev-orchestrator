from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import launch_codex_once
from _bridge_common import (
    BridgeError,
    clear_error_fields,
    codex_report_is_ready,
    load_state,
    repo_relative,
    runtime_prompt_path,
    runtime_report_path,
    save_state,
    write_text,
)
from copilot_stability_preamble import COPILOT_STABILITY_PREAMBLE
from issue_centric_codex_run import CodexRunExecutionResult
from issue_centric_contract import IssueCentricAction
from issue_centric_transport import PreparedIssueCentricDecision


class IssueCentricCodexLaunchError(ValueError):
    """Raised when issue-centric codex launch cannot be prepared safely."""


def _parse_codex_report_result(report_text: str) -> str:
    match = re.search(r"^\s*-\s+result:\s+(\S+)", report_text, re.MULTILINE)
    if match:
        value = match.group(1).rstrip(".,;:").lower()
        if value in ("completed", "consultation_needed", "blocked", "failed"):
            return value
    return "completed"


@dataclass(frozen=True)
class IssueCentricCodexLaunchResult:
    status: str
    launch_status: str
    launch_entrypoint: str
    prompt_path: Path | None
    prompt_log_path: Path | None
    launch_log_path: Path
    continuation_status: str
    continuation_log_path: Path
    report_status: str
    report_file: str
    final_mode: str
    safe_stop_reason: str


def launch_issue_centric_codex_run(
    prepared: PreparedIssueCentricDecision,
    execution: CodexRunExecutionResult,
    *,
    state: Mapping[str, Any],
    project_config: Mapping[str, Any],
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    launch_runner: Callable[[dict[str, object], list[str] | None], int] | None = None,
    runtime_prompt_path_fn: Callable[[Mapping[str, Any] | None], Path] = runtime_prompt_path,
    runtime_report_path_fn: Callable[[], Path] = runtime_report_path,
    write_text_fn: Callable[[Path, str], None] = write_text,
    save_state_fn: Callable[[dict[str, object]], None] = save_state,
    load_state_fn: Callable[[], dict[str, object]] = load_state,
    now_fn: Callable[[], datetime] | None = None,
) -> IssueCentricCodexLaunchResult:
    if prepared.decision.action is not IssueCentricAction.CODEX_RUN:
        raise IssueCentricCodexLaunchError("issue-centric Codex launch only supports action=codex_run.")
    if execution.status != "completed":
        raise IssueCentricCodexLaunchError("issue-centric Codex launch requires a completed codex_run execution result.")
    if execution.payload is None or execution.resolved_issue is None or execution.created_comment is None:
        raise IssueCentricCodexLaunchError(
            "issue-centric Codex launch requires a resolved issue, trigger comment, and assembled payload."
        )

    now = (now_fn or _utcnow)()
    launch_entrypoint = "launch_codex_once.run"
    prompt_path = runtime_prompt_path_fn(project_config)
    prompt_text = build_issue_centric_codex_prompt(prepared, execution)
    write_text_fn(prompt_path, prompt_text)
    prompt_log_path = log_writer(
        "prepared_issue_centric_codex_prompt",
        prompt_text,
        "md",
    )

    ready_state = clear_error_fields(dict(state))
    ready_state.update(
        {
            "mode": "ready_for_codex",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": True,
            "last_prompt_file": repo_relative(prompt_path),
            "last_issue_centric_launch_status": "launching",
            "last_issue_centric_launch_entrypoint": launch_entrypoint,
            "last_issue_centric_launch_prompt_log": repo_relative(prompt_log_path),
        }
    )
    save_state_fn(ready_state)

    launch_callable = launch_runner or launch_codex_once.run
    try:
        launch_callable(dict(ready_state), [])
        final_state = clear_error_fields(dict(load_state_fn()))
        final_mode = str(final_state.get("mode", "")).strip()
        launch_status = "launched"
        continuation_status, report_status, report_file, safe_stop_reason, result_status = assess_issue_centric_continuation(
            final_state,
            report_path=runtime_report_path_fn(),
        )
    except Exception as exc:
        final_state = dict(load_state_fn())
        final_mode = str(final_state.get("mode", "")).strip()
        launch_status = "failed_after_trigger_comment"
        continuation_status = "launch_failed_after_trigger_comment"
        report_status = "not_ready"
        report_file = ""
        safe_stop_reason = (
            "Trigger comment registration succeeded, but the issue-centric Codex launch failed after prompt "
            f"materialization. {exc}"
        )
        result_status = "blocked"
        launch_log_path = _write_launch_log(
            log_writer=log_writer,
            repo_relative=repo_relative,
            now=now,
            prepared=prepared,
            execution=execution,
            prompt_path=prompt_path,
            prompt_log_path=prompt_log_path,
            launch_entrypoint=launch_entrypoint,
            result_status=result_status,
            launch_status=launch_status,
            final_mode=final_mode,
            safe_stop_reason=safe_stop_reason,
        )
        continuation_log_path = _write_continuation_log(
            log_writer=log_writer,
            repo_relative=repo_relative,
            now=now,
            execution=execution,
            launch_log_path=launch_log_path,
            continuation_status=continuation_status,
            report_status=report_status,
            report_file=report_file,
            final_mode=final_mode,
            safe_stop_reason=safe_stop_reason,
        )
        final_state.update(
            {
                "last_issue_centric_launch_status": launch_status,
                "last_issue_centric_launch_entrypoint": launch_entrypoint,
                "last_issue_centric_launch_prompt_log": repo_relative(prompt_log_path),
                "last_issue_centric_launch_log": repo_relative(launch_log_path),
                "last_issue_centric_continuation_status": continuation_status,
                "last_issue_centric_continuation_log": repo_relative(continuation_log_path),
                "last_issue_centric_report_status": report_status,
                "last_issue_centric_report_file": report_file,
                "last_issue_centric_stop_reason": safe_stop_reason,
                "chatgpt_decision_note": safe_stop_reason,
            }
        )
        save_state_fn(final_state)
        raise BridgeError(safe_stop_reason) from exc

    launch_log_path = _write_launch_log(
        log_writer=log_writer,
        repo_relative=repo_relative,
        now=now,
        prepared=prepared,
        execution=execution,
        prompt_path=prompt_path,
        prompt_log_path=prompt_log_path,
        launch_entrypoint=launch_entrypoint,
        result_status=result_status,
        launch_status=launch_status,
        final_mode=final_mode,
        safe_stop_reason=safe_stop_reason,
    )
    continuation_log_path = _write_continuation_log(
        log_writer=log_writer,
        repo_relative=repo_relative,
        now=now,
        execution=execution,
        launch_log_path=launch_log_path,
        continuation_status=continuation_status,
        report_status=report_status,
        report_file=report_file,
        final_mode=final_mode,
        safe_stop_reason=safe_stop_reason,
    )
    final_state.update(
        {
            "last_issue_centric_launch_status": launch_status,
            "last_issue_centric_launch_entrypoint": launch_entrypoint,
            "last_issue_centric_launch_prompt_log": repo_relative(prompt_log_path),
            "last_issue_centric_launch_log": repo_relative(launch_log_path),
            "last_issue_centric_continuation_status": continuation_status,
            "last_issue_centric_continuation_log": repo_relative(continuation_log_path),
            "last_issue_centric_report_status": report_status,
            "last_issue_centric_report_file": report_file,
            "last_issue_centric_stop_reason": safe_stop_reason,
            "chatgpt_decision_note": safe_stop_reason,
        }
    )
    save_state_fn(final_state)
    return IssueCentricCodexLaunchResult(
        status=result_status,
        launch_status=launch_status,
        launch_entrypoint=launch_entrypoint,
        prompt_path=prompt_path,
        prompt_log_path=prompt_log_path,
        launch_log_path=launch_log_path,
        continuation_status=continuation_status,
        continuation_log_path=continuation_log_path,
        report_status=report_status,
        report_file=report_file,
        final_mode=final_mode,
        safe_stop_reason=safe_stop_reason,
    )


def build_issue_centric_codex_prompt(
    prepared: PreparedIssueCentricDecision,
    execution: CodexRunExecutionResult,
) -> str:
    if execution.payload is None or execution.resolved_issue is None or execution.created_comment is None:
        raise IssueCentricCodexLaunchError(
            "issue-centric Codex launch prompt requires an assembled payload and trigger comment."
        )
    request_text = execution.payload.request.strip()
    if not request_text:
        raise IssueCentricCodexLaunchError("issue-centric Codex launch prompt requires a non-empty request.")

    docs = [
        Path(execution.payload.repo).resolve() / "docs" / "ISSUE_CENTRIC_RUNTIME_CONTRACT.md",
        Path(execution.payload.repo).resolve() / "docs" / "RUNTIME_TOUCHPOINT_INVENTORY.md",
    ]
    lines = [
        "# Issue-Centric Codex Prompt",
        "",
        "normal path です。free-form override ではなく、target issue を今回の実行単位として扱ってください。",
        "",
        "## Execution Context",
        "",
        f"- repo: {execution.payload.repo}",
        f"- target issue: {execution.resolved_issue.issue_url}",
        f"- trigger comment: {execution.created_comment.url}",
        f"- summary: {prepared.decision.summary}",
        f"- close_current_issue judgement: {str(prepared.decision.close_current_issue).lower()}",
        f"- create_followup_issue judgement: {str(prepared.decision.create_followup_issue).lower()}",
        "",
        "## Required Steps",
        "",
        "- 対象 Issue を確認する",
        "- 必要なら対象 Issue の既存コメントを確認する",
        "- 今回登録した trigger comment を確認する",
        "- AGENTS.md があれば確認する",
        "- repo docs を確認する",
        "- Git 運用ルールを守る",
        "- target issue の範囲を越えず、今回の 1 フェーズだけ実装する",
        "",
        "## Request",
        "",
        request_text,
        "",
        "## Report Handoff",
        "",
        "- 既存の Codex report template を使う",
        f"- BRIDGE_SUMMARY か本文のどちらかで target issue `{execution.resolved_issue.issue_url}` を明示する",
        f"- BRIDGE_SUMMARY か本文のどちらかで trigger comment `{execution.created_comment.url}` を明示する",
        "- BRIDGE_SUMMARY の result: フィールドを必ず記入する（completed / consultation_needed / blocked / failed のいずれか）",
        "- result: completed は全タスク実施済み + target issue に完了コメントを投稿した場合のみ使う",
        "- close / follow-up / review automation の判断は report の残課題へ留める",
        "",
        "## 追加確認 docs",
        "",
    ]
    for path in docs:
        lines.append(f"- {path}")
    body = "\n".join(lines).rstrip()
    return COPILOT_STABILITY_PREAMBLE + "\n\n" + body + "\n"


def _write_launch_log(
    *,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    now: datetime,
    prepared: PreparedIssueCentricDecision,
    execution: CodexRunExecutionResult,
    prompt_path: Path,
    prompt_log_path: Path,
    launch_entrypoint: str,
    result_status: str,
    launch_status: str,
    final_mode: str,
    safe_stop_reason: str,
) -> Path:
    payload = execution.payload
    return log_writer(
        f"issue_centric_codex_launch_{result_status}",
        json.dumps(
            {
                "action": "codex_run",
                "status": result_status,
                "executed_at": now.isoformat(),
                "launch_entrypoint": launch_entrypoint,
                "launch_status": launch_status,
                "final_bridge_mode": final_mode,
                "decision_summary": prepared.decision.summary,
                "target_issue": payload.target_issue if payload is not None else "",
                "trigger_comment": payload.trigger_comment if payload is not None else "",
                "request_chars": len(payload.request) if payload is not None else 0,
                "source_execution_log": repo_relative(execution.execution_log_path),
                "source_payload_log": (
                    repo_relative(execution.payload_log_path)
                    if execution.payload_log_path is not None
                    else ""
                ),
                "prompt_path": repo_relative(prompt_path),
                "prompt_log": repo_relative(prompt_log_path),
                "safe_stop_reason": safe_stop_reason,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "json",
    )


def _write_continuation_log(
    *,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    now: datetime,
    execution: CodexRunExecutionResult,
    launch_log_path: Path,
    continuation_status: str,
    report_status: str,
    report_file: str,
    final_mode: str,
    safe_stop_reason: str,
) -> Path:
    payload = execution.payload
    return log_writer(
        "issue_centric_codex_continuation",
        json.dumps(
            {
                "action": "codex_run",
                "executed_at": now.isoformat(),
                "continuation_status": continuation_status,
                "report_status": report_status,
                "report_file": report_file,
                "final_bridge_mode": final_mode,
                "target_issue": payload.target_issue if payload is not None else "",
                "trigger_comment": payload.trigger_comment if payload is not None else "",
                "launch_log": repo_relative(launch_log_path),
                "safe_stop_reason": safe_stop_reason,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "json",
    )


def assess_issue_centric_continuation(
    final_state: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[str, str, str, str, str]:
    final_mode = str(final_state.get("mode", "")).strip()
    report_ready = codex_report_is_ready(report_path)
    report_file = repo_relative(report_path) if report_ready else ""

    if report_ready and final_mode == "codex_done":
        report_text = report_path.read_text(encoding="utf-8")
        result_from_report = _parse_codex_report_result(report_text)
        if result_from_report == "consultation_needed":
            safe_stop_reason = "Issue-centric Codex launch produced a report, but Codex reported consultation_needed. ChatGPT review required before proceeding."
        elif result_from_report in ("blocked", "failed"):
            safe_stop_reason = f"Issue-centric Codex launch produced a report, but Codex reported {result_from_report}. Manual intervention may be needed."
        else:
            safe_stop_reason = "Issue-centric Codex launch completed and the existing codex_done flow produced a report ready for archive."
        return (
            "report_ready_for_archive",
            "ready_for_archive",
            report_file,
            safe_stop_reason,
            result_from_report,
        )
    if report_ready:
        report_text = report_path.read_text(encoding="utf-8")
        result_from_report = _parse_codex_report_result(report_text)
        if result_from_report == "consultation_needed":
            safe_stop_reason = "Issue-centric Codex launch produced a report (recovery path), but Codex reported consultation_needed. ChatGPT review required before proceeding."
        elif result_from_report in ("blocked", "failed"):
            safe_stop_reason = f"Issue-centric Codex launch produced a report (recovery path), but Codex reported {result_from_report}. Manual intervention may be needed."
        else:
            safe_stop_reason = "Issue-centric Codex launch completed and a report is ready, but the final mode was not codex_done. The existing report recovery / archive path can take over next."
        return (
            "report_ready_for_recovery",
            "ready_for_recovery",
            report_file,
            safe_stop_reason,
            result_from_report,
        )
    if final_mode == "codex_running" and bool(final_state.get("need_codex_run")):
        return (
            "delegated_to_existing_codex_wait",
            "waiting_for_report",
            "",
            "Issue-centric Codex launch was handed off to the existing codex_running wait / poll path.",
            "completed",
        )
    return (
        "handoff_incomplete",
        "not_ready",
        "",
        "Issue-centric Codex launch returned, but the bridge did not reach codex_running or codex_done and no report was ready. This slice stops before continuation can proceed.",
        "blocked",
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
