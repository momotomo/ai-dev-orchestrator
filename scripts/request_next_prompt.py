#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _bridge_common import (
    BridgeError,
    can_reuse_prepared_request,
    clear_error_fields,
    clear_pending_request_fields,
    clear_prepared_request_fields,
    guarded_main,
    load_project_config,
    log_text,
    promote_pending_request,
    read_prepared_request_text,
    repo_relative,
    send_initial_request_to_chatgpt,
    save_state,
    stage_prepared_request,
    stable_text_hash,
    worker_repo_path,
)
from issue_centric_contract import build_issue_centric_reply_contract_section


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    project_config = load_project_config()
    parser = argparse.ArgumentParser(
        description=(
            "通常入口では current ready issue の参照を受け取り、"
            "bridge が最小の初回 request と issue-centric 返答契約を Safari の現在 ChatGPT タブへ送信します。"
            " free-form 初回本文は override 用にだけ残します。"
        )
    )
    parser.add_argument(
        "--ready-issue-ref",
        default="",
        help="通常入口で使う current ready issue の参照。例: '#123 sample browser wording cleanup'",
    )
    parser.add_argument(
        "--request-body",
        default="",
        help="例外 / recovery / override 経路で使う初回本文。指定時は ready issue 参照の入力を省略する",
    )
    parser.add_argument(
        "--select-issue",
        action="store_true",
        default=False,
        help="初回 issue 選定モード: ChatGPT に open issue から ready issue を 1 件選ばせる。実装開始は次の request で行う",
    )
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


def normalize_ready_issue_ref(raw_ref: str) -> str:
    return " ".join(raw_ref.strip().split())


def build_override_example_templates(project_path: Path) -> list[str]:
    project_name = project_path.name or project_path.as_posix()
    return [
        "\n".join(
            [
                f"対象案件: {project_name}",
                f"対象 repo: {project_path}",
                "current ready issue: #123 sample browser wording cleanup",
                "override 理由: runtime recovery のため今回だけ短い補足を足したい",
                "今回やってほしいこと: sample browser の軽い UI polish に留める",
                "触らないこと: schema / resolver / preview / playback / export",
                "bridge が機械処理できる現行 issue-centric contract で返答してください。",
            ]
        ),
        "\n".join(
            [
                f"対象案件: {project_name}",
                "backlog home: #10 runtime touchpoint migration",
                "override 理由: ready issue を切る前に 1 回だけ探索したい",
                "今回やってほしいこと: current runtime の initial entry boundary だけ確認したい",
                "bridge が機械処理できる現行 issue-centric contract で返答してください。",
            ]
        ),
        "\n".join(
            [
                f"対象 repo: {project_path}",
                "current ready issue: #123",
                "override 理由: urgent one-point correction",
                "今回やってほしいこと: [ここを短く入力]",
                "触らないこと: [あれば短く入力]",
                "bridge が機械処理できる現行 issue-centric contract で返答してください。",
            ]
        ),
    ]


def prompt_ready_issue_reference(project_path: Path) -> str:
    print("通常入口では、current ready issue の参照を 1 行で入力してください。", flush=True)
    print(f"target repo: {project_path}", flush=True)
    print("例: #123 sample browser wording cleanup", flush=True)
    print("この参照をもとに、bridge が最小の初回 request を組み立てて issue-centric 返答契約を追記します。", flush=True)
    print("free-form 初回本文は通常入口ではありません。ready issue を使えない時だけ空入力で override へ進んでください。", flush=True)
    try:
        return input().strip()
    except EOFError:
        return ""


