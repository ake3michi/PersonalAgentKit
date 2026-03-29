# Automatic Tend Submission — Agent Contract

This document is self-contained.

---

## What this capability means for you

The system may create a bounded `tend` goal without an explicit operator
request when background state needs review. Today that automatic behavior is
limited to exactly two trigger kinds:

- `run_failure`
- `queued_attention_needed`

If you are dispatched on one of those tend goals, treat the trigger metadata as
the reason for the survey. Do not expand it into a broader autonomy loop.

---

## When the system auto-submits one of these tends

### `run_failure`

The system auto-submits a tend when:

- a run closes as `failure`, `killed`, `timeout`, or `zero_output`
- and the failed goal type is neither `converse` nor `tend`

The tend goal records:

- `tend.trigger_kinds = ["run_failure"]`
- `tend.trigger_goal = <failed-goal-id>`
- `tend.trigger_run = <failed-run-id>`

### `queued_attention_needed`

The system auto-submits a tend when:

- a non-`converse`, non-`tend` goal has stayed `queued` for at least
  `2 * poll_interval`
- and, after the current dispatch pass has already claimed the work it is
  about to run, the goal is either still unassigned or otherwise still eligible
  to dispatch now

Timing matters here. The coordinator asks this question only after it has
already taken the ordinary dispatch step for that pass. So aged queued work is
not a tend trigger just because it is being dispatched now or because it is the
next queued item behind same-pass claimed work on its plant.

The tend goal records:

- `tend.trigger_kinds = ["queued_attention_needed"]`
- `tend.trigger_goal = <queued-goal-id>`

`trigger_run` is usually absent for this family because the trigger is a queued
goal, not a failed run.

---

## When the system must not auto-submit one

Do not assume every failure or wait creates a tend. The current runtime does
not auto-submit one when:

- the failed goal type is `converse`
- the failed goal type is `tend`
- the run succeeded
- another tend goal is already `queued` or `running`
- an aged queued goal was already selected to dispatch in the current pass
- an aged queued goal is only waiting behind same-pass claimed work on its
  plant
- a queued goal is only waiting on `not_before`
- a queued goal is only waiting on an open dependency
- a queued goal is only waiting behind a legitimately running plant
- the work is ordinary conversation hop or checkpoint work
- the only new information is a queued-goal supplement or dispatch packet
- the garden is merely idle

Queued conversation clarifications belong to supplements, not to tend.

---

## Priority and lane meaning

Automatic tend stays in the normal non-`converse` lane. There is no separate
tend lane and no converse-lane bypass.

Under the current runtime ordering:

- operator-requested tends default to `priority: 6`
- automatic background tends default to `priority: 4`

The purpose of those numbers is only to keep operator-requested tend ahead of
automatic tend inside the same normal lane. They do not let tend outrank the
converse lane.

---

## What you can read on the tend goal and in events

On the tend goal record, inspect the `tend` object:

```json
{
  "trigger_kinds": ["run_failure"],
  "trigger_goal": "220-fix-broken-worker",
  "trigger_run": "220-fix-broken-worker-r1"
}
```

The event log then records:

- `TendStarted` after `RunStarted`
- `TendFinished` before `RunFinished`

Those tend events include the same trigger metadata plus tend-run summary data
such as:

- `follow_up_goal_count`
- `follow_up_goals`
- `memory_updated`
- `operator_note_written`
- `operator_note_path`

The generic goal/run events for that tend goal also carry the tend metadata via
the normal goal event metadata helper.

`operator_note_path` now comes from the run's validated `tend_survey`
operator-message record. Conversation-origin operator-request tends can point
at a top-level reply delivery copy; no-origin tends point under
`inbox/<garden-name>/notes/`.

---

## How to behave inside the tend run

Use the trigger metadata to bound the survey:

- for `run_failure`, review the failed goal/run and choose the next already
  authorized step
- for `queued_attention_needed`, review why the queue needed attention and
  whether any authorized follow-up is warranted

Do not assume the triggering queued goal is still blocked by the time you run.
It may already have dispatched or closed. Treat the trigger as a reason to
survey, not as proof that the original blockage still exists. If the queue now
shows only ordinary waiting state, record that explicitly rather than
re-escalating it as a fresh policy problem.

If the survey reveals one clear, already authorized, non-speculative next step,
submit one bounded follow-up goal during the tend run. Otherwise say explicitly
that no such follow-up exists.
