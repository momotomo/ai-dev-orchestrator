# GitHub Issue / Projects Operations

This document explains the GitHub-side operating model for the phased
issue-centric transition in `ai-dev-orchestrator`.

Use [docs/ISSUE_CENTRIC_FLOW.md](ISSUE_CENTRIC_FLOW.md) for the source-of-truth
layers and transition framing.
Use this document for the practical GitHub issue, GitHub Projects, review, and
PR flow.

This is still a docs-first / GitHub-operations phase.
It does **not** mean the bridge runtime is already fully issue-centric.

The first-party path remains:

- ChatGPT Projects
- Safari on macOS
- Codex CLI

This repository remains a narrow first-party workflow, not a generic browser
automation framework.

## What This Phase Covers

Phase 2 focuses on the GitHub operating surface:

- issue states
- GitHub Projects usage
- ready-issue criteria
- review expectations
- PR and completion-comment handoff points

This phase does **not** yet change:

- bridge runtime implementation
- bridge state machine behavior
- issue-number-based runtime orchestration
- the current same-chat default
- handoff / new-chat as the exception path
- the Safari timeout assumptions of 1800 seconds normal wait and 600 seconds
  extended wait

Unsupported paths still have no behavioral guarantee and remain at the
operator's own risk.

## Core Policy

Use these operating rules consistently:

- `1 issue = 1 phase = one coherent value bundle`
- only `ready` issues are direct Codex implementation targets
- `planned` issues may still be split, merged, or rewritten
- the GitHub issue is the execution-unit source of truth
- repo docs remain the permanent-rules source of truth
- PRs, commits, and issue completion comments remain the implementation-result
  source of truth

The goal is to keep one ready issue small enough for one Codex phase, while
still delivering a coherent user-visible, operator-visible, or maintenance
visible unit of value.

## Responsibility Split

Use the GitHub operating surfaces like this:

- ChatGPT Projects: upstream design, decomposition, tradeoffs, and post-Codex
  review
- GitHub issue: execution-unit definition, scope, acceptance criteria, and
  current state
- GitHub Projects: queue visibility and state mirroring for operational
  tracking
- Codex: implement one `ready` issue, then post the completion comment and link
  the diff
- Pull request: reviewable implementation artifact and merge vehicle
- Repo docs: durable rules, workflow policy, and public explanation

GitHub Projects is an operating surface, not a second long-form task document.
If a Project view and an issue body disagree, fix the issue body and state
first, then bring the Project item back into sync.

## State Model

Use at least these six states:

- `planned`: candidate work that still allows decomposition, merging, or
  reframing
- `ready`: bounded work with explicit scope and acceptance criteria; direct
  Codex target
- `in_progress`: currently being implemented against the ready issue
- `review`: Codex implementation is finished enough for diff review and issue
  review
- `done`: accepted result, with implementation records and completion context in
  place
- `blocked`: progress is paused because a dependency, decision, failure, or
  external condition must be resolved first

### `planned`

Use `planned` when the work is real but not yet direct Codex input.

- splitting or merging is allowed
- acceptance criteria may still move
- do not send a `planned` issue directly to Codex

### `ready`

Use `ready` only when the issue already represents one phase and one coherent
value bundle.

At minimum, a `ready` issue should include:

- enough background to understand why the slice exists
- a concrete goal
- explicit in-scope and out-of-scope boundaries
- acceptance criteria
- related docs or related issues
- Codex notes or implementation constraints when needed

Only `ready` issues should be used as the direct implementation unit for Codex.

### `in_progress`

Use `in_progress` when implementation has started on the ready issue.

- the issue should stay bounded to the same ready scope
- if the scope must expand materially, stop and split out follow-up work instead
  of silently growing the issue

### `review`

Use `review` after Codex finishes the phase and posts the completion comment.

During `review`:

- inspect the diff or PR
- inspect the issue completion comment
- compare both against the ready issue acceptance criteria
- decide whether the issue is truly done, still blocked, or needs follow-up
  work

Codex completion does **not** automatically mean `done`.

### `done`

Use `done` when the accepted result is in a reviewable or merged state and the
issue record is coherent.

At minimum, `done` should leave behind:

- the ready issue body
- the completion comment
- the relevant PR or commit references

