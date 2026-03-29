import contextlib
import io
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from system import cli
from system.events import read_events
from system.goals import read_goal
from system.plants import commission_plant
from system.submit import submit_goal, submit_retrospective_goal


class RawRetrospectiveGoalSubmissionTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _submit(self, data: dict):
        return submit_goal(
            data,
            _goals_dir=self.root / "goals",
            _now="2026-03-24T00:10:00Z",
        )

    def _payload(self, **overrides) -> dict:
        payload = {
            "window": "since_last_retrospective_or_recent",
            "recent_run_limit": 5,
            "action_boundary": "observe_only",
        }
        payload.update(overrides)
        return payload

    def test_submit_goal_accepts_valid_raw_retrospective_payload(self) -> None:
        result, goal_id = self._submit(
            {
                "type": "evaluate",
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "body": "Perform a bounded retrospective.",
                "retrospective": self._payload(),
            }
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")
        self.assertEqual(goal["retrospective"], self._payload())

    def test_submit_goal_rejects_retrospective_payload_on_non_evaluate_goal(self) -> None:
        result, goal_id = self._submit(
            {
                "type": "build",
                "submitted_by": "operator",
                "body": "This is not an evaluation.",
                "retrospective": self._payload(),
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "RETROSPECTIVE_REQUIRES_EVALUATE")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_non_object_retrospective_payload(self) -> None:
        result, goal_id = self._submit(
            {
                "type": "evaluate",
                "submitted_by": "operator",
                "body": "Perform a bounded retrospective.",
                "retrospective": "observe_only",
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_RETROSPECTIVE_PAYLOAD")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_unknown_retrospective_field(self) -> None:
        result, goal_id = self._submit(
            {
                "type": "evaluate",
                "submitted_by": "operator",
                "body": "Perform a bounded retrospective.",
                "retrospective": self._payload(extra="value"),
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "UNKNOWN_RETROSPECTIVE_FIELD")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_missing_retrospective_window(self) -> None:
        payload = self._payload()
        del payload["window"]
        result, goal_id = self._submit(
            {
                "type": "evaluate",
                "submitted_by": "operator",
                "body": "Perform a bounded retrospective.",
                "retrospective": payload,
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "MISSING_RETROSPECTIVE_WINDOW")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_invalid_retrospective_window(self) -> None:
        result, goal_id = self._submit(
            {
                "type": "evaluate",
                "submitted_by": "operator",
                "body": "Perform a bounded retrospective.",
                "retrospective": self._payload(window="recent_only"),
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_RETROSPECTIVE_WINDOW")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_missing_retrospective_recent_run_limit(self) -> None:
        payload = self._payload()
        del payload["recent_run_limit"]
        result, goal_id = self._submit(
            {
                "type": "evaluate",
                "submitted_by": "operator",
                "body": "Perform a bounded retrospective.",
                "retrospective": payload,
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "MISSING_RETROSPECTIVE_RECENT_RUN_LIMIT")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_invalid_retrospective_recent_run_limit(self) -> None:
        result, goal_id = self._submit(
            {
                "type": "evaluate",
                "submitted_by": "operator",
                "body": "Perform a bounded retrospective.",
                "retrospective": self._payload(recent_run_limit=2),
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_RETROSPECTIVE_RECENT_RUN_LIMIT")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_missing_retrospective_action_boundary(self) -> None:
        payload = self._payload()
        del payload["action_boundary"]
        result, goal_id = self._submit(
            {
                "type": "evaluate",
                "submitted_by": "operator",
                "body": "Perform a bounded retrospective.",
                "retrospective": payload,
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "MISSING_RETROSPECTIVE_ACTION_BOUNDARY")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_invalid_retrospective_action_boundary(self) -> None:
        result, goal_id = self._submit(
            {
                "type": "evaluate",
                "submitted_by": "operator",
                "body": "Perform a bounded retrospective.",
                "retrospective": self._payload(action_boundary="many_follow_ups"),
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_RETROSPECTIVE_ACTION_BOUNDARY")
        self.assertIsNone(goal_id)


class SubmitRetrospectiveGoalTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_submit_retrospective_goal_records_explicit_contract_and_submission_event(self) -> None:
        result, goal_id = submit_retrospective_goal(
            submitted_by="operator",
            recent_run_limit=10,
            allow_follow_up_goal=True,
            priority=4,
            driver="codex",
            model="gpt-5.4",
            reasoning_effort="high",
            _goals_dir=self.root / "goals",
            _now="2026-03-24T00:15:00Z",
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")
        self.assertEqual(goal["type"], "evaluate")
        self.assertEqual(goal["assigned_to"], "gardener")
        self.assertEqual(goal["priority"], 4)
        self.assertEqual(goal["driver"], "codex")
        self.assertEqual(goal["model"], "gpt-5.4")
        self.assertEqual(goal["reasoning_effort"], "high")
        self.assertEqual(
            goal["retrospective"],
            {
                "window": "since_last_retrospective_or_recent",
                "recent_run_limit": 10,
                "action_boundary": "allow_one_bounded_follow_up_goal",
            },
        )
        self.assertIn("`retrospective.window`", goal["body"])
        self.assertIn("`retrospective.recent_run_limit`: `10`", goal["body"])
        self.assertIn(
            "`retrospective.action_boundary`: `allow_one_bounded_follow_up_goal`",
            goal["body"],
        )

        events = read_events(path=self.root / "events" / "coordinator.jsonl")
        submitted = [event for event in events if event["type"] == "GoalSubmitted"]
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["goal"], goal_id)
        self.assertEqual(submitted[0]["goal_type"], "evaluate")


class RetrospectiveCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _args(self, **overrides) -> SimpleNamespace:
        data = {
            "root": str(self.root),
            "submitted_by": "operator",
            "recent_run_limit": 5,
            "allow_follow_up_goal": False,
            "priority": None,
            "driver": None,
            "model": None,
            "reasoning_effort": None,
        }
        data.update(overrides)
        return SimpleNamespace(**data)

    def test_main_submits_retrospective_goal(self) -> None:
        stdout = io.StringIO()
        argv = [
            "pak2",
            "retrospective",
            "--root",
            str(self.root),
            "--recent-run-limit",
            "6",
            "--allow-follow-up-goal",
            "--priority",
            "3",
            "--driver",
            "codex",
            "--model",
            "gpt-5.4",
            "--reasoning-effort",
            "medium",
        ]

        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
            cli.main()

        output = stdout.getvalue()
        self.assertIn("Submitted:", output)
        goal_files = sorted((self.root / "goals").glob("*.json"))
        self.assertEqual(len(goal_files), 1)
        goal = read_goal(goal_files[0].stem, _goals_dir=self.root / "goals")
        self.assertEqual(goal["assigned_to"], "gardener")
        self.assertEqual(goal["retrospective"]["recent_run_limit"], 6)
        self.assertEqual(
            goal["retrospective"]["action_boundary"],
            "allow_one_bounded_follow_up_goal",
        )
        self.assertEqual(goal["priority"], 3)
        self.assertEqual(goal["driver"], "codex")
        self.assertEqual(goal["model"], "gpt-5.4")
        self.assertEqual(goal["reasoning_effort"], "medium")

    def test_cmd_retrospective_exits_with_named_validation_error(self) -> None:
        stderr = io.StringIO()

        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(stderr):
            cli.cmd_retrospective(self._args(recent_run_limit=2))

        self.assertEqual(cm.exception.code, 1)
        self.assertIn("INVALID_RETROSPECTIVE_RECENT_RUN_LIMIT", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
