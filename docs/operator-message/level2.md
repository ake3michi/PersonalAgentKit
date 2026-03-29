# Operator Messages — Engineer Reference

## What this capability is

Operator Messages is the current system-owned emission surface for exactly two
gardener-authored note classes:

- `tend_survey`
- `recently_concluded`

It exists to remove the ambiguity caused by writing raw markdown files directly
into the same top-level reply directory that `pak2 chat` treats as ordinary
conversation replies.

This capability does not change startup status or startup garden-reply
representation. Those paths remain separate in this slice.

## Record and validation surfaces

Persisted records:

- [`schema/operator-message.schema.json`](../../schema/operator-message.schema.json)
- `<runtime-root>/runs/<run-id>/operator-messages.jsonl`

Validation boundaries:

- `system.validate.validate_operator_message_request(data: dict) -> ValidationResult`
- `system.validate.validate_operator_message_record(data: dict) -> ValidationResult`

Runtime entry points:

| Entry point | Layer | Responsibility |
|-------------|-------|----------------|
| `system.operator_messages.emit_tend_survey()` | Public API | Emit one gardener-authored tend survey note with validated routing. |
| `system.operator_messages.emit_recently_concluded()` | Public API | Emit one gardener-authored recently concluded note with validated routing. |
| `system.operator_messages.read_operator_message_records()` | Read helper | Load durable per-run emission records. |
| `system.driver._emit_tend_finished()` | Tend observability | Derive `operator_note_written` and `operator_note_path` from the validated tend survey records for that run instead of diffing raw inbox files. |

## Routing rules

The routing decision is not caller-selected in this slice. The system derives
it from the current goal's `origin` metadata.

| Current goal origin | Canonical human record | Delivery policy | Filesystem path behavior |
|---------------------|------------------------|-----------------|--------------------------|
| `origin.kind == "conversation"` | Append a `sender: "garden"` message to the originating conversation | `reply_copy` | Attempt a top-level reply-surface delivery copy in `<runtime-root>/inbox/<garden-name>/` |
| No origin | No conversation append | `out_of_band_note` | Write one note file under `<runtime-root>/inbox/<garden-name>/notes/` |

The top-level reply directory is therefore reserved for transcript-backed
delivery copies only. Background or otherwise no-origin notes move to the
dedicated `notes/` subdirectory so `pak2 chat` does not render them as ordinary
replies.

## Persisted record shape

Each successful emission appends one validated JSON object to
`runs/<run-id>/operator-messages.jsonl`.

Required fields:

- `schema_version`
- `kind`
- `sender`
- `transcript_policy`
- `delivery_policy`
- `emitted_at`
- `source_goal_id`
- `source_run_id`

Conditional fields:

- `origin` for conversation-backed emissions
- `conversation_message_id` for conversation-backed emissions
- `delivery_path` when a filesystem file was written

Current policy values:

- `sender = "garden"`
- `transcript_policy = "canonical"` for conversation-backed emissions
- `transcript_policy = "none"` for no-origin emissions
- `delivery_policy = "reply_copy"` for conversation-backed emissions
- `delivery_policy = "out_of_band_note"` for no-origin emissions

## Request validation and context rules

The public emitters validate the request before any write:

- only `tend_survey` and `recently_concluded` are accepted
- only `sender: "garden"` is accepted
- `source_goal_id` and `source_run_id` must come from the current run context
- `tend_survey` is only valid from a current `tend` goal
- `recently_concluded` is only valid from a durable non-`converse`,
  non-`tend` goal

If a conversation-backed emission is requested, the origin conversation must
exist and still be open.

## Failure modes

Named rejections currently include:

| Code | Meaning |
|------|---------|
| `MISSING_RUNTIME_CONTEXT` | The current goal/run context was not available in the environment |
| `OPERATOR_MESSAGE_NOT_AUTHORIZED` | A non-gardener caller attempted to use this gardener-only slice |
| `UNKNOWN_OPERATOR_MESSAGE_FIELD` | An unsupported field was provided to the request or record validator |
| `INVALID_OPERATOR_MESSAGE_KIND` | The request named an unsupported message kind |
| `INVALID_OPERATOR_MESSAGE_SENDER` | The request named an unsupported sender |
| `INVALID_OPERATOR_MESSAGE_CONTEXT` | The requested kind does not match the current goal type or conversation state |
| `INVALID_OPERATOR_MESSAGE_ORIGIN` | The provided origin linkage was malformed |
| `INVALID_OPERATOR_MESSAGE_POLICY` | The persisted record does not match the allowed routing policy combinations |
| `INVALID_OPERATOR_MESSAGE_PATH` | The persisted `delivery_path` does not match the allowed reply or notes surfaces |
| `CONVERSATION_NOT_FOUND` | The conversation-linked emission referenced a missing conversation |
| `EMPTY_CONTENT` | The emitted content was empty or whitespace-only |
| `IO_ERROR` | Writing the durable record or an out-of-band note failed |

## Current scope boundary

This slice intentionally does not:

- change startup status emission
- change the startup seed's raw garden-reply path
- introduce a general-purpose message helper for unrelated classes
- change broader chat UX beyond keeping out-of-band notes off the ordinary
  top-level reply scan

## Grounding in implementation and tests

Implementation:

- [`system/operator_messages.py`](../../system/operator_messages.py)
- [`system/driver.py`](../../system/driver.py)
- [`system/validate.py`](../../system/validate.py)

Focused tests:

- [`tests/test_operator_messages.py`](../../tests/test_operator_messages.py)
- [`tests/test_tend.py`](../../tests/test_tend.py)
