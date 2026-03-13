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
from runner.drivers.codex_driver import CodexDriver


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

    def normalize_transcript(self, *, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return CodexDriver().normalize_transcript(events=events)


PLUGIN = FakeDriver()
""".replace("__BIN__", str(driver_bin))
    driver = """#!/usr/bin/env python3
import json
import sys

_ = sys.stdin.read()
print(json.dumps({"type": "item.started", "item": {"id": "todo1", "type": "todo_list", "items": [{"text": "Inspect reader artifacts", "completed": False}, {"text": "Write transcript", "completed": False}]}}))
print(json.dumps({"type": "item.completed", "item": {"id": "msg1", "type": "agent_message", "text": "Working on the reader contract."}}))
print(json.dumps({"type": "item.completed", "item": {"id": "cmd1", "type": "command_execution", "command": "/bin/bash -lc 'printf \\"hello\\\\nworld\\\\n\\"'", "aggregated_output": "hello\\nworld\\n", "exit_code": 0, "status": "completed"}}))
print(json.dumps({"type": "item.completed", "item": {"id": "change1", "type": "file_change", "changes": [{"path": "/tmp/demo.txt", "kind": "update"}], "status": "completed"}}))
print(json.dumps({"type": "item.updated", "item": {"id": "todo1", "type": "todo_list", "items": [{"text": "Inspect reader artifacts", "completed": True}, {"text": "Write transcript", "completed": True}]}}))
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
            "PAK_ALLOW_NESTED_RUN": "1",
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
        transcript_text = (run_dir / "transcript.md").read_text(encoding="utf-8")
        pointer_text = pointer_file.read_text(encoding="utf-8")

        assert_contains(pointer_text, "Status: `success`", "run pointer should update once the run completes")
        assert_contains(readme_text, "Status: `success`", "run README should show the terminal status")
        assert_contains(readme_text, "Goal file: `goals/001-reader-contract.md`", "run README should link back to the goal")
        assert_contains(readme_text, "`_stdout.md`", "run README should point readers to the summary artifact")
        assert_contains(readme_text, "`transcript.md`", "run README should list the transcript artifact")
        assert_contains(readme_text, "`meta.json`", "run README should preserve the evidence path")
        assert readme_text.count("- `_stdout.md`") == 1, "run README should list _stdout.md only once"
        assert (run_dir / "transcript.md").exists(), "terminal runs should generate transcript.md"
        assert_contains(transcript_text, "README.md` remains the first reader entry", "transcript should preserve reader hierarchy")
        assert_contains(transcript_text, "`events.jsonl` remains the raw evidence source", "transcript should preserve evidence hierarchy")
        assert_contains(transcript_text, "## Assistant Message", "transcript should render assistant messages")
        assert_contains(transcript_text, "## Tool Activity", "transcript should render tool activity")
        assert_contains(transcript_text, "- Tool: `command_execution`", "codex command executions should normalize as tool activity")
        assert_contains(transcript_text, "/bin/bash -lc 'printf", "transcript should keep command metadata")
        assert_contains(transcript_text, "Output summary (from `aggregated_output`)", "transcript should label command output as a summary")
        assert_contains(transcript_text, "## File Change", "transcript should render file changes")
        assert_contains(transcript_text, "/tmp/demo.txt", "transcript should include changed file paths")
        assert transcript_text.count("## Todo List Update") == 2, "transcript should render only changed todo snapshots"
        assert_contains(transcript_text, "## Unrendered Event", "transcript should surface unsupported raw events")
        assert_contains(transcript_text, "- Raw event type: `turn.completed`", "placeholder should identify the raw event type")

        assert goal_file.exists()
        assert pointer_file.exists()
        assert (run_dir / "meta.json").exists()

    print("verify_reader_run_contract: ok")


if __name__ == "__main__":
    main()
