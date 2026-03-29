"""
Validated operator-message emission for the first gardener-owned note slice.

This surface currently supports exactly two message kinds:

- tend_survey
- recently_concluded

Conversation-origin emissions append to the originating conversation as the
canonical human record and then attempt a transcript-backed delivery copy.
Background/no-origin emissions are written out of band under inbox/<garden>/notes/.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib

from .channels import FilesystemChannel
from .conversations import (
    append_message,
    queue_external_append_hop,
    read_conversation,
    read_messages,
)
from .garden import filesystem_reply_dir, garden_paths, garden_root_path
from .goals import read_goal
from .validate import (
    ValidationResult,
    validate_operator_message_record,
    validate_operator_message_request,
)

_ENV_GARDEN_ROOT = "PAK2_GARDEN_ROOT"
_ENV_CURRENT_GOAL_ID = "PAK2_CURRENT_GOAL_ID"
_ENV_CURRENT_RUN_ID = "PAK2_CURRENT_RUN_ID"
_ENV_CURRENT_PLANT = "PAK2_CURRENT_PLANT"
_ENV_CURRENT_GOALS_DIR = "PAK2_CURRENT_GOALS_DIR"

OPERATOR_MESSAGE_SCHEMA_VERSION = 1
OPERATOR_MESSAGE_KIND_TEND_SURVEY = "tend_survey"
OPERATOR_MESSAGE_KIND_RECENTLY_CONCLUDED = "recently_concluded"

_OPERATOR_MESSAGE_RECORD_NAME = "operator-messages.jsonl"
_OPERATOR_NOTES_DIRNAME = "notes"
_OUT_OF_BAND_BASENAMES = {
    OPERATOR_MESSAGE_KIND_TEND_SURVEY: "state-note.md",
    OPERATOR_MESSAGE_KIND_RECENTLY_CONCLUDED: "recently-concluded.md",
}


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compact_ts(ts: str | None = None) -> str:
    value = ts or _now_utc()
    return value.replace("-", "").replace(":", "").replace("Z", "")


def _append_json_line(path: pathlib.Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _resolve_garden_root(root: pathlib.Path | None = None) -> pathlib.Path:
    if root is not None:
        return pathlib.Path(root)
    env_root = os.getenv(_ENV_GARDEN_ROOT, "").strip()
    if env_root:
        return pathlib.Path(env_root)
    return garden_root_path()


def _resolve_goals_dir(root: pathlib.Path, explicit: pathlib.Path | None = None) -> pathlib.Path:
    if explicit is not None:
        return pathlib.Path(explicit)
    env_goals_dir = os.getenv(_ENV_CURRENT_GOALS_DIR, "").strip()
    if env_goals_dir:
        return pathlib.Path(env_goals_dir)
    return garden_paths(garden_root=root).goals_dir


def operator_messages_path(
    run_id: str,
    *,
    _runs_dir: pathlib.Path | None = None,
    _garden_root: pathlib.Path | None = None,
) -> pathlib.Path:
    if _runs_dir is not None:
        return pathlib.Path(_runs_dir) / run_id / _OPERATOR_MESSAGE_RECORD_NAME
    return garden_paths(garden_root=_garden_root).runs_dir / run_id / _OPERATOR_MESSAGE_RECORD_NAME


def operator_notes_dir(
    root: pathlib.Path,
    *,
    ensure: bool = False,
) -> pathlib.Path:
    reply_dir = filesystem_reply_dir(root, ensure=ensure)
    notes_dir = reply_dir / _OPERATOR_NOTES_DIRNAME
    if ensure:
        notes_dir.mkdir(parents=True, exist_ok=True)
    return notes_dir


def read_operator_message_records(
    run_id: str,
    *,
    _runs_dir: pathlib.Path | None = None,
    _garden_root: pathlib.Path | None = None,
) -> list[dict]:
    path = operator_messages_path(run_id, _runs_dir=_runs_dir, _garden_root=_garden_root)
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def emit_tend_survey(
    content: str,
    *,
    origin: dict | None = None,
    _garden_root: pathlib.Path | None = None,
    _goals_dir: pathlib.Path | None = None,
    _now: str | None = None,
) -> tuple[ValidationResult, dict | None]:
    return _emit_operator_message(
        OPERATOR_MESSAGE_KIND_TEND_SURVEY,
        content,
        origin=origin,
        _garden_root=_garden_root,
        _goals_dir=_goals_dir,
        _now=_now,
    )


def emit_recently_concluded(
    content: str,
    *,
    origin: dict | None = None,
    _garden_root: pathlib.Path | None = None,
    _goals_dir: pathlib.Path | None = None,
    _now: str | None = None,
) -> tuple[ValidationResult, dict | None]:
    return _emit_operator_message(
        OPERATOR_MESSAGE_KIND_RECENTLY_CONCLUDED,
        content,
        origin=origin,
        _garden_root=_garden_root,
        _goals_dir=_goals_dir,
        _now=_now,
    )


def _emit_operator_message(
    kind: str,
    content: str,
    *,
    origin: dict | None = None,
    _garden_root: pathlib.Path | None = None,
    _goals_dir: pathlib.Path | None = None,
    _now: str | None = None,
) -> tuple[ValidationResult, dict | None]:
    now = _now or _now_utc()
    root = _garden_root_path(_garden_root)
    goal_id = os.getenv(_ENV_CURRENT_GOAL_ID, "").strip()
    run_id = os.getenv(_ENV_CURRENT_RUN_ID, "").strip()
    current_plant = os.getenv(_ENV_CURRENT_PLANT, "").strip()
    if not goal_id or not run_id:
        return ValidationResult.reject(
            "MISSING_RUNTIME_CONTEXT",
            "operator message emission requires current goal and run ids",
        ), None

    goals_dir = _resolve_goals_dir(root, explicit=_goals_dir)
    goal = read_goal(goal_id, _goals_dir=goals_dir)
    if goal is None:
        return ValidationResult.reject(
            "GOAL_NOT_FOUND",
            f"current goal {goal_id!r} was not found for operator message emission",
        ), None
    current_plant = current_plant or str(goal.get("assigned_to") or "")
    if current_plant and current_plant != "gardener":
        return ValidationResult.reject(
            "OPERATOR_MESSAGE_NOT_AUTHORIZED",
            f"operator message emission is gardener-only in this slice, got plant {current_plant!r}",
        ), None
    goal_type = str(goal.get("type") or "")
    if kind == OPERATOR_MESSAGE_KIND_TEND_SURVEY and goal_type != "tend":
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE_CONTEXT",
            f"{kind} is only valid from tend runs, got goal type {goal_type!r}",
        ), None
    if kind == OPERATOR_MESSAGE_KIND_RECENTLY_CONCLUDED and goal_type in {"converse", "tend"}:
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE_CONTEXT",
            (
                f"{kind} is only valid for durable non-converse, non-tend runs, "
                f"got goal type {goal_type!r}"
            ),
        ), None

    origin_data = origin if origin is not None else _goal_origin(goal)
    request = {
        "kind": kind,
        "sender": "garden",
        "content": content,
        "source_goal_id": goal_id,
        "source_run_id": run_id,
    }
    if origin_data is not None:
        request["origin"] = origin_data
    result = validate_operator_message_request(request)
    if not result.ok:
        return result, None

    if origin_data is not None:
        emit_result = _emit_conversation_backed(
            root=root,
            now=now,
            goal=goal,
            run_id=run_id,
            kind=kind,
            content=content,
            origin=origin_data,
        )
    else:
        emit_result = _emit_out_of_band(
            root=root,
            now=now,
            goal_id=goal_id,
            run_id=run_id,
            kind=kind,
            content=content,
        )
    if not emit_result[0].ok:
        return emit_result
    return emit_result


def _garden_root_path(root: pathlib.Path | None) -> pathlib.Path:
    return pathlib.Path(_resolve_garden_root(root)).resolve()


def _goal_origin(goal: dict) -> dict | None:
    origin = goal.get("origin")
    return origin if isinstance(origin, dict) else None


def _emit_conversation_backed(
    *,
    root: pathlib.Path,
    now: str,
    goal: dict,
    run_id: str,
    kind: str,
    content: str,
    origin: dict,
) -> tuple[ValidationResult, dict | None]:
    paths = garden_paths(garden_root=root)
    conversation_id = str(origin["conversation_id"])
    conversation = read_conversation(conversation_id, _conv_dir=paths.conversations_dir)
    if conversation is None:
        return ValidationResult.reject(
            "CONVERSATION_NOT_FOUND",
            f"origin conversation {conversation_id!r} was not found",
        ), None
    if conversation.get("status") != "open":
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE_CONTEXT",
            f"origin conversation {conversation_id!r} is not open",
        ), None

    existing_records = [
        record
        for record in read_operator_message_records(run_id, _garden_root=root)
        if record.get("kind") == kind
    ]
    if existing_records:
        return ValidationResult.accept(), existing_records[-1]

    message_id = _stable_operator_message_id(run_id, kind)
    delivery_path = None
    if conversation.get("channel") == "filesystem":
        delivery_path = _filesystem_reply_copy_path(root, conversation_id, now=now)

    existing_message = _find_existing_conversation_message(
        conversation_id,
        message_id,
        content=content,
        reply_to=origin.get("message_id"),
        _conv_dir=paths.conversations_dir,
    )
    record_emitted_at = now
    if existing_message is not None:
        record_emitted_at = str(existing_message.get("ts") or now)
        delivery_path = None

    record = {
        "schema_version": OPERATOR_MESSAGE_SCHEMA_VERSION,
        "kind": kind,
        "sender": "garden",
        "origin": dict(origin),
        "transcript_policy": "canonical",
        "delivery_policy": "reply_copy",
        "emitted_at": record_emitted_at,
        "source_goal_id": goal["id"],
        "source_run_id": run_id,
        "conversation_message_id": message_id,
    }
    if delivery_path is not None:
        record["delivery_path"] = delivery_path
    result = validate_operator_message_record(record)
    if not result.ok:
        return result, None

    if existing_message is None:
        append_result, message_id = append_message(
            conversation_id,
            "garden",
            content,
            channel=conversation.get("channel") or "filesystem",
            reply_to=origin.get("message_id"),
            _conv_dir=paths.conversations_dir,
            _now=now,
            _message_id=message_id,
        )
        if not append_result.ok or message_id is None:
            return append_result, None

    queue_external_append_hop(
        conversation_id,
        source_goal=goal,
        source_run_id=run_id,
        trigger_message_id=message_id,
        _conv_dir=paths.conversations_dir,
        _goals_dir=paths.goals_dir,
        _events_path=paths.coordinator_events_path,
        _now=now,
    )

    if existing_message is None and conversation.get("channel") == "filesystem":
        _send_filesystem_reply_copy(root, conversation_id, content, now=now)
    return _persist_record(record, root=root, run_id=run_id)


def _emit_out_of_band(
    *,
    root: pathlib.Path,
    now: str,
    goal_id: str,
    run_id: str,
    kind: str,
    content: str,
) -> tuple[ValidationResult, dict | None]:
    delivery_path = _write_out_of_band_note(root, kind, content, now=now)
    record = {
        "schema_version": OPERATOR_MESSAGE_SCHEMA_VERSION,
        "kind": kind,
        "sender": "garden",
        "transcript_policy": "none",
        "delivery_policy": "out_of_band_note",
        "emitted_at": now,
        "source_goal_id": goal_id,
        "source_run_id": run_id,
        "delivery_path": delivery_path,
    }
    return _persist_record(record, root=root, run_id=run_id)


def _persist_record(
    record: dict,
    *,
    root: pathlib.Path,
    run_id: str,
) -> tuple[ValidationResult, dict | None]:
    result = validate_operator_message_record(record)
    if not result.ok:
        return result, None
    path = operator_messages_path(run_id, _garden_root=root)
    try:
        _append_json_line(path, record)
    except OSError as exc:
        return ValidationResult.reject("IO_ERROR", str(exc)), None
    return ValidationResult.accept(), record


def _runtime_relative_path(root: pathlib.Path, path: pathlib.Path) -> str:
    runtime_root = garden_paths(garden_root=root).runtime_root.resolve()
    return str(path.resolve().relative_to(runtime_root))


def _filesystem_reply_copy_path(root: pathlib.Path, conversation_id: str, *, now: str) -> str:
    reply_dir = filesystem_reply_dir(root, ensure=True)
    slug = conversation_id.replace("/", "-")[:40]
    path = reply_dir / f"{_reply_copy_timestamp(now)}-{slug}.md"
    return _runtime_relative_path(root, path)


def _reply_copy_timestamp(now: str) -> str:
    return datetime.datetime.fromisoformat(now.replace("Z", "+00:00")).strftime("%Y%m%dT%H%M%SZ")


def _send_filesystem_reply_copy(
    root: pathlib.Path,
    conversation_id: str,
    content: str,
    *,
    now: str,
) -> str:
    path = _filesystem_reply_copy_path(root, conversation_id, now=now)
    FilesystemChannel(root).send(conversation_id, content, now=now)
    return path


def _stable_operator_message_id(run_id: str, kind: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{kind}".encode("utf-8")).hexdigest()
    compact = f"{int(digest[:16], 16) % (10 ** 14):014d}"
    return f"msg-{compact}-gar-{digest[16:20]}"


def _find_existing_conversation_message(
    conversation_id: str,
    message_id: str,
    *,
    content: str,
    reply_to: str | None,
    _conv_dir: pathlib.Path,
) -> dict | None:
    for message in read_messages(conversation_id, _conv_dir=_conv_dir):
        if message.get("id") != message_id:
            continue
        if message.get("sender") != "garden":
            return None
        if message.get("content") != content:
            return None
        if message.get("reply_to") != reply_to:
            return None
        return message
    return None


def _write_out_of_band_note(
    root: pathlib.Path,
    kind: str,
    content: str,
    *,
    now: str,
) -> str:
    notes_dir = operator_notes_dir(root, ensure=True)
    basename = _OUT_OF_BAND_BASENAMES[kind]
    path = notes_dir / f"{_compact_ts(now)}-{basename}"
    path.write_text(content, encoding="utf-8")
    return _runtime_relative_path(root, path)


__all__ = [
    "OPERATOR_MESSAGE_KIND_RECENTLY_CONCLUDED",
    "OPERATOR_MESSAGE_KIND_TEND_SURVEY",
    "OPERATOR_MESSAGE_SCHEMA_VERSION",
    "emit_recently_concluded",
    "emit_tend_survey",
    "operator_messages_path",
    "operator_notes_dir",
    "read_operator_message_records",
]
