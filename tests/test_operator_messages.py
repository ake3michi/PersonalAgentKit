import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # pragma: no cover - optional test dependency
    Draft202012Validator = None

from system import events as events_module
from system.conversations import (
    append_message,
    open_conversation,
    read_conversation,
    read_messages,
    update_conversation,
)
from system.garden import garden_paths
from system.goals import list_goals
from system.operator_messages import (
    emit_recently_concluded,
    emit_tend_survey,
    read_operator_message_records,
)
from system.plants import commission_plant
from system.submit import submit_goal
from system.validate import ValidationResult, validate_operator_message_record


def _write_runtime_config(root: pathlib.Path) -> None:
    (root / "PAK2.toml").write_text(
        "[runtime]\nroot = \".runtime\"\n",
        encoding="utf-8",
    )


class OperatorMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "goals").mkdir(parents=True, exist_ok=True)
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-26T19:59:00Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _load_schema(self) -> dict:
        schema_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "schema"
            / "operator-message.schema.json"
        )
        return json.loads(schema_path.read_text(encoding="utf-8"))

    def _assert_schema_valid(self, payload: dict) -> None:
        if Draft202012Validator is None:
            self.skipTest("jsonschema is not installed")
        validator = Draft202012Validator(
            self._load_schema(),
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

    def _runtime_env(self, *, goal_id: str, run_id: str) -> dict[str, str]:
        return {
            "PAK2_GARDEN_ROOT": str(self.root),
            "PAK2_CURRENT_GOAL_ID": goal_id,
            "PAK2_CURRENT_RUN_ID": run_id,
            "PAK2_CURRENT_PLANT": "gardener",
            "PAK2_CURRENT_GOALS_DIR": str(self.root / "goals"),
        }

    def test_validate_operator_message_record_accepts_conversation_backed_record(self) -> None:
        record = {
            "schema_version": 1,
            "kind": "tend_survey",
            "sender": "garden",
            "origin": {
                "kind": "conversation",
                "conversation_id": "1-hello",
                "message_id": "msg-20260326200000-ope-abcd",
                "ts": "2026-03-26T20:00:00Z",
            },
            "transcript_policy": "canonical",
            "delivery_policy": "reply_copy",
            "emitted_at": "2026-03-26T20:00:01Z",
            "source_goal_id": "130-operator-message-slice",
            "source_run_id": "130-operator-message-slice-r1",
            "delivery_path": "inbox/garden/20260326T200001-1-hello.md",
            "conversation_message_id": "msg-20260326200001-gar-wxyz",
        }

        result = validate_operator_message_record(record)
        self.assertTrue(result.ok, result.detail)
        self._assert_schema_valid(record)

    def test_emit_tend_survey_routes_conversation_origin_into_conversation_and_reply_copy(self) -> None:
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="inbox/operator",
            topic="operator-tend",
            _conv_dir=self.root / "conversations",
            _now="2026-03-26T20:00:00Z",
        )
        self.assertTrue(result.ok)
        conv_id = conv_id or ""

        result, source_message_id = append_message(
            conv_id,
            "operator",
            "How is the garden doing?",
            channel="filesystem",
            _conv_dir=self.root / "conversations",
            _now="2026-03-26T20:00:01Z",
        )
        self.assertTrue(result.ok)

        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Perform a bounded survey.",
                "origin": {
                    "kind": "conversation",
                    "conversation_id": conv_id,
                    "message_id": source_message_id,
                    "ts": "2026-03-26T20:00:01Z",
                },
                "tend": {
                    "trigger_kinds": ["operator_request"],
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-26T20:00:01Z",
        )
        self.assertTrue(result.ok)
        goal_id = goal_id or ""
        run_id = f"{goal_id}-r1"

        with patch.dict(os.environ, self._runtime_env(goal_id=goal_id, run_id=run_id), clear=False):
            result, record = emit_tend_survey(
                "The garden is healthy.\n",
                _garden_root=self.root,
                _now="2026-03-26T20:00:02Z",
            )

        self.assertTrue(result.ok, result.detail)
        assert record is not None
        self.assertEqual(record["kind"], "tend_survey")
        self.assertEqual(record["transcript_policy"], "canonical")
        self.assertEqual(record["delivery_policy"], "reply_copy")
        self.assertEqual(record["origin"]["conversation_id"], conv_id)
        self.assertEqual(record["origin"]["message_id"], source_message_id)
        self.assertIn("conversation_message_id", record)
        self.assertIn("delivery_path", record)
        self.assertNotIn("/notes/", record["delivery_path"])

        reply_path = self.root / record["delivery_path"]
        self.assertTrue(reply_path.exists())
        self.assertEqual(reply_path.read_text(encoding="utf-8"), "The garden is healthy.\n")

        messages = read_messages(conv_id, _conv_dir=self.root / "conversations")
        self.assertEqual(messages[-1]["sender"], "garden")
        self.assertEqual(messages[-1]["reply_to"], source_message_id)
        self.assertEqual(messages[-1]["content"], "The garden is healthy.\n")
        self.assertEqual(messages[-1]["id"], record["conversation_message_id"])

        records = read_operator_message_records(run_id, _garden_root=self.root)
        self.assertEqual(records, [record])
        self._assert_schema_valid(record)

    def test_emit_tend_survey_uses_runtime_relative_reply_copy_path_under_split_runtime_root(
        self,
    ) -> None:
        _write_runtime_config(self.root)
        paths = garden_paths(garden_root=self.root)

        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref=".runtime/inbox/operator",
            topic="operator-tend",
            _conv_dir=paths.conversations_dir,
            _now="2026-03-26T20:00:00Z",
        )
        self.assertTrue(result.ok)
        conv_id = conv_id or ""

        result, source_message_id = append_message(
            conv_id,
            "operator",
            "How is the garden doing?",
            channel="filesystem",
            _conv_dir=paths.conversations_dir,
            _now="2026-03-26T20:00:01Z",
        )
        self.assertTrue(result.ok)

        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Perform a bounded survey.",
                "origin": {
                    "kind": "conversation",
                    "conversation_id": conv_id,
                    "message_id": source_message_id,
                    "ts": "2026-03-26T20:00:01Z",
                },
                "tend": {
                    "trigger_kinds": ["operator_request"],
                },
            },
            _goals_dir=paths.goals_dir,
            _now="2026-03-26T20:00:01Z",
        )
        self.assertTrue(result.ok)
        goal_id = goal_id or ""
        run_id = f"{goal_id}-r1"

        env = self._runtime_env(goal_id=goal_id, run_id=run_id)
        env["PAK2_CURRENT_GOALS_DIR"] = str(paths.goals_dir)
        with patch.dict(os.environ, env, clear=False):
            result, record = emit_tend_survey(
                "The garden is healthy.\n",
                _garden_root=self.root,
                _now="2026-03-26T20:00:02Z",
            )

        self.assertTrue(result.ok, result.detail)
        assert record is not None
        self.assertEqual(
            record["delivery_path"],
            f"inbox/garden/20260326T200002Z-{conv_id.replace('/', '-')[:40]}.md",
        )
        self.assertTrue((paths.runtime_root / record["delivery_path"]).exists())

        records = read_operator_message_records(run_id, _garden_root=self.root)
        self.assertEqual(records, [record])

    def test_emit_tend_survey_retry_recovers_existing_message_without_duplicate_append(
        self,
    ) -> None:
        _write_runtime_config(self.root)
        paths = garden_paths(garden_root=self.root)

        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref=".runtime/inbox/operator",
            topic="operator-tend",
            _conv_dir=paths.conversations_dir,
            _now="2026-03-26T20:00:00Z",
        )
        self.assertTrue(result.ok)
        conv_id = conv_id or ""

        result, source_message_id = append_message(
            conv_id,
            "operator",
            "How is the garden doing?",
            channel="filesystem",
            _conv_dir=paths.conversations_dir,
            _now="2026-03-26T20:00:01Z",
        )
        self.assertTrue(result.ok)

        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Perform a bounded survey.",
                "origin": {
                    "kind": "conversation",
                    "conversation_id": conv_id,
                    "message_id": source_message_id,
                    "ts": "2026-03-26T20:00:01Z",
                },
                "tend": {
                    "trigger_kinds": ["operator_request"],
                },
            },
            _goals_dir=paths.goals_dir,
            _now="2026-03-26T20:00:01Z",
        )
        self.assertTrue(result.ok)
        goal_id = goal_id or ""
        run_id = f"{goal_id}-r1"
        env = self._runtime_env(goal_id=goal_id, run_id=run_id)
        env["PAK2_CURRENT_GOALS_DIR"] = str(paths.goals_dir)
        content = "The garden is healthy.\n"

        with patch.dict(os.environ, env, clear=False):
            with patch(
                "system.operator_messages._persist_record",
                return_value=(ValidationResult.reject("IO_ERROR", "boom"), None),
            ):
                result, record = emit_tend_survey(
                    content,
                    _garden_root=self.root,
                    _now="2026-03-26T20:00:03Z",
                )

        self.assertFalse(result.ok)
        self.assertIsNone(record)

        messages = read_messages(conv_id, _conv_dir=paths.conversations_dir)
        matching = [message for message in messages if message["content"] == content]
        self.assertEqual(len(matching), 1)
        first_message_id = matching[0]["id"]
        reply_files = sorted((paths.runtime_root / "inbox" / "garden").glob("*.md"))
        self.assertEqual(len(reply_files), 1)
        self.assertEqual(reply_files[0].read_text(encoding="utf-8"), content)

        with patch.dict(os.environ, env, clear=False):
            result, record = emit_tend_survey(
                content,
                _garden_root=self.root,
                _now="2026-03-26T20:00:10Z",
            )

        self.assertTrue(result.ok, result.detail)
        assert record is not None
        self.assertEqual(record["conversation_message_id"], first_message_id)

        messages = read_messages(conv_id, _conv_dir=paths.conversations_dir)
        matching = [message for message in messages if message["content"] == content]
        self.assertEqual(len(matching), 1)

        reply_files = sorted((paths.runtime_root / "inbox" / "garden").glob("*.md"))
        self.assertEqual(len(reply_files), 1)
        self.assertEqual(reply_files[0].read_text(encoding="utf-8"), content)

        records = read_operator_message_records(run_id, _garden_root=self.root)
        self.assertEqual(records, [record])

    def test_emit_recently_concluded_routes_no_origin_to_notes_subdirectory(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Land the bounded slice.",
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-26T20:05:00Z",
        )
        self.assertTrue(result.ok)
        goal_id = goal_id or ""
        run_id = f"{goal_id}-r1"

        with patch.dict(os.environ, self._runtime_env(goal_id=goal_id, run_id=run_id), clear=False):
            result, record = emit_recently_concluded(
                "The bounded slice finished cleanly.\n",
                _garden_root=self.root,
                _now="2026-03-26T20:05:00Z",
            )

        self.assertTrue(result.ok, result.detail)
        assert record is not None
        self.assertEqual(record["kind"], "recently_concluded")
        self.assertEqual(record["transcript_policy"], "none")
        self.assertEqual(record["delivery_policy"], "out_of_band_note")
        self.assertEqual(
            record["delivery_path"],
            "inbox/garden/notes/20260326T200500-recently-concluded.md",
        )
        self.assertNotIn("conversation_message_id", record)
        self.assertTrue((self.root / record["delivery_path"]).exists())

        records = read_operator_message_records(run_id, _garden_root=self.root)
        self.assertEqual(records, [record])
        self._assert_schema_valid(record)

    def test_emit_tend_survey_queues_eager_hop_after_external_append(self) -> None:
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="inbox/operator",
            topic="operator-tend",
            _conv_dir=self.root / "conversations",
            _now="2026-03-26T20:00:00Z",
        )
        self.assertTrue(result.ok)
        conv_id = conv_id or ""

        result, source_message_id = append_message(
            conv_id,
            "operator",
            "How is the garden doing?",
            channel="filesystem",
            _conv_dir=self.root / "conversations",
            _now="2026-03-26T20:00:01Z",
        )
        self.assertTrue(result.ok)
        result, prior_reply_message_id = append_message(
            conv_id,
            "garden",
            "The latest conversation reply came from the active session.",
            channel="filesystem",
            _conv_dir=self.root / "conversations",
            _now="2026-03-26T20:00:02Z",
        )
        self.assertTrue(result.ok)
        result = update_conversation(
            conv_id,
            _conv_dir=self.root / "conversations",
            _now="2026-03-26T20:00:02Z",
            session_id="session-1",
            session_ordinal=1,
            session_turns=2,
            session_started_at="2026-03-26T20:00:00Z",
            context_at="2026-03-26T20:00:02Z",
        )
        self.assertTrue(result.ok)

        result, goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Perform a bounded survey.",
                "origin": {
                    "kind": "conversation",
                    "conversation_id": conv_id,
                    "message_id": source_message_id,
                    "ts": "2026-03-26T20:00:01Z",
                },
                "tend": {
                    "trigger_kinds": ["operator_request"],
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-26T20:00:01Z",
        )
        self.assertTrue(result.ok)
        goal_id = goal_id or ""
        run_id = f"{goal_id}-r1"

        with patch.dict(os.environ, self._runtime_env(goal_id=goal_id, run_id=run_id), clear=False):
            result, record = emit_tend_survey(
                "The garden is healthy.\n",
                _garden_root=self.root,
                _now="2026-03-26T20:00:03Z",
            )

        self.assertTrue(result.ok, result.detail)
        assert record is not None

        conv = read_conversation(conv_id, _conv_dir=self.root / "conversations")
        self.assertIsNotNone(conv)
        self.assertIsNotNone(conv["post_reply_hop"])

        hop_goals = [
            goal
            for goal in list_goals(_goals_dir=self.root / "goals")
            if goal.get("post_reply_hop")
        ]
        self.assertEqual(len(hop_goals), 1)
        hop_goal = hop_goals[0]
        self.assertEqual(hop_goal["type"], "converse")
        self.assertEqual(hop_goal["conversation_id"], conv_id)
        self.assertEqual(
            hop_goal["post_reply_hop"]["source_reply_message_id"],
            prior_reply_message_id,
        )
        self.assertEqual(
            hop_goal["post_reply_hop"]["source_reply_recorded_at"],
            "2026-03-26T20:00:02Z",
        )
        self.assertEqual(hop_goal["post_reply_hop"]["source_goal_id"], goal_id)
        self.assertEqual(hop_goal["post_reply_hop"]["source_run_id"], run_id)
        self.assertGreaterEqual(hop_goal["post_reply_hop"]["pressure"]["history_messages"], 3)
        self.assertEqual(conv["post_reply_hop"]["goal_id"], hop_goal["id"])
        self.assertEqual(
            conv["post_reply_hop"]["source_reply_message_id"],
            prior_reply_message_id,
        )

        messages = read_messages(conv_id, _conv_dir=self.root / "conversations")
        self.assertEqual(messages[-1]["id"], record["conversation_message_id"])
        self.assertEqual(messages[-1]["content"], "The garden is healthy.\n")

        events = events_module.read_events(path=self.root / "events" / "coordinator.jsonl")
        hop_submission = next(
            event
            for event in events
            if event.get("type") == "GoalSubmitted" and event.get("goal") == hop_goal["id"]
        )
        self.assertEqual(hop_submission["goal_subtype"], "post_reply_hop")
        self.assertEqual(hop_submission["conversation_id"], conv_id)

        hop_queue = next(
            event
            for event in events
            if event.get("type") == "ConversationHopQueued"
            and event.get("hop_goal") == hop_goal["id"]
        )
        self.assertEqual(hop_queue["conversation_id"], conv_id)
        self.assertEqual(hop_queue["source_message_id"], record["conversation_message_id"])

    def test_emit_tend_survey_rejects_non_tend_goal_context(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "build",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Not a tend goal.",
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-26T20:10:00Z",
        )
        self.assertTrue(result.ok)
        goal_id = goal_id or ""

        with patch.dict(
            os.environ,
            self._runtime_env(goal_id=goal_id, run_id=f"{goal_id}-r1"),
            clear=False,
        ):
            result, record = emit_tend_survey(
                "This should fail.\n",
                _garden_root=self.root,
                _now="2026-03-26T20:10:01Z",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_OPERATOR_MESSAGE_CONTEXT")
        self.assertIsNone(record)


if __name__ == "__main__":
    unittest.main()
