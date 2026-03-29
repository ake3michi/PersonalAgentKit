"""
Public submission API. This is the surface named in the agent contract.

Agents call `submit_goal()` to submit goals and `append_goal_supplement()` to
attach pre-dispatch conversation clarifications to queued durable goals.
"""

import datetime
import os
import pathlib

from .garden import garden_paths
from .validate import ValidationResult, validate_goal
from .goals import list_goals
from .goals import append_goal_supplement as _append_goal_supplement
from .goals import (
    _events_path_for_goal_write,
    _next_id_in,
    _plants_dir_for_goals_dir,
    _slugify,
    _write_goal_record,
)
from .goals import submit_goal as _submit_goal
from .plant_commission import (
    PLANT_COMMISSION_ASSIGNED_TO,
    PLANT_COMMISSION_GOAL_TYPE,
    build_plant_commission_payload,
    render_plant_commission_body,
)
from .retrospective import (
    DEFAULT_RETROSPECTIVE_RECENT_RUN_LIMIT,
    build_retrospective_payload,
    render_retrospective_body,
)
from .tend import TEND_TRIGGER_KINDS, default_tend_priority, normalize_tend_trigger_kinds

_ENV_GARDEN_ROOT = "PAK2_GARDEN_ROOT"
_ENV_CURRENT_GOAL_TYPE = "PAK2_CURRENT_GOAL_TYPE"
_ENV_CURRENT_GOAL_ID = "PAK2_CURRENT_GOAL_ID"
_ENV_CURRENT_RUN_ID = "PAK2_CURRENT_RUN_ID"
_ENV_CURRENT_PLANT = "PAK2_CURRENT_PLANT"
_ENV_CURRENT_CONVERSATION_ID = "PAK2_CURRENT_CONVERSATION_ID"
_ENV_CURRENT_CONVERSATION_MESSAGE_ID = "PAK2_CURRENT_CONVERSATION_MESSAGE_ID"
_ENV_CURRENT_GOALS_DIR = "PAK2_CURRENT_GOALS_DIR"
_SAME_INITIATIVE_CODE_CHANGE_TYPES = frozenset({"build", "fix"})


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_goals_dir(explicit: pathlib.Path | None) -> pathlib.Path | None:
    if explicit is not None:
        return explicit
    current_goals_dir = os.getenv(_ENV_CURRENT_GOALS_DIR, "").strip()
    if current_goals_dir:
        return pathlib.Path(current_goals_dir)
    garden_root = os.getenv(_ENV_GARDEN_ROOT)
    if not garden_root:
        return None
    return garden_paths(garden_root=pathlib.Path(garden_root)).goals_dir


def _conversation_origin_from_env(now: str) -> dict | None:
    if os.getenv(_ENV_CURRENT_GOAL_TYPE) != "converse":
        return None
    conversation_id = os.getenv(_ENV_CURRENT_CONVERSATION_ID)
    if not conversation_id:
        return None
    origin = {
        "kind": "conversation",
        "conversation_id": conversation_id,
        "ts": now,
    }
    message_id = os.getenv(_ENV_CURRENT_CONVERSATION_MESSAGE_ID)
    if message_id:
        origin["message_id"] = message_id
    return origin


def _submitted_from_env(now: str) -> dict | None:
    goal_id = os.getenv(_ENV_CURRENT_GOAL_ID)
    run_id = os.getenv(_ENV_CURRENT_RUN_ID)
    plant = os.getenv(_ENV_CURRENT_PLANT)
    goal_type = os.getenv(_ENV_CURRENT_GOAL_TYPE)
    if not goal_id or not run_id:
        return None

    source = {
        "goal_id": goal_id,
        "run_id": run_id,
        "ts": now,
    }
    if plant:
        source["plant"] = plant
    if goal_type:
        source["goal_type"] = goal_type
    return source


