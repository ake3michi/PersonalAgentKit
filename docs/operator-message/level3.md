# Operator Messages — Agent Contract

This document is self-contained.

---

## What this capability is for

Use this capability when the gardener needs to emit one of these two
operator-facing note classes:

- `tend_survey`
- `recently_concluded`

Do not write those note classes directly into `inbox/<garden-name>/` anymore.
Use the system-owned emission surface instead so routing and durable recording
stay consistent.

Startup status and startup garden-reply representation are not part of this
surface.

---

## Public API

```python
from system.operator_messages import emit_recently_concluded, emit_tend_survey

result, record = emit_tend_survey("The garden is healthy.\n")
if not result.ok:
    raise RuntimeError(result.reason)
```

```python
from system.operator_messages import emit_recently_concluded

result, record = emit_recently_concluded(
    "The bounded slice finished cleanly.\n"
)
if not result.ok:
    raise RuntimeError(result.reason)
```

The functions inspect the current goal/run environment and choose the routing
policy for you. In this slice they are gardener-only.

---

## Routing behavior

If the current goal has `origin.kind == "conversation"`:

- the system appends a `sender: "garden"` message to that originating
  conversation
- that conversation append is the canonical human record
- the system then attempts a delivery copy on the top-level reply surface

If the current goal has no origin:

- no conversation append occurs
- the system writes an out-of-band note under
  `<runtime-root>/inbox/<garden-name>/notes/`

That means:

- top-level `inbox/<garden-name>/*.md` is for transcript-backed delivery copies
  only
- `inbox/<garden-name>/notes/*.md` is for out-of-band notes only

---

## Durable record

Every successful emission appends one JSON record to:

`<runtime-root>/runs/<run-id>/operator-messages.jsonl`

The current record schema is:

```json
{
  "schema_version": 1,
  "kind": "tend_survey",
  "sender": "garden",
  "origin": {
    "kind": "conversation",
    "conversation_id": "1-hello",
    "message_id": "msg-20260326200000-ope-abcd",
    "ts": "2026-03-26T20:00:00Z"
  },
  "transcript_policy": "canonical",
  "delivery_policy": "reply_copy",
  "emitted_at": "2026-03-26T20:00:01Z",
  "source_goal_id": "130-operator-message-slice",
  "source_run_id": "130-operator-message-slice-r1",
  "delivery_path": "inbox/garden/20260326T200001-1-hello.md",
  "conversation_message_id": "msg-20260326200001-gar-wxyz"
}
```

For no-origin out-of-band notes:

- `origin` is absent
- `conversation_message_id` is absent
- `transcript_policy = "none"`
- `delivery_policy = "out_of_band_note"`
- `delivery_path` points into `inbox/<garden-name>/notes/`

---

## Context rules

- `emit_tend_survey()` is only valid when the current goal type is `tend`
- `emit_recently_concluded()` is only valid for current goal types other than
  `converse` and `tend`
- the current goal and run ids must be available in the environment
- the current caller must be `gardener` in this slice

If a conversation-backed emission is requested and the origin conversation does
not exist or is not open, the emission is rejected.

---

## What you may read

- `<runtime-root>/runs/<run-id>/operator-messages.jsonl`
- the originating conversation record when the message is conversation-backed
- the emitted note file when `delivery_path` is present

## What you must never do

- write `tend_survey` or `recently_concluded` directly into
  `inbox/<garden-name>/`
- write directly into `inbox/<garden-name>/notes/` for those classes
- edit `operator-messages.jsonl` by hand

The system owns the emission, routing, and durable recording surface.

---

## Named rejection codes

| Code | What to do |
|------|------------|
| `MISSING_RUNTIME_CONTEXT` | Ensure the current goal/run environment is present |
| `OPERATOR_MESSAGE_NOT_AUTHORIZED` | Use this slice only from gardener runs |
| `INVALID_OPERATOR_MESSAGE_KIND` | Use only `tend_survey` or `recently_concluded` |
| `INVALID_OPERATOR_MESSAGE_SENDER` | Use only the gardener-owned sender |
| `INVALID_OPERATOR_MESSAGE_CONTEXT` | Match the message kind to the current goal type and open-conversation requirement |
| `INVALID_OPERATOR_MESSAGE_ORIGIN` | Fix the origin linkage object |
| `INVALID_OPERATOR_MESSAGE_POLICY` | Fix the persisted record's routing fields |
| `INVALID_OPERATOR_MESSAGE_PATH` | Keep delivery paths on the reply surface or notes subdirectory only |
| `CONVERSATION_NOT_FOUND` | Use a real originating conversation id |
| `EMPTY_CONTENT` | Provide non-empty markdown content |
| `IO_ERROR` | Retry only after checking the filesystem state |
