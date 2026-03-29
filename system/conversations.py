"""
Conversations: schema, storage, and diff computation.

conversations/<id>/
  meta.json       — conversation record
  messages.jsonl  — append-only message log
  SUMMARY.md      — optional handoff summary for fresh-session resumes

context_at drives diff injection: when a conversation resumes, the driver
injects what changed in the garden's state and autonomous activity since
that timestamp. This is how two concurrent conversations stay coherent
without directly knowing about each other — they share one state layer.

Session checkpoints keep the conversation ID and message log stable while
letting the backing model session reset. SUMMARY.md stores compacted
conversation context, and compacted_through marks the last message already
absorbed into that summary. A fresh session can then resume from summary
plus the raw tail after compacted_through instead of replaying full history.
"""

import datetime
import json
import pathlib
import random
import re
import string

from .validate import (
    ValidationResult,
    validate_conversation,
    validate_conversation_checkpoint,
    validate_conversation_turn,
    validate_message,
)
from .events import append_event, coordinator_events_path, read_events
from .garden import garden_paths

_CONVERSATIONS_DIR = garden_paths().conversations_dir
_SUMMARY_FILE_NAME = "SUMMARY.md"
_TURNS_FILE_NAME = "turns.jsonl"
_CHECKPOINTS_FILE_NAME = "checkpoints.jsonl"
_CHECKPOINTS_DIR_NAME = "checkpoints"
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_EXTERNAL_APPEND_HOP_REASON = "external assistant append refreshed durable conversation"
_POST_REPLY_HOP_GOAL_TYPE = "converse"
_POST_REPLY_HOP_PRIORITY = 8
_PRESSURE_THRESHOLDS = {
    "tail_messages": 12,
    "tail_chars": 12000,
    "prompt_chars": 18000,
    "session_turns": 8,
    "input_tokens": 1000000,
}


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text: str, max_len: int = 40) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:max_len].rstrip("-")


def _next_id_in(conv_dir: pathlib.Path) -> int:
    # Note: ID allocation is not atomic. Two concurrent open_conversation calls
    # can compute the same N. In practice the somatic loop is single-threaded,
    # so this is not a real risk. If multi-threaded creation is ever needed,
    # replace with a file lock or atomic counter.
    existing = []
    if conv_dir.exists():
        for p in conv_dir.iterdir():
            if p.is_dir():
                try:
                    existing.append(int(p.name.split("-")[0]))
                except (ValueError, IndexError):
                    pass
    return max(existing, default=0) + 1


