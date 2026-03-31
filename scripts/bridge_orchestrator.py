#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

import archive_codex_report
import fetch_next_prompt
import launch_codex_once
import request_next_prompt
import request_prompt_from_report
from _bridge_common import OUTBOX_DIR, clear_error_fields, codex_report_is_ready, guarded_main, save_state


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="bridge/state.json を見て次の 1 手だけ進めます。")
    parser.add_argument("--codex-bin", default="codex", help="launch_codex_once.py に渡す Codex CLI コマンド")
    parser.add_argument("--codex-model", default="", help="launch_codex_once.py に渡す model 名")
    parser.add_argument("--codex-timeout-seconds", type=int, default=7200, help="Codex 実行の最大秒数")
    parser.add_argument("--dry-run-codex", action="store_true", help="ready_for_codex でも Codex を起動せず内容だけ確認する")
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
    if args.codex_model:
        launch_argv.extend(["--model", args.codex_model])
    if args.dry_run_codex:
        launch_argv.append("--dry-run")
    return launch_argv


def build_request_argv(args: argparse.Namespace) -> list[str]:
    request_argv: list[str] = []
    if args.next_todo:
        request_argv.extend(["--next-todo", args.next_todo])
    if args.open_questions:
        request_argv.extend(["--open-questions", args.open_questions])
    if args.current_status:
        request_argv.extend(["--current-status", args.current_status])
    return request_argv


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
    print("bridge/outbox/codex_report.md を検出したため、state を codex_done に進めました。")
    return True


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mode = str(state.get("mode", "idle"))

    if mode == "idle" and bool(state.get("need_chatgpt_prompt")):
        print("state=idle / need_chatgpt_prompt=true のため、ChatGPT へ次プロンプト要求を送ります。")
        return request_next_prompt.run(dict(state), build_request_argv(args))

    if mode == "waiting_prompt_reply":
        print("state=waiting_prompt_reply のため、ChatGPT 返答から次の Codex 用プロンプトを回収します。")
        return fetch_next_prompt.run(dict(state), [])

    if mode == "ready_for_codex" and bool(state.get("need_codex_run")):
        print("state=ready_for_codex のため、bridge が Codex worker を 1 回起動します。")
        return launch_codex_once.run(dict(state), build_codex_launch_argv(args))

    if mode == "ready_for_codex":
        print("state=ready_for_codex ですが need_codex_run=false のため、状態を確認してください。")
        return 0

    if mode == "codex_running":
        if maybe_promote_codex_done(state):
            return 0
        print("state=codex_running です。Codex worker の完了を待っています。")
        return 0

    if mode == "codex_done":
        print("state=codex_done のため、完了報告を履歴へ退避します。")
        return archive_codex_report.run(dict(state))

    if mode == "idle" and bool(state.get("need_chatgpt_next")):
        print("state=idle / need_chatgpt_next=true のため、完了報告をもとに次フェーズ要求を送ります。")
        return request_prompt_from_report.run(dict(state), build_request_argv(args))

    print("今回の 1 手はありません。state.json を確認してください。")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
