# Event — Engineer Reference

## What an event is

An event is an immutable, schema-validated JSON record appended to the event
log at `<runtime-root>/events/coordinator.jsonl` (one JSON object per line). Events are the
audit trail. The full history of any goal or run is reconstructable from events
alone.

## Schema

See [`schema/event.schema.json`](../../schema/event.schema.json).

### Required fields (all events)

| Field | Type | Description |
|-------|------|-------------|
| `ts` | ISO 8601 UTC | System-assigned at emit time. |
| `type` | string | Named event type. See table below. |
| `actor` | string | Who caused this: `"system"`, `"operator"`, or agent name. |

### Conditional fields (by event type)

| Event type | Additional required fields |
|------------|--------------------------|
| `GoalTransitioned` | `goal`, `from`, `to` |
| `GoalClosed` | `goal`, `goal_reason` |
| `RunFinished` | `goal`, `run`, `run_reason` |
| `DispatchPacketMaterialized` | `goal`, `run` |
| `TendStarted` | `goal`, `run`, `trigger_kinds` |
| `TendFinished` | `goal`, `run`, `follow_up_goal_count`, `memory_updated`, `operator_note_written` |
| `ActiveThreadsRefreshStarted` | `plant`, `active_threads_path` |
| `ActiveThreadsRefreshFinished` | `plant`, `active_threads_path`, `active_threads_outcome` |
| `DashboardInvocationStarted` | `dashboard_invocation_id`, `dashboard_mode`, `dashboard_refresh_seconds`, `dashboard_tty` |
| `DashboardInvocationFinished` | `dashboard_invocation_id`, `dashboard_mode`, `dashboard_refresh_seconds`, `dashboard_tty`, `dashboard_outcome`, `dashboard_render_count`, `dashboard_wall_ms`, `dashboard_record_path` |
| `ConversationHopQueued` | `goal`, `run`, `conversation_id`, `hop_goal`, `hop_requested_by`, `hop_reason`, `hop_automatic` |
| `ConversationHopQueueFailed` | `goal`, `run`, `conversation_id`, `hop_requested_by`, `hop_reason`, `hop_automatic`, `detail` |
| `ConversationCheckpointWritten` | `conversation_id`, `checkpoint_id`, `checkpoint_requested_by`, `checkpoint_reason`, `checkpoint_summary_path`, `source_message_id`, `source_session_ordinal`, `source_session_turns`, `checkpoint_count` |
| `SystemError` | `error_reason` |

### Optional fields

`goal`, `run`, and typed metadata fields are included as relevant to the event type.

Common optional metadata now includes:

- `goal_type` — the submitted goal's type
- `goal_subtype` — a more specific goal surface when used; current values are
  `post_reply_hop` and `plant_commission`
- `goal_priority` — the submitted goal's numeric priority
- `goal_origin` — e.g. `conversation`
- `conversation_id` — when the goal is linked to a conversation
- `source_message_id` — conversation message source, when present
- `source_goal_id` / `source_run_id` — the run that submitted the goal, when known
- `hop_goal`, `hop_requested_by`, `hop_reason`, `hop_automatic` — queued post-reply hop outcome metadata
- `checkpoint_*`, `source_session_*` — durable checkpoint-write metadata
- `trigger_kinds` — tend trigger family on tend lifecycle and related goal/run events
- `trigger_goal` / `trigger_run` — the goal or run that triggered a tend
- `follow_up_goals` — goal ids submitted by a tend run
- `operator_note_path` — the tend survey delivery path recorded for a tend run;
  this may be a transcript-backed top-level reply copy or an out-of-band
  `notes/` path
- `active_threads_*` — Active Threads refresh target path, typed outcome, and rejection reason
- `dashboard_*` — dashboard invocation mode, measured wall time, and record path metadata
- `packet_path`, `supplement_*` — dispatch-packet and supplement observability fields

Path-valued event fields stay relative instead of absolute:

- runtime-churn paths such as `dashboard_record_path`, `packet_path`, and
  `operator_note_path` are relative to `<runtime-root>/`
- plant-local paths such as `active_threads_path` stay relative to the garden
  root because those artifacts live under `plants/...`

## Event types

| Type | When emitted |
|------|-------------|
| `GoalSubmitted` | A goal is accepted by the submission protocol |
| `GoalTransitioned` | A goal moves to a new state |
| `GoalDispatched` | Dispatcher hands goal to a worker |
| `GoalClosed` | A goal reaches `closed` state |
| `GoalSupplemented` | A queued durable goal receives an immutable pre-dispatch supplement |
| `DispatchPacketMaterialized` | The exact pre-dispatch packet for a run is frozen to disk |
| `RunStarted` | A run subprocess launches |
| `RunFinished` | A run reaches a terminal status |
| `EvalSpawned` | An evaluate goal is created for a completed goal |
| `EvalClosed` | An evaluate goal closes, parent transitions |
| `TendStarted` | A tend run begins its survey after `RunStarted` |
| `TendFinished` | A tend run emits its semantic summary before `RunFinished` |
| `ActiveThreadsRefreshStarted` | `write_active_threads()` begins one bounded Active Threads refresh |
| `ActiveThreadsRefreshFinished` | That refresh ends with a typed success or rejection outcome |
| `DashboardInvocationStarted` | `pak2 dashboard` starts one CLI session |
| `DashboardInvocationFinished` | `pak2 dashboard` finishes and records its measured wall-time cost |
| `ConversationHopQueued` | A converse turn successfully queues a post-reply hop goal after replying |
| `ConversationHopQueueFailed` | A converse turn needed a post-reply hop but could not queue it |
| `ConversationCheckpointWritten` | A checkpoint record and archive summary are durably written |
| `MemoryUpdated` | A participant writes new memory |
| `SkillAdded` | A new skill is codified |
| `SkillArchived` | A skill is archived for disuse |
| `SystemError` | The system encountered a named error condition |

