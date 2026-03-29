# Goal Supplements And Dispatch Packets — What They Are

Sometimes you ask the garden to do durable work in a conversation. The garden
queues that work as a goal, and then you realize you need to add one more
constraint before it starts.

A **goal supplement** is how the system records that later clarification without
rewriting the original goal. A **dispatch packet** is the frozen copy of the
goal plus all accepted supplements that the agent actually sees for one run.

This gives you two things at once:

- the original request stays intact
- the run gets the queued clarifications that arrived before dispatch

---

## When this is useful

Use this capability when all of the following are true:

- the work came from a conversation and has already been queued as a durable goal
- the goal is still waiting to start
- the task is still the same task
- you only need to add a clarification, constraint, or reminder

Example: you already queued a fix goal, then add "preserve the current API
shape" before the run starts.

If the task itself changed, do not treat that as a supplement. Submit a
replacement goal instead.

---

## What the system does

1. It keeps the original goal body unchanged.
2. It records each later clarification as a separate supplement.
3. Right before dispatch, it freezes the goal body plus all supplements that
   arrived in time into one dispatch packet.
4. That packet is the exact pre-dispatch version of the work for that run.

Later supplements do not rewrite a packet that was already frozen.

---

## What you need to know to use it safely

- Supplements only work while the target goal is still `queued`.
- The later clarification must come from the same conversation as the original
  goal.
- Supplements are for clarifications and constraints, not for changing the task
  into a different task.
- The system owns the supplement log and the dispatch packet. Do not edit those
  records directly.

If you inspect the files, supplements are recorded under
`<runtime-root>/goals/supplements/<goal-id>.jsonl`, and the frozen packet for a
run is written to `<runtime-root>/runs/<run-id>/dispatch-packet.json`.

For the detailed contracts, see
[`Level 2`](./level2.md) and [`Level 3`](./level3.md).
