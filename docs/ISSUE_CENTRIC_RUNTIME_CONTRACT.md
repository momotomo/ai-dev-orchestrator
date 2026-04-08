# Issue-Centric Runtime Contract

This document records the current agreed design contract for the phased move
toward issue-centric runtime operation in `ai-dev-orchestrator`.

Use this document as the runtime / interface contract source of truth for
follow-up implementation work.
Use [ISSUE_CENTRIC_FLOW.md](ISSUE_CENTRIC_FLOW.md) for source-of-truth layers,
[GITHUB_ISSUE_PROJECTS_OPERATIONS.md](GITHUB_ISSUE_PROJECTS_OPERATIONS.md) for
GitHub-side operating rules, and
[RUNTIME_TOUCHPOINT_INVENTORY.md](RUNTIME_TOUCHPOINT_INVENTORY.md) for the
staging boundaries that led to this contract.

This is still a design document.
It does **not** claim that the full contract below is already implemented in
the bridge runtime.

The current implementation boundary is:

- implemented: parser / validator / normalizer front-end
- implemented: BASE64 payload decode into prepared runtime artifacts
- implemented: narrow `issue_create` execution from decoded `CHATGPT_ISSUE_BODY`
- implemented: narrow GitHub Project placement for `issue_create` when
  `github_project_url` and required State config are present
- implemented: narrow `codex_run` execution from decoded `CHATGPT_CODEX_BODY`
  through target-issue trigger comment creation
- implemented: narrow issue-centric Codex launch wiring that turns the
  assembled `repo / target_issue / request / trigger_comment` payload into a
  runtime prompt and delegates to the existing `launch_codex_once` entrypoint
- implemented: narrow continuation handoff from issue-centric `codex_run`
  launch into the existing `codex_running` / `codex_done` / report archive /
  next-request preparation flow
- implemented: narrow `close_current_issue` mutation for safe `no_action`
  closes and for `issue_create` closes that run only after the new issue is
  created successfully
- implemented: narrow `human_review_needed` execution that posts decoded
  `CHATGPT_REVIEW` as a target-issue review comment
- implemented: narrow post-review close for
  `human_review_needed + close_current_issue = true` after review comment
  posting succeeds
- implemented: narrow `no_action + create_followup_issue` execution that
  decodes `CHATGPT_FOLLOWUP_ISSUE_BODY`, creates one follow-up issue, and
  closes the current issue only after follow-up creation / Project placement
  succeeds
- implemented: narrow `human_review_needed + create_followup_issue`
  execution that posts the review comment first, then creates one follow-up
  issue, and only then evaluates optional close
- implemented: narrow `issue_create + create_followup_issue` execution that
  creates the primary issue first, then creates one follow-up issue, and only
  then evaluates optional close
- implemented: narrow `codex_run + create_followup_issue` execution that
  completes trigger comment registration, Codex launch / continuation handoff,
  and only then creates one follow-up issue
- implemented: narrow
  `codex_run + create_followup_issue + close_current_issue` execution that
  closes the current issue only after trigger comment registration, Codex
  launch / continuation handoff, and follow-up issue create all succeed
- implemented: `scripts/issue_centric_execution.py` as the current execution
  dispatcher / orchestrator for the already-supported narrow execution matrix
- implemented: narrow current-issue GitHub Project `State` lifecycle sync for
  project-configured repos after successful `codex_run`
  (`in_progress`), `human_review_needed` (`review`), and
  `close_current_issue` (`done`) steps
- implemented: narrow normalized issue-centric continuation summary that
  records the current issue, created issues, closed issue, lifecycle-sync
  result, principal issue candidate, and next-request hint for the next
  ChatGPT request layer
- implemented: narrow next-request target resolver that uses the normalized
  summary to choose the next ChatGPT `target_issue` context before falling
  back to older state-based hints
- implemented: narrow route-selection layer that prefers the issue-centric
  normalized summary / resolver result for the next ChatGPT request only when
  the summary is coherent, the resolved `target_issue` is stable, and the
  latest issue-centric execution did not end in fatal failure
- implemented: narrow restart-safe recovery / rehydration layer that reloads
  normalized summary, dispatch result, and saved issue-centric state before
  next-request preparation, then reuses issue-centric routing only when that
  recovered context remains coherent
- implemented: narrow issue-centric runtime snapshot / state bridge that keeps
  writing fine-grained `last_issue_centric_*` fields but lets request
  preparation, operator-facing status, and restart/recovery read one
  normalized snapshot first and fall back only when that snapshot is missing
  or inconsistent
- implemented: narrow run-loop alignment that now treats issue-centric
  `fresh_prepared` and `fresh_pending` generations as first-class state when
  choosing the next runtime action, so prepared requests are reused instead of
  being rebuilt, pending generations stay in reply-wait, and degraded /
  unavailable modes fall back explicitly
