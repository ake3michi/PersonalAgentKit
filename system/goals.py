"""
Goal store: read, write, and transition goals in goals/.

Goals are submitted through submit_goal() — never written directly.
State transitions are enforced here; the event log is written on every change.
"""

import json
import pathlib
import re
import datetime
import random
import string

from .validate import (
    ValidationResult,
    validate_dispatch_packet,
    validate_goal,
    validate_goal_supplement,
)
from .events import append_event
from .garden import discover_garden_root, garden_paths
from .plant_commission import PLANT_COMMISSION_GOAL_SUBTYPE, plant_commission_payload
from .tend import tend_event_metadata

_GOALS_DIR = garden_paths().goals_dir
_SUPPLEMENTS_DIR_NAME = "supplements"
_DISPATCH_PACKET_NAME = "dispatch-packet.json"
_SPAWN_EVAL_ELIGIBLE_TYPES = frozenset({"build", "fix"})

# Valid transitions: from_status -> set of allowed to_statuses
_TRANSITIONS: dict[str, set[str]] = {
    "queued":     {"dispatched", "closed"},
    "dispatched": {"running", "queued", "closed"},
    "running":    {"completed", "closed"},
    "completed":  {"evaluating", "closed"},
    "evaluating": {"closed"},
    "closed":     set(),
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")
def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text: str, max_len: int = 40) -> str:
    lowered = text.lower()
    slug = _SLUG_RE.sub("-", lowered).strip("-")
    return slug[:max_len].rstrip("-")


def _next_id() -> int:
    """Return the next goal sequence number (max existing + 1, or 1)."""
    existing = []
    if _GOALS_DIR.exists():
        for p in _GOALS_DIR.iterdir():
            if p.suffix == ".json":
                try:
                    n = int(p.stem.split("-")[0])
                    existing.append(n)
                except (ValueError, IndexError):
                    pass
    return max(existing, default=0) + 1


def _goal_path(goal_id: str) -> pathlib.Path:
    return _GOALS_DIR / f"{goal_id}.json"


def _supplements_dir(goals_dir: pathlib.Path) -> pathlib.Path:
    return goals_dir / _SUPPLEMENTS_DIR_NAME


def _supplements_path(goal_id: str, goals_dir: pathlib.Path) -> pathlib.Path:
    return _supplements_dir(goals_dir) / f"{goal_id}.jsonl"


def _dispatch_packet_path(run_id: str, runs_dir: pathlib.Path) -> pathlib.Path:
    return runs_dir / run_id / _DISPATCH_PACKET_NAME


def _storage_root_for(directory: pathlib.Path) -> pathlib.Path:
    return directory.parent


def _events_path_for_goals_dir(goals_dir: pathlib.Path) -> pathlib.Path:
    return _storage_root_for(goals_dir) / "events" / "coordinator.jsonl"


def _runs_dir_for_goals_dir(goals_dir: pathlib.Path) -> pathlib.Path:
    return _storage_root_for(goals_dir) / "runs"


