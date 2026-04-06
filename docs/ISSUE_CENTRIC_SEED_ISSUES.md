# Issue-Centric Seed Issues

This document defines the minimum seed issue catalog for the phased
issue-centric transition in `ai-dev-orchestrator`.

Use [docs/ISSUE_CENTRIC_FLOW.md](ISSUE_CENTRIC_FLOW.md) for source-of-truth
layers.
Use [docs/GITHUB_ISSUE_PROJECTS_OPERATIONS.md](GITHUB_ISSUE_PROJECTS_OPERATIONS.md)
for issue states, GitHub Projects usage, and review flow.
Use this document for Epic sizing, planned issue grain, ready-promotion gates,
and the initial seed backlog shape.

This is still a planning and GitHub-operations phase.
It does **not** mean the bridge runtime is already fully issue-centric.

The first-party path remains:

- ChatGPT Projects
- Safari on macOS
- Codex CLI

The repo remains a narrow first-party workflow, not a generic browser
automation framework.

The current runtime assumptions also remain:

- same-chat by default
- handoff / new-chat only as exception paths
- Safari timeout assumptions of 1800 seconds normal wait and 600 seconds
  extended wait
- unsupported paths are not guaranteed and remain at the operator's own risk

## Why A Seed Catalog Exists

The goal is to stop making ChatGPT invent the backlog from scratch every time.

The seed set should make it easy to:

- see the major migration themes
- create or register Epic and `planned` issues consistently
- promote only the next bounded slice to `ready`
- avoid over-specifying future implementation before a slice is actually ready

This document deliberately favors a stable backlog skeleton over premature
ready-issue detail.

## How To Use This Catalog

Use the seed set like this:

1. Create or register the Epic issues first.
2. Create the child `planned` issues under those Epics.
3. Keep seed issues in `planned` until one slice becomes a direct Codex target.
4. Promote only one bounded slice at a time to `ready`.
5. Use the ready issue template only after the ready gate below is satisfied.

If the GitHub surface is still mostly empty, seed the issues first and keep the
Project setup intentionally minimal.

## Current Public Bootstrap

The current public bootstrap is intentionally smaller than the full seed
catalog.

