# Goal — Agent Contract

This document is self-contained. An agent must be able to follow it without
consulting any other source.

---

## What you may do

- **Read** any goal file in `<runtime-root>/goals/`.
- **Submit** new goals via the submission protocol (see below).
- **Read** the event log to observe goal transitions.

## What you must never do

- Write directly to a goal file.
- Set the `id`, `submitted_at`, `parent_goal`, or `_coordinator_id` fields.
- Transition a goal's status by editing the file.
- Delete a goal file.

All state transitions are performed by the system. You submit decisions; the
system acts on them.

---

## Submitting a goal

To submit a goal, call `system.submit.submit_goal(data)`. The system validates,
assigns identity, and places the goal in `<runtime-root>/goals/`.

### Required fields you must provide

```json
{
  "type": "<build|fix|spike|tend|evaluate|research|converse>",
  "submitted_by": "<your agent name>",
  "body": "<non-empty description of the work>"
}
```

`submitted_by` must be lowercase alphanumeric with hyphens, starting with a
letter. Examples: `"operator"`, `"gardener"`, `"coder"`.

### Optional fields you may provide

```json
{
  "priority": 5,
  "assigned_to": "<active commissioned plant>",
  "depends_on": ["41-prior-goal"],
  "not_before": "2026-03-18T15:00:00Z",
  "spawn_eval": true,
  "origin": {
    "kind": "conversation",
    "conversation_id": "1-hello",
    "message_id": "msg-20260326200000-ope-abcd",
    "ts": "2026-03-26T20:00:00Z"
  },
  "driver": "codex",
  "model": "gpt-5.4"
}
```

`not_before` must not be earlier than the current time.
If you provide `assigned_to`, that plant must already exist in `plants/` and
have `status: "active"`.
If you omit `driver` and `model`, and no environment override or
`[defaults]` entry is present, the system launches the goal with `codex` and
`gpt-5.4`.

If you provide `origin`, it must be a complete conversation reference with
`kind: "conversation"`, a valid `conversation_id`, and a valid ISO 8601 UTC
`ts`. `message_id` is optional. When you call `submit_goal()` from a
`converse` run for durable non-`converse` work, prefer omitting `origin`
unless you are intentionally copying an earlier conversation link; the helper
fills it automatically. Partial explicit origin objects are rejected with
`INVALID_GOAL_ORIGIN`.

If you submit a raw `type: "tend"` goal instead of using
`system.submit.submit_tend_goal()`, you must also include a `tend` object that
matches [`schema/goal-tend.schema.json`](../../schema/goal-tend.schema.json).
For the exact raw-tend contract, use [Goal — Engineer Reference](./level2.md)
as the authoritative surface; it points at the schema and the
`system.validate.validate_goal()` submission boundary that enforces it.
For the live automatic `run_failure` / `queued_attention_needed` tend behavior
built on top of that payload, see
[`Automatic Tend Submission`](../automatic-tend/level3.md).

```json
{
  "type": "tend",
  "submitted_by": "gardener",
  "body": "Perform a bounded tend pass.",
  "tend": {
    "trigger_kinds": ["operator_request"],
    "trigger_goal": "98-converse-turn",
    "trigger_run": "98-converse-turn-r1"
  }
}
```

For a manual retrospective, prefer
`system.submit.submit_retrospective_goal()` or the operator-facing
`pak2 retrospective` command. If you bypass the helper and submit the raw
goal yourself, it must still be an ordinary `evaluate` goal and the
`retrospective` object must match
[`schema/goal-retrospective.schema.json`](../../schema/goal-retrospective.schema.json).
The stable capability contract is
[`Manual Retrospective`](../retrospective/level3.md).

For later-plant commissioning, prefer
`system.submit.submit_plant_commission_goal()`. That helper submits an
ordinary `build` goal assigned to `gardener` with a structured
`plant_commission` payload. When the dedicated commissioning run starts, use
`system.plants.execute_plant_commission()` to create the plant and queue its
first bounded goal with the same run-local timestamp. If you bypass the helper
and submit the raw goal yourself, the `plant_commission` object must match
[`schema/goal-plant-commission.schema.json`](../../schema/goal-plant-commission.schema.json).

