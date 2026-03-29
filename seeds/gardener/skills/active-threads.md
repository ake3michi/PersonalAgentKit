# Active Threads

Use this skill when your plant needs one canonical current-work record.

## Purpose

Keep `plants/<plant>/memory/active-threads.json` current so later runs can
answer:

- what threads are active now
- how those threads relate
- what changed recently

without reconstructing the answer from scattered goals, runs, and
conversation handoffs.

## When To Use It

- when more than one meaningful thread is active
- when a bounded review materially narrows or reopens a thread
- when a new durable thread is introduced from conversation or autonomous
  follow-up
- before answering a state/progress question that would otherwise require
  reconstructing the full recent history

## Procedure

1. Read `plants/<plant>/memory/active-threads.json` if it already exists.
2. Read only the recent artifacts needed to explain the current threads.
3. Keep the thread list small: active, near-done, blocked, or watched work
   only.
4. Record explicit relations between threads instead of burying them in prose.
5. Record concrete evidence for each thread and recent update.
6. Write the artifact through:

```python
from system.active_threads import write_active_threads
```

7. Keep `MEMORY.md` for durable identity, policy, and long-lived learnings.
   Put fast-changing thread state in `active-threads.json`.

## Environment Notes

- This protocol is bounded and non-autonomous. It does not justify periodic
  self-start behavior.
- The canonical schema lives in `schema/active-threads.schema.json`.
- The agent contract lives in `docs/active-threads/level3.md`.
