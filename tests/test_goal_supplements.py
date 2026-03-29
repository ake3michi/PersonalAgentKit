import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from system.driver import _build_prompt
from system.events import read_events
from system.goals import (
    list_goal_supplements,
    materialize_dispatch_packet,
    read_goal,
    transition_goal,
)
from system.plants import commission_plant
from system.submit import append_goal_supplement, submit_goal


class GoalSupplementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.goals_dir = self.root / "goals"
        self.runs_dir = self.root / "runs"
        self.events_path = self.root / "events" / "coordinator.jsonl"
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)
        (self.root / "MOTIVATION.md").write_text(
            "Keep the garden coherent across runs.\n",
            encoding="utf-8",
        )
        (self.root / "plants" / "gardener" / "memory" / "MEMORY.md").write_text(
            "The garden remembers durable operator context.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _conversation_env(self, *, message_id: str = "msg-20260320200000-ope-abcd") -> dict:
        return {
            "PAK2_GARDEN_ROOT": str(self.root),
            "PAK2_CURRENT_GOAL_TYPE": "converse",
            "PAK2_CURRENT_GOAL_ID": "98-converse-turn",
            "PAK2_CURRENT_RUN_ID": "98-converse-turn-r1",
            "PAK2_CURRENT_PLANT": "gardener",
            "PAK2_CURRENT_CONVERSATION_ID": "1-hello",
            "PAK2_CURRENT_CONVERSATION_MESSAGE_ID": message_id,
        }

    def _submit_conversation_goal(self, body: str, *, now: str) -> str:
        with patch.dict(os.environ, self._conversation_env(), clear=False):
            result, goal_id = submit_goal(
                {
                    "type": "fix",
                    "submitted_by": "gardener",
                    "assigned_to": "gardener",
                    "priority": 5,
                    "driver": "codex",
                    "model": "gpt-5.4",
                    "body": body,
                },
                _goals_dir=self.goals_dir,
                _now=now,
            )
        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        return goal_id or ""

    def test_submit_goal_from_converse_run_tags_origin_and_policy(self) -> None:
        now = "2026-03-20T20:00:00Z"
        goal_id = self._submit_conversation_goal("Original immutable task body.", now=now)

        goal = read_goal(goal_id, _goals_dir=self.goals_dir)
        self.assertEqual(goal["body"], "Original immutable task body.")
        self.assertNotIn("conversation_id", goal)
        self.assertEqual(
            goal["origin"],
            {
                "kind": "conversation",
                "conversation_id": "1-hello",
                "message_id": "msg-20260320200000-ope-abcd",
                "ts": now,
            },
        )
        self.assertEqual(goal["pre_dispatch_updates"], {"policy": "supplement"})

        event = read_events(path=self.events_path)[-1]
        self.assertEqual(event["type"], "GoalSubmitted")
        self.assertEqual(event["goal"], goal_id)
        self.assertEqual(event["goal_origin"], "conversation")
        self.assertEqual(event["conversation_id"], "1-hello")
        self.assertEqual(event["source_message_id"], "msg-20260320200000-ope-abcd")

    def test_submit_goal_rejects_explicit_conversation_origin_missing_ts(self) -> None:
        initial_events = read_events(path=self.events_path)

        result, goal_id = submit_goal(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "priority": 5,
                "body": "Retry the bounded operator-facing note-routing slice.",
                "origin": {
                    "kind": "conversation",
                    "conversation_id": "1-hello",
                    "message_id": "msg-20260320200000-ope-abcd",
                },
            },
            _goals_dir=self.goals_dir,
            _now="2026-03-20T20:05:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_GOAL_ORIGIN")
        self.assertEqual(result.detail, "goal.origin missing required field: ts")
        self.assertIsNone(goal_id)
        self.assertFalse(self.goals_dir.exists())
        self.assertEqual(read_events(path=self.events_path), initial_events)

    def test_append_goal_supplement_records_append_only_event(self) -> None:
        goal_id = self._submit_conversation_goal(
            "Implement the durable fix.",
            now="2026-03-20T20:01:00Z",
        )

        with patch.dict(
            os.environ,
            self._conversation_env(message_id="msg-20260320200110-ope-efgh"),
            clear=False,
        ):
            result, supplement_id = append_goal_supplement(
                goal_id,
                {
                    "kind": "clarification",
                    "content": "Preserve the current API shape.",
                },
                _goals_dir=self.goals_dir,
                _now="2026-03-20T20:01:10Z",
            )
        self.assertTrue(result.ok)
        self.assertIsNotNone(supplement_id)

        supplements = list_goal_supplements(goal_id, _goals_dir=self.goals_dir)
        self.assertEqual(len(supplements), 1)
        self.assertEqual(supplements[0]["id"], supplement_id)
        self.assertEqual(supplements[0]["kind"], "clarification")
        self.assertEqual(supplements[0]["content"], "Preserve the current API shape.")
        self.assertEqual(
            supplements[0]["source"],
            {
                "kind": "conversation",
                "conversation_id": "1-hello",
                "message_id": "msg-20260320200110-ope-efgh",
            },
        )
        self.assertEqual(supplements[0]["source_goal_id"], "98-converse-turn")
        self.assertEqual(supplements[0]["source_run_id"], "98-converse-turn-r1")

        event = read_events(path=self.events_path)[-1]
        self.assertEqual(event["type"], "GoalSupplemented")
        self.assertEqual(event["goal"], goal_id)
        self.assertEqual(event["supplement_id"], supplement_id)
        self.assertEqual(event["supplement_kind"], "clarification")
        self.assertEqual(event["conversation_id"], "1-hello")
        self.assertEqual(event["source_message_id"], "msg-20260320200110-ope-efgh")

    def test_append_goal_supplement_rejects_non_queued_goal(self) -> None:
        goal_id = self._submit_conversation_goal(
            "Queued task before dispatch.",
            now="2026-03-20T20:02:00Z",
        )
        result = transition_goal(
            goal_id,
            "dispatched",
            _goals_dir=self.goals_dir,
            _now="2026-03-20T20:02:05Z",
        )
        self.assertTrue(result.ok)

        with patch.dict(os.environ, self._conversation_env(), clear=False):
            result, supplement_id = append_goal_supplement(
                goal_id,
                {
                    "kind": "clarification",
                    "content": "This should be rejected.",
                },
                _goals_dir=self.goals_dir,
                _now="2026-03-20T20:02:10Z",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "GOAL_NOT_QUEUED")
        self.assertIsNone(supplement_id)

    def test_append_goal_supplement_rejects_malformed_payload(self) -> None:
        goal_id = self._submit_conversation_goal(
            "Queued task before dispatch.",
            now="2026-03-20T20:02:00Z",
        )

        with patch.dict(os.environ, self._conversation_env(), clear=False):
            result, supplement_id = append_goal_supplement(
                goal_id,
                ["not", "an", "object"],  # type: ignore[arg-type]
                _goals_dir=self.goals_dir,
                _now="2026-03-20T20:02:10Z",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_SHAPE")
        self.assertIsNone(supplement_id)

    def test_append_goal_supplement_rejects_empty_kind(self) -> None:
        goal_id = self._submit_conversation_goal(
            "Queued task before dispatch.",
            now="2026-03-20T20:02:00Z",
        )

        with patch.dict(os.environ, self._conversation_env(), clear=False):
            result, supplement_id = append_goal_supplement(
                goal_id,
                {
                    "kind": "   ",
                    "content": "This should be rejected.",
                },
                _goals_dir=self.goals_dir,
                _now="2026-03-20T20:02:10Z",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "EMPTY_KIND")
        self.assertIsNone(supplement_id)

    def test_append_goal_supplement_rejects_source_conversation_mismatch(self) -> None:
        goal_id = self._submit_conversation_goal(
            "Queued task before dispatch.",
            now="2026-03-20T20:02:00Z",
        )

        with patch.dict(os.environ, self._conversation_env(), clear=False):
            result, supplement_id = append_goal_supplement(
                goal_id,
                {
                    "kind": "clarification",
                    "content": "This should be rejected.",
                    "source": {
                        "kind": "conversation",
                        "conversation_id": "2-other-thread",
                        "message_id": "msg-20260320200000-ope-abcd",
                    },
                },
                _goals_dir=self.goals_dir,
                _now="2026-03-20T20:02:10Z",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "SUPPLEMENT_SOURCE_MISMATCH")
        self.assertIsNone(supplement_id)

    def test_materialized_dispatch_packet_filters_by_cutoff_and_separates_prompt(self) -> None:
        goal_id = self._submit_conversation_goal(
            "Original immutable task body.",
            now="2026-03-20T20:03:00Z",
        )
        goal = read_goal(goal_id, _goals_dir=self.goals_dir)
        assert goal is not None

        with patch.dict(
            os.environ,
            self._conversation_env(message_id="msg-20260320200310-ope-f1st"),
            clear=False,
        ):
            result, first_supplement = append_goal_supplement(
                goal_id,
                {
                    "kind": "clarification",
                    "content": "Include regression coverage for the queue path.",
                },
                _goals_dir=self.goals_dir,
                _now="2026-03-20T20:03:10Z",
            )
        self.assertTrue(result.ok)

        with patch.dict(
            os.environ,
            self._conversation_env(message_id="msg-20260320200330-ope-s2nd"),
            clear=False,
        ):
            result, second_supplement = append_goal_supplement(
                goal_id,
                {
                    "kind": "constraint",
                    "content": "Do not rely on model-based summarization yet.",
                },
                _goals_dir=self.goals_dir,
                _now="2026-03-20T20:03:30Z",
            )
        self.assertTrue(result.ok)

        result, packet = materialize_dispatch_packet(
            goal,
            f"{goal_id}-r1",
            "2026-03-20T20:03:20Z",
            _goals_dir=self.goals_dir,
            _runs_dir=self.runs_dir,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(packet)
        self.assertEqual(packet["goal_body"], "Original immutable task body.")
        self.assertEqual(packet["supplement_count"], 1)
        self.assertEqual(packet["supplements"][0]["id"], first_supplement)
        self.assertNotEqual(packet["supplements"][0]["id"], second_supplement)

        packet_path = self.runs_dir / f"{goal_id}-r1" / "dispatch-packet.json"
        self.assertTrue(packet_path.exists())
        packet_from_disk = json.loads(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(packet_from_disk, packet)

        event = read_events(path=self.events_path)[-1]
        self.assertEqual(event["type"], "DispatchPacketMaterialized")
        self.assertEqual(event["goal"], goal_id)
        self.assertEqual(event["run"], f"{goal_id}-r1")
        self.assertEqual(event["supplement_count"], 1)
        self.assertEqual(event["supplement_ids"], [first_supplement])

        prompt = _build_prompt(
            goal,
            f"{goal_id}-r1",
            "gardener",
            self.root,
            dispatch_packet=packet,
        )
        self.assertIn("# Task", prompt)
        self.assertIn("Original immutable task body.", prompt)
        self.assertIn("# Pre-dispatch supplements", prompt)
        self.assertIn("Include regression coverage for the queue path.", prompt)
        self.assertNotIn("Do not rely on model-based summarization yet.", prompt)
        self.assertLess(prompt.index("# Task"), prompt.index("# Pre-dispatch supplements"))


if __name__ == "__main__":
    unittest.main()