## Reason fields (typed per event type)

Reason codes are split into three typed fields to allow the schema to enforce
valid values per event type. Do not use a generic `reason` field.

### `goal_reason` — GoalClosed, EvalClosed

| Code | Meaning |
|------|---------|
| `success` | Goal completed successfully |
| `failure` | Run failed |
| `cancelled` | Goal stopped before running |
| `dependency_impossible` | A dependency can never close |

### `run_reason` — RunFinished

| Code | Meaning |
|------|---------|
| `success` | Run completed |
| `failure` | Run failed |
| `killed` | Watchdog or operator stopped the run |
| `timeout` | Run exceeded time limit |
| `zero_output` | Run produced no output |

### `error_reason` — SystemError

| Code | Meaning |
|------|---------|
| `schema_violation` | A record failed schema validation |
| `invalid_transition` | An illegal state transition was attempted |
| `submission_rejected` | A goal submission was rejected |
| `validator_unavailable` | Validator could not run |

## Conditional required fields by type

| Event type | Required fields beyond ts/type/actor |
|------------|--------------------------------------|
| `GoalSubmitted` | `goal` |
| `GoalTransitioned` | `goal`, `from`, `to` (must be valid statuses) |
| `GoalDispatched` | `goal` |
| `GoalClosed` | `goal`, `goal_reason` |
| `GoalSupplemented` | `goal` |
| `DispatchPacketMaterialized` | `goal`, `run` |
| `RunStarted` | `goal`, `run` |
| `RunFinished` | `goal`, `run`, `run_reason` |
| `EvalSpawned` | `goal`, `eval_goal` |
| `EvalClosed` | `goal`, `eval_goal`, `goal_reason` |
| `TendStarted` | `goal`, `run`, `trigger_kinds` |
| `TendFinished` | `goal`, `run`, `follow_up_goal_count`, `memory_updated`, `operator_note_written` |
| `ActiveThreadsRefreshStarted` | `plant`, `active_threads_path` |
| `ActiveThreadsRefreshFinished` | `plant`, `active_threads_path`, `active_threads_outcome` |
| `DashboardInvocationStarted` | `dashboard_invocation_id`, `dashboard_mode`, `dashboard_refresh_seconds`, `dashboard_tty` |
| `DashboardInvocationFinished` | `dashboard_invocation_id`, `dashboard_mode`, `dashboard_refresh_seconds`, `dashboard_tty`, `dashboard_outcome`, `dashboard_render_count`, `dashboard_wall_ms`, `dashboard_record_path` |
| `ConversationHopQueued` | `goal`, `run`, `conversation_id`, `hop_goal`, `hop_requested_by`, `hop_reason`, `hop_automatic` |
| `ConversationHopQueueFailed` | `goal`, `run`, `conversation_id`, `hop_requested_by`, `hop_reason`, `hop_automatic`, `detail` |
| `ConversationCheckpointWritten` | `conversation_id`, `checkpoint_id`, `checkpoint_requested_by`, `checkpoint_reason`, `checkpoint_summary_path`, `source_message_id`, `source_session_ordinal`, `source_session_turns`, `checkpoint_count` |
| `SystemError` | `error_reason` |

## Failure modes

| Reason code | When it occurs |
|-------------|---------------|
| `MISSING_REQUIRED_FIELD` | A required field is absent for this event type |
| `INVALID_TYPE` | `type` is not a known event type |
| `INVALID_TIMESTAMP` | `ts` is not ISO 8601 UTC |
| `INVALID_ACTOR` | `actor` is empty, uppercase, or otherwise malformed |
| `INVALID_STATUS` | `from` or `to` on GoalTransitioned is not a valid goal status |
| `INVALID_REASON` | Reason code is not valid for this event type |
| `INVALID_TEND_TRIGGER_KIND` | A tend event used an unknown trigger kind |
| `INVALID_GOAL_FORMAT` | A goal-shaped event field is malformed |
| `INVALID_ID_FORMAT` | A run, conversation, message, checkpoint, or dashboard invocation id is malformed |
| `INVALID_PLANT_NAME` | A plant field is empty or does not match the plant-name pattern |
| `INVALID_SHAPE` | Document is not a JSON object |

### Active Threads refresh fields

- `ActiveThreadsRefreshStarted` means `system.active_threads.write_active_threads()`
  began one bounded refresh of the artifact named by `active_threads_path`.
- `ActiveThreadsRefreshFinished` means that refresh ended.
- `active_threads_outcome` is currently `success`, `validation_rejected`, or
  `io_error`.
- When `active_threads_outcome` is not `success`, `active_threads_reason`
  carries the helper's named `ValidationResult.reason`, and `detail` may carry
  the explanatory text.

## Validation

`system/validate.py:validate_event(data: dict) -> ValidationResult`

Called before every append. Returns `.ok=True` or `.ok=False` with `.reason`
and `.detail`. Never raises.

## Tests

Focused regression coverage currently lives in:

- `tests/test_active_threads.py`
- `tests/test_coordinator.py`
- `tests/test_tend.py`
- `tests/test_conversation_session_maintenance.py`
- `tests/test_dashboard.py`
- `tests/test_dashboard_invocations.py`

For the current automatic `run_failure` / `queued_attention_needed` trigger
pair that produces these tend events, see
[`Automatic Tend Submission`](../automatic-tend/level2.md).