def prompt_override_request_body(example_texts: list[str]) -> str:
    print("free-form 初回本文の override 入力へ切り替えます。", flush=True)
    print("これは exception / recovery / override 用です。通常入口の代わりとしては使いません。", flush=True)
    print("open ready issue があるなら本文内でもそれを指してください。", flush=True)
    print("ここで入力した本文がそのまま ChatGPT へ送られます。これが override request の runtime 入力正本です。", flush=True)
    print("bridge は本文を改変せず、送信直前に issue-centric 返答契約だけを追記します。", flush=True)
    print("これは初回 request 専用で、human_review / need_info 再開時の補足入力とは別です。", flush=True)
    print("返答フォーマット指定まで自分で書く必要はありません。進めたい内容だけを書いてください。", flush=True)
    print("以下の短い override 例文を、そのまま少し書き換えて使えます。", flush=True)
    print("", flush=True)
    for index, example_text in enumerate(example_texts, start=1):
        print(f"[例 {index}]", flush=True)
        print(example_text, flush=True)
        print("", flush=True)
    print("bridge が issue-centric 返答契約を自動で付けるので、本文には今回進めたいことだけを入れてください。", flush=True)
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


def compose_ready_issue_request_text(ready_issue_ref: str, project_path: Path) -> str:
    normalized_ref = normalize_ready_issue_ref(ready_issue_ref)
    if not normalized_ref:
        raise BridgeError("current ready issue 参照が空です。")
    project_name = project_path.name or project_path.as_posix()
    contract_section = build_issue_centric_reply_contract_section()
    body = "\n".join(
        [
            f"対象案件: {project_name}",
            f"対象 repo: {project_path}",
            f"current ready issue: {normalized_ref}",
            "ready issue を今回の実行単位正本として使う",
            "この ready issue の範囲から広げず、次の 1 回分だけ判断してください。",
            "必要なら `codex_run` を選び、`target_issue` には必ず current ready issue の番号だけを使ってください。",
            f"この request では `target_issue` は {normalized_ref.split()[0]} に固定されます。",
            "closed や stale な別 issue を target_issue にしてはいけません。",
            "follow-up issue や parent issue の話に広げてはいけません。",
        ]
    )
    return f"{body}\n\n{contract_section}\n"


def compose_override_request_text(user_body: str) -> str:
    body = user_body.strip()
    if not body:
        raise BridgeError("override request 本文が空です。")
    contract_section = build_issue_centric_reply_contract_section()
    return f"{body}\n\n{contract_section}\n"


def compose_initial_selection_request_text(project_path: Path) -> str:
    """Return request text asking ChatGPT to select ONE ready issue from open issues.

    This request uses ``initial_selection:`` source prefix so that
    ready-issue binding validation is NOT applied.  The intent is issue
    selection only — execution starts in the next request.
    """
    project_name = project_path.name or project_path.as_posix()
    contract_section = build_issue_centric_reply_contract_section()
    body = "\n".join(
        [
            f"対象案件: {project_name}",
            f"対象 repo: {project_path}",
            "今回のお願い: open issue の中から、次に着手するのが最も自然な ready issue を 1 件だけ選んでください。",
            "選んだ issue を `target_issue` に入れて `no_action` で返してください。",
            "実装の判断や codex_run の指示は今回しないでください。",
            "選定理由は `summary` に短く書いてください。",
        ]
    )
    return f"{body}\n\n{contract_section}\n"


def build_ready_issue_request_source(ready_issue_ref: str) -> str:
    return f"ready_issue:{stable_text_hash(normalize_ready_issue_ref(ready_issue_ref))}"


def build_override_request_source(user_body: str) -> str:
    return f"override:{stable_text_hash(user_body.strip())}"


def build_initial_selection_request_source(project_path: str) -> str:
    return f"initial_selection:{stable_text_hash(project_path.strip())}"


def request_source_kind(request_source: str) -> str:
    if request_source.startswith("ready_issue:"):
        return "ready_issue"
    if request_source.startswith(("override:", "initial:")):
        return "override"
    if request_source.startswith("initial_selection:"):
        return "initial_selection"
    return "initial"


def request_log_prefixes(request_source: str) -> tuple[str, str]:
    kind = request_source_kind(request_source)
    if kind == "ready_issue":
        return "prepared_prompt_request_from_ready_issue", "sent_prompt_request_from_ready_issue"
    if kind == "override":
        return "prepared_prompt_request_from_override", "sent_prompt_request_from_override"
    if kind == "initial_selection":
        return "prepared_prompt_request_from_initial_selection", "sent_prompt_request_from_initial_selection"
    return "prepared_prompt_request", "sent_prompt_request"


