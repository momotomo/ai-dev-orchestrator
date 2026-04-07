#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from _bridge_common import (
    BridgeStop,
    browser_fetch_timeout_seconds,
    browser_runner_heartbeat_seconds,
    bridge_runtime_root,
    check_stop_conditions,
    codex_report_is_ready,
    is_apple_event_timeout_text,
    latest_codex_progress_snapshot,
    load_browser_config,
    load_state,
    load_project_config,
    log_text,
    mark_error,
    project_config_warnings,
    print_project_config_warnings,
    prepare_issue_centric_runtime_mode,
    present_bridge_handoff,
    present_bridge_status,
    is_retryable_pending_handoff_error,
    recover_pending_handoff_state,
    recover_prepared_request_state,
    recover_report_ready_state,
    recover_codex_report,
    resolve_issue_centric_preferred_loop_action,
    repo_relative,
    runtime_prompt_path,
    runtime_report_path,
    runtime_stop_path,
    safari_timeout_checklist_text,
    should_prioritize_unarchived_report,
    should_rotate_before_next_chat_request,
    state_snapshot,
    worker_repo_path,
)


DEFAULT_MAX_STEPS = 6
DEFAULT_SLEEP_SECONDS = 1.0
DEFAULT_HEARTBEAT_SECONDS = 15.0
DEFAULT_FETCH_TIMEOUT_SECONDS = 0
DEFAULT_CODEX_RUNNING_WAIT_SECONDS = 120.0
DEFAULT_CODEX_RUNNING_POLL_SECONDS = 5.0


def start_bridge_mode(state: dict[str, Any]) -> str:
    action = describe_next_action(state)
    if action == "request_next_prompt":
        return "ready issue 参照から始められます"
    if action == "request_prompt_from_report" and str(state.get("mode", "")).strip() == "awaiting_user":
        return "補足を入れて再開できます"
    if is_retryable_pending_handoff_error(state):
        return "同じコマンドで再試行できます"
    blocked_guidance = blocked_next_guidance(state)
    if blocked_guidance is not None:
        return "先に確認が必要です"
    return "このまま再開できます"


def start_bridge_resume_guidance(args: argparse.Namespace, state: dict[str, Any]) -> tuple[str, str, str]:
    blocked_guidance = blocked_next_guidance(state)
    stale_codex_running = is_stale_codex_running_candidate("", state)
    status = present_bridge_status(state, blocked=bool(blocked_guidance), stale_codex_running=stale_codex_running)
    note = blocked_guidance[1] if blocked_guidance is not None else suggested_next_note(state)
    if should_prioritize_unarchived_report(state):
        note = "未退避 report が残っているため、handoff より先に archive と次の ChatGPT 返送導線へ戻します。"
    elif str(state.get("pending_handoff_log", "")).strip() and should_rotate_before_next_chat_request(state):
        note = (
            "次の ChatGPT request を送る前に使う handoff は回収済みですが、"
            " まだ新チャットへ送れていません。"
            " 同じコマンドを再実行すると composer 確認と送信確認を再試行します。"
        )
    guidance = entry_guidance(state, args)
    return status.label, guidance, note


def configure_output_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(line_buffering=True)


