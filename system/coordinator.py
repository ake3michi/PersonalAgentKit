"""
Coordinator: schedules and dispatches goals.

reconcile() performs one synchronous pass — used by genesis and tests.
Coordinator runs the async event loop for production use (pak2 cycle).

Scheduling algorithm (applied each pass):
  Two independent dispatch lanes:
  1. Normal goals (non-converse): up to max_concurrent, plant-exclusive
  2. Converse goals: up to max_converse (default 1), independent of normal lane
  Within each lane: resolve_dep_failures, find_eligible (priority desc /
  submitted_at asc), reserve plants, dispatch in threads.

  The separate converse lane means a long-running build or tend never
  blocks a conversation from dispatching, but a pass will not assign the
  same plant twice.

Wakeup sources (Coordinator):
  - worker thread finishes    → immediate re-evaluate (plant now free)
  - somatic loop submits goal → immediate re-evaluate via wake() callback
  - not_before arrives        → sleep until earliest, wake on schedule
  - poll interval             → safety net for any missed events
  - KeyboardInterrupt         → clean shutdown
"""

import datetime
import json
import pathlib
import threading

from .driver import dispatch as _driver_dispatch
from .driver_plugins import resolve_driver_name, resolve_model_name
from .channels import FilesystemChannel
from .conversations import (
    append_message,
    list_conversations,
    read_conversation,
    read_messages,
)
from .goals import (
    _goal_event_metadata,
    ensure_spawned_eval_goal,
    list_goals,
    read_goal,
    transition_goal,
)
from .runs import open_run, close_run, list_runs
from .garden import discover_garden_root, garden_paths
from .submit import submit_tend_goal
from .tend import TEND_TRIGGER_QUEUED_ATTENTION, TEND_TRIGGER_RUN_FAILURE

_DEFAULT_WATCHDOG_SECONDS = 300
_DEFAULT_MAX_CONCURRENT   = 2
_DEFAULT_MAX_CONVERSE     = 1   # converse goals get their own concurrency lane
_DEFAULT_POLL_INTERVAL    = 60  # seconds; also caps sleep-until-not_before


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts: str) -> datetime.datetime:
    return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc
    )


def _run_last_activity_at(run: dict, runs_dir: pathlib.Path) -> datetime.datetime:
    """Return watchdog liveness time from run output mtime or started_at."""
    events_file = runs_dir / run["id"] / "events.jsonl"
    try:
        return datetime.datetime.fromtimestamp(
            events_file.stat().st_mtime, tz=datetime.timezone.utc
        )
    except OSError:
        return _parse_ts(run["started_at"])


def _format_goal_submission_event(event: dict) -> str:
    """Human-readable cycle output for a GoalSubmitted event."""
    ts = event.get("ts", _now_utc())
    actor = event.get("actor", "unknown")
    goal_id = event.get("goal", "<unknown-goal>")
    conversation_id = event.get("conversation_id")

    if event.get("goal_subtype") == "post_reply_hop":
        details = []
        if conversation_id:
            details.append(f"conversation_id={conversation_id}")
        reason = event.get("hop_reason")
        if reason:
            details.append(f"reason={reason}")
        if event.get("hop_automatic") is not None:
            details.append(
                "automatic=yes" if event.get("hop_automatic") else "automatic=no"
            )
        suffix = f" ({', '.join(details)})" if details else ""
        return f"[{ts}] conversation hop queued by {actor}: {goal_id}{suffix}"

    details = []
    goal_type = event.get("goal_type")
    if goal_type:
        details.append(f"type={goal_type}")
    if conversation_id:
        details.append(f"conversation_id={conversation_id}")

    suffix = f" ({', '.join(details)})" if details else ""
    return f"[{ts}] goal submitted by {actor}: {goal_id}{suffix}"


# ---------------------------------------------------------------------------
# Pure scheduling functions
# ---------------------------------------------------------------------------

