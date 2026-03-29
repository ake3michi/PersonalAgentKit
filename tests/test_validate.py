import pathlib
import tempfile
import unittest

from system.plants import archive_plant, commission_plant
from system.submit import submit_goal
from system.validate import validate_plant


class PlantValidationTests(unittest.TestCase):
    def test_validate_plant_accepts_valid_record(self) -> None:
        result = validate_plant(
            {
                "name": "reviewer",
                "seed": "specialist",
                "status": "active",
                "created_at": "2026-03-24T02:00:00Z",
                "commissioned_by": "gardener",
            }
        )

        self.assertTrue(result.ok)

    def test_validate_plant_rejects_missing_seed(self) -> None:
        result = validate_plant(
            {
                "name": "reviewer",
                "seed": "   ",
                "status": "active",
                "created_at": "2026-03-24T02:00:00Z",
                "commissioned_by": "gardener",
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "MISSING_SEED")


class GoalAssignedPlantValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T02:10:00Z",
        )
        self.assertTrue(result.ok)
        result = commission_plant(
            "reviewer",
            "specialist",
            "gardener",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T02:10:01Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_submit_goal_accepts_commissioned_active_assigned_plant(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "reviewer",
                "body": "Ship the first bounded task.",
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-24T02:15:00Z",
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)

    def test_submit_goal_accepts_unassigned_goal(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "research",
                "submitted_by": "gardener",
                "body": "Leave this unassigned for routing.",
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-24T02:15:00Z",
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)

    def test_submit_goal_rejects_unknown_assigned_plant(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "unknown-plant",
                "body": "This should fail cleanly.",
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-24T02:15:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "UNKNOWN_ASSIGNED_PLANT")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_archived_assigned_plant(self) -> None:
        result = archive_plant(
            "reviewer",
            actor="gardener",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T02:14:00Z",
        )
        self.assertTrue(result.ok)

        result, goal_id = submit_goal(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "reviewer",
                "body": "This should not dispatch.",
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-24T02:15:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "ASSIGNED_PLANT_INACTIVE")
        self.assertIsNone(goal_id)


if __name__ == "__main__":
    unittest.main()
