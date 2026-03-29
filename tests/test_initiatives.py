import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from system.events import read_events
from system.goals import read_goal
from system.initiatives import read_initiative_record, write_initiative_record
from system.plants import commission_plant
from system.submit import submit_same_initiative_code_change_with_evaluate
from system.validate import validate_initiative_record


def _sample_initiative_record() -> dict:
    return {
        "schema_version": 1,
        "id": "git-backed-run-memory-runtime-root-separation",
        "plant": "gardener",
        "title": "Git-backed run memory and runtime-root separation",
        "status": "active",
        "approved_by": {
            "kind": "conversation",
            "conversation_id": "1-hello",
            "message_id": "msg-20260324184545-ope-nbf3",
            "ts": "2026-03-24T18:45:45Z",
        },
        "objective": "Separate authored work from runtime churn so git history can serve as durable memory without obscuring authored diffs.",
        "scope_boundary": "Carry the approved migration in explicit tranches without changing runtime behavior in the record-creation run.",
        "non_goals": [
            "Do not change runtime path behavior in this record-creation run.",
            "Do not implement runtime-root separation in the first tranche.",
        ],
        "success_checks": [
            "Only one tranche is active at a time.",
            "Each code-changing tranche closes with a review or evaluate artifact before any successor tranche may start.",
        ],
        "budget_policy": {
            "mode": "track_only",
            "notes": "Track included goals, runs, and token totals without hard ceilings in the first version.",
        },
        "tranches": [
            {
                "id": "path-centralization",
                "title": "Centralize runtime path resolution behind config",
                "objective": "Move hard-coded runtime path resolution behind shared config while preserving current runtime behavior.",
                "status": "active",
                "allowed_goal_types": ["fix", "evaluate"],
                "execution_mode": "ordinary_goals_only",
                "review_policy": "mandatory_review_or_evaluate_stop",
                "stop_rules": [
                    "Do not move runtime directories to a new root in this tranche.",
                    "Do not add runtime auto-commit in this tranche.",
                ],
                "successor": {
                    "condition": "review_or_evaluate_recommends_next_tranche",
                    "next_tranche_id": "runtime-root-separation",
                    "summary": "Advance only if the tranche-closing artifact explicitly recommends runtime-root separation as the next same-initiative tranche.",
                },
            },
            {
                "id": "runtime-root-separation",
                "title": "Move runtime churn under a dedicated runtime root",
                "objective": "Relocate high-churn runtime artifacts after path access is centralized.",
                "status": "planned",
                "allowed_goal_types": ["fix", "evaluate"],
                "execution_mode": "ordinary_goals_only",
                "review_policy": "mandatory_review_or_evaluate_stop",
                "stop_rules": [
                    "Do not add runtime auto-commit in this tranche.",
                ],
                "successor": {
                    "condition": "review_or_evaluate_recommends_next_tranche",
                    "next_tranche_id": "runtime-auto-commit",
                    "summary": "Advance only if the tranche-closing artifact explicitly recommends runtime auto-commit as the next same-initiative tranche.",
                },
            },
            {
                "id": "runtime-auto-commit",
                "title": "Auto-commit runtime artifacts with authored-commit provenance",
                "objective": "Record runtime history in git with explicit authored-commit provenance.",
                "status": "planned",
                "allowed_goal_types": ["fix", "evaluate"],
                "execution_mode": "ordinary_goals_only",
                "review_policy": "mandatory_review_or_evaluate_stop",
                "stop_rules": [
                    "Stop after the tranche-closing review or evaluate artifact before the final closure review.",
                ],
                "successor": {
                    "condition": "review_or_evaluate_recommends_next_tranche",
                    "next_tranche_id": "closure-review",
                    "summary": "Advance only if the tranche-closing artifact explicitly recommends the closure review as the next same-initiative tranche.",
                },
            },
            {
                "id": "closure-review",
                "title": "Close the initiative with a bounded acceptance review",
                "objective": "Confirm that the initiative closed inside the approved boundary.",
                "status": "planned",
                "allowed_goal_types": ["evaluate"],
                "execution_mode": "ordinary_goals_only",
                "review_policy": "mandatory_review_or_evaluate_stop",
                "stop_rules": [
                    "Do not reopen implementation inside the closure review run.",
                ],
                "successor": {
                    "condition": "initiative_complete_after_clean_review",
                    "next_tranche_id": None,
                    "summary": "Mark the initiative complete only if the closure review succeeds without naming a broader follow-up.",
                },
            },
        ],
        "current_tranche_id": "path-centralization",
        "next_authorized_step": {
            "tranche_id": "path-centralization",
            "status": "ready",
            "goal_type": "fix",
            "summary": "Queue one bounded implementation goal that only centralizes runtime path resolution behind config.",
            "may_start_bounded_campaign": False,
            "stop_after": "Stop after the tranche-closing review or evaluate artifact for path centralization.",
        },
        "ledger": {
            "goal_ids": [],
            "run_ids": [],
            "totals": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
            },
        },
        "updated_at": "2026-03-24T19:38:44Z",
        "updated_by_run": "386-using-the-results-of-goal-378-after-the-r1",
    }


