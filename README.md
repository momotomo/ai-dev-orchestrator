# ai-dev-orchestrator

`ai-dev-orchestrator` is a macOS + Safari automation bridge between ChatGPT and Codex.

Its job is simple in principle:

1. ask ChatGPT for the next one-phase Codex prompt
2. run Codex once against your target repository
3. take the Codex report and send it back to ChatGPT
4. continue to the next request when it is safe to do so

This repository is optimized for a very specific workflow, not for generic browser automation.

## Quick Start

If you want to try the bridge once without learning the whole runtime model first:

1. Prepare Safari and open the target ChatGPT Project conversation or project page.
2. Copy [bridge/project_config.example.json](bridge/project_config.example.json) to `bridge/project_config.json` and set your target repository path.
3. Run:

```bash
python3 scripts/start_bridge.py --project-path /ABSOLUTE/PATH/TO/target-repo --max-execution-count 6
```

4. With the current bridge runtime, on the first request only, type the ChatGPT
   instruction **yourself**.
5. Let the bridge continue, and if it stops, use `--status` or `--doctor` before retrying.

The first request body is still not auto-generated in the current runtime.
During the phased move toward issue-centric normal operation, the ready issue is
the intended execution-unit source of truth while this first-request path
remains a runtime entry path. See
[docs/ISSUE_CENTRIC_FLOW.md](docs/ISSUE_CENTRIC_FLOW.md).

## Normal Operator Entry During The Transition

For normal operation, the operator should start here:

1. check the current open `ready` issue
2. if one exists, use it as the direct execution-unit reference
3. if none exists, review the `planned` backlog and promote the next bounded
   slice to one `ready` issue
4. only then, if the current runtime still asks for an initial request or
   override input, type a short request that points back to that `ready` issue

This means the normal entry is `ready`-issue-first even though the current
bridge runtime still uses a user-authored first request when that runtime path
is exercised.

Before you start, it is often worth spending a few manual messages in ChatGPT to align the task size, constraints, and what should count as a single Codex phase.

## What This Tool Is For

This bridge is for people who want to keep a long-running implementation loop going between:

- ChatGPT, which decides the next one-phase task for Codex
- Codex, which performs exactly one implementation phase and writes a report

The normal loop is:

1. you start the bridge
2. on the first request only in the current runtime, you write the initial
   ChatGPT instruction yourself
3. ChatGPT returns the next Codex prompt
4. Codex runs once and writes a report
5. the bridge sends that report back to ChatGPT
6. ChatGPT returns the next prompt

The bridge is intentionally conservative in many places, but it is not a safety guarantee.

## Good Fit / Poor Fit

Good fit:

- you already use ChatGPT Projects
- you want ChatGPT to plan the next one-phase Codex task repeatedly
- you are comfortable supervising an automation loop
- you can tolerate Safari/UI-driven fragility

Poor fit:

- you want a generic browser automation framework
- you want API-first or headless execution
- you want strong guarantees or unattended reliability
- you do not want any operator-authored first-request or override path in the
  current runtime
- you want the tool to decide project structure or task granularity for you

## Important Assumptions

This repository assumes all of the following:

- macOS
- Safari
- ChatGPT in Safari
- the ChatGPT **Project** feature
- Codex CLI installed locally
- Apple Events / Safari automation enabled

This is not designed around:

- Chrome
- headless browser automation
- API-only ChatGPT usage
- automatic project creation
- automatically creating a brand-new ChatGPT project for you

The current bridge expects that you already have a ChatGPT Project and are operating inside it.

## Strong Warnings

Use this tool at your own risk.

- It automates real browser actions.
- It depends heavily on ChatGPT UI structure, Safari behavior, macOS Automation permissions, and Codex CLI behavior.
- ChatGPT UI changes, Safari behavior changes, or Codex CLI changes can break it without warning.
- It can consume Codex usage aggressively if you let it keep running.
- It is safety-biased, but not foolproof.
- You are still responsible for reviewing prompts, changes, reports, Git actions, and repository state.

If you need strong guarantees, this repository is not enough on its own.

## Before You Use It

It is often better to spend a little time talking to ChatGPT manually first.

For example, before starting the bridge, you may want to:

- narrow the scope of the current task
- decide what Codex should and should not touch
- align on how small a “one phase” should be
- clarify any domain-specific constraints

That usually produces better one-phase prompts and smoother bridge runs.

## Current First Request / Override Example

