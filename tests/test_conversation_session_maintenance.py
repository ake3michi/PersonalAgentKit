import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from system import events as events_module
from system.conversations import (
    append_message,
    compute_activity_diff,
    format_diff,
    open_conversation,
    read_conversation,
    read_conversation_checkpoints,
    read_conversation_turns,
    request_conversation_hop,
    update_conversation,
    write_conversation_checkpoint,
)
from system.driver import _dispatch_conversation, dispatch
from system.goals import list_goals
from system.operator_messages import emit_tend_survey
from system.plants import commission_plant
from system.runs import open_run
from system.submit import submit_goal


class ConversationSessionMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "conversations").mkdir(parents=True, exist_ok=True)
        (self.root / "goals").mkdir(parents=True, exist_ok=True)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        (self.root / "inbox" / "garden").mkdir(parents=True, exist_ok=True)
        (self.root / "events").mkdir(parents=True, exist_ok=True)
        (self.root / "events" / "coordinator.jsonl").write_text("", encoding="utf-8")
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)
        (self.root / "events" / "coordinator.jsonl").write_text("", encoding="utf-8")
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

    def _open_conversation(self) -> str:
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="inbox/operator",
            topic="maintenance",
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

    def test_activity_diff_keeps_converse_metadata_and_formats_it_distinctly(self) -> None:
        events_path = self.root / "events" / "coordinator.jsonl"
        events_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-03-20T07:00:01Z",
                            "type": "GoalSubmitted",
                            "actor": "operator",
                            "goal": "82-check-status",
                            "goal_type": "converse",
                            "conversation_id": "1-hello",
                        }
                    ),
                    json.dumps(
                        {
                            "ts": "2026-03-20T07:00:02Z",
                            "type": "GoalSubmitted",
                            "actor": "gardener",
                            "goal": "90-investigate-status",
                            "goal_type": "research",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        activity_diff = compute_activity_diff(
            events_path,
            "2026-03-20T07:00:00Z",
        )

        self.assertEqual(
            activity_diff,
            [
                {
                    "type": "goal_submitted",
                    "goal": "82-check-status",
                    "actor": "operator",
                    "goal_type": "converse",
                    "conversation_id": "1-hello",
                },
                {
                    "type": "goal_submitted",
                    "goal": "90-investigate-status",
                    "actor": "gardener",
                    "goal_type": "research",
                    "conversation_id": None,
                },
            ],
        )

        rendered = format_diff([], activity_diff)
        self.assertIn(
            "conversation turn queued: 82-check-status "
            "(actor=operator, conversation_id=1-hello)",
            rendered,
        )
        self.assertIn(
            "goal submitted: 90-investigate-status (actor=gardener, type=research)",
            rendered,
        )

    def test_activity_diff_prefers_dedicated_continuity_events(self) -> None:
        events_path = self.root / "events" / "coordinator.jsonl"
        events_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-03-20T07:00:01Z",
                            "type": "GoalSubmitted",
                            "actor": "gardener",
                            "goal": "83-post-reply-hop",
                            "goal_type": "converse",
                            "goal_subtype": "post_reply_hop",
                            "conversation_id": "1-hello",
                        }
                    ),
                    json.dumps(
                        {
                            "ts": "2026-03-20T07:00:02Z",
                            "type": "ConversationHopQueued",
                            "actor": "system",
                            "goal": "82-chat-turn",
                            "run": "82-chat-turn-r1",
                            "conversation_id": "1-hello",
                            "hop_goal": "83-post-reply-hop",
                            "hop_requested_by": "system",
                            "hop_reason": "automatic pressure handoff",
                            "hop_automatic": True,
                        }
                    ),
                    json.dumps(
                        {
                            "ts": "2026-03-20T07:00:03Z",
                            "type": "ConversationCheckpointWritten",
                            "actor": "system",
                            "goal": "83-post-reply-hop",
                            "run": "83-post-reply-hop-r1",
                            "conversation_id": "1-hello",
                            "checkpoint_id": "ckpt-20260320070003-ab12",
                            "checkpoint_requested_by": "system",
                            "checkpoint_reason": "automatic pressure handoff",
                            "checkpoint_summary_path": "checkpoints/ckpt-20260320070003-ab12.md",
                            "source_message_id": "msg-20260320070000-gar-ab12",
                            "source_session_ordinal": 2,
                            "source_session_turns": 6,
                            "checkpoint_count": 3,
                        }
                    ),
                    json.dumps(
                        {
                            "ts": "2026-03-20T07:00:04Z",
                            "type": "RunFinished",
                            "actor": "system",
                            "goal": "83-post-reply-hop",
                            "run": "83-post-reply-hop-r1",
                            "run_reason": "success",
                            "goal_subtype": "post_reply_hop",
                            "conversation_id": "1-hello",
                            "hop_outcome": "checkpointed",
                            "checkpoint_id": "ckpt-20260320070003-ab12",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        activity_diff = compute_activity_diff(
            events_path,
            "2026-03-20T07:00:00Z",
        )

        self.assertEqual(
            activity_diff,
            [
                {
                    "type": "conversation_hop_queued",
                    "conversation_id": "1-hello",
                    "hop_goal": "83-post-reply-hop",
                    "reason": "automatic pressure handoff",
                    "automatic": True,
                },
                {
                    "type": "conversation_checkpoint_written",
                    "conversation_id": "1-hello",
                    "checkpoint_id": "ckpt-20260320070003-ab12",
                    "reason": "automatic pressure handoff",
                },
            ],
        )

        rendered = format_diff([], activity_diff)
        self.assertEqual(rendered.count("conversation hop queued:"), 1)
        self.assertIn(
            "conversation checkpoint written: ckpt-20260320070003-ab12",
            rendered,
        )
        self.assertNotIn("run 83-post-reply-hop-r1 finished", rendered)

    def test_dispatch_conversation_excludes_current_goal_from_activity_diff(self) -> None:
        conv_id = self._open_conversation()
        self._append(conv_id, "operator", "Earlier operator context", "2026-03-20T07:00:01Z")
        self._append(conv_id, "garden", "Earlier garden reply", "2026-03-20T07:00:02Z")
        self._append(conv_id, "operator", "Latest operator message", "2026-03-20T07:00:03Z")

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:03Z",
            session_id="session-1",
            session_ordinal=1,
            session_turns=2,
            context_at="2026-03-20T07:00:00Z",
        )
        self.assertTrue(result.ok)

        (self.root / "events" / "coordinator.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-03-20T07:00:04Z",
                            "type": "GoalSubmitted",
                            "actor": "gardener",
                            "goal": "4-background-followup",
                            "goal_type": "fix",
                        }
                    ),
                    json.dumps(
                        {
                            "ts": "2026-03-20T07:00:05Z",
                            "type": "GoalSubmitted",
                            "actor": "operator",
                            "goal": "5-current-converse-turn",
                            "goal_type": "converse",
                            "conversation_id": conv_id,
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        goal = {
            "id": "5-current-converse-turn",
            "body": "Latest operator message",
            "assigned_to": "gardener",
            "conversation_id": conv_id,
        }
        prompt_holder: dict[str, str] = {}

        class FakeChannel:
            def send(self, conversation_id: str, content: str, **kwargs) -> None:
                return None

        def fake_launch(model, prompt, events_path, timeout=None, session_id=None,
                        driver_name="claude", cwd=None, reasoning_effort=None,
                        env=None):
            prompt_holder["prompt"] = prompt
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 0,
                            "output_tokens": 50,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (events_path.parent / "last-message.md").write_text(
                "Conversation reply",
                encoding="utf-8",
            )
            return 0

        result, run_id = open_run(
            goal["id"],
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T07:00:05Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(run_id)

        with patch("system.driver._launch", side_effect=fake_launch):
            with patch("system.driver._make_channel", return_value=FakeChannel()):
                _dispatch_conversation(
                    goal,
                    run_id,
                    self.root,
                    conv_id,
                    "codex",
                    "gpt-5.4",
                    "xhigh",
                )

        prompt = prompt_holder["prompt"]
        self.assertIn(
            "goal submitted: 4-background-followup (actor=gardener, type=fix)",
            prompt,
        )
        self.assertNotIn("5-current-converse-turn", prompt)

    def test_dispatch_conversation_uses_startup_note_as_history_without_prefixing_reply(self) -> None:
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="inbox/operator",
            topic="startup",
            started_by="system",
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:40Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)
        conv_id = conv_id or ""
        self._append(
            conv_id,
            "system",
            "System startup note from recorded bootstrap facts:\n- Gardener commissioned.",
            "2026-03-20T07:00:40Z",
        )
        self._append(conv_id, "operator", "What is happening here?", "2026-03-20T07:00:41Z")

        goal = {
            "id": "1-startup-history",
            "body": "What is happening here?",
            "assigned_to": "gardener",
            "conversation_id": conv_id,
        }
        sent_messages: list[str] = []
        prompt_holder: dict[str, str] = {}

        class FakeChannel:
            def send(self, conversation_id: str, content: str, **kwargs) -> None:
                sent_messages.append(content)

        def fake_launch(model, prompt, events_path, timeout=None, session_id=None,
                        driver_name="claude", cwd=None, reasoning_effort=None,
                        env=None):
            prompt_holder["prompt"] = prompt
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 0,
                            "output_tokens": 50,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (events_path.parent / "last-message.md").write_text(
                "Ordinary conversation reply.",
                encoding="utf-8",
            )
            return 0

        result, run_id = open_run(
            goal["id"],
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T07:00:42Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(run_id)

        with patch("system.driver._launch", side_effect=fake_launch):
            with patch("system.driver._make_channel", return_value=FakeChannel()):
                _dispatch_conversation(
                    goal,
                    run_id,
                    self.root,
                    conv_id,
                    "codex",
                    "gpt-5.4",
                    "xhigh",
                )

        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0], "Ordinary conversation reply.")
        self.assertIn(
            "Garden: System startup note from recorded bootstrap facts:",
            prompt_holder["prompt"],
        )

    def test_dispatch_conversation_preserves_reply_text_when_history_exists(self) -> None:
        conv_id = self._open_conversation()
        self._append(conv_id, "operator", "Earlier operator context", "2026-03-20T07:00:01Z")
        self._append(conv_id, "garden", "Earlier garden reply", "2026-03-20T07:00:02Z")
        self._append(conv_id, "operator", "Latest operator message", "2026-03-20T07:00:03Z")

        goal = {
            "id": "2-history-preserves-reply",
            "body": "Latest operator message",
            "assigned_to": "gardener",
            "conversation_id": conv_id,
        }
        sent_messages: list[str] = []

        class FakeChannel:
            def send(self, conversation_id: str, content: str, **kwargs) -> None:
                sent_messages.append(content)

        def fake_launch(model, prompt, events_path, timeout=None, session_id=None,
                        driver_name="claude", cwd=None, reasoning_effort=None,
                        env=None):
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 0,
                            "output_tokens": 50,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (events_path.parent / "last-message.md").write_text(
                "Ordinary conversation reply.",
                encoding="utf-8",
            )
            return 0

        result, run_id = open_run(
            goal["id"],
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T07:00:04Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(run_id)

        with patch("system.driver._launch", side_effect=fake_launch):
            with patch("system.driver._make_channel", return_value=FakeChannel()):
                _dispatch_conversation(
                    goal,
                    run_id,
                    self.root,
                    conv_id,
                    "codex",
                    "gpt-5.4",
                    "xhigh",
                )

        self.assertEqual(sent_messages, ["Ordinary conversation reply."])

    def test_request_conversation_hop_records_pending_request(self) -> None:
        conv_id = self._open_conversation()

        result = request_conversation_hop(
            conv_id,
            requested_by="operator",
            reason="fresh session before the next reply",
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:05:00Z",
        )
        self.assertTrue(result.ok)

        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertEqual(
            conv["pending_hop"],
            {
                "requested_at": "2026-03-20T07:05:00Z",
                "requested_by": "operator",
                "reason": "fresh session before the next reply",
            },
        )

    def test_write_conversation_checkpoint_archives_summary_and_record(self) -> None:
        conv_id = self._open_conversation()
        self._append(conv_id, "operator", "First operator message", "2026-03-20T07:00:01Z")
        handoff_marker = self._append(
            conv_id, "garden", "First garden reply", "2026-03-20T07:00:02Z"
        )

        from system.conversations import read_conversation_summary

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:02Z",
            session_id="session-1",
            session_ordinal=1,
            session_turns=3,
        )
        self.assertTrue(result.ok)

        result, record = write_conversation_checkpoint(
            conv_id,
            "# Conversation Handoff Summary\n\n- Preserve continuity.",
            handoff_marker,
            requested_by="operator",
            reason="manual checkpoint",
            source_session_id="session-1",
            run_id="1-maintenance-r1",
            driver="codex",
            model="gpt-5.4",
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:03Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(record)

        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertIsNone(conv["session_id"])
        self.assertEqual(conv["last_checkpoint_id"], record["id"])
        self.assertEqual(conv["checkpoint_count"], 1)
        self.assertEqual(conv["session_turns"], 0)
        self.assertEqual(conv["last_checkpoint_at"], "2026-03-20T07:00:03Z")
        self.assertEqual(
            read_conversation_summary(conv_id, _conv_dir=self.conv_dir),
            "# Conversation Handoff Summary\n\n- Preserve continuity.",
        )

        checkpoints = read_conversation_checkpoints(conv_id, _conv_dir=self.conv_dir)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["reason"], "manual checkpoint")
        archive_path = self.conv_dir / conv_id / checkpoints[0]["summary_path"]
        self.assertTrue(archive_path.exists())

        root_events = events_module.read_events(path=self.root / "events" / "coordinator.jsonl")
        checkpoint_event = root_events[-1]
        self.assertEqual(checkpoint_event["type"], "ConversationCheckpointWritten")
        self.assertEqual(checkpoint_event["conversation_id"], conv_id)
        self.assertEqual(checkpoint_event["checkpoint_id"], record["id"])
        self.assertEqual(checkpoint_event["checkpoint_requested_by"], "operator")
        self.assertEqual(checkpoint_event["checkpoint_reason"], "manual checkpoint")
        self.assertEqual(checkpoint_event["source_message_id"], handoff_marker)
        self.assertEqual(checkpoint_event["checkpoint_count"], 1)

    def test_write_conversation_checkpoint_omits_missing_source_session_id_from_event(self) -> None:
        conv_id = self._open_conversation()
        self._append(conv_id, "operator", "First operator message", "2026-03-20T07:00:01Z")
        handoff_marker = self._append(
            conv_id, "garden", "First garden reply", "2026-03-20T07:00:02Z"
        )

        result, record = write_conversation_checkpoint(
            conv_id,
            "# Conversation Handoff Summary\n\n- Preserve continuity.",
            handoff_marker,
            requested_by="operator",
            reason="manual checkpoint",
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:03Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(record)

        root_events = events_module.read_events(path=self.root / "events" / "coordinator.jsonl")
        checkpoint_event = root_events[-1]
        self.assertEqual(checkpoint_event["type"], "ConversationCheckpointWritten")
        self.assertNotIn("source_session_id", checkpoint_event)

    def test_dispatch_conversation_manual_hops_as_separate_goal_after_reply_delivery(self) -> None:
        conv_id = self._open_conversation()
        self._append(conv_id, "operator", "Earlier operator context", "2026-03-20T07:00:01Z")
        self._append(conv_id, "garden", "Earlier garden reply", "2026-03-20T07:00:02Z")
        self._append(conv_id, "operator", "Latest operator message", "2026-03-20T07:00:03Z")

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:03Z",
            session_id="session-1",
            session_ordinal=1,
            session_turns=2,
        )
        self.assertTrue(result.ok)

        result = request_conversation_hop(
            conv_id,
            requested_by="operator",
            reason="fresh session after this reply",
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:03Z",
        )
        self.assertTrue(result.ok)

        goal = {
            "id": "1-manual-hop",
            "body": "Latest operator message",
            "assigned_to": "gardener",
            "conversation_id": conv_id,
        }
        call_order: list[tuple[str, str]] = []

        class FakeChannel:
            def send(self, conversation_id: str, content: str, **kwargs) -> None:
                call_order.append(("send", content))

        def fake_launch(model, prompt, events_path, timeout=None, session_id=None,
                        driver_name="claude", cwd=None, reasoning_effort=None,
                        env=None):
            events_path.parent.mkdir(parents=True, exist_ok=True)
            if prompt.startswith("# Conversation checkpoint request"):
                call_order.append(("checkpoint_launch", session_id or ""))
                events_path.write_text("", encoding="utf-8")
                (events_path.parent / "last-message.md").write_text(
                    "# Conversation Handoff Summary\n\n"
                    "## Operator and style\n- Keep continuity natural.\n\n"
                    "## Durable context from earlier turns\n- Preserve earlier facts.\n\n"
                    "## Current agenda\n- Follow up after the latest reply.\n\n"
                    "## Commitments and open loops\n- Continue without mentioning the hop.\n",
                    encoding="utf-8",
                )
                return 0

            call_order.append(("reply_launch", session_id or ""))
            events_path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 1500,
                            "cached_input_tokens": 100,
                            "output_tokens": 80,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (events_path.parent / "last-message.md").write_text(
                "Manual-hop reply",
                encoding="utf-8",
            )
            return 0

        result, run_id = open_run(
            goal["id"],
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T07:00:04Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(run_id)

        with patch("system.driver._launch", side_effect=fake_launch):
            with patch("system.driver._make_channel", return_value=FakeChannel()):
                _dispatch_conversation(
                    goal,
                    run_id,
                    self.root,
                    conv_id,
                    "codex",
                    "gpt-5.4",
                    "xhigh",
                )

        self.assertEqual(call_order[0], ("reply_launch", "session-1"))
        self.assertEqual(call_order[1], ("send", "Manual-hop reply"))
        self.assertEqual(len(call_order), 2)

        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertEqual(conv["last_turn_mode"], "resumed")
        self.assertEqual(conv["session_id"], "session-1")
        self.assertEqual(conv["session_ordinal"], 1)
        self.assertEqual(conv["session_turns"], 3)
        self.assertEqual(conv["pending_hop"]["reason"], "fresh session after this reply")
        self.assertIsNone(conv["last_checkpoint_id"])
        self.assertEqual(conv["post_reply_hop"]["source_run_id"], run_id)

        queued_goals = list_goals(_goals_dir=self.root / "goals")
        self.assertEqual(len(queued_goals), 1)
        hop_goal = queued_goals[0]
        self.assertEqual(hop_goal["type"], "converse")
        self.assertEqual(hop_goal["conversation_id"], conv_id)
        self.assertEqual(
            hop_goal["post_reply_hop"]["source_reply_message_id"],
            conv["post_reply_hop"]["source_reply_message_id"],
        )

        turns = read_conversation_turns(conv_id, _conv_dir=self.conv_dir)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["mode"], "resumed")
        self.assertTrue(turns[0]["hop"]["requested"])
        self.assertTrue(turns[0]["hop"]["queued"])
        self.assertFalse(turns[0]["hop"]["performed"])
        self.assertFalse(turns[0]["hop"]["automatic"])
        self.assertEqual(turns[0]["hop"]["reason"], "fresh session after this reply")
        self.assertEqual(turns[0]["hop"]["goal_id"], hop_goal["id"])
        self.assertEqual(turns[0]["lineage"]["session_ordinal"], 1)
        self.assertEqual(turns[0]["lineage"]["session_turn"], 3)
        self.assertEqual(turns[0]["session_id_before"], "session-1")
        self.assertEqual(turns[0]["session_id_after"], "session-1")

        result, hop_run_id = open_run(
            hop_goal["id"],
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T07:00:05Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(hop_run_id)

        with patch("system.driver._launch", side_effect=fake_launch):
            dispatch(
                hop_goal,
                hop_run_id,
                _garden_root=self.root,
            )

        self.assertEqual(call_order[2], ("checkpoint_launch", "session-1"))

        checkpoints = read_conversation_checkpoints(conv_id, _conv_dir=self.conv_dir)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["reason"], "fresh session after this reply")
        self.assertEqual(checkpoints[0]["source_session_id"], "session-1")
        self.assertEqual(checkpoints[0]["source_session_turns"], 3)

        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertIsNone(conv["session_id"])
        self.assertEqual(conv["session_turns"], 0)
        self.assertIsNone(conv["pending_hop"])
        self.assertIsNone(conv["post_reply_hop"])
        self.assertIsNotNone(conv["last_checkpoint_id"])

        hop_artifact = json.loads(
            (self.root / "runs" / hop_run_id / "conversation-hop.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(hop_artifact["outcome"], "checkpointed")
        self.assertEqual(hop_artifact["checkpoint_id"], checkpoints[0]["id"])

    def test_dispatch_conversation_auto_hops_as_separate_goal_and_records_turn_snapshot(self) -> None:
        conv_id = self._open_conversation()
        self._append(conv_id, "operator", "Earlier operator context", "2026-03-20T07:00:01Z")
        self._append(conv_id, "garden", "Earlier garden reply", "2026-03-20T07:00:02Z")
        self._append(conv_id, "operator", "Latest operator message", "2026-03-20T07:00:03Z")
        live_events_path = self.root / "live-root" / "events" / "coordinator.jsonl"

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:03Z",
            session_id="session-1",
            session_ordinal=1,
            session_turns=7,
            last_pressure={
                "band": "critical",
                "provider_input_tokens": 1500000,
                "tail_messages": 3,
                "prompt_chars": 9000,
            },
        )
        self.assertTrue(result.ok)

        goal = {
            "id": "1-auto-hop",
            "body": "Latest operator message",
            "assigned_to": "gardener",
            "conversation_id": conv_id,
        }
        call_order: list[tuple[str, str]] = []

        class FakeChannel:
            def send(self, conversation_id: str, content: str, **kwargs) -> None:
                call_order.append(("send", content))

        def fake_launch(model, prompt, events_path, timeout=None, session_id=None,
                        driver_name="claude", cwd=None, reasoning_effort=None,
                        env=None):
            events_path.parent.mkdir(parents=True, exist_ok=True)
            if prompt.startswith("# Conversation checkpoint request"):
                call_order.append(("checkpoint_launch", session_id or ""))
                events_path.write_text("", encoding="utf-8")
                (events_path.parent / "last-message.md").write_text(
                    "# Conversation Handoff Summary\n\n"
                    "## Operator and style\n- Keep continuity natural.\n\n"
                    "## Durable context from earlier turns\n- Preserve earlier facts.\n\n"
                    "## Current agenda\n- Answer the latest operator question.\n\n"
                    "## Commitments and open loops\n- Continue without mentioning the hop.\n",
                    encoding="utf-8",
                )
                return 0

            call_order.append(("reply_launch", session_id or ""))
            events_path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 2345,
                            "cached_input_tokens": 200,
                            "output_tokens": 120,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (events_path.parent / "last-message.md").write_text(
                "Post-reply auto-hop response",
                encoding="utf-8",
            )
            return 0

        with patch.object(events_module, "_LOG_PATH", live_events_path):
            result, run_id = open_run(
                goal["id"],
                "gardener",
                "codex",
                "gpt-5.4",
                _runs_dir=self.root / "runs",
                _now="2026-03-20T07:00:04Z",
            )
            self.assertTrue(result.ok)
            self.assertIsNotNone(run_id)

            with patch("system.driver._launch", side_effect=fake_launch):
                with patch("system.driver._make_channel", return_value=FakeChannel()):
                    _dispatch_conversation(
                        goal,
                        run_id,
                        self.root,
                        conv_id,
                        "codex",
                        "gpt-5.4",
                        "xhigh",
                    )

        self.assertEqual(call_order[0], ("reply_launch", "session-1"))
        self.assertEqual(call_order[1], ("send", "Post-reply auto-hop response"))
        self.assertEqual(len(call_order), 2)

        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertEqual(conv["last_turn_mode"], "resumed")
        self.assertEqual(conv["session_id"], "session-1")
        self.assertEqual(conv["session_ordinal"], 1)
        self.assertEqual(conv["session_turns"], 8)
        self.assertIsNone(conv["last_checkpoint_id"])
        self.assertIsNotNone(conv["post_reply_hop"])
        self.assertEqual(conv["last_pressure"]["provider_input_tokens"], 2345)

        queued_goals = list_goals(_goals_dir=self.root / "goals")
        self.assertEqual(len(queued_goals), 1)
        hop_goal = queued_goals[0]
        self.assertEqual(hop_goal["type"], "converse")
        self.assertTrue(hop_goal["post_reply_hop"]["automatic"])

        turns = read_conversation_turns(conv_id, _conv_dir=self.conv_dir)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["mode"], "resumed")
        self.assertTrue(turns[0]["hop"]["queued"])
        self.assertFalse(turns[0]["hop"]["performed"])
        self.assertTrue(turns[0]["hop"]["automatic"])
        self.assertEqual(turns[0]["session_id_before"], "session-1")
        self.assertEqual(turns[0]["session_id_after"], "session-1")
        self.assertEqual(turns[0]["lineage"]["session_ordinal"], 1)
        self.assertEqual(turns[0]["lineage"]["session_turn"], 8)
        self.assertEqual(turns[0]["hop"]["goal_id"], hop_goal["id"])

        run_conv = json.loads(
            (self.root / "runs" / run_id / "conversation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(run_conv["run_id"], run_id)
        self.assertEqual(run_conv["lineage"]["session_ordinal"], 1)
        self.assertEqual(run_conv["lineage"]["session_turn"], 8)
        self.assertEqual(run_conv["pressure"]["provider_input_tokens"], 2345)

        result, hop_run_id = open_run(
            hop_goal["id"],
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T07:00:05Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(hop_run_id)

        with patch("system.driver._launch", side_effect=fake_launch):
            dispatch(
                hop_goal,
                hop_run_id,
                _garden_root=self.root,
            )

        self.assertEqual(call_order[2], ("checkpoint_launch", "session-1"))

        checkpoints = read_conversation_checkpoints(conv_id, _conv_dir=self.conv_dir)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["source_session_id"], "session-1")
        self.assertEqual(checkpoints[0]["source_session_turns"], 8)

        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertIsNone(conv["session_id"])
        self.assertEqual(conv["session_turns"], 0)
        self.assertIsNone(conv["post_reply_hop"])
        self.assertIsNotNone(conv["last_checkpoint_id"])

        hop_artifact = json.loads(
            (self.root / "runs" / hop_run_id / "conversation-hop.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(hop_artifact["outcome"], "checkpointed")
        self.assertEqual(hop_artifact["checkpoint_id"], checkpoints[0]["id"])
        self.assertTrue((self.root / "runs" / hop_run_id / "record.json").exists())

        root_events = events_module.read_events(path=self.root / "events" / "coordinator.jsonl")
        self.assertEqual(
            [(event["type"], event.get("run")) for event in root_events],
            [
                ("RunStarted", run_id),
                ("GoalSubmitted", None),
                ("ConversationHopQueued", run_id),
                ("RunFinished", run_id),
                ("RunStarted", hop_run_id),
                ("ConversationCheckpointWritten", hop_run_id),
                ("RunFinished", hop_run_id),
            ],
        )
        hop_submission = root_events[1]
        self.assertEqual(hop_submission["goal_subtype"], "post_reply_hop")
        self.assertEqual(hop_submission["conversation_id"], conv_id)
        self.assertTrue(hop_submission["hop_automatic"])
        hop_queue = root_events[2]
        self.assertEqual(hop_queue["conversation_id"], conv_id)
        self.assertEqual(hop_queue["hop_goal"], hop_goal["id"])
        self.assertTrue(hop_queue["hop_automatic"])
        checkpoint_event = root_events[-2]
        self.assertEqual(checkpoint_event["conversation_id"], conv_id)
        self.assertEqual(checkpoint_event["checkpoint_id"], checkpoints[0]["id"])
        self.assertEqual(
            checkpoint_event["checkpoint_summary_path"],
            checkpoints[0]["summary_path"],
        )
        hop_finish = root_events[-1]
        self.assertEqual(hop_finish["goal_subtype"], "post_reply_hop")
        self.assertEqual(hop_finish["hop_outcome"], "checkpointed")
        self.assertEqual(hop_finish["checkpoint_id"], checkpoints[0]["id"])
        self.assertEqual(events_module.read_events(path=live_events_path), [])

    def test_dispatch_conversation_emits_hop_queue_failed_event_when_submission_fails(self) -> None:
        conv_id = self._open_conversation()
        self._append(conv_id, "operator", "Earlier operator context", "2026-03-20T07:00:01Z")
        self._append(conv_id, "garden", "Earlier garden reply", "2026-03-20T07:00:02Z")
        self._append(conv_id, "operator", "Latest operator message", "2026-03-20T07:00:03Z")

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:03Z",
            session_id="session-1",
            session_ordinal=1,
            session_turns=7,
            last_pressure={
                "band": "critical",
                "provider_input_tokens": 1500000,
                "tail_messages": 3,
                "prompt_chars": 9000,
            },
        )
        self.assertTrue(result.ok)

        goal = {
            "id": "1-auto-hop-failure",
            "body": "Latest operator message",
            "assigned_to": "gardener",
            "conversation_id": conv_id,
        }

        class FakeChannel:
            def send(self, conversation_id: str, content: str, **kwargs) -> None:
                return None

        def fake_launch(model, prompt, events_path, timeout=None, session_id=None,
                        driver_name="claude", cwd=None, reasoning_effort=None,
                        env=None):
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 2345,
                            "cached_input_tokens": 200,
                            "output_tokens": 120,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (events_path.parent / "last-message.md").write_text(
                "Post-reply auto-hop response",
                encoding="utf-8",
            )
            return 0

        result, run_id = open_run(
            goal["id"],
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T07:00:04Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(run_id)

        with patch("system.driver._launch", side_effect=fake_launch):
            with patch("system.driver._make_channel", return_value=FakeChannel()):
                with patch(
                    "system.driver._submit_post_reply_hop_goal",
                    return_value=(
                        None,
                        "post-reply hop goal submission failed: goal store unavailable",
                    ),
                ):
                    _dispatch_conversation(
                        goal,
                        run_id,
                        self.root,
                        conv_id,
                        "codex",
                        "gpt-5.4",
                        "xhigh",
                    )

        self.assertEqual(list_goals(_goals_dir=self.root / "goals"), [])
        root_events = events_module.read_events(path=self.root / "events" / "coordinator.jsonl")
        self.assertEqual(
            [event["type"] for event in root_events],
            ["RunStarted", "ConversationHopQueueFailed", "RunFinished"],
        )
        hop_failure = root_events[1]
        self.assertEqual(hop_failure["conversation_id"], conv_id)
        self.assertTrue(hop_failure["hop_automatic"])
        self.assertEqual(hop_failure["hop_reason"], "automatic pressure handoff")
        self.assertIn("goal store unavailable", hop_failure["detail"])

    def test_external_append_hop_forces_next_operator_turn_to_start_fresh(self) -> None:
        conv_id = self._open_conversation()
        source_message_id = self._append(
            conv_id,
            "operator",
            "How is the garden doing?",
            "2026-03-20T07:00:01Z",
        )
        prior_reply_message_id = self._append(
            conv_id,
            "garden",
            "Earlier garden reply from the active session.",
            "2026-03-20T07:00:02Z",
        )

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-20T07:00:02Z",
            session_id="session-1",
            session_ordinal=1,
            session_turns=2,
            session_started_at="2026-03-20T07:00:00Z",
            context_at="2026-03-20T07:00:02Z",
        )
        self.assertTrue(result.ok)

        result, tend_goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Perform a bounded survey.",
                "origin": {
                    "kind": "conversation",
                    "conversation_id": conv_id,
                    "message_id": source_message_id,
                    "ts": "2026-03-20T07:00:01Z",
                },
                "tend": {
                    "trigger_kinds": ["operator_request"],
                },
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-20T07:00:01Z",
        )
        self.assertTrue(result.ok)
        tend_goal_id = tend_goal_id or ""
        tend_run_id = f"{tend_goal_id}-r1"

        with patch.dict(
            os.environ,
            {
                "PAK2_GARDEN_ROOT": str(self.root),
                "PAK2_CURRENT_GOAL_ID": tend_goal_id,
                "PAK2_CURRENT_RUN_ID": tend_run_id,
                "PAK2_CURRENT_PLANT": "gardener",
                "PAK2_CURRENT_GOALS_DIR": str(self.root / "goals"),
            },
            clear=False,
        ):
            result, record = emit_tend_survey(
                "The garden is healthy.\n",
                _garden_root=self.root,
                _now="2026-03-20T07:00:03Z",
            )

        self.assertTrue(result.ok, result.detail)
        assert record is not None

        hop_goal = next(
            goal for goal in list_goals(_goals_dir=self.root / "goals")
            if goal.get("post_reply_hop")
        )
        self.assertEqual(
            hop_goal["post_reply_hop"]["source_reply_message_id"],
            prior_reply_message_id,
        )

        def fake_hop_launch(model, prompt, events_path, timeout=None, session_id=None,
                            driver_name="claude", cwd=None, reasoning_effort=None,
                            env=None):
            self.assertEqual(session_id, "session-1")
            self.assertTrue(prompt.startswith("# Conversation checkpoint request"))
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text("", encoding="utf-8")
            (events_path.parent / "last-message.md").write_text(
                "# Conversation Handoff Summary\n\n"
                "## Operator and style\n- Keep continuity natural.\n\n"
                "## Durable context from earlier turns\n"
                "- Preserve the earlier session facts.\n\n"
                "## Current agenda\n"
                "- Continue from the durable conversation.\n\n"
                "## Commitments and open loops\n"
                "- Do not mention the hop.\n",
                encoding="utf-8",
            )
            return 0

        result, hop_run_id = open_run(
            hop_goal["id"],
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T07:00:04Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(hop_run_id)

        with patch("system.driver._launch", side_effect=fake_hop_launch):
            dispatch(
                hop_goal,
                hop_run_id,
                _garden_root=self.root,
            )

        conv = read_conversation(conv_id, _conv_dir=self.conv_dir)
        self.assertIsNotNone(conv)
        self.assertIsNone(conv["session_id"])
        self.assertIsNone(conv["post_reply_hop"])
        self.assertIsNotNone(conv["last_checkpoint_id"])

        self._append(
            conv_id,
            "operator",
            "What should I know next?",
            "2026-03-20T07:00:05Z",
        )
        result, converse_goal_id = submit_goal(
            {
                "type": "converse",
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "priority": 7,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": "What should I know next?",
                "conversation_id": conv_id,
            },
            _goals_dir=self.root / "goals",
            _now="2026-03-20T07:00:05Z",
        )
        self.assertTrue(result.ok)
        converse_goal_id = converse_goal_id or ""
        converse_goal = next(
            goal for goal in list_goals(_goals_dir=self.root / "goals")
            if goal["id"] == converse_goal_id
        )
        prompt_holder: dict[str, str | None] = {"session_id": None, "prompt": None}

        class FakeChannel:
            def send(self, conversation_id: str, content: str, **kwargs) -> None:
                return None

        def fake_reply_launch(model, prompt, events_path, timeout=None, session_id=None,
                              driver_name="claude", cwd=None, reasoning_effort=None,
                              env=None):
            prompt_holder["session_id"] = session_id
            prompt_holder["prompt"] = prompt
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "thread.started",
                                "thread_id": "session-2",
                            }
                        ),
                        json.dumps(
                            {
                                "type": "turn.completed",
                                "usage": {
                                    "input_tokens": 900,
                                    "cached_input_tokens": 0,
                                    "output_tokens": 70,
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (events_path.parent / "last-message.md").write_text(
                "Fresh-session reply",
                encoding="utf-8",
            )
            return 0

        result, converse_run_id = open_run(
            converse_goal_id,
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T07:00:06Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(converse_run_id)

        with patch("system.driver._launch", side_effect=fake_reply_launch):
            with patch("system.driver._make_channel", return_value=FakeChannel()):
                _dispatch_conversation(
                    converse_goal,
                    converse_run_id,
                    self.root,
                    conv_id,
                    "codex",
                    "gpt-5.4",
                    "xhigh",
                )

        self.assertIsNone(prompt_holder["session_id"])
        self.assertIsNotNone(prompt_holder["prompt"])
        self.assertIn("# Conversation handoff", str(prompt_holder["prompt"]))
        self.assertIn("Garden: The garden is healthy.", str(prompt_holder["prompt"]))
        self.assertIn("# Recent conversation tail", str(prompt_holder["prompt"]))


if __name__ == "__main__":
    unittest.main()