def _compact_ts(ts: str | None = None) -> str:
    value = ts or _now_utc()
    return value.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")


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
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _append_json_line(path: pathlib.Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _goal_origin(goal: dict) -> dict:
    origin = goal.get("origin")
    return origin if isinstance(origin, dict) else {}


def _goal_update_policy(goal: dict) -> str | None:
    updates = goal.get("pre_dispatch_updates")
    if not isinstance(updates, dict):
        return None
    policy = updates.get("policy")
    return str(policy) if policy is not None else None


def _goal_submitted_from(goal: dict) -> dict:
    source = goal.get("submitted_from")
    return source if isinstance(source, dict) else {}


def _goal_allows_pre_dispatch_supplements(goal: dict) -> bool:
    origin = _goal_origin(goal)
    return (
        goal.get("type") != "converse"
        and origin.get("kind") == "conversation"
        and _goal_update_policy(goal) == "supplement"
    )


def _goal_event_metadata(goal: dict) -> dict:
    metadata: dict = {}

    goal_type = goal.get("type")
    if goal_type:
        metadata["goal_type"] = goal_type

    priority = goal.get("priority")
    if isinstance(priority, int) and not isinstance(priority, bool):
        metadata["goal_priority"] = priority

    conversation_id = goal.get("conversation_id")
    origin = _goal_origin(goal)
    origin_kind = origin.get("kind")
    if origin_kind:
        metadata["goal_origin"] = origin_kind
        if not conversation_id and origin_kind == "conversation":
            conversation_id = origin.get("conversation_id")
    if conversation_id:
        metadata["conversation_id"] = conversation_id
    source_message_id = goal.get("source_message_id")
    if not source_message_id and origin_kind == "conversation":
        source_message_id = origin.get("message_id")
    if source_message_id:
        metadata["source_message_id"] = source_message_id

    submitted_from = _goal_submitted_from(goal)
    source_goal_id = submitted_from.get("goal_id")
    if source_goal_id:
        metadata["source_goal_id"] = source_goal_id
    source_run_id = submitted_from.get("run_id")
    if source_run_id:
        metadata["source_run_id"] = source_run_id

    hop = goal.get("post_reply_hop") or {}
    if hop:
        metadata["goal_subtype"] = "post_reply_hop"
        if hop.get("automatic") is not None:
            metadata["hop_automatic"] = bool(hop.get("automatic"))
        requested_by = hop.get("requested_by")
        if requested_by:
            metadata["hop_requested_by"] = requested_by
        reason = hop.get("reason")
        if reason:
            metadata["hop_reason"] = reason

    if "goal_subtype" not in metadata and plant_commission_payload(goal):
        metadata["goal_subtype"] = PLANT_COMMISSION_GOAL_SUBTYPE

    metadata.update(tend_event_metadata(goal))

    return metadata


def _plants_dir_for_goals_dir(goals_dir: pathlib.Path) -> pathlib.Path:
    return garden_paths(garden_root=discover_garden_root(goals_dir)).plants_dir


def _events_path_for_goal_write(goals_dir: pathlib.Path,
                                default_paths=None) -> pathlib.Path:
    if default_paths is not None:
        return default_paths.coordinator_events_path
    return _events_path_for_goals_dir(goals_dir)


def _write_goal_record(record: dict, *, goals_dir: pathlib.Path,
                       events_path: pathlib.Path, now: str) -> None:
    goals_dir.mkdir(parents=True, exist_ok=True)
    goal_file = goals_dir / f"{record['id']}.json"
    goal_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    append_event({
        "ts": now,
        "type": "GoalSubmitted",
        "actor": record["submitted_by"],
        "goal": record["id"],
        "goal_type": record["type"],
        **_goal_event_metadata(record),
    }, path=events_path)


def _goal_should_spawn_eval(goal: dict) -> bool:
    return (
        bool(goal.get("spawn_eval"))
        and goal.get("type") in _SPAWN_EVAL_ELIGIBLE_TYPES
    )


def _existing_eval_goal_for_parent(parent_goal_id: str, *,
                                   goals_dir: pathlib.Path) -> dict | None:
    for goal in list_goals(_goals_dir=goals_dir):
        if goal.get("type") == "evaluate" and goal.get("parent_goal") == parent_goal_id:
            return goal
    return None


def _render_spawn_eval_body(parent_goal: dict) -> str:
    return (
        "Evaluate the completed results of goal "
        f"{parent_goal['id']} ({parent_goal['type']})."
    )


def ensure_spawned_eval_goal(parent_goal_id: str, *,
                             _goals_dir: pathlib.Path | None = None,
                             _now: str | None = None) -> tuple[ValidationResult, str | None]:
    """
    Create the single system-spawned evaluate goal for a parent goal, if needed.

    Returns the existing or newly-created evaluate goal id. If the parent goal is
    not currently eligible for spawn_eval, returns (accept, None).
    """
    goals_dir = _goals_dir if _goals_dir is not None else _GOALS_DIR
    default_paths = garden_paths() if _goals_dir is None else None
    now = _now or _now_utc()

    parent_goal = read_goal(parent_goal_id, _goals_dir=goals_dir)
    if parent_goal is None:
        return ValidationResult.reject("GOAL_NOT_FOUND", parent_goal_id), None
    if not _goal_should_spawn_eval(parent_goal):
        return ValidationResult.accept(), None

    existing = _existing_eval_goal_for_parent(parent_goal_id, goals_dir=goals_dir)
    if existing is not None:
        return ValidationResult.accept(), existing["id"]

    body = _render_spawn_eval_body(parent_goal)
    slug = _slugify(body) or "evaluate"
    n = _next_id() if _goals_dir is None else _next_id_in(goals_dir)
    eval_goal_id = f"{n}-{slug}"

    record = {
        "id": eval_goal_id,
        "status": "queued",
        "submitted_at": now,
        "type": "evaluate",
        "submitted_by": "system",
        "body": body,
        "parent_goal": parent_goal_id,
    }
    for field in ("assigned_to", "priority", "driver", "model", "reasoning_effort"):
        if field in parent_goal:
            record[field] = parent_goal[field]

    result = validate_goal(record, _plants_dir=_plants_dir_for_goals_dir(goals_dir))
    if not result.ok:
        return result, None

    events_path = _events_path_for_goal_write(goals_dir, default_paths)
    _write_goal_record(record, goals_dir=goals_dir, events_path=events_path, now=now)
    append_event({
        "ts": now,
        "type": "EvalSpawned",
        "actor": "system",
        "goal": parent_goal_id,
        "eval_goal": eval_goal_id,
        **_goal_event_metadata(parent_goal),
    }, path=events_path)

    return ValidationResult.accept(), eval_goal_id


def _close_parent_after_eval(goal: dict, *, actor: str, closed_reason: str,
                             goals_dir: pathlib.Path,
                             now: str,
                             events_path: pathlib.Path) -> None:
    if goal.get("type") != "evaluate":
        return
    parent_goal_id = goal.get("parent_goal")
    if not parent_goal_id:
        return
    parent_goal = read_goal(parent_goal_id, _goals_dir=goals_dir)
    if parent_goal is None or parent_goal.get("status") != "evaluating":
        return

    result = transition_goal(
        parent_goal_id,
        "closed",
        actor=actor,
        closed_reason=closed_reason,
        _goals_dir=goals_dir,
        _now=now,
    )
    if not result.ok:
        return

    parent_goal = read_goal(parent_goal_id, _goals_dir=goals_dir)
    if parent_goal is None:
        return
    append_event({
        "ts": now,
        "type": "EvalClosed",
        "actor": actor,
        "goal": parent_goal_id,
        "eval_goal": goal["id"],
        "goal_reason": closed_reason,
        **_goal_event_metadata(parent_goal),
    }, path=events_path)


def submit_goal(data: dict, *, _goals_dir: pathlib.Path | None = None,
                _now: str | None = None) -> tuple[ValidationResult, str | None]:
    """
    Validate and persist a new goal. Returns (result, goal_id).
    goal_id is None on failure.
    """
    goals_dir = _goals_dir if _goals_dir is not None else _GOALS_DIR
    default_paths = garden_paths() if _goals_dir is None else None

    # Fields the caller must not supply
    for forbidden in ("id", "status", "submitted_at", "parent_goal", "closed_reason"):
        if forbidden in data:
            return ValidationResult.reject(
                "SUBMISSION_REJECTED", f"field '{forbidden}' is system-assigned"
            ), None

    now = _now or _now_utc()
    slug = _slugify(data.get("body", "goal")) or "goal"
    n = _next_id() if _goals_dir is None else _next_id_in(goals_dir)
    goal_id = f"{n}-{slug}"

    record = {
        "id": goal_id,
        "status": "queued",
        "submitted_at": now,
        **data,
    }

    plants_dir = (
        default_paths.plants_dir
        if default_paths is not None
        else garden_paths(garden_root=discover_garden_root(goals_dir)).plants_dir
    )
    result = validate_goal(record, _plants_dir=plants_dir)
    if not result.ok:
        return result, None

    events_path = _events_path_for_goal_write(goals_dir, default_paths)
    _write_goal_record(record, goals_dir=goals_dir, events_path=events_path, now=now)

    return ValidationResult.accept(), goal_id


def _next_id_in(goals_dir: pathlib.Path) -> int:
    existing = []
    if goals_dir.exists():
        for p in goals_dir.iterdir():
            if p.suffix == ".json":
                try:
                    n = int(p.stem.split("-")[0])
                    existing.append(n)
                except (ValueError, IndexError):
                    pass
    return max(existing, default=0) + 1


def read_goal(goal_id: str, *, _goals_dir: pathlib.Path | None = None) -> dict | None:
    """Return goal record or None if not found."""
    goals_dir = _goals_dir if _goals_dir is not None else _GOALS_DIR
    p = goals_dir / f"{goal_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_goals(status: str | None = None,
               *, _goals_dir: pathlib.Path | None = None) -> list[dict]:
    """Return all goals, optionally filtered by status."""
    goals_dir = _goals_dir if _goals_dir is not None else _GOALS_DIR
    if not goals_dir.exists():
        return []
    goals = []
    for p in sorted(goals_dir.iterdir()):
        if p.suffix == ".json":
            try:
                g = json.loads(p.read_text(encoding="utf-8"))
                if status is None or g.get("status") == status:
                    goals.append(g)
            except (json.JSONDecodeError, OSError):
                pass
    return goals


