"""
Read-only terminal dashboard for garden observability.

The dashboard scans the existing source-of-truth files and renders a compact
terminal surface for cycle health, active work, conversation state, cost,
alerts, and recent coordinator activity.
"""

from __future__ import annotations

import datetime
import json
import pathlib
from dataclasses import asdict, dataclass
from functools import lru_cache

from .conversations import list_conversations, read_latest_conversation_turn
from .coordinator import (
    _DEFAULT_POLL_INTERVAL,
    _DEFAULT_WATCHDOG_SECONDS,
    _blocked_conversation_ids,
    _list_all_runs,
    _parse_ts,
    _run_last_activity_at,
    find_eligible,
)
from .garden import garden_paths
from .goals import list_goals
from .validate import ValidationResult

_OPEN_GOAL_STATUSES = {"queued", "dispatched", "running", "completed", "evaluating"}
_ACTIVE_PANEL_LIMIT = 8
_ALERT_PANEL_LIMIT = 5
_CONVERSATION_PANEL_LIMIT = 5
_RECENT_ACTIVITY_LIMIT = 8
_TOP_COST_RUNS_LIMIT = 3
_RECENT_RUN_ALERT_WINDOW_SECONDS = 3600
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
_DASHBOARD_SNAPSHOT_SCHEMA = "dashboard-snapshot.schema.json"
_DASHBOARD_RENDER_SCHEMA = "dashboard-render.schema.json"
_RENDER_PANEL_KEYS = (
    "cycle_health",
    "active_work",
    "conversations",
    "cost",
    "alerts",
    "recent_activity",
)
_RENDER_PANEL_TITLES = {
    "cycle_health": "Cycle Health",
    "active_work": "Active Work",
    "conversations": "Conversations",
    "cost": "Cost",
    "alerts": "Alerts",
    "recent_activity": "Recent Activity",
}
_TWO_COLUMN_RENDER_ROWS = (
    ("cycle_health", "alerts"),
    ("active_work", "recent_activity"),
    ("conversations", "cost"),
)
_STACKED_RENDER_ROWS = tuple((key,) for key in _RENDER_PANEL_KEYS)


@dataclass(slots=True)
class CycleHealth:
    coordinator_process_status: str
    coordinator_process_count: int
    work_status: str
    open_work_count: int
    freshest_run_output_age_seconds: int | None
    running_runs: int
    queued_goals: int
    eligible_queued: int
    blocked_queued: int
    last_coordinator_event_age_seconds: int | None
    watchdog_seconds: int
    poll_interval_seconds: int


@dataclass(slots=True)
class GoalEntry:
    goal_id: str
    goal_type: str
    status: str
    plant: str | None
    priority: int
    age_seconds: int | None
    bucket: str
    blocked_reason: str | None
    current_run_id: str | None
    current_run_status: str | None
    run_age_seconds: int | None
    run_silence_age_seconds: int | None
    run_event_count: int | None
    run_lifecycle_phase: str | None
    submitted_at: str | None


@dataclass(slots=True)
class ConversationEntry:
    conversation_id: str
    last_activity_age_seconds: int | None
    session_ordinal: int
    session_turns: int
    mode: str
    pressure_band: str
    pressure_score: float | None
    needs_hop: bool
    pending_hop: bool
    active_run_id: str | None
    active_phase: str | None
    last_turn_run_id: str | None


@dataclass(slots=True)
class CostRun:
    run_id: str
    goal_id: str
    driver: str | None
    model: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


@dataclass(slots=True)
class CostSummary:
    today_input_tokens: int
    today_output_tokens: int
    today_cache_read_tokens: int
    unknown_completed_runs: int
    latest_completed_run: CostRun | None
    top_input_runs: list[CostRun]
    active_driver_models: list[str]


@dataclass(slots=True)
class Alert:
    severity: str
    subject: str
    identifier: str
    reason: str
    age_seconds: int | None


@dataclass(slots=True)
class RecentEvent:
    ts: str
    event_type: str
    goal_id: str | None
    goal_type: str | None
    goal_subtype: str | None
    conversation_id: str | None
    run_id: str | None
    reason: str | None
    checkpoint_id: str | None
    hop_outcome: str | None
    age_seconds: int | None
    from_status: str | None = None
    to_status: str | None = None


@dataclass(slots=True)
class DashboardSnapshot:
    root: str
    generated_at: str
    state: str
    alert_counts: dict[str, int]
    cycle_health: CycleHealth
    active_work: list[GoalEntry]
    conversations: list[ConversationEntry]
    cost: CostSummary
    alerts: list[Alert]
    recent_activity: list[RecentEvent]


