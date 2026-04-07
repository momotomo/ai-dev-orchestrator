#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import archive_codex_report
import fetch_next_prompt
import launch_codex_once
import request_next_prompt
import request_prompt_from_report
from _bridge_common import ROOT_DIR, BridgeError, browser_fetch_timeout_seconds, clear_error_fields, codex_report_is_ready, format_next_action_note, guarded_main, has_pending_issue_centric_codex_dispatch, load_browser_config, load_project_config, load_state, prepared_request_action, present_bridge_status, print_project_config_warnings, project_repo_path, read_text, recover_pending_handoff_state, recover_prepared_request_state, recover_report_ready_state, resolve_fallback_legacy_transition, resolve_issue_centric_route_choice, resolve_next_generation_transition, resolve_prepared_request_transition, resolve_runtime_next_action, runtime_prompt_path, save_state, should_prioritize_unarchived_report, should_rotate_before_next_chat_request, worker_repo_path
from issue_centric_close_current_issue import execute_close_current_issue
from issue_centric_codex_launch import launch_issue_centric_codex_run
from issue_centric_codex_run import execute_codex_run_action
from issue_centric_contract import maybe_parse_issue_centric_reply
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
        "--codex-bin",
        default=str(project_config.get("codex_bin", "codex")),
        help="launch_codex_once.py に渡す Codex CLI コマンド",
    )
    parser.add_argument(
        "--codex-model",
        default=str(project_config.get("codex_model", "")),
        help="launch_codex_once.py に渡す model 名",
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
    if args.codex_model:
        launch_argv.extend(["--model", args.codex_model])
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

    raw_log_ref = str(metadata.get("raw_response_log", "")).strip()
    if not raw_log_ref:
        raise BridgeError("issue-centric codex dispatch に必要な raw response log が metadata にありません。")
    raw_log_path = resolve_saved_runtime_path(raw_log_ref)
    raw_text = read_text(raw_log_path).strip()
    if not raw_text:
        raise BridgeError(f"issue-centric raw response log を読めませんでした: {raw_log_ref}")

    contract_decision = maybe_parse_issue_centric_reply(raw_text)
    if contract_decision is None or contract_decision.action.value != "codex_run":
        raise BridgeError("pending issue-centric codex dispatch を raw response log から再構成できませんでした。")

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
) -> int:
    contract_decision, materialized, raw_log_ref, metadata_ref, artifact_ref = load_pending_issue_centric_codex_materialized(state)
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
        execute_followup_issue_action_fn=execute_followup_issue_action,
        execute_current_issue_project_state_sync_fn=execute_current_issue_project_state_sync,
        launch_runner=launch_codex_once.run,
    )
    save_state(dispatch_result.final_state)
    print(dispatch_result.stop_message)
    return 0


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
    mode = str(state.get("mode", "idle"))
    if should_prioritize_unarchived_report(state):
        status = present_bridge_status(state)
        print(f"{status.label}です。未退避 report を先に archive します。")
        return archive_codex_report.run(dict(state))

    if has_pending_issue_centric_codex_dispatch(state):
        status = present_bridge_status(state)
        print(f"{status.label}です。prepared Codex body を issue-centric codex_run dispatch へ進めます。")
        return dispatch_pending_issue_centric_codex_run(dict(state), project_config=project_config)

    # Preserve the existing Codex lifecycle steps as mode-driven compatibility branches.
    if mode == "ready_for_codex" and bool(state.get("need_codex_run")):
        status = present_bridge_status(state)
        print(f"{status.label}です。bridge が Codex worker を 1 回起動します。")
        return launch_codex_once.run(dict(state), build_codex_launch_argv(args))

    if mode == "ready_for_codex":
        status = present_bridge_status(state, blocked=True)
        print(f"{status.label}です。Codex 用 prompt はありますが、起動条件を確認してください。")
        return 0

    if mode == "codex_running":
        if maybe_promote_codex_done(state):
            return 0
        status = present_bridge_status(state)
        print(
            f"{status.label}です。Codex worker の完了待ちです。"
            " live 再開前に長く残った state なら、report / error / pause / bridge/STOP を確認して"
            " stale runtime でないか先に見てください。"
        )
        return 0

    if mode == "codex_done":
        status = present_bridge_status(state)
        print(f"{status.label}です。完了報告を履歴へ退避します。")
        return archive_codex_report.run(dict(state))

    # Issue-centric state view is the primary routing authority.
    # mode is preserved for Codex lifecycle steps and compatibility display.
    runtime_action, runtime_action_reason = resolve_runtime_next_action(state)
    # route_choice provides routing context for wording and informational notes.
    route_choice = resolve_issue_centric_route_choice(state)

    if runtime_action == "pending_reply":
        # Wording layer: shared dispatch note for pending_reply.
        note = format_next_action_note(state, next_action="pending_reply", runtime_action_reason=runtime_action_reason)
        status = present_bridge_status(state)
        print(f"{status.label}です。{note}")
        return fetch_next_prompt.run(dict(state), build_fetch_argv(args))

    # Action-key resolution via shared spine helpers.
    # prepared_request → resolve_prepared_request_transition (may fall back to need_next_generation)
    # need_next_generation → resolve_next_generation_transition
    # fallback_legacy      → resolve_fallback_legacy_transition
    if runtime_action == "prepared_request":
        next_action = resolve_prepared_request_transition(state)
        if next_action == "need_next_generation":
            # builder could not be determined; treat as need_next_generation
            next_action = resolve_next_generation_transition(state)
    elif runtime_action == "need_next_generation":
        next_action = resolve_next_generation_transition(state)
    else:
        # fallback_legacy: runtime is degraded / unavailable / invalidated.
        # Codex lifecycle branches (ready_for_codex, codex_running, codex_done) are
        # handled earlier in this function and will not appear as next_action here.
        next_action = resolve_fallback_legacy_transition(state)

    # Wording layer: shared dispatch note (action key + routing context).
    note = format_next_action_note(
        state,
        next_action=next_action,
        runtime_action=runtime_action,
        runtime_action_reason=runtime_action_reason,
        route_choice=route_choice,
    )
    status = present_bridge_status(state)
    print(f"{status.label}です。{note}")

    # Dispatch layer: route to the appropriate script.
    if next_action == "request_next_prompt":
        return request_next_prompt.run(dict(state), build_initial_request_argv(args))
    if next_action == "request_prompt_from_report":
        return request_prompt_from_report.run(dict(state), build_report_request_argv(args))
    if next_action == "fetch_next_prompt":
        return fetch_next_prompt.run(dict(state), build_fetch_argv(args))
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