- implemented: narrow issue-centric state-view bridge that keeps the legacy
  `mode` field for compatibility but now also writes read-side helper fields
  such as `last_issue_centric_state_view` and
  `last_issue_centric_wait_kind`, so `state.json`, CLI status, and request
  summaries can say "prepared request", "pending reply", "consumed", or
  "invalidated" without rewriting the whole state machine
- implemented: narrow route-role normalization that now treats the
  issue-centric path as the default read-side route whenever the runtime is
  `issue_centric_ready`, while keeping the older request-centric path as an
  explicit legacy fallback only for degraded / unavailable / invalidated
  situations
- implemented: narrow issue-centric spine closure for the normal loop, so
  prepare / send / fetch / recovery / next-request resolution now prefer one
  shared issue-centric route choice and only drop to the older
  request-centric helpers when the runtime is degraded, unavailable, or
  invalidated
- not yet implemented: follow-up mutation for other actions or broader
  post-review automation
- not yet implemented: large state-machine rewrite or full contract cutover

The current dispatcher-owned execution matrix is:

- `issue_create`
- `issue_create + close_current_issue = true`
- `issue_create + create_followup_issue = true`
- `issue_create + create_followup_issue = true + close_current_issue = true`
- `codex_run`
- `codex_run + create_followup_issue = true`
- `codex_run + create_followup_issue = true + close_current_issue = true`
- `human_review_needed`
- `human_review_needed + close_current_issue = true`
- `human_review_needed + create_followup_issue = true`
- `human_review_needed + create_followup_issue = true + close_current_issue = true`
- `no_action`
- `no_action + create_followup_issue = true`
- `no_action + create_followup_issue = true + close_current_issue = true`

The dispatcher still blocks these combinations on purpose:

- `codex_run + close_current_issue = true`
- multi-flag combinations outside the narrow paths above

The current read-side bridge is intentionally layered like this:

- fine-grained `last_issue_centric_*` fields remain the write-side execution
  record
- the normalized summary derives the next principal issue candidate and
  next-request hint
- the next-request resolver turns that summary into a narrow `target_issue`
  proposal
- route selection decides whether issue-centric or legacy fallback should own
  the next request
- restart-safe recovery rehydrates that decision after interruption
- the runtime snapshot is the current read-side bridge that packages those
  outputs into one preferred source for request preparation, operator status,
  and resume/recovery
- the runtime readiness / health gate now decides whether that recovered
  snapshot is `issue_centric_ready`, `issue_centric_degraded_fallback`, or
  `issue_centric_unavailable` before request builders and operator-facing
  status consume it
- the freshness / invalidation helper now decides whether that snapshot is
  still fresh enough to count as ready, has gone stale because the same
  generation already drove one next-request preparation, or has been
  explicitly invalidated because the bridge had to adopt legacy fallback for
  that generation
- the generation-lifecycle layer now sits between request preparation and
  freshness so the bridge can distinguish `fresh_prepared`,
  `fresh_pending`, `consumed`, and `invalidated` generations instead of
  treating every prepared next request as already consumed
- the run loop now uses that same runtime mode plus generation lifecycle when
  choosing the next step: `fresh_prepared` means reuse and send the prepared
  request, `fresh_pending` means wait for reply recovery, and only
  `consumed` / `invalidated` / degraded states fall back to the older
  request-centric branches
- the thin state-view bridge now sits on top of those layers for read-side
  consumers: it does not replace the legacy `mode` enum, but it gives
  `state.json`, operator status, and request-side summaries one normalized
  vocabulary for "prepared request", "pending reply", "consumed", and
  "invalidated"
- the route-choice layer now also gives those same read-side consumers one
  shared answer to "preferred or fallback?": if the runtime is
  `issue_centric_ready`, issue-centric is the first choice for prepare /
  send / fetch / next-action resolution; otherwise the older request-centric
  path is preserved only as an explicit conditional fallback
- the normal prepare / send / fetch / recovery / next-request loop now closes
  through that same issue-centric spine as long as the runtime stays ready:
  prepared requests stay on the issue-centric path, pending reply recovery
  stays there, and the next-request layer only falls back when degraded /
  unavailable / invalidated conditions explicitly require it

Until full cutover, snapshot-first reads still coexist with legacy fallback.

## Full Cutover (Normal Path): Dispatch Plan Is Primary Subject

> Status as of 2026-04-09 (phase 7 full cutover merged)
> Previously: "Full Cutover Pre-Stage" (slice 7 merged 2026-04-08)

The dispatch plan / action-view layer is now the **primary** source of truth
for all operator-facing routing decisions **except** the Codex lifecycle branch.
The legacy `mode`-driven request-centric path is now a **safety fallback**, not
a peer alternative.

