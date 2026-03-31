#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from typing import Any

from _bridge_common import (
    BridgeStop,
    OUTBOX_DIR,
    ROOT_DIR,
    STOP_PATH,
    browser_fetch_timeout_seconds,
    browser_runner_heartbeat_seconds,
    check_stop_conditions,
    codex_report_is_ready,
    is_apple_event_timeout_text,
    load_browser_config,
    load_state,
    load_project_config,
    log_text,
    mark_error,
    project_config_warnings,
    print_project_config_warnings,
    present_bridge_status,
    safari_timeout_checklist_text,
    state_snapshot,
    worker_repo_path,
)


DEFAULT_MAX_STEPS = 6
DEFAULT_SLEEP_SECONDS = 1.0
DEFAULT_HEARTBEAT_SECONDS = 15.0
DEFAULT_FETCH_TIMEOUT_SECONDS = 0
DEFAULT_CODEX_RUNNING_WAIT_SECONDS = 120.0
DEFAULT_CODEX_RUNNING_POLL_SECONDS = 5.0


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
        description="bridge の通常入口です。--project-path と --max-execution-count を渡して数手まとめて進めます。",
        epilog=(
            "通常起動例: python3 scripts/run_until_stop.py "
            "--project-path /path/to/target-repo --max-execution-count 6"
        ),
    )
    parser.add_argument("--max-steps", "--max-execution-count", dest="max_steps", type=int, default=DEFAULT_MAX_STEPS, help="最大何手まで進めるか")
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
    return parser.parse_args(argv)


def entry_guidance(state: dict[str, Any], args: argparse.Namespace) -> str:
    action = describe_next_action(state)
    if action == "request_next_prompt":
        return (
            "このあと初回だけ、ChatGPT に送る本文入力を求めます。"
            " 表示される短い例文をもとに入力すると、その本文をそのまま送信します。"
        )
    if action == "fetch_next_prompt":
        return (
            f"既存チャットの返答を待って回収します。Safari fetch 待機の既定値は {args.fetch_timeout_seconds} 秒です。"
        )
    if action == "launch_codex_once":
        return "prompt はそろっています。bridge が Codex worker を 1 回起動します。"
    if action == "wait_for_codex_report":
        return "Codex worker の完了報告を待ちます。"
    if action == "archive_codex_report":
        return "完了報告を archive して次の依頼へ進めます。"
    if action == "request_prompt_from_report":
        return "完了報告をもとに次の依頼を送ります。"
    if action == "completed":
        return "追加の操作は不要です。"
    return "summary と note を見て次の 1 手を判断してください。"


def print_entry_banner(state: dict[str, Any], args: argparse.Namespace) -> None:
    status = present_bridge_status(state)
    project_path = args.worker_repo_path or "."
    print("bridge entry: python3 scripts/run_until_stop.py")
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
    mode = str(state.get("mode", "idle"))
    if mode == "idle" and bool(state.get("need_chatgpt_prompt")):
        return "request_next_prompt"
    if mode == "waiting_prompt_reply":
        return "fetch_next_prompt"
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
    return command


def format_runner_command(args: argparse.Namespace) -> str:
    command = ["python3", "scripts/run_until_stop.py", "--max-execution-count", str(args.max_steps)]
    if args.worker_repo_path:
        command.extend(["--project-path", str(args.worker_repo_path)])
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


def suggested_next_command(args: argparse.Namespace, final_state: dict[str, Any]) -> str:
    action = describe_next_action(final_state)
    if action in {"completed", "no_action"}:
        return "なし"
    return format_runner_command(args)


def suggested_next_note(final_state: dict[str, Any]) -> str:
    action = describe_next_action(final_state)
    if action == "request_next_prompt":
        return "Safari の current tab を対象チャットに合わせたまま再実行してください。初回だけ、表示される例文をもとに本文入力を行います。"
    if action == "fetch_next_prompt":
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
        return "Safari の current tab を対象チャットに合わせたまま再実行してください。"
    if action == "completed":
        return "追加の操作は不要です。"
    if action == "no_action":
        return "state.json と logs を確認し、必要なら原因を解消してから再開してください。"
    return "state.json を確認してから再実行してください。"