def append_goal_supplement(goal_id: str, data: dict, *,
                           _goals_dir: pathlib.Path | None = None,
                           _now: str | None = None) -> tuple[ValidationResult, str | None]:
    """
    Append an immutable pre-dispatch supplement to a queued goal.

    Supplements are only allowed on queued conversation-originated non-converse
    goals that explicitly opt into the supplement policy.
    """
    goals_dir = _goals_dir if _goals_dir is not None else _GOALS_DIR
    default_paths = garden_paths() if _goals_dir is None else None
    goal = read_goal(goal_id, _goals_dir=goals_dir)
    if goal is None:
        return ValidationResult.reject("GOAL_NOT_FOUND", goal_id), None
    if goal.get("status") != "queued":
        return ValidationResult.reject(
            "GOAL_NOT_QUEUED",
            f"goal must be queued to accept supplements, got: {goal.get('status')!r}",
        ), None
    if not _goal_allows_pre_dispatch_supplements(goal):
        return ValidationResult.reject(
            "SUPPLEMENTS_NOT_ALLOWED",
            "goal does not allow pre-dispatch supplements",
        ), None
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "supplement payload must be a JSON object",
        ), None

    now = _now or _now_utc()
    supplement_id = f"supp-{_compact_ts(now)}-{_random_suffix()}"
    record = {
        "id": supplement_id,
        "goal": goal_id,
        "ts": now,
        "actor": str(data.get("actor") or ""),
        "source": data.get("source"),
        "kind": str(data.get("kind") or "").strip(),
        "content": str(data.get("content") or "").rstrip(),
    }
    for field in ("source_goal_id", "source_run_id"):
        if field in data:
            record[field] = data[field]

    result = validate_goal_supplement(record)
    if not result.ok:
        return result, None

    origin = _goal_origin(goal)
    conversation_id = record["source"]["conversation_id"]
    if conversation_id != origin.get("conversation_id"):
        return ValidationResult.reject(
            "SUPPLEMENT_SOURCE_MISMATCH",
            "supplement source conversation does not match the goal origin",
        ), None

    path = _supplements_path(goal_id, goals_dir)
    _append_json_line(path, record)

    events_path = (
        default_paths.coordinator_events_path
        if default_paths is not None
        else _events_path_for_goals_dir(goals_dir)
    )
    append_event({
        "ts": now,
        "type": "GoalSupplemented",
        "actor": record["actor"],
        "goal": goal_id,
        "conversation_id": conversation_id,
        "source_message_id": record["source"].get("message_id"),
        "supplement_id": supplement_id,
        "supplement_kind": record["kind"],
        "supplement_chars": len(record["content"]),
    }, path=events_path)

    return ValidationResult.accept(), supplement_id