### What is the primary path

For any runtime state that is not inside `ready_for_codex` / `codex_running` /
`codex_done`:

- **`resolve_runtime_dispatch_plan(state)`** returns the authoritative
  `next_action` / `runtime_action` / `is_fallback` answer.
- Operator-facing status, stop summaries, and guided notes all read from
  `RuntimeDispatchPlan` first.
- `mode` is written for compatibility and Codex lifecycle display only; it
  is not consulted for routing in the normal path.

### What is the Codex lifecycle compatibility branch

`ready_for_codex` / `codex_running` / `codex_done` remain mode-driven
compatibility branches.  They are **not** subject to dispatch-plan routing.
When the runtime is in one of these modes, the bridge dispatches directly to
the Codex lifecycle handlers without going through `resolve_runtime_dispatch_plan`.

These branches are kept as-is until full cutover.  Full cutover may eventually
replace them with an action-view equivalent, but that change is out of scope
until the rest of the state machine is reshaped.

### What is the safety fallback

The **legacy request-centric route** is now an **explicit safety fallback** that
activates only when the issue-centric runtime is:

- `issue_centric_degraded_fallback` — snapshot exists but is degraded
- `issue_centric_unavailable` — snapshot or runtime prerequisites are missing
- `issue_centric_invalidated` — the current generation was explicitly invalidated

In all other cases, the issue-centric preferred route owns the next action.
Operator-facing wording marks fallback exits with `is_fallback = True` in the
dispatch plan and with explicit fallback-reason notes in the stop summary.

### What is replaced vs what remains

**Replaced / centralized (slices 1–7):**

- `resolve_runtime_dispatch_plan()` — normal-path routing authority
- `RuntimeDispatchPlan` — operator vocabulary for action, route, fallback
- `describe_next_action()` / `completed` / `no_action` detection —
  action-view primary, Codex lifecycle guards retained
- Operator stop summary `## next_step` — dispatch plan fields + `action_stop_note`
- `format_operator_stop_note()` — shared action-view stop phrasing
- `format_next_action_note()` — shared next-action phrasing
- `is_awaiting_user_supplement()` / `is_fetch_extended_wait_state()` /
  `is_fetch_late_completion_state()` — substate reads abstracted from callers
- Loop heartbeat fetch-substate detection — new helpers, no raw mode reads

**Retained as compatibility / safety fallback (not yet replaced):**

- Codex lifecycle branch (`ready_for_codex` / `codex_running` / `codex_done`) —
  mode-driven, kept until full cutover
- Legacy request-centric prepare / fetch / next-step helpers —
  activated only when `is_fallback = True`
- `mode` field — written for compatibility display and downstream callers
- `state_signature()` change-detection utility — includes `mode` by design

### What changed in this full cutover phase

**New explicit boundaries added (phase 7 full cutover):**

- `CODEX_LIFECYCLE_MODES` — frozenset constant naming all Codex lifecycle modes
- `is_codex_lifecycle_state(state)` — returns `True` when the runtime is inside
  the Codex lifecycle compatibility branch (mode-driven, not dispatch-plan-routed)
- `is_normal_path_state(state)` — returns `True` when dispatch plan is the sole
  routing authority (not Codex lifecycle, not pending dispatch)
- `describe_next_action()` — `is_codex_lifecycle_state()` guard replaces raw
  mode comparisons; normal-path fallthrough uses `resolve_runtime_dispatch_plan()`
- `run()` main loop — `describe_next_action(before)` called **once** per iteration
  (double-call eliminated); result drives all routing in that iteration
- `bridge_orchestrator.py run()` — `is_codex_lifecycle_state()` guard replaces
  raw `mode = str(state.get(...))` reads for Codex lifecycle branches

### Remaining post-cutover items

The following items are **not** part of this cutover and are left for a future phase:

1. **Codex lifecycle branch reshape**: replace `is_codex_lifecycle_state()` branches
   with action-view equivalents (`action=launch_codex_once`,
   `action=wait_for_codex_report`, `action=handle_codex_done`)
2. **Legacy request-centric path removal**: once the reshape above is stable,
   remove the request-centric fallback branches from
   `run_until_stop.py` and `bridge_orchestrator.py`
3. **`mode` demotion**: keep `mode` as a backward-compatible write field but
   remove it from all routing logic; dispatch plan becomes the only authority

Until those three changes land, the current coexistence contract remains:
dispatch plan is primary, Codex lifecycle is compatibility, legacy is safety fallback.

## Rewrite Boundary Before State-Machine Work

Treat the current runtime read path like this before any broader
state-machine rewrite:

- the normal prepare / send / fetch / recovery / next-request loop is now the
  issue-centric spine whenever the runtime is `issue_centric_ready`
