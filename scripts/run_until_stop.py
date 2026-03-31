#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from typing import Any

from _bridge_common import (
    BridgeStop,
    ROOT_DIR,
    check_stop_conditions,
    load_state,
    log_text,
    mark_error,
    state_snapshot,
)


DEFAULT_MAX_STEPS = 6
DEFAULT_SLEEP_SECONDS = 1.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="bridge_orchestrator.py を止まる条件まで数手まとめて実行します。")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="最大何手まで進めるか")
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS, help="各手のあいだに待つ秒数")
    parser.add_argument("--dry-run", action="store_true", help="実行せず、次の 1 手と停止条件だけ表示する")
    parser.add_argument("--codex-bin", default="codex", help="bridge_orchestrator.py に渡す Codex CLI コマンド")
    parser.add_argument("--codex-model", default="", help="bridge_orchestrator.py に渡す model 名")
    parser.add_argument("--codex-timeout-seconds", type=int, default=7200, help="Codex 実行の最大秒数")
    parser.add_argument("--dry-run-codex", action="store_true", help="ready_for_codex でも Codex を起動せず内容だけ確認する")
    parser.add_argument("--next-todo", default="", help="初回 / 次回 request に渡す next_todo")
    parser.add_argument("--open-questions", default="", help="初回 / 次回 request に渡す open_questions")
    parser.add_argument("--current-status", default="", help="request 系 script に渡す CURRENT_STATUS 上書き")
    return parser.parse_args(argv)


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


def suggested_next_command(final_state: dict[str, Any]) -> str:
    action = describe_next_action(final_state)
    if action in {"completed", "no_action"}:
        return "なし"
    return "python3 scripts/bridge_orchestrator.py"


def summarize_run(
    *,
    args: argparse.Namespace,
    reason: str,
    steps: int,
    initial_state: dict[str, Any],
    final_state: dict[str, Any],
    history: list[str],
) -> str:
    lines = [
        "# Run Until Stop Summary",
        "",
        f"- reason: {reason}",
        f"- steps: {steps}",
        f"- max_steps: {args.max_steps}",
        f"- sleep_seconds: {args.sleep_seconds}",
        f"- dry_run: {args.dry_run}",
        f"- next_action: {describe_next_action(final_state)}",
        f"- suggested_next_command: {suggested_next_command(final_state)}",
        "",
        "## initial_state",
        state_snapshot(initial_state),
        "",
        "## final_state",
        state_snapshot(final_state),
        "",
        "## history",
    ]
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
    initial_state: dict[str, Any],
    final_state: dict[str, Any],
    history: list[str],
    exit_code: int = 0,
) -> int:
    summary = summarize_run(
        args=args,
        reason=reason,
        steps=steps,
        initial_state=initial_state,
        final_state=final_state,
        history=history,
    )
    log_path = log_text("run_until_stop_summary", summary)
    print(summary.rstrip())
    print(f"log: {log_path}")
    return exit_code


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    initial_state = load_state()
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
            initial_state=initial_state,
            final_state=final_state,
            history=history,
        )

    if is_completed_state(initial_state):
        return finish(
            args=args,
            reason="completed 相当の状態です。追加の 1 手はありません。",
            steps=steps,
            initial_state=initial_state,
            final_state=initial_state,
            history=history,
        )

    if args.dry_run:
        history.append(f"- dry_run next_action: {describe_next_action(initial_state)}")
        return finish(
            args=args,
            reason="dry-run のため実行せず停止しました。",
            steps=steps,
            initial_state=initial_state,
            final_state=initial_state,
            history=history,
        )

    command = build_orchestrator_command(args)

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
                    initial_state=initial_state,
                    final_state=final_state,
                    history=history,
                )

            if is_completed_state(before):
                return finish(
                    args=args,
                    reason="completed 相当の状態に到達しました。",
                    steps=steps,
                    initial_state=initial_state,
                    final_state=before,
                    history=history,
                )

            action = describe_next_action(before)
            if action == "no_action":
                history.append(f"- step {steps + 1}: no_action / mode={before.get('mode', '')}")
                return finish(
                    args=args,
                    reason="bridge_orchestrator.py で進める次の 1 手が見つかりませんでした。",
                    steps=steps,
                    initial_state=initial_state,
                    final_state=before,
                    history=history,
                )

            print(f"[step {steps + 1}] action={action} mode={before.get('mode', '')}")
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                cwd=ROOT_DIR,
            )
            steps += 1
            after = load_state()
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            history.append(
                f"- step {steps}: action={action} rc={result.returncode} "
                f"before={before.get('mode', '')} after={after.get('mode', '')}"
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
            initial_state=initial_state,
            final_state=final_state,
            history=history,
            exit_code=1,
        )


if __name__ == "__main__":
    sys.exit(run())