def _random_suffix(length: int = 4) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _read_json_lines(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return records


def _append_json_line(path: pathlib.Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _compact_ts(ts: str | None = None) -> str:
    value = ts or _now_utc()
    return value.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")


def _coerce_nonnegative_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _messages_chars(messages: list[dict]) -> int:
    return sum(len(str(message.get("content", ""))) for message in messages)


def find_open_conversation_for_channel(channel: str, channel_ref: str, *,
                                       _conv_dir: pathlib.Path | None = None) -> dict | None:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    for conv in list_conversations(status="open", _conv_dir=conv_dir):
        if conv.get("channel") == channel and conv.get("channel_ref") == channel_ref:
            return conv
    return None


def open_conversation(channel: str, channel_ref: str,
                      presence_model: str = "async",
                      participants: list | None = None,
                      topic: str = "conversation",
                      *,
                      started_by: str = "operator",
                      _conv_dir: pathlib.Path | None = None,
                      _now: str | None = None) -> tuple[ValidationResult, str | None]:
    """Create a new conversation. Returns (result, conv_id)."""
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    now = _now or _now_utc()
    n = _next_id_in(conv_dir)
    conv_id = f"{n}-{_slugify(topic) or 'conversation'}"

    record = {
        "id": conv_id,
        "status": "open",
        "channel": channel,
        "channel_ref": channel_ref,
        "presence_model": presence_model,
        "started_by": started_by,
        "participants": participants or ["operator", "garden"],
        "started_at": now,
        "last_activity_at": now,
        "context_at": now,
        "session_id": None,
        "compacted_through": None,
        "session_ordinal": 0,
        "session_turns": 0,
        "session_started_at": None,
        "checkpoint_count": 0,
        "last_checkpoint_id": None,
        "last_checkpoint_at": None,
        "last_turn_mode": None,
        "last_turn_run_id": None,
        "last_pressure": None,
        "pending_hop": None,
        "post_reply_hop": None,
    }
    result = validate_conversation(record)
    if not result.ok:
        return result, None

    (conv_dir / conv_id).mkdir(parents=True, exist_ok=True)
    (conv_dir / conv_id / "meta.json").write_text(json.dumps(record, indent=2) + "\n",
                                                   encoding="utf-8")
    (conv_dir / conv_id / "messages.jsonl").write_text("", encoding="utf-8")
    return ValidationResult.accept(), conv_id


def read_conversation(conv_id: str, *,
                      _conv_dir: pathlib.Path | None = None) -> dict | None:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    meta = conv_dir / conv_id / "meta.json"
    if not meta.exists():
        return None
    return json.loads(meta.read_text(encoding="utf-8"))


def update_conversation(conv_id: str, *,
                        _conv_dir: pathlib.Path | None = None,
                        _now: str | None = None,
                        **fields) -> ValidationResult:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    conv = read_conversation(conv_id, _conv_dir=conv_dir)
    if conv is None:
        return ValidationResult.reject("CONVERSATION_NOT_FOUND", conv_id)
    conv.update(fields)
    conv["last_activity_at"] = _now or _now_utc()
    result = validate_conversation(conv)
    if not result.ok:
        return result
    (conv_dir / conv_id / "meta.json").write_text(json.dumps(conv, indent=2) + "\n",
                                                   encoding="utf-8")
    return ValidationResult.accept()


def append_message(conv_id: str, sender: str, content: str,
                   channel: str | None = None,
                   reply_to: str | None = None,
                   *,
                   _conv_dir: pathlib.Path | None = None,
                   _now: str | None = None,
                   _message_id: str | None = None) -> tuple[ValidationResult, str | None]:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    now = _now or _now_utc()
    conv = read_conversation(conv_id, _conv_dir=conv_dir)
    if conv is None:
        return ValidationResult.reject("CONVERSATION_NOT_FOUND", conv_id), None

    if _message_id is not None:
        msg_id = str(_message_id).strip()
    else:
        ts_compact = now.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")
        msg_id = f"msg-{ts_compact}-{sender[:3]}-{_random_suffix()}"

    message = {
        "id": msg_id,
        "conversation_id": conv_id,
        "ts": now,
        "sender": sender,
        "content": content,
        "channel": channel or conv["channel"],
        "reply_to": reply_to,
    }
    result = validate_message(message)
    if not result.ok:
        return result, None

    path = conv_dir / conv_id / "messages.jsonl"
    if _message_id is not None:
        for existing in read_messages(conv_id, _conv_dir=conv_dir):
            if existing.get("id") != msg_id:
                continue
            for field in ("conversation_id", "sender", "content", "channel", "reply_to"):
                if existing.get(field) != message.get(field):
                    return ValidationResult.reject(
                        "MESSAGE_ID_CONFLICT",
                        (
                            f"message id {msg_id!r} already exists with different "
                            f"{field}: {existing.get(field)!r} != {message.get(field)!r}"
                        ),
                    ), None
            return ValidationResult.accept(), msg_id

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message) + "\n")

    update_conversation(conv_id, _conv_dir=conv_dir, _now=now)
    return ValidationResult.accept(), msg_id


def read_messages(conv_id: str, *,
                  _conv_dir: pathlib.Path | None = None,
                  limit: int | None = None) -> list[dict]:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    path = conv_dir / conv_id / "messages.jsonl"
    if not path.exists():
        return []
    messages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return messages[-limit:] if limit else messages


def list_conversations(status: str | None = None,
                       *,
                       _conv_dir: pathlib.Path | None = None) -> list[dict]:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    if not conv_dir.exists():
        return []
    result = []
    for p in sorted(conv_dir.iterdir()):
        meta = p / "meta.json"
        if meta.exists():
            try:
                c = json.loads(meta.read_text(encoding="utf-8"))
                if status is None or c.get("status") == status:
                    result.append(c)
            except (json.JSONDecodeError, OSError):
                pass
    return result


def conversation_summary_path(conv_id: str, *,
                              _conv_dir: pathlib.Path | None = None) -> pathlib.Path:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    return conv_dir / conv_id / _SUMMARY_FILE_NAME


def conversation_turns_path(conv_id: str, *,
                            _conv_dir: pathlib.Path | None = None) -> pathlib.Path:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    return conv_dir / conv_id / _TURNS_FILE_NAME


def conversation_checkpoints_path(conv_id: str, *,
                                  _conv_dir: pathlib.Path | None = None) -> pathlib.Path:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    return conv_dir / conv_id / _CHECKPOINTS_FILE_NAME


def conversation_checkpoint_archive_dir(conv_id: str, *,
                                        _conv_dir: pathlib.Path | None = None) -> pathlib.Path:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    return conv_dir / conv_id / _CHECKPOINTS_DIR_NAME


def read_conversation_summary(conv_id: str, *,
                              _conv_dir: pathlib.Path | None = None) -> str | None:
    path = conversation_summary_path(conv_id, _conv_dir=_conv_dir)
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    return content or None


def read_conversation_turns(conv_id: str, *,
                            _conv_dir: pathlib.Path | None = None,
                            limit: int | None = None) -> list[dict]:
    records = _read_json_lines(conversation_turns_path(conv_id, _conv_dir=_conv_dir))
    return records[-limit:] if limit else records


def read_latest_conversation_turn(conv_id: str, *,
                                  _conv_dir: pathlib.Path | None = None) -> dict | None:
    turns = read_conversation_turns(conv_id, _conv_dir=_conv_dir, limit=1)
    return turns[-1] if turns else None