def list_goal_supplements(goal_id: str, *,
                          _goals_dir: pathlib.Path | None = None) -> list[dict]:
    goals_dir = _goals_dir if _goals_dir is not None else _GOALS_DIR
    return _read_json_lines(_supplements_path(goal_id, goals_dir))


def materialize_dispatch_packet(goal: dict, run_id: str, cutoff: str, *,
                                _goals_dir: pathlib.Path | None = None,
                                _runs_dir: pathlib.Path | None = None) -> tuple[ValidationResult, dict | None]:
    """
    Freeze the exact dispatch packet for a run.

    Only conversation-originated non-converse goals that opt into supplement
    handling get a dispatch packet artifact.
    """
    if not _goal_allows_pre_dispatch_supplements(goal):
        return ValidationResult.accept(), None

    goals_dir = _goals_dir if _goals_dir is not None else _GOALS_DIR
    default_paths = garden_paths() if _goals_dir is None else None
    runs_dir = (
        _runs_dir
        if _runs_dir is not None
        else (
            default_paths.runs_dir
            if default_paths is not None
            else _runs_dir_for_goals_dir(goals_dir)
        )
    )

    items = [
        record
        for record in list_goal_supplements(goal["id"], _goals_dir=goals_dir)
        if str(record.get("ts") or "") <= cutoff
    ]
    items.sort(key=lambda record: (str(record.get("ts") or ""), str(record.get("id") or "")))

    origin = dict(_goal_origin(goal))
    packet = {
        "goal_id": goal["id"],
        "run_id": run_id,
        "cutoff": cutoff,
        "origin": origin,
        "goal_body": goal.get("body", ""),
        "supplement_count": len(items),
        "supplement_chars": sum(len(str(item.get("content") or "")) for item in items),
        "supplements": items,
    }

    result = validate_dispatch_packet(packet)
    if not result.ok:
        return result, None

    path = _dispatch_packet_path(run_id, runs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")

    events_path = (
        default_paths.coordinator_events_path
        if default_paths is not None
        else _events_path_for_goals_dir(goals_dir)
    )
    append_event({
        "ts": cutoff,
        "type": "DispatchPacketMaterialized",
        "actor": "system",
        "goal": goal["id"],
        "run": run_id,
        "conversation_id": origin.get("conversation_id"),
        "source_message_id": origin.get("message_id"),
        "packet_path": str(path.relative_to(runs_dir.parent)),
        "supplement_count": packet["supplement_count"],
        "supplement_chars": packet["supplement_chars"],
        "supplement_ids": [item.get("id") for item in items],
    }, path=events_path)

    return ValidationResult.accept(), packet


def transition_goal(goal_id: str, to_status: str, *,
                    actor: str = "system",
                    closed_reason: str | None = None,
                    _goals_dir: pathlib.Path | None = None,
                    _now: str | None = None) -> ValidationResult:
    """
    Transition a goal to a new status. Enforces the state machine.
    closed_reason is required when to_status is 'closed'.
    """
    goals_dir = _goals_dir if _goals_dir is not None else _GOALS_DIR
    default_paths = garden_paths() if _goals_dir is None else None
    goal = read_goal(goal_id, _goals_dir=goals_dir)
    if goal is None:
        return ValidationResult.reject("GOAL_NOT_FOUND", goal_id)

    from_status = goal["status"]
    allowed = _TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        return ValidationResult.reject(
            "INVALID_TRANSITION",
            f"{from_status} → {to_status} is not allowed",
        )

    if to_status == "closed" and not closed_reason:
        return ValidationResult.reject("MISSING_CLOSED_REASON",
                                       "closed_reason required when closing a goal")

    now = _now or _now_utc()
    goal["status"] = to_status
    if to_status == "closed":
        goal["closed_reason"] = closed_reason

    goal_file = goals_dir / f"{goal_id}.json"
    goal_file.write_text(json.dumps(goal, indent=2) + "\n", encoding="utf-8")
    events_path = (
        default_paths.coordinator_events_path
        if default_paths is not None
        else _events_path_for_goals_dir(goals_dir)
    )

    event: dict = {
        "ts": now,
        "type": "GoalTransitioned",
        "actor": actor,
        "goal": goal_id,
        "from": from_status,
        "to": to_status,
        **_goal_event_metadata(goal),
    }
    append_event(event, path=events_path)

    if to_status == "closed":
        append_event({
            "ts": now,
            "type": "GoalClosed",
            "actor": actor,
            "goal": goal_id,
            "goal_reason": closed_reason,
            **_goal_event_metadata(goal),
        }, path=events_path)
        _close_parent_after_eval(
            goal,
            actor=actor,
            closed_reason=closed_reason,
            goals_dir=goals_dir,
            now=now,
            events_path=events_path,
        )

    return ValidationResult.accept()
