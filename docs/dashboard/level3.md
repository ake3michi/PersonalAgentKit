# Live Observability Dashboard — Agent Contract

This document is self-contained.

---

## What this capability means for you

The Live Observability Dashboard is a read-only convenience surface over the
garden's existing goal, run, conversation, and event records.

Use it to orient quickly. Do not treat it as a new source of truth.

For current state, goal and run records still win. The dashboard is a derived
view. The only writes it now performs are audit-only records of the dashboard
invocation itself.

---

## What you may do

- Run `pak2 dashboard --root . --once` to get one human-readable snapshot.
- Run `pak2 dashboard --root . --refresh 2` in a TTY for a live updating view.
- Call `system.dashboard.build_snapshot(root)` if you need the in-memory
  dataclass tree directly.
- Call `system.dashboard.build_snapshot_tree(root)` if you need the
  machine-readable snapshot tree that matches
  [`schema/dashboard-snapshot.schema.json`](../../schema/dashboard-snapshot.schema.json).
- Call `system.dashboard.validate_snapshot_tree(tree)` if you need a named
  accept/reject result for that machine-readable snapshot contract.
- Call `system.dashboard.build_render_tree(snapshot, width=..., height=...)`
  if you need the machine-readable terminal render tree that matches
  [`schema/dashboard-render.schema.json`](../../schema/dashboard-render.schema.json).
- Call `system.dashboard.validate_render_tree(tree)` if you need a named
  accept/reject result for that machine-readable render contract.
- Call `system.dashboard.render_dashboard(snapshot, width=..., height=...)`
  if you already have a snapshot and need terminal text.
- Call `system.validate.validate_dashboard_invocation(data)` if you need a
  named accept/reject result for one persisted dashboard invocation record.
- Read `<runtime-root>/dashboard/invocations/<id>.json` if you need the persisted cost record
  for a completed dashboard session.

Example:

```python
from pathlib import Path

from system.dashboard import (
    build_render_tree,
    build_snapshot,
    build_snapshot_tree,
    render_dashboard,
    validate_render_tree,
    validate_snapshot_tree,
)

snapshot = build_snapshot(Path("."))
tree = build_snapshot_tree(Path("."))
render_tree = build_render_tree(snapshot, width=120)
result = validate_snapshot_tree(tree)
render_result = validate_render_tree(render_tree)
text = render_dashboard(snapshot, width=120)
assert result.ok, result
assert render_result.ok, render_result
print(tree["state"])
print(render_tree["layout"])
print(text)
```

## What you may read indirectly through it

The dashboard reads these surfaces:

- `<runtime-root>/goals/*.json`
- `<runtime-root>/runs/*/meta.json`
- `<runtime-root>/runs/*/events.jsonl` mtime for liveness
- `<runtime-root>/conversations/*/meta.json`
- latest `<runtime-root>/conversations/*/turns.jsonl` record
- `<runtime-root>/events/coordinator.jsonl`
- `/proc` for coordinator-process detection on Linux
- `<runtime-root>/dashboard/invocations/*.json` only when you inspect prior invocation cost
  records directly; the live dashboard view itself does not read them back

## What it emits and writes

Running the dashboard still does not submit goals or mutate garden execution
state, but it now writes an explicit audit trail for the invocation itself:

- `DashboardInvocationStarted` in `<runtime-root>/events/coordinator.jsonl`
- `DashboardInvocationFinished` in `<runtime-root>/events/coordinator.jsonl`
- one terminal cost record at `<runtime-root>/dashboard/invocations/<id>.json`

It does not:

- submit a goal
- open or close a run
- write a dashboard snapshot file
- fold dashboard wall-time cost into the on-screen provider-token cost panel

### Dashboard invocation record

The persisted invocation record is validated by
`system.validate.validate_dashboard_invocation(data)` and matches
[`schema/dashboard-invocation.schema.json`](../../schema/dashboard-invocation.schema.json).