def append_conversation_turn(conv_id: str, record: dict, *,
                             _conv_dir: pathlib.Path | None = None) -> ValidationResult:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    if read_conversation(conv_id, _conv_dir=conv_dir) is None:
        return ValidationResult.reject("CONVERSATION_NOT_FOUND", conv_id)
    payload = dict(record)
    payload.setdefault("conversation_id", conv_id)
    result = validate_conversation_turn(payload)
    if not result.ok:
        return result
    _append_json_line(conversation_turns_path(conv_id, _conv_dir=conv_dir), payload)
    return ValidationResult.accept()


def read_conversation_checkpoints(conv_id: str, *,
                                  _conv_dir: pathlib.Path | None = None,
                                  limit: int | None = None) -> list[dict]:
    records = _read_json_lines(conversation_checkpoints_path(conv_id, _conv_dir=_conv_dir))
    return records[-limit:] if limit else records


def write_conversation_summary(conv_id: str, summary: str, *,
                               _conv_dir: pathlib.Path | None = None) -> ValidationResult:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    if read_conversation(conv_id, _conv_dir=conv_dir) is None:
        return ValidationResult.reject("CONVERSATION_NOT_FOUND", conv_id)
    if not str(summary).strip():
        return ValidationResult.reject("EMPTY_CONTENT", "summary must not be empty")
    path = conversation_summary_path(conv_id, _conv_dir=conv_dir)
    path.write_text(str(summary).rstrip() + "\n", encoding="utf-8")
    return ValidationResult.accept()


def request_conversation_hop(conv_id: str, *,
                             requested_by: str = "operator",
                             reason: str = "operator requested session hop",
                             _conv_dir: pathlib.Path | None = None,
                             _now: str | None = None) -> ValidationResult:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    conv = read_conversation(conv_id, _conv_dir=conv_dir)
    if conv is None:
        return ValidationResult.reject("CONVERSATION_NOT_FOUND", conv_id)
    request = {
        "requested_at": _now or _now_utc(),
        "requested_by": requested_by,
        "reason": str(reason).strip() or "operator requested session hop",
    }
    return update_conversation(conv_id, _conv_dir=conv_dir, pending_hop=request)


def _conversation_session_checkpoint_marker(conv: dict, messages: list[dict]) -> dict | None:
    """
    Return the last durable message known to be inside the active backend session.

    External garden notes appended after context_at must remain raw tail context
    for the replacement session rather than being compacted into a summary the
    stale session cannot faithfully produce.
    """
    context_at = str(conv.get("context_at") or conv.get("started_at") or "")
    marker = None
    for message in messages:
        ts = str(message.get("ts") or "")
        if not context_at or ts <= context_at:
            marker = message
    return marker


def tail_messages_after(messages: list[dict], compacted_through: str | None) -> list[dict]:
    """
    Return the raw tail after compacted_through.

    If compacted_through is absent or cannot be found, fall back to the full
    input list so prompt construction fails open rather than dropping context.
    """
    if not compacted_through:
        return list(messages)
    for idx, message in enumerate(messages):
        if message.get("id") == compacted_through:
            return messages[idx + 1:]
    return list(messages)


