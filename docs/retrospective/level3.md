# Manual Retrospective — Agent Contract

This document is self-contained.

---

## What this is

Manual retrospective is the bounded submission surface for the garden's
retrospective protocol. It is not a new runtime goal type. You submit an
ordinary `evaluate` goal with a `retrospective` payload.

Prefer the helper:

```python
from system.submit import submit_retrospective_goal

result, goal_id = submit_retrospective_goal(
    submitted_by="gardener",
    recent_run_limit=5,
    allow_follow_up_goal=False,
)
```

That helper always routes the goal to `gardener` and generates a body that
repeats the contract in plain text for the run.

---

## Raw payload contract

If you must submit the goal yourself, use `type: "evaluate"` and include this
payload:

```json
{
  "type": "evaluate",
  "submitted_by": "gardener",
  "assigned_to": "gardener",
  "body": "Perform a bounded retrospective using the existing gardener-local evaluate protocol.",
  "retrospective": {
    "window": "since_last_retrospective_or_recent",
    "recent_run_limit": 5,
    "action_boundary": "observe_only"
  }
}
```

### Required `retrospective` fields

- `window`
  - current allowed value: `since_last_retrospective_or_recent`
- `recent_run_limit`
  - integer `3-10`
- `action_boundary`
  - `observe_only`
  - `allow_one_bounded_follow_up_goal`

---

## What you must never do

- Do not invent a new goal type named `retrospective`.
- Do not submit retrospective work as `tend`.
- Do not add a scheduler, cadence thread, or idle-time trigger as part of this
  manual submission surface.
- Do not expect submission itself to update memory or knowledge files.

Execution-time behavior still comes from the gardener's local retrospective
skill in `plants/gardener/skills/retrospective-pass.md`.

---

## Rejection codes

If submission fails, these retrospective-specific codes can appear:

- `RETROSPECTIVE_REQUIRES_EVALUATE`
- `INVALID_RETROSPECTIVE_PAYLOAD`
- `UNKNOWN_RETROSPECTIVE_FIELD`
- `MISSING_RETROSPECTIVE_WINDOW`
- `INVALID_RETROSPECTIVE_WINDOW`
- `MISSING_RETROSPECTIVE_RECENT_RUN_LIMIT`
- `INVALID_RETROSPECTIVE_RECENT_RUN_LIMIT`
- `MISSING_RETROSPECTIVE_ACTION_BOUNDARY`
- `INVALID_RETROSPECTIVE_ACTION_BOUNDARY`

Ordinary goal-submission rejections still apply too, including
`UNKNOWN_ASSIGNED_PLANT`, `ASSIGNED_PLANT_INACTIVE`, `INVALID_PRIORITY`,
`INVALID_SUBMITTED_BY`, and `SUBMISSION_REJECTED`.
