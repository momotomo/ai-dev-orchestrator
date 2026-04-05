# OSS Publishing Checklist

This checklist is for the final human pass before publishing `chatgpt-codex-bridge` as an OSS repository.

It is intentionally short and practical:

- what Codex can reasonably prepare inside this repository
- what still needs manual user judgment
- what depends on external services or local environment

Use [README.md](../README.md) for product-level explanation.
Use this file when you are deciding whether the repository is ready to publish.

## 1. What Codex Can Prepare Inside This Repo

Codex can usually help with:

- README and bridge docs clarity
- command help text and runbook consistency
- bridge runtime behavior and recovery logic
- prompt / report / handoff contract wording
- `.gitignore` cleanup
- sample config files
- safe refactors inside this repository

Codex can also inspect the local repository state and suggest publishing steps.

## 2. What Still Requires Human Action

The following are not fully owned by Codex and should be treated as manual publishing tasks:

- GitHub repository visibility choice
- GitHub repository rename
- GitHub About / Description / Website / Topics
- branch protection rules
- default branch policy
- repository-level permissions
- ChatGPT Project naming and settings
- Safari / macOS Automation permissions
- Codex CLI login / auth refresh
- final review of whether publishing this automation is appropriate

If a task happens in GitHub settings, account settings, browser settings, OS settings, or ChatGPT product settings, assume human action is required unless you explicitly perform it yourself outside Codex.

## 3. Human Decisions That Should Not Be Auto-Selected

Do not auto-decide these without a human:

- LICENSE choice
- public vs private visibility
- whether logs or examples contain sensitive project details
- whether the current repository name is appropriate for public release
- whether current screenshots / project names / sample prompts reveal private work
- whether the current safety warnings are sufficient for your risk tolerance

If a LICENSE is still unset, that is a human publishing decision, not a Codex default.

## 4. Runtime / Environment Checks Before Publishing

Before calling the repo “ready”, confirm:

- Safari is really the intended supported browser
- macOS Automation and Apple Events are still required and documented
- ChatGPT Project feature is a hard prerequisite and remains visible in README
- Codex CLI is still the intended worker runtime
- `bridge/browser_config.json` still reflects the current default timeout assumptions
- the current heavy-chat model is still accurate:
  - same-chat by default
  - handoff / new chat only as preprocessing before the next ChatGPT request when late completion made the chat too heavy

## 5. Safety / Responsibility Checks

Before publishing, confirm the repo still clearly says all of the following:

- the first ChatGPT request is user-authored and is the source of truth
- the bridge appends reply contracts but does not invent the first task body
- the tool is safety-biased but not guaranteed safe
- the tool can consume Codex usage aggressively
- Safari / ChatGPT UI changes may break it
- the tool is environment-dependent and should be used at the operator’s own risk

If any of those become unclear in README, fix README before publishing.

## 6. Publishing Metadata Checks

Manual hosting-side checklist:

- repository name is final enough for public use
- repository description is accurate
- optional Website / homepage link is correct
- Topics are appropriate
- visibility is intentional
- any branch protection or rulesets are intentional
- issue / PR defaults are acceptable for public contributions

Codex can suggest wording, but these are still hosting-side actions.

See [docs/GITHUB_METADATA_PROPOSAL.md](GITHUB_METADATA_PROPOSAL.md)
for candidate description / website / topic wording.

## 7. Local File Hygiene Before Publishing

Before publishing, verify that runtime and local-only files are not being accidentally committed.

Check especially:

- `bridge/state.json`
- `bridge/project_config.json`
- `bridge/inbox/*`
- `bridge/outbox/*`
- `logs/*`
- local backup files

The repo should ship templates and docs, not your active runtime state.

Tracked placeholders such as `.gitkeep` files are fine. Live state, live
prompt/report files, local backups, and operator-specific config should stay
local.

## 8. Practical “Ready To Publish?” Gate

You are probably ready to publish when all of these are true:

- README explains what the tool is and is not
- the normal entry point is clearly `scripts/start_bridge.py`
- the first-request ownership model is obvious
- same-chat default and handoff/new-chat preprocessing behavior are explained
- major failure modes are called out
- manual responsibilities are separated from Codex-owned changes
- runtime junk is not tracked

If any of those are still fuzzy, publish later.

## 9. After Publishing

After the repo is public, plan for some manual follow-up:

- verify the public README renders well on GitHub
- check that links and examples still read naturally in the published view
- confirm `.gitignore` is preventing local runtime artifacts from leaking back in
- decide how you want to handle external issues, PRs, or support expectations

This repository is usable without promising broad compatibility.