def parse_args(argv: list[str] | None = None, project_config: dict[str, object] | None = None) -> argparse.Namespace:
    project_config = project_config or load_project_config()
    browser_config = load_browser_config()
    parser = argparse.ArgumentParser(
        description="bridge の実行エンジンです。通常入口は scripts/start_bridge.py を使います。",
        epilog=(
            "通常起動例: python3 scripts/start_bridge.py "
            "--project-path /path/to/target-repo --max-execution-count 6"
        ),
    )
    parser.add_argument("--max-steps", "--max-execution-count", dest="max_steps", type=int, default=DEFAULT_MAX_STEPS, help="最大何手まで進めるか")
    parser.add_argument(
        "--stop-at-cycle-boundary",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="archive で cycle が 1 つ進んだらその run を自然停止し、次 cycle の request は次回実行へ回す",
    )
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS, help="各手のあいだに待つ秒数")
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=float(browser_runner_heartbeat_seconds(browser_config)),
        help="長待ち中に進捗を出す間隔。0 以下で heartbeat を出さない",
    )
    parser.add_argument(
        "--fetch-timeout-seconds",
        type=int,
        default=int(browser_fetch_timeout_seconds(browser_config)),
        help="waiting_prompt_reply 時に fetch 側へ渡す最大待機秒数。0 の場合は browser_config.json を使う",
    )
    parser.add_argument(
        "--codex-running-wait-seconds",
        type=float,
        default=DEFAULT_CODEX_RUNNING_WAIT_SECONDS,
        help="state=codex_running で report を待つ最大秒数。0 以下で即停止する",
    )
    parser.add_argument(
        "--codex-running-poll-seconds",
        type=float,
        default=DEFAULT_CODEX_RUNNING_POLL_SECONDS,
        help="state=codex_running で report を確認する間隔",
    )
    parser.add_argument("--dry-run", action="store_true", help="実行せず、次の 1 手と停止条件だけ表示する")
    parser.add_argument(
        "--codex-bin",
        default=str(project_config.get("codex_bin", "codex")),
        help="bridge_orchestrator.py に渡す Codex CLI コマンド",
    )
    parser.add_argument(
        "--codex-model",
        default=str(project_config.get("codex_model", "")),
        help="bridge_orchestrator.py に渡す model 名",
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
        help="通常起動で指定する worker 対象 repo root",
    )
    parser.add_argument("--dry-run-codex", action="store_true", help="ready_for_codex でも Codex を起動せず内容だけ確認する")
    parser.add_argument("--next-todo", default="", help="report ベース request に渡す next_todo")
    parser.add_argument("--open-questions", default="", help="report ベース request に渡す open_questions")
    parser.add_argument("--current-status", default="", help="report ベース request に渡す CURRENT_STATUS 上書き")
    parser.add_argument("--ready-issue-ref", default="", help="通常入口で使う current ready issue の参照")
    parser.add_argument("--request-body", default="", help="例外 / recovery / override 用の初回本文")
    parser.add_argument("--entry-script", default="scripts/run_until_stop.py", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def entry_guidance(state: dict[str, Any], args: argparse.Namespace) -> str:
    action = describe_next_action(state)
    if action == "request_next_prompt":
        if getattr(args, "request_body", "").strip():
            return (
                "このあと指定済みの free-form override 本文を使って最初の依頼を送ります。"
                " 通常入口の ready issue 参照は今回だけ使いません。"
            )
        if getattr(args, "ready_issue_ref", "").strip():
            return (
                "このあと指定済みの current ready issue 参照を使って最初の依頼を組み立てます。"
                " free-form 初回本文は override 用にだけ残しています。"
            )
        return (
            "このあと通常は current ready issue の参照を受けて最初の依頼を組み立てます。"
            " ready issue を使えない時だけ free-form override 本文を入力し、bridge は固定の返答契約だけを足します。"
        )
    if action == "request_prompt_from_report" and str(state.get("mode", "")).strip() == "awaiting_user":
        decision = str(state.get("chatgpt_decision", "")).strip()
        if decision == "human_review":
            return "このあと判断結果や方針の補足入力を求め、次の ChatGPT request に添えて送ります。"
        if decision == "need_info":
            return "このあと不足情報の補足入力を求め、次の ChatGPT request に添えて送ります。"
        return "このあと再開用の補足入力を求め、次の ChatGPT request に添えて送ります。"
    if action == "request_prompt_from_report" and str(state.get("pending_handoff_log", "")).strip() and should_rotate_before_next_chat_request(state):
        return "次の ChatGPT request を送る前に、回収済み handoff の composer 入力確認と新チャット送信確認を再試行します。"
    if action == "fetch_next_prompt":
        return f"ChatGPT の返答を待って回収します。Safari fetch 待機の既定値は {args.fetch_timeout_seconds} 秒です。"
    if action == "launch_codex_once":
        return "prompt はそろっています。bridge が Codex worker を 1 回起動します。"
    if action == "wait_for_codex_report":
        return "Codex worker の完了報告を待ちます。"
    if action == "archive_codex_report":
        return "完了報告を archive して次の依頼へ進めます。"
    if action == "request_prompt_from_report":
        return "完了報告をもとに、同じチャットへ次の依頼を送ります。"
    if action == "completed":
        return "追加の操作は不要です。"
    return "summary と doctor を見て次の 1 手を判断してください。"


def print_entry_banner(state: dict[str, Any], args: argparse.Namespace) -> None:
    status = present_bridge_status(state)
    project_path = args.worker_repo_path or "."
    print(f"bridge entry: python3 {args.entry_script}")
    print(f"- project_path: {project_path}")
    print(f"- max_execution_count: {args.max_steps}")
    print(f"- 現在の状況: {status.label}")
    print(f"- このあと: {entry_guidance(state, args)}")


def is_completed_state(state: dict[str, Any]) -> bool:
    mode = str(state.get("mode", "idle"))
    if mode == "completed":
        return True
    return (
        mode == "idle"
        and not bool(state.get("need_chatgpt_prompt"))
        and not bool(state.get("need_chatgpt_next"))
        and not bool(state.get("need_codex_run"))
    )


def is_no_codex_decision_state(state: dict[str, Any]) -> bool:
    return str(state.get("chatgpt_decision", "")).strip() in {"completed", "human_review", "need_info"}


def no_codex_decision_reason(state: dict[str, Any]) -> str:
    decision = str(state.get("chatgpt_decision", "")).strip()
    if decision == "completed":
        return "ChatGPT が Codex 不要の完了判断を返したため、正常停止しました。"
    if decision in {"human_review", "need_info"}:
        return "ChatGPT が Codex 不要の人確認判断を返したため、自動継続せず停止しました。"
    return "ChatGPT が Codex 不要と判断したため停止しました。"


def state_signature(state: dict[str, Any]) -> tuple[Any, ...]:
    return (
        state.get("mode"),
        bool(state.get("need_chatgpt_prompt")),
        bool(state.get("need_chatgpt_next")),
        bool(state.get("need_codex_run")),
        bool(state.get("pause")),
        bool(state.get("error")),
        str(state.get("last_prompt_file", "")),
        str(state.get("last_report_file", "")),
        int(state.get("cycle", 0)),
    )


def describe_next_action(state: dict[str, Any]) -> str:
    if should_prioritize_unarchived_report(state):
        return "archive_codex_report"
    preferred_action, _ = resolve_issue_centric_preferred_loop_action(state)
    if preferred_action:
        return preferred_action
    mode = str(state.get("mode", "idle"))
    if mode == "idle" and bool(state.get("need_chatgpt_prompt")):
        return "request_next_prompt"
    if mode in {"waiting_prompt_reply", "extended_wait", "await_late_completion"}:
        return "fetch_next_prompt"
    if mode == "awaiting_user" and str(state.get("chatgpt_decision", "")).strip() in {"human_review", "need_info"}:
        return "request_prompt_from_report"
    if mode == "ready_for_codex" and bool(state.get("need_codex_run")):
        return "launch_codex_once"
    if mode == "codex_running":
        return "wait_for_codex_report"
    if mode == "codex_done":
        return "archive_codex_report"
    if mode == "idle" and bool(state.get("need_chatgpt_next")):
        return "request_prompt_from_report"
    if is_completed_state(state):
        return "completed"
    return "no_action"


def build_orchestrator_command(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "scripts/bridge_orchestrator.py"]
    command.extend(["--codex-bin", args.codex_bin])
    command.extend(["--codex-timeout-seconds", str(args.codex_timeout_seconds)])
    if args.fetch_timeout_seconds > 0:
        command.extend(["--fetch-timeout-seconds", str(args.fetch_timeout_seconds)])
    if args.worker_repo_path:
        command.extend(["--project-path", args.worker_repo_path])
    if args.codex_model:
        command.extend(["--codex-model", args.codex_model])
    if args.dry_run_codex:
        command.append("--dry-run-codex")
    if args.next_todo:
        command.extend(["--next-todo", args.next_todo])
    if args.open_questions:
        command.extend(["--open-questions", args.open_questions])
    if args.current_status:
        command.extend(["--current-status", args.current_status])
    if args.ready_issue_ref:
        command.extend(["--ready-issue-ref", args.ready_issue_ref])
    if args.request_body:
        command.extend(["--request-body", args.request_body])
    return command


def format_runner_command(args: argparse.Namespace) -> str:
    command = ["python3", args.entry_script, "--max-execution-count", str(args.max_steps)]
    if args.worker_repo_path:
        command.extend(["--project-path", str(args.worker_repo_path)])
    if args.ready_issue_ref:
        command.extend(["--ready-issue-ref", str(args.ready_issue_ref)])
    if args.request_body:
        command.extend(["--request-body", str(args.request_body)])
    if args.stop_at_cycle_boundary:
        command.append("--stop-at-cycle-boundary")
    if args.sleep_seconds != DEFAULT_SLEEP_SECONDS:
        command.extend(["--sleep-seconds", str(args.sleep_seconds)])
    if args.heartbeat_seconds != DEFAULT_HEARTBEAT_SECONDS:
        command.extend(["--heartbeat-seconds", str(args.heartbeat_seconds)])
    if args.fetch_timeout_seconds > 0:
        command.extend(["--fetch-timeout-seconds", str(args.fetch_timeout_seconds)])
    if args.codex_running_wait_seconds != DEFAULT_CODEX_RUNNING_WAIT_SECONDS:
        command.extend(["--codex-running-wait-seconds", str(args.codex_running_wait_seconds)])
    if args.codex_running_poll_seconds != DEFAULT_CODEX_RUNNING_POLL_SECONDS:
        command.extend(["--codex-running-poll-seconds", str(args.codex_running_poll_seconds)])
    if args.dry_run_codex:
        command.append("--dry-run-codex")
    return " ".join(command)


def format_start_bridge_command(args: argparse.Namespace, mode: str = "run") -> str:
    command = ["python3", "scripts/start_bridge.py"]
    if args.worker_repo_path:
        command.extend(["--project-path", str(args.worker_repo_path)])
    if getattr(args, "ready_issue_ref", "").strip():
        command.extend(["--ready-issue-ref", str(args.ready_issue_ref)])
    command.extend(["--max-execution-count", str(args.max_steps)])
    if mode == "status":
        command.append("--status")
    elif mode == "resume":
        command.append("--resume")
    elif mode == "doctor":
        command.append("--doctor")
    elif mode == "clear-error":
        command.append("--clear-error")
    return " ".join(command)


def recommended_operator_step(
    args: argparse.Namespace,
    final_state: dict[str, Any],
    *,
    reason: str = "",
) -> tuple[str, str]:
    action = describe_next_action(final_state)
    stale_codex_running = is_stale_codex_running_candidate(reason, final_state)

    if runtime_stop_path().exists():
        return ("まず状況確認", format_start_bridge_command(args, mode="doctor"))
    if bool(final_state.get("pause")):
        return ("まず状況確認", format_start_bridge_command(args, mode="doctor"))
    if bool(final_state.get("error")):
        return ("停止要因を整理して再開", format_start_bridge_command(args, mode="clear-error"))
    if has_unarchived_report_conflict(final_state) or stale_codex_running:
        return ("まず状況確認", format_start_bridge_command(args, mode="doctor"))
    if action in {"completed", "no_action"}:
        return ("追加操作なし", "なし")
    if action == "request_next_prompt":
        if getattr(args, "request_body", "").strip():
            return ("override 入力で開始", format_start_bridge_command(args, mode="run"))
        return ("ready issue 参照で開始", format_start_bridge_command(args, mode="run"))
    if action == "request_prompt_from_report" and str(final_state.get("mode", "")).strip() == "awaiting_user":
        return ("補足を入れて再開", format_start_bridge_command(args, mode="resume"))
    if str(final_state.get("pending_handoff_log", "")).strip() and should_rotate_before_next_chat_request(final_state):
        return ("handoff 再送を再試行", format_start_bridge_command(args, mode="resume"))
    return ("そのまま再開", format_start_bridge_command(args, mode="resume"))


def suggested_next_command(args: argparse.Namespace, final_state: dict[str, Any]) -> str:
    return recommended_operator_step(args, final_state)[1]


def suggested_next_note(final_state: dict[str, Any]) -> str:
    action = describe_next_action(final_state)
    pending_request_signal = str(final_state.get("pending_request_signal", "")).strip()
    if is_no_codex_decision_state(final_state):
        note = str(final_state.get("chatgpt_decision_note", "")).strip()
        decision = str(final_state.get("chatgpt_decision", "")).strip()
        if decision == "completed":
            if note:
                return note
            return "ChatGPT が完了判断を返したため、追加の Codex 実行は不要です。"
        if decision == "human_review":
            base = note or "ChatGPT が人判断待ちと判断しました。"
            return f"{base} bridge を再実行すると判断結果の補足入力を受けて次 request を送ります。"
        if decision == "need_info":
            base = note or "ChatGPT が情報不足と判断しました。"
            return f"{base} bridge を再実行すると不足情報の補足入力を受けて次 request を送ります。"
        return "ChatGPT が Codex 不要と判断しました。人が次の判断を行ってください。"
    if action == "request_next_prompt":
        return (
            "Safari の current tab を対象チャットに合わせたまま再実行してください。"
            " 通常は current ready issue の参照で始め、ready issue を使えない時だけ free-form override を入力します。"
        )
    if action == "fetch_next_prompt":
        route_note = issue_centric_route_note(final_state)
        if route_note and "reply 待ち" in route_note:
            return route_note.strip()
        if pending_request_signal == "submitted_unconfirmed":
            return (
                "新しいチャットへの送信は通った可能性が高いため、"
                "同じ handoff は再送せず reply を待ってから再実行してください。"
            )
        return "CHATGPT_PROMPT_REPLY が同じ current tab に出たら再実行してください。"
    if action == "launch_codex_once":
        return (
            "bridge が worker を起動できる状態です。初回導線へ戻らず、"
            "summary の suggested_next_command をそのまま再実行してください。"
            " prompt と current tab も確認してください。"
        )
    if action == "wait_for_codex_report":
        return "Codex worker が bridge/outbox/codex_report.md を書いたら再実行してください。"
    if action == "archive_codex_report":
        return "report はそろっているので、archive と次 request へ進めるため再実行してください。"
    if action == "request_prompt_from_report":
        if str(final_state.get("pending_handoff_log", "")).strip() and should_rotate_before_next_chat_request(final_state):
            base = (
                "次の ChatGPT request を送る前に使う handoff は回収済みですが、まだ新チャットへ送れていません。"
                " project ページの composer と送信可否を確認したまま再実行してください。"
            )
            route_note = issue_centric_route_note(final_state)
            return f"{base}{route_note}"
        base = "Safari の current tab を対象チャットに合わせたまま再実行してください。"
        route_note = issue_centric_route_note(final_state)
        return f"{base}{route_note}"
    if action == "completed":
        return "追加の操作は不要です。"
    if action == "no_action":
        return "summary と doctor を確認し、必要なら原因を解消してから再開してください。"
    return "summary と doctor を確認してから再実行してください。"


def issue_centric_route_note(final_state: dict[str, Any]) -> str:
    runtime_mode, _ = prepare_issue_centric_runtime_mode(final_state)
    if runtime_mode is None:
        return ""
    recovery_status = runtime_mode.recovery_status
    recovery_source = runtime_mode.recovery_source
    route_selected = runtime_mode.route_selected
    target_issue = runtime_mode.target_issue
    fallback_reason = runtime_mode.fallback_reason or runtime_mode.runtime_mode_reason
    generation_lifecycle = runtime_mode.generation_lifecycle
    generation_lifecycle_reason = runtime_mode.generation_lifecycle_reason
    freshness_status = runtime_mode.freshness_status
    freshness_reason = runtime_mode.freshness_reason or fallback_reason
    if runtime_mode.runtime_mode == "issue_centric_unavailable":
        return (
            " issue-centric runtime は今回 unavailable のため、legacy fallback で続行します。"
            f" 理由: {fallback_reason or 'issue-centric runtime unavailable'}."
        )
    if freshness_status == "issue_centric_invalidated":
        if target_issue:
            return (
                " issue-centric runtime は invalidated のため、legacy fallback で "
                f"{target_issue} を target_issue 候補として扱います。"
                f" 理由: {freshness_reason or 'issue-centric context invalidated'}."
            )
        return (
            " issue-centric runtime は invalidated のため、legacy fallback で続行します。"
            f" 理由: {freshness_reason or 'issue-centric context invalidated'}."
        )
    if freshness_status == "issue_centric_stale":
        if target_issue:
            return (
                " issue-centric runtime は stale fallback のため、legacy fallback で "
                f"{target_issue} を target_issue 候補として扱います。"
                f" 理由: {freshness_reason or 'issue-centric context is stale'}."
            )
        return (
            " issue-centric runtime は stale fallback のため、legacy fallback で続行します。"
            f" 理由: {freshness_reason or 'issue-centric context is stale'}."
        )
    if recovery_status == "issue_centric_recovered" and route_selected == "issue_centric" and target_issue:
        source_note = f" ({recovery_source})" if recovery_source else ""
        return (
            " 保存済みの issue-centric summary から再構築した文脈を使い、"
            f"{target_issue} を target_issue として継続します{source_note}。"
        )
    if route_selected == "issue_centric" and target_issue and generation_lifecycle == "fresh_pending":
        return (
            " issue-centric request は送信済みで reply 待ちです。"
            f" {target_issue} を target_issue とする generation を pending のまま継続します。"
            f" 理由: {generation_lifecycle_reason or 'pending request bound to generation'}."
        )
    if route_selected == "issue_centric" and target_issue and generation_lifecycle == "fresh_prepared":
        return (
            " issue-centric request は prepared 状態です。"
            f" {target_issue} を target_issue とする generation を再利用できます。"
            f" 理由: {generation_lifecycle_reason or 'prepared request bound to generation'}."
        )
    if runtime_mode.runtime_mode == "issue_centric_degraded_fallback":
        if target_issue:
            return (
                " issue-centric runtime は degraded fallback のため、legacy fallback で "
                f"{target_issue} を target_issue 候補として扱います。"
                f" 理由: {fallback_reason or 'issue-centric degraded fallback'}."
            )
        return (
            " issue-centric runtime は degraded fallback のため、legacy fallback で続行します。"
            f" 理由: {fallback_reason or 'issue-centric degraded fallback'}."
        )
    if recovery_status == "issue_centric_recovery_fallback":
        if target_issue:
            return (
                " issue-centric recovery は今回使えず、legacy fallback で "
                f"{target_issue} を target_issue 候補として扱います。"
                f" 理由: {fallback_reason or 'issue-centric recovery fallback'}."
            )
        return (
            " issue-centric recovery は今回使えず、legacy fallback で続行します。"
            f" 理由: {fallback_reason or 'issue-centric recovery fallback'}."
        )
    fallback_reason = (
        str(final_state.get("last_issue_centric_route_fallback_reason", "")).strip()
        or str(final_state.get("last_issue_centric_next_request_fallback_reason", "")).strip()
    )
    if route_selected == "issue_centric" and target_issue:
        return f" 次回 request は issue-centric route を優先し、{target_issue} を target_issue として扱います。"
    if route_selected == "fallback_legacy":
        if target_issue:
            return (
                " issue-centric route は今回使わず、legacy fallback で "
                f"{target_issue} を target_issue 候補として扱います。"
                f" 理由: {fallback_reason or 'route selection fallback'}."
            )
        return (
            " issue-centric route は今回使わず、legacy fallback で続行します。"
            f" 理由: {fallback_reason or 'route selection fallback'}."
        )
    return ""


def blocked_next_guidance(final_state: dict[str, Any]) -> tuple[str, str] | None:
    if runtime_stop_path().exists():
        return (
            "なし",
            "bridge/STOP があるため停止中です。意図した停止か確認し、続けるなら STOP を外してから再実行してください。",
        )

    if bool(final_state.get("pause")):
        return (
            "なし",
            "手動 pause 中です。pause を解除してから再実行してください。",
        )

    if bool(final_state.get("error")):
        error_message = str(final_state.get("error_message", "")).strip()
        pending_handoff_log = (
            str(final_state.get("pending_handoff_log", "")).strip() if should_rotate_before_next_chat_request(final_state) else ""
        )
        if should_prioritize_unarchived_report(final_state):
            note = (
                "bridge/outbox/codex_report.md に未退避 report が残っています。"
                " handoff 再送より先に、その report を archive して ChatGPT 返送導線へ戻してください。"
            )
        elif is_apple_event_timeout_text(error_message):
            note = (
                "Safari timeout が起きています。"
                f" {safari_timeout_checklist_text()} reply が見えてから error を clear して再実行してください。"
            )
        elif pending_handoff_log:
            note = (
                "次の ChatGPT request を送る前に使う handoff は回収済みですが、まだ新チャットへ送れていません。"
                " project ページと『＜project名＞ 内の新しいチャット』入力欄を確認し、"
                " error を clear して再実行すると同じ handoff で入力確認と送信確認を再試行します。"
                f" handoff_log: {pending_handoff_log}"
            )
        elif str(final_state.get("pending_request_signal", "")).strip() == "submitted_unconfirmed":
            note = (
                "新しいチャットへの送信は通った可能性が高いため、"
                " clear-error や handoff 再送へ戻らず reply 回収側を優先してください。"
            )
        else:
            note = "bridge 側の停止要因を解消し、error を clear してから再実行してください。"
        if error_message:
            note += f" 技術メモ: {error_message}"
        return ("なし", note)

    if has_unarchived_report_conflict(final_state):
        return (
            "なし",
            "前回 report を archive するか、live 開始前の runtime に戻してから再実行してください。",
        )

    return None


def is_stale_codex_running_candidate(reason: str, final_state: dict[str, Any]) -> bool:
    if describe_next_action(final_state) != "wait_for_codex_report":
        return False

    if runtime_stop_path().exists():
        return False

    if bool(final_state.get("pause")) or bool(final_state.get("error")):
        return False

    if codex_report_is_ready(runtime_report_path()):
        return False

    normalized_reason = reason.strip()
    return normalized_reason.startswith("Codex report 待ちの上限に達したため停止しました。") or normalized_reason.startswith(
        "Codex report 待ちのため停止しました。"
    )


def stale_codex_running_note() -> str:
    return (
        "state=codex_running のまま report が無いため、実 worker 継続中か stale runtime かを先に確認してください。"
        " bridge/outbox/codex_report.md、state.error、state.pause、bridge/STOP を見て、"
        "どれも無く worker も動いていないなら stale です。"
        " 同じ prompt をやり直すなら ready_for_codex + need_codex_run=true、"
        "最初から request をやり直すなら idle + need_chatgpt_prompt=true に戻してから再実行してください。"
    )


def has_unarchived_report_conflict(state: dict[str, Any]) -> bool:
    if not codex_report_is_ready(runtime_report_path()):
        return False
    if should_prioritize_unarchived_report(state):
        return False

    mode = str(state.get("mode", "idle"))
    if mode in {"codex_done", "codex_running"}:
        return False
    if mode == "idle" and bool(state.get("need_chatgpt_next")):
        return False
    return True


def describe_wait_message(action: str) -> str:
    if action == "fetch_next_prompt":
        return "ChatGPT reply を待っています。"
    if action == "launch_codex_once":
        return "Codex worker の完了を待っています。"
    if action == "wait_for_codex_report":
        return "Codex report を待っています。"
    if action == "request_next_prompt":
        return "ChatGPT request の送信完了を待っています。"
    if action == "request_prompt_from_report":
        return "次の ChatGPT request の送信処理を進めています。"
    return "bridge の処理完了を待っています。"


def should_include_codex_progress(final_state: dict[str, Any], history: list[str]) -> bool:
    mode = str(final_state.get("mode", "")).strip()
    if mode in {"ready_for_codex", "codex_running", "codex_done"}:
        return True
    return any("action=launch_codex_once" in entry or "action=wait_for_codex_report" in entry for entry in history)


def handoff_report_reference(final_state: dict[str, Any]) -> str:
    outbox_report = runtime_report_path()
    if codex_report_is_ready(outbox_report):
        return repo_relative(outbox_report)

    last_report_file = str(final_state.get("last_report_file", "")).strip()
    if not last_report_file:
        return ""

    candidate = Path(last_report_file).expanduser()
    if not candidate.is_absolute():
        candidate = (bridge_runtime_root() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if candidate.exists():
        return repo_relative(candidate)
    return last_report_file


def promote_report_ready_state(state: dict[str, Any]) -> dict[str, Any]:
    state, _ = recover_report_ready_state(state, prompt_path=runtime_prompt_path())
    state, _ = recover_prepared_request_state(state)
    return state


def format_elapsed(seconds: float) -> str:
    whole_seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(whole_seconds, 60)
    if minutes:
        return f"{minutes}m{remaining_seconds:02d}s"
    return f"{remaining_seconds}s"


def fetch_retry_diagnostics(history: list[str]) -> tuple[int, int]:
    timeout_count = 0
    recovery_count = 0
    for entry in history:
        timeout_count += entry.count("[retry] fetch_next_prompt で Safari timeout を検知しました。")
        recovery_count += entry.count("[retry] fetch_next_prompt は Safari timeout 後の再試行で回復しました。")
    return timeout_count, recovery_count


def wait_for_codex_report(
    *,
    args: argparse.Namespace,
    history: list[str],
) -> bool:
    report_path = runtime_report_path()
    if codex_report_is_ready(report_path):
        history.append("- codex report: すでに生成済みです")
        return True

    max_wait_seconds = float(args.codex_running_wait_seconds)
    if max_wait_seconds <= 0:
        history.append("- codex report: 待機設定が 0 秒のため即停止します")
        return False

    poll_seconds = max(0.2, float(args.codex_running_poll_seconds))
    heartbeat_seconds = float(args.heartbeat_seconds)
    prompt_path = runtime_prompt_path()
    prompt_mtime = prompt_path.stat().st_mtime if prompt_path.exists() else None
    started_at = time.time()
    deadline = started_at + max_wait_seconds
    next_heartbeat_at = started_at + heartbeat_seconds if heartbeat_seconds > 0 else None
    last_codex_progress_line = ""

    while time.time() < deadline:
        current_state = promote_report_ready_state(load_state())
        check_stop_conditions(current_state)
        recovered_report = recover_codex_report(
            report_path,
            search_recent_logs=True,
            newer_than=prompt_mtime,
        )
        if codex_report_is_ready(report_path):
            waited = time.time() - started_at
            if recovered_report is not None:
                history.append(
                    f"- codex report: {format_elapsed(waited)} 待機して {repo_relative(recovered_report)} から回収しました"
                )
            else:
                history.append(f"- codex report: {format_elapsed(waited)} 待機して生成を検出しました")
            return True

        now = time.time()
        snapshot = latest_codex_progress_snapshot(since=prompt_mtime)
        if snapshot is not None and snapshot.progress_line != last_codex_progress_line:
            print(f"[codex] {snapshot.progress_line}")
            last_codex_progress_line = snapshot.progress_line
        if next_heartbeat_at is not None and now >= next_heartbeat_at:
            status = present_bridge_status(current_state)
            print(
                f"[wait] status={status.label} action=wait_for_codex_report elapsed={format_elapsed(now - started_at)} "
                f"{describe_wait_message('wait_for_codex_report')}"
            )
            next_heartbeat_at = now + heartbeat_seconds

        remaining = max(0.0, deadline - time.time())
        if remaining <= 0:
            break
        time.sleep(min(poll_seconds, remaining))

    history.append(
        f"- codex report: {format_elapsed(max_wait_seconds)} 待機しても未生成でした"
    )
    return codex_report_is_ready(report_path)


def run_command_with_heartbeat(
    command: list[str],
    *,
    cwd: str | os.PathLike[str],
    env: dict[str, str],
    action: str,
    status_label: str,
    heartbeat_seconds: float,
    interactive: bool = False,
) -> tuple[subprocess.CompletedProcess[str], float]:
    started_at = time.time()
    last_codex_progress_line = ""
    if interactive:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            text=True,
        )
        return (
            subprocess.CompletedProcess(
                command,
                completed.returncode,
                "",
                "",
            ),
            time.time() - started_at,
        )

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
    )
    next_heartbeat_at = started_at + heartbeat_seconds if heartbeat_seconds > 0 else None

    try:
        while True:
            returncode = process.poll()
            if returncode is not None:
                stdout_text, stderr_text = process.communicate()
                completed = subprocess.CompletedProcess(
                    command,
                    returncode,
                    stdout_text,
                    stderr_text,
                )
                return completed, time.time() - started_at

            now = time.time()
            if action == "launch_codex_once":
                snapshot = latest_codex_progress_snapshot(since=started_at)
                if snapshot is not None and snapshot.progress_line != last_codex_progress_line:
                    print(f"[codex] {snapshot.progress_line}")
                    last_codex_progress_line = snapshot.progress_line
            if next_heartbeat_at is not None and now >= next_heartbeat_at:
                wait_suffix = ""
                if action == "fetch_next_prompt":
                    current_mode = str(load_state().get("mode", "")).strip()
                    if current_mode == "extended_wait":
                        wait_suffix = " stage=extended_wait"
                    elif current_mode == "await_late_completion":
                        wait_suffix = " stage=late_completion_mode"
                print(
                    f"[wait] status={status_label} action={action} elapsed={format_elapsed(now - started_at)} "
                    f"{describe_wait_message(action)}{wait_suffix}"
                )
                next_heartbeat_at = now + heartbeat_seconds

            time.sleep(0.5)
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        raise