### Fields you must not provide

`id`, `status`, `submitted_at`, `parent_goal`, `closed_reason`

The submission will be rejected with `SUBMISSION_REJECTED` if you include them.

---

## Goal IDs

Goal IDs are assigned by the system. Format: `N-slug` where N is a positive
integer with no leading zeros and no upper limit. Examples: `1-my-goal`,
`42-fix-the-thing`, `1000-large-refactor`.

---

## Goal lifecycle

A goal is always in exactly one of these states:

| State | Meaning |
|-------|---------|
| `queued` | Waiting to be dispatched |
| `dispatched` | Claimed by a worker |
| `running` | Subprocess is live |
| `completed` | Run succeeded |
| `evaluating` | Waiting for an evaluate goal to close |
| `closed` | Terminal. No further transitions. |

### Valid transitions (system-enforced)

```
queued      → dispatched, closed
dispatched  → running, queued (rollback), closed
running     → completed, closed
completed   → evaluating, closed
evaluating  → closed
closed      → (none)
```

You will never cause a transition directly. You observe them in the event log.

---

## spawn_eval vs. goal type "evaluate"

These are different things:

- `"spawn_eval": true` — a field honored on eligible `build` and `fix` goals
  that causes the system to create exactly one new goal of type `evaluate`
  when the parent goal completes successfully.
- `"type": "evaluate"` — a goal whose purpose is to evaluate another goal's
  output. Set by the system when spawning; you should not set this yourself.

---

## Reading a goal

Goal files are JSON in `<runtime-root>/goals/`.

```json
{
  "id":           "42-fix-the-thing",
  "status":       "queued",
  "type":         "fix",
  "submitted_at": "2026-03-18T12:00:00Z",
  "submitted_by": "gardener",
  "body":         "Fix the broken state machine transition.",
  "priority":     3,
  "depends_on":   ["41-prior-goal"]
}
```

When `status` is `closed`, `closed_reason` will be one of:

| Value | Meaning |
|-------|---------|
| `success` | Work completed |
| `failure` | Agent tried and could not complete |
| `cancelled` | Stopped before running |
| `dependency_impossible` | A dependency can never close |

---

## Reflection requirement

If you complete a run for a goal of type `build`, `fix`, `evaluate`, or `tend`,
you must include a `reflection` field in the run record before the system will
accept the run as `success`. The reflection must be non-empty and non-whitespace.

Goals of type `spike` and `research` do not require reflection.

---

## Reason codes you may receive on rejection

For raw `type: "tend"` submissions, [Goal — Engineer Reference](./level2.md)
is the authoritative contract surface. The tend-related entries below are the
same goal-submission reason codes repeated here for agent convenience.

