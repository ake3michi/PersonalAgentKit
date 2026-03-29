"""
Driver dispatch: launches goal runs through a backend plugin.

dispatch(goal, run_id) is the entry point — it replaces _default_dispatch
in the coordinator. It runs synchronously: it blocks until the agent
subprocess exits, then finalizes the run record.

Context loading:
  Genesis run (plant has no memory): seed content is used as system context.
  Normal run (plant has memory):     memory + skills + knowledge are loaded.

The backend CLI's raw output stream is written to
<runtime-root>/runs/<run-id>/events.jsonl. Cost, status, session ids, and
final text are parsed by the selected driver plugin at close time.
"""

import datetime
import hashlib
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading

from .driver_plugins import (
    get_driver_plugin,
    resolve_driver_name,
    resolve_model_name,
    resolve_reasoning_effort,
)
from .events import append_event
from .goals import _goal_event_metadata, list_goals, materialize_dispatch_packet
from .operator_messages import (
    OPERATOR_MESSAGE_KIND_TEND_SURVEY,
    read_operator_message_records,
)
from .plant_commission import render_plant_commission_context
from .submit import submit_goal
from .runs import close_run, update_run_lifecycle
from .tend import tend_metadata
from .validate import REFLECTION_REQUIRED_TYPES
from .conversations import (
    read_conversation, update_conversation, append_message,
    read_messages, read_conversation_summary, tail_messages_after,
    append_conversation_turn, write_conversation_checkpoint,
    compute_context_pressure, describe_context_pressure,
    compute_state_diff, compute_activity_diff, format_diff,
)

from .channels import FilesystemChannel
from .garden import filesystem_reply_dir, garden_paths

# Registry of channel types. Maps channel name → callable(root) → Channel.
# Used by _dispatch_conversation to reconstruct the right output channel.
# The operator is the trust boundary for converse goals: messages arrive via
# a channel the operator controls, so converse goals inherit operator trust.
_CHANNEL_REGISTRY = {
    "filesystem": lambda r: FilesystemChannel(r),
}


def _make_channel(channel_name: str, root):
    factory = _CHANNEL_REGISTRY.get(channel_name)
    return factory(root) if factory else None


# Registry of active subprocesses. Populated by _launch, cleaned up on exit.
# Allows kill_active_procs() to terminate orphans on coordinator shutdown.
_active_procs: set = set()
_active_procs_lock = threading.Lock()


def kill_active_procs() -> None:
    """Kill all backend subprocesses currently tracked by _launch."""
    with _active_procs_lock:
        procs = list(_active_procs)
    for proc in procs:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


_GARDEN_ROOT = pathlib.Path(".")

_REFLECTION_PROMPT = (
    "You have just completed a goal. Please write a brief reflection: "
    "what did you learn, what worked well, and what would you do differently? "
    "Be specific and concise — a few sentences is enough."
)
_POST_REPLY_HOP_GOAL_TYPE = "converse"
_POST_REPLY_HOP_PRIORITY = 8
_CONVERSATION_EXECUTION_POLICY = """# Converse execution policy

Converse runs are the garden's somatic layer. Use them for intake,
clarification, delegation, and status reporting.

Default to delegation. If the operator's request would require edits,
tests, research, extended inspection, or any other substantive execution,
do not do that work inside this converse run. Submit a non-`converse`
goal instead, usually assigned to `gardener`, and reply with what goal
you submitted, why, and what the operator should expect next.

Delegation is execution mode, not loss of ownership. You stay responsible
for the outcome even when the work runs asynchronously.

Only answer inline when the operator is asking for brief conversational
work that does not require durable execution. If you are unsure whether
to delegate, delegate.

Use the standard goal submission API:

```python
from system.submit import append_goal_supplement, submit_goal

result, goal_id = submit_goal({
    "type": "fix",
    "submitted_by": "gardener",
    "assigned_to": "gardener",
    "body": "Describe the durable work to execute.",
})
```

If a later conversation turn needs to update a still-queued durable goal,
keep the original goal body immutable and append a supplement instead:

```python
result, supplement_id = append_goal_supplement(goal_id, {
    "kind": "clarification",
    "content": "Add the later clarification or constraint here.",
})
```

Use supplements only for queued goals whose original task is still correct.
If the task itself changed, submit a replacement goal instead of rewriting it.

If the operator specifically wants a fresh garden-state survey, prefer a
bounded `tend` goal over generic research:

```python
from system.submit import submit_tend_goal

result, goal_id = submit_tend_goal(
    body="Perform a bounded tend pass for the requested garden-state survey.",
    submitted_by="gardener",
    trigger_kinds=["operator_request"],
)
```
"""


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_run_lifecycle(run_id: str, phase: str | None, *,
                       root: pathlib.Path) -> None:
    paths = garden_paths(garden_root=root)
    result = update_run_lifecycle(
        run_id,
        phase=phase,
        _runs_dir=paths.runs_dir,
    )
    if result.ok:
        return
    print(
        f"driver: run lifecycle update failed for {run_id}: "
        f"{result.reason} — {result.detail}",
        file=sys.stderr,
    )


def _write_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _agent_env(goal: dict, run_id: str, *, root: pathlib.Path) -> dict:
    goal_id = goal.get("id")
    goals_dir = None
    if goal_id:
        candidate_dirs = []
        default_goals_dir = garden_paths(garden_root=root).goals_dir
        candidate_dirs.append(default_goals_dir)
        legacy_goals_dir = root / "goals"
        if legacy_goals_dir != default_goals_dir:
            candidate_dirs.append(legacy_goals_dir)
        for candidate in candidate_dirs:
            if (candidate / f"{goal_id}.json").exists():
                goals_dir = candidate.resolve()
                break

    env = {
        "PAK2_GARDEN_ROOT": str(root.resolve()),
        "PAK2_CURRENT_GOAL_ID": goal.get("id"),
        "PAK2_CURRENT_RUN_ID": run_id,
        "PAK2_CURRENT_GOAL_TYPE": goal.get("type"),
        "PAK2_CURRENT_PLANT": goal.get("assigned_to", "gardener"),
    }
    if goals_dir is not None:
        env["PAK2_CURRENT_GOALS_DIR"] = str(goals_dir)
    conversation_id = goal.get("conversation_id")
    if conversation_id:
        env["PAK2_CURRENT_CONVERSATION_ID"] = conversation_id
    source_message_id = goal.get("source_message_id")
    if source_message_id:
        env["PAK2_CURRENT_CONVERSATION_MESSAGE_ID"] = source_message_id
    return {
        key: str(value)
        for key, value in env.items()
        if value is not None
    }


