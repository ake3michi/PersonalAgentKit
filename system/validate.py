"""
validate.py — schema enforcement at the system boundary.

All validation functions return a ValidationResult. They never raise.
The caller decides what to do with a rejection — the validator only names it.

Reason codes are strings in SCREAMING_SNAKE_CASE. Each represents a distinct
failure mode that can be handled independently. See docs/ for the full list.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .retrospective import (
    MAX_RETROSPECTIVE_RECENT_RUN_LIMIT,
    MIN_RETROSPECTIVE_RECENT_RUN_LIMIT,
    RETROSPECTIVE_ACTION_BOUNDARIES,
    RETROSPECTIVE_WINDOW_MODES,
)
from .plant_commission import (
    PLANT_COMMISSION_ASSIGNED_TO,
    PLANT_COMMISSION_GOAL_TYPE,
    PLANT_COMMISSION_INITIAL_GOAL_TYPES,
)
from .tend import TEND_TRIGGER_KINDS

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: Optional[str] = None   # None when ok is True
    detail: Optional[str] = None   # Human-readable explanation, never parsed

    @staticmethod
    def accept() -> "ValidationResult":
        return ValidationResult(ok=True)

    @staticmethod
    def reject(reason: str, detail: str = "") -> "ValidationResult":
        return ValidationResult(ok=False, reason=reason, detail=detail)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_ID_PATTERN      = re.compile(r"^[1-9][0-9]*-[a-z0-9-]+$")
_RUN_ID_PATTERN  = re.compile(r"^[1-9][0-9]*-[a-z0-9-]+-r[1-9][0-9]*$")
_ACTOR_PATTERN   = re.compile(r"^[a-z][a-z0-9-]*$")
_DASHBOARD_INVOCATION_ID_PATTERN = re.compile(r"^dash-[0-9]{14}-[a-z0-9]{4}$")
_ISO8601_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)

PLANT_STATUSES  = {"active", "archived"}
GOAL_STATUSES   = {"queued", "dispatched", "running", "completed", "evaluating", "closed"}
GOAL_TYPES      = {"build", "fix", "spike", "tend", "evaluate", "research", "converse"}

CONVERSATION_STATUSES = {"open", "closed", "archived"}
PRESENCE_MODELS       = {"sync", "async"}
CONVERSATION_STARTERS = {"operator", "system"}
CLOSED_REASONS  = {"success", "failure", "cancelled", "dependency_impossible"}

EVENT_TYPES = {
    "GoalSubmitted", "GoalTransitioned", "GoalDispatched", "GoalClosed",
    "GoalSupplemented", "DispatchPacketMaterialized",
    "RunStarted", "RunFinished", "EvalSpawned", "EvalClosed",
    "TendStarted", "TendFinished", "MemoryUpdated",
    "InitiativeRefreshStarted", "InitiativeRefreshFinished",
    "ActiveThreadsRefreshStarted", "ActiveThreadsRefreshFinished",
    "DashboardInvocationStarted", "DashboardInvocationFinished",
    "ConversationHopQueued", "ConversationHopQueueFailed",
    "ConversationCheckpointWritten",
    "SkillAdded", "SkillArchived", "SystemError",
    "PlantCommissioned", "PlantArchived",
}
_PLANT_REQUIRED_EVENTS = {"PlantCommissioned", "PlantArchived"}
_CONVERSATION_REQUIRED_EVENTS = {
    "ConversationHopQueued",
    "ConversationHopQueueFailed",
    "ConversationCheckpointWritten",
}
# Events that require a goal field
_GOAL_REQUIRED_EVENTS = {
    "GoalSubmitted", "GoalTransitioned", "GoalDispatched", "GoalClosed",
    "GoalSupplemented", "DispatchPacketMaterialized",
    "RunStarted", "RunFinished", "EvalSpawned", "EvalClosed",
    "TendStarted", "TendFinished",
    "ConversationHopQueued", "ConversationHopQueueFailed",
}
# Events that require a run field
_RUN_REQUIRED_EVENTS = {
    "RunStarted", "RunFinished", "DispatchPacketMaterialized",
    "TendStarted", "TendFinished",
    "ConversationHopQueued", "ConversationHopQueueFailed",
}

GOAL_REASON_CODES   = {"success", "failure", "cancelled", "dependency_impossible"}
RUN_REASON_CODES    = {"success", "failure", "killed", "timeout", "zero_output"}
ERROR_REASON_CODES  = {
    "schema_violation", "invalid_transition", "submission_rejected", "validator_unavailable"
}
DASHBOARD_MODES = {"once", "live"}
DASHBOARD_INVOCATION_OUTCOMES = {"success", "interrupted", "failure"}
ACTIVE_THREADS_REFRESH_OUTCOMES = {"success", "validation_rejected", "io_error"}
INITIATIVE_REFRESH_OUTCOMES = {"success", "validation_rejected", "io_error"}

RUN_STATUSES  = {"running", "success", "failure", "killed", "timeout", "zero_output"}
RUN_TERMINAL  = {"success", "failure", "killed", "timeout", "zero_output"}
RUN_FAILED    = {"failure", "killed", "timeout", "zero_output"}
COST_SOURCES  = {"provider", "estimated", "unknown"}
DASHBOARD_COST_SOURCES = {"measured"}
FAILURE_REASONS = {"failure", "killed", "timeout", "zero_output"}
REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
_TEND_PAYLOAD_FIELDS = {"trigger_kinds", "trigger_goal", "trigger_run"}
_PLANT_COMMISSION_FIELDS = {"plant_name", "seed", "initial_goal"}
_PLANT_COMMISSION_INITIAL_GOAL_FIELDS = {
    "type",
    "body",
    "priority",
    "driver",
    "model",
    "reasoning_effort",
}
_RETROSPECTIVE_FIELDS = {"window", "recent_run_limit", "action_boundary"}
_MESSAGE_ID_PATTERN = re.compile(r"^msg-[0-9]{14}-[a-z]{3}-[a-z0-9]{4}$")
_CHECKPOINT_ID_PATTERN = re.compile(r"^ckpt-[0-9]{14}-[a-z0-9]{4}$")
_SUPPLEMENT_ID_PATTERN = re.compile(r"^supp-[0-9]{14}-[a-z0-9]{4}$")
_DASHBOARD_RECORD_PATH_PATTERN = re.compile(
    r"^dashboard/invocations/dash-[0-9]{14}-[a-z0-9]{4}\.json$"
)
_ACTIVE_THREADS_PATH_PATTERN = re.compile(
    r"^plants/[a-z][a-z0-9-]*/memory/active-threads\.json$"
)
_CHECKPOINT_SUMMARY_PATH_PATTERN = re.compile(
    r"^checkpoints/ckpt-[0-9]{14}-[a-z0-9]{4}\.md$"
)
_TURN_ID_PATTERN = re.compile(r"^turn-[0-9]{14}$")
CONVERSATION_TURN_MODES = {"resumed", "fresh-handoff", "fresh-start"}
PRESSURE_BANDS = {"low", "medium", "high", "critical"}
PRESSURE_PROMPT_SOURCES = {"resume-session", "summary+tail", "full-history"}
_CONVERSATION_FIELDS = {
    "id",
    "status",
    "channel",
    "channel_ref",
    "presence_model",
    "started_by",
    "participants",
    "topic",
    "started_at",
    "last_activity_at",
    "context_at",
    "session_id",
    "compacted_through",
    "session_ordinal",
    "session_turns",
    "session_started_at",
    "checkpoint_count",
    "last_checkpoint_id",
    "last_checkpoint_at",
    "last_turn_mode",
    "last_turn_run_id",
    "last_pressure",
    "pending_hop",
    "post_reply_hop",
}
_MESSAGE_FIELDS = {
    "id",
    "conversation_id",
    "ts",
    "sender",
    "content",
    "channel",
    "reply_to",
}
_PRESSURE_FIELDS = {
    "band",
    "score",
    "needs_hop",
    "prompt_source",
    "summary_present",
    "history_messages",
    "history_chars",
    "tail_messages",
    "tail_chars",
    "summary_chars",
    "prompt_chars",
    "session_turns",
    "provider_input_tokens",
    "provider_cached_input_tokens",
    "provider_output_tokens",
    "reasons",
    "thresholds",
}
_PRESSURE_THRESHOLD_FIELDS = {
    "tail_messages",
    "tail_chars",
    "prompt_chars",
    "session_turns",
    "input_tokens",
}
_PENDING_HOP_FIELDS = {"requested_at", "requested_by", "reason"}
_POST_REPLY_HOP_FIELDS = {
    "requested_at",
    "requested_by",
    "reason",
    "automatic",
    "source_goal_id",
    "source_run_id",
    "source_reply_message_id",
    "source_reply_recorded_at",
    "source_session_id",
    "source_session_ordinal",
    "source_session_turns",
    "pressure",
    "goal_id",
}
_TURN_FIELDS = {
    "id",
    "conversation_id",
    "run_id",
    "goal_id",
    "ts",
    "status",
    "mode",
    "diff_present",
    "lineage",
    "pressure",
    "hop",
    "session_id_before",
    "session_id_after",
}
_TURN_LINEAGE_FIELDS = {
    "session_ordinal",
    "session_turn",
    "label",
    "checkpoint_id",
    "checkpoint_count",
}
_TURN_HOP_FIELDS = {
    "requested",
    "reason",
    "queued",
    "goal_id",
    "performed",
    "checkpoint_id",
    "error",
    "automatic",
}
_CHECKPOINT_FIELDS = {
    "id",
    "conversation_id",
    "ts",
    "requested_by",
    "reason",
    "compacted_through",
    "source_session_id",
    "source_session_ordinal",
    "source_session_turns",
    "summary_path",
    "run_id",
    "driver",
    "model",
    "pressure",
}
_SUPPLEMENT_SOURCE_FIELDS = {"kind", "conversation_id", "message_id"}
_GOAL_SUPPLEMENT_FIELDS = {
    "id",
    "goal",
    "ts",
    "actor",
    "source",
    "kind",
    "content",
    "source_goal_id",
    "source_run_id",
}
_GOAL_ORIGIN_FIELDS = {"kind", "conversation_id", "ts", "message_id"}
_DISPATCH_PACKET_ORIGIN_FIELDS = {"kind", "conversation_id", "ts", "message_id"}
_DISPATCH_PACKET_FIELDS = {
    "goal_id",
    "run_id",
    "cutoff",
    "origin",
    "goal_body",
    "supplement_count",
    "supplement_chars",
    "supplements",
}
_DASHBOARD_INVOCATION_COST_FIELDS = {"source", "wall_ms"}
_DASHBOARD_INVOCATION_FIELDS = {
    "id",
    "actor",
    "source_goal_id",
    "source_run_id",
    "started_at",
    "completed_at",
    "root",
    "mode",
    "refresh_seconds",
    "tty",
    "render_count",
    "outcome",
    "error_detail",
    "cost",
}
OPERATOR_MESSAGE_KINDS = {"tend_survey", "recently_concluded"}
OPERATOR_MESSAGE_SENDERS = {"garden"}
OPERATOR_MESSAGE_TRANSCRIPT_POLICIES = {"canonical", "none"}
OPERATOR_MESSAGE_DELIVERY_POLICIES = {"reply_copy", "out_of_band_note"}
_OPERATOR_MESSAGE_REQUEST_FIELDS = {
    "kind",
    "sender",
    "content",
    "origin",
    "source_goal_id",
    "source_run_id",
}
_OPERATOR_MESSAGE_RECORD_FIELDS = {
    "schema_version",
    "kind",
    "sender",
    "origin",
    "transcript_policy",
    "delivery_policy",
    "emitted_at",
    "source_goal_id",
    "source_run_id",
    "delivery_path",
    "conversation_message_id",
}
_OPERATOR_MESSAGE_ORIGIN_FIELDS = {"kind", "conversation_id", "ts", "message_id"}
_OPERATOR_MESSAGE_SCHEMA_VERSION = 1
_OPERATOR_MESSAGE_DELIVERY_PATH_PATTERN = re.compile(
    r"^inbox/[a-z][a-z0-9-]*/(?:notes/)?[^/]+\.md$"
)
ACTIVE_THREAD_STATES = {"active", "near_done", "watching", "blocked"}
ACTIVE_THREAD_PRIORITIES = {"primary", "secondary", "background"}
_ACTIVE_THREADS_FIELDS = {
    "schema_version",
    "captured_at",
    "captured_by_run",
    "plant",
    "summary",
    "threads",
    "recent_updates",
}
_ACTIVE_THREAD_FIELDS = {
    "id",
    "title",
    "state",
    "priority",
    "last_changed_at",
    "summary",
    "current_focus",
    "next_step",
    "related_thread_ids",
    "evidence",
}
_ACTIVE_THREAD_UPDATE_FIELDS = {
    "ts",
    "summary",
    "thread_ids",
    "evidence",
}
INITIATIVE_STATUSES = {
    "desired",
    "approved",
    "active",
    "paused",
    "blocked",
    "completed",
    "abandoned",
}
INITIATIVE_TRANCHE_STATUSES = {"planned", "active", "completed", "abandoned"}
INITIATIVE_EXECUTION_MODES = {"ordinary_goals_only", "bounded_campaign_optional"}
INITIATIVE_REVIEW_POLICIES = {"mandatory_review_or_evaluate_stop"}
INITIATIVE_SUCCESSOR_CONDITIONS = {
    "review_or_evaluate_recommends_next_tranche",
    "initiative_complete_after_clean_review",
}
INITIATIVE_NEXT_STEP_STATUSES = {"ready", "waiting", "blocked", "done"}
INITIATIVE_BUDGET_MODES = {"track_only", "ceilinged"}
INITIATIVE_ALLOWED_GOAL_TYPES = {"build", "fix", "spike", "evaluate", "research"}
_INITIATIVE_FIELDS = {
    "schema_version",
    "id",
    "plant",
    "title",
    "status",
    "approved_by",
    "objective",
    "scope_boundary",
    "non_goals",
    "success_checks",
    "budget_policy",
    "tranches",
    "current_tranche_id",
    "next_authorized_step",
    "ledger",
    "updated_at",
    "updated_by_run",
}
_INITIATIVE_APPROVAL_FIELDS = {"kind", "conversation_id", "message_id", "ts"}
_INITIATIVE_BUDGET_POLICY_FIELDS = {
    "mode",
    "notes",
    "max_input_tokens",
    "max_output_tokens",
    "max_cache_read_tokens",
}
_INITIATIVE_TRANCHE_FIELDS = {
    "id",
    "title",
    "objective",
    "status",
    "allowed_goal_types",
    "execution_mode",
    "review_policy",
    "stop_rules",
    "successor",
}
_INITIATIVE_SUCCESSOR_FIELDS = {"condition", "next_tranche_id", "summary"}
_INITIATIVE_NEXT_STEP_FIELDS = {
    "tranche_id",
    "status",
    "goal_type",
    "summary",
    "may_start_bounded_campaign",
    "stop_after",
}
_INITIATIVE_LEDGER_FIELDS = {"goal_ids", "run_ids", "totals"}
_INITIATIVE_LEDGER_TOTAL_FIELDS = {
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
}

# Goal types that require a reflection on successful completion.
# Enforced by validate_run_close, not by the run schema alone.
REFLECTION_REQUIRED_TYPES = {"build", "fix", "evaluate", "tend"}


def _parse_iso8601(ts: str) -> Optional[datetime]:
    """Parse an ISO 8601 UTC timestamp. Returns None if unparseable."""
    try:
        # Normalise Z suffix
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, AttributeError):
        return None


def _is_nonnegative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_nonempty_string(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_known_fields(data: dict, *, allowed: set[str],
                           reason: str, label: str) -> ValidationResult:
    for field in data:
        if field not in allowed:
            return ValidationResult.reject(
                reason,
                f"unknown {label} field: {field!r}",
            )
    return ValidationResult.accept()


def _validate_nonempty_string_list(
    value,
    *,
    reason: str,
    label: str,
    min_items: int = 0,
) -> ValidationResult:
    if not isinstance(value, list):
        return ValidationResult.reject(reason, f"{label} must be a list")
    if len(value) < min_items:
        return ValidationResult.reject(
            reason,
            f"{label} must contain at least {min_items} item(s)",
        )
    for item in value:
        if not _is_nonempty_string(item):
            return ValidationResult.reject(
                reason,
                f"{label} items must be non-empty strings, got: {item!r}",
            )
    return ValidationResult.accept()


def _validate_pressure(data: dict, *, reason: str, label: str) -> ValidationResult:
    if not isinstance(data, dict):
        return ValidationResult.reject(reason, f"{label} must be a JSON object")
    result = _validate_known_fields(
        data,
        allowed=_PRESSURE_FIELDS,
        reason=reason,
        label=f"{label}.pressure",
    )
    if not result.ok:
        return result

    if "band" in data and data["band"] not in PRESSURE_BANDS:
        return ValidationResult.reject(
            reason,
            f"{label}.band must be one of {sorted(PRESSURE_BANDS)}, got: {data['band']!r}",
        )
    if "score" in data and (not _is_number(data["score"]) or data["score"] < 0):
        return ValidationResult.reject(
            reason,
            f"{label}.score must be a non-negative number, got: {data['score']!r}",
        )
    for field in ("needs_hop", "summary_present"):
        if field in data and not isinstance(data[field], bool):
            return ValidationResult.reject(
                reason,
                f"{label}.{field} must be a boolean, got: {data[field]!r}",
            )
    if "prompt_source" in data and data["prompt_source"] not in PRESSURE_PROMPT_SOURCES:
        return ValidationResult.reject(
            reason,
            (
                f"{label}.prompt_source must be one of "
                f"{sorted(PRESSURE_PROMPT_SOURCES)}, got: {data['prompt_source']!r}"
            ),
        )
    for field in (
        "history_messages",
        "history_chars",
        "tail_messages",
        "tail_chars",
        "summary_chars",
        "prompt_chars",
        "session_turns",
    ):
        if field in data and not _is_nonnegative_int(data[field]):
            return ValidationResult.reject(
                reason,
                f"{label}.{field} must be a non-negative integer, got: {data[field]!r}",
            )
    for field in (
        "provider_input_tokens",
        "provider_cached_input_tokens",
        "provider_output_tokens",
    ):
        if field in data and data[field] is not None and not _is_nonnegative_int(data[field]):
            return ValidationResult.reject(
                reason,
                f"{label}.{field} must be null or a non-negative integer, got: {data[field]!r}",
            )
    if "reasons" in data:
        reasons = data["reasons"]
        if not isinstance(reasons, list) or any(not _is_nonempty_string(item) for item in reasons):
            return ValidationResult.reject(
                reason,
                f"{label}.reasons must be an array of non-empty strings",
            )
    if "thresholds" in data:
        thresholds = data["thresholds"]
        if not isinstance(thresholds, dict):
            return ValidationResult.reject(
                reason,
                f"{label}.thresholds must be a JSON object",
            )
        for field in thresholds:
            if field not in _PRESSURE_THRESHOLD_FIELDS:
                return ValidationResult.reject(
                    reason,
                    f"unknown {label}.thresholds field: {field!r}",
                )
            if not _is_nonnegative_int(thresholds[field]):
                return ValidationResult.reject(
                    reason,
                    (
                        f"{label}.thresholds.{field} must be a non-negative integer, "
                        f"got: {thresholds[field]!r}"
                    ),
                )
    return ValidationResult.accept()


def _validate_pending_hop(data: dict, *, reason: str) -> ValidationResult:
    if not isinstance(data, dict):
        return ValidationResult.reject(reason, "pending_hop must be a JSON object")
    result = _validate_known_fields(
        data,
        allowed=_PENDING_HOP_FIELDS,
        reason=reason,
        label="pending_hop",
    )
    if not result.ok:
        return result
    for field in _PENDING_HOP_FIELDS:
        if field not in data:
            return ValidationResult.reject(reason, f"pending_hop missing required field: {field}")
    if not _ISO8601_PATTERN.match(str(data["requested_at"])):
        return ValidationResult.reject(reason, "pending_hop.requested_at must be ISO 8601 UTC")
    if not _is_nonempty_string(data["requested_by"]):
        return ValidationResult.reject(reason, "pending_hop.requested_by must be non-empty")
    if not _is_nonempty_string(data["reason"]):
        return ValidationResult.reject(reason, "pending_hop.reason must be non-empty")
    return ValidationResult.accept()


def _validate_post_reply_hop(data: dict, *, reason: str) -> ValidationResult:
    if not isinstance(data, dict):
        return ValidationResult.reject(reason, "post_reply_hop must be a JSON object")
    result = _validate_known_fields(
        data,
        allowed=_POST_REPLY_HOP_FIELDS,
        reason=reason,
        label="post_reply_hop",
    )
    if not result.ok:
        return result
    for field in _POST_REPLY_HOP_FIELDS:
        if field not in data:
            return ValidationResult.reject(
                reason,
                f"post_reply_hop missing required field: {field}",
            )
    if not _ISO8601_PATTERN.match(str(data["requested_at"])):
        return ValidationResult.reject(
            reason,
            "post_reply_hop.requested_at must be ISO 8601 UTC",
        )
    if not _is_nonempty_string(data["requested_by"]):
        return ValidationResult.reject(
            reason,
            "post_reply_hop.requested_by must be non-empty",
        )
    if not _is_nonempty_string(data["reason"]):
        return ValidationResult.reject(reason, "post_reply_hop.reason must be non-empty")
    if not isinstance(data["automatic"], bool):
        return ValidationResult.reject(
            reason,
            "post_reply_hop.automatic must be a boolean",
        )
    if not _ID_PATTERN.match(str(data["source_goal_id"])):
        return ValidationResult.reject(
            reason,
            f"post_reply_hop.source_goal_id must be a valid goal id, got: {data['source_goal_id']!r}",
        )
    if not _RUN_ID_PATTERN.match(str(data["source_run_id"])):
        return ValidationResult.reject(
            reason,
            f"post_reply_hop.source_run_id must be a valid run id, got: {data['source_run_id']!r}",
        )
    if not _MESSAGE_ID_PATTERN.match(str(data["source_reply_message_id"])):
        return ValidationResult.reject(
            reason,
            (
                "post_reply_hop.source_reply_message_id must be a valid message id, "
                f"got: {data['source_reply_message_id']!r}"
            ),
        )
    if not _ISO8601_PATTERN.match(str(data["source_reply_recorded_at"])):
        return ValidationResult.reject(
            reason,
            "post_reply_hop.source_reply_recorded_at must be ISO 8601 UTC",
        )
    if not _is_nonempty_string(data["source_session_id"]):
        return ValidationResult.reject(
            reason,
            "post_reply_hop.source_session_id must be non-empty",
        )
    if not _is_nonnegative_int(data["source_session_ordinal"]):
        return ValidationResult.reject(
            reason,
            "post_reply_hop.source_session_ordinal must be a non-negative integer",
        )
    if not _is_nonnegative_int(data["source_session_turns"]):
        return ValidationResult.reject(
            reason,
            "post_reply_hop.source_session_turns must be a non-negative integer",
        )
    result = _validate_pressure(
        data["pressure"],
        reason=reason,
        label="post_reply_hop",
    )
    if not result.ok:
        return result
    if not _ID_PATTERN.match(str(data["goal_id"])):
        return ValidationResult.reject(
            reason,
            f"post_reply_hop.goal_id must be a valid goal id, got: {data['goal_id']!r}",
        )
    return ValidationResult.accept()


def _validate_turn_lineage(data: dict) -> ValidationResult:
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_TURN_LINEAGE",
            "lineage must be a JSON object",
        )
    result = _validate_known_fields(
        data,
        allowed=_TURN_LINEAGE_FIELDS,
        reason="INVALID_TURN_LINEAGE",
        label="lineage",
    )
    if not result.ok:
        return result
    for field in _TURN_LINEAGE_FIELDS:
        if field not in data:
            return ValidationResult.reject(
                "INVALID_TURN_LINEAGE",
                f"lineage missing required field: {field}",
            )
    if not _is_nonnegative_int(data["session_ordinal"]):
        return ValidationResult.reject(
            "INVALID_TURN_LINEAGE",
            "lineage.session_ordinal must be a non-negative integer",
        )
    if not _is_nonnegative_int(data["session_turn"]):
        return ValidationResult.reject(
            "INVALID_TURN_LINEAGE",
            "lineage.session_turn must be a non-negative integer",
        )
    if not _is_nonempty_string(data["label"]):
        return ValidationResult.reject(
            "INVALID_TURN_LINEAGE",
            "lineage.label must be non-empty",
        )
    checkpoint_id = data["checkpoint_id"]
    if checkpoint_id is not None and not _CHECKPOINT_ID_PATTERN.match(str(checkpoint_id)):
        return ValidationResult.reject(
            "INVALID_TURN_LINEAGE",
            f"lineage.checkpoint_id must be a valid checkpoint id, got: {checkpoint_id!r}",
        )
    if not _is_nonnegative_int(data["checkpoint_count"]):
        return ValidationResult.reject(
            "INVALID_TURN_LINEAGE",
            "lineage.checkpoint_count must be a non-negative integer",
        )
    return ValidationResult.accept()


def _validate_turn_hop(data: dict) -> ValidationResult:
    if not isinstance(data, dict):
        return ValidationResult.reject("INVALID_TURN_HOP", "hop must be a JSON object")
    result = _validate_known_fields(
        data,
        allowed=_TURN_HOP_FIELDS,
        reason="INVALID_TURN_HOP",
        label="hop",
    )
    if not result.ok:
        return result
    for field in ("requested", "reason", "performed", "checkpoint_id", "error", "automatic"):
        if field not in data:
            return ValidationResult.reject(
                "INVALID_TURN_HOP",
                f"hop missing required field: {field}",
            )
    for field in ("requested", "performed", "automatic"):
        if not isinstance(data[field], bool):
            return ValidationResult.reject(
                "INVALID_TURN_HOP",
                f"hop.{field} must be a boolean",
            )
    if "queued" in data and not isinstance(data["queued"], bool):
        return ValidationResult.reject(
            "INVALID_TURN_HOP",
            "hop.queued must be a boolean when present",
        )
    for field in ("reason", "error"):
        value = data[field]
        if value is not None and not _is_nonempty_string(value):
            return ValidationResult.reject(
                "INVALID_TURN_HOP",
                f"hop.{field} must be null or a non-empty string",
            )
    goal_id = data.get("goal_id")
    if "goal_id" in data and goal_id is not None and not _ID_PATTERN.match(str(goal_id)):
        return ValidationResult.reject(
            "INVALID_TURN_HOP",
            f"hop.goal_id must be null or a valid goal id, got: {goal_id!r}",
        )
    checkpoint_id = data["checkpoint_id"]
    if checkpoint_id is not None and not _CHECKPOINT_ID_PATTERN.match(str(checkpoint_id)):
        return ValidationResult.reject(
            "INVALID_TURN_HOP",
            f"hop.checkpoint_id must be null or a valid checkpoint id, got: {checkpoint_id!r}",
        )
    queued_present = "queued" in data
    goal_present = "goal_id" in data
    if queued_present != goal_present:
        return ValidationResult.reject(
            "INVALID_TURN_HOP",
            "hop.queued and hop.goal_id must either both be present or both be absent",
        )
    if queued_present:
        if data["queued"] and goal_id is None:
            return ValidationResult.reject(
                "INVALID_TURN_HOP",
                "hop.goal_id is required when hop.queued is true",
            )
        if not data["queued"] and goal_id is not None:
            return ValidationResult.reject(
                "INVALID_TURN_HOP",
                "hop.goal_id must be null when hop.queued is false",
            )
    if data["performed"] and checkpoint_id is None:
        return ValidationResult.reject(
            "INVALID_TURN_HOP",
            "hop.checkpoint_id is required when hop.performed is true",
        )
    return ValidationResult.accept()


def validate_conversation_turn(data: dict) -> ValidationResult:
    """Validate a conversation turn record."""
    if not isinstance(data, dict):
        return ValidationResult.reject("INVALID_SHAPE", "Conversation turn must be a JSON object")
    for field in (
        "id",
        "conversation_id",
        "run_id",
        "goal_id",
        "ts",
        "status",
        "mode",
        "diff_present",
        "lineage",
        "pressure",
        "hop",
        "session_id_before",
        "session_id_after",
    ):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"Missing required field: {field}",
            )
    result = _validate_known_fields(
        data,
        allowed=_TURN_FIELDS,
        reason="UNKNOWN_TURN_FIELD",
        label="conversation turn",
    )
    if not result.ok:
        return result
    if not _TURN_ID_PATTERN.match(str(data["id"])):
        return ValidationResult.reject(
            "INVALID_TURN_FIELD",
            f"id must be a valid turn id, got: {data['id']!r}",
        )
    if not _ID_PATTERN.match(str(data["conversation_id"])):
        return ValidationResult.reject(
            "INVALID_TURN_FIELD",
            f"conversation_id must be a valid conversation id, got: {data['conversation_id']!r}",
        )
    if not _RUN_ID_PATTERN.match(str(data["run_id"])):
        return ValidationResult.reject(
            "INVALID_TURN_FIELD",
            f"run_id must be a valid run id, got: {data['run_id']!r}",
        )
    if not _ID_PATTERN.match(str(data["goal_id"])):
        return ValidationResult.reject(
            "INVALID_TURN_FIELD",
            f"goal_id must be a valid goal id, got: {data['goal_id']!r}",
        )
    if not _ISO8601_PATTERN.match(str(data["ts"])):
        return ValidationResult.reject(
            "INVALID_TIMESTAMP",
            "ts must be ISO 8601 UTC",
        )
    if data["status"] not in RUN_TERMINAL:
        return ValidationResult.reject(
            "INVALID_TURN_FIELD",
            f"status must be one of {sorted(RUN_TERMINAL)}, got: {data['status']!r}",
        )
    if data["mode"] not in CONVERSATION_TURN_MODES:
        return ValidationResult.reject(
            "INVALID_TURN_FIELD",
            f"mode must be one of {sorted(CONVERSATION_TURN_MODES)}, got: {data['mode']!r}",
        )
    if not isinstance(data["diff_present"], bool):
        return ValidationResult.reject(
            "INVALID_TURN_FIELD",
            "diff_present must be a boolean",
        )
    result = _validate_turn_lineage(data["lineage"])
    if not result.ok:
        return result
    result = _validate_pressure(
        data["pressure"],
        reason="INVALID_TURN_PRESSURE",
        label="turn",
    )
    if not result.ok:
        return result
    result = _validate_turn_hop(data["hop"])
    if not result.ok:
        return result
    for field in ("session_id_before", "session_id_after"):
        value = data[field]
        if value is not None and not _is_nonempty_string(value):
            return ValidationResult.reject(
                "INVALID_TURN_FIELD",
                f"{field} must be null or a non-empty string",
            )
    return ValidationResult.accept()


def validate_conversation_checkpoint(data: dict) -> ValidationResult:
    """Validate a conversation checkpoint record."""
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "Conversation checkpoint must be a JSON object",
        )
    for field in (
        "id",
        "conversation_id",
        "ts",
        "requested_by",
        "reason",
        "compacted_through",
        "source_session_id",
        "source_session_ordinal",
        "source_session_turns",
        "summary_path",
        "run_id",
        "driver",
        "model",
        "pressure",
    ):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"Missing required field: {field}",
            )
    result = _validate_known_fields(
        data,
        allowed=_CHECKPOINT_FIELDS,
        reason="UNKNOWN_CHECKPOINT_FIELD",
        label="conversation checkpoint",
    )
    if not result.ok:
        return result
    if not _CHECKPOINT_ID_PATTERN.match(str(data["id"])):
        return ValidationResult.reject(
            "INVALID_CHECKPOINT_FIELD",
            f"id must be a valid checkpoint id, got: {data['id']!r}",
        )
    if not _ID_PATTERN.match(str(data["conversation_id"])):
        return ValidationResult.reject(
            "INVALID_CHECKPOINT_FIELD",
            f"conversation_id must be a valid conversation id, got: {data['conversation_id']!r}",
        )
    if not _ISO8601_PATTERN.match(str(data["ts"])):
        return ValidationResult.reject(
            "INVALID_TIMESTAMP",
            "ts must be ISO 8601 UTC",
        )
    for field in ("requested_by", "reason"):
        if not _is_nonempty_string(data[field]):
            return ValidationResult.reject(
                "INVALID_CHECKPOINT_FIELD",
                f"{field} must be non-empty",
            )
    if not _MESSAGE_ID_PATTERN.match(str(data["compacted_through"])):
        return ValidationResult.reject(
            "INVALID_CHECKPOINT_FIELD",
            (
                "compacted_through must be a valid message id, "
                f"got: {data['compacted_through']!r}"
            ),
        )
    source_session_id = data["source_session_id"]
    if source_session_id is not None and not _is_nonempty_string(source_session_id):
        return ValidationResult.reject(
            "INVALID_CHECKPOINT_FIELD",
            "source_session_id must be null or a non-empty string",
        )
    for field in ("source_session_ordinal", "source_session_turns"):
        if not _is_nonnegative_int(data[field]):
            return ValidationResult.reject(
                "INVALID_CHECKPOINT_FIELD",
                f"{field} must be a non-negative integer",
            )
    summary_path = data["summary_path"]
    expected_summary_path = (
        f"checkpoints/{data['id']}.md"
        if _CHECKPOINT_ID_PATTERN.match(str(data["id"]))
        else None
    )
    if not _is_nonempty_string(summary_path) or summary_path.startswith("/") or ".." in Path(summary_path).parts:
        return ValidationResult.reject(
            "INVALID_CHECKPOINT_FIELD",
            f"summary_path must be a safe relative path, got: {summary_path!r}",
        )
    if expected_summary_path is not None and summary_path != expected_summary_path:
        return ValidationResult.reject(
            "INVALID_CHECKPOINT_FIELD",
            (
                f"summary_path must match {expected_summary_path!r}, "
                f"got: {summary_path!r}"
            ),
        )
    run_id = data["run_id"]
    if run_id is not None and not _RUN_ID_PATTERN.match(str(run_id)):
        return ValidationResult.reject(
            "INVALID_CHECKPOINT_FIELD",
            f"run_id must be null or a valid run id, got: {run_id!r}",
        )
    for field in ("driver", "model"):
        value = data[field]
        if value is not None and not _is_nonempty_string(value):
            return ValidationResult.reject(
                "INVALID_CHECKPOINT_FIELD",
                f"{field} must be null or a non-empty string",
            )
    pressure = data["pressure"]
    if pressure is not None:
        result = _validate_pressure(
            pressure,
            reason="INVALID_CHECKPOINT_PRESSURE",
            label="checkpoint",
        )
        if not result.ok:
            return result
    return ValidationResult.accept()


def _validate_tend_payload(data: dict) -> ValidationResult:
    """
    Validate the raw tend payload.

    The queued-state requirement closes the submission boundary while allowing
    historical tend goals that predate explicit metadata to remain readable.
    """
    raw = data.get("tend")
    if data.get("status") != "queued" and raw is None:
        return ValidationResult.accept()

    if raw is None:
        return ValidationResult.reject(
            "MISSING_TEND_PAYLOAD",
            "tend goals must include a tend payload",
        )
    if not isinstance(raw, dict):
        return ValidationResult.reject(
            "INVALID_TEND_PAYLOAD",
            "tend must be a JSON object",
        )

    for field in raw:
        if field not in _TEND_PAYLOAD_FIELDS:
            return ValidationResult.reject(
                "UNKNOWN_TEND_FIELD",
                f"unknown tend field: {field!r}",
            )

    if "trigger_kinds" not in raw:
        return ValidationResult.reject(
            "MISSING_TEND_TRIGGER_KINDS",
            "tend.trigger_kinds is required",
        )

    trigger_kinds = raw["trigger_kinds"]
    if not isinstance(trigger_kinds, list) or not trigger_kinds:
        return ValidationResult.reject(
            "INVALID_TEND_TRIGGER_KINDS",
            "tend.trigger_kinds must be a non-empty array",
        )

    seen: set[str] = set()
    for kind in trigger_kinds:
        if not isinstance(kind, str) or not kind.strip():
            return ValidationResult.reject(
                "INVALID_TEND_TRIGGER_KINDS",
                "tend.trigger_kinds entries must be non-empty strings",
            )
        if kind != kind.strip():
            return ValidationResult.reject(
                "INVALID_TEND_TRIGGER_KINDS",
                "tend.trigger_kinds entries must not include surrounding whitespace",
            )
        if kind in seen:
            return ValidationResult.reject(
                "INVALID_TEND_TRIGGER_KINDS",
                "tend.trigger_kinds must not contain duplicates",
            )
        if kind not in TEND_TRIGGER_KINDS:
            return ValidationResult.reject(
                "INVALID_TEND_TRIGGER_KIND",
                f"unknown tend trigger kind: {kind!r}",
            )
        seen.add(kind)

    if "trigger_goal" in raw and not _ID_PATTERN.match(str(raw["trigger_goal"])):
        return ValidationResult.reject(
            "INVALID_TEND_TRIGGER_GOAL",
            f"trigger_goal must be a valid goal id, got: {raw['trigger_goal']!r}",
        )

    if "trigger_run" in raw and not _RUN_ID_PATTERN.match(str(raw["trigger_run"])):
        return ValidationResult.reject(
            "INVALID_TEND_TRIGGER_RUN",
            f"trigger_run must be a valid run id, got: {raw['trigger_run']!r}",
        )

    return ValidationResult.accept()


def _validate_retrospective_payload(data: dict) -> ValidationResult:
    raw = data.get("retrospective")
    if raw is None:
        return ValidationResult.accept()

    if data.get("type") != "evaluate":
        return ValidationResult.reject(
            "RETROSPECTIVE_REQUIRES_EVALUATE",
            "retrospective payloads are only valid on evaluate goals",
        )

    if not isinstance(raw, dict):
        return ValidationResult.reject(
            "INVALID_RETROSPECTIVE_PAYLOAD",
            "retrospective must be a JSON object",
        )

    result = _validate_known_fields(
        raw,
        allowed=_RETROSPECTIVE_FIELDS,
        reason="UNKNOWN_RETROSPECTIVE_FIELD",
        label="retrospective payload",
    )
    if not result.ok:
        return result

    if "window" not in raw:
        return ValidationResult.reject(
            "MISSING_RETROSPECTIVE_WINDOW",
            "retrospective.window is required",
        )
    if raw["window"] not in RETROSPECTIVE_WINDOW_MODES:
        return ValidationResult.reject(
            "INVALID_RETROSPECTIVE_WINDOW",
            (
                "retrospective.window must be one of "
                f"{sorted(RETROSPECTIVE_WINDOW_MODES)}, got: {raw['window']!r}"
            ),
        )

    if "recent_run_limit" not in raw:
        return ValidationResult.reject(
            "MISSING_RETROSPECTIVE_RECENT_RUN_LIMIT",
            "retrospective.recent_run_limit is required",
        )
    recent_run_limit = raw["recent_run_limit"]
    if (
        not isinstance(recent_run_limit, int)
        or isinstance(recent_run_limit, bool)
        or not (
            MIN_RETROSPECTIVE_RECENT_RUN_LIMIT
            <= recent_run_limit
            <= MAX_RETROSPECTIVE_RECENT_RUN_LIMIT
        )
    ):
        return ValidationResult.reject(
            "INVALID_RETROSPECTIVE_RECENT_RUN_LIMIT",
            (
                "retrospective.recent_run_limit must be an integer "
                f"{MIN_RETROSPECTIVE_RECENT_RUN_LIMIT}-"
                f"{MAX_RETROSPECTIVE_RECENT_RUN_LIMIT}, "
                f"got: {recent_run_limit!r}"
            ),
        )

    if "action_boundary" not in raw:
        return ValidationResult.reject(
            "MISSING_RETROSPECTIVE_ACTION_BOUNDARY",
            "retrospective.action_boundary is required",
        )
    if raw["action_boundary"] not in RETROSPECTIVE_ACTION_BOUNDARIES:
        return ValidationResult.reject(
            "INVALID_RETROSPECTIVE_ACTION_BOUNDARY",
            (
                "retrospective.action_boundary must be one of "
                f"{sorted(RETROSPECTIVE_ACTION_BOUNDARIES)}, "
                f"got: {raw['action_boundary']!r}"
            ),
        )

    return ValidationResult.accept()


def _validate_plant_commission_payload(data: dict) -> ValidationResult:
    raw = data.get("plant_commission")
    if raw is None:
        return ValidationResult.accept()

    if data.get("type") != PLANT_COMMISSION_GOAL_TYPE:
        return ValidationResult.reject(
            "PLANT_COMMISSION_REQUIRES_BUILD",
            "plant_commission payloads are only valid on build goals",
        )

    if data.get("assigned_to") != PLANT_COMMISSION_ASSIGNED_TO:
        return ValidationResult.reject(
            "PLANT_COMMISSION_REQUIRES_GARDENER",
            "plant_commission goals must be assigned to gardener",
        )

    if not isinstance(raw, dict):
        return ValidationResult.reject(
            "INVALID_PLANT_COMMISSION_PAYLOAD",
            "plant_commission must be a JSON object",
        )

    result = _validate_known_fields(
        raw,
        allowed=_PLANT_COMMISSION_FIELDS,
        reason="UNKNOWN_PLANT_COMMISSION_FIELD",
        label="plant_commission payload",
    )
    if not result.ok:
        return result

    plant_name = str(raw.get("plant_name", "")).strip()
    if not plant_name:
        return ValidationResult.reject(
            "MISSING_PLANT_COMMISSION_PLANT_NAME",
            "plant_commission.plant_name is required",
        )
    if not _ACTOR_PATTERN.match(plant_name):
        return ValidationResult.reject(
            "INVALID_PLANT_COMMISSION_PLANT_NAME",
            (
                "plant_commission.plant_name must be lowercase alphanumeric "
                f"with hyphens, starting with a letter, got: {raw['plant_name']!r}"
            ),
        )

    seed = str(raw.get("seed", "")).strip()
    if not seed:
        return ValidationResult.reject(
            "MISSING_PLANT_COMMISSION_SEED",
            "plant_commission.seed is required",
        )
    if not _ACTOR_PATTERN.match(seed):
        return ValidationResult.reject(
            "INVALID_PLANT_COMMISSION_SEED",
            (
                "plant_commission.seed must be lowercase alphanumeric "
                f"with hyphens, starting with a letter, got: {raw['seed']!r}"
            ),
        )

    if "initial_goal" not in raw:
        return ValidationResult.reject(
            "MISSING_PLANT_COMMISSION_INITIAL_GOAL",
            "plant_commission.initial_goal is required",
        )

    initial_goal = raw["initial_goal"]
    if not isinstance(initial_goal, dict):
        return ValidationResult.reject(
            "INVALID_PLANT_COMMISSION_INITIAL_GOAL",
            "plant_commission.initial_goal must be a JSON object",
        )

    result = _validate_known_fields(
        initial_goal,
        allowed=_PLANT_COMMISSION_INITIAL_GOAL_FIELDS,
        reason="UNKNOWN_PLANT_COMMISSION_INITIAL_GOAL_FIELD",
        label="plant_commission.initial_goal payload",
    )
    if not result.ok:
        return result

    if "type" not in initial_goal:
        return ValidationResult.reject(
            "MISSING_PLANT_COMMISSION_INITIAL_GOAL_TYPE",
            "plant_commission.initial_goal.type is required",
        )
    goal_type = initial_goal["type"]
    if goal_type not in PLANT_COMMISSION_INITIAL_GOAL_TYPES:
        return ValidationResult.reject(
            "INVALID_PLANT_COMMISSION_INITIAL_GOAL_TYPE",
            (
                "plant_commission.initial_goal.type must be one of "
                f"{sorted(PLANT_COMMISSION_INITIAL_GOAL_TYPES)}, got: {goal_type!r}"
            ),
        )

    if not str(initial_goal.get("body", "")).strip():
        return ValidationResult.reject(
            "MISSING_PLANT_COMMISSION_INITIAL_GOAL_BODY",
            "plant_commission.initial_goal.body must not be empty",
        )

    if "priority" in initial_goal:
        priority = initial_goal["priority"]
        if (
            not isinstance(priority, int)
            or isinstance(priority, bool)
            or not (1 <= priority <= 10)
        ):
            return ValidationResult.reject(
                "INVALID_PLANT_COMMISSION_INITIAL_GOAL_PRIORITY",
                (
                    "plant_commission.initial_goal.priority must be an integer "
                    f"1-10, got: {priority!r}"
                ),
            )

    if "reasoning_effort" in initial_goal:
        effort = initial_goal["reasoning_effort"]
        if effort not in REASONING_EFFORTS:
            return ValidationResult.reject(
                "INVALID_PLANT_COMMISSION_INITIAL_GOAL_REASONING_EFFORT",
                (
                    "plant_commission.initial_goal.reasoning_effort must be one "
                    f"of {sorted(REASONING_EFFORTS)}, got: {effort!r}"
                ),
            )

    return ValidationResult.accept()


def _validate_conversation_reference(data: dict, *,
                                     allowed: set[str],
                                     required: tuple[str, ...],
                                     reason: str,
                                     label: str) -> ValidationResult:
    if not isinstance(data, dict):
        return ValidationResult.reject(reason, f"{label} must be a JSON object")
    result = _validate_known_fields(
        data,
        allowed=allowed,
        reason=reason,
        label=label,
    )
    if not result.ok:
        return result
    for field in required:
        if field not in data:
            return ValidationResult.reject(
                reason,
                f"{label} missing required field: {field}",
            )
    if data.get("kind") != "conversation":
        return ValidationResult.reject(
            reason,
            f"{label}.kind must be 'conversation'",
        )
    conversation_id = data.get("conversation_id")
    if not _ID_PATTERN.match(str(conversation_id)):
        return ValidationResult.reject(
            reason,
            (
                f"{label}.conversation_id must be a valid conversation id, "
                f"got: {conversation_id!r}"
            ),
        )
    if "message_id" in data and not _MESSAGE_ID_PATTERN.match(str(data["message_id"])):
        return ValidationResult.reject(
            reason,
            f"{label}.message_id must be a valid message id, got: {data['message_id']!r}",
        )
    if "ts" in data and not _ISO8601_PATTERN.match(str(data["ts"])):
        return ValidationResult.reject(
            reason,
            f"{label}.ts must be ISO 8601 UTC, got: {data['ts']!r}",
        )
    return ValidationResult.accept()


def validate_active_threads(data: dict) -> ValidationResult:
    """Validate a plant-local active-threads artifact."""
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_ACTIVE_THREADS_SHAPE",
            "Active threads artifact must be a JSON object",
        )
    result = _validate_known_fields(
        data,
        allowed=_ACTIVE_THREADS_FIELDS,
        reason="UNKNOWN_ACTIVE_THREADS_FIELD",
        label="active threads artifact",
    )
    if not result.ok:
        return result

    for field in (
        "schema_version",
        "captured_at",
        "captured_by_run",
        "plant",
        "summary",
        "threads",
        "recent_updates",
    ):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"Missing required field: {field}",
            )

    if data["schema_version"] != 1:
        return ValidationResult.reject(
            "INVALID_ACTIVE_THREADS_SCHEMA_VERSION",
            f"schema_version must be 1, got: {data['schema_version']!r}",
        )
    if not _ISO8601_PATTERN.match(str(data["captured_at"])):
        return ValidationResult.reject(
            "INVALID_TIMESTAMP",
            f"captured_at must be ISO 8601 UTC, got: {data['captured_at']!r}",
        )
    if not _RUN_ID_PATTERN.match(str(data["captured_by_run"])):
        return ValidationResult.reject(
            "INVALID_ACTIVE_THREADS_RUN",
            f"captured_by_run must be a valid run id, got: {data['captured_by_run']!r}",
        )
    if not _ACTOR_PATTERN.match(str(data["plant"])):
        return ValidationResult.reject(
            "INVALID_ACTIVE_THREADS_PLANT",
            f"plant must be lowercase alphanumeric with hyphens, got: {data['plant']!r}",
        )
    if not _is_nonempty_string(data["summary"]):
        return ValidationResult.reject(
            "EMPTY_ACTIVE_THREADS_SUMMARY",
            "summary must not be empty",
        )
    if not isinstance(data["threads"], list):
        return ValidationResult.reject(
            "INVALID_ACTIVE_THREADS_SHAPE",
            "threads must be a list",
        )
    if not isinstance(data["recent_updates"], list):
        return ValidationResult.reject(
            "INVALID_ACTIVE_THREADS_SHAPE",
            "recent_updates must be a list",
        )

    known_thread_ids: list[str] = []
    for index, thread in enumerate(data["threads"]):
        label = f"thread[{index}]"
        if not isinstance(thread, dict):
            return ValidationResult.reject(
                "INVALID_ACTIVE_THREADS_THREAD",
                f"{label} must be a JSON object",
            )
        result = _validate_known_fields(
            thread,
            allowed=_ACTIVE_THREAD_FIELDS,
            reason="UNKNOWN_ACTIVE_THREADS_THREAD_FIELD",
            label=label,
        )
        if not result.ok:
            return result
        for field in (
            "id",
            "title",
            "state",
            "priority",
            "last_changed_at",
            "summary",
            "current_focus",
            "next_step",
            "related_thread_ids",
            "evidence",
        ):
            if field not in thread:
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"{label} missing required field: {field}",
                )
        thread_id = thread["id"]
        if not re.match(r"^[a-z0-9-]+$", str(thread_id)):
            return ValidationResult.reject(
                "INVALID_ACTIVE_THREADS_THREAD_ID",
                f"{label}.id must be lowercase alphanumeric with hyphens, got: {thread_id!r}",
            )
        if thread_id in known_thread_ids:
            return ValidationResult.reject(
                "DUPLICATE_ACTIVE_THREAD_ID",
                f"duplicate thread id: {thread_id}",
            )
        known_thread_ids.append(thread_id)
        for field in ("title", "summary", "current_focus", "next_step"):
            if not _is_nonempty_string(thread[field]):
                return ValidationResult.reject(
                    "EMPTY_ACTIVE_THREADS_THREAD_FIELD",
                    f"{label}.{field} must not be empty",
                )
        if thread["state"] not in ACTIVE_THREAD_STATES:
            return ValidationResult.reject(
                "INVALID_ACTIVE_THREADS_THREAD_STATE",
                f"{label}.state must be one of {sorted(ACTIVE_THREAD_STATES)}, got: {thread['state']!r}",
            )
        if thread["priority"] not in ACTIVE_THREAD_PRIORITIES:
            return ValidationResult.reject(
                "INVALID_ACTIVE_THREADS_THREAD_PRIORITY",
                (
                    f"{label}.priority must be one of "
                    f"{sorted(ACTIVE_THREAD_PRIORITIES)}, got: {thread['priority']!r}"
                ),
            )
        if not _ISO8601_PATTERN.match(str(thread["last_changed_at"])):
            return ValidationResult.reject(
                "INVALID_TIMESTAMP",
                f"{label}.last_changed_at must be ISO 8601 UTC, got: {thread['last_changed_at']!r}",
            )
        result = _validate_nonempty_string_list(
            thread["related_thread_ids"],
            reason="INVALID_ACTIVE_THREADS_THREAD_RELATION",
            label=f"{label}.related_thread_ids",
        )
        if not result.ok:
            return result
        result = _validate_nonempty_string_list(
            thread["evidence"],
            reason="INVALID_ACTIVE_THREADS_THREAD_EVIDENCE",
            label=f"{label}.evidence",
            min_items=1,
        )
        if not result.ok:
            return result

    known_thread_id_set = set(known_thread_ids)
    for thread in data["threads"]:
        thread_id = thread["id"]
        for related_id in thread["related_thread_ids"]:
            if related_id == thread_id:
                return ValidationResult.reject(
                    "SELF_REFERENTIAL_ACTIVE_THREAD",
                    f"thread {thread_id!r} may not relate to itself",
                )
            if related_id not in known_thread_id_set:
                return ValidationResult.reject(
                    "UNKNOWN_ACTIVE_THREAD_RELATION",
                    f"thread {thread_id!r} references unknown related thread {related_id!r}",
                )

    for index, update in enumerate(data["recent_updates"]):
        label = f"recent_updates[{index}]"
        if not isinstance(update, dict):
            return ValidationResult.reject(
                "INVALID_ACTIVE_THREADS_UPDATE",
                f"{label} must be a JSON object",
            )
        result = _validate_known_fields(
            update,
            allowed=_ACTIVE_THREAD_UPDATE_FIELDS,
            reason="UNKNOWN_ACTIVE_THREADS_UPDATE_FIELD",
            label=label,
        )
        if not result.ok:
            return result
        for field in ("ts", "summary", "thread_ids", "evidence"):
            if field not in update:
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"{label} missing required field: {field}",
                )
        if not _ISO8601_PATTERN.match(str(update["ts"])):
            return ValidationResult.reject(
                "INVALID_TIMESTAMP",
                f"{label}.ts must be ISO 8601 UTC, got: {update['ts']!r}",
            )
        if not _is_nonempty_string(update["summary"]):
            return ValidationResult.reject(
                "EMPTY_ACTIVE_THREADS_UPDATE_SUMMARY",
                f"{label}.summary must not be empty",
            )
        result = _validate_nonempty_string_list(
            update["thread_ids"],
            reason="INVALID_ACTIVE_THREADS_UPDATE_THREAD_IDS",
            label=f"{label}.thread_ids",
            min_items=1,
        )
        if not result.ok:
            return result
        for thread_id in update["thread_ids"]:
            if not re.match(r"^[a-z0-9-]+$", str(thread_id)):
                return ValidationResult.reject(
                    "INVALID_ACTIVE_THREADS_UPDATE_THREAD_IDS",
                    (
                        f"{label}.thread_ids entries must be lowercase alphanumeric "
                        f"with hyphens, got: {thread_id!r}"
                    ),
                )
        result = _validate_nonempty_string_list(
            update["evidence"],
            reason="INVALID_ACTIVE_THREADS_UPDATE_EVIDENCE",
            label=f"{label}.evidence",
            min_items=1,
        )
        if not result.ok:
            return result

    return ValidationResult.accept()


def validate_initiative_record(data: dict) -> ValidationResult:
    """Validate a plant-local initiative record."""
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_SHAPE",
            "Initiative record must be a JSON object",
        )
    result = _validate_known_fields(
        data,
        allowed=_INITIATIVE_FIELDS,
        reason="UNKNOWN_INITIATIVE_FIELD",
        label="initiative record",
    )
    if not result.ok:
        return result

    for field in (
        "schema_version",
        "id",
        "plant",
        "title",
        "status",
        "approved_by",
        "objective",
        "scope_boundary",
        "non_goals",
        "success_checks",
        "budget_policy",
        "tranches",
        "current_tranche_id",
        "next_authorized_step",
        "ledger",
        "updated_at",
        "updated_by_run",
    ):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"Missing required field: {field}",
            )

    if data["schema_version"] != 1:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_SCHEMA_VERSION",
            f"schema_version must be 1, got: {data['schema_version']!r}",
        )
    if not re.match(r"^[a-z0-9-]+$", str(data["id"])):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_ID",
            f"id must be lowercase alphanumeric with hyphens, got: {data['id']!r}",
        )
    if not _ACTOR_PATTERN.match(str(data["plant"])):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_PLANT",
            f"plant must be lowercase alphanumeric with hyphens, got: {data['plant']!r}",
        )
    for field in ("title", "objective", "scope_boundary"):
        if not _is_nonempty_string(data[field]):
            return ValidationResult.reject(
                "EMPTY_INITIATIVE_FIELD",
                f"{field} must not be empty",
            )
    if data["status"] not in INITIATIVE_STATUSES:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_STATUS",
            f"status must be one of {sorted(INITIATIVE_STATUSES)}, got: {data['status']!r}",
        )
    if not _ISO8601_PATTERN.match(str(data["updated_at"])):
        return ValidationResult.reject(
            "INVALID_TIMESTAMP",
            f"updated_at must be ISO 8601 UTC, got: {data['updated_at']!r}",
        )
    if not _RUN_ID_PATTERN.match(str(data["updated_by_run"])):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_RUN",
            f"updated_by_run must be a valid run id, got: {data['updated_by_run']!r}",
        )

    result = _validate_conversation_reference(
        data["approved_by"],
        allowed=_INITIATIVE_APPROVAL_FIELDS,
        required=("kind", "conversation_id", "message_id", "ts"),
        reason="INVALID_INITIATIVE_APPROVAL_SOURCE",
        label="approved_by",
    )
    if not result.ok:
        return result

    result = _validate_nonempty_string_list(
        data["non_goals"],
        reason="INVALID_INITIATIVE_STRING_LIST",
        label="non_goals",
        min_items=1,
    )
    if not result.ok:
        return result
    result = _validate_nonempty_string_list(
        data["success_checks"],
        reason="INVALID_INITIATIVE_STRING_LIST",
        label="success_checks",
        min_items=1,
    )
    if not result.ok:
        return result

    budget_policy = data["budget_policy"]
    if not isinstance(budget_policy, dict):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_BUDGET_POLICY",
            "budget_policy must be a JSON object",
        )
    result = _validate_known_fields(
        budget_policy,
        allowed=_INITIATIVE_BUDGET_POLICY_FIELDS,
        reason="INVALID_INITIATIVE_BUDGET_POLICY",
        label="budget_policy",
    )
    if not result.ok:
        return result
    if "mode" not in budget_policy:
        return ValidationResult.reject(
            "MISSING_REQUIRED_FIELD",
            "budget_policy missing required field: mode",
        )
    if budget_policy["mode"] not in INITIATIVE_BUDGET_MODES:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_BUDGET_POLICY",
            (
                "budget_policy.mode must be one of "
                f"{sorted(INITIATIVE_BUDGET_MODES)}, got: {budget_policy['mode']!r}"
            ),
        )
    if "notes" in budget_policy and not _is_nonempty_string(budget_policy["notes"]):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_BUDGET_POLICY",
            "budget_policy.notes must be a non-empty string when present",
        )
    ceiling_fields = (
        "max_input_tokens",
        "max_output_tokens",
        "max_cache_read_tokens",
    )
    present_ceiling_fields = [
        field for field in ceiling_fields if field in budget_policy
    ]
    for field in present_ceiling_fields:
        if not _is_nonnegative_int(budget_policy[field]):
            return ValidationResult.reject(
                "INVALID_INITIATIVE_BUDGET_POLICY",
                f"budget_policy.{field} must be a non-negative integer",
            )
    if budget_policy["mode"] == "track_only" and present_ceiling_fields:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_BUDGET_POLICY",
            "track_only budget_policy may not declare max_* ceilings",
        )
    if budget_policy["mode"] == "ceilinged" and not present_ceiling_fields:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_BUDGET_POLICY",
            "ceilinged budget_policy must declare at least one max_* field",
        )

    if not isinstance(data["tranches"], list) or not data["tranches"]:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_TRANCHE",
            "tranches must be a non-empty list",
        )

    tranche_ids: list[str] = []
    tranche_goal_types: dict[str, set[str]] = {}
    active_tranche_ids: list[str] = []
    successor_targets: list[tuple[str, str | None]] = []
    for index, tranche in enumerate(data["tranches"]):
        label = f"tranches[{index}]"
        if not isinstance(tranche, dict):
            return ValidationResult.reject(
                "INVALID_INITIATIVE_TRANCHE",
                f"{label} must be a JSON object",
            )
        result = _validate_known_fields(
            tranche,
            allowed=_INITIATIVE_TRANCHE_FIELDS,
            reason="UNKNOWN_INITIATIVE_TRANCHE_FIELD",
            label=label,
        )
        if not result.ok:
            return result
        for field in (
            "id",
            "title",
            "objective",
            "status",
            "allowed_goal_types",
            "execution_mode",
            "review_policy",
            "stop_rules",
            "successor",
        ):
            if field not in tranche:
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"{label} missing required field: {field}",
                )

        tranche_id = tranche["id"]
        if not re.match(r"^[a-z0-9-]+$", str(tranche_id)):
            return ValidationResult.reject(
                "INVALID_INITIATIVE_TRANCHE_ID",
                f"{label}.id must be lowercase alphanumeric with hyphens, got: {tranche_id!r}",
            )
        if tranche_id in tranche_ids:
            return ValidationResult.reject(
                "DUPLICATE_INITIATIVE_TRANCHE_ID",
                f"duplicate tranche id: {tranche_id}",
            )
        tranche_ids.append(tranche_id)
        if tranche["status"] == "active":
            active_tranche_ids.append(tranche_id)
        for field in ("title", "objective"):
            if not _is_nonempty_string(tranche[field]):
                return ValidationResult.reject(
                    "EMPTY_INITIATIVE_FIELD",
                    f"{label}.{field} must not be empty",
                )
        if tranche["status"] not in INITIATIVE_TRANCHE_STATUSES:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_TRANCHE_STATUS",
                (
                    f"{label}.status must be one of "
                    f"{sorted(INITIATIVE_TRANCHE_STATUSES)}, got: {tranche['status']!r}"
                ),
            )
        allowed_goal_types = tranche["allowed_goal_types"]
        if not isinstance(allowed_goal_types, list) or not allowed_goal_types:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_TRANCHE_GOAL_TYPES",
                f"{label}.allowed_goal_types must be a non-empty list",
            )
        goal_type_set: set[str] = set()
        for goal_type in allowed_goal_types:
            if goal_type not in INITIATIVE_ALLOWED_GOAL_TYPES:
                return ValidationResult.reject(
                    "INVALID_INITIATIVE_TRANCHE_GOAL_TYPES",
                    (
                        f"{label}.allowed_goal_types entries must be one of "
                        f"{sorted(INITIATIVE_ALLOWED_GOAL_TYPES)}, got: {goal_type!r}"
                    ),
                )
            goal_type_set.add(goal_type)
        tranche_goal_types[tranche_id] = goal_type_set
        if tranche["execution_mode"] not in INITIATIVE_EXECUTION_MODES:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_TRANCHE_EXECUTION_MODE",
                (
                    f"{label}.execution_mode must be one of "
                    f"{sorted(INITIATIVE_EXECUTION_MODES)}, got: {tranche['execution_mode']!r}"
                ),
            )
        if tranche["review_policy"] not in INITIATIVE_REVIEW_POLICIES:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_TRANCHE_REVIEW_POLICY",
                (
                    f"{label}.review_policy must be one of "
                    f"{sorted(INITIATIVE_REVIEW_POLICIES)}, got: {tranche['review_policy']!r}"
                ),
            )
        result = _validate_nonempty_string_list(
            tranche["stop_rules"],
            reason="INVALID_INITIATIVE_TRANCHE_STOP_RULES",
            label=f"{label}.stop_rules",
            min_items=1,
        )
        if not result.ok:
            return result

        successor = tranche["successor"]
        successor_label = f"{label}.successor"
        if not isinstance(successor, dict):
            return ValidationResult.reject(
                "INVALID_INITIATIVE_SUCCESSOR",
                f"{successor_label} must be a JSON object",
            )
        result = _validate_known_fields(
            successor,
            allowed=_INITIATIVE_SUCCESSOR_FIELDS,
            reason="INVALID_INITIATIVE_SUCCESSOR",
            label=successor_label,
        )
        if not result.ok:
            return result
        for field in ("condition", "next_tranche_id", "summary"):
            if field not in successor:
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"{successor_label} missing required field: {field}",
                )
        if successor["condition"] not in INITIATIVE_SUCCESSOR_CONDITIONS:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_SUCCESSOR",
                (
                    f"{successor_label}.condition must be one of "
                    f"{sorted(INITIATIVE_SUCCESSOR_CONDITIONS)}, got: {successor['condition']!r}"
                ),
            )
        if not _is_nonempty_string(successor["summary"]):
            return ValidationResult.reject(
                "INVALID_INITIATIVE_SUCCESSOR",
                f"{successor_label}.summary must be a non-empty string",
            )
        next_tranche_id = successor["next_tranche_id"]
        if next_tranche_id is not None and not re.match(r"^[a-z0-9-]+$", str(next_tranche_id)):
            return ValidationResult.reject(
                "INVALID_INITIATIVE_SUCCESSOR",
                (
                    f"{successor_label}.next_tranche_id must be null or lowercase "
                    f"alphanumeric with hyphens, got: {next_tranche_id!r}"
                ),
            )
        if successor["condition"] == "initiative_complete_after_clean_review":
            if next_tranche_id is not None:
                return ValidationResult.reject(
                    "INVALID_INITIATIVE_SUCCESSOR",
                    (
                        f"{successor_label}.next_tranche_id must be null when "
                        "condition is initiative_complete_after_clean_review"
                    ),
                )
        else:
            if next_tranche_id is None:
                return ValidationResult.reject(
                    "INVALID_INITIATIVE_SUCCESSOR",
                    (
                        f"{successor_label}.next_tranche_id must be present when "
                        "condition names a successor tranche"
                    ),
                )
        successor_targets.append((tranche_id, next_tranche_id))

    tranche_id_set = set(tranche_ids)
    for tranche_id, next_tranche_id in successor_targets:
        if next_tranche_id is None:
            continue
        if next_tranche_id == tranche_id:
            return ValidationResult.reject(
                "SELF_REFERENTIAL_INITIATIVE_SUCCESSOR",
                f"tranche {tranche_id!r} may not name itself as successor",
            )
        if next_tranche_id not in tranche_id_set:
            return ValidationResult.reject(
                "UNKNOWN_INITIATIVE_SUCCESSOR_TRANCHE",
                f"tranche {tranche_id!r} references unknown successor tranche {next_tranche_id!r}",
            )

    if len(active_tranche_ids) > 1:
        return ValidationResult.reject(
            "MULTIPLE_ACTIVE_INITIATIVE_TRANCHES",
            f"initiative may not have more than one active tranche, got: {active_tranche_ids!r}",
        )

    current_tranche_id = data["current_tranche_id"]
    if current_tranche_id is not None and not re.match(r"^[a-z0-9-]+$", str(current_tranche_id)):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_CURRENT_TRANCHE",
            (
                "current_tranche_id must be null or lowercase alphanumeric "
                f"with hyphens, got: {current_tranche_id!r}"
            ),
        )
    if current_tranche_id is not None and current_tranche_id not in tranche_id_set:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_CURRENT_TRANCHE",
            f"current_tranche_id must reference a known tranche, got: {current_tranche_id!r}",
        )
    if data["status"] in {"active", "paused", "blocked"}:
        if current_tranche_id is None:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_CURRENT_TRANCHE",
                f"status {data['status']!r} requires current_tranche_id",
            )
        if len(active_tranche_ids) != 1 or active_tranche_ids[0] != current_tranche_id:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_CURRENT_TRANCHE",
                (
                    "current_tranche_id must match the single active tranche when "
                    f"status is {data['status']!r}"
                ),
            )
    else:
        if current_tranche_id is not None:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_CURRENT_TRANCHE",
                f"status {data['status']!r} may not declare current_tranche_id",
            )
        if active_tranche_ids:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_CURRENT_TRANCHE",
                (
                    f"status {data['status']!r} may not keep an active tranche, "
                    f"got: {active_tranche_ids!r}"
                ),
            )

    next_authorized_step = data["next_authorized_step"]
    if not isinstance(next_authorized_step, dict):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_NEXT_STEP",
            "next_authorized_step must be a JSON object",
        )
    result = _validate_known_fields(
        next_authorized_step,
        allowed=_INITIATIVE_NEXT_STEP_FIELDS,
        reason="INVALID_INITIATIVE_NEXT_STEP",
        label="next_authorized_step",
    )
    if not result.ok:
        return result
    for field in (
        "tranche_id",
        "status",
        "goal_type",
        "summary",
        "may_start_bounded_campaign",
        "stop_after",
    ):
        if field not in next_authorized_step:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"next_authorized_step missing required field: {field}",
            )
    next_step_tranche_id = next_authorized_step["tranche_id"]
    if not re.match(r"^[a-z0-9-]+$", str(next_step_tranche_id)):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_NEXT_STEP",
            (
                "next_authorized_step.tranche_id must be lowercase alphanumeric "
                f"with hyphens, got: {next_step_tranche_id!r}"
            ),
        )
    if next_step_tranche_id not in tranche_id_set:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_NEXT_STEP",
            (
                "next_authorized_step.tranche_id must reference a known tranche, "
                f"got: {next_step_tranche_id!r}"
            ),
        )
    if next_authorized_step["status"] not in INITIATIVE_NEXT_STEP_STATUSES:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_NEXT_STEP",
            (
                "next_authorized_step.status must be one of "
                f"{sorted(INITIATIVE_NEXT_STEP_STATUSES)}, got: {next_authorized_step['status']!r}"
            ),
        )
    if next_authorized_step["goal_type"] not in INITIATIVE_ALLOWED_GOAL_TYPES:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_NEXT_STEP",
            (
                "next_authorized_step.goal_type must be one of "
                f"{sorted(INITIATIVE_ALLOWED_GOAL_TYPES)}, got: {next_authorized_step['goal_type']!r}"
            ),
        )
    for field in ("summary", "stop_after"):
        if not _is_nonempty_string(next_authorized_step[field]):
            return ValidationResult.reject(
                "INVALID_INITIATIVE_NEXT_STEP",
                f"next_authorized_step.{field} must be a non-empty string",
            )
    if not isinstance(next_authorized_step["may_start_bounded_campaign"], bool):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_NEXT_STEP",
            "next_authorized_step.may_start_bounded_campaign must be a boolean",
        )
    if current_tranche_id is not None and next_step_tranche_id != current_tranche_id:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_NEXT_STEP",
            (
                "next_authorized_step.tranche_id must match current_tranche_id while "
                "the initiative is active"
            ),
        )
    if next_authorized_step["goal_type"] not in tranche_goal_types[next_step_tranche_id]:
        return ValidationResult.reject(
            "INVALID_INITIATIVE_NEXT_STEP",
            (
                "next_authorized_step.goal_type must be allowed by its tranche, "
                f"got: {next_authorized_step['goal_type']!r}"
            ),
        )
    tranche_by_id = {
        tranche["id"]: tranche
        for tranche in data["tranches"]
    }
    if (
        next_authorized_step["may_start_bounded_campaign"]
        and tranche_by_id[next_step_tranche_id]["execution_mode"] != "bounded_campaign_optional"
    ):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_NEXT_STEP",
            (
                "next_authorized_step may not authorize bounded campaign mode when "
                "its tranche execution_mode does not permit it"
            ),
        )

    ledger = data["ledger"]
    if not isinstance(ledger, dict):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_LEDGER",
            "ledger must be a JSON object",
        )
    result = _validate_known_fields(
        ledger,
        allowed=_INITIATIVE_LEDGER_FIELDS,
        reason="INVALID_INITIATIVE_LEDGER",
        label="ledger",
    )
    if not result.ok:
        return result
    for field in ("goal_ids", "run_ids", "totals"):
        if field not in ledger:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"ledger missing required field: {field}",
            )
    if not isinstance(ledger["goal_ids"], list):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_LEDGER",
            "ledger.goal_ids must be a list",
        )
    if not isinstance(ledger["run_ids"], list):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_LEDGER",
            "ledger.run_ids must be a list",
        )
    seen_goal_ids: set[str] = set()
    for goal_id in ledger["goal_ids"]:
        if not _ID_PATTERN.match(str(goal_id)):
            return ValidationResult.reject(
                "INVALID_INITIATIVE_LEDGER",
                f"ledger.goal_ids entries must be valid goal ids, got: {goal_id!r}",
            )
        if goal_id in seen_goal_ids:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_LEDGER",
                f"ledger.goal_ids may not contain duplicates, got: {goal_id!r}",
            )
        seen_goal_ids.add(goal_id)
    seen_run_ids: set[str] = set()
    for run_id in ledger["run_ids"]:
        if not _RUN_ID_PATTERN.match(str(run_id)):
            return ValidationResult.reject(
                "INVALID_INITIATIVE_LEDGER",
                f"ledger.run_ids entries must be valid run ids, got: {run_id!r}",
            )
        if run_id in seen_run_ids:
            return ValidationResult.reject(
                "INVALID_INITIATIVE_LEDGER",
                f"ledger.run_ids may not contain duplicates, got: {run_id!r}",
            )
        seen_run_ids.add(run_id)
    totals = ledger["totals"]
    if not isinstance(totals, dict):
        return ValidationResult.reject(
            "INVALID_INITIATIVE_LEDGER_TOTALS",
            "ledger.totals must be a JSON object",
        )
    result = _validate_known_fields(
        totals,
        allowed=_INITIATIVE_LEDGER_TOTAL_FIELDS,
        reason="INVALID_INITIATIVE_LEDGER_TOTALS",
        label="ledger.totals",
    )
    if not result.ok:
        return result
    for field in ("input_tokens", "output_tokens", "cache_read_tokens"):
        if field not in totals:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"ledger.totals missing required field: {field}",
            )
        if not _is_nonnegative_int(totals[field]):
            return ValidationResult.reject(
                "INVALID_INITIATIVE_LEDGER_TOTALS",
                f"ledger.totals.{field} must be a non-negative integer",
            )

    return ValidationResult.accept()


def validate_goal_supplement(data: dict) -> ValidationResult:
    """Validate a persisted goal supplement record."""
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "Goal supplement must be a JSON object",
        )
    result = _validate_known_fields(
        data,
        allowed=_GOAL_SUPPLEMENT_FIELDS,
        reason="UNKNOWN_SUPPLEMENT_FIELD",
        label="goal supplement",
    )
    if not result.ok:
        return result
    for field in ("id", "goal", "ts", "actor", "source", "kind", "content"):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"Missing required field: {field}",
            )
    if not _SUPPLEMENT_ID_PATTERN.match(str(data["id"])):
        return ValidationResult.reject(
            "INVALID_SUPPLEMENT_ID",
            f"id must be a valid supplement id, got: {data['id']!r}",
        )
    if not _ID_PATTERN.match(str(data["goal"])):
        return ValidationResult.reject(
            "INVALID_SUPPLEMENT_GOAL",
            f"goal must be a valid goal id, got: {data['goal']!r}",
        )
    if not _ISO8601_PATTERN.match(str(data["ts"])):
        return ValidationResult.reject(
            "INVALID_TIMESTAMP",
            f"ts must be ISO 8601 UTC, got: {data['ts']!r}",
        )
    if not _ACTOR_PATTERN.match(str(data["actor"])):
        return ValidationResult.reject(
            "INVALID_ACTOR",
            f"actor must be lowercase alphanumeric with hyphens, got: {data['actor']!r}",
        )
    result = _validate_conversation_reference(
        data["source"],
        allowed=_SUPPLEMENT_SOURCE_FIELDS,
        required=("kind", "conversation_id"),
        reason="INVALID_SOURCE",
        label="source",
    )
    if not result.ok:
        return result
    if not _is_nonempty_string(data["kind"]):
        return ValidationResult.reject("EMPTY_KIND", "kind must not be empty")
    if not _is_nonempty_string(data["content"]):
        return ValidationResult.reject("EMPTY_CONTENT", "content must not be empty")
    if "source_goal_id" in data and not _ID_PATTERN.match(str(data["source_goal_id"])):
        return ValidationResult.reject(
            "INVALID_SOURCE_GOAL",
            f"source_goal_id must be a valid goal id, got: {data['source_goal_id']!r}",
        )
    if "source_run_id" in data and not _RUN_ID_PATTERN.match(str(data["source_run_id"])):
        return ValidationResult.reject(
            "INVALID_SOURCE_RUN",
            f"source_run_id must be a valid run id, got: {data['source_run_id']!r}",
        )
    return ValidationResult.accept()


def validate_dashboard_invocation(data: dict) -> ValidationResult:
    """Validate a persisted dashboard invocation record."""
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "Dashboard invocation must be a JSON object",
        )

    result = _validate_known_fields(
        data,
        allowed=_DASHBOARD_INVOCATION_FIELDS,
        reason="UNKNOWN_DASHBOARD_INVOCATION_FIELD",
        label="dashboard invocation",
    )
    if not result.ok:
        return result

    for field in (
        "id",
        "actor",
        "started_at",
        "completed_at",
        "root",
        "mode",
        "refresh_seconds",
        "tty",
        "render_count",
        "outcome",
        "cost",
    ):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"Missing required field: {field}",
            )

    if not _DASHBOARD_INVOCATION_ID_PATTERN.match(str(data["id"])):
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            f"id must be a valid dashboard invocation id, got: {data['id']!r}",
        )
    if not _ACTOR_PATTERN.match(str(data["actor"])):
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            f"actor must be lowercase alphanumeric with hyphens, got: {data['actor']!r}",
        )
    for field in ("source_goal_id", "source_run_id"):
        value = data.get(field)
        if value is None:
            continue
        pattern = _ID_PATTERN if field == "source_goal_id" else _RUN_ID_PATTERN
        if not pattern.match(str(value)):
            return ValidationResult.reject(
                "INVALID_DASHBOARD_INVOCATION",
                f"{field} must be a valid identifier, got: {value!r}",
            )
    for field in ("started_at", "completed_at"):
        if not _ISO8601_PATTERN.match(str(data[field])):
            return ValidationResult.reject(
                "INVALID_TIMESTAMP",
                f"{field} must be ISO 8601 UTC, got: {data[field]!r}",
            )
    if not _is_nonempty_string(data["root"]):
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            "root must be a non-empty string",
        )
    if data["mode"] not in DASHBOARD_MODES:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            f"mode must be one of {sorted(DASHBOARD_MODES)}, got: {data['mode']!r}",
        )
    if not _is_number(data["refresh_seconds"]) or data["refresh_seconds"] <= 0:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            f"refresh_seconds must be a number greater than 0, got: {data['refresh_seconds']!r}",
        )
    if not isinstance(data["tty"], bool):
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            f"tty must be a boolean, got: {data['tty']!r}",
        )
    if not _is_nonnegative_int(data["render_count"]):
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            f"render_count must be a non-negative integer, got: {data['render_count']!r}",
        )
    if data["outcome"] not in DASHBOARD_INVOCATION_OUTCOMES:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            (
                "outcome must be one of "
                f"{sorted(DASHBOARD_INVOCATION_OUTCOMES)}, got: {data['outcome']!r}"
            ),
        )
    if data["outcome"] == "success" and data["render_count"] < 1:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            "successful dashboard invocations must render at least once",
        )

    error_detail = data.get("error_detail")
    if data["outcome"] == "failure":
        if not _is_nonempty_string(error_detail):
            return ValidationResult.reject(
                "INVALID_DASHBOARD_INVOCATION",
                "error_detail is required when outcome is 'failure'",
            )
    elif error_detail is not None:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            "error_detail is only allowed when outcome is 'failure'",
        )

    cost = data["cost"]
    if not isinstance(cost, dict):
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            "cost must be a JSON object",
        )
    result = _validate_known_fields(
        cost,
        allowed=_DASHBOARD_INVOCATION_COST_FIELDS,
        reason="INVALID_DASHBOARD_INVOCATION",
        label="dashboard invocation.cost",
    )
    if not result.ok:
        return result
    for field in ("source", "wall_ms"):
        if field not in cost:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"Missing required field: cost.{field}",
            )
    if cost["source"] not in DASHBOARD_COST_SOURCES:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            (
                "cost.source must be one of "
                f"{sorted(DASHBOARD_COST_SOURCES)}, got: {cost['source']!r}"
            ),
        )
    if not _is_nonnegative_int(cost["wall_ms"]):
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            f"cost.wall_ms must be a non-negative integer, got: {cost['wall_ms']!r}",
        )

    started_at = _parse_iso8601(str(data["started_at"]))
    completed_at = _parse_iso8601(str(data["completed_at"]))
    if started_at is None or completed_at is None:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            "started_at and completed_at must be parseable ISO 8601 timestamps",
        )
    if completed_at < started_at:
        return ValidationResult.reject(
            "INVALID_DASHBOARD_INVOCATION",
            "completed_at must be at or after started_at",
        )

    return ValidationResult.accept()


def validate_dispatch_packet(data: dict) -> ValidationResult:
    """Validate a persisted dispatch packet."""
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "Dispatch packet must be a JSON object",
        )
    result = _validate_known_fields(
        data,
        allowed=_DISPATCH_PACKET_FIELDS,
        reason="INVALID_DISPATCH_PACKET",
        label="dispatch packet",
    )
    if not result.ok:
        return result
    for field in (
        "goal_id",
        "run_id",
        "cutoff",
        "origin",
        "goal_body",
        "supplement_count",
        "supplement_chars",
        "supplements",
    ):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"Missing required field: {field}",
            )
    if not _ID_PATTERN.match(str(data["goal_id"])):
        return ValidationResult.reject(
            "INVALID_DISPATCH_PACKET",
            f"goal_id must be a valid goal id, got: {data['goal_id']!r}",
        )
    if not _RUN_ID_PATTERN.match(str(data["run_id"])):
        return ValidationResult.reject(
            "INVALID_DISPATCH_PACKET",
            f"run_id must be a valid run id, got: {data['run_id']!r}",
        )
    if not _ISO8601_PATTERN.match(str(data["cutoff"])):
        return ValidationResult.reject(
            "INVALID_DISPATCH_PACKET",
            f"cutoff must be ISO 8601 UTC, got: {data['cutoff']!r}",
        )
    result = _validate_conversation_reference(
        data["origin"],
        allowed=_DISPATCH_PACKET_ORIGIN_FIELDS,
        required=("kind", "conversation_id", "ts"),
        reason="INVALID_DISPATCH_PACKET",
        label="dispatch packet.origin",
    )
    if not result.ok:
        return result
    if not _is_nonempty_string(data["goal_body"]):
        return ValidationResult.reject(
            "INVALID_DISPATCH_PACKET",
            "goal_body must be a non-empty string",
        )
    if not _is_nonnegative_int(data["supplement_count"]):
        return ValidationResult.reject(
            "INVALID_DISPATCH_PACKET",
            "supplement_count must be a non-negative integer",
        )
    if not _is_nonnegative_int(data["supplement_chars"]):
        return ValidationResult.reject(
            "INVALID_DISPATCH_PACKET",
            "supplement_chars must be a non-negative integer",
        )
    if not isinstance(data["supplements"], list):
        return ValidationResult.reject(
            "INVALID_DISPATCH_PACKET",
            "supplements must be an array",
        )
    if data["supplement_count"] != len(data["supplements"]):
        return ValidationResult.reject(
            "INVALID_DISPATCH_PACKET",
            (
                "supplement_count must equal the number of supplements, "
                f"got: {data['supplement_count']!r} and {len(data['supplements'])!r}"
            ),
        )

    cutoff_dt = _parse_iso8601(str(data["cutoff"]))
    origin_ts = str(data["origin"]["ts"])
    origin_dt = _parse_iso8601(origin_ts)
    if cutoff_dt is not None and origin_dt is not None and origin_dt > cutoff_dt:
        return ValidationResult.reject(
            "INVALID_DISPATCH_PACKET",
            f"origin.ts must be at or before cutoff, got: {origin_ts!r}",
        )

    expected_goal_id = str(data["goal_id"])
    expected_conversation_id = str(data["origin"]["conversation_id"])
    total_chars = 0
    for index, item in enumerate(data["supplements"]):
        result = validate_goal_supplement(item)
        if not result.ok:
            detail = result.detail or "invalid goal supplement"
            return ValidationResult.reject(
                result.reason or "INVALID_DISPATCH_PACKET",
                f"supplements[{index}]: {detail}",
            )
        if item["goal"] != expected_goal_id:
            return ValidationResult.reject(
                "INVALID_DISPATCH_PACKET",
                (
                    f"supplements[{index}].goal must match goal_id "
                    f"{expected_goal_id!r}, got: {item['goal']!r}"
                ),
            )
        source_conversation_id = item["source"]["conversation_id"]
        if source_conversation_id != expected_conversation_id:
            return ValidationResult.reject(
                "INVALID_DISPATCH_PACKET",
                (
                    f"supplements[{index}].source.conversation_id must match "
                    f"origin.conversation_id {expected_conversation_id!r}, got: "
                    f"{source_conversation_id!r}"
                ),
            )
        supplement_dt = _parse_iso8601(str(item["ts"]))
        if cutoff_dt is not None and supplement_dt is not None and supplement_dt > cutoff_dt:
            return ValidationResult.reject(
                "INVALID_DISPATCH_PACKET",
                f"supplements[{index}].ts must be at or before cutoff, got: {item['ts']!r}",
            )
        total_chars += len(str(item["content"]))

    if data["supplement_chars"] != total_chars:
        return ValidationResult.reject(
            "INVALID_DISPATCH_PACKET",
            (
                "supplement_chars must equal the total content length across "
                f"supplements, got: {data['supplement_chars']!r} and {total_chars!r}"
            ),
        )

    return ValidationResult.accept()


# ---------------------------------------------------------------------------
# Goal
# ---------------------------------------------------------------------------

def validate_goal(data: dict, *, _plants_dir: Path | None = None) -> ValidationResult:
    """Validate a goal record at the submission boundary."""

    if not isinstance(data, dict):
        return ValidationResult.reject("INVALID_SHAPE", "Goal must be a JSON object")

    # Required fields
    for field in ("id", "status", "type", "submitted_at", "submitted_by", "body"):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD", f"Missing required field: {field}"
            )

    # id format
    if not _ID_PATTERN.match(str(data["id"])):
        return ValidationResult.reject(
            "INVALID_ID_FORMAT",
            f"id must be a positive integer followed by a slug (e.g. 1-my-goal), got: {data['id']!r}"
        )

    # status
    if data["status"] not in GOAL_STATUSES:
        return ValidationResult.reject(
            "INVALID_STATUS",
            f"status must be one of {sorted(GOAL_STATUSES)}, got: {data['status']!r}"
        )

    # type
    if data["type"] not in GOAL_TYPES:
        return ValidationResult.reject(
            "INVALID_TYPE",
            f"type must be one of {sorted(GOAL_TYPES)}, got: {data['type']!r}"
        )

    # submitted_at
    if not _ISO8601_PATTERN.match(str(data["submitted_at"])):
        return ValidationResult.reject(
            "INVALID_TIMESTAMP",
            f"submitted_at must be ISO 8601 UTC, got: {data['submitted_at']!r}"
        )

    # submitted_by format
    if not _ACTOR_PATTERN.match(str(data.get("submitted_by", ""))):
        return ValidationResult.reject(
            "INVALID_SUBMITTED_BY",
            f"submitted_by must be lowercase alphanumeric with hyphens, starting with a letter, "
            f"got: {data['submitted_by']!r}"
        )

    # body non-empty
    if not str(data.get("body", "")).strip():
        return ValidationResult.reject("EMPTY_BODY", "body must not be empty")

    # priority range
    if "priority" in data:
        p = data["priority"]
        if not isinstance(p, int) or isinstance(p, bool) or not (1 <= p <= 10):
            return ValidationResult.reject(
                "INVALID_PRIORITY", f"priority must be an integer 1–10, got: {p!r}"
            )

    if "reasoning_effort" in data:
        effort = data["reasoning_effort"]
        if effort not in REASONING_EFFORTS:
            return ValidationResult.reject(
                "INVALID_REASONING_EFFORT",
                f"reasoning_effort must be one of {sorted(REASONING_EFFORTS)}, got: {effort!r}"
            )

    assigned_to = data.get("assigned_to")
    if assigned_to is not None:
        plant_name = str(assigned_to).strip()
        if not plant_name:
            return ValidationResult.reject(
                "UNKNOWN_ASSIGNED_PLANT",
                "assigned_to must name a commissioned plant",
            )
        if _plants_dir is None:
            return ValidationResult.reject(
                "UNKNOWN_ASSIGNED_PLANT",
                f"assigned plant '{plant_name}' is not commissioned",
            )
        plant_meta = _plants_dir / plant_name / "meta.json"
        if not plant_meta.is_file():
            return ValidationResult.reject(
                "UNKNOWN_ASSIGNED_PLANT",
                f"assigned plant '{plant_name}' is not commissioned",
            )
        try:
            plant = json.loads(plant_meta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ValidationResult.reject(
                "UNKNOWN_ASSIGNED_PLANT",
                f"assigned plant '{plant_name}' is not commissioned",
            )
        if not isinstance(plant, dict):
            return ValidationResult.reject(
                "UNKNOWN_ASSIGNED_PLANT",
                f"assigned plant '{plant_name}' is not commissioned",
            )
        if plant.get("status") != "active":
            return ValidationResult.reject(
                "ASSIGNED_PLANT_INACTIVE",
                f"assigned plant '{plant_name}' is not active",
            )

    if "origin" in data:
        origin_result = _validate_conversation_reference(
            data["origin"],
            allowed=_GOAL_ORIGIN_FIELDS,
            required=("kind", "conversation_id", "ts"),
            reason="INVALID_GOAL_ORIGIN",
            label="goal.origin",
        )
        if not origin_result.ok:
            return origin_result

    if data["type"] == "tend":
        tend_result = _validate_tend_payload(data)
        if not tend_result.ok:
            return tend_result

    retrospective_result = _validate_retrospective_payload(data)
    if not retrospective_result.ok:
        return retrospective_result

    plant_commission_result = _validate_plant_commission_payload(data)
    if not plant_commission_result.ok:
        return plant_commission_result

    # closed_reason — required when closed, forbidden when not closed
    if data["status"] == "closed":
        if "closed_reason" not in data:
            return ValidationResult.reject(
                "MISSING_CLOSED_REASON", "closed_reason is required when status is closed"
            )
        if data["closed_reason"] not in CLOSED_REASONS:
            return ValidationResult.reject(
                "INVALID_CLOSED_REASON",
                f"closed_reason must be one of {sorted(CLOSED_REASONS)}, "
                f"got: {data['closed_reason']!r}"
            )
    else:
        if "closed_reason" in data:
            return ValidationResult.reject(
                "SPURIOUS_CLOSED_REASON",
                f"closed_reason must not be present when status is {data['status']!r}"
            )

    # depends_on format
    if "depends_on" in data:
        if not isinstance(data["depends_on"], list):
            return ValidationResult.reject(
                "INVALID_DEPENDS_ON", "depends_on must be an array"
            )
        for dep in data["depends_on"]:
            if not _ID_PATTERN.match(str(dep)):
                return ValidationResult.reject(
                    "INVALID_DEPENDS_ON_FORMAT",
                    f"depends_on entry must be a positive-integer slug, got: {dep!r}"
                )

    # not_before format and temporal constraint
    if "not_before" in data:
        nb = str(data["not_before"])
        if not _ISO8601_PATTERN.match(nb):
            return ValidationResult.reject(
                "INVALID_TIMESTAMP",
                f"not_before must be ISO 8601 UTC, got: {nb!r}"
            )
        nb_dt = _parse_iso8601(nb)
        sa_dt = _parse_iso8601(str(data["submitted_at"]))
        if nb_dt and sa_dt and nb_dt < sa_dt:
            return ValidationResult.reject(
                "NOT_BEFORE_BEFORE_SUBMITTED",
                "not_before must not be earlier than submitted_at"
            )

    return ValidationResult.accept()


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

def validate_event(data: dict) -> ValidationResult:
    """Validate an event before appending to the event log."""

    if not isinstance(data, dict):
        return ValidationResult.reject("INVALID_SHAPE", "Event must be a JSON object")

    # Required fields
    for field in ("ts", "type", "actor"):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD", f"Missing required field: {field}"
            )

    # ts format
    if not _ISO8601_PATTERN.match(str(data["ts"])):
        return ValidationResult.reject(
            "INVALID_TIMESTAMP", f"ts must be ISO 8601 UTC, got: {data['ts']!r}"
        )

    # type
    if data["type"] not in EVENT_TYPES:
        return ValidationResult.reject(
            "INVALID_TYPE",
            f"type must be one of {sorted(EVENT_TYPES)}, got: {data['type']!r}"
        )

    # actor format
    if not _ACTOR_PATTERN.match(str(data.get("actor", ""))):
        return ValidationResult.reject(
            "INVALID_ACTOR",
            f"actor must be lowercase alphanumeric with hyphens, starting with a letter, "
            f"got: {data.get('actor')!r}"
        )

    t = data["type"]

    goal_id = data.get("goal")
    if goal_id is not None and not _ID_PATTERN.match(str(goal_id)):
        return ValidationResult.reject(
            "INVALID_GOAL_FORMAT",
            f"goal must be a valid goal id, got: {goal_id!r}",
        )

    run_id = data.get("run")
    if run_id is not None and not _RUN_ID_PATTERN.match(str(run_id)):
        return ValidationResult.reject(
            "INVALID_ID_FORMAT",
            f"run must be a valid run id, got: {run_id!r}",
        )

    conversation_id = data.get("conversation_id")
    if conversation_id is not None and not _ID_PATTERN.match(str(conversation_id)):
        return ValidationResult.reject(
            "INVALID_ID_FORMAT",
            f"conversation_id must be a valid conversation id, got: {conversation_id!r}",
        )

    source_message_id = data.get("source_message_id")
    if source_message_id is not None and not _MESSAGE_ID_PATTERN.match(str(source_message_id)):
        return ValidationResult.reject(
            "INVALID_ID_FORMAT",
            f"source_message_id must be a valid message id, got: {source_message_id!r}",
        )

    source_goal_id = data.get("source_goal_id")
    if source_goal_id is not None and not _ID_PATTERN.match(str(source_goal_id)):
        return ValidationResult.reject(
            "INVALID_GOAL_FORMAT",
            f"source_goal_id must be a valid goal id, got: {source_goal_id!r}",
        )

    source_run_id = data.get("source_run_id")
    if source_run_id is not None and not _RUN_ID_PATTERN.match(str(source_run_id)):
        return ValidationResult.reject(
            "INVALID_ID_FORMAT",
            f"source_run_id must be a valid run id, got: {source_run_id!r}",
        )

    hop_goal = data.get("hop_goal")
    if hop_goal is not None and not _ID_PATTERN.match(str(hop_goal)):
        return ValidationResult.reject(
            "INVALID_GOAL_FORMAT",
            f"hop_goal must be a valid goal id, got: {hop_goal!r}",
        )

    checkpoint_id = data.get("checkpoint_id")
    if checkpoint_id is not None and not _CHECKPOINT_ID_PATTERN.match(str(checkpoint_id)):
        return ValidationResult.reject(
            "INVALID_ID_FORMAT",
            f"checkpoint_id must be a valid checkpoint id, got: {checkpoint_id!r}",
        )

    dashboard_invocation_id = data.get("dashboard_invocation_id")
    if (
        dashboard_invocation_id is not None
        and not _DASHBOARD_INVOCATION_ID_PATTERN.match(str(dashboard_invocation_id))
    ):
        return ValidationResult.reject(
            "INVALID_ID_FORMAT",
            (
                "dashboard_invocation_id must be a valid dashboard invocation id, "
                f"got: {dashboard_invocation_id!r}"
            ),
        )

    for field in ("hop_requested_by", "checkpoint_requested_by"):
        value = data.get(field)
        if value is not None and not _ACTOR_PATTERN.match(str(value)):
            return ValidationResult.reject(
                "INVALID_ACTOR",
                f"{field} must be lowercase alphanumeric with hyphens, got: {value!r}",
            )

    for field in ("hop_reason", "checkpoint_reason", "goal_origin", "goal_subtype"):
        value = data.get(field)
        if value is not None and not _is_nonempty_string(value):
            return ValidationResult.reject(
                "INVALID_SHAPE",
                f"{field} must be a non-empty string when present",
            )

    for field in (
        "goal_type",
        "driver",
        "model",
        "packet_path",
        "operator_note_path",
        "dashboard_mode",
        "dashboard_record_path",
        "active_threads_reason",
    ):
        value = data.get(field)
        if value is not None and not _is_nonempty_string(value):
            return ValidationResult.reject(
                "INVALID_SHAPE",
                f"{field} must be a non-empty string when present",
            )

    for field in ("hop_automatic", "dashboard_tty"):
        if data.get(field) is not None and not isinstance(data.get(field), bool):
            return ValidationResult.reject(
                "INVALID_SHAPE",
                f"{field} must be a boolean when present",
            )

    if data.get("dashboard_mode") is not None and data["dashboard_mode"] not in DASHBOARD_MODES:
        return ValidationResult.reject(
            "INVALID_SHAPE",
            f"dashboard_mode must be one of {sorted(DASHBOARD_MODES)}, got: {data['dashboard_mode']!r}",
        )

    if (
        data.get("dashboard_outcome") is not None
        and data["dashboard_outcome"] not in DASHBOARD_INVOCATION_OUTCOMES
    ):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            (
                "dashboard_outcome must be one of "
                f"{sorted(DASHBOARD_INVOCATION_OUTCOMES)}, got: {data['dashboard_outcome']!r}"
            ),
        )

    checkpoint_summary_path = data.get("checkpoint_summary_path")
    if (
        checkpoint_summary_path is not None
        and not _CHECKPOINT_SUMMARY_PATH_PATTERN.match(str(checkpoint_summary_path))
    ):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "checkpoint_summary_path must match checkpoints/<checkpoint-id>.md",
        )

    dashboard_record_path = data.get("dashboard_record_path")
    if (
        dashboard_record_path is not None
        and not _DASHBOARD_RECORD_PATH_PATTERN.match(str(dashboard_record_path))
    ):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "dashboard_record_path must match dashboard/invocations/<invocation-id>.json",
        )

    active_threads_path = data.get("active_threads_path")
    if (
        active_threads_path is not None
        and not _ACTIVE_THREADS_PATH_PATTERN.match(str(active_threads_path))
    ):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "active_threads_path must match plants/<plant>/memory/active-threads.json",
        )

    for field in (
        "goal_priority",
        "checkpoint_count",
        "source_session_ordinal",
        "source_session_turns",
        "follow_up_goal_count",
        "supplement_chars",
        "dashboard_render_count",
        "dashboard_wall_ms",
    ):
        value = data.get(field)
        if value is not None and not _is_nonnegative_int(value):
            return ValidationResult.reject(
                "INVALID_SHAPE",
                f"{field} must be a non-negative integer when present",
            )

    dashboard_refresh_seconds = data.get("dashboard_refresh_seconds")
    if (
        dashboard_refresh_seconds is not None
        and (
            not _is_number(dashboard_refresh_seconds)
            or dashboard_refresh_seconds <= 0
        )
    ):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            (
                "dashboard_refresh_seconds must be a number greater than 0 when present, "
                f"got: {dashboard_refresh_seconds!r}"
            ),
        )

    if "source_session_id" in data and not _is_nonempty_string(data["source_session_id"]):
        return ValidationResult.reject(
            "INVALID_SHAPE",
            "source_session_id must be a non-empty string when present",
        )

    # goal required for goal-related events
    if t in _GOAL_REQUIRED_EVENTS and not data.get("goal"):
        return ValidationResult.reject(
            "MISSING_REQUIRED_FIELD", f"{t} requires a goal field"
        )

    # run required for run-related events
    if t in _RUN_REQUIRED_EVENTS and not data.get("run"):
        return ValidationResult.reject(
            "MISSING_REQUIRED_FIELD", f"{t} requires a run field"
        )

    if t in _CONVERSATION_REQUIRED_EVENTS and not data.get("conversation_id"):
        return ValidationResult.reject(
            "MISSING_REQUIRED_FIELD", f"{t} requires a conversation_id field"
        )

    # GoalTransitioned: from and to must be valid statuses
    if t == "GoalTransitioned":
        for field in ("from", "to"):
            if not data.get(field):
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD", f"GoalTransitioned requires field: {field}"
                )
            if data[field] not in GOAL_STATUSES:
                return ValidationResult.reject(
                    "INVALID_STATUS",
                    f"GoalTransitioned.{field} must be a valid goal status, got: {data[field]!r}"
                )

    # EvalSpawned / EvalClosed require eval_goal
    if t in ("EvalSpawned", "EvalClosed") and not data.get("eval_goal"):
        return ValidationResult.reject(
            "MISSING_REQUIRED_FIELD", f"{t} requires an eval_goal field"
        )

    # PlantCommissioned / PlantArchived require plant
    if t in _PLANT_REQUIRED_EVENTS and not data.get("plant"):
        return ValidationResult.reject(
            "MISSING_REQUIRED_FIELD", f"{t} requires a plant field"
        )

    # GoalClosed / EvalClosed: goal_reason required and typed
    if t in ("GoalClosed", "EvalClosed"):
        if not data.get("goal_reason"):
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD", f"{t} requires a goal_reason"
            )
        if data["goal_reason"] not in GOAL_REASON_CODES:
            return ValidationResult.reject(
                "INVALID_REASON",
                f"{t}.goal_reason must be one of {sorted(GOAL_REASON_CODES)}, "
                f"got: {data['goal_reason']!r}"
            )

    # RunFinished: run_reason required and typed
    if t == "RunFinished":
        if not data.get("run_reason"):
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD", "RunFinished requires a run_reason"
            )
        if data["run_reason"] not in RUN_REASON_CODES:
            return ValidationResult.reject(
                "INVALID_REASON",
                f"RunFinished.run_reason must be one of {sorted(RUN_REASON_CODES)}, "
                f"got: {data['run_reason']!r}"
            )

    if t == "DashboardInvocationStarted":
        for field in (
            "dashboard_invocation_id",
            "dashboard_mode",
            "dashboard_refresh_seconds",
            "dashboard_tty",
        ):
            if field not in data:
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"DashboardInvocationStarted requires field: {field}",
                )

    if t == "ActiveThreadsRefreshStarted":
        for field in ("plant", "active_threads_path"):
            if field not in data:
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"ActiveThreadsRefreshStarted requires field: {field}",
                )
        if not _ACTOR_PATTERN.match(str(data.get("plant", ""))):
            return ValidationResult.reject(
                "INVALID_PLANT_NAME",
                f"plant must be lowercase alphanumeric with hyphens, got: {data.get('plant')!r}",
            )

    if t == "ActiveThreadsRefreshFinished":
        for field in ("plant", "active_threads_path", "active_threads_outcome"):
            if field not in data:
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"ActiveThreadsRefreshFinished requires field: {field}",
                )
        if not _ACTOR_PATTERN.match(str(data.get("plant", ""))):
            return ValidationResult.reject(
                "INVALID_PLANT_NAME",
                f"plant must be lowercase alphanumeric with hyphens, got: {data.get('plant')!r}",
            )
        outcome = data.get("active_threads_outcome")
        if outcome not in ACTIVE_THREADS_REFRESH_OUTCOMES:
            return ValidationResult.reject(
                "INVALID_SHAPE",
                (
                    "active_threads_outcome must be one of "
                    f"{sorted(ACTIVE_THREADS_REFRESH_OUTCOMES)}, got: {outcome!r}"
                ),
            )
        if outcome != "success" and not _is_nonempty_string(data.get("active_threads_reason")):
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                "ActiveThreadsRefreshFinished requires active_threads_reason when outcome is not success",
            )

    if t == "InitiativeRefreshStarted":
        for field in ("plant", "initiative_id", "initiative_path"):
            if field not in data:
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"InitiativeRefreshStarted requires field: {field}",
                )
        if not _ACTOR_PATTERN.match(str(data.get("plant", ""))):
            return ValidationResult.reject(
                "INVALID_PLANT_NAME",
                f"plant must be lowercase alphanumeric with hyphens, got: {data.get('plant')!r}",
            )
        if not re.match(r"^[a-z0-9-]+$", str(data.get("initiative_id", ""))):
            return ValidationResult.reject(
                "INVALID_SHAPE",
                (
                    "initiative_id must be lowercase alphanumeric with hyphens, "
                    f"got: {data.get('initiative_id')!r}"
                ),
            )

    if t == "InitiativeRefreshFinished":
        for field in ("plant", "initiative_id", "initiative_path", "initiative_outcome"):
            if field not in data:
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"InitiativeRefreshFinished requires field: {field}",
                )
        if not _ACTOR_PATTERN.match(str(data.get("plant", ""))):
            return ValidationResult.reject(
                "INVALID_PLANT_NAME",
                f"plant must be lowercase alphanumeric with hyphens, got: {data.get('plant')!r}",
            )
        if not re.match(r"^[a-z0-9-]+$", str(data.get("initiative_id", ""))):
            return ValidationResult.reject(
                "INVALID_SHAPE",
                (
                    "initiative_id must be lowercase alphanumeric with hyphens, "
                    f"got: {data.get('initiative_id')!r}"
                ),
            )
        outcome = data.get("initiative_outcome")
        if outcome not in INITIATIVE_REFRESH_OUTCOMES:
            return ValidationResult.reject(
                "INVALID_SHAPE",
                (
                    "initiative_outcome must be one of "
                    f"{sorted(INITIATIVE_REFRESH_OUTCOMES)}, got: {outcome!r}"
                ),
            )
        if outcome != "success" and not _is_nonempty_string(data.get("initiative_reason")):
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                "InitiativeRefreshFinished requires initiative_reason when outcome is not success",
            )

    if t == "DashboardInvocationFinished":
        for field in (
            "dashboard_invocation_id",
            "dashboard_mode",
            "dashboard_refresh_seconds",
            "dashboard_tty",
            "dashboard_outcome",
            "dashboard_render_count",
            "dashboard_wall_ms",
            "dashboard_record_path",
        ):
            if field not in data:
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"DashboardInvocationFinished requires field: {field}",
                )

    if t == "TendStarted":
        trigger_kinds = data.get("trigger_kinds")
        if not isinstance(trigger_kinds, list) or not trigger_kinds:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                "TendStarted requires a non-empty trigger_kinds array",
            )
        for kind in trigger_kinds:
            if kind not in TEND_TRIGGER_KINDS:
                return ValidationResult.reject(
                    "INVALID_TEND_TRIGGER_KIND",
                    f"unknown tend trigger kind: {kind!r}",
                )
        trigger_goal = data.get("trigger_goal")
        if trigger_goal and not _ID_PATTERN.match(str(trigger_goal)):
            return ValidationResult.reject(
                "INVALID_GOAL_FORMAT",
                f"trigger_goal must be a valid goal id, got: {trigger_goal!r}",
            )
        trigger_run = data.get("trigger_run")
        if trigger_run and not _RUN_ID_PATTERN.match(str(trigger_run)):
            return ValidationResult.reject(
                "INVALID_ID_FORMAT",
                f"trigger_run must be a valid run id, got: {trigger_run!r}",
            )

    if t == "TendFinished":
        count = data.get("follow_up_goal_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return ValidationResult.reject(
                "INVALID_SHAPE",
                "TendFinished.follow_up_goal_count must be a non-negative integer",
            )
        for field in ("memory_updated", "operator_note_written"):
            if not isinstance(data.get(field), bool):
                return ValidationResult.reject(
                    "INVALID_SHAPE",
                    f"TendFinished.{field} must be a boolean",
                )
        follow_up_goals = data.get("follow_up_goals")
        if follow_up_goals is not None:
            if not isinstance(follow_up_goals, list):
                return ValidationResult.reject(
                    "INVALID_SHAPE",
                    "TendFinished.follow_up_goals must be an array when present",
                )
            for goal_id in follow_up_goals:
                if not _ID_PATTERN.match(str(goal_id)):
                    return ValidationResult.reject(
                        "INVALID_GOAL_FORMAT",
                        f"follow_up_goals entry must be a valid goal id, got: {goal_id!r}",
                    )
        operator_note_path = data.get("operator_note_path")
        if operator_note_path is not None and not str(operator_note_path).strip():
            return ValidationResult.reject(
                "INVALID_SHAPE",
                "TendFinished.operator_note_path must be non-empty when present",
            )

    if t == "ConversationHopQueued":
        for field in ("hop_goal", "hop_requested_by", "hop_reason"):
            if not data.get(field):
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"ConversationHopQueued requires a {field} field",
                )
        if not isinstance(data.get("hop_automatic"), bool):
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                "ConversationHopQueued requires a hop_automatic field",
            )

    if t == "ConversationHopQueueFailed":
        for field in ("hop_requested_by", "hop_reason", "detail"):
            if not data.get(field):
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"ConversationHopQueueFailed requires a {field} field",
                )
        if not isinstance(data.get("hop_automatic"), bool):
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                "ConversationHopQueueFailed requires a hop_automatic field",
            )

    if t == "ConversationCheckpointWritten":
        for field in (
            "checkpoint_id",
            "checkpoint_requested_by",
            "checkpoint_reason",
            "checkpoint_summary_path",
            "source_message_id",
            "source_session_ordinal",
            "source_session_turns",
            "checkpoint_count",
        ):
            if data.get(field) in (None, ""):
                return ValidationResult.reject(
                    "MISSING_REQUIRED_FIELD",
                    f"ConversationCheckpointWritten requires a {field} field",
                )

    # SystemError: error_reason required and typed
    if t == "SystemError":
        if not data.get("error_reason"):
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD", "SystemError requires an error_reason"
            )
        if data["error_reason"] not in ERROR_REASON_CODES:
            return ValidationResult.reject(
                "INVALID_REASON",
                f"SystemError.error_reason must be one of {sorted(ERROR_REASON_CODES)}, "
                f"got: {data['error_reason']!r}"
            )

    return ValidationResult.accept()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def validate_run(data: dict) -> ValidationResult:
    """Validate a run record. Called at run start and at close time."""

    if not isinstance(data, dict):
        return ValidationResult.reject("INVALID_SHAPE", "Run must be a JSON object")

    # Required fields
    for field in ("id", "goal", "plant", "status", "started_at", "driver", "model"):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD", f"Missing required field: {field}"
            )

    # id format (rejects r0)
    if not _RUN_ID_PATTERN.match(str(data["id"])):
        return ValidationResult.reject(
            "INVALID_ID_FORMAT",
            f"Run id must match N-slug-rN format (attempt number starts at r1), got: {data['id']!r}"
        )

    # goal format
    if not _ID_PATTERN.match(str(data["goal"])):
        return ValidationResult.reject(
            "INVALID_GOAL_FORMAT",
            f"goal must be a positive-integer slug, got: {data['goal']!r}"
        )

    # plant format
    if not _ACTOR_PATTERN.match(str(data.get("plant", ""))):
        return ValidationResult.reject(
            "INVALID_PLANT_NAME",
            f"plant must be lowercase alphanumeric with hyphens, starting with a letter, "
            f"got: {data.get('plant')!r}"
        )

    # status
    if data["status"] not in RUN_STATUSES:
        return ValidationResult.reject(
            "INVALID_STATUS",
            f"status must be one of {sorted(RUN_STATUSES)}, got: {data['status']!r}"
        )

    # started_at
    if not _ISO8601_PATTERN.match(str(data["started_at"])):
        return ValidationResult.reject(
            "INVALID_TIMESTAMP",
            f"started_at must be ISO 8601 UTC, got: {data['started_at']!r}"
        )

    status = data["status"]

    # Terminal runs require completed_at and cost
    if status in RUN_TERMINAL:
        if not data.get("completed_at"):
            return ValidationResult.reject(
                "MISSING_COMPLETED_AT",
                "completed_at is required for terminal runs"
            )
        if not _ISO8601_PATTERN.match(str(data["completed_at"])):
            return ValidationResult.reject(
                "INVALID_TIMESTAMP",
                f"completed_at must be ISO 8601 UTC, got: {data['completed_at']!r}"
            )
        if "cost" not in data:
            return ValidationResult.reject(
                "MISSING_COST",
                "cost is required for terminal runs (use source='unknown' if unavailable)"
            )

    # cost shape
    if "cost" in data:
        result = _validate_cost(data["cost"])
        if not result.ok:
            return result

    # Failed runs require failure_reason (enum)
    if status in RUN_FAILED:
        fr = data.get("failure_reason")
        if not fr:
            return ValidationResult.reject(
                "MISSING_FAILURE_REASON",
                f"failure_reason is required when status is {status!r}"
            )
        if fr not in FAILURE_REASONS:
            return ValidationResult.reject(
                "INVALID_FAILURE_REASON",
                f"failure_reason must be one of {sorted(FAILURE_REASONS)}, got: {fr!r}"
            )

    return ValidationResult.accept()


def validate_run_close(run: dict, goal_type: str) -> ValidationResult:
    """
    Cross-schema validation at run close time.

    For goal types that require reflection (build, fix, evaluate, tend),
    reflection must be present and non-empty on successful runs.
    The driver solicits reflection via session continuation before calling
    close_run, so by the time this runs the field should already be populated.
    """
    result = validate_run(run)
    if not result.ok:
        return result

    if (goal_type in REFLECTION_REQUIRED_TYPES
            and run.get("status") == "success"):
        reflection = run.get("reflection")
        if not reflection or not str(reflection).strip():
            return ValidationResult.reject(
                "MISSING_REFLECTION",
                f"reflection is required for successful {goal_type!r} runs; "
                "it is solicited by the driver as a session continuation"
            )

    return ValidationResult.accept()


# ---------------------------------------------------------------------------
# Plant
# ---------------------------------------------------------------------------

def validate_plant(data: dict) -> ValidationResult:
    """Validate a plant record."""

    if not isinstance(data, dict):
        return ValidationResult.reject("INVALID_SHAPE", "Plant must be a JSON object")

    for field in ("name", "seed", "status", "created_at", "commissioned_by"):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD", f"Missing required field: {field}"
            )

    if not _ACTOR_PATTERN.match(str(data["name"])):
        return ValidationResult.reject(
            "INVALID_PLANT_NAME",
            f"name must be lowercase alphanumeric with hyphens, starting with a letter, "
            f"got: {data['name']!r}"
        )

    if not str(data.get("seed", "")).strip():
        return ValidationResult.reject("MISSING_SEED", "seed must not be empty")

    if data["status"] not in PLANT_STATUSES:
        return ValidationResult.reject(
            "INVALID_STATUS",
            f"status must be one of {sorted(PLANT_STATUSES)}, got: {data['status']!r}"
        )

    if not _ISO8601_PATTERN.match(str(data.get("created_at", ""))):
        return ValidationResult.reject(
            "INVALID_TIMESTAMP",
            f"created_at must be ISO 8601 UTC, got: {data.get('created_at')!r}"
        )

    if not _ACTOR_PATTERN.match(str(data.get("commissioned_by", ""))):
        return ValidationResult.reject(
            "INVALID_COMMISSIONED_BY",
            f"commissioned_by must be lowercase alphanumeric with hyphens, "
            f"starting with a letter, got: {data.get('commissioned_by')!r}"
        )

    return ValidationResult.accept()


def validate_conversation(data: dict) -> ValidationResult:
    """Validate a conversation record."""
    if not isinstance(data, dict):
        return ValidationResult.reject("INVALID_SHAPE", "Conversation must be a JSON object")
    result = _validate_known_fields(
        data,
        allowed=_CONVERSATION_FIELDS,
        reason="UNKNOWN_CONVERSATION_FIELD",
        label="conversation",
    )
    if not result.ok:
        return result
    for field in ("id", "status", "channel", "channel_ref", "presence_model",
                  "started_at", "last_activity_at", "context_at"):
        if field not in data:
            return ValidationResult.reject("MISSING_REQUIRED_FIELD", f"Missing required field: {field}")
    if not _ID_PATTERN.match(str(data["id"])):
        return ValidationResult.reject(
            "INVALID_CONVERSATION_FIELD",
            f"id must be a valid conversation id, got: {data['id']!r}",
        )
    if data["status"] not in CONVERSATION_STATUSES:
        return ValidationResult.reject("INVALID_STATUS",
            f"status must be one of {sorted(CONVERSATION_STATUSES)}, got: {data['status']!r}")
    if data["presence_model"] not in PRESENCE_MODELS:
        return ValidationResult.reject("INVALID_PRESENCE_MODEL",
            f"presence_model must be one of {sorted(PRESENCE_MODELS)}, got: {data['presence_model']!r}")
    if "started_by" in data and data["started_by"] not in CONVERSATION_STARTERS:
        return ValidationResult.reject(
            "INVALID_CONVERSATION_FIELD",
            (
                "started_by must be one of "
                f"{sorted(CONVERSATION_STARTERS)}, got: {data['started_by']!r}"
            ),
        )
    for field in ("channel", "channel_ref"):
        if not _is_nonempty_string(data.get(field)):
            return ValidationResult.reject(
                "INVALID_CONVERSATION_FIELD",
                f"{field} must be a non-empty string, got: {data.get(field)!r}",
            )
    for ts_field in ("started_at", "last_activity_at", "context_at"):
        if not _ISO8601_PATTERN.match(str(data.get(ts_field, ""))):
            return ValidationResult.reject("INVALID_TIMESTAMP",
                f"{ts_field} must be ISO 8601 UTC, got: {data.get(ts_field)!r}")
    if "participants" in data:
        participants = data["participants"]
        if not isinstance(participants, list) or any(not _is_nonempty_string(p) for p in participants):
            return ValidationResult.reject(
                "INVALID_CONVERSATION_FIELD",
                "participants must be an array of non-empty strings",
            )
    if "topic" in data and not _is_nonempty_string(data["topic"]):
        return ValidationResult.reject(
            "INVALID_CONVERSATION_FIELD",
            "topic must be a non-empty string",
        )
    if "session_id" in data and data["session_id"] is not None and not _is_nonempty_string(data["session_id"]):
        return ValidationResult.reject(
            "INVALID_CONVERSATION_FIELD",
            "session_id must be null or a non-empty string",
        )
    if "compacted_through" in data:
        compacted = data["compacted_through"]
        if compacted is not None and not _MESSAGE_ID_PATTERN.match(str(compacted)):
            return ValidationResult.reject(
                "INVALID_CONVERSATION_FIELD",
                f"compacted_through must be null or a valid message id, got: {compacted!r}",
            )
    for int_field in ("session_ordinal", "session_turns", "checkpoint_count"):
        if int_field in data and not _is_nonnegative_int(data[int_field]):
            return ValidationResult.reject(
                "INVALID_CONVERSATION_FIELD",
                f"{int_field} must be a non-negative integer, got: {data[int_field]!r}",
            )
    for ts_field in ("session_started_at", "last_checkpoint_at"):
        if ts_field in data and data[ts_field] is not None and not _ISO8601_PATTERN.match(str(data[ts_field])):
            return ValidationResult.reject(
                "INVALID_TIMESTAMP",
                f"{ts_field} must be null or ISO 8601 UTC, got: {data[ts_field]!r}",
            )
    if "last_checkpoint_id" in data:
        checkpoint_id = data["last_checkpoint_id"]
        if checkpoint_id is not None and not _CHECKPOINT_ID_PATTERN.match(str(checkpoint_id)):
            return ValidationResult.reject(
                "INVALID_CONVERSATION_FIELD",
                f"last_checkpoint_id must be null or a valid checkpoint id, got: {checkpoint_id!r}",
            )
    if "last_turn_mode" in data:
        mode = data["last_turn_mode"]
        if mode is not None and mode not in CONVERSATION_TURN_MODES:
            return ValidationResult.reject(
                "INVALID_CONVERSATION_FIELD",
                f"last_turn_mode must be null or one of {sorted(CONVERSATION_TURN_MODES)}, got: {mode!r}",
            )
    if "last_turn_run_id" in data:
        run_id = data["last_turn_run_id"]
        if run_id is not None and not _RUN_ID_PATTERN.match(str(run_id)):
            return ValidationResult.reject(
                "INVALID_CONVERSATION_FIELD",
                f"last_turn_run_id must be null or a valid run id, got: {run_id!r}",
            )
    if "last_pressure" in data and data["last_pressure"] is not None:
        result = _validate_pressure(
            data["last_pressure"],
            reason="INVALID_CONVERSATION_PRESSURE",
            label="conversation",
        )
        if not result.ok:
            return result
    if "pending_hop" in data and data["pending_hop"] is not None:
        result = _validate_pending_hop(
            data["pending_hop"],
            reason="INVALID_CONVERSATION_HOP",
        )
        if not result.ok:
            return result
    if "post_reply_hop" in data and data["post_reply_hop"] is not None:
        result = _validate_post_reply_hop(
            data["post_reply_hop"],
            reason="INVALID_POST_REPLY_HOP",
        )
        if not result.ok:
            return result
    return ValidationResult.accept()


def validate_message(data: dict) -> ValidationResult:
    """Validate a conversation message."""
    if not isinstance(data, dict):
        return ValidationResult.reject("INVALID_SHAPE", "Message must be a JSON object")
    result = _validate_known_fields(
        data,
        allowed=_MESSAGE_FIELDS,
        reason="UNKNOWN_MESSAGE_FIELD",
        label="message",
    )
    if not result.ok:
        return result
    for field in ("id", "conversation_id", "ts", "sender", "content", "channel"):
        if field not in data:
            return ValidationResult.reject("MISSING_REQUIRED_FIELD", f"Missing required field: {field}")
    if not _MESSAGE_ID_PATTERN.match(str(data["id"])):
        return ValidationResult.reject(
            "INVALID_MESSAGE_FIELD",
            f"id must be a valid message id, got: {data['id']!r}",
        )
    if not _ID_PATTERN.match(str(data["conversation_id"])):
        return ValidationResult.reject(
            "INVALID_MESSAGE_FIELD",
            f"conversation_id must be a valid conversation id, got: {data['conversation_id']!r}",
        )
    if not _ISO8601_PATTERN.match(str(data.get("ts", ""))):
        return ValidationResult.reject("INVALID_TIMESTAMP", f"ts must be ISO 8601 UTC")
    for field in ("sender", "channel"):
        if not _is_nonempty_string(data.get(field)):
            return ValidationResult.reject(
                "INVALID_MESSAGE_FIELD",
                f"{field} must be a non-empty string, got: {data.get(field)!r}",
            )
    reply_to = data.get("reply_to")
    if reply_to is not None and not _MESSAGE_ID_PATTERN.match(str(reply_to)):
        return ValidationResult.reject(
            "INVALID_MESSAGE_FIELD",
            f"reply_to must be null or a valid message id, got: {reply_to!r}",
        )
    if not str(data.get("content", "")).strip():
        return ValidationResult.reject("EMPTY_CONTENT", "content must not be empty")
    return ValidationResult.accept()


def validate_operator_message_request(data: dict) -> ValidationResult:
    """Validate an operator-message emission request."""
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE",
            "operator message request must be a JSON object",
        )
    result = _validate_known_fields(
        data,
        allowed=_OPERATOR_MESSAGE_REQUEST_FIELDS,
        reason="UNKNOWN_OPERATOR_MESSAGE_FIELD",
        label="operator message request",
    )
    if not result.ok:
        return result
    for field in ("kind", "sender", "content", "source_goal_id", "source_run_id"):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"operator message request missing required field: {field}",
            )
    kind = data.get("kind")
    if kind not in OPERATOR_MESSAGE_KINDS:
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE_KIND",
            (
                "operator message kind must be one of "
                f"{sorted(OPERATOR_MESSAGE_KINDS)}, got: {kind!r}"
            ),
        )
    sender = data.get("sender")
    if sender not in OPERATOR_MESSAGE_SENDERS:
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE_SENDER",
            (
                "operator message sender must be one of "
                f"{sorted(OPERATOR_MESSAGE_SENDERS)}, got: {sender!r}"
            ),
        )
    if not str(data.get("content", "")).strip():
        return ValidationResult.reject(
            "EMPTY_CONTENT",
            "operator message content must not be empty",
        )
    source_goal_id = data.get("source_goal_id")
    if not _ID_PATTERN.match(str(source_goal_id)):
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE",
            f"source_goal_id must be a valid goal id, got: {source_goal_id!r}",
        )
    source_run_id = data.get("source_run_id")
    if not _RUN_ID_PATTERN.match(str(source_run_id)):
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE",
            f"source_run_id must be a valid run id, got: {source_run_id!r}",
        )
    if "origin" in data and data["origin"] is not None:
        result = _validate_conversation_reference(
            data["origin"],
            allowed=_OPERATOR_MESSAGE_ORIGIN_FIELDS,
            required=("kind", "conversation_id", "ts"),
            reason="INVALID_OPERATOR_MESSAGE_ORIGIN",
            label="operator message request.origin",
        )
        if not result.ok:
            return result
    return ValidationResult.accept()


def validate_operator_message_record(data: dict) -> ValidationResult:
    """Validate one persisted operator-message emission record."""
    if not isinstance(data, dict):
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE",
            "operator message record must be a JSON object",
        )
    result = _validate_known_fields(
        data,
        allowed=_OPERATOR_MESSAGE_RECORD_FIELDS,
        reason="UNKNOWN_OPERATOR_MESSAGE_FIELD",
        label="operator message record",
    )
    if not result.ok:
        return result
    for field in (
        "schema_version",
        "kind",
        "sender",
        "transcript_policy",
        "delivery_policy",
        "emitted_at",
        "source_goal_id",
        "source_run_id",
    ):
        if field not in data:
            return ValidationResult.reject(
                "MISSING_REQUIRED_FIELD",
                f"operator message record missing required field: {field}",
            )
    if data.get("schema_version") != _OPERATOR_MESSAGE_SCHEMA_VERSION:
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE",
            (
                "operator message schema_version must be "
                f"{_OPERATOR_MESSAGE_SCHEMA_VERSION}, got: {data.get('schema_version')!r}"
            ),
        )
    request_result = validate_operator_message_request(
        {
            "kind": data.get("kind"),
            "sender": data.get("sender"),
            "content": "recorded",
            "origin": data.get("origin"),
            "source_goal_id": data.get("source_goal_id"),
            "source_run_id": data.get("source_run_id"),
        }
    )
    if not request_result.ok:
        return ValidationResult.reject(
            request_result.reason or "INVALID_OPERATOR_MESSAGE",
            request_result.detail or "invalid operator message record base fields",
        )
    transcript_policy = data.get("transcript_policy")
    if transcript_policy not in OPERATOR_MESSAGE_TRANSCRIPT_POLICIES:
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE_POLICY",
            (
                "transcript_policy must be one of "
                f"{sorted(OPERATOR_MESSAGE_TRANSCRIPT_POLICIES)}, got: {transcript_policy!r}"
            ),
        )
    delivery_policy = data.get("delivery_policy")
    if delivery_policy not in OPERATOR_MESSAGE_DELIVERY_POLICIES:
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE_POLICY",
            (
                "delivery_policy must be one of "
                f"{sorted(OPERATOR_MESSAGE_DELIVERY_POLICIES)}, got: {delivery_policy!r}"
            ),
        )
    if not _ISO8601_PATTERN.match(str(data.get("emitted_at", ""))):
        return ValidationResult.reject(
            "INVALID_TIMESTAMP",
            f"emitted_at must be ISO 8601 UTC, got: {data.get('emitted_at')!r}",
        )
    delivery_path = data.get("delivery_path")
    if delivery_path is not None and not _OPERATOR_MESSAGE_DELIVERY_PATH_PATTERN.match(str(delivery_path)):
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE_PATH",
            (
                "delivery_path must match inbox/<garden>/<name>.md or "
                f"inbox/<garden>/notes/<name>.md, got: {delivery_path!r}"
            ),
        )
    conversation_message_id = data.get("conversation_message_id")
    if conversation_message_id is not None and not _MESSAGE_ID_PATTERN.match(str(conversation_message_id)):
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE",
            (
                "conversation_message_id must be a valid message id when present, "
                f"got: {conversation_message_id!r}"
            ),
        )
    if transcript_policy == "canonical":
        if data.get("origin") is None:
            return ValidationResult.reject(
                "INVALID_OPERATOR_MESSAGE_POLICY",
                "canonical operator messages require origin conversation linkage",
            )
        if delivery_policy != "reply_copy":
            return ValidationResult.reject(
                "INVALID_OPERATOR_MESSAGE_POLICY",
                "canonical operator messages must use delivery_policy reply_copy",
            )
        if conversation_message_id is None:
            return ValidationResult.reject(
                "INVALID_OPERATOR_MESSAGE_POLICY",
                "canonical operator messages require conversation_message_id",
            )
    else:
        if data.get("origin") is not None:
            return ValidationResult.reject(
                "INVALID_OPERATOR_MESSAGE_POLICY",
                "out-of-band operator messages must not carry origin linkage",
            )
        if delivery_policy != "out_of_band_note":
            return ValidationResult.reject(
                "INVALID_OPERATOR_MESSAGE_POLICY",
                "out-of-band operator messages must use delivery_policy out_of_band_note",
            )
        if conversation_message_id is not None:
            return ValidationResult.reject(
                "INVALID_OPERATOR_MESSAGE_POLICY",
                "out-of-band operator messages must not record conversation_message_id",
            )
    if delivery_policy == "out_of_band_note" and delivery_path is None:
        return ValidationResult.reject(
            "INVALID_OPERATOR_MESSAGE_POLICY",
            "out-of-band operator messages require delivery_path",
        )
    if delivery_policy == "reply_copy" and delivery_path is not None:
        delivery_name = Path(str(delivery_path)).name
        if not delivery_name.endswith(".md"):
            return ValidationResult.reject(
                "INVALID_OPERATOR_MESSAGE_PATH",
                "reply_copy delivery_path must point to a markdown file",
            )
    return ValidationResult.accept()


def _validate_cost(cost: dict) -> ValidationResult:
    if not isinstance(cost, dict):
        return ValidationResult.reject("INVALID_COST_SHAPE", "cost must be an object")
    if "source" not in cost:
        return ValidationResult.reject(
            "MISSING_REQUIRED_FIELD", "cost.source is required"
        )
    if cost["source"] not in COST_SOURCES:
        return ValidationResult.reject(
            "INVALID_COST_SOURCE",
            f"cost.source must be one of {sorted(COST_SOURCES)}, got: {cost['source']!r}"
        )
    for field in ("input_tokens", "output_tokens", "cache_read_tokens"):
        if field in cost and (
            not isinstance(cost[field], int)
            or isinstance(cost[field], bool)
            or cost[field] < 0
        ):
            return ValidationResult.reject(
                "INVALID_COST_FIELD", f"{field} must be a non-negative integer"
            )
    if "actual_usd" in cost and (
        not isinstance(cost["actual_usd"], (int, float))
        or isinstance(cost["actual_usd"], bool)
        or cost["actual_usd"] < 0
    ):
        return ValidationResult.reject(
            "INVALID_COST_FIELD", "actual_usd must be a non-negative number"
        )
    return ValidationResult.accept()