def summarize_run(
    *,
    args: argparse.Namespace,
    reason: str,
    steps: int,
    warnings: list[str],
    initial_state: dict[str, Any],
    final_state: dict[str, Any],
    history: list[str],
    suggested_next_command_override: str | None = None,
    suggested_next_note_override: str | None = None,
    cycle_boundary_stop: bool = False,
) -> str:
    blocked_guidance = blocked_next_guidance(final_state)
    stale_codex_running = is_stale_codex_running_candidate(reason, final_state)
    fetch_retry_timeouts, fetch_retry_recoveries = fetch_retry_diagnostics(history)
    safari_timeout_blocked = bool(final_state.get("error")) and is_apple_event_timeout_text(
        str(final_state.get("error_message", "")).strip()
    )
    suggested_command = suggested_next_command_override
    suggested_note = suggested_next_note_override

    if suggested_command is None and blocked_guidance is not None:
        suggested_command = blocked_guidance[0]
    if suggested_note is None and blocked_guidance is not None:
        suggested_note = blocked_guidance[1]
    if suggested_command is None and stale_codex_running:
        suggested_command = "なし"
    if suggested_note is None and stale_codex_running:
        suggested_note = stale_codex_running_note()
    if suggested_command is None:
        suggested_command = suggested_next_command(args, final_state)
    if suggested_note is None:
        suggested_note = suggested_next_note(final_state)
    recommendation_label, recommended_command = recommended_operator_step(args, final_state, reason=reason)
    if suggested_command == "なし" and recommended_command != "なし":
        suggested_command = recommended_command
    handoff = present_bridge_handoff(
        final_state,
        reason=reason,
        suggested_note=suggested_note,
        blocked=bool(blocked_guidance),
        stale_codex_running=stale_codex_running,
        cycle_boundary_stop=cycle_boundary_stop,
    )
    initial_status = present_bridge_status(initial_state)
    final_status = present_bridge_status(
        final_state,
        blocked=bool(blocked_guidance),
        stale_codex_running=stale_codex_running,
    )
    report_reference = handoff_report_reference(final_state)
    codex_snapshot = latest_codex_progress_snapshot() if should_include_codex_progress(final_state, history) else None
    lines = [
        "# Run Until Stop Summary",
        "",
        "## next_step",
        f"- 現在の状況: {final_status.label}",
        f"- 停止時の案内: {handoff.title}",
        f"- おすすめの動き: {recommendation_label}",
        f"- おすすめ 1 コマンド: {recommended_command}",
        f"- 次に見るもの: {handoff.detail}",
        f"- 次の操作: {suggested_command}",
        f"- 補足: {suggested_note}",
        "",
        "## run",
        f"- initial_user_status: {initial_status.label}",
        f"- steps: {steps}",
        f"- max_steps: {args.max_steps}",
        f"- sleep_seconds: {args.sleep_seconds}",
        f"- dry_run: {args.dry_run}",
        f"- heartbeat_seconds: {args.heartbeat_seconds}",
        f"- fetch_timeout_seconds: {args.fetch_timeout_seconds}",
        f"- codex_running_wait_seconds: {args.codex_running_wait_seconds}",
        f"- codex_running_poll_seconds: {args.codex_running_poll_seconds}",
        f"- warnings: {len(warnings)}",
        f"- blocked: {bool(blocked_guidance)}",
        f"- cycle_boundary_stop: {cycle_boundary_stop}",
        f"- stale_codex_running_candidate: {stale_codex_running}",
        f"- fetch_retry_timeouts: {fetch_retry_timeouts}",
        f"- fetch_retry_recoveries: {fetch_retry_recoveries}",
        f"- safari_timeout_blocked: {safari_timeout_blocked}",
        f"- next_action: {describe_next_action(final_state)}",
        f"- report_reference: {report_reference}",
        "",
        "## debug",
        f"- technical_reason: {reason}",
        f"- final_user_status_detail: {final_status.detail}",
        "",
    ]
    if codex_snapshot is not None:
        lines.extend(
            [
                "## codex_progress",
                f"- status: {codex_snapshot.status}",
                f"- last_message: {codex_snapshot.excerpt or 'なし'}",
                f"- last_message_path: {codex_snapshot.last_message_path}",
                f"- stdout_log_path: {codex_snapshot.stdout_log_path}",
                f"- stdout_tail: {codex_snapshot.stdout_tail or 'なし'}",
                f"- stderr_log_path: {codex_snapshot.stderr_log_path}",
                f"- stderr_tail: {codex_snapshot.stderr_tail or 'なし'}",
                "",
            ]
        )
    if warnings:
        lines.extend(
            [
                "## warnings",
                *[f"- {message}" for message in warnings],
                "",
            ]
        )
    lines.extend(
        [
        "## initial_state",
        state_snapshot(initial_state),
        "",
        "## final_state",
        state_snapshot(final_state),
        "",
        "## history",
        ]
    )
    if history:
        lines.extend(history)
    else:
        lines.append("- no steps executed")
    return "\n".join(lines).strip() + "\n"


