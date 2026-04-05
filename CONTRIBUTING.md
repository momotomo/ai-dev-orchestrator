# Contributing

Thanks for taking a look at `chatgpt-codex-bridge`.

This repository is a narrow automation tool, not a generic browser automation framework. Before proposing changes, please read:

- [README.md](/Users/kasuyatomohiro/chatgpt-codex-bridge/README.md)
- [docs/OSS_PUBLISHING_CHECKLIST.md](/Users/kasuyatomohiro/chatgpt-codex-bridge/docs/OSS_PUBLISHING_CHECKLIST.md)
- [bridge/README_BRIDGE_FLOW.md](/Users/kasuyatomohiro/chatgpt-codex-bridge/bridge/README_BRIDGE_FLOW.md) when you need runtime detail

## Scope

This repo is strongly dependent on:

- ChatGPT Projects
- Safari
- macOS Automation / Apple Events
- Codex CLI

If a proposal assumes headless automation, generic browser support, or API-only operation, it is probably outside the intended scope.

## Before Proposing A Change

Please keep these assumptions in mind:

- the first ChatGPT request is user-authored and is the source of truth
- the bridge may append reply contracts, but it should not silently invent the initial request body
- same-chat continuation is the default
- handoff / new-chat behavior is only for heavier recovery-oriented cases

Changes that weaken those assumptions should be treated carefully and explained clearly.

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
- preserve the user-authored first request model
- keep README / runbook / help text aligned with actual behavior
- avoid over-promising reliability or safety

If the change depends on GitHub-side settings, ChatGPT-side setup, Safari settings, or other manual operator actions, call that out clearly in the PR description.
