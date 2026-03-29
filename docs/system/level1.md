# The System — What It Is

## What is the system?

The system is the engine that turns goal submissions into completed work.

You submit a goal — a description of something you want done. The system
queues it, hands it to an agent, watches over it while it runs, and records
everything that happened when it finishes. You do not need to manage any of
this manually.

## What does it do for me?

- **Accepts goals** you submit and puts them in a queue.
- **Dispatches goals** to an agent automatically, when the agent is ready.
- **Watches active work** and detects if something gets stuck or goes silent.
- **Records outcomes** — what ran, how long it took, whether it succeeded,
  and what it cost.
- **Keeps an audit trail** — a permanent log of every significant event,
  in order, with timestamps.

## What do I need to know to use it?

What matters most:

1. **You submit goals.** The system handles everything after that.
2. **The event log tells you what happened.** It's at
   `<runtime-root>/events/coordinator.jsonl`.
   Read it to see the history of any goal.
3. **The system never edits history.** Once something is recorded, it stays.
   A failed run is not erased — it is recorded as a failure.
4. **Some background review is automatic.** If background work fails or queued
   background work needs attention, the system may queue one bounded tend goal.
   See
   [`Automatic Tend Submission`](../automatic-tend/level1.md).
5. **Some gardener notes are system-routed.** Gardener-authored
   `tend_survey` and `recently_concluded` notes use
   [`Operator Messages`](../operator-message/level1.md) instead of raw inbox
   writes.
6. **After `./pak2 init`, fresh startup is two commands.** `./pak2 genesis`
   initializes the garden and queues the first gardener goal. `./pak2 cycle`
   opens the startup conversation surface and dispatches queued work.

If a conversation already queued durable work and you need to add one more
clarification before it starts, see
[`Goal Supplements And Dispatch Packets`](../goal-supplement/level1.md).

If you want a one-screen, read-only view of current work, conversation
pressure, recent activity, and today's token spend, see
[`Live Observability Dashboard`](../dashboard/level1.md).

If you want the gardener to perform a bounded cross-run retrospective on
demand, see [`Manual Retrospective`](../retrospective/level1.md).

If you want a later plant's planting step to show up as its own durable
goal/run instead of an inline side effect, use the dedicated commissioning
surface described in [`Plant`](../plant/level1.md).

If a plant needs one compact file that says what is active now, how those
threads relate, and what changed recently, see
[`Active Threads`](../active-threads/level1.md).

## What can go wrong?

- **Your goal submission is rejected** — this means a required field was missing
  or malformed. The rejection tells you exactly what to fix.
- **A run times out** — the system detects this automatically and marks the goal
  as failed. You can retry by submitting the goal again.
- **The system is not running** — goals accumulate in the queue but are not
  dispatched. Start the coordinator to resume.
