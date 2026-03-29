"""
Helpers for manual retrospective goal submission.
"""

from __future__ import annotations

RETROSPECTIVE_WINDOW_SINCE_LAST_OR_RECENT = "since_last_retrospective_or_recent"
RETROSPECTIVE_WINDOW_MODES = frozenset({
    RETROSPECTIVE_WINDOW_SINCE_LAST_OR_RECENT,
})

RETROSPECTIVE_ACTION_OBSERVE_ONLY = "observe_only"
RETROSPECTIVE_ACTION_ALLOW_ONE_FOLLOW_UP = "allow_one_bounded_follow_up_goal"
RETROSPECTIVE_ACTION_BOUNDARIES = frozenset({
    RETROSPECTIVE_ACTION_OBSERVE_ONLY,
    RETROSPECTIVE_ACTION_ALLOW_ONE_FOLLOW_UP,
})

DEFAULT_RETROSPECTIVE_RECENT_RUN_LIMIT = 5
MIN_RETROSPECTIVE_RECENT_RUN_LIMIT = 3
MAX_RETROSPECTIVE_RECENT_RUN_LIMIT = 10


def retrospective_action_boundary(*, allow_follow_up_goal: bool) -> str:
    if allow_follow_up_goal:
        return RETROSPECTIVE_ACTION_ALLOW_ONE_FOLLOW_UP
    return RETROSPECTIVE_ACTION_OBSERVE_ONLY


def build_retrospective_payload(*,
                                recent_run_limit: int = DEFAULT_RETROSPECTIVE_RECENT_RUN_LIMIT,
                                allow_follow_up_goal: bool = False) -> dict:
    return {
        "window": RETROSPECTIVE_WINDOW_SINCE_LAST_OR_RECENT,
        "recent_run_limit": recent_run_limit,
        "action_boundary": retrospective_action_boundary(
            allow_follow_up_goal=allow_follow_up_goal,
        ),
    }


def render_retrospective_body(payload: dict) -> str:
    window = payload["window"]
    recent_run_limit = payload["recent_run_limit"]
    action_boundary = payload["action_boundary"]

    if action_boundary == RETROSPECTIVE_ACTION_ALLOW_ONE_FOLLOW_UP:
        action_line = (
            "You may submit at most one bounded follow-up goal only if the "
            "recommendation is concrete and grounded in the evidence."
        )
    else:
        action_line = "Do not submit follow-up goals during this retrospective."

    return (
        "Perform a bounded retrospective using the existing gardener-local "
        "evaluate protocol in `plants/gardener/skills/retrospective-pass.md`.\n\n"
        "Retrospective contract:\n"
        f"- `retrospective.window`: `{window}`\n"
        f"- `retrospective.recent_run_limit`: `{recent_run_limit}`\n"
        f"- `retrospective.action_boundary`: `{action_boundary}`\n"
        "- Default exclusions remain `converse` and `tend` unless this task "
        "explicitly says otherwise.\n"
        "- Do not update memory.\n"
        "- Do not write knowledge files.\n"
        f"- {action_line}"
    )


__all__ = [
    "DEFAULT_RETROSPECTIVE_RECENT_RUN_LIMIT",
    "MAX_RETROSPECTIVE_RECENT_RUN_LIMIT",
    "MIN_RETROSPECTIVE_RECENT_RUN_LIMIT",
    "RETROSPECTIVE_ACTION_ALLOW_ONE_FOLLOW_UP",
    "RETROSPECTIVE_ACTION_BOUNDARIES",
    "RETROSPECTIVE_ACTION_OBSERVE_ONLY",
    "RETROSPECTIVE_WINDOW_MODES",
    "RETROSPECTIVE_WINDOW_SINCE_LAST_OR_RECENT",
    "build_retrospective_payload",
    "render_retrospective_body",
    "retrospective_action_boundary",
]
