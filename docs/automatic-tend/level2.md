# Automatic Tend Submission — Engineer Reference

## What this capability is

Automatic Tend Submission is the coordinator-side policy that queues one
bounded `tend` goal for background review when either:

- a background run fails, or
- a background queued goal needs attention

The current capability name is **Automatic Tend Submission**. It covers only
the live trigger pair `run_failure` and `queued_attention_needed`.

This capability is implemented on top of the existing tend helper and tend
event contracts. It does not add a new goal type, a new tend lane, or a new
schema file.

## Record and validation surfaces

This capability reuses existing contracts:

- raw tend goal payloads:
  [`schema/goal-tend.schema.json`](../../schema/goal-tend.schema.json)
- tend lifecycle events:
  [`schema/event.schema.json`](../../schema/event.schema.json)

The relevant validation boundaries are:

- `system.validate.validate_goal(data: dict) -> ValidationResult`
- `system.validate.validate_event(data: dict) -> ValidationResult`

## Live boundary entry points

| Entry point | Layer | Responsibility |
|-------------|-------|----------------|
| `system.coordinator._submit_run_failure_tend()` | Coordinator policy | Queues a bounded tend after a failed background run when the run status is one of the failed terminal states and the goal type is eligible. |
| `system.coordinator._submit_queued_attention_tend()` | Coordinator policy | Queues a bounded tend when an aged queued background goal is still unassigned or is still dispatch-eligible after the current pass has already claimed the work it is about to run. |
| `system.submit.submit_tend_goal()` | Submission helper | Deduplicates against any already queued or running tend, assigns default tend priority, and persists `tend.trigger_*` metadata. |
| `system.driver._emit_tend_started()` | Runtime observability | Emits `TendStarted` after `RunStarted` with the goal's tend metadata attached. |
| `system.driver._emit_tend_finished()` | Runtime observability | Emits `TendFinished` before `RunFinished` with summary output plus the same tend metadata. |

## Trigger family 1: `run_failure`

### Exact trigger

`system.coordinator._submit_run_failure_tend()` submits a tend only when all of
the following are true:

- the run status is `failure`, `killed`, `timeout`, or `zero_output`
- the goal record exists
- the failed goal type is not `converse`
- the failed goal type is not `tend`

The coordinator calls this helper from both places that can surface background
run failure today:

- after a synchronous or async worker closes a run
- after the watchdog kills a silent run

### Exact non-triggers

This family does not create a new tend when:

- the run status is `success`
- the goal is missing
- the failed goal type is `converse`
- the failed goal type is `tend`
- another tend goal is already `queued` or `running`

That last case is handled by `submit_tend_goal()` dedupe. The helper returns
the existing tend goal id instead of creating a second one.

### Persisted tend metadata

The submitted tend goal uses:

- `tend.trigger_kinds = ["run_failure"]`
- `tend.trigger_goal = <failed-goal-id>`
- `tend.trigger_run = <failed-run-id>`
- default `priority = 4`

## Trigger family 2: `queued_attention_needed`

### Timing model

`system.coordinator._submit_queued_attention_tend()` is intentionally a
post-selection check, not a generic "this has waited a while" check.

Both `reconcile()` and `Coordinator._tick()` call it only after the current
pass has already:

- recomputed live eligibility
- selected and claimed any work it is about to dispatch now
- built the pass-local `ignored_goal_ids` and `reserved_plants` filters

So the real question is:

- after ordinary dispatch for this pass has already been accounted for, does an
  aged queued goal still look abnormal enough to justify a bounded tend?

Expected waiting does not qualify. No tend should be submitted merely because
an aged queued goal:

- was already selected to dispatch in this pass
- is only waiting behind same-pass claimed work on the same plant
- is waiting on `not_before`, an open dependency, or a legitimately busy plant

`queued_attention_needed` is reserved for the remaining cases: aged queued work
that is still unassigned, or still immediately dispatch-eligible, after those
same-pass and deterministic waits have been removed.

### Exact trigger

`system.coordinator._submit_queued_attention_tend()` evaluates queued goals once
per reconcile/tick pass after the coordinator has already claimed any work it
is about to dispatch in that pass. A goal is a candidate only when all of the
following are true:

- `status == "queued"`
- `type` is neither `converse` nor `tend`
- its age is at least `2 * poll_interval`
- and one of these is true:
  - `assigned_to` is absent, or
  - the goal is still dispatch-eligible now after accounting for the current
    pass's claimed work

Here, "dispatch-eligible now" is the same condition produced by
`find_eligible(..., converse_only=False)`:

- `assigned_to` is set
- `not_before` is absent or elapsed
- every dependency is already closed with `closed_reason == "success"`
- the plant is free in the normal non-`converse` lane

The helper then subtracts work already claimed by the current pass:

- it ignores goals that this pass has already selected to dispatch
- it ignores goals whose plant is already claimed by other selected work in the
  same pass

If more than one candidate exists, the coordinator chooses one by the same
ordering it uses elsewhere in that lane: priority descending, then
`submitted_at` ascending.