def compute_context_pressure(conv: dict, messages: list[dict], *,
                             summary: str | None = None,
                             provider_usage: dict | None = None) -> dict:
    """
    Build a garden-local context-pressure snapshot.

    This is a heuristic score, not a provider context-window measurement.
    The goal is to make pressure visible and give the system a deterministic
    basis for deciding when to checkpoint a conversation session.
    """
    history_messages = list(messages)
    tail_messages = tail_messages_after(history_messages, conv.get("compacted_through"))
    history_chars = _messages_chars(history_messages)
    tail_chars = _messages_chars(tail_messages)
    summary_chars = len((summary or "").strip())
    prompt_chars = summary_chars + tail_chars if summary else history_chars
    session_turns = _coerce_nonnegative_int(conv.get("session_turns")) or 0
    provider_usage = provider_usage or {}
    last_pressure = conv.get("last_pressure") or {}
    provider_input_tokens = _coerce_nonnegative_int(
        provider_usage.get("input_tokens", last_pressure.get("provider_input_tokens"))
    )
    provider_cached_input_tokens = _coerce_nonnegative_int(
        provider_usage.get(
            "cached_input_tokens",
            provider_usage.get(
                "cache_read_tokens",
                last_pressure.get("provider_cached_input_tokens"),
            ),
        )
    )
    provider_output_tokens = _coerce_nonnegative_int(
        provider_usage.get("output_tokens", last_pressure.get("provider_output_tokens"))
    )

    ratios = {
        "tail_messages": len(tail_messages) / _PRESSURE_THRESHOLDS["tail_messages"],
        "tail_chars": tail_chars / _PRESSURE_THRESHOLDS["tail_chars"],
        "prompt_chars": prompt_chars / _PRESSURE_THRESHOLDS["prompt_chars"],
        "session_turns": session_turns / _PRESSURE_THRESHOLDS["session_turns"],
    }
    if provider_input_tokens is not None:
        ratios["provider_input_tokens"] = (
            provider_input_tokens / _PRESSURE_THRESHOLDS["input_tokens"]
        )

    score = max(ratios.values(), default=0.0)
    if score >= 1.0:
        band = "critical"
    elif score >= 0.85:
        band = "high"
    elif score >= 0.5:
        band = "medium"
    else:
        band = "low"

    reasons = []
    if provider_input_tokens is not None and ratios.get("provider_input_tokens", 0.0) >= 0.75:
        reasons.append(f"provider input {provider_input_tokens} tok")
    if len(tail_messages) >= int(_PRESSURE_THRESHOLDS["tail_messages"] * 0.75):
        reasons.append(f"tail {len(tail_messages)} msgs")
    if tail_chars >= int(_PRESSURE_THRESHOLDS["tail_chars"] * 0.75):
        reasons.append(f"tail {tail_chars} chars")
    if prompt_chars >= int(_PRESSURE_THRESHOLDS["prompt_chars"] * 0.75):
        reasons.append(f"prompt {prompt_chars} chars")
    if session_turns >= int(_PRESSURE_THRESHOLDS["session_turns"] * 0.75):
        reasons.append(f"session {session_turns} turns")

    prompt_source = "resume-session"
    if not conv.get("session_id"):
        prompt_source = "summary+tail" if summary else "full-history"

    return {
        "band": band,
        "score": round(score, 3),
        "needs_hop": bool(conv.get("session_id")) and score >= 1.0,
        "prompt_source": prompt_source,
        "summary_present": bool(summary),
        "history_messages": len(history_messages),
        "history_chars": history_chars,
        "tail_messages": len(tail_messages),
        "tail_chars": tail_chars,
        "summary_chars": summary_chars,
        "prompt_chars": prompt_chars,
        "session_turns": session_turns,
        "provider_input_tokens": provider_input_tokens,
        "provider_cached_input_tokens": provider_cached_input_tokens,
        "provider_output_tokens": provider_output_tokens,
        "reasons": reasons,
        "thresholds": dict(_PRESSURE_THRESHOLDS),
    }


def describe_context_pressure(pressure: dict | None) -> str:
    if not pressure:
        return "pressure unknown"
    parts = [f"pressure {pressure.get('band', 'unknown')}"]
    provider_input = pressure.get("provider_input_tokens")
    if provider_input is not None:
        parts.append(f"{provider_input} input tok")
    parts.append(f"tail {pressure.get('tail_messages', 0)} msgs")
    parts.append(f"prompt {pressure.get('prompt_chars', 0)} chars")
    return " | ".join(parts)


def _conversation_events_path(conv_dir: pathlib.Path, explicit: pathlib.Path | None) -> pathlib.Path:
    if explicit is not None:
        return explicit
    if conv_dir == _CONVERSATIONS_DIR:
        return garden_paths().coordinator_events_path
    return coordinator_events_path(conv_dir.parent)


def _conversation_goals_dir(conv_dir: pathlib.Path,
                            explicit: pathlib.Path | None) -> pathlib.Path:
    if explicit is not None:
        return explicit
    if conv_dir == _CONVERSATIONS_DIR:
        return garden_paths().goals_dir
    return garden_paths(garden_root=conv_dir.parent).goals_dir


def _retire_stale_session(conv_id: str, *,
                          conv_dir: pathlib.Path,
                          now: str) -> None:
    result = update_conversation(
        conv_id,
        _conv_dir=conv_dir,
        _now=now,
        session_id=None,
        session_turns=0,
        session_started_at=None,
        pending_hop=None,
        post_reply_hop=None,
    )
    if result.ok:
        return
    print(
        f"conversations: failed to retire stale session for {conv_id}: "
        f"{result.reason} — {result.detail}",
    )


