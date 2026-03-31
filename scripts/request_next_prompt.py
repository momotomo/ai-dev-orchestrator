#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _bridge_common import BridgeError, clear_error_fields, guarded_main, load_project_config, log_text, send_to_chatgpt, save_state, worker_repo_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    project_config = load_project_config()
    parser = argparse.ArgumentParser(description="初回だけ、ユーザーが入力した本文をそのまま Safari の現在 ChatGPT タブへ送信します。")
    parser.add_argument("--request-body", default="", help="初回に ChatGPT へ送る本文。指定時は対話入力を省略する")
    parser.add_argument(
        "--project-path",
        default=str(worker_repo_path(project_config)),
        help="例文テンプレート表示用の project path",
    )
    return parser.parse_args(argv)


def resolve_project_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def build_example_template(project_path: Path) -> str:
    project_name = project_path.name or project_path.as_posix()
    return "\n".join(
        [
            f"対象案件: {project_name}",
            f"対象 repo: {project_path}",
            "現在の継続テーマ: [ここを入力]",
            "狙い: [ここを入力]",
            "次の 1 フェーズ分の Codex 用 prompt を返してください。",
        ]
    )


def prompt_initial_request_body(example_text: str) -> str:
    print("初回だけ、ChatGPT に送る最初の文面を入力してください。", flush=True)
    print("この入力本文が初回 request の正本です。bridge は入力した本文をそのまま送ります。", flush=True)
    print("以下はそのまま使える短い例文です。必要な行だけ書き換えてください。", flush=True)
    print("", flush=True)
    print(example_text, flush=True)
    print("", flush=True)
    print("入力後は Safari の current tab にそのまま送信し、続けて返答待ちへ進みます。", flush=True)
    print("入力終了は Ctrl-D、または空行を 2 回です。空入力では進みません。", flush=True)

    lines: list[str] = []
    empty_streak = 0
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            empty_streak += 1
            if empty_streak >= 2:
                break
        else:
            empty_streak = 0
        lines.append(line)
    return "\n".join(lines).strip()


def resolve_request_text(args: argparse.Namespace) -> str:
    if args.request_body.strip():
        return args.request_body.strip() + "\n"

    example_text = build_example_template(resolve_project_path(args.project_path))

    if sys.stdin is not None and not sys.stdin.isatty():
        request_text = sys.stdin.read().strip()
    else:
        request_text = prompt_initial_request_body(example_text)

    if not request_text.strip():
        raise BridgeError(
            "初回 request 本文が空です。"
            " 例文をもとに本文を入力するか、`--request-body` で本文を渡してください。"
        )
    return request_text.strip() + "\n"


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    request_text = resolve_request_text(args)

    send_to_chatgpt(request_text)
    request_log = log_text("sent_prompt_request", request_text)

    mutable_state = clear_error_fields(dict(state))
    mutable_state.update(
        {
            "mode": "waiting_prompt_reply",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
        }
    )
    save_state(mutable_state)
    print(f"sent: {request_log}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
