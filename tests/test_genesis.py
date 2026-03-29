import contextlib
import io
import pathlib
import subprocess
import tempfile
import unittest

from system.genesis import genesis
from system.garden import garden_paths
from system.goals import list_goals
from system.plants import commission_plant, materialize_seed_context


_GARDENER_SEED_ASSETS = {
    pathlib.Path("skills/active-threads.md"): "# Active Threads\n",
    pathlib.Path("skills/tend-pass.md"): "# Tend Pass\n",
    pathlib.Path("skills/initiative-records.md"): "# Initiative Records\n",
    pathlib.Path("skills/recently-concluded-note.md"): "# Recently Concluded Note\n",
    pathlib.Path("skills/retrospective-pass.md"): "# Retrospective Pass\n",
    pathlib.Path("knowledge/active-thread-protocol.md"): "# Active Thread Protocol\n",
    pathlib.Path("knowledge/initiative-record-protocol.md"): "# Initiative Record Protocol\n",
    pathlib.Path("knowledge/recently-concluded-work-note-protocol.md"): (
        "# Recently Concluded Work Note Protocol\n"
    ),
    pathlib.Path("knowledge/retrospective-protocol-choice.md"): (
        "# Retrospective Protocol Choice\n"
    ),
}
_MATERIALIZED_GARDENER_FILES = (
    pathlib.Path("skills/tend-pass.md"),
    pathlib.Path("skills/initiative-records.md"),
    pathlib.Path("knowledge/initiative-record-protocol.md"),
    pathlib.Path("skills/recently-concluded-note.md"),
    pathlib.Path("knowledge/recently-concluded-work-note-protocol.md"),
)


def _write_basic_gardener_seed(root: pathlib.Path) -> None:
    (root / "seeds").mkdir(parents=True, exist_ok=True)
    (root / "seeds" / "gardener.md").write_text(
        "# Gardener seed\n",
        encoding="utf-8",
    )
    for relative_path, content in _GARDENER_SEED_ASSETS.items():
        path = root / "seeds" / "gardener" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _assert_materialized_files_match_seed(
    test_case: unittest.TestCase,
    root: pathlib.Path,
    *,
    plant_name: str = "gardener",
    seed_name: str = "gardener",
) -> None:
    for relative_path in _MATERIALIZED_GARDENER_FILES:
        test_case.assertEqual(
            (root / "plants" / plant_name / relative_path).read_text(encoding="utf-8"),
            (root / "seeds" / seed_name / relative_path).read_text(encoding="utf-8"),
            f"materialized file drifted from seeded source: {relative_path}",
        )


class SeedAssetMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        _write_basic_gardener_seed(self.root)
        (self.root / "plants").mkdir()
        commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-23T03:00:00Z",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_materialize_seed_context_writes_seed_reference_and_copies_assets(self) -> None:
        result = materialize_seed_context(
            self.root,
            plant_name="gardener",
            seed_name="gardener",
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            (self.root / "plants" / "gardener" / "seed").read_text(encoding="utf-8"),
            "gardener\n",
        )
        self.assertTrue(
            (self.root / "plants" / "gardener" / "skills" / "active-threads.md").exists()
        )
        self.assertTrue(
            (self.root / "plants" / "gardener" / "skills" / "tend-pass.md").exists()
        )
        self.assertTrue(
            (
                self.root
                / "plants"
                / "gardener"
                / "knowledge"
                / "active-thread-protocol.md"
            ).exists()
        )
        self.assertTrue(
            (
                self.root
                / "plants"
                / "gardener"
                / "skills"
                / "initiative-records.md"
            ).exists()
        )
        self.assertTrue(
            (
                self.root
                / "plants"
                / "gardener"
                / "skills"
                / "recently-concluded-note.md"
            ).exists()
        )
        self.assertTrue(
            (
                self.root
                / "plants"
                / "gardener"
                / "skills"
                / "retrospective-pass.md"
            ).exists()
        )
        self.assertTrue(
            (
                self.root
                / "plants"
                / "gardener"
                / "knowledge"
                / "initiative-record-protocol.md"
            ).exists()
        )
        self.assertTrue(
            (
                self.root
                / "plants"
                / "gardener"
                / "knowledge"
                / "recently-concluded-work-note-protocol.md"
            ).exists()
        )
        self.assertTrue(
            (
                self.root
                / "plants"
                / "gardener"
                / "knowledge"
                / "retrospective-protocol-choice.md"
            ).exists()
        )
        _assert_materialized_files_match_seed(self, self.root)


class GenesisQueueOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "CHARTER.md").write_text("# Charter\n", encoding="utf-8")
        _write_basic_gardener_seed(self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_genesis_queues_first_goal_without_dispatching(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            genesis(self.root)

        paths = garden_paths(garden_root=self.root)
        goals = list_goals(_goals_dir=paths.goals_dir)

        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["status"], "queued")
        self.assertEqual(goals[0]["assigned_to"], "gardener")
        self.assertTrue((paths.plants_dir / "gardener" / "meta.json").exists())
        self.assertTrue((paths.plants_dir / "gardener" / "skills" / "initiative-records.md").exists())
        self.assertTrue((paths.plants_dir / "gardener" / "skills" / "tend-pass.md").exists())
        self.assertTrue(
            (paths.plants_dir / "gardener" / "skills" / "recently-concluded-note.md").exists()
        )
        self.assertTrue((paths.plants_dir / "gardener" / "skills" / "retrospective-pass.md").exists())
        self.assertTrue(
            (
                paths.plants_dir
                / "gardener"
                / "knowledge"
                / "initiative-record-protocol.md"
            ).exists()
        )
        self.assertTrue(
            (
                paths.plants_dir
                / "gardener"
                / "knowledge"
                / "recently-concluded-work-note-protocol.md"
            ).exists()
        )
        self.assertTrue(
            (
                paths.plants_dir
                / "gardener"
                / "knowledge"
                / "retrospective-protocol-choice.md"
            ).exists()
        )
        _assert_materialized_files_match_seed(self, self.root)
        self.assertEqual(list(paths.runs_dir.glob("*/meta.json")), [])
        output = stdout.getvalue()
        self.assertIn("run `./pak2 cycle`", output)
        self.assertNotIn("run `pak2 cycle`", output)

    def test_genesis_is_idempotent_while_first_goal_is_still_queued(self) -> None:
        genesis(self.root)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            genesis(self.root)

        paths = garden_paths(garden_root=self.root)
        goals = list_goals(_goals_dir=paths.goals_dir)

        self.assertEqual(len(goals), 1)
        self.assertIn("initialization already staged", stdout.getvalue())

    def test_genesis_refreshes_seed_context_when_gardener_record_already_exists(self) -> None:
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-23T03:00:00Z",
        )
        self.assertTrue(result.ok)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            genesis(self.root)

        paths = garden_paths(garden_root=self.root)
        goals = list_goals(_goals_dir=paths.goals_dir)

        self.assertEqual(
            (paths.plants_dir / "gardener" / "seed").read_text(encoding="utf-8"),
            "gardener\n",
        )
        self.assertTrue(
            (paths.plants_dir / "gardener" / "skills" / "active-threads.md").exists()
        )
        self.assertTrue(
            (paths.plants_dir / "gardener" / "skills" / "tend-pass.md").exists()
        )
        self.assertTrue(
            (paths.plants_dir / "gardener" / "skills" / "initiative-records.md").exists()
        )
        self.assertTrue(
            (paths.plants_dir / "gardener" / "skills" / "recently-concluded-note.md").exists()
        )
        self.assertTrue(
            (
                paths.plants_dir
                / "gardener"
                / "knowledge"
                / "active-thread-protocol.md"
            ).exists()
        )
        self.assertTrue(
            (paths.plants_dir / "gardener" / "skills" / "retrospective-pass.md").exists()
        )
        self.assertTrue(
            (
                paths.plants_dir
                / "gardener"
                / "knowledge"
                / "initiative-record-protocol.md"
            ).exists()
        )
        self.assertTrue(
            (
                paths.plants_dir
                / "gardener"
                / "knowledge"
                / "recently-concluded-work-note-protocol.md"
            ).exists()
        )
        self.assertTrue(
            (
                paths.plants_dir
                / "gardener"
                / "knowledge"
                / "retrospective-protocol-choice.md"
            ).exists()
        )
        _assert_materialized_files_match_seed(self, self.root)
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["status"], "queued")
        self.assertIn("genesis: gardener plant commissioned", stdout.getvalue())

    def test_genesis_missing_charter_guides_available_bootstrap_sources(self) -> None:
        (self.root / "CHARTER.md").unlink()
        (self.root / "CHARTER.md.example").write_text("# Charter Template\n", encoding="utf-8")
        (self.root / "examples").mkdir(parents=True, exist_ok=True)
        quickstart = self.root / "examples" / "charter-quickstart.md"
        quickstart.write_text("# Charter\n", encoding="utf-8")

        with self.assertRaises(SystemExit) as raised:
            genesis(self.root)

        message = str(raised.exception)
        self.assertIn(f"genesis: CHARTER.md not found at {self.root / 'CHARTER.md'}", message)
        self.assertIn(f"Fastest start: copy {quickstart} to {self.root / 'CHARTER.md'}.", message)
        self.assertIn(
            f"For a custom charter, start from {self.root / 'CHARTER.md.example'}.",
            message,
        )


class GenesisBootstrapCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        _write_basic_gardener_seed(self.root)
        (self.root / "PAK2.toml").write_text(
            "[runtime]\nroot = \".runtime\"\n",
            encoding="utf-8",
        )

        self._git("init", "-q")
        self._git("config", "user.name", "Test User")
        self._git("config", "user.email", "test@example.com")
        self._git("add", "PAK2.toml", "seeds")
        self._git("commit", "-q", "-m", "init")

        (self.root / "CHARTER.md").write_text("# Charter\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def test_genesis_commits_bootstrap_files_before_first_goal_is_queued(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            genesis(self.root)

        latest_commit = self._git("rev-parse", "--short", "HEAD")
        self.assertIn(
            f"genesis: bootstrap files committed ({latest_commit})",
            stdout.getvalue(),
        )
        self.assertEqual(
            self._git("log", "--pretty=%s", "-1"),
            "genesis: track gardener bootstrap files",
        )

        committed_paths = set(self._git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines())
        self.assertEqual(
            committed_paths,
            {
                "CHARTER.md",
                "plants/gardener/knowledge/active-thread-protocol.md",
                "plants/gardener/knowledge/initiative-record-protocol.md",
                "plants/gardener/knowledge/recently-concluded-work-note-protocol.md",
                "plants/gardener/knowledge/retrospective-protocol-choice.md",
                "plants/gardener/meta.json",
                "plants/gardener/seed",
                "plants/gardener/skills/active-threads.md",
                "plants/gardener/skills/initiative-records.md",
                "plants/gardener/skills/recently-concluded-note.md",
                "plants/gardener/skills/retrospective-pass.md",
                "plants/gardener/skills/tend-pass.md",
            },
        )
        self.assertEqual(
            self._git("status", "--short", "--", "CHARTER.md", "plants/gardener"),
            "",
        )

        paths = garden_paths(garden_root=self.root)
        goals = list_goals(_goals_dir=paths.goals_dir)
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["status"], "queued")


if __name__ == "__main__":
    unittest.main()