- the older request-centric route remains intentionally present, but only as
  the explicit fallback path for `issue_centric_degraded_fallback`,
  `issue_centric_unavailable`, or `issue_centric_invalidated` situations
- the legacy `mode` enum still matters for compatibility and downstream
  callers, but it is no longer the preferred read-side source when the
  issue-centric bridge is coherent

Read-side consumers should prefer this order:

1. runtime snapshot
2. runtime readiness / health gate
3. generation lifecycle
4. thin state-view bridge (`last_issue_centric_state_view`,
   `last_issue_centric_wait_kind`, related runtime-mode fields)
5. legacy `mode` and older request-centric recovery hints only when the
   layers above require fallback

Rewrite work should preserve these current semantics:

- `fresh_prepared` means a request was prepared for the current generation and
  should be reused / sent, not rebuilt or marked consumed
- `fresh_pending` means the request was sent and the runtime should prefer
  reply recovery instead of re-prepare or re-dispatch
- `issue_centric_consumed` means reply recovery closed that request lifecycle
  and the next generation may now be prepared
- `issue_centric_invalidated` means fallback / reset / inconsistency made that
  generation unfit for issue-centric reuse
- `last_issue_centric_*` fields remain the fine-grained write-side execution
  record, while the runtime snapshot and thin state bridge are the current
  read-side bridge

Rewrite work may replace these implementation details later, but should not
change the semantics above without an explicit contract update:

- ad-hoc legacy `mode` branching that still exists for compatibility
- older request-centric helper ownership of prepare / fetch / next-step paths
- thin read-side bridge composition, as long as one coherent issue-centric
  snapshot-first source remains available
- operator wording details, as long as prepared / pending / consumed /
  invalidated and preferred / fallback meanings stay visible

## Overall Assumptions

Use these assumptions consistently:

- the `ready` issue is the execution-unit source of truth
- the backlog may use three layers: `Epic -> planned parent -> ready`
  implementation issue
- Codex directly implements only the `ready` implementation issue
- the current open `ready` count should usually stay at `0` or `1`
- ChatGPT is the judgment and review authority
- the bridge is the execution and routing authority
- Codex is the implementation authority for one bounded `ready` issue
- the first-party path remains `ChatGPT Projects + macOS Safari + Codex CLI`
- `same-chat` remains the default continuation mode
- `handoff / new chat` remains the exception path
- Safari timeout expectations remain `1800 + 600`
- `submitted_unconfirmed` and `pending_request_signal` remain important current
  delivery-safety signals
- unsupported paths remain best-effort and at the operator's own risk

The bridge should continue moving toward issue-aware orchestration, not toward
becoming a generic long-form meaning transport between tools.

## Role Split

Use the three actors like this:

- ChatGPT: decide the next bounded slice, review completed work, and decide
  close / continue / follow-up outcomes
- bridge: carry the contract fields, execute the runtime steps, and perform the
  eventual issue-create / issue-close side effects
- Codex: implement one `ready` issue, then leave the result back on that issue

## Bridge To ChatGPT Contract

### Bridge To ChatGPT Input

The bridge should send only this minimum structured context:

- `repo`
- `target_issue`
- `request`

Use `target_issue` like this:

- `target_issue = none`: ask ChatGPT to decide the next bounded issue-level
  action
- `target_issue = <issue number or issue URL>`: ask ChatGPT to review that
  issue and decide whether to continue, close, or split follow-up work

`request` is the human-readable instruction for the current turn.
The bridge may still add its parser contract, but it should avoid adding extra
hidden task definition beyond these fields.

### ChatGPT To Bridge Output

ChatGPT should return:

1. one JSON envelope
2. optional body blocks

The current parser / dispatcher front-end expects that envelope to be framed
as:

```text
===CHATGPT_DECISION_JSON===
{...}
===END_DECISION_JSON===
```

The JSON envelope should include at least:

- `action`
- `target_issue`
- `close_current_issue`
- `create_followup_issue`
- `summary`

`action` is limited to:

- `issue_create`
- `codex_run`
- `no_action`
- `human_review_needed`

Use the optional body blocks below:

- `CHATGPT_ISSUE_BODY`
- `CHATGPT_CODEX_BODY`
- `CHATGPT_REVIEW`
- `CHATGPT_FOLLOWUP_ISSUE_BODY`

The current parser framing for those blocks is:

```text
===CHATGPT_ISSUE_BODY===
[BASE64 payload]
===END_ISSUE_BODY===

===CHATGPT_CODEX_BODY===
[BASE64 payload]
===END_CODEX_BODY===

===CHATGPT_REVIEW===
[BASE64 payload]
===END_REVIEW===

===CHATGPT_FOLLOWUP_ISSUE_BODY===
[BASE64 payload]
===END_FOLLOWUP_ISSUE_BODY===
```

