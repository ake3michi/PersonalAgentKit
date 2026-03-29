"""
Run store: create, update, and close run records in
<runtime-root>/runs/<run-id>/meta.json.

Runs are opened by the system before the agent subprocess launches.
Agents update their own run record. The system closes it via close_run().
"""

import datetime
import json
import pathlib
import subprocess
import sys

from .validate import ValidationResult, validate_run, validate_run_close
from .events import append_event
from .garden import discover_garden_root, garden_paths
from .runtime_history import capture_runtime_history_for_run

_RUNS_DIR = garden_paths().runs_dir
_RESERVED_EVENT_FIELDS = frozenset({
    "ts", "type", "actor", "goal", "run", "run_reason",
})


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_path(run_id: str, runs_dir: pathlib.Path) -> pathlib.Path:
    return runs_dir / run_id / "meta.json"


def _events_path_for_runs_dir(runs_dir: pathlib.Path) -> pathlib.Path:
    return runs_dir.parent / "events" / "coordinator.jsonl"


def _event_extras(event_data: dict | None) -> dict:
    if not event_data:
        return {}
    return {
        key: value
        for key, value in event_data.items()
        if key not in _RESERVED_EVENT_FIELDS and value is not None
    }


def _run_git(args: list[str], *, cwd: pathlib.Path) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None


def _parse_status_path(line: str) -> tuple[str, str] | None:
    if len(line) < 4 or line[2] != " ":
        return None
    status = line[:2]
    path = line[3:].strip()
    if not path:
        return None
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    if path.startswith('"') and path.endswith('"') and len(path) >= 2:
        path = path[1:-1]
    return status, path


def _capture_worktree_baseline(*, runs_dir: pathlib.Path, captured_at: str) -> dict | None:
    garden_root = discover_garden_root(runs_dir.resolve())
    status = _run_git(["status", "--short", "--untracked-files=all"], cwd=garden_root)
    if status is None or status.returncode != 0:
        return None

    tracked_dirty_paths: set[str] = set()
    untracked_dirty_roots: set[str] = set()
    untracked_dirty_count = 0

    for line in status.stdout.splitlines():
        parsed = _parse_status_path(line)
        if parsed is None:
            continue
        state, path = parsed
        if state == "??":
            untracked_dirty_count += 1
            parts = pathlib.PurePosixPath(path).parts
            untracked_dirty_roots.add(parts[0] if parts else path)
            continue
        tracked_dirty_paths.add(path)

    return {
        "captured_at": captured_at,
        "tracked_dirty_paths": sorted(tracked_dirty_paths),
        "untracked_dirty_count": untracked_dirty_count,
        "untracked_dirty_roots": sorted(untracked_dirty_roots),
    }


def _next_attempt(goal_id: str, runs_dir: pathlib.Path) -> int:
    """Return the next attempt number for this goal (1-based)."""
    existing = []
    if runs_dir.exists():
        for p in runs_dir.iterdir():
            if p.is_dir() and p.name.startswith(goal_id + "-r"):
                suffix = p.name[len(goal_id) + 2:]  # after "-r"
                try:
                    existing.append(int(suffix))
                except ValueError:
                    pass
    return max(existing, default=0) + 1


def open_run(goal_id: str, plant: str, driver: str, model: str, *,
             goal_type: str | None = None,
             event_data: dict | None = None,
             _runs_dir: pathlib.Path | None = None,
             _now: str | None = None) -> tuple[ValidationResult, str | None]:
    """
    Create a new run record for goal_id executed by plant. Returns (result, run_id).
    Emits RunStarted event. run_id is None on failure.
    """
    runs_dir = _runs_dir if _runs_dir is not None else _RUNS_DIR
    default_paths = garden_paths() if _runs_dir is None else None
    now = _now or _now_utc()

    attempt = _next_attempt(goal_id, runs_dir)
    run_id = f"{goal_id}-r{attempt}"
    worktree_baseline = None
    if goal_type and goal_type != "converse":
        worktree_baseline = _capture_worktree_baseline(
            runs_dir=runs_dir,
            captured_at=now,
        )

    record = {
        "id": run_id,
        "goal": goal_id,
        "plant": plant,
        "status": "running",
        "started_at": now,
        "driver": driver,
        "model": model,
    }
    if worktree_baseline is not None:
        record["worktree_baseline"] = worktree_baseline

    result = validate_run(record)
    if not result.ok:
        return result, None

    run_file = _run_path(run_id, runs_dir)
    run_file.parent.mkdir(parents=True, exist_ok=True)
    run_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    events_path = (
        default_paths.coordinator_events_path
        if default_paths is not None
        else _events_path_for_runs_dir(runs_dir)
    )

    append_event({
        "ts": now,
        "type": "RunStarted",
        "actor": "system",
        "goal": goal_id,
        "run": run_id,
        **_event_extras(event_data),
    }, path=events_path)

    return ValidationResult.accept(), run_id


