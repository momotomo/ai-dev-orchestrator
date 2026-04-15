#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from _bridge_common import (
    BRIDGE_DIR,
    BridgeStop,
    build_chatgpt_handoff_request,
    build_chatgpt_request,
    build_pinned_ready_issue_ic_section,
    can_reuse_prepared_request,
    clear_chat_rotation_fields,
    clear_error_fields,
    clear_pending_handoff_fields,
    clear_pending_request_fields,
    clear_prepared_request_fields,
    extract_last_chatgpt_handoff,
    guarded_main,
    load_project_config,
    log_text,
    prepare_issue_centric_runtime_mode,
    prepare_issue_centric_runtime_snapshot,
    present_resume_prompt,
    promote_pending_request,
    read_pending_handoff_text,
    read_prepared_request_text,
    read_last_report_text,
    repo_relative,
    resolve_issue_centric_route_choice,
    rotate_chat_with_handoff,
    send_to_chatgpt,
    save_state,
    stage_prepared_request,
    stable_text_hash,
    should_prioritize_unarchived_report,
    should_rotate_before_next_chat_request,
    wait_for_handoff_reply_text,
)

DEFAULT_NEXT_TODO = "前回 report を踏まえて、次の 1 フェーズ分の Codex 用 prompt を作成してください。"
DEFAULT_OPEN_QUESTIONS = "未解決事項があれば安全側で補ってください。"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    project_config = load_project_config()
    parser = argparse.ArgumentParser(description="Safari の現在 ChatGPT タブへ完了報告ベースの prompt request を送信します。")
    parser.add_argument(
        "--next-todo",
        default=str(project_config.get("report_request_next_todo", DEFAULT_NEXT_TODO)),
        help="次にやりたいこと",
    )
    parser.add_argument(
        "--open-questions",
        default=str(project_config.get("report_request_open_questions", DEFAULT_OPEN_QUESTIONS)),
        help="未解決事項",
    )
    parser.add_argument("--current-status", default="", help="CURRENT_STATUS の上書き")
    parser.add_argument("--resume-note", default="", help="human_review / need_info 再開時に添える補足入力")
    return parser.parse_args(argv)


def prompt_resume_note(state: dict[str, object]) -> str:
    resume_prompt = present_resume_prompt(state)
    print(resume_prompt.title, flush=True)
    print(resume_prompt.detail, flush=True)
    print("この入力は初回 request を上書きせず、次の ChatGPT request に添える補足だけとして使います。", flush=True)
    print("以下はそのまま使える短い例です。必要な行だけ書き換えてください。", flush=True)
    print("", flush=True)
    print(resume_prompt.example, flush=True)
    print("", flush=True)
    print("入力終了は Ctrl-D、または空行を 2 回です。空入力では送信しません。", flush=True)

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


def resolve_resume_note(state: dict[str, object], args: argparse.Namespace) -> str:
    if args.resume_note.strip():
        return args.resume_note.strip()

    if str(state.get("mode", "")).strip() != "awaiting_user":
        return ""

    if sys.stdin is not None and not sys.stdin.isatty():
        return sys.stdin.read().strip()

    return prompt_resume_note(state)


def build_report_request_source(state: dict[str, object], resume_note: str) -> str:
    last_report_file = str(state.get("last_report_file", "")).strip() or "unknown-report"
    if str(state.get("mode", "")).strip() == "awaiting_user":
        decision = str(state.get("chatgpt_decision", "")).strip() or "resume"
        resume_hash = stable_text_hash(resume_note.strip() or "no-note")
        return f"handoff:{decision}:{last_report_file}:{resume_hash}"
    principal_issue = str(state.get("last_issue_centric_principal_issue", "")).strip()
    if principal_issue:
        return f"report:{last_report_file}:issue:{principal_issue}"
    return f"report:{last_report_file}"


def load_retryable_prepared_request(state: dict[str, object]) -> tuple[str, str, str] | None:
    if str(state.get("pending_request_source", "")).strip():
        return None
    prepared_status = str(state.get("prepared_request_status", "")).strip()
    prepared_source = str(state.get("prepared_request_source", "")).strip()
    prepared_hash = str(state.get("prepared_request_hash", "")).strip()
    if not can_reuse_prepared_request(state):
        return None
    if not prepared_source.startswith(("report:", "handoff:", "human_review_continue:")):
        return None
    prepared_text = read_prepared_request_text(state)
    if not prepared_text:
        return None
    return prepared_text, prepared_hash or stable_text_hash(prepared_text), prepared_source