Use them under these rules:

- `CHATGPT_ISSUE_BODY` appears only when `action = issue_create`
- `CHATGPT_CODEX_BODY` appears only when `action = codex_run`
- `CHATGPT_REVIEW` appears only when ChatGPT actually performed review
- `CHATGPT_FOLLOWUP_ISSUE_BODY` appears only when
  `create_followup_issue = true`
- all four body blocks are BASE64-encoded body payloads

For the current bounded transport implementation:

- multi-line BASE64 payloads are allowed; line whitespace is stripped before
  decode
- decoded UTF-8 text is preserved as-is, including trailing newlines
- a payload that decodes to the empty string is treated as invalid
- invalid BASE64 and invalid UTF-8 are reported as different transport errors

For the first bounded `issue_create` execution slice:

- the decoded `CHATGPT_ISSUE_BODY` must use a narrow draft rule
- the first non-empty line must be a level-1 heading of the form `# Title`
- that H1 becomes the GitHub issue title
- the remaining text becomes the GitHub issue body
- an empty title or empty remaining body is rejected before mutation
- if no Project is configured, the bridge may use issue-only fallback for the
  create step
- if `github_project_url` is configured, the bridge must pass Project preflight
  before issue creation
- the current narrow Project preflight requires:
  - `github_project_url`
  - `github_project_state_field_name` (defaults to `State` when omitted)
  - `github_project_default_issue_state`
- if Project preflight fails, the bridge stops before issue creation
- if Project preflight succeeds, the bridge may:
  - create the GitHub issue
  - add that issue to the configured Project
  - set the configured Project `State` value
- if Project item creation fails after issue creation, the created issue is
  left in place and the bridge records partial success
- if Project `State` set fails after Project item creation, both the created
  issue and Project item are left in place and the bridge records partial
  success
- when `issue_create + close_current_issue = true`, close is considered only
  after Project placement succeeds

For the first bounded `codex_run` execution slice:

- the decoded `CHATGPT_CODEX_BODY` is treated as the trigger comment body
  without markdown reformatting
- `target_issue` may be resolved from `#123`, `123`, `owner/repo#123`, or a
  full GitHub issue URL
- the bridge may register that body as a trigger comment on the resolved issue
- after comment creation, the bridge may assemble `repo / target_issue /
  request / trigger_comment` metadata and materialize a narrow issue-centric
  Codex prompt from it
- that prompt may be delegated to the existing `launch_codex_once` entrypoint
  without changing the larger runtime state machine
- after launch, the bridge may reuse the existing `codex_running` wait /
  `codex_done` / report archive / next-request preparation path rather than
  introducing a separate issue-centric completion loop
- the launch slice must make it clear whether comment registration succeeded,
  whether prompt materialization succeeded, and whether the existing launch
  entrypoint was reached
- the continuation slice must make it clear whether the existing wait / report /
  archive path has taken over
- the current implementation still does not automate close / follow-up / review
  decisions after that continuation handoff

The BASE64 requirement is part of the agreed design because the bridge should
not rely on visible-text extraction or copy-button behavior to preserve
markdown fidelity in future implementation phases.

Apply these additional rules:

- when `target_issue = none`, `action = codex_run` is not allowed
- `create_followup_issue` is a helper flag, not a separate action
- `close_current_issue` means close is appropriate, not that ChatGPT performs
  the close itself
- `summary` is a short explanation of what the bridge should do next

For the current bounded `create_followup_issue` slice:

- `CHATGPT_FOLLOWUP_ISSUE_BODY` is a narrow contract extension used only when
  `create_followup_issue = true`
- if `create_followup_issue = true` and the follow-up body block is missing,
  the contract is invalid
- if the follow-up body block is present while
  `create_followup_issue = false`, the contract is invalid
- execution is currently limited to
  `action = no_action + create_followup_issue = true`,
  `action = human_review_needed + create_followup_issue = true`, and
  `action = issue_create + create_followup_issue = true`, and
  `action = codex_run + create_followup_issue = true`
- `action = codex_run + create_followup_issue = true + close_current_issue =
  true` now uses a narrow
  `trigger comment -> launch -> continuation -> follow-up issue create ->
  close` path
- broader multi-flag combinations outside those narrow paths remain blocked in
  this slice
- the decoded follow-up body uses the same narrow `# Title` draft rule as
  `issue_create`
- if `close_current_issue = true`, the bridge evaluates close only after the
  follow-up issue create path and any required Project placement succeed
- if follow-up creation is partial, blocked, or failed, the current issue is
  left open

For the current bounded `close_current_issue` slice:

- `action = issue_create` and `close_current_issue = true` runs close only
  after the new issue-create mutation succeeds
- `action = no_action` and `close_current_issue = true` is allowed as a narrow
  "close only" path
