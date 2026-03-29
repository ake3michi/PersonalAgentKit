# Run — Engineer Reference

## What a run is

A run is an immutable record of a single attempt to fulfill a goal. Once a run
reaches a terminal status, its record is never modified. Annotations (operator
notes, post-hoc analysis) are separate files, never edits to the run record.

## Schema

See [`schema/run.schema.json`](../../schema/run.schema.json).

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `N-slug-rN` format. System-assigned. |
| `goal` | string | `N-slug` format. The goal this run attempts. |
| `plant` | string | The plant that executed this run. |
| `status` | string | Current run status. Terminal statuses are immutable. |
| `started_at` | ISO 8601 UTC | Written before subprocess launches. |
| `driver` | string | Execution driver (e.g. `"codex"` or `"claude"`). |
| `model` | string | Model identifier. |

### Conditionally required fields

| Field | Required when |
|-------|--------------|
| `completed_at` | Status is terminal |
| `cost` | Status is terminal |
| `failure_reason` | Status is `failure`, `killed`, `timeout`, or `zero_output` |
| `reflection` | Status is `success` AND goal type requires it (see below) |

### Optional fields

`outputs`, `num_turns`, `worktree_baseline`

## Run ID format

```
N-slug-rN
```

Where `N-slug` is the goal ID and `N` is the attempt number (starting at 1).
A goal's first run is `42-fix-the-thing-r1`. A retry is `42-fix-the-thing-r2`.

## Statuses

| Status | Terminal? | Description |
|--------|-----------|-------------|
| `running` | No | Subprocess is live |
| `success` | Yes | Run completed |
| `failure` | Yes | Run failed |
| `killed` | Yes | Stopped by watchdog or operator |
| `timeout` | Yes | Exceeded time limit |
| `zero_output` | Yes | Completed but produced no output |

## Cost

Cost is always recorded for terminal runs. The `source` field is required:

| Source | Meaning |
|--------|---------|
| `provider` | Reported directly by the API |
| `estimated` | Computed locally from token counts |
| `unknown` | Driver did not report cost |

`unknown` is a named state, not an absence. A run whose cost cannot be
determined must record `{"source": "unknown"}`, not omit the field.

## Reflection

Reflection is required for successful runs of goal types `build`, `fix`,
`evaluate`, and `tend`. It is the agent's answer to "what was learned?" —
not a summary of what was done.

This requirement cannot be enforced by the run schema alone (the run doesn't
know the goal type). It is enforced by `validate_run_close()` in
`system/validate.py`, which cross-references the goal's type at close time.

Goal types `spike` and `research` are exempt — they produce artifacts instead.

## Run directory

Each run has its own directory at `<runtime-root>/runs/<run-id>/`:

```
<runtime-root>/runs/<run-id>/
  meta.json       ← this record (managed by the system)
  events.jsonl    ← raw agent output stream (written by the driver)
```

`meta.json` is written by the system at run start and finalized at close.
The agent never writes to `meta.json`. `events.jsonl` captures the raw
event stream from the agent subprocess, from which cost, reflection, and
transcript are derived.

For non-`converse` runs in a readable git worktree, `meta.json` may also
include a system-managed `worktree_baseline` captured before the run directory
or coordinator event files are created. It records:

- `tracked_dirty_paths`: tracked files that were already dirty at run start
- `untracked_dirty_count`: how many untracked paths already existed
- `untracked_dirty_roots`: the distinct top-level roots represented in that
  untracked set

This is intentionally path-level provenance only. It helps later review answer
"was `system/cli.py` already dirty before this run?" without claiming automatic
hunk attribution or a full authored-vs-runtime classifier.

In the current first `runtime-auto-commit` slice, split-runtime-root gardens
also get one system-managed provenance artifact at close:

```
<runtime-root>/history/commits/<run-id>.json
```

That record captures the authored `HEAD` commit and whether authored paths
outside the runtime root were `clean` or `dirty` when the run closed. The
system then commits the runtime tree inside the runtime root's nested git repo.
This does not modify `meta.json` after terminal close; the run record remains
immutable.

The close helper publishes these exact bounded outcome codes for the slice:

| Outcome code | Contract |
|-------------|----------|
| `committed` | Split-root capture succeeded: the provenance record was written and the runtime root's nested git history advanced. |
| `runtime_root_not_split` | The garden is using the legacy unified root, so this slice is intentionally inactive and no runtime-history capture is attempted. |
| `runtime_root_outside_garden` | The configured runtime root could not be related back to the garden root, so the slice stops before reading authored provenance. |
| `authored_repo_unavailable` | The authored repo `HEAD` or authored-tree clean/dirty probe could not be read, so no runtime-history record is written. |
| `record_write_failed` | Split-root capture started, but writing `<runtime-root>/history/commits/<run-id>.json` failed, so no runtime commit follows. |
| `runtime_repo_init_failed` | The provenance record was written, but creating or configuring the runtime root's nested git repo failed. |
| `runtime_repo_stage_failed` | The provenance record was written and the runtime repo exists, but staging the runtime tree failed. |
| `no_runtime_changes` | The provenance record was written and staging succeeded, but the runtime repo had no new diff to commit for this capture. |
| `runtime_repo_diff_failed` | Staging succeeded, but the cached-diff check failed unexpectedly before commit. |
| `runtime_repo_commit_failed` | The runtime-history commit command failed after staging detected changes. |
| `runtime_repo_head_unavailable` | The runtime-history commit succeeded, but the runtime repo `HEAD` could not be read back to publish the commit id. |

## Watchdog

A run in `running` status is considered alive by the mtime of
`<runtime-root>/runs/<run-id>/events.jsonl` when that file exists. Before the file exists,
the watchdog falls back to `started_at`. If the silence exceeds the
configured watchdog interval, the run is transitioned to `killed` on the
next reconciliation pass. The `failure_reason` is set to `"killed"`.

## Failure modes

| Reason code | When it occurs |
|-------------|---------------|
| `MISSING_REQUIRED_FIELD` | A required field is absent |
| `INVALID_ID_FORMAT` | `id` does not match `N-slug-rN` pattern |
| `INVALID_GOAL_FORMAT` | `goal` does not match `N-slug` pattern |
| `INVALID_PLANT_NAME` | `plant` does not match name pattern |
| `INVALID_STATUS` | `status` is not a valid run status |
| `INVALID_TIMESTAMP` | `started_at` or `completed_at` is not ISO 8601 UTC |
| `MISSING_COMPLETED_AT` | Terminal run missing `completed_at` |
| `MISSING_COST` | Terminal run missing `cost` |
| `INVALID_COST_SOURCE` | `cost.source` is not `provider`, `estimated`, or `unknown` |
| `INVALID_COST_FIELD` | A cost field has a negative value |
| `MISSING_FAILURE_REASON` | Failed run missing `failure_reason` |
| `INVALID_FAILURE_REASON` | `failure_reason` is not a valid reason code |
| `MISSING_REFLECTION` | Successful run of reflection-required type missing `reflection` |
| `INVALID_SHAPE` | Document is not a JSON object |

## Validation

- `system/validate.py:validate_run(data)` — called at run start and at close.
- `system/validate.py:validate_run_close(run, goal_type)` — called at close only.
  Cross-references goal type to enforce reflection requirement.

## Tests

`tests/test_validate.py` — `TestValidateRun` and `TestValidateRunClose` classes.
