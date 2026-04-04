#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

import run_until_stop


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "bridge の通常入口です。project path と max execution count を渡して起動します。"
            " 初回だけ最初の ChatGPT request 本文を入力し、その後は必要時だけ再実行します。"
        )
    )
    parser.add_argument(
        "--project-path",
        help="worker 対象 repo root",
    )
    parser.add_argument(
        "--max-execution-count",
        type=int,
        default=run_until_stop.DEFAULT_MAX_STEPS,
        help="1 回の run で最大何手まで進めるか",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="実行せず、今どこで止まっているかと次に何をすればよいかだけ表示する",
    )
    return parser.parse_args(argv)


def print_resume_overview(args: argparse.Namespace) -> None:
    project_config = run_until_stop.load_project_config()
    browser_config = run_until_stop.load_browser_config()
    state = run_until_stop.load_state()
    derived_args = run_until_stop.parse_args(
        [
            "--project-path",
            args.project_path or str(run_until_stop.worker_repo_path(project_config)),
            "--max-execution-count",
            str(args.max_execution_count),
            "--fetch-timeout-seconds",
            str(run_until_stop.browser_fetch_timeout_seconds(browser_config)),
            "--entry-script",
            "scripts/start_bridge.py",
        ],
        project_config,
    )
    mode_label = run_until_stop.start_bridge_mode(state)
    status_label, guidance, note = run_until_stop.start_bridge_resume_guidance(derived_args, state)
    print("bridge status:", flush=True)
    print(f"- 入口判断: {mode_label}", flush=True)
    print(f"- 現在の状況: {status_label}", flush=True)
    print(f"- 次に起きること: {guidance}", flush=True)
    print(f"- 次に見るもの: {note}", flush=True)
    if mode_label == "先に確認":
        print("- このまま再実行する前に、handoff と summary の案内を確認してください。", flush=True)
    else:
        print("- 同じコマンドでそのまま再開して大丈夫です。", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.status and not args.project_path:
        raise SystemExit("--project-path は通常実行時に必須です。状態確認だけなら --status を使えます。")
    print("bridge start: このコマンドが通常入口です。", flush=True)
    print(f"- project_path: {args.project_path or '(state から確認)'}", flush=True)
    print(f"- max_execution_count: {args.max_execution_count}", flush=True)
    print("- 初回だけ依頼文を入力します。2 回目以降は同じコマンドで継続できます。", flush=True)
    print("- 内部 state ではなく、人向け表示と handoff を見ながら進める想定です。", flush=True)
    print_resume_overview(args)
    if args.status:
        return 0
    forwarded_argv = [
        "--project-path",
        args.project_path,
        "--max-execution-count",
        str(args.max_execution_count),
        "--entry-script",
        "scripts/start_bridge.py",
    ]
    return run_until_stop.run(forwarded_argv)


if __name__ == "__main__":
    sys.exit(main())