def _indent_block(text: str, prefix: str = "  ") -> str:
    return "\n".join(
        f"{prefix}{line}" if line else prefix.rstrip()
        for line in str(text).splitlines()
    )


def _relative_prompt_path(path: pathlib.Path, *, root: pathlib.Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _build_pre_dispatch_supplements_section(dispatch_packet: dict | None) -> str | None:
    if not dispatch_packet:
        return None

    lines = ["# Pre-dispatch supplements", ""]
    supplements = dispatch_packet.get("supplements") or []
    if not supplements:
        lines.append("No pre-dispatch supplements were attached.")
        return "\n".join(lines)

    for supplement in supplements:
        source = supplement.get("source") or {}
        source_bits = []
        if source.get("conversation_id"):
            source_bits.append(f"conversation {source['conversation_id']}")
        if source.get("message_id"):
            source_bits.append(str(source["message_id"]))
        header = f"- [{supplement.get('ts')}] {supplement.get('kind')}"
        if source_bits:
            header += " from " + ", ".join(source_bits)
        lines.append(header)
        lines.append(_indent_block(supplement.get("content", "")))
    return "\n".join(lines)


def _post_reply_hop_event_data(goal: dict | None, *,
                               conversation_id: str | None = None,
                               outcome: str | None = None,
                               checkpoint_id: str | None = None) -> dict:
    if not goal or not goal.get("post_reply_hop"):
        return {}

    hop_record = goal.get("post_reply_hop") or {}
    event = {
        "goal_subtype": "post_reply_hop",
        "conversation_id": conversation_id or goal.get("conversation_id"),
        "hop_automatic": bool(hop_record.get("automatic")),
        "hop_requested_by": hop_record.get("requested_by"),
        "hop_reason": hop_record.get("reason"),
        "hop_outcome": outcome,
        "checkpoint_id": checkpoint_id,
    }
    return {key: value for key, value in event.items() if value is not None}


def _emit_conversation_hop_queued(*,
                                  goal: dict,
                                  run_id: str,
                                  root: pathlib.Path,
                                  conversation_id: str,
                                  hop_goal_id: str,
                                  completed_at: str,
                                  reply_message_id: str,
                                  pending_hop: dict | None) -> None:
    paths = garden_paths(garden_root=root)
    append_event(
        {
            "ts": completed_at,
            "type": "ConversationHopQueued",
            "actor": "system",
            "goal": goal["id"],
            "run": run_id,
            "conversation_id": conversation_id,
            "hop_goal": hop_goal_id,
            "source_message_id": reply_message_id,
            "hop_requested_by": (pending_hop or {}).get("requested_by", "system"),
            "hop_reason": (pending_hop or {}).get("reason", "automatic pressure handoff"),
            "hop_automatic": not bool(pending_hop),
        },
        path=paths.coordinator_events_path,
    )


def _emit_conversation_hop_queue_failed(*,
                                        goal: dict,
                                        run_id: str,
                                        root: pathlib.Path,
                                        conversation_id: str,
                                        completed_at: str,
                                        detail: str,
                                        reply_message_id: str | None,
                                        pending_hop: dict | None) -> None:
    paths = garden_paths(garden_root=root)
    event = {
        "ts": completed_at,
        "type": "ConversationHopQueueFailed",
        "actor": "system",
        "goal": goal["id"],
        "run": run_id,
        "conversation_id": conversation_id,
        "hop_requested_by": (pending_hop or {}).get("requested_by", "system"),
        "hop_reason": (pending_hop or {}).get("reason", "automatic pressure handoff"),
        "hop_automatic": not bool(pending_hop),
        "detail": detail,
    }
    if reply_message_id:
        event["source_message_id"] = reply_message_id
    append_event(event, path=paths.coordinator_events_path)


def _session_ordinal(conv: dict) -> int:
    try:
        return int(conv.get("session_ordinal") or 0)
    except (TypeError, ValueError):
        return 0


def _session_turns(conv: dict) -> int:
    try:
        return int(conv.get("session_turns") or 0)
    except (TypeError, ValueError):
        return 0


def _conversation_turn_mode(session_id: str | None, summary: str | None) -> str:
    if session_id:
        return "resumed"
    return "fresh-handoff" if summary else "fresh-start"


def _pending_lineage_label(conv: dict, turn_mode: str) -> str:
    session_ordinal = _session_ordinal(conv)
    session_turns = _session_turns(conv)
    if turn_mode == "resumed":
        active = session_ordinal or 1
        return f"session {active} turn {session_turns + 1}"
    next_session = session_ordinal + 1 if session_ordinal or turn_mode != "fresh-start" else 1
    checkpoint_id = conv.get("last_checkpoint_id")
    if turn_mode == "fresh-handoff" and checkpoint_id:
        return f"session {next_session} via {checkpoint_id}"
    return f"session {next_session}"


def _completed_lineage_label(session_ordinal: int, session_turn: int,
                             checkpoint_id: str | None) -> str:
    label = f"session {session_ordinal} turn {session_turn}"
    if checkpoint_id and session_turn == 1:
        return f"{label} via {checkpoint_id}"
    return label


def _checkpoint_marker(messages: list[dict]) -> str | None:
    if not messages:
        return None
    history = messages[:-1] if messages[-1].get("sender") == "operator" else messages
    if not history:
        return None
    return history[-1].get("id")


def _build_conversation_status_block(conv: dict, turn_mode: str, pressure: dict, *,
                                     pending_hop: dict | None = None,
                                     checkpoint_record: dict | None = None,
                                     hop_error: str | None = None) -> str:
    lines = [
        "# Conversation session status",
        "",
        f"- Turn mode: {turn_mode}",
        f"- Active lineage: {_pending_lineage_label(conv, turn_mode)}",
        f"- Context pressure: {describe_context_pressure(pressure)}",
    ]
    reasons = pressure.get("reasons") or []
    if reasons:
        lines.append(f"- Pressure signals: {', '.join(reasons)}")
    if pending_hop:
        lines.append(
            f"- Requested hop: {pending_hop.get('requested_by', 'operator')} "
            f"({pending_hop.get('reason', 'session hop')})"
        )
    if checkpoint_record:
        lines.append(
            f"- Checkpoint prepared this turn: {checkpoint_record['id']} "
            f"({checkpoint_record.get('reason', 'handoff')})"
        )
    elif hop_error:
        lines.append(f"- Hop attempt could not complete: {hop_error}")
    lines.extend([
        "",
        "Keep the operator-facing reply natural. Only mention session plumbing if the operator asks.",
    ])
    return "\n".join(lines)


def _build_conversation_checkpoint_prompt(pressure: dict, pending_hop: dict | None) -> str:
    lines = [
        "# Conversation checkpoint request",
        "",
        "You have already answered the operator in this session.",
        "Create an internal handoff summary so the next operator message can start",
        "from a fresh session. Do not answer the operator again.",
        "Do not mention the checkpoint.",
        "Produce only markdown.",
        "",
        "Use exactly this structure:",
        "# Conversation Handoff Summary",
        "## Operator and style",
        "- ...",
        "## Durable context from earlier turns",
        "- ...",
        "## Current agenda",
        "- ...",
        "## Commitments and open loops",
        "- ...",
    ]
    if pending_hop:
        lines.extend([
            "",
            f"Checkpoint trigger: operator request from {pending_hop.get('requested_by', 'operator')}.",
            f"Requested reason: {pending_hop.get('reason', 'session hop')}",
        ])
    elif pressure.get("reasons"):
        lines.extend([
            "",
            "Checkpoint trigger: conversation pressure is high enough to justify a fresh session.",
            "Pressure signals: " + ", ".join(pressure["reasons"]),
        ])
    lines.extend([
        "",
        "Preserve durable facts, live commitments, and tone. Omit routine filler and keep it concise.",
    ])
    return "\n".join(lines)


def _build_conversation_policy_block() -> str:
    return _CONVERSATION_EXECUTION_POLICY


def _build_resumed_conversation_prompt(goal: dict, diff_text: str, *,
                                       status_block: str) -> str:
    parts = [status_block, _build_conversation_policy_block()]
    if diff_text:
        parts.append(diff_text)
    parts.append(f"# New message\n\nOperator: {goal.get('body', '')}")
    return "\n\n".join(parts)


def _submit_post_reply_hop_goal(
    *,
    goal: dict,
    run_id: str,
    root: pathlib.Path,
    conv_id: str,
    reply_message_id: str,
    completed_at: str,
    session_id: str,
    session_ordinal: int,
    session_turns: int,
    pressure: dict,
    pending_hop: dict | None,
    driver_name: str,
    model: str,
    reasoning_effort: str | None,
) -> tuple[dict | None, str | None]:
    requested_by = (pending_hop or {}).get("requested_by", "system")
    reason = (pending_hop or {}).get("reason", "automatic pressure handoff")
    hop_record = {
        "requested_at": (pending_hop or {}).get("requested_at", completed_at),
        "requested_by": requested_by,
        "reason": reason,
        "automatic": not bool(pending_hop),
        "source_goal_id": goal["id"],
        "source_run_id": run_id,
        "source_reply_message_id": reply_message_id,
        "source_reply_recorded_at": completed_at,
        "source_session_id": session_id,
        "source_session_ordinal": session_ordinal,
        "source_session_turns": session_turns,
        "pressure": pressure,
    }

    goal_data = {
        "type": _POST_REPLY_HOP_GOAL_TYPE,
        "submitted_by": goal.get("assigned_to", "gardener"),
        "assigned_to": goal.get("assigned_to", "gardener"),
        "priority": _POST_REPLY_HOP_PRIORITY,
        "driver": driver_name,
        "model": model,
        "body": (
            f"Checkpoint conversation {conv_id} after reply {reply_message_id} "
            "so the next turn can resume from a fresh session."
        ),
        "conversation_id": conv_id,
        "post_reply_hop": hop_record,
    }
    if reasoning_effort:
        goal_data["reasoning_effort"] = reasoning_effort

    result, hop_goal_id = submit_goal(
        goal_data,
        _goals_dir=garden_paths(garden_root=root).goals_dir,
        _now=completed_at,
    )
    if not result.ok or hop_goal_id is None:
        detail = result.detail or result.reason
        return None, f"post-reply hop goal submission failed: {detail}"

    hop_record["goal_id"] = hop_goal_id
    return hop_record, None


def _clear_post_reply_hop(conv_id: str, goal_id: str, *,
                          conv_dir: pathlib.Path,
                          now: str) -> None:
    conv = read_conversation(conv_id, _conv_dir=conv_dir)
    if conv is None:
        return
    active_hop = conv.get("post_reply_hop") or {}
    if active_hop.get("goal_id") != goal_id:
        return
    result = update_conversation(
        conv_id,
        _conv_dir=conv_dir,
        _now=now,
        post_reply_hop=None,
    )
    if not result.ok:
        print(
            f"driver: failed to clear post-reply hop for {conv_id}: "
            f"{result.reason} — {result.detail}",
            file=sys.stderr,
        )


def _dispatch_post_reply_hop(goal: dict, run_id: str,
                             root: pathlib.Path, conv_id: str,
                             driver_name: str, model: str,
                             reasoning_effort: str | None) -> None:
    paths = garden_paths(garden_root=root)
    conv_dir = paths.conversations_dir
    run_dir = paths.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"

    hop_record = dict(goal.get("post_reply_hop") or {})
    artifact = {
        "conversation_id": conv_id,
        "goal_id": goal["id"],
        "run_id": run_id,
        "requested_by": hop_record.get("requested_by"),
        "reason": hop_record.get("reason"),
        "automatic": bool(hop_record.get("automatic")),
        "source_goal_id": hop_record.get("source_goal_id"),
        "source_run_id": hop_record.get("source_run_id"),
        "source_reply_message_id": hop_record.get("source_reply_message_id"),
        "source_session_id": hop_record.get("source_session_id"),
        "source_session_ordinal": hop_record.get("source_session_ordinal"),
        "source_session_turns": hop_record.get("source_session_turns"),
        "checkpoint_id": None,
        "outcome": None,
        "error": None,
    }

    cost = {"source": "unknown"}
    status = "success"
    conv = read_conversation(conv_id, _conv_dir=conv_dir)
    if conv is None:
        artifact["outcome"] = "failure"
        artifact["error"] = "conversation not found"
        status = "failure"
    else:
        active_hop = conv.get("post_reply_hop") or {}
        if active_hop and active_hop.get("goal_id") not in (None, goal["id"]):
            artifact["outcome"] = "superseded"
            artifact["error"] = (
                f"conversation now tracks {active_hop.get('goal_id')} instead"
            )
        else:
            session_id = hop_record.get("source_session_id") or conv.get("session_id")
            compacted_through = hop_record.get("source_reply_message_id")
            if not session_id:
                artifact["outcome"] = "stale"
                artifact["error"] = "no active session to checkpoint"
            elif not compacted_through:
                artifact["outcome"] = "failure"
                artifact["error"] = "missing reply message marker"
                status = "failure"
            else:
                pending_hop = None
                if not hop_record.get("automatic"):
                    pending_hop = {
                        "requested_by": hop_record.get("requested_by", "operator"),
                        "reason": hop_record.get("reason", "session hop"),
                    }
                prompt = _build_conversation_checkpoint_prompt(
                    hop_record.get("pressure") or {},
                    pending_hop,
                )
                _set_run_lifecycle(run_id, "checkpointing-hop", root=root)
                try:
                    returncode = _launch(
                        model,
                        prompt,
                        events_path,
                        session_id=session_id,
                        driver_name=driver_name,
                        cwd=root,
                        reasoning_effort=reasoning_effort,
                        env=_agent_env(goal, run_id, root=root),
                    )
                finally:
                    _set_run_lifecycle(run_id, None, root=root)
                cost, launch_status = _parse_events(
                    events_path,
                    returncode,
                    driver_name=driver_name,
                )
                if launch_status != "success":
                    artifact["outcome"] = "failure"
                    artifact["error"] = f"checkpoint subprocess failed ({returncode})"
                    status = "failure"
                else:
                    summary = _extract_last_text(events_path, driver_name=driver_name)
                    if not summary:
                        artifact["outcome"] = "failure"
                        artifact["error"] = "checkpoint produced no summary"
                        status = "failure"
                    else:
                        checkpoint_ts = _now_utc()
                        result, checkpoint_record = write_conversation_checkpoint(
                            conv_id,
                            summary,
                            compacted_through,
                            requested_by=hop_record.get("requested_by", "system"),
                            reason=hop_record.get("reason", "automatic pressure handoff"),
                            source_session_id=session_id,
                            source_session_ordinal=hop_record.get("source_session_ordinal"),
                            source_session_turns=hop_record.get("source_session_turns"),
                            run_id=run_id,
                            driver=driver_name,
                            model=model,
                            pressure=hop_record.get("pressure"),
                            _conv_dir=conv_dir,
                            _now=checkpoint_ts,
                            _events_path=paths.coordinator_events_path,
                            _event_goal=goal["id"],
                        )
                        if not result.ok or checkpoint_record is None:
                            detail = result.detail or result.reason
                            artifact["outcome"] = "failure"
                            artifact["error"] = f"checkpoint record failed: {detail}"
                            status = "failure"
                        else:
                            _write_json(run_dir / "record.json", checkpoint_record)
                            artifact["checkpoint_id"] = checkpoint_record["id"]
                            artifact["outcome"] = "checkpointed"
                            conv_after = read_conversation(conv_id, _conv_dir=conv_dir)
                            messages_after = read_messages(conv_id, _conv_dir=conv_dir)
                            summary_after = read_conversation_summary(
                                conv_id,
                                _conv_dir=conv_dir,
                            )
                            if conv_after is not None:
                                pressure_after = compute_context_pressure(
                                    conv_after,
                                    messages_after,
                                    summary=summary_after,
                                )
                                result = update_conversation(
                                    conv_id,
                                    _conv_dir=conv_dir,
                                    _now=checkpoint_ts,
                                    last_pressure=pressure_after,
                                )
                                if not result.ok:
                                    print(
                                        f"driver: failed to update post-hop pressure for {conv_id}: "
                                        f"{result.reason} — {result.detail}",
                                        file=sys.stderr,
                                    )

    completed_at = _now_utc()
    if status != "success":
        _clear_post_reply_hop(
            conv_id,
            goal["id"],
            conv_dir=conv_dir,
            now=completed_at,
        )
    _write_json(run_dir / "conversation-hop.json", artifact)

    kwargs: dict = {
        "cost": cost,
        "event_data": _post_reply_hop_event_data(
            goal,
            conversation_id=conv_id,
            outcome=artifact["outcome"],
            checkpoint_id=artifact["checkpoint_id"],
        ),
        "_runs_dir": paths.runs_dir,
    }
    if status != "success":
        kwargs["failure_reason"] = "failure"
    result = close_run(run_id, status, goal.get("type", "spike"), **kwargs)
    if not result.ok:
        print(
            f"driver: close_run failed for {run_id}: {result.reason} — {result.detail}",
            file=sys.stderr,
        )


def dispatch(goal: dict, run_id: str, *,
             _garden_root: pathlib.Path | None = None) -> None:
    """
    Launch the agent subprocess for this goal/run. Blocks until complete.
    Finalizes the run record on exit.
    """
    root = _garden_root or _GARDEN_ROOT
    paths = garden_paths(garden_root=root)
    driver_name = resolve_driver_name(goal, garden_root=root)
    model = resolve_model_name(goal, driver_name=driver_name, garden_root=root)
    reasoning_effort = resolve_reasoning_effort(goal, garden_root=root)
    conv_id = goal.get("conversation_id")
    if goal.get("post_reply_hop"):
        _dispatch_post_reply_hop(
            goal,
            run_id,
            root,
            conv_id,
            driver_name,
            model,
            reasoning_effort,
        )
    elif conv_id:
        _dispatch_conversation(goal, run_id, root, conv_id,
                               driver_name, model, reasoning_effort)
    else:
        plant_name = goal.get("assigned_to", "gardener")
        events_path = paths.runs_dir / run_id / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        cutoff = str(goal.get("_dispatch_cutoff") or _now_utc())
        result, dispatch_packet = materialize_dispatch_packet(
            goal,
            run_id,
            cutoff,
            _goals_dir=paths.goals_dir,
            _runs_dir=paths.runs_dir,
        )
        if not result.ok:
            events_path.write_text(
                json.dumps({
                    "type": "error",
                    "error": (
                        "dispatch packet materialization failed: "
                        f"{result.reason} — {result.detail}"
                    ),
                }) + "\n",
                encoding="utf-8",
            )
            _finalize(run_id, goal, 1, events_path, root,
                      driver_name=driver_name)
            return
        tend_snapshot = None
        if goal.get("type") == "tend":
            tend_snapshot = _capture_tend_snapshot(goal, root)
            _emit_tend_started(goal, run_id, root, now=_now_utc())
        prompt = _build_prompt(
            goal,
            run_id,
            plant_name,
            root=root,
            dispatch_packet=dispatch_packet,
        )
        returncode = _launch(model, prompt, events_path,
                             driver_name=driver_name, cwd=root,
                             reasoning_effort=reasoning_effort,
                             env=_agent_env(goal, run_id, root=root))
        _finalize(run_id, goal, returncode, events_path, root,
                  driver_name=driver_name, tend_snapshot=tend_snapshot)


def _dispatch_conversation(goal: dict, run_id: str,
                           root: pathlib.Path, conv_id: str,
                           driver_name: str, model: str,
                           reasoning_effort: str | None) -> None:
    """Dispatch a converse goal: resume session if possible, inject diff, extract response."""
    paths = garden_paths(garden_root=root)
    conv_dir  = paths.conversations_dir
    plant_dir = paths.plants_dir / goal.get("assigned_to", "gardener")

    conv = read_conversation(conv_id, _conv_dir=conv_dir)
    if conv is None:
        print(f"driver: conversation {conv_id} not found", file=sys.stderr)
        return

    context_at = conv.get("context_at", conv["started_at"])
    session_id = conv.get("session_id")
    pending_hop = conv.get("pending_hop")
    session_id_before = session_id
    pending_hop_before = pending_hop
    prior_session_ordinal = _session_ordinal(conv)
    prior_session_turns = _session_turns(conv)
    messages_before = read_messages(conv_id, _conv_dir=conv_dir)
    summary = read_conversation_summary(conv_id, _conv_dir=conv_dir)
    pressure_before = compute_context_pressure(conv, messages_before, summary=summary)

    # Compute diffs
    state_diff    = compute_state_diff(plant_dir, context_at)
    activity_diff = compute_activity_diff(
        paths.coordinator_events_path,
        context_at,
        exclude_goal_ids={goal["id"]},
    )
    diff_text = format_diff(state_diff, activity_diff)

    events_path = paths.runs_dir / run_id / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)

    hop_error = None
    if pending_hop and not session_id:
        update_conversation(
            conv_id,
            _conv_dir=conv_dir,
            _now=conv.get("last_activity_at"),
            pending_hop=None,
        )
        conv = read_conversation(conv_id, _conv_dir=conv_dir) or conv
        pending_hop = None
        session_id = conv.get("session_id")

    should_hop_after_reply = bool(session_id) and (
        bool(pending_hop) or pressure_before.get("needs_hop")
    )

    turn_mode = _conversation_turn_mode(session_id, summary)
    status_block = _build_conversation_status_block(
        conv,
        turn_mode,
        pressure_before,
        pending_hop=pending_hop,
        hop_error=hop_error,
    )

    if session_id:
        prompt = _build_resumed_conversation_prompt(
            goal,
            diff_text,
            status_block=status_block,
        )
    else:
        prompt = _build_conversation_prompt(
            goal, run_id, conv, diff_text, plant_dir, root, status_block=status_block
        )

    returncode = _launch(model, prompt, events_path, session_id=session_id,
                         driver_name=driver_name, cwd=root,
                         reasoning_effort=reasoning_effort,
                         env=_agent_env(goal, run_id, root=root))

    completed_at = _now_utc()
    response_text = _extract_last_text(events_path, driver_name=driver_name)
    cost, status = _parse_events(events_path, returncode, driver_name=driver_name)

    new_session_id = _parse_session_id(events_path, driver_name=driver_name) or session_id
    reply_recorded = False
    reply_message_id = None
    channel = _make_channel(conv["channel"], root)
    if response_text:
        result, reply_message_id = append_message(
            conv_id,
            "garden",
            response_text,
            channel=conv["channel"],
            _conv_dir=conv_dir,
            _now=completed_at,
        )
        if result.ok:
            reply_recorded = True
        else:
            print(
                f"driver: failed to record conversation reply for {conv_id}: "
                f"{result.reason} — {result.detail}",
                file=sys.stderr,
            )
        try:
            if channel:
                channel.send(conv_id, response_text)
            else:
                print(f"driver: no channel handler for {conv['channel']!r}, "
                      f"reply stored in conversation only", file=sys.stderr)
        except Exception as exc:
            print(f"driver: channel delivery failed for {conv_id}: {exc}", file=sys.stderr)

    session_ordinal_after_reply = prior_session_ordinal
    session_turn_after_reply = prior_session_turns
    session_started_at_after_reply = conv.get("session_started_at")
    if turn_mode == "resumed":
        session_ordinal_after_reply = prior_session_ordinal or 1
        session_turn_after_reply = prior_session_turns + 1
        session_started_at_after_reply = conv.get("session_started_at") or completed_at
    elif new_session_id:
        session_ordinal_after_reply = prior_session_ordinal + 1 if prior_session_ordinal else 1
        session_turn_after_reply = 1
        session_started_at_after_reply = completed_at
    else:
        session_turn_after_reply = 0
        session_started_at_after_reply = None

    post_reply_hop = None
    if should_hop_after_reply:
        if not reply_recorded or not reply_message_id:
            hop_error = "reply was not recorded before post-reply hop"
        elif not new_session_id:
            hop_error = "no active session available for post-reply hop"
        else:
            post_reply_hop, hop_error = _submit_post_reply_hop_goal(
                goal=goal,
                run_id=run_id,
                root=root,
                conv_id=conv_id,
                reply_message_id=reply_message_id,
                completed_at=completed_at,
                session_id=new_session_id,
                session_ordinal=session_ordinal_after_reply,
                session_turns=session_turn_after_reply,
                pressure=pressure_before,
                pending_hop=pending_hop,
                driver_name=driver_name,
                model=model,
                reasoning_effort=reasoning_effort,
            )
        if post_reply_hop:
            _emit_conversation_hop_queued(
                goal=goal,
                run_id=run_id,
                root=root,
                conversation_id=conv_id,
                hop_goal_id=post_reply_hop["goal_id"],
                completed_at=completed_at,
                reply_message_id=reply_message_id,
                pending_hop=pending_hop,
            )
        elif hop_error:
            _emit_conversation_hop_queue_failed(
                goal=goal,
                run_id=run_id,
                root=root,
                conversation_id=conv_id,
                completed_at=completed_at,
                detail=hop_error,
                reply_message_id=reply_message_id,
                pending_hop=pending_hop,
            )

    stored_session_id = new_session_id
    stored_session_turns = session_turn_after_reply
    stored_session_started_at = session_started_at_after_reply

    messages_after = read_messages(conv_id, _conv_dir=conv_dir)
    summary_after = read_conversation_summary(conv_id, _conv_dir=conv_dir)
    pressure_conv = dict(conv)
    pressure_conv["session_id"] = stored_session_id
    pressure_conv["session_ordinal"] = session_ordinal_after_reply
    pressure_conv["session_turns"] = stored_session_turns
    pressure_after = compute_context_pressure(
        pressure_conv,
        messages_after,
        summary=summary_after,
        provider_usage=cost,
    )
    checkpoint_id = conv.get("last_checkpoint_id")

    update_fields = {
        "session_id": stored_session_id,
        "context_at": completed_at,
        "session_ordinal": session_ordinal_after_reply,
        "session_turns": stored_session_turns,
        "session_started_at": stored_session_started_at,
        "last_turn_mode": turn_mode,
        "last_turn_run_id": run_id,
        "last_pressure": pressure_after,
    }
    if post_reply_hop:
        update_fields["post_reply_hop"] = post_reply_hop
    if not session_id_before:
        update_fields["pending_hop"] = None
    update_conversation(conv_id, _conv_dir=conv_dir, _now=completed_at, **update_fields)

    turn_record = {
        "id": f"turn-{completed_at.replace('-', '').replace(':', '').replace('T', '').replace('Z', '')}",
        "conversation_id": conv_id,
        "run_id": run_id,
        "goal_id": goal["id"],
        "ts": completed_at,
        "status": status,
        "mode": turn_mode,
        "diff_present": bool(diff_text),
        "lineage": {
            "session_ordinal": session_ordinal_after_reply,
            "session_turn": session_turn_after_reply,
            "label": _completed_lineage_label(
                session_ordinal_after_reply or 1,
                session_turn_after_reply,
                checkpoint_id,
            ),
            "checkpoint_id": checkpoint_id,
            "checkpoint_count": conv.get("checkpoint_count", 0),
        },
        "pressure": pressure_after,
        "hop": {
            "requested": bool(pending_hop_before),
            "reason": (pending_hop_before or {}).get("reason"),
            "queued": bool(post_reply_hop),
            "goal_id": post_reply_hop["goal_id"] if post_reply_hop else None,
            "performed": False,
            "checkpoint_id": None,
            "error": hop_error,
            "automatic": bool(post_reply_hop and post_reply_hop.get("automatic")),
        },
        "session_id_before": session_id_before,
        "session_id_after": stored_session_id,
    }
    result = append_conversation_turn(conv_id, turn_record, _conv_dir=conv_dir)
    if not result.ok:
        print(
            f"driver: failed to record conversation turn for {conv_id}: "
            f"{result.reason} — {result.detail}",
            file=sys.stderr,
        )
    _write_json(paths.runs_dir / run_id / "conversation.json", turn_record)

    kwargs: dict = {"cost": cost, "_runs_dir": paths.runs_dir}
    if status != "success":
        kwargs["failure_reason"] = "failure"
    close_run(run_id, status, "converse", **kwargs)


