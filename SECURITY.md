# Security

`ai-dev-orchestrator` is **not** a security-hardened automation platform and does not make a security guarantee.

It automates:

- browser interactions
- local CLI execution
- local prompt / report files

Use it with care.

## Before Reporting A Security Problem

Please assume that public issue threads are not safe places for secrets.

Do **not** paste the following into a public issue:

- API keys
- auth tokens
- cookies
- full local config files
- private repository paths you do not want exposed
- raw logs that contain sensitive project content
- browser storage or session details

Redact first.

## What To Report Carefully

Please report things like:

- behavior that can send data to the wrong chat or wrong target surface
- unsafe runtime recovery behavior
- accidental reuse of stale prompt / report / handoff data
- browser automation that could operate on the wrong page
- surprising filesystem or CLI behavior that could expose local data

## How To Report

If the problem can be described safely in public, open an issue with sensitive details removed.

If the problem is sensitive and you do **not** already have a private channel with the maintainer:

1. do not post secrets publicly
2. open a minimal public issue saying you found a sensitive problem
3. ask for a safer reporting path without including the sensitive payload

This repository does not promise a formal security response SLA.

## Practical Limits

Because this tool depends heavily on Safari, ChatGPT UI, local automation permissions, and Codex CLI behavior, some “security” failures may really be:

- UI drift
- target-tab confusion
- local environment misconfiguration
- stale runtime state
- operator error

Those are still worth reporting, but please describe the environment and reproduction path clearly.
