# Conversations — Engineer Reference

## What a conversation is

A conversation is a persistent, bidirectional exchange between the operator and
the garden, routed through a named channel. It is a first-class object with its
own storage, lifecycle, and state.

Storage: `<runtime-root>/conversations/<id>/meta.json` +
`<runtime-root>/conversations/<id>/messages.jsonl`; optional
`<runtime-root>/conversations/<id>/SUMMARY.md` for session handoffs;
`<runtime-root>/conversations/<id>/turns.jsonl` and
`<runtime-root>/conversations/<id>/checkpoints.jsonl`
for explicit turn and checkpoint records

Each inbound message triggers a `converse` goal, which the coordinator
dispatches like any other goal. The driver, seeing `conversation_id` on the
goal, routes to the conversation handler instead of the standard agent launcher.

---

## Schema

This capability uses four persisted record contracts:

- [`schema/conversation.schema.json`](../../schema/conversation.schema.json) for `meta.json`
- [`schema/message.schema.json`](../../schema/message.schema.json) for `messages.jsonl`
- [`schema/conversation-turn.schema.json`](../../schema/conversation-turn.schema.json) for `turns.jsonl`
- [`schema/conversation-checkpoint.schema.json`](../../schema/conversation-checkpoint.schema.json) for `checkpoints.jsonl`

The write-boundary validators are `validate_conversation()`,
`validate_message()`, `validate_conversation_turn()`, and
`validate_conversation_checkpoint()` in `system/validate.py`.

### Conversation fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `N-slug`. System-assigned. |
| `status` | string | `open`, `closed`, `archived` |
| `channel` | string | Channel type name (e.g. `"filesystem"`) |
| `channel_ref` | string | Stable endpoint identifier within the channel. For filesystem: `"inbox/operator"`. |
| `presence_model` | string | `"sync"` or `"async"` |
| `started_by` | string | Who opened the conversation. Current values: `operator`, `system`. |
| `participants` | string[] | Named participants. Default: `["operator", "garden"]` |
| `started_at` | ISO 8601 | When the conversation was opened |
| `last_activity_at` | ISO 8601 | Updated on every message append |
| `context_at` | ISO 8601 | State/activity diff cutoff for next resume |
| `session_id` | string\|null | Opaque backend session/thread ID used for continuity between turns |
| `compacted_through` | string\|null | Last message ID already absorbed into `SUMMARY.md` |
| `session_ordinal` | integer | Current backend session number within the conversation |
| `session_turns` | integer | Completed turns in the active backend session |
| `session_started_at` | ISO 8601\|null | When the active backend session began |
| `checkpoint_count` | integer | Number of recorded handoff checkpoints |
| `last_checkpoint_id` | string\|null | Most recent checkpoint record ID |
| `last_checkpoint_at` | ISO 8601\|null | When the most recent checkpoint was written |
| `last_turn_mode` | string\|null | Latest turn mode: `resumed`, `fresh-handoff`, or `fresh-start` |
| `last_turn_run_id` | string\|null | Run ID of the latest converse turn |
| `last_pressure` | object\|null | Latest context-pressure snapshot |
| `pending_hop` | object\|null | Operator/system request for the conversation to end the turn on a fresh backend session |
| `post_reply_hop` | object\|null | Queued checkpoint work that will execute after the already-sent reply when a live session must be handed off |

### Message fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `msg-<compact-ts>-<3-char-sender-prefix>-<4-char-random>` |
| `conversation_id` | string | Parent conversation ID |
| `ts` | ISO 8601 | When the message was appended |
| `sender` | string | `"operator"` for inbound; garden/plant name for outbound |
| `content` | string | Non-empty message body |
| `channel` | string | Channel type the message traveled through |
| `reply_to` | string\|null | Message ID this replies to, if applicable |

