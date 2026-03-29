import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from system.driver import _agent_env, _build_prompt
from system.channels import FilesystemChannel
from system.coordinator import reconcile
from system.garden import garden_paths, set_garden_name
from system.goals import materialize_dispatch_packet, read_goal, submit_goal
from system.runs import close_run, read_run
from system.submit import submit_tend_goal


def _write_runtime_config(root: pathlib.Path, *, include_defaults: bool = False) -> None:
    lines = [
        "[runtime]",
        'root = ".runtime"',
    ]
    if include_defaults:
        lines.extend(
            [
                "",
                "[defaults]",
                'driver = "claude"',
                'model = "gpt-5.4-mini"',
            ]
        )
    (root / "PAK2.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_active_plant(root: pathlib.Path, name: str = "gardener") -> None:
    plant_dir = root / "plants" / name
    (plant_dir / "memory").mkdir(parents=True, exist_ok=True)
    (plant_dir / "meta.json").write_text(
        json.dumps(
            {
                "name": name,
                "seed": name,
                "status": "active",
                "created_at": "2026-03-24T20:00:00Z",
                "commissioned_by": "operator",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class RuntimeRootConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_garden_paths_use_configured_runtime_root(self) -> None:
        _write_runtime_config(self.root)

        paths = garden_paths(garden_root=self.root)

        self.assertEqual(paths.garden_root, self.root)
        self.assertEqual(paths.runtime_root, self.root / ".runtime")
        self.assertEqual(paths.goals_dir, self.root / ".runtime" / "goals")
        self.assertEqual(paths.runs_dir, self.root / ".runtime" / "runs")
        self.assertEqual(paths.events_dir, self.root / ".runtime" / "events")
        self.assertEqual(
            paths.coordinator_events_path,
            self.root / ".runtime" / "events" / "coordinator.jsonl",
        )
        self.assertEqual(paths.conversations_dir, self.root / ".runtime" / "conversations")
        self.assertEqual(paths.inbox_dir, self.root / ".runtime" / "inbox")
        self.assertEqual(paths.dashboard_invocations_dir, self.root / ".runtime" / "dashboard" / "invocations")
        self.assertEqual(paths.plants_dir, self.root / "plants")
        self.assertEqual(paths.seeds_dir, self.root / "seeds")

    def test_filesystem_channel_uses_runtime_root_inbox_when_configured(self) -> None:
        _write_runtime_config(self.root)
        result = set_garden_name("sprout", garden_root=self.root)
        self.assertTrue(result.ok)

        channel = FilesystemChannel(self.root)
        channel.send("1-hello", "Hello operator.\n")

        sent_files = list((self.root / ".runtime" / "inbox" / "sprout").glob("*.md"))
        self.assertEqual(len(sent_files), 1)
        self.assertEqual(sent_files[0].read_text(encoding="utf-8"), "Hello operator.\n")
        config_text = (self.root / "PAK2.toml").read_text(encoding="utf-8")
        self.assertIn("[runtime]", config_text)
        self.assertIn('root = ".runtime"', config_text)
        self.assertIn("[garden]", config_text)
        self.assertIn('name = "sprout"', config_text)


class SplitRuntimeRootDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        _write_runtime_config(self.root, include_defaults=True)
        _write_active_plant(self.root)
        self.paths = garden_paths(garden_root=self.root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_submit_goal_uses_garden_root_plants_with_split_runtime_root(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "fix",
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "body": "Route runtime churn through the configured runtime root.",
            },
            _goals_dir=self.paths.goals_dir,
            _now="2026-03-24T20:10:00Z",
        )

        self.assertTrue(result.ok, result.detail)
        self.assertTrue((self.paths.goals_dir / f"{goal_id}.json").exists())
        events = [
            json.loads(line)
            for line in self.paths.coordinator_events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual([event["type"] for event in events], ["GoalSubmitted"])

    def test_reconcile_reads_defaults_from_garden_root_with_split_runtime_root(self) -> None:
        result, goal_id = submit_goal(
            {
                "type": "fix",
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "body": "Dispatch one split-layout runtime-root test goal.",
            },
            _goals_dir=self.paths.goals_dir,
            _now="2026-03-24T20:12:00Z",
        )
        self.assertTrue(result.ok, result.detail)

        def fake_dispatch(goal: dict, run_id: str) -> None:
            close_run(
                run_id,
                "success",
                goal["type"],
                cost={"source": "unknown"},
                _runs_dir=self.paths.runs_dir,
                _now="2026-03-24T20:12:05Z",
            )

        summary = reconcile(
            _goals_dir=self.paths.goals_dir,
            _runs_dir=self.paths.runs_dir,
            _events_path=self.paths.coordinator_events_path,
            _dispatch_fn=fake_dispatch,
            _now="2026-03-24T20:12:00Z",
        )

        self.assertEqual(len(summary["dispatched"]), 1)
        run_id = summary["dispatched"][0]
        run = read_run(run_id, _runs_dir=self.paths.runs_dir)
        self.assertEqual(run["driver"], "claude")
        self.assertEqual(run["model"], "gpt-5.4-mini")
        self.assertEqual(run["goal"], goal_id)

    def test_explicit_top_level_queue_uses_sibling_events_and_runs(self) -> None:
        legacy_goals_dir = self.root / "goals"
        now = "2026-03-24T20:14:00Z"
        result, goal_id = submit_goal(
            {
                "type": "fix",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "body": "Keep explicit queue paths on the same storage lane.",
                "origin": {
                    "kind": "conversation",
                    "conversation_id": "1-hello",
                    "message_id": "msg-20260324201400-ope-abcd",
                    "ts": now,
                },
                "pre_dispatch_updates": {
                    "policy": "supplement",
                },
            },
            _goals_dir=legacy_goals_dir,
            _now=now,
        )

        self.assertTrue(result.ok, result.detail)
        goal = read_goal(goal_id or "", _goals_dir=legacy_goals_dir)
        self.assertIsNotNone(goal)

        result, packet = materialize_dispatch_packet(
            goal or {},
            f"{goal_id}-r1",
            now,
            _goals_dir=legacy_goals_dir,
        )

        self.assertTrue(result.ok, result.detail)
        self.assertIsNotNone(packet)
        self.assertTrue((self.root / "runs" / f"{goal_id}-r1" / "dispatch-packet.json").exists())
        self.assertFalse((self.paths.runs_dir / f"{goal_id}-r1" / "dispatch-packet.json").exists())

        legacy_events_path = self.root / "events" / "coordinator.jsonl"
        self.assertTrue(legacy_events_path.exists())
        events = [
            json.loads(line)
            for line in legacy_events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            [event["type"] for event in events],
            ["GoalSubmitted", "DispatchPacketMaterialized"],
        )
        self.assertFalse(self.paths.coordinator_events_path.exists())

    def test_converse_run_submissions_reuse_current_goal_queue(self) -> None:
        legacy_goals_dir = self.root / "goals"
        source_now = "2026-03-24T20:18:00Z"
        result, source_goal_id = submit_goal(
            {
                "type": "converse",
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "priority": 7,
                "body": "Check the current garden state after idle.",
                "conversation_id": "1-hello",
                "source_message_id": "msg-20260324201800-ope-abcd",
            },
            _goals_dir=legacy_goals_dir,
            _now=source_now,
        )

        self.assertTrue(result.ok, result.detail)
        goal = read_goal(source_goal_id or "", _goals_dir=legacy_goals_dir)
        self.assertIsNotNone(goal)

        env = _agent_env(goal or {}, f"{source_goal_id}-r1", root=self.root)
        self.assertEqual(env.get("PAK2_CURRENT_GOALS_DIR"), str(legacy_goals_dir.resolve()))

        with patch.dict(os.environ, env, clear=False):
            result, tend_goal_id = submit_tend_goal(
                body="Perform a bounded tend pass for the operator request.",
                submitted_by="gardener",
                trigger_kinds=["operator_request"],
                _now="2026-03-24T20:18:10Z",
            )

        self.assertTrue(result.ok, result.detail)
        self.assertEqual(tend_goal_id, "2-perform-a-bounded-tend-pass-for-the-oper")
        self.assertTrue((legacy_goals_dir / f"{tend_goal_id}.json").exists())
        self.assertFalse((self.paths.goals_dir / f"{tend_goal_id}.json").exists())

        legacy_events_path = self.root / "events" / "coordinator.jsonl"
        events = [
            json.loads(line)
            for line in legacy_events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            [(event["type"], event["goal"]) for event in events],
            [
                ("GoalSubmitted", source_goal_id),
                ("GoalSubmitted", tend_goal_id),
            ],
        )
        self.assertFalse(self.paths.coordinator_events_path.exists())

    def test_build_prompt_uses_runtime_root_run_directory_when_configured(self) -> None:
        goal = {
            "id": "1-route-run-local-artifacts",
            "type": "fix",
            "assigned_to": "gardener",
            "body": "Write the run-local artifact in the current run directory.",
        }

        prompt = _build_prompt(
            goal,
            "1-route-run-local-artifacts-r1",
            "gardener",
            self.root,
        )

        self.assertIn(
            "Run directory: `.runtime/runs/1-route-run-local-artifacts-r1/`",
            prompt,
        )
        self.assertNotIn(
            "Run directory: `runs/1-route-run-local-artifacts-r1/`",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
