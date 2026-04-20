#!/usr/bin/env python3
"""launch_github_copilot.py — GitHub Copilot 実行 entrypoint.

bridge の prompt ファイルを入力として GitHub Copilot を 1 回だけ起動します。
``execution_agent: github_copilot`` のとき ``bridge_orchestrator.py`` から呼ばれます。

実行インターフェース:
- デフォルトは ``gh copilot suggest --target=shell`` (GitHub CLI Copilot extension)
- ``--github-copilot-bin`` で任意の実行可能ファイルに差し替えられます
- prompt は stdin でコマンドへ渡されます

このスクリプトは ``launch_codex_once.py`` と同じ lifecycle を踏みます:
1. state=ready_for_codex + need_codex_run=True を確認
2. prompt を読む
3. コマンドを subprocess 起動 (stdin=prompt)
4. タイムアウト / KeyboardInterrupt をハンドル
5. 完了後に bridge/outbox/codex_report.md があれば done とする
   なければ失敗として state.error=True を記録

なお、GitHub Copilot CLI が実際に ``codex_report.md`` を生成するかはインターフェース
実装ごとに異なります。実際の Copilot 呼び出し先を差し替えた場合、ステップ 5 の
report 検証ロジックを合わせて調整してください。
"""
from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from _bridge_common import (
    BRIDGE_DIR,
    BRIDGE_SUMMARY_END,
    BRIDGE_SUMMARY_START,
    ROOT_DIR,
    BridgeError,
    clear_error_fields,
    codex_report_is_ready,
    guarded_main,
    load_project_config,
    now_stamp,
    print_project_config_warnings,
    read_text,
    recover_codex_report,
    repo_relative,
    runtime_logs_dir,
    runtime_prompt_path,
    runtime_report_path,
    save_state,
    worker_repo_path,
    write_text,
)

DEFAULT_GITHUB_COPILOT_BIN = "copilot"
COPILOT_CLI_BIN = "copilot"
REASONING_EFFORT_ALLOWED = frozenset({"low", "medium", "high"})
DEFAULT_TIMEOUT_SECONDS = 7200
PROGRESS_POLL_SECONDS = 1.0

# Regex to find a Codex Report heading in stdout (e.g. "# Codex Report", "## Codex Report").
_CODEX_REPORT_HEADING_RE = re.compile(
    r"^#{1,3}\s+Codex Report",
    re.MULTILINE | re.IGNORECASE,
)


def extract_codex_report_from_stdout(text: str) -> str:
    """stdout テキストに有効な Codex Report 本文が含まれていれば抽出して返す。

    有効条件:
    - ``===BRIDGE_SUMMARY===`` と ``===END_BRIDGE_SUMMARY===`` が両方存在すること

    抽出開始点:
    - ``# Codex Report`` / ``## Codex Report`` 見出しがあればそこから
    - なければ ``===BRIDGE_SUMMARY===`` の位置から

    雑多なログや無関係な stdout は空文字列を返す。
    """
    if BRIDGE_SUMMARY_START not in text or BRIDGE_SUMMARY_END not in text:
        return ""
    # Prefer extracting from the Codex Report heading to preserve full context.
    m = _CODEX_REPORT_HEADING_RE.search(text)
    if m:
        return text[m.start():].strip()
    # Fallback: start from the BRIDGE_SUMMARY block.
    idx = text.find(BRIDGE_SUMMARY_START)
    return text[idx:].strip()


def parse_args(
    argv: list[str] | None = None,
    project_config: dict[str, object] | None = None,
) -> argparse.Namespace:
    project_config = project_config or load_project_config()
    parser = argparse.ArgumentParser(
        description="bridge/inbox/codex_prompt.md を入力に GitHub Copilot を 1 回だけ起動します。"
    )
    parser.add_argument(
        "--github-copilot-bin",
        default=str(project_config.get("github_copilot_bin", DEFAULT_GITHUB_COPILOT_BIN)),
        help="GitHub Copilot 実行コマンド (default: gh)",
    )
    # agent_model (active-provider common field) provides the model for GitHub Copilot.
    # gh copilot suggest does not yet have a stable --model flag, so the model value is
    # stored in args.model and forwarded to custom wrapper scripts via --model argv when
    # the bin is not the default "gh".  For the default "gh" path, the model is noted
    # in the process environment description only (gh CLI does not accept --model today).
    _agent_model = str(project_config.get("agent_model", "")).strip()
    parser.add_argument(
        "--model",
        default=_agent_model,
        help="GitHub Copilot 実行時の model 名 (agent_model から設定。未設定なら provider default)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(project_config.get("codex_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        help="GitHub Copilot 実行の最大秒数",
    )
    parser.add_argument(
        "--worker-repo-path",
        "--repo-path",
        dest="worker_repo_path",
        default=str(worker_repo_path(project_config)),
        help="GitHub Copilot を実行する worker 対象 repo root",
    )
    parser.add_argument(
        "--prompt-file",
        default=str(runtime_prompt_path()),
        help="GitHub Copilot 用 prompt ファイル",
    )
    parser.add_argument(
        "--report-file",
        default=str(runtime_report_path()),
        help="実行結果 report 出力先",
    )
    parser.add_argument(
        "--autopilot",
        action="store_true",
        default=bool(project_config.get("github_copilot_autopilot", False)),
        help="--autopilot を copilot CLI に付与する (github_copilot_autopilot から設定)",
    )
    _reasoning_effort = str(project_config.get("github_copilot_reasoning_effort", "")).strip()
    parser.add_argument(
        "--reasoning-effort",
        default=_reasoning_effort,
        metavar="EFFORT",
        help="reasoning effort (low/medium/high)。空または未設定なら付与しない (github_copilot_reasoning_effort から設定)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="GitHub Copilot は起動せず、実行内容だけ確認する",
    )
    return parser.parse_args(argv)