def find_eligible(goals: list, all_runs: list, now: str, *,
                  converse_only: bool = False,
                  blocked_conversations: set[str] | None = None) -> list:
    """
    Return queued goals ready to dispatch, sorted by priority desc, submitted_at asc.

    Eligible requires all of:
    - assigned_to is set
    - assigned plant has no currently running run of the same category
    - not_before is absent or <= now
    - all depends_on goals are closed with closed_reason='success'

    converse_only=False: non-converse goals; plant busy = has a running non-converse run
    converse_only=True:  converse goals;     plant busy = has a running converse run
    blocked_conversations: open conversations with queued/running post-reply hop
    work that must finish before the next converse turn dispatches.

    The category split gives converse goals their own lane so a long-running
    build or tend does not block conversation dispatch.
    """
    blocked_conversations = blocked_conversations or set()
    closed_success = {
        g["id"] for g in goals
        if g["status"] == "closed" and g.get("closed_reason") == "success"
    }

    # Build goal-type lookup to classify running runs by category
    goal_types = {g["id"]: g.get("type") for g in goals}

    active_plants = set()
    for r in all_runs:
        if r["status"] != "running":
            continue
        run_is_converse = goal_types.get(r["goal"]) == "converse"
        if run_is_converse == converse_only:
            active_plants.add(r["plant"])

    eligible = []
    for g in goals:
        if g["status"] != "queued":
            continue
        is_converse = g.get("type") == "converse"
        if converse_only and not is_converse:
            continue
        if not converse_only and is_converse:
            continue
        if (
            is_converse
            and not g.get("post_reply_hop")
            and g.get("conversation_id") in blocked_conversations
        ):
            continue
        plant = g.get("assigned_to")
        if not plant:
            continue
        if plant in active_plants:
            continue
        nb = g.get("not_before")
        if nb and nb > now:
            continue
        deps = g.get("depends_on", [])
        if not all(d in closed_success for d in deps):
            continue
        eligible.append(g)

    eligible.sort(key=lambda g: (-g.get("priority", 5), g["submitted_at"]))
    return eligible


def select_dispatch_goals(goals: list, all_runs: list, now: str, *, capacity: int,
                          converse_only: bool = False,
                          reserved_plants: set[str] | None = None,
                          blocked_conversations: set[str] | None = None) -> list:
    """
    Return up to capacity eligible goals, reserving plant occupancy as we pick.

    This prevents a single dispatch pass from assigning multiple goals to the
    same plant off one stale view of run state.
    """
    if capacity <= 0:
        return []

    reserved = reserved_plants if reserved_plants is not None else set()
    selected = []

    for goal in find_eligible(
        goals,
        all_runs,
        now,
        converse_only=converse_only,
        blocked_conversations=blocked_conversations,
    ):
        plant = goal.get("assigned_to")
        if not plant:
            continue
        if plant in reserved and not goal.get("post_reply_hop"):
            continue
        selected.append(goal)
        if not goal.get("post_reply_hop"):
            reserved.add(plant)
        if len(selected) >= capacity:
            break

    return selected


