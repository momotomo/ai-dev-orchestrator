# Contributing

Thanks for taking a look at `ai-dev-orchestrator`.

This repository is a narrow automation tool, not a generic browser automation framework. Before proposing changes, please read:

- [README.md](README.md)
- [docs/ISSUE_CENTRIC_FLOW.md](docs/ISSUE_CENTRIC_FLOW.md)
- [docs/GITHUB_ISSUE_PROJECTS_OPERATIONS.md](docs/GITHUB_ISSUE_PROJECTS_OPERATIONS.md)
- [docs/ISSUE_CENTRIC_SEED_ISSUES.md](docs/ISSUE_CENTRIC_SEED_ISSUES.md)
- [docs/OSS_PUBLISHING_CHECKLIST.md](docs/OSS_PUBLISHING_CHECKLIST.md)
- [bridge/README_BRIDGE_FLOW.md](bridge/README_BRIDGE_FLOW.md) when you need runtime detail

## Scope

This repo is strongly dependent on:

- ChatGPT Projects
- Safari
- macOS Automation / Apple Events
- Codex CLI

If a proposal assumes headless automation, generic browser support, or API-only operation, it is probably outside the intended scope.

## Before Proposing A Change

Please keep these assumptions in mind:

- repo docs are the permanent rules source of truth
- during the current transition, the ready issue is the normal execution-unit source of truth
- only `ready` issues should be direct Codex implementation targets
- Epic and `planned` issues should stay flexible enough to allow future split and merge decisions
- the current bridge runtime may still use a user-authored first ChatGPT request when that entry path or override path is used
- the bridge may append reply contracts, but it should not silently invent the initial request body
- same-chat continuation is the default
- handoff / new-chat behavior is only for heavier recovery-oriented cases

Changes that weaken those assumptions should be treated carefully and explained clearly.

## Local Checks

The current GitHub Actions workflow runs the same lightweight checks that are expected locally:

- `python3 -m py_compile scripts/*.py tests/*.py`
- `python3 -m unittest discover -s tests -p 'test_*.py'`

If you touch CLI guidance, handoff behavior, or request/fetch recovery wording, please run both before opening a PR.

## Bug Reports

If you are filing a bug, it is especially helpful to include:

- macOS version
- Safari version
- whether ChatGPT Project mode was in use
- whether the problem happened in same-chat continuation, handoff/new-chat, or heavy-chat recovery
- which command you ran
  - for example: `start_bridge.py`, `--status`, `--doctor`, `--resume`, `--clear-error`
- the relevant `--status` and `--doctor` output
- a short reproduction sequence
- whether Codex CLI auth looked healthy

For stop / recovery bugs, `--status` and `--doctor` usually help more than raw state alone.

## Sensitive Data

Please do **not** paste secrets or sensitive local details directly into a public issue.

Be careful with:

- private filesystem paths
- project names that should stay private
- prompt / report contents from private work
- config files
- tokens, cookies, auth headers, or browser storage

If needed, redact aggressively and describe the behavior instead of pasting raw artifacts.

## Pull Requests

Small, focused pull requests are easier to review than broad refactors.

In general, good pull requests here:

- keep runtime behavior changes narrow
- preserve the documented issue-centric source-of-truth model and clearly call out any change to the current first-request / override path
- keep GitHub issue / Projects guidance aligned with ready-issue execution and review flow
- keep seed issue docs and templates aligned with the planned-to-ready promotion model
- keep README / runbook / help text aligned with actual behavior
- avoid over-promising reliability or safety

If the change depends on GitHub-side settings, ChatGPT-side setup, Safari settings, or other manual operator actions, call that out clearly in the PR description.