def queue_external_append_hop(conv_id: str, *,
                              source_goal: dict,
                              source_run_id: str,
                              trigger_message_id: str,
                              _conv_dir: pathlib.Path | None = None,
                              _goals_dir: pathlib.Path | None = None,
                              _events_path: pathlib.Path | None = None,
                              _now: str | None = None) -> tuple[ValidationResult, dict | None]:
    """
    Queue a replacement fresh-session hop after a garden note is appended
    outside an active converse turn.

    The checkpoint boundary stays at the last message already inside the stale
    backend session so the newly appended note remains in the fresh-session tail.
    """
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    now = _now or _now_utc()
    conv = read_conversation(conv_id, _conv_dir=conv_dir)
    if conv is None:
        return ValidationResult.reject("CONVERSATION_NOT_FOUND", conv_id), None

    session_id = conv.get("session_id")
    if not session_id:
        return ValidationResult.accept(), None

    active_hop = conv.get("post_reply_hop") or {}
    if active_hop:
        return ValidationResult.accept(), active_hop

    source_goal_id = str(source_goal.get("id") or "").strip()
    if not source_goal_id:
        return ValidationResult.reject(
            "MISSING_REQUIRED_FIELD",
            "source_goal.id is required for external append hop queueing",
        ), None

    events_path = _conversation_events_path(conv_dir, _events_path)
    goals_dir = _conversation_goals_dir(conv_dir, _goals_dir)
    messages = read_messages(conv_id, _conv_dir=conv_dir)
    summary = read_conversation_summary(conv_id, _conv_dir=conv_dir)
    checkpoint_marker = _conversation_session_checkpoint_marker(conv, messages)
    pending_hop = conv.get("pending_hop") if isinstance(conv.get("pending_hop"), dict) else None
    requested_by = str((pending_hop or {}).get("requested_by") or "system")
    reason = str((pending_hop or {}).get("reason") or _EXTERNAL_APPEND_HOP_REASON).strip()
    if not reason:
        reason = _EXTERNAL_APPEND_HOP_REASON
    automatic = not bool(pending_hop)

    if checkpoint_marker is None:
        detail = "no in-session checkpoint marker available for external append hop"
        append_event(
            {
                "ts": now,
                "type": "ConversationHopQueueFailed",
                "actor": "system",
                "goal": source_goal_id,
                "run": source_run_id,
                "conversation_id": conv_id,
                "source_message_id": trigger_message_id,
                "hop_requested_by": requested_by,
                "hop_reason": reason,
                "hop_automatic": automatic,
                "detail": detail,
            },
            path=events_path,
        )
        _retire_stale_session(conv_id, conv_dir=conv_dir, now=now)
        return ValidationResult.reject("MESSAGE_NOT_FOUND", detail), None

    pressure = compute_context_pressure(conv, messages, summary=summary)
    hop_record = {
        "requested_at": (pending_hop or {}).get("requested_at", now),
        "requested_by": requested_by,
        "reason": reason,
        "automatic": automatic,
        "source_goal_id": source_goal_id,
        "source_run_id": source_run_id,
        "source_reply_message_id": checkpoint_marker["id"],
        "source_reply_recorded_at": checkpoint_marker["ts"],
        "source_session_id": session_id,
        "source_session_ordinal": conv.get("session_ordinal", 0),
        "source_session_turns": conv.get("session_turns", 0),
        "pressure": pressure,
    }

    goal_data = {
        "type": _POST_REPLY_HOP_GOAL_TYPE,
        "submitted_by": source_goal.get("assigned_to", "gardener"),
        "assigned_to": source_goal.get("assigned_to", "gardener"),
        "priority": _POST_REPLY_HOP_PRIORITY,
        "body": (
            f"Checkpoint conversation {conv_id} after external assistant append "
            f"{trigger_message_id} so the next turn can resume from a fresh session."
        ),
        "conversation_id": conv_id,
        "post_reply_hop": hop_record,
    }
    for field in ("driver", "model", "reasoning_effort"):
        value = source_goal.get(field)
        if value:
            goal_data[field] = value

    from .submit import submit_goal
    from .goals import transition_goal

    result, hop_goal_id = submit_goal(
        goal_data,
        _goals_dir=goals_dir,
        _now=now,
    )
    if not result.ok or hop_goal_id is None:
        detail = result.detail or result.reason
        append_event(
            {
                "ts": now,
                "type": "ConversationHopQueueFailed",
                "actor": "system",
                "goal": source_goal_id,
                "run": source_run_id,
                "conversation_id": conv_id,
                "source_message_id": trigger_message_id,
                "hop_requested_by": requested_by,
                "hop_reason": reason,
                "hop_automatic": automatic,
                "detail": detail,
            },
            path=events_path,
        )
        _retire_stale_session(conv_id, conv_dir=conv_dir, now=now)
        return result, None

    hop_record["goal_id"] = hop_goal_id
    update_fields = {"post_reply_hop": hop_record}
    if pending_hop:
        update_fields["pending_hop"] = None
    update_result = update_conversation(
        conv_id,
        _conv_dir=conv_dir,
        _now=now,
        **update_fields,
    )
    if not update_result.ok:
        transition_goal(
            hop_goal_id,
            "closed",
            actor="system",
            closed_reason="cancelled",
            _goals_dir=goals_dir,
            _now=now,
        )
        detail = update_result.detail or update_result.reason
        append_event(
            {
                "ts": now,
                "type": "ConversationHopQueueFailed",
                "actor": "system",
                "goal": source_goal_id,
                "run": source_run_id,
                "conversation_id": conv_id,
                "source_message_id": trigger_message_id,
                "hop_requested_by": requested_by,
                "hop_reason": reason,
                "hop_automatic": automatic,
                "detail": detail,
            },
            path=events_path,
        )
        _retire_stale_session(conv_id, conv_dir=conv_dir, now=now)
        return update_result, None

    append_event(
        {
            "ts": now,
            "type": "ConversationHopQueued",
            "actor": "system",
            "goal": source_goal_id,
            "run": source_run_id,
            "conversation_id": conv_id,
            "hop_goal": hop_goal_id,
            "source_message_id": trigger_message_id,
            "hop_requested_by": requested_by,
            "hop_reason": reason,
            "hop_automatic": automatic,
        },
        path=events_path,
    )
    return ValidationResult.accept(), hop_record


