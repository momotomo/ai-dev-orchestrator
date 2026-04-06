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
  - the operator types the first request body
  - the bridge appends only the fixed reply contract
  - the runtime records this as an `initial:` request source
- why this matters later:
  - this is the clearest boundary where a future ready-issue reference could
    become the runtime's primary execution anchor
- inventory conclusion:
  - this is a real runtime boundary
  - the first future runtime slice can likely change request scaffolding here
    without changing the whole state machine

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
  - `pending_request_source` and related hashes distinguish `initial:`,
    `report:`, `handoff:`, and `human_review_continue:` request families
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
  - extracted prompt text is written to `bridge/inbox/codex_prompt.md`
- why this matters later:
  - issue-centric runtime migration might eventually carry issue identifiers or
    backlog-home metadata in a more explicit way
- inventory conclusion:
  - no change is required for the inventory phase
  - the first runtime-adjacent slice can probably leave reply parsing unchanged

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
  - operator guidance still mentions the user-authored first request because the
    current runtime still asks for it
  - docs now explain that normal operation should already be `ready`-issue-first
- why this matters later:
  - once the runtime becomes more ready-aware, CLI wording will need to stop
    sounding like free-form input is the normal planning surface
- inventory conclusion:
  - some wording can still improve in docs
  - runtime-facing CLI text is a real migration boundary once behavior changes

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
- extending request provenance beyond `initial:` / `report:` / `handoff:`
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

- one ready issue that narrows the initial request boundary around the current
  `ready` issue while preserving manual override cases
- one ready issue that adds issue-aware provenance to report-based continuation
  without changing late-completion or handoff behavior
- one later ready issue that maps late-completion and project-page send signals
  into the future issue-centric model

Until those slices are promoted, this document is the current boundary record
for runtime-adjacent work.