def build_github_copilot_command(args: argparse.Namespace, prompt_text: str = "") -> list[str]:
    """Build the subprocess command for GitHub Copilot.

    ``copilot`` bin (new default):
        ``copilot [--model M] [--reasoning-effort R] [--autopilot] -p <prompt> -s --allow-all-tools``
        Prompt is passed via ``-p``; stdin is not used.

    ``gh`` bin (legacy):
        ``gh copilot suggest --target=shell -``
        Prompt is passed via stdin (backward compat).

    Custom wrapper bin (e.g. ``github_copilot_wrapper.py --exec provider``):
        ``<bin> [--model M] [--report-file R]``
        Prompt is passed via stdin.
    """
    bin_path = args.github_copilot_bin.strip()
    model = str(getattr(args, "model", "")).strip()
    report_file = str(getattr(args, "report_file", "")).strip()
    reasoning_effort = str(getattr(args, "reasoning_effort", "") or "").strip()
    autopilot = bool(getattr(args, "autopilot", False))

    if bin_path == COPILOT_CLI_BIN:
        # New copilot CLI direct invocation: prompt via -p, non-interactive.
        cmd = [COPILOT_CLI_BIN]
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["--reasoning-effort", reasoning_effort])
        if autopilot:
            cmd.append("--autopilot")
        cmd.extend(["-p", prompt_text])
        cmd.extend(["-s", "--allow-all-tools"])
        return cmd

    if bin_path == "gh":
        # Legacy gh copilot suggest path: prompt via stdin.
        return ["gh", "copilot", "suggest", "--target=shell", "-"]

    # Custom wrapper (e.g. github_copilot_wrapper.py --exec /path/to/provider).
    # github_copilot_bin may contain inline args — use shlex.split to separate them.
    cmd = shlex.split(bin_path)
    if model:
        cmd.extend(["--model", model])
    if report_file:
        cmd.extend(["--report-file", report_file])
    return cmd


def validate_github_copilot_args(args: argparse.Namespace) -> None:
    """Validate GitHub Copilot 起動引数。不正値の場合 ``BridgeError`` を送出する。"""
    reasoning_effort = str(getattr(args, "reasoning_effort", "") or "").strip()
    if reasoning_effort and reasoning_effort not in REASONING_EFFORT_ALLOWED:
        raise BridgeError(
            f"github_copilot_reasoning_effort の値が不正です: '{reasoning_effort}'. "
            f"許可値: {sorted(REASONING_EFFORT_ALLOWED)}"
        )


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


