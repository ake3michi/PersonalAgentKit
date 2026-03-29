import pathlib
import tempfile
import unittest
import json

from system.conversations import (
    append_conversation_turn,
    append_message,
    open_conversation,
    read_conversation,
    read_conversation_checkpoints,
    read_conversation_turns,
    update_conversation,
    write_conversation_checkpoint,
)
from system.validate import validate_event


class ConversationContractBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "conversations").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @property
    def conv_dir(self) -> pathlib.Path:
        return self.root / "conversations"

    def _open_conversation(self) -> str:
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="inbox/operator",
            topic="contract",
            _conv_dir=self.conv_dir,
            _now="2026-03-21T15:00:00Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)
        return conv_id or ""

    def _append(self, conv_id: str, sender: str, content: str, ts: str) -> str:
        result, msg_id = append_message(
            conv_id,
            sender,
            content,
            channel="filesystem",
            _conv_dir=self.conv_dir,
            _now=ts,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(msg_id)
        return msg_id or ""

    def test_update_conversation_accepts_live_metadata_shape(self) -> None:
        conv_id = self._open_conversation()
        reply_message_id = self._append(
            conv_id,
            "garden",
            "Reply before a fresh-session checkpoint.",
            "2026-03-21T15:00:01Z",
        )

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-21T15:00:02Z",
            session_id="session-123",
            compacted_through=reply_message_id,
            session_ordinal=2,
            session_turns=4,
            session_started_at="2026-03-21T15:00:00Z",
            checkpoint_count=1,
            last_checkpoint_id="ckpt-20260321150002-ab12",
            last_checkpoint_at="2026-03-21T15:00:02Z",
            last_turn_mode="resumed",
            last_turn_run_id="12-contract-turn-r1",
            last_pressure={
                "band": "medium",
                "prompt_chars": 8000,
                "tail_messages": 3,
            },
            pending_hop={
                "requested_at": "2026-03-21T15:00:02Z",
                "requested_by": "operator",
                "reason": "fresh session next turn",
            },
            post_reply_hop={
                "requested_at": "2026-03-21T15:00:02Z",
                "requested_by": "system",
                "reason": "automatic pressure handoff",
                "automatic": True,
                "source_goal_id": "12-contract-turn",
                "source_run_id": "12-contract-turn-r1",
                "source_reply_message_id": reply_message_id,
                "source_reply_recorded_at": "2026-03-21T15:00:02Z",
                "source_session_id": "session-123",
                "source_session_ordinal": 2,
                "source_session_turns": 4,
                "pressure": {
                    "band": "high",
                    "provider_input_tokens": 123456,
                },
                "goal_id": "13-post-reply-hop",
            },
        )

        self.assertTrue(result.ok)
        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertEqual(conv["post_reply_hop"]["goal_id"], "13-post-reply-hop")
        self.assertEqual(conv["started_by"], "operator")

    def test_open_conversation_accepts_system_started_provenance(self) -> None:
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="inbox/operator",
            topic="startup",
            started_by="system",
            _conv_dir=self.conv_dir,
            _now="2026-03-21T15:00:00Z",
        )

        self.assertTrue(result.ok)
        conv = read_conversation(conv_id or "", _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertEqual(conv["started_by"], "system")

    def test_open_conversation_rejects_unknown_started_by_value(self) -> None:
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="inbox/operator",
            topic="startup",
            started_by="gardener",
            _conv_dir=self.conv_dir,
            _now="2026-03-21T15:00:00Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_CONVERSATION_FIELD")
        self.assertIsNone(conv_id)

    def test_update_conversation_rejects_unknown_meta_field(self) -> None:
        conv_id = self._open_conversation()

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-21T15:00:02Z",
            mystery_field="nope",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "UNKNOWN_CONVERSATION_FIELD")

    def test_update_conversation_rejects_schema_invalid_null_meta_fields(self) -> None:
        conv_id = self._open_conversation()

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-21T15:00:02Z",
            topic=None,
            session_ordinal=None,
            session_turns=None,
            checkpoint_count=None,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_CONVERSATION_FIELD")
        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertNotIn("topic", conv)
        self.assertEqual(conv["session_ordinal"], 0)
        self.assertEqual(conv["session_turns"], 0)
        self.assertEqual(conv["checkpoint_count"], 0)

    def test_update_conversation_accepts_legacy_meta_without_new_fields(self) -> None:
        conv_id = "1-legacy-contract"
        legacy_record = {
            "id": conv_id,
            "status": "open",
            "channel": "filesystem",
            "channel_ref": "inbox/operator",
            "presence_model": "async",
            "participants": ["operator", "garden"],
            "started_at": "2026-03-21T15:00:00Z",
            "last_activity_at": "2026-03-21T15:00:00Z",
            "context_at": "2026-03-21T15:00:00Z",
        }
        conv_path = self.conv_dir / conv_id
        conv_path.mkdir(parents=True, exist_ok=True)
        (conv_path / "meta.json").write_text(
            json.dumps(legacy_record, indent=2) + "\n",
            encoding="utf-8",
        )
        (conv_path / "messages.jsonl").write_text("", encoding="utf-8")

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-21T15:00:03Z",
            context_at="2026-03-21T15:00:02Z",
        )

        self.assertTrue(result.ok)
        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertEqual(conv["context_at"], "2026-03-21T15:00:02Z")
        self.assertEqual(conv["last_activity_at"], "2026-03-21T15:00:03Z")
        self.assertNotIn("started_by", conv)
        self.assertNotIn("topic", conv)
        self.assertNotIn("session_ordinal", conv)
        self.assertNotIn("session_turns", conv)
        self.assertNotIn("checkpoint_count", conv)

    def test_append_message_rejects_invalid_reply_to_format(self) -> None:
        conv_id = self._open_conversation()

        result, msg_id = append_message(
            conv_id,
            "garden",
            "Reply with a bad reply_to marker.",
            channel="filesystem",
            reply_to="bad-message-id",
            _conv_dir=self.conv_dir,
            _now="2026-03-21T15:00:01Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_MESSAGE_FIELD")
        self.assertIsNone(msg_id)

    def test_append_conversation_turn_accepts_valid_record(self) -> None:
        conv_id = self._open_conversation()

        result = append_conversation_turn(
            conv_id,
            {
                "id": "turn-20260321150003",
                "run_id": "12-contract-turn-r1",
                "goal_id": "12-contract-turn",
                "ts": "2026-03-21T15:00:03Z",
                "status": "success",
                "mode": "fresh-start",
                "diff_present": True,
                "lineage": {
                    "session_ordinal": 1,
                    "session_turn": 1,
                    "label": "session 1 turn 1",
                    "checkpoint_id": None,
                    "checkpoint_count": 0,
                },
                "pressure": {
                    "band": "low",
                    "tail_messages": 1,
                    "prompt_chars": 900,
                },
                "hop": {
                    "requested": False,
                    "reason": None,
                    "queued": False,
                    "goal_id": None,
                    "performed": False,
                    "checkpoint_id": None,
                    "error": None,
                    "automatic": False,
                },
                "session_id_before": None,
                "session_id_after": "session-123",
            },
            _conv_dir=self.conv_dir,
        )

        self.assertTrue(result.ok)
        turns = read_conversation_turns(conv_id, _conv_dir=self.conv_dir)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["id"], "turn-20260321150003")

    def test_append_conversation_turn_rejects_inconsistent_hop_state(self) -> None:
        conv_id = self._open_conversation()

        result = append_conversation_turn(
            conv_id,
            {
                "id": "turn-20260321150003",
                "run_id": "12-contract-turn-r1",
                "goal_id": "12-contract-turn",
                "ts": "2026-03-21T15:00:03Z",
                "status": "success",
                "mode": "resumed",
                "diff_present": True,
                "lineage": {
                    "session_ordinal": 2,
                    "session_turn": 4,
                    "label": "session 2 turn 4",
                    "checkpoint_id": None,
                    "checkpoint_count": 1,
                },
                "pressure": {
                    "band": "high",
                    "tail_messages": 3,
                },
                "hop": {
                    "requested": True,
                    "reason": "fresh session next turn",
                    "queued": True,
                    "goal_id": None,
                    "performed": False,
                    "checkpoint_id": None,
                    "error": None,
                    "automatic": False,
                },
                "session_id_before": "session-123",
                "session_id_after": "session-123",
            },
            _conv_dir=self.conv_dir,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_TURN_HOP")

    def test_write_conversation_checkpoint_rejects_invalid_run_id(self) -> None:
        conv_id = self._open_conversation()
        handoff_marker = self._append(
            conv_id,
            "garden",
            "Reply before checkpoint.",
            "2026-03-21T15:00:01Z",
        )

        result, record = write_conversation_checkpoint(
            conv_id,
            "# Conversation Handoff Summary\n\n- Preserve continuity.",
            handoff_marker,
            requested_by="operator",
            reason="manual checkpoint",
            run_id="bad-run-id",
            _conv_dir=self.conv_dir,
            _now="2026-03-21T15:00:03Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_CHECKPOINT_FIELD")
        self.assertIsNone(record)
        self.assertEqual(
            read_conversation_checkpoints(conv_id, _conv_dir=self.conv_dir),
            [],
        )

    def test_write_conversation_checkpoint_rejects_invalid_pressure_shape(self) -> None:
        conv_id = self._open_conversation()
        handoff_marker = self._append(
            conv_id,
            "garden",
            "Reply before checkpoint.",
            "2026-03-21T15:00:01Z",
        )

        result, record = write_conversation_checkpoint(
            conv_id,
            "# Conversation Handoff Summary\n\n- Preserve continuity.",
            handoff_marker,
            requested_by="operator",
            reason="manual checkpoint",
            pressure="too much",
            _conv_dir=self.conv_dir,
            _now="2026-03-21T15:00:03Z",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_CHECKPOINT_PRESSURE")
        self.assertIsNone(record)

    def test_validate_event_rejects_null_checkpoint_source_session_id_but_accepts_omission(self) -> None:
        event = {
            "ts": "2026-03-21T15:00:03Z",
            "type": "ConversationCheckpointWritten",
            "actor": "system",
            "conversation_id": "1-contract",
            "checkpoint_id": "ckpt-20260321150003-ab12",
            "checkpoint_requested_by": "system",
            "checkpoint_reason": "manual checkpoint",
            "checkpoint_summary_path": "checkpoints/ckpt-20260321150003-ab12.md",
            "source_message_id": "msg-20260321150001-gar-ab12",
            "source_session_id": None,
            "source_session_ordinal": 0,
            "source_session_turns": 0,
            "checkpoint_count": 1,
        }

        result = validate_event(event)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_SHAPE")

        event.pop("source_session_id")
        result = validate_event(event)
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
