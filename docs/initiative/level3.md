# Initiative Records — Agent Contract

This document is self-contained.

---

## What this artifact is for

Use an `Initiative Record` when the operator has approved one named multi-stage
effort that is larger than ordinary bounded campaign mode, but you still need
explicit tranche boundaries and one visible next step.

Artifact path:

`plants/<your-plant>/memory/initiatives/<initiative-id>.json`

## What it is not

- not a new goal type
- not a new scheduler lane
- not permission to keep going until done

Ordinary goals and runs still do the work. The initiative record only states
how that work is allowed to progress.

## How to write it

Use:

```python
from system.initiatives import write_initiative_record
```

Optional helpers:

```python
from system.initiatives import initiative_record_path, read_initiative_record
```

Validator:

`system.validate.validate_initiative_record(data)`

Schema:

[`schema/initiative.schema.json`](../../schema/initiative.schema.json)

Event contract:

- `InitiativeRefreshStarted`
- `InitiativeRefreshFinished`

## Rules you must follow

- Keep only one tranche `active` at a time.
- When the initiative `status` is `active`, `paused`, or `blocked`,
  `current_tranche_id` must name that active tranche.
- Every code-changing tranche must end with a review or `evaluate` stop before
  any later tranche may start.
- If a clean tranche-closing `evaluate` run advances to the next approved
  same-initiative code-changing tranche, use
  `system.submit.submit_same_initiative_code_change_with_evaluate(...)` so the
  implementation goal and its mandatory follow-on evaluate stop are queued
  together.
- Do not queue a later tranche just because it exists in the record.
  Advancement still needs a closing artifact that explicitly recommends the
  same successor tranche.
- Do not use that helper for discovery/research tranches, general multi-goal
  chaining, or `closure-review`.
- If a tranche allows bounded campaign mode, campaign mode stays inside that
  tranche and still obeys the existing gardener-local bounded campaign policy.
- Record execution provenance in `ledger.goal_ids`, `ledger.run_ids`, and
  `ledger.totals`; do not treat the initiative record as a replacement for
  those goals and runs.

## Minimum useful contents

- approval source
- objective and scope boundary
- non-goals and success checks
- ordered tranche list with explicit successor conditions
- `current_tranche_id`
- `next_authorized_step`
- cumulative ledger

## Failure codes

The validator/write path can reject with:

- `INVALID_INITIATIVE_SHAPE`
- `UNKNOWN_INITIATIVE_FIELD`
- `MISSING_REQUIRED_FIELD`
- `INVALID_INITIATIVE_SCHEMA_VERSION`
- `INVALID_INITIATIVE_ID`
- `INVALID_INITIATIVE_PLANT`
- `EMPTY_INITIATIVE_FIELD`
- `INVALID_INITIATIVE_STATUS`
- `INVALID_INITIATIVE_RUN`
- `INVALID_INITIATIVE_APPROVAL_SOURCE`
- `INVALID_INITIATIVE_STRING_LIST`
- `INVALID_INITIATIVE_BUDGET_POLICY`
- `INVALID_INITIATIVE_TRANCHE`
- `UNKNOWN_INITIATIVE_TRANCHE_FIELD`
- `INVALID_INITIATIVE_TRANCHE_ID`
- `DUPLICATE_INITIATIVE_TRANCHE_ID`
- `INVALID_INITIATIVE_TRANCHE_STATUS`
- `INVALID_INITIATIVE_TRANCHE_GOAL_TYPES`
- `INVALID_INITIATIVE_TRANCHE_EXECUTION_MODE`
- `INVALID_INITIATIVE_TRANCHE_REVIEW_POLICY`
- `INVALID_INITIATIVE_TRANCHE_STOP_RULES`
- `INVALID_INITIATIVE_SUCCESSOR`
- `UNKNOWN_INITIATIVE_SUCCESSOR_TRANCHE`
- `SELF_REFERENTIAL_INITIATIVE_SUCCESSOR`
- `INVALID_INITIATIVE_CURRENT_TRANCHE`
- `MULTIPLE_ACTIVE_INITIATIVE_TRANCHES`
- `INVALID_INITIATIVE_NEXT_STEP`
- `INVALID_INITIATIVE_LEDGER`
- `INVALID_INITIATIVE_LEDGER_TOTALS`
- `IO_ERROR`

When the finish event outcome is not `success`, `initiative_reason` carries the
same named reason code returned by the helper.
