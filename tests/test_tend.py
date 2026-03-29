import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from system import driver
from system.coordinator import _open_and_transition
from system.events import read_events
from system.garden import set_garden_name
from system.goals import list_goals, read_goal
from system.operator_messages import emit_tend_survey, read_operator_message_records
from system.plants import commission_plant
from system.submit import submit_goal, submit_tend_goal


class RawTendGoalSubmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "goals").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_submit_goal_accepts_valid_raw_tend_payload(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "body": "Perform a bounded tend pass.",
                "tend": {
                    "trigger_kinds": ["operator_request"],
                    "trigger_goal": "98-converse-turn",
                    "trigger_run": "98-converse-turn-r1",
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-21T10:30:00Z",
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")
        self.assertEqual(
            goal["tend"],
            {
                "trigger_kinds": ["operator_request"],
                "trigger_goal": "98-converse-turn",
                "trigger_run": "98-converse-turn-r1",
            },
        )

    def test_submit_goal_rejects_raw_tend_without_payload(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "body": "Perform a bounded tend pass.",
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-21T10:30:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "MISSING_TEND_PAYLOAD")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_raw_tend_without_trigger_kinds(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "body": "Perform a bounded tend pass.",
                "tend": {},
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-21T10:30:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "MISSING_TEND_TRIGGER_KINDS")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_raw_tend_with_malformed_trigger_kinds(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "body": "Perform a bounded tend pass.",
                "tend": {
                    "trigger_kinds": "operator_request",
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-21T10:30:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_TEND_TRIGGER_KINDS")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_raw_tend_with_unknown_trigger_kind(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "body": "Perform a bounded tend pass.",
                "tend": {
                    "trigger_kinds": ["mystery_trigger"],
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-21T10:30:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_TEND_TRIGGER_KIND")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_raw_tend_with_duplicate_trigger_kinds(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "body": "Perform a bounded tend pass.",
                "tend": {
                    "trigger_kinds": [
                        "operator_request",
                        "operator_request",
                    ],
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-21T10:30:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_TEND_TRIGGER_KINDS")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_raw_tend_with_whitespace_padded_trigger_kind(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "body": "Perform a bounded tend pass.",
                "tend": {
                    "trigger_kinds": [" operator_request "],
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-21T10:30:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_TEND_TRIGGER_KINDS")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_raw_tend_with_invalid_trigger_goal(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "body": "Perform a bounded tend pass.",
                "tend": {
                    "trigger_kinds": ["operator_request"],
                    "trigger_goal": "bad goal id",
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-21T10:30:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_TEND_TRIGGER_GOAL")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_raw_tend_with_invalid_trigger_run(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "body": "Perform a bounded tend pass.",
                "tend": {
                    "trigger_kinds": ["operator_request"],
                    "trigger_run": "bad-run-id",
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-21T10:30:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_TEND_TRIGGER_RUN")
        self.assertIsNone(goal_id)

    def test_submit_goal_rejects_raw_tend_with_unknown_field(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "body": "Perform a bounded tend pass.",
                "tend": {
                    "trigger_kinds": ["operator_request"],
                    "unexpected": "value",
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-21T10:30:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "UNKNOWN_TEND_FIELD")
        self.assertIsNone(goal_id)


class SubmitTendGoalTests(unittest.TestCase):
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

    def _conversation_env(self) -> dict:
        return {
            "PAK2_GARDEN_ROOT": str(self.root),
            "PAK2_CURRENT_GOAL_TYPE": "converse",
            "PAK2_CURRENT_GOAL_ID": "98-converse-turn",
            "PAK2_CURRENT_RUN_ID": "98-converse-turn-r1",
            "PAK2_CURRENT_PLANT": "gardener",
            "PAK2_CURRENT_CONVERSATION_ID": "1-hello",
            "PAK2_CURRENT_CONVERSATION_MESSAGE_ID": "msg-20260320210000-ope-abcd",
        }

    def test_submit_tend_goal_tags_origin_source_and_default_priority(self) -> None:
        now = "2026-03-20T21:00:00Z"
        with patch.dict(os.environ, self._conversation_env(), clear=False):
            result, goal_id = submit_tend_goal(
                body="Perform a bounded tend pass for the requested garden survey.",
                submitted_by="gardener",
                trigger_kinds=["operator_request"],
                driver="codex",
                model="gpt-5.4",
                _goals_dir=self.root / "goals",
                _now=now,
            )

        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)

        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")
        self.assertEqual(goal["type"], "tend")
        self.assertEqual(goal["priority"], 6)
        self.assertEqual(
            goal["origin"],
            {
                "kind": "conversation",
                "conversation_id": "1-hello",
                "message_id": "msg-20260320210000-ope-abcd",
                "ts": now,
            },
        )
        self.assertEqual(goal["pre_dispatch_updates"], {"policy": "supplement"})
        self.assertEqual(
            goal["submitted_from"],
            {
                "goal_id": "98-converse-turn",
                "run_id": "98-converse-turn-r1",
                "ts": now,
                "plant": "gardener",
                "goal_type": "converse",
            },
        )
        self.assertEqual(
            goal["tend"],
            {
                "trigger_kinds": ["operator_request"],
                "trigger_goal": "98-converse-turn",
                "trigger_run": "98-converse-turn-r1",
            },
        )

    def test_submit_tend_goal_deduplicates_when_tend_is_already_active(self) -> None:
        first_result, first_goal_id = submit_tend_goal(
            body="First bounded tend pass.",
            submitted_by="gardener",
            trigger_kinds=["post_genesis"],
            _goals_dir=self.root / "goals",
            _now="2026-03-20T21:01:00Z",
        )
        self.assertTrue(first_result.ok)
        self.assertIsNotNone(first_goal_id)

        second_result, second_goal_id = submit_tend_goal(
            body="Second bounded tend pass.",
            submitted_by="gardener",
            trigger_kinds=["operator_request"],
            _goals_dir=self.root / "goals",
            _now="2026-03-20T21:01:05Z",
        )
        self.assertTrue(second_result.ok)
        self.assertEqual(second_goal_id, first_goal_id)
        self.assertEqual(len(list_goals(_goals_dir=self.root / "goals")), 1)


class TendRuntimeObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "goals").mkdir(parents=True, exist_ok=True)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        (self.root / "events").mkdir(parents=True, exist_ok=True)
        (self.root / "inbox" / "garden").mkdir(parents=True, exist_ok=True)
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)
        (self.root / "plants" / "gardener" / "memory" / "MEMORY.md").write_text(
            "Initial durable memory.\n",
            encoding="utf-8",
        )
        (self.root / "MOTIVATION.md").write_text(
            "Keep the garden coherent.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_dispatch_emits_tend_lifecycle_events_and_summary(self) -> None:
        now = "2026-03-20T21:10:00Z"
        result, goal_id = submit_tend_goal(
            body="Perform a bounded post-genesis tend pass.",
            submitted_by="gardener",
            trigger_kinds=["post_genesis"],
            driver="codex",
            model="gpt-5.4",
            _goals_dir=self.root / "goals",
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)

        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")
        assert goal is not None
        run_id = _open_and_transition(goal, now, self.root / "goals", self.root / "runs")
        self.assertIsNotNone(run_id)

        prompt_holder = {"prompt": None, "follow_up_goal_id": None}

        def fake_launch(model, prompt, events_path, **kwargs):
            prompt_holder["prompt"] = prompt
            env = kwargs.get("env") or {}
            with patch.dict(os.environ, env, clear=False):
                result, follow_up_goal_id = submit_goal(
                    {
                        "type": "fix",
                        "submitted_by": "gardener",
                        "assigned_to": "gardener",
                        "body": "Follow up on the tend findings.",
                    },
                    _goals_dir=self.root / "goals",
                    _now="2026-03-20T21:10:10Z",
                )
            self.assertTrue(result.ok)
            prompt_holder["follow_up_goal_id"] = follow_up_goal_id

            (self.root / "plants" / "gardener" / "memory" / "MEMORY.md").write_text(
                "Updated durable memory.\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, env, clear=False):
                result, _ = emit_tend_survey(
                    "State note.\n",
                    _garden_root=self.root,
                    _now="2026-03-20T21:10:11Z",
                )
            self.assertTrue(result.ok)
            events_path.write_text("{\"type\":\"result\"}\n", encoding="utf-8")
            return 0

        with patch.object(driver, "_launch", side_effect=fake_launch), patch.object(
            driver, "_parse_events", return_value=({"source": "unknown"}, "success")
        ), patch.object(driver, "_solicit_reflection", return_value="Reflection text."):
            driver.dispatch(goal, run_id or "", _garden_root=self.root)

        prompt = prompt_holder["prompt"] or ""
        self.assertIn("# Tend Context", prompt)
        self.assertIn("Trigger kinds: post_genesis", prompt)
        self.assertIn(
            "Operator-facing framing: bounded environment orientation of the local garden.",
            prompt,
        )
        self.assertIn(
            "describe the work to the operator as environment orientation",
            prompt,
        )
        self.assertIn("Goal priority: `4`", prompt)
        self.assertIn(
            "submit one bounded follow-up goal during this run rather than only reporting it.",
            prompt,
        )

        events = read_events(path=self.root / "events" / "coordinator.jsonl")
        tend_started = next(event for event in events if event["type"] == "TendStarted")
        tend_finished = next(event for event in events if event["type"] == "TendFinished")
        run_finished = next(event for event in events if event["type"] == "RunFinished")

        self.assertEqual(tend_started["goal"], goal_id)
        self.assertEqual(tend_started["run"], run_id)
        self.assertEqual(tend_started["trigger_kinds"], ["post_genesis"])
        self.assertEqual(tend_started["goal_priority"], 4)

        self.assertEqual(tend_finished["goal"], goal_id)
        self.assertEqual(tend_finished["run"], run_id)
        self.assertEqual(tend_finished["follow_up_goal_count"], 1)
        self.assertEqual(
            tend_finished["follow_up_goals"],
            [prompt_holder["follow_up_goal_id"]],
        )
        self.assertTrue(tend_finished["memory_updated"])
        self.assertTrue(tend_finished["operator_note_written"])
        self.assertEqual(
            tend_finished["operator_note_path"],
            "inbox/garden/notes/20260320T211011-state-note.md",
        )
        self.assertLess(events.index(tend_finished), events.index(run_finished))

        records = read_operator_message_records(run_id or "", _garden_root=self.root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "tend_survey")
        self.assertEqual(records[0]["delivery_policy"], "out_of_band_note")
        self.assertEqual(
            records[0]["delivery_path"],
            "inbox/garden/notes/20260320T211011-state-note.md",
        )

        follow_up_goal = read_goal(
            prompt_holder["follow_up_goal_id"] or "",
            _goals_dir=self.root / "goals",
        )
        self.assertEqual(follow_up_goal["submitted_from"]["run_id"], run_id)

        run_meta = json.loads(
            (self.root / "runs" / (run_id or "") / "meta.json").read_text(encoding="utf-8")
        )
        self.assertEqual(run_meta["status"], "success")

    def test_tend_finished_tracks_operator_note_in_configured_reply_directory(self) -> None:
        result = set_garden_name("sprout", garden_root=self.root)
        self.assertTrue(result.ok)

        (self.root / "plants" / "gardener" / "memory").mkdir(parents=True, exist_ok=True)
        (self.root / "plants" / "gardener" / "memory" / "MEMORY.md").write_text(
            "Initial durable memory.\n",
            encoding="utf-8",
        )

        result, goal_id = submit_tend_goal(
            body="Perform a bounded tend pass.",
            submitted_by="gardener",
            trigger_kinds=["operator_request"],
            _goals_dir=self.root / "goals",
            _now="2026-03-20T21:10:00Z",
        )
        self.assertTrue(result.ok)

        goal = read_goal(goal_id or "", _goals_dir=self.root / "goals")
        run_id = _open_and_transition(
            goal,
            "2026-03-20T21:10:10Z",
            self.root / "goals",
            self.root / "runs",
        )
        self.assertIsNotNone(run_id)

        def fake_launch(*args, **kwargs):
            events_path = args[2]
            env = kwargs.get("env") or {}
            (self.root / "plants" / "gardener" / "memory" / "MEMORY.md").write_text(
                "Updated durable memory.\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, env, clear=False):
                result, _ = emit_tend_survey(
                    "State note.\n",
                    _garden_root=self.root,
                    _now="2026-03-20T21:10:11Z",
                )
            self.assertTrue(result.ok)
            events_path.write_text("{\"type\":\"result\"}\n", encoding="utf-8")
            return 0

        with patch.object(driver, "_launch", side_effect=fake_launch), patch.object(
            driver, "_parse_events", return_value=({"source": "unknown"}, "success")
        ), patch.object(driver, "_solicit_reflection", return_value="Reflection text."):
            driver.dispatch(goal, run_id or "", _garden_root=self.root)

        events = read_events(path=self.root / "events" / "coordinator.jsonl")
        tend_finished = next(event for event in events if event["type"] == "TendFinished")
        self.assertEqual(
            tend_finished["operator_note_path"],
            "inbox/sprout/notes/20260320T211011-state-note.md",
        )


if __name__ == "__main__":
    unittest.main()
