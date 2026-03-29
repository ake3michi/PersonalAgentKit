"""
Helpers for the plant-local active-threads artifact.

The artifact is agent-maintained, but the write path goes through this module
so the schema and relation checks stay consistent.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re

from .events import append_event, coordinator_events_path
from .garden import garden_root_path
from .validate import ValidationResult, validate_active_threads

_PLANTS_DIR = pathlib.Path("plants")
_ACTIVE_THREADS_NAME = "active-threads.json"
_ENV_GARDEN_ROOT = "PAK2_GARDEN_ROOT"
_ENV_CURRENT_GOAL_ID = "PAK2_CURRENT_GOAL_ID"
_ENV_CURRENT_RUN_ID = "PAK2_CURRENT_RUN_ID"
_ENV_CURRENT_PLANT = "PAK2_CURRENT_PLANT"
_GOAL_ID_RE = re.compile(r"^[1-9][0-9]*-[a-z0-9-]+$")
_RUN_ID_RE = re.compile(r"^(?P<goal>[1-9][0-9]*-[a-z0-9-]+)-r[1-9][0-9]*$")
_ACTOR_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _garden_root(*, _plants_dir: pathlib.Path | None = None) -> pathlib.Path:
    if _plants_dir is not None:
        return pathlib.Path(_plants_dir).resolve().parent
    return garden_root_path().resolve()


def _relative_active_threads_path(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _current_actor(plant: str) -> str:
    actor = os.getenv(_ENV_CURRENT_PLANT)
    if actor and _ACTOR_RE.match(actor):
        return actor
    return plant


def _goal_from_run_id(run_id: str | None) -> str | None:
    if not run_id:
        return None
    match = _RUN_ID_RE.match(run_id)
    if not match:
        return None
    return match.group("goal")


def _event_goal_and_run(data: object) -> tuple[str | None, str | None]:
    goal_id = os.getenv(_ENV_CURRENT_GOAL_ID)
    run_id = os.getenv(_ENV_CURRENT_RUN_ID)

    if run_id and not _RUN_ID_RE.match(run_id):
        run_id = None
    if goal_id and not _GOAL_ID_RE.match(goal_id):
        goal_id = None

    if run_id is None:
        candidate_run = data.get("captured_by_run") if isinstance(data, dict) else None
        if isinstance(candidate_run, str) and _RUN_ID_RE.match(candidate_run):
            run_id = candidate_run

    if goal_id is None:
        goal_id = _goal_from_run_id(run_id)

    return goal_id, run_id


def _refresh_event_fields(
    *,
    plant: str,
    path: pathlib.Path,
    root: pathlib.Path,
    data: object,
) -> dict[str, str]:
    goal_id, run_id = _event_goal_and_run(data)
    event = {
        "actor": _current_actor(plant),
        "plant": plant,
        "active_threads_path": _relative_active_threads_path(path, root),
    }
    if goal_id:
        event["goal"] = goal_id
    if run_id:
        event["run"] = run_id
    return event


def active_threads_path(plant: str, *, _plants_dir: pathlib.Path | None = None) -> pathlib.Path:
    plants_dir = _plants_dir if _plants_dir is not None else _PLANTS_DIR
    return plants_dir / plant / "memory" / _ACTIVE_THREADS_NAME


def read_active_threads(plant: str, *, _plants_dir: pathlib.Path | None = None) -> dict | None:
    path = active_threads_path(plant, _plants_dir=_plants_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_active_threads(
    plant: str,
    data: dict,
    *,
    _plants_dir: pathlib.Path | None = None,
) -> ValidationResult:
    path = active_threads_path(plant, _plants_dir=_plants_dir)
    root = _garden_root(_plants_dir=_plants_dir)
    event_fields = _refresh_event_fields(plant=plant, path=path, root=root, data=data)

    started = append_event(
        {
            "ts": _now_utc(),
            "type": "ActiveThreadsRefreshStarted",
            **event_fields,
        },
        path=coordinator_events_path(root),
    )
    if not started.ok:
        return started

    result = validate_active_threads(data)
    if not result.ok:
        append_result = append_event(
            {
                "ts": _now_utc(),
                "type": "ActiveThreadsRefreshFinished",
                "active_threads_outcome": "validation_rejected",
                "active_threads_reason": result.reason,
                "detail": result.detail,
                **event_fields,
            },
            path=coordinator_events_path(root),
        )
        if not append_result.ok:
            return append_result
        return result

    if data.get("plant") != plant:
        result = ValidationResult.reject(
            "INVALID_ACTIVE_THREADS_PLANT",
            f"artifact plant {data.get('plant')!r} does not match target plant {plant!r}",
        )
        append_result = append_event(
            {
                "ts": _now_utc(),
                "type": "ActiveThreadsRefreshFinished",
                "active_threads_outcome": "validation_rejected",
                "active_threads_reason": result.reason,
                "detail": result.detail,
                **event_fields,
            },
            path=coordinator_events_path(root),
        )
        if not append_result.ok:
            return append_result
        return result

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        result = ValidationResult.reject("IO_ERROR", str(exc))
        append_result = append_event(
            {
                "ts": _now_utc(),
                "type": "ActiveThreadsRefreshFinished",
                "active_threads_outcome": "io_error",
                "active_threads_reason": result.reason,
                "detail": result.detail,
                **event_fields,
            },
            path=coordinator_events_path(root),
        )
        if not append_result.ok:
            return append_result
        return result

    append_result = append_event(
        {
            "ts": _now_utc(),
            "type": "ActiveThreadsRefreshFinished",
            "active_threads_outcome": "success",
            **event_fields,
        },
        path=coordinator_events_path(root),
    )
    if not append_result.ok:
        return append_result
    return ValidationResult.accept()
