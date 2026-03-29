import contextlib
import io
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from system import cli
from system.garden import set_garden_name


class CliChatReplyDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _args(self) -> SimpleNamespace:
        return SimpleNamespace(root=str(self.root))

    def _fake_thread(self) -> Mock:
        thread = Mock()
        thread.start.return_value = None
        thread.join.return_value = None
        return thread

    def test_cmd_chat_reads_replies_from_configured_directory(self) -> None:
        result = set_garden_name("sprout", garden_root=self.root)
        self.assertTrue(result.ok)

        legacy_dir = self.root / "inbox" / "garden"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "legacy.md").write_text("Legacy reply.\n", encoding="utf-8")

        configured_dir = self.root / "inbox" / "sprout"
        configured_dir.mkdir(parents=True, exist_ok=True)

        inputs = iter(("hello", "exit"))

        def fake_input(_prompt: str) -> str:
            value = next(inputs)
            if value == "hello":
                (configured_dir / "20260323T190200Z-1-hello.md").write_text(
                    "Configured reply.\n",
                    encoding="utf-8",
                )
            return value

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch(
            "builtins.input",
            side_effect=fake_input,
        ), patch(
            "threading.Thread",
            return_value=self._fake_thread(),
        ), patch(
            "time.sleep",
            return_value=None,
        ):
            cli.cmd_chat(self._args())

        output = stdout.getvalue()
        self.assertIn("Configured reply.", output)
        self.assertNotIn("Legacy reply.", output)

    def test_cmd_chat_reads_replies_from_default_directory_without_garden_name(self) -> None:
        configured_dir = self.root / "inbox" / "sprout"
        configured_dir.mkdir(parents=True, exist_ok=True)
        (configured_dir / "configured.md").write_text("Configured reply.\n", encoding="utf-8")

        default_dir = self.root / "inbox" / "garden"
        default_dir.mkdir(parents=True, exist_ok=True)

        inputs = iter(("hello", "exit"))

        def fake_input(_prompt: str) -> str:
            value = next(inputs)
            if value == "hello":
                (default_dir / "20260323T190200Z-1-hello.md").write_text(
                    "Default reply.\n",
                    encoding="utf-8",
                )
            return value

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch(
            "builtins.input",
            side_effect=fake_input,
        ), patch(
            "threading.Thread",
            return_value=self._fake_thread(),
        ), patch(
            "time.sleep",
            return_value=None,
        ):
            cli.cmd_chat(self._args())

        output = stdout.getvalue()
        self.assertIn("Default reply.", output)
        self.assertNotIn("Configured reply.", output)

    def test_cmd_chat_does_not_force_reply_dir_migration_before_first_reply(self) -> None:
        result = set_garden_name("sprout", garden_root=self.root)
        self.assertTrue(result.ok)

        legacy_dir = self.root / "inbox" / "garden"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "legacy.md").write_text("Legacy reply.\n", encoding="utf-8")

        with patch(
            "builtins.input",
            side_effect=EOFError,
        ), patch(
            "threading.Thread",
            return_value=self._fake_thread(),
        ):
            cli.cmd_chat(self._args())

        self.assertTrue(legacy_dir.exists())
        self.assertFalse((self.root / "inbox" / "sprout").exists())

    def test_cmd_chat_uses_memory_identity_for_chat_label_when_reply_dir_is_default(self) -> None:
        memory_dir = self.root / "plants" / "gardener" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "MEMORY.md").write_text("# sprout\n\n## Identity\n", encoding="utf-8")

        default_dir = self.root / "inbox" / "garden"
        default_dir.mkdir(parents=True, exist_ok=True)

        inputs = iter(("hello", "exit"))

        def fake_input(_prompt: str) -> str:
            value = next(inputs)
            if value == "hello":
                (default_dir / "20260323T190200Z-1-hello.md").write_text(
                    "Identity reply.\n",
                    encoding="utf-8",
                )
            return value

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), patch(
            "builtins.input",
            side_effect=fake_input,
        ), patch(
            "threading.Thread",
            return_value=self._fake_thread(),
        ), patch(
            "time.sleep",
            return_value=None,
        ):
            cli.cmd_chat(self._args())

        output = stdout.getvalue()
        self.assertIn("=== sprout chat ===", output)
        self.assertIn("\nsprout:\n", output)
        self.assertNotIn("\ngarden:\n", output)


class CmdChatTTYTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _args(self) -> SimpleNamespace:
        return SimpleNamespace(root=str(self.root))

    def _fake_thread(self) -> Mock:
        thread = Mock()
        thread.start.return_value = None
        thread.join.return_value = None
        return thread

    def test_cmd_chat_uses_sent_message_render_for_pending_tty_send(self) -> None:
        result = set_garden_name("sprout", garden_root=self.root)
        self.assertTrue(result.ok)

        fake_ui = Mock()
        fake_ui.__enter__ = Mock(return_value=fake_ui)
        fake_ui.__exit__ = Mock(return_value=None)
        fake_ui.read_message.side_effect = ["hello\nsprout", EOFError]

        fake_stdout = io.StringIO()
        fake_stdout.isatty = lambda: True  # type: ignore[attr-defined]
        fake_stdin = SimpleNamespace(isatty=lambda: True)

        with patch.object(cli.sys, "stdout", fake_stdout), patch.object(
            cli.sys,
            "stdin",
            fake_stdin,
        ), patch(
            "system.cli._ChatTTYUI",
            return_value=fake_ui,
        ), patch(
            "system.cli.termios",
            object(),
        ), patch(
            "system.cli.tty",
            object(),
        ), patch(
            "threading.Thread",
            return_value=self._fake_thread(),
        ), patch(
            "time.sleep",
            return_value=None,
        ):
            cli.cmd_chat(self._args())

        inbox_messages = sorted((self.root / "inbox" / "operator").glob("*.md"))

        fake_ui.print_sent_message.assert_called_once_with("hello\nsprout")
        fake_ui.print_note.assert_called_with("bye", redraw_prompt=False)
        self.assertEqual(len(inbox_messages), 1)
        self.assertEqual(inbox_messages[0].read_text(encoding="utf-8"), "hello\nsprout")
        output = fake_stdout.getvalue()
        self.assertIn("Use the visible multi-line draft area below.", output)
        self.assertIn("Long lines wrap in place.", output)
        self.assertIn("Press Enter to send or Ctrl-J to add a newline.", output)


class ChatLineEditorTests(unittest.TestCase):
    def test_editor_supports_cursor_motion_mid_line_insert_and_multiline_submit(self) -> None:
        editor = cli._ChatLineEditor()

        for key in "helo":
            action, payload = editor.handle_key(key)
            self.assertEqual(action, "continue")
            self.assertIsNone(payload)

        editor.handle_key("\x1b[D")
        editor.handle_key("\x1b[D")
        action, payload = editor.handle_key("l")

        self.assertEqual(action, "continue")
        self.assertIsNone(payload)
        self.assertEqual(editor.text, "hello")
        self.assertEqual(editor.cursor, 3)

        action, payload = editor.handle_key("\n")

        self.assertEqual(action, "continue")
        self.assertIsNone(payload)

        for key in "sprout":
            action, payload = editor.handle_key(key)
            self.assertEqual(action, "continue")
            self.assertIsNone(payload)

        self.assertEqual(editor.text, "hel\nsproutlo")
        self.assertEqual(editor.cursor, len("hel\nsprout"))

        action, payload = editor.handle_key("\r")

        self.assertEqual(action, "submit")
        self.assertEqual(payload, "hel\nsproutlo")
        self.assertEqual(editor.text, "")
        self.assertEqual(editor.cursor, 0)

    def test_editor_supports_vertical_cursor_motion_between_lines(self) -> None:
        editor = cli._ChatLineEditor()

        for key in "abc\ndefg":
            editor.handle_key(key)

        editor.handle_key("\x1b[A")
        action, payload = editor.handle_key("X")

        self.assertEqual(action, "continue")
        self.assertIsNone(payload)
        self.assertEqual(editor.text, "abcX\ndefg")

    def test_render_view_wraps_long_lines_in_place(self) -> None:
        editor = cli._ChatLineEditor()

        for key in "abcdefghij":
            editor.handle_key(key)

        visible_lines, cursor_row, cursor_column = editor.render_view(
            width=9,
            prompt_width=5,
            max_lines=4,
        )

        self.assertEqual(visible_lines, ["abcd", "efgh", "ij"])
        self.assertEqual(cursor_row, 2)
        self.assertEqual(cursor_column, 7)

    def test_editor_uses_visual_rows_for_vertical_motion_when_wrapped(self) -> None:
        editor = cli._ChatLineEditor()

        for key in "abcdefghij":
            editor.handle_key(key)

        action, payload = editor.handle_key("\x1b[A", available_width=4)

        self.assertEqual(action, "continue")
        self.assertIsNone(payload)
        self.assertEqual(editor.cursor, 6)

        action, payload = editor.handle_key("X", available_width=4)

        self.assertEqual(action, "continue")
        self.assertIsNone(payload)
        self.assertEqual(editor.text, "abcdefXghij")


class ChatTTYUITests(unittest.TestCase):
    def test_print_sent_message_keeps_multiline_operator_text_visible_without_waiting_line(self) -> None:
        ui = cli._ChatTTYUI(garden_label="sprout", use_ansi=False)
        writes: list[str] = []
        ui._write = writes.append  # type: ignore[method-assign]

        ui.print_sent_message("hello\nsprout")

        rendered = "".join(writes)
        self.assertIn("you: hello\n", rendered)
        self.assertIn("     sprout\n", rendered)
        self.assertNotIn("waiting for sprout", rendered)
        self.assertEqual(writes[-1], "\ryou: ")

    def test_print_reply_redraws_prompt_with_existing_multiline_draft(self) -> None:
        ui = cli._ChatTTYUI(garden_label="sprout", use_ansi=False)
        writes: list[str] = []
        ui._write = writes.append  # type: ignore[method-assign]

        for key in "draft\nnext":
            ui.editor.handle_key(key)

        ui.redraw_prompt()
        ui.print_reply("Async reply.\n", status_line="[context: mode=resumed]")

        rendered = "".join(writes)
        self.assertIn("[context: mode=resumed]\n", rendered)
        self.assertIn("sprout:\n", rendered)
        self.assertIn("Async reply.\n", rendered)
        self.assertTrue(rendered.endswith("\ryou: draft\n     next"))


if __name__ == "__main__":
    unittest.main()
