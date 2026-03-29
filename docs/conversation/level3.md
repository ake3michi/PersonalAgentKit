# Conversation — Agent Contract

This document is self-contained. An agent must be able to follow it without
consulting any other source.

---

## What you may do

- **Persist the chosen garden name** via `system.garden.set_garden_name()`.
- **Read** any conversation meta at `<runtime-root>/conversations/<id>/meta.json`.
- **Read** any conversation's messages at `<runtime-root>/conversations/<id>/messages.jsonl`.
- **Read** any conversation handoff summary at `<runtime-root>/conversations/<id>/SUMMARY.md`.
- **Read** any conversation's turn or checkpoint records at
  `<runtime-root>/conversations/<id>/turns.jsonl` and
  `<runtime-root>/conversations/<id>/checkpoints.jsonl`.
- **Append messages** via `system.conversations.append_message()`.
- **Update** a conversation's `session_id`, `context_at`, or `status` via
  `system.conversations.update_conversation()`.
- **Prepare a manual checkpoint** via
  `system.conversations.prepare_conversation_checkpoint()`.
- **Request a future hop** via `system.conversations.request_conversation_hop()`.
- **Submit** a `converse` goal via `system.submit.submit_goal()`.
- **Submit** a normal non-`converse` goal when a conversation should hand work
  off to the autonomic lane instead of doing it inline.

## What you must never do

- Write directly to `<runtime-root>/conversations/<id>/meta.json`.
- Write directly to `<runtime-root>/conversations/<id>/messages.jsonl`.
- Write directly to `<runtime-root>/conversations/<id>/SUMMARY.md`.
- Set `id`, `started_at`, or `channel` on a conversation you did not create.
- Create a new conversation without going through `open_conversation()`.

For the filesystem channel, outbound replies go to
`<runtime-root>/inbox/<garden-name>/`. `<runtime-root>` is read from
`PAK2.toml` `[runtime].root` and defaults to the garden root when unset.
`<garden-name>` is read from `[garden].name` and defaults to `garden` when
unset.

---

## Submitting a converse goal

Converse goals are submitted the same way as other goals. The `conversation_id`
field routes the driver to the conversation handler.

```python
from system.submit import submit_goal

result, goal_id = submit_goal({
    "type":            "converse",
    "submitted_by":    "operator",
    "body":            "The operator's message text",
    "assigned_to":     "gardener",
    "priority":        7,
    "conversation_id": "1-conversation-topic",
})
```

### Required fields for converse goals

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Must be `"converse"` |
| `submitted_by` | string | `"operator"` or agent name |
| `body` | string | The message text. Non-empty. |
| `conversation_id` | string | ID of the open conversation to continue |

### Fields you may provide

All standard goal optional fields (`priority`, `assigned_to`, `not_before`,
`depends_on`) are supported. `conversation_id` is the only converse-specific
field.

### Fields you must not provide

`id`, `status`, `submitted_at`, `closed_reason`. These are system-assigned.

---

## Conversation record

`<runtime-root>/conversations/<id>/meta.json`:

```json
{
  "id":                "1-hello",
  "status":            "open",
  "channel":           "filesystem",
  "channel_ref":       "inbox/operator",
  "presence_model":    "async",
  "started_by":        "operator",
  "participants":      ["operator", "garden"],
  "started_at":        "2026-03-19T10:00:00Z",
  "last_activity_at":  "2026-03-19T11:00:00Z",
  "context_at":        "2026-03-19T11:00:00Z",
  "session_id":        "session-abc123",
  "compacted_through": "msg-20260319104500-gar-a1b2",
  "session_ordinal":   2,
  "session_turns":     4,
  "checkpoint_count":  1,
  "last_checkpoint_id": "ckpt-20260319105000-1a2b",
  "last_checkpoint_at": "2026-03-19T10:50:00Z",
  "last_turn_mode":    "resumed",
  "last_turn_run_id":  "42-converse-turn-r1",
  "last_pressure":     null,
  "post_reply_hop":    null,
  "pending_hop":       null
}
```

The authoritative machine-readable contracts are:

- [`schema/conversation.schema.json`](../../schema/conversation.schema.json) for `meta.json`
- [`schema/message.schema.json`](../../schema/message.schema.json) for `messages.jsonl`
- [`schema/conversation-turn.schema.json`](../../schema/conversation-turn.schema.json) for `turns.jsonl`
- [`schema/conversation-checkpoint.schema.json`](../../schema/conversation-checkpoint.schema.json) for `checkpoints.jsonl`

The write-boundary validators are `validate_conversation()`,
`validate_message()`, `validate_conversation_turn()`, and
`validate_conversation_checkpoint()` in `system.validate`.