- `action = codex_run` and `close_current_issue = true` remains blocked unless
  the bridge is explicitly executing the narrow
  `codex_run + create_followup_issue + close_current_issue` path after
  continuation and follow-up creation succeed
- `action = human_review_needed` and `close_current_issue = true` is blocked in
  this slice unless the review comment step has already completed and the
  bridge is explicitly executing the narrow post-review close path
- if the bridge cannot safely resolve the close target issue from the decision
  target or the current issue-centric state, it must stop before mutation
- if the issue is already closed, the bridge records a no-op close result
  instead of sending another close mutation
- if `github_project_url` is configured, this slice may attempt a narrow
  current-issue Project `State` sync only after the close mutation succeeds
- if that `done` sync fails, the close success remains in place and the bridge
  records a partial lifecycle-sync outcome instead of rolling the close back

For the current bounded `human_review_needed` slice:

- the decoded `CHATGPT_REVIEW` is posted as-is as a normal issue comment on the
  resolved target issue
- the target issue must resolve safely from the decision target and the current
  issue-centric state; mismatches stop before mutation
- the target issue must still be open; this slice does not post review comments
  on already closed issues
- `human_review_needed` without `CHATGPT_REVIEW` stops before mutation in this
  execution slice
- `human_review_needed` is the main action; review comment posting is attempted
  before any later close / follow-up decision is considered
- `human_review_needed + close_current_issue = true` now uses a narrow
  post-review close path
- the order is fixed as `review comment -> close`
- if review comment posting is blocked or fails, close is not attempted
- if close fails after review succeeds, the review comment remains posted and
  the bridge records a review-succeeded / close-failed outcome
- when `github_project_url` is configured, the bridge may attempt a narrow
  current-issue Project `State` sync to the configured review state after the
  review comment step succeeds
- when the same path also closes the current issue successfully, the bridge may
  then attempt a second narrow sync to the configured done state
- if either sync fails, the earlier review / close success remains in place and
  only the lifecycle-sync portion is recorded as partial
- `human_review_needed + create_followup_issue = true` now uses a narrow
  `review comment -> follow-up issue create` path
- `human_review_needed + create_followup_issue = true + close_current_issue =
  true` now uses a narrow
  `review comment -> follow-up issue create -> close` path
- `issue_create + create_followup_issue = true` now uses a narrow
  `primary issue create -> follow-up issue create` path
- `issue_create + create_followup_issue = true + close_current_issue = true`
  now uses a narrow
  `primary issue create -> follow-up issue create -> close` path
- `codex_run + create_followup_issue = true` now uses a narrow
  `trigger comment -> launch -> continuation -> follow-up issue create` path
- `codex_run + create_followup_issue = true + close_current_issue = true` now
  uses a narrow
  `trigger comment -> launch -> continuation -> follow-up issue create ->
  close` path
- if the review comment step is blocked or fails, the bridge does not attempt
  follow-up issue creation
- if the primary issue create step is blocked or fails, the bridge does not
  attempt follow-up issue creation
- if trigger comment registration, launch, or continuation handoff is blocked
  or fails, the bridge does not attempt follow-up issue creation
- if the primary issue create step succeeds but follow-up creation is partial,
  blocked, or failed, the primary issue remains created and the current issue
  stays open
- if follow-up creation is partial, blocked, or failed after review succeeds,
  the review comment remains posted and the current issue stays open
- if follow-up creation is partial, blocked, or failed after codex launch /
  continuation succeed, the Codex execution remains launched and only the
  follow-up path is recorded as incomplete
- if close fails after codex launch / continuation and follow-up issue create
  succeed, those earlier successes remain in place and only the close step is
  recorded as failed
- if close fails after review and follow-up succeed, those earlier successes
  remain in place and only the close step is recorded as failed
- if close fails after primary and follow-up issue creation succeed, those
  earlier successes remain in place and only the close step is recorded as
  failed
- `no_action + create_followup_issue = true` may create one follow-up issue in
  this slice, but broader follow-up automation remains unimplemented

For the current dispatcher / orchestrator boundary:

- `fetch_next_prompt.py` now stays narrow around contract extraction,
  transport materialization, dispatcher call, and final state persistence
- `scripts/issue_centric_execution.py` owns the current execution matrix,
  step ordering, blocked-combination policy, and final status aggregation
- the dispatcher also owns the narrow current-issue Project `State` lifecycle
  sync policy for already-supported actions
- the dispatcher now also owns the normalized execution-summary writeout used
  by the next-request preparation layer
- step implementations remain in their existing narrow helpers:
  `issue_centric_issue_create.py`,
  `issue_centric_codex_run.py`,
  `issue_centric_codex_launch.py`,
  `issue_centric_human_review.py`,
  `issue_centric_close_current_issue.py`, and
  `issue_centric_followup_issue.py`
