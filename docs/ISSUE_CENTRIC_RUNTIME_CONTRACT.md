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
- implemented: narrow `codex_run` execution from decoded `CHATGPT_CODEX_BODY`
  through target-issue trigger comment creation
- implemented: narrow issue-centric Codex launch wiring that turns the
  assembled `repo / target_issue / request / trigger_comment` payload into a
  runtime prompt and delegates to the existing `launch_codex_once` entrypoint
- implemented: narrow continuation handoff from issue-centric `codex_run`
  launch into the existing `codex_running` / `codex_done` / report archive /
  next-request preparation flow
- not yet implemented: bridge-side issue close execution
- not yet implemented: close / follow-up mutation or review automation
- not yet implemented: large state-machine rewrite or full contract cutover

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
```

Use them under these rules:

- `CHATGPT_ISSUE_BODY` appears only when `action = issue_create`
- `CHATGPT_CODEX_BODY` appears only when `action = codex_run`
- `CHATGPT_REVIEW` appears only when ChatGPT actually performed review
- all three body blocks are BASE64-encoded body payloads

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
- if `github_project_url` is configured, this slice stops before mutation
  because Project placement is not implemented yet
- if no Project is configured, the bridge may use issue-only fallback for the
  create step

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
It has **not** yet implemented issue-centric close / follow-up mutation,
review automation, Projects update, or a full runtime cutover.

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

- parser / dispatcher execution wiring beyond extraction, validation, and
  internal decision normalization
- bridge-side issue create / close execution
- BODY-base64 transport parsing for the new ChatGPT body blocks
- large state-machine redesign
- a full ChatGPT-side or Codex-side switch to the new contract

Until those implementation slices land, the current bridge/runtime behavior and
current docs still describe the live system, while this document describes the
agreed contract boundary for what comes next.