**`context_at`** — the timestamp through which state and activity diffs have
been injected. Read this to know what the garden already knows. Do not set it
manually — the driver advances it after each run.

**`started_by`** — who opened the conversation. Fresh filesystem startup
threads created by `pak2 cycle` use `"system"` so the record does not imply the
operator initiated them.

**`session_id`** — the opaque backend session/thread ID from the most recent
converse run. If present, the driver passes it back to the selected driver
plugin on the next run so the backend can resume conversation continuity
(for example `claude --resume <session_id>` or `codex exec resume <session_id>`).
You may read this field but must not modify it directly.

After a manual checkpoint, or after a queued post-reply hop completes,
`session_id` is intentionally cleared. That tells the driver to start a fresh
model session on the next message.

**`compacted_through`** — the last message already represented in
`SUMMARY.md`. Messages after this marker remain raw tail context for the next
fresh session.

**`session_ordinal` / `session_turns`** — visible lineage for the active
backend session. These are garden-maintained counters so chat and run records
can say whether a turn resumed or fresh-started.

**`last_pressure`** — the latest garden-local context-pressure snapshot. It is
heuristic, not a provider context-window guarantee.

**`pending_hop`** — a stored request for the conversation to end the current
turn on a fresh backend session. If a live session exists, the driver answers
first, then converts this into queued `post_reply_hop` work. The driver also
clears it when it notices the conversation is already awaiting a fresh start.

**`post_reply_hop`** — the queued checkpoint job that will execute after the
reply already sent to the operator. It is distinct from `pending_hop`, which
is only a request.

Terminology:

- `hop` is the request or queued decision to switch the next turn onto a fresh backend session.
- `checkpoint` is the durable record written when that hop actually runs.
- `handoff` is the summary content used by the next fresh session.

---

## Message record

Each line in `<runtime-root>/conversations/<id>/messages.jsonl`:

```json
{
  "id":              "msg-20260319100000-ope-a1b2",
  "conversation_id": "1-hello",
  "ts":              "2026-03-19T10:00:00Z",
  "sender":          "operator",
  "content":         "Hello garden, what is your current state?",
  "channel":         "filesystem",
  "reply_to":        null
}
```

Messages are immutable once written. Do not modify existing lines.

`turns.jsonl` and `checkpoints.jsonl` are also system-written durable records.
Read them freely, but only write them through `append_conversation_turn()` and
`write_conversation_checkpoint()`, which enforce the turn/checkpoint schemas.

---

## Conversation lifecycle

| Status | Meaning |
|--------|---------|
| `open` | Active. New messages are accepted. |
| `closed` | Concluded. No further messages expected. |
| `archived` | Retained for history. Inactive. |

Valid transitions (enforced by validator):

```
open → closed
open → archived
closed → archived
```

There is no re-opening. If a closed conversation receives a new message, the
somatic loop creates a new conversation.

---

## What the driver provides to a converse run

When your goal has `conversation_id` set, the driver builds your prompt as:

1. **Garden motivation** — `MOTIVATION.md`
2. **Plant context** — your `memory/MEMORY.md` (or seed if absent)
3. **Conversation handoff** — `SUMMARY.md`, if present
4. **Session status** — turn mode, context pressure, lineage, and any hop
   request carried into this turn
5. **State diff** — files in your `memory/`, `skills/`, `knowledge/` modified
   since `context_at`
6. **Activity diff** — coordinator events (goals, runs) since `context_at`
7. **Message history** — either:
   - all prior messages in the conversation, oldest first, or
   - if `SUMMARY.md` is present, only the raw tail after `compacted_through`
8. **New message** — the inbound message you are responding to
9. **Instruction** — respond to the message; your reply will be delivered
   through the channel

On the first `pak2 cycle` after bootstrap, the CLI may ensure the filesystem
reply surface exists, create one system-started filesystem conversation,
deliver one startup note from recorded bootstrap facts on that surface, and
record it as conversation history only after delivery succeeds, before
ordinary queued work dispatch begins. While that same system-started
conversation has not yet received an operator message, the coordinator may
append additional `sender: "system"` startup updates for the first non-`converse`
run, but only when they restate already-recorded lifecycle facts such as run
start, run finish, and newly observed filesystem note paths. If that run
creates one or more new filesystem reply notes during the same window, the
coordinator may also append matching `sender: "garden"` messages whose bodies
represent the substantive note text in-thread and identify the source run ID
plus original note path. That startup history is regular conversation history
by the time the operator sends a later message; the driver does not synthesize
an extra prefix onto that later reply.

The prompt also carries an execution policy for converse runs:

- Treat the conversation as the somatic layer.
- Default to delegation for substantive work.
- If the operator's request would require edits, tests, research, extended
  inspection, or other durable execution, submit a normal non-`converse`
  goal instead of doing that work inside the converse run.
