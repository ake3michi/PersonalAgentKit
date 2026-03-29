# Event — Agent Contract

This document is self-contained.

---

## What you may do

- **Read** the event log at `<runtime-root>/events/coordinator.jsonl`.
- **Observe** events to understand system state.

## What you must never do

- Append to the event log directly.
- Modify or delete any event.
- Assume an action occurred if there is no event for it.

Events are written by the system only. You observe them; you do not produce them.

---

## Reading the event log

The log is at `<runtime-root>/events/coordinator.jsonl`. Each line is one JSON event.
Read sequentially from top. The last transition event for a goal is its
current state.

```jsonl
{"ts":"2026-03-18T12:00:00Z","type":"GoalSubmitted","actor":"gardener","goal":"42-fix-the-thing","goal_type":"fix"}
{"ts":"2026-03-18T12:01:00Z","type":"GoalTransitioned","actor":"system","goal":"42-fix-the-thing","from":"queued","to":"dispatched"}
{"ts":"2026-03-18T12:01:01Z","type":"RunStarted","actor":"system","goal":"42-fix-the-thing","run":"42-fix-the-thing-r1"}
{"ts":"2026-03-18T12:10:00Z","type":"RunFinished","actor":"system","goal":"42-fix-the-thing","run":"42-fix-the-thing-r1","run_reason":"success"}
{"ts":"2026-03-18T12:10:01Z","type":"GoalClosed","actor":"system","goal":"42-fix-the-thing","goal_reason":"success"}
{"ts":"2026-03-18T12:20:00Z","type":"TendStarted","actor":"system","goal":"43-review-garden-state","run":"43-review-garden-state-r1","trigger_kinds":["operator_request"]}
{"ts":"2026-03-18T12:25:00Z","type":"TendFinished","actor":"system","goal":"43-review-garden-state","run":"43-review-garden-state-r1","follow_up_goal_count":1,"memory_updated":true,"operator_note_written":true}
{"ts":"2026-03-23T03:00:00Z","type":"ActiveThreadsRefreshStarted","actor":"gardener","plant":"gardener","goal":"285-after-goal-281-finishes-design-and-land","run":"285-after-goal-281-finishes-design-and-land-r1","active_threads_path":"plants/gardener/memory/active-threads.json"}
{"ts":"2026-03-23T03:00:01Z","type":"ActiveThreadsRefreshFinished","actor":"gardener","plant":"gardener","goal":"285-after-goal-281-finishes-design-and-land","run":"285-after-goal-281-finishes-design-and-land-r1","active_threads_path":"plants/gardener/memory/active-threads.json","active_threads_outcome":"success"}
{"ts":"2026-03-23T02:00:00Z","type":"DashboardInvocationStarted","actor":"operator","dashboard_invocation_id":"dash-20260323020000-abcd","dashboard_mode":"once","dashboard_refresh_seconds":2.0,"dashboard_tty":false}
{"ts":"2026-03-23T02:00:01Z","type":"DashboardInvocationFinished","actor":"operator","dashboard_invocation_id":"dash-20260323020000-abcd","dashboard_mode":"once","dashboard_refresh_seconds":2.0,"dashboard_tty":false,"dashboard_outcome":"success","dashboard_render_count":1,"dashboard_wall_ms":1000,"dashboard_record_path":"dashboard/invocations/dash-20260323020000-abcd.json"}
```

Path-valued event fields stay relative instead of absolute. In the example
above, `dashboard_record_path` means
`<runtime-root>/dashboard/invocations/dash-20260323020000-abcd.json`.

---

## Required fields on every event

| Field | Type | Description |
|-------|------|-------------|
| `ts` | ISO 8601 UTC string | When it happened |
| `type` | string | What happened (see table below) |
| `actor` | string | Who caused it — lowercase alphanumeric, starts with letter. Use `"system"`, `"operator"`, or your agent name. |

---

## Required fields by event type

