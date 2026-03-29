import json
import pathlib
import subprocess
import tempfile
import unittest

from system.runs import close_run, open_run, read_run, update_run_lifecycle


class RunLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _init_git_repo(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)

    def _commit_file(self, relative_path: str, content: str) -> pathlib.Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", relative_path], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", f"add {relative_path}"], cwd=self.root, check=True)
        return path

    def test_update_run_lifecycle_tracks_transient_phase_until_close(self) -> None:
        result, run_id = open_run(
            "1-demo-goal",
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T15:00:00Z",
        )
        self.assertTrue(result.ok)
        assert run_id is not None

        result = update_run_lifecycle(
            run_id,
            phase="writing-reflection",
            _runs_dir=self.root / "runs",
            _now="2026-03-20T15:00:05Z",
        )
        self.assertTrue(result.ok)

        run = read_run(run_id, _runs_dir=self.root / "runs")
        self.assertEqual(
            run["lifecycle"],
            {
                "phase": "writing-reflection",
                "updated_at": "2026-03-20T15:00:05Z",
            },
        )

        result = close_run(
            run_id,
            "success",
            "spike",
            cost={"source": "unknown"},
            _runs_dir=self.root / "runs",
            _now="2026-03-20T15:00:10Z",
        )
        self.assertTrue(result.ok)

        run = read_run(run_id, _runs_dir=self.root / "runs")
        self.assertNotIn("lifecycle", run)

        events_path = self.root / "events" / "coordinator.jsonl"
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            [event["type"] for event in events],
            ["RunStarted", "RunFinished"],
        )

    def test_open_run_records_preexisting_worktree_baseline_for_non_converse_goal(self) -> None:
        self._init_git_repo()
        dirty_file = self._commit_file("system/cli.py", "print('before')\n")
        dirty_file.write_text("print('after')\n", encoding="utf-8")

        runtime_file = self.root / ".runtime" / "goals" / "demo.json"
        runtime_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_file.write_text("{}\n", encoding="utf-8")

        extra_untracked = self.root / "dashboard" / "note.txt"
        extra_untracked.parent.mkdir(parents=True, exist_ok=True)
        extra_untracked.write_text("note\n", encoding="utf-8")

        result, run_id = open_run(
            "1-demo-goal",
            "gardener",
            "codex",
            "gpt-5.4",
            goal_type="fix",
            _runs_dir=self.root / "runs",
            _now="2026-03-26T05:55:00Z",
        )
        self.assertTrue(result.ok)
        assert run_id is not None

        run = read_run(run_id, _runs_dir=self.root / "runs")
        self.assertEqual(
            run["worktree_baseline"],
            {
                "captured_at": "2026-03-26T05:55:00Z",
                "tracked_dirty_paths": ["system/cli.py"],
                "untracked_dirty_count": 2,
                "untracked_dirty_roots": [".runtime", "dashboard"],
            },
        )

    def test_open_run_skips_worktree_baseline_for_converse_goal(self) -> None:
        self._init_git_repo()
        dirty_file = self._commit_file("system/cli.py", "print('before')\n")
        dirty_file.write_text("print('after')\n", encoding="utf-8")

        result, run_id = open_run(
            "1-demo-goal",
            "gardener",
            "codex",
            "gpt-5.4",
            goal_type="converse",
            _runs_dir=self.root / "runs",
            _now="2026-03-26T05:56:00Z",
        )
        self.assertTrue(result.ok)
        assert run_id is not None

        run = read_run(run_id, _runs_dir=self.root / "runs")
        self.assertNotIn("worktree_baseline", run)


if __name__ == "__main__":
    unittest.main()
