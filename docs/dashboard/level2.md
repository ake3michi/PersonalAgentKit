# Live Observability Dashboard — Engineer Reference

## What this capability is

Live Observability Dashboard is the current read-only terminal surface for
composing cycle health, active work, conversation state, cost, alerts, and
recent coordinator activity into one view.

The live operator entry point is:

```bash
pak2 dashboard --root DIR [--refresh SECS] [--once]
```

In a TTY, the command refreshes in place every `--refresh` seconds unless
`--once` is set. When stdout is not a TTY, it always falls back to a single
render and exits.

The read path still rescans the existing source-of-truth files on every
refresh, but each invocation now also leaves its own audit trail:

- `DashboardInvocationStarted` / `DashboardInvocationFinished` in
  `<runtime-root>/events/coordinator.jsonl`
- one terminal cost record at `<runtime-root>/dashboard/invocations/<id>.json`

Runtime churn for this capability lives under `<runtime-root>/...`, resolved
through `system.garden.garden_paths()`. Authored plant-local state such as
`plants/...` remains at the garden root.

## Live boundary entry points

| Entry point | Layer | Responsibility |
|-------------|-------|----------------|
| `system.cli.cmd_dashboard(args)` | CLI surface | Validates `--refresh`, chooses one-shot vs live mode, records invocation start/finish observability, sizes the terminal, and repeatedly calls the snapshot/renderer pair. |
| `system.dashboard_invocations.start_dashboard_invocation(root, mode=..., refresh_seconds=..., tty=...) -> (ValidationResult, DashboardInvocationContext \| None)` | Invocation start boundary | Emits `DashboardInvocationStarted` and returns the invocation context used by the finish path. |
| `system.dashboard_invocations.finish_dashboard_invocation(context, outcome=..., render_count=..., error_detail=...) -> ValidationResult` | Invocation finish boundary | Validates and writes `<runtime-root>/dashboard/invocations/<id>.json`, then emits `DashboardInvocationFinished`. |
| `system.validate.validate_dashboard_invocation(data) -> ValidationResult` | Invocation record validator | Validates a candidate dashboard invocation record against [`schema/dashboard-invocation.schema.json`](../../schema/dashboard-invocation.schema.json) plus the current timing/outcome invariants. |
| `system.dashboard.build_snapshot(root, now=None, watchdog_seconds=..., poll_interval=...) -> DashboardSnapshot` | Read model | Scans the garden root and returns the current in-memory dashboard snapshot. |
| `system.dashboard.build_snapshot_tree(root, now=None, watchdog_seconds=..., poll_interval=...) -> dict` | Machine-readable contract | Returns the JSON-serializable snapshot tree that matches [`schema/dashboard-snapshot.schema.json`](../../schema/dashboard-snapshot.schema.json). |
| `system.dashboard.validate_snapshot_tree(data) -> ValidationResult` | Validation boundary | Validates a candidate snapshot tree against [`schema/dashboard-snapshot.schema.json`](../../schema/dashboard-snapshot.schema.json) and returns named rejection codes instead of raising. |
| `system.dashboard.build_render_tree(snapshot, width=120, height=None) -> dict` | Machine-readable render contract | Returns the JSON-serializable render tree that matches [`schema/dashboard-render.schema.json`](../../schema/dashboard-render.schema.json) and is equivalent to the final text produced by `render_dashboard()`. |
| `system.dashboard.validate_render_tree(data) -> ValidationResult` | Render validation boundary | Validates a candidate render tree against [`schema/dashboard-render.schema.json`](../../schema/dashboard-render.schema.json) plus the current layout/truncation invariants and returns named rejection codes instead of raising. |
| `system.dashboard.render_dashboard(snapshot, width=120, height=None) -> str` | Renderer | Formats the snapshot into the current terminal layout, truncating to `height` when requested. |

## Current snapshot and render contracts

The dashboard now has aligned snapshot and render surfaces:

- [`system.dashboard.build_snapshot()`](../../system/dashboard.py) returns the
  in-memory dataclass tree used by the renderer.