### `blocked`

Use `blocked` when work cannot move safely.

Typical reasons include:

- upstream design decision still missing
- external dependency or environment problem
- unexpected runtime limitation
- issue scope turned out to be wrong and must be re-cut

When a `blocked` issue becomes actionable again, move it back to `planned`,
`ready`, or `in_progress` based on what changed.

## Recommended Transitions

The normal path is:

1. `planned`
2. `ready`
3. `in_progress`
4. `review`
5. `done`

Common exceptions:

- `planned -> planned`: split or merge before execution
- `ready -> planned`: the issue was not actually bounded yet
- `in_progress -> blocked`: implementation cannot continue safely
- `review -> blocked`: review found a blocker or a missing decision
- `review -> planned`: follow-up work needs fresh issue slicing

## GitHub Projects Usage

If you use GitHub Projects, keep the setup minimal.

Recommended approach:

- use a single-select `Status` field with the exact values `planned`, `ready`,
  `in_progress`, `review`, `done`, and `blocked`
- keep the Project item synchronized with the GitHub issue state
- use Project views for queue management, not as a replacement for issue scope
  or acceptance criteria

If you are not using a Project field yet, labels can temporarily mirror the
state model.

## Minimal Label Taxonomy

Keep labels small and honest.

If needed, the minimal proposal is:

- `state:planned`
- `state:ready`
- `state:in_progress`
- `state:review`
- `state:done`
- `state:blocked`

Optional work-type labels can stay narrow:

- `type:docs`
- `type:runtime`
- `type:ops`

If you use both Project status and `state:*` labels, keep them synchronized.
Do not let labels become a second conflicting state system.

## Ready-Issue To Review Flow

Use this as the default GitHub operating loop:

1. ChatGPT uses upstream design context to shape or refine the issue.
2. The issue stays `planned` until one phase and one coherent value bundle are
   explicit.
3. The issue moves to `ready` when it becomes a direct Codex target.
4. Codex implements that `ready` issue and posts the completion comment with
   diff references.
5. The issue moves to `review`.
6. ChatGPT reviews the completion comment and the diff.
7. If needed, ChatGPT opens a follow-up issue or child issue rather than
   silently stretching the original scope.
8. The issue moves to `done` only when review accepts the result.

## Minimal Review Rubric

When an issue is in `review`, check at least the following:

- do the changes satisfy the issue's acceptance criteria?
- did the implementation stay inside the ready issue scope without unnecessary
  spillover?
- does the result stay consistent with existing bridge flow, existing docs, and
  current transition assumptions?
- are unresolved items clearly separated into follow-up issues when they should
  not stay inside the current issue?
- does the explanation still read coherently as a public OSS repository, not as
  an over-broad framework claim?

The practical review inputs are:

- the ready issue body
- the Codex completion comment
- the PR or commit diff
- the relevant repo docs

## Planned Issue Seed Notes

Large themes should usually start as `planned`.

When a future theme is still too large for one phase:

- capture the theme as a `planned` issue
- list candidate splits or child issues
- move only one bounded slice to `ready`

Use [docs/templates/PLANNED_ISSUE_SEED_TEMPLATE.md](templates/PLANNED_ISSUE_SEED_TEMPLATE.md)
if you want a lightweight starting point for future seed issues.

Use [docs/ISSUE_CENTRIC_SEED_ISSUES.md](ISSUE_CENTRIC_SEED_ISSUES.md) for the
current Epic and planned issue catalog.

## Related Templates

Use these together:

- [docs/ISSUE_CENTRIC_FLOW.md](ISSUE_CENTRIC_FLOW.md)
- [docs/ISSUE_CENTRIC_SEED_ISSUES.md](ISSUE_CENTRIC_SEED_ISSUES.md)
- [Epic seed template](templates/EPIC_SEED_TEMPLATE.md)
- [Ready issue template](../.github/ISSUE_TEMPLATE/ready_issue.md)
- [Codex completion comment template](templates/CODEX_COMPLETION_COMMENT_TEMPLATE.md)
- [Planned issue seed template](templates/PLANNED_ISSUE_SEED_TEMPLATE.md)
- [PR template](../.github/PULL_REQUEST_TEMPLATE.md)
