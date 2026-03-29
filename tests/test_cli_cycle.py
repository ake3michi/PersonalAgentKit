import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from system import cli
from system.channels import FilesystemChannel
from system.conversations import list_conversations, read_conversation, read_messages
from system.garden import filesystem_reply_dir, garden_paths
from system.genesis import genesis
from system.goals import list_goals
from system.somatic import SomaticLoop


class CliCycleStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "CHARTER.md").write_text("# Charter\n", encoding="utf-8")
        (self.root / "seeds").mkdir(parents=True, exist_ok=True)
        (self.root / "seeds" / "gardener.md").write_text(
            "# Gardener seed\n",
            encoding="utf-8",
        )
        (self.root / "seeds" / "gardener" / "skills").mkdir(parents=True, exist_ok=True)
        (self.root / "seeds" / "gardener" / "knowledge").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _args(self) -> SimpleNamespace:
        return SimpleNamespace(
            root=str(self.root),
            max_concurrent=2,
            poll_interval=60,
        )

    def _fake_thread(self) -> Mock:
        thread = Mock()
        thread.start.return_value = None
        return thread

    def _run_cycle(self) -> None:
        holder: dict[str, object] = {}

        class FakeCoordinator:
            def __init__(self, root: pathlib.Path, *, max_concurrent: int, poll_interval: int):
                self.root = root
                self.max_concurrent = max_concurrent
                self.poll_interval = poll_interval
                self.wake = Mock()
                self.startup_conversation_id = None
                holder["coord"] = self

            def run(self) -> None:
                return None

            def set_startup_conversation(self, conversation_id: str | None) -> None:
                self.startup_conversation_id = conversation_id

        with patch("system.coordinator.Coordinator", FakeCoordinator), patch(
            "threading.Thread",
            return_value=self._fake_thread(),
        ):
            cli.cmd_cycle(self._args())
        self.coordinator = holder.get("coord")

    def test_cmd_cycle_opens_system_started_startup_thread_and_reuses_it(self) -> None:
        genesis(self.root)
        paths = garden_paths(garden_root=self.root)
        self._run_cycle()

        conversations = list_conversations(_conv_dir=paths.conversations_dir)
        self.assertEqual(len(conversations), 1)
        conv_id = conversations[0]["id"]
        conv = read_conversation(conv_id, _conv_dir=paths.conversations_dir)
        self.assertIsNotNone(conv)
        self.assertEqual(conv["started_by"], "system")
        self.assertEqual(conv["channel"], "filesystem")
        self.assertEqual(conv["channel_ref"], str(paths.operator_inbox_dir.relative_to(self.root)))
        self.assertIsNotNone(self.coordinator)
        self.assertEqual(self.coordinator.startup_conversation_id, conv_id)

        messages = read_messages(conv_id, _conv_dir=paths.conversations_dir)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["sender"], "system")
        self.assertIn("System startup note from recorded bootstrap facts:", messages[0]["content"])
        self.assertIn("is queued.", messages[0]["content"])
        self.assertTrue(list((self.root / "inbox" / "garden").glob("*.md")))

        loop = SomaticLoop(self.root)
        loop._handle(
            {
                "content": "Hello after startup",
                "channel_ref": str(paths.operator_inbox_dir.relative_to(self.root)),
            },
            FilesystemChannel(self.root),
        )

        conversations = list_conversations(_conv_dir=paths.conversations_dir)
        self.assertEqual(len(conversations), 1)
        messages = read_messages(conv_id, _conv_dir=paths.conversations_dir)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["sender"], "operator")
        self.assertEqual(messages[1]["content"], "Hello after startup")

        goals = list_goals(_goals_dir=paths.goals_dir)
        self.assertEqual(len(goals), 2)
        converse_goals = [goal for goal in goals if goal["type"] == "converse"]
        self.assertEqual(len(converse_goals), 1)
        self.assertEqual(converse_goals[0]["conversation_id"], conv_id)

    def test_cmd_cycle_wires_converse_finish_back_into_somatic_wake(self) -> None:
        genesis(self.root)
        holder: dict[str, object] = {}

        class FakeCoordinator:
            def __init__(self, root: pathlib.Path, *, max_concurrent: int, poll_interval: int):
                self.root = root
                self.max_concurrent = max_concurrent
                self.poll_interval = poll_interval
                self.wake = Mock()
                self.on_converse_finished = None
                holder["coord"] = self

            def run(self) -> None:
                return None

            def set_startup_conversation(self, conversation_id: str | None) -> None:
                return None

        class FakeSomaticLoop:
            def __init__(self, root: pathlib.Path, *, on_goal_submitted=None, **kwargs):
                self.root = root
                self.on_goal_submitted = on_goal_submitted
                self.wake = Mock()
                holder["somatic"] = self

            def run(self) -> None:
                return None

        with patch("system.coordinator.Coordinator", FakeCoordinator), patch(
            "system.somatic.SomaticLoop",
            FakeSomaticLoop,
        ), patch("threading.Thread", return_value=self._fake_thread()):
            cli.cmd_cycle(self._args())

        coord = holder.get("coord")
        somatic = holder.get("somatic")
        self.assertIsNotNone(coord)
        self.assertIsNotNone(somatic)
        self.assertIs(somatic.on_goal_submitted, coord.wake)
        self.assertIs(coord.on_converse_finished, somatic.wake)

    def test_cmd_cycle_does_not_record_startup_note_when_delivery_fails(self) -> None:
        genesis(self.root)
        paths = garden_paths(garden_root=self.root)

        with patch(
            "system.channels.FilesystemChannel.send",
            side_effect=RuntimeError("disk full"),
        ):
            self._run_cycle()

        conversations = list_conversations(_conv_dir=paths.conversations_dir)
        self.assertEqual(len(conversations), 1)
        conv_id = conversations[0]["id"]
        conv = read_conversation(conv_id, _conv_dir=paths.conversations_dir)
        self.assertIsNotNone(conv)
        self.assertEqual(conv["started_by"], "system")
        self.assertEqual(read_messages(conv_id, _conv_dir=paths.conversations_dir), [])
        self.assertTrue(filesystem_reply_dir(self.root).exists())
        self.assertEqual(list(filesystem_reply_dir(self.root).glob("*.md")), [])

    def test_cmd_cycle_records_existing_delivered_startup_note_without_resending(self) -> None:
        genesis(self.root)
        paths = garden_paths(garden_root=self.root)

        with patch("system.cli._append_cycle_startup_message", return_value=False):
            self._run_cycle()

        conversations = list_conversations(_conv_dir=paths.conversations_dir)
        self.assertEqual(len(conversations), 1)
        conv_id = conversations[0]["id"]
        reply_files = list(filesystem_reply_dir(self.root).glob("*.md"))
        self.assertEqual(len(reply_files), 1)
        self.assertEqual(read_messages(conv_id, _conv_dir=paths.conversations_dir), [])

        self._run_cycle()

        reply_files = list(filesystem_reply_dir(self.root).glob("*.md"))
        self.assertEqual(len(reply_files), 1)
        messages = read_messages(conv_id, _conv_dir=paths.conversations_dir)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["sender"], "system")
        self.assertEqual(
            messages[0]["content"],
            reply_files[0].read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