def log_wait_event(event: object) -> None:
    event_name = str(getattr(event, "name", "")).strip()
    latest_text = str(getattr(event, "latest_text", "") or "")
    if not event_name:
        return
    stage_log = log_text(event_name, latest_text, suffix="txt")
    print(f"{event_name}: {stage_log}")


def dispatch_request(
    state: dict[str, object],
    *,
    request_text: str,
    request_hash: str,
    request_source: str,
    prepared_prefix: str,
    sent_prefix: str,
    issue_centric_runtime_snapshot: object | None = None,
    success_updates: dict[str, object] | None = None,
) -> int:
    prepared_log = log_text(prepared_prefix, request_text)
    prepared_log_rel = repo_relative(prepared_log)

    prepared_state = clear_error_fields(dict(state))
    clear_pending_request_fields(prepared_state)
    if issue_centric_runtime_snapshot is not None:
        prepared_state.update(
            _issue_centric_next_request_state_updates(issue_centric_runtime_snapshot, phase="prepared")
        )
    stage_prepared_request(
        prepared_state,
        request_hash=request_hash,
        request_source=request_source,
        request_log=prepared_log_rel,
    )
    save_state(prepared_state)

    try:
        send_to_chatgpt(request_text)
    except Exception:
        retry_state = clear_error_fields(dict(state))
        clear_pending_request_fields(retry_state)
        if issue_centric_runtime_snapshot is not None:
            retry_state.update(
                _issue_centric_next_request_state_updates(issue_centric_runtime_snapshot, phase="prepared")
            )
        stage_prepared_request(
            retry_state,
            request_hash=request_hash,
            request_source=request_source,
            request_log=prepared_log_rel,
            status="retry_send",
        )
        save_state(retry_state)
        raise

    request_log = log_text(sent_prefix, request_text)
    mutable_state = clear_error_fields(dict(state))
    clear_pending_handoff_fields(mutable_state)
    promote_pending_request(
        mutable_state,
        request_hash=request_hash,
        request_source=request_source,
        request_log=repo_relative(request_log),
    )
    if issue_centric_runtime_snapshot is not None:
        mutable_state.update(
            _issue_centric_next_request_state_updates(issue_centric_runtime_snapshot, phase="pending")
        )
    if success_updates:
        mutable_state.update(success_updates)
    save_state(mutable_state)
    print(f"sent: {request_log}")
    return 0