def _build_conversation_prompt(goal: dict, run_id: str, conv: dict,
                                diff_text: str, plant_dir: pathlib.Path,
                                root: pathlib.Path,
                                status_block: str | None = None) -> str:
    """Build a fresh-session prompt for a converse goal."""
    paths = garden_paths(garden_root=root)
    parts = []

    motivation_path = paths.motivation_path
    if motivation_path.exists():
        parts.append(f"# Garden Motivation\n\n{motivation_path.read_text()}")

    memory_file = plant_dir / "memory" / "MEMORY.md"
    if memory_file.exists() and memory_file.read_text().strip():
        parts.append(f"# Your Memory\n\n{memory_file.read_text()}")
        skills_index = _index_dir_md(plant_dir / "skills", "skills")
        if skills_index:
            parts.append(f"# Your Skills\n\n{skills_index}")
        knowledge_index = _index_dir_md(plant_dir / "knowledge", "knowledge")
        if knowledge_index:
            parts.append(f"# Your Knowledge\n\n{knowledge_index}")

    if status_block:
        parts.append(status_block)
    parts.append(_build_conversation_policy_block())

    conv_dir = paths.conversations_dir
    summary = read_conversation_summary(conv["id"], _conv_dir=conv_dir)
    if summary:
        parts.append(
            "# Conversation handoff\n\n"
            "This is a fresh session for an existing conversation. Continue as the "
            "same ongoing exchange and keep the operator-facing continuity natural.\n\n"
            + summary
        )

    if diff_text:
        parts.append(diff_text)

    # Recent conversation history
    messages = read_messages(conv["id"], _conv_dir=conv_dir)
    history_messages = messages[:-1] if messages else []
    if summary:
        history_messages = tail_messages_after(
            history_messages,
            conv.get("compacted_through"),
        )
    if history_messages:
        history = []
        for m in history_messages:
            role = "Operator" if m["sender"] == "operator" else "Garden"
            history.append(f"{role}: {m['content']}")
        heading = "# Recent conversation tail" if summary else "# Conversation history"
        parts.append(heading + "\n\n" + "\n\n".join(history))

    parts.append(f"# New message\n\nOperator: {goal.get('body', '')}")
    parts.append(f"Your working directory is `{root.resolve()}`. Respond to the operator.")
    return "\n\n---\n\n".join(parts)


