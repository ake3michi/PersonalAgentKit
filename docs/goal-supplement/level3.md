# Goal Supplements And Dispatch Packets — Agent Contract

This document is self-contained. An agent must be able to follow it without
consulting any other source.

---

## What you may do

- Read `<runtime-root>/goals/<goal-id>.json` to inspect the durable goal.
- Read `<runtime-root>/goals/supplements/<goal-id>.jsonl` to inspect already-recorded
  supplements.
- Read `<runtime-root>/runs/<run-id>/dispatch-packet.json` when your run has one.
- Submit a durable non-`converse` goal with `system.submit.submit_goal()`.
- Append a pre-dispatch supplement with
  `system.submit.append_goal_supplement()`.

## What you must never do

- Rewrite a queued goal's `body` just to fold in later clarifications.
- Write directly to `<runtime-root>/goals/supplements/<goal-id>.jsonl`.
- Write directly to `<runtime-root>/runs/<run-id>/dispatch-packet.json`.
- Append a supplement after the target goal has left `queued`.

The original goal body is the immutable base task. Later constraints belong in
supplements only when the task itself is still the same task.

---

## When to use a supplement

Use a supplement when all of the following are true:

- the target goal is still `queued`
- the goal came from a conversation and still has supplement policy enabled
- the original goal body is still correct
- the new information is a clarification, constraint, or addendum rather than
  a materially different task

If the task itself changed, submit a replacement goal instead of rewriting the
existing one through supplements.

When `system.submit.submit_goal()` is called from a `converse` run for durable
non-`converse` work, the runtime auto-tags the new goal with conversation
origin metadata and supplement policy. That is what makes the goal eligible for
`append_goal_supplement()` later.

---

## Appending a supplement

Use the public helper:

```python
from system.submit import append_goal_supplement

result, supplement_id = append_goal_supplement(
    goal_id,
    {
        "kind": "clarification",
        "content": "Preserve the current API shape.",
    },
)

if not result.ok:
    raise RuntimeError(result.reason)
```

### Payload you may provide

| Field | Required | Meaning |
|-------|----------|---------|
| `kind` | Yes | Short category such as `clarification` or `constraint` |
| `content` | Yes | Non-empty text that should accompany the immutable goal body |
| `actor` | No | Defaults to the current plant when available |
| `source` | No | Defaults to the current conversation and message when available |
| `source_goal_id` | No | Defaults to the current goal ID when available |
| `source_run_id` | No | Defaults to the current run ID when available |

If the current environment does not provide a plant or conversation source,
provide the missing `actor` and/or `source` explicitly or the helper will
reject the supplement.

### Persisted supplement shape

The authoritative schema is
[`schema/goal-supplement.schema.json`](../../schema/goal-supplement.schema.json).
The system writes a record with these fields:

| Field | Meaning |
|-------|---------|
| `id` | System-assigned supplement ID: `supp-<compact-ts>-<suffix>` |
| `goal` | Target goal ID |
| `ts` | Append timestamp |
| `actor` | Plant or agent that appended the supplement |
| `source` | Conversation reference for the later clarification |
| `kind` | Supplement category |
| `content` | Supplement text |
| `source_goal_id` | Optional source goal ID from the current run |
| `source_run_id` | Optional source run ID from the current run |

Supplements are append-only. Existing records are never edited in place.

---

## Dispatch packets

The system, not the agent, creates the dispatch packet for an eligible goal
just before launch. The packet freezes the exact task body plus the exact set
of supplements that were present at the dispatch cutoff.

The authoritative schema is
[`schema/dispatch-packet.schema.json`](../../schema/dispatch-packet.schema.json).
The packet contains:

| Field | Meaning |
|-------|---------|
| `goal_id` | Goal being dispatched |
| `run_id` | Run receiving the packet |
| `cutoff` | Dispatch-time cutoff timestamp |
| `origin` | Conversation origin for the durable goal |
| `goal_body` | Immutable original goal body |
| `supplement_count` | Number of included supplements |
| `supplement_chars` | Total character count across included supplement content |
| `supplements` | The included supplement records, sorted by `(ts, id)` |

Only supplements with `ts <= cutoff` are included. Later supplements are for a
future run, not the already-materialized packet.

The driver injects this packet into your prompt as:

- `# Task` with the immutable `goal_body`
- `# Pre-dispatch supplements` with any included supplements

If a packet exists, you may read `<runtime-root>/runs/<run-id>/dispatch-packet.json`, but you
must treat it as system-owned and read-only.

---

## Rejection reason codes you may receive

These are the named reason codes `system.submit.append_goal_supplement()`
returns to callers:

| Code | What to do |
|------|-----------|
| `GOAL_NOT_FOUND` | Check the target goal ID |
| `GOAL_NOT_QUEUED` | Stop; the goal already left `queued` and can no longer accept supplements |
| `SUPPLEMENTS_NOT_ALLOWED` | Only use supplements on conversation-origin durable goals with supplement policy |
| `INVALID_SHAPE` | Pass a JSON object payload |
| `INVALID_ACTOR` | Provide a valid lowercase actor name, or call from a run that sets `PAK2_CURRENT_PLANT` |
| `INVALID_SOURCE` | Provide a valid conversation source object with `kind: "conversation"` and a valid `conversation_id` |
| `EMPTY_KIND` | Provide a non-empty `kind` |
| `EMPTY_CONTENT` | Provide non-empty `content` |
| `INVALID_SOURCE_GOAL` | Fix `source_goal_id` to a valid goal ID |
| `INVALID_SOURCE_RUN` | Fix `source_run_id` to a valid run ID |
| `SUPPLEMENT_SOURCE_MISMATCH` | Use the same source conversation as the target goal's origin |

`INVALID_DISPATCH_PACKET` is not a helper return code. It is a system-side
dispatch failure if stored supplement data becomes inconsistent before launch.
