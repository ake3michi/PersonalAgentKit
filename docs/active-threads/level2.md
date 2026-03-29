# Active Threads — Engineer Reference

## Purpose

`Active Threads` is a plant-local protocol for keeping current work legible
without expanding `MEMORY.md` into a fragile run-by-run narrative.

The canonical artifact lives at:

`plants/<plant>/memory/active-threads.json`

It is intended to answer, from one file:

- what threads are active now
- how those threads relate
- what changed recently

## Schema

See [`schema/active-threads.schema.json`](../../schema/active-threads.schema.json).

Top-level required fields:

- `schema_version`
- `captured_at`
- `captured_by_run`
- `plant`
- `summary`
- `threads`
- `recent_updates`

### Thread fields

Each thread records:

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

`state` is one of:

- `active`
- `near_done`
- `watching`
- `blocked`

`priority` is one of:

- `primary`
- `secondary`
- `background`

### Recent update fields

Each recent update records:

- `ts`
- `summary`
- `thread_ids`
- `evidence`

## Boundary

The supported write boundary is:

```python
from system.active_threads import write_active_threads
```

That helper validates the candidate artifact via:

```python
from system.validate import validate_active_threads
```

and then:

- emits `ActiveThreadsRefreshStarted` to `<runtime-root>/events/coordinator.jsonl`
- validates the candidate artifact
- writes `plants/<plant>/memory/active-threads.json` on success
- emits `ActiveThreadsRefreshFinished` with the refresh outcome

Read helpers:

```python
from system.active_threads import read_active_threads, active_threads_path
```

## Update rules

Keep the file small and current.

Refresh it when:

- a bounded review closes, reopens, or narrows a thread
- one thread spawns or absorbs another thread
- a conversation-origin request creates a new durable aim
- you need to answer a state/progress question without rereading scattered
  records

Recommended maintenance rules:

- track only live or near-live work
- allow `recent_updates` to keep a just-closed thread id briefly after that
  thread leaves `threads`
- keep `recent_updates` compact and newest-first
- record relations explicitly rather than implying them in prose
- keep `MEMORY.md` for durable identity, policy, and long-lived learnings
- use `active-threads.json` for current focus and recent change tracking

## Refresh event surface

Ordinary helper-driven refreshes now emit one bounded event pair in
`<runtime-root>/events/coordinator.jsonl`:

- `ActiveThreadsRefreshStarted`
- `ActiveThreadsRefreshFinished`

Required event fields:

- started: `plant`, `active_threads_path`
- finished: `plant`, `active_threads_path`, `active_threads_outcome`

Optional event metadata:

- `goal` / `run` when the refresh happens inside a normal run context
- `active_threads_reason` plus `detail` when the finish outcome is not
  `success`

Current `active_threads_outcome` values:

- `success`
- `validation_rejected`
- `io_error`

## Seed/template inheritance

Future gardens inherit the protocol through seed-local gardener assets in:

- `seeds/gardener/skills/active-threads.md`
- `seeds/gardener/knowledge/active-thread-protocol.md`

The current inheritance path is shared with the general seeded-plant flow:

- `system.plants.commission_seeded_plant()` is the ordinary bootstrap path.
  It commissions the plant and then calls
  `system.plants.materialize_seed_context()` to write `plants/<plant>/seed`
  plus copy seed-local `skills/` and `knowledge/` into `plants/<plant>/`.
- `system.genesis.genesis()` uses that seeded commissioning helper for a fresh
  gardener bootstrap. If the gardener record already exists, genesis refreshes
  the same seed context by calling
  `system.plants.materialize_seed_context()` directly before continuing.

That shared path is why the protocol is available in new gardens without
hand-recreating it.

## Failure modes

`validate_active_threads()` and `write_active_threads()` surface these named
rejections:

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

## Verification

Focused coverage lives in:

- `tests/test_active_threads.py`
- `tests/test_genesis.py`
- `tests/test_plants.py`
- `tests/test_cli_init.py`

`tests/test_active_threads.py` covers the happy path plus each named Active
Threads rejection code, including the ordinary refresh start/finish event
surface. `tests/test_plants.py` covers seeded commissioning writing the seed
reference plus copied skills/knowledge, and `tests/test_genesis.py` covers the
direct seed-context materialization and bootstrap behavior used at garden
startup.