def _build_prompt(goal: dict, run_id: str, plant_name: str,
                  root: pathlib.Path,
                  dispatch_packet: dict | None = None) -> str:
    """Build the full prompt for this run."""
    paths = garden_paths(garden_root=root)
    run_dir_display = _relative_prompt_path(paths.runs_dir / run_id, root=root).rstrip("/") + "/"
    parts = []

    # Garden-wide motivation
    motivation_path = paths.motivation_path
    if motivation_path.exists():
        parts.append(f"# Garden Motivation\n\n{motivation_path.read_text()}")

    # Plant context: memory + skill/knowledge index (or seed for genesis)
    plant_dir = paths.plants_dir / plant_name
    memory_file = plant_dir / "memory" / "MEMORY.md"

    if memory_file.exists() and memory_file.read_text().strip():
        # Normal run — load memory fully; index skills and knowledge
        parts.append(f"# Your Memory\n\n{memory_file.read_text()}")
        skills_index = _index_dir_md(plant_dir / "skills", "skills")
        if skills_index:
            parts.append(f"# Your Skills\n\n{skills_index}")
        knowledge_index = _index_dir_md(plant_dir / "knowledge", "knowledge")
        if knowledge_index:
            parts.append(f"# Your Knowledge\n\n{knowledge_index}")
    else:
        # Genesis run — load seed
        seed_name = _read_seed_name(plant_dir) or plant_name
        seed_path = paths.seeds_dir / f"{seed_name}.md"
        if seed_path.exists():
            parts.append(seed_path.read_text())

    # Run context
    parts.append(
        f"# Your Current Run\n\n"
        f"Run ID: `{run_id}`\n"
        f"Goal ID: `{goal['id']}`\n"
        f"Goal type: `{goal.get('type', 'unknown')}`\n"
        f"Goal priority: `{goal.get('priority', 5)}`\n"
        f"Plant: `{goal.get('assigned_to', plant_name)}`\n"
        f"Run directory: `{run_dir_display}`\n"
    )

    tend_context = _build_tend_context(goal)
    if tend_context:
        parts.append(tend_context)

    plant_commission_context = render_plant_commission_context(
        goal.get("plant_commission")
    )
    if plant_commission_context:
        parts.append(plant_commission_context)

    # Task
    task_body = goal.get("body", "")
    if dispatch_packet and dispatch_packet.get("goal_body") is not None:
        task_body = dispatch_packet.get("goal_body", "")
    parts.append(f"# Task\n\n{task_body}")

    supplement_section = _build_pre_dispatch_supplements_section(dispatch_packet)
    if supplement_section:
        parts.append(supplement_section)

    parts.append(f"Your working directory is `{root.resolve()}`. Begin.")

    return "\n\n---\n\n".join(parts)


