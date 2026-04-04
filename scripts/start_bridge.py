#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

import run_until_stop
from _bridge_common import clear_error_fields, save_state


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "bridge の通常入口です。project path と max execution count を渡して起動します。"
            " 初回だけ最初の ChatGPT request 本文を入力し、その後は必要時だけ再実行します。"
            " --status / --resume / --doctor / --clear-error で軽い運用保守もできます。"
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
    parser.add_argument(
        "--resume",
        action="store_true",
        help="通常実行と同じ。続きから再開することを明示したい時に使う",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="runtime の軽い診断だけを行い、再開前に見るべき点を短く表示する",
    )
    parser.add_argument(
        "--clear-error",
        "--reset",
        action="store_true",
        dest="clear_error",
        help="bridge 側の error / error_message だけを最小解除し、再開可能ならその状態へ寄せる",
    )
    return parser.parse_args(argv)


def build_derived_args(args: argparse.Namespace) -> argparse.Namespace:
    project_config = run_until_stop.load_project_config()
    browser_config = run_until_stop.load_browser_config()
    return run_until_stop.parse_args(
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


def print_resume_overview(args: argparse.Namespace) -> None:
    state = run_until_stop.load_state()
    derived_args = build_derived_args(args)
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


def print_doctor(args: argparse.Namespace) -> None:
    state = run_until_stop.load_state()
    derived_args = build_derived_args(args)
    mode_label = run_until_stop.start_bridge_mode(state)
    status_label, guidance, note = run_until_stop.start_bridge_resume_guidance(derived_args, state)
    report_ready = run_until_stop.codex_report_is_ready(run_until_stop.runtime_report_path())
    prompt_ready = run_until_stop.runtime_prompt_path().exists()
    stop_exists = run_until_stop.runtime_stop_path().exists()
    backup_count = len(list(run_until_stop.bridge_runtime_root().glob("state.json.bak.*")))
    pending_request_log = str(state.get("pending_request_log", "")).strip() or "なし"
    pending_handoff_log = str(state.get("pending_handoff_log", "")).strip() or "なし"
    if stop_exists:
        clear_error_status = "不可: bridge/STOP があるため"
    elif bool(state.get("pause")):
        clear_error_status = "不可: pause=true のため"
    elif bool(state.get("error")):
        clear_error_status = "可能: bridge 側の error だけを解除して再開候補へ寄せられます"
    else:
        clear_error_status = "不要: error 停止ではありません"
    print("bridge doctor:", flush=True)
    print(f"- 入口判断: {mode_label}", flush=True)
    print(f"- 現在の状況: {status_label}", flush=True)
    print(f"- 次に起きること: {guidance}", flush=True)
    print(f"- 次に見るもの: {note}", flush=True)
    print(f"- prompt_ready: {'yes' if prompt_ready else 'no'}", flush=True)
    print(f"- report_ready: {'yes' if report_ready else 'no'}", flush=True)
    print(f"- pending_request_log: {pending_request_log}", flush=True)
    print(f"- pending_handoff_log: {pending_handoff_log}", flush=True)
    print(f"- stop_file: {'present' if stop_exists else 'absent'}", flush=True)
    print(f"- pause: {'あり' if bool(state.get('pause')) else 'なし'}", flush=True)
    print(f"- bridge_error: {'あり' if bool(state.get('error')) else 'なし'}", flush=True)
    print(f"- state_backups: {backup_count} (削除不要)", flush=True)
    print(f"- clear_error: {clear_error_status}", flush=True)
    print("- logs / history / prompt / report は doctor では変更しません。", flush=True)


def clear_error_for_resume(args: argparse.Namespace) -> int:
    state = run_until_stop.load_state()
    if run_until_stop.runtime_stop_path().exists():
        print("bridge clear-error: bridge/STOP があるため変更しません。先に STOP を外してください。", flush=True)
        return 1
    if bool(state.get("pause")):
        print("bridge clear-error: pause=true のため変更しません。先に pause を解除してください。", flush=True)
        return 1

    prompt_path = run_until_stop.runtime_prompt_path()
    recovered_state, recovered_report = run_until_stop.recover_report_ready_state(state, prompt_path=prompt_path)
    recovered_state, recovered_handoff = run_until_stop.recover_pending_handoff_state(recovered_state)
    current_state = run_until_stop.load_state() if (recovered_report is not None or recovered_handoff) else recovered_state

    if not bool(current_state.get("error")):
        print("bridge clear-error: 解除対象の error はありません。現在の状態でそのまま再開できます。", flush=True)
        print_resume_overview(args)
        return 0

    updated_state = clear_error_fields(dict(current_state))
    save_state(updated_state)
    print("bridge clear-error: error と error_message だけを解除しました。", flush=True)
    print("- prompt / report / handoff / logs / backups は残しています。", flush=True)
    print_resume_overview(args)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_config = run_until_stop.load_project_config()
    effective_project_path = args.project_path or str(run_until_stop.worker_repo_path(project_config))
    project_path_display = args.project_path or f"(引数未指定; 既定値 {effective_project_path})"
    print("bridge start: このコマンドが通常入口です。", flush=True)
    print(f"- project_path: {project_path_display}", flush=True)
    print(f"- max_execution_count: {args.max_execution_count}", flush=True)
    print("- 初回だけ依頼文を入力します。2 回目以降は同じコマンドで継続できます。", flush=True)
    print("- 内部 state ではなく、人向け表示と handoff を見ながら進める想定です。", flush=True)
    if args.status:
        print_resume_overview(args)
        return 0
    if args.resume:
        print("- resume: 同じコマンドで続きから再開します。", flush=True)
    if args.doctor:
        print_doctor(args)
        return 0
    if args.clear_error:
        return clear_error_for_resume(args)
    print_resume_overview(args)
    forwarded_argv = [
        "--project-path",
        effective_project_path,
        "--max-execution-count",
        str(args.max_execution_count),
        "--entry-script",
        "scripts/start_bridge.py",
    ]
    return run_until_stop.run(forwarded_argv)


if __name__ == "__main__":
    sys.exit(main())