def read_run(run_id: str, *, _runs_dir: pathlib.Path | None = None) -> dict | None:
    """Return run record or None if not found."""
    runs_dir = _runs_dir if _runs_dir is not None else _RUNS_DIR
    p = _run_path(run_id, runs_dir)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def update_run_lifecycle(run_id: str, *, phase: str | None,
                         _runs_dir: pathlib.Path | None = None,
                         _now: str | None = None) -> ValidationResult:
    """
    Update or clear transient lifecycle state for a running run.

    This is system-managed state used to surface subphases like conversation
    checkpointing or reflection solicitation while the run is still open.
    """
    runs_dir = _runs_dir if _runs_dir is not None else _RUNS_DIR
    run = read_run(run_id, _runs_dir=runs_dir)
    if run is None:
        return ValidationResult.reject("RUN_NOT_FOUND", run_id)

    if run["status"] != "running":
        return ValidationResult.reject(
            "INVALID_TRANSITION",
            f"run is not active (status={run['status']!r})",
        )

    if phase is None:
        run.pop("lifecycle", None)
    else:
        run["lifecycle"] = {
            "phase": str(phase),
            "updated_at": _now or _now_utc(),
        }

    result = validate_run(run)
    if not result.ok:
        return result

    run_file = _run_path(run_id, runs_dir)
    run_file.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    return ValidationResult.accept()


def close_run(run_id: str, status: str, goal_type: str, *,
              completed_at: str | None = None,
              cost: dict | None = None,
              failure_reason: str | None = None,
              reflection: str | None = None,
              outputs: list | None = None,
              num_turns: int | None = None,
              event_data: dict | None = None,
              _runs_dir: pathlib.Path | None = None,
              _now: str | None = None) -> ValidationResult:
    """
    Close a run with a terminal status. Validates against goal_type for reflection.
    Emits RunFinished event.
    """
    runs_dir = _runs_dir if _runs_dir is not None else _RUNS_DIR
    default_paths = garden_paths() if _runs_dir is None else None
    run = read_run(run_id, _runs_dir=runs_dir)
    if run is None:
        return ValidationResult.reject("RUN_NOT_FOUND", run_id)

    if run["status"] != "running":
        return ValidationResult.reject(
            "INVALID_TRANSITION",
            f"run is already in terminal status '{run['status']}'"
        )

    now = completed_at or _now or _now_utc()

    run.pop("lifecycle", None)
    run["status"] = status
    run["completed_at"] = now
    run["cost"] = cost or {"source": "unknown"}

    if failure_reason is not None:
        run["failure_reason"] = failure_reason
    if reflection is not None:
        run["reflection"] = reflection
    if outputs is not None:
        run["outputs"] = outputs
    if num_turns is not None:
        run["num_turns"] = num_turns

    # Validate the full record, then cross-validate against goal type
    result = validate_run(run)
    if not result.ok:
        return result

    result = validate_run_close(run, goal_type)
    if not result.ok:
        return result

    run_file = _run_path(run_id, runs_dir)
    run_file.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    events_path = (
        default_paths.coordinator_events_path
        if default_paths is not None
        else _events_path_for_runs_dir(runs_dir)
    )

    run_reason = status if status in ("success", "failure", "killed", "timeout", "zero_output") else "failure"

    append_event({
        "ts": now,
        "type": "RunFinished",
        "actor": "system",
        "goal": run["goal"],
        "run": run_id,
        "run_reason": run_reason,
        **_event_extras(event_data),
    }, path=events_path)

    history = capture_runtime_history_for_run(
        run_id=run_id,
        goal_id=run["goal"],
        run_status=status,
        completed_at=now,
        runs_dir=runs_dir,
    )
    if history.attempted and not history.committed:
        print(
            f"runs: runtime history capture failed for {run_id}: "
            f"{history.reason} — {history.detail or 'no detail'}",
            file=sys.stderr,
        )

    return ValidationResult.accept()


def list_runs(goal_id: str | None = None,
              *, _runs_dir: pathlib.Path | None = None) -> list[dict]:
    """Return all runs, optionally filtered by goal_id."""
    runs_dir = _runs_dir if _runs_dir is not None else _RUNS_DIR
    if not runs_dir.exists():
        return []
    runs = []
    for p in sorted(runs_dir.iterdir()):
        if p.is_dir():
            meta = p / "meta.json"
            if meta.exists():
                try:
                    r = json.loads(meta.read_text(encoding="utf-8"))
                    if goal_id is None or r.get("goal") == goal_id:
                        runs.append(r)
                except (json.JSONDecodeError, OSError):
                    pass
    return runs