def _build_tend_context(goal: dict) -> str:
    if goal.get("type") != "tend":
        return ""

    metadata = tend_metadata(goal)
    origin = goal.get("origin")
    origin = origin if isinstance(origin, dict) else {}
    submitted_from = goal.get("submitted_from")
    submitted_from = submitted_from if isinstance(submitted_from, dict) else {}

    lines = ["# Tend Context", ""]
    trigger_kinds = metadata.get("trigger_kinds") or []
    if trigger_kinds:
        lines.append(f"Trigger kinds: {', '.join(trigger_kinds)}")
    trigger_goal = metadata.get("trigger_goal")
    if trigger_goal:
        lines.append(f"Trigger goal: {trigger_goal}")
    trigger_run = metadata.get("trigger_run")
    if trigger_run:
        lines.append(f"Trigger run: {trigger_run}")
    if origin.get("kind") == "conversation" and origin.get("conversation_id"):
        lines.append(f"Conversation origin: {origin['conversation_id']}")
    source_run_id = submitted_from.get("run_id")
    if source_run_id and source_run_id != trigger_run:
        lines.append(f"Submitted from run: {source_run_id}")
    if "post_genesis" in trigger_kinds:
        lines.append(
            "Operator-facing framing: bounded environment orientation of the local garden."
        )
    lines.extend([
        "",
        "Use this metadata when explaining why the tend exists. Do not widen it into a broader autonomy loop.",
        "When the trigger is `post_genesis`, keep the underlying runtime primitive as `tend`,",
        "but describe the work to the operator as environment orientation rather than opaque maintenance.",
        "If the survey identifies a clear, already authorized, non-speculative next step,",
        "submit one bounded follow-up goal during this run rather than only reporting it.",
        "If no such step exists, say that explicitly instead of inventing speculative work.",
    ])
    return "\n".join(lines)


