#!/usr/bin/env python3
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def write_file(path: Path, content: str, executable: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def scaffold_repo(tmp_root: Path):
    for rel in [
        "goals",
        "runs",
        "memory",
        "scripts",
        "schema",
        "runner",
        "plugins",
        "tmp",
        "plants/worker/runs",
        "plants/worker/memory",
    ]:
        (tmp_root / rel).mkdir(parents=True, exist_ok=True)

    write_file(tmp_root / "MOTIVATION.md", "# Motivation\n")
    write_file(tmp_root / "memory" / "MEMORY.md", "# Root Memory\n")
    write_file(tmp_root / "plants" / "worker" / "MOTIVATION.md", "# Worker Motivation\n")
    write_file(tmp_root / "plants" / "worker" / "memory" / "MEMORY.md", "# Worker Memory\n")

    shutil.copy2(REPO_ROOT / "scripts" / "personalagentkit", tmp_root / "scripts" / "personalagentkit")
    shutil.copy2(REPO_ROOT / "schema" / "run.schema.json", tmp_root / "schema" / "run.schema.json")
    shutil.copytree(REPO_ROOT / "runner", tmp_root / "runner", dirs_exist_ok=True)

    script_path = tmp_root / "scripts" / "personalagentkit"
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

    subprocess.run(["git", "init"], cwd=tmp_root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Reader Contract Test"], cwd=tmp_root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "reader-contract@example.com"], cwd=tmp_root, check=True, capture_output=True, text=True)


def create_fake_driver(plugin_dir: Path, driver_bin: Path):
    plugin = """from __future__ import annotations

from typing import Any, Mapping

from runner.plugin_api import DriverConfig


class FakeDriver:
    config = DriverConfig(name="fake", binary="python3", default_model="fake-model")

    def build_command(self, *, model: str) -> list[str]:
        return [r\"__BIN__\", model]

    def prepare_env(self, env: Mapping[str, str]) -> dict[str, str]:
        return dict(env)

    def parse_events(self, *, events: list[dict[str, Any]], model: str) -> dict[str, Any]:
        return {
            "output": "reader contract complete",
            "cost": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "actual_usd": 0.01,
                "estimated_usd": None,
                "pricing": {
                    "source": "provider-native",
                    "provider": "fake",
                    "model": model,
                    "version": "fake-v1",
                    "retrieved_at": None,
                    "notes": None,
                },
            },
            "num_turns": 1,
            "duration_ms": 1000,
        }


PLUGIN = FakeDriver()
""".replace("__BIN__", str(driver_bin))
    driver = """#!/usr/bin/env python3
import json
import sys

_ = sys.stdin.read()
print(json.dumps({"type": "item.completed", "item": {"id": "msg1", "type": "agent_message", "text": "Working on the reader contract."}}))
print(json.dumps({"type": "item.completed", "item": {"id": "msg2", "type": "agent_message", "text": "Reader contract finished."}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}, "duration_ms": 1000}))
sys.stdout.flush()
"""
    write_file(plugin_dir / "fake_driver.py", plugin)
    write_file(driver_bin, driver, executable=True)


def assert_contains(text: str, needle: str, message: str):
    if needle not in text:
        raise AssertionError(f"{message}: missing {needle!r}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        scaffold_repo(repo_root)
        create_fake_driver(repo_root / "plugins", repo_root / "tmp" / "fake-driver")

        source_goal = repo_root / "tmp" / "reader-contract.md"
        write_file(
            source_goal,
            """---
assigned_to: worker
---
# Build the reader contract

Write a short summary to stdout.
""",
        )

        env = {
            **os.environ,
            "PAK_DRIVER_PLUGIN_PATH": str(repo_root / "plugins"),
            "PAK_DRIVER": "fake",
            "PAK_MODEL": "fake-model",
        }

        subprocess.run(
            ["./scripts/personalagentkit", "submit", str(source_goal)],
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        goal_file = repo_root / "goals" / "001-reader-contract.md"
        pointer_file = repo_root / "goals" / "001-reader-contract.run"
        pointer_text = pointer_file.read_text(encoding="utf-8")
        assert_contains(pointer_text, "Status: `queued`", "submit should create a queued run pointer")
        assert_contains(
            pointer_text,
            "Run directory: `plants/worker/runs/001-reader-contract`",
            "run pointer should disclose the plant run path",
        )
        assert_contains(
            pointer_text,
            "Open first when the run exists: `plants/worker/runs/001-reader-contract/README.md`",
            "run pointer should disclose the reader entry file",
        )

        subprocess.run(
            ["./scripts/personalagentkit", "run", "goals/001-reader-contract.md"],
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        run_dir = repo_root / "plants" / "worker" / "runs" / "001-reader-contract"
        readme_text = (run_dir / "README.md").read_text(encoding="utf-8")
        pointer_text = pointer_file.read_text(encoding="utf-8")

        assert_contains(pointer_text, "Status: `success`", "run pointer should update once the run completes")
        assert_contains(readme_text, "Status: `success`", "run README should show the terminal status")
        assert_contains(readme_text, "Goal file: `goals/001-reader-contract.md`", "run README should link back to the goal")
        assert_contains(readme_text, "`_stdout.md`", "run README should point readers to the summary artifact")
        assert_contains(readme_text, "`meta.json`", "run README should preserve the evidence path")

        assert goal_file.exists()
        assert pointer_file.exists()
        assert (run_dir / "meta.json").exists()

    print("verify_reader_run_contract: ok")


if __name__ == "__main__":
    main()
