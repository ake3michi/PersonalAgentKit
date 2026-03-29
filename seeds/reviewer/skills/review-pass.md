# Review Pass

Use this skill for bounded independent review work.

## Purpose

Inspect a claimed capability or completed slice and decide whether it is
actually acceptable against the stated contract.

## Procedure

1. Restate the exact review scope from the goal.
2. Read the acceptance bar first:
   - `DONE.md`
   - the goal body
   - any named review artifact or prior run artifact
3. Inspect the real implementation and proof surface:
   - source files
   - docs
   - tests
   - live runtime artifacts, if the goal claims live use
4. Report findings before summaries.
5. Keep each finding concrete: what is wrong, where it is, and why it matters.
6. If the goal says to stop at the first blocker, do that.
7. If no blocker exists, say that explicitly and record any residual risk.

## Output Shape

- Write a run-local review artifact before finishing.
- Put findings first, ordered by severity.
- Use file references when you can.
- Keep any overall summary brief.

## Boundaries

- Do not quietly fix review findings during a review run unless the task
  explicitly authorizes review-and-repair.
- Do not call something complete because the code looks close; verify the
  claimed proof surface.
