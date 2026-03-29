# Initiative Records — Engineer Reference

## Purpose

`Initiative Records` give the gardener one machine-readable place to track an
operator-approved multi-stage effort that is larger than current bounded
campaign mode but still needs explicit boundaries.

Canonical path:

`plants/<plant>/memory/initiatives/<initiative-id>.json`

Schema:

[`schema/initiative.schema.json`](../../schema/initiative.schema.json)

## Top-level fields

Required fields:

- `schema_version`
- `id`
- `plant`
- `title`
- `status`
- `approved_by`
- `objective`
- `scope_boundary`
- `non_goals`
- `success_checks`
- `budget_policy`
- `tranches`
- `current_tranche_id`
- `next_authorized_step`
- `ledger`
- `updated_at`
- `updated_by_run`

## State model

Initiative `status` is one of:

- `desired`
- `approved`
- `active`
- `paused`
- `blocked`
- `completed`
- `abandoned`

Tranche `status` is one of:

- `planned`
- `active`
- `completed`
- `abandoned`

Rules enforced by `system.validate.validate_initiative_record()`:

- at most one tranche may be `active`
- `current_tranche_id` must match the single active tranche when initiative
  status is `active`, `paused`, or `blocked`
- inactive initiative states may not keep an active tranche
- a tranche successor must reference another known tranche or `null` for clean
  initiative closure

## Relationship to existing garden mechanisms

Ordinary goals and runs:

- remain the atomic audited execution unit
- carry all real implementation, review, and verification work
- are referenced from the initiative `ledger`, not replaced by the record

Bounded campaign mode:

- remains the short inner execution tool
- may be allowed by a tranche through `execution_mode:
  bounded_campaign_optional`
- may not span multiple tranches or replace the tranche review stop

Reviews and evaluates:

- every code-changing tranche must end with
  `review_policy: mandatory_review_or_evaluate_stop`
- later tranches do not begin until the closing artifact says the tranche
  closed cleanly
- when a clean tranche-closing `evaluate` run advances to the next approved
  same-initiative code-changing tranche, submit the implementation goal and
  its mandatory follow-on evaluate stop together via
  `system.submit.submit_same_initiative_code_change_with_evaluate(...)`

Artifact-driven successor handoff:

- remains the queueing rule for at most one faithful next bounded goal
- an initiative record adds one more guardrail: the artifact may only advance
  to the tranche already named by the record

## Helper surface

Use:

```python
from system.initiatives import (
    initiative_record_path,
    read_initiative_record,
    write_initiative_record,
)
```

For the specific same-initiative continuation shape above, use:

```python
from system.submit import submit_same_initiative_code_change_with_evaluate
```

Boundary:

- only for `build` / `fix` same-initiative tranche launches
- queues exactly one follow-on `evaluate` stop
- does not auto-queue any later successor and does not start `closure-review`

`write_initiative_record()` validates via:

```python
from system.validate import validate_initiative_record
```

and then:

- emits `InitiativeRefreshStarted`
- validates the candidate record
- writes the plant-local JSON file on success
- emits `InitiativeRefreshFinished` with the outcome

## Event surface

Started event fields:

- `plant`
- `initiative_id`
- `initiative_path`

Finished event fields:

- `plant`
- `initiative_id`
- `initiative_path`
- `initiative_outcome`

Failure events also carry:

- `initiative_reason`
- `detail`

Current `initiative_outcome` values:

- `success`
- `validation_rejected`
- `io_error`

## Failure modes

Named rejection codes currently include:

- `INVALID_INITIATIVE_SHAPE`
- `UNKNOWN_INITIATIVE_FIELD`
- `INVALID_INITIATIVE_SCHEMA_VERSION`
- `INVALID_INITIATIVE_ID`
- `INVALID_INITIATIVE_PLANT`
- `EMPTY_INITIATIVE_FIELD`
- `INVALID_INITIATIVE_STATUS`
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

## Verification

Focused coverage lives in:

- `tests/test_initiatives.py`
