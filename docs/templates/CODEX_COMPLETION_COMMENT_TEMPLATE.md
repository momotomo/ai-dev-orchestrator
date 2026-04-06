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

## Remaining Issues

- follow-up issue(s) or child issue(s):
- blockers, known gaps, or decisions still needed:

## Branch / Commit / PR

- branch:
- commit:
- PR:
- suggested next issue state: `review` or `blocked`
```