def finish(
    *,
    args: argparse.Namespace,
    reason: str,
    steps: int,
    warnings: list[str],
    initial_state: dict[str, Any],
    final_state: dict[str, Any],
    history: list[str],
    suggested_next_command_override: str | None = None,
    suggested_next_note_override: str | None = None,
    cycle_boundary_stop: bool = False,
    exit_code: int = 0,
) -> int:
    blocked_guidance = blocked_next_guidance(final_state)
    stale_codex_running = is_stale_codex_running_candidate(reason, final_state)
    if suggested_next_command_override is None and blocked_guidance is not None:
        suggested_next_command_override = blocked_guidance[0]
    if suggested_next_note_override is None and blocked_guidance is not None:
        suggested_next_note_override = blocked_guidance[1]
    if suggested_next_command_override is None and stale_codex_running:
        suggested_next_command_override = "なし"
    if suggested_next_note_override is None and stale_codex_running:
        suggested_next_note_override = stale_codex_running_note()
    if suggested_next_command_override is None:
        suggested_next_command_override = suggested_next_command(args, final_state)
    if suggested_next_note_override is None:
        suggested_next_note_override = suggested_next_note(final_state)
    recommendation_label, recommended_command = recommended_operator_step(args, final_state, reason=reason)
    if suggested_next_command_override == "なし" and recommended_command != "なし":
        suggested_next_command_override = recommended_command

    summary = summarize_run(
        args=args,
        reason=reason,
        steps=steps,
        warnings=warnings,
        initial_state=initial_state,
        final_state=final_state,
        history=history,
        suggested_next_command_override=suggested_next_command_override,
        suggested_next_note_override=suggested_next_note_override,
        cycle_boundary_stop=cycle_boundary_stop,
    )
    log_path = log_text("run_until_stop_summary", summary)
    handoff = present_bridge_handoff(
        final_state,
        reason=reason,
        suggested_note=suggested_next_note_override,
        blocked=bool(blocked_guidance),
        stale_codex_running=stale_codex_running,
        cycle_boundary_stop=cycle_boundary_stop,
    )
    report_reference = handoff_report_reference(final_state)
    codex_snapshot = latest_codex_progress_snapshot() if should_include_codex_progress(final_state, history) else None
    print(summary.rstrip())
    print(f"log: {log_path}")
    print(f"next step: {handoff.title}")
    print(f"- recommended_action: {recommendation_label}")
    print(f"- recommended_command: {recommended_command}")
    print(f"- note: {handoff.detail}")
    print(f"- summary: {repo_relative(log_path)}")
    print(f"- suggested_next_command: {suggested_next_command_override}")
    if report_reference:
        print(f"- report: {report_reference}")
    if codex_snapshot is not None:
        print(f"- codex_last_status: {codex_snapshot.status}")
        if codex_snapshot.excerpt:
            print(f"- codex_last_message: {codex_snapshot.excerpt}")
        if codex_snapshot.stdout_tail:
            print(f"- codex_stdout_tail: {codex_snapshot.stdout_tail}")
        if codex_snapshot.stderr_tail:
            print(f"- codex_stderr_tail: {codex_snapshot.stderr_tail}")
    return exit_code


