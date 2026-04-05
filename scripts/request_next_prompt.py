#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _bridge_common import (
    BridgeError,
    build_chatgpt_reply_contract_section,
    clear_error_fields,
    clear_pending_request_fields,
    clear_prepared_request_fields,
    guarded_main,
    load_project_config,
    log_text,
    promote_pending_request,
    read_prepared_request_text,
    repo_relative,
    send_to_chatgpt,
    save_state,
    stage_prepared_request,
    stable_text_hash,
    worker_repo_path,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    project_config = load_project_config()
    parser = argparse.ArgumentParser(
        description="初回だけ、ユーザーが入力した本文を正本として受け取り、bridge が固定の返答契約を付けて Safari の現在 ChatGPT タブへ送信します。"
    )
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


def build_example_templates(project_path: Path) -> list[str]:
    project_name = project_path.name or project_path.as_posix()
    return [
        "\n".join(
            [
                f"対象案件: {project_name}",
                f"対象 repo: {project_path}",
                "今進めたいこと: sample browser の軽い UI polish",
                "制約: schema / resolver / preview / playback / export は変えない",
                "次の 1 フェーズ分の Codex 用 prompt を返してください。",
            ]
        ),
        "\n".join(
            [
                f"対象案件: {project_name}",
                "今の状況: 前の phase は完了、次は 1 つだけ具体的に進めたい",
                "今回の狙い: 既存挙動を壊さず最小差分で改善する",
                "次の Codex 用 1 フェーズ prompt を返してください。",
            ]
        ),
        "\n".join(
            [
                f"対象 repo: {project_path}",
                "現在の継続テーマ: [ここを短く入力]",
                "今回やってほしいこと: [ここを短く入力]",
                "触らないこと: [あれば短く入力]",
                "次の 1 フェーズ分の Codex 用 prompt を返してください。",
            ]
        ),
    ]


def prompt_initial_request_body(example_texts: list[str]) -> str:
    print("初回だけ、ChatGPT に送る最初の依頼文を入力してください。", flush=True)
    print("ここで入力した本文がそのまま ChatGPT へ送られます。これが初回 request の正本です。", flush=True)
    print("bridge は本文を改変せず、送信直前に固定の返答契約だけを追記します。", flush=True)
    print("これは初回 request 専用で、human_review / need_info 再開時の補足入力とは別です。", flush=True)
    print("返答フォーマット指定まで自分で書く必要はありません。進めたい内容だけを書いてください。", flush=True)
    print("以下の短い例文を、そのまま少し書き換えて使えます。", flush=True)
    print("", flush=True)
    for index, example_text in enumerate(example_texts, start=1):
        print(f"[例 {index}]", flush=True)
        print(example_text, flush=True)
        print("", flush=True)
    print("bridge が固定の返答契約を自動で付けるので、本文には今回進めたいことだけを入れてください。", flush=True)
    print("入力後は Safari の current tab へ送信し、続けて返答待ちへ進みます。", flush=True)
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


def resolve_request_body(args: argparse.Namespace) -> str:
    if args.request_body.strip():
        return args.request_body.strip() + "\n"

    example_texts = build_example_templates(resolve_project_path(args.project_path))

    if sys.stdin is not None and not sys.stdin.isatty():
        request_text = sys.stdin.read().strip()
    else:
        request_text = prompt_initial_request_body(example_texts)

    if not request_text.strip():
        raise BridgeError(
            "初回 request 本文が空です。"
            " 例文をもとに本文を入力するか、`--request-body` で本文を渡してください。"
        )
    return request_text.strip() + "\n"


def compose_initial_request_text(user_body: str) -> str:
    body = user_body.strip()
    if not body:
        raise BridgeError("初回 request 本文が空です。")
    contract_section = build_chatgpt_reply_contract_section()
    return f"{body}\n\n{contract_section}\n"


def build_initial_request_source(user_body: str) -> str:
    return f"initial:{stable_text_hash(user_body.strip())}"


def load_retryable_initial_request(state: dict[str, object]) -> tuple[str, str, str] | None:
    if str(state.get("pending_request_source", "")).strip():
        return None
    prepared_status = str(state.get("prepared_request_status", "")).strip()
    prepared_source = str(state.get("prepared_request_source", "")).strip()
    prepared_hash = str(state.get("prepared_request_hash", "")).strip()
    if prepared_status != "retry_send" or not prepared_source.startswith("initial:"):
        return None
    prepared_text = read_prepared_request_text(state)
    if not prepared_text:
        return None
    return prepared_text, prepared_hash or stable_text_hash(prepared_text), prepared_source


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    retryable_request = None if args.request_body.strip() else load_retryable_initial_request(state)
    if retryable_request is not None:
        request_text, request_hash, request_source = retryable_request
        print("request: 前回未送信の初回 request を再送します。")
    else:
        user_body = resolve_request_body(args)
        request_text = compose_initial_request_text(user_body)
        request_hash = stable_text_hash(request_text)
        request_source = build_initial_request_source(user_body)

    if (
        str(state.get("mode", "")).strip() == "waiting_prompt_reply"
        and str(state.get("pending_request_source", "")).strip() == request_source
    ):
        print("request: 同じ初回 request は送信済みのため再送しませんでした。")
        if str(state.get("pending_request_log", "")).strip():
            print(f"pending: {state.get('pending_request_log', '')}")
        return 0

    prepared_log = log_text("prepared_prompt_request", request_text)
    prepared_state = clear_error_fields(dict(state))
    stage_prepared_request(
        prepared_state,
        request_hash=request_hash,
        request_source=request_source,
        request_log=repo_relative(prepared_log),
    )
    save_state(prepared_state)

    try:
        send_to_chatgpt(request_text)
    except Exception:
        retry_state = clear_error_fields(dict(state))
        stage_prepared_request(
            retry_state,
            request_hash=request_hash,
            request_source=request_source,
            request_log=repo_relative(prepared_log),
            status="retry_send",
        )
        save_state(retry_state)
        raise

    request_log = log_text("sent_prompt_request", request_text)
    mutable_state = clear_error_fields(dict(state))
    clear_pending_request_fields(mutable_state)
    clear_prepared_request_fields(mutable_state)
    promote_pending_request(
        mutable_state,
        request_hash=request_hash,
        request_source=request_source,
        request_log=repo_relative(request_log),
    )
    save_state(mutable_state)

    print(f"sent: {request_log}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