- [`system.dashboard.build_snapshot_tree()`](../../system/dashboard.py)
  returns the machine-readable snapshot tree described by
  [`schema/dashboard-snapshot.schema.json`](../../schema/dashboard-snapshot.schema.json).
- [`system.dashboard.validate_snapshot_tree()`](../../system/dashboard.py)
  validates any candidate snapshot tree against that schema and returns a
  non-raising `ValidationResult`.
- [`system.dashboard.build_render_tree()`](../../system/dashboard.py)
  returns the machine-readable terminal render tree described by
  [`schema/dashboard-render.schema.json`](../../schema/dashboard-render.schema.json).
- [`system.dashboard.validate_render_tree()`](../../system/dashboard.py)
  validates any candidate render tree against that schema plus the current
  row-order, header, and truncation invariants.
- [`system.dashboard.render_dashboard()`](../../system/dashboard.py)
  returns the final terminal text by joining the render tree's `text_lines`.

The in-process dataclass surface is represented by the dataclasses in
[`system/dashboard.py`](../../system/dashboard.py):

- `DashboardSnapshot`
- `CycleHealth`
- `GoalEntry`
- `ConversationEntry`
- `CostSummary`
- `CostRun`
- `Alert`
- `RecentEvent`

There is currently:

- no persisted dashboard snapshot file
- no CLI JSON output mode
- one persisted dashboard invocation record per completed CLI session

`snapshot.state` is the same value as `snapshot.cycle_health.work_status`, and
today those values are:

- `active`
- `stuck`
- `idle`

## Source-of-truth and derived data

| Need | Source | Current behavior |
|------|--------|------------------|
| Goal/current-work truth | `<runtime-root>/goals/*.json`, `<runtime-root>/runs/*/meta.json` | Open work comes from goal/run records, not from the event log. |
| Queue eligibility | `system.coordinator.find_eligible(...)` | Uses the live coordinator lane rules, including blocked conversation hops. |
| Run liveness | `<runtime-root>/runs/<run-id>/events.jsonl` mtime, else run `started_at` | Uses the same silence signal as the watchdog via `_run_last_activity_at()`. |
| Coordinator presence | `/proc/<pid>/cmdline` and `/proc/<pid>/cwd` | Best-effort Linux-only scan for `pak2 cycle` / `system.cli` processes attached to the same garden root. |
| Conversations | `<runtime-root>/conversations/*/meta.json`, latest `turns.jsonl` record | Uses the latest turn when present, else falls back to conversation metadata. |
| Recent activity | `<runtime-root>/events/coordinator.jsonl` | Filtered to events backed by the current root, deduped for repeated run events, and cleaned up to prefer dedicated hop/checkpoint events over duplicate generic hop lifecycle lines. |
| Cost | Terminal `<runtime-root>/runs/*/meta.json` records completed on the current UTC date | Totals count only `cost.source == "provider"` runs; non-provider terminal runs increase `unknown_completed_runs`. |
| Dashboard invocation audit | `<runtime-root>/events/coordinator.jsonl`, `<runtime-root>/dashboard/invocations/*.json` | Each CLI session records start/finish events plus one measured wall-time cost record for the invocation itself. |

## Panels and current invariants

### Header

The header shows:

- `generated_at`
- absolute garden root
- current derived state
- coordinator process status
- alert counts by severity

### Cycle Health

Current fields:

- `coordinator_process_status`: `up` or `down`
- `coordinator_process_count`
- `work_status`
- `open_work_count`
- `running_runs`
- `queued_goals`
- `eligible_queued`
- `blocked_queued`
- `freshest_run_output_age_seconds`
- `last_coordinator_event_age_seconds`
- `watchdog_seconds`
- `poll_interval_seconds`

### Active Work

The dashboard currently shows up to `8` open goals, sorted by:

1. bucket order: `running`, `active`, `eligible`, `blocked`
2. `submitted_at` ascending
3. `goal_id`

Current bucket meanings:

- `running`: goal status is `running`
- `active`: goal status is `dispatched`, `completed`, or `evaluating`
- `eligible`: goal is queued and dispatch-eligible now
- `blocked`: goal is queued but not eligible now