### Exact non-triggers

This family does not create a new tend for:

- queued goals younger than `2 * poll_interval`
- queued goals of type `converse`
- queued goals of type `tend`
- queued goals already selected to dispatch in the current pass
- queued goals only waiting behind same-pass claimed plant-lane work
- queued goals waiting on `not_before`
- queued goals waiting on an open dependency
- queued goals waiting on a legitimately busy plant
- ordinary post-reply hop or checkpoint work
- queued conversation supplements or dispatch-packet materialization
- a garden that is merely idle
- a queued goal that is simply the next ordinary dispatch after waiting behind
  work already claimed in the same pass
- a garden that already has a tend goal `queued` or `running`

The focused coordinator tests encode both the same-pass exclusions and the
deterministic waits:

- `test_tick_submits_queued_attention_tend_for_aged_dispatch_eligible_goal`
- `test_tick_does_not_submit_queued_attention_tend_for_aged_goal_selected_this_pass`
- `test_tick_submits_queued_attention_tend_for_aged_unassigned_goal`
- `test_reconcile_does_not_submit_queued_attention_tend_for_goal_waiting_behind_same_pass_work`
- `test_tick_does_not_submit_queued_attention_tend_for_legitimate_waits`

### Persisted tend metadata

The submitted tend goal uses:

- `tend.trigger_kinds = ["queued_attention_needed"]`
- `tend.trigger_goal = <queued-goal-id>`
- no `tend.trigger_run`
- default `priority = 4`

One important runtime nuance: by the time the tend actually runs, the original
queued goal may already have dispatched or even closed. The trigger means
"review why this needed attention," not "guarantee the queue is still blocked."

## Emitted metadata and events

Automatic tend submission reuses the goal event metadata helper, so tend
metadata propagates onto the tend goal's lifecycle and tend-specific events.

| Surface | Fields |
|---------|--------|
| Tend goal record | `priority`, `tend.trigger_kinds`, `tend.trigger_goal`, `tend.trigger_run` when present |
| `GoalSubmitted`, `GoalTransitioned`, `RunStarted`, `RunFinished` for the tend goal | `goal_priority`, `trigger_kinds`, `trigger_goal`, `trigger_run`, plus origin/source metadata when present |
| `TendStarted` | required `goal`, `run`, `trigger_kinds`, plus `goal_priority`, `trigger_goal`, `trigger_run`, and any origin/source metadata |
| `TendFinished` | required `goal`, `run`, `follow_up_goal_count`, `memory_updated`, `operator_note_written`, plus `follow_up_goals`, `operator_note_path`, and the same tend/origin metadata. `operator_note_*` now reflects the run's validated `tend_survey` operator-message record rather than a raw inbox-file diff. |

For conversation-originated operator-requested tends, `origin`,
`conversation_id`, `source_message_id`, `source_goal_id`, and `source_run_id`
can all appear on these events. Automatic background tends usually emit only
the tend-specific fields and `goal_priority`.

## Lane placement and priority intent

Automatic Tend Submission stays inside the existing scheduler design:

- no new tend lane
- no hop-style bypass behavior for tend
- tend stays in the normal non-`converse` lane
- converse and post-reply-hop work keep their separate converse lane

The current runtime orders larger numeric priority values first within a lane.
Under that live ordering:

- operator-requested tend defaults to `priority: 6`
- auto-submitted background tend defaults to `priority: 4`

The intent is relative, not absolute:

- operator-requested tend should outrank automatic background tend
- neither tend class should outrank the converse lane

## Explicit non-goals

Automatic Tend Submission does not attempt to solve:

- periodic or idle-time autonomous tending
- a dedicated tend lane
- a queued-supplement trigger
- ordinary conversation hop or checkpoint triggers

Queued conversation clarifications remain the responsibility of the supplement
and dispatch-packet capability, not tend.

## Grounding in live implementation and tests

Implementation:

- [`system/coordinator.py`](../../system/coordinator.py)
- [`system/submit.py`](../../system/submit.py)
- [`system/tend.py`](../../system/tend.py)
- [`system/driver.py`](../../system/driver.py)

Focused tests:

- [`tests/test_coordinator.py`](../../tests/test_coordinator.py)
- [`tests/test_tend.py`](../../tests/test_tend.py)

The post-goal264 queue-attention timing contract is grounded specifically in:

- `system.coordinator._submit_queued_attention_tend()`
- `system.coordinator.reconcile()`
- `system.coordinator.Coordinator._tick()`
- `tests.test_coordinator.CoordinatorTests.test_tick_does_not_submit_queued_attention_tend_for_aged_goal_selected_this_pass`
- `tests.test_coordinator.CoordinatorTests.test_reconcile_does_not_submit_queued_attention_tend_for_goal_waiting_behind_same_pass_work`
- `tests.test_coordinator.CoordinatorTests.test_tick_does_not_submit_queued_attention_tend_for_legitimate_waits`

Garden-local tend policy overlays, when a garden chooses to keep one, live
under `plants/<plant>/knowledge/` and are not part of the `pak2 init`
template contract.