Top-level fields:

- `id`
- `actor`
- `source_goal_id` and `source_run_id` when the dashboard was launched from a run
- `started_at`
- `completed_at`
- `root`
- `mode`
- `refresh_seconds`
- `tty`
- `render_count`
- `outcome`
- `error_detail` only when `outcome == "failure"`
- `cost`

`cost` includes:

- `source` — currently always `measured`
- `wall_ms` — measured wall-clock time for the invocation

## Current snapshot shape

`build_snapshot_tree()` returns a JSON-serializable object that matches
[`schema/dashboard-snapshot.schema.json`](../../schema/dashboard-snapshot.schema.json).
`build_snapshot()` returns the equivalent `DashboardSnapshot` dataclass tree
for in-process use.
`validate_snapshot_tree()` returns a `ValidationResult` for any candidate tree
you want to accept or reject explicitly.

The top-level snapshot object includes:

- `root`
- `generated_at`
- `state`
- `alert_counts`
- `cycle_health`
- `active_work`
- `conversations`
- `cost`
- `alerts`
- `recent_activity`

### `state`

Current values:

- `active`
- `stuck`
- `idle`

### `cycle_health`

Fields:

- `coordinator_process_status`: `up` or `down`
- `coordinator_process_count`
- `work_status`
- `open_work_count`
- `freshest_run_output_age_seconds`
- `running_runs`
- `queued_goals`
- `eligible_queued`
- `blocked_queued`
- `last_coordinator_event_age_seconds`
- `watchdog_seconds`
- `poll_interval_seconds`

### `active_work[]`

Each `GoalEntry` includes:

- `goal_id`
- `goal_type`
- `status`
- `plant`
- `priority`
- `age_seconds`
- `bucket`
- `blocked_reason`
- `current_run_id`
- `current_run_status`
- `run_age_seconds`
- `run_silence_age_seconds`
- `run_event_count`
- `run_lifecycle_phase`
- `submitted_at`

Current `bucket` values:

- `running`
- `active`
- `eligible`
- `blocked`

Current `blocked_reason` values:

- `dependency`
- `not_before`
- `conversation_hop`
- `plant_busy`
- `unassigned`

### `conversations[]`

Each `ConversationEntry` includes:

- `conversation_id`
- `last_activity_age_seconds`
- `session_ordinal`
- `session_turns`
- `mode`
- `pressure_band`
- `pressure_score`
- `needs_hop`
- `pending_hop`
- `active_run_id`
- `active_phase`
- `last_turn_run_id`

Current `mode` values:

- `fresh-start`
- `fresh-handoff`
- `resumed`

### `cost`

`CostSummary` includes:

- `today_input_tokens`
- `today_output_tokens`
- `today_cache_read_tokens`
- `unknown_completed_runs`
- `latest_completed_run`
- `top_input_runs`
- `active_driver_models`

Token totals count only today's completed runs with `cost.source == "provider"`.
Dashboard invocation records are separate and are not included in these
provider-token totals.

### `alerts[]`

Each `Alert` includes:

- `severity`
- `subject`
- `identifier`
- `reason`
- `age_seconds`

Current `severity` values:

- `critical`
- `warning`
- `info`

Current `subject` values:

- `run`
- `goal`
- `conversation`
- `cycle`
- `cost`

### `recent_activity[]`

Each `RecentEvent` includes:

- `ts`
- `event_type`
- `goal_id`
- `goal_type`
- `goal_subtype`
- `conversation_id`
- `run_id`
- `reason`
- `checkpoint_id`
- `hop_outcome`
- `age_seconds`
- `from_status`
- `to_status`

The dashboard shows at most the newest `8` filtered activity records.

## Current render shape