If you are using the current first-request path, keep it short, concrete, and
written by you. During the issue-centric transition, it should usually point to
the ready issue instead of restating the whole task from scratch.
By the time you type this message, you should already know the current `ready`
issue or have just promoted one from the `planned` backlog.

You can usually start from something like this:

```text
Target project: melody-craft-studio
Target repo: /Users/you/projects/melody-craft-studio
Ready issue: #123 sample browser wording cleanup
Use the ready issue as the execution-unit source of truth
Please return the next one-phase Codex prompt.
```

Or even shorter:

```text
Target repo: /Users/you/projects/melody-craft-studio
Ready issue: #123
Use the ready issue and keep the next phase inside the sample browser only
Please return the next one-phase Codex prompt.
```

The bridge sends your body as written and appends only the reply contract it needs for parsing.

## Environment Requirements

At minimum, you need:

- Python 3
- Safari
- ChatGPT logged in inside Safari
- Codex CLI available as `codex` or configured in `bridge/project_config.json`

No extra Python packages are currently required.

See [requirements.txt](requirements.txt).

## Required Safari / ChatGPT Setup

Before live use:

- open the target ChatGPT conversation or project page in Safari
- keep Safari on the intended current tab while the bridge is running
- enable `Develop > Allow JavaScript from Apple Events`
- allow macOS Automation access for the app/process that runs the bridge
- ensure ChatGPT Project pages are usable in your current account and UI

The bridge assumes Safari current tab is the active operating surface.

If you only remember three checks, remember these:

- Safari current tab is the intended ChatGPT surface
- `Allow JavaScript from Apple Events` is enabled
- ChatGPT Project UI is visible and usable in the current session

## Configuration

The main project-level config is:

- [bridge/project_config.example.json](bridge/project_config.example.json)

Typical local setup is:

1. copy it to `bridge/project_config.json`
2. fill in your target repository path
3. optionally set Codex binary / model / sandbox overrides

Browser timing and Safari-specific behavior live in:

- [bridge/browser_config.json](bridge/browser_config.json)

This repository currently treats Safari fetch waiting as:

- 1800 seconds normal timeout
- then 600 seconds extended wait
- then late-completion monitoring if needed

Runtime state, prompt / report artifacts, and live logs are intended to stay
local. The repository keeps templates, docs, and `.gitkeep` placeholders, but
active files such as `bridge/state.json`, `bridge/project_config.json`,
`bridge/inbox/*`, `bridge/outbox/*`, and `logs/*` should not be committed.

## The Normal Entry Point

The normal entry point is:

```bash
python3 scripts/start_bridge.py --project-path /ABSOLUTE/PATH/TO/target-repo --max-execution-count 6
```

That is the intended day-to-day command.

For repository health, GitHub Actions also runs the current lightweight Python checks on `push` and `pull_request`:

- `python3 -m py_compile scripts/*.py tests/*.py`
- `python3 -m unittest discover -s tests -p 'test_*.py'`

### What `max_execution_count` Means

It is an upper bound, not a promise that the bridge will always take exactly that many steps.

The bridge may stop earlier when:

- ChatGPT returns `completed`
- ChatGPT returns `human_review`
- ChatGPT returns `need_info`
- a blocked / error condition needs human attention

## What Happens On The First Run

The normal day-one flow is:

1. start `scripts/start_bridge.py`
2. type the first ChatGPT request yourself
3. the bridge appends the fixed reply contract
4. ChatGPT returns the next Codex prompt
5. Codex runs once
6. the bridge sends the Codex report back to ChatGPT

After that, continuation is normally report-based.

## First Request In The Current Runtime

The current bridge runtime still treats the first ChatGPT request as special.

- The bridge does **not** auto-generate it from stale internal state.
- The text you type is the runtime input source for that first send.
- The bridge keeps your body as-is.
- The bridge only appends the fixed reply contract needed for parsing.

In other words:

- you write the actual intent
- the bridge adds only the machine-readable reply contract

This is important because it keeps the origin of the first request explicit and
reviewable.

During the issue-centric transition, this typed first request does **not**
replace the repo-level source-of-truth model. For normal operation, the
execution-unit source of truth is the ready issue described in
[docs/ISSUE_CENTRIC_FLOW.md](docs/ISSUE_CENTRIC_FLOW.md), while the current
first-request path remains a runtime entry path or override path until bridge
changes land.

If the first request is vague, the whole loop usually degrades. It is worth making that first instruction concrete.

## After the First Request

After the first request, the normal continuation is report-based.

That means:

