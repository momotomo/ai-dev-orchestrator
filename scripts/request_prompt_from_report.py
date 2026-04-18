#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import re
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
    _LIFECYCLE_ONLY_REQUEST_GUIDANCE,
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

_REPORT_SUMMARY_FIELD_RE = re.compile(r"^\s*-\s+([A-Za-z0-9_]+):\s+(.+?)\s*$", re.MULTILINE)


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


def _parse_report_summary_fields(report_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _REPORT_SUMMARY_FIELD_RE.finditer(report_text):
        key = str(match.group(1)).strip().lower()
        value = str(match.group(2)).strip()
        if key and value and key not in fields:
            fields[key] = value
    return fields


def _is_ready_bounded_continuation(state: dict[str, object]) -> bool:
    """Return True when the current continuation is for a Ready:-prefixed bounded issue.

    A continuation qualifies as a Ready: bounded issue if ``current_ready_issue_ref``
    (saved by request_next_prompt when ``--ready-issue-ref`` is supplied) is non-empty
    and its value contains the "Ready:" prefix (case-insensitive).  This is the naming
    convention used by the orchestrator to mark a bounded single-issue work item.

    When ``current_ready_issue_ref`` is absent or does not start with "Ready:", the
    issue is treated as a parent / planned issue, allowing child / follow-up creation
    in the completion followup continuation.
    """
    ready_issue_ref = str(state.get("current_ready_issue_ref", "")).strip()
    if not ready_issue_ref:
        return False
    return "ready:" in ready_issue_ref.lower()


def _is_completion_followup_eligible(
    summary_fields: dict[str, str],
    state: dict[str, object],
) -> bool:
    """Return True when the archived report and state qualify for a completion followup section.

    All five conditions must hold:

    1. ``result=completed`` in the archived report summary — the last Codex run finished
       successfully.
    2. ``live_ready=confirmed`` in the archived report summary — the result was verified
       in the live environment.
    3. ``last_issue_centric_action == codex_run`` — the action that produced the archived
       report was a Codex run (not an issue-create or no-action cycle).
    4. ``last_issue_centric_principal_issue_kind == current_issue`` — the principal issue
       of the last cycle is the current issue (not a parent or planned issue).
    5. ``last_issue_centric_next_request_hint == continue_on_current_issue`` — the
       normalized summary explicitly requests continuation on the same issue.

    When any condition fails the caller should skip building the completion followup
    section entirely.
    """
    if summary_fields.get("result", "").strip().lower() != "completed":
        return False
    if summary_fields.get("live_ready", "").strip().lower() != "confirmed":
        return False
    if str(state.get("last_issue_centric_action", "")).strip() != "codex_run":
        return False
    if str(state.get("last_issue_centric_principal_issue_kind", "")).strip() != "current_issue":
        return False
    if str(state.get("last_issue_centric_next_request_hint", "")).strip() != "continue_on_current_issue":
        return False
    return True


def _resolve_completion_followup_target_issue(state: dict[str, object]) -> str:
    """Resolve the target issue URL/ref for a completion followup section.

    Tries state fields in priority order and returns the first non-empty value:

    1. ``last_issue_centric_next_request_target`` — explicitly set by the normalized
       summary when the next request should target a specific issue.
    2. ``last_issue_centric_principal_issue`` — the principal issue that drove the last
       Codex run; used when no explicit next-request target was recorded.
    3. ``last_issue_centric_resolved_issue`` — the issue that was resolved by the last
       cycle; fallback when principal_issue is absent.
    4. ``last_issue_centric_target_issue`` — the raw target issue ref saved at the start
       of the last cycle; last-resort fallback.

    Returns ``""`` if all four fields are empty or absent, which signals the caller that
    a completion followup section cannot be built.
    """
    for key in (
        "last_issue_centric_next_request_target",
        "last_issue_centric_principal_issue",
        "last_issue_centric_resolved_issue",
        "last_issue_centric_target_issue",
    ):
        value = str(state.get(key, "")).strip()
        if value:
            return value
    return ""


def _build_completion_followup_section(state: dict[str, object], report_text: str) -> str:
    summary_fields = _parse_report_summary_fields(report_text)
    if not _is_completion_followup_eligible(summary_fields, state):
        return ""

    target_issue = _resolve_completion_followup_target_issue(state)
    if not target_issue:
        return ""

    lines = [
        "## issue_centric_completion_followup",
        f"- archived_report_result: {summary_fields.get('result', '')}",
        f"- archived_report_live_ready: {summary_fields.get('live_ready', '')}",
        f"- target_issue: {target_issue}",
    ]

    if _is_ready_bounded_continuation(state):
        # Bounded Ready: issue — close-only lifecycle decision, no new Codex prompt.
        lines += [
            "- directive: 今回は新しい Codex 用 prompt を作りません。今回判断するのは lifecycle automation だけです。",
            "- directive: この continuation で action=codex_run は不正です。CHATGPT_CODEX_BODY を返さないでください。",
            "- directive: 正規経路は action=no_action です。create_followup_issue=false のまま返してください。",
            "- directive: current issue を閉じるべきなら close_current_issue=true を返してください。閉じない判断でも action=no_action のまま返してください。",
            "- directive: target_issue は current issue の issue ref だけを使ってください。",
            "- directive: parent update は今回 scope 外です。未対応なら summary で短く境界を示してください。",
        ]
    else:
        # Parent / planned / non-Ready: issue — allow child issue creation, additional
        # codex run, or close.  These issues have possible sub-issues and are not
        # bounded to a single Ready: work item.
        lines += [
            "- directive: target_issue は parent / planned issue です (Ready: 接頭辞なし)。",
            "- directive: archived report を踏まえて、以下の action から最適な 1 つを選んでください:",
            "  - action=issue_create + create_followup_issue=true: target_issue の下に child / follow-up issue を 1 件作る",
            "  - action=codex_run: target_issue に対してもう 1 フェーズ bounded に続ける (Codex 用 prompt を作成)",
            "  - action=no_action + close_current_issue=true: target_issue を閉じて次へ進む",
            "  - action=no_action + close_current_issue=false: 今回は何もしない",
            "- directive: child / follow-up issue が適切な場合は action=issue_create + create_followup_issue=true を返してください。",
            "- directive: target_issue は current issue の issue ref だけを使ってください。",
            "- directive: parent update は今回 scope 外です。未対応なら summary で短く境界を示してください。",
        ]

    return "\n".join(lines)


def _should_use_pinned_ready_issue_path(state: dict[str, object]) -> bool:
    """Return True when the continuation should use a fresh pinned ready issue path.

    Two conditions must both hold:

    1. ``pending_request_source`` starts with ``"ready_issue:"`` — the previous request
       was made for an initial ready-issue entry, not a normal report continuation.
    2. ``current_ready_issue_ref`` is non-empty — the orchestrator saved an explicit
       ready issue ref that should anchor this continuation.

    When both are True, ``run_resume_request()`` should skip the old
    ``last_issue_centric_*`` snapshot entirely and build a fresh IC section from the
    pinned ready issue ref.  This prevents context carry-over from a previous issue
    that might still be visible in the state fields.
    """
    pending_source = str(state.get("pending_request_source", "")).strip()
    pinned_ref = str(state.get("current_ready_issue_ref", "")).strip()
    return pending_source.startswith("ready_issue:") and bool(pinned_ref)


def _is_ready_bounded_completion_followup_request(
    state: dict[str, object],
    *,
    effective_next_todo: str,
    original_next_todo: str,
) -> bool:
    """Return True when this request is a Ready:-bounded completion followup continuation.

    The lifecycle-only guidance path applies when two conditions hold simultaneously:

    1. ``_is_ready_bounded_continuation(state)`` is True — the current issue carries
       the "Ready:" prefix that marks it as a single, bounded work item (no child-issue
       creation is expected after completion).

    2. ``effective_next_todo != original_next_todo`` — the next_todo was overridden by
       ``_resolve_completion_followup_request()``, which only modifies next_todo when a
       non-empty completion followup section was produced.  A changed next_todo therefore
       signals that we are inside the completion followup path, not a plain continuation.

    When both are True the caller should use ``_LIFECYCLE_ONLY_REQUEST_GUIDANCE`` so the
    request no longer asks ChatGPT to produce a new Codex prompt.
    """
    return (
        _is_ready_bounded_continuation(state)
        and effective_next_todo != original_next_todo
    )


def _build_completion_followup_wording(state: dict[str, object]) -> tuple[str, str]:
    """Return (completion_next_todo, completion_open_questions) for a completion followup request.

    The wording differs based on whether the current continuation is for a Ready:-bounded
    issue or a parent / planned issue:

    - **Ready bounded**: lifecycle automation only — no new Codex prompt.  The returned
      next_todo instructs ChatGPT to evaluate lifecycle actions (close or no-action) using
      the issue-centric contract.
    - **Parent / planned**: a broader set of actions is appropriate — child issue creation,
      additional Codex run, or issue close.  The returned next_todo asks ChatGPT to pick
      the most suitable action from those options.

    In both cases ``completion_open_questions`` instructs ChatGPT to acknowledge the
    parent-update scope boundary in the summary if anything is left unhandled.
    """
    if _is_ready_bounded_continuation(state):
        completion_next_todo = (
            "新しい Codex 用 prompt は作りません。archived report 後の lifecycle automation だけを issue-centric contract で判断してください。"
            " この continuation で action=codex_run は不正です。action=no_action を返し、current issue を閉じるなら close_current_issue=true を返してください。"
        )
    else:
        completion_next_todo = (
            "archived report を踏まえて、parent / planned issue の continuation を issue-centric contract で判断してください。"
            " child / follow-up issue の作成 (action=issue_create)、追加 Codex 実行 (action=codex_run)、issue close (action=no_action + close_current_issue=true) から選んでください。"
        )
    completion_open_questions = "parent issue update は今回 scope 外です。未対応境界だけを summary で短く返してください。"
    return completion_next_todo, completion_open_questions


def _resolve_completion_followup_request(
    state: dict[str, object],
    *,
    last_report: str,
    issue_centric_next_request_section: str,
    route_selected: str,
    next_todo: str,
    open_questions: str,
) -> tuple[str, str, str]:
    if route_selected != "issue_centric":
        return issue_centric_next_request_section, next_todo, open_questions

    completion_section = _build_completion_followup_section(state, last_report)
    if not completion_section:
        return issue_centric_next_request_section, next_todo, open_questions

    merged_section = issue_centric_next_request_section.rstrip()
    if merged_section:
        merged_section = merged_section + "\n\n" + completion_section
    else:
        merged_section = completion_section

    completion_next_todo, completion_open_questions = _build_completion_followup_wording(state)
    return merged_section, completion_next_todo, completion_open_questions


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


def _stage_prepared_request_state(
    state: dict[str, object],
    *,
    request_hash: str,
    request_source: str,
    request_log_rel: str,
    issue_centric_runtime_snapshot: object | None,
    status: str = "prepared",
) -> None:
    """Build, stage, and save a prepared request state snapshot.

    Called for both the initial ``"prepared"`` staging and the
    ``"retry_send"`` fallback staging when ``send_to_chatgpt`` raises.
    IC generation binding is applied when ``issue_centric_runtime_snapshot``
    is provided.
    """
    staged = clear_error_fields(dict(state))
    clear_pending_request_fields(staged)
    if issue_centric_runtime_snapshot is not None:
        staged.update(
            _issue_centric_next_request_state_updates(issue_centric_runtime_snapshot, phase="prepared")
        )
    stage_prepared_request(
        staged,
        request_hash=request_hash,
        request_source=request_source,
        request_log=request_log_rel,
        status=status,
    )
    save_state(staged)


def _apply_pending_request_state(
    state: dict[str, object],
    *,
    request_hash: str,
    request_source: str,
    request_log_path: str,
    issue_centric_runtime_snapshot: object | None,
    success_updates: dict[str, object] | None,
) -> None:
    """Build and save the pending request state after a successful send.

    Clears error fields and pending handoff fields, promotes the prepared
    request to pending, applies IC generation binding, merges any
    caller-supplied ``success_updates``, and saves.
    """
    mutable = clear_error_fields(dict(state))
    clear_pending_handoff_fields(mutable)
    promote_pending_request(
        mutable,
        request_hash=request_hash,
        request_source=request_source,
        request_log=repo_relative(request_log_path),
    )
    if issue_centric_runtime_snapshot is not None:
        mutable.update(
            _issue_centric_next_request_state_updates(issue_centric_runtime_snapshot, phase="pending")
        )
    if success_updates:
        mutable.update(success_updates)
    save_state(mutable)


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
    # log
    prepared_log = log_text(prepared_prefix, request_text)
    prepared_log_rel = repo_relative(prepared_log)
    # state transition — prepared staging
    _stage_prepared_request_state(
        state,
        request_hash=request_hash,
        request_source=request_source,
        request_log_rel=prepared_log_rel,
        issue_centric_runtime_snapshot=issue_centric_runtime_snapshot,
    )
    try:
        send_to_chatgpt(request_text)
    except Exception:
        # state transition — retry_send fallback staging
        _stage_prepared_request_state(
            state,
            request_hash=request_hash,
            request_source=request_source,
            request_log_rel=prepared_log_rel,
            issue_centric_runtime_snapshot=issue_centric_runtime_snapshot,
            status="retry_send",
        )
        raise
    # log + state transition — pending
    request_log = log_text(sent_prefix, request_text)
    _apply_pending_request_state(
        state,
        request_hash=request_hash,
        request_source=request_source,
        request_log_path=request_log,
        issue_centric_runtime_snapshot=issue_centric_runtime_snapshot,
        success_updates=success_updates,
    )
    print(f"sent: {request_log}")
    return 0


@dataclasses.dataclass
class _IcResolvedContext:
    """Resolved issue-centric context for a report request cycle.

    Carries the four values that describe the IC resolution outcome so that
    callers can read ``.runtime_snapshot``, ``.runtime_mode``,
    ``.next_request_section``, and ``.route_selected`` as named fields
    instead of relying on positional tuple destructuring.
    """

    runtime_snapshot: object | None = None
    runtime_mode: object | None = None
    next_request_section: str = ""
    route_selected: str = ""


def _build_ic_runtime_mode_state(
    state: dict[str, object],
    snapshot: object | None,
) -> dict[str, object]:
    """Build the runtime-mode-state dict used for IC mode and route resolution.

    Copies ``state`` and, when ``snapshot`` is not None, overlays
    ``last_issue_centric_runtime_snapshot`` and
    ``last_issue_centric_snapshot_status`` from the snapshot so that
    ``prepare_issue_centric_runtime_mode`` and
    ``resolve_issue_centric_route_choice`` see the persisted snapshot values.
    """
    runtime_mode_state = dict(state)
    if snapshot is not None:
        runtime_mode_state.update(
            {
                "last_issue_centric_runtime_snapshot": str(getattr(snapshot, "snapshot_path", "") or "").strip(),
                "last_issue_centric_snapshot_status": str(getattr(snapshot, "snapshot_status", "") or "").strip(),
            }
        )
    return runtime_mode_state


def _resolve_normal_ic_context(
    state: dict[str, object],
) -> "_IcResolvedContext":
    """Resolve issue-centric context via the normal snapshot / mode / route path.

    Three stages:

    * **A — snapshot prepare / persist**: ``prepare_issue_centric_runtime_snapshot``
      and ``_persist_runtime_snapshot_if_needed``.
    * **B — runtime-mode-state build**: ``_build_ic_runtime_mode_state`` overlays
      snapshot path/status onto a copy of ``state``.
    * **C — mode / section / route resolution**: ``prepare_issue_centric_runtime_mode``
      and ``resolve_issue_centric_route_choice``.

    Returns an :class:`_IcResolvedContext` with named fields.
    Used by both ``_resolve_report_request_ic_context`` and
    ``run_rotated_report_request``.
    """
    # A — snapshot
    snapshot, _ = prepare_issue_centric_runtime_snapshot(state)
    snapshot = _persist_runtime_snapshot_if_needed(snapshot)
    # B — runtime-mode-state
    runtime_mode_state = _build_ic_runtime_mode_state(state, snapshot)
    # C — mode / section / route
    runtime_mode, next_request_section = prepare_issue_centric_runtime_mode(runtime_mode_state)
    route_choice = resolve_issue_centric_route_choice(runtime_mode_state)
    return _IcResolvedContext(
        runtime_snapshot=snapshot,
        runtime_mode=runtime_mode,
        next_request_section=next_request_section,
        route_selected=route_choice.route_selected,
    )


def _resolve_report_request_ic_context(
    state: dict[str, object],
) -> "_IcResolvedContext":
    """Resolve issue-centric context for a report-based resume request.

    Returns an :class:`_IcResolvedContext` with named fields.

    Two paths:

    * **Pinned ready issue path** — when ``_should_use_pinned_ready_issue_path(state)``
      is True, snapshot and runtime_mode are ``None`` and a fresh IC section is built
      from the pinned ready issue ref.  Old ``last_issue_centric_*`` context is not
      carried over.

    * **Normal path** — delegates to ``_resolve_normal_ic_context``.
    """
    if _should_use_pinned_ready_issue_path(state):
        _pinned_ready_issue_ref = str(state.get("current_ready_issue_ref", "")).strip()
        return _IcResolvedContext(
            runtime_snapshot=None,
            runtime_mode=None,
            next_request_section=build_pinned_ready_issue_ic_section(_pinned_ready_issue_ref),
            route_selected="issue_centric",
        )
    return _resolve_normal_ic_context(state)


def _resolve_resume_request_payload(
    state: dict[str, object],
    *,
    retryable_request: tuple[str, str, str] | None,
    args: argparse.Namespace,
    last_report: str,
    resume_note: str,
    effective_next_todo: str,
    effective_open_questions: str,
    issue_centric_next_request_section: str,
) -> tuple[str, str, str, str | None]:
    """Resolve the request payload for a resume request.

    Returns ``(request_text, request_hash, request_source, prepared_status)``.

    Two paths:

    * **Retryable prepared request** — if ``retryable_request`` is provided it is used
      directly; otherwise ``load_retryable_prepared_request(state)`` is tried.  When a
      retryable request is found the returned ``prepared_status`` is the current
      ``prepared_request_status`` value from *state* (may be ``"prepared"`` or another
      status string, including empty string).

    * **Fresh build** — when no retryable request is available,
      ``build_chatgpt_request()`` assembles the text, ``stable_text_hash()`` hashes it,
      and ``build_report_request_source()`` produces the source token.
      ``prepared_status`` is ``None`` in this case.
    """
    if retryable_request is None:
        retryable_request = load_retryable_prepared_request(state)
    if retryable_request is not None:
        request_text, request_hash, request_source = retryable_request
        prepared_status: str | None = str(state.get("prepared_request_status", "")).strip()
        return request_text, request_hash, request_source, prepared_status
    template_path = BRIDGE_DIR / "chatgpt_prompt_request_template.md"
    _request_guidance = (
        _LIFECYCLE_ONLY_REQUEST_GUIDANCE
        if _is_ready_bounded_completion_followup_request(
            state,
            effective_next_todo=effective_next_todo,
            original_next_todo=args.next_todo,
        )
        else None
    )
    request_text = build_chatgpt_request(
        state=state,
        template_path=template_path,
        next_todo=effective_next_todo,
        open_questions=effective_open_questions,
        current_status=args.current_status or None,
        last_report=last_report,
        resume_note=resume_note or None,
        issue_centric_next_request_section=issue_centric_next_request_section,
        request_guidance=_request_guidance,
    )
    request_hash = stable_text_hash(request_text)
    request_source = build_report_request_source(state, resume_note)
    return request_text, request_hash, request_source, None


def _log_prepared_request_reuse(prepared_status: str, route_selected: str) -> None:
    """Print the appropriate reuse message for a prepared request.

    Called only when a prepared request is being reused (``prepared_status`` is not
    ``None``).  Distinguishes the "re-generate skipped" prepared path from the
    "unsent retry" path, and within the former selects the route-specific wording.
    """
    if prepared_status == "prepared":
        if route_selected == "issue_centric":
            print("request: issue-centric preferred route の prepared ChatGPT request を再生成せず送信します。")
        else:
            print("request: legacy fallback へ寄せた prepared の ChatGPT request を再生成せず送信します。")
    else:
        print("request: 前回未送信の ChatGPT request を再送します。")


def _is_duplicate_pending_request(state: dict[str, object], request_source: str) -> bool:
    """Return True when the same request source is already pending.

    Prints the duplicate-detection message (and the pending log path if available)
    as a side-effect when a duplicate is detected.
    """
    if (
        str(state.get("mode", "")).strip() == "waiting_prompt_reply"
        and str(state.get("pending_request_source", "")).strip() == request_source
    ):
        print("request: 同じ report からの request は送信済みのため再送しませんでした。")
        if str(state.get("pending_request_log", "")).strip():
            print(f"pending: {state.get('pending_request_log', '')}")
        return True
    return False


def run_resume_request(
    state: dict[str, object],
    args: argparse.Namespace,
    last_report: str,
    resume_note: str,
    retryable_request: tuple[str, str, str] | None = None,
) -> int:
    # 1. resolve
    plan = _resolve_resume_request_plan(state, args, last_report, resume_note, retryable_request)
    # 2. execute
    return _execute_resume_request_plan(plan)


def run_rotated_report_request(
    state: dict[str, object],
    args: argparse.Namespace,
    last_report: str,
) -> int:
    # 1. resolve
    plan = _resolve_rotated_request_plan(state, args, last_report)
    # 2. execute
    return _execute_rotated_request_plan(plan)


def _acquire_rotated_handoff(
    state: dict[str, object],
    args: argparse.Namespace,
    last_report: str,
    *,
    request_source: str,
    ic_context: "_IcResolvedContext",
) -> tuple[str, str]:
    """Acquire handoff text for a rotated report request.

    Returns ``(handoff_text, handoff_received_log)``.

    Two paths:

    * **Cached pending handoff** — if ``pending_handoff_source`` matches
      ``request_source`` and a pending handoff text is available, it is reused
      directly without sending a new request.

    * **Fresh acquisition** — builds and sends a handoff request, waits for the
      reply, extracts the handoff text, and persists the pending handoff state.
    """
    pending_handoff_source = str(state.get("pending_handoff_source", "")).strip()
    if pending_handoff_source == request_source:
        pending_handoff_text = read_pending_handoff_text(state)
        if pending_handoff_text:
            print("next step: 次の ChatGPT request を送る前に、回収済み handoff で新チャット送信を再試行します。")
            return pending_handoff_text, str(state.get("pending_handoff_log", "") or "")
    handoff_request_text = build_chatgpt_handoff_request(
        state=state,
        last_report=last_report,
        next_todo=args.next_todo,
        open_questions=args.open_questions,
        current_status=args.current_status or None,
        issue_centric_next_request_section=ic_context.next_request_section,
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
    if ic_context.runtime_snapshot is not None:
        handoff_state.update(
            _issue_centric_next_request_state_updates(
                ic_context.runtime_mode or ic_context.runtime_snapshot,
                phase="prepared",
            )
        )
    save_state(handoff_state)
    return handoff_text, handoff_received_log


def _apply_rotated_pending_request_state(
    state: dict[str, object],
    *,
    request_hash: str,
    request_source: str,
    request_log_path: str,
    rotation_signal: str,
    rotated_chat: dict[str, object],
    issue_centric_runtime_snapshot: object | None,
    issue_centric_runtime_mode: object | None,
) -> None:
    """Build and save the pending request state for a rotated chat request.

    Clears error, pending request, pending handoff, and chat rotation fields,
    then populates all pending request fields including rotation metadata and
    IC generation binding.
    """
    mutable = clear_error_fields(dict(state))
    clear_pending_request_fields(mutable)
    clear_pending_handoff_fields(mutable)
    clear_chat_rotation_fields(mutable)
    mutable.update(
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
            "pending_request_log": repo_relative(request_log_path),
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
        mutable.update(
            _issue_centric_next_request_state_updates(
                issue_centric_runtime_mode or issue_centric_runtime_snapshot,
                phase="pending",
            )
        )
    save_state(mutable)


def _apply_rotated_request_result(
    state: dict[str, object],
    *,
    handoff_text: str,
    handoff_received_log: str,
    request_source: str,
    ic_context: "_IcResolvedContext",
) -> int:
    """Apply rotated report request result to state, logs, and stdout.

    Rotates the chat with the handoff text, updates state with pending request
    fields and IC context, saves state, and prints the rotation result.
    Returns 0.
    """
    # log
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
    # state transition — pending
    _apply_rotated_pending_request_state(
        state,
        request_hash=request_hash,
        request_source=request_source,
        request_log_path=request_log,
        rotation_signal=rotation_signal,
        rotated_chat=rotated_chat,
        issue_centric_runtime_snapshot=ic_context.runtime_snapshot,
        issue_centric_runtime_mode=ic_context.runtime_mode,
    )
    # print
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


@dataclasses.dataclass
class _ResumeRequestPlan:
    """Resolved execution plan for a resume report-request cycle.

    Built by ``_resolve_resume_request_plan`` and consumed by
    ``_execute_resume_request_plan``.  Carries every value needed to execute
    the resume path so that ``run_resume_request`` reads as two clear steps:
    resolve → execute.
    """

    state: dict[str, object]
    args: "argparse.Namespace"
    last_report: str
    resume_note: str
    ic_context: "_IcResolvedContext"
    effective_section: str
    effective_next_todo: str
    effective_open_questions: str
    request_text: str
    request_hash: str
    request_source: str
    prepared_status: "str | None"
    # ``runtime_mode or runtime_snapshot`` — passed to dispatch as
    # ``issue_centric_runtime_snapshot`` (mode object takes priority when present)
    ic_snapshot_for_dispatch: object | None = None


def _resolve_resume_request_plan(
    state: dict[str, object],
    args: "argparse.Namespace",
    last_report: str,
    resume_note: str,
    retryable_request: "tuple[str, str, str] | None",
) -> "_ResumeRequestPlan":
    """Resolve all values required to execute a resume report-request cycle.

    Resolves IC context, completion-followup adjustment, and the request payload,
    then bundles everything into a :class:`_ResumeRequestPlan` ready for
    :func:`_execute_resume_request_plan`.
    """
    ic = _resolve_report_request_ic_context(state)
    effective_section, effective_next_todo, effective_open_questions = (
        _resolve_completion_followup_request(
            state,
            last_report=last_report,
            issue_centric_next_request_section=ic.next_request_section,
            route_selected=ic.route_selected,
            next_todo=args.next_todo,
            open_questions=args.open_questions,
        )
    )
    request_text, request_hash, request_source, prepared_status = _resolve_resume_request_payload(
        state,
        retryable_request=retryable_request,
        args=args,
        last_report=last_report,
        resume_note=resume_note,
        effective_next_todo=effective_next_todo,
        effective_open_questions=effective_open_questions,
        issue_centric_next_request_section=effective_section,
    )
    return _ResumeRequestPlan(
        state=state,
        args=args,
        last_report=last_report,
        resume_note=resume_note,
        ic_context=ic,
        effective_section=effective_section,
        effective_next_todo=effective_next_todo,
        effective_open_questions=effective_open_questions,
        request_text=request_text,
        request_hash=request_hash,
        request_source=request_source,
        prepared_status=prepared_status,
        ic_snapshot_for_dispatch=ic.runtime_mode or ic.runtime_snapshot,
    )


def _execute_resume_request_plan(plan: "_ResumeRequestPlan") -> int:
    """Execute a resolved :class:`_ResumeRequestPlan`.

    Three steps:
    1. Log prepared-request reuse if applicable.
    2. Guard against duplicate pending request (early return 0).
    3. Dispatch the request.
    """
    if plan.prepared_status is not None:
        _log_prepared_request_reuse(plan.prepared_status, plan.ic_context.route_selected)
    if _is_duplicate_pending_request(plan.state, plan.request_source):
        return 0
    return dispatch_request(
        plan.state,
        request_text=plan.request_text,
        request_hash=plan.request_hash,
        request_source=plan.request_source,
        prepared_prefix="prepared_prompt_request_from_report",
        sent_prefix="sent_prompt_request_from_report",
        issue_centric_runtime_snapshot=plan.ic_snapshot_for_dispatch,
        success_updates={
            "chatgpt_decision": "",
            "chatgpt_decision_note": "",
            "human_review_auto_continue_count": 0,
        },
    )


@dataclasses.dataclass
class _RotatedRequestPlan:
    """Resolved execution plan for a rotated report-request cycle.

    Built by ``_resolve_rotated_request_plan`` and consumed by
    ``_execute_rotated_request_plan``.  Carries the IC context, request source,
    and acquired handoff so that ``run_rotated_report_request`` reads as two clear
    steps: resolve → execute.
    """

    state: dict[str, object]
    last_report: str
    request_source: str
    ic_context: "_IcResolvedContext"
    handoff_text: str
    handoff_received_log: str


def _resolve_rotated_request_plan(
    state: dict[str, object],
    args: "argparse.Namespace",
    last_report: str,
) -> "_RotatedRequestPlan":
    """Resolve all values required to execute a rotated report-request cycle.

    Resolves IC context via the normal path, builds the request source, and
    acquires the handoff text (from cache or fresh acquisition).  Returns a
    :class:`_RotatedRequestPlan` ready for :func:`_execute_rotated_request_plan`.
    """
    ic = _resolve_normal_ic_context(state)
    request_source = build_report_request_source(state, "")
    handoff_text, handoff_received_log = _acquire_rotated_handoff(
        state,
        args,
        last_report,
        request_source=request_source,
        ic_context=ic,
    )
    return _RotatedRequestPlan(
        state=state,
        last_report=last_report,
        request_source=request_source,
        ic_context=ic,
        handoff_text=handoff_text,
        handoff_received_log=handoff_received_log,
    )


def _execute_rotated_request_plan(plan: "_RotatedRequestPlan") -> int:
    """Execute a resolved :class:`_RotatedRequestPlan`.

    Delegates to ``_apply_rotated_request_result`` which handles chat rotation,
    state transition, and result printing.
    """
    return _apply_rotated_request_result(
        plan.state,
        handoff_text=plan.handoff_text,
        handoff_received_log=plan.handoff_received_log,
        request_source=plan.request_source,
        ic_context=plan.ic_context,
    )


def _clean_stale_pending_handoff_if_needed(state: dict[str, object]) -> dict[str, object]:
    """Clear a stale pending handoff from state when rotation is not needed.

    If ``pending_handoff_log`` is present but ``should_rotate_before_next_chat_request``
    returns False, the pending handoff is stale and must be cleared so that the normal
    resume path does not accidentally pick it up.  The cleaned state is saved and
    returned.  If no cleanup is needed the original state object is returned unchanged.
    """
    if not should_rotate_before_next_chat_request(state) and str(state.get("pending_handoff_log", "")).strip():
        cleaned_state = dict(state)
        clear_pending_handoff_fields(cleaned_state)
        save_state(cleaned_state)
        return cleaned_state
    return state


@dataclasses.dataclass
class _ReportRequestEntryPlan:
    """Resolved entry plan for a report request cycle.

    ``path`` names the execution branch:

    * ``"retryable_resume"``    — retryable prepared request detected; call
      ``run_resume_request`` immediately with ``retryable_request``.
    * ``"awaiting_user_stop"``  — mode is ``awaiting_user`` but ``resume_note``
      is empty; print guidance and return 0.
    * ``"awaiting_user_resume"`` — mode is ``awaiting_user`` with a non-empty
      ``resume_note``; call ``run_resume_request`` with the note.
    * ``"rotated"``             — chat rotation needed; call
      ``run_rotated_report_request``.
    * ``"normal_resume"``       — standard report-based continuation; call
      ``run_resume_request`` with an empty note.
    """

    path: str
    state: dict[str, object]
    args: argparse.Namespace
    last_report: str
    resume_note: str
    retryable_request: tuple[str, str, str] | None


def _resolve_report_request_entry_plan(
    state: dict[str, object],
    args: argparse.Namespace,
) -> _ReportRequestEntryPlan:
    """Resolve the entry plan for a report request cycle.

    Reads retryable request, resume note, and state flags; applies stale
    pending handoff cleanup when needed; and returns a
    ``_ReportRequestEntryPlan`` that names the execution path without
    performing any side effects beyond the cleanup save.
    """
    retryable_request = load_retryable_prepared_request(state)
    if retryable_request is not None:
        return _ReportRequestEntryPlan(
            path="retryable_resume",
            state=state,
            args=args,
            last_report=read_last_report_text(state),
            resume_note="",
            retryable_request=retryable_request,
        )
    resume_note = resolve_resume_note(state, args)
    if str(state.get("mode", "")).strip() == "awaiting_user" and not resume_note.strip():
        return _ReportRequestEntryPlan(
            path="awaiting_user_stop",
            state=state,
            args=args,
            last_report="",
            resume_note=resume_note,
            retryable_request=None,
        )
    last_report = read_last_report_text(state)
    state = _clean_stale_pending_handoff_if_needed(state)
    if str(state.get("mode", "")).strip() == "awaiting_user":
        return _ReportRequestEntryPlan(
            path="awaiting_user_resume",
            state=state,
            args=args,
            last_report=last_report,
            resume_note=resume_note,
            retryable_request=None,
        )
    if should_rotate_before_next_chat_request(state):
        return _ReportRequestEntryPlan(
            path="rotated",
            state=state,
            args=args,
            last_report=last_report,
            resume_note="",
            retryable_request=None,
        )
    return _ReportRequestEntryPlan(
        path="normal_resume",
        state=state,
        args=args,
        last_report=last_report,
        resume_note="",
        retryable_request=None,
    )


def _execute_report_request_entry_plan(plan: _ReportRequestEntryPlan) -> int:
    """Execute the resolved entry plan for a report request cycle."""
    if plan.path == "awaiting_user_stop":
        print("再開用の補足入力が空のため送信しませんでした。必要な補足を入力して再実行してください。")
        return 0
    if plan.path == "retryable_resume":
        return run_resume_request(plan.state, plan.args, plan.last_report, "", plan.retryable_request)
    if plan.path == "awaiting_user_resume":
        return run_resume_request(plan.state, plan.args, plan.last_report, plan.resume_note)
    if plan.path == "rotated":
        return run_rotated_report_request(plan.state, plan.args, plan.last_report)
    # normal_resume
    return run_resume_request(plan.state, plan.args, plan.last_report, "")


def run(state: dict[str, object], argv: list[str] | None = None) -> int:
    # 1. preflight
    if should_prioritize_unarchived_report(state):
        raise BridgeStop(
            "bridge/outbox/codex_report.md に未退避 report が残っているため、"
            "handoff / 新チャット送信へは進みません。先に report archive から再開してください。"
        )
    # 2. args
    args = parse_args(argv)
    # 3. entry plan
    plan = _resolve_report_request_entry_plan(state, args)
    # 4. execute
    return _execute_report_request_entry_plan(plan)


def _issue_centric_next_request_state_updates(
    context: object,
    *,
    phase: str,
) -> dict[str, object]:
    """Build the IC next-request state update dict for a prepared or pending request.

    Extracts snapshot / runtime_mode / target / recovery base fields from
    ``context``, delegates generation lifecycle resolution to
    ``_resolve_ic_generation_lifecycle``, and assembles the final state update
    dict.  External key names are unchanged.
    """
    # --- base fields from context ---
    snapshot_path = str(getattr(context, "snapshot_path", "") or "").strip()
    snapshot_status = str(getattr(context, "snapshot_status", "") or "").strip()
    generation_id = str(getattr(context, "generation_id", "") or "").strip()
    runtime_mode = str(getattr(context, "runtime_mode", "") or "").strip()
    runtime_mode_reason = str(getattr(context, "runtime_mode_reason", "") or "").strip()
    runtime_mode_source = str(getattr(context, "runtime_mode_source", "") or "").strip()
    target_issue = str(getattr(context, "target_issue", "") or "").strip()
    target_issue_source = str(getattr(context, "target_issue_source", "") or "").strip()
    fallback_reason = str(getattr(context, "fallback_reason", "") or "").strip()
    route_selected = str(getattr(context, "route_selected", "") or "").strip()
    recovery_status = str(getattr(context, "recovery_status", "") or "").strip()
    recovery_source = str(getattr(context, "recovery_source", "") or "").strip()
    # context-derived freshness / invalidation defaults (used only when no
    # generation_id is present — see _resolve_ic_generation_lifecycle)
    ctx_freshness_status = str(getattr(context, "freshness_status", "") or "").strip()
    ctx_freshness_reason = str(getattr(context, "freshness_reason", "") or "").strip()
    ctx_freshness_source = str(getattr(context, "freshness_source", "") or "").strip()
    ctx_invalidation_status = str(getattr(context, "invalidation_status", "") or "").strip()
    ctx_invalidation_reason = str(getattr(context, "invalidation_reason", "") or "").strip()
    # --- generation lifecycle resolution ---
    lc = _resolve_ic_generation_lifecycle(
        generation_id,
        runtime_mode=runtime_mode,
        runtime_mode_reason=runtime_mode_reason,
        fallback_reason=fallback_reason,
        route_selected=route_selected,
        phase=phase,
        ctx_freshness_status=ctx_freshness_status,
        ctx_freshness_reason=ctx_freshness_reason,
        ctx_freshness_source=ctx_freshness_source,
        ctx_invalidation_status=ctx_invalidation_status,
        ctx_invalidation_reason=ctx_invalidation_reason,
    )
    # --- assemble payload ---
    return {
        "last_issue_centric_runtime_snapshot": snapshot_path,
        "last_issue_centric_snapshot_status": snapshot_status,
        "last_issue_centric_runtime_generation_id": generation_id,
        "last_issue_centric_generation_lifecycle": lc.generation_lifecycle,
        "last_issue_centric_generation_lifecycle_reason": lc.generation_lifecycle_reason,
        "last_issue_centric_generation_lifecycle_source": lc.generation_lifecycle_source,
        "last_issue_centric_prepared_generation_id": lc.prepared_generation_id,
        "last_issue_centric_pending_generation_id": lc.pending_generation_id,
        "last_issue_centric_runtime_mode": runtime_mode,
        "last_issue_centric_runtime_mode_reason": runtime_mode_reason,
        "last_issue_centric_runtime_mode_source": runtime_mode_source,
        "last_issue_centric_freshness_status": lc.freshness_status,
        "last_issue_centric_freshness_reason": lc.freshness_reason,
        "last_issue_centric_freshness_source": lc.freshness_source,
        "last_issue_centric_invalidation_status": lc.invalidation_status,
        "last_issue_centric_invalidation_reason": lc.invalidation_reason,
        "last_issue_centric_invalidated_generation_id": lc.invalidated_generation_id,
        "last_issue_centric_consumed_generation_id": lc.consumed_generation_id,
        "last_issue_centric_next_request_target": target_issue,
        "last_issue_centric_next_request_target_source": target_issue_source,
        "last_issue_centric_next_request_fallback_reason": lc.fallback_reason,
        "last_issue_centric_route_selected": lc.route_selected,
        "last_issue_centric_route_fallback_reason": lc.fallback_reason,
        "last_issue_centric_recovery_status": recovery_status,
        "last_issue_centric_recovery_source": recovery_source,
        "last_issue_centric_recovery_fallback_reason": lc.fallback_reason,
    }


@dataclasses.dataclass
class _IcGenerationLifecycle:
    """Resolved IC generation lifecycle fields for a next-request state update.

    All string fields default to empty string.  The ``route_selected`` and
    ``fallback_reason`` fields carry either the context-derived originals (when no
    override is needed) or the corrected values (in the degraded/unavailable case).
    """

    freshness_status: str = ""
    freshness_reason: str = ""
    freshness_source: str = ""
    invalidation_status: str = ""
    invalidation_reason: str = ""
    generation_lifecycle: str = ""
    generation_lifecycle_reason: str = ""
    generation_lifecycle_source: str = ""
    prepared_generation_id: str = ""
    pending_generation_id: str = ""
    consumed_generation_id: str = ""
    invalidated_generation_id: str = ""
    route_selected: str = ""
    fallback_reason: str = ""


def _resolve_ic_generation_lifecycle(
    generation_id: str,
    *,
    runtime_mode: str,
    runtime_mode_reason: str,
    fallback_reason: str,
    route_selected: str,
    phase: str,
    ctx_freshness_status: str = "",
    ctx_freshness_reason: str = "",
    ctx_freshness_source: str = "",
    ctx_invalidation_status: str = "",
    ctx_invalidation_reason: str = "",
) -> _IcGenerationLifecycle:
    """Resolve IC generation lifecycle fields for a next-request state update.

    Five cases:

    * **No generation_id** — lifecycle fields remain empty; freshness and
      invalidation fall back to context-derived values.
    * **degraded/unavailable fallback** — generation is marked invalidated;
      ``route_selected`` is forced to ``"fallback_legacy"``.
    * **phase=prepared** — generation is bound as ``prepared_generation_id``;
      lifecycle is ``"fresh_prepared"``.
    * **phase=pending** — generation is bound as ``pending_generation_id``;
      lifecycle is ``"fresh_pending"``.
    * **generation present, no phase match** — lifecycle is
      ``"fresh_available"``; generation is not bound to a specific id slot.
    """
    if not generation_id:
        # no generation: pass context defaults through unchanged
        return _IcGenerationLifecycle(
            freshness_status=ctx_freshness_status,
            freshness_reason=ctx_freshness_reason,
            freshness_source=ctx_freshness_source,
            invalidation_status=ctx_invalidation_status,
            invalidation_reason=ctx_invalidation_reason,
            route_selected=route_selected,
            fallback_reason=fallback_reason,
        )
    if runtime_mode in {"issue_centric_degraded_fallback", "issue_centric_unavailable"}:
        # generation invalidated by degraded/unavailable fallback
        invalidation_reason = runtime_mode_reason or fallback_reason or "issue_centric_context_invalidated"
        return _IcGenerationLifecycle(
            freshness_status="issue_centric_invalidated",
            freshness_reason=invalidation_reason,
            freshness_source="legacy_fallback_selection",
            invalidation_status="issue_centric_invalidated",
            invalidation_reason=invalidation_reason,
            generation_lifecycle="issue_centric_invalidated",
            generation_lifecycle_reason=invalidation_reason,
            generation_lifecycle_source="legacy_fallback_selection",
            invalidated_generation_id=generation_id,
            route_selected="fallback_legacy",
            fallback_reason=invalidation_reason,
        )
    if phase == "prepared":
        # binds prepared_generation_id; lifecycle is fresh_prepared
        return _IcGenerationLifecycle(
            freshness_status="issue_centric_fresh",
            freshness_reason="prepared_request_bound_to_generation",
            freshness_source="prepared_request_state",
            generation_lifecycle="fresh_prepared",
            generation_lifecycle_reason="prepared_request_bound_to_generation",
            generation_lifecycle_source="prepared_request_state",
            prepared_generation_id=generation_id,
            route_selected=route_selected,
            fallback_reason=fallback_reason,
        )
    if phase == "pending":
        # binds pending_generation_id; lifecycle is fresh_pending
        return _IcGenerationLifecycle(
            freshness_status="issue_centric_fresh",
            freshness_reason="pending_request_bound_to_generation",
            freshness_source="pending_request_state",
            generation_lifecycle="fresh_pending",
            generation_lifecycle_reason="pending_request_bound_to_generation",
            generation_lifecycle_source="pending_request_state",
            pending_generation_id=generation_id,
            route_selected=route_selected,
            fallback_reason=fallback_reason,
        )
    # generation present, no specific phase match → fresh_available
    return _IcGenerationLifecycle(
        freshness_status="issue_centric_fresh",
        freshness_reason="latest_issue_centric_generation_available",
        freshness_source="runtime_snapshot_generation",
        generation_lifecycle="fresh_available",
        generation_lifecycle_reason="latest_issue_centric_generation_available",
        generation_lifecycle_source="runtime_snapshot_generation",
        route_selected=route_selected,
        fallback_reason=fallback_reason,
    )


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