def write_conversation_checkpoint(conv_id: str, summary: str, compacted_through: str, *,
                                  requested_by: str = "system",
                                  reason: str = "manual checkpoint",
                                  source_session_id: str | None = None,
                                  source_session_ordinal: int | None = None,
                                  source_session_turns: int | None = None,
                                  run_id: str | None = None,
                                  driver: str | None = None,
                                  model: str | None = None,
                                  pressure: dict | None = None,
                                  _conv_dir: pathlib.Path | None = None,
                                  _now: str | None = None,
                                  _events_path: pathlib.Path | None = None,
                                  _event_actor: str = "system",
                                  _event_goal: str | None = None,
                                  ) -> tuple[ValidationResult, dict | None]:
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    conv = read_conversation(conv_id, _conv_dir=conv_dir)
    if conv is None:
        return ValidationResult.reject("CONVERSATION_NOT_FOUND", conv_id), None
    if not str(summary).strip():
        return ValidationResult.reject("EMPTY_CONTENT", "summary must not be empty"), None
    if not str(compacted_through).strip():
        return ValidationResult.reject(
            "MISSING_REQUIRED_FIELD",
            "compacted_through must name an existing message id",
        ), None

    messages = read_messages(conv_id, _conv_dir=conv_dir)
    if not any(message.get("id") == compacted_through for message in messages):
        return ValidationResult.reject("MESSAGE_NOT_FOUND", compacted_through), None

    now = _now or _now_utc()
    checkpoint_id = f"ckpt-{_compact_ts(now)}-{_random_suffix()}"
    summary_rel_path = f"{_CHECKPOINTS_DIR_NAME}/{checkpoint_id}.md"

    record = {
        "id": checkpoint_id,
        "conversation_id": conv_id,
        "ts": now,
        "requested_by": requested_by,
        "reason": reason,
        "compacted_through": compacted_through,
        "source_session_id": source_session_id or conv.get("session_id"),
        "source_session_ordinal": (
            source_session_ordinal
            if source_session_ordinal is not None
            else conv.get("session_ordinal", 0)
        ),
        "source_session_turns": (
            source_session_turns
            if source_session_turns is not None
            else conv.get("session_turns", 0)
        ),
        "summary_path": summary_rel_path,
        "run_id": run_id,
        "driver": driver,
        "model": model,
        "pressure": pressure,
    }
    result = validate_conversation_checkpoint(record)
    if not result.ok:
        return result, None

    result = write_conversation_summary(conv_id, summary, _conv_dir=conv_dir)
    if not result.ok:
        return result, None

    archive_dir = conversation_checkpoint_archive_dir(conv_id, _conv_dir=conv_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{checkpoint_id}.md"
    archive_path.write_text(str(summary).rstrip() + "\n", encoding="utf-8")
    _append_json_line(conversation_checkpoints_path(conv_id, _conv_dir=conv_dir), record)

    checkpoint_count = int(conv.get("checkpoint_count") or 0) + 1
    checkpoint_event = {
        "ts": now,
        "type": "ConversationCheckpointWritten",
        "actor": _event_actor,
        "conversation_id": conv_id,
        "checkpoint_id": checkpoint_id,
        "checkpoint_requested_by": requested_by,
        "checkpoint_reason": reason,
        "checkpoint_summary_path": summary_rel_path,
        "source_message_id": compacted_through,
        "source_session_ordinal": record["source_session_ordinal"],
        "source_session_turns": record["source_session_turns"],
        "checkpoint_count": checkpoint_count,
    }
    if record["source_session_id"] is not None:
        checkpoint_event["source_session_id"] = record["source_session_id"]
    if run_id:
        checkpoint_event["run"] = run_id
    if _event_goal:
        checkpoint_event["goal"] = _event_goal
    if driver:
        checkpoint_event["driver"] = driver
    if model:
        checkpoint_event["model"] = model
    append_event(
        checkpoint_event,
        path=(
            _events_path
            or (
                garden_paths().coordinator_events_path
                if _conv_dir is None
                else coordinator_events_path(conv_dir.parent)
            )
        ),
    )

    update_result = update_conversation(
        conv_id,
        _conv_dir=conv_dir,
        _now=now,
        compacted_through=compacted_through,
        session_id=None,
        session_turns=0,
        session_started_at=None,
        checkpoint_count=checkpoint_count,
        last_checkpoint_id=checkpoint_id,
        last_checkpoint_at=now,
        pending_hop=None,
        post_reply_hop=None,
    )
    if not update_result.ok:
        return update_result, None

    return ValidationResult.accept(), record


def prepare_conversation_checkpoint(conv_id: str, summary: str, compacted_through: str, *,
                                    _conv_dir: pathlib.Path | None = None) -> ValidationResult:
    """
    Write SUMMARY.md, record a checkpoint, and clear the active session.

    compacted_through is the last message already absorbed into the summary.
    The next fresh session can then use SUMMARY.md plus the raw tail after that
    message. session_id is cleared so the driver does not attempt --resume.
    """
    conv_dir = _conv_dir or _CONVERSATIONS_DIR
    conv = read_conversation(conv_id, _conv_dir=conv_dir)
    if conv is None:
        return ValidationResult.reject("CONVERSATION_NOT_FOUND", conv_id)
    result, _ = write_conversation_checkpoint(
        conv_id,
        summary,
        compacted_through,
        requested_by="system",
        reason="manual checkpoint",
        _conv_dir=conv_dir,
        _now=conv["last_activity_at"],
    )
    return result


def prepare_conversation_handoff(conv_id: str, summary: str, compacted_through: str, *,
                                 _conv_dir: pathlib.Path | None = None) -> ValidationResult:
    """Backward-compatible alias for prepare_conversation_checkpoint()."""
    return prepare_conversation_checkpoint(
        conv_id,
        summary,
        compacted_through,
        _conv_dir=_conv_dir,
    )


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def compute_state_diff(plant_dir: pathlib.Path, context_at: str) -> list[dict]:
    """
    Files added or modified in memory/, skills/, knowledge/ since context_at.
    Returns list of {type, name, heading, change} dicts.
    """
    try:
        cutoff = datetime.datetime.fromisoformat(context_at.replace("Z", "+00:00"))
    except ValueError:
        return []
    changes = []
    for subdir, kind in [("memory", "memory"), ("skills", "skill"), ("knowledge", "knowledge")]:
        d = plant_dir / subdir
        if not d.exists():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix != ".md" or not f.is_file():
                continue
            # Stat first — only read content if mtime qualifies.
            mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime,
                                                    tz=datetime.timezone.utc)
            if mtime <= cutoff:
                continue
            content = f.read_text(encoding="utf-8", errors="ignore").strip()
            heading = next(
                (line.lstrip("#").strip() for line in content.splitlines()
                 if line.startswith("#")),
                f.stem,
            )
            changes.append({"type": kind, "name": f.name,
                            "heading": heading, "change": "modified"})
    return changes