- If the choice is unclear, delegate.

The diff is marked with `[Since your last exchange in this conversation: ...]`.
If nothing changed, the diff is omitted.

After some turns, the driver may queue a checkpoint follow-up. When pressure or
an explicit hop request says the current backend session should end, the driver
still launches the operator-facing reply in that session, records and delivers
the reply, and only then submits a separate `post_reply_hop` goal. That goal
later resumes the same backend session, asks the model for a handoff summary,
writes a checkpoint record, clears `session_id`, and leaves the conversation
ready for a fresh next turn.

## Preparing a manual checkpoint

Use this when you want the next message in an existing conversation to start
from a fresh model session instead of `--resume`.

```python
from system.conversations import prepare_conversation_checkpoint

result = prepare_conversation_checkpoint(
    "1-hello",
    "# Conversation Handoff Summary\n\n- Operator prefers natural continuity.",
    "msg-20260319104500-gar-a1b2",
)
if not result.ok:
    raise RuntimeError(result.reason)
```

This writes `SUMMARY.md`, records `compacted_through`, clears `session_id`,
and appends a checkpoint record. The conversation itself stays open and keeps
the same ID. `prepare_conversation_handoff()` remains a compatibility alias
for older callers.

---

## Appending a reply message

After generating a response, the driver appends your reply. If you are handling
a conversation run, do not append messages yourself — the driver does this.

If you need to append a message outside a converse run (e.g. a tend run that
checks in on a conversation):

```python
from system.conversations import append_message

result, msg_id = append_message(
    "1-hello",
    "garden",
    "The garden is healthy. Three goals completed since last check.",
    _conv_dir=root / "conversations",
)
if not result.ok:
    # result.reason is a named code (see below)
    raise RuntimeError(result.reason)
```

For gardener-authored `tend_survey` and `recently_concluded` notes, do not
use this lower-level append path directly. Use
[`Operator Messages`](../operator-message/level3.md) so routing and durable
recording stay consistent.

---

## Rejection reason codes

| Code | What to do |
|------|-----------|
| `CONVERSATION_NOT_FOUND` | Check the conversation ID; it may not exist or may be closed |
| `MISSING_REQUIRED_FIELD` | Add the missing field |
| `INVALID_STATUS` | Use one of: `open`, `closed`, `archived` |
| `INVALID_PRESENCE_MODEL` | Use one of: `sync`, `async` |
| `INVALID_TIMESTAMP` | Use ISO 8601 UTC format: `2026-03-19T12:00:00Z` |
| `UNKNOWN_CONVERSATION_FIELD` | Remove any unsupported field from `meta.json` writes |
| `INVALID_CONVERSATION_FIELD` | Fix the bad conversation field type or identifier |
| `INVALID_CONVERSATION_PRESSURE` | Fix the shape of `last_pressure` |
| `INVALID_CONVERSATION_HOP` | Fix the shape of `pending_hop` |
| `INVALID_POST_REPLY_HOP` | Fix the shape of `post_reply_hop` |
| `UNKNOWN_MESSAGE_FIELD` | Remove any unsupported field from message writes |
| `INVALID_MESSAGE_FIELD` | Fix the bad message field type or identifier |
| `UNKNOWN_TURN_FIELD` | Remove any unsupported field from turn writes |
| `INVALID_TURN_FIELD` | Fix the bad top-level turn field type or identifier |
| `INVALID_TURN_LINEAGE` | Fix the `lineage` object on the turn record |
| `INVALID_TURN_HOP` | Fix the `hop` object on the turn record |
| `INVALID_TURN_PRESSURE` | Fix the `pressure` object on the turn record |
| `UNKNOWN_CHECKPOINT_FIELD` | Remove any unsupported field from checkpoint writes |
| `INVALID_CHECKPOINT_FIELD` | Fix the bad checkpoint field type, identifier, or `summary_path` |
| `INVALID_CHECKPOINT_PRESSURE` | Fix the `pressure` object on the checkpoint record |
| `EMPTY_CONTENT` | Message content must not be empty or whitespace-only |
| `MESSAGE_NOT_FOUND` | `compacted_through` must match an existing message in the conversation |
| `INVALID_SHAPE` | Data must be a JSON object |

---

## Reading conversation state safely

You may read `<runtime-root>/conversations/<id>/meta.json` directly. Trust `status` as the
source of truth. If `status` is `closed` or `archived`, the conversation is
concluded.

You may read `<runtime-root>/conversations/<id>/messages.jsonl` by reading lines and parsing
each as JSON. Skip blank lines and lines that fail to parse — the file may be
partially written during a concurrent append.
