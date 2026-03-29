import pathlib
import tempfile
import unittest

from system.conversations import (
    append_message,
    open_conversation,
    prepare_conversation_checkpoint,
    read_conversation,
    read_conversation_checkpoints,
    read_conversation_summary,
    update_conversation,
)
from system.driver import _build_conversation_prompt, _build_resumed_conversation_prompt


class ConversationCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "conversations").mkdir(parents=True, exist_ok=True)
        (self.root / "plants" / "gardener" / "memory").mkdir(parents=True, exist_ok=True)
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

    @property
    def conv_dir(self) -> pathlib.Path:
        return self.root / "conversations"

    @property
    def plant_dir(self) -> pathlib.Path:
        return self.root / "plants" / "gardener"

    def _open_conversation(self) -> str:
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="inbox/operator",
            topic="hello",
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:00Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)
        return conv_id

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
        return msg_id

    def test_prepare_conversation_checkpoint_writes_summary_and_clears_session(self) -> None:
        conv_id = self._open_conversation()
        self._append(conv_id, "operator", "First operator message", "2026-03-20T07:00:01Z")
        checkpoint_marker = self._append(
            conv_id, "garden", "First garden reply", "2026-03-20T07:00:02Z"
        )

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:02Z",
            session_id="thread-123",
        )
        self.assertTrue(result.ok)

        summary = "# Conversation Handoff Summary\n\n- Preserve continuity.\n"
        result = prepare_conversation_checkpoint(
            conv_id,
            summary,
            checkpoint_marker,
            _conv_dir=self.conv_dir,
        )
        self.assertTrue(result.ok)

        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertIsNone(conv["session_id"])
        self.assertEqual(conv["compacted_through"], checkpoint_marker)
        self.assertEqual(conv["last_activity_at"], "2026-03-20T07:00:02Z")
        self.assertEqual(
            read_conversation_summary(conv_id, _conv_dir=self.conv_dir),
            "# Conversation Handoff Summary\n\n- Preserve continuity.",
        )
        checkpoints = read_conversation_checkpoints(conv_id, _conv_dir=self.conv_dir)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["reason"], "manual checkpoint")
        self.assertEqual(checkpoints[0]["compacted_through"], checkpoint_marker)

    def test_prepare_conversation_checkpoint_rejects_unknown_message(self) -> None:
        conv_id = self._open_conversation()
        self._append(conv_id, "operator", "Only message", "2026-03-20T07:00:01Z")

        result = prepare_conversation_checkpoint(
            conv_id,
            "Summary",
            "msg-does-not-exist",
            _conv_dir=self.conv_dir,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "MESSAGE_NOT_FOUND")

    def test_fresh_prompt_uses_summary_and_recent_tail_after_checkpoint(self) -> None:
        conv_id = self._open_conversation()
        self._append(conv_id, "operator", "early operator context", "2026-03-20T07:00:01Z")
        checkpoint_marker = self._append(
            conv_id, "garden", "early garden context", "2026-03-20T07:00:02Z"
        )
        self._append(conv_id, "operator", "tail operator context", "2026-03-20T07:00:03Z")
        self._append(conv_id, "garden", "tail garden context", "2026-03-20T07:00:04Z")

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:04Z",
            session_id="thread-123",
        )
        self.assertTrue(result.ok)

        result = prepare_conversation_checkpoint(
            conv_id,
            "# Conversation Handoff Summary\n\n- Keep the same voice.",
            checkpoint_marker,
            _conv_dir=self.conv_dir,
        )
        self.assertTrue(result.ok)

        self._append(conv_id, "operator", "current operator message", "2026-03-20T07:00:05Z")
        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)

        prompt = _build_conversation_prompt(
            {
                "id": "goal-1",
                "body": "current operator message",
                "assigned_to": "gardener",
            },
            "run-1",
            conv,
            "[Diff section]",
            self.plant_dir,
            self.root,
        )

        self.assertIn("# Conversation handoff", prompt)
        self.assertIn("# Recent conversation tail", prompt)
        self.assertNotIn("# Conversation history", prompt)
        self.assertIn("tail operator context", prompt)
        self.assertIn("tail garden context", prompt)
        self.assertNotIn("early operator context", prompt)
        self.assertNotIn("early garden context", prompt)
        self.assertIn("[Diff section]", prompt)
        self.assertIn("# Converse execution policy", prompt)
        self.assertIn("Default to delegation.", prompt)
        self.assertIn("append_goal_supplement", prompt)
        self.assertIn(
            "from system.submit import append_goal_supplement, submit_goal",
            prompt,
        )
        self.assertEqual(prompt.count("Operator: current operator message"), 1)

    def test_resumed_prompt_includes_delegation_policy(self) -> None:
        prompt = _build_resumed_conversation_prompt(
            {"body": "current operator message"},
            "[Diff section]",
            status_block="# Conversation session status",
        )

        self.assertIn("# Conversation session status", prompt)
        self.assertIn("# Converse execution policy", prompt)
        self.assertIn("Default to delegation.", prompt)
        self.assertIn("If you are unsure whether", prompt)
        self.assertIn("append_goal_supplement", prompt)
        self.assertIn(
            "from system.submit import append_goal_supplement, submit_goal",
            prompt,
        )
        self.assertIn("[Diff section]", prompt)
        self.assertEqual(prompt.count("Operator: current operator message"), 1)


if __name__ == "__main__":
    unittest.main()
