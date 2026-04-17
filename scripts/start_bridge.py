#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import sys

import run_until_stop
from _bridge_common import (
    clear_error_fields,
    clear_pending_request_fields,
    format_lifecycle_sync_state_note,
    has_pending_issue_centric_codex_dispatch,
    is_initial_bridge_state,
    load_state,
    resolve_start_resume_entry_action,
    resolve_unified_next_action,
    save_state,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "bridge の通常入口です。\n"
            "初期状態では自動で issue selection から始まり、その後は resume 優先で継続します。"
        ),
        epilog=(
            "よく使う例:\n"
            "  python3 scripts/start_bridge.py --project-path /ABSOLUTE/PATH/TO/repo\n"
            "  python3 scripts/start_bridge.py --project-path /ABSOLUTE/PATH/TO/repo --ready-issue-ref '#123 sample browser wording cleanup' --max-execution-count 6\n"
            "  python3 scripts/start_bridge.py --status --project-path /ABSOLUTE/PATH/TO/repo\n"
            "  python3 scripts/start_bridge.py --doctor --project-path /ABSOLUTE/PATH/TO/repo\n\n"
            "初期状態（state.json が brand-new）では --ready-issue-ref 不要で自動 issue selection に入ります。\n"
            "再開可能なら resume を優先します。--ready-issue-ref は明示指定用の入口です。\n"
            "--request-body は exception / recovery / override 専用で、通常起動では不要です。"
        ),
    )
    parser.add_argument(
        "--project-path",
        help="Codex worker を動かす target repo の絶対パス",
    )
    parser.add_argument(
        "--ready-issue-ref",
        default="",
        help="明示指定用の入口。ready issue を指定して進めたい場合に使う。例: '#123 sample browser wording cleanup'\n初期状態では省略してよい（自動で issue selection フローに入る）",
    )
    parser.add_argument(
        "--request-body",
        default="",
        help="exception / recovery / override 専用の初回本文。通常起動では不要",
    )
    parser.add_argument(
        "--select-issue",
        action="store_true",
        default=False,
        help="issue selection モードを明示指定する。初期状態では自動でこのモードに入るため、通常は不要",
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
        action="store_true",
        dest="clear_error",
        help="recoverable error だけを解除して resume できる状態へ戻す。reset とは別物。doctor や stop summary が勧めた時だけ使う",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        dest="reset",
        help="state.json を完全初期化する（mode=idle + need_chatgpt_prompt=True）。clear-error とは別物。通常は使わない",
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
            + (["--select-issue"] if getattr(args, "select_issue", False) else [])
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
    print("- clear-error は error だけを解除して resume に戻す操作です（state 全体の reset ではありません）。", flush=True)
    print("- reset が必要な場合は --reset を使ってください（state.json を完全初期化します）。", flush=True)
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


def _is_safe_stale_fallback_state(state: dict) -> bool:
    """Return True when the state is a stale no-action fallback with nothing genuinely pending.

    This detects the case where a previous run (e.g. rehearsal) left the state at
    mode=awaiting_user with old issue-centric fields, which causes resolve_unified_next_action()
    to return 'no_action' and blocks fresh-start for a new project.

    Safe to reset when ALL of the following hold:
      - resolve_unified_next_action() == 'no_action'  (bridge is already stuck)
      - error=false, pause=false                       (no error recovery in progress)
      - no pending_request_hash                        (no unsent request)
      - no pending_handoff_hash                        (no handoff in flight)
      - no last_issue_centric_pending_generation_id    (no pending Codex run)
      - no last_issue_centric_prepared_generation_id   (no prepared Codex body)
      - mode NOT in active Codex lifecycle states
    """
    _active_codex_modes = {"ready_for_codex", "codex_running", "codex_done"}
    if str(state.get("mode", "")).strip() in _active_codex_modes:
        return False
    if bool(state.get("error")):
        return False
    if bool(state.get("pause")):
        return False
    if str(state.get("pending_request_hash", "")).strip():
        return False
    if str(state.get("pending_handoff_hash", "")).strip():
        return False
    if str(state.get("last_issue_centric_pending_generation_id", "")).strip():
        return False
    if str(state.get("last_issue_centric_prepared_generation_id", "")).strip():
        return False
    return resolve_unified_next_action(state) == "no_action"


def _is_stale_ready_issue_awaiting_state(state: dict) -> bool:
    """Return True when the state is a stale awaiting_user state from a ready_issue: run.

    This detects the case where a previous ready_issue run (or invalid-contract recovery)
    left mode=awaiting_user with an old ready_issue: pending request, causing
    resolve_unified_next_action() to return 'no_action' and blocking --select-issue
    fresh-start.

    Safe to reset when ALL of the following hold:
      - mode == "awaiting_user"                              (not waiting_prompt_reply)
      - pending_request_source starts with "ready_issue:"   (initial request, not continuation)
      - error=false, pause=false                            (no error recovery in progress)
      - resolve_unified_next_action() == 'no_action'        (bridge is already stuck)

    Genuine awaiting_user states (chatgpt_decision=human_review/need_info or
    issue_centric:codex_run) are not affected because they resolve to
    request_prompt_from_report or dispatch_issue_centric_codex_run, not no_action.
    """
    if str(state.get("mode", "")).strip() != "awaiting_user":
        return False
    if not str(state.get("pending_request_source", "")).strip().startswith("ready_issue:"):
        return False
    if bool(state.get("error")):
        return False
    if bool(state.get("pause")):
        return False
    return resolve_unified_next_action(state) == "no_action"


def reset_stale_fallback_for_fresh_start(args: argparse.Namespace) -> bool:
    """Reset stale no-action fallback state to allow fresh-start on the new target repo.

    When start_bridge.py is called for a new project (e.g. PromptWeave after rehearsal),
    the old state.json may have mode=awaiting_user + stale issue-centric fields that
    cause resolve_unified_next_action() to return 'no_action', blocking fresh-start.

    If the state is safe to reset (nothing genuinely pending), transition to
    mode=idle + need_chatgpt_prompt=True so the normal fresh-start path proceeds.

    Returns True if a reset was applied, False otherwise.
    """
    if args.status or args.doctor or args.clear_error:
        return False
    state = load_state()
    is_safe_stale = _is_safe_stale_fallback_state(state)
    is_stale_ready_issue = not is_safe_stale and _is_stale_ready_issue_awaiting_state(state)
    if not is_safe_stale and not is_stale_ready_issue:
        return False
    updated = dict(state)
    if is_stale_ready_issue:
        clear_pending_request_fields(updated)
        updated["last_issue_centric_pending_generation_id"] = ""
        print(
            "bridge start: stale ready_issue: pending state (awaiting_user / no_action) を検出しました。"
            " pending request を解除して fresh-start へ進みます。",
            flush=True,
        )
    else:
        print(
            "bridge start: stale fallback state (no_action / 保留なし) を検出しました。"
            " mode=idle + need_chatgpt_prompt=True にリセットして fresh-start へ進みます。",
            flush=True,
        )
    updated["mode"] = "idle"
    updated["need_chatgpt_prompt"] = True
    save_state(updated)
    return True


def reset_state_for_fresh_start(args: argparse.Namespace) -> int:
    """--reset: Unconditionally reset state.json to the initial default state."""
    from _bridge_common import DEFAULT_STATE  # noqa: PLC0415

    state = load_state()
    if bool(state.get("pending_request_hash", "")):
        print(
            "bridge reset: pending_request_hash が残っています。"
            " 未送信リクエストが失われる可能性があります。",
            flush=True,
        )
    save_state(DEFAULT_STATE.copy())
    print("bridge reset: state.json を初期状態 (mode=idle + need_chatgpt_prompt=True) にリセットしました。", flush=True)
    print("- pending / prepared / error / pause フィールドはすべて初期値に戻りました。", flush=True)
    return 0


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
    if getattr(args, "reset", False):
        return reset_state_for_fresh_start(args)
    recover_resume_from_pending_issue_centric_codex_dispatch(args)
    reset_stale_fallback_for_fresh_start(args)

    # --- start / resume entry routing ---
    # When no explicit override args are given, use resolve_start_resume_entry_action
    # to determine the appropriate entry action.
    has_explicit_request = bool(args.ready_issue_ref or args.request_body or getattr(args, "select_issue", False))
    if not has_explicit_request:
        current_state = run_until_stop.load_state()
        entry_action = resolve_start_resume_entry_action(current_state)
        if entry_action == "blocked_error":
            print(
                "bridge start: state.error=true のため実行を中止します。"
                " 先に --clear-error または --doctor で状態を確認してください。",
                flush=True,
            )
            return 1
        elif entry_action == "blocked_pause":
            print(
                "bridge start: state.pause=true のため実行を中止します。"
                " 先に pause を解除してください。",
                flush=True,
            )
            return 1
        elif entry_action == "resume_pending_reply":
            print(
                "bridge start: 送信済みリクエストへの返信待ち状態を検出しました。"
                " 返信待ちのまま続きから再開します（二重送信防止）。",
                flush=True,
            )
            # Fall through: run_until_stop will pick up waiting_prompt_reply → fetch_next_prompt
        elif entry_action == "resume_pending_handoff":
            print(
                "bridge start: pending handoff を検出しました。そのまま続きから再開します。",
                flush=True,
            )
        elif entry_action == "resume_prepared_request":
            print(
                "bridge start: 準備済みリクエストを検出しました。そのまま続きから再開します。",
                flush=True,
            )
        elif entry_action == "resume_issue_centric_codex_dispatch":
            print(
                "bridge start: pending issue-centric codex dispatch を検出しました。そのまま続きから再開します。",
                flush=True,
            )
        elif entry_action == "fresh_start_issue_selection":
            # Initial state: route to the proper initial_selection flow (--select-issue).
            # This ensures request_source becomes "initial_selection:*" (not "override:*"),
            # and fetch_next_prompt.py's existing initial_selection special handling applies.
            repo = str(project_config.get("github_repository", "") or effective_project_path)
            print(
                "bridge start: 初期状態を検出しました。issue-centric initial selection フローへ入ります。",
                flush=True,
            )
            print(f"- repo: {repo}", flush=True)
            # Use select_issue flag so build_initial_request() uses initial_selection: source.
            args.select_issue = True

    print("bridge start: このコマンドが通常入口です。", flush=True)
    print(f"- project_path: {project_path_display}", flush=True)
    print(f"- max_execution_count: {args.max_execution_count}", flush=True)
    if args.ready_issue_ref:
        print(f"- ready_issue_ref: {args.ready_issue_ref}", flush=True)
        print("- 明示指定された ready issue を使って最初の ChatGPT request を組み立てます。", flush=True)
    elif getattr(args, "select_issue", False):
        print("- issue selection モード: open issue から ready issue を 1 件選ばせます。実装開始は次の request で行います。", flush=True)
    elif args.request_body:
        print("- exception / recovery / override 経路: 指定された初回本文を使います。", flush=True)
    else:
        print("- resume 優先: 前回の状態から続きとして進めます。", flush=True)
    print("- --request-body は exception / recovery / override 用です。通常起動では不要です。", flush=True)
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
    if getattr(args, "select_issue", False):
        forwarded_argv.append("--select-issue")
    return run_until_stop.run(forwarded_argv)


if __name__ == "__main__":
    sys.exit(main())