### Turn fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `turn-<compact-ts>` |
| `conversation_id` | string | Parent conversation ID |
| `run_id` | string | Converse run that produced this turn |
| `goal_id` | string | Converse goal that produced this turn |
| `ts` | ISO 8601 | When the turn completed |
| `status` | string | Terminal converse run status |
| `mode` | string | `resumed`, `fresh-handoff`, or `fresh-start` |
| `diff_present` | bool | Whether state/activity diff text was injected into the prompt |
| `lineage` | object | Session lineage snapshot for chat/dashboard surfaces |
| `pressure` | object | Context-pressure snapshot after the reply was recorded |
| `hop` | object | Whether a hop was requested, queued, performed, or failed |
| `session_id_before` | string\|null | Backend session ID used to answer this turn |
| `session_id_after` | string\|null | Backend session ID stored after the turn completed |

### Checkpoint fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `ckpt-<compact-ts>-<4-char-random>` |
| `conversation_id` | string | Parent conversation ID |
| `ts` | ISO 8601 | When the checkpoint record was written |
| `requested_by` | string | Who asked for the fresh-session checkpoint |
| `reason` | string | Why the checkpoint was taken |
| `compacted_through` | string | Last message already absorbed into the handoff summary |
| `source_session_id` | string\|null | Backend session ID being checkpointed |
| `source_session_ordinal` | integer | Session lineage number being checkpointed |
| `source_session_turns` | integer | Turn count within that session |
| `summary_path` | string | Relative archive path for the checkpoint summary |
| `run_id` | string\|null | Post-reply-hop run that created the checkpoint, if any |
| `driver` | string\|null | Driver used to create the checkpoint summary |
| `model` | string\|null | Model used to create the checkpoint summary |
| `pressure` | object\|null | Pressure snapshot that motivated the checkpoint |

---

## Lifecycle

```
open ──────────── closed
  │
  └── archived
```

`open` is the normal state. The system does not auto-close conversations —
they remain open until explicitly closed (by an agent or future automation).
`archived` is retained history with no further activity expected.

There is no terminal state in the strict goal sense — conversations can be
reopened by simply sending another message (which creates a new conversation
if the old one is closed).

---

## Channel abstraction

`Channel` (ABC in `system/channels.py`) defines three methods:

| Method | Purpose |
|--------|---------|
| `available() → bool` | Is the operator reachable right now? |
| `poll() → list[dict]` | Return new inbound messages since last poll |
| `send(conv_id, content)` | Deliver a reply |

`available()` defaults to `True` for async channels (deliver when ready),
and must be overridden for sync channels (check for active presence signal).

`FilesystemChannel` watches `<runtime-root>/inbox/operator/`, tracks seen files
in `<runtime-root>/inbox/.seen`, and delivers replies to
`<runtime-root>/inbox/<garden_name>/`. The runtime resolves `<runtime-root>`
from `PAK2.toml` `[runtime].root` and `<garden_name>` from `[garden].name`,
defaulting to the legacy root layout and the name `garden` when unset.

Migration policy for legacy reply directories:

- if `<runtime-root>/inbox/garden/` exists and
  `<runtime-root>/inbox/<garden_name>/` does not, the runtime renames the
  legacy directory to the configured one on first reply delivery
- if both directories already exist, the runtime preserves both and writes new
  replies only to `<runtime-root>/inbox/<garden_name>/`

The `channel_ref` is the stable identifier for a channel endpoint — for
filesystem it is `"inbox/operator"` relative to the runtime root, not the
individual file.
Individual files are tracked as `file` in raw message dicts and are used
only for the seen-tracking mechanism.

---

## The somatic loop

`SomaticLoop` (`system/somatic.py`) runs in a thread alongside the autonomic
`Coordinator`. It polls channels every 5 seconds.

On each tick:
1. Poll all channels for new messages
2. For each message: find the open conversation for `(channel, channel_ref)`,
   or create a new one if none exists
3. Append the message to `<runtime-root>/conversations/<id>/messages.jsonl`
4. Submit a `converse` goal with `conversation_id` set

The coordinator dispatches the `converse` goal to the gardener plant. The
driver handles the rest.

