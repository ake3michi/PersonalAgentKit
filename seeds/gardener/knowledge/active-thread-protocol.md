# Active Thread Protocol

Status: template protocol for gardener-local current-work tracking.

## Why this exists

- `MEMORY.md` is good for durable identity and long-lived learnings, but it
  becomes noisy and stale when it tries to hold every active thread.
- Goals, runs, and conversation handoffs contain the truth, but reconstructing
  current focus from all of them is expensive.
- The garden needs one cheap, explicit layer that says what is active now,
  how threads relate, and what changed recently.

## Canonical Artifact

Path:

`plants/<plant>/memory/active-threads.json`

Schema:

`schema/active-threads.schema.json`

The artifact is plant-local. It does not replace goals, runs, events, or
dashboard state.

## State Vocabulary

Allowed thread `state` values:

- `active`
- `near_done`
- `watching`
- `blocked`

Allowed thread `priority` values:

- `primary`
- `secondary`
- `background`

## Update Rules

- Keep thread ids stable while a thread remains live.
- Record only current or near-current work.
- When a thread is no longer live, remove it from `threads` and let the most
  recent relevant change remain in `recent_updates` for a short period, using
  the same thread id even after it leaves the live list.
- Keep `recent_updates` compact and evidence-backed.
- Keep relations explicit with `related_thread_ids`.
- Use the helper `system.active_threads.write_active_threads()` so schema and
  relation checks are enforced consistently.

## Scope Boundary

- This protocol is not a periodic self-start mechanism.
- It does not authorize speculative goal submission.
- It does not replace durable review artifacts; it points at them.
- It is the current-work index only.