- Codex writes a report to the bridge outbox
- the bridge sends that report back to ChatGPT
- ChatGPT returns the next one-phase prompt

So the flow becomes increasingly automatic after the first user-authored request.

## Issue-Centric Transition

This repository is moving in phases toward issue-centric normal operation.

Source of truth is being separated like this:

- ChatGPT Projects design context: upstream design source of truth
- ready issue: execution-unit source of truth
- repo docs: permanent rules source of truth
- PRs / commits / issue completion comments: implementation-result source of truth

The current bridge runtime still keeps same-chat as the default, keeps
handoff / new-chat as the exception path, and still has the user-authored
first-request path until runtime changes land. Unsupported paths still have no
behavioral guarantee. See
[docs/ISSUE_CENTRIC_FLOW.md](docs/ISSUE_CENTRIC_FLOW.md).

For GitHub-side issue states, GitHub Projects handling, and review flow, see
[docs/GITHUB_ISSUE_PROJECTS_OPERATIONS.md](docs/GITHUB_ISSUE_PROJECTS_OPERATIONS.md).

For Epic sizing, planned issue grain, and the initial seed backlog catalog, see
[docs/ISSUE_CENTRIC_SEED_ISSUES.md](docs/ISSUE_CENTRIC_SEED_ISSUES.md).

## Same-Chat by Default

Normal continuation stays in the same ChatGPT conversation by default.

That is the expected behavior for ordinary report-based continuation.

The bridge should not rotate to a new chat during normal same-chat continuation unless the chat has shown signs of becoming too heavy.

In short:

- normal case: stay in the same chat
- heavy late-completion case: rotate before the next request

## When Handoff / New Chat Rotation Happens

Handoff / new-chat rotation is not the default.

It is only intended for heavy-chat recovery cases, especially when a reply had to go through:

- 1800-second timeout
- then 600-second extended wait
- then late-completion monitoring

When that happens, the bridge may decide that the **next** ChatGPT request should be sent only after creating a fresh chat inside the same ChatGPT Project.

The key idea is:

- handoff is **not** ordinary continuation
- handoff is **preprocessing before the next ChatGPT request**

So the order is:

1. the heavy reply is fully recovered
2. that reply is used for the Codex phase
3. only before the next ChatGPT request, the bridge may rotate into a fresh project chat

That means handoff is not “normal continuation” and not “cycle cleanup.” It is a conditional preprocessing step before the next ChatGPT send.

## ChatGPT Project Requirement

This repository assumes you are using ChatGPT Projects.

That matters because the bridge depends on:

- project pages
- project-specific “new chat” composer behavior
- project-scoped handoff flow

The bridge may fail or behave unexpectedly if you try to use it as if ordinary non-project chats were the primary model.

## ChatGPT Reply Contract

The bridge expects ChatGPT to return one of these two formats:

- `===CHATGPT_PROMPT_REPLY=== ... ===END_REPLY===`
- `===CHATGPT_NO_CODEX=== ... ===END_NO_CODEX===`

`CHATGPT_NO_CODEX` must begin with one of:

- `completed`
- `human_review`
- `need_info`

The bridge handles those as:

- `completed`: no more Codex work needed right now
- `human_review`: a human decision is needed
- `need_info`: additional user input is needed

## Human Review Behavior

`human_review` does not always stop immediately.

The bridge may auto-continue once to avoid unnecessary stops.

If `human_review` keeps coming back, the bridge eventually stops and asks for human input.

## Operational Commands

The main operational commands are:

```bash
python3 scripts/start_bridge.py --project-path /ABSOLUTE/PATH/TO/target-repo --max-execution-count 6
python3 scripts/start_bridge.py --status --project-path /ABSOLUTE/PATH/TO/target-repo
python3 scripts/start_bridge.py --resume --project-path /ABSOLUTE/PATH/TO/target-repo --max-execution-count 6
python3 scripts/start_bridge.py --doctor --project-path /ABSOLUTE/PATH/TO/target-repo
python3 scripts/start_bridge.py --clear-error --project-path /ABSOLUTE/PATH/TO/target-repo --max-execution-count 6
```

Use them like this:

- `start_bridge.py`: normal entry point
- `--status`: quick “where am I and what should I run next?”
- `--resume`: explicit “continue from here” when the bridge guidance already says normal resume is fine
- `--doctor`: inspect why it stopped and whether you should resume, wait, or clear an error
- `--clear-error`: clear only bridge-side stop causes when doctor / summary indicates that is the right move

