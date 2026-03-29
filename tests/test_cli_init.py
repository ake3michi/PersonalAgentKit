import contextlib
import io
import pathlib
import re
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from system import cli
from system.garden import garden_paths
from system.genesis import genesis
from system.goals import list_goals

_DOC_REL_LINK_RE = re.compile(r"\[[^\]]+\]\((?!https?://|mailto:|#)([^)]+)\)")
_EXPECTED_SCHEMA_FILES = (
    "active-threads.schema.json",
    "conversation-checkpoint.schema.json",
    "conversation-turn.schema.json",
    "conversation.schema.json",
    "dashboard-invocation.schema.json",
    "dashboard-render.schema.json",
    "dashboard-snapshot.schema.json",
    "dispatch-packet.schema.json",
    "event.schema.json",
    "goal-supplement.schema.json",
    "goal-retrospective.schema.json",
    "goal-plant-commission.schema.json",
    "goal.schema.json",
    "goal-tend.schema.json",
    "initiative.schema.json",
    "message.schema.json",
    "operator-message.schema.json",
    "plant.schema.json",
    "run.schema.json",
)
_SEEDED_GARDENER_PARITY_FILES = (
    pathlib.Path("skills/tend-pass.md"),
    pathlib.Path("skills/initiative-records.md"),
    pathlib.Path("knowledge/initiative-record-protocol.md"),
    pathlib.Path("skills/recently-concluded-note.md"),
    pathlib.Path("knowledge/recently-concluded-work-note-protocol.md"),
)


def _iter_doc_relative_targets(root: pathlib.Path):
    docs_root = root / "docs"
    for doc_path in sorted(docs_root.glob("*/*.md")):
        doc_text = doc_path.read_text(encoding="utf-8")
        for rel_target in _DOC_REL_LINK_RE.findall(doc_text):
            target = rel_target.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            target = target.split("#", 1)[0]
            if not target:
                continue
            yield doc_path, target


class InitTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _init_args(
        self,
        dest: pathlib.Path,
        *,
        default_driver: str | None = None,
        default_model: str | None = None,
        default_reasoning_effort: str | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            dir=str(dest),
            default_driver=default_driver,
            default_model=default_model,
            default_reasoning_effort=default_reasoning_effort,
        )

    def _run_init(
        self,
        dest: pathlib.Path,
        *,
        default_driver: str | None = None,
        default_model: str | None = None,
        default_reasoning_effort: str | None = None,
    ) -> tuple[io.StringIO, object]:
        stdout = io.StringIO()
        with patch.object(cli.subprocess, "run") as mock_run, contextlib.redirect_stdout(stdout):
            cli.cmd_init(
                self._init_args(
                    dest,
                    default_driver=default_driver,
                    default_model=default_model,
                    default_reasoning_effort=default_reasoning_effort,
                )
            )
        return stdout, mock_run

    def _assert_seeded_gardener_file_matches_template(
        self,
        dest: pathlib.Path,
        relative_path: pathlib.Path,
    ) -> None:
        self.assertEqual(
            (cli._TEMPLATE_ROOT / "seeds" / "gardener" / relative_path).read_text(
                encoding="utf-8"
            ),
            (dest / "seeds" / "gardener" / relative_path).read_text(encoding="utf-8"),
            f"seeded gardener file drifted from shipped template copy: {relative_path}",
        )

    def test_cmd_init_copies_done_contract_and_bootstrap_references(self) -> None:
        dest = self.root / "fresh-garden"
        stdout, mock_run = self._run_init(dest)

        charter_path = dest / "CHARTER.md"
        done_path = dest / "DONE.md"
        readme_path = dest / "README.md"
        license_path = dest / "LICENSE"
        config_example_path = dest / "PAK2.toml.example"
        quickstart_charter_path = dest / "examples" / "charter-quickstart.md"
        self.assertTrue(charter_path.exists())
        self.assertEqual(
            charter_path.read_text(encoding="utf-8"),
            quickstart_charter_path.read_text(encoding="utf-8"),
        )
        self.assertTrue(done_path.exists())
        self.assertTrue(readme_path.exists())
        self.assertTrue(license_path.exists())
        self.assertTrue(config_example_path.exists())
        self.assertIn("# Definition of Done", done_path.read_text(encoding="utf-8"))
        readme_text = readme_path.read_text(encoding="utf-8")
        self.assertIn("## See It Run", readme_text)
        self.assertIn("PAK2 is a seed for an agent you grow locally.", readme_text)
        self.assertIn("./pak2 init my-garden", readme_text)
        self.assertIn("cd my-garden", readme_text)
        self.assertIn("./pak2 genesis", readme_text)
        self.assertIn("./pak2 cycle", readme_text)
        self.assertIn("./pak2 chat", readme_text)
        self.assertIn("./pak2 dashboard", readme_text)
        self.assertIn("## Customize It", readme_text)
        self.assertIn("CHARTER.md.example", readme_text)
        self.assertIn("PAK2.toml.example", readme_text)
        self.assertIn("--default-driver", readme_text)
        self.assertIn("--default-model", readme_text)
        self.assertIn("--default-reasoning-effort", readme_text)
        self.assertNotIn("python3 ./pak2", readme_text)
        self.assertIn("PAK2 is source code you run", readme_text)
        self.assertIn("MIT License", readme_text)
        self.assertNotIn("does not yet provide", readme_text)
        self.assertIn("## Operator, Garden, Plants", readme_text)
        self.assertIn("not a hosted product", readme_text)

        license_text = license_path.read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Permission is hereby granted, free of charge", license_text)

        config_example_text = config_example_path.read_text(encoding="utf-8")
        self.assertIn("[runtime]", config_example_text)
        self.assertIn("[defaults]", config_example_text)
        self.assertIn("[garden]", config_example_text)
        self.assertIn('root = ".runtime"', config_example_text)
        self.assertIn('driver = "codex"', config_example_text)
        self.assertIn('model = "gpt-5.4"', config_example_text)
        self.assertIn('reasoning_effort = "xhigh"', config_example_text)
        self.assertIn('name = "my-garden"', config_example_text)

        seed_text = (dest / "seeds" / "gardener.md").read_text(encoding="utf-8")
        self.assertIn("Then read `DONE.md`", seed_text)
        self.assertIn("Do not treat", seed_text)
        self.assertIn("active-threads.json", seed_text)
        self.assertIn("tend-pass.md", seed_text)
        self.assertIn("initiative-records.md", seed_text)
        self.assertIn("recently-concluded-note.md", seed_text)
        self.assertIn("retrospective", seed_text)
        self.assertTrue((dest / "seeds" / "gardener" / "skills" / "active-threads.md").exists())
        self.assertTrue((dest / "seeds" / "gardener" / "skills" / "tend-pass.md").exists())
        self.assertTrue(
            (dest / "seeds" / "gardener" / "knowledge" / "active-thread-protocol.md").exists()
        )
        tend_pass_skill = dest / "seeds" / "gardener" / "skills" / "tend-pass.md"
        initiative_skill = dest / "seeds" / "gardener" / "skills" / "initiative-records.md"
        initiative_knowledge = (
            dest / "seeds" / "gardener" / "knowledge" / "initiative-record-protocol.md"
        )
        recently_concluded_skill = (
            dest / "seeds" / "gardener" / "skills" / "recently-concluded-note.md"
        )
        recently_concluded_knowledge = (
            dest
            / "seeds"
            / "gardener"
            / "knowledge"
            / "recently-concluded-work-note-protocol.md"
        )
        retrospective_skill = dest / "seeds" / "gardener" / "skills" / "retrospective-pass.md"
        retrospective_knowledge = (
            dest / "seeds" / "gardener" / "knowledge" / "retrospective-protocol-choice.md"
        )
        self.assertTrue(initiative_skill.exists())
        self.assertTrue(initiative_knowledge.exists())
        self.assertTrue(recently_concluded_skill.exists())
        self.assertTrue(recently_concluded_knowledge.exists())
        self.assertTrue(retrospective_skill.exists())
        self.assertTrue(retrospective_knowledge.exists())
        for relative_path in _SEEDED_GARDENER_PARITY_FILES:
            self._assert_seeded_gardener_file_matches_template(dest, relative_path)
        self.assertNotIn(
            "Claude via the claude CLI",
            charter_path.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "Use this garden's configured runtime defaults.",
            quickstart_charter_path.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "--default-driver",
            quickstart_charter_path.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "--default-model",
            quickstart_charter_path.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "--default-reasoning-effort",
            quickstart_charter_path.read_text(encoding="utf-8"),
        )
        self.assertFalse((dest / "chat").exists())
        self.assertNotIn(
            "rg is unavailable in this workspace",
            initiative_skill.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "system.operator_messages.emit_tend_survey()",
            tend_pass_skill.read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "2026-03-24",
            initiative_knowledge.read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "sprout",
            initiative_knowledge.read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "bounded-campaign-mode.md",
            recently_concluded_skill.read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "2026-03-21",
            recently_concluded_knowledge.read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "bounded-campaign-mode.md",
            recently_concluded_knowledge.read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "rg is unavailable in this workspace",
            retrospective_skill.read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "2026-03-21",
            retrospective_knowledge.read_text(encoding="utf-8"),
        )

        reviewer_seed = (dest / "seeds" / "reviewer.md").read_text(encoding="utf-8")
        self.assertIn("# Reviewer Seed", reviewer_seed)
        self.assertIn("plants/<your-name>/skills/review-pass.md", reviewer_seed)
        self.assertTrue((dest / "seeds" / "reviewer" / "skills" / "review-pass.md").exists())
        self.assertTrue(
            (dest / "seeds" / "reviewer" / "knowledge" / "review-scope.md").exists()
        )

        garden_text = (dest / "GARDEN.md").read_text(encoding="utf-8")
        self.assertIn("Read `DONE.md` alongside this document.", garden_text)
        self.assertIn("completion contract", garden_text)

        tend_schema = dest / "schema" / "goal-tend.schema.json"
        self.assertTrue(tend_schema.exists())
        self.assertIn("\"title\": \"Tend Goal Payload\"", tend_schema.read_text(encoding="utf-8"))

        config_text = (dest / "PAK2.toml").read_text(encoding="utf-8")
        self.assertIn("[runtime]", config_text)
        self.assertIn('root = ".runtime"', config_text)
        self.assertNotIn("[defaults]", config_text)

        output = stdout.getvalue()
        self.assertIn(f"Bootstrap charter: {charter_path}", output)
        self.assertIn(f"Completion contract: {done_path}", output)
        self.assertIn(f"Next: review {charter_path} and {done_path}, then run:", output)
        self.assertIn(f"  cd {dest}", output)
        self.assertIn("  ./pak2 genesis", output)
        self.assertIn("  ./pak2 cycle", output)
        self.assertNotIn("pak2 genesis --root", output)
        self.assertNotIn("pak2 cycle --root", output)

        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(mock_run.call_args_list[0].args[0], ["git", "init", str(dest)])

    def test_cmd_init_writes_requested_runtime_overrides_to_config(self) -> None:
        dest = self.root / "override-garden"
        self._run_init(
            dest,
            default_driver="claude",
            default_model="claude-opus-4-6",
            default_reasoning_effort="high",
        )

        config_text = (dest / "PAK2.toml").read_text(encoding="utf-8")
        self.assertIn("[defaults]", config_text)
        self.assertIn('driver = "claude"', config_text)
        self.assertIn('model = "claude-opus-4-6"', config_text)
        self.assertIn('reasoning_effort = "high"', config_text)

    def test_cmd_init_bootstraps_charter_that_can_go_straight_into_genesis(self) -> None:
        dest = self.root / "bootstrap-ready-garden"
        self._run_init(dest)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            genesis(dest)

        paths = garden_paths(garden_root=dest)
        goals = list_goals(_goals_dir=paths.goals_dir)

        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["status"], "queued")
        self.assertIn("genesis: initialization complete", stdout.getvalue())

    def test_cmd_init_gitignore_ignores_default_runtime_root(self) -> None:
        dest = self.root / "runtime-garden"
        self._run_init(dest)

        runtime_file = dest / ".runtime" / "goals" / "demo.json"
        runtime_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_file.write_text("{}\n", encoding="utf-8")

        subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
        ignored = subprocess.run(
            ["git", "check-ignore", "-v", ".runtime/goals/demo.json"],
            cwd=dest,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(ignored.returncode, 0, ignored.stderr or ignored.stdout)
        self.assertIn(".gitignore", ignored.stdout)
        self.assertIn(".runtime/", ignored.stdout)

    def test_cmd_init_preserves_schema_template_parity_and_doc_targets(self) -> None:
        dest = self.root / "schema-garden"
        self._run_init(dest)

        template_schema = cli._TEMPLATE_ROOT / "schema"
        dest_schema = dest / "schema"

        self.assertEqual(
            sorted(path.name for path in template_schema.glob("*.schema.json")),
            sorted(path.name for path in dest_schema.glob("*.schema.json")),
        )
        self.assertEqual(
            sorted(path.name for path in dest_schema.glob("*.schema.json")),
            sorted(_EXPECTED_SCHEMA_FILES),
        )

        for name in _EXPECTED_SCHEMA_FILES:
            self.assertEqual(
                (template_schema / name).read_text(encoding="utf-8"),
                (dest_schema / name).read_text(encoding="utf-8"),
            )

        template_tests = cli._TEMPLATE_ROOT / "tests"
        dest_tests = dest / "tests"
        self.assertTrue(dest_tests.exists())
        self.assertEqual(
            sorted(path.name for path in template_tests.glob("test_*.py")),
            sorted(path.name for path in dest_tests.glob("test_*.py")),
        )

        for root in (cli._TEMPLATE_ROOT, dest):
            for doc_path, rel_target in _iter_doc_relative_targets(root):
                self.assertTrue(
                    (doc_path.parent / rel_target).resolve().exists(),
                    f"missing doc target for {doc_path}: {rel_target}",
                )


if __name__ == "__main__":
    unittest.main()