def _apply_submission_context(data: dict, *, now: str) -> dict:
    payload = dict(data)
    submitted_from = _submitted_from_env(now)
    if submitted_from and not isinstance(payload.get("submitted_from"), dict):
        payload["submitted_from"] = submitted_from
    origin = _conversation_origin_from_env(now)
    if (
        origin
        and payload.get("type") != "converse"
        and "conversation_id" not in payload
    ):
        if not isinstance(payload.get("origin"), dict):
            payload["origin"] = origin
        updates = payload.get("pre_dispatch_updates")
        if not isinstance(updates, dict):
            payload["pre_dispatch_updates"] = {"policy": "supplement"}
        elif "policy" not in updates:
            payload["pre_dispatch_updates"] = {
                **updates,
                "policy": "supplement",
            }
    return payload


def submit_goal(data: dict, *, _goals_dir: pathlib.Path | None = None,
                _now: str | None = None):
    """
    Submit a goal, auto-tagging durable work that originates from a converse run.
    """
    now = _now or _now_utc()
    payload = _apply_submission_context(data, now=now)
    return _submit_goal(
        payload,
        _goals_dir=_default_goals_dir(_goals_dir),
        _now=now,
    )


def submit_same_initiative_code_change_with_evaluate(*,
                                                     submitted_by: str,
                                                     implementation_goal_type: str,
                                                     implementation_body: str,
                                                     evaluate_body: str,
                                                     implementation_assigned_to: str | None = "gardener",
                                                     evaluate_assigned_to: str | None = "gardener",
                                                     implementation_depends_on: list[str] | None = None,
                                                     implementation_priority: int | None = None,
                                                     evaluate_priority: int | None = None,
                                                     driver: str | None = None,
                                                     model: str | None = None,
                                                     reasoning_effort: str | None = None,
                                                     evaluate_driver: str | None = None,
                                                     evaluate_model: str | None = None,
                                                     evaluate_reasoning_effort: str | None = None,
                                                     _goals_dir: pathlib.Path | None = None,
                                                     _now: str | None = None):
    """
    Submit one code-changing same-initiative tranche goal together with its
    mandatory follow-on evaluate stop.
    """
    goal_type = implementation_goal_type.strip() if isinstance(implementation_goal_type, str) else ""
    if goal_type not in _SAME_INITIATIVE_CODE_CHANGE_TYPES:
        return ValidationResult.reject(
            "INVALID_SAME_INITIATIVE_IMPLEMENTATION_GOAL_TYPE",
            "paired same-initiative submission only supports `build` or `fix` implementation goals",
        ), None

    now = _now or _now_utc()
    goals_dir = _default_goals_dir(_goals_dir)
    if goals_dir is None:
        goals_dir = garden_paths().goals_dir
    events_path = _events_path_for_goal_write(goals_dir, None)
    plants_dir = _plants_dir_for_goals_dir(goals_dir)

    implementation_payload = {
        "type": goal_type,
        "submitted_by": submitted_by,
        "body": implementation_body,
    }
    if implementation_assigned_to is not None:
        implementation_payload["assigned_to"] = implementation_assigned_to
    if implementation_depends_on:
        implementation_payload["depends_on"] = list(implementation_depends_on)
    if implementation_priority is not None:
        implementation_payload["priority"] = implementation_priority
    if driver:
        implementation_payload["driver"] = driver
    if model:
        implementation_payload["model"] = model
    if reasoning_effort:
        implementation_payload["reasoning_effort"] = reasoning_effort
    implementation_payload = _apply_submission_context(implementation_payload, now=now)

    next_id = _next_id_in(goals_dir)
    implementation_goal_id = (
        f"{next_id}-{_slugify(implementation_payload.get('body', 'goal')) or 'goal'}"
    )
    implementation_record = {
        "id": implementation_goal_id,
        "status": "queued",
        "submitted_at": now,
        **implementation_payload,
    }
    result = validate_goal(implementation_record, _plants_dir=plants_dir)
    if not result.ok:
        return result, None

    evaluate_payload = {
        "type": "evaluate",
        "submitted_by": submitted_by,
        "depends_on": [implementation_goal_id],
        "body": evaluate_body,
    }
    if evaluate_assigned_to is not None:
        evaluate_payload["assigned_to"] = evaluate_assigned_to
    if evaluate_priority is not None:
        evaluate_payload["priority"] = evaluate_priority
    elif implementation_priority is not None:
        evaluate_payload["priority"] = implementation_priority

    final_evaluate_driver = evaluate_driver if evaluate_driver is not None else driver
    final_evaluate_model = evaluate_model if evaluate_model is not None else model
    final_evaluate_reasoning = (
        evaluate_reasoning_effort
        if evaluate_reasoning_effort is not None
        else reasoning_effort
    )
    if final_evaluate_driver:
        evaluate_payload["driver"] = final_evaluate_driver
    if final_evaluate_model:
        evaluate_payload["model"] = final_evaluate_model
    if final_evaluate_reasoning:
        evaluate_payload["reasoning_effort"] = final_evaluate_reasoning
    evaluate_payload = _apply_submission_context(evaluate_payload, now=now)

    evaluate_goal_id = (
        f"{next_id + 1}-{_slugify(evaluate_payload.get('body', 'goal')) or 'goal'}"
    )
    evaluate_record = {
        "id": evaluate_goal_id,
        "status": "queued",
        "submitted_at": now,
        **evaluate_payload,
    }

    # Validate both records before writing either goal so the pair stays bounded.
    result = validate_goal(evaluate_record, _plants_dir=plants_dir)
    if not result.ok:
        return result, None

    _write_goal_record(
        implementation_record,
        goals_dir=goals_dir,
        events_path=events_path,
        now=now,
    )
    _write_goal_record(
        evaluate_record,
        goals_dir=goals_dir,
        events_path=events_path,
        now=now,
    )

    return ValidationResult.accept(), {
        "implementation_goal_id": implementation_goal_id,
        "evaluate_goal_id": evaluate_goal_id,
    }


