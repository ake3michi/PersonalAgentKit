import pathlib
import tempfile
import unittest

from system.driver import _build_prompt
from system.events import read_events
from system.goals import read_goal
from system.plants import commission_plant, execute_plant_commission, read_plant
from system.submit import submit_goal, submit_plant_commission_goal


class RawPlantCommissionGoalSubmissionTests(unittest.TestCase):
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

    def _payload(self, **overrides) -> dict:
        payload = {
            "plant_name": "reviewer",
            "seed": "reviewer",
            "initial_goal": {
                "type": "evaluate",
                "body": "Perform the first bounded independent review.",
                "priority": 4,
                "reasoning_effort": "high",
            },
        }
        payload.update(overrides)
        return payload

    def _submit(self, data: dict):
        return submit_goal(
            data,
            _goals_dir=self.root / "goals",
            _now="2026-03-24T00:10:00Z",
        )

    def test_submit_goal_accepts_valid_raw_plant_commission_payload(self) -> None:
        result, goal_id = self._submit(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Commission a dedicated later plant.",
                "plant_commission": self._payload(),
            }
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")
        self.assertEqual(goal["plant_commission"], self._payload())

    def test_submit_goal_rejects_plant_commission_on_non_build_goal(self) -> None:
        result, goal_id = self._submit(
            {
                "type": "evaluate",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "This is not a build goal.",
                "plant_commission": self._payload(),
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "PLANT_COMMISSION_REQUIRES_BUILD")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_plant_commission_without_gardener_assignment(self) -> None:
        result = commission_plant(
            "reviewer",
            "reviewer",
            "gardener",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:01Z",
        )
        self.assertTrue(result.ok)

        result, goal_id = self._submit(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "reviewer",
                "body": "Wrong plant for the commission surface.",
                "plant_commission": self._payload(),
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "PLANT_COMMISSION_REQUIRES_GARDENER")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_unknown_plant_commission_field(self) -> None:
        payload = self._payload(extra="value")
        result, goal_id = self._submit(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Unknown field should fail.",
                "plant_commission": payload,
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "UNKNOWN_PLANT_COMMISSION_FIELD")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_invalid_initial_goal_type(self) -> None:
        payload = self._payload(initial_goal={"type": "converse", "body": "Nope."})
        result, goal_id = self._submit(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Bad initial goal type.",
                "plant_commission": payload,
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_PLANT_COMMISSION_INITIAL_GOAL_TYPE")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_invalid_initial_goal_priority(self) -> None:
        payload = self._payload(
            initial_goal={
                "type": "evaluate",
                "body": "Review it.",
                "priority": 0,
            }
        )
        result, goal_id = self._submit(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Bad initial goal priority.",
                "plant_commission": payload,
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_PLANT_COMMISSION_INITIAL_GOAL_PRIORITY")
        self.assertIsNone(goal_id)


class PlantCommissionGoalWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "seeds").mkdir(parents=True, exist_ok=True)
        (self.root / "seeds" / "gardener.md").write_text(
            "# Gardener Seed\n",
            encoding="utf-8",
        )
        (self.root / "seeds" / "reviewer.md").write_text(
            "# Reviewer Seed\n",
            encoding="utf-8",
        )
        (self.root / "seeds" / "reviewer" / "skills").mkdir(parents=True)
        (self.root / "seeds" / "reviewer" / "knowledge").mkdir(parents=True)
        (self.root / "MOTIVATION.md").write_text(
            "Keep the garden legible.\n",
            encoding="utf-8",
        )

        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T01:00:00Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_submit_plant_commission_goal_records_dedicated_goal_surface(self) -> None:
        result, goal_id = submit_plant_commission_goal(
            submitted_by="gardener",
            plant_name="reviewer",
            seed="reviewer",
            initial_goal_type="evaluate",
            initial_goal_body="Perform the first bounded independent review.",
            initial_goal_priority=4,
            initial_goal_driver="codex",
            initial_goal_model="gpt-5.4",
            initial_goal_reasoning_effort="high",
            priority=3,
            _goals_dir=self.root / "goals",
            _now="2026-03-24T01:10:00Z",
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")
        self.assertEqual(goal["type"], "build")
        self.assertEqual(goal["assigned_to"], "gardener")
        self.assertEqual(goal["priority"], 3)
        self.assertEqual(goal["plant_commission"]["plant_name"], "reviewer")
        self.assertEqual(goal["plant_commission"]["seed"], "reviewer")
        self.assertEqual(goal["plant_commission"]["initial_goal"]["type"], "evaluate")
        self.assertIn("dedicated commissioning goal surface", goal["body"])
        self.assertIn("Do not hardcode `_now` outside tests.", goal["body"])

        events = read_events(path=self.root / "events" / "coordinator.jsonl")
        submitted = [event for event in events if event["type"] == "GoalSubmitted"]
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["goal"], goal_id)
        self.assertEqual(submitted[0]["goal_subtype"], "plant_commission")

    def test_execute_plant_commission_uses_one_timestamp_for_plant_and_initial_goal(self) -> None:
        result, goal_id = submit_plant_commission_goal(
            submitted_by="gardener",
            plant_name="reviewer",
            seed="reviewer",
            initial_goal_type="evaluate",
            initial_goal_body="Perform the first bounded independent review.",
            initial_goal_priority=4,
            initial_goal_driver="codex",
            initial_goal_model="gpt-5.4",
            initial_goal_reasoning_effort="high",
            _goals_dir=self.root / "goals",
            _now="2026-03-24T01:15:00Z",
        )
        self.assertTrue(result.ok)
        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")

        result, first_goal_id = execute_plant_commission(
            goal,
            commissioned_by="gardener",
            _garden_root=self.root,
            _goals_dir=self.root / "goals",
            _now="2026-03-24T01:20:00Z",
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(first_goal_id)

        plant = read_plant("reviewer", _plants_dir=self.root / "plants")
        self.assertEqual(plant["created_at"], "2026-03-24T01:20:00Z")

        first_goal = read_goal(first_goal_id or "", _goals_dir=self.root / "goals")
        self.assertEqual(first_goal["submitted_at"], "2026-03-24T01:20:00Z")
        self.assertEqual(first_goal["assigned_to"], "reviewer")
        self.assertEqual(first_goal["depends_on"], [goal["id"]])
        self.assertEqual(first_goal["priority"], 4)
        self.assertEqual(first_goal["driver"], "codex")
        self.assertEqual(first_goal["model"], "gpt-5.4")
        self.assertEqual(first_goal["reasoning_effort"], "high")

        events = read_events(path=self.root / "events" / "coordinator.jsonl")
        plant_event = [event for event in events if event["type"] == "PlantCommissioned"][-1]
        self.assertEqual(plant_event["ts"], "2026-03-24T01:20:00Z")
        self.assertEqual(plant_event["plant"], "reviewer")

        submitted = [event for event in events if event["type"] == "GoalSubmitted"]
        self.assertEqual(submitted[-1]["ts"], "2026-03-24T01:20:00Z")
        self.assertEqual(submitted[-1]["goal"], first_goal_id)

    def test_build_prompt_includes_plant_commission_context(self) -> None:
        result, goal_id = submit_plant_commission_goal(
            submitted_by="gardener",
            plant_name="reviewer",
            seed="reviewer",
            initial_goal_type="evaluate",
            initial_goal_body="Perform the first bounded independent review.",
            _goals_dir=self.root / "goals",
            _now="2026-03-24T01:25:00Z",
        )
        self.assertTrue(result.ok)
        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")

        prompt = _build_prompt(
            goal,
            f"{goal_id}-r1",
            "gardener",
            self.root,
        )

        self.assertIn("# Plant Commission Context", prompt)
        self.assertIn("Plant name: reviewer", prompt)
        self.assertIn("Seed: reviewer", prompt)
        self.assertIn("Initial goal type: evaluate", prompt)
        self.assertIn("Use `system.plants.execute_plant_commission()`", prompt)


if __name__ == "__main__":
    unittest.main()