def request_source_label(request_source: str) -> str:
    kind = request_source_kind(request_source)
    if kind == "ready_issue":
        return "ready issue 参照"
    if kind == "override":
        return "free-form override"
    if kind == "initial_selection":
        return "初回 issue 選定"
    return "初回 request"


def build_initial_request(args: argparse.Namespace) -> tuple[str, str, str, str]:
    """Return (request_text, request_hash, request_source, ready_issue_ref).

    ``ready_issue_ref`` is the normalized ready issue ref when the request was built
    from a ready_issue path (non-empty), otherwise empty string.  It is saved to state
    as ``current_ready_issue_ref`` so that continuation requests can detect a fresh-start
    context and prevent carry-over from a previous issue's ``last_issue_centric_*`` state.
    """
    ready_issue_ref = normalize_ready_issue_ref(args.ready_issue_ref)
    request_body = args.request_body.strip()
    project_path = resolve_project_path(args.project_path)

    select_issue = bool(getattr(args, "select_issue", False))
    if select_issue and (ready_issue_ref or request_body):
        raise BridgeError("`--select-issue` は `--ready-issue-ref` / `--request-body` と同時に使えません。")

    if select_issue:
        request_text = compose_initial_selection_request_text(project_path)
        request_source = build_initial_selection_request_source(str(project_path))
        return request_text, stable_text_hash(request_text), request_source, ""

    if ready_issue_ref and request_body:
        raise BridgeError("`--ready-issue-ref` と `--request-body` は同時に使えません。通常入口か override のどちらか 1 つを選んでください。")

    if ready_issue_ref:
        request_text = compose_ready_issue_request_text(ready_issue_ref, project_path)
        return request_text, stable_text_hash(request_text), build_ready_issue_request_source(ready_issue_ref), ready_issue_ref

    if request_body:
        request_text = compose_override_request_text(request_body)
        return request_text, stable_text_hash(request_text), build_override_request_source(request_body), ""

    if sys.stdin is not None and not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()
        if not stdin_text:
            raise BridgeError(
                "初回 override 本文が空です。"
                " 通常入口では `--ready-issue-ref` を使うか、例外経路なら `--request-body` または対話入力で本文を渡してください。"
            )
        request_text = compose_override_request_text(stdin_text)
        return request_text, stable_text_hash(request_text), build_override_request_source(stdin_text), ""

    interactive_ready_issue_ref = normalize_ready_issue_ref(prompt_ready_issue_reference(project_path))
    if interactive_ready_issue_ref:
        request_text = compose_ready_issue_request_text(interactive_ready_issue_ref, project_path)
        return request_text, stable_text_hash(request_text), build_ready_issue_request_source(interactive_ready_issue_ref), interactive_ready_issue_ref

    override_text = prompt_override_request_body(build_override_example_templates(project_path))
    if not override_text.strip():
        raise BridgeError(
            "初回 override 本文が空です。"
            " 通常入口では current ready issue の参照を入力するか、例外経路なら `--request-body` で本文を渡してください。"
        )
    request_text = compose_override_request_text(override_text)
    return request_text, stable_text_hash(request_text), build_override_request_source(override_text), ""