def run_resume_request(
    state: dict[str, object],
    args: argparse.Namespace,
    last_report: str,
    resume_note: str,
    retryable_request: tuple[str, str, str] | None = None,
) -> int:
    # Detect fresh-start ready-issue continuation to prevent carry-over from
    # a previous issue's last_issue_centric_* context.
    # When pending_request_source is "ready_issue:..." and current_ready_issue_ref
    # is set, we know this continuation follows the initial request for a newly
    # pinned ready issue.  In that case, skip the old IC snapshot entirely and
    # build a fresh IC section from the explicit ready issue ref.
    _pending_source = str(state.get("pending_request_source", "")).strip()
    _pinned_ready_issue_ref = str(state.get("current_ready_issue_ref", "")).strip()
    _use_pinned_ready_issue = (
        _pending_source.startswith("ready_issue:") and bool(_pinned_ready_issue_ref)
    )

    if _use_pinned_ready_issue:
        issue_centric_runtime_snapshot = None
        issue_centric_runtime_mode = None
        issue_centric_next_request_section = build_pinned_ready_issue_ic_section(_pinned_ready_issue_ref)
        _route_selected = "issue_centric"
    else:
        issue_centric_runtime_snapshot, _ = (
            prepare_issue_centric_runtime_snapshot(state)
        )
        issue_centric_runtime_snapshot = _persist_runtime_snapshot_if_needed(issue_centric_runtime_snapshot)
        runtime_mode_state = dict(state)
        if issue_centric_runtime_snapshot is not None:
            runtime_mode_state.update(
                {
                    "last_issue_centric_runtime_snapshot": str(getattr(issue_centric_runtime_snapshot, "snapshot_path", "") or "").strip(),
                    "last_issue_centric_snapshot_status": str(getattr(issue_centric_runtime_snapshot, "snapshot_status", "") or "").strip(),
                }
            )
        issue_centric_runtime_mode, issue_centric_next_request_section = prepare_issue_centric_runtime_mode(
            runtime_mode_state
        )
        route_choice = resolve_issue_centric_route_choice(runtime_mode_state)
        _route_selected = route_choice.route_selected

    if retryable_request is None:
        retryable_request = load_retryable_prepared_request(state)
    if retryable_request is not None:
        request_text, request_hash, request_source = retryable_request
        prepared_status = str(state.get("prepared_request_status", "")).strip()
        if prepared_status == "prepared":
            if _route_selected == "issue_centric":
                print("request: issue-centric preferred route の prepared ChatGPT request を再生成せず送信します。")
            else:
                print("request: legacy fallback へ寄せた prepared の ChatGPT request を再生成せず送信します。")
        else:
            print("request: 前回未送信の ChatGPT request を再送します。")
    else:
        template_path = BRIDGE_DIR / "chatgpt_prompt_request_template.md"
        request_text = build_chatgpt_request(
            state=state,
            template_path=template_path,
            next_todo=args.next_todo,
            open_questions=args.open_questions,
            current_status=args.current_status or None,
            last_report=last_report,
            resume_note=resume_note or None,
            issue_centric_next_request_section=issue_centric_next_request_section,
            issue_centric_route_selected=_route_selected,
        )
        request_hash = stable_text_hash(request_text)
        request_source = build_report_request_source(state, resume_note)

    if (
        str(state.get("mode", "")).strip() == "waiting_prompt_reply"
        and str(state.get("pending_request_source", "")).strip() == request_source
    ):
        print("request: 同じ report からの request は送信済みのため再送しませんでした。")
        if str(state.get("pending_request_log", "")).strip():
            print(f"pending: {state.get('pending_request_log', '')}")
        return 0

    return dispatch_request(
        state,
        request_text=request_text,
        request_hash=request_hash,
        request_source=request_source,
        prepared_prefix="prepared_prompt_request_from_report",
        sent_prefix="sent_prompt_request_from_report",
        issue_centric_runtime_snapshot=issue_centric_runtime_mode or issue_centric_runtime_snapshot,
        success_updates={
            "chatgpt_decision": "",
            "chatgpt_decision_note": "",
            "human_review_auto_continue_count": 0,
        },
    )