On the first `pak2 cycle` of a newly bootstrapped garden, before any run has
started, the CLI may ensure the filesystem reply surface exists, open one
filesystem conversation with `started_by: "system"`, deliver a compact startup
note built from recorded bootstrap facts on that surface, and record that note
in conversation history only after delivery succeeds. While that same
system-started conversation has not yet received an operator message, the
coordinator may also append short system status updates for the first durable
run, limited to recorded lifecycle facts such as run start, run finish, and
newly observed filesystem note paths. If that first durable run writes a
filesystem reply note during the same startup window, the coordinator also
records a separate `sender: "garden"` conversation message that restates the
substantive note body with explicit provenance to the source run and note
path. The first later operator message still reuses that same open
conversation because somatic matching keys on `(channel, channel_ref)`.

Converse runs are not the default place to perform durable work. Their job is
to interpret the operator's message, answer brief questions inline, and submit
normal non-`converse` goals when the request would require edits, tests,
research, extended inspection, or other substantive execution. If the choice
is unclear, delegate.

---

## Context diffs

The driver injects a context diff on each message. It computes:

**State diff** (`compute_state_diff`): files added or modified in the plant's
`memory/`, `skills/`, `knowledge/` directories since `context_at`.

**Activity diff** (`compute_activity_diff`): coordinator events since
`context_at` — goals submitted, runs finished, goals closed, plants
commissioned. Converse submissions are labeled as conversation-turn queue
events rather than durable work, and the currently executing converse goal is
omitted from its own diff.

The diff is injected into the prompt after plant memory and before message
history. After the run, `context_at` is advanced to the current time so the
next message sees only new changes.

---

## Session continuity

The driver stores the backend session/thread ID in
`conversation.session_id` after each run. On subsequent messages, it asks the
selected driver plugin to resume that backend continuity state instead of
starting a fresh one. This gives the model in-context history of the
conversation with no replay needed.

A manual checkpoint can also leave the conversation carrying a `SUMMARY.md`
handoff summary plus `compacted_through`. In that state, `session_id` is
intentionally cleared. The next message starts a fresh model session from:

- plant memory and garden motivation
- `SUMMARY.md`
- raw messages after `compacted_through`
- state/activity diff since `context_at`
- the new operator message

The queued `post_reply_hop` path eventually produces the same fresh-session
state, but it does so after the current reply has already been recorded and
delivered.

If no summary exists, a fresh session falls back to building from the full
message log.

Terminology:

- `hop` is the request or queued decision to move the next turn onto a fresh backend session.
- `checkpoint` is the durable record and archived summary written when that hop is executed.
- `handoff` is the summary content consumed by the next fresh session.

## Context pressure and hops

Each converse turn records a pressure snapshot in `turns.jsonl`. The snapshot
is heuristic rather than provider-authoritative: it combines raw tail size,
prompt size, session-turn count, and any provider token usage reported by the
backend. `pak2 chat` surfaces this out of band so the operator can inspect
pressure without polluting the garden's reply text.

When pressure crosses the garden threshold, or when the operator requests a
hop (`/hop` in chat or `pak2 hop`), the driver still answers in the current
backend session when one exists. It records and delivers the reply first, then
submits a separate `post_reply_hop` converse goal and appends a turn record
showing that the hop was queued.

That follow-up hop goal later resumes the same backend session, asks the model
for a handoff summary, archives it in `checkpoints/<id>.md`, appends a record
to `checkpoints.jsonl`, updates `SUMMARY.md`, clears `session_id`, and clears
the hop-tracking fields so the next operator message starts fresh.

The coordinator event log now records this lifecycle explicitly: successful and
failed hop-queue outcomes emit dedicated conversation hop events, and every
durable checkpoint write emits `ConversationCheckpointWritten`.

If a hop is requested while no backend session is active, there is nothing to
checkpoint; the driver clears `pending_hop` and the next turn simply starts
fresh.

## Manual checkpoint