| Code | What to do |
|------|-----------|
| `MISSING_REQUIRED_FIELD` | Add the missing field |
| `INVALID_TYPE` | Use one of: `build`, `fix`, `spike`, `tend`, `evaluate`, `research`, `converse` |
| `INVALID_PRIORITY` | Use an integer from 1 to 10 |
| `EMPTY_BODY` | Provide a non-empty body |
| `INVALID_SUBMITTED_BY` | Use lowercase alphanumeric starting with a letter |
| `UNKNOWN_ASSIGNED_PLANT` | Commission the plant before assigning work to it |
| `ASSIGNED_PLANT_INACTIVE` | Reassign the goal away from the archived plant |
| `INVALID_GOAL_ORIGIN` | Provide a complete conversation origin with `kind`, `conversation_id`, and `ts` |
| `INVALID_DEPENDS_ON_FORMAT` | Each entry must be a valid goal ID (positive integer slug) |
| `INVALID_TIMESTAMP` | Use ISO 8601 UTC format: `2026-03-18T12:00:00Z` |
| `NOT_BEFORE_BEFORE_SUBMITTED` | `not_before` must not be in the past relative to `submitted_at` |
| `MISSING_TEND_PAYLOAD` | Add the required `tend` object for raw `type: "tend"` submissions |
| `INVALID_TEND_PAYLOAD` | Make `tend` a JSON object |
| `UNKNOWN_TEND_FIELD` | Remove any unsupported keys from `tend` |
| `MISSING_TEND_TRIGGER_KINDS` | Add `tend.trigger_kinds` |
| `INVALID_TEND_TRIGGER_KINDS` | Use a non-empty array of unique trigger strings |
| `INVALID_TEND_TRIGGER_KIND` | Use one of: `operator_request`, `post_genesis`, `run_failure`, `queued_attention_needed` |
| `INVALID_TEND_TRIGGER_GOAL` | Use a valid goal ID in `tend.trigger_goal` |
| `INVALID_TEND_TRIGGER_RUN` | Use a valid run ID in `tend.trigger_run` |
| `PLANT_COMMISSION_REQUIRES_BUILD` | Submit `plant_commission` payloads only on `build` goals |
| `PLANT_COMMISSION_REQUIRES_GARDENER` | Assign `plant_commission` goals to `gardener` |
| `INVALID_PLANT_COMMISSION_PAYLOAD` | Make `plant_commission` a JSON object |
| `UNKNOWN_PLANT_COMMISSION_FIELD` | Remove unsupported keys from `plant_commission` |
| `MISSING_PLANT_COMMISSION_PLANT_NAME` | Add `plant_commission.plant_name` |
| `INVALID_PLANT_COMMISSION_PLANT_NAME` | Use a valid lowercase plant name in `plant_commission.plant_name` |
| `MISSING_PLANT_COMMISSION_SEED` | Add `plant_commission.seed` |
| `INVALID_PLANT_COMMISSION_SEED` | Use a valid lowercase seed name in `plant_commission.seed` |
| `MISSING_PLANT_COMMISSION_INITIAL_GOAL` | Add `plant_commission.initial_goal` |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL` | Make `plant_commission.initial_goal` a JSON object |
| `UNKNOWN_PLANT_COMMISSION_INITIAL_GOAL_FIELD` | Remove unsupported keys from `plant_commission.initial_goal` |
| `MISSING_PLANT_COMMISSION_INITIAL_GOAL_TYPE` | Add `plant_commission.initial_goal.type` |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL_TYPE` | Use one of: `build`, `fix`, `spike`, `evaluate`, `research` |
| `MISSING_PLANT_COMMISSION_INITIAL_GOAL_BODY` | Add a non-empty `plant_commission.initial_goal.body` |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL_PRIORITY` | Use an integer from `1` to `10` in `plant_commission.initial_goal.priority` |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL_REASONING_EFFORT` | Use one of: `low`, `medium`, `high`, `xhigh` |
| `RETROSPECTIVE_REQUIRES_EVALUATE` | Submit retrospective payloads only on `evaluate` goals |
| `INVALID_RETROSPECTIVE_PAYLOAD` | Make `retrospective` a JSON object |
| `UNKNOWN_RETROSPECTIVE_FIELD` | Remove unsupported keys from `retrospective` |
| `MISSING_RETROSPECTIVE_WINDOW` | Add `retrospective.window` |
| `INVALID_RETROSPECTIVE_WINDOW` | Use the current retrospective window policy |
| `MISSING_RETROSPECTIVE_RECENT_RUN_LIMIT` | Add `retrospective.recent_run_limit` |
| `INVALID_RETROSPECTIVE_RECENT_RUN_LIMIT` | Use an integer from `3` to `10` |
| `MISSING_RETROSPECTIVE_ACTION_BOUNDARY` | Add `retrospective.action_boundary` |
| `INVALID_RETROSPECTIVE_ACTION_BOUNDARY` | Use a known retrospective action boundary |
| `SUBMISSION_REJECTED` | You included a system-assigned field; remove it |