def compute_activity_diff(events_path: pathlib.Path, context_at: str, *,
                          exclude_goal_ids: set[str] | None = None) -> list[dict]:
    """Coordinator events since context_at, summarised by type."""
    if not events_path.exists():
        return []
    excluded_goals = {str(goal_id) for goal_id in (exclude_goal_ids or set()) if goal_id}
    events = []
    queued_hop_goals: set[str] = set()
    checkpoint_hop_goals: set[str] = set()
    checkpoint_hop_runs: set[str] = set()
    for e in read_events(path=events_path):
        # Lexicographic comparison is correct while both sides use the same
        # ISO 8601 UTC format (YYYY-MM-DDTHH:MM:SSZ). Would silently produce
        # wrong results if timestamp format ever diverged.
        if e.get("ts", "") <= context_at:
            continue
        goal_id = e.get("goal")
        if goal_id and str(goal_id) in excluded_goals:
            continue
        events.append(e)
        if e.get("type") == "ConversationHopQueued" and e.get("hop_goal"):
            queued_hop_goals.add(str(e["hop_goal"]))
        if e.get("type") == "ConversationCheckpointWritten":
            if e.get("goal"):
                checkpoint_hop_goals.add(str(e["goal"]))
            if e.get("run"):
                checkpoint_hop_runs.add(str(e["run"]))

    summary = []
    for e in events:
        goal_id = e.get("goal")
        run_id = e.get("run")
        if e.get("goal_subtype") == "post_reply_hop":
            if e.get("type") == "GoalSubmitted" and goal_id and str(goal_id) in queued_hop_goals:
                continue
            if e.get("type") == "GoalClosed" and goal_id and str(goal_id) in checkpoint_hop_goals:
                continue
            if e.get("type") == "RunFinished" and (
                (goal_id and str(goal_id) in checkpoint_hop_goals)
                or (run_id and str(run_id) in checkpoint_hop_runs)
            ):
                continue
        t = e.get("type")
        if t == "ConversationHopQueued":
            summary.append(
                {
                    "type": "conversation_hop_queued",
                    "conversation_id": e.get("conversation_id"),
                    "hop_goal": e.get("hop_goal"),
                    "reason": e.get("hop_reason"),
                    "automatic": e.get("hop_automatic"),
                }
            )
        elif t == "ConversationHopQueueFailed":
            summary.append(
                {
                    "type": "conversation_hop_queue_failed",
                    "conversation_id": e.get("conversation_id"),
                    "reason": e.get("hop_reason"),
                    "automatic": e.get("hop_automatic"),
                    "detail": e.get("detail"),
                }
            )
        elif t == "ConversationCheckpointWritten":
            summary.append(
                {
                    "type": "conversation_checkpoint_written",
                    "conversation_id": e.get("conversation_id"),
                    "checkpoint_id": e.get("checkpoint_id"),
                    "reason": e.get("checkpoint_reason"),
                }
            )
        elif t == "GoalClosed":
            summary.append({"type": "goal_closed",
                            "goal": goal_id, "reason": e.get("goal_reason")})
        elif t == "GoalSubmitted":
            record = {
                "type": "goal_submitted",
                "goal": goal_id,
                "actor": e.get("actor"),
                "goal_type": e.get("goal_type"),
                "conversation_id": e.get("conversation_id"),
            }
            if e.get("goal_subtype"):
                record["goal_subtype"] = e.get("goal_subtype")
            if e.get("hop_reason"):
                record["hop_reason"] = e.get("hop_reason")
            if e.get("hop_automatic") is not None:
                record["hop_automatic"] = e.get("hop_automatic")
            summary.append(record)
        elif t == "RunFinished":
            summary.append({"type": "run_finished",
                            "run": e.get("run"), "reason": e.get("run_reason")})
        elif t == "PlantCommissioned":
            summary.append({"type": "plant_commissioned", "plant": e.get("plant")})
    return summary