As a rule of thumb:

- use `--status` when you are unsure
- use `--resume` when the bridge already says resume is safe
- use `--doctor` when something feels wrong
- use `--clear-error` only after `--doctor` or the stop summary points you there

When a run stops, read the printed `next step:` line first. It is intended to
be the shortest human-facing answer to “what should I do now?”

## What `clear-error` Is For

`clear-error` is intentionally narrow.

It is not a full reset.

It should be used only when the bridge guidance or doctor output tells you that the stop is recoverable and you should clear the bridge-side error condition.

It is meant to preserve:

- prompt files
- report files
- handoff artifacts
- logs

It is not meant to erase runtime history blindly.

## What Often Breaks

Common failure classes include:

- Safari current tab is not what the bridge expects
- Apple Events / Automation is not allowed
- ChatGPT UI changed
- ChatGPT Project page layout changed
- Project-page composer detection changed
- ChatGPT reply took too long
- Codex CLI auth expired
- a handoff was recovered but not actually sent
- a report exists but recovery / archive order was interrupted

This repository tries to surface these conditions via:

- `--status`
- `--doctor`
- stop summaries
- runtime logs

## Troubleshooting: First Things To Check

If a run stops unexpectedly, check these first:

1. Safari current tab is still the intended ChatGPT surface
2. Safari Automation / Apple Events permission is still valid
3. ChatGPT Project page or conversation UI did not drift
4. Codex CLI auth is still valid
5. the chat may have become too heavy and entered long-wait / late-completion behavior

In many cases, those five checks are more useful than reading raw state first.

## What This Tool Depends On

This tool depends strongly on:

- Safari DOM shape
- ChatGPT Project UI
- project page composer behavior
- macOS Automation permissions
- Codex CLI behavior and configuration
- local filesystem layout in this repository

If any of those change, behavior may drift or break.

This is especially true for:

- ChatGPT project-page composer detection
- same-chat vs project-page surface detection
- Safari tab targeting
- Codex CLI authentication

## What This Tool Does Not Guarantee

It does not guarantee:

- that ChatGPT will always produce the ideal next prompt
- that Safari automation will always reach the intended tab
- that UI-driven handoff / rotation will always succeed
- that Git operations in the worker repo are always safe
- that long-running automation will stay stable over time

It also does not fully own every hosting-side or account-side operation.

For example, some changes are outside Codex’s scope and may still require manual user actions, such as:

- changing repository names on the hosting service
- changing ChatGPT Project settings
- adjusting account permissions or browser-level settings

## Practical Safety Guidance

Treat the bridge as an automation assistant, not as an authority.

You should still review:

- the first request you type
- the prompts coming back from ChatGPT
- Codex output and report contents
- Git state in the target repository
- any repeated retries or unusual stops

If something feels inconsistent, stop and inspect before letting it continue.

## More Detailed Operational Docs

Use the top-level README for adoption and first-run judgment.

If you need the deeper runtime behavior, see:

- [bridge/README_BRIDGE_FLOW.md](bridge/README_BRIDGE_FLOW.md)
- [bridge/run_one_cycle.md](bridge/run_one_cycle.md)
- [bridge/browser_notes.md](bridge/browser_notes.md)
- [docs/ISSUE_CENTRIC_FLOW.md](docs/ISSUE_CENTRIC_FLOW.md) for the phased issue-centric source-of-truth model and template entry points
- [docs/GITHUB_ISSUE_PROJECTS_OPERATIONS.md](docs/GITHUB_ISSUE_PROJECTS_OPERATIONS.md) for GitHub issue states, Projects usage, review rubric, and template flow
- [docs/ISSUE_CENTRIC_SEED_ISSUES.md](docs/ISSUE_CENTRIC_SEED_ISSUES.md) for Epic units, planned issue seeds, ready-promotion gates, and backlog bootstrap order
- [docs/OSS_PUBLISHING_CHECKLIST.md](docs/OSS_PUBLISHING_CHECKLIST.md) for publish-time manual checks and hosting-side responsibilities
- [docs/GITHUB_METADATA_PROPOSAL.md](docs/GITHUB_METADATA_PROPOSAL.md) for GitHub description / website / topics candidate wording
- [CONTRIBUTING.md](CONTRIBUTING.md) for contribution and bug-report expectations
- [SECURITY.md](SECURITY.md) for sensitive-report handling and security limitations

Those docs are more implementation-oriented.

This README is intentionally the top-level OSS-facing explanation.
