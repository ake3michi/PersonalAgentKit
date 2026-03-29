"""
Event log: append and read entries in events/coordinator.jsonl.

All writes go through append_event(), which validates before writing.
The file is never read for state — it is the audit trail.
"""

import fcntl
import json
import pathlib

from .garden import garden_paths
from .validate import ValidationResult, validate_event

_RELATIVE_LOG_PATH = pathlib.Path("events/coordinator.jsonl")
_LOG_PATH = _RELATIVE_LOG_PATH


def coordinator_events_path(garden_root: pathlib.Path | None = None) -> pathlib.Path:
    """Return the coordinator event log path for a garden root."""
    if garden_root is None:
        return _LOG_PATH
    return garden_paths(garden_root=garden_root).coordinator_events_path


def append_event(data: dict, *, path: pathlib.Path | None = None) -> ValidationResult:
    """Validate and append one event to the log. Never raises."""
    result = validate_event(data)
    if not result.ok:
        return result

    line = json.dumps(data, separators=(",", ":")) + "\n"
    log_path = path if path is not None else _LOG_PATH
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(line)
            fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError as exc:
        return ValidationResult.reject("IO_ERROR", str(exc))

    return ValidationResult.accept()


def read_events(path: pathlib.Path | None = None) -> list[dict]:
    """Return all events in order. Returns empty list if log does not exist."""
    p = path if path is not None else _LOG_PATH
    if not p.exists():
        return []
    events = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