`prepare_conversation_checkpoint()` is the low-level helper for explicit
manual checkpoint writes. It writes `SUMMARY.md`, records
`compacted_through`, clears `session_id`, and writes a durable checkpoint
record so hop decisions remain visible after the fact. Unlike `pending_hop`,
this helper performs the checkpoint immediately instead of queueing
post-reply work. `prepare_conversation_handoff()` remains a compatibility
alias for older callers.

---

## Invariants

- Every message appended to `messages.jsonl` passes `validate_message`.
- Every conversation `meta.json` write passes `validate_conversation`.
- Every turn appended to `turns.jsonl` passes `validate_conversation_turn`.
- Every checkpoint appended to `checkpoints.jsonl` passes `validate_conversation_checkpoint`.
- `context_at` only advances forward.
- `channel_ref` is stable for the lifetime of the conversation.
- `session_id` is `null` until a run has completed and extracted a session ID,
  and may be cleared deliberately to force the next turn onto a fresh session.
- If `compacted_through` is set, it marks the last message already represented
  in `SUMMARY.md`.

---

## Failure modes

| Code | Raised by | Meaning |
|------|-----------|---------|
| `CONVERSATION_NOT_FOUND` | `append_message`, `update_conversation` | No meta.json at the given conversation ID |
| `MISSING_REQUIRED_FIELD` | Conversation/message/turn/checkpoint validators | A required field is absent |
| `INVALID_STATUS` | `validate_conversation` | Status not in `{open, closed, archived}` |
| `INVALID_PRESENCE_MODEL` | `validate_conversation` | Presence model not in `{sync, async}` |
| `INVALID_TIMESTAMP` | Conversation/message/turn/checkpoint validators | Timestamp is not ISO 8601 UTC |
| `UNKNOWN_CONVERSATION_FIELD` | `validate_conversation` | `meta.json` contains a field outside the contract |
| `INVALID_CONVERSATION_FIELD` | `validate_conversation` | A conversation metadata field has the wrong type or identifier format |
| `INVALID_CONVERSATION_PRESSURE` | `validate_conversation` | `last_pressure` is malformed |
| `INVALID_CONVERSATION_HOP` | `validate_conversation` | `pending_hop` is malformed |
| `INVALID_POST_REPLY_HOP` | `validate_conversation` | `post_reply_hop` is malformed |
| `UNKNOWN_MESSAGE_FIELD` | `validate_message` | A message contains a field outside the contract |
| `INVALID_MESSAGE_FIELD` | `validate_message` | A message field has the wrong type or identifier format |
| `UNKNOWN_TURN_FIELD` | `validate_conversation_turn` | A turn record contains a field outside the contract |
| `INVALID_TURN_FIELD` | `validate_conversation_turn` | A top-level turn field has the wrong type or identifier format |
| `INVALID_TURN_LINEAGE` | `validate_conversation_turn` | `lineage` is malformed |
| `INVALID_TURN_HOP` | `validate_conversation_turn` | `hop` is malformed or internally inconsistent |
| `INVALID_TURN_PRESSURE` | `validate_conversation_turn` | `pressure` is malformed |
| `UNKNOWN_CHECKPOINT_FIELD` | `validate_conversation_checkpoint` | A checkpoint record contains a field outside the contract |
| `INVALID_CHECKPOINT_FIELD` | `validate_conversation_checkpoint` | A checkpoint field has the wrong type, identifier format, or summary path |
| `INVALID_CHECKPOINT_PRESSURE` | `validate_conversation_checkpoint` | `pressure` is malformed |
| `EMPTY_CONTENT` | `validate_message` | Message content is empty or whitespace-only |
| `MESSAGE_NOT_FOUND` | `prepare_conversation_checkpoint` | `compacted_through` does not match any message in the conversation |
| `INVALID_SHAPE` | Conversation/message/turn/checkpoint validators | Data is not a dict |

---

## Cost

Each `converse` goal produces a run with a cost record (tokens, USD) in
`<runtime-root>/runs/<run-id>/meta.json`. Conversation cost is the sum of costs across all
`converse` runs for goals with `conversation_id` set. The driver records cost
the same way as all other goal types.
