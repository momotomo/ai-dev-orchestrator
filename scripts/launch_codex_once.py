#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from _bridge_common import (
    BRIDGE_DIR,
    INBOX_DIR,
    LOGS_DIR,
    OUTBOX_DIR,
    ROOT_DIR,
    STATE_PATH,
    BridgeError,
    clear_error_fields,
    codex_report_is_ready,
    guarded_main,
    load_project_config,
    now_stamp,
    print_project_config_warnings,
    read_text,
    render_template,
    repo_relative,
    save_state,
    worker_repo_path,
    write_text,
)

DEFAULT_CODEX_BIN = "codex"
DEFAULT_TIMEOUT_SECONDS = 7200


def parse_args(argv: list[str] | None = None, project_config: dict[str, object] | None = None) -> argparse.Namespace:
    project_config = project_config or load_project_config()
    parser = argparse.ArgumentParser(description="bridge/inbox/codex_prompt.md を入力に Codex を 1 回だけ起動します。")
    parser.add_argument("--codex-bin", default=str(project_config.get("codex_bin", DEFAULT_CODEX_BIN)), help="Codex CLI コマンド")
    parser.add_argument("--model", default=str(project_config.get("codex_model", "")), help="Codex CLI に渡す model 名")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(project_config.get("codex_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        help="Codex 実行の最大秒数",
    )
    parser.add_argument(
        "--worker-repo-path",
        "--repo-path",
        dest="worker_repo_path",
        default=str(worker_repo_path(project_config)),
        help="Codex CLI を実行する worker 対象 repo root",
    )
    parser.add_argument("--prompt-file", default=str(INBOX_DIR / "codex_prompt.md"), help="Codex 用 prompt ファイル")
    parser.add_argument("--runner-template", default=str(BRIDGE_DIR / "codex_run_prompt.md"), help="Codex 起動用 wrapper prompt")
    parser.add_argument("--report-file", default=str(OUTBOX_DIR / "codex_report.md"), help="Codex report 出力先")
    parser.add_argument("--dry-run", action="store_true", help="Codex は起動せず、実行内容だけ確認する")
    return parser.parse_args(argv)


def build_launch_prompt(template_path: Path, prompt_path: Path, report_path: Path, fallback_report_path: Path) -> str:
    template_text = read_text(template_path).strip()
    if not template_text:
        raise BridgeError(f"Codex 起動用テンプレートを読めませんでした: {repo_relative(template_path)}")

    values = {
        "RUNNER_RULES_FILE": repo_relative(BRIDGE_DIR / "codex_runner_rules.md"),
        "PROMPT_FILE": repo_relative(prompt_path),
        "REPORT_TEMPLATE_FILE": repo_relative(BRIDGE_DIR / "codex_report_template.md"),
        "REPORT_FILE": repo_relative(report_path),
        "FALLBACK_REPORT_FILE": str(fallback_report_path),
        "STATE_FILE": repo_relative(STATE_PATH),
    }
    return render_template(template_text, values).strip() + "\n"


def build_codex_command(args: argparse.Namespace, last_message_path: Path, worker_path: Path) -> list[str]:
    command = [
        args.codex_bin,
        "--ask-for-approval",
        "never",
        "exec",
        "-C",
        str(worker_path),
        "--sandbox",
        "workspace-write",
        "-o",
        str(last_message_path),
        "-",
    ]
    if args.model:
        command[2:2] = ["--model", args.model]
    return command


def mark_launch_failure(state: dict[str, object], message: str) -> None:
    failed_state = dict(state)
    failed_state.update(
        {
            "mode": "ready_for_codex",
            "need_codex_run": True,
            "error": True,
            "error_message": message,
        }
    )
    save_state(failed_state)


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    project_config = load_project_config()
    args = parse_args(argv, project_config)
    print_project_config_warnings(project_config)
    prompt_path = Path(args.prompt_file)
    template_path = Path(args.runner_template)
    report_path = Path(args.report_file)
    worker_path = Path(args.worker_repo_path).expanduser()
    if not worker_path.is_absolute():
        worker_path = (ROOT_DIR / worker_path).resolve()
    else:
        worker_path = worker_path.resolve()

    if not args.dry_run:
        if str(state.get("mode", "idle")) != "ready_for_codex":
            raise BridgeError("launch_codex_once.py は state=ready_for_codex のときだけ実行してください。")
        if not bool(state.get("need_codex_run")):
            raise BridgeError("need_codex_run=false のため Codex worker を起動しません。")

    prompt_text = read_text(prompt_path).strip()
    if not prompt_text:
        raise BridgeError(f"Codex 起動前の prompt が空です: {repo_relative(prompt_path)}")
    if codex_report_is_ready(report_path):
        raise BridgeError(
            "bridge/outbox/codex_report.md に未退避の完了報告が残っています。"
            " 先に archive を完了してください。"
        )

    stamp = now_stamp()
    prompt_log_path = LOGS_DIR / f"{stamp}_codex_launch_prompt.md"
    stdout_log_path = LOGS_DIR / f"{stamp}_codex_launch_stdout.txt"
    stderr_log_path = LOGS_DIR / f"{stamp}_codex_launch_stderr.txt"
    last_message_path = LOGS_DIR / f"{stamp}_codex_last_message.txt"
    fallback_report_path = Path("/tmp") / f"{ROOT_DIR.name}_{stamp}_codex_report.md"
    launch_prompt = build_launch_prompt(template_path, prompt_path, report_path, fallback_report_path)
    write_text(prompt_log_path, launch_prompt)

    command = build_codex_command(args, last_message_path, worker_path)
    if args.dry_run:
        print("dry-run command:", " ".join(command))
        print(f"worker repo path: {worker_path}")
        print(f"launch prompt: {prompt_log_path}")
        return 0

    running_state = clear_error_fields(dict(state))
    running_state.update(
        {
            "mode": "codex_running",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": True,
            "last_prompt_file": repo_relative(prompt_path),
        }
    )
    save_state(running_state)

    try:
        result = subprocess.run(
            command,
            input=launch_prompt,
            text=True,
            capture_output=True,
            check=False,
            cwd=worker_path,
            timeout=args.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        message = (
            "Codex 実行がタイムアウトしました。"
            f" prompt: {repo_relative(prompt_log_path)} を確認し、必要なら再実行してください。"
        )
        mark_launch_failure(state, message)
        raise BridgeError(message) from exc
    except KeyboardInterrupt as exc:
        message = (
            "Codex 実行を手動中断しました。"
            f" prompt: {repo_relative(prompt_log_path)} を確認し、必要なら再実行してください。"
        )
        mark_launch_failure(state, message)
        raise BridgeError(message) from exc

    write_text(stdout_log_path, result.stdout or "")
    write_text(stderr_log_path, result.stderr or "")

    if not codex_report_is_ready(report_path) and fallback_report_path.exists():
        fallback_report_text = read_text(fallback_report_path).strip()
        if fallback_report_text:
            write_text(report_path, fallback_report_text + "\n")

    if codex_report_is_ready(report_path):
        done_state = clear_error_fields(dict(state))
        done_state.update(
            {
                "mode": "codex_done",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": False,
                "need_codex_run": False,
                "last_prompt_file": repo_relative(prompt_path),
            }
        )
        save_state(done_state)
        print(f"codex prompt log: {prompt_log_path}")
        print(f"codex stdout log: {stdout_log_path}")
        print(f"codex stderr log: {stderr_log_path}")
        print(f"codex last message: {last_message_path}")
        if fallback_report_path.exists():
            print(f"fallback report: {fallback_report_path}")
        print(f"report: {report_path}")
        if result.returncode != 0:
            print(f"warning: codex exited with code {result.returncode} but report was found.")
        return 0

    message = (
        "Codex 実行後も bridge/outbox/codex_report.md が生成されませんでした。"
        f" stdout: {repo_relative(stdout_log_path)} stderr: {repo_relative(stderr_log_path)}"
    )
    if result.returncode != 0:
        message += f" exit_code={result.returncode}"
    mark_launch_failure(state, message)
    raise BridgeError(message)


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
