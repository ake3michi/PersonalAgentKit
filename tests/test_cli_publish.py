import contextlib
import io
import pathlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from system import cli


def _relative_files(root: pathlib.Path) -> set[str]:
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
    }


class PublishCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.source_root = cli._TEMPLATE_ROOT

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _publish_args(self, dest: pathlib.Path) -> SimpleNamespace:
        return SimpleNamespace(root=str(self.source_root), dir=str(dest))

    def test_cmd_publish_matches_authored_surface_and_excludes_runtime_state(self) -> None:
        publish_dest = self.root / "publish-export"
        init_dest = self.root / "init-export"
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            cli.cmd_publish(self._publish_args(publish_dest))

        with patch.object(cli.subprocess, "run") as mock_run:
            cli.cmd_init(
                SimpleNamespace(
                    dir=str(init_dest),
                    default_driver=None,
                    default_model=None,
                    default_reasoning_effort=None,
                )
            )

        publish_files = _relative_files(publish_dest)
        init_files = _relative_files(init_dest)

        self.assertEqual(publish_files | {"CHARTER.md"}, init_files)
        self.assertEqual(init_files - publish_files, {"CHARTER.md"})
        self.assertTrue((publish_dest / "README.md").exists())
        self.assertTrue((publish_dest / "LICENSE").exists())
        self.assertTrue((publish_dest / "CHARTER.md.example").exists())
        self.assertTrue((publish_dest / "examples" / "charter-quickstart.md").exists())
        self.assertFalse((publish_dest / "CHARTER.md").exists())
        self.assertTrue((publish_dest / "PAK2.toml").exists())
        self.assertTrue((publish_dest / "PAK2.toml.example").exists())
        self.assertTrue((publish_dest / ".gitignore").exists())

        self.assertFalse((publish_dest / ".runtime").exists())
        self.assertFalse((publish_dest / "goals").exists())
        self.assertFalse((publish_dest / "runs").exists())
        self.assertFalse((publish_dest / "events").exists())
        self.assertFalse((publish_dest / "conversations").exists())
        self.assertFalse((publish_dest / "inbox").exists())
        self.assertFalse((publish_dest / "dashboard").exists())
        self.assertFalse((publish_dest / "plants").exists())
        self.assertFalse((publish_dest / "assets").exists())

        config_text = (publish_dest / "PAK2.toml").read_text(encoding="utf-8")
        system_text = (publish_dest / "docs" / "system" / "level3.md").read_text(
            encoding="utf-8"
        )
        goal_text = (publish_dest / "docs" / "goal" / "level3.md").read_text(
            encoding="utf-8"
        )
        run_text = (publish_dest / "docs" / "run" / "level3.md").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("[defaults]", config_text)
        self.assertIn('"codex"', system_text)
        self.assertIn('"gpt-5.4"', system_text)
        self.assertNotIn('Default: `"claude"`', system_text)
        self.assertNotIn('Default: `"claude-opus-4-6"`', system_text)
        self.assertIn('"driver": "codex"', goal_text)
        self.assertIn('"model": "gpt-5.4"', goal_text)
        self.assertNotIn('"driver": "claude"', goal_text)
        self.assertNotIn('"model": "claude-opus-4-6"', goal_text)
        self.assertIn('"driver": "codex"', run_text)
        self.assertIn('"model": "gpt-5.4"', run_text)
        self.assertNotIn('"driver": "claude"', run_text)
        self.assertNotIn('"model": "claude-opus-4-6"', run_text)

        output = stdout.getvalue()
        self.assertIn(f"Publish worktree created at {publish_dest}", output)
        self.assertIn(
            f"Quickstart charter example: {publish_dest / 'examples' / 'charter-quickstart.md'}",
            output,
        )
        self.assertIn(f"Custom charter template: {publish_dest / 'CHARTER.md.example'}", output)
        self.assertIn("`pak2 init` materializes `CHARTER.md` in each new garden.", output)
        self.assertIn("No git push, tag, or release action was performed.", output)
        self.assertEqual(mock_run.call_count, 3)

    def test_cmd_publish_replaces_existing_git_checkout_contents_except_git_dir(self) -> None:
        dest = self.root / "public-repo"
        dest.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
        (dest / "old.txt").write_text("remove me\n", encoding="utf-8")
        (dest / ".github").mkdir()
        (dest / ".github" / "workflow.yml").write_text("old\n", encoding="utf-8")

        cli.cmd_publish(self._publish_args(dest))

        self.assertTrue((dest / ".git").exists())
        self.assertFalse((dest / "old.txt").exists())
        self.assertFalse((dest / ".github").exists())
        self.assertTrue((dest / "pak2").exists())
        self.assertTrue((dest / "README.md").exists())
        self.assertTrue((dest / "LICENSE").exists())
        self.assertFalse((dest / "CHARTER.md").exists())
        self.assertTrue((dest / "PAK2.toml.example").exists())
        self.assertTrue((dest / "examples" / "charter-quickstart.md").exists())
        self.assertTrue((dest / "docs" / "system" / "level1.md").exists())

    def test_cmd_publish_rejects_nonempty_non_git_destination(self) -> None:
        dest = self.root / "occupied"
        dest.mkdir()
        (dest / "junk.txt").write_text("junk\n", encoding="utf-8")

        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as exc, contextlib.redirect_stderr(stderr):
            cli.cmd_publish(self._publish_args(dest))

        self.assertEqual(exc.exception.code, 1)
        self.assertIn("destination exists and is not empty", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