def submit_tend_goal(*,
                     body: str,
                     submitted_by: str,
                     trigger_kinds,
                     assigned_to: str | None = "gardener",
                     priority: int | None = None,
                     trigger_goal: str | None = None,
                     trigger_run: str | None = None,
                     depends_on: list[str] | None = None,
                     driver: str | None = None,
                     model: str | None = None,
                     reasoning_effort: str | None = None,
                     _goals_dir: pathlib.Path | None = None,
                     _now: str | None = None):
    """
    Submit a tend goal with bounded-rollout metadata and active-tend dedupe.
    """
    now = _now or _now_utc()
    normalized_kinds = normalize_tend_trigger_kinds(trigger_kinds)
    if not normalized_kinds:
        return ValidationResult.reject(
            "INVALID_TEND_TRIGGER_KIND",
            "trigger_kinds must include at least one tend trigger",
        ), None
    for kind in normalized_kinds:
        if kind not in TEND_TRIGGER_KINDS:
            return ValidationResult.reject(
                "INVALID_TEND_TRIGGER_KIND",
                f"unknown tend trigger kind: {kind!r}",
            ), None

    goals_dir = _default_goals_dir(_goals_dir)
    for goal in list_goals(_goals_dir=goals_dir):
        if goal.get("type") == "tend" and goal.get("status") in {"queued", "running"}:
            return ValidationResult.accept(), goal["id"]

    payload = {
        "type": "tend",
        "submitted_by": submitted_by,
        "body": body,
        "priority": priority if priority is not None else default_tend_priority(normalized_kinds),
        "tend": {
            "trigger_kinds": normalized_kinds,
        },
    }
    if assigned_to is not None:
        payload["assigned_to"] = assigned_to
    if depends_on:
        payload["depends_on"] = list(depends_on)
    if driver:
        payload["driver"] = driver
    if model:
        payload["model"] = model
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort

    env_goal = os.getenv(_ENV_CURRENT_GOAL_ID)
    env_run = os.getenv(_ENV_CURRENT_RUN_ID)
    if trigger_goal or env_goal:
        payload["tend"]["trigger_goal"] = trigger_goal or env_goal
    if trigger_run or env_run:
        payload["tend"]["trigger_run"] = trigger_run or env_run

    return submit_goal(payload, _goals_dir=goals_dir, _now=now)


