# AGENTS.md

This repository is prepared for `ai-dev-orchestrator`.

## Repository Scope

- Repository name: {{TARGET_REPO_NAME}}
- Keep each Codex or Copilot task bounded to the requested issue or prompt.
- Prefer targeted edits over broad cleanup or speculative refactors.

## Working Rules

- Read the touched files before editing and keep existing conventions unless the
  task explicitly asks for a change.
- Call out risky assumptions early instead of hiding them in code.
- When verification is partial or skipped, say that plainly in the completion
  report.
- Keep `.github/copilot-instructions.md` aligned with this file when repo-side
  agent rules change.

## Verification

- Run the smallest relevant automated check for the touched area before
  reporting done.
- If no automated check exists, say that no repo-local check was available.
- Do not claim completion without naming the check you ran or the reason it was
  skipped.