def resolve_dep_failures(*,
                         _goals_dir: pathlib.Path | None = None,
                         _now: str | None = None) -> list[str]:
    """
    Close queued goals whose dependencies have permanently failed.
    A dep is permanently failed when it is closed with reason failure, cancelled,
    or dependency_impossible.
    Returns list of goal IDs transitioned to dependency_impossible.
    """
    from .goals import _GOALS_DIR as _DEFAULT_GOALS_DIR
    goals_dir = _goals_dir or _DEFAULT_GOALS_DIR
    now = _now or _now_utc()
    goals = list_goals(_goals_dir=goals_dir)

    permanently_failed = {
        g["id"] for g in goals
        if g["status"] == "closed"
        and g.get("closed_reason") in ("failure", "cancelled", "dependency_impossible")
    }

    closed = []
    for g in goals:
        if g["status"] != "queued":
            continue
        deps = g.get("depends_on", [])
        if any(d in permanently_failed for d in deps):
            r = transition_goal(g["id"], "closed", actor="system",
                                closed_reason="dependency_impossible",
                                _goals_dir=goals_dir, _now=now)
            if r.ok:
                closed.append(g["id"])
    return closed


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _list_all_runs(runs_dir: pathlib.Path | None) -> list:
    """Return all run records from every run directory."""
    if not runs_dir or not runs_dir.exists():
        return []
    runs = []
    for run_dir in runs_dir.iterdir():
        meta = run_dir / "meta.json"
        if meta.exists():
            try:
                runs.append(json.loads(meta.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
    return runs


def _blocked_conversation_ids(root: pathlib.Path) -> set[str]:
    paths = garden_paths(garden_root=root)
    return {
        str(conv["id"])
        for conv in list_conversations(status="open", _conv_dir=paths.conversations_dir)
        if conv.get("post_reply_hop")
    }


def _filesystem_reply_note_paths(root: pathlib.Path) -> list[str]:
    inbox_dir = garden_paths(garden_root=root).inbox_dir
    if not inbox_dir.exists():
        return []
    note_paths = []
    for child in sorted(inbox_dir.iterdir()):
        if not child.is_dir() or child.name == "operator":
            continue
        for note in sorted(child.glob("*.md")):
            if note.is_file():
                note_paths.append(str(note.relative_to(root)))
    return note_paths


def _read_filesystem_reply_note(root: pathlib.Path, note_path: str) -> str | None:
    path = root / note_path
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _conversation_delivery_note_path(note_path: str, conversation_id: str | None) -> bool:
    if not conversation_id:
        return False
    slug = conversation_id.replace("/", "-")[:40]
    return pathlib.Path(note_path).name.endswith(f"-{slug}.md")


def _render_startup_run_started_update(goal: dict, run_id: str) -> str:
    goal_type = goal.get("type", "goal")
    plant = goal.get("assigned_to", "gardener")
    return "\n".join(
        [
            "System startup update from recorded lifecycle facts:",
            (
                f"- Started run `{run_id}` for the first queued `{goal_type}` goal "
                f"`{goal['id']}` on `{plant}`."
            ),
        ]
    )


def _render_startup_run_finished_update(run_id: str, status: str,
                                        new_note_paths: list[str]) -> str:
    lines = [
        "System startup update from recorded lifecycle facts:",
        f"- Run `{run_id}` finished with `{status}`.",
    ]
    if len(new_note_paths) == 1:
        lines.append(f"- A new filesystem note appeared at `{new_note_paths[0]}`.")
    elif len(new_note_paths) > 1:
        rendered = ", ".join(f"`{path}`" for path in new_note_paths)
        lines.append(f"- New filesystem notes appeared at {rendered}.")
    return "\n".join(lines)


def _render_startup_reply_representation(run_id: str, note_path: str, content: str) -> str:
    reply_text = content.rstrip("\n")
    lines = [
        "Garden reply represented from startup filesystem delivery:",
        f"- Source run: `{run_id}`.",
        f"- Original note path: `{note_path}`.",
    ]
    if reply_text:
        lines.extend(["", reply_text])
    else:
        lines.extend(["", "[The filesystem note was empty.]"])
    return "\n".join(lines)


def _close_goal_after_run(goal_id: str, runs_dir: pathlib.Path,
                          goals_dir: pathlib.Path, now: str) -> None:
    """
    Finalize a goal after its run finishes.

    Successful eligible goals with spawn_eval enabled stop in `evaluating`
    until their spawned evaluate goal closes. All other terminal outcomes
    follow the existing completed/closed path.
    """
    goal = read_goal(goal_id, _goals_dir=goals_dir)
    if goal is None:
        return
    if goal.get("status") in {"evaluating", "closed"}:
        return

    runs = list_runs(goal_id, _runs_dir=runs_dir)
    terminal = [r for r in runs
                if r.get("status") in ("success", "failure", "killed", "timeout", "zero_output")]
    if not terminal:
        return
    terminal_run = terminal[-1]
    event_now = str(terminal_run.get("completed_at") or now)
    run_status = terminal_run["status"]
    closed_reason = "success" if run_status == "success" else "failure"

    if goal.get("status") == "running":
        result = transition_goal(
            goal_id,
            "completed",
            actor="system",
            _goals_dir=goals_dir,
            _now=event_now,
        )
        if not result.ok:
            return
        goal = read_goal(goal_id, _goals_dir=goals_dir)
        if goal is None:
            return
    elif goal.get("status") != "completed":
        return

    if run_status == "success":
        spawn_result, eval_goal_id = ensure_spawned_eval_goal(
            goal_id,
            _goals_dir=goals_dir,
            _now=event_now,
        )
        if spawn_result.ok and eval_goal_id:
            transition_goal(
                goal_id,
                "evaluating",
                actor="system",
                _goals_dir=goals_dir,
                _now=event_now,
            )
            return

    transition_goal(goal_id, "closed", actor="system",
                    closed_reason=closed_reason, _goals_dir=goals_dir, _now=event_now)


def _read_run_status(run_id: str, runs_dir: pathlib.Path) -> str | None:
    run_meta = runs_dir / run_id / "meta.json"
    try:
        payload = json.loads(run_meta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    status = payload.get("status")
    return str(status) if status is not None else None


def _submit_run_failure_tend(*,
                             goal: dict | None,
                             run_id: str,
                             run_status: str | None,
                             goals_dir: pathlib.Path,
                             now: str) -> str | None:
    if run_status not in {"failure", "killed", "timeout", "zero_output"}:
        return None
    if not goal or goal.get("type") in {"converse", "tend"}:
        return None

    result, tend_goal_id = submit_tend_goal(
        body=(
            f"Perform a bounded tend pass to review background run {run_id} "
            f"after goal {goal['id']} closed with {run_status}."
        ),
        submitted_by="system",
        trigger_kinds=[TEND_TRIGGER_RUN_FAILURE],
        trigger_goal=goal["id"],
        trigger_run=run_id,
        _goals_dir=goals_dir,
        _now=now,
    )
    if result.ok:
        return tend_goal_id
    return None


def _goal_age_seconds(goal: dict, now: str) -> float | None:
    submitted_at = goal.get("submitted_at")
    if not submitted_at:
        return None
    try:
        return (_parse_ts(now) - _parse_ts(str(submitted_at))).total_seconds()
    except ValueError:
        return None


def _submit_queued_attention_tend(*,
                                  now: str,
                                  goals_dir: pathlib.Path,
                                  runs_dir: pathlib.Path,
                                  poll_interval: int,
                                  garden_root: pathlib.Path | None = None,
                                  reserved_plants: set[str] | None = None,
                                  ignored_goal_ids: set[str] | None = None) -> str | None:
    attention_after = max(0, poll_interval * 2)
    reserved_plants = reserved_plants or set()
    ignored_goal_ids = ignored_goal_ids or set()
    goals = list_goals(_goals_dir=goals_dir)
    all_runs = _list_all_runs(runs_dir)
    blocked_conversations = _blocked_conversation_ids(
        garden_root if garden_root is not None else discover_garden_root(goals_dir)
    )
    eligible_ids = {
        goal["id"]
        for goal in find_eligible(
            goals,
            all_runs,
            now,
            converse_only=False,
            blocked_conversations=blocked_conversations,
        )
    }

    candidates = []
    for goal in goals:
        if goal.get("status") != "queued":
            continue
        if goal.get("type") in {"converse", "tend"}:
            continue
        if goal["id"] in ignored_goal_ids:
            continue
        age_seconds = _goal_age_seconds(goal, now)
        if age_seconds is None or age_seconds < attention_after:
            continue
        if not goal.get("assigned_to"):
            candidates.append(goal)
            continue
        if goal.get("assigned_to") in reserved_plants:
            continue
        if goal["id"] in eligible_ids:
            candidates.append(goal)

    candidates.sort(key=lambda goal: (-goal.get("priority", 5), goal["submitted_at"]))
    if not candidates:
        return None

    trigger_goal = candidates[0]["id"]
    result, tend_goal_id = submit_tend_goal(
        body=(
            "Perform a bounded tend pass for queued_attention_needed on "
            f"queued goal {trigger_goal}."
        ),
        submitted_by="system",
        trigger_kinds=[TEND_TRIGGER_QUEUED_ATTENTION],
        trigger_goal=trigger_goal,
        _goals_dir=goals_dir,
        _now=now,
    )
    if result.ok:
        return tend_goal_id
    return None


def _open_and_transition(goal, now, goals_dir, runs_dir, *,
                         garden_root: pathlib.Path | None = None):
    """
    Transition goal queued → dispatched → running, open run record.
    Returns run_id on success, None on failure (rolls back to queued).
    """
    goal_id = goal["id"]
    plant   = goal.get("assigned_to", "gardener")
    root = garden_root if garden_root is not None else discover_garden_root(goals_dir)
    driver  = resolve_driver_name(goal, garden_root=root)
    model   = resolve_model_name(goal, driver_name=driver, garden_root=root)

    r = transition_goal(goal_id, "dispatched", actor="system",
                        _goals_dir=goals_dir, _now=now)
    if not r.ok:
        return None

    result, run_id = open_run(
        goal_id,
        plant,
        driver,
        model,
        goal_type=goal.get("type"),
        event_data=_goal_event_metadata(goal),
        _runs_dir=runs_dir,
        _now=now,
    )
    if not result.ok:
        transition_goal(goal_id, "queued", actor="system",
                        _goals_dir=goals_dir, _now=now)
        return None

    r = transition_goal(goal_id, "running", actor="system",
                        _goals_dir=goals_dir, _now=now)
    if not r.ok:
        return None

    return run_id


# ---------------------------------------------------------------------------
# Watchdog (shared between reconcile and Coordinator)
# ---------------------------------------------------------------------------

def _phase_watchdog(now, summary, goals_dir, runs_dir, _events_path, watchdog_seconds):
    """Kill runs that have been silent longer than the watchdog threshold."""
    now_dt = _parse_ts(now)

    for goal in list_goals(status="running", _goals_dir=goals_dir):
        goal_id = goal["id"]
        active_runs = [r for r in list_runs(goal_id, _runs_dir=runs_dir)
                       if r.get("status") == "running"]

        for run in active_runs:
            run_id = run["id"]
            last_ts = _run_last_activity_at(run, runs_dir)
            age = (now_dt - last_ts).total_seconds()

            if age <= watchdog_seconds:
                continue

            goal_obj = read_goal(goal_id, _goals_dir=goals_dir)
            goal_type = goal_obj.get("type", "spike") if goal_obj else "spike"
            close_run(run_id, "killed", goal_type, failure_reason="killed",
                      _runs_dir=runs_dir, _now=now)
            transition_goal(goal_id, "closed", actor="system",
                            closed_reason="failure", _goals_dir=goals_dir, _now=now)
            _submit_run_failure_tend(
                goal=goal_obj,
                run_id=run_id,
                run_status="killed",
                goals_dir=goals_dir,
                now=now,
            )
            summary["killed"].append(run_id)


# ---------------------------------------------------------------------------
# Synchronous reconcile (genesis, tests)
# ---------------------------------------------------------------------------

def reconcile(*,
              _goals_dir: pathlib.Path | None = None,
              _runs_dir: pathlib.Path | None = None,
              _events_path: pathlib.Path | None = None,
              _watchdog_seconds: int = _DEFAULT_WATCHDOG_SECONDS,
              _now: str | None = None,
              _dispatch_fn=None,
              _max_concurrent: int = _DEFAULT_MAX_CONCURRENT) -> dict:
    """
    One synchronous pass. Dispatches eligible goals sequentially (blocking).
    For the async concurrent loop, see Coordinator.
    """
    paths = garden_paths()
    goals_dir = _goals_dir or paths.goals_dir
    runs_dir  = _runs_dir  or paths.runs_dir
    now       = _now or _now_utc()
    dispatch  = _dispatch_fn if _dispatch_fn is not None else _driver_dispatch
    garden_root = paths.garden_root if _goals_dir is None else discover_garden_root(goals_dir)

    summary = {"dispatched": [], "skipped": [], "killed": [], "closed": [], "errors": []}

    # Dependency failure resolution
    closed_deps = resolve_dep_failures(_goals_dir=goals_dir, _now=now)
    summary["closed"].extend(closed_deps)

    # Find and dispatch eligible goals
    goals    = list_goals(_goals_dir=goals_dir)
    all_runs = _list_all_runs(runs_dir)
    blocked_conversations = _blocked_conversation_ids(garden_root)
    reserved_plants = set()
    selected = select_dispatch_goals(
        goals,
        all_runs,
        now,
        capacity=_max_concurrent,
        reserved_plants=reserved_plants,
        blocked_conversations=blocked_conversations,
    )

    # Goals without assigned_to are skipped (not an error — gardener may route them)
    for g in goals:
        if g["status"] == "queued" and not g.get("assigned_to"):
            summary["skipped"].append(g["id"])

    for goal in selected:
        run_id = _open_and_transition(goal, now, goals_dir, runs_dir, garden_root=garden_root)
        if run_id is None:
            summary["errors"].append({"goal": goal["id"], "reason": "transition_failed"})
            continue
        dispatch({**goal, "_dispatch_cutoff": now}, run_id)
        finish_now = _now_utc()
        _close_goal_after_run(goal["id"], runs_dir, goals_dir, finish_now)
        _submit_run_failure_tend(
            goal=goal,
            run_id=run_id,
            run_status=_read_run_status(run_id, runs_dir),
            goals_dir=goals_dir,
            now=finish_now,
        )
        summary["dispatched"].append(run_id)

    _submit_queued_attention_tend(
        now=now,
        goals_dir=goals_dir,
        runs_dir=runs_dir,
        poll_interval=_DEFAULT_POLL_INTERVAL,
        garden_root=garden_root,
        reserved_plants=reserved_plants,
        ignored_goal_ids={goal["id"] for goal in selected},
    )

    # Watchdog
    _phase_watchdog(now, summary, goals_dir, runs_dir, _events_path, _watchdog_seconds)

    return summary


# ---------------------------------------------------------------------------
# Coordinator: async event loop
# ---------------------------------------------------------------------------

class Coordinator:
    """
    Runs goals concurrently in worker threads.
    Two dispatch lanes: normal goals (up to max_concurrent) and converse goals
    (up to max_converse, independent lane so conversations are never blocked
    by a long-running build or tend).
    Wakes up on: run finish, somatic wake() call, not_before arrival, poll interval.
    """

    def __init__(self, root: pathlib.Path, *,
                 max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
                 max_converse: int   = _DEFAULT_MAX_CONVERSE,
                 poll_interval: int  = _DEFAULT_POLL_INTERVAL,
                 watchdog_seconds: int = _DEFAULT_WATCHDOG_SECONDS):
        self.root             = root
        self.max_concurrent   = max_concurrent
        self.max_converse     = max_converse
        self.poll_interval    = poll_interval
        self.watchdog_seconds = watchdog_seconds
        self._wakeup          = threading.Event()
        self._lock            = threading.Lock()
        self._active          = {}   # run_id → thread
        self._converse_runs   = set()  # run_ids that are converse goals
        self._idle            = False   # True once we've logged the idle transition
        self._event_cursor    = self._initial_event_cursor()
        self._startup_conversation_id = None
        self._startup_tracked_run_id = None
        self._startup_note_paths_before = set()
        self._startup_tracking_closed = False
        self.on_converse_finished = None

    def wake(self) -> None:
        """Signal the coordinator to check for new work immediately."""
        self._wakeup.set()

    def set_startup_conversation(self, conversation_id: str | None) -> None:
        self._startup_conversation_id = conversation_id
        self._startup_tracked_run_id = None
        self._startup_note_paths_before = set()
        self._startup_tracking_closed = False if conversation_id else True

    @property
    def _goals_dir(self): return garden_paths(garden_root=self.root).goals_dir

    @property
    def _runs_dir(self): return garden_paths(garden_root=self.root).runs_dir

    @property
    def _events_path(self): return garden_paths(garden_root=self.root).coordinator_events_path

    def _initial_event_cursor(self) -> int:
        try:
            return self._events_path.stat().st_size
        except OSError:
            return 0

    def run(self) -> None:
        print(f"coordinator: starting "
              f"(max_concurrent={self.max_concurrent}, poll={self.poll_interval}s)",
              flush=True)
        try:
            while True:
                try:
                    self._tick()
                except Exception as exc:
                    print(f"coordinator: tick error: {exc}", flush=True)
                timeout = self._next_wakeup()
                self._wakeup.wait(timeout=timeout)
                self._wakeup.clear()
        except KeyboardInterrupt:
            print("\ncoordinator: shutting down", flush=True)

    def _tick(self) -> None:
        now = _now_utc()
        self._emit_goal_submission_events()
        self._reap()

        # Watchdog
        summary = {"dispatched": [], "skipped": [], "killed": [], "closed": [], "errors": []}
        _phase_watchdog(now, summary, self._goals_dir, self._runs_dir,
                        self._events_path, self.watchdog_seconds)
        if summary["killed"]:
            print(f"[{now}] watchdog killed: {summary['killed']}", flush=True)

        # Dependency failure propagation
        closed_deps = resolve_dep_failures(_goals_dir=self._goals_dir, _now=now)
        if closed_deps:
            print(f"[{now}] dependency_impossible: {closed_deps}", flush=True)

        goals    = list_goals(_goals_dir=self._goals_dir)
        all_runs = _list_all_runs(self._runs_dir)
        blocked_conversations = _blocked_conversation_ids(self.root)
        dispatched_any = False
        reserved_plants = set()
        normal_selected = []
        converse_selected = []

        # --- Normal goals (non-converse) ---
        with self._lock:
            normal_active = len(self._active) - len(self._converse_runs)
        normal_capacity = self.max_concurrent - normal_active
        if normal_capacity > 0:
            normal_selected = select_dispatch_goals(
                goals,
                all_runs,
                now,
                capacity=normal_capacity,
                converse_only=False,
                reserved_plants=reserved_plants,
                blocked_conversations=blocked_conversations,
            )

        # --- Converse goals (own lane, independent of normal capacity) ---
        with self._lock:
            converse_active = len(self._converse_runs)
        converse_capacity = self.max_converse - converse_active
        if converse_capacity > 0:
            converse_selected = select_dispatch_goals(
                goals,
                all_runs,
                now,
                capacity=converse_capacity,
                converse_only=True,
                reserved_plants=reserved_plants,
                blocked_conversations=blocked_conversations,
            )

        for goal in normal_selected:
            self._dispatch_async(goal, now, converse=False)
            dispatched_any = True

        for goal in converse_selected:
            self._dispatch_async(goal, now, converse=True)
            dispatched_any = True

        _submit_queued_attention_tend(
            now=now,
            goals_dir=self._goals_dir,
            runs_dir=self._runs_dir,
            poll_interval=self.poll_interval,
            garden_root=self.root,
            reserved_plants=reserved_plants,
            ignored_goal_ids={goal["id"] for goal in normal_selected},
        )

        if dispatched_any:
            self._idle = False

        # Log once when the garden goes idle
        with self._lock:
            total_active = len(self._active)
        if not dispatched_any and total_active == 0:
            if not self._idle:
                queued_total = sum(1 for g in goals if g["status"] == "queued")
                if queued_total:
                    print(f"[{now}] garden waiting — {queued_total} goal(s) not yet eligible",
                          flush=True)
                else:
                    print(f"[{now}] garden idle", flush=True)
                self._idle = True

    def _emit_goal_submission_events(self) -> None:
        """Print new GoalSubmitted events appended since the last tick."""
        try:
            size = self._events_path.stat().st_size
        except OSError:
            self._event_cursor = 0
            return

        if size < self._event_cursor:
            self._event_cursor = 0

        try:
            with open(self._events_path, encoding="utf-8") as fh:
                fh.seek(self._event_cursor)
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "GoalSubmitted":
                        print(_format_goal_submission_event(event), flush=True)
                self._event_cursor = fh.tell()
        except OSError as exc:
            print(f"coordinator: event tail error: {exc}", flush=True)

    def _startup_conversation_ready(self) -> bool:
        conv_id = self._startup_conversation_id
        if not conv_id or self._startup_tracking_closed:
            return False
        conv_dir = garden_paths(garden_root=self.root).conversations_dir
        conv = read_conversation(conv_id, _conv_dir=conv_dir)
        if conv is None or conv.get("status") != "open":
            self._startup_tracking_closed = True
            return False
        if conv.get("started_by") != "system" or conv.get("channel") != "filesystem":
            self._startup_tracking_closed = True
            return False
        messages = read_messages(conv_id, _conv_dir=conv_dir)
        if any(message.get("sender") == "operator" for message in messages):
            self._startup_tracking_closed = True
            return False
        return True

    def _publish_startup_conversation_update(self, content: str, *, now: str) -> None:
        conv_id = self._startup_conversation_id
        if not conv_id:
            return
        conv_dir = garden_paths(garden_root=self.root).conversations_dir
        try:
            FilesystemChannel(self.root).send(conv_id, content)
        except Exception as exc:
            print(
                f"startup: failed to deliver startup progress update for {conv_id}: {exc}",
                flush=True,
            )
            return
        result, _ = append_message(
            conv_id,
            "system",
            content,
            channel="filesystem",
            _conv_dir=conv_dir,
            _now=now,
        )
        if not result.ok:
            print(
                f"startup: failed to record startup progress update for {conv_id}: "
                f"{result.reason} — {result.detail}",
                flush=True,
            )

    def _record_startup_reply_representation(self, content: str, *, now: str) -> None:
        conv_id = self._startup_conversation_id
        if not conv_id:
            return
        conv_dir = garden_paths(garden_root=self.root).conversations_dir
        result, _ = append_message(
            conv_id,
            "garden",
            content,
            channel="filesystem",
            _conv_dir=conv_dir,
            _now=now,
        )
        if not result.ok:
            print(
                f"startup: failed to record startup reply representation for {conv_id}: "
                f"{result.reason} — {result.detail}",
                flush=True,
            )

    def _maybe_publish_startup_run_started(self, goal: dict, run_id: str, *, now: str) -> None:
        if self._startup_tracked_run_id is not None:
            return
        if not self._startup_conversation_ready():
            return
        self._startup_tracked_run_id = run_id
        self._startup_note_paths_before = set(_filesystem_reply_note_paths(self.root))
        self._publish_startup_conversation_update(
            _render_startup_run_started_update(goal, run_id),
            now=now,
        )

    def _maybe_publish_startup_run_finished(self, run_id: str, status: str, *, now: str) -> None:
        if self._startup_tracked_run_id != run_id:
            return
        new_note_paths = sorted(
            set(_filesystem_reply_note_paths(self.root)) - self._startup_note_paths_before
        )
        represented_note_paths = [
            path
            for path in new_note_paths
            if not _conversation_delivery_note_path(path, self._startup_conversation_id)
        ]
        if self._startup_conversation_ready():
            self._publish_startup_conversation_update(
                _render_startup_run_finished_update(run_id, status, represented_note_paths),
                now=now,
            )
            for note_path in represented_note_paths:
                note_content = _read_filesystem_reply_note(self.root, note_path)
                if note_content is None:
                    continue
                self._record_startup_reply_representation(
                    _render_startup_reply_representation(
                        run_id,
                        note_path,
                        note_content,
                    ),
                    now=now,
                )
        self._startup_tracked_run_id = None
        self._startup_note_paths_before = set()
        self._startup_tracking_closed = True

    def _dispatch_async(self, goal: dict, now: str, *, converse: bool = False) -> None:
        goal_id = goal["id"]
        plant   = goal.get("assigned_to", "gardener")
        dispatch_goal = {**goal, "_dispatch_cutoff": now}

        run_id = _open_and_transition(
            goal,
            now,
            self._goals_dir,
            self._runs_dir,
            garden_root=self.root,
        )
        if run_id is None:
            print(f"coordinator: failed to open run for {goal_id}", flush=True)
            return

        if not converse:
            self._maybe_publish_startup_run_started(goal, run_id, now=now)

        def worker():
            try:
                _driver_dispatch(dispatch_goal, run_id, _garden_root=self.root)
                finish_now = _now_utc()
                _close_goal_after_run(
                    goal_id, self._runs_dir, self._goals_dir, finish_now
                )
                status = _read_run_status(run_id, self._runs_dir) or "unknown"
                _submit_run_failure_tend(
                    goal=goal,
                    run_id=run_id,
                    run_status=status,
                    goals_dir=self._goals_dir,
                    now=finish_now,
                )
                if not converse:
                    self._maybe_publish_startup_run_finished(
                        run_id,
                        status,
                        now=finish_now,
                    )
                print(f"[{finish_now}] completed {run_id} ({status})", flush=True)
            except Exception as exc:
                print(f"coordinator: worker error for {run_id}: {exc}", flush=True)
            finally:
                with self._lock:
                    self._active.pop(run_id, None)
                    self._converse_runs.discard(run_id)
                self._wakeup.set()   # wake main loop: plant is free
                if converse and self.on_converse_finished:
                    try:
                        self.on_converse_finished()
                    except Exception as exc:
                        print(
                            f"coordinator: converse-finish callback error for {run_id}: "
                            f"{exc}",
                            flush=True,
                        )

        t = threading.Thread(target=worker, daemon=True, name=f"run-{run_id}")
        with self._lock:
            self._active[run_id] = t
            if converse:
                self._converse_runs.add(run_id)
        t.start()
        label = "converse" if converse else "goal"
        print(f"[{now}] dispatched {label} {run_id} (plant={plant})", flush=True)

    def _reap(self) -> None:
        """Remove finished threads from the active registry."""
        with self._lock:
            done = [rid for rid, t in self._active.items() if not t.is_alive()]
            for rid in done:
                del self._active[rid]

    def _next_wakeup(self) -> float:
        """
        Seconds until next scheduled wakeup.
        Sleeps until the earliest not_before among waiting goals, capped at poll_interval.
        In true idle (nothing queued, nothing running) returns poll_interval as safety net.
        """
        goals  = list_goals(_goals_dir=self._goals_dir)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        now_s  = _now_utc()

        future_secs = []
        for g in goals:
            if g["status"] == "queued" and g.get("not_before", "") > now_s:
                try:
                    secs = (_parse_ts(g["not_before"]) - now_dt).total_seconds()
                    if secs > 0:
                        future_secs.append(secs)
                except ValueError:
                    pass

        if future_secs:
            return max(1.0, min(min(future_secs), self.poll_interval))
        return float(self.poll_interval)


def cycle(root: pathlib.Path, *,
          max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
          poll_interval: int  = _DEFAULT_POLL_INTERVAL) -> None:
    """Run the coordinator loop. Blocks until KeyboardInterrupt."""
    Coordinator(root, max_concurrent=max_concurrent,
                poll_interval=poll_interval).run()
