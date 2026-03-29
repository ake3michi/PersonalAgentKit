import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from system import runtime_history
from system.garden import garden_paths
from system.runs import close_run, open_run


def _write_runtime_config(root: pathlib.Path) -> None:
    (root / "PAK2.toml").write_text(
        "[runtime]\nroot = \".runtime\"\n",
        encoding="utf-8",
    )


def _init_git_repo(root: pathlib.Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()


def _git_completed_process(
    args: list[str],
    returncode: int,
    *,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git", *args],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class RuntimeHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        _write_runtime_config(self.root)
        (self.root / "README.md").write_text("seed\n", encoding="utf-8")
        self.head = _init_git_repo(self.root)
        self.paths = garden_paths(garden_root=self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _capture_runtime_history(self, **overrides):
        kwargs = {
            "run_id": "1-demo-goal-r1",
            "goal_id": "1-demo-goal",
            "run_status": "success",
            "completed_at": "2026-03-25T07:00:05Z",
            "garden_root": self.root,
        }
        kwargs.update(overrides)
        return runtime_history.capture_runtime_history_for_run(**kwargs)

    def test_close_run_creates_runtime_history_commit_with_authored_provenance(self) -> None:
        result, run_id = open_run(
            "1-demo-goal",
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.paths.runs_dir,
            _now="2026-03-25T07:00:00Z",
        )
        self.assertTrue(result.ok)
        assert run_id is not None

        result = close_run(
            run_id,
            "success",
            "spike",
            cost={"source": "unknown"},
            _runs_dir=self.paths.runs_dir,
            _now="2026-03-25T07:00:05Z",
        )
        self.assertTrue(result.ok)

        runtime_git_dir = self.paths.runtime_root / ".git"
        self.assertTrue(runtime_git_dir.exists())

        record_path = self.paths.runtime_root / "history" / "commits" / f"{run_id}.json"
        self.assertTrue(record_path.exists())
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["run_id"], run_id)
        self.assertEqual(record["goal_id"], "1-demo-goal")
        self.assertEqual(record["run_status"], "success")
        self.assertEqual(record["authored_commit"], self.head)
        self.assertEqual(record["authored_tree_state"], "clean")

        message = subprocess.check_output(
            ["git", "log", "-1", "--format=%B"],
            cwd=self.paths.runtime_root,
            text=True,
        )
        self.assertIn(f"Authored-Commit: {self.head}", message)
        self.assertIn("Authored-Tree: clean", message)
        self.assertIn(f"Source-Run: {run_id}", message)
        self.assertIn(f"Record-Path: history/commits/{run_id}.json", message)

    def test_close_run_marks_authored_tree_dirty_only_for_non_runtime_changes(self) -> None:
        (self.root / "README.md").write_text("seed\ndirty\n", encoding="utf-8")

        result, run_id = open_run(
            "1-demo-goal",
            "gardener",
            "codex",
            "gpt-5.4",
            _runs_dir=self.paths.runs_dir,
            _now="2026-03-25T07:10:00Z",
        )
        self.assertTrue(result.ok)
        assert run_id is not None

        result = close_run(
            run_id,
            "success",
            "spike",
            cost={"source": "unknown"},
            _runs_dir=self.paths.runs_dir,
            _now="2026-03-25T07:10:05Z",
        )
        self.assertTrue(result.ok)

        record_path = self.paths.runtime_root / "history" / "commits" / f"{run_id}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["authored_commit"], self.head)
        self.assertEqual(record["authored_tree_state"], "dirty")

        message = subprocess.check_output(
            ["git", "log", "-1", "--format=%B"],
            cwd=self.paths.runtime_root,
            text=True,
        )
        self.assertIn(f"Authored-Commit: {self.head}", message)
        self.assertIn("Authored-Tree: dirty", message)

    def test_capture_runtime_history_returns_runtime_root_not_split_for_legacy_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir)
            (root / "README.md").write_text("seed\n", encoding="utf-8")
            _init_git_repo(root)

            result = runtime_history.capture_runtime_history_for_run(
                run_id="1-demo-goal-r1",
                goal_id="1-demo-goal",
                run_status="success",
                completed_at="2026-03-25T07:00:05Z",
                garden_root=root,
            )

        self.assertFalse(result.attempted)
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "runtime_root_not_split")
        self.assertIsNone(result.record_path)

    def test_capture_runtime_history_returns_runtime_root_outside_garden_when_unrelatable(self) -> None:
        with patch("system.runtime_history._runtime_root_relative_to_garden", return_value=None):
            result = self._capture_runtime_history()

        self.assertFalse(result.attempted)
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "runtime_root_outside_garden")
        self.assertIsNone(result.record_path)

    def test_capture_runtime_history_returns_authored_repo_unavailable_without_authored_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir)
            _write_runtime_config(root)
            (root / "README.md").write_text("seed\n", encoding="utf-8")

            result = runtime_history.capture_runtime_history_for_run(
                run_id="1-demo-goal-r1",
                goal_id="1-demo-goal",
                run_status="success",
                completed_at="2026-03-25T07:00:05Z",
                garden_root=root,
            )

        self.assertFalse(result.attempted)
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "authored_repo_unavailable")
        self.assertIsNotNone(result.detail)
        self.assertIsNone(result.record_path)

    def test_capture_runtime_history_reports_record_write_failed(self) -> None:
        with patch("system.runtime_history._write_record", return_value="disk full"):
            result = self._capture_runtime_history()

        self.assertTrue(result.attempted)
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "record_write_failed")
        self.assertEqual(result.detail, "disk full")
        self.assertEqual(result.authored_commit, self.head)
        self.assertEqual(result.authored_tree_state, "clean")
        self.assertEqual(
            result.record_path,
            self.paths.runtime_root / "history" / "commits" / "1-demo-goal-r1.json",
        )
        self.assertFalse(result.record_path.exists())

    def test_capture_runtime_history_reports_runtime_repo_init_failed(self) -> None:
        with patch("system.runtime_history._ensure_runtime_repo", return_value="git init failed"):
            result = self._capture_runtime_history()

        self.assertTrue(result.attempted)
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "runtime_repo_init_failed")
        self.assertEqual(result.detail, "git init failed")
        self.assertEqual(result.authored_commit, self.head)
        self.assertEqual(result.authored_tree_state, "clean")
        assert result.record_path is not None
        self.assertTrue(result.record_path.exists())

    def test_capture_runtime_history_reports_runtime_repo_stage_failed(self) -> None:
        original_run_git = runtime_history._run_git

        def fake_run_git(args: list[str], *, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
            if args == ["add", "-A", "--", "."]:
                return _git_completed_process(args, 1, stderr="stage failed")
            return original_run_git(args, cwd=cwd)

        with patch("system.runtime_history._run_git", side_effect=fake_run_git):
            result = self._capture_runtime_history()

        self.assertTrue(result.attempted)
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "runtime_repo_stage_failed")
        self.assertEqual(result.detail, "stage failed")
        self.assertEqual(result.authored_commit, self.head)
        self.assertEqual(result.authored_tree_state, "clean")
        assert result.record_path is not None
        self.assertTrue(result.record_path.exists())

    def test_capture_runtime_history_returns_no_runtime_changes_on_repeat_capture(self) -> None:
        first = self._capture_runtime_history()
        self.assertTrue(first.committed)
        self.assertEqual(first.reason, "committed")

        before = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=self.paths.runtime_root,
            text=True,
        ).strip()

        second = self._capture_runtime_history()

        after = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=self.paths.runtime_root,
            text=True,
        ).strip()

        self.assertTrue(second.attempted)
        self.assertFalse(second.committed)
        self.assertEqual(second.reason, "no_runtime_changes")
        self.assertEqual(second.authored_commit, self.head)
        self.assertEqual(second.authored_tree_state, "clean")
        self.assertEqual(after, before)

    def test_capture_runtime_history_reports_runtime_repo_diff_failed(self) -> None:
        original_run_git = runtime_history._run_git

        def fake_run_git(args: list[str], *, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
            if args == ["diff", "--cached", "--quiet", "--exit-code"]:
                return _git_completed_process(args, 2, stderr="diff failed")
            return original_run_git(args, cwd=cwd)

        with patch("system.runtime_history._run_git", side_effect=fake_run_git):
            result = self._capture_runtime_history()

        self.assertTrue(result.attempted)
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "runtime_repo_diff_failed")
        self.assertEqual(result.detail, "diff failed")
        self.assertEqual(result.authored_commit, self.head)
        self.assertEqual(result.authored_tree_state, "clean")

    def test_capture_runtime_history_reports_runtime_repo_commit_failed(self) -> None:
        original_run_git = runtime_history._run_git

        def fake_run_git(args: list[str], *, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
            if args[:3] == ["commit", "-q", "-m"]:
                return _git_completed_process(args, 1, stderr="commit failed")
            return original_run_git(args, cwd=cwd)

        with patch("system.runtime_history._run_git", side_effect=fake_run_git):
            result = self._capture_runtime_history()

        self.assertTrue(result.attempted)
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "runtime_repo_commit_failed")
        self.assertEqual(result.detail, "commit failed")
        self.assertEqual(result.authored_commit, self.head)
        self.assertEqual(result.authored_tree_state, "clean")

    def test_capture_runtime_history_reports_runtime_repo_head_unavailable(self) -> None:
        original_git_stdout = runtime_history._git_stdout

        def fake_git_stdout(args: list[str], *, cwd: pathlib.Path):
            if args == ["rev-parse", "HEAD"] and pathlib.Path(cwd) == self.paths.runtime_root:
                return None, "head unavailable"
            return original_git_stdout(args, cwd=cwd)

        with patch("system.runtime_history._git_stdout", side_effect=fake_git_stdout):
            result = self._capture_runtime_history()

        self.assertTrue(result.attempted)
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "runtime_repo_head_unavailable")
        self.assertEqual(result.detail, "head unavailable")
        self.assertEqual(result.authored_commit, self.head)
        self.assertEqual(result.authored_tree_state, "clean")


if __name__ == "__main__":
    unittest.main()
