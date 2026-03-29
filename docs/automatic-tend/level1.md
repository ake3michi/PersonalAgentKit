# Automatic Tend Submission — What It Is

Sometimes the garden should review itself without waiting for the operator to
ask. The current system does that in one narrow way: it can automatically queue
one bounded `tend` goal when background work fails, or when background queued
work has aged long enough to need attention after the coordinator has already
taken its ordinary dispatch step.

This is not a general autonomy loop. It is one small safety valve on top of the
normal goal system.

---

## When it happens

The current runtime has exactly two automatic tend trigger families:

- **Background run failure**: a non-`converse`, non-`tend` run closes with
  `failure`, `killed`, `timeout`, or `zero_output`.
- **Queued attention needed**: a non-`converse`, non-`tend` goal has stayed
  `queued` for at least `2 * poll_interval` and, after the current dispatch
  pass has already selected and claimed the work it is about to run, is either
  still unassigned or otherwise still ready to dispatch now.

If a tend goal is already `queued` or `running`, the system does not create a
second one. It reuses the active tend instead.

---

## When waiting is expected

For `queued_attention_needed`, timing matters. The coordinator asks that
question only after it has already claimed the work it can dispatch in the
current pass.

That means ordinary waiting is not a tend trigger, even for aged queued work.
No automatic tend is submitted just because a queued goal:

- was already selected to dispatch in the current pass
- is simply next in line behind same-pass claimed work on the same plant
- is waiting on `not_before`, an open dependency, or a legitimately busy plant

Automatic tend is only for the leftover abnormal cases: aged queued work that
is still unassigned, or still immediately dispatchable, even after those
ordinary waits have been accounted for.

---

## What the system records

When the system auto-submits one of these tend goals, it records why:

- the tend goal gets `tend.trigger_kinds`
- it also records `trigger_goal` when a specific goal caused the review
- it records `trigger_run` for the run-failure family

When that tend run starts and finishes, the event log also records
`TendStarted` and `TendFinished` so you can see why the tend existed and what
it produced.

---

## What it does not do

This capability is intentionally narrow.

It does **not**:

- run periodic or idle-time tending
- add a special tend scheduler lane
- trigger on queued-goal conversation supplements
- trigger on ordinary conversation hop or checkpoint work
- trigger just because an aged queued goal was already selected in the current
  pass
- trigger just because an aged queued goal is waiting behind same-pass claimed
  work on its plant
- trigger just because a queued goal is waiting on `not_before`, a still-open
  dependency, or a legitimately busy plant
- trigger just because a queued goal has become the next ordinary dispatch
  after waiting behind already-claimed work

Those are either deterministic waits or separate capabilities.

For the exact engineer and agent contracts, see
[`Level 2`](./level2.md) and [`Level 3`](./level3.md).
