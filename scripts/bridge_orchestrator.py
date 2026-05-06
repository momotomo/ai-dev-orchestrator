#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import archive_codex_report
import fetch_next_prompt
import launch_codex_once
import launch_github_copilot
import request_next_prompt
import request_prompt_from_report
from _bridge_common import ROOT_DIR, BridgeError, BridgeStop, browser_fetch_timeout_seconds, clear_error_fields, codex_report_is_ready, detect_ic_stop_path, format_operator_stop_note, guarded_main, has_pending_issue_centric_codex_dispatch, is_blocked_codex_lifecycle_state, load_browser_config, load_project_config, load_state, prepared_request_action, present_bridge_status, print_project_config_warnings, project_repo_path, read_text, recover_pending_handoff_state, recover_prepared_request_state, recover_report_ready_state, resolve_execution_agent, resolve_runtime_dispatch_plan, resolve_unified_next_action, runtime_prompt_path, save_state, should_prioritize_unarchived_report, should_rotate_before_next_chat_request, worker_repo_path
from issue_centric_ci_gate import (
    CIGateResult,
    apply_ci_gate_state,
    clear_ci_gate_state,
    evaluate_ci_gate,
    is_waiting_ci,
)
from issue_centric_close_current_issue import execute_close_current_issue
from issue_centric_parent_update import execute_parent_issue_update_after_close
from issue_centric_codex_launch import launch_issue_centric_codex_run
from issue_centric_codex_run import execute_codex_run_action
from issue_centric_contract import IssueCentricAction, IssueCentricDecision, maybe_parse_issue_centric_reply
from issue_centric_current_issue_project_state import execute_current_issue_project_state_sync
from issue_centric_execution import dispatch_issue_centric_execution
from issue_centric_followup_issue import execute_followup_issue_action
from issue_centric_github import IssueCentricGitHubError, fetch_github_issue, resolve_github_token, resolve_target_issue
from issue_centric_human_review import execute_human_review_action
from issue_centric_issue_create import execute_issue_create_action
from issue_centric_transport import MaterializedIssueCentricDecision, decode_issue_centric_decision


@dataclass(frozen=True)
class ReadyIssueAutoContinueValidation:
    ok: bool
    reason: str = ""


def parse_args(argv: list[str] | None = None, project_config: dict[str, object] | None = None) -> argparse.Namespace:
    project_config = project_config or load_project_config()
    browser_config = load_browser_config()
    parser = argparse.ArgumentParser(description="bridge/state.json を見て次の 1 手だけ進めます。")
    parser.add_argument(
        "--execution-agent",
        default=str(project_config.get("execution_agent", "codex")),
        help="実行エージェント。有効値: codex / github_copilot (default: project_config.json の execution_agent)",
    )
    parser.add_argument(
        "--github-copilot-bin",
        default=str(project_config.get("github_copilot_bin", "gh")),
        help="launch_github_copilot.py に渡す GitHub Copilot CLI コマンド (default: gh)",
    )
    parser.add_argument(
        "--agent-model",
        default=str(project_config.get("agent_model", "")),
        help="active execution agent に渡す model 名 (execution_agent に依らず共通。未設定なら provider default)",
    )
    parser.add_argument(
        "--codex-bin",
        default=str(project_config.get("codex_bin", "codex")),
        help="launch_codex_once.py に渡す Codex CLI コマンド",
    )
    parser.add_argument(
        "--codex-model",
        default=str(project_config.get("codex_model", "")),
        help="launch_codex_once.py に渡す model 名 (--agent-model が空のときの fallback)",
    )
    parser.add_argument(
        "--codex-timeout-seconds",
        type=int,
        default=int(project_config.get("codex_timeout_seconds", 7200)),
        help="Codex 実行の最大秒数",
    )
    parser.add_argument(
        "--worker-repo-path",
        "--repo-path",
        "--project-path",
        dest="worker_repo_path",
        default=str(worker_repo_path(project_config)),
        help="launch_codex_once.py に渡す worker 対象 repo root",
    )
    parser.add_argument("--dry-run-codex", action="store_true", help="ready_for_codex でも Codex を起動せず内容だけ確認する")
    parser.add_argument(
        "--fetch-timeout-seconds",
        type=int,
        default=int(browser_fetch_timeout_seconds(browser_config)),
        help="waiting_prompt_reply 時に fetch_next_prompt.py へ渡す最大待機秒数。0 の場合は browser_config.json を使う",
    )
    parser.add_argument("--next-todo", default="", help="request 系 script に渡す next_todo")
    parser.add_argument("--open-questions", default="", help="request 系 script に渡す open_questions")
    parser.add_argument("--current-status", default="", help="request 系 script に渡す CURRENT_STATUS 上書き")
    parser.add_argument("--ready-issue-ref", default="", help="request_next_prompt.py に渡す current ready issue 参照")
    parser.add_argument("--request-body", default="", help="request_next_prompt.py に渡す override 用の初回本文")
    parser.add_argument("--select-issue", action="store_true", default=False, help="初回 issue 選定モード: request_next_prompt.py に転送する")
    return parser.parse_args(argv)


def build_codex_launch_argv(args: argparse.Namespace) -> list[str]:
    launch_argv = [
        "--codex-bin",
        args.codex_bin,
        "--timeout-seconds",
        str(args.codex_timeout_seconds),
    ]
    if args.worker_repo_path:
        launch_argv.extend(["--worker-repo-path", args.worker_repo_path])
    # agent_model (common active-provider field) takes priority over codex_model (legacy).
    effective_model = str(getattr(args, "agent_model", "")).strip() or str(getattr(args, "codex_model", "")).strip()
    if effective_model:
        launch_argv.extend(["--model", effective_model])
    if args.dry_run_codex:
        launch_argv.append("--dry-run")
    return launch_argv


def build_github_copilot_launch_argv(args: argparse.Namespace) -> list[str]:
    launch_argv = [
        "--github-copilot-bin",
        args.github_copilot_bin,
        "--timeout-seconds",
        str(args.codex_timeout_seconds),
    ]
    if args.worker_repo_path:
        launch_argv.extend(["--worker-repo-path", args.worker_repo_path])
    # Forward agent_model to launch_github_copilot.py for use in custom wrapper scripts.
    agent_model = str(getattr(args, "agent_model", "")).strip()
    if agent_model:
        launch_argv.extend(["--model", agent_model])
    if args.dry_run_codex:
        launch_argv.append("--dry-run")
    return launch_argv