Current blocked reasons:

- `dependency`
- `not_before`
- `conversation_hop`
- `plant_busy`
- `unassigned`

### Conversations

The dashboard currently shows up to `5` open conversations. The row derives:

- `mode` from the latest turn when present, else from conversation metadata
- pressure from the latest turn when present, else `last_pressure`
- active converse phase from the running converse run's lifecycle metadata

Current mode values are:

- `fresh-start`
- `fresh-handoff`
- `resumed`

### Cost

The current cost panel shows:

- today's provider-reported input, output, and cache-read token totals
- the latest completed provider-cost run today
- the top `3` provider-cost runs today by input tokens
- count of today's completed runs with non-provider cost source
- active `driver/model` pairs from running runs

This is a visibility surface, not a billing ledger.
Dashboard invocation wall-time records are separate and do not roll into these
provider-token totals.

### Alerts

Alerts are derived in memory on every snapshot. They are not persisted.
Only one alert per `(subject, identifier)` survives; the higher-severity or
more recent candidate wins.

Current severities are `critical`, `warning`, and `info`.

Current subjects are:

- `run`
- `goal`
- `conversation`
- `cycle`
- `cost`

Current alert behavior:

- `critical`
  - running run silent for at least `watchdog_seconds`
  - goal marked `running` without a running run
  - queued goal is eligible but still queued for more than `2 * poll_interval`
  - cycle is `stuck`, there are no active runs, and open work has no recent progress
- `warning`
  - running run silent for at least `0.6 * watchdog_seconds`
  - queued goal has been `unassigned` for more than `2 * poll_interval`
  - queued goal has been waiting on `plant_busy` for more than `2 * poll_interval`
  - open conversation needs a hop or is under `high` / `critical` pressure
  - recent run closed with `failure`, `killed`, `timeout`, or `zero_output`
- `info`
  - queued goal is waiting on `not_before`
  - conversation has a pending hop request
  - recent run cost source is `unknown`

### Recent Activity

The dashboard shows the newest `8` filtered coordinator events first. It uses
the event log for recency and provenance only, not for current-state truth.
Dashboard invocation start/finish events now appear here as ordinary recent
activity lines.

## Current render contract

`build_render_tree()` returns the machine-readable render object described by
[`schema/dashboard-render.schema.json`](../../schema/dashboard-render.schema.json).
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

Current `layout` values are:

- `two-column`
- `stacked`

Current panel keys are:

- `cycle_health`
- `active_work`
- `conversations`
- `cost`
- `alerts`
- `recent_activity`

`panels.<key>.body_lines` holds the pre-frame lines for that panel. `rows`
describes the current panel grouping order:

- `two-column`: `["cycle_health", "alerts"]`,
  `["active_work", "recent_activity"]`,
  `["conversations", "cost"]`
- `stacked`: `["cycle_health"]`, `["active_work"]`, `["conversations"]`,
  `["cost"]`, `["alerts"]`, `["recent_activity"]`

`text_lines` is the final rendered output split into lines.
`render_dashboard(snapshot, width, height)` is exactly
`"\n".join(build_render_tree(snapshot, width, height)["text_lines"])`.
`header` is the first `text_lines` entry.
When `truncated` is `true`, the last `text_lines` entry is the current
truncation marker.

## Failure and limitation surface

The current dashboard failure surface is narrow and mostly best-effort:

- `pak2 dashboard --refresh <= 0` prints
  `error: --refresh must be greater than 0` to stderr and exits `1`.
- If dashboard invocation start/finish observability cannot be written, the CLI
  prints an error to stderr; a start failure exits immediately, while a finish
  failure leaves the already rendered output intact but still reports the
  observability error.
- Missing optional files usually degrade to empty panels or partial data rather
  than raising.
- Malformed JSON lines in coordinator activity or conversation turn files are
  skipped silently by the local readers.
- If `/proc` is unavailable or inaccessible, coordinator process detection
  reports `down`.

### Snapshot-tree validator

