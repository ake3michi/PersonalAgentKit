# Goal — Engineer Reference

## What a goal is

A goal is a unit of work with a defined lifecycle managed by the system's
state machine. Goals are submitted through the submission protocol — they are
never created by writing directly to the goals directory. The system assigns
identity (the `id` field); submitters provide intent.

## Schema

See [`schema/goal.schema.json`](../../schema/goal.schema.json).
For raw `type: "tend"` submissions, this document is the stable contract
surface. The `tend` payload schema lives at
[`schema/goal-tend.schema.json`](../../schema/goal-tend.schema.json), and
`system/validate.py:validate_goal(data: dict) -> ValidationResult` is the
submission-boundary validator that enforces it.

Manual retrospective submissions use the same goal boundary. Their payload
schema lives at
[`schema/goal-retrospective.schema.json`](../../schema/goal-retrospective.schema.json),
and the operator/helper surface for that payload lives in
[`docs/retrospective/level2.md`](../retrospective/level2.md).

Dedicated later-plant commissioning uses the same goal boundary too. Its
payload schema lives at
[`schema/goal-plant-commission.schema.json`](../../schema/goal-plant-commission.schema.json),
and the normal helper/execution surface for that payload lives across
[`docs/plant/level2.md`](../plant/level2.md) and
[`docs/system/level2.md`](../system/level2.md).

### Conversation-origin durable goal contract

When `system.submit.submit_goal()` is called from a `converse` run for durable
non-`converse` work, the helper auto-tags the new goal with conversation
origin metadata. If a later retry or handoff needs to preserve that linkage
outside a live converse run, the caller may supply `origin` explicitly, but it
must be a complete conversation reference:

```json
{
  "origin": {
    "kind": "conversation",
    "conversation_id": "1-hello",
    "message_id": "msg-20260326200000-ope-abcd",
    "ts": "2026-03-26T20:00:00Z"
  }
}
```

`message_id` is optional. `kind`, `conversation_id`, and `ts` are required.
Partial explicit origin objects are rejected at submission with
`INVALID_GOAL_ORIGIN` instead of persisting into a later
`DispatchPacketMaterialized` failure.

### Raw `tend` submission contract

Prefer `system.submit.submit_tend_goal()` when code needs to create a bounded
tend goal. If a caller submits a raw `type: "tend"` goal instead, the `tend`
object must match
[`schema/goal-tend.schema.json`](../../schema/goal-tend.schema.json). The
named `MISSING_TEND_*`, `INVALID_TEND_*`, and `UNKNOWN_TEND_FIELD` reason
codes below all come from `validate_goal()` at that goal-submission boundary.
The current automatic background trigger pair built on top of this tend payload
is documented in [`docs/automatic-tend/level2.md`](../automatic-tend/level2.md).

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `N-slug` format (positive integer, no leading zeros, no upper limit). System-assigned. |
| `status` | string | Current lifecycle state. See state machine below. |
| `type` | string | Work category. Determines reflection and eval rules. |
| `submitted_at` | ISO 8601 UTC | System-assigned at submission. |
| `submitted_by` | string | Lowercase alphanumeric with hyphens, starts with a letter. `"operator"` or agent name. |
| `body` | string | Non-empty. Human-readable description of the work. |

### Optional fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `priority` | int 1–10 | 5 | Dispatch order. 1 = most urgent. |
| `assigned_to` | string | — | Route to a named active commissioned plant. Submission rejects unknown or archived plants. |
| `depends_on` | string[] | — | Goal IDs that must be closed first. |
| `not_before` | ISO 8601 UTC | — | Earliest dispatch time. Must not precede `submitted_at`. |
| `spawn_eval` | bool | false | On eligible `build` / `fix` goals, spawn exactly one system-created evaluate goal after successful completion. Distinct from goal type `evaluate`. |
| `tend` | object | — | Required on raw `type: "tend"` submissions. Must match [`schema/goal-tend.schema.json`](../../schema/goal-tend.schema.json). |
| `plant_commission` | object | — | Optional on dedicated later-plant `build` goals assigned to `gardener`. Must match [`schema/goal-plant-commission.schema.json`](../../schema/goal-plant-commission.schema.json). |
| `retrospective` | object | — | Optional on `evaluate` goals used for the manual retrospective protocol. Must match [`schema/goal-retrospective.schema.json`](../../schema/goal-retrospective.schema.json). |
| `origin` | object | — | Optional explicit conversation linkage for durable non-`converse` work. Must be a complete conversation reference with `kind: "conversation"`, a valid `conversation_id`, required `ts`, and optional valid `message_id`. Prefer omitting it during live `converse` submissions so the helper can fill it automatically. |
| `parent_goal` | string | — | Set by system for evaluate goals. |
| `driver` | string | — | Override default execution driver. |
| `model` | string | — | Override default model. |
| `closed_reason` | string | — | Required when status is `closed`. Forbidden otherwise. |

