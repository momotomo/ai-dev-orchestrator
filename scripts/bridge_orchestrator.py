#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import archive_codex_report
import fetch_next_prompt
import launch_codex_once
import launch_github_copilot
import request_next_prompt
import request_prompt_from_report
from _bridge_common import ROOT_DIR, BridgeError, browser_fetch_timeout_seconds, clear_error_fields, codex_report_is_ready, detect_ic_stop_path, format_operator_stop_note, guarded_main, has_pending_issue_centric_codex_dispatch, is_blocked_codex_lifecycle_state, load_browser_config, load_project_config, load_state, prepared_request_action, present_bridge_status, print_project_config_warnings, project_repo_path, read_text, recover_pending_handoff_state, recover_prepared_request_state, recover_report_ready_state, resolve_execution_agent, resolve_runtime_dispatch_plan, resolve_unified_next_action, runtime_prompt_path, save_state, should_prioritize_unarchived_report, should_rotate_before_next_chat_request, worker_repo_path
from issue_centric_close_current_issue import execute_close_current_issue
from issue_centric_parent_update import execute_parent_issue_update_after_close
from issue_centric_codex_launch import launch_issue_centric_codex_run
from issue_centric_codex_run import execute_codex_run_action
from issue_centric_contract import IssueCentricAction, IssueCentricDecision, maybe_parse_issue_centric_reply
from issue_centric_current_issue_project_state import execute_current_issue_project_state_sync
from issue_centric_execution import dispatch_issue_centric_execution
from issue_centric_followup_issue import execute_followup_issue_action
from issue_centric_human_review import execute_human_review_action
from issue_centric_issue_create import execute_issue_create_action
from issue_centric_transport import MaterializedIssueCentricDecision, decode_issue_centric_decision


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


def _is_ic_close_completed_for_auto_continuation(state: dict[str, object]) -> bool:
    """Return True when the last IC execution closed the current issue successfully.

    Used by bridge_orchestrator.run() to detect when auto-continuation to the
    next ready issue selection is appropriate.  Both conditions must hold:

      1. chatgpt_decision starts with "issue_centric:" — the current state is
         from an issue-centric dispatch cycle, not a legacy / override cycle.
      2. last_issue_centric_close_status == "completed" — the close execution
         succeeded.  This field is set by _apply_close_execution_state() and
         cleared by _apply_ic_continuation_reset() at the start of the next
         fetch cycle, so a stale "completed" value from a prior cycle cannot
         trigger a false positive after the state has been refreshed by a new
         ChatGPT reply.

    The caller is responsible for guarding IC stop paths (initial_selection_stop
    / human_review_needed) via detect_ic_stop_path() before calling this helper.
    """
    chatgpt_decision = str(state.get("chatgpt_decision", "")).strip()
    if not chatgpt_decision.startswith("issue_centric:"):
        return False
    close_status = str(state.get("last_issue_centric_close_status", "")).strip()
    return close_status == "completed"


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
        return request_prompt_from_report.run(dict(state), build_report_request_argv(args))
    if plan.next_action == "fetch_next_prompt":
        return fetch_next_prompt.run(dict(state), build_fetch_argv(args))

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
