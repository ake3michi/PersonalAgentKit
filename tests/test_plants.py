import pathlib
import tempfile
import unittest

from system.driver import _build_prompt
from system.events import read_events
from system.goals import read_goal
from system.plants import (
    commission_seeded_plant,
    read_plant,
    submit_initial_goal_for_plant,
)


class SeededPlantCommissioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "seeds").mkdir(parents=True, exist_ok=True)
        (self.root / "seeds" / "specialist.md").write_text(
            "# Specialist seed\n",
            encoding="utf-8",
        )
        (self.root / "seeds" / "specialist" / "skills").mkdir(parents=True)
        (self.root / "seeds" / "specialist" / "knowledge").mkdir(parents=True)
        (self.root / "seeds" / "specialist" / "skills" / "triage.md").write_text(
            "# Triage\n",
            encoding="utf-8",
        )
        (self.root / "seeds" / "specialist" / "knowledge" / "domain.md").write_text(
            "# Domain\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_commission_seeded_plant_writes_seed_reference_and_assets(self) -> None:
        result = commission_seeded_plant(
            "reviewer",
            "specialist",
            "gardener",
            _garden_root=self.root,
            _now="2026-03-24T01:00:00Z",
        )

        self.assertTrue(result.ok)
        plant = read_plant("reviewer", _plants_dir=self.root / "plants")
        self.assertIsNotNone(plant)
        self.assertEqual(plant["seed"], "specialist")
        self.assertEqual(
            (self.root / "plants" / "reviewer" / "seed").read_text(encoding="utf-8"),
            "specialist\n",
        )
        self.assertTrue(
            (self.root / "plants" / "reviewer" / "skills" / "triage.md").exists()
        )
        self.assertTrue(
            (self.root / "plants" / "reviewer" / "knowledge" / "domain.md").exists()
        )

        events = read_events(path=self.root / "events" / "coordinator.jsonl")
        self.assertEqual(events[-1]["type"], "PlantCommissioned")
        self.assertEqual(events[-1]["plant"], "reviewer")

    def test_commission_seeded_plant_rejects_missing_seed_prompt(self) -> None:
        result = commission_seeded_plant(
            "reviewer",
            "missing-seed",
            "gardener",
            _garden_root=self.root,
            _now="2026-03-24T01:00:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "SEED_NOT_FOUND")
        self.assertIsNone(read_plant("reviewer", _plants_dir=self.root / "plants"))

    def test_commission_seeded_plant_succeeds_without_seed_asset_directories(self) -> None:
        (self.root / "seeds" / "lightweight.md").write_text(
            "# Lightweight seed\n",
            encoding="utf-8",
        )

        result = commission_seeded_plant(
            "explorer",
            "lightweight",
            "gardener",
            _garden_root=self.root,
            _now="2026-03-24T01:00:00Z",
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            (self.root / "plants" / "explorer" / "seed").read_text(encoding="utf-8"),
            "lightweight\n",
        )
        self.assertTrue((self.root / "plants" / "explorer" / "skills").is_dir())
        self.assertTrue((self.root / "plants" / "explorer" / "knowledge").is_dir())


class InitialPlantGoalSubmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "seeds").mkdir(parents=True, exist_ok=True)
        (self.root / "seeds" / "specialist.md").write_text(
            "# Specialist seed\n",
            encoding="utf-8",
        )
        result = commission_seeded_plant(
            "reviewer",
            "specialist",
            "gardener",
            _garden_root=self.root,
            _now="2026-03-24T01:00:00Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_submit_initial_goal_for_plant_queues_goal_through_submission_api(self) -> None:
        result, goal_id = submit_initial_goal_for_plant(
            plant_name="reviewer",
            goal_type="build",
            submitted_by="gardener",
            body="Bootstrap the new specialist's first task.",
            priority=4,
            _goals_dir=self.root / "goals",
            _now="2026-03-24T01:05:00Z",
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")
        self.assertEqual(goal["assigned_to"], "reviewer")
        self.assertEqual(goal["priority"], 4)
        self.assertEqual(goal["status"], "queued")
        self.assertEqual(goal["type"], "build")

        events = read_events(path=self.root / "events" / "coordinator.jsonl")
        self.assertEqual(events[-1]["type"], "GoalSubmitted")
        self.assertEqual(events[-1]["goal"], goal_id)


class SeededPromptBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "seeds").mkdir(parents=True, exist_ok=True)
        (self.root / "seeds" / "reviewer.md").write_text(
            "# Reviewer Seed\n\nRead `plants/<your-name>/skills/review-pass.md`.\n",
            encoding="utf-8",
        )
        (self.root / "seeds" / "reviewer" / "skills").mkdir(parents=True)
        (self.root / "seeds" / "reviewer" / "knowledge").mkdir(parents=True)
        (self.root / "seeds" / "reviewer" / "skills" / "review-pass.md").write_text(
            "# Review Pass\n",
            encoding="utf-8",
        )
        (self.root / "seeds" / "reviewer" / "knowledge" / "review-scope.md").write_text(
            "# Reviewer Scope\n",
            encoding="utf-8",
        )
        (self.root / "MOTIVATION.md").write_text(
            "Keep the garden legible.\n",
            encoding="utf-8",
        )

        result = commission_seeded_plant(
            "reviewer",
            "reviewer",
            "gardener",
            _garden_root=self.root,
            _now="2026-03-24T01:10:00Z",
        )
        self.assertTrue(result.ok)
        self.goal = {
            "id": "365-review-the-reviewer-workflow",
            "type": "evaluate",
            "priority": 5,
            "assigned_to": "reviewer",
            "body": "Review the reviewer seed workflow.",
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_build_prompt_uses_seed_before_memory_exists(self) -> None:
        prompt = _build_prompt(
            self.goal,
            "365-review-the-reviewer-workflow-r1",
            "reviewer",
            self.root,
        )

        self.assertIn("# Reviewer Seed", prompt)
        self.assertIn("plants/<your-name>/skills/review-pass.md", prompt)
        self.assertNotIn("# Your Memory", prompt)
        self.assertNotIn("# Your Skills", prompt)
        self.assertNotIn("# Your Knowledge", prompt)

    def test_build_prompt_uses_seed_reference_for_later_plant(self) -> None:
        result = commission_seeded_plant(
            "audit-bot",
            "reviewer",
            "gardener",
            _garden_root=self.root,
            _now="2026-03-24T01:11:00Z",
        )
        self.assertTrue(result.ok)

        goal = {
            "id": "366-bootstrap-audit-bot",
            "type": "evaluate",
            "priority": 5,
            "assigned_to": "audit-bot",
            "body": "Review the reviewer seed workflow from a new plant name.",
        }

        prompt = _build_prompt(
            goal,
            "366-bootstrap-audit-bot-r1",
            "audit-bot",
            self.root,
        )

        self.assertIn("# Reviewer Seed", prompt)
        self.assertIn("plants/<your-name>/skills/review-pass.md", prompt)
        self.assertNotIn("# Your Memory", prompt)

    def test_build_prompt_switches_to_memory_and_indexes_after_bootstrap(self) -> None:
        (self.root / "plants" / "reviewer" / "memory" / "MEMORY.md").write_text(
            "# reviewer\n\nI handle bounded review work.\n",
            encoding="utf-8",
        )

        prompt = _build_prompt(
            self.goal,
            "365-review-the-reviewer-workflow-r1",
            "reviewer",
            self.root,
        )

        self.assertIn("# Your Memory", prompt)
        self.assertIn("I handle bounded review work.", prompt)
        self.assertIn("# Your Skills", prompt)
        self.assertIn("review-pass.md", prompt)
        self.assertIn("# Your Knowledge", prompt)
        self.assertIn("review-scope.md", prompt)
        self.assertNotIn("# Reviewer Seed", prompt)


if __name__ == "__main__":
    unittest.main()