- the dispatcher records a thin `last_issue_centric_dispatch_result` summary
  so the chosen matrix path, step order, and final status can be audited
- it also records a thin normalized continuation summary and next-request hint
  so the next ChatGPT request can read one stable issue-centric view instead of
  re-deriving intent from many scattered `last_issue_centric_*` fields
- current-issue lifecycle sync remains narrow:
  - `codex_run` success may sync the current issue to `in_progress`
  - `human_review_needed` success may sync the current issue to `review`
  - `close_current_issue` success may sync the current issue to `done`
  - if the current issue cannot be matched to an existing Project item, this
    slice records partial / blocked sync instead of auto-creating one
- the normalized continuation summary currently uses a narrow principal-issue
  rule:
  - a created follow-up issue wins when the current issue was closed, and also
    for the current `no_action + create_followup_issue` path
  - an `issue_create` primary issue wins when no closer follow-up handoff was
    created
  - an open current issue wins for the narrow `codex_run` and
    `human_review_needed` continuation paths
  - anything ambiguous falls back to `issue_resolution_unclear`
- the next-request resolver currently uses a matching narrow rule:
  - `followup_issue` principal candidates win first
  - `primary_issue` principal candidates win second
  - `current_issue` principal candidates win third
  - `issue_resolution_unclear` or any summary/state inconsistency falls back
    to older state-based issue hints
  - missing or stale summary data does not block the old report-based request
    path
- the next-request route selector now sits one layer above that resolver:
  - issue-centric is preferred only when normalized summary, resolver output,
    and state agree on one `target_issue`
  - `issue_resolution_unclear`, stale / inconsistent summary data, resolver
    fallback, or fatal execution failure all force legacy fallback
  - request builders consume the selector result instead of deciding
    issue-centric preference themselves
  - operator-facing status may show only a thin
    `issue_centric` / `fallback_legacy` route note until a later full cutover
- the restart-safe recovery layer now sits one layer above the route selector:
  - on resume / restart it reloads normalized summary first, then consults the
    saved dispatch result when present, then reconciles both against the saved
    issue-centric state fields
  - if summary, dispatch, and state still agree on one principal issue and one
    `target_issue`, the bridge records `issue_centric_recovered` and keeps the
    preferred issue-centric route
  - if summary is missing, broken, contradictory, `issue_resolution_unclear`,
    or paired with an unreadable / failed dispatch result, the bridge records
    `issue_centric_recovery_fallback` and returns to legacy fallback
- the runtime readiness / health gate now sits one layer above recovery:
  - `issue_centric_ready` requires a coherent runtime snapshot, one stable
    principal issue, one stable `target_issue`, and a non-unclear
    next-request hint, plus a fresh issue-centric generation that has not
    already been consumed or invalidated
  - `issue_centric_degraded_fallback` is used when issue-centric artifacts
    still exist but route selection, target resolution, or snapshot freshness
    is not strong enough to trust as the primary request path
  - `issue_centric_unavailable` is used when snapshot / summary / recovery
    data is missing or broken enough that request preparation should skip
    issue-centric reuse entirely
  - request preparation, operator-facing status, and doctor-style summaries
    should all consult that same gate before choosing the next route
- the freshness / invalidation layer now sits alongside that gate:
  - a snapshot is only considered fresh when its generation still matches the
    latest issue-centric execution context and its generation lifecycle is
    still `fresh_available`, `fresh_prepared`, or `fresh_pending`
  - request generation alone does not consume a generation; it remains
    `fresh_prepared` until send succeeds, then becomes `fresh_pending` while
    reply recovery is still outstanding
  - a generation becomes `consumed` only after reply recovery closes that
    request lifecycle, and consumed generations are then treated as stale
    until a newer execution writes a newer issue-centric generation
  - a generation that was explicitly sent through legacy fallback is treated
    as invalidated and is not reused by recovery or request preparation

## Bridge To Codex Contract

### Bridge To Codex Input

The bridge should send Codex this minimum context:

- `repo`
- `target_issue`
- `request`
- `trigger_comment` (optional)

`request` should stay natural-language, but it should instruct Codex to:

- confirm the target issue
- inspect issue comments when needed
- inspect `AGENTS.md` if present
- inspect repo docs
- follow the repo's Git operating rules

When ChatGPT returns `CHATGPT_CODEX_BODY`, the bridge should decode that body
and register it as a comment on the target issue before Codex runs.
Codex should then treat the issue body, issue comments, `AGENTS.md` if present,
and repo docs as the durable sources it reads directly.

