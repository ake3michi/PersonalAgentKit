# Initiative Record Protocol

Status: template protocol for plant-local initiative governance.

## Why this exists

- Some operator-approved work is larger than one bounded campaign, but still
  should not require a fresh manual "go" after every slice.
- Pre-queueing a long depends-on goal chain makes later goal bodies brittle and
  hides where the real stop points are.
- The garden needs one operator-legible artifact that says what was approved,
  which tranche is active now, and what evidence is required before any later
  tranche starts.

## Canonical Artifact

Path:

`plants/<plant>/memory/initiatives/<initiative-id>.json`

Schema:

`schema/initiative.schema.json`

Write helper:

`system.initiatives.write_initiative_record()`

The artifact is plant-local. It does not replace goals, runs, events, or the
current-work index in `active-threads.json`.

## When to use it

Use an initiative record only when all of the following are true:

- the operator has already approved one named multi-stage effort
- the effort is larger than one bounded campaign, or would otherwise require
  repeated manual "go" prompts despite having one stable direction
- the work can still be decomposed into explicit ordered tranches with review
  stops and named successor conditions

Do not use it for:

- ordinary one-goal or one-review threads
- garden-state surveys; use `tend`
- open-ended research or cleanup
- anything that would need a new scheduler lane or runtime primitive just to
  function

## Relationship to ordinary goals

- Goals and runs remain the atomic audited work unit.
- The initiative record never dispatches itself.
- Each tranche advances through ordinary bounded goals, with normal run-local
  verification and provenance.
- The record's `ledger` points at those goals and runs; it does not replace
  them.

## Relationship to bounded campaigns

- Initiative records are the outer governance container.
- A tranche may still allow one short bounded campaign inside its own scope
  when that campaign is already defined elsewhere.
- Such a campaign keeps its own hard limits and mandatory close.
- A campaign may not span multiple initiative tranches.

## Relationship to reviews and evaluates

- Every code-changing tranche must end with a mandatory review or `evaluate`
  stop.
- When a clean tranche-closing `evaluate` run advances to the next approved
  same-initiative code-changing tranche, queue the implementation goal and its
  mandatory follow-on evaluate stop together via
  `system.submit.submit_same_initiative_code_change_with_evaluate(...)`.
- Later tranches are not allowed to begin until that closing artifact says the
  tranche closed cleanly.
- The final closure tranche is itself a bounded review/evaluate stop, not a
  reopened implementation run.

## Relationship to artifact-driven successor handoff

- The initiative record does not replace artifact-driven successor handoff.
- Instead, it narrows it: a closing review/evaluate artifact may queue at most
  one faithful next goal or tranche, and only if the record already names that
  successor.
- For code-changing same-initiative successors, that one faithful next step is
  the paired implementation goal plus its mandatory evaluate stop, not a raw
  single-goal submission.
- Handoff may not jump to an unlisted tranche, broaden into adjacent work, or
  skip the tranche review stop.

## Update rules

- Keep the initiative id stable for the life of the initiative.
- Keep exactly one tranche `active` at a time.
- Keep `current_tranche_id` aligned with that active tranche whenever the
  initiative itself is `active`, `paused`, or `blocked`.
- Keep `next_authorized_step` explicit and narrow: one current tranche, one
  next bounded goal shape, one stop-after rule.
- Use `non_goals`, `stop_rules`, and successor summaries to record what not to
  broaden into.
- Update `ledger` only with real goal and run provenance; do not infer or
  backfill it loosely.

## Scope Boundary

- This is plant-local policy plus artifact structure.
- It does not change coordinator scheduling, driver behavior, or goal types.
- It does not authorize automatic cross-tranche continuation without a matching
  closing artifact.