def run_rotated_report_request(
    state: dict[str, object],
    args: argparse.Namespace,
    last_report: str,
) -> int:
    issue_centric_runtime_snapshot, _ = (
        prepare_issue_centric_runtime_snapshot(state)
    )
    issue_centric_runtime_snapshot = _persist_runtime_snapshot_if_needed(issue_centric_runtime_snapshot)
    runtime_mode_state = dict(state)
    if issue_centric_runtime_snapshot is not None:
        runtime_mode_state.update(
            {
                "last_issue_centric_runtime_snapshot": str(getattr(issue_centric_runtime_snapshot, "snapshot_path", "") or "").strip(),
                "last_issue_centric_snapshot_status": str(getattr(issue_centric_runtime_snapshot, "snapshot_status", "") or "").strip(),
            }
        )
    issue_centric_runtime_mode, issue_centric_next_request_section = prepare_issue_centric_runtime_mode(
        runtime_mode_state
    )
    route_choice = resolve_issue_centric_route_choice(runtime_mode_state)
    request_source = build_report_request_source(state, "")
    pending_handoff_text = ""
    pending_handoff_source = str(state.get("pending_handoff_source", "")).strip()
    if pending_handoff_source == request_source:
        pending_handoff_text = read_pending_handoff_text(state)

    if pending_handoff_text:
        handoff_text = pending_handoff_text
        handoff_received_log = state.get("pending_handoff_log", "") or ""
        print("next step: 次の ChatGPT request を送る前に、回収済み handoff で新チャット送信を再試行します。")
    else:
        handoff_request_text = build_chatgpt_handoff_request(
            state=state,
            last_report=last_report,
            next_todo=args.next_todo,
            open_questions=args.open_questions,
            current_status=args.current_status or None,
            issue_centric_next_request_section=issue_centric_next_request_section,
            issue_centric_route_selected=route_choice.route_selected,
        )
        handoff_request_log = log_text("handoff_requested", handoff_request_text)
        send_to_chatgpt(handoff_request_text)
        print(f"handoff requested: {handoff_request_log}")

        raw_text = wait_for_handoff_reply_text(
            request_text=handoff_request_text,
            stage_callback=log_wait_event,
        )
        handoff_text = extract_last_chatgpt_handoff(raw_text, after_text=handoff_request_text)
        handoff_received_log = log_text("handoff_received", handoff_text)
        handoff_state = clear_error_fields(dict(state))
        clear_pending_request_fields(handoff_state)
        handoff_state.update(
            {
                "mode": "idle",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": True,
                "need_codex_run": False,
                "pending_handoff_hash": stable_text_hash(handoff_text),
                "pending_handoff_source": request_source,
                "pending_handoff_log": repo_relative(handoff_received_log),
            }
        )
        if issue_centric_runtime_snapshot is not None:
            handoff_state.update(
                _issue_centric_next_request_state_updates(
                    issue_centric_runtime_mode or issue_centric_runtime_snapshot,
                    phase="prepared",
                )
            )
        save_state(handoff_state)

    rotated_chat = rotate_chat_with_handoff(handoff_text)
    rotation_signal = str(rotated_chat.get("signal", "")).strip()
    soft_wait = rotation_signal == "submitted_unconfirmed"
    chat_rotated_log = log_text(
        "chat_rotated",
        "\n".join(
            [
                f"url: {rotated_chat.get('url', '')}",
                f"title: {rotated_chat.get('title', '')}",
                f"signal: {rotation_signal}",
                f"delivery_mode: {'soft_success_wait' if soft_wait else 'confirmed_send'}",
                f"github_source_attach_status: {rotated_chat.get('github_source_attach_status', '')}",
                f"github_source_attach_boundary: {rotated_chat.get('github_source_attach_boundary', '')}",
                f"github_source_attach_detail: {rotated_chat.get('github_source_attach_detail', '')}",
                f"github_source_attach_log: {rotated_chat.get('github_source_attach_log', '')}",
                "request_send_continued_without_github_source: "
                f"{bool(rotated_chat.get('request_send_continued_without_github_source'))}",
                f"match_kind: {rotated_chat.get('match_kind', '')}",
                f"matched_hint: {rotated_chat.get('matched_hint', '')}",
                f"project_name: {rotated_chat.get('project_name', '')}",
                f"warning: {rotated_chat.get('warning', '')}",
            ]
        ),
    )
    request_log = log_text(
        "sent_prompt_request_from_report_soft_wait" if soft_wait else "sent_prompt_request_from_report",
        handoff_text,
    )
    request_hash = stable_text_hash(handoff_text)

    mutable_state = clear_error_fields(dict(state))
    clear_pending_request_fields(mutable_state)
    clear_pending_handoff_fields(mutable_state)
    clear_chat_rotation_fields(mutable_state)
    mutable_state.update(
        {
            "mode": "waiting_prompt_reply",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "chatgpt_decision": "",
            "chatgpt_decision_note": "",
            "human_review_auto_continue_count": 0,
            "pending_request_hash": request_hash,
            "pending_request_source": request_source,
            "pending_request_log": repo_relative(request_log),
            "pending_request_signal": rotation_signal,
            "current_chat_session": rotated_chat.get("url", ""),
            "github_source_attach_status": str(rotated_chat.get("github_source_attach_status", "")),
            "github_source_attach_boundary": str(rotated_chat.get("github_source_attach_boundary", "")),
            "github_source_attach_detail": str(rotated_chat.get("github_source_attach_detail", "")),
            "github_source_attach_context": str(rotated_chat.get("github_source_attach_context", "")),
            "github_source_attach_log": str(rotated_chat.get("github_source_attach_log", "")),
            "request_send_continued_without_github_source": bool(
                rotated_chat.get("request_send_continued_without_github_source")
            ),
        }
    )
    if issue_centric_runtime_snapshot is not None:
        mutable_state.update(
            _issue_centric_next_request_state_updates(
                issue_centric_runtime_mode or issue_centric_runtime_snapshot,
                phase="pending",
            )
        )
    save_state(mutable_state)

    if handoff_received_log:
        print(f"handoff received: {handoff_received_log}")
    print(f"chat rotated: {chat_rotated_log}")
    if rotation_signal:
        print(f"chat rotated signal: {rotation_signal}")
    if rotated_chat.get("warning"):
        print(f"chat rotated note: {rotated_chat.get('warning', '')}")
    if soft_wait:
        print("next step: handoff の送信成立を優先し、再送せず ChatGPT 返答待ちへ進みます。")
    if rotated_chat.get("match_kind"):
        print(
            "chat rotated composer:"
            f" match_kind={rotated_chat.get('match_kind', '')}"
            f" matched_hint={rotated_chat.get('matched_hint', '')}"
            f" project_name={rotated_chat.get('project_name', '')}"
        )
    if soft_wait:
        print(f"request queued (soft-wait): {request_log}")
    else:
        print(f"sent: {request_log}")
    return 0


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    if should_prioritize_unarchived_report(state):
        raise BridgeStop(
            "bridge/outbox/codex_report.md に未退避 report が残っているため、"
            "handoff / 新チャット送信へは進みません。先に report archive から再開してください。"
        )
    args = parse_args(argv)
    retryable_request = load_retryable_prepared_request(state)
    if retryable_request is not None:
        return run_resume_request(state, args, read_last_report_text(state), "", retryable_request)
    resume_note = resolve_resume_note(state, args)
    if str(state.get("mode", "")).strip() == "awaiting_user" and not resume_note.strip():
        print("再開用の補足入力が空のため送信しませんでした。必要な補足を入力して再実行してください。")
        return 0
    last_report = read_last_report_text(state)
    if not should_rotate_before_next_chat_request(state) and str(state.get("pending_handoff_log", "")).strip():
        cleaned_state = dict(state)
        clear_pending_handoff_fields(cleaned_state)
        save_state(cleaned_state)
        state = cleaned_state
    if str(state.get("mode", "")).strip() == "awaiting_user":
        return run_resume_request(state, args, last_report, resume_note)
    if should_rotate_before_next_chat_request(state):
        return run_rotated_report_request(state, args, last_report)
    return run_resume_request(state, args, last_report, "")


