#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import sys

import run_until_stop
from _bridge_common import (
    clear_error_fields,
    format_lifecycle_sync_state_note,
    has_pending_issue_centric_codex_dispatch,
    save_state,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "bridge の通常入口です。\n"
            "通常は current ready issue の参照から始め、その後は report ベースで継続します。"
        ),
        epilog=(
            "よく使う例:\n"
            "  python3 scripts/start_bridge.py --project-path /ABSOLUTE/PATH/TO/repo --ready-issue-ref '#123 sample browser wording cleanup' --max-execution-count 6\n"
            "  python3 scripts/start_bridge.py --status --project-path /ABSOLUTE/PATH/TO/repo\n"
            "  python3 scripts/start_bridge.py --doctor --project-path /ABSOLUTE/PATH/TO/repo\n\n"
            "通常入口では ready issue の参照を使います。free-form 初回本文は exception / recovery / override 用で、bridge は本文を改変せず reply contract だけを追加します。"
        ),
    )
    parser.add_argument(
        "--project-path",
        help="Codex worker を動かす target repo の絶対パス",
    )
    parser.add_argument(
        "--ready-issue-ref",
        default="",
        help="通常入口で使う current ready issue の参照。例: '#123 sample browser wording cleanup'",
    )
    parser.add_argument(
        "--request-body",
        default="",
        help="例外 / recovery / override 用の初回本文。通常入口の代替としては使わない",
    )
    parser.add_argument(
        "--max-execution-count",
        type=int,
        default=run_until_stop.DEFAULT_MAX_STEPS,
        help="この実行で進める最大手数の上限",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="実行せず、今の状況と次の 1 手だけを短く確認する",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="bridge が再開可能と言っている時に、そのまま続きから進める",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="止まった理由と、resume / clear-error / 待機のどれがよいかを確認する",
    )
    parser.add_argument(
        "--clear-error",
        "--reset",
        action="store_true",
        dest="clear_error",
        help="bridge 側の recoverable error だけを解除する。doctor や stop summary が勧めた時だけ使う",
    )
    return parser.parse_args(argv)


def build_derived_args(args: argparse.Namespace) -> argparse.Namespace:
    project_config = run_until_stop.load_project_config()
    browser_config = run_until_stop.load_browser_config()
    return run_until_stop.parse_args(
        (
            [
            "--project-path",
            args.project_path or str(run_until_stop.worker_repo_path(project_config)),
            "--max-execution-count",
            str(args.max_execution_count),
            "--fetch-timeout-seconds",
            str(run_until_stop.browser_fetch_timeout_seconds(browser_config)),
            "--entry-script",
            "scripts/start_bridge.py",
        ]
            + (["--ready-issue-ref", args.ready_issue_ref] if args.ready_issue_ref else [])
            + (["--request-body", args.request_body] if args.request_body else [])
        ),
        project_config,
    )


def print_resume_overview(args: argparse.Namespace) -> None:
    state = run_until_stop.load_state()
    derived_args = build_derived_args(args)
    mode_label = run_until_stop.start_bridge_mode(state)
    status_label, guidance, note = run_until_stop.start_bridge_resume_guidance(derived_args, state)
    recommendation_label, recommended_command = run_until_stop.recommended_operator_step(derived_args, state)
    print("bridge status:", flush=True)
    print(f"- 現在の状況: {status_label}", flush=True)
    print(f"- 再開のしかた: {mode_label}", flush=True)
    print(f"- 次に起きること: {guidance}", flush=True)
    print(f"- まずやること: {recommendation_label}", flush=True)
    print(f"- おすすめ 1 コマンド: {recommended_command}", flush=True)
    print(f"- 先に見るもの: {note}", flush=True)
    if mode_label == "先に確認が必要です":
        print("- このまま再実行する前に、stop summary と doctor の案内を確認してください。", flush=True)
    else:
        print("- 同じコマンドでそのまま再開して大丈夫です。", flush=True)