def _index_dir_md(directory: pathlib.Path, label: str) -> str:
    """
    Build an index of .md files in a directory.
    Lists each file with its first heading as a description.
    The agent reads individual files as needed rather than receiving all content.
    """
    if not directory.exists():
        return ""
    entries = []
    for p in sorted(directory.iterdir()):
        if p.suffix == ".md" and p.is_file():
            content = p.read_text().strip()
            if not content:
                continue
            heading = next(
                (line.lstrip("#").strip() for line in content.splitlines()
                 if line.startswith("#")),
                p.stem,
            )
            entries.append(f"- `{p.name}` — {heading}")
    if not entries:
        return ""
    lines = [f"Available {label} files (read the ones relevant to your task):"]
    lines.extend(entries)
    return "\n".join(lines)


def _read_seed_name(plant_dir: pathlib.Path) -> str | None:
    """Read the seed reference file if present."""
    seed_file = plant_dir / "seed"
    if seed_file.exists() and seed_file.is_file():
        return seed_file.read_text().strip() or None
    return None


def _file_digest(path: pathlib.Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture_tend_snapshot(goal: dict, root: pathlib.Path) -> dict:
    paths = garden_paths(garden_root=root)
    plant_name = goal.get("assigned_to", "gardener")
    memory_path = paths.plants_dir / plant_name / "memory" / "MEMORY.md"
    return {
        "memory_path": memory_path,
        "memory_digest": _file_digest(memory_path),
    }


def _follow_up_goals_for_run(run_id: str, root: pathlib.Path) -> list[str]:
    paths = garden_paths(garden_root=root)
    follow_up_goals = []
    for goal in list_goals(_goals_dir=paths.goals_dir):
        submitted_from = goal.get("submitted_from")
        if not isinstance(submitted_from, dict):
            continue
        if submitted_from.get("run_id") == run_id:
            follow_up_goals.append(goal["id"])
    return follow_up_goals


def _emit_tend_started(goal: dict, run_id: str, root: pathlib.Path, *, now: str) -> None:
    paths = garden_paths(garden_root=root)
    append_event({
        "ts": now,
        "type": "TendStarted",
        "actor": "system",
        "goal": goal["id"],
        "run": run_id,
        **_goal_event_metadata(goal),
    }, path=paths.coordinator_events_path)


def _emit_tend_finished(goal: dict, run_id: str, root: pathlib.Path, *,
                        now: str, snapshot: dict | None) -> None:
    paths = garden_paths(garden_root=root)
    follow_up_goals = _follow_up_goals_for_run(run_id, root)
    operator_note_path = None
    operator_note_written = False
    memory_updated = False

    if snapshot:
        memory_path = snapshot.get("memory_path")
        if isinstance(memory_path, pathlib.Path):
            memory_updated = _file_digest(memory_path) != snapshot.get("memory_digest")

        note_records = [
            record
            for record in read_operator_message_records(run_id, _garden_root=root)
            if record.get("kind") == OPERATOR_MESSAGE_KIND_TEND_SURVEY
        ]
        operator_note_written = bool(note_records)
        if note_records:
            operator_note_path = note_records[-1].get("delivery_path")

    event = {
        "ts": now,
        "type": "TendFinished",
        "actor": "system",
        "goal": goal["id"],
        "run": run_id,
        "follow_up_goal_count": len(follow_up_goals),
        "memory_updated": memory_updated,
        "operator_note_written": operator_note_written,
        **_goal_event_metadata(goal),
    }
    if follow_up_goals:
        event["follow_up_goals"] = follow_up_goals
    if operator_note_path:
        event["operator_note_path"] = operator_note_path
    append_event(event, path=paths.coordinator_events_path)


def _launch(model: str, prompt: str, events_path: pathlib.Path,
            timeout: int | None = None,
            session_id: str | None = None,
            driver_name: str = "claude",
            cwd: pathlib.Path | None = None,
            reasoning_effort: str | None = None,
            env: dict | None = None) -> int:
    """
    Launch the backend CLI subprocess. Writes stdout to events_path.
    Stderr is forwarded to this process's stderr for visibility.
    Returns the process exit code.

    timeout: seconds before the subprocess is killed (None = no limit).
    Note: the coordinator's watchdog handles run records independently;
    this timeout handles the actual subprocess.
    """
    try:
        plugin = get_driver_plugin(driver_name)
        command = plugin.build_launch_command(
            model=model,
            events_path=events_path,
            cwd=cwd,
            session_id=session_id,
            reasoning_effort=reasoning_effort,
        )
        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)
        with open(events_path, "w", encoding="utf-8") as events_fh:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=events_fh,
                stderr=sys.stderr,
                text=True,
                start_new_session=True,  # own process group → clean killpg on shutdown
                cwd=str(cwd) if cwd else None,
                env=proc_env,
            )
        with _active_procs_lock:
            _active_procs.add(proc)
        try:
            _, _ = proc.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
            events_path.write_text(
                json.dumps({"type": "error", "error": "subprocess timeout"}) + "\n",
                encoding="utf-8",
            )
            return 1
        finally:
            with _active_procs_lock:
                _active_procs.discard(proc)
        return proc.returncode
    except FileNotFoundError:
        events_path.write_text(
            json.dumps({"type": "error", "error": f"{driver_name} CLI not found"}) + "\n",
            encoding="utf-8",
        )
        return 1
    except KeyError as exc:
        events_path.write_text(
            json.dumps({"type": "error", "error": str(exc)}) + "\n",
            encoding="utf-8",
        )
        return 1
    except Exception as exc:
        events_path.write_text(
            json.dumps({"type": "error", "error": str(exc)}) + "\n",
            encoding="utf-8",
        )
        return 1


