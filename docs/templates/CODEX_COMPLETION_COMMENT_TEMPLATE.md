# Codex Completion Comment Template

Use this as the minimal issue completion comment when Codex finishes a ready
issue execution unit.

After posting it, the usual next step is to move the issue to `review` unless
the phase is clearly `blocked`.

```md
## Summary

- What was implemented in this phase?
- Which acceptance criteria were completed?

## Changed Files

- `path/to/file`

## Validation

- Commands run:
- Manual checks:
- Remaining unverified areas:

## Review Focus

- acceptance criteria that should be checked again in review:
- scope-boundary checks:
- docs or behavior consistency checks:

## Backlog Curation / Next-Ready Impact

- should the current open `ready` queue return to zero after this issue?
- which existing `planned` issue should absorb any follow-up?
- which planned issues should be reconsidered during the next ready-selection pass?

## Remaining Issues

- follow-up decision: none, existing issue, or new issue needed
- follow-up issue(s) or child issue(s):
- blockers, known gaps, or decisions still needed:

## Branch / Commit / PR

- branch:
- commit:
- PR:
- suggested next issue state: `review` or `blocked`
```