The current implementation can now prepare the decoded body, register it as the
trigger comment, assemble the downstream launch payload, and hand that payload
to the existing `launch_codex_once` entrypoint through a narrow adapter.
It can also hand the resulting execution back to the existing
`codex_running` / `codex_done` / report archive / next-request path.
It has **not** yet implemented broader `codex_run + close_current_issue`
automation outside the narrow follow-up path, broader Projects update, or a
full runtime cutover.

### Codex To Bridge Output

Codex should return:

- `result`
- `target_issue`
- `summary`

`result` is limited to:

- `completed`
- `consultation_needed`
- `blocked`
- `failed`

Use those results with these meanings:

- `completed`: Codex left a completion report comment on the issue
- `consultation_needed`: Codex left a consultation comment on the issue
- `blocked`: Codex left a blocked-reason comment on the issue
- `failed`: a bridge or runtime execution failure happened before a valid issue
  outcome was recorded

## End-To-End Transition Summary

The intended high-level flow is:

1. if `target_issue = none`, ChatGPT normally returns `issue_create`
2. after `issue_create`, the bridge creates the new issue and treats that new
   issue as the current execution target
3. `codex_run` means implement or re-implement an already chosen target issue
4. after Codex returns `completed`, control goes back to ChatGPT review
5. after `consultation_needed` or `blocked`, control also goes back to ChatGPT
   judgment
6. `failed` means runtime abnormality, not an issue-state decision

After review, the intended outcomes are:

- close the current issue and create the next issue
- keep the current issue open and continue on the same issue
- create a follow-up issue
- stop for explicit human judgment

In the current bounded implementation, close mutation is only wired for
`no_action` and for the post-success `issue_create` path.
Current-issue Project `State` sync is wired only for already-supported narrow
`codex_run`, `human_review_needed`, and `close_current_issue` outcomes.
Next-request issue context is also still narrow: the bridge writes a normalized
summary / hint layer and the existing request builder appends that layer to
`CURRENT_STATUS`, but it does not yet replace the full report-based request
shape or introduce a new global state machine.
The next-request builder may now also render a narrow
`issue_centric_next_request` section with `repo`, resolved `target_issue`,
resolution source, route selection, fallback reason, and
`next_request_hint`; it may also include recovery status and recovery source
when restart-safe rehydration was used, but that still sits on top of the
existing report-based request format rather than replacing it.

## State Model

The minimum state model stays:

- `planned`
- `ready`
- `in_progress`
- `review`
- `done`
- `blocked`

Use them like this:

- `planned`: candidate or parent work that may still be split or merged
- `ready`: one bounded implementation issue that Codex may directly execute
- `in_progress`: implementation is currently underway
- `review`: completion exists and ChatGPT review is pending or active
- `done`: accepted result with coherent issue history
- `blocked`: execution cannot continue safely without outside change

## GitHub Projects And Issue-Only Fallback

If a GitHub Project is specified for the workflow, manage runtime-adjacent
backlog state through that Project.
If no Project is specified, use issue-only fallback.

When Projects are used:

- `State` is the mandatory canonical state field
- `Kind` and `Track` may be useful, but they are recommended fields, not
  required fields
- the current narrow runtime sync also expects these config values when
  current-issue lifecycle sync is desired:
  - `github_project_in_progress_state`
  - `github_project_review_state`
  - `github_project_done_state`
- current-issue lifecycle sync only updates `State`
- if the current issue does not already have a Project item, this bounded slice
  records a partial / blocked sync result instead of auto-adding the item

When Projects are not used:

- `state:*` labels are the canonical fallback state system
- small issue labels such as `type:epic`, `track:runtime`, `track:ops`, and
  `track:docs` remain acceptable issue-only helpers

If both a Project `State` field and `state:*` labels exist, keep them aligned
and treat the Project `State` field as the canonical state record.

### Minimum State Mapping

| Canonical state | GitHub Project `State` field | Issue-only fallback label |
| --- | --- | --- |
| planned | `planned` | `state:planned` |
| ready | `ready` | `state:ready` |
| in_progress | `in_progress` | `state:in_progress` |
| review | `review` | `state:review` |
| done | `done` | `state:done` |
| blocked | `blocked` | `state:blocked` |

## What This Contract Does Not Claim Yet

This document records agreed design direction.
It does **not** claim that the repository already implements all of the
contract below.

In particular, this document does **not** claim that the following are already
finished:

- bridge-side follow-up mutation, full close automation for every action, and
  Projects-aware close / state sync
- dispatcher support for broader action / helper-flag combinations beyond the
  current narrow matrix
- BODY-base64 transport execution beyond the bounded `issue_create`,
  `codex_run`, `close_current_issue`, and `human_review_needed` slices
- large state-machine redesign
- a full ChatGPT-side or Codex-side switch to the new contract

Until those implementation slices land, the current bridge/runtime behavior and
current docs still describe the live system, while this document describes the
agreed contract boundary for what comes next.