def blocked_next_guidance(final_state: dict[str, Any]) -> tuple[str, str] | None:
    if STOP_PATH.exists():
        return (
            "なし",
            "bridge/STOP があるため停止中です。意図した停止か確認し、続けるなら STOP を外してから再実行してください。",
        )

    if bool(final_state.get("pause")):
        return (
            "なし",
            "state.pause=true のため停止中です。pause を解除してから再実行してください。",
        )

    if bool(final_state.get("error")):
        error_message = str(final_state.get("error_message", "")).strip()
        if is_apple_event_timeout_text(error_message):
            note = (
                "Safari timeout が起きています。"
                f" {safari_timeout_checklist_text()} reply が見えてから error を clear して再実行してください。"
            )
        else:
            note = "state.error=true の原因を解消し、error を clear してから再実行してください。"
        if error_message:
            note += f" error_message: {error_message}"
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

    if STOP_PATH.exists():
        return False

    if bool(final_state.get("pause")) or bool(final_state.get("error")):
        return False

    if codex_report_is_ready(OUTBOX_DIR / "codex_report.md"):
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
    if not codex_report_is_ready(OUTBOX_DIR / "codex_report.md"):
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
        return "次フェーズ request の送信完了を待っています。"
    return "bridge の処理完了を待っています。"


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
    report_path = OUTBOX_DIR / "codex_report.md"
    if codex_report_is_ready(report_path):
        history.append("- codex report: すでに生成済みです")
        return True

    max_wait_seconds = float(args.codex_running_wait_seconds)
    if max_wait_seconds <= 0:
        history.append("- codex report: 待機設定が 0 秒のため即停止します")
        return False

    poll_seconds = max(0.2, float(args.codex_running_poll_seconds))
    heartbeat_seconds = float(args.heartbeat_seconds)
    started_at = time.time()
    deadline = started_at + max_wait_seconds
    next_heartbeat_at = started_at + heartbeat_seconds if heartbeat_seconds > 0 else None

    while time.time() < deadline:
        current_state = load_state()
        check_stop_conditions(current_state)
        if codex_report_is_ready(report_path):
            waited = time.time() - started_at
            history.append(f"- codex report: {format_elapsed(waited)} 待機して生成を検出しました")
            return True

        now = time.time()
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
            if next_heartbeat_at is not None and now >= next_heartbeat_at:
                print(
                    f"[wait] status={status_label} action={action} elapsed={format_elapsed(now - started_at)} "
                    f"{describe_wait_message(action)}"
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
    initial_status = present_bridge_status(initial_state)
    final_status = present_bridge_status(
        final_state,
        blocked=bool(blocked_guidance),
        stale_codex_running=stale_codex_running,
    )
    lines = [
        "# Run Until Stop Summary",
        "",
        f"- reason: {reason}",
        f"- initial_user_status: {initial_status.label}",
        f"- final_user_status: {final_status.label}",
        f"- final_user_status_detail: {final_status.detail}",
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
        f"- stale_codex_running_candidate: {stale_codex_running}",
        f"- fetch_retry_timeouts: {fetch_retry_timeouts}",
        f"- fetch_retry_recoveries: {fetch_retry_recoveries}",
        f"- safari_timeout_blocked: {safari_timeout_blocked}",
        f"- next_action: {describe_next_action(final_state)}",
        f"- suggested_next_command: {suggested_command}",
        f"- suggested_next_note: {suggested_note}",
        "",
    ]
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
    exit_code: int = 0,
) -> int:
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
    )
    log_path = log_text("run_until_stop_summary", summary)
    print(summary.rstrip())
    print(f"log: {log_path}")
    return exit_code


def run(argv: list[str] | None = None) -> int:
    configure_output_streams()
    project_config = load_project_config()
    args = parse_args(argv, project_config)
    warnings = project_config_warnings(project_config)
    print_project_config_warnings(project_config)
    initial_state = load_state()
    print_entry_banner(initial_state, args)
    history: list[str] = []
    steps = 0

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
        return finish(
            args=args,
            reason="completed 相当の状態です。追加の 1 手はありません。",
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

    try:
        while steps < args.max_steps:
            before = load_state()
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
                return finish(
                    args=args,
                    reason="completed 相当の状態に到達しました。",
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
                return finish(
                    args=args,
                    reason="bridge_orchestrator.py で進める次の 1 手が見つかりませんでした。",
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
            interactive = action == "request_next_prompt"
            result, elapsed_seconds = run_command_with_heartbeat(
                command,
                cwd=ROOT_DIR,
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
                history.append("  interactive: 初回 request 本文は terminal 入力をそのまま ChatGPT へ送信した")
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

            if state_signature(before) == state_signature(after):
                if action == "wait_for_codex_report":
                    reason = (
                        "Codex report 待ちのため停止しました。"
                        " bridge/outbox/codex_report.md が生成されたら再実行してください。"
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