def load_retryable_initial_request(state: dict[str, object]) -> tuple[str, str, str] | None:
    if str(state.get("pending_request_source", "")).strip():
        return None
    prepared_source = str(state.get("prepared_request_source", "")).strip()
    prepared_hash = str(state.get("prepared_request_hash", "")).strip()
    if not can_reuse_prepared_request(state) or not prepared_source.startswith(("ready_issue:", "override:", "initial:", "initial_selection:")):
        return None
    prepared_text = read_prepared_request_text(state)
    if not prepared_text:
        return None
    return prepared_text, prepared_hash or stable_text_hash(prepared_text), prepared_source


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    retryable_request = None if (args.request_body.strip() or args.ready_issue_ref.strip()) else load_retryable_initial_request(state)
    if retryable_request is not None:
        request_text, request_hash, request_source = retryable_request
        # When retrying a previously prepared ready_issue request, recover the pinned ref
        # from state so continuation requests can still detect the fresh-start context.
        raw_ready_issue_ref = str(state.get("current_ready_issue_ref", "")).strip()
        prepared_status = str(state.get("prepared_request_status", "")).strip()
        if prepared_status == "prepared":
            print(f"request: prepared の {request_source_label(request_source)} entry request を再生成せず送信します。")
        else:
            print(f"request: 前回未送信の {request_source_label(request_source)} entry request を再送します。")
    else:
        request_text, request_hash, request_source, raw_ready_issue_ref = build_initial_request(args)

    if (
        str(state.get("mode", "")).strip() in {"waiting_prompt_reply", "extended_wait", "await_late_completion"}
        and str(state.get("pending_request_source", "")).strip() == request_source
    ):
        print(f"request: 同じ {request_source_label(request_source)} entry request は送信済みのため再送しませんでした。")
        if str(state.get("pending_request_log", "")).strip():
            print(f"pending: {state.get('pending_request_log', '')}")
        return 0

    prepared_prefix, sent_prefix = request_log_prefixes(request_source)
    prepared_log = log_text(prepared_prefix, request_text)
    prepared_state = clear_error_fields(dict(state))
    stage_prepared_request(
        prepared_state,
        request_hash=request_hash,
        request_source=request_source,
        request_log=repo_relative(prepared_log),
    )
    prepared_state["current_ready_issue_ref"] = raw_ready_issue_ref
    save_state(prepared_state)

    try:
        send_result = send_initial_request_to_chatgpt(request_text)
    except Exception:
        retry_state = clear_error_fields(dict(state))
        stage_prepared_request(
            retry_state,
            request_hash=request_hash,
            request_source=request_source,
            request_log=repo_relative(prepared_log),
            status="retry_send",
        )
        retry_state["current_ready_issue_ref"] = raw_ready_issue_ref
        save_state(retry_state)
        raise

    transport_log = log_text(
        f"{sent_prefix}_transport",
        json.dumps(
            {
                "signal": str(send_result.get("signal", "")),
                "url": str(send_result.get("url", "")),
                "title": str(send_result.get("title", "")),
                "match_kind": str(send_result.get("match_kind", "")),
                "matched_hint": str(send_result.get("matched_hint", "")),
                "project_name": str(send_result.get("project_name", "")),
                "github_source_attach_status": str(send_result.get("github_source_attach_status", "")),
                "github_source_attach_boundary": str(send_result.get("github_source_attach_boundary", "")),
                "github_source_attach_detail": str(send_result.get("github_source_attach_detail", "")),
                "github_source_attach_context": str(send_result.get("github_source_attach_context", "")),
                "github_source_attach_log": str(send_result.get("github_source_attach_log", "")),
                "request_send_continued_without_github_source": bool(
                    send_result.get("request_send_continued_without_github_source")
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    request_log = log_text(sent_prefix, request_text)
    mutable_state = clear_error_fields(dict(state))
    clear_pending_request_fields(mutable_state)
    clear_prepared_request_fields(mutable_state)
    promote_pending_request(
        mutable_state,
        request_hash=request_hash,
        request_source=request_source,
        request_log=repo_relative(request_log),
    )
    mutable_state.update(
        {
            "pending_request_signal": str(send_result.get("signal", "")),
            "current_chat_session": str(send_result.get("url", "")),
            "github_source_attach_status": str(send_result.get("github_source_attach_status", "")),
            "github_source_attach_boundary": str(send_result.get("github_source_attach_boundary", "")),
            "github_source_attach_detail": str(send_result.get("github_source_attach_detail", "")),
            "github_source_attach_context": str(send_result.get("github_source_attach_context", "")),
            "github_source_attach_log": str(repo_relative(transport_log)),
            "request_send_continued_without_github_source": bool(
                send_result.get("request_send_continued_without_github_source")
            ),
            "current_ready_issue_ref": raw_ready_issue_ref,
        }
    )
    save_state(mutable_state)

    print(f"sent: {request_log}")
    print(f"request transport: {transport_log}")
    return 0


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