def build_initial_request_argv(args: argparse.Namespace) -> list[str]:
    request_argv: list[str] = []
    if args.worker_repo_path:
        request_argv.extend(["--project-path", args.worker_repo_path])
    if args.ready_issue_ref:
        request_argv.extend(["--ready-issue-ref", args.ready_issue_ref])
    if args.request_body:
        request_argv.extend(["--request-body", args.request_body])
    if getattr(args, "select_issue", False):
        request_argv.append("--select-issue")
    return request_argv


def build_report_request_argv(args: argparse.Namespace) -> list[str]:
    request_argv: list[str] = []
    if args.next_todo:
        request_argv.extend(["--next-todo", args.next_todo])
    if args.open_questions:
        request_argv.extend(["--open-questions", args.open_questions])
    if args.current_status:
        request_argv.extend(["--current-status", args.current_status])
    return request_argv


def build_fetch_argv(args: argparse.Namespace) -> list[str]:
    fetch_argv: list[str] = []
    if args.fetch_timeout_seconds > 0:
        fetch_argv.extend(["--timeout-seconds", str(args.fetch_timeout_seconds)])
    return fetch_argv


def resolve_saved_runtime_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (ROOT_DIR / raw_path).resolve()
    else:
        path = path.resolve()
    return path


def _reconstruct_issue_centric_codex_decision_from_metadata(
    metadata: dict[str, object],
    state: dict[str, object],
) -> IssueCentricDecision:
    """Reconstruct a codex_run IssueCentricDecision from saved metadata + artifact file.

    Used when raw response log is unavailable or cannot be re-parsed (e.g. multi-turn
    page dump parse ambiguity, or file missing after max-execution-count stop + resume).
    """
    action_raw = str(metadata.get("action", "")).strip()
    if action_raw != "codex_run":
        raise BridgeError(
            f"pending codex dispatch を metadata から再構成しましたが、action が codex_run ではありません: {action_raw!r}"
        )
    target_issue_raw = str(metadata.get("target_issue", "")).strip()
    if not target_issue_raw or target_issue_raw.lower() == "none":
        raise BridgeError("pending codex dispatch の metadata に有効な target_issue がありません。")
    close_current_issue = bool(metadata.get("close_current_issue", False))
    create_followup_issue = bool(metadata.get("create_followup_issue", False))
    summary = str(metadata.get("summary", "")).strip()
    artifact_ref = (
        str(state.get("last_issue_centric_artifact_file", "")).strip()
        or str((metadata.get("prepared_artifact") or {}).get("path", "")).strip()
    )
    if not artifact_ref:
        raise BridgeError(
            "pending codex dispatch の再構成に必要な artifact パスが state・metadata のいずれにもありません。"
        )
    artifact_path = resolve_saved_runtime_path(artifact_ref)
    artifact_text = read_text(artifact_path)
    if not artifact_text.strip():
        raise BridgeError(
            f"pending codex dispatch の artifact ファイルを読めませんでした: {artifact_ref}"
        )
    codex_body_base64 = base64.b64encode(artifact_text.encode("utf-8")).decode("ascii")
    return IssueCentricDecision(
        action=IssueCentricAction.CODEX_RUN,
        target_issue=target_issue_raw,
        close_current_issue=close_current_issue,
        create_followup_issue=create_followup_issue,
        summary=summary,
        issue_body_base64=None,
        codex_body_base64=codex_body_base64,
        review_base64=None,
        followup_issue_body_base64=None,
        raw_json="",
        raw_segment="(reconstructed from saved artifact)",
    )


def load_pending_issue_centric_codex_materialized(
    state: dict[str, object],
) -> tuple[object, MaterializedIssueCentricDecision, str, str, str]:
    metadata_ref = str(state.get("last_issue_centric_metadata_log", "")).strip()
    if not metadata_ref:
        raise BridgeError("issue-centric codex dispatch に必要な metadata log がありません。")
    metadata_path = resolve_saved_runtime_path(metadata_ref)
    try:
        metadata = json.loads(read_text(metadata_path).strip())
    except json.JSONDecodeError as exc:
        raise BridgeError(f"issue-centric metadata log を読めませんでした: {metadata_ref}") from exc

    # Primary path: try to re-parse from raw response log.
    raw_log_ref = str(metadata.get("raw_response_log", "")).strip()
    contract_decision = None
    if raw_log_ref:
        try:
            raw_log_path = resolve_saved_runtime_path(raw_log_ref)
            raw_text = read_text(raw_log_path).strip()
            if raw_text:
                parsed = maybe_parse_issue_centric_reply(raw_text)
                if parsed is not None and parsed.action.value == "codex_run":
                    contract_decision = parsed
        except Exception:
            pass

    # Fallback: reconstruct from metadata + artifact file when raw log is unavailable
    # or cannot be re-parsed (e.g. after max-execution-count stop + resume).
    if contract_decision is None:
        contract_decision = _reconstruct_issue_centric_codex_decision_from_metadata(metadata, state)

    prepared = decode_issue_centric_decision(contract_decision)
    artifact_ref = (
        str(state.get("last_issue_centric_artifact_file", "")).strip()
        or str(metadata.get("prepared_artifact", {}).get("path", "")).strip()
    )
    artifact_path = resolve_saved_runtime_path(artifact_ref) if artifact_ref else None
    return (
        contract_decision,
        MaterializedIssueCentricDecision(
            prepared=prepared,
            metadata_log_path=metadata_path,
            artifact_log_path=artifact_path,
            metadata=metadata,
        ),
        raw_log_ref,
        metadata_ref,
        artifact_ref,
    )


def dispatch_pending_issue_centric_codex_run(
    state: dict[str, object],
    *,
    project_config: dict[str, object],
    execution_agent: str = "codex",
) -> int:
    contract_decision, materialized, raw_log_ref, metadata_ref, artifact_ref = load_pending_issue_centric_codex_materialized(state)
    launch_runner = (
        launch_github_copilot.run
        if execution_agent == "github_copilot"
        else launch_codex_once.run
    )
    dispatch_result = dispatch_issue_centric_execution(
        contract_decision=contract_decision,
        materialized=materialized,
        prior_state=state,
        mutable_state=clear_error_fields(dict(state)),
        project_config=project_config,
        repo_path=project_repo_path(project_config),
        source_raw_log=raw_log_ref,
        source_decision_log=str(state.get("last_issue_centric_decision_log", "")).strip(),
        source_metadata_log=metadata_ref,
        source_artifact_path=artifact_ref,
        log_writer=fetch_next_prompt.log_text,
        repo_relative=fetch_next_prompt.repo_relative,
        load_state_fn=load_state,
        save_state_fn=save_state,
        execute_issue_create_action_fn=execute_issue_create_action,
        execute_codex_run_action_fn=execute_codex_run_action,
        launch_issue_centric_codex_run_fn=launch_issue_centric_codex_run,
        execute_human_review_action_fn=execute_human_review_action,
        execute_close_current_issue_fn=execute_close_current_issue,
        execute_parent_issue_update_fn=execute_parent_issue_update_after_close,
        execute_followup_issue_action_fn=execute_followup_issue_action,
        execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync,
        launch_runner=launch_runner,
    )
    save_state(dispatch_result.final_state)
    print(dispatch_result.stop_message)
    return 0