def submit_plant_commission_goal(*,
                                 submitted_by: str,
                                 plant_name: str,
                                 seed: str,
                                 initial_goal_type: str,
                                 initial_goal_body: str,
                                 initial_goal_priority: int | None = None,
                                 initial_goal_driver: str | None = None,
                                 initial_goal_model: str | None = None,
                                 initial_goal_reasoning_effort: str | None = None,
                                 priority: int | None = None,
                                 driver: str | None = None,
                                 model: str | None = None,
                                 reasoning_effort: str | None = None,
                                 _goals_dir: pathlib.Path | None = None,
                                 _now: str | None = None):
    """
    Submit a dedicated gardener build goal for later-plant commissioning.
    """
    payload = build_plant_commission_payload(
        plant_name=plant_name,
        seed=seed,
        initial_goal_type=initial_goal_type,
        initial_goal_body=initial_goal_body,
        initial_goal_priority=initial_goal_priority,
        initial_goal_driver=initial_goal_driver,
        initial_goal_model=initial_goal_model,
        initial_goal_reasoning_effort=initial_goal_reasoning_effort,
    )
    goal = {
        "type": PLANT_COMMISSION_GOAL_TYPE,
        "submitted_by": submitted_by,
        "assigned_to": PLANT_COMMISSION_ASSIGNED_TO,
        "body": render_plant_commission_body(payload),
        "plant_commission": payload,
    }
    if priority is not None:
        goal["priority"] = priority
    if driver:
        goal["driver"] = driver
    if model:
        goal["model"] = model
    if reasoning_effort:
        goal["reasoning_effort"] = reasoning_effort
    return submit_goal(
        goal,
        _goals_dir=_default_goals_dir(_goals_dir),
        _now=_now or _now_utc(),
    )


def submit_retrospective_goal(*,
                              submitted_by: str,
                              recent_run_limit: int = DEFAULT_RETROSPECTIVE_RECENT_RUN_LIMIT,
                              allow_follow_up_goal: bool = False,
                              priority: int | None = None,
                              driver: str | None = None,
                              model: str | None = None,
                              reasoning_effort: str | None = None,
                              _goals_dir: pathlib.Path | None = None,
                              _now: str | None = None):
    """
    Submit a gardener evaluate goal for the manual retrospective protocol.
    """
    payload = build_retrospective_payload(
        recent_run_limit=recent_run_limit,
        allow_follow_up_goal=allow_follow_up_goal,
    )
    goal = {
        "type": "evaluate",
        "submitted_by": submitted_by,
        "assigned_to": "gardener",
        "body": render_retrospective_body(payload),
        "retrospective": payload,
    }
    if priority is not None:
        goal["priority"] = priority
    if driver:
        goal["driver"] = driver
    if model:
        goal["model"] = model
    if reasoning_effort:
        goal["reasoning_effort"] = reasoning_effort

    return submit_goal(
        goal,
        _goals_dir=_default_goals_dir(_goals_dir),
        _now=_now or _now_utc(),
    )


def append_goal_supplement(goal_id: str, data: dict, *,
                           _goals_dir: pathlib.Path | None = None,
                           _now: str | None = None):
    """
    Append a pre-dispatch supplement, defaulting actor/source from the current
    converse run when available.
    """
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "supplement payload must be a JSON object",
        ), None
    payload = dict(data)
    if "actor" not in payload:
        actor = os.getenv(_ENV_CURRENT_PLANT)
        if actor:
            payload["actor"] = actor
    if "source" not in payload:
        conversation_id = os.getenv(_ENV_CURRENT_CONVERSATION_ID)
        if conversation_id:
            source = {
                "kind": "conversation",
                "conversation_id": conversation_id,
            }
            message_id = os.getenv(_ENV_CURRENT_CONVERSATION_MESSAGE_ID)
            if message_id:
                source["message_id"] = message_id
            payload["source"] = source
    if "source_goal_id" not in payload:
        goal_ref = os.getenv(_ENV_CURRENT_GOAL_ID)
        if goal_ref:
            payload["source_goal_id"] = goal_ref
    if "source_run_id" not in payload:
        run_ref = os.getenv(_ENV_CURRENT_RUN_ID)
        if run_ref:
            payload["source_run_id"] = run_ref
    return _append_goal_supplement(
        goal_id,
        payload,
        _goals_dir=_default_goals_dir(_goals_dir),
        _now=_now or _now_utc(),
    )


__all__ = [
    "append_goal_supplement",
    "submit_goal",
    "submit_plant_commission_goal",
    "submit_retrospective_goal",
    "submit_tend_goal",
]