If you need a named accept/reject boundary for the machine-readable snapshot
tree, call `system.dashboard.validate_snapshot_tree(data)`. It checks the
payload against
[`schema/dashboard-snapshot.schema.json`](../../schema/dashboard-snapshot.schema.json)
and returns a `ValidationResult`; it does not raise on ordinary validation
failure.

Current rejection codes:

| Code | Source | Meaning |
|------|--------|---------|
| `INVALID_SHAPE` | `validate_snapshot_tree()` | The payload is not a JSON object |
| `INVALID_DASHBOARD_SNAPSHOT` | `validate_snapshot_tree()` | The payload does not conform to [`schema/dashboard-snapshot.schema.json`](../../schema/dashboard-snapshot.schema.json); `detail` names the first failing path |
| `DASHBOARD_VALIDATOR_UNAVAILABLE` | `validate_snapshot_tree()` | The validator could not load `jsonschema` or the snapshot schema file |

`build_snapshot_tree()` does not call `validate_snapshot_tree()` automatically.
Use the validator only when you need an explicit contract check or a named
rejection surface.

### Render-tree validator

If you need a named accept/reject boundary for the machine-readable terminal
render contract, call `system.dashboard.validate_render_tree(data)`. It checks
the payload against
[`schema/dashboard-render.schema.json`](../../schema/dashboard-render.schema.json)
and the current layout/truncation invariants, and returns a `ValidationResult`;
it does not raise on ordinary validation failure.

Current rejection codes:

| Code | Source | Meaning |
|------|--------|---------|
| `INVALID_SHAPE` | `validate_render_tree()` | The payload is not a JSON object |
| `INVALID_DASHBOARD_RENDER` | `validate_render_tree()` | The payload does not conform to [`schema/dashboard-render.schema.json`](../../schema/dashboard-render.schema.json) or the current row/header/truncation invariants; `detail` names the first failing condition |
| `DASHBOARD_RENDER_VALIDATOR_UNAVAILABLE` | `validate_render_tree()` | The validator could not load `jsonschema` or the render schema file |

`build_render_tree()` does not call `validate_render_tree()` automatically.
Use the validator only when you need an explicit contract check or a named
rejection surface.

### Dashboard invocation record validator

If you need a named accept/reject boundary for the persisted dashboard
invocation record, call
`system.validate.validate_dashboard_invocation(data)`. It checks the payload
against [`schema/dashboard-invocation.schema.json`](../../schema/dashboard-invocation.schema.json)
plus the current timing/outcome invariants and returns a `ValidationResult`.

Current rejection codes:

| Code | Source | Meaning |
|------|--------|---------|
| `INVALID_SHAPE` | `validate_dashboard_invocation()` | The payload is not a JSON object |
| `UNKNOWN_DASHBOARD_INVOCATION_FIELD` | `validate_dashboard_invocation()` | The record contains a field outside the contract |
| `MISSING_REQUIRED_FIELD` | `validate_dashboard_invocation()` | A required top-level or `cost.*` field is absent |
| `INVALID_TIMESTAMP` | `validate_dashboard_invocation()` | `started_at` or `completed_at` is not ISO 8601 UTC |
| `INVALID_DASHBOARD_INVOCATION` | `validate_dashboard_invocation()` | The payload violates the current id, actor, timing, mode, outcome, render-count, or cost invariants |

## Grounding in live implementation and tests

Implementation:

- [`system/cli.py`](../../system/cli.py)
- [`system/dashboard.py`](../../system/dashboard.py)
- [`system/dashboard_invocations.py`](../../system/dashboard_invocations.py)
- [`schema/dashboard-invocation.schema.json`](../../schema/dashboard-invocation.schema.json)
- [`schema/dashboard-render.schema.json`](../../schema/dashboard-render.schema.json)
- [`schema/dashboard-snapshot.schema.json`](../../schema/dashboard-snapshot.schema.json)

Focused coverage:

- [`tests/test_dashboard.py`](../../tests/test_dashboard.py)
- [`tests/test_dashboard_invocations.py`](../../tests/test_dashboard_invocations.py)
- [`tests/test_cli_init.py`](../../tests/test_cli_init.py)