def _resolve_post_fetch_initial_selection_ref(post_fetch_state: dict[str, object]) -> str:
    """After fetch_next_prompt raises BridgeStop, return the ready issue ref for auto-continue.

    Called when bridge_orchestrator catches a BridgeStop from fetch_next_prompt.run().
    Returns the selected ready issue ref when the stop was an initial_selection_stop and
    auto-continuation is appropriate; otherwise returns empty string.

    Two sources are checked in order:

    1. ``selected_ready_issue_ref`` — written by ``_apply_ic_fetch_stop_state()`` for
       ``initial_selection_stop`` paths.  This is the primary and normal path.
    2. ``last_issue_centric_target_issue`` fallback — used when ``selected_ready_issue_ref``
       is absent despite an initial-selection context.  Requires ``chatgpt_decision_note``
       to contain '選定' (the selection confirmation wording) as a safe discriminator,
       since ``pending_request_source`` is cleared by ``clear_pending_request_fields``
       before the state is saved in ``fetch_next_prompt.run()``.

    Returns empty string for all other BridgeStop paths (codex_run_stop, human_review,
    error, pause, STOP-file) so the caller can safely re-raise.
    """
    chatgpt_decision = str(post_fetch_state.get("chatgpt_decision", "")).strip()
    if not chatgpt_decision.startswith("issue_centric:"):
        return ""
    # Primary: selected_ready_issue_ref written by _apply_ic_fetch_stop_state.
    sel = str(post_fetch_state.get("selected_ready_issue_ref", "")).strip()
    if sel:
        return sel
    # Fallback: selected_ready_issue_ref absent (edge case).  Use target_issue when
    # chatgpt_decision_note confirms this was an initial-selection result.
    decision_note = str(post_fetch_state.get("chatgpt_decision_note", "")).strip()
    target = str(post_fetch_state.get("last_issue_centric_target_issue", "")).strip()
    if target and target != "none" and "選定" in decision_note:
        return target
    return ""


def _extract_ready_issue_ref_token(raw_ref: str) -> str:
    normalized = request_next_prompt.normalize_ready_issue_ref(raw_ref)
    if not normalized:
        return ""
    return normalized.split(maxsplit=1)[0].strip()


def _selected_issue_matches_recently_closed(
    *,
    repository: str,
    issue_number: int,
    state: dict[str, object],
) -> bool:
    closed_number = str(state.get("last_issue_centric_closed_issue_number", "")).strip()
    if not closed_number:
        return False
    try:
        if int(closed_number) != issue_number:
            return False
    except ValueError:
        return False

    closed_url = str(state.get("last_issue_centric_closed_issue_url", "")).strip()
    if not closed_url:
        return True
    return closed_url == f"https://github.com/{repository}/issues/{issue_number}"


def validate_selected_ready_issue_for_auto_continue(
    selected_ready_issue_ref: str,
    state: dict[str, object],
    *,
    prior_state: dict[str, object] | None = None,
) -> ReadyIssueAutoContinueValidation:
    """Validate a selected ready issue immediately before auto-continuing.

    This is intentionally narrow: it protects the initial-selection →
    ``request_next_prompt --ready-issue-ref`` handoff from stale / closed issue
    selections without changing pending reply retry or correction retry paths.
    """
    selected_token = _extract_ready_issue_ref_token(selected_ready_issue_ref)
    if not selected_token:
        return ReadyIssueAutoContinueValidation(False, "selected_ready_issue_ref から issue ref を抽出できませんでした。")

    project_config = load_project_config()
    default_repository = str(project_config.get("github_repository", "")).strip()
    try:
        resolved = resolve_target_issue(selected_token, default_repository=default_repository)
    except IssueCentricGitHubError as exc:
        return ReadyIssueAutoContinueValidation(False, f"selected ready issue ref を解決できませんでした: {exc}")

    if resolved.issue_number <= 0:
        return ReadyIssueAutoContinueValidation(False, f"selected ready issue ref が無効です: {selected_token}")

    state_candidates = [state]
    if prior_state is not None:
        state_candidates.append(prior_state)
    for candidate_state in state_candidates:
        if _selected_issue_matches_recently_closed(
            repository=resolved.repository,
            issue_number=resolved.issue_number,
            state=candidate_state,
        ):
            return ReadyIssueAutoContinueValidation(
                False,
                f"selected ready issue {selected_token} は current run で直前に close 済みです。",
            )

    try:
        token, _ = resolve_github_token()
        issue = fetch_github_issue(resolved.repository, resolved.issue_number, token)
    except IssueCentricGitHubError as exc:
        return ReadyIssueAutoContinueValidation(False, f"selected ready issue の live validation に失敗しました: {exc}")

    if issue.state.lower() != "open":
        return ReadyIssueAutoContinueValidation(
            False,
            f"selected ready issue {issue.url} は {issue.state} です。open issue だけ auto-continue します。",
        )

    return ReadyIssueAutoContinueValidation(True)


def _print_selected_ready_issue_validation_stop(selected_ref: str, validation: ReadyIssueAutoContinueValidation) -> None:
    print(
        "initial selection の selected ready issue を live validation で止めました。"
        f" selected={selected_ref}"
        f" reason={validation.reason}"
        " stale / closed issue への ready issue request は送信しません。"
    )


# close_status values that mean the GitHub issue close mutation completed
# successfully (set by issue_centric_close_current_issue.execute_close_current_issue):
#   "closed"        — issue was open and was just closed
#   "already_closed" — issue was already closed before the mutation ran
# ("completed" is NOT written by that function; it was the old expected value
# that caused auto-continuation to silently never fire.)
_IC_CLOSE_COMPLETE_STATUSES: frozenset[str] = frozenset({"closed", "already_closed"})


