"""
Dashboard invocation observability.

The dashboard stays read-only with respect to garden execution state, but the
CLI now emits explicit start/finish events and persists one invocation cost
record for each completed dashboard session.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import random
import re
import string
from dataclasses import dataclass

from .events import append_event, coordinator_events_path
from .garden import garden_paths
from .validate import ValidationResult, validate_dashboard_invocation

_ENV_CURRENT_GOAL_ID = "PAK2_CURRENT_GOAL_ID"
_ENV_CURRENT_RUN_ID = "PAK2_CURRENT_RUN_ID"
_ENV_CURRENT_PLANT = "PAK2_CURRENT_PLANT"

_DASHBOARD_INVOCATIONS_DIR = pathlib.Path("dashboard") / "invocations"
_ACTOR_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_GOAL_ID_RE = re.compile(r"^[1-9][0-9]*-[a-z0-9-]+$")
_RUN_ID_RE = re.compile(r"^[1-9][0-9]*-[a-z0-9-]+-r[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class DashboardInvocationContext:
    invocation_id: str
    root: pathlib.Path
    actor: str
    started_at: str
    mode: str
    refresh_seconds: float
    tty: bool
    source_goal_id: str | None = None
    source_run_id: str | None = None


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compact_ts(ts: str) -> str:
    return ts.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")


def _random_suffix(length: int = 4) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _parse_iso8601(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(
        datetime.timezone.utc
    )


def _current_actor() -> str:
    actor = os.getenv(_ENV_CURRENT_PLANT)
    if actor and _ACTOR_RE.match(actor):
        return actor
    return "operator"


def _current_source_ids() -> tuple[str | None, str | None]:
    goal_id = os.getenv(_ENV_CURRENT_GOAL_ID)
    run_id = os.getenv(_ENV_CURRENT_RUN_ID)
    return (
        goal_id if goal_id and _GOAL_ID_RE.match(goal_id) else None,
        run_id if run_id and _RUN_ID_RE.match(run_id) else None,
    )


def _source_event_metadata(
    source_goal_id: str | None,
    source_run_id: str | None,
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if source_goal_id:
        metadata["source_goal_id"] = source_goal_id
    if source_run_id:
        metadata["source_run_id"] = source_run_id
    return metadata


def dashboard_invocation_path(root: pathlib.Path, invocation_id: str) -> pathlib.Path:
    paths = garden_paths(garden_root=pathlib.Path(root).resolve())
    return paths.dashboard_invocations_dir / f"{invocation_id}.json"


def start_dashboard_invocation(
    root: pathlib.Path,
    *,
    mode: str,
    refresh_seconds: float,
    tty: bool,
    started_at: str | None = None,
) -> tuple[ValidationResult, DashboardInvocationContext | None]:
    root = pathlib.Path(root).resolve()
    now = started_at or _now_utc()
    invocation_id = f"dash-{_compact_ts(now)}-{_random_suffix()}"
    source_goal_id, source_run_id = _current_source_ids()
    context = DashboardInvocationContext(
        invocation_id=invocation_id,
        root=root,
        actor=_current_actor(),
        started_at=now,
        mode=mode,
        refresh_seconds=float(refresh_seconds),
        tty=bool(tty),
        source_goal_id=source_goal_id,
        source_run_id=source_run_id,
    )
    result = append_event(
        {
            "ts": now,
            "type": "DashboardInvocationStarted",
            "actor": context.actor,
            "dashboard_invocation_id": context.invocation_id,
            "dashboard_mode": context.mode,
            "dashboard_refresh_seconds": context.refresh_seconds,
            "dashboard_tty": context.tty,
            **_source_event_metadata(context.source_goal_id, context.source_run_id),
        },
        path=coordinator_events_path(root),
    )
    if not result.ok:
        return result, None
    return ValidationResult.accept(), context


def finish_dashboard_invocation(
    context: DashboardInvocationContext,
    *,
    outcome: str,
    render_count: int,
    completed_at: str | None = None,
    error_detail: str | None = None,
) -> ValidationResult:
    completed = completed_at or _now_utc()
    started_dt = _parse_iso8601(context.started_at)
    completed_dt = _parse_iso8601(completed)
    wall_ms = max(0, int((completed_dt - started_dt).total_seconds() * 1000))
    paths = garden_paths(garden_root=context.root)

    record = {
        "id": context.invocation_id,
        "actor": context.actor,
        "started_at": context.started_at,
        "completed_at": completed,
        "root": str(context.root),
        "mode": context.mode,
        "refresh_seconds": context.refresh_seconds,
        "tty": context.tty,
        "render_count": int(render_count),
        "outcome": outcome,
        "cost": {
            "source": "measured",
            "wall_ms": wall_ms,
        },
    }
    if context.source_goal_id:
        record["source_goal_id"] = context.source_goal_id
    if context.source_run_id:
        record["source_run_id"] = context.source_run_id
    if error_detail:
        record["error_detail"] = error_detail

    result = validate_dashboard_invocation(record)
    if not result.ok:
        return result

    path = paths.dashboard_invocations_dir / f"{context.invocation_id}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return ValidationResult.reject("IO_ERROR", str(exc))

    event = {
        "ts": completed,
        "type": "DashboardInvocationFinished",
        "actor": context.actor,
        "dashboard_invocation_id": context.invocation_id,
        "dashboard_mode": context.mode,
        "dashboard_refresh_seconds": context.refresh_seconds,
        "dashboard_tty": context.tty,
        "dashboard_outcome": outcome,
        "dashboard_render_count": int(render_count),
        "dashboard_wall_ms": wall_ms,
        "dashboard_record_path": str(path.relative_to(paths.runtime_root)),
        **_source_event_metadata(context.source_goal_id, context.source_run_id),
    }
    if error_detail:
        event["detail"] = error_detail
    return append_event(event, path=coordinator_events_path(context.root))


__all__ = [
    "DashboardInvocationContext",
    "dashboard_invocation_path",
    "finish_dashboard_invocation",
    "start_dashboard_invocation",
]
