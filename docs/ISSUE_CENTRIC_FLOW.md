# Issue-Centric Flow

This document defines the source-of-truth layers for the phased move toward
issue-centric operation in `ai-dev-orchestrator`.

It is a transition document, not a claim that the bridge runtime is already
fully migrated. The current first-party path remains:

- ChatGPT Projects
- Safari on macOS
- Codex CLI

This repository is still a narrow first-party workflow, not a generic browser
automation framework.

## Why This Exists

Normal operation is being re-centered around a ready issue as the execution
unit. The goal is to let the bridge carry issue numbers and state transitions
instead of acting as a long-form meaning transport between tools.

Phase 1 in this repository is docs-first:

- redefine source of truth in repo docs
- add minimal ready-issue and completion-comment templates
- align README and contribution guidance

This phase does **not** yet change:

- bridge runtime implementation
- bridge state machine behavior
- same-chat as the default continuation mode
- handoff / new-chat as the exception path
- the Safari fetch wait assumptions of 1800 seconds normal wait and 600
  seconds extended wait

## Source-Of-Truth Layers

Use the following layers consistently:

- Upstream design source of truth: ChatGPT Projects design context
- Execution-unit source of truth: ready issue
- Permanent rules source of truth: repository docs
- Implementation-result source of truth: PRs, commits, and issue completion
  comments

A ready issue is the smallest execution unit that is explicit enough for one
Codex implementation phase without needing a second hidden task definition.

## Current Runtime Relationship

The current bridge runtime now accepts a ready issue reference as the normal
initial entry, while still keeping a user-authored free-form override path.

When the normal runtime entry is used:

- the operator passes a current `ready` issue reference
- the bridge shapes only a minimal first request around that reference
- the bridge still appends its fixed reply contract
- the runtime records the send as a ready-entry provenance source

When the free-form override path is used:

- the operator still types the initial ChatGPT request body
- the bridge still appends its fixed reply contract
- the typed body is the runtime input source for that override send

That does **not** replace the issue-centric source-of-truth model above.

During this transition, normal operation should treat the ready issue as the
execution-unit source of truth, while the current runtime entry and override
paths remain operational surfaces until further bridge changes land.

In practice, normal operation should start from the current `ready` issue
reference directly.
If there is no current `ready` issue in a genuine exception or recovery case,
the override request should still stay bounded and name the intended backlog or
issue home the result should return to.

## Current Operator Entry During The Transition

Use the operator entry in this order:

1. check whether there is a current open `ready` issue
2. if there is one, use that issue as the direct execution-unit reference
3. if there is no open `ready` issue, review the `planned` backlog and promote
   the next bounded slice to one `ready` issue
4. start the runtime with that ready issue reference, or enter it when the
   runtime asks for it
5. only if the runtime cannot use the normal entry, use a short override that
   points back to the chosen `ready` issue

This keeps the normal entry centered on the `ready` issue while the current
runtime still preserves a bounded override path.

## Minimal Exception / Recovery / Override Cases

Free-form initial input is still allowed in the current runtime, but only as an
exception path.

Use it only when at least one of these narrow cases applies:

- backlog curation is not ready yet and one exploratory Codex-sized pass is
  needed before the next issue can be written clearly
- an urgent one-point correction needs one bounded pass before the backlog can
  be updated normally
- the bridge or operator flow needs a short recovery clarification after an
  abnormal stop, `human_review`, `need_info`, or similar interruption
- a temporary override is needed to steer the runtime back toward the intended
  `ready` issue or backlog home

When this path is used:

- keep the request short and bounded to one phase
- point back to the current `ready` issue when one exists
- if no `ready` issue exists yet, name the intended backlog or issue home
- return the outcome to backlog / issue truth afterward instead of letting the
  free-form text become a parallel long-lived task definition

## Normal Flow During The Transition

1. Use ChatGPT Projects for upstream design context, tradeoffs, and planning.
2. Capture the next Codex-sized execution unit in a ready issue.
3. Use repository docs for durable operating rules and constraints.
4. Run the current first-party path on ChatGPT Projects + macOS Safari +
   Codex CLI.
5. If a first request or override is needed, point it at the ready issue rather
   than inventing a parallel source of truth.
6. Record implementation results in commits, PRs, and an issue completion
   comment.

## Bridge Direction

The bridge should move toward orchestration based on issue identifiers and
state, not long prose hand-carried across turns.

That means the long-term direction is:

- bridge carries issue number, state, and routing metadata
- ready issue carries the execution-unit meaning
- repo docs carry stable rules
- completion comments and Git history carry the outcome

For the current inventory of which bridge/runtime surfaces would need future
implementation changes, see [RUNTIME_TOUCHPOINT_INVENTORY.md](RUNTIME_TOUCHPOINT_INVENTORY.md).
For the currently agreed bridge / ChatGPT / Codex design contract, see
[ISSUE_CENTRIC_RUNTIME_CONTRACT.md](ISSUE_CENTRIC_RUNTIME_CONTRACT.md).

## Defaults And Exceptions

The intended defaults remain:

- same-chat by default
- handoff / new-chat only as an exception path
- Safari timeout assumptions unchanged

The intended issue-centric operating model also means:

- normal operation should not require a mandatory free-form initial user input
- an override path may still remain for exceptions, recovery, or exploratory
  work, but it should stay bounded and return to backlog / issue truth
- unsupported paths continue to have no behavioral guarantee and remain at the
  operator's own risk

## Templates

Use these repo templates as the minimal starting point:

- [Ready issue template](../.github/ISSUE_TEMPLATE/ready_issue.md)
- [Codex completion comment template](templates/CODEX_COMPLETION_COMMENT_TEMPLATE.md)
- [GitHub issue / Projects operations](GITHUB_ISSUE_PROJECTS_OPERATIONS.md)
- [Seed issue catalog](ISSUE_CENTRIC_SEED_ISSUES.md)
- [Runtime touchpoint inventory](RUNTIME_TOUCHPOINT_INVENTORY.md)
- [Issue-centric runtime contract](ISSUE_CENTRIC_RUNTIME_CONTRACT.md)