def mark_launch_done(state: dict[str, object], prompt_path: Path) -> None:
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


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    project_config = load_project_config()
    args = parse_args(argv, project_config)
    validate_github_copilot_args(args)
    print_project_config_warnings(project_config)

    prompt_path = Path(args.prompt_file)
    report_path = Path(args.report_file)
    worker_path = Path(args.worker_repo_path).expanduser()
    if not worker_path.is_absolute():
        worker_path = (ROOT_DIR / worker_path).resolve()
    else:
        worker_path = worker_path.resolve()

    if not args.dry_run:
        if str(state.get("mode", "idle")) != "ready_for_codex":
            raise BridgeError(
                "launch_github_copilot.py は state=ready_for_codex のときだけ実行してください。"
            )
        if not bool(state.get("need_codex_run")):
            raise BridgeError("need_codex_run=false のため GitHub Copilot を起動しません。")

    prompt_text = read_text(prompt_path).strip()
    if not prompt_text:
        raise BridgeError(
            f"GitHub Copilot 起動前の prompt が空です: {repo_relative(prompt_path)}"
        )

    prompt_mtime = prompt_path.stat().st_mtime if prompt_path.exists() else None
    recovered_before_launch = recover_codex_report(
        report_path,
        search_recent_logs=True,
        newer_than=prompt_mtime,
    )
    if recovered_before_launch is not None:
        if args.dry_run:
            print(f"recovered report before launch: {recovered_before_launch}")
            return 0
        mark_launch_done(state, prompt_path)
        print(f"recovered report before launch: {recovered_before_launch}")
        print(f"report: {report_path}")
        return 0

    if codex_report_is_ready(report_path):
        raise BridgeError(
            "bridge/outbox/codex_report.md に未退避の完了報告が残っています。"
            " 先に archive を完了してください。"
        )

    stamp = now_stamp()
    logs_dir = runtime_logs_dir()
    prompt_log_path = logs_dir / f"{stamp}_github_copilot_launch_prompt.md"
    stdout_log_path = logs_dir / f"{stamp}_github_copilot_launch_stdout.txt"
    stderr_log_path = logs_dir / f"{stamp}_github_copilot_launch_stderr.txt"

    write_text(prompt_log_path, prompt_text)

    command = build_github_copilot_command(args, prompt_text=prompt_text)
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

    started_at = time.time()
    process: subprocess.Popen[str] | None = None
    result: subprocess.CompletedProcess[str] | None = None

    write_text(stdout_log_path, "")
    write_text(stderr_log_path, "")
    try:
        with (
            stdout_log_path.open("w", encoding="utf-8") as stdout_handle,
            stderr_log_path.open("w", encoding="utf-8") as stderr_handle,
        ):
            is_copilot_cli = (args.github_copilot_bin.strip() == COPILOT_CLI_BIN)
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL if is_copilot_cli else subprocess.PIPE,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                cwd=worker_path,
            )
            if not is_copilot_cli and process.stdin is not None:
                process.stdin.write(prompt_text)
                process.stdin.close()

            deadline = started_at + args.timeout_seconds
            while True:
                returncode = process.poll()
                if returncode is not None:
                    result = subprocess.CompletedProcess(
                        command,
                        returncode,
                        read_text(stdout_log_path),
                        read_text(stderr_log_path),
                    )
                    break
                if time.time() >= deadline:
                    raise subprocess.TimeoutExpired(command, args.timeout_seconds)
                time.sleep(PROGRESS_POLL_SECONDS)
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        message = (
            "GitHub Copilot 実行がタイムアウトしました。"
            f" prompt: {repo_relative(prompt_log_path)} を確認し、必要なら再実行してください。"
        )
        mark_launch_failure(state, message)
        raise BridgeError(message) from exc
    except KeyboardInterrupt as exc:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        message = (
            "GitHub Copilot 実行を手動中断しました。"
            f" prompt: {repo_relative(prompt_log_path)} を確認し、必要なら再実行してください。"
        )
        mark_launch_failure(state, message)
        raise BridgeError(message) from exc

    assert result is not None
    recovered_after_launch = recover_codex_report(
        report_path,
        log_paths=[stdout_log_path, stderr_log_path],
    )

    # Fallback: extract Codex Report content directly from copilot stdout.
    # The copilot CLI writes the report to stdout rather than to a file.
    # recover_codex_report() only handles file-path recovery, so we supplement
    # it here by detecting and saving valid report text found in stdout.
    if not codex_report_is_ready(report_path):
        stdout_text = read_text(stdout_log_path)
        extracted = extract_codex_report_from_stdout(stdout_text)
        if extracted:
            write_text(report_path, extracted.rstrip() + "\n")
            print(
                f"report extracted from copilot stdout: {repo_relative(stdout_log_path)}"
            )

    if codex_report_is_ready(report_path):
        mark_launch_done(state, prompt_path)
        print(f"github_copilot prompt log: {prompt_log_path}")
        print(f"github_copilot stdout log: {stdout_log_path}")
        print(f"github_copilot stderr log: {stderr_log_path}")
        if recovered_after_launch is not None:
            print(f"recovered report: {recovered_after_launch}")
        print(f"report: {report_path}")
        if result.returncode != 0:
            print(f"warning: github_copilot exited with code {result.returncode} but report was found.")
        return 0

    message = (
        "GitHub Copilot 実行後も bridge/outbox/codex_report.md が生成されませんでした。"
        f" stdout: {repo_relative(stdout_log_path)} stderr: {repo_relative(stderr_log_path)}"
    )
    if result.returncode != 0:
        message += f" exit_code={result.returncode}"
    mark_launch_failure(state, message)
    raise BridgeError(message)


if __name__ == "__main__":
    from _bridge_common import load_state

    sys.exit(
        guarded_main(
            lambda state: run(state),
        )
    )
