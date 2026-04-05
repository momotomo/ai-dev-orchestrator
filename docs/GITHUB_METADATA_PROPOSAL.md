# GitHub Metadata Proposal

This file is a lightweight proposal memo for GitHub-side repository metadata.

It exists so the repository can hold candidate wording without pretending those
hosting-side settings are managed automatically from inside the repo.

Actual changes to GitHub About, Website, Topics, visibility, or repository
rename are still manual human actions.

## Description Candidates

Pick one and adjust it manually in GitHub settings if you decide to publish.

1. Bridge ChatGPT Projects and Codex CLI to iterate one implementation phase at a time through Safari-driven local automation.
2. Local Safari-based bridge between ChatGPT Projects and Codex CLI for phase-by-phase prompt generation, execution, and report continuation.
3. Opinionated macOS automation that loops ChatGPT Project prompts into Codex work and feeds Codex reports back for the next phase.

## Website / Homepage Guidance

Only set the GitHub Website field if you already have a stable public page.

Reasonable options:

- leave it unset until there is a public landing page or docs site
- point it to a future project homepage maintained outside the repo
- avoid pointing it to an internal or unstable local-only path

If you do not have an external page yet, leaving Website blank is safer than
guessing.

## Topics Candidates

Use a small, honest set rather than broad discovery spam.

Suggested candidates:

- `chatgpt`
- `codex`
- `macos`
- `safari`
- `apple-events`
- `automation`
- `developer-tools`
- `prompt-engineering`

Use with care:

- `browser-automation`
- `chatgpt-projects`
- `openai-codex`

Those can be accurate, but they also increase the risk of people assuming this
repo is a generic framework instead of a narrow, environment-dependent tool.

## Repository Rename Considerations

Do not rename the repository automatically from inside this repo.

If you are considering a public-facing rename, check:

- whether the current name is clear enough for external readers
- whether the name implies a broader guarantee than the tool actually provides
- whether existing local scripts, bookmarks, or documentation assume the
  current name
- whether any external links or issue references would become confusing

## Manual-Only GitHub Tasks

These remain human-owned even if Codex helps draft wording:

- About / Description
- Website / Homepage
- Topics
- visibility choice
- repository rename
- branch protection and rulesets
- repository permissions
- default issue / PR settings
- LICENSE choice

See [docs/OSS_PUBLISHING_CHECKLIST.md](/Users/kasuyatomohiro/chatgpt-codex-bridge/docs/OSS_PUBLISHING_CHECKLIST.md)
for the broader publishing checklist.