def _is_ic_close_completed_for_auto_continuation(state: dict[str, object]) -> bool:
    """Return True when the last IC execution closed the current issue successfully.

    Used by bridge_orchestrator.run() to detect when auto-continuation to the
    next ready issue selection is appropriate.  Both conditions must hold:

      1. chatgpt_decision starts with "issue_centric:" — the current state is
         from an issue-centric dispatch cycle, not a legacy / override cycle.
      2. last_issue_centric_close_status is in _IC_CLOSE_COMPLETE_STATUSES
         ("closed" or "already_closed") — the close execution succeeded.
         This field is set by _apply_close_execution_state() and cleared by
         _apply_ic_continuation_reset() at the start of the next fetch cycle,
         so a stale value from a prior cycle cannot trigger a false positive
         after the state has been refreshed by a new ChatGPT reply.

    Returns False when a pending or prepared request with an IC-transition source
    (initial_selection or ready_issue) is already in flight, preventing duplicate
    auto-continuation triggers if the main loop re-enters this path before the
    reply is collected.

    The caller is responsible for guarding IC stop paths (initial_selection_stop
    / human_review_needed) via detect_ic_stop_path() before calling this helper.
    """
    chatgpt_decision = str(state.get("chatgpt_decision", "")).strip()
    if not chatgpt_decision.startswith("issue_centric:"):
        return False
    close_status = str(state.get("last_issue_centric_close_status", "")).strip()
    if close_status not in _IC_CLOSE_COMPLETE_STATUSES:
        return False
    # Guard: if a selection or ready-issue request is already in flight (pending or
    # staged-but-not-yet-promoted), the auto-continuation has already been triggered.
    # Returning False prevents a duplicate --select-issue send.
    pending_source = str(state.get("pending_request_source", "")).strip()
    prepared_source = str(state.get("prepared_request_source", "")).strip()
    active_source = pending_source or prepared_source
    if active_source.startswith(("initial_selection:", "ready_issue:")):
        return False
    return True


def _is_ic_issue_create_completed_for_auto_continuation(state: dict[str, object]) -> bool:
    """Return True when the last IC execution created an issue without closing the current issue.

    Used by bridge_orchestrator.run() to detect when auto-continuation to the
    created issue's implementation cycle is appropriate.  All three conditions must hold:

      1. chatgpt_decision starts with "issue_centric:" — the current state is
         from an issue-centric dispatch cycle, not a legacy / override cycle.
      2. last_issue_centric_created_issue_number is non-empty — a created issue
         is clearly identified.  This field is set by _apply_issue_create_execution_state()
         and cleared by _apply_ic_continuation_reset() at the start of the next
         fetch cycle, so a stale value from a prior cycle cannot trigger a false
         positive after the state has been refreshed by a new ChatGPT reply.
      3. last_issue_centric_close_status != "completed" — not a close path.
         issue_create_then_close and similar close paths are already handled by
         _is_ic_close_completed_for_auto_continuation(); this guard prevents double
         triggering on those paths.

    The caller is responsible for guarding IC stop paths (initial_selection_stop
    / human_review_needed) via detect_ic_stop_path() before calling this helper.
    """
    chatgpt_decision = str(state.get("chatgpt_decision", "")).strip()
    if not chatgpt_decision.startswith("issue_centric:"):
        return False
    created_number = str(state.get("last_issue_centric_created_issue_number", "")).strip()
    if not created_number:
        return False
    close_status = str(state.get("last_issue_centric_close_status", "")).strip()
    return close_status not in _IC_CLOSE_COMPLETE_STATUSES


def maybe_promote_codex_done(state: dict[str, object]) -> bool:
    updated_state, recovered_report = recover_report_ready_state(state, prompt_path=runtime_prompt_path())
    if not codex_report_is_ready():
        return False
    if str(updated_state.get("mode", "")).strip() != "codex_done":
        updated = clear_error_fields(dict(updated_state))
        updated.update(
            {
                "mode": "codex_done",
                "need_codex_run": False,
            }
        )
        save_state(updated)
        updated_state = updated
    status = present_bridge_status(updated_state)
    if recovered_report is not None:
        print(f"{status.label}です。fallback report を {recovered_report} から取り込み、次 request 準備へ進みます。")
    else:
        print(f"{status.label}です。bridge/outbox/codex_report.md を検出したため、次 request 準備へ進みます。")
    return True


# ---------------------------------------------------------------------------
# CI gate helpers
# ---------------------------------------------------------------------------

# Default polling interval (seconds) when waiting for a CI run to complete.
CI_GATE_POLL_SECONDS: float = 15.0
# Default maximum time (seconds) to wait for CI completion before giving up.
CI_GATE_TIMEOUT_SECONDS: float = 1800.0
# Default short wait (seconds) for a just-pushed CI run to appear before treating it as missing.
CI_GATE_MISSING_RUN_TIMEOUT_SECONDS: float = 60.0