def _issue_centric_next_request_state_updates(
    context: object,
    *,
    phase: str,
) -> dict[str, object]:
    snapshot_path = str(getattr(context, "snapshot_path", "") or "").strip()
    snapshot_status = str(getattr(context, "snapshot_status", "") or "").strip()
    generation_id = str(getattr(context, "generation_id", "") or "").strip()
    runtime_mode = str(getattr(context, "runtime_mode", "") or "").strip()
    runtime_mode_reason = str(getattr(context, "runtime_mode_reason", "") or "").strip()
    runtime_mode_source = str(getattr(context, "runtime_mode_source", "") or "").strip()
    freshness_status = str(getattr(context, "freshness_status", "") or "").strip()
    freshness_reason = str(getattr(context, "freshness_reason", "") or "").strip()
    freshness_source = str(getattr(context, "freshness_source", "") or "").strip()
    invalidation_status = str(getattr(context, "invalidation_status", "") or "").strip()
    invalidation_reason = str(getattr(context, "invalidation_reason", "") or "").strip()
    target_issue = str(getattr(context, "target_issue", "") or "").strip()
    target_issue_source = str(getattr(context, "target_issue_source", "") or "").strip()
    fallback_reason = str(getattr(context, "fallback_reason", "") or "").strip()
    route_selected = str(getattr(context, "route_selected", "") or "").strip()
    recovery_status = str(getattr(context, "recovery_status", "") or "").strip()
    recovery_source = str(getattr(context, "recovery_source", "") or "").strip()
    generation_lifecycle = ""
    generation_lifecycle_reason = ""
    generation_lifecycle_source = ""
    prepared_generation_id = ""
    pending_generation_id = ""
    consumed_generation_id = ""
    invalidated_generation_id = ""
    if generation_id and runtime_mode in {"issue_centric_degraded_fallback", "issue_centric_unavailable"}:
        freshness_status = "issue_centric_invalidated"
        freshness_reason = runtime_mode_reason or fallback_reason or "issue_centric_context_invalidated"
        freshness_source = "legacy_fallback_selection"
        invalidation_status = "issue_centric_invalidated"
        invalidation_reason = runtime_mode_reason or fallback_reason or "issue_centric_context_invalidated"
        generation_lifecycle = "issue_centric_invalidated"
        generation_lifecycle_reason = invalidation_reason
        generation_lifecycle_source = "legacy_fallback_selection"
        invalidated_generation_id = generation_id
        route_selected = "fallback_legacy"
        fallback_reason = invalidation_reason
    elif generation_id and phase == "prepared":
        freshness_status = "issue_centric_fresh"
        freshness_reason = "prepared_request_bound_to_generation"
        freshness_source = "prepared_request_state"
        generation_lifecycle = "fresh_prepared"
        generation_lifecycle_reason = freshness_reason
        generation_lifecycle_source = freshness_source
        prepared_generation_id = generation_id
    elif generation_id and phase == "pending":
        freshness_status = "issue_centric_fresh"
        freshness_reason = "pending_request_bound_to_generation"
        freshness_source = "pending_request_state"
        generation_lifecycle = "fresh_pending"
        generation_lifecycle_reason = freshness_reason
        generation_lifecycle_source = freshness_source
        pending_generation_id = generation_id
    elif generation_id:
        freshness_status = "issue_centric_fresh"
        freshness_reason = "latest_issue_centric_generation_available"
        freshness_source = "runtime_snapshot_generation"
        generation_lifecycle = "fresh_available"
        generation_lifecycle_reason = freshness_reason
        generation_lifecycle_source = freshness_source
    return {
        "last_issue_centric_runtime_snapshot": snapshot_path,
        "last_issue_centric_snapshot_status": snapshot_status,
        "last_issue_centric_runtime_generation_id": generation_id,
        "last_issue_centric_generation_lifecycle": generation_lifecycle,
        "last_issue_centric_generation_lifecycle_reason": generation_lifecycle_reason,
        "last_issue_centric_generation_lifecycle_source": generation_lifecycle_source,
        "last_issue_centric_prepared_generation_id": prepared_generation_id,
        "last_issue_centric_pending_generation_id": pending_generation_id,
        "last_issue_centric_runtime_mode": runtime_mode,
        "last_issue_centric_runtime_mode_reason": runtime_mode_reason,
        "last_issue_centric_runtime_mode_source": runtime_mode_source,
        "last_issue_centric_freshness_status": freshness_status,
        "last_issue_centric_freshness_reason": freshness_reason,
        "last_issue_centric_freshness_source": freshness_source,
        "last_issue_centric_invalidation_status": invalidation_status,
        "last_issue_centric_invalidation_reason": invalidation_reason,
        "last_issue_centric_invalidated_generation_id": invalidated_generation_id,
        "last_issue_centric_consumed_generation_id": consumed_generation_id,
        "last_issue_centric_next_request_target": target_issue,
        "last_issue_centric_next_request_target_source": target_issue_source,
        "last_issue_centric_next_request_fallback_reason": fallback_reason,
        "last_issue_centric_route_selected": route_selected,
        "last_issue_centric_route_fallback_reason": fallback_reason,
        "last_issue_centric_recovery_status": recovery_status,
        "last_issue_centric_recovery_source": recovery_source,
        "last_issue_centric_recovery_fallback_reason": fallback_reason,
    }


def _persist_runtime_snapshot_if_needed(snapshot: object | None) -> object | None:
    if snapshot is None:
        return None
    snapshot_path = str(getattr(snapshot, "snapshot_path", "") or "").strip()
    if snapshot_path:
        return snapshot
    payload = dict(vars(snapshot))
    payload["snapshot_path"] = ""
    status = str(payload.get("snapshot_status", "")).strip() or "issue_centric_snapshot"
    log_path = log_text(
        f"issue_centric_runtime_snapshot_{status}",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    payload["snapshot_path"] = repo_relative(log_path)
    return type(snapshot)(**payload)


if __name__ == "__main__":
    sys.exit(guarded_main(lambda state: run(state)))