@lru_cache(maxsize=1)
def _dashboard_snapshot_validator():
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - exercised via mocked failure path
        raise RuntimeError("jsonschema is not available") from exc

    schema_path = pathlib.Path(__file__).resolve().parent.parent / "schema" / _DASHBOARD_SNAPSHOT_SCHEMA
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(
        schema,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


@lru_cache(maxsize=1)
def _dashboard_render_validator():
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - exercised via mocked failure path
        raise RuntimeError("jsonschema is not available") from exc

    schema_path = pathlib.Path(__file__).resolve().parent.parent / "schema" / _DASHBOARD_RENDER_SCHEMA
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(
        schema,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def _jsonschema_validation_error_key(error) -> tuple[list[str], str]:
    return ([str(part) for part in error.path], error.message)


def _jsonschema_validation_detail(error) -> str:
    path = "/".join(str(part) for part in error.path) or "<root>"
    return f"{path}: {error.message}"


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _coerce_now(now: datetime.datetime | str | None) -> datetime.datetime:
    if now is None:
        return _now_utc()
    if isinstance(now, datetime.datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=datetime.timezone.utc)
        return now.astimezone(datetime.timezone.utc)
    return _parse_ts(now)


def _format_ts(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_parse_ts(ts: str | None) -> datetime.datetime | None:
    if not ts:
        return None
    try:
        return _parse_ts(ts)
    except (TypeError, ValueError):
        return None


def _age_seconds(now: datetime.datetime, ts: str | None) -> int | None:
    dt = _safe_parse_ts(ts)
    if dt is None:
        return None
    return max(0, int((now - dt).total_seconds()))


def _read_json_lines(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return records


def _count_nonempty_lines(path: pathlib.Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _read_proc_cmdline(proc_dir: pathlib.Path) -> list[str]:
    try:
        raw = (proc_dir / "cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]


def _proc_cwd(proc_dir: pathlib.Path) -> pathlib.Path | None:
    try:
        return (proc_dir / "cwd").resolve()
    except OSError:
        return None


def _looks_like_cycle_process(argv: list[str]) -> bool:
    if not argv or "cycle" not in argv:
        return False
    if any(pathlib.Path(arg).name == "pak2" for arg in argv):
        return True
    return "system.cli" in " ".join(argv)


def _resolve_process_root(proc_dir: pathlib.Path, argv: list[str]) -> pathlib.Path | None:
    cwd = _proc_cwd(proc_dir)
    root_arg: str | None = None
    for idx, arg in enumerate(argv):
        if arg == "--root" and idx + 1 < len(argv):
            root_arg = argv[idx + 1]
            break
        if arg.startswith("--root="):
            root_arg = arg.split("=", 1)[1]
            break
    if root_arg is None:
        return cwd

    candidate = pathlib.Path(root_arg)
    if not candidate.is_absolute():
        if cwd is None:
            return None
        candidate = cwd / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def _find_coordinator_processes(root: pathlib.Path) -> list[int]:
    proc_root = pathlib.Path("/proc")
    if not proc_root.exists():
        return []

    target_root = pathlib.Path(root).resolve()
    pids: list[int] = []
    for proc_dir in proc_root.iterdir():
        if not proc_dir.name.isdigit():
            continue
        argv = _read_proc_cmdline(proc_dir)
        if not _looks_like_cycle_process(argv):
            continue
        process_root = _resolve_process_root(proc_dir, argv)
        if process_root != target_root:
            continue
        pids.append(int(proc_dir.name))
    return sorted(pids)


def _run_sort_key(run: dict) -> tuple[str, str]:
    completed_at = str(run.get("completed_at") or "")
    started_at = str(run.get("started_at") or "")
    return (completed_at or started_at, str(run.get("id") or ""))


def _pick_goal_run(goal: dict, runs: list[dict]) -> dict | None:
    if not runs:
        return None
    running = [run for run in runs if run.get("status") == "running"]
    if running:
        return sorted(running, key=_run_sort_key)[-1]
    return sorted(runs, key=_run_sort_key)[-1]


def _bucket_for_goal(goal: dict, eligible_ids: set[str]) -> str:
    if goal.get("status") == "running":
        return "running"
    if goal.get("status") in {"dispatched", "completed", "evaluating"}:
        return "active"
    if goal.get("status") == "queued" and goal.get("id") in eligible_ids:
        return "eligible"
    return "blocked"


def _blocked_reason(
    goal: dict,
    *,
    closed_success: set[str],
    normal_active_plants: set[str],
    converse_active_plants: set[str],
    blocked_conversations: set[str],
    now_iso: str,
) -> str | None:
    if goal.get("status") != "queued":
        return None
    if not goal.get("assigned_to"):
        return "unassigned"
    deps = goal.get("depends_on") or []
    if any(dep not in closed_success for dep in deps):
        return "dependency"
    not_before = goal.get("not_before")
    if not_before and str(not_before) > now_iso:
        return "not_before"
    if (
        goal.get("type") == "converse"
        and not goal.get("post_reply_hop")
        and goal.get("conversation_id") in blocked_conversations
    ):
        return "conversation_hop"
    plant = goal.get("assigned_to")
    is_converse = goal.get("type") == "converse"
    active_plants = converse_active_plants if is_converse else normal_active_plants
    if plant in active_plants:
        return "plant_busy"
    return None


def _compact_age(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def _compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _trim(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return "." * width
    return text[: width - 3] + "..."


def _trim_middle(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return "." * width
    available = width - 3
    left = (available + 1) // 2
    right = available // 2
    if right == 0:
        return text[:left] + "..."
    return text[:left] + "..." + text[-right:]


def _short_id(identifier: str | None, width: int = 28) -> str:
    return _trim_middle(str(identifier or "?"), width)


def _goal_tag(goal_type: str) -> str:
    return {
        "converse": "chat",
        "evaluate": "eval",
    }.get(goal_type, "")


def _goal_subject(goal_type: str | None) -> str:
    return "eval" if goal_type == "evaluate" else "goal"


def _panel(title: str, lines: list[str], width: int) -> list[str]:
    panel_width = max(24, width)
    inner = panel_width - 4
    title_text = _trim(f" {title} ", inner)
    top = "+" + "-" + title_text.center(inner, "-") + "+"
    body = [f"| {_trim(line, inner):<{inner}} |" for line in lines]
    bottom = "+" + "-" * (panel_width - 2) + "+"
    return [top, *body, bottom]


def _merge_columns(left: list[str], right: list[str], width: int, gap: int = 2) -> list[str]:
    left_width = max((len(line) for line in left), default=width)
    right_width = max((len(line) for line in right), default=width)
    height = max(len(left), len(right))
    lines = []
    for idx in range(height):
        left_line = left[idx] if idx < len(left) else " " * left_width
        right_line = right[idx] if idx < len(right) else " " * right_width
        lines.append(f"{left_line:<{left_width}}{' ' * gap}{right_line}")
    return lines


def _severity_rank(severity: str) -> int:
    return _SEVERITY_ORDER.get(severity, 99)


def _render_layout(width: int) -> str:
    return "two-column" if width >= 120 else "stacked"


def _render_rows(layout: str) -> tuple[tuple[str, ...], ...]:
    if layout == "two-column":
        return _TWO_COLUMN_RENDER_ROWS
    return _STACKED_RENDER_ROWS


def _effective_height_limit(height: int | None) -> int | None:
    if height is None or height <= 0:
        return None
    return height


def _reason_from_event(event: dict) -> str | None:
    if event.get("type") == "DashboardInvocationStarted":
        mode = event.get("dashboard_mode")
        refresh = event.get("dashboard_refresh_seconds")
        if mode == "live" and refresh is not None:
            return f"mode=live refresh={refresh:g}s"
        if mode:
            return f"mode={mode}"
    if event.get("type") == "DashboardInvocationFinished":
        parts = []
        outcome = event.get("dashboard_outcome")
        if outcome:
            parts.append(str(outcome))
        wall_ms = event.get("dashboard_wall_ms")
        if wall_ms is not None:
            parts.append(f"wall={wall_ms}ms")
        render_count = event.get("dashboard_render_count")
        if render_count is not None:
            parts.append(f"renders={render_count}")
        detail = event.get("detail")
        if detail:
            parts.append(str(detail))
        if parts:
            return " ".join(parts)
    for key in (
        "goal_reason",
        "run_reason",
        "checkpoint_reason",
        "detail",
        "hop_reason",
        "reason",
    ):
        value = event.get(key)
        if value:
            return str(value)
    return None


def _activity_events(events: list[dict], runs_by_id: dict[str, dict]) -> list[dict]:
    """Filter dashboard activity down to events backed by the active garden root."""
    activity: list[dict] = []
    seen_run_events: set[tuple[str, str, str, str]] = set()
    queued_hop_goals: set[str] = set()
    checkpoint_hop_goals: set[str] = set()
    checkpoint_hop_runs: set[str] = set()
    for event in events:
        if event.get("type") == "ConversationHopQueued" and event.get("hop_goal"):
            queued_hop_goals.add(str(event["hop_goal"]))
        if event.get("type") == "ConversationCheckpointWritten":
            if event.get("goal"):
                checkpoint_hop_goals.add(str(event["goal"]))
            if event.get("run"):
                checkpoint_hop_runs.add(str(event["run"]))
    for event in events:
        event_type = str(event.get("type") or "")
        goal_id = str(event.get("goal") or "")
        run_id = str(event.get("run") or "")
        if event.get("goal_subtype") == "post_reply_hop":
            if event_type == "GoalSubmitted" and goal_id in queued_hop_goals:
                continue
            if event_type == "GoalClosed" and goal_id in checkpoint_hop_goals:
                continue
            if event_type == "RunFinished" and (
                goal_id in checkpoint_hop_goals or run_id in checkpoint_hop_runs
            ):
                continue
        if event_type in {"RunStarted", "RunFinished"}:
            if not run_id:
                continue
            run = runs_by_id.get(run_id)
            if run is None:
                continue
            run_goal_id = str(run.get("goal") or "")
            if goal_id and run_goal_id and goal_id != run_goal_id:
                continue
            signature = (
                event_type,
                run_id,
                goal_id or run_goal_id,
                _reason_from_event(event) or "",
            )
            if signature in seen_run_events:
                continue
            seen_run_events.add(signature)
        activity.append(event)
    return activity


def _min_known_age(*ages: int | None) -> int | None:
    known = [age for age in ages if age is not None]
    if not known:
        return None
    return min(known)


def _coordinator_work_status(
    *,
    open_work_count: int,
    last_coordinator_event_age_seconds: int | None,
    freshest_run_output_age_seconds: int | None,
    watchdog_seconds: int,
    poll_interval: int,
) -> str:
    if open_work_count == 0:
        return "idle"
    if (
        freshest_run_output_age_seconds is not None
        and freshest_run_output_age_seconds <= watchdog_seconds
    ):
        return "active"
    if (
        last_coordinator_event_age_seconds is not None
        and last_coordinator_event_age_seconds <= (poll_interval * 2)
    ):
        return "active"
    return "stuck"


def _intervention_status(alert_counts: dict[str, int]) -> str:
    if alert_counts.get("critical", 0) > 0:
        return "required"
    if alert_counts.get("warning", 0) > 0:
        return "recommended"
    if alert_counts.get("info", 0) > 0:
        return "monitor"
    return "none"


def _compact_event_time(ts: str) -> str:
    dt = _safe_parse_ts(ts)
    if dt is None:
        return ts
    return dt.strftime("%H:%M:%S")


def _work_line(entry: GoalEntry) -> str:
    label = {
        "running": "run",
        "active": "act",
        "eligible": "rdy",
        "blocked": "blk",
    }.get(entry.bucket, entry.bucket)
    line = f"{label} "
    tag = _goal_tag(entry.goal_type)
    if tag:
        line += f"{tag} "
    line += _short_id(entry.goal_id, 30)
    if entry.current_run_status == "running" and entry.run_age_seconds is not None:
        if entry.run_lifecycle_phase:
            line += f" phase={entry.run_lifecycle_phase}"
        line += f" run={_compact_age(entry.run_age_seconds)}"
        if entry.run_silence_age_seconds is not None:
            line += f" last-event={_compact_age(entry.run_silence_age_seconds)}"
        if entry.run_event_count is not None:
            line += f" events={entry.run_event_count}"
        return line
    line += f" age={_compact_age(entry.age_seconds)}"
    if entry.current_run_id:
        line += f" last={_short_id(entry.current_run_id, 20)}"
        if entry.current_run_status:
            line += f"/{entry.current_run_status}"
    if entry.blocked_reason:
        line += f" why={entry.blocked_reason}"
    elif entry.bucket == "eligible":
        line += " ready"
    return line


def _conversation_lines(conv: ConversationEntry) -> list[str]:
    score = "?"
    if conv.pressure_score is not None:
        score = f"{conv.pressure_score:.3f}"
    status_parts = [
        f"pressure={conv.pressure_band}/{score}",
        f"hop={'yes' if conv.needs_hop else 'no'}",
        f"pending={'yes' if conv.pending_hop else 'no'}",
    ]
    if conv.active_phase:
        status_parts.append(f"active={conv.active_phase}")
    status_parts.append(
        f"run={_short_id(conv.active_run_id or conv.last_turn_run_id or '-', 22)}"
    )
    return [
        (
            f"{_short_id(conv.conversation_id, 18)} age={_compact_age(conv.last_activity_age_seconds)} "
            f"s{conv.session_ordinal}/t{conv.session_turns} mode={conv.mode}"
        ),
        " ".join(status_parts),
    ]


def _alert_line(alert: Alert) -> str:
    severity = {"critical": "crit", "warning": "warn", "info": "info"}.get(
        alert.severity,
        alert.severity,
    )
    subject = {"conversation": "conv"}.get(alert.subject, alert.subject)
    return (
        f"{severity} {subject} {_short_id(alert.identifier, 20)} "
        f"{alert.reason} age={_compact_age(alert.age_seconds)}"
    )


def _recent_activity_line(event: RecentEvent) -> str:
    when = _compact_event_time(event.ts)
    goal_id = _short_id(event.goal_id, 24) if event.goal_id else "?"
    goal_subject = _goal_subject(event.goal_type)
    run_id = _short_id(event.run_id, 24) if event.run_id else "?"
    conv_label = _short_id(event.conversation_id, 18) if event.conversation_id else goal_id
    if event.event_type == "DashboardInvocationStarted":
        suffix = f" {event.reason}" if event.reason else ""
        return f"{when} dashboard started{suffix}".rstrip()
    if event.event_type == "DashboardInvocationFinished":
        suffix = f" {event.reason}" if event.reason else ""
        return f"{when} dashboard finished{suffix}".rstrip()
    if event.event_type == "ConversationHopQueued":
        return f"{when} hop {conv_label} queued"
    if event.event_type == "ConversationHopQueueFailed":
        suffix = f" {event.reason}" if event.reason else ""
        return f"{when} hop {conv_label} queue-failed{suffix}".rstrip()
    if event.event_type == "ConversationCheckpointWritten":
        checkpoint_id = event.checkpoint_id or "?"
        suffix = f" {event.reason}" if event.reason else ""
        return f"{when} checkpoint {conv_label} wrote {checkpoint_id}{suffix}".rstrip()
    if event.goal_subtype == "post_reply_hop":
        if event.event_type == "GoalSubmitted":
            return f"{when} hop {conv_label} queued"
        if event.event_type == "GoalTransitioned":
            transition = "transitioned"
            if event.from_status and event.to_status:
                transition = f"{event.from_status}->{event.to_status}"
            return f"{when} hop {conv_label} {transition}"
        if event.event_type == "GoalClosed":
            suffix = f" {event.reason}" if event.reason else ""
            return f"{when} hop {conv_label} closed{suffix}"
        if event.event_type == "RunStarted":
            return f"{when} hop {conv_label} started"
        if event.event_type == "RunFinished":
            outcome = event.hop_outcome or event.reason
            suffix = f" {outcome}" if outcome else ""
            if event.checkpoint_id:
                suffix += f" {event.checkpoint_id}"
            return f"{when} hop {conv_label} finished{suffix}".rstrip()
    if event.event_type == "GoalTransitioned":
        transition = "transitioned"
        if event.from_status and event.to_status:
            transition = f"{event.from_status}->{event.to_status}"
        return f"{when} {goal_subject} {goal_id} {transition}"
    if event.event_type == "GoalSubmitted":
        return f"{when} {goal_subject} {goal_id} submitted"
    if event.event_type == "GoalClosed":
        suffix = f" {event.reason}" if event.reason else ""
        return f"{when} {goal_subject} {goal_id} closed{suffix}"
    if event.event_type == "RunStarted":
        return f"{when} run {run_id} started"
    if event.event_type == "RunFinished":
        suffix = f" {event.reason}" if event.reason else ""
        return f"{when} run {run_id} finished{suffix}"
    target = run_id if event.run_id else goal_id
    return f"{when} {event.event_type} {target}"


def _dashboard_panel_bodies(snapshot: DashboardSnapshot) -> dict[str, list[str]]:
    cycle_lines = [
        "coordinator process: "
        f"{snapshot.cycle_health.coordinator_process_status} "
        f"proc={snapshot.cycle_health.coordinator_process_count}",
        f"work: {snapshot.cycle_health.work_status}",
        f"open work: {snapshot.cycle_health.open_work_count}",
        f"running runs: {snapshot.cycle_health.running_runs}",
        f"intervention: {_intervention_status(snapshot.alert_counts)}",
        f"queued goals: {snapshot.cycle_health.queued_goals}",
        f"eligible queued: {snapshot.cycle_health.eligible_queued}",
        f"blocked queued: {snapshot.cycle_health.blocked_queued}",
        "freshest run output: "
        + _compact_age(snapshot.cycle_health.freshest_run_output_age_seconds),
        "last coordinator event: "
        + _compact_age(snapshot.cycle_health.last_coordinator_event_age_seconds),
        f"watchdog: {_compact_age(snapshot.cycle_health.watchdog_seconds)}",
        f"poll interval: {_compact_age(snapshot.cycle_health.poll_interval_seconds)}",
    ]

    work_lines = []
    active_entries = [
        entry for entry in snapshot.active_work if entry.status != "queued"
    ]
    queued_entries = [
        entry for entry in snapshot.active_work if entry.status == "queued"
    ]
    work_lines.append("active:")
    if active_entries:
        for entry in active_entries:
            work_lines.append(_work_line(entry))
    else:
        work_lines.append("none")
    work_lines.append("")
    work_lines.append(f"queue ({snapshot.cycle_health.queued_goals} total):")
    if queued_entries:
        for entry in queued_entries:
            work_lines.append(_work_line(entry))
        hidden_queued = snapshot.cycle_health.queued_goals - len(queued_entries)
        if hidden_queued > 0:
            work_lines.append(f"... {hidden_queued} more queued")
    elif snapshot.cycle_health.queued_goals > 0:
        work_lines.append("queued items not shown in top work list")
    else:
        work_lines.append("none")

    conversation_lines = []
    if snapshot.conversations:
        for conv in snapshot.conversations:
            conversation_lines.extend(_conversation_lines(conv))
    else:
        conversation_lines = ["no open conversations"]

    cost_lines = [
        "today in="
        + _compact_number(snapshot.cost.today_input_tokens)
        + " out="
        + _compact_number(snapshot.cost.today_output_tokens)
        + " cache="
        + _compact_number(snapshot.cost.today_cache_read_tokens),
    ]
    if snapshot.cost.latest_completed_run:
        latest = snapshot.cost.latest_completed_run
        cost_lines.append(
            f"latest {_short_id(latest.run_id, 24)} in={_compact_number(latest.input_tokens)} "
            f"out={_compact_number(latest.output_tokens)}"
        )
    else:
        cost_lines.append("latest none with provider cost today")
    cost_lines.append(f"today unknown={snapshot.cost.unknown_completed_runs}")
    if snapshot.cost.top_input_runs:
        for run in snapshot.cost.top_input_runs:
            cost_lines.append(
                f"top {_short_id(run.run_id, 24)} in={_compact_number(run.input_tokens)}"
            )
    else:
        cost_lines.append("top runs: none with provider tokens today")
    if snapshot.cost.active_driver_models:
        cost_lines.append("active " + ", ".join(snapshot.cost.active_driver_models))
    else:
        cost_lines.append("active none")

    alert_lines = []
    if snapshot.alerts:
        for alert in snapshot.alerts:
            alert_lines.append(_alert_line(alert))
    else:
        alert_lines = ["no active alerts"]

    activity_lines = []
    if snapshot.recent_activity:
        for event in snapshot.recent_activity:
            activity_lines.append(_recent_activity_line(event))
    else:
        activity_lines = ["no coordinator activity yet"]

    return {
        "cycle_health": cycle_lines,
        "active_work": work_lines,
        "conversations": conversation_lines,
        "cost": cost_lines,
        "alerts": alert_lines,
        "recent_activity": activity_lines,
    }


def build_snapshot(
    root: pathlib.Path,
    now: datetime.datetime | str | None = None,
    *,
    watchdog_seconds: int = _DEFAULT_WATCHDOG_SECONDS,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
) -> DashboardSnapshot:
    root = pathlib.Path(root).resolve()
    paths = garden_paths(garden_root=root)
    now_dt = _coerce_now(now)
    now_iso = _format_ts(now_dt)

    goals = list_goals(_goals_dir=paths.goals_dir)
    all_runs = _list_all_runs(paths.runs_dir)
    blocked_conversations = _blocked_conversation_ids(root)
    open_goals = [goal for goal in goals if goal.get("status") in _OPEN_GOAL_STATUSES]
    queued_goals = [goal for goal in goals if goal.get("status") == "queued"]
    running_runs = [run for run in all_runs if run.get("status") == "running"]
    runs_by_goal: dict[str, list[dict]] = {}
    runs_by_id: dict[str, dict] = {}
    for run in all_runs:
        goal_id = run.get("goal")
        run_id = run.get("id")
        if run_id:
            runs_by_id[str(run_id)] = run
        if goal_id:
            runs_by_goal.setdefault(goal_id, []).append(run)

    goal_types = {goal["id"]: goal.get("type") for goal in goals}
    normal_active_plants = {
        run.get("plant")
        for run in running_runs
        if goal_types.get(run.get("goal")) != "converse" and run.get("plant")
    }
    converse_active_plants = {
        run.get("plant")
        for run in running_runs
        if goal_types.get(run.get("goal")) == "converse" and run.get("plant")
    }
    closed_success = {
        goal["id"]
        for goal in goals
        if goal.get("status") == "closed" and goal.get("closed_reason") == "success"
    }

    eligible_ids = {
        goal["id"]
        for goal in (
            find_eligible(
                goals,
                all_runs,
                now_iso,
                converse_only=False,
                blocked_conversations=blocked_conversations,
            )
            + find_eligible(
                goals,
                all_runs,
                now_iso,
                converse_only=True,
                blocked_conversations=blocked_conversations,
            )
        )
    }

    goal_entries: list[GoalEntry] = []
    current_runs_by_goal: dict[str, dict | None] = {}
    for goal in open_goals:
        blocked_reason = _blocked_reason(
            goal,
            closed_success=closed_success,
            normal_active_plants=normal_active_plants,
            converse_active_plants=converse_active_plants,
            blocked_conversations=blocked_conversations,
            now_iso=now_iso,
        )
        current_run = _pick_goal_run(goal, runs_by_goal.get(goal["id"], []))
        current_runs_by_goal[goal["id"]] = current_run
        run_age_seconds = None
        run_silence_seconds = None
        run_event_count = None
        run_lifecycle_phase = None
        if current_run and current_run.get("status") == "running":
            run_age_seconds = _age_seconds(now_dt, current_run.get("started_at"))
            run_silence_seconds = max(
                0,
                int((now_dt - _run_last_activity_at(current_run, paths.runs_dir)).total_seconds()),
            )
            run_event_count = _count_nonempty_lines(
                paths.runs_dir / current_run["id"] / "events.jsonl"
            )
            lifecycle = current_run.get("lifecycle") or {}
            if isinstance(lifecycle, dict):
                phase = lifecycle.get("phase")
                if phase:
                    run_lifecycle_phase = str(phase)
        goal_entries.append(
            GoalEntry(
                goal_id=goal["id"],
                goal_type=str(goal.get("type") or "?"),
                status=str(goal.get("status") or "?"),
                plant=goal.get("assigned_to"),
                priority=int(goal.get("priority", 5)),
                age_seconds=_age_seconds(now_dt, goal.get("submitted_at")),
                bucket=_bucket_for_goal(goal, eligible_ids),
                blocked_reason=blocked_reason,
                current_run_id=current_run.get("id") if current_run else None,
                current_run_status=current_run.get("status") if current_run else None,
                run_age_seconds=run_age_seconds,
                run_silence_age_seconds=run_silence_seconds,
                run_event_count=run_event_count,
                run_lifecycle_phase=run_lifecycle_phase,
                submitted_at=goal.get("submitted_at"),
            )
        )

    bucket_order = {"running": 0, "active": 1, "eligible": 2, "blocked": 3}
    goal_entries.sort(
        key=lambda entry: (
            bucket_order.get(entry.bucket, 99),
            entry.submitted_at or "",
            entry.goal_id,
        )
    )

    coordinator_log_path = paths.coordinator_events_path
    events = _read_json_lines(coordinator_log_path)
    activity_events = _activity_events(events, runs_by_id)
    last_event_ts = activity_events[-1].get("ts") if activity_events else None
    last_event_age = _age_seconds(now_dt, last_event_ts)
    coordinator_processes = _find_coordinator_processes(root)
    freshest_run_output_age = None
    for run in running_runs:
        silence_age = max(
            0,
            int((now_dt - _run_last_activity_at(run, paths.runs_dir)).total_seconds()),
        )
        freshest_run_output_age = _min_known_age(freshest_run_output_age, silence_age)

    work_status = _coordinator_work_status(
        open_work_count=len(open_goals),
        last_coordinator_event_age_seconds=last_event_age,
        freshest_run_output_age_seconds=freshest_run_output_age,
        watchdog_seconds=watchdog_seconds,
        poll_interval=poll_interval,
    )

    cycle_health = CycleHealth(
        coordinator_process_status="up" if coordinator_processes else "down",
        coordinator_process_count=len(coordinator_processes),
        work_status=work_status,
        open_work_count=len(open_goals),
        freshest_run_output_age_seconds=freshest_run_output_age,
        running_runs=len(running_runs),
        queued_goals=len(queued_goals),
        eligible_queued=len(eligible_ids),
        blocked_queued=max(0, len(queued_goals) - len(eligible_ids)),
        last_coordinator_event_age_seconds=last_event_age,
        watchdog_seconds=watchdog_seconds,
        poll_interval_seconds=poll_interval,
    )

    conversations: list[ConversationEntry] = []
    active_conversation_runs: dict[str, dict] = {}
    for goal in open_goals:
        if goal.get("type") != "converse":
            continue
        conv_id = goal.get("conversation_id")
        current_run = current_runs_by_goal.get(goal["id"])
        if not conv_id or not current_run or current_run.get("status") != "running":
            continue
        previous = active_conversation_runs.get(str(conv_id))
        if previous is None or _run_sort_key(current_run) > _run_sort_key(previous):
            active_conversation_runs[str(conv_id)] = current_run
    for conv in sorted(
        list_conversations(status="open", _conv_dir=paths.conversations_dir),
        key=lambda item: item.get("last_activity_at", ""),
        reverse=True,
    ):
        turn = read_latest_conversation_turn(conv["id"], _conv_dir=paths.conversations_dir) or {}
        pressure = turn.get("pressure") or conv.get("last_pressure") or {}
        mode = turn.get("mode") or conv.get("last_turn_mode")
        if not mode:
            mode = "resumed" if conv.get("session_id") else (
                "fresh-handoff" if conv.get("last_checkpoint_id") else "fresh-start"
            )
        active_run = active_conversation_runs.get(conv["id"])
        active_phase = None
        if active_run:
            lifecycle = active_run.get("lifecycle") or {}
            if isinstance(lifecycle, dict) and lifecycle.get("phase"):
                active_phase = str(lifecycle["phase"])
        conversations.append(
            ConversationEntry(
                conversation_id=conv["id"],
                last_activity_age_seconds=_age_seconds(now_dt, conv.get("last_activity_at")),
                session_ordinal=int(conv.get("session_ordinal") or 0),
                session_turns=int(conv.get("session_turns") or 0),
                mode=str(mode),
                pressure_band=str(pressure.get("band") or "unknown"),
                pressure_score=(
                    float(pressure["score"]) if pressure.get("score") is not None else None
                ),
                needs_hop=bool(pressure.get("needs_hop")),
                pending_hop=bool(conv.get("pending_hop")),
                active_run_id=active_run.get("id") if active_run else None,
                active_phase=active_phase,
                last_turn_run_id=(
                    turn.get("run_id") or conv.get("last_turn_run_id") or None
                ),
            )
        )

    completed_today = []
    today = now_dt.date()
    for run in all_runs:
        if run.get("status") == "running":
            continue
        completed_at = _safe_parse_ts(run.get("completed_at"))
        if completed_at is None or completed_at.date() != today:
            continue
        completed_today.append(run)

    today_input = 0
    today_output = 0
    today_cache = 0
    unknown_completed = 0
    latest_completed_run: CostRun | None = None
    latest_completed_at: datetime.datetime | None = None
    top_input_runs: list[CostRun] = []
    for run in completed_today:
        cost = run.get("cost") or {}
        if cost.get("source") == "provider":
            input_tokens = int(cost.get("input_tokens") or 0)
            output_tokens = int(cost.get("output_tokens") or 0)
            cache_tokens = int(cost.get("cache_read_tokens") or 0)
            today_input += input_tokens
            today_output += output_tokens
            today_cache += cache_tokens
            cost_run = CostRun(
                run_id=run["id"],
                goal_id=str(run.get("goal") or "?"),
                driver=run.get("driver"),
                model=run.get("model"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_tokens,
            )
            top_input_runs.append(cost_run)
            completed_at = _safe_parse_ts(run.get("completed_at"))
            if completed_at is not None and (
                latest_completed_at is None
                or completed_at > latest_completed_at
                or (completed_at == latest_completed_at and cost_run.run_id > latest_completed_run.run_id)
            ):
                latest_completed_at = completed_at
                latest_completed_run = cost_run
        else:
            unknown_completed += 1

    top_input_runs.sort(key=lambda item: (-item.input_tokens, item.run_id))
    cost = CostSummary(
        today_input_tokens=today_input,
        today_output_tokens=today_output,
        today_cache_read_tokens=today_cache,
        unknown_completed_runs=unknown_completed,
        latest_completed_run=latest_completed_run,
        top_input_runs=top_input_runs[:_TOP_COST_RUNS_LIMIT],
        active_driver_models=sorted(
            {
                f"{run.get('driver', '?')}/{run.get('model', '?')}"
                for run in running_runs
            }
        ),
    )

    alerts_by_subject: dict[tuple[str, str], Alert] = {}

    def record_alert(
        severity: str,
        subject: str,
        identifier: str,
        reason: str,
        age_seconds: int | None,
    ) -> None:
        key = (subject, identifier)
        current = alerts_by_subject.get(key)
        candidate = Alert(severity, subject, identifier, reason, age_seconds)
        if current is None:
            alerts_by_subject[key] = candidate
            return
        current_rank = _severity_rank(current.severity)
        candidate_rank = _severity_rank(candidate.severity)
        if candidate_rank < current_rank:
            alerts_by_subject[key] = candidate
            return
        if candidate_rank == current_rank:
            current_age = current.age_seconds if current.age_seconds is not None else 10**12
            candidate_age = candidate.age_seconds if candidate.age_seconds is not None else 10**12
            if candidate_age < current_age:
                alerts_by_subject[key] = candidate

    stale_goal_threshold = poll_interval * 2
    recent_run_threshold = _RECENT_RUN_ALERT_WINDOW_SECONDS
    for run in running_runs:
        silence_age = max(
            0,
            int((now_dt - _run_last_activity_at(run, paths.runs_dir)).total_seconds()),
        )
        if silence_age >= watchdog_seconds:
            record_alert(
                "critical",
                "run",
                run["id"],
                f"silent for {_compact_age(silence_age)}",
                silence_age,
            )
        elif silence_age >= int(watchdog_seconds * 0.6):
            record_alert(
                "warning",
                "run",
                run["id"],
                f"watchdog risk: silent for {_compact_age(silence_age)}",
                silence_age,
            )

    for entry in goal_entries:
        if entry.status == "running" and entry.current_run_status != "running":
            record_alert(
                "critical",
                "goal",
                entry.goal_id,
                "goal marked running without a running run",
                entry.age_seconds,
            )
        if entry.status == "queued" and entry.bucket == "eligible" and (entry.age_seconds or 0) > stale_goal_threshold:
            record_alert(
                "critical",
                "goal",
                entry.goal_id,
                f"eligible but still queued for {_compact_age(entry.age_seconds)}",
                entry.age_seconds,
            )
        if entry.status == "queued" and entry.blocked_reason == "unassigned" and (entry.age_seconds or 0) > stale_goal_threshold:
            record_alert(
                "warning",
                "goal",
                entry.goal_id,
                f"unassigned for {_compact_age(entry.age_seconds)}",
                entry.age_seconds,
            )
        if entry.status == "queued" and entry.blocked_reason == "plant_busy" and (entry.age_seconds or 0) > stale_goal_threshold:
            record_alert(
                "warning",
                "goal",
                entry.goal_id,
                f"waiting on plant availability for {_compact_age(entry.age_seconds)}",
                entry.age_seconds,
            )
        if entry.status == "queued" and entry.blocked_reason == "not_before":
            reason = "waiting on not_before"
            goal = next((g for g in queued_goals if g["id"] == entry.goal_id), None)
            if goal and goal.get("not_before"):
                reason = f"waiting until {goal['not_before']}"
            record_alert("info", "goal", entry.goal_id, reason, entry.age_seconds)

    if cycle_health.work_status == "stuck" and cycle_health.running_runs == 0:
        process_down = cycle_health.coordinator_process_status == "down"
        reason = "open work has no recent coordinator activity"
        if cycle_health.eligible_queued > 0:
            reason = "eligible work has no recent coordinator activity"
        if process_down:
            reason = "coordinator process absent and open work has no recent progress"
        record_alert(
            "critical" if process_down else "warning",
            "cycle",
            "coordinator",
            reason,
            _min_known_age(
                cycle_health.last_coordinator_event_age_seconds,
                cycle_health.freshest_run_output_age_seconds,
            ),
        )

    for conv in conversations:
        if conv.needs_hop:
            record_alert(
                "warning",
                "conversation",
                conv.conversation_id,
                "conversation needs session hop",
                conv.last_activity_age_seconds,
            )
        if conv.pressure_band in {"high", "critical"}:
            record_alert(
                "warning",
                "conversation",
                conv.conversation_id,
                f"conversation pressure {conv.pressure_band}",
                conv.last_activity_age_seconds,
            )
        if conv.pending_hop:
            record_alert(
                "info",
                "conversation",
                conv.conversation_id,
                "hop requested and pending",
                conv.last_activity_age_seconds,
            )

    for run in all_runs:
        status = str(run.get("status") or "")
        age = _age_seconds(now_dt, run.get("completed_at"))
        if age is None or age > recent_run_threshold:
            continue
        if status in {"failure", "killed", "timeout", "zero_output"}:
            record_alert(
                "warning",
                "run",
                run["id"],
                f"recent run finished {status}",
                age,
            )
        if (run.get("cost") or {}).get("source") == "unknown":
            record_alert(
                "info",
                "cost",
                run["id"],
                "recent run cost source unknown",
                age,
            )

    alerts = sorted(
        alerts_by_subject.values(),
        key=lambda alert: (
            _severity_rank(alert.severity),
            alert.age_seconds if alert.age_seconds is not None else 10**12,
            alert.subject,
            alert.identifier,
        ),
    )
    alert_counts = {"critical": 0, "warning": 0, "info": 0}
    for alert in alerts:
        if alert.severity in alert_counts:
            alert_counts[alert.severity] += 1

    recent_activity = []
    for event in reversed(activity_events[-_RECENT_ACTIVITY_LIMIT:]):
        recent_activity.append(
            RecentEvent(
                ts=str(event.get("ts") or "?"),
                event_type=str(event.get("type") or "?"),
                goal_id=event.get("goal"),
                goal_type=(
                    str(goal_types[event["goal"]])
                    if event.get("goal") in goal_types and goal_types[event["goal"]] is not None
                    else (str(event.get("goal_type")) if event.get("goal_type") else None)
                ),
                goal_subtype=(
                    str(event.get("goal_subtype")) if event.get("goal_subtype") else None
                ),
                conversation_id=(
                    str(event.get("conversation_id"))
                    if event.get("conversation_id") is not None
                    else None
                ),
                run_id=event.get("run"),
                reason=_reason_from_event(event),
                checkpoint_id=(
                    str(event.get("checkpoint_id"))
                    if event.get("checkpoint_id") is not None
                    else None
                ),
                hop_outcome=(
                    str(event.get("hop_outcome"))
                    if event.get("hop_outcome") is not None
                    else None
                ),
                age_seconds=_age_seconds(now_dt, event.get("ts")),
                from_status=event.get("from"),
                to_status=event.get("to"),
            )
        )

    state = cycle_health.work_status

    return DashboardSnapshot(
        root=str(root),
        generated_at=now_iso,
        state=state,
        alert_counts=alert_counts,
        cycle_health=cycle_health,
        active_work=goal_entries[:_ACTIVE_PANEL_LIMIT],
        conversations=conversations[:_CONVERSATION_PANEL_LIMIT],
        cost=cost,
        alerts=alerts[:_ALERT_PANEL_LIMIT],
        recent_activity=recent_activity,
    )


def build_snapshot_tree(
    root: pathlib.Path,
    now: datetime.datetime | str | None = None,
    *,
    watchdog_seconds: int = _DEFAULT_WATCHDOG_SECONDS,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
) -> dict:
    """
    Return the machine-readable dashboard snapshot tree.

    The returned JSON-serializable object matches
    schema/dashboard-snapshot.schema.json and is equivalent to the in-memory
    DashboardSnapshot dataclass tree produced by build_snapshot().
    """
    return asdict(
        build_snapshot(
            root,
            now=now,
            watchdog_seconds=watchdog_seconds,
            poll_interval=poll_interval,
        )
    )


def validate_snapshot_tree(data: object) -> ValidationResult:
    """
    Validate a machine-readable dashboard snapshot tree.

    The validator checks `data` against schema/dashboard-snapshot.schema.json
    and returns named rejections instead of raising.
    """
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "Dashboard snapshot tree must be a JSON object",
        )

    try:
        validator = _dashboard_snapshot_validator()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        return ValidationResult.reject(
            "DASHBOARD_VALIDATOR_UNAVAILABLE",
            f"dashboard snapshot validator unavailable: {exc}",
        )

    errors = sorted(validator.iter_errors(data), key=_jsonschema_validation_error_key)
    if errors:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_SNAPSHOT",
            _jsonschema_validation_detail(errors[0]),
        )
    return ValidationResult.accept()


def build_render_tree(
    snapshot: DashboardSnapshot,
    width: int = 120,
    height: int | None = None,
) -> dict:
    """
    Return the machine-readable dashboard render tree.

    The returned JSON-serializable object matches
    schema/dashboard-render.schema.json and is equivalent to the final text
    returned by render_dashboard().
    """
    width = max(80, width)
    height_limit = _effective_height_limit(height)
    layout = _render_layout(width)
    row_keys = _render_rows(layout)
    header_line = _trim(
        (
            f"{snapshot.generated_at} UTC | root={snapshot.root} | state={snapshot.state} "
            f"| coord={snapshot.cycle_health.coordinator_process_status} | "
            f"alerts c={snapshot.alert_counts['critical']} "
            f"w={snapshot.alert_counts['warning']} i={snapshot.alert_counts['info']}"
        ),
        width,
    )
    panel_bodies = _dashboard_panel_bodies(snapshot)
    panels = {
        key: {
            "title": _RENDER_PANEL_TITLES[key],
            "body_lines": panel_bodies[key],
        }
        for key in _RENDER_PANEL_KEYS
    }

    if layout == "two-column":
        col_width = (width - 2) // 2
        rendered_rows = [
            _merge_columns(
                _panel(
                    panels[left_key]["title"],
                    panels[left_key]["body_lines"],
                    col_width,
                ),
                _panel(
                    panels[right_key]["title"],
                    panels[right_key]["body_lines"],
                    col_width,
                ),
                col_width,
            )
            for left_key, right_key in row_keys
        ]
    else:
        rendered_rows = [
            _panel(
                panels[key]["title"],
                panels[key]["body_lines"],
                width,
            )
            for key, in row_keys
        ]

    text_lines = [header_line]
    for rendered_row in rendered_rows:
        text_lines.append("")
        text_lines.extend(rendered_row)

    truncated = False
    if height_limit is not None and len(text_lines) > height_limit:
        truncated = True
        text_lines = text_lines[: max(1, height_limit - 1)] + [
            _trim("...(truncated)", width)
        ]

    return {
        "generated_at": snapshot.generated_at,
        "width": width,
        "height_limit": height_limit,
        "layout": layout,
        "header": header_line,
        "truncated": truncated,
        "panels": panels,
        "rows": [{"panel_keys": list(keys)} for keys in row_keys],
        "text_lines": text_lines,
    }


def validate_render_tree(data: object) -> ValidationResult:
    """
    Validate a machine-readable dashboard render tree.

    The validator checks `data` against schema/dashboard-render.schema.json
    and enforces the current layout and truncation invariants without raising
    on ordinary validation failure.
    """
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "Dashboard render tree must be a JSON object",
        )

    try:
        validator = _dashboard_render_validator()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        return ValidationResult.reject(
            "DASHBOARD_RENDER_VALIDATOR_UNAVAILABLE",
            f"dashboard render validator unavailable: {exc}",
        )

    errors = sorted(validator.iter_errors(data), key=_jsonschema_validation_error_key)
    if errors:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_RENDER",
            _jsonschema_validation_detail(errors[0]),
        )

    expected_rows = [list(keys) for keys in _render_rows(str(data["layout"]))]
    actual_rows = [row["panel_keys"] for row in data["rows"]]
    if actual_rows != expected_rows:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_RENDER",
            f"rows: expected {expected_rows!r}, got {actual_rows!r}",
        )

    text_lines = data["text_lines"]
    if text_lines[0] != data["header"]:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_RENDER",
            "header must match the first text_lines entry",
        )

    truncation_marker = _trim("...(truncated)", int(data["width"]))
    if data["truncated"]:
        if text_lines[-1] != truncation_marker:
            return ValidationResult.reject(
                "INVALID_DASHBOARD_RENDER",
                "truncated render must end with the truncation marker",
            )
    elif text_lines[-1] == truncation_marker:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_RENDER",
            "non-truncated render must not end with the truncation marker",
        )

    return ValidationResult.accept()


def render_dashboard(
    snapshot: DashboardSnapshot,
    width: int = 120,
    height: int | None = None,
) -> str:
    render_tree = build_render_tree(snapshot, width=width, height=height)
    return "\n".join(render_tree["text_lines"])