def format_diff(state_diff: list, activity_diff: list) -> str:
    """Format diffs for injection into a run prompt."""
    if not state_diff and not activity_diff:
        return ""
    lines = ["[Since your last exchange in this conversation:"]
    if activity_diff:
        lines.append("\n  Garden activity:")
        for item in activity_diff:
            t = item["type"]
            if t == "goal_closed":
                lines.append(f"    goal {item['goal']} closed ({item['reason']})")
            elif t == "conversation_hop_queued":
                details = []
                conversation_id = item.get("conversation_id")
                if conversation_id:
                    details.append(f"conversation_id={conversation_id}")
                reason = item.get("reason")
                if reason:
                    details.append(f"reason={reason}")
                if item.get("automatic") is not None:
                    details.append(
                        "automatic=yes" if item.get("automatic") else "automatic=no"
                    )
                suffix = f" ({', '.join(details)})" if details else ""
                lines.append(
                    f"    conversation hop queued: {item.get('hop_goal') or '?'}{suffix}"
                )
            elif t == "conversation_hop_queue_failed":
                details = []
                conversation_id = item.get("conversation_id")
                if conversation_id:
                    details.append(f"conversation_id={conversation_id}")
                reason = item.get("reason")
                if reason:
                    details.append(f"reason={reason}")
                if item.get("automatic") is not None:
                    details.append(
                        "automatic=yes" if item.get("automatic") else "automatic=no"
                    )
                detail = item.get("detail")
                if detail:
                    details.append(f"detail={detail}")
                suffix = f" ({', '.join(details)})" if details else ""
                lines.append("    conversation hop queue failed" + suffix)
            elif t == "conversation_checkpoint_written":
                details = []
                conversation_id = item.get("conversation_id")
                if conversation_id:
                    details.append(f"conversation_id={conversation_id}")
                reason = item.get("reason")
                if reason:
                    details.append(f"reason={reason}")
                suffix = f" ({', '.join(details)})" if details else ""
                lines.append(
                    "    conversation checkpoint written: "
                    f"{item.get('checkpoint_id') or '?'}{suffix}"
                )
            elif t == "goal_submitted":
                details = []
                actor = item.get("actor")
                if actor:
                    details.append(f"actor={actor}")
                goal_type = item.get("goal_type")
                goal_subtype = item.get("goal_subtype")
                if goal_type and goal_type != "converse":
                    details.append(f"type={goal_type}")
                conversation_id = item.get("conversation_id")
                if conversation_id:
                    details.append(f"conversation_id={conversation_id}")
                if goal_subtype == "post_reply_hop":
                    reason = item.get("hop_reason")
                    if reason:
                        details.append(f"reason={reason}")
                    if item.get("hop_automatic") is not None:
                        details.append(
                            "automatic=yes" if item.get("hop_automatic") else "automatic=no"
                        )
                suffix = f" ({', '.join(details)})" if details else ""
                if goal_subtype == "post_reply_hop":
                    lines.append(f"    conversation hop queued: {item['goal']}{suffix}")
                elif goal_type == "converse":
                    lines.append(f"    conversation turn queued: {item['goal']}{suffix}")
                else:
                    lines.append(f"    goal submitted: {item['goal']}{suffix}")
            elif t == "run_finished":
                lines.append(f"    run {item['run']} finished ({item['reason']})")
            elif t == "plant_commissioned":
                lines.append(f"    plant commissioned: {item['plant']}")
    if state_diff:
        lines.append("\n  State changes:")
        for item in state_diff:
            lines.append(f"    {item['change']} {item['type']}: "
                         f"`{item['name']}` — {item['heading']}")
    lines.append("]")
    return "\n".join(lines)
