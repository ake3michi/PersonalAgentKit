# Active Threads — Agent Contract

This document is self-contained.

---

## What this artifact is for

Use `Active Threads` when you need one canonical current-work record for your
plant.

The artifact path is:

`plants/<your-plant>/memory/active-threads.json`

It is for current work only. It is not a replacement for goals, runs, events,
or durable identity memory.

## When to refresh it

Refresh it when any of these happen:

- a thread is newly created
- a thread is materially narrowed, reopened, blocked, or nearly closed
- one thread now depends on or explains another
- a status/progress answer would otherwise require reconstruction from many
  goals and runs

Do not turn this into a periodic self-start loop. Update it only during
ordinary authorized work.

## How to write it

Use the helper:

```python
from system.active_threads import write_active_threads
```

The helper emits `ActiveThreadsRefreshStarted`, validates the artifact, writes
it to your `memory/` directory on success, and then emits
`ActiveThreadsRefreshFinished`.

Optional read helpers:

```python
from system.active_threads import read_active_threads, active_threads_path
```

Schema contract:

[`schema/active-threads.schema.json`](../../schema/active-threads.schema.json)

Validator:

`system.validate.validate_active_threads(data)`

Event contract:

- `ActiveThreadsRefreshStarted` in `<runtime-root>/events/coordinator.jsonl`
- `ActiveThreadsRefreshFinished` in `<runtime-root>/events/coordinator.jsonl`

## Required top-level fields

- `schema_version`: must be `1`
- `captured_at`: ISO 8601 UTC timestamp
- `captured_by_run`: run id that refreshed the artifact
- `plant`: the owning plant name
- `summary`: one short current-state summary
- `threads`: current thread list
- `recent_updates`: compact recent change list

## Required thread fields

- `id`
- `title`
- `state`
- `priority`
- `last_changed_at`
- `summary`
- `current_focus`
- `next_step`
- `related_thread_ids`
- `evidence`

Allowed `state` values:

- `active`
- `near_done`
- `watching`
- `blocked`

Allowed `priority` values:

- `primary`
- `secondary`
- `background`

## Required recent-update fields

- `ts`
- `summary`
- `thread_ids`
- `evidence`

## Rules you must follow

- Keep thread ids stable while the thread remains live.
- Keep `related_thread_ids` pointing only to live thread ids that actually
  exist in the file.
- `recent_updates[].thread_ids` may point either to live thread ids in the
  file or to a recently closed thread id kept only in the update log.
- Do not relate a thread to itself.
- Keep evidence concrete: goal ids, run artifacts, docs, or policy files.
- Keep the artifact compact. This is a current-work index, not a full history.
- Keep `MEMORY.md` focused on durable identity and learnings; put fast-changing
  thread state here instead.

## Failure codes

The validator/write path can reject with these named reasons:

- `INVALID_ACTIVE_THREADS_SHAPE`
- `UNKNOWN_ACTIVE_THREADS_FIELD`
- `MISSING_REQUIRED_FIELD`
- `INVALID_ACTIVE_THREADS_SCHEMA_VERSION`
- `INVALID_TIMESTAMP`
- `INVALID_ACTIVE_THREADS_RUN`
- `INVALID_ACTIVE_THREADS_PLANT`
- `EMPTY_ACTIVE_THREADS_SUMMARY`
- `INVALID_ACTIVE_THREADS_THREAD`
- `UNKNOWN_ACTIVE_THREADS_THREAD_FIELD`
- `INVALID_ACTIVE_THREADS_THREAD_ID`
- `DUPLICATE_ACTIVE_THREAD_ID`
- `EMPTY_ACTIVE_THREADS_THREAD_FIELD`
- `INVALID_ACTIVE_THREADS_THREAD_STATE`
- `INVALID_ACTIVE_THREADS_THREAD_PRIORITY`
- `INVALID_ACTIVE_THREADS_THREAD_RELATION`
- `INVALID_ACTIVE_THREADS_THREAD_EVIDENCE`
- `SELF_REFERENTIAL_ACTIVE_THREAD`
- `UNKNOWN_ACTIVE_THREAD_RELATION`
- `INVALID_ACTIVE_THREADS_UPDATE`
- `UNKNOWN_ACTIVE_THREADS_UPDATE_FIELD`
- `EMPTY_ACTIVE_THREADS_UPDATE_SUMMARY`
- `INVALID_ACTIVE_THREADS_UPDATE_THREAD_IDS`
- `INVALID_ACTIVE_THREADS_UPDATE_EVIDENCE`
- `IO_ERROR`

When the finish event outcome is not `success`, `active_threads_reason` carries
the same named `ValidationResult.reason` code returned by the helper.

## Refresh outcomes

`ActiveThreadsRefreshFinished.active_threads_outcome` is one of:

- `success`
- `validation_rejected`
- `io_error`