def _finalize(run_id: str, goal: dict, returncode: int,
              events_path: pathlib.Path, root: pathlib.Path,
              driver_name: str = "claude",
              tend_snapshot: dict | None = None) -> None:
    """
    Read events.jsonl, extract cost and status, solicit reflection if needed,
    then call close_run().
    """
    cost, status = _parse_events(events_path, returncode, driver_name=driver_name)
    goal_type = goal.get("type", "spike")
    model = resolve_model_name(goal, driver_name=driver_name, garden_root=root)
    reasoning_effort = resolve_reasoning_effort(goal, garden_root=root)

    kwargs: dict = {"cost": cost}
    if status != "success":
        # _parse_events returns "success" or "failure"; "killed"/"timeout"/
        # "zero_output" are set by the coordinator watchdog, not the driver.
        kwargs["failure_reason"] = "failure"
    elif goal_type in REFLECTION_REQUIRED_TYPES:
        _set_run_lifecycle(run_id, "writing-reflection", root=root)
        try:
            reflection = _solicit_reflection(
                model, run_id, events_path, driver_name=driver_name, cwd=root,
                reasoning_effort=reasoning_effort
            )
        finally:
            _set_run_lifecycle(run_id, None, root=root)
        if reflection:
            kwargs["reflection"] = reflection
        else:
            print(
                f"driver: reflection solicitation returned nothing for {run_id} "
                f"— close_run will reject with MISSING_REFLECTION",
                file=sys.stderr,
            )

    completed_at = _now_utc()
    if goal_type == "tend" and not (status == "success" and "reflection" not in kwargs):
        _emit_tend_finished(
            goal,
            run_id,
            root,
            now=completed_at,
            snapshot=tend_snapshot,
        )

    result = close_run(
        run_id,
        status,
        goal_type,
        _runs_dir=garden_paths(garden_root=root).runs_dir,
        completed_at=completed_at,
        event_data=_goal_event_metadata(goal),
        **kwargs,
    )
    if not result.ok:
        print(
            f"driver: close_run failed for {run_id}: {result.reason} — {result.detail}",
            file=sys.stderr,
        )


