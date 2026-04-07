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
- implemented: `scripts/issue_centric_execution.py` as the current execution
  dispatcher / orchestrator for the already-supported narrow execution matrix
- not yet implemented: follow-up mutation for other actions or broader
  post-review automation
- not yet implemented: large state-machine rewrite or full contract cutover

The current dispatcher-owned execution matrix is:

- `issue_create`
- `issue_create + close_current_issue = true`
- `issue_create + create_followup_issue = true`
- `issue_create + create_followup_issue = true + close_current_issue = true`
- `codex_run`
- `human_review_needed`
- `human_review_needed + close_current_issue = true`
- `human_review_needed + create_followup_issue = true`
- `human_review_needed + create_followup_issue = true + close_current_issue = true`
- `no_action`
- `no_action + create_followup_issue = true`
- `no_action + create_followup_issue = true + close_current_issue = true`

The dispatcher still blocks these combinations on purpose:

- `codex_run + close_current_issue = true`
- `codex_run + create_followup_issue = true`
- multi-flag combinations outside the narrow paths above

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
  `action = issue_create + create_followup_issue = true`
- `codex_run + create_followup_issue = true` and broader multi-flag
  combinations outside those narrow paths remain blocked in this slice
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
- `action = codex_run` and `close_current_issue = true` is blocked in this
  slice before any `codex_run` mutation proceeds
- `action = human_review_needed` and `close_current_issue = true` is blocked in
  this slice unless the review comment step has already completed and the
  bridge is explicitly executing the narrow post-review close path
- if the bridge cannot safely resolve the close target issue from the decision
  target or the current issue-centric state, it must stop before mutation
- if the issue is already closed, the bridge records a no-op close result
  instead of sending another close mutation
- if `github_project_url` is configured, this slice stops before close mutation
  because Project state sync is not implemented yet

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
- if the review comment step is blocked or fails, the bridge does not attempt
  follow-up issue creation
- if the primary issue create step is blocked or fails, the bridge does not
  attempt follow-up issue creation
- if the primary issue create step succeeds but follow-up creation is partial,
  blocked, or failed, the primary issue remains created and the current issue
  stays open
- if follow-up creation is partial, blocked, or failed after review succeeds,
  the review comment remains posted and the current issue stays open
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
- step implementations remain in their existing narrow helpers:
  `issue_centric_issue_create.py`,
  `issue_centric_codex_run.py`,
  `issue_centric_codex_launch.py`,
  `issue_centric_human_review.py`,
  `issue_centric_close_current_issue.py`, and
  `issue_centric_followup_issue.py`
- the dispatcher records a thin `last_issue_centric_dispatch_result` summary
  so the chosen matrix path, step order, and final status can be audited

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
It has **not** yet implemented issue-centric `close_current_issue` for
`codex_run` or post-review close automation, follow-up mutation, Projects
update, or a full runtime cutover.

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