`build_render_tree()` returns a JSON-serializable object that matches
[`schema/dashboard-render.schema.json`](../../schema/dashboard-render.schema.json).
`render_dashboard()` returns exactly `"\n".join(render_tree["text_lines"])`
for the same `snapshot`, `width`, and `height`.
`validate_render_tree()` returns a `ValidationResult` for any candidate render
tree you want to accept or reject explicitly.

The top-level render object includes:

- `generated_at`
- `width`
- `height_limit`
- `layout`
- `header`
- `truncated`
- `panels`
- `rows`
- `text_lines`

Current `layout` values:

- `two-column`
- `stacked`

Current panel keys:

- `cycle_health`
- `active_work`
- `conversations`
- `cost`
- `alerts`
- `recent_activity`

Each `panels.<key>` object includes:

- `title`
- `body_lines`

`rows` lists the panel keys in current render order. Today that means:

- `two-column`: `["cycle_health", "alerts"]`,
  `["active_work", "recent_activity"]`,
  `["conversations", "cost"]`
- `stacked`: `["cycle_health"]`, `["active_work"]`, `["conversations"]`,
  `["cost"]`, `["alerts"]`, `["recent_activity"]`

If `truncated` is `true`, the last `text_lines` entry is the truncation
marker. `header` must match the first `text_lines` entry.

## Reason codes you may receive on dashboard validation

`system.dashboard.validate_snapshot_tree(data)` returns these named reasons on
rejection:

| Code | What it means |
|------|----------------|
| `INVALID_SHAPE` | The top-level payload is not a JSON object |
| `INVALID_DASHBOARD_SNAPSHOT` | The payload does not match [`schema/dashboard-snapshot.schema.json`](../../schema/dashboard-snapshot.schema.json); `detail` names the first failing path |
| `DASHBOARD_VALIDATOR_UNAVAILABLE` | The validator could not load `jsonschema` or the snapshot schema file |

`system.dashboard.validate_render_tree(data)` returns these named reasons on
rejection:

| Code | What it means |
|------|----------------|
| `INVALID_SHAPE` | The top-level payload is not a JSON object |
| `INVALID_DASHBOARD_RENDER` | The payload does not match [`schema/dashboard-render.schema.json`](../../schema/dashboard-render.schema.json) or the current row/header/truncation invariants; `detail` names the first failing condition |
| `DASHBOARD_RENDER_VALIDATOR_UNAVAILABLE` | The validator could not load `jsonschema` or the render schema file |

`system.validate.validate_dashboard_invocation(data)` returns these named
reasons on rejection:

| Code | What it means |
|------|----------------|
| `INVALID_SHAPE` | The top-level payload is not a JSON object |
| `UNKNOWN_DASHBOARD_INVOCATION_FIELD` | The record contains a field outside the contract |
| `MISSING_REQUIRED_FIELD` | A required top-level or `cost.*` field is absent |
| `INVALID_TIMESTAMP` | `started_at` or `completed_at` is not ISO 8601 UTC |
| `INVALID_DASHBOARD_INVOCATION` | The record does not satisfy the current id, actor, timing, mode, outcome, render-count, or cost invariants |

## How to use it safely

- Use the dashboard for orientation, not as the final authority for recovery or
  mutation decisions.
- If a row matters, confirm it against the underlying goal, run, conversation,
  or event record before acting.
- Treat `recent_activity` as history only. It is not the source of truth for
  current state.
- Treat missing rows conservatively: the readers skip malformed JSON lines and
  some unreadable files rather than failing loudly.

## Current failure and limitation behavior

- `--refresh` must be greater than `0` or the CLI exits with stderr text.
- There is still no CLI JSON mode; machine-readable access is library-only via
  `build_snapshot_tree()` and `build_render_tree()`.
- If invocation observability cannot be written, the CLI reports that error on
  stderr; a start failure aborts the command, while a finish failure reports
  the error after the render attempt.
- Coordinator-process detection depends on `/proc`, so non-Linux environments
  can show `coordinator_process_status = "down"` even when work exists.