def print_doctor(args: argparse.Namespace) -> None:
    state = run_until_stop.load_state()
    derived_args = build_derived_args(args)
    mode_label = run_until_stop.start_bridge_mode(state)
    status_label, guidance, note = run_until_stop.start_bridge_resume_guidance(derived_args, state)
    recommendation_label, recommended_command = run_until_stop.recommended_operator_step(derived_args, state)
    report_ready = run_until_stop.codex_report_is_ready(run_until_stop.runtime_report_path())
    prompt_ready = run_until_stop.runtime_prompt_path().exists()
    stop_exists = run_until_stop.runtime_stop_path().exists()
    backup_count = len(list(run_until_stop.bridge_runtime_root().glob("state.json.bak.*")))
    pending_request_log_raw = str(state.get("pending_request_log", "")).strip()
    rotation_requested = run_until_stop.should_rotate_before_next_chat_request(state)
    pending_handoff_log_raw = str(state.get("pending_handoff_log", "")).strip() if rotation_requested else ""
    pending_request_log = pending_request_log_raw or "なし"
    pending_handoff_log = pending_handoff_log_raw or "なし"
    error_message = str(state.get("error_message", "")).strip()
    if stop_exists:
        clear_error_status = "不可: bridge/STOP があるため"
    elif bool(state.get("pause")):
        clear_error_status = "不可: pause=true のため"
    elif report_ready and run_until_stop.should_prioritize_unarchived_report(state):
        clear_error_status = "不要: 未退避 report を優先して archive 側へ戻してください"
    elif pending_handoff_log_raw:
        clear_error_status = "可能: project ページ確認後に clear-error で同じ handoff の入力確認と送信確認へ戻せます"
    elif run_until_stop.is_apple_event_timeout_text(error_message):
        clear_error_status = "可能: Safari current tab と Automation を確認後に clear-error で再開候補へ戻せます"
    elif bool(state.get("error")):
        clear_error_status = "可能: 停止要因を直した後に clear-error で再開候補へ寄せられます"
    else:
        clear_error_status = "不要: error 停止ではありません"
    print("bridge doctor:", flush=True)
    print(f"- 現在の状況: {status_label}", flush=True)
    print(f"- 判定: {recommendation_label}", flush=True)
    print(f"- おすすめ 1 コマンド: {recommended_command}", flush=True)
    print(f"- 次に起きること: {guidance}", flush=True)
    print(f"- まず見るもの: {note}", flush=True)
    print("- 詳細診断:", flush=True)
    print(f"  - 再開のしかた: {mode_label}", flush=True)
    print(f"  - prompt保存: {'あり' if prompt_ready else 'なし'}", flush=True)
    print(f"  - report保存: {'あり' if report_ready else 'なし'}", flush=True)
    print(f"  - pending_request_log: {pending_request_log}", flush=True)
    print(f"  - pending_handoff_log: {pending_handoff_log}", flush=True)
    print(f"  - stop_file: {'present' if stop_exists else 'absent'}", flush=True)
    print(f"  - pause: {'あり' if bool(state.get('pause')) else 'なし'}", flush=True)
    print(f"  - bridge_error: {'あり' if bool(state.get('error')) else 'なし'}", flush=True)
    print(f"  - state_backups: {backup_count} (削除不要)", flush=True)
    print(f"  - lifecycle_sync_state: {format_lifecycle_sync_state_note(state)}", flush=True)
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


def recover_resume_from_pending_issue_centric_codex_dispatch(args: argparse.Namespace) -> bool:
    """Clear error only for --resume when pending issue-centric codex dispatch is reconstructable."""
    if not args.resume or args.status or args.doctor or args.clear_error:
        return False

    current_state = run_until_stop.load_state()
    if not bool(current_state.get("error")):
        return False
    if not has_pending_issue_centric_codex_dispatch(current_state):
        return False

    try:
        bridge_orchestrator = importlib.import_module("bridge_orchestrator")
        bridge_orchestrator.load_pending_issue_centric_codex_materialized(dict(current_state))
    except Exception:
        return False

    save_state(clear_error_fields(dict(current_state)))
    print(
        "bridge resume: recoverable pending issue-centric codex dispatch が再構成可能なため、"
        " error を解除してそのまま再開します。",
        flush=True,
    )
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_config = run_until_stop.load_project_config()
    effective_project_path = args.project_path or str(run_until_stop.worker_repo_path(project_config))
    project_path_display = args.project_path or f"(引数未指定; 既定値 {effective_project_path})"
    if args.status:
        print_resume_overview(args)
        return 0
    if args.doctor:
        print_doctor(args)
        return 0
    if args.clear_error:
        return clear_error_for_resume(args)
    recover_resume_from_pending_issue_centric_codex_dispatch(args)
    print("bridge start: このコマンドが通常入口です。", flush=True)
    print(f"- project_path: {project_path_display}", flush=True)
    print(f"- max_execution_count: {args.max_execution_count}", flush=True)
    if args.ready_issue_ref:
        print(f"- ready_issue_ref: {args.ready_issue_ref}", flush=True)
        print("- 通常入口として、この ready issue 参照を使って最初の ChatGPT request を組み立てます。", flush=True)
    elif args.request_body:
        print("- free-form override: 指定された初回本文を例外経路として使います。", flush=True)
    else:
        print("- 通常入口では、current ready issue の参照を受けて最初の ChatGPT request を組み立てます。", flush=True)
    print("- free-form 初回本文は exception / recovery / override 用にだけ残しています。", flush=True)
    print("- 2 回目以降は report ベースで継続し、通常は同じチャットを使います。", flush=True)
    if args.resume:
        print("- resume: ここから続きとして進めます。", flush=True)
    print_resume_overview(args)
    forwarded_argv = [
        "--project-path",
        effective_project_path,
        "--max-execution-count",
        str(args.max_execution_count),
        "--entry-script",
        "scripts/start_bridge.py",
    ]
    if args.ready_issue_ref:
        forwarded_argv.extend(["--ready-issue-ref", args.ready_issue_ref])
    if args.request_body:
        forwarded_argv.extend(["--request-body", args.request_body])
    return run_until_stop.run(forwarded_argv)


if __name__ == "__main__":
    sys.exit(main())
