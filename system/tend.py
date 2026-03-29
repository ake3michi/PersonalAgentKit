"""
Helpers for bounded tend goals and their observability metadata.
"""

from __future__ import annotations

TEND_TRIGGER_POST_GENESIS = "post_genesis"
TEND_TRIGGER_OPERATOR_REQUEST = "operator_request"
TEND_TRIGGER_RUN_FAILURE = "run_failure"
TEND_TRIGGER_QUEUED_ATTENTION = "queued_attention_needed"

TEND_TRIGGER_KINDS = frozenset({
    TEND_TRIGGER_POST_GENESIS,
    TEND_TRIGGER_OPERATOR_REQUEST,
    TEND_TRIGGER_RUN_FAILURE,
    TEND_TRIGGER_QUEUED_ATTENTION,
})

TEND_PRIORITY_OPERATOR_REQUEST = 6
TEND_PRIORITY_BACKGROUND = 4


def normalize_tend_trigger_kinds(value) -> list[str]:
    """Return a de-duplicated ordered trigger list."""
    if value is None:
        return []
    if isinstance(value, (set, frozenset)):
        items = sorted(value)
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        kind = str(item).strip()
        if not kind or kind in seen:
            continue
        seen.add(kind)
        normalized.append(kind)
    return normalized


def default_tend_priority(trigger_kinds) -> int:
    normalized = normalize_tend_trigger_kinds(trigger_kinds)
    if TEND_TRIGGER_OPERATOR_REQUEST in normalized:
        return TEND_PRIORITY_OPERATOR_REQUEST
    return TEND_PRIORITY_BACKGROUND


def tend_metadata(goal: dict) -> dict:
    """
    Return normalized tend metadata, inferring the smallest safe defaults for
    legacy tend goals that predate explicit trigger metadata.
    """
    if goal.get("type") != "tend":
        return {}

    raw = goal.get("tend")
    explicit = raw if isinstance(raw, dict) else {}

    metadata: dict = {}
    trigger_kinds = normalize_tend_trigger_kinds(explicit.get("trigger_kinds"))
    if not trigger_kinds:
        trigger_kinds = _infer_trigger_kinds(goal)
    if trigger_kinds:
        metadata["trigger_kinds"] = trigger_kinds

    trigger_goal = explicit.get("trigger_goal")
    if trigger_goal is None:
        trigger_goal = _submitted_from(goal).get("goal_id")
    if trigger_goal:
        metadata["trigger_goal"] = str(trigger_goal)

    trigger_run = explicit.get("trigger_run")
    if trigger_run is None:
        trigger_run = _submitted_from(goal).get("run_id")
    if trigger_run:
        metadata["trigger_run"] = str(trigger_run)

    return metadata


def tend_event_metadata(goal: dict) -> dict:
    return dict(tend_metadata(goal))


def _infer_trigger_kinds(goal: dict) -> list[str]:
    origin = goal.get("origin")
    if isinstance(origin, dict) and origin.get("kind") == "conversation":
        return [TEND_TRIGGER_OPERATOR_REQUEST]

    body = str(goal.get("body") or "").lower()
    if "post-genesis" in body:
        return [TEND_TRIGGER_POST_GENESIS]

    return [TEND_TRIGGER_OPERATOR_REQUEST]


def _submitted_from(goal: dict) -> dict:
    source = goal.get("submitted_from")
    return source if isinstance(source, dict) else {}


__all__ = [
    "TEND_TRIGGER_KINDS",
    "TEND_TRIGGER_OPERATOR_REQUEST",
    "TEND_TRIGGER_POST_GENESIS",
    "TEND_TRIGGER_QUEUED_ATTENTION",
    "TEND_TRIGGER_RUN_FAILURE",
    "TEND_PRIORITY_BACKGROUND",
    "TEND_PRIORITY_OPERATOR_REQUEST",
    "default_tend_priority",
    "normalize_tend_trigger_kinds",
    "tend_event_metadata",
    "tend_metadata",
]
