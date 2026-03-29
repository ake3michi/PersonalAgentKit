# Manual Retrospective — Engineer Reference

## What this capability is

Manual retrospective is the first-class submission surface for the garden's
existing retrospective protocol. It is intentionally small:

- CLI entry point: `pak2 retrospective`
- Helper API: `system.submit.submit_retrospective_goal()`
- Goal shape: ordinary `type: "evaluate"` assigned to `gardener`
- Event surface: ordinary `GoalSubmitted`
- Automation: none

The live protocol choice remains the gardener-local note at
`plants/gardener/knowledge/retrospective-protocol-choice.md`.

## Schema

The manual retrospective payload lives at
[`schema/goal-retrospective.schema.json`](../../schema/goal-retrospective.schema.json).
It is carried on the goal's optional `retrospective` field in
[`schema/goal.schema.json`](../../schema/goal.schema.json).

### Payload fields

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `window` | string | Yes | Current selection policy. In this slice the only valid value is `since_last_retrospective_or_recent`. |
| `recent_run_limit` | integer | Yes | Fallback cap when no prior retrospective boundary exists. Must be `3-10`. |
| `action_boundary` | string | Yes | `observe_only` or `allow_one_bounded_follow_up_goal`. |

## Submission surfaces

### CLI

```bash
pak2 retrospective --root . --recent-run-limit 5
```

The CLI always submits:

- `type: "evaluate"`
- `assigned_to: "gardener"`
- an explicit `retrospective` payload
- a generated goal body that repeats the contract in plain text for the run

### Helper

```python
from system.submit import submit_retrospective_goal

result, goal_id = submit_retrospective_goal(
    submitted_by="operator",
    recent_run_limit=5,
    allow_follow_up_goal=False,
)
```

The helper is the stable programmatic surface for manual retrospective
submission. It feeds the ordinary goal-submission validator; it does not bypass
`system.validate.validate_goal()`.

## Invariants

- Retrospective remains an `evaluate` goal, not a new runtime goal type.
- Submission writes no memory or knowledge artifacts.
- The command adds no scheduler lane, cadence policy, or background worker.
- Invocation is recorded only through the ordinary `GoalSubmitted` event and
  normal goal/run records.

## Failure modes

These are the retrospective-specific submission rejections enforced by
`system.validate.validate_goal()`:

| Reason code | When it occurs |
|-------------|---------------|
| `RETROSPECTIVE_REQUIRES_EVALUATE` | A `retrospective` payload appears on a non-`evaluate` goal |
| `INVALID_RETROSPECTIVE_PAYLOAD` | `retrospective` is present but is not a JSON object |
| `UNKNOWN_RETROSPECTIVE_FIELD` | `retrospective` contains an unsupported key |
| `MISSING_RETROSPECTIVE_WINDOW` | `retrospective.window` is absent |
| `INVALID_RETROSPECTIVE_WINDOW` | `retrospective.window` is not a known policy value |
| `MISSING_RETROSPECTIVE_RECENT_RUN_LIMIT` | `retrospective.recent_run_limit` is absent |
| `INVALID_RETROSPECTIVE_RECENT_RUN_LIMIT` | `retrospective.recent_run_limit` is not an integer in `3-10` |
| `MISSING_RETROSPECTIVE_ACTION_BOUNDARY` | `retrospective.action_boundary` is absent |
| `INVALID_RETROSPECTIVE_ACTION_BOUNDARY` | `retrospective.action_boundary` is not a known policy value |

Ordinary goal-submission failures such as `UNKNOWN_ASSIGNED_PLANT`,
`ASSIGNED_PLANT_INACTIVE`, `INVALID_PRIORITY`, and `SUBMISSION_REJECTED` still
apply because this capability is built on top of the normal goal boundary.

## Tests

`tests/test_retrospective.py` covers:

- raw payload happy path
- each named retrospective-specific failure mode
- helper submission integration
- CLI submission and CLI-visible rejection handling
