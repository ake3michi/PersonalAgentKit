# Goal Supplements And Dispatch Packets — Engineer Reference

## What this capability is

Goal supplements keep a durable goal body immutable while still allowing later
conversation turns to add bounded clarifications before dispatch. Dispatch
packets freeze the exact pre-dispatch view that the driver will hand to the
agent for one run.

The capability is only active for queued, conversation-originated,
non-`converse` goals whose `pre_dispatch_updates.policy` is `"supplement"`.

## Persisted contracts

This capability has two machine-readable record contracts:

- [`schema/goal-supplement.schema.json`](../../schema/goal-supplement.schema.json)
  for append-only supplement records at `<runtime-root>/goals/supplements/<goal-id>.jsonl`
- [`schema/dispatch-packet.schema.json`](../../schema/dispatch-packet.schema.json)
  for the frozen packet at `<runtime-root>/runs/<run-id>/dispatch-packet.json`

The write-boundary validators are:

- `system.validate.validate_goal_supplement(data: dict) -> ValidationResult`
- `system.validate.validate_dispatch_packet(data: dict) -> ValidationResult`

## Live boundary entry points

| Entry point | Layer | Responsibility |
|-------------|-------|----------------|
| `system.submit.append_goal_supplement(goal_id, data)` | Public API | Fills default `actor`, `source_goal_id`, and `source_run_id` from the current run when available, fills default `source` from the current conversation when available, then forwards to the goal-store boundary. |
| `system.goals.append_goal_supplement(goal_id, data, ...)` | Goal-store boundary | Rejects ineligible goals, persists one supplement record, and emits `GoalSupplemented`. |
| `system.goals.materialize_dispatch_packet(goal, run_id, cutoff, ...)` | Dispatch freeze boundary | Freezes the goal body plus all supplements at or before `cutoff`, validates the packet, writes `dispatch-packet.json`, and emits `DispatchPacketMaterialized`. |
| `system.driver.dispatch(goal, run_id)` | Production caller | Invokes `materialize_dispatch_packet()` before launching the agent and injects the packet's supplements into the prompt with `_build_pre_dispatch_supplements_section()`. |

## Invariants

- The original goal `body` stays immutable. Later clarifications belong in
  supplements, not in-place goal rewrites.
- Supplements are append-only records. Existing supplement lines are never
  edited or deleted.
- A supplement is accepted only while the target goal is still `queued`.
- The supplement source conversation must match the goal's conversation origin.
- A dispatch packet includes only supplements with `ts <= cutoff`.
- Dispatch packets sort supplements by `(ts, id)` before writing.
- `supplement_count` must equal the number of packet supplements.
- `supplement_chars` must equal the total character length of all packet
  supplement `content` strings.

## Failure modes

### `append_goal_supplement()` rejections

These are the helper-visible reason codes callers should design around:

| Reason code | Source | When it occurs |
|-------------|--------|----------------|
| `GOAL_NOT_FOUND` | `system.goals.append_goal_supplement()` | The target goal ID does not exist |
| `GOAL_NOT_QUEUED` | `system.goals.append_goal_supplement()` | The goal already left `queued` |
| `SUPPLEMENTS_NOT_ALLOWED` | `system.goals.append_goal_supplement()` | The goal is not a conversation-origin durable goal using supplement policy |
| `INVALID_SHAPE` | `system.submit.append_goal_supplement()` / `validate_goal_supplement()` | The payload is not a JSON object |
| `INVALID_ACTOR` | `validate_goal_supplement()` | `actor` is missing after defaulting or is not a valid plant/agent name |
| `INVALID_SOURCE` | `validate_goal_supplement()` | `source` is missing after defaulting or does not match the conversation reference shape |
| `EMPTY_KIND` | `validate_goal_supplement()` | `kind` is empty or whitespace-only |
| `EMPTY_CONTENT` | `validate_goal_supplement()` | `content` is empty or whitespace-only |
| `INVALID_SOURCE_GOAL` | `validate_goal_supplement()` | `source_goal_id` is present but not a valid goal ID |
| `INVALID_SOURCE_RUN` | `validate_goal_supplement()` | `source_run_id` is present but not a valid run ID |
| `SUPPLEMENT_SOURCE_MISMATCH` | `system.goals.append_goal_supplement()` | `source.conversation_id` does not match the target goal origin |

`system.submit.append_goal_supplement()` normalizes the payload before
validation, so callers should not expect `UNKNOWN_SUPPLEMENT_FIELD`,
`INVALID_SUPPLEMENT_ID`, or `INVALID_SUPPLEMENT_GOAL` from the public helper.

### Dispatch-packet validation failures

These are the packet-side reason codes enforced when the driver freezes a run's
pre-dispatch view:

| Reason code | Source | When it occurs |
|-------------|--------|----------------|
| `INVALID_DISPATCH_PACKET` | `validate_dispatch_packet()` | Packet fields, origin metadata, counts, cutoff ordering, or goal/source matching are inconsistent |
| `INVALID_SHAPE` | `validate_dispatch_packet()` | The packet is not a JSON object |
| `MISSING_REQUIRED_FIELD` | `validate_dispatch_packet()` | A required packet field is absent |
| Any `validate_goal_supplement()` reason code | `validate_goal_supplement()` via `validate_dispatch_packet()` | A persisted supplement inside the packet is invalid; the packet validator forwards the nested supplement reason code |

If packet validation fails in production, `system.driver.dispatch()` records a
run-local error instead of launching the agent.

## Tests

- [`tests/test_goal_supplements.py`](../../tests/test_goal_supplements.py)

This suite covers:

- conversation-origin goal tagging plus supplement policy setup
- append-only supplement persistence and `GoalSupplemented` events
- malformed-payload, non-queued-goal, empty-kind, and source-mismatch
  rejections
- dispatch-packet cutoff filtering, packet persistence, event emission, and
  prompt ordering