def _resolve_ci_gate_poll_config(
    project_config: dict[str, object],
) -> tuple[float, float]:
    """Return (poll_seconds, timeout_seconds) from project_config or defaults."""
    try:
        poll = float(project_config.get("ci_gate_poll_seconds", CI_GATE_POLL_SECONDS))
    except (TypeError, ValueError):
        poll = CI_GATE_POLL_SECONDS
    try:
        timeout = float(project_config.get("ci_gate_timeout_seconds", CI_GATE_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        timeout = CI_GATE_TIMEOUT_SECONDS
    return max(5.0, poll), max(60.0, timeout)


def _resolve_ci_gate_missing_run_timeout_seconds(
    project_config: dict[str, object],
) -> float:
    """Return the short discovery timeout used when no CI run is found yet."""
    try:
        timeout = float(
            project_config.get(
                "ci_gate_missing_run_timeout_seconds",
                CI_GATE_MISSING_RUN_TIMEOUT_SECONDS,
            )
        )
    except (TypeError, ValueError):
        timeout = CI_GATE_MISSING_RUN_TIMEOUT_SECONDS
    return max(0.0, timeout)


def _save_ci_gate_context_to_state(
    mutable_state: dict[str, object],
    result: CIGateResult,
) -> None:
    """Persist CI gate result context into *mutable_state* for downstream request building."""
    mutable_state["last_ci_gate_run_id"] = result.run_id
    mutable_state["last_ci_gate_run_url"] = (
        result.run_status.html_url if result.run_status else ""
    )
    mutable_state["last_ci_gate_workflow"] = (
        result.run_status.name if result.run_status else ""
    )
    mutable_state["last_ci_gate_status"] = (
        result.run_status.status if result.run_status else ""
    )
    mutable_state["last_ci_gate_conclusion"] = (
        (result.run_status.conclusion or "") if result.run_status else ""
    )
    mutable_state["last_ci_gate_failure_detail"] = result.failure_detail


def _poll_ci_gate_until_run_discovered(
    state: dict[str, object],
    project_config: dict[str, object],
    *,
    current_issue: str = "",
    report_text: str = "",
) -> CIGateResult | None:
    """Poll briefly while no CI run is found, then return the latest result.

    GitHub Actions runs can appear a few seconds after a push/report is archived.
    This helper avoids treating that small creation window as a definitive
    skipped gate while still preserving the existing safe fallback when no run
    appears within the short discovery timeout.
    """
    poll_seconds, _ = _resolve_ci_gate_poll_config(project_config)
    timeout_seconds = _resolve_ci_gate_missing_run_timeout_seconds(project_config)
    started_at = time.monotonic()
    current_state = dict(state)

    while True:
        result = _run_ci_gate_check(current_state, project_config, report_text=report_text)
        if result is None:
            return None
        if result.verdict != "skipped":
            return result

        elapsed = time.monotonic() - started_at
        if elapsed >= timeout_seconds:
            print(
                f"CI gate: no CI run found after {int(timeout_seconds)}s. "
                "Proceeding with existing missing-run fallback."
            )
            return result

        print(
            f"CI gate: no CI run found yet elapsed={int(elapsed)}s"
            f" (next check in {int(poll_seconds)}s)"
        )
        remaining = max(0.0, timeout_seconds - elapsed)
        time.sleep(min(poll_seconds, remaining))


def _resolve_ci_gate_report_text(state: dict[str, object]) -> str:
    """Return the text of the most-recent archived Codex report.

    Used to extract CI run IDs and commit SHAs when evaluating the CI gate.
    Returns empty string when no report is available.
    """
    from _bridge_common import bridge_runtime_root
    report_ref = str(state.get("last_report_file", "")).strip()
    if not report_ref:
        return ""
    candidate = (bridge_runtime_root() / report_ref).resolve()
    if not candidate.exists():
        # Try relative to ROOT_DIR as well.
        candidate = (ROOT_DIR / report_ref).resolve()
    return read_text(candidate, default="")


def _run_ci_gate_check(
    state: dict[str, object],
    project_config: dict[str, object],
    *,
    report_text: str = "",
) -> CIGateResult | None:
    """Evaluate the CI gate and return a result, or None when gate is disabled.

    The gate is disabled when ``github_repository`` is not configured, because
    we need a repository to check Actions runs.

    When the gate is active but the GitHub token is unavailable, returns an
    ``"indeterminate"`` result rather than raising.
    """
    repository = str(project_config.get("github_repository", "")).strip()
    if not repository:
        # Gate cannot run without a target repository.
        return None

    try:
        from issue_centric_github import resolve_github_token
        token, _ = resolve_github_token()
    except Exception as exc:
        import datetime as _dt
        indeterminate_at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        new_state = dict(state)
        new_state["ci_gate_status"] = "indeterminate"
        new_state["ci_gate_checked_at"] = indeterminate_at
        save_state(new_state)
        print(
            f"CI gate: GitHub token unavailable ({exc}). "
            "Marking indeterminate — human review required."
        )
        from issue_centric_ci_gate import CIGateResult as _CGR
        return _CGR(
            verdict="indeterminate",
            run_id=str(state.get("ci_gate_run_id", "")),
            commit_sha=str(state.get("ci_gate_commit_sha", "")),
            checked_at=indeterminate_at,
            attempt_count=int(state.get("ci_gate_attempt_count", 0)) + 1,
            run_status=None,
            note=f"GitHub token unavailable: {exc}",
        )

    effective_report_text = report_text or _resolve_ci_gate_report_text(state)
    return evaluate_ci_gate(
        report_text=effective_report_text,
        repository=repository,
        token=token,
        prior_state=state,
    )


def _poll_ci_gate_until_complete(
    state: dict[str, object],
    project_config: dict[str, object],
    *,
    current_issue: str = "",
    report_text: str = "",
) -> tuple[CIGateResult, bool] | None:
    """Poll the CI gate synchronously until the run completes or the timeout elapses.

    Each poll sleeps ``ci_gate_poll_seconds`` (default 15 s) and logs a heartbeat
    line so long runs are visible.  The timeout is ``ci_gate_timeout_seconds``
    (default 1800 s).  Both values can be overridden in ``project_config``.

    Returns:
        ``None``                      — gate is disabled (no repository configured).
        ``(result, False)``           — CI gate verdict is no longer ``"waiting_ci"``
                                        (success / failure / indeterminate / skipped).
        ``(result, True)``            — timeout elapsed; ``result`` is the last
                                        ``"waiting_ci"`` snapshot.
    """
    poll_seconds, timeout_seconds = _resolve_ci_gate_poll_config(project_config)
    started_at = time.monotonic()
    current_state = dict(state)

    while True:
        result = _run_ci_gate_check(current_state, project_config, report_text=report_text)
        if result is None:
            return None  # gate disabled

        # Persist updated attempt_count / checked_at / run_id to state.
        new_state = dict(current_state)
        apply_ci_gate_state(new_state, result, current_issue=current_issue)
        save_state(new_state)
        current_state = new_state

        # Done once the verdict is definitive.
        if result.verdict != "waiting_ci":
            return result, False

        elapsed = time.monotonic() - started_at

        # Timeout: stop polling, return last pending snapshot with timed_out=True.
        if elapsed >= timeout_seconds:
            return result, True

        # Heartbeat log.
        run_id = result.run_id or "unknown"
        status_str = result.run_status.status if result.run_status else "in_progress"
        print(
            f"CI gate: waiting for run {run_id} status={status_str} elapsed={int(elapsed)}s"
            f" (next check in {int(poll_seconds)}s)"
        )

        # Sleep before next poll (but not longer than the remaining timeout).
        remaining = max(0.0, timeout_seconds - elapsed)
        time.sleep(min(poll_seconds, remaining))


def _handle_waiting_ci_recheck(
    state: dict[str, object],
    project_config: dict[str, object],
    argv: list[str] | None = None,
) -> int:
    """Re-evaluate CI gate for a state already in ``waiting_ci``, polling until done.

    Called at the top of ``run()`` when ``ci_gate_status == "waiting_ci"``.

    Polls synchronously until the CI run completes (success / failure /
    indeterminate) or the configured timeout elapses, then either proceeds to
    send the next ChatGPT request or stops with a clear reason.

    Returns:
      0  — success path: advances via ``run()`` to ``request_prompt_from_report``
      0  — failure path: proceeds via ``run()`` to ChatGPT with CI failure context
      0  — indeterminate: state saved, human review required
      0  — timeout: ``error`` set in state for a clear CI-timeout stop
    """
    current_issue = str(state.get("ci_gate_current_issue", "")).strip()
    run_id_hint = str(state.get("ci_gate_run_id", "")).strip()
    print(
        f"CI gate: waiting_ci state detected"
        f" (run={run_id_hint or 'unknown'}, issue={current_issue or 'unknown'})."
        " Polling CI until complete..."
    )

    poll_result = _poll_ci_gate_until_complete(
        state, project_config, current_issue=current_issue
    )

    if poll_result is None:
        # Gate disabled (no repository configured) — clear gate and proceed.
        new_state = dict(state)
        clear_ci_gate_state(new_state)
        save_state(new_state)
        print("CI gate: disabled (no github_repository). Cleared waiting_ci state.")
        return 0

    result, timed_out = poll_result

    if timed_out:
        run_id = result.run_id or "unknown"
        status_str = result.run_status.status if result.run_status else "in_progress"
        _, timeout_seconds = _resolve_ci_gate_poll_config(project_config)
        timeout_msg = (
            f"CI gate timeout: run {run_id} still {status_str!r} after {int(timeout_seconds)}s."
            " Bridge will not proceed until CI completes."
            " Clear this error and re-run once CI finishes."
        )
        new_state = dict(state)
        apply_ci_gate_state(new_state, result, current_issue=current_issue)
        new_state["error"] = True
        new_state["error_message"] = timeout_msg
        save_state(new_state)
        print(f"CI gate timeout: run {run_id} still {status_str!r} after {int(timeout_seconds)}s")
        return 0

    new_state = dict(state)
    apply_ci_gate_state(new_state, result, current_issue=current_issue)

    if result.verdict == "success":
        _save_ci_gate_context_to_state(new_state, result)
        clear_ci_gate_state(new_state)
        save_state(new_state)
        print(
            f"CI gate: success (run_id={result.run_id}). "
            "Proceeding to send report to ChatGPT."
        )
        # Re-enter run() with the cleared state so normal dispatch continues.
        return run(new_state, argv)

    if result.verdict == "failure":
        _save_ci_gate_context_to_state(new_state, result)
        clear_ci_gate_state(new_state)
        save_state(new_state)
        print(
            f"CI gate: failure (run_id={result.run_id},"
            f" conclusion={result.run_status.conclusion if result.run_status else 'failure'})."
            " Proceeding to ChatGPT with CI failure context."
        )
        # Proceed via run() so ChatGPT receives CI failure info in the request context.
        return run(new_state, argv)

    # indeterminate
    save_state(new_state)
    print(
        f"CI gate: indeterminate (run_id={result.run_id}, attempt={result.attempt_count}). "
        f"{result.note} Human review required."
    )
    return 0


def _handle_ci_gate_before_report_request(
    state: dict[str, object],
    project_config: dict[str, object],
    args: argparse.Namespace,
) -> int | None:
    """Check CI gate before sending a report to ChatGPT, polling if pending.

    Called from ``run()`` immediately before ``request_prompt_from_report``.

    When the CI run is pending (``waiting_ci``), polls synchronously (up to
    ``ci_gate_timeout_seconds``) rather than stopping immediately.  This
    prevents run_until_stop from seeing an unchanged state and stopping with
    the wrong guidance.

    Returns:
      None  — gate passed (success / skipped / disabled); caller should proceed
              with request_prompt_from_report normally.
      int   — gate held or failed; return this exit code to the caller.
    """
    current_issue = (
        str(state.get("last_issue_centric_current_issue", "")).strip()
        or str(state.get("last_issue_centric_principal_issue", "")).strip()
        or str(state.get("last_issue_centric_target_issue", "")).strip()
    )

    # Do a quick initial check to handle the "skipped / success / already done" cases
    # cheaply before committing to a potential long-running polling loop.
    initial_result = _run_ci_gate_check(state, project_config)
    if initial_result is None:
        # Gate disabled.
        return None

    new_state = dict(state)
    apply_ci_gate_state(new_state, initial_result, current_issue=current_issue)

    if initial_result.verdict == "skipped":
        # No CI run found yet. A just-pushed Actions run can appear shortly
        # after the report is archived, so poll briefly before using the
        # existing missing-run fallback.
        discovered_result = _poll_ci_gate_until_run_discovered(
            state, project_config, current_issue=current_issue
        )
        if discovered_result is None:
            return None
        initial_result = discovered_result
        new_state = dict(state)
        apply_ci_gate_state(new_state, initial_result, current_issue=current_issue)
        if initial_result.verdict == "skipped":
            return None

    if initial_result.verdict == "success":
        _save_ci_gate_context_to_state(new_state, initial_result)
        clear_ci_gate_state(new_state)
        save_state(new_state)
        print(
            f"CI gate: success (run_id={initial_result.run_id}). "
            "Proceeding to send report to ChatGPT."
        )
        return None

    if initial_result.verdict == "indeterminate":
        save_state(new_state)
        print(
            f"CI gate: indeterminate (run_id={initial_result.run_id}). "
            f"{initial_result.note} Human review required. Not sending report to ChatGPT."
        )
        return 0

    if initial_result.verdict == "failure":
        _save_ci_gate_context_to_state(new_state, initial_result)
        clear_ci_gate_state(new_state)
        save_state(new_state)
        print(
            f"CI gate: failure (run_id={initial_result.run_id},"
            f" conclusion={initial_result.run_status.conclusion if initial_result.run_status else 'failure'})."
            " Proceeding to ChatGPT with CI failure context."
        )
        # Return None so request_prompt_from_report runs and ChatGPT receives
        # the CI failure context via build_request_context_section.
        return None

    # verdict == "waiting_ci": poll synchronously until complete or timeout.
    run_id = initial_result.run_id or "unknown"
    status_str = initial_result.run_status.status if initial_result.run_status else "in_progress"
    print(
        f"CI gate: CI run {run_id} is {status_str!r}."
        " Polling until CI completes (do not stop bridge)..."
    )
    # The initial check already saved state; pass the updated state to the poller
    # so the attempt_count / run_id from the initial check is used.
    save_state(new_state)

    poll_result = _poll_ci_gate_until_complete(
        new_state, project_config, current_issue=current_issue
    )

    if poll_result is None:
        # Gate became disabled during polling — clear and proceed.
        final_state = dict(new_state)
        clear_ci_gate_state(final_state)
        save_state(final_state)
        print("CI gate: disabled (no github_repository). Cleared gate state.")
        return None

    result, timed_out = poll_result

    if timed_out:
        run_id = result.run_id or "unknown"
        status_str = result.run_status.status if result.run_status else "in_progress"
        _, timeout_seconds = _resolve_ci_gate_poll_config(project_config)
        timeout_msg = (
            f"CI gate timeout: run {run_id} still {status_str!r} after {int(timeout_seconds)}s."
            " Bridge will not proceed until CI completes."
            " Clear this error and re-run once CI finishes."
        )
        final_state = dict(new_state)
        apply_ci_gate_state(final_state, result, current_issue=current_issue)
        final_state["error"] = True
        final_state["error_message"] = timeout_msg
        save_state(final_state)
        print(f"CI gate timeout: run {run_id} still {status_str!r} after {int(timeout_seconds)}s")
        return 0

    final_state = dict(new_state)
    apply_ci_gate_state(final_state, result, current_issue=current_issue)

    if result.verdict == "success":
        _save_ci_gate_context_to_state(final_state, result)
        clear_ci_gate_state(final_state)
        save_state(final_state)
        print(
            f"CI gate: success (run_id={result.run_id}). "
            "Proceeding to send report to ChatGPT."
        )
        return None

    if result.verdict == "failure":
        _save_ci_gate_context_to_state(final_state, result)
        clear_ci_gate_state(final_state)
        save_state(final_state)
        print(
            f"CI gate: failure (run_id={result.run_id},"
            f" conclusion={result.run_status.conclusion if result.run_status else 'failure'})."
            " Proceeding to ChatGPT with CI failure context."
        )
        return None

    # indeterminate
    save_state(final_state)
    print(
        f"CI gate: indeterminate (run_id={result.run_id}). "
        f"{result.note} Human review required. Not sending report to ChatGPT."
    )
    return 0




def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    project_config = load_project_config()
    args = parse_args(argv, project_config)
    print_project_config_warnings(project_config)

    # Resolve the active execution agent from CLI arg / config.
    # Valid values: "codex" | "github_copilot".
    # Invalid / missing values raise BridgeError via resolve_execution_agent().
    execution_agent = resolve_execution_agent(
        {"execution_agent": args.execution_agent} if args.execution_agent else project_config
    )

    # CI gate: if we are in waiting_ci state, recheck CI before anything else.
    if is_waiting_ci(state):
        return _handle_waiting_ci_recheck(state, project_config, argv)

    if should_prioritize_unarchived_report(state):
        status = present_bridge_status(state)
        print(f"{status.label}です。未退避 report を先に archive します。")
        return archive_codex_report.run(dict(state))

    if has_pending_issue_centric_codex_dispatch(state):
        status = present_bridge_status(state)
        print(f"{status.label}です。prepared Codex body を issue-centric codex_run dispatch へ進めます。")
        return dispatch_pending_issue_centric_codex_run(dict(state), project_config=project_config, execution_agent=execution_agent)

    # Blocked lifecycle guard: operator confirmation required, no dispatch.
    # resolve_unified_next_action() falls through to the dispatch plan for blocked lifecycle
    # states (ready_for_codex without need_codex_run), so this guard must come before
    # the unified action call to avoid incorrect normal-path routing.
    # is_blocked_codex_lifecycle_state() encapsulates lifecycle classification so this
    # call site does not need to import resolve_codex_lifecycle_view() directly.
    if is_blocked_codex_lifecycle_state(state):
        status = present_bridge_status(state)
        print(f"{status.label}です。Codex 用 prompt はありますが、起動条件を確認してください。")
        return 0

    # action-view routing: resolve_unified_next_action() is the single authority.
    # Covers both Codex lifecycle (launch_codex_once / wait_for_codex_report /
    # archive_codex_report) and normal-path (request_next_prompt / fetch_next_prompt /
    # request_prompt_from_report) action keys.  resolve_codex_lifecycle_view() is no
    # longer called at this call site; all dispatch decisions go through the action key.
    action = resolve_unified_next_action(state)
    status = present_bridge_status(state)

    # Codex lifecycle dispatch arms (named by action key, not by mode).
    if action == "launch_codex_once":
        # Codex lifecycle: ready_for_codex + need_codex_run=True
        # Route to the provider-specific launch script.
        if execution_agent == "github_copilot":
            print(f"{status.label}です。bridge が GitHub Copilot を 1 回起動します。")
            return launch_github_copilot.run(dict(state), build_github_copilot_launch_argv(args))
        # Default: execution_agent == "codex"
        print(f"{status.label}です。bridge が Codex worker を 1 回起動します。")
        return launch_codex_once.run(dict(state), build_codex_launch_argv(args))

    if action == "wait_for_codex_report":
        # Codex lifecycle: codex_running — promote to codex_done if report ready
        if maybe_promote_codex_done(state):
            return 0
        print(
            f"{status.label}です。Codex worker の完了待ちです。"
            " live 再開前に長く残った state なら、report / error / pause / bridge/STOP を確認して"
            " stale runtime でないか先に見てください。"
        )
        return 0

    if action == "archive_codex_report":
        # Reached from codex_done lifecycle; unarchived report priority was handled above.
        print(f"{status.label}です。完了報告を履歴へ退避します。")
        return archive_codex_report.run(dict(state))

    # Normal path: dispatch plan is the primary routing authority.
    # resolve_runtime_dispatch_plan() is called here (and only here) for plan.note.
    # status is already resolved above via present_bridge_status(state).
    plan = resolve_runtime_dispatch_plan(state)
    # IC stop paths: surface chatgpt_decision_note rather than the generic plan note.
    _ic_stop = detect_ic_stop_path(state)
    if _ic_stop == "initial_selection_stop":
        _selected_ref = str(state.get("selected_ready_issue_ref", "")).strip()
        if _selected_ref:
            _validation = validate_selected_ready_issue_for_auto_continue(_selected_ref, state)
            if not _validation.ok:
                _print_selected_ready_issue_validation_stop(_selected_ref, _validation)
                # Clear the invalidated selection so run_until_stop.py stop summary does not
                # suggest --ready-issue-ref <closed issue> to the operator.
                _cleared = dict(state)
                _cleared["selected_ready_issue_ref"] = ""
                save_state(_cleared)
                return 0
            # Auto-continue: ChatGPT clearly selected ONE ready issue.
            # Proceed directly to next issue implementation without operator re-run.
            # Clear selected_ready_issue_ref in the forwarded state so it is not
            # carried into the next cycle's detect_ic_stop_path() evaluation.
            print(
                f"{status.label}です。ready issue {_selected_ref} が選定されました。"
                " 自動で次 issue の実装へ継続します。"
            )
            auto_state = dict(state)
            auto_state["selected_ready_issue_ref"] = ""
            next_argv: list[str] = []
            if args.worker_repo_path:
                next_argv.extend(["--project-path", args.worker_repo_path])
            next_argv.extend(["--ready-issue-ref", _selected_ref])
            return request_next_prompt.run(auto_state, next_argv)
        # Fallback: selected_ready_issue_ref absent despite initial_selection_stop
        # (should not happen in normal flow but guard against inconsistent state).
        _ic_note = str(state.get("chatgpt_decision_note", "")).strip()
        _stop_note = _ic_note or plan.note
        print(f"{status.label}です。{_stop_note}")
    elif _ic_stop == "human_review_needed":
        _ic_note = str(state.get("chatgpt_decision_note", "")).strip()
        _stop_note = _ic_note or plan.note
        print(f"{status.label}です。{_stop_note}")
    elif plan.next_action == "completed":
        # Use format_operator_stop_note on the completed path to surface project sync warning.
        _stop_note = format_operator_stop_note(state, plan=plan)
        print(f"{status.label}です。{_stop_note}")
    else:
        print(f"{status.label}です。{plan.note}")

    # Dispatch layer: route to the appropriate script.
    if plan.next_action == "request_next_prompt":
        return request_next_prompt.run(dict(state), build_initial_request_argv(args))
    if plan.next_action == "request_prompt_from_report":
        # CI gate: check GitHub Actions status before sending the report to ChatGPT.
        # If CI is still running (queued / in_progress) we hold in waiting_ci state
        # instead of sending — this prevents the "CI not done, please stop" loop.
        _ci_gate_rc = _handle_ci_gate_before_report_request(dict(state), project_config, args)
        if _ci_gate_rc is not None:
            return _ci_gate_rc
        return request_prompt_from_report.run(dict(state), build_report_request_argv(args))
    if plan.next_action == "fetch_next_prompt":
        # Wrap in a BridgeStop catch so that initial_selection_stop from fetch_next_prompt
        # can be handled as an in-run auto-continuation rather than propagating to
        # guarded_main() and printing a [stop] message.
        try:
            return fetch_next_prompt.run(dict(state), build_fetch_argv(args))
        except BridgeStop:
            # fetch_next_prompt saved updated state before raising.  Reload it and check
            # whether this was an initial_selection_stop that can be auto-continued.
            _post_fetch_state = load_state()
            _is_ref = _resolve_post_fetch_initial_selection_ref(_post_fetch_state)
            if _is_ref:
                _validation = validate_selected_ready_issue_for_auto_continue(
                    _is_ref,
                    _post_fetch_state,
                    prior_state=state,
                )
                if not _validation.ok:
                    _print_selected_ready_issue_validation_stop(_is_ref, _validation)
                    # Clear the invalidated selection so run_until_stop.py stop summary does not
                    # suggest --ready-issue-ref <closed issue> to the operator.
                    _post_fetch_state["selected_ready_issue_ref"] = ""
                    save_state(_post_fetch_state)
                    return 0
                print(
                    f"{status.label}です。initial selection 完了: ready issue {_is_ref} が選定されました。"
                    " 自動で次 issue の実装へ継続します。"
                )
                _is_auto = dict(_post_fetch_state)
                _is_auto["selected_ready_issue_ref"] = ""
                _is_argv: list[str] = []
                if args.worker_repo_path:
                    _is_argv.extend(["--project-path", args.worker_repo_path])
                _is_argv.extend(["--ready-issue-ref", _is_ref])
                return request_next_prompt.run(_is_auto, _is_argv)
            raise

    # IC close auto-continuation: when the last IC execution closed the current
    # issue and no IC stop path requires human intervention, proceed directly to
    # the next ready issue selection instead of stopping and waiting for restart.
    # _ic_stop == "" guards initial_selection_stop / human_review_needed paths.
    if _ic_stop == "" and _is_ic_close_completed_for_auto_continuation(state):
        print(
            f"{status.label}です。current issue のクローズを検出しました。"
            " 次の ready issue 選定へ自動で進みます。"
        )
        select_argv: list[str] = []
        if args.worker_repo_path:
            select_argv.extend(["--project-path", args.worker_repo_path])
        select_argv.append("--select-issue")
        return request_next_prompt.run(dict(state), select_argv)

    # IC issue_create auto-continuation: when the last IC execution created an issue
    # without closing the current issue, proceed directly to the created issue's
    # implementation cycle instead of stopping and waiting for operator --ready-issue-ref.
    # _ic_stop == "" guards initial_selection_stop / human_review_needed paths.
    # _is_ic_close_completed_for_auto_continuation() is False here (guarded above),
    # so issue_create_then_close paths (which trigger the close block above) never reach
    # this point.
    if _ic_stop == "" and _is_ic_issue_create_completed_for_auto_continuation(state):
        _created_number = str(state.get("last_issue_centric_created_issue_number", "")).strip()
        _created_title = str(state.get("last_issue_centric_created_issue_title", "")).strip()
        _created_ref = (
            f"#{_created_number} {_created_title}".strip() if _created_title else f"#{_created_number}"
        )
        print(
            f"{status.label}です。issue #{_created_number} を作成しました。"
            f" 作成された issue {_created_ref} を current として次の実装へ自動で進みます。"
        )
        issue_create_state = dict(state)
        # Clear created-issue fields so the next cycle's detect_ic_stop_path()
        # and _is_ic_issue_create_completed_for_auto_continuation() do not see
        # stale values.  _apply_ic_continuation_reset() will also clear these
        # at the start of the next fetch cycle, but clearing here prevents
        # false positives in any intermediate bridge invocations.
        issue_create_state["last_issue_centric_created_issue_number"] = ""
        issue_create_state["last_issue_centric_created_issue_url"] = ""
        issue_create_state["last_issue_centric_created_issue_title"] = ""
        issue_create_argv: list[str] = []
        if args.worker_repo_path:
            issue_create_argv.extend(["--project-path", args.worker_repo_path])
        issue_create_argv.extend(["--ready-issue-ref", _created_ref])
        return request_next_prompt.run(issue_create_state, issue_create_argv)

    return 0


if __name__ == "__main__":
    sys.exit(
        guarded_main(
            lambda state: run(state),
            recover_state=lambda state: recover_pending_handoff_state(
                recover_prepared_request_state(
                    recover_report_ready_state(state, prompt_path=runtime_prompt_path())[0]
                )[0]
            )[0],
        )
    )