class InitiativeValidationTests(unittest.TestCase):
    def _assert_rejection(self, data: dict, reason: str) -> None:
        result = validate_initiative_record(data)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, reason)

    def test_validate_initiative_record_accepts_valid_record(self) -> None:
        result = validate_initiative_record(_sample_initiative_record())

        self.assertTrue(result.ok, result.detail)

    def test_validate_initiative_schema_accepts_valid_record(self) -> None:
        schema_path = pathlib.Path(__file__).resolve().parent.parent / "schema" / "initiative.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(
            schema,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )

        errors = sorted(
            validator.iter_errors(_sample_initiative_record()),
            key=lambda error: ([str(part) for part in error.path], error.message),
        )

        self.assertEqual(
            [],
            [
                f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
                for error in errors
            ],
        )

    def test_validate_initiative_record_rejects_track_only_budget_with_ceiling(self) -> None:
        data = _sample_initiative_record()
        data["budget_policy"]["max_input_tokens"] = 10

        self._assert_rejection(data, "INVALID_INITIATIVE_BUDGET_POLICY")

    def test_validate_initiative_record_rejects_multiple_active_tranches(self) -> None:
        data = _sample_initiative_record()
        data["tranches"][1]["status"] = "active"

        self._assert_rejection(data, "MULTIPLE_ACTIVE_INITIATIVE_TRANCHES")

    def test_validate_initiative_record_rejects_unknown_successor_tranche(self) -> None:
        data = _sample_initiative_record()
        data["tranches"][0]["successor"]["next_tranche_id"] = "missing-tranche"

        self._assert_rejection(data, "UNKNOWN_INITIATIVE_SUCCESSOR_TRANCHE")

    def test_validate_initiative_record_rejects_active_status_without_current_tranche(self) -> None:
        data = _sample_initiative_record()
        data["current_tranche_id"] = None

        self._assert_rejection(data, "INVALID_INITIATIVE_CURRENT_TRANCHE")

    def test_validate_initiative_record_rejects_next_step_outside_current_tranche(self) -> None:
        data = _sample_initiative_record()
        data["next_authorized_step"]["tranche_id"] = "runtime-root-separation"

        self._assert_rejection(data, "INVALID_INITIATIVE_NEXT_STEP")


class InitiativeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.plants_dir = self.root / "plants"
        (self.plants_dir / "gardener" / "memory").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _load_event_schema(self) -> dict:
        schema_path = pathlib.Path(__file__).resolve().parent.parent / "schema" / "event.schema.json"
        return json.loads(schema_path.read_text(encoding="utf-8"))

    def _assert_event_schema_valid(self, payload: dict) -> None:
        validator = Draft202012Validator(
            self._load_event_schema(),
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda error: ([str(part) for part in error.path], error.message),
        )
        self.assertEqual(
            [],
            [
                f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
                for error in errors
            ],
        )

    def _read_event_log(self) -> list[dict]:
        events_path = self.root / "events" / "coordinator.jsonl"
        if not events_path.exists():
            return []
        return [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_write_and_read_initiative_record_round_trip(self) -> None:
        data = _sample_initiative_record()

        with patch.dict(os.environ, {}, clear=True):
            result = write_initiative_record(
                "gardener",
                data["id"],
                data,
                _plants_dir=self.plants_dir,
            )
            stored = read_initiative_record(
                "gardener",
                data["id"],
                _plants_dir=self.plants_dir,
            )
            events = self._read_event_log()

        self.assertTrue(result.ok, result.detail)
        self.assertEqual(stored, data)
        self.assertEqual(
            [event["type"] for event in events],
            ["InitiativeRefreshStarted", "InitiativeRefreshFinished"],
        )

        started, finished = events
        self._assert_event_schema_valid(started)
        self._assert_event_schema_valid(finished)
        self.assertEqual(started["actor"], "gardener")
        self.assertEqual(started["plant"], "gardener")
        self.assertEqual(started["goal"], "386-using-the-results-of-goal-378-after-the")
        self.assertEqual(started["run"], data["updated_by_run"])
        self.assertEqual(
            started["initiative_path"],
            "plants/gardener/memory/initiatives/git-backed-run-memory-runtime-root-separation.json",
        )
        self.assertEqual(finished["initiative_outcome"], "success")

    def test_write_initiative_record_rejects_mismatched_target_id(self) -> None:
        data = _sample_initiative_record()

        with patch.dict(os.environ, {}, clear=True):
            result = write_initiative_record(
                "gardener",
                "other-initiative",
                data,
                _plants_dir=self.plants_dir,
            )
            events = self._read_event_log()

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_INITIATIVE_ID")
        self.assertIsNone(
            read_initiative_record(
                "gardener",
                "other-initiative",
                _plants_dir=self.plants_dir,
            )
        )
        self.assertEqual(
            [event["type"] for event in events],
            ["InitiativeRefreshStarted", "InitiativeRefreshFinished"],
        )

        started, finished = events
        self._assert_event_schema_valid(started)
        self._assert_event_schema_valid(finished)
        self.assertEqual(finished["initiative_outcome"], "validation_rejected")
        self.assertEqual(finished["initiative_reason"], "INVALID_INITIATIVE_ID")

    def test_write_initiative_record_rejects_non_object_payload_without_crashing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = write_initiative_record(
                "gardener",
                "git-backed-run-memory-runtime-root-separation",
                [],
                _plants_dir=self.plants_dir,
            )

        events = self._read_event_log()

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_INITIATIVE_SHAPE")
        self.assertEqual(
            [event["type"] for event in events],
            ["InitiativeRefreshStarted", "InitiativeRefreshFinished"],
        )

        started, finished = events
        self._assert_event_schema_valid(started)
        self._assert_event_schema_valid(finished)
        self.assertNotIn("goal", started)
        self.assertNotIn("run", started)
        self.assertEqual(finished["initiative_outcome"], "validation_rejected")
        self.assertEqual(finished["initiative_reason"], "INVALID_INITIATIVE_SHAPE")


class SubmitSameInitiativeCodeChangeWithEvaluateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "goals").mkdir(parents=True, exist_ok=True)
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)
        result = commission_plant(
            "reviewer",
            "reviewer",
            "gardener",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:01Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _conversation_env(self) -> dict:
        return {
            "PAK2_GARDEN_ROOT": str(self.root),
            "PAK2_CURRENT_GOAL_TYPE": "converse",
            "PAK2_CURRENT_GOAL_ID": "98-converse-turn",
            "PAK2_CURRENT_RUN_ID": "98-converse-turn-r1",
            "PAK2_CURRENT_PLANT": "gardener",
            "PAK2_CURRENT_CONVERSATION_ID": "1-hello",
            "PAK2_CURRENT_CONVERSATION_MESSAGE_ID": "msg-20260320200000-ope-abcd",
        }

    def test_submit_same_initiative_code_change_with_evaluate_submits_pair_with_dependency_and_metadata(self) -> None:
        now = "2026-03-24T00:10:00Z"
        with patch.dict(os.environ, self._conversation_env(), clear=False):
            result, goal_ids = submit_same_initiative_code_change_with_evaluate(
                submitted_by="gardener",
                implementation_goal_type="build",
                implementation_body="Using the clean tranche-closing decision, execute the next approved same-initiative tranche.",
                evaluate_body="After the implementation goal closes successfully, perform the mandatory tranche-closing evaluate stop.",
                implementation_assigned_to="gardener",
                evaluate_assigned_to="reviewer",
                implementation_depends_on=["98-converse-turn"],
                implementation_priority=3,
                driver="codex",
                model="gpt-5.4",
                reasoning_effort="medium",
                _goals_dir=self.root / "goals",
                _now=now,
            )

        self.assertTrue(result.ok, result.detail)
        self.assertIsNotNone(goal_ids)
        implementation_goal_id = goal_ids["implementation_goal_id"]
        evaluate_goal_id = goal_ids["evaluate_goal_id"]

        implementation_goal = read_goal(implementation_goal_id, _goals_dir=self.root / "goals")
        evaluate_goal = read_goal(evaluate_goal_id, _goals_dir=self.root / "goals")
        self.assertIsNotNone(implementation_goal)
        self.assertIsNotNone(evaluate_goal)

        self.assertEqual(implementation_goal["type"], "build")
        self.assertEqual(implementation_goal["assigned_to"], "gardener")
        self.assertEqual(implementation_goal["depends_on"], ["98-converse-turn"])
        self.assertEqual(implementation_goal["priority"], 3)
        self.assertEqual(implementation_goal["driver"], "codex")
        self.assertEqual(implementation_goal["model"], "gpt-5.4")
        self.assertEqual(implementation_goal["reasoning_effort"], "medium")
        self.assertEqual(
            implementation_goal["submitted_from"],
            {
                "goal_id": "98-converse-turn",
                "run_id": "98-converse-turn-r1",
                "ts": now,
                "plant": "gardener",
                "goal_type": "converse",
            },
        )
        self.assertEqual(
            implementation_goal["origin"],
            {
                "kind": "conversation",
                "conversation_id": "1-hello",
                "message_id": "msg-20260320200000-ope-abcd",
                "ts": now,
            },
        )
        self.assertEqual(implementation_goal["pre_dispatch_updates"], {"policy": "supplement"})

        self.assertEqual(evaluate_goal["type"], "evaluate")
        self.assertEqual(evaluate_goal["assigned_to"], "reviewer")
        self.assertEqual(evaluate_goal["depends_on"], [implementation_goal_id])
        self.assertEqual(evaluate_goal["priority"], 3)
        self.assertEqual(evaluate_goal["driver"], "codex")
        self.assertEqual(evaluate_goal["model"], "gpt-5.4")
        self.assertEqual(evaluate_goal["reasoning_effort"], "medium")
        self.assertEqual(evaluate_goal["submitted_from"], implementation_goal["submitted_from"])
        self.assertEqual(evaluate_goal["origin"], implementation_goal["origin"])
        self.assertEqual(evaluate_goal["pre_dispatch_updates"], {"policy": "supplement"})

        submitted = [
            event
            for event in read_events(path=self.root / "events" / "coordinator.jsonl")
            if event["type"] == "GoalSubmitted"
        ]
        self.assertEqual([event["goal"] for event in submitted], [implementation_goal_id, evaluate_goal_id])
        self.assertEqual([event["goal_type"] for event in submitted], ["build", "evaluate"])
        self.assertEqual(submitted[0]["conversation_id"], "1-hello")
        self.assertEqual(submitted[1]["source_goal_id"], "98-converse-turn")

    def test_submit_same_initiative_code_change_with_evaluate_rejects_non_code_changing_types(self) -> None:
        result, goal_ids = submit_same_initiative_code_change_with_evaluate(
            submitted_by="gardener",
            implementation_goal_type="research",
            implementation_body="Investigate the next step.",
            evaluate_body="Review the investigation.",
            _goals_dir=self.root / "goals",
            _now="2026-03-24T00:20:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_SAME_INITIATIVE_IMPLEMENTATION_GOAL_TYPE")
        self.assertIsNone(goal_ids)
        self.assertEqual(sorted((self.root / "goals").glob("*.json")), [])
        submitted = [
            event
            for event in read_events(path=self.root / "events" / "coordinator.jsonl")
            if event["type"] == "GoalSubmitted"
        ]
        self.assertEqual(submitted, [])

    def test_submit_same_initiative_code_change_with_evaluate_validates_follow_on_before_writing(self) -> None:
        result, goal_ids = submit_same_initiative_code_change_with_evaluate(
            submitted_by="gardener",
            implementation_goal_type="fix",
            implementation_body="Implement the bounded repair tranche.",
            evaluate_body="Review the bounded repair tranche.",
            evaluate_assigned_to="unknown-plant",
            _goals_dir=self.root / "goals",
            _now="2026-03-24T00:25:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "UNKNOWN_ASSIGNED_PLANT")
        self.assertIsNone(goal_ids)
        self.assertEqual(sorted((self.root / "goals").glob("*.json")), [])
        submitted = [
            event
            for event in read_events(path=self.root / "events" / "coordinator.jsonl")
            if event["type"] == "GoalSubmitted"
        ]
        self.assertEqual(submitted, [])


if __name__ == "__main__":
    unittest.main()