| Type | Additional required fields |
|------|---------------------------|
| `GoalSubmitted` | `goal` |
| `GoalTransitioned` | `goal`, `from`, `to` (valid statuses only) |
| `GoalDispatched` | `goal` |
| `GoalClosed` | `goal`, `goal_reason` |
| `GoalSupplemented` | `goal` |
| `DispatchPacketMaterialized` | `goal`, `run` |
| `RunStarted` | `goal`, `run` |
| `RunFinished` | `goal`, `run`, `run_reason` |
| `EvalSpawned` | `goal`, `eval_goal` |
| `EvalClosed` | `goal`, `eval_goal`, `goal_reason` |
| `SystemError` | `error_reason` |
| `TendStarted` | `goal`, `run`, `trigger_kinds` |
| `TendFinished` | `goal`, `run`, `follow_up_goal_count`, `memory_updated`, `operator_note_written` |
| `ActiveThreadsRefreshStarted` | `plant`, `active_threads_path` |
| `ActiveThreadsRefreshFinished` | `plant`, `active_threads_path`, `active_threads_outcome` |
| `DashboardInvocationStarted` | `dashboard_invocation_id`, `dashboard_mode`, `dashboard_refresh_seconds`, `dashboard_tty` |
| `DashboardInvocationFinished` | `dashboard_invocation_id`, `dashboard_mode`, `dashboard_refresh_seconds`, `dashboard_tty`, `dashboard_outcome`, `dashboard_render_count`, `dashboard_wall_ms`, `dashboard_record_path` |
| `ConversationHopQueued` | `goal`, `run`, `conversation_id`, `hop_goal`, `hop_requested_by`, `hop_reason`, `hop_automatic` |
| `ConversationHopQueueFailed` | `goal`, `run`, `conversation_id`, `hop_requested_by`, `hop_reason`, `hop_automatic`, `detail` |
| `ConversationCheckpointWritten` | `conversation_id`, `checkpoint_id`, `checkpoint_requested_by`, `checkpoint_reason`, `checkpoint_summary_path`, `source_message_id`, `source_session_ordinal`, `source_session_turns`, `checkpoint_count` |
| `MemoryUpdated`, `SkillAdded`, `SkillArchived` | (none beyond ts/type/actor) |

Events may also include observability metadata such as `goal_type`,
`goal_subtype`, `goal_priority`, `goal_origin`, `conversation_id`, `source_message_id`,
`source_goal_id`, `source_run_id`, `hop_goal`, `hop_requested_by`,
`hop_reason`, `hop_automatic`, `checkpoint_*`, `source_session_*`,
`trigger_kinds`, `trigger_goal`, `trigger_run`, `follow_up_goals`,
`operator_note_path`, `active_threads_*`, `dashboard_*`, `packet_path`, and
supplement stats.

For tend runs, `operator_note_path` now comes from the validated operator-message
record for the run's emitted `tend_survey`. It can point either to a top-level
reply delivery copy or to `inbox/<garden-name>/notes/...` for an out-of-band
note.

Current `goal_subtype` values in production are:

- `post_reply_hop`
- `plant_commission`

---

## Reason fields

Reason codes are split by context. Do not use a generic `reason` field.

### `goal_reason` — used by GoalClosed and EvalClosed

| Value | Meaning |
|-------|---------|
| `success` | Goal completed |
| `failure` | Agent tried; could not complete |
| `cancelled` | Goal stopped before running |
| `dependency_impossible` | A dependency will never close |

### `run_reason` — used by RunFinished

| Value | Meaning |
|-------|---------|
| `success` | Run completed |
| `failure` | Run failed |
| `killed` | Watchdog or operator terminated it |
| `timeout` | Exceeded time limit |
| `zero_output` | Produced no output |

### `error_reason` — used by SystemError

| Value | Meaning for you |
|-------|----------------|
| `schema_violation` | A record you submitted failed validation |
| `invalid_transition` | A transition was attempted that is not allowed |
| `submission_rejected` | Your goal submission was rejected |
| `validator_unavailable` | System could not validate; treat as transient |

### `trigger_kinds` — used by TendStarted

Allowed values:

- `post_genesis`
- `operator_request`
- `run_failure`
- `queued_attention_needed`

For the live automatic trigger pair and its non-trigger rules, see
[`Automatic Tend Submission`](../automatic-tend/level3.md).

### Conversation lifecycle fields

- `ConversationHopQueued` means the current converse run replied first and then
  successfully queued follow-up checkpoint work as a separate goal.
- `ConversationHopQueueFailed` means that same queueing step was needed but
  could not complete; inspect `detail`.
- `ConversationCheckpointWritten` means the durable checkpoint record and
  archived summary now exist, independent of generic run state.

### Dashboard invocation fields

- `DashboardInvocationStarted` means one `pak2 dashboard` CLI session has
  begun.
- `DashboardInvocationFinished` means that session ended and wrote the measured
  wall-time cost record named by `dashboard_record_path`.
- `dashboard_mode` is currently `once` or `live`.
- `dashboard_outcome` is currently `success`, `interrupted`, or `failure`.

### Active Threads refresh fields

- `ActiveThreadsRefreshStarted` means `write_active_threads()` began one bounded
  refresh of `active_threads_path`.
- `ActiveThreadsRefreshFinished` means that refresh ended.
- `active_threads_outcome` is currently `success`, `validation_rejected`, or
  `io_error`.
- When `active_threads_outcome` is not `success`, `active_threads_reason`
  carries the helper's named rejection code.

---

## Inferring current goal state

To find the current state of goal `42-fix-the-thing`:

1. Filter the log for `goal == "42-fix-the-thing"`.
2. Find the last `GoalTransitioned` event. The `to` field is the current state.
3. If a `GoalClosed` event exists, the goal is closed. Read `goal_reason`.

Do not infer state from the goal file alone — the event log is authoritative.
