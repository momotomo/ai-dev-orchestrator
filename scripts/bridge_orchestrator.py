#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

import archive_codex_report
import fetch_next_prompt
import launch_codex_once
import request_next_prompt
import request_prompt_from_report
from _bridge_common import OUTBOX_DIR, browser_fetch_timeout_seconds, clear_error_fields, codex_report_is_ready, guarded_main, load_browser_config, load_project_config, present_bridge_status, print_project_config_warnings, save_state, worker_repo_path


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


def maybe_promote_codex_done(state: dict[str, object]) -> bool:
    if not codex_report_is_ready(OUTBOX_DIR / "codex_report.md"):
        return False

    updated = clear_error_fields(dict(state))
    updated.update(
        {
            "mode": "codex_done",
            "need_codex_run": False,
        }
    )
    save_state(updated)
    status = present_bridge_status(updated)
    print(f"{status.label}です。bridge/outbox/codex_report.md を検出したため、次 request 準備へ進みます。")
    return True


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    project_config = load_project_config()
    args = parse_args(argv, project_config)
    print_project_config_warnings(project_config)
    mode = str(state.get("mode", "idle"))

    if mode == "idle" and bool(state.get("need_chatgpt_prompt")):
        status = present_bridge_status(state)
        print(f"{status.label}です。ChatGPT に送る最初の文面を入力して送信します。")
        return request_next_prompt.run(dict(state), build_initial_request_argv(args))

    if mode == "waiting_prompt_reply":
        status = present_bridge_status(state)
        print(f"{status.label}です。ChatGPT 返答から次の Codex 用プロンプトを回収します。")
        return fetch_next_prompt.run(dict(state), build_fetch_argv(args))

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

    if mode == "idle" and bool(state.get("need_chatgpt_next")):
        status = present_bridge_status(state)
        print(f"{status.label}です。完了報告をもとに次フェーズ要求を送ります。")
        return request_prompt_from_report.run(dict(state), build_report_request_argv(args))

    status = present_bridge_status(state, blocked=True)
    print(f"{status.label}です。今回の 1 手はありません。state.json を確認してください。")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
