import pathlib
import tempfile
import unittest

from system.channels import FilesystemChannel
from system.garden import (
    DEFAULT_GARDEN_NAME,
    garden_paths,
    resolve_garden_display_name,
    read_garden_name,
    resolve_garden_name,
    set_garden_name,
)


class GardenConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_resolve_garden_name_defaults_to_legacy_name(self) -> None:
        self.assertEqual(
            resolve_garden_name(garden_root=self.root),
            DEFAULT_GARDEN_NAME,
        )

    def test_resolve_garden_display_name_falls_back_to_memory_identity(self) -> None:
        memory_dir = self.root / "plants" / "gardener" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "MEMORY.md").write_text("# sprout\n\n## Identity\n", encoding="utf-8")

        self.assertEqual(
            resolve_garden_display_name(garden_root=self.root),
            "sprout",
        )

    def test_resolve_garden_display_name_prefers_configured_name(self) -> None:
        memory_dir = self.root / "plants" / "gardener" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "MEMORY.md").write_text("# sprout\n", encoding="utf-8")

        result = set_garden_name("clearpath", garden_root=self.root)

        self.assertTrue(result.ok)
        self.assertEqual(
            resolve_garden_display_name(garden_root=self.root),
            "clearpath",
        )

    def test_set_garden_name_rejects_invalid_name(self) -> None:
        result = set_garden_name("Clear Path", garden_root=self.root)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_GARDEN_NAME")

    def test_set_garden_name_preserves_existing_defaults(self) -> None:
        (self.root / "PAK2.toml").write_text(
            "[defaults]\nmodel = \"gpt-5.4\"\n",
            encoding="utf-8",
        )

        result = set_garden_name("clearpath", garden_root=self.root)

        self.assertTrue(result.ok)
        config_text = (self.root / "PAK2.toml").read_text(encoding="utf-8")
        self.assertIn("[defaults]", config_text)
        self.assertIn("model = \"gpt-5.4\"", config_text)
        self.assertIn("[garden]", config_text)
        self.assertIn("name = \"clearpath\"", config_text)
        self.assertEqual(read_garden_name(garden_root=self.root), "clearpath")

    def test_garden_paths_preserve_current_runtime_layout(self) -> None:
        paths = garden_paths(garden_root=self.root)

        self.assertEqual(paths.garden_root, self.root)
        self.assertEqual(paths.runtime_root, self.root)
        self.assertEqual(paths.goals_dir, self.root / "goals")
        self.assertEqual(paths.runs_dir, self.root / "runs")
        self.assertEqual(paths.events_dir, self.root / "events")
        self.assertEqual(paths.coordinator_events_path, self.root / "events" / "coordinator.jsonl")
        self.assertEqual(paths.conversations_dir, self.root / "conversations")
        self.assertEqual(paths.inbox_dir, self.root / "inbox")
        self.assertEqual(paths.operator_inbox_dir, self.root / "inbox" / "operator")
        self.assertEqual(paths.dashboard_invocations_dir, self.root / "dashboard" / "invocations")
        self.assertEqual(paths.plants_dir, self.root / "plants")
        self.assertEqual(paths.seeds_dir, self.root / "seeds")


class FilesystemChannelGardenNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_send_uses_configured_reply_directory(self) -> None:
        result = set_garden_name("clearpath", garden_root=self.root)
        self.assertTrue(result.ok)

        channel = FilesystemChannel(self.root)
        channel.send("1-hello", "Hello operator.\n")

        sent_files = list((self.root / "inbox" / "clearpath").glob("*.md"))
        self.assertEqual(len(sent_files), 1)
        self.assertEqual(sent_files[0].read_text(encoding="utf-8"), "Hello operator.\n")

    def test_send_reads_updated_config_after_channel_construction(self) -> None:
        channel = FilesystemChannel(self.root)

        result = set_garden_name("clearpath", garden_root=self.root)
        self.assertTrue(result.ok)

        channel.send("1-hello", "Hello operator.\n")

        sent_files = list((self.root / "inbox" / "clearpath").glob("*.md"))
        self.assertEqual(len(sent_files), 1)

    def test_send_renames_legacy_reply_directory_when_target_missing(self) -> None:
        legacy_dir = self.root / "inbox" / "garden"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "legacy.md").write_text("Legacy reply.\n", encoding="utf-8")

        result = set_garden_name("clearpath", garden_root=self.root)
        self.assertTrue(result.ok)

        channel = FilesystemChannel(self.root)
        channel.send("1-hello", "New reply.\n")

        target_dir = self.root / "inbox" / "clearpath"
        self.assertFalse(legacy_dir.exists())
        self.assertTrue((target_dir / "legacy.md").exists())
        self.assertGreaterEqual(len(list(target_dir.glob("*.md"))), 2)

    def test_send_preserves_legacy_reply_directory_when_target_exists(self) -> None:
        legacy_dir = self.root / "inbox" / "garden"
        target_dir = self.root / "inbox" / "clearpath"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "legacy.md").write_text("Legacy reply.\n", encoding="utf-8")
        (target_dir / "existing.md").write_text("Existing reply.\n", encoding="utf-8")

        result = set_garden_name("clearpath", garden_root=self.root)
        self.assertTrue(result.ok)

        channel = FilesystemChannel(self.root)
        channel.send("1-hello", "New reply.\n")

        self.assertTrue((legacy_dir / "legacy.md").exists())
        self.assertTrue((target_dir / "existing.md").exists())
        self.assertGreaterEqual(len(list(target_dir.glob("*.md"))), 2)


if __name__ == "__main__":
    unittest.main()
