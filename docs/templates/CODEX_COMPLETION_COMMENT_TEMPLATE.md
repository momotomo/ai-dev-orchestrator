# Codex Completion Comment Template

Use this as the minimal issue completion comment when Codex finishes a ready
issue execution unit.
Treat it as the minimum handoff packet for ChatGPT review, not as a second task
definition.

After posting it, the usual next step is to move the issue to `review` unless
the phase is clearly `blocked`.

```md
## Summary

- What was implemented in this phase?
- Which acceptance criteria were completed?
- Which acceptance criteria are still intentionally left for review or follow-up?

## Changed Files

- `path/to/file`

## Validation

- Commands run:
- Manual checks:
- Remaining unverified areas:

## Review Focus

- acceptance criteria that must be checked explicitly in review:
- acceptance criteria that should be checked again in review:
- scope-boundary checks:
- docs or behavior consistency checks:
- follow-up split or backlog-return checks:

## Operator Entry / Override Impact

- does this change the normal operator entry or only clarify it?
- does free-form initial input remain an exception / recovery / override path?
- which related docs or issue examples should stay aligned?

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

The review side should be able to map this comment back to at least:

- issue acceptance criteria coverage
- scope-boundary confirmation
- docs or behavior consistency checks
- follow-up split or backlog-return decisions
