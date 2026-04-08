# Runtime Touchpoint Inventory

This document inventories the minimum bridge/runtime touchpoints that would
matter for a future move toward `ready`-issue-first execution in
`ai-dev-orchestrator`.

It is intentionally an inventory / staging / boundary-definition document.
It does **not** implement runtime migration.

The first-party path remains:

- ChatGPT Projects
- Safari on macOS
- Codex CLI

This repository remains a narrow first-party workflow, not a generic browser
automation framework.

## What This Inventory Is For

The issue-centric foundation now has completed public examples for:

- GitHub bootstrap
- backlog cadence
- normal operator entry
- free-form override positioning
- completion-to-review handoff

The next safe step is to identify where future runtime changes would actually
land, while keeping the current runtime honest about what still stays manual or
report-based.

This inventory should make it easier to cut the next bounded runtime-ready
issues without smuggling in a broad rewrite.
For the current agreed interface shape those slices should converge toward, see
[ISSUE_CENTRIC_RUNTIME_CONTRACT.md](ISSUE_CENTRIC_RUNTIME_CONTRACT.md).

The first bounded implementation child cut from this inventory is
[#20](https://github.com/momotomo/ai-dev-orchestrator/issues/20), now a
completed example that keeps the change at the initial request boundary instead
of widening into report continuation or signal remapping.

## What Stays True In This Phase

During this inventory phase, all of the following stay unchanged:

- normal operation still starts from the current open `ready` issue
- if open `ready` is `0`, backlog curation still selects the next bounded
  `ready` issue first
- free-form initial input is still an exception / recovery / override path, not
  the normal entry
- same-chat remains the default continuation mode
- handoff / new-chat remains the exception path
- Safari timeout assumptions remain `1800 + 600` seconds before late-completion
  handling
- unsupported paths remain operator-risk paths with no behavioral guarantee

## Current Runtime Touchpoints

### 1. Initial Request Entry

- current files:
  - `scripts/request_next_prompt.py`
  - `scripts/bridge_orchestrator.py`
  - `scripts/start_bridge.py`
- current behavior:
  - the operator normally starts with a current `ready` issue reference
  - the bridge shapes a minimal initial request around that reference and
    appends only the fixed reply contract
  - free-form initial input still exists only as exception / recovery /
    override
  - the runtime records the normal path as `ready_issue:` and the override path
    as `override:`
- why this matters later:
  - this is still the clearest boundary where ready-issue-first runtime
    migration begins
- inventory conclusion:
  - this is a real runtime boundary
  - `#20` completed the first bounded change here without changing the whole
    state machine

### 2. Report-Based Next Request Generation

- current files:
  - `scripts/request_prompt_from_report.py`
  - `bridge/chatgpt_prompt_request_template.md`
  - `scripts/_bridge_common.py`
- current behavior:
  - the next request is built from the archived Codex report, optional resume
    note, `next_todo`, and `open_questions`
  - request provenance is recorded as `report:` or `handoff:`
- why this matters later:
  - ready-issue-first execution eventually needs the next request to point back
    to the intended ready issue or backlog home instead of carrying meaning only
    through prose
- inventory conclusion:
  - this is the second major runtime boundary after initial entry
  - it can likely be migrated in a bounded slice that keeps the current
    reply-contract flow intact

### 3. Request Provenance And Idempotency

- current files:
  - `scripts/request_next_prompt.py`
  - `scripts/request_prompt_from_report.py`
  - `scripts/_bridge_common.py`
- current behavior:
  - `pending_request_source` and related hashes now distinguish
    `ready_issue:`, `override:`, `report:`, `handoff:`, and
    `human_review_continue:` request families
  - idempotency guards use these sources to avoid duplicate sends
- why this matters later:
  - if the runtime becomes ready-issue-aware, request provenance likely needs a
    stable issue-oriented identity as well
- inventory conclusion:
  - this is a runtime-required boundary
  - it still does **not** require immediate state-machine redesign

### 4. Reply Extraction And Prompt Materialization

- current files:
  - `scripts/fetch_next_prompt.py`
  - `scripts/_bridge_common.py`
  - `bridge/prompt_extraction_rules.md`
- current behavior:
  - the runtime expects either `CHATGPT_PROMPT_REPLY` or `CHATGPT_NO_CODEX`
  - the runtime still reads ChatGPT reply text from visible DOM text
    (`innerText` / `textContent`), not from a markdown-lossless transport
  - extracted prompt text is written to `bridge/inbox/codex_prompt.md`
- why this matters later:
  - issue-centric runtime migration might eventually carry issue identifiers or
    backlog-home metadata in a more explicit way
  - markdown-fidelity transport is now its own runtime-adjacent boundary, not
    just a docs concern
- inventory conclusion:
  - `#22` is the completed bounded ready child for markdown-fidelity
    feasibility on top of this boundary
  - the observed feasibility verdict is recorded in
    `docs/MARKDOWN_FIDELITY_FEASIBILITY.md`
  - the first Plan A front-end slice can safely stop after extracting and
    validating the new JSON envelope plus opaque BASE64 body payloads
  - the next bounded Plan A transport slice can decode allowed BASE64 payloads
    into UTF-8 prepared artifacts and still stop safely before any issue
    mutation or execution wiring is added
  - the next bounded execution slice after that can connect only
    `action=issue_create` to a narrow GitHub issue-create mutation while still
    leaving close, follow-up, review automation, Codex dispatch, and Project
    placement out of scope
  - the next sibling execution slice can connect only `action=codex_run` to a
    narrow GitHub issue-comment mutation plus assembled launch payload while
    still leaving issue-centric Codex launch, close, follow-up, and review
    automation out of scope
  - the current bounded launch slice now goes one step farther: it reuses the
    existing `launch_codex_once` entrypoint with an issue-centric adapter after
    trigger-comment registration, while still stopping short of close,
    follow-up, review automation, or any broader state-machine rewrite
  - the current continuation slice goes one step farther again: it reuses the
    existing `codex_running` / `codex_done` / report recovery / archive /
    next-request preparation flow after issue-centric launch, while still
    leaving close, follow-up, review automation, Project updates, and any full
    cutover out of scope
  - the current close slice goes one step farther again: it wires
    `close_current_issue` as a narrow post-action mutation only for
    `no_action` closes and for `issue_create` closes that run after successful
    issue creation, while still leaving follow-up mutation, review automation,
    Project updates, and any broader cutover out of scope
  - the current review slice goes one step farther again: it wires
    `human_review_needed` to a narrow target-issue review comment mutation,
    and now allows the narrow `review comment -> close` happy path for
    `human_review_needed + close_current_issue = true`, while keeping
    follow-up mutation, Project updates, and broader post-review automation
    out of scope
  - the current Project slice goes one step farther again: it wires
    `issue_create` to narrow GitHub Project placement plus `State` field
    setting when explicit Project config is present, while keeping Project
    sync for other actions, follow-up mutation, and broader cutover out of
    scope
  - the current follow-up slice goes one step farther again: it adds
    `CHATGPT_FOLLOWUP_ISSUE_BODY` as a narrow transport block and wires
    `no_action + create_followup_issue` to one follow-up issue create path,
    optionally followed by `close_current_issue` only after follow-up creation
    and any required Project placement succeed; broader follow-up automation
    for other actions remains out of scope
  - the current review-followup combo slice goes one step farther again: it
    wires `human_review_needed + create_followup_issue` to a narrow
    `review comment -> follow-up issue create` path, and allows optional
    close only after that follow-up path succeeds; broader follow-up
    automation for `issue_create` / `codex_run` remains out of scope
  - the current issue-create-followup combo slice goes one step farther
    again: it wires `issue_create + create_followup_issue` to a narrow
    `primary issue create -> follow-up issue create -> optional close`
    path, reusing the same Project placement / `State` sync rules for both
    issues; broader `codex_run + create_followup_issue` automation remains
    out of scope
  - the current codex-run-followup combo slice goes one step farther again:
    it wires `codex_run + create_followup_issue` to a narrow
    `trigger comment -> Codex launch -> continuation handoff -> follow-up
    issue create` path
  - the current codex-run-followup-close combo slice goes one step farther
    again: it wires
    `codex_run + create_followup_issue + close_current_issue` to a narrow
    `trigger comment -> Codex launch -> continuation handoff -> follow-up
    issue create -> close` path, while broader `codex_run + close` and other
    multi-flag automation stay out of scope
  - the current dispatcher slice goes one step farther again: it moves the
    already-implemented issue-centric execution matrix out of
    `fetch_next_prompt.py` and into `scripts/issue_centric_execution.py`,
    keeping behavior stable while making path selection, step order, blocked
    combinations, and final-status aggregation explicit in one place
  - the current Project lifecycle slice goes one step farther again: it keeps
    created-issue Project placement as-is and now adds narrow current-issue
    `State` sync after successful `codex_run` (`in_progress`),
    `human_review_needed` (`review`), and `close_current_issue` (`done`)
    steps, while still refusing to auto-add a missing current-issue Project
    item or broaden Projects handling for other action families
  - the current continuation-summary slice goes one step farther again: it
    normalizes the latest issue-centric execution result into one summary with
    a principal issue candidate and next-request hint, then lets the existing
    report-based request builder append that summary to `CURRENT_STATUS`
    instead of forcing the next-request layer to inspect many scattered
    execution fields directly
  - the current next-request resolver slice goes one step farther again: it
    lets the report-based request builders prefer the normalized summary when
    resolving the next `target_issue`, while still falling back to older
    state-based hints if the summary is missing, stale, or unclear
  - the current route-selection slice goes one step farther again: it treats
    that normalized-summary resolver as the preferred next-request route only
    when summary, resolver, and state are coherent, and otherwise keeps the
    old report-based builder path as an explicit legacy fallback instead of
    making each builder guess independently
  - the current recovery slice goes one step farther again: it reloads the
    saved normalized summary and dispatch result on restart, reconciles them
    against saved issue-centric state, and only then allows the preferred
    issue-centric next-request route to continue; otherwise it records an
    explicit recovery fallback and keeps the legacy report path alive
  - the current runtime-snapshot slice goes one step farther again: it keeps
    the fine-grained `last_issue_centric_*` write path intact, but rebuilds a
    single snapshot for request preparation, operator-facing status, and
    restart/resume so the read side no longer has to stitch every field
    together independently
  - the current runtime-mode slice goes one step farther again: it evaluates
    that snapshot, recovery result, resolver output, and route-selection
    result together, then exposes one shared readiness gate
    (`issue_centric_ready` / `issue_centric_degraded_fallback` /
    `issue_centric_unavailable`) for request preparation and operator-facing
    status
  - the current freshness slice goes one step farther again: it gives that
    runtime snapshot one generation-aware freshness / invalidation layer so a
    previously good issue-centric context stops counting as ready once the
    same generation has already driven one next-request preparation or has
    been explicitly invalidated by legacy fallback
  - the current generation-lifecycle slice goes one step farther again: it
    aligns that freshness logic with the existing request lifecycle so an
    issue-centric generation is only `fresh_prepared` after request creation,
    `fresh_pending` after send / pending request handoff, and `consumed` only
    after reply recovery closes that request lifecycle
  - the current run-loop alignment slice goes one step farther again: it lets
    `run_until_stop.py` and the one-step orchestrator treat
    `fresh_prepared` as "send the prepared request without rebuilding it",
    `fresh_pending` as "wait for reply recovery", and degraded / unavailable
    issue-centric mode as explicit fallback instead of relying only on the
    older ad-hoc mode branches
  - the current state-view normalization slice goes one step farther again:
    it keeps the legacy `mode` enum for compatibility, but now writes a thin
    issue-centric read-side overlay (`last_issue_centric_state_view`,
    `last_issue_centric_wait_kind`) so `state.json`, CLI/operator wording,
    and request-side summaries can describe prepared / pending / consumed /
    invalidated states without a full state-machine rewrite
  - the current route-role normalization slice goes one step farther again:
    it keeps the legacy request-centric path in place, but now treats it as
    an explicit fallback path for degraded / unavailable / invalidated
    issue-centric states instead of as a peer default alongside the
    issue-centric preferred route
  - the current issue-centric spine closure slice goes one step farther
    again: it lets the ordinary prepare / send / fetch / recovery /
    next-request loop follow the same issue-centric preferred route end to
    end, while keeping the older request-centric path only as the explicit
    fallback for degraded / unavailable / invalidated states
  - current evidence is strong enough to say visible-text extraction is lossy,
    but not strong enough to promote the UI copy path as the primary transport
  - the next implementation slice should therefore prefer Plan A
    (BODY/base64 transport) over making Plan B the default runtime path

### 5. Same-Chat Continuation, Handoff, And Project-Page Send Signals

- current files:
  - `scripts/request_prompt_from_report.py`
  - `scripts/fetch_next_prompt.py`
  - `scripts/_bridge_common.py`
  - `bridge/run_one_cycle.md`
- current behavior:
  - ordinary report continuation stays in the same chat
  - handoff / new-chat happens only before the next ChatGPT request when late
    completion or other exception conditions require it
  - `submitted_unconfirmed` and `pending_request_signal` represent delivery-layer
    uncertainty around project-page send confirmation
- why this matters later:
  - ready-issue-first migration must not casually break late-completion or
    project-page recovery semantics
- inventory conclusion:
  - these are not first-slice rewrite targets
  - they should be preserved as delivery/runtime signals until a later dedicated
    mapping phase

### 6. Operator-Facing CLI Guidance And Runbook Wording

- current files:
  - `scripts/start_bridge.py`
  - `scripts/run_until_stop.py`
  - `README.md`
  - `bridge/README_BRIDGE_FLOW.md`
- current behavior:
  - operator guidance now treats `ready` issue reference as the normal runtime
    entry
  - free-form first input is now described as exception / recovery / override
  - docs and CLI wording now align on `normal = ready issue`,
    `exception = free-form override`
- why this matters later:
  - future runtime slices still need to carry that issue identity farther than
    the initial entry boundary
- inventory conclusion:
  - `#20` completed the first wording-alignment slice
  - report-continuation wording is still a later migration boundary

### 7. State Machine And Persisted Runtime Fields

- current files:
  - `scripts/bridge_orchestrator.py`
  - `scripts/run_until_stop.py`
  - `scripts/_bridge_common.py`
  - `bridge/state_flow.md`
- current behavior:
  - write-side compatibility still keeps `mode`, `need_chatgpt_prompt`,
    `need_chatgpt_next`, `need_codex_run`, and related recovery fields alive
  - read-side consumers now prefer the issue-centric runtime snapshot,
    readiness gate, generation lifecycle, and thin state-view bridge instead
    of reading only the legacy `mode` branches
  - issue-aware identity is now persisted narrowly through fields such as
    principal issue, next-request target, runtime snapshot, and route /
    recovery metadata
  - the ordinary prepare / send / fetch / recovery / next-request loop now
    treats the issue-centric spine as the mainline path and uses the older
    request-centric route only as an explicit fallback
- why this matters later:
  - the rewrite phase should preserve the current issue-centric read-side
    semantics even if it reshapes the older write-side `mode` branches
  - full issue-number-based orchestration may still simplify or replace some
    of these compatibility fields later, but should not demote the
    issue-centric spine back to a peer route
- inventory conclusion:
  - the current read-side source of truth is now snapshot-first and
    issue-centric-first
  - the next rewrite should keep these semantics stable:
    `fresh_prepared` means send/reuse, `fresh_pending` means wait/recover,
    `issue_centric_consumed` means the lifecycle closed, and
    `issue_centric_invalidated` means fallback/reset/inconsistency
  - legacy `mode` and related request-centric branches remain compatibility
    and fallback surfaces, not the preferred read-side source
  - this section is now a rewrite boundary, not a pre-runtime placeholder

## Current Routing Classification (Post Full-Cutover Streamline)

> Status as of 2026-04-09 (post-cutover streamline merged; normal path dispatch-plan primary).
> Previously "Full Cutover Pre-Stage" (slice 7 merged 2026-04-08).
> Classify each touchpoint as **primary**, **Codex lifecycle**, or **safety fallback**
> so the next Codex lifecycle reshape phase can track exactly what still needs to move.

### Primary — Issue-Centric Spine / Dispatch Plan / Action-View

These touchpoints now route through `resolve_runtime_dispatch_plan()` and
`RuntimeDispatchPlan`.  They are the authoritative source for operator-facing
decisions whenever the runtime is not inside the Codex lifecycle branch.

| Touchpoint | Primary routing |
|---|---|
| Run-loop action selection | `resolve_runtime_dispatch_plan()` → `next_action` |
| Operator stop summary `## next_step` | `RuntimeDispatchPlan` fields + `action_stop_note` |
| Operator progress note / guidance | `format_next_action_note()` / `format_operator_stop_note()` |
| `issue_centric_route_note()` | dispatch plan + runtime mode + route choice |
| Fetch-substate detection | `is_fetch_extended_wait_state()` / `is_fetch_late_completion_state()` |
| `completed` / `no_action` detection | `is_completed_state()` / action-view helpers |
| Operator entry status label | `present_bridge_status()` |

### Codex Lifecycle Compatibility Branch

These touchpoints remain mode-driven until full cutover.
They are **not** subject to dispatch-plan routing.
`mode` ∈ `{ready_for_codex, codex_running, codex_done}` drives them directly.

| Touchpoint | Mode |
|---|---|
| `bridge_orchestrator.py` Codex launch | `ready_for_codex + need_codex_run = true` |
| `bridge_orchestrator.py` Codex wait | `codex_running` |
| `bridge_orchestrator.py` Codex done → archive | `codex_done` |
| `resolve_unified_next_action()` Codex labels | `ready_for_codex` / `codex_running` / `codex_done` |
| `should_include_codex_progress()` | `ready_for_codex` / `codex_running` / `codex_done` |
| `stale_codex_running_note()` | `codex_running` state recovery instructions |

Full cutover target: replace these with `action=launch_codex_once` /
`action=wait_for_codex_report` / `action=handle_codex_done` action-view equivalents.

### Safety Fallback — Legacy Request-Centric Route

These touchpoints activate only when the issue-centric runtime is
`degraded_fallback`, `unavailable`, or `invalidated`.
They are **not** the normal path.  Operator-facing wording marks them with
`is_fallback = True` and an explicit `fallback_reason`.

| Touchpoint | Fallback condition |
|---|---|
| `run_until_stop.py` legacy request-centric branches | `fallback_legacy` runtime action |
| `bridge_orchestrator.py` legacy request scripts | never reached when dispatch plan routes first |
| `format_operator_stop_note()` fallback phrases | `plan.is_fallback = True` |
| `format_next_action_note()` fallback phrases | `fallback_legacy` runtime action |
| `resolve_issue_centric_route_choice()` legacy path | `route_selected == "fallback_legacy"` |
| `issue_centric_route_note()` fallback strings | unavailable / invalidated / degraded / stale |

Full cutover target: remove these branches once Codex lifecycle branch is
replaced and the issue-centric spine is stable end-to-end.

## Boundary Between Docs Work And Runtime Work

The following have already moved far enough through docs and GitHub operations
that they do not require runtime changes first:

- source-of-truth layering
- ready issue as the normal execution-unit reference
- planned-to-ready promotion cadence
- free-form input as exception / recovery / override
- completion comment to review handoff

The following require runtime work before the bridge itself becomes more
ready-issue-first:

- changing how the initial request is scaffolded or sourced
- carrying ready-issue or backlog-home identity through report-based
  continuation
- extending request provenance beyond `ready_issue:` / `override:` /
  `report:` / `handoff:`
- broadening the dispatcher matrix beyond the current narrow execution paths

- updating operator-facing runtime wording so it matches the new behavior

The following should stay deferred until later runtime phases:

- issue-number-driven orchestration as a primary runtime mechanism
- state-machine redesign
- changes to same-chat vs handoff semantics
- changes to late completion, `submitted_unconfirmed`, or
  `pending_request_signal` behavior

## Minimal Staging Proposal

Use the runtime-adjacent work in this order:

1. inventory only
   - document the touchpoints and boundaries
   - keep runtime behavior unchanged
2. request-source boundary slice
   - make the initial runtime entry more ready-issue-aware
   - keep same-chat, handoff, and reply parsing unchanged
3. report-continuation boundary slice
   - carry ready-issue or backlog-home identity through the next-request path
   - preserve current late-completion handling
4. signal-mapping slice
   - map `submitted_unconfirmed`, `pending_request_signal`, and late-completion
     states into the future issue-centric model without regressing delivery
     recovery
5. orchestration slice
   - only after the earlier boundaries are proven small enough

## Next Likely Runtime-Ready Slices

This inventory should make the next bounded slices easier to cut from `#10` and
Epic `#4`.

Likely next candidates:

- completed bounded implementation child:
  - [#20 Ready: accept a ready issue reference as the normal initial bridge entry](https://github.com/momotomo/ai-dev-orchestrator/issues/20)
- completed bounded feasibility child:
  - [#22 Ready: test markdown-fidelity copy-response feasibility for bridge reply extraction](https://github.com/momotomo/ai-dev-orchestrator/issues/22)
- current open ready child:
  - none
- one likely next ready slice after `#22`:
  - move reply-body transport off visible DOM text and onto a bounded
    BODY/base64 path without changing same-chat or late-completion semantics
- one ready issue that adds issue-aware provenance to report-based continuation
  without changing late-completion or handoff behavior
- one later ready issue that maps late-completion and project-page send
  signals into the future issue-centric model

Until those slices are promoted, this document is the current boundary record
for runtime-adjacent work.

## Legacy Route Inventory (Post-Cutover, 2026-04-09)

> Status: full cutover (normal path) and post-cutover streamline are both merged.
> Normal path is now dispatch-plan primary.  This section classifies remaining
> legacy / compatibility surfaces as of the post-cutover streamline.

### Classification Key

- **DELETE**: can be removed once the Codex lifecycle branch is reshaped into action-view
- **MAINTAIN**: required for safety fallback / recovery; do not remove yet
- **NAME-ONLY**: behavior stays; rename / docstring / comment cleanup only
- **DEFER**: do not touch until a later dedicated phase

---

### A. Codex Lifecycle Compatibility Branch (mode-driven routing)

> **2026-04-08 (phase7 centralize)**: `CodexLifecycleView` + `resolve_codex_lifecycle_view()`
> introduced.  Scattered per-site mode switches replaced.
>
> **2026-04-08 (phase7 action-bridge)**: `resolve_unified_next_action()` added to
> `_bridge_common.py` as the single authoritative answer for "what action next?" across
> ALL state classes.  Both Codex lifecycle compatibility and normal dispatch-plan paths
> return the same action-key vocabulary through this one function.
> `describe_next_action()` in `run_until_stop.py` was a thin wrapper over it and has now
> been inlined.
> `is_codex_lifecycle_state()` import removed from `run_until_stop.py` and
> `bridge_orchestrator.py` (was unused in code; only referenced in comments).
> `is_normal_path_state()` now delegates to `resolve_codex_lifecycle_view()` instead
> of reading `CODEX_LIFECYCLE_MODES` directly.
>
> **2026-04-08 (phase7 status-view-guard)**: `is_codex_lifecycle_state()` function deleted.
> Its single remaining call site inside `resolve_codex_lifecycle_view()` is replaced with
> an inline `mode not in CODEX_LIFECYCLE_MODES` check.  `present_bridge_status()` Codex
> lifecycle outer guard (previously `is_codex_lifecycle_state()`) is now fully enclosed
> inside `resolve_codex_lifecycle_view()` — the call site only sees the view.  Status/view
> callers no longer hold any raw lifecycle classification dependency.
>
> **2026-04-08 (phase7 lifecycle-modes-inline)**: `CODEX_LIFECYCLE_MODES` constant deleted.
> `resolve_codex_lifecycle_view()` now uses a local inline set literal `{"ready_for_codex",
> "codex_running", "codex_done"}` for the guard check.  The three lifecycle mode strings are
> no longer exported from the module; all lifecycle classification knowledge is contained
> inside `resolve_codex_lifecycle_view()` itself.
>
> **2026-04-08 (phase7 describe-next-action-inline)**: `describe_next_action()` in
> `run_until_stop.py` deleted.  All 9 call sites replaced with direct
> `resolve_unified_next_action()` calls.  `resolve_unified_next_action()` is now the single,
> unambiguous authority for "next action?" across all callers — no local wrapper remains.

| Item | File | Classification | Status (2026-04-08) | Gate to remove |
|---|---|---|---|---|
| `resolve_unified_next_action()` | `_bridge_common.py` | **MAINTAIN** | Canonical "next action?" for all states; covers lifecycle + normal path | Remove together with lifecycle guards after action-view reshape |
| `resolve_codex_lifecycle_view()` | `_bridge_common.py` | **MAINTAIN** | **Sole** classification authority; lifecycle mode strings are internal to this helper only | After action-view reshape |
| `CodexLifecycleView` dataclass | `_bridge_common.py` | **MAINTAIN** | Carries action, status wording, is_blocked; used by display and dispatch layers | Same gate |
| `describe_next_action()` | `run_until_stop.py` | **REMOVED** ✅ | (describe-next-action-inline) Deleted; all call sites replaced with direct `resolve_unified_next_action()` calls | ✅ done |
| `is_codex_lifecycle_state()` outer guard in `describe_next_action()` | `run_until_stop.py` | **REMOVED** ✅ | (action-bridge) import removed | ✅ done |
| `is_codex_lifecycle_state()` outer guard in `bridge_orchestrator.run()` | `bridge_orchestrator.py` | **REMOVED** ✅ | (action-bridge) import removed | ✅ done |
| `is_codex_lifecycle_state()` use in `present_bridge_status()` | `_bridge_common.py` | **REMOVED** ✅ | (centralize) Inside `resolve_codex_lifecycle_view()`; callers see only the view | ✅ done |
| `is_codex_lifecycle_state()` function | `_bridge_common.py` | **REMOVED** ✅ | (status-view-guard) Deleted; check inlined into `resolve_codex_lifecycle_view()` | ✅ done |
| `is_normal_path_state()` | `_bridge_common.py` | **MAINTAIN** | Delegates to `resolve_codex_lifecycle_view()`; stays in sync with lifecycle authority | After action-view reshape |
| `mode` reads inside Codex lifecycle blocks | various | **REMOVED** ✅ | Centralised into `resolve_codex_lifecycle_view()` | ✅ done |
| `should_include_codex_progress()` mode reads | `run_until_stop.py` | **MAINTAIN** | Codex lifecycle progress snapshot for operator wording; not yet centralised | After Codex lifecycle reshape |
| `stale_codex_running_note()` and stale guard reads | `run_until_stop.py` | **MAINTAIN** | Stale runtime detection for codex_running must survive until action-view | After Codex lifecycle reshape |
| `CODEX_LIFECYCLE_MODES` constant | `_bridge_common.py` | **REMOVED** ✅ | (lifecycle-modes-inline) Deleted; mode strings inlined into `resolve_codex_lifecycle_view()` | ✅ done |

**Next deletion priority (minimum safe unit when action-view reshape is ready):**
1. `resolve_codex_lifecycle_view()` outer call in `bridge_orchestrator.run()` and `present_bridge_status()`
   Gate: action-view equivalents wired in state machine (lifecycle states reshaped into normal-path actions).
2. `describe_next_action()` in `run_until_stop.py` — ~~inline the single `resolve_unified_next_action()` call~~ **✅ Done (describe-next-action-inline phase)**
3. `resolve_fallback_legacy_transition()` itself — remaining arms cover only legacy request-centric modes;
   gate: legacy request-centric path (idle/awaiting_user/waiting_prompt_reply) fully replaced by dispatch plan.

> **2026-04-08 (phase7 fallback-arms-cleanup)**: `resolve_fallback_legacy_transition()` Codex
> lifecycle 3 arms (`ready_for_codex`, `codex_running`, `codex_done`) deleted.  Deletion gate
> cleared by adding a `resolve_codex_lifecycle_view()` guard at the top of `summarize_run()` in
> `run_until_stop.py`: lifecycle `final_state` now produces `_summary_next_action` from
> `lifecycle_view.action` and is never forwarded to `resolve_runtime_dispatch_plan()`.
> `resolve_codex_lifecycle_view` import added to `run_until_stop.py`.

---

### B. Safety Fallback Helpers (legacy request-centric transition chain)

| Item | File | Classification | Why it remains | Gate to remove |
|---|---|---|---|---|
| `resolve_fallback_legacy_transition()` | `_bridge_common.py` | **MAINTAIN** | Called by `resolve_runtime_dispatch_plan()` when `is_fallback=True`; Codex lifecycle arms removed | Legacy request-centric path fully replaced |
| `resolve_next_generation_transition()` | `_bridge_common.py` | **MAINTAIN** | Called by dispatch plan for `need_next_generation` runtime action | Same as above |
| `resolve_fallback_legacy_transition()` Codex lifecycle branches inside | `_bridge_common.py` | **REMOVED** ✅ | (fallback-arms-cleanup) Deleted; `summarize_run()` now guards lifecycle state via `resolve_codex_lifecycle_view()` | ✅ done |
| `format_next_action_note()` fallback phrases | `_bridge_common.py` | **MAINTAIN** | Operator-facing wording for `is_fallback=True` plan; actively needed | Same as fallback transition gate |
| `issue_centric_route_note()` fallback strings in `run_until_stop.py` | `run_until_stop.py` | **MAINTAIN** | Per-condition fallback reason wording for operator guidance | Same gate |

---

### C. Legacy `mode` Field and `need_*` Fields

| Item | Classification | Why it remains | Gate to remove |
|---|---|---|---|
| `mode` written by `bridge_orchestrator.py` and request scripts | **MAINTAIN** | Downstream callers, display, state_signature() change detection | mode demotion phase |
| `need_chatgpt_prompt` / `need_chatgpt_next` / `need_codex_run` | **MAINTAIN** | Write-path still used; read-path already behind helpers | mode demotion phase |
| `state_signature()` includes `mode`, `need_chatgpt_*`, `need_codex_run` | **MAINTAIN** | Change-detection tuple must remain stable; these fields still change | mode demotion phase |
| `## debug` section `mode_compat` in stop summary | **NAME-ONLY** | Already labeled `_compat`; documents survival as compat field | No action needed now |
| `mode` in history entries (`before=... after=...`) | **MAINTAIN** | Debug trace value; helps diagnose stale state or loop issues | mode demotion phase |

---

### D. `resolve_issue_centric_route_choice()` and `resolve_issue_centric_preferred_loop_action()`

| Item | Classification | Why it remains | Gate to remove |
|---|---|---|---|
| `resolve_issue_centric_route_choice()` | **MAINTAIN** | Called internally by `resolve_runtime_dispatch_plan()` to populate `route_choice` | No standalone callers outside dispatch plan; refactor opportunity after legacy path removal |
| `resolve_issue_centric_preferred_loop_action()` | **REMOVED** (2026-04-08) | Thin wrapper; no callers outside tests; deletion completed in phase7 cleanup | — |
| mode reads inside `resolve_issue_centric_route_choice()` (for `fresh_pending` / `fresh_prepared` loop action) | **MAINTAIN** | Compatibility guard for `preferred_loop_action` resolution; used when `route_choice.preferred_loop_action` is consulted | Same gate as above |

---

### E. Legacy Wording / Comment Cleanup (already-done and residual)

| Item | Classification | Status |
|---|---|---|
| `present_bridge_status()` normal path | **done** | Dispatch plan primary; no legacy mode reads in normal path |
| `present_bridge_handoff()` normal path | **done** | `is_completed_state()` replaces raw mode/need_* reads |
| `format_next_action_note()` `request_prompt_from_report` branch | **done** | `is_awaiting_user_supplement()` replaces raw mode read |
| `resolve_runtime_next_action()` docstring | **done** | "internal dispatch step" framing |
| `resolve_fallback_legacy_transition()` docstring | **done** | "safety fallback only" framing |
| `resolve_next_generation_transition()` docstring | **done** | "residual compatibility helper" framing |
| `resolve_issue_centric_preferred_loop_action()` | **done** | Removed 2026-04-08; callers updated to `resolve_issue_centric_route_choice()` directly |

---

### F. Next Safe Deletion Ordering

The following ordering minimises risk:

1. **Docstring / wording cleanup only** (no behavior change):
   - Any remaining `legacy default` / `peer route` language in comments
   - `## debug` section labels that still imply mode is a routing subject

2. **`resolve_fallback_legacy_transition()` Codex arms removed** (after Codex lifecycle reshape):
   - `ready_for_codex` / `codex_running` / `codex_done` arms inside the fallback
     chain become unreachable once Codex lifecycle is action-view; remove them then

3. **`is_codex_lifecycle_state()` guards removed with Codex lifecycle reshape**:
   - All three guard blocks go away simultaneously when action-view replaces mode-driven Codex routing

4. **`mode` / `need_*` field demotion** (last; requires dedicated phase):
   - Do not remove until all runtime writers are confirmed to not depend on read-side mode
   - `state_signature()` must be updated at the same time
