"""
Helpers for the dedicated later-plant commissioning goal surface.
"""

from __future__ import annotations

PLANT_COMMISSION_GOAL_SUBTYPE = "plant_commission"
PLANT_COMMISSION_GOAL_TYPE = "build"
PLANT_COMMISSION_ASSIGNED_TO = "gardener"
PLANT_COMMISSION_INITIAL_GOAL_TYPES = frozenset({
    "build",
    "fix",
    "spike",
    "evaluate",
    "research",
})


def build_plant_commission_payload(*,
                                   plant_name: str,
                                   seed: str,
                                   initial_goal_type: str,
                                   initial_goal_body: str,
                                   initial_goal_priority: int | None = None,
                                   initial_goal_driver: str | None = None,
                                   initial_goal_model: str | None = None,
                                   initial_goal_reasoning_effort: str | None = None) -> dict:
    initial_goal = {
        "type": initial_goal_type,
        "body": initial_goal_body,
    }
    if initial_goal_priority is not None:
        initial_goal["priority"] = initial_goal_priority
    if initial_goal_driver:
        initial_goal["driver"] = initial_goal_driver
    if initial_goal_model:
        initial_goal["model"] = initial_goal_model
    if initial_goal_reasoning_effort:
        initial_goal["reasoning_effort"] = initial_goal_reasoning_effort

    return {
        "plant_name": plant_name,
        "seed": seed,
        "initial_goal": initial_goal,
    }


def render_plant_commission_body(payload: dict) -> str:
    initial_goal = payload["initial_goal"]
    lines = [
        (
            f"Commission later plant `{payload['plant_name']}` from seed "
            f"`{payload['seed']}` through the dedicated commissioning goal surface."
        ),
        "",
        "Plant commissioning contract:",
        f"- `plant_commission.plant_name`: `{payload['plant_name']}`",
        f"- `plant_commission.seed`: `{payload['seed']}`",
        f"- `plant_commission.initial_goal.type`: `{initial_goal['type']}`",
        (
            f"- `plant_commission.initial_goal.body`: "
            f"`{_single_line(initial_goal['body'])}`"
        ),
        (
            "- Execute this contract with "
            "`system.plants.execute_plant_commission()` so the plant record, "
            "commission event, and first-goal handoff share one run-local timestamp."
        ),
        (
            "- The first goal submitted from this run should automatically depend "
            "on this commissioning goal closing."
        ),
        "- Do not hardcode `_now` outside tests.",
    ]
    if initial_goal.get("priority") is not None:
        lines.append(
            f"- `plant_commission.initial_goal.priority`: `{initial_goal['priority']}`"
        )
    if initial_goal.get("driver"):
        lines.append(
            f"- `plant_commission.initial_goal.driver`: `{initial_goal['driver']}`"
        )
    if initial_goal.get("model"):
        lines.append(
            f"- `plant_commission.initial_goal.model`: `{initial_goal['model']}`"
        )
    if initial_goal.get("reasoning_effort"):
        lines.append(
            "- `plant_commission.initial_goal.reasoning_effort`: "
            f"`{initial_goal['reasoning_effort']}`"
        )
    return "\n".join(lines)


def render_plant_commission_context(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""

    initial_goal = payload.get("initial_goal")
    if not isinstance(initial_goal, dict):
        return ""

    lines = [
        "# Plant Commission Context",
        "",
        f"Plant name: {payload.get('plant_name', '')}",
        f"Seed: {payload.get('seed', '')}",
        f"Initial goal type: {initial_goal.get('type', '')}",
        f"Initial goal body: {_single_line(initial_goal.get('body', ''))}",
        "Use `system.plants.execute_plant_commission()` to apply this contract.",
        "That helper keeps the plant record, PlantCommissioned event, and first-goal handoff on one run-local timestamp.",
        "The first goal it submits should depend on this commissioning goal.",
    ]
    if initial_goal.get("priority") is not None:
        lines.append(f"Initial goal priority: {initial_goal['priority']}")
    if initial_goal.get("driver"):
        lines.append(f"Initial goal driver: {initial_goal['driver']}")
    if initial_goal.get("model"):
        lines.append(f"Initial goal model: {initial_goal['model']}")
    if initial_goal.get("reasoning_effort"):
        lines.append(
            f"Initial goal reasoning effort: {initial_goal['reasoning_effort']}"
        )
    return "\n".join(lines)


def plant_commission_payload(goal: dict) -> dict | None:
    payload = goal.get("plant_commission")
    return payload if isinstance(payload, dict) else None


def _single_line(text: str) -> str:
    return " ".join(str(text).split())


__all__ = [
    "PLANT_COMMISSION_ASSIGNED_TO",
    "PLANT_COMMISSION_GOAL_SUBTYPE",
    "PLANT_COMMISSION_GOAL_TYPE",
    "PLANT_COMMISSION_INITIAL_GOAL_TYPES",
    "build_plant_commission_payload",
    "plant_commission_payload",
    "render_plant_commission_body",
    "render_plant_commission_context",
]