## State machine

```
queued ──────────────────────────────────────┐
  │                                          │
  ▼                                          │ (cancel / impossible dep)
dispatched ──────────────────────────────────┤
  │                                          │
  ▼                                          │ (cancel before start)
running ─────────────────────────────────────┤
  │                                          │ (failure / killed)
  ▼                                          │
completed ───────────────────────────────────┤
  │ (spawn_eval=true)                        │
  ▼                                          │
evaluating ──────────────────────────────────┘
                                             │
                                             ▼
                                           closed  ◀── only terminal state
```

Transitions are enforced by `system/validate.py`. Any attempt to write an
invalid transition is rejected with reason `INVALID_TRANSITION`.

## Goal types and their rules

| Type | Reflection required on success? | spawn_eval eligible? |
|------|--------------------------------|---------------------|
| `build` | Yes | Yes |
| `fix` | Yes | Yes |
| `spike` | No (produces artifact instead) | No |
| `tend` | Yes | No |
| `evaluate` | Yes | No |
| `research` | No (produces artifact instead) | No |

Reflection requirements are enforced at run close time by `validate_run_close()`
in `system/validate.py`, which cross-references the goal type.

## Failure modes

| Reason code | When it occurs |
|-------------|---------------|
| `MISSING_REQUIRED_FIELD` | A required field is absent |
| `INVALID_ID_FORMAT` | `id` is not a positive integer slug (e.g. leading zeros, no number) |
| `INVALID_STATUS` | `status` is not a valid lifecycle state |
| `INVALID_TYPE` | `type` is not a known work category |
| `INVALID_TIMESTAMP` | `submitted_at` or `not_before` is not ISO 8601 UTC |
| `NOT_BEFORE_BEFORE_SUBMITTED` | `not_before` is earlier than `submitted_at` |
| `EMPTY_BODY` | `body` is empty or whitespace |
| `INVALID_PRIORITY` | `priority` is not an integer in 1–10 |
| `INVALID_SUBMITTED_BY` | `submitted_by` is not lowercase alphanumeric starting with a letter |
| `UNKNOWN_ASSIGNED_PLANT` | `assigned_to` names a plant that has not been commissioned |
| `ASSIGNED_PLANT_INACTIVE` | `assigned_to` names a commissioned plant whose status is not `active` |
| `INVALID_GOAL_ORIGIN` | `origin` is present but is not a complete conversation reference with required `kind`, `conversation_id`, and `ts` |
| `MISSING_CLOSED_REASON` | `status` is `closed` but `closed_reason` is absent |
| `INVALID_CLOSED_REASON` | `closed_reason` is not a known reason code |
| `SPURIOUS_CLOSED_REASON` | `closed_reason` present when status is not `closed` |
| `INVALID_DEPENDS_ON` | `depends_on` is not an array |
| `INVALID_DEPENDS_ON_FORMAT` | An entry in `depends_on` is not a valid goal ID |
| `MISSING_TEND_PAYLOAD` | A raw `tend` goal omitted the `tend` payload entirely |
| `INVALID_TEND_PAYLOAD` | `tend` is present but is not a JSON object |
| `UNKNOWN_TEND_FIELD` | `tend` contains a field outside the schema contract |
| `MISSING_TEND_TRIGGER_KINDS` | `tend.trigger_kinds` is absent |
| `INVALID_TEND_TRIGGER_KINDS` | `tend.trigger_kinds` is not a non-empty unique string array |
| `INVALID_TEND_TRIGGER_KIND` | `tend.trigger_kinds` contains an unknown trigger value |
| `INVALID_TEND_TRIGGER_GOAL` | `tend.trigger_goal` is not a valid goal id |
| `INVALID_TEND_TRIGGER_RUN` | `tend.trigger_run` is not a valid run id |
| `PLANT_COMMISSION_REQUIRES_BUILD` | `plant_commission` appears on a non-`build` goal |
| `PLANT_COMMISSION_REQUIRES_GARDENER` | `plant_commission` goal is not assigned to `gardener` |
| `INVALID_PLANT_COMMISSION_PAYLOAD` | `plant_commission` is present but is not a JSON object |
| `UNKNOWN_PLANT_COMMISSION_FIELD` | `plant_commission` contains a field outside the schema contract |
| `MISSING_PLANT_COMMISSION_PLANT_NAME` | `plant_commission.plant_name` is absent or empty |
| `INVALID_PLANT_COMMISSION_PLANT_NAME` | `plant_commission.plant_name` is malformed |
| `MISSING_PLANT_COMMISSION_SEED` | `plant_commission.seed` is absent or empty |
| `INVALID_PLANT_COMMISSION_SEED` | `plant_commission.seed` is malformed |
| `MISSING_PLANT_COMMISSION_INITIAL_GOAL` | `plant_commission.initial_goal` is absent |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL` | `plant_commission.initial_goal` is not a JSON object |
| `UNKNOWN_PLANT_COMMISSION_INITIAL_GOAL_FIELD` | `plant_commission.initial_goal` contains an unsupported field |
| `MISSING_PLANT_COMMISSION_INITIAL_GOAL_TYPE` | `plant_commission.initial_goal.type` is absent |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL_TYPE` | `plant_commission.initial_goal.type` is not one of the allowed durable work types |
| `MISSING_PLANT_COMMISSION_INITIAL_GOAL_BODY` | `plant_commission.initial_goal.body` is empty |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL_PRIORITY` | `plant_commission.initial_goal.priority` is not an integer in `1-10` |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL_REASONING_EFFORT` | `plant_commission.initial_goal.reasoning_effort` is not a known value |
| `RETROSPECTIVE_REQUIRES_EVALUATE` | `retrospective` appears on a non-`evaluate` goal |
| `INVALID_RETROSPECTIVE_PAYLOAD` | `retrospective` is present but is not a JSON object |
| `UNKNOWN_RETROSPECTIVE_FIELD` | `retrospective` contains a field outside the schema contract |
| `MISSING_RETROSPECTIVE_WINDOW` | `retrospective.window` is absent |
| `INVALID_RETROSPECTIVE_WINDOW` | `retrospective.window` is not a known policy value |
| `MISSING_RETROSPECTIVE_RECENT_RUN_LIMIT` | `retrospective.recent_run_limit` is absent |
| `INVALID_RETROSPECTIVE_RECENT_RUN_LIMIT` | `retrospective.recent_run_limit` is not an integer in `3-10` |
| `MISSING_RETROSPECTIVE_ACTION_BOUNDARY` | `retrospective.action_boundary` is absent |
| `INVALID_RETROSPECTIVE_ACTION_BOUNDARY` | `retrospective.action_boundary` is not a known policy value |
| `INVALID_SHAPE` | The document is not a JSON object |

## Validation

`system/validate.py:validate_goal(data: dict, *, _plants_dir: Path | None = None)
-> ValidationResult`

Called at submission from `system.goals.submit_goal()`. Returns `.ok=True` or
`.ok=False` with a `.reason` code and `.detail` string. Never raises. When
`assigned_to` is present, the submission boundary checks
`plants/{name}/meta.json` and requires the referenced plant status to be
`active`. When `origin` is present, the same boundary requires a complete
conversation reference before the goal can be persisted. Raw `type: "tend"`,
manual-retrospective payload validation, and the dedicated later-plant
`plant_commission` payload validation also happen here.

## Tests

`tests/test_validate.py` — `GoalAssignedPlantValidationTests`.
`tests/test_tend.py` — `RawTendGoalSubmissionTests` and
`SubmitTendGoalTests`.
`tests/test_plant_commission.py` — raw later-plant commissioning payload
validation and helper/workflow coverage.
`tests/test_retrospective.py` — raw retrospective payload validation,
helper integration, and CLI submission coverage.
`tests/test_goal_supplements.py` — conversation-origin submission tagging and
rejection of malformed explicit `origin` metadata before persistence.
