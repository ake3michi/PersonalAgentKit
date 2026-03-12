#!/usr/bin/env python3
import importlib.util
import json
import shutil
import tempfile
import threading
import time
import types
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(repo_root: Path, rel_path: str, name: str):
    import sys

    sys.path.insert(0, str(repo_root))
    try:
        spec = importlib.util.spec_from_file_location(name, repo_root / rel_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def scaffold_repo(tmp_root: Path):
    for rel in ["goals", "plants/worker/runs", "scripts", "runner"]:
        (tmp_root / rel).mkdir(parents=True, exist_ok=True)

    shutil.copy2(REPO_ROOT / "scripts" / "dispatch.py", tmp_root / "scripts" / "dispatch.py")
    shutil.copytree(REPO_ROOT / "runner", tmp_root / "runner", dirs_exist_ok=True)


def write_goal(repo_root: Path, nnn: str, slug: str):
    write_file(
        repo_root / "goals" / f"{nnn}-{slug}.md",
        f"""---
assigned_to: worker
priority: 1
---
# {slug}
""",
    )


def write_running_meta(repo_root: Path, run_id: str):
    run_dir = repo_root / "plants" / "worker" / "runs" / run_id
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    write_file(
        run_dir / "meta.json",
        json.dumps(
            {
                "run_id": run_id,
                "goal_file": f"goals/{run_id}.md",
                "started_at": started_at,
                "completed_at": None,
                "status": "running",
                "driver": "codex",
                "model": "gpt-5.4",
                "agent": "gpt-5.4",
                "cost": None,
                "outputs": [],
                "notes": None,
            },
            indent=2,
        )
        + "\n",
    )


def assert_true(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def verify_existing_running_run_blocks_queue():
    with tempfile.TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        scaffold_repo(repo_root)
        write_running_meta(repo_root, "001-first")
        write_goal(repo_root, "002", "second")

        dispatch = load_module(repo_root, "scripts/dispatch.py", "dispatch_plant_serialization_meta")
        dispatcher = dispatch.Dispatcher(
            repo_root=repo_root,
            max_workers=2,
            tend_interval=300,
            max_cost=None,
        )

        entries, blocked, _ = dispatcher._scan_queue()
        assert_true(entries == [], "queued goal should not be runnable while the plant already has a running meta")
        assert_true(blocked == 1, "running plant should count as a blocked queued goal")


def verify_same_scan_only_launches_one_goal_per_plant():
    with tempfile.TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        scaffold_repo(repo_root)
        write_goal(repo_root, "001", "first")
        write_goal(repo_root, "002", "second")

        dispatch = load_module(repo_root, "scripts/dispatch.py", "dispatch_plant_serialization_fill_slots")
        dispatcher = dispatch.Dispatcher(
            repo_root=repo_root,
            max_workers=2,
            tend_interval=300,
            max_cost=None,
        )

        release = threading.Event()
        launches: list[str] = []

        def fake_worker(self, entry):
            launches.append(entry.goal_rel)
            release.wait(timeout=5)
            with self.lock:
                self.active_slots -= 1
                self.in_progress.discard(entry.goal_rel)
                self._mark_plant_inactive_locked(entry.assigned_to, Path(entry.goal_rel).stem)
                self._recent_completions += 1
            self.slot_freed.set()

        dispatcher._worker = types.MethodType(fake_worker, dispatcher)

        entries, blocked, _ = dispatcher._scan_queue()
        assert_true(len(entries) == 2, "both goals should be initially runnable before any launch")
        assert_true(blocked == 0, "nothing should be blocked before the first goal starts")

        launched = dispatcher._fill_slots(entries)
        assert_true(launched == 1, "dispatcher should only launch one goal for a plant in a single fill pass")

        deadline = time.time() + 2
        while len(launches) < 1 and time.time() < deadline:
            time.sleep(0.01)
        assert_true(launches == ["goals/001-first.md"], "first goal should claim the plant slot")

        entries_after, blocked_after, _ = dispatcher._scan_queue()
        assert_true(entries_after == [], "second goal should stay out of the runnable queue while the first is active")
        assert_true(blocked_after == 1, "second goal should remain queued as blocked until the first finishes")

        release.set()
        deadline = time.time() + 2
        while dispatcher.active_slots != 0 and time.time() < deadline:
            time.sleep(0.01)


def main():
    verify_existing_running_run_blocks_queue()
    verify_same_scan_only_launches_one_goal_per_plant()
    print("verify_plant_run_serialization: ok")


if __name__ == "__main__":
    main()