- Labels:
  - [repo labels](https://github.com/momotomo/ai-dev-orchestrator/labels)
  - current added taxonomy: `type:epic`, `state:*`, `track:*`
- Project:
  - [ai-dev-orchestrator Issue-Centric Seed Backlog](https://github.com/users/momotomo/projects/4)
- Registered Epic issues:
  - [#1 Epic: issue-centric execution contract and operator path](https://github.com/momotomo/ai-dev-orchestrator/issues/1)
  - [#2 Epic: GitHub backlog and Project bootstrap for issue-centric work](https://github.com/momotomo/ai-dev-orchestrator/issues/2)
  - [#3 Epic: completion, review, and follow-up slicing loop](https://github.com/momotomo/ai-dev-orchestrator/issues/3)
  - [#4 Epic: runtime migration inventory and staged orchestration](https://github.com/momotomo/ai-dev-orchestrator/issues/4)
- Registered planned issues:
  - [#5 Planned: make ready issue the normal operator entry example](https://github.com/momotomo/ai-dev-orchestrator/issues/5)
  - [#6 Planned: position free-form initial input as an exception and recovery override](https://github.com/momotomo/ai-dev-orchestrator/issues/6)
  - [#7 Planned: keep GitHub Project and label bootstrap intentionally small](https://github.com/momotomo/ai-dev-orchestrator/issues/7)
  - [#8 Planned: define planned-to-ready promotion cadence and backlog curation](https://github.com/momotomo/ai-dev-orchestrator/issues/8)
  - [#9 Planned: tighten Codex completion to ChatGPT review handoff](https://github.com/momotomo/ai-dev-orchestrator/issues/9)
  - [#10 Planned: inventory minimal runtime touchpoints for ready-issue-first execution](https://github.com/momotomo/ai-dev-orchestrator/issues/10)
- Current open ready issues:
  - none
- Completed ready examples:
  - [#11 Ready: confirm labels-first GitHub bootstrap as the initial operating route](https://github.com/momotomo/ai-dev-orchestrator/issues/11)
  - [#12 Ready: define one-next-ready cadence for the labels-first backlog](https://github.com/momotomo/ai-dev-orchestrator/issues/12)
  - [#14 Ready: document ready issue selection as the normal operator entry](https://github.com/momotomo/ai-dev-orchestrator/issues/14)
  - [#15 Ready: document free-form initial input as the exception, recovery, and override path](https://github.com/momotomo/ai-dev-orchestrator/issues/15)

The first public ready example intentionally keeps
[#7 Planned: keep GitHub Project and label bootstrap intentionally small](https://github.com/momotomo/ai-dev-orchestrator/issues/7)
as the backlog-shaped `planned` parent and uses
[#11 Ready: confirm labels-first GitHub bootstrap as the initial operating route](https://github.com/momotomo/ai-dev-orchestrator/issues/11)
as the bounded direct Codex target and first completed review-loop example.
Promote only the next bounded slice to `ready`.

## First Ready Promotion Example

Use the `#7 -> #11` pair as the smallest reference pattern for future
promotion.

- `#7` stays `planned` because it still holds the broader backlog theme and
  future slices
- `#11` first became `ready` because it carried one bounded execution unit with
  explicit acceptance criteria and review focus
- `#11` now also serves as the smallest public example of completion comment,
  ChatGPT review, and `done` transition
- `#12` adds the smallest public example of finishing a backlog-cadence slice
  and returning the open `ready` queue to zero
- `#14` adds the smallest public example of finishing the normal
  operator-entry slice under planned parent `#5`
- `#15` adds the smallest public example of finishing the exception /
  recovery / override-path slice under planned parent `#6`
- the current labels-first bootstrap and plain Project view stay sufficient for
  this initial operating route
- future backlog work stays in `planned` issues instead of stretching the first
  `ready` issue

## Promotion Cadence Pattern

Use the current public backlog like this:

1. keep completed examples linked from their parent `planned` issues
2. after a `done` ready issue, review the remaining `planned` backlog
3. choose the next `ready` slice only when one issue is clearly the next direct
   Codex target
4. leave other plausible candidates in `planned`

In the current public set:

- `#11` is the completed calibration example
- `#5` remains the flexible planned parent for operator-entry wording
- `#14` is the completed ready child for the normal operator-entry slice
- `#6` remains the flexible planned parent for exception / recovery / override
  wording
- `#15` is the completed ready child for the exception / recovery /
  override-path slice
- `#8` remains the flexible planned parent for promotion-cadence work
- `#12` is the completed ready child for the backlog-cadence slice
- the open `ready` queue may return to zero after `#15` while the next bounded
  direct target is reconsidered
- `#5`, `#6`, `#9`, and `#10` remain planned because follow-up and next-slice
  decisions should return to backlog curation instead of stretching completed
  ready issues

## Epic Unit

An Epic issue should represent one stable theme that spans multiple future
phases.

Use an Epic when:

- the theme is too large for one Codex phase
- the work will likely need several `planned` or `ready` child issues
- the theme needs a durable boundary and outcome statement

Do **not** use an Epic as a direct Codex implementation target.

A good Epic should capture:

- why the theme exists
- what is inside and outside the theme
- what would count as the Epic being meaningfully done
- which child `planned` issues probably belong under it

## Planned Issue Grain

A `planned` issue should be smaller than an Epic, but still flexible enough to
allow future split or merge decisions.

Use a `planned` issue when:

- the value direction is real
- the next likely slices can already be named
- the exact ready implementation slice should still be allowed to move

A good `planned` issue should usually contain:

- one coherent planning direction
- a short summary of why it matters
- an acceptance shape rather than rigid implementation detail
- candidate ready slices or likely follow-up directions
- open questions or blockers, if any

`planned` issues are not the implementation source of truth.
The implementation source of truth stays the `ready` issue.

## Ready Promotion Gate

Promote a `planned` issue to `ready` only when all of the following are true:

- it now represents `1 issue = 1 phase = one coherent value bundle`
- it is small enough to be a direct Codex target
- scope and non-goals are explicit
- acceptance criteria are concrete enough to review against a diff
- dependencies or open design questions are resolved enough for one phase
- it does not secretly require a broad state-machine rewrite or unrelated
  runtime redesign
- it keeps the current first-party path and current runtime assumptions honest

When a slice passes that gate:

1. keep the parent Epic and the remaining `planned` backlog intact
2. create or rewrite the specific child issue as `ready`
3. use [Ready issue template](../.github/ISSUE_TEMPLATE/ready_issue.md)

## Suggested GitHub Project Flow

If you use GitHub Projects, keep the flow minimal and readable.

Suggested order:

1. register Epic issues
2. register child `planned` issues
3. create one Project only after the seed set is visible enough to manage
4. promote only the next one clear slice to `ready` by default

Suggested Project views:

- by `Status`
- grouped by `Epic`
- filtered to `ready` when choosing the next Codex target

Optional lightweight classification:

- `track:docs`
- `track:ops`
- `track:runtime`

A minimal initial bootstrap may keep state in `state:*` labels and use the
Project as a plain list or grouping surface until a custom state field becomes
worth the maintenance cost.

`gh` is optional for this bootstrap step; GitHub UI or direct API calls are
valid as well.

If you start from the current default GitHub label set only, add as little as
possible beyond the state model already proposed in
[docs/GITHUB_ISSUE_PROJECTS_OPERATIONS.md](GITHUB_ISSUE_PROJECTS_OPERATIONS.md).

## Epic Catalog

Use these as the minimal Epic units for the issue-centric migration backlog.

### Epic A

- Suggested title: `Epic: issue-centric execution contract and operator path`
- Purpose: move normal operation toward ready-issue-first execution without
  pretending the runtime is already fully migrated
- Done when:
  - normal-path docs consistently point to ready issues
  - override input is clearly an exception path
  - first-party and unsupported path explanations stay aligned

### Epic B

- Suggested title: `Epic: GitHub backlog and Project bootstrap for issue-centric work`
- Purpose: make GitHub issues and Projects readable as the operational backlog
  surface for the migration
- Done when:
  - Epic and `planned` issues are easy to register and maintain
  - Project and label usage stay intentionally small
  - planned-to-ready promotion is operationally clear

### Epic C

- Suggested title: `Epic: completion, review, and follow-up slicing loop`
- Purpose: keep Codex completion, ChatGPT review, PR review, and follow-up
  issue cutting coherent as the normal loop
- Done when:
  - completion comments hand off naturally into review
  - follow-up issues are cut intentionally instead of stretching one issue
  - review guidance and templates stay aligned

### Epic D

- Suggested title: `Epic: runtime migration inventory and staged orchestration`
- Purpose: inventory future runtime changes without implementing them yet
- Done when:
  - runtime-adjacent work is clearly shelved as future `planned` issues
  - issue-number-driven orchestration has a staged decomposition
  - late completion and handoff signals are positioned in the future model

## Planned Issue Catalog

All issues below are seed candidates for `planned`, not automatic `ready`
issues.

### Seed 01

- Suggested title: `Planned: make ready issue the normal operator entry example`
- Epic: `Epic: issue-centric execution contract and operator path`
- Track: `docs`
- Summary:
  - make the operator-facing examples and runbook defaults point to selecting a
    ready issue, instead of authoring fresh free-form task bodies each cycle
- Acceptance shape:
  - docs clearly show ready issue selection as the normal path
  - the current first-request path remains documented as transitional
  - same-chat, handoff, and Safari timeout assumptions do not drift
- Likely future ready slices:
  - one docs-only slice for README and runbook examples
  - one examples-only slice for issue-driven prompt examples

### Seed 02

- Suggested title: `Planned: position free-form initial input as an exception and recovery override`
- Epic: `Epic: issue-centric execution contract and operator path`
- Track: `docs`
- Summary:
  - define when manual free-form input is still allowed and how it must point
    back to the ready issue instead of becoming a parallel source of truth
- Acceptance shape:
  - exception cases are explicit
  - override use does not blur the normal ready-issue path
  - unsupported paths remain operator-risk paths, not maintained guarantees
- Likely future ready slices:
  - one docs slice for override-path rules, including when free-form input is
    still allowed and how it must return to backlog / issue truth afterward
  - one runtime-inventory slice that maps override usage to future changes

### Seed 03

- Suggested title: `Planned: keep first-party and unsupported path explanations aligned during migration`
- Epic: `Epic: issue-centric execution contract and operator path`
- Track: `docs`
- Summary:
  - keep the public explanation honest about the first-party path while the
    issue-centric transition proceeds
- Acceptance shape:
  - README, release, and operational docs do not over-promise generic behavior
  - first-party path stays explicit as ChatGPT Projects + macOS Safari + Codex
    CLI
  - unsupported paths remain clearly non-guaranteed
- Likely future ready slices:
  - one docs-only alignment slice

### Seed 04

- Suggested title: `Planned: bootstrap Epic and planned issue registration order`
- Epic: `Epic: GitHub backlog and Project bootstrap for issue-centric work`
- Track: `ops`
- Summary:
  - define the minimum order for registering Epics and child `planned` issues
    so the backlog becomes visible without forcing every theme to be ready
- Acceptance shape:
  - Epic-first registration order is documented
  - parent / child linking pattern is clear
  - an empty or near-empty GitHub issue surface can be bootstrapped safely
- Likely future ready slices:
  - one docs + registration slice for initial Epic creation

### Seed 05

- Suggested title: `Planned: keep GitHub Project and label bootstrap intentionally small`
- Epic: `Epic: GitHub backlog and Project bootstrap for issue-centric work`
- Track: `ops`
- Summary:
  - decide the smallest useful Project and label additions beyond the default
    GitHub surface
- Acceptance shape:
  - Project status use and optional label use stay minimal
  - state mirroring rules are explicit
  - labels do not become a second conflicting workflow
- Likely future ready slices:
  - one manual-ops slice for state labels and Project setup
  - one docs slice for bootstrap instructions

### Seed 06

- Suggested title: `Planned: define planned-to-ready promotion cadence and backlog curation`
- Epic: `Epic: GitHub backlog and Project bootstrap for issue-centric work`
- Track: `ops`
- Summary:
  - define how ChatGPT reviews the seed backlog, merges duplicates, and promotes
    only the next bounded slice to `ready`
- Acceptance shape:
  - split / merge rules stay explicit
  - only a small number of issues are `ready` at once
  - seed issues remain flexible enough to evolve
- Likely future ready slices:
  - one docs-only cadence slice
  - one follow-up slice for duplicate merge or archival rules if still needed

### Seed 07

- Suggested title: `Planned: tighten Codex completion to ChatGPT review handoff`
- Epic: `Epic: completion, review, and follow-up slicing loop`
- Track: `docs`
- Summary:
  - keep the completion comment contract focused on what ChatGPT needs in order
    to review a ready issue without reopening the entire task definition
- Acceptance shape:
  - completion comments reflect ready issue acceptance criteria
  - review inputs are explicit and stable
  - completion-to-review transition stays small and repeatable
- Likely future ready slices:
  - one template/docs slice for completion and review wording

### Seed 08

- Suggested title: `Planned: cut follow-up and child issues without stretching the current issue`
- Epic: `Epic: completion, review, and follow-up slicing loop`
- Track: `ops`
- Summary:
  - define when unresolved work becomes a follow-up or child issue instead of
    silently broadening the current issue during review
- Acceptance shape:
  - follow-up issue cutting rules are easy to apply
  - unresolved work is separated from accepted work
  - `1 issue = 1 phase = one coherent value bundle` remains intact
- Likely future ready slices:
  - one docs-only slice for follow-up cutting rules

### Seed 09

- Suggested title: `Planned: keep review rubric, templates, and issue states aligned`
- Epic: `Epic: completion, review, and follow-up slicing loop`
- Track: `docs`
- Summary:
  - continue aligning the review rubric, PR template, completion template, and
    issue state definitions as the operating model gets more concrete
- Acceptance shape:
  - review inputs do not drift apart
  - state transitions remain easy to apply in GitHub
  - public docs still read coherently as OSS guidance
- Likely future ready slices:
  - one docs-only template alignment slice

### Seed 10

- Suggested title: `Planned: inventory minimal runtime touchpoints for ready-issue-first execution`
- Epic: `Epic: runtime migration inventory and staged orchestration`
- Track: `runtime`
- Summary:
  - inventory the minimum future runtime changes needed to make ready issues
    first-class inputs without implementing those changes yet
- Acceptance shape:
  - the inventory distinguishes docs/ops work from runtime work
  - future runtime changes are listed as candidate slices, not bundled together
  - current bridge behavior remains unchanged
- Likely future ready slices:
  - one inventory-only memo slice

### Seed 11

- Suggested title: `Planned: stage issue-number-driven orchestration migration`
- Epic: `Epic: runtime migration inventory and staged orchestration`
- Track: `runtime`
- Summary:
  - break the move toward issue-number-based orchestration into explicit stages
    rather than one broad rewrite
- Acceptance shape:
  - stages are ordered and bounded
  - each stage can later become its own ready issue
  - large state-machine rewrites are not hidden inside one planning item
- Likely future ready slices:
  - one staged-plan memo slice

### Seed 12

- Suggested title: `Planned: map late completion and handoff signals into the future issue-centric model`
- Epic: `Epic: runtime migration inventory and staged orchestration`
- Track: `runtime`
- Summary:
  - define how late completion, handoff, `submitted_unconfirmed`, and
    `pending_request_signal` should be interpreted when the runtime later moves
    closer to issue-number-driven orchestration
- Acceptance shape:
  - current semantics are preserved and described honestly
  - future model mapping is explicit enough to split into later ready issues
  - same-chat default and exception-path semantics do not regress
- Likely future ready slices:
  - one signal-mapping memo slice

## Suggested Initial Registration Order

If you are registering the seed backlog from a mostly blank GitHub surface,
start in this order:

1. create the four Epic issues
2. create Seeds 01-03 under Epic A
3. create Seeds 04-06 under Epic B
4. create Seeds 07-09 under Epic C
5. create Seeds 10-12 under Epic D
6. keep everything in `planned` first
7. promote only one clear slice to `ready` after the ready gate is satisfied,
   unless a rare second fallback slice is truly needed

## Likely First Ready Candidates

The following are reasonable early promotion candidates once they are bounded
enough:

- Seed 01, currently promoted publicly as `#14`
- Seed 05, completed publicly as `#11`
- Seed 06, completed publicly as `#12`
- Seed 02, if the next step is clarifying override-path docs only
- Seed 10, if the next step is a runtime-inventory memo without code changes

Promote them only when the child issue is rewritten as a true `ready` issue.

## Related Templates

Use these together:

- [Epic seed template](templates/EPIC_SEED_TEMPLATE.md)
- [Planned issue seed template](templates/PLANNED_ISSUE_SEED_TEMPLATE.md)
- [Ready issue template](../.github/ISSUE_TEMPLATE/ready_issue.md)
- [Codex completion comment template](templates/CODEX_COMPLETION_COMMENT_TEMPLATE.md)