def run(argv: list[str] | None = None) -> int:
    configure_output_streams()
    project_config = load_project_config()
    args = parse_args(argv, project_config)
    warnings = project_config_warnings(project_config)
    print_project_config_warnings(project_config)
    history: list[str] = []
    steps = 0
    prompt_path = runtime_prompt_path()
    initial_state, recovered_report = recover_report_ready_state(load_state(), prompt_path=prompt_path)
    initial_state, recovered_prepared = recover_prepared_request_state(initial_state)
    initial_state, recovered_handoff = recover_pending_handoff_state(initial_state)
    start_cycle = int(initial_state.get("cycle", 0))
    if recovered_report is not None:
        history.append(f"- preflight: fallback report を {repo_relative(recovered_report)} から回収しました")
    if recovered_prepared:
        history.append("- preflight: prepared request を再送可能な状態へ復旧しました")
    if recovered_handoff:
        history.append("- preflight: 回収済み handoff を再利用できる状態へ復旧しました")
    print_entry_banner(initial_state, args)

    try:
        check_stop_conditions(initial_state)
    except BridgeStop as exc:
        final_state = load_state()
        return finish(
            args=args,
            reason=str(exc),
            steps=steps,
            warnings=warnings,
            initial_state=initial_state,
            final_state=final_state,
            history=history,
        )

    if is_completed_state(initial_state):
        reason = "completed 相当の状態です。追加の 1 手はありません。"
        if is_no_codex_decision_state(initial_state):
            reason = no_codex_decision_reason(initial_state)
        return finish(
            args=args,
            reason=reason,
            steps=steps,
            warnings=warnings,
            initial_state=initial_state,
            final_state=initial_state,
            history=history,
        )

    if has_unarchived_report_conflict(initial_state):
        history.append("- preflight: bridge/outbox/codex_report.md に未退避 report が残っていました")
        return finish(
            args=args,
            reason=(
                "bridge/outbox/codex_report.md に未退避の完了報告が残っているため、"
                "連続運転を開始しませんでした。"
            ),
            steps=steps,
            warnings=warnings,
            initial_state=initial_state,
            final_state=initial_state,
            history=history,
            suggested_next_note_override=(
                "前回 report を archive するか、live 開始前の runtime に戻してから再実行してください。"
            ),
        )

    if args.dry_run:
        history.append(f"- dry_run next_action: {describe_next_action(initial_state)}")
        return finish(
            args=args,
            reason="dry-run のため実行せず停止しました。",
            steps=steps,
            warnings=warnings,
            initial_state=initial_state,
            final_state=initial_state,
            history=history,
        )

    command = build_orchestrator_command(args)
    child_env = dict(os.environ)
    child_env["BRIDGE_SUPPRESS_PROJECT_WARNINGS"] = "1"
    child_env["BRIDGE_SUPPRESS_CODEX_PROGRESS"] = "1"

    try:
        while steps < args.max_steps:
            before = promote_report_ready_state(load_state())
            before, recovered_handoff = recover_pending_handoff_state(before)
            if recovered_handoff:
                history.append("- preflight: 回収済み handoff を再利用できる状態へ復旧しました")
            try:
                check_stop_conditions(before)
            except BridgeStop as exc:
                final_state = load_state()
                return finish(
                    args=args,
                    reason=str(exc),
                    steps=steps,
                    warnings=warnings,
                    initial_state=initial_state,
                    final_state=final_state,
                    history=history,
                )

            if is_completed_state(before):
                reason = "completed 相当の状態に到達しました。"
                if is_no_codex_decision_state(before):
                    reason = no_codex_decision_reason(before)
                return finish(
                    args=args,
                    reason=reason,
                    steps=steps,
                    warnings=warnings,
                    initial_state=initial_state,
                    final_state=before,
                    history=history,
                )

            action = describe_next_action(before)
            if action == "no_action":
                history.append(
                    f"- step {steps + 1}: no_action / status={present_bridge_status(before, blocked=True).label}"
                )
                reason = "bridge_orchestrator.py で進める次の 1 手が見つかりませんでした。"
                if is_no_codex_decision_state(before):
                    reason = no_codex_decision_reason(before)
                return finish(
                    args=args,
                    reason=reason,
                    steps=steps,
                    warnings=warnings,
                    initial_state=initial_state,
                    final_state=before,
                    history=history,
                )

            if action == "wait_for_codex_report":
                before_status = present_bridge_status(before)
                print(
                    f"[step {steps + 1}] status={before_status.label} action={action} "
                    f"(max_wait={args.codex_running_wait_seconds}s)"
                )
                if not wait_for_codex_report(args=args, history=history):
                    final_state = load_state()
                    return finish(
                        args=args,
                        reason=(
                            "Codex report 待ちの上限に達したため停止しました。"
                            " bridge/outbox/codex_report.md が生成されたら再実行してください。"
                        ),
                        steps=steps,
                        warnings=warnings,
                        initial_state=initial_state,
                        final_state=final_state,
                        history=history,
                    )

            before_status = present_bridge_status(before)
            print(f"[step {steps + 1}] status={before_status.label} action={action}")
            interactive = action == "request_next_prompt" or (
                action == "request_prompt_from_report" and str(before.get("mode", "")).strip() == "awaiting_user"
            )
            result, elapsed_seconds = run_command_with_heartbeat(
                command,
                cwd=bridge_runtime_root(),
                env=child_env,
                action=action,
                status_label=before_status.label,
                heartbeat_seconds=float(args.heartbeat_seconds),
                interactive=interactive,
            )
            steps += 1
            after = load_state()
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            after_status = present_bridge_status(after)
            history.append(
                f"- step {steps}: status={before_status.label}->{after_status.label} action={action} rc={result.returncode} "
                f"before={before.get('mode', '')} after={after.get('mode', '')}"
            )
            if interactive:
                history.append("  interactive: 初回 request 本文を terminal で受け取り、bridge が固定の返答契約を追記して送信した")
            if elapsed_seconds >= max(1.0, float(args.heartbeat_seconds)):
                history.append(
                    f"  waited: action={action} elapsed={format_elapsed(elapsed_seconds)}"
                )
            if stdout:
                history.append(f"  stdout: {stdout.replace(chr(10), ' / ')}")
                print(stdout)
            if stderr:
                history.append(f"  stderr: {stderr.replace(chr(10), ' / ')}")
                print(stderr, file=sys.stderr)

            if result.returncode != 0:
                return finish(
                    args=args,
                    reason=f"bridge_orchestrator.py が rc={result.returncode} で停止しました。",
                    steps=steps,
                    warnings=warnings,
                    initial_state=initial_state,
                    final_state=after,
                    history=history,
                    exit_code=result.returncode,
                )

            if args.stop_at_cycle_boundary and int(after.get("cycle", 0)) > start_cycle:
                completed_cycle = int(after.get("cycle", 0))
                history.append(
                    f"- cycle boundary: cycle {completed_cycle} の archive 完了で停止し、次 cycle request は次回実行へ回しました"
                )
                return finish(
                    args=args,
                    reason=f"cycle {completed_cycle} の完了まで進めたため停止しました。",
                    steps=steps,
                    warnings=warnings,
                    initial_state=initial_state,
                    final_state=after,
                    history=history,
                    suggested_next_note_override=(
                        f"この run は cycle {completed_cycle} の完了までで止めました。"
                        " 次 cycle の ChatGPT request は次回実行で進みます。"
                    ),
                    cycle_boundary_stop=True,
                )

            if state_signature(before) == state_signature(after):
                if action == "wait_for_codex_report":
                    reason = (
                        "Codex report 待ちのため停止しました。"
                        " bridge/outbox/codex_report.md が生成されたら再実行してください。"
                    )
                elif action == "request_prompt_from_report" and str(before.get("mode", "")).strip() == "awaiting_user":
                    reason = (
                        "再開用の補足入力が空のため送信せず停止しました。"
                        " 必要な補足を入力して再実行してください。"
                    )
                else:
                    reason = (
                        "state が変化しなかったため停止しました。"
                        f" mode={after.get('mode', '')} next_action={describe_next_action(after)}"
                    )
                return finish(
                    args=args,
                    reason=reason,
                    steps=steps,
                    warnings=warnings,
                    initial_state=initial_state,
                    final_state=after,
                    history=history,
                )

            if args.sleep_seconds > 0 and steps < args.max_steps:
                time.sleep(args.sleep_seconds)

        final_state = load_state()
        return finish(
            args=args,
            reason=f"--max-steps={args.max_steps} に到達したため停止しました。",
            steps=steps,
            warnings=warnings,
            initial_state=initial_state,
            final_state=final_state,
            history=history,
        )

    except KeyboardInterrupt:
        final_state = load_state()
        return finish(
            args=args,
            reason="ユーザー中断で停止しました。",
            steps=steps,
            warnings=warnings,
            initial_state=initial_state,
            final_state=final_state,
            history=history,
            exit_code=130,
        )
    except Exception as exc:
        mark_error(str(exc))
        final_state = load_state()
        history.append(f"- exception: {exc}")
        return finish(
            args=args,
            reason=f"run_until_stop.py 内で例外が発生しました: {exc}",
            steps=steps,
            warnings=warnings,
            initial_state=initial_state,
            final_state=final_state,
            history=history,
            exit_code=1,
        )


if __name__ == "__main__":
    sys.exit(run())
