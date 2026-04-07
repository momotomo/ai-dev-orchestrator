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
  - routing is driven by `mode`, `need_chatgpt_prompt`, `need_chatgpt_next`,
    `need_codex_run`, and related recovery fields
  - no issue identifier is currently persisted as a first-class runtime field
- why this matters later:
  - full issue-number-based orchestration would eventually want more explicit
    issue-aware routing
- inventory conclusion:
  - this is explicitly **not** a target for the current phase
  - first runtime-ready slices should avoid state-machine expansion unless the
    inventory proves it is unavoidable

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