def _parse_events(events_path: pathlib.Path, returncode: int,
                  driver_name: str = "claude") -> tuple:
    """
    Parse the events stream. Returns (cost, status).
    Delegates backend-specific interpretation to the driver plugin.
    """
    try:
        plugin = get_driver_plugin(driver_name)
    except KeyError:
        return {"source": "unknown"}, "failure"
    return plugin.parse_events(events_path, returncode)


def _parse_session_id(events_path: pathlib.Path,
                      driver_name: str = "claude") -> str | None:
    """Extract backend session/thread id from events.jsonl."""
    try:
        plugin = get_driver_plugin(driver_name)
    except KeyError:
        return None
    return plugin.parse_session_id(events_path)


def _solicit_reflection(model: str, run_id: str, events_path: pathlib.Path,
                        timeout: int | None = None,
                        driver_name: str = "claude",
                        cwd: pathlib.Path | None = None,
                        reasoning_effort: str | None = None) -> str | None:
    """
    Continue the backend session to solicit a reflection.
    Writes output to reflection.jsonl in the run directory.
    Returns the reflection text, or None if solicitation failed.
    """
    session_id = _parse_session_id(events_path, driver_name=driver_name)
    if not session_id:
        print(
            f"driver: cannot solicit reflection for {run_id}: no session_id in events",
            file=sys.stderr,
        )
        return None

    reflection_path = events_path.parent / "reflection.jsonl"
    try:
        plugin = get_driver_plugin(driver_name)
        command = plugin.build_reflection_command(
            model=model,
            reflection_path=reflection_path,
            session_id=session_id,
            cwd=cwd,
            reasoning_effort=reasoning_effort,
        )
        with open(reflection_path, "w", encoding="utf-8") as fh:
            proc = subprocess.run(
                command,
                input=_REFLECTION_PROMPT,
                stdout=fh,
                stderr=sys.stderr,
                text=True,
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
            )
        if proc.returncode != 0:
            print(
                f"driver: reflection solicitation exited {proc.returncode} for {run_id}",
                file=sys.stderr,
            )
            return None
    except subprocess.TimeoutExpired:
        print(f"driver: reflection solicitation timed out for {run_id}", file=sys.stderr)
        return None
    except FileNotFoundError:
        print(f"driver: {driver_name} CLI not found during reflection solicitation for {run_id}",
              file=sys.stderr)
        return None
    except KeyError as exc:
        print(f"driver: reflection solicitation failed for {run_id}: {exc}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"driver: reflection solicitation failed for {run_id}: {exc}", file=sys.stderr)
        return None

    return _extract_last_text(reflection_path, driver_name=driver_name)


def _extract_last_text(path: pathlib.Path,
                       driver_name: str = "claude") -> str | None:
    """Extract the backend's last text response from run artifacts."""
    try:
        plugin = get_driver_plugin(driver_name)
    except KeyError:
        return None
    return plugin.extract_last_text(path)
