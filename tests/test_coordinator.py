import datetime
import io
import json
import os
import pathlib
import threading
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from system import coordinator
from system import events as events_module
from system.conversations import (
    append_message,
    open_conversation,
    read_messages,
    update_conversation,
)
from system.garden import garden_paths
from system.goals import list_goals, read_goal, submit_goal, transition_goal
from system.operator_messages import emit_tend_survey
from system.plants import commission_plant
from system.runs import close_run, list_runs, open_run, read_run
from system.somatic import SomaticLoop


def _to_epoch(ts: str) -> float:
    dt = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


class WatchdogLivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.goals_dir = self.root / "goals"
        self.runs_dir = self.root / "runs"
        self.events_path = self.root / "events" / "coordinator.jsonl"
        self.original_log_path = events_module._LOG_PATH
        events_module._LOG_PATH = self.events_path
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)
        result = commission_plant(
            "worker",
            "worker",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:01Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        events_module._LOG_PATH = self.original_log_path
        self.tempdir.cleanup()

    def _open_running_run(self, started_at: str) -> tuple[str, str]:
        result, goal_id = submit_goal(
            {
                "type": "fix",
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "priority": 5,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": "Exercise watchdog liveness detection.",
            },
            _goals_dir=self.goals_dir,
            _now=started_at,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)

        result = transition_goal(
            goal_id, "dispatched", _goals_dir=self.goals_dir, _now=started_at
        )
        self.assertTrue(result.ok)

        result, run_id = open_run(
            goal_id, "gardener", "codex", "gpt-5.4",
            _runs_dir=self.runs_dir, _now=started_at,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(run_id)

        result = transition_goal(
            goal_id, "running", _goals_dir=self.goals_dir, _now=started_at
        )
        self.assertTrue(result.ok)

        return goal_id, run_id

    def _summary(self) -> dict:
        return {
            "dispatched": [],
            "skipped": [],
            "killed": [],
            "closed": [],
            "errors": [],
        }

    def test_watchdog_uses_run_events_mtime_when_present(self) -> None:
        started_at = "2026-03-20T06:00:00Z"
        checked_at = "2026-03-20T06:06:00Z"
        recent_output_at = "2026-03-20T06:04:00Z"
        goal_id, run_id = self._open_running_run(started_at)

        run_events = self.runs_dir / run_id / "events.jsonl"
        run_events.write_text("{\"type\":\"output\"}\n", encoding="utf-8")
        epoch = _to_epoch(recent_output_at)
        os.utime(run_events, (epoch, epoch))

        summary = self._summary()
        coordinator._phase_watchdog(
            checked_at, summary, self.goals_dir, self.runs_dir, self.events_path, 300
        )

        self.assertEqual(summary["killed"], [])
        self.assertEqual(read_run(run_id, _runs_dir=self.runs_dir)["status"], "running")
        self.assertEqual(read_goal(goal_id, _goals_dir=self.goals_dir)["status"], "running")

    def test_watchdog_falls_back_to_run_started_at_when_events_file_missing(self) -> None:
        started_at = "2026-03-20T06:00:00Z"
        checked_at = "2026-03-20T06:06:00Z"
        goal_id, run_id = self._open_running_run(started_at)

        summary = self._summary()
        coordinator._phase_watchdog(
            checked_at, summary, self.goals_dir, self.runs_dir, self.events_path, 300
        )

        self.assertEqual(summary["killed"], [run_id])
        run = read_run(run_id, _runs_dir=self.runs_dir)
        goal = read_goal(goal_id, _goals_dir=self.goals_dir)
        self.assertEqual(run["status"], "killed")
        self.assertEqual(run["failure_reason"], "killed")
        self.assertEqual(goal["status"], "closed")
        self.assertEqual(goal["closed_reason"], "failure")


class GoalSubmissionObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.goals_dir = self.root / "goals"
        self.events_path = self.root / "events" / "coordinator.jsonl"
        self.conv_dir = self.root / "conversations"
        self.original_log_path = events_module._LOG_PATH
        events_module._LOG_PATH = self.events_path
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)
        result = commission_plant(
            "worker",
            "worker",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:01Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        events_module._LOG_PATH = self.original_log_path
        self.tempdir.cleanup()

    def test_submit_goal_records_type_and_conversation_on_event(self) -> None:
        now = "2026-03-20T08:15:00Z"
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="operator",
            topic="hello",
            _conv_dir=self.conv_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)

        result, goal_id = submit_goal(
            {
                "type": "converse",
                "submitted_by": "operator",
                "body": "Continue the conversation.",
                "conversation_id": conv_id,
            },
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)

        event = events_module.read_events(path=self.events_path)[-1]
        self.assertEqual(event["type"], "GoalSubmitted")
        self.assertEqual(event["actor"], "operator")
        self.assertEqual(event["goal"], goal_id)
        self.assertEqual(event["goal_type"], "converse")
        self.assertEqual(event["conversation_id"], conv_id)

    def test_coordinator_tick_logs_new_goal_submissions_once(self) -> None:
        now = "2026-03-20T08:16:00Z"
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="operator",
            topic="hello",
            _conv_dir=self.conv_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)

        coord = coordinator.Coordinator(self.root, poll_interval=60)

        result, goal_id = submit_goal(
            {
                "type": "converse",
                "submitted_by": "operator",
                "body": "Continue the conversation.",
                "conversation_id": conv_id,
            },
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)

        first = io.StringIO()
        with redirect_stdout(first):
            coord._tick()

        second = io.StringIO()
        with redirect_stdout(second):
            coord._tick()

        expected = (
            f"[{now}] goal submitted by operator: {goal_id} "
            f"(type=converse, conversation_id={conv_id})"
        )
        self.assertIn(expected, first.getvalue())
        self.assertNotIn(expected, second.getvalue())


class SpawnEvalLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.goals_dir = self.root / "goals"
        self.runs_dir = self.root / "runs"
        self.events_path = self.root / "events" / "coordinator.jsonl"
        self.original_log_path = events_module._LOG_PATH
        events_module._LOG_PATH = self.events_path
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)
        result = commission_plant(
            "worker",
            "worker",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:01Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        events_module._LOG_PATH = self.original_log_path
        self.tempdir.cleanup()

    def _submit_goal(self, *, now: str, body: str,
                     goal_type: str = "fix",
                     assigned_to: str = "gardener",
                     spawn_eval: bool = False) -> str:
        result, goal_id = submit_goal(
            {
                "type": goal_type,
                "submitted_by": "operator",
                "assigned_to": assigned_to,
                "priority": 5,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": body,
                "spawn_eval": spawn_eval,
            },
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        return goal_id or ""

    def _eval_goals_for_parent(self, parent_goal_id: str) -> list[dict]:
        return [
            goal for goal in list_goals(_goals_dir=self.goals_dir)
            if goal.get("parent_goal") == parent_goal_id
        ]

    def _dispatch_via_reconcile(self, goal_id: str, *, finished_at: str,
                                run_status: str = "success") -> dict:
        def fake_dispatch(goal: dict, run_id: str) -> None:
            close_kwargs = {
                "_runs_dir": self.runs_dir,
                "_now": finished_at,
            }
            if run_status == "success":
                close_kwargs["reflection"] = "Finished successfully."
            else:
                close_kwargs["failure_reason"] = run_status
            close_run(run_id, run_status, goal["type"], **close_kwargs)

        summary = coordinator.reconcile(
            _goals_dir=self.goals_dir,
            _runs_dir=self.runs_dir,
            _events_path=self.events_path,
            _dispatch_fn=fake_dispatch,
            _now=read_goal(goal_id, _goals_dir=self.goals_dir)["submitted_at"],
        )
        self.assertEqual(len(summary["dispatched"]), 1)
        return summary

    def _open_running_eval(self, goal_id: str, *, now: str) -> str:
        goal = read_goal(goal_id, _goals_dir=self.goals_dir)
        assert goal is not None

        result = transition_goal(
            goal_id, "dispatched", _goals_dir=self.goals_dir, _now=now
        )
        self.assertTrue(result.ok)
        result, run_id = open_run(
            goal_id,
            goal["assigned_to"],
            goal["driver"],
            goal["model"],
            _runs_dir=self.runs_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(run_id)
        result = transition_goal(
            goal_id, "running", _goals_dir=self.goals_dir, _now=now
        )
        self.assertTrue(result.ok)
        return run_id or ""

    def test_reconcile_success_spawns_eval_and_leaves_parent_evaluating(self) -> None:
        submitted_at = "2026-03-20T09:30:00Z"
        parent_goal_id = self._submit_goal(
            now=submitted_at,
            body="Finish work and request automatic evaluation.",
            spawn_eval=True,
        )

        summary = self._dispatch_via_reconcile(
            parent_goal_id,
            finished_at="2026-03-20T09:30:30Z",
        )

        parent = read_goal(parent_goal_id, _goals_dir=self.goals_dir)
        self.assertEqual(summary["dispatched"], [f"{parent_goal_id}-r1"])
        self.assertIsNotNone(parent)
        self.assertEqual(parent["status"], "evaluating")
        self.assertNotIn("closed_reason", parent)

        eval_goals = self._eval_goals_for_parent(parent_goal_id)
        self.assertEqual(len(eval_goals), 1)
        eval_goal = eval_goals[0]
        self.assertEqual(eval_goal["type"], "evaluate")
        self.assertEqual(eval_goal["status"], "queued")
        self.assertEqual(eval_goal["submitted_by"], "system")
        self.assertEqual(eval_goal["assigned_to"], "gardener")

        events = events_module.read_events(path=self.events_path)
        self.assertFalse(
            any(
                event.get("type") == "GoalClosed"
                and event.get("goal") == parent_goal_id
                for event in events
            )
        )
        self.assertEqual(
            [
                (event["type"], event.get("from"), event.get("to"))
                for event in events
                if event.get("goal") == parent_goal_id
                and event["type"] == "GoalTransitioned"
            ],
            [
                ("GoalTransitioned", "queued", "dispatched"),
                ("GoalTransitioned", "dispatched", "running"),
                ("GoalTransitioned", "running", "completed"),
                ("GoalTransitioned", "completed", "evaluating"),
            ],
        )
        self.assertEqual(
            [
                event
                for event in events
                if event.get("type") == "EvalSpawned"
                and event.get("goal") == parent_goal_id
            ],
            [
                {
                    "ts": "2026-03-20T09:30:30Z",
                    "type": "EvalSpawned",
                    "actor": "system",
                    "goal": parent_goal_id,
                    "eval_goal": eval_goal["id"],
                    "goal_type": "fix",
                    "goal_priority": 5,
                }
            ],
        )

    def test_non_success_goal_outcomes_do_not_spawn_eval(self) -> None:
        failed_goal_id = self._submit_goal(
            now="2026-03-20T09:35:00Z",
            body="Fail before eval can spawn.",
            spawn_eval=True,
        )
        self._dispatch_via_reconcile(
            failed_goal_id,
            finished_at="2026-03-20T09:35:30Z",
            run_status="failure",
        )

        failed_goal = read_goal(failed_goal_id, _goals_dir=self.goals_dir)
        self.assertEqual(failed_goal["status"], "closed")
        self.assertEqual(failed_goal["closed_reason"], "failure")
        self.assertEqual(self._eval_goals_for_parent(failed_goal_id), [])

        cancelled_goal_id = self._submit_goal(
            now="2026-03-20T09:36:00Z",
            body="Cancel before dispatch.",
            spawn_eval=True,
        )
        result = transition_goal(
            cancelled_goal_id,
            "closed",
            actor="system",
            closed_reason="cancelled",
            _goals_dir=self.goals_dir,
            _now="2026-03-20T09:36:10Z",
        )
        self.assertTrue(result.ok)
        self.assertEqual(self._eval_goals_for_parent(cancelled_goal_id), [])

        blocked_goal_id = self._submit_goal(
            now="2026-03-20T09:37:00Z",
            body="Dependency became impossible.",
            spawn_eval=True,
        )
        result = transition_goal(
            blocked_goal_id,
            "closed",
            actor="system",
            closed_reason="dependency_impossible",
            _goals_dir=self.goals_dir,
            _now="2026-03-20T09:37:10Z",
        )
        self.assertTrue(result.ok)
        self.assertEqual(self._eval_goals_for_parent(blocked_goal_id), [])

        events = events_module.read_events(path=self.events_path)
        self.assertFalse(any(event.get("type") == "EvalSpawned" for event in events))

    def test_closing_spawned_eval_closes_parent_and_emits_eval_closed(self) -> None:
        parent_goal_id = self._submit_goal(
            now="2026-03-20T09:40:00Z",
            body="Complete work, then close through spawned eval.",
            spawn_eval=True,
        )
        self._dispatch_via_reconcile(
            parent_goal_id,
            finished_at="2026-03-20T09:40:30Z",
        )
        eval_goal = self._eval_goals_for_parent(parent_goal_id)[0]

        run_id = self._open_running_eval(
            eval_goal["id"],
            now="2026-03-20T09:41:00Z",
        )
        result = close_run(
            run_id,
            "success",
            eval_goal["type"],
            reflection="Evaluation completed successfully.",
            _runs_dir=self.runs_dir,
            _now="2026-03-20T09:41:30Z",
        )
        self.assertTrue(result.ok)

        coordinator._close_goal_after_run(
            eval_goal["id"],
            self.runs_dir,
            self.goals_dir,
            "2026-03-20T09:41:30Z",
        )

        parent = read_goal(parent_goal_id, _goals_dir=self.goals_dir)
        closed_eval_goal = read_goal(eval_goal["id"], _goals_dir=self.goals_dir)
        self.assertEqual(closed_eval_goal["status"], "closed")
        self.assertEqual(closed_eval_goal["closed_reason"], "success")
        self.assertEqual(parent["status"], "closed")
        self.assertEqual(parent["closed_reason"], "success")

        events = events_module.read_events(path=self.events_path)
        self.assertIn(
            {
                "ts": "2026-03-20T09:41:30Z",
                "type": "EvalClosed",
                "actor": "system",
                "goal": parent_goal_id,
                "eval_goal": eval_goal["id"],
                "goal_reason": "success",
                "goal_type": "fix",
                "goal_priority": 5,
            },
            events,
        )
        self.assertIn(
            {
                "ts": "2026-03-20T09:41:30Z",
                "type": "GoalClosed",
                "actor": "system",
                "goal": parent_goal_id,
                "goal_reason": "success",
                "goal_type": "fix",
                "goal_priority": 5,
            },
            events,
        )

    def test_close_goal_after_run_does_not_spawn_duplicate_eval_goal(self) -> None:
        parent_goal_id = self._submit_goal(
            now="2026-03-20T09:45:00Z",
            body="Spawn eval once only.",
            spawn_eval=True,
        )
        self._dispatch_via_reconcile(
            parent_goal_id,
            finished_at="2026-03-20T09:45:30Z",
        )

        coordinator._close_goal_after_run(
            parent_goal_id,
            self.runs_dir,
            self.goals_dir,
            "2026-03-20T09:46:00Z",
        )

        self.assertEqual(len(self._eval_goals_for_parent(parent_goal_id)), 1)
        events = events_module.read_events(path=self.events_path)
        self.assertEqual(
            sum(1 for event in events if event.get("type") == "EvalSpawned"),
            1,
        )


class DispatchReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.goals_dir = self.root / "goals"
        self.runs_dir = self.root / "runs"
        self.events_path = self.root / "events" / "coordinator.jsonl"
        self.conv_dir = self.root / "conversations"
        self.original_log_path = events_module._LOG_PATH
        events_module._LOG_PATH = self.events_path
        self.original_driver_dispatch = coordinator._driver_dispatch
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)
        result = commission_plant(
            "worker",
            "worker",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:01Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        coordinator._driver_dispatch = self.original_driver_dispatch
        events_module._LOG_PATH = self.original_log_path
        self.tempdir.cleanup()

    def _submit_goal(self, body: str, *, now: str,
                     goal_type: str = "fix", priority: int = 5) -> str:
        result, goal_id = submit_goal(
            {
                "type": goal_type,
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "priority": priority,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": body,
            },
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        return goal_id

    def _wait_for_workers(self, coord: coordinator.Coordinator) -> None:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with coord._lock:
                active = list(coord._active.values())
            if not active:
                break
            for worker in active:
                worker.join(timeout=0.1)
            coord._reap()

    def test_reconcile_reserves_plant_between_same_pass_selections(self) -> None:
        now = "2026-03-20T09:00:00Z"
        first_goal = self._submit_goal("First queued fix.", now=now)
        second_goal = self._submit_goal("Second queued fix.", now=now)
        dispatched_runs = []

        def fake_dispatch(goal: dict, run_id: str) -> None:
            dispatched_runs.append((goal["id"], run_id))
            close_run(
                run_id, "success", goal["type"],
                reflection="Finished successfully.",
                _runs_dir=self.runs_dir, _now=now,
            )

        summary = coordinator.reconcile(
            _goals_dir=self.goals_dir,
            _runs_dir=self.runs_dir,
            _events_path=self.events_path,
            _dispatch_fn=fake_dispatch,
            _now=now,
            _max_concurrent=2,
        )

        self.assertEqual(len(summary["dispatched"]), 1)
        self.assertEqual([goal_id for goal_id, _ in dispatched_runs], [first_goal])
        self.assertEqual(read_goal(first_goal, _goals_dir=self.goals_dir)["status"], "closed")
        self.assertEqual(read_goal(second_goal, _goals_dir=self.goals_dir)["status"], "queued")

    def test_tick_dispatches_only_one_same_plant_goal_per_pass(self) -> None:
        now = "2026-03-20T09:05:00Z"
        first_goal = self._submit_goal("First async fix.", now=now)
        second_goal = self._submit_goal("Second async fix.", now=now)
        release = threading.Event()
        started = []

        def fake_dispatch(goal: dict, run_id: str, _garden_root=None) -> None:
            started.append((goal["id"], run_id))
            release.wait(timeout=2)
            close_run(
                run_id, "success", goal["type"],
                reflection="Finished successfully.",
                _runs_dir=self.runs_dir, _now="2026-03-20T09:05:30Z",
            )

        coordinator._driver_dispatch = fake_dispatch
        coord = coordinator.Coordinator(self.root, max_concurrent=2, poll_interval=60)
        coord._tick()

        deadline = time.time() + 1.0
        while len(started) < 1 and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual([goal_id for goal_id, _ in started], [first_goal])
        running_runs = [run for run in list_runs(_runs_dir=self.runs_dir) if run["status"] == "running"]
        self.assertEqual(len(running_runs), 1)
        self.assertEqual(running_runs[0]["goal"], first_goal)
        self.assertEqual(read_goal(first_goal, _goals_dir=self.goals_dir)["status"], "running")
        self.assertEqual(read_goal(second_goal, _goals_dir=self.goals_dir)["status"], "queued")

        release.set()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with coord._lock:
                active = list(coord._active.values())
            if not active:
                break
            for worker in active:
                worker.join(timeout=0.1)
            coord._reap()

    def test_tick_reserves_plants_across_normal_and_converse_lanes(self) -> None:
        now = "2026-03-20T09:10:00Z"
        fix_goal = self._submit_goal("Queued fix work.", now=now)
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="operator",
            topic="dispatch reservation",
            _conv_dir=self.conv_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)
        result, converse_goal = submit_goal(
            {
                "type": "converse",
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "priority": 5,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": "Continue the conversation.",
                "conversation_id": conv_id,
            },
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(converse_goal)

        release = threading.Event()
        started = []

        def fake_dispatch(goal: dict, run_id: str, _garden_root=None) -> None:
            started.append((goal["id"], run_id))
            release.wait(timeout=2)
            close_run(
                run_id, "success", goal["type"],
                reflection="Finished successfully." if goal["type"] == "fix" else None,
                _runs_dir=self.runs_dir, _now="2026-03-20T09:10:30Z",
            )

        coordinator._driver_dispatch = fake_dispatch
        coord = coordinator.Coordinator(
            self.root, max_concurrent=1, max_converse=1, poll_interval=60
        )
        coord._tick()

        deadline = time.time() + 1.0
        while len(started) < 1 and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual([goal_id for goal_id, _ in started], [fix_goal])
        running_runs = [run for run in list_runs(_runs_dir=self.runs_dir) if run["status"] == "running"]
        self.assertEqual(len(running_runs), 1)
        self.assertEqual(running_runs[0]["goal"], fix_goal)
        self.assertEqual(read_goal(fix_goal, _goals_dir=self.goals_dir)["status"], "running")
        self.assertEqual(read_goal(converse_goal, _goals_dir=self.goals_dir)["status"], "queued")

        release.set()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with coord._lock:
                active = list(coord._active.values())
            if not active:
                break
            for worker in active:
                worker.join(timeout=0.1)
            coord._reap()

    def test_tick_dispatches_post_reply_hop_without_waiting_for_normal_lane(self) -> None:
        now = "2026-03-20T09:15:00Z"
        fix_goal = self._submit_goal("Queued fix work.", now=now)
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="operator",
            topic="post reply hop priority",
            _conv_dir=self.conv_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)

        result, hop_goal = submit_goal(
            {
                "type": "converse",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "priority": 8,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": "Checkpoint the conversation before the next operator turn.",
                "conversation_id": conv_id,
                "post_reply_hop": {
                    "requested_at": now,
                    "requested_by": "system",
                    "reason": "automatic pressure handoff",
                    "automatic": True,
                    "source_run_id": "97-chat-r1",
                },
            },
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(hop_goal)

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now=now,
            post_reply_hop={
                "requested_at": now,
                "requested_by": "system",
                "reason": "automatic pressure handoff",
                "automatic": True,
                "source_goal_id": "97-chat-turn",
                "goal_id": hop_goal,
                "source_run_id": "97-chat-r1",
                "source_reply_message_id": "msg-20260320091500-gar-ab12",
                "source_reply_recorded_at": now,
                "source_session_id": "session-1",
                "source_session_ordinal": 1,
                "source_session_turns": 8,
                "pressure": {
                    "band": "high",
                    "prompt_chars": 9000,
                    "tail_messages": 3,
                },
            },
        )
        self.assertTrue(result.ok)

        result, converse_goal = submit_goal(
            {
                "type": "converse",
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "priority": 7,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": "Continue the conversation.",
                "conversation_id": conv_id,
            },
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(converse_goal)

        release = threading.Event()
        started = []

        def fake_dispatch(goal: dict, run_id: str, _garden_root=None) -> None:
            started.append((goal["id"], run_id))
            release.wait(timeout=2)
            close_run(
                run_id,
                "success",
                goal["type"],
                _runs_dir=self.runs_dir,
                _now="2026-03-20T09:15:30Z",
            )

        coordinator._driver_dispatch = fake_dispatch
        coord = coordinator.Coordinator(
            self.root, max_concurrent=1, max_converse=1, poll_interval=60
        )
        coord._tick()

        deadline = time.time() + 1.0
        while len(started) < 2 and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(
            {goal_id for goal_id, _ in started},
            {fix_goal, hop_goal},
        )
        self.assertEqual(read_goal(converse_goal, _goals_dir=self.goals_dir)["status"], "queued")
        running_runs = [run for run in list_runs(_runs_dir=self.runs_dir) if run["status"] == "running"]
        self.assertEqual({run["goal"] for run in running_runs}, {fix_goal, hop_goal})

        release.set()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with coord._lock:
                active = list(coord._active.values())
            if not active:
                break
            for worker in active:
                worker.join(timeout=0.1)
            coord._reap()

    def test_tick_blocks_converse_dispatch_while_post_reply_hop_goal_is_active(self) -> None:
        now = "2026-03-20T09:20:00Z"
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="operator",
            topic="hop block",
            _conv_dir=self.conv_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)

        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now=now,
            post_reply_hop={
                "requested_at": now,
                "requested_by": "system",
                "reason": "automatic pressure handoff",
                "automatic": True,
                "source_goal_id": "97-chat-turn",
                "goal_id": "98-post-reply-hop",
                "source_run_id": "97-chat-r1",
                "source_reply_message_id": "msg-20260320092000-gar-ab12",
                "source_reply_recorded_at": now,
                "source_session_id": "session-1",
                "source_session_ordinal": 1,
                "source_session_turns": 8,
                "pressure": {
                    "band": "high",
                    "prompt_chars": 9000,
                    "tail_messages": 3,
                },
            },
        )
        self.assertTrue(result.ok)

        result, converse_goal = submit_goal(
            {
                "type": "converse",
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "priority": 5,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": "Continue the conversation.",
                "conversation_id": conv_id,
            },
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(converse_goal)

        started = []

        def fake_dispatch(goal: dict, run_id: str, _garden_root=None) -> None:
            started.append((goal["id"], run_id))

        coordinator._driver_dispatch = fake_dispatch
        coord = coordinator.Coordinator(
            self.root, max_concurrent=1, max_converse=1, poll_interval=60
        )
        coord._tick()

        self.assertEqual(started, [])
        self.assertEqual(read_goal(converse_goal, _goals_dir=self.goals_dir)["status"], "queued")
        self.assertEqual(list_runs(_runs_dir=self.runs_dir), [])

    def test_tick_naturally_awaits_eager_external_append_hop_before_next_turn(self) -> None:
        now = "2026-03-20T09:22:00Z"
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="operator",
            topic="external append hop",
            _conv_dir=self.conv_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)
        conv_id = conv_id or ""

        result, source_message_id = append_message(
            conv_id,
            "operator",
            "How is the garden doing?",
            channel="filesystem",
            _conv_dir=self.conv_dir,
            _now="2026-03-20T09:22:01Z",
        )
        self.assertTrue(result.ok)
        result, prior_reply_message_id = append_message(
            conv_id,
            "garden",
            "Earlier garden reply from the active session.",
            channel="filesystem",
            _conv_dir=self.conv_dir,
            _now="2026-03-20T09:22:02Z",
        )
        self.assertTrue(result.ok)
        result = update_conversation(
            conv_id,
            _conv_dir=self.conv_dir,
            _now="2026-03-20T09:22:02Z",
            session_id="session-1",
            session_ordinal=1,
            session_turns=2,
            session_started_at=now,
            context_at="2026-03-20T09:22:02Z",
        )
        self.assertTrue(result.ok)

        result, tend_goal_id = submit_goal(
            {
                "type": "tend",
                "submitted_by": "gardener",
                "assigned_to": "gardener",
                "priority": 6,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": "Perform a bounded survey.",
                "origin": {
                    "kind": "conversation",
                    "conversation_id": conv_id,
                    "message_id": source_message_id,
                    "ts": "2026-03-20T09:22:01Z",
                },
                "tend": {
                    "trigger_kinds": ["operator_request"],
                },
            },
            _goals_dir=self.goals_dir,
            _now="2026-03-20T09:22:01Z",
        )
        self.assertTrue(result.ok)
        tend_goal_id = tend_goal_id or ""
        tend_run_id = f"{tend_goal_id}-r1"

        with patch.dict(
            os.environ,
            {
                "PAK2_GARDEN_ROOT": str(self.root),
                "PAK2_CURRENT_GOAL_ID": tend_goal_id,
                "PAK2_CURRENT_RUN_ID": tend_run_id,
                "PAK2_CURRENT_PLANT": "gardener",
                "PAK2_CURRENT_GOALS_DIR": str(self.goals_dir),
            },
            clear=False,
        ):
            result, record = emit_tend_survey(
                "The garden is healthy.\n",
                _garden_root=self.root,
                _now="2026-03-20T09:22:03Z",
            )
        self.assertTrue(result.ok, result.detail)
        assert record is not None

        hop_goal = next(
            goal for goal in list_goals(_goals_dir=self.goals_dir)
            if goal.get("post_reply_hop")
        )
        self.assertEqual(
            hop_goal["post_reply_hop"]["source_reply_message_id"],
            prior_reply_message_id,
        )

        result, converse_goal = submit_goal(
            {
                "type": "converse",
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "priority": 7,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": "What should I know next?",
                "conversation_id": conv_id,
            },
            _goals_dir=self.goals_dir,
            _now="2026-03-20T09:22:04Z",
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(converse_goal)

        release = threading.Event()
        started: list[tuple[str, str]] = []

        def fake_dispatch(goal: dict, run_id: str, _garden_root=None) -> None:
            started.append((goal["id"], run_id))
            if goal["id"] == hop_goal["id"]:
                release.wait(timeout=2)
                update_conversation(
                    conv_id,
                    _conv_dir=self.conv_dir,
                    _now="2026-03-20T09:22:30Z",
                    session_id=None,
                    session_turns=0,
                    session_started_at=None,
                    pending_hop=None,
                    post_reply_hop=None,
                )
            close_run(
                run_id,
                "success",
                goal["type"],
                reflection="Finished successfully." if goal["type"] != "converse" else None,
                _runs_dir=self.runs_dir,
                _now="2026-03-20T09:22:30Z",
            )

        coordinator._driver_dispatch = fake_dispatch
        coord = coordinator.Coordinator(
            self.root, max_concurrent=0, max_converse=1, poll_interval=60
        )
        coord._tick()

        deadline = time.time() + 1.0
        while len(started) < 1 and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual([goal_id for goal_id, _ in started], [hop_goal["id"]])
        self.assertEqual(read_goal(converse_goal, _goals_dir=self.goals_dir)["status"], "queued")
        running_runs = [run for run in list_runs(_runs_dir=self.runs_dir) if run["status"] == "running"]
        self.assertEqual([run["goal"] for run in running_runs], [hop_goal["id"]])

        release.set()
        self._wait_for_workers(coord)

        coord._tick()
        deadline = time.time() + 1.0
        while len(started) < 2 and time.time() < deadline:
            time.sleep(0.01)
        self._wait_for_workers(coord)

        self.assertEqual(
            [goal_id for goal_id, _ in started],
            [hop_goal["id"], converse_goal],
        )

    def test_converse_finish_wakes_somatic_to_pick_up_message_arrived_during_run(self) -> None:
        now = "2026-03-20T09:25:00Z"
        paths = garden_paths(garden_root=self.root)
        inbox = paths.operator_inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        first_note = inbox / "20260320T092500Z-first.md"
        second_note = inbox / "20260320T092501Z-second.md"
        first_note.write_text("First operator message", encoding="utf-8")

        coord = coordinator.Coordinator(
            self.root,
            max_concurrent=1,
            max_converse=1,
            poll_interval=3600,
        )
        somatic = SomaticLoop(
            self.root,
            poll_interval=3600,
            on_goal_submitted=coord.wake,
        )
        coord.on_converse_finished = somatic.wake

        first_tick_done = threading.Event()
        second_tick_done = threading.Event()

        def bounded_somatic_run() -> None:
            somatic._tick()
            first_tick_done.set()
            if somatic._wakeup.wait(timeout=1.0):
                somatic._wakeup.clear()
                somatic._tick()
                second_tick_done.set()

        somatic_thread = threading.Thread(target=bounded_somatic_run, daemon=True)
        somatic_thread.start()
        self.assertTrue(first_tick_done.wait(timeout=1.0))

        def fake_launch(model, prompt, events_path, timeout=None, session_id=None,
                        driver_name="claude", cwd=None, reasoning_effort=None,
                        env=None):
            second_note.write_text("Second operator message", encoding="utf-8")
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 120,
                            "cached_input_tokens": 0,
                            "output_tokens": 40,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (events_path.parent / "last-message.md").write_text(
                "First reply",
                encoding="utf-8",
            )
            return 0

        coordinator._driver_dispatch = self.original_driver_dispatch
        with patch("system.driver._launch", side_effect=fake_launch):
            coord._tick()
            self._wait_for_workers(coord)

        somatic_thread.join(timeout=1.0)
        self.assertFalse(somatic_thread.is_alive())
        self.assertTrue(second_tick_done.is_set())

        goals = list_goals(_goals_dir=self.goals_dir)
        converse_goals = [goal for goal in goals if goal["type"] == "converse"]
        self.assertEqual(len(converse_goals), 2)
        conversation_ids = {goal["conversation_id"] for goal in converse_goals}
        self.assertEqual(len(conversation_ids), 1)
        conv_id = next(iter(conversation_ids))

        first_goal = next(
            goal for goal in converse_goals if goal["body"] == "First operator message"
        )
        second_goal = next(
            goal for goal in converse_goals if goal["body"] == "Second operator message"
        )
        self.assertEqual(read_goal(first_goal["id"], _goals_dir=self.goals_dir)["status"], "closed")
        self.assertEqual(read_goal(second_goal["id"], _goals_dir=self.goals_dir)["status"], "queued")

        messages = read_messages(conv_id, _conv_dir=self.conv_dir)
        self.assertEqual(
            [(message["sender"], message["content"]) for message in messages],
            [
                ("operator", "First operator message"),
                ("garden", "First reply"),
                ("operator", "Second operator message"),
            ],
        )

        seen = sorted((self.root / "inbox" / ".seen").read_text(encoding="utf-8").splitlines())
        self.assertEqual(seen, [first_note.name, second_note.name])


class StartupConversationProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.goals_dir = self.root / "goals"
        self.runs_dir = self.root / "runs"
        self.events_path = self.root / "events" / "coordinator.jsonl"
        self.conv_dir = self.root / "conversations"
        self.original_log_path = events_module._LOG_PATH
        events_module._LOG_PATH = self.events_path
        self.original_driver_dispatch = coordinator._driver_dispatch
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        coordinator._driver_dispatch = self.original_driver_dispatch
        events_module._LOG_PATH = self.original_log_path
        self.tempdir.cleanup()

    def _submit_goal(self, *, now: str, body: str, goal_type: str = "fix") -> str:
        result, goal_id = submit_goal(
            {
                "type": goal_type,
                "submitted_by": "operator",
                "assigned_to": "gardener",
                "priority": 5,
                "driver": "codex",
                "model": "gpt-5.4",
                "body": body,
            },
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        return goal_id or ""

    def _wait_for_workers(self, coord: coordinator.Coordinator) -> None:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with coord._lock:
                active = list(coord._active.values())
            if not active:
                break
            for worker in active:
                worker.join(timeout=0.1)
            coord._reap()

    def test_tick_publishes_startup_run_start_and_finish_into_system_thread(self) -> None:
        now = "2026-03-20T09:50:00Z"
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="inbox/operator",
            topic="startup",
            started_by="system",
            _conv_dir=self.conv_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)
        conv_id = conv_id or ""

        goal_id = self._submit_goal(
            now=now,
            body="Perform the first startup run.",
        )
        note_path = self.root / "inbox" / "garden" / "20260320T095030Z-state-note.md"

        def fake_dispatch(goal: dict, run_id: str, _garden_root=None) -> None:
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text("Startup note.\n", encoding="utf-8")
            close_run(
                run_id,
                "success",
                goal["type"],
                reflection="Finished successfully.",
                _runs_dir=self.runs_dir,
                _now="2026-03-20T09:50:30Z",
            )

        coordinator._driver_dispatch = fake_dispatch
        coord = coordinator.Coordinator(
            self.root,
            max_concurrent=1,
            max_converse=0,
            poll_interval=60,
        )
        coord.set_startup_conversation(conv_id)
        coord._tick()
        self._wait_for_workers(coord)

        messages = read_messages(conv_id, _conv_dir=self.conv_dir)
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]["sender"], "system")
        self.assertIn(f"Started run `{goal_id}-r1`", messages[0]["content"])
        self.assertIn(f"`{goal_id}`", messages[0]["content"])
        self.assertEqual(messages[1]["sender"], "system")
        self.assertIn(f"Run `{goal_id}-r1` finished with `success`.", messages[1]["content"])
        self.assertIn("inbox/garden/20260320T095030Z-state-note.md", messages[1]["content"])
        self.assertEqual(messages[2]["sender"], "garden")
        self.assertIn("Garden reply represented from startup filesystem delivery:", messages[2]["content"])
        self.assertIn(f"Source run: `{goal_id}-r1`.", messages[2]["content"])
        self.assertIn("Original note path: `inbox/garden/20260320T095030Z-state-note.md`.", messages[2]["content"])
        self.assertIn("Startup note.", messages[2]["content"])

    def test_tick_stops_startup_updates_once_operator_has_replied(self) -> None:
        now = "2026-03-20T09:55:00Z"
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="inbox/operator",
            topic="startup",
            started_by="system",
            _conv_dir=self.conv_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)
        conv_id = conv_id or ""
        result, _ = append_message(
            conv_id,
            "operator",
            "Hello from the operator.",
            channel="filesystem",
            _conv_dir=self.conv_dir,
            _now="2026-03-20T09:55:01Z",
        )
        self.assertTrue(result.ok)

        goal_id = self._submit_goal(
            now=now,
            body="Perform the first startup run.",
        )

        def fake_dispatch(goal: dict, run_id: str, _garden_root=None) -> None:
            close_run(
                run_id,
                "success",
                goal["type"],
                reflection="Finished successfully.",
                _runs_dir=self.runs_dir,
                _now="2026-03-20T09:55:30Z",
            )

        coordinator._driver_dispatch = fake_dispatch
        coord = coordinator.Coordinator(
            self.root,
            max_concurrent=1,
            max_converse=0,
            poll_interval=60,
        )
        coord.set_startup_conversation(conv_id)
        coord._tick()
        self._wait_for_workers(coord)

        messages = read_messages(conv_id, _conv_dir=self.conv_dir)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["sender"], "operator")
        self.assertEqual(messages[0]["content"], "Hello from the operator.")
        self.assertEqual(read_goal(goal_id, _goals_dir=self.goals_dir)["status"], "closed")


class AutomaticTendSubmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.goals_dir = self.root / "goals"
        self.runs_dir = self.root / "runs"
        self.events_path = self.root / "events" / "coordinator.jsonl"
        self.conv_dir = self.root / "conversations"
        self.original_log_path = events_module._LOG_PATH
        events_module._LOG_PATH = self.events_path
        result = commission_plant(
            "gardener",
            "gardener",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:00Z",
        )
        self.assertTrue(result.ok)
        result = commission_plant(
            "worker",
            "worker",
            "operator",
            _plants_dir=self.root / "plants",
            _now="2026-03-24T00:00:01Z",
        )
        self.assertTrue(result.ok)

    def tearDown(self) -> None:
        events_module._LOG_PATH = self.original_log_path
        self.tempdir.cleanup()

    def _submit_goal(self, body: str, *, now: str, goal_type: str = "fix",
                     assigned_to: str | None = "gardener",
                     depends_on: list[str] | None = None,
                     not_before: str | None = None,
                     conversation_id: str | None = None) -> str:
        payload = {
            "type": goal_type,
            "submitted_by": "operator",
            "priority": 5,
            "driver": "codex",
            "model": "gpt-5.4",
            "body": body,
        }
        if assigned_to is not None:
            payload["assigned_to"] = assigned_to
        if depends_on:
            payload["depends_on"] = list(depends_on)
        if not_before:
            payload["not_before"] = not_before
        if conversation_id:
            payload["conversation_id"] = conversation_id

        result, goal_id = submit_goal(
            payload,
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)
        return goal_id or ""

    def _open_running_run(self, goal_id: str, *, started_at: str) -> str:
        goal = read_goal(goal_id, _goals_dir=self.goals_dir)
        assert goal is not None

        result = transition_goal(
            goal_id, "dispatched", _goals_dir=self.goals_dir, _now=started_at
        )
        self.assertTrue(result.ok)

        result, run_id = open_run(
            goal_id,
            goal.get("assigned_to", "gardener"),
            goal.get("driver", "codex"),
            goal.get("model", "gpt-5.4"),
            _runs_dir=self.runs_dir,
            _now=started_at,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(run_id)

        result = transition_goal(
            goal_id, "running", _goals_dir=self.goals_dir, _now=started_at
        )
        self.assertTrue(result.ok)
        return run_id or ""

    def _tend_goals(self) -> list[dict]:
        return [
            goal for goal in list_goals(_goals_dir=self.goals_dir)
            if goal.get("type") == "tend"
        ]

    def test_reconcile_submits_run_failure_tend_for_failed_background_goal(self) -> None:
        now = "2026-03-20T10:00:00Z"
        goal_id = self._submit_goal("Fail the background work.", now=now)
        dispatched_runs = []

        def fake_dispatch(goal: dict, run_id: str) -> None:
            dispatched_runs.append(run_id)
            close_run(
                run_id,
                "failure",
                goal["type"],
                failure_reason="failure",
                _runs_dir=self.runs_dir,
                _now="2026-03-20T10:00:30Z",
            )

        summary = coordinator.reconcile(
            _goals_dir=self.goals_dir,
            _runs_dir=self.runs_dir,
            _events_path=self.events_path,
            _dispatch_fn=fake_dispatch,
            _now=now,
        )

        self.assertEqual(len(summary["dispatched"]), 1)
        self.assertEqual(read_goal(goal_id, _goals_dir=self.goals_dir)["closed_reason"], "failure")
        tend_goals = self._tend_goals()
        self.assertEqual(len(tend_goals), 1)
        self.assertEqual(tend_goals[0]["status"], "queued")
        self.assertEqual(tend_goals[0]["tend"]["trigger_kinds"], ["run_failure"])
        self.assertEqual(tend_goals[0]["tend"]["trigger_goal"], goal_id)
        self.assertEqual(tend_goals[0]["tend"]["trigger_run"], dispatched_runs[0])

    def test_reconcile_closes_goal_and_submits_run_failure_tend_for_killed_background_goal(self) -> None:
        now = "2026-03-20T10:02:00Z"
        goal_id = self._submit_goal("Kill the background work.", now=now)
        dispatched_runs = []

        def fake_dispatch(goal: dict, run_id: str) -> None:
            dispatched_runs.append(run_id)
            close_run(
                run_id,
                "killed",
                goal["type"],
                failure_reason="killed",
                _runs_dir=self.runs_dir,
                _now="2026-03-20T10:02:30Z",
            )

        summary = coordinator.reconcile(
            _goals_dir=self.goals_dir,
            _runs_dir=self.runs_dir,
            _events_path=self.events_path,
            _dispatch_fn=fake_dispatch,
            _now=now,
        )

        self.assertEqual(len(summary["dispatched"]), 1)
        self.assertEqual(read_goal(goal_id, _goals_dir=self.goals_dir)["closed_reason"], "failure")
        tend_goals = self._tend_goals()
        self.assertEqual(len(tend_goals), 1)
        self.assertEqual(tend_goals[0]["status"], "queued")
        self.assertEqual(tend_goals[0]["tend"]["trigger_kinds"], ["run_failure"])
        self.assertEqual(tend_goals[0]["tend"]["trigger_goal"], goal_id)
        self.assertEqual(tend_goals[0]["tend"]["trigger_run"], dispatched_runs[0])

    def test_watchdog_submits_run_failure_tend_for_killed_background_run(self) -> None:
        started_at = "2026-03-20T10:05:00Z"
        checked_at = "2026-03-20T10:11:00Z"
        goal_id = self._submit_goal("Watchdog should kill this run.", now=started_at)
        run_id = self._open_running_run(goal_id, started_at=started_at)

        summary = {
            "dispatched": [],
            "skipped": [],
            "killed": [],
            "closed": [],
            "errors": [],
        }
        coordinator._phase_watchdog(
            checked_at,
            summary,
            self.goals_dir,
            self.runs_dir,
            self.events_path,
            300,
        )

        self.assertEqual(summary["killed"], [run_id])
        tend_goals = self._tend_goals()
        self.assertEqual(len(tend_goals), 1)
        self.assertEqual(tend_goals[0]["tend"]["trigger_kinds"], ["run_failure"])
        self.assertEqual(tend_goals[0]["tend"]["trigger_goal"], goal_id)
        self.assertEqual(tend_goals[0]["tend"]["trigger_run"], run_id)

    def test_reconcile_does_not_submit_run_failure_tend_for_failed_tend_goal(self) -> None:
        now = "2026-03-20T10:20:00Z"
        from system.submit import submit_tend_goal

        result, goal_id = submit_tend_goal(
            body="Perform a bounded tend pass.",
            submitted_by="gardener",
            trigger_kinds=["post_genesis"],
            driver="codex",
            model="gpt-5.4",
            _goals_dir=self.goals_dir,
            _now=now,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(goal_id)

        def fake_dispatch(goal: dict, run_id: str) -> None:
            close_run(
                run_id,
                "failure",
                goal["type"],
                failure_reason="failure",
                _runs_dir=self.runs_dir,
                _now="2026-03-20T10:20:30Z",
            )

        coordinator.reconcile(
            _goals_dir=self.goals_dir,
            _runs_dir=self.runs_dir,
            _events_path=self.events_path,
            _dispatch_fn=fake_dispatch,
            _now=now,
        )

        all_goals = list_goals(_goals_dir=self.goals_dir)
        self.assertEqual(len(all_goals), 1)
        self.assertEqual(all_goals[0]["id"], goal_id)
        self.assertEqual(all_goals[0]["closed_reason"], "failure")

    def test_tick_submits_queued_attention_tend_for_aged_dispatch_eligible_goal(self) -> None:
        submitted_at = "2026-03-20T10:30:00Z"
        now = "2026-03-20T10:32:00Z"
        goal_id = self._submit_goal("Queued but eligible background work.", now=submitted_at)

        coord = coordinator.Coordinator(
            self.root,
            max_concurrent=0,
            max_converse=0,
            poll_interval=60,
        )
        with patch.object(coordinator, "_now_utc", return_value=now):
            coord._tick()

        tend_goals = self._tend_goals()
        self.assertEqual(len(tend_goals), 1)
        self.assertEqual(tend_goals[0]["tend"]["trigger_kinds"], ["queued_attention_needed"])
        self.assertEqual(tend_goals[0]["tend"]["trigger_goal"], goal_id)

    def test_tick_does_not_submit_queued_attention_tend_for_aged_goal_selected_this_pass(self) -> None:
        submitted_at = "2026-03-20T10:33:00Z"
        now = "2026-03-20T10:35:00Z"
        goal_id = self._submit_goal("Aged goal should dispatch instead of spawning a tend.", now=submitted_at)

        def fake_dispatch(goal: dict, run_id: str, _garden_root=None) -> None:
            close_run(
                run_id,
                "success",
                goal["type"],
                reflection="Finished successfully.",
                _runs_dir=self.runs_dir,
                _now=now,
            )

        coord = coordinator.Coordinator(
            self.root,
            max_concurrent=1,
            max_converse=0,
            poll_interval=60,
        )
        with patch.object(coordinator, "_driver_dispatch", fake_dispatch):
            with patch.object(coordinator, "_now_utc", return_value=now):
                coord._tick()
                deadline = time.time() + 1.0
                while time.time() < deadline:
                    with coord._lock:
                        active = list(coord._active.values())
                    if not active:
                        break
                    for worker in active:
                        worker.join(timeout=0.1)
                    coord._reap()

        self.assertEqual(self._tend_goals(), [])
        self.assertEqual(read_goal(goal_id, _goals_dir=self.goals_dir)["status"], "closed")

    def test_tick_submits_queued_attention_tend_for_aged_unassigned_goal(self) -> None:
        submitted_at = "2026-03-20T10:35:00Z"
        now = "2026-03-20T10:37:00Z"
        goal_id = self._submit_goal(
            "Queued and still unassigned.",
            now=submitted_at,
            assigned_to=None,
        )

        coord = coordinator.Coordinator(
            self.root,
            max_concurrent=0,
            max_converse=0,
            poll_interval=60,
        )
        with patch.object(coordinator, "_now_utc", return_value=now):
            coord._tick()

        tend_goals = self._tend_goals()
        self.assertEqual(len(tend_goals), 1)
        self.assertEqual(tend_goals[0]["tend"]["trigger_kinds"], ["queued_attention_needed"])
        self.assertEqual(tend_goals[0]["tend"]["trigger_goal"], goal_id)

    def test_reconcile_does_not_submit_queued_attention_tend_for_goal_waiting_behind_same_pass_work(self) -> None:
        submitted_at = "2026-03-20T10:38:00Z"
        now = "2026-03-20T10:40:00Z"
        first_goal = self._submit_goal("First aged queued goal.", now=submitted_at)
        second_goal = self._submit_goal("Second aged queued goal.", now=submitted_at)
        dispatched_runs = []

        def fake_dispatch(goal: dict, run_id: str) -> None:
            dispatched_runs.append(run_id)
            close_run(
                run_id,
                "success",
                goal["type"],
                reflection="Finished successfully.",
                _runs_dir=self.runs_dir,
                _now=now,
            )

        summary = coordinator.reconcile(
            _goals_dir=self.goals_dir,
            _runs_dir=self.runs_dir,
            _events_path=self.events_path,
            _dispatch_fn=fake_dispatch,
            _now=now,
            _max_concurrent=2,
        )

        self.assertEqual(len(summary["dispatched"]), 1)
        self.assertEqual(dispatched_runs, summary["dispatched"])
        self.assertEqual(self._tend_goals(), [])
        self.assertEqual(read_goal(first_goal, _goals_dir=self.goals_dir)["status"], "closed")
        self.assertEqual(read_goal(second_goal, _goals_dir=self.goals_dir)["status"], "queued")

    def test_tick_does_not_submit_queued_attention_tend_for_legitimate_waits(self) -> None:
        submitted_at = "2026-03-20T10:40:00Z"
        now = "2026-03-20T10:42:00Z"

        not_before_goal = self._submit_goal(
            "Waiting for not_before.",
            now=submitted_at,
            not_before="2026-03-20T10:50:00Z",
        )
        dep_root = self._submit_goal(
            "Dependency root still running.",
            now="2026-03-20T10:41:00Z",
            assigned_to="worker",
        )
        self._open_running_run(dep_root, started_at="2026-03-20T10:41:00Z")
        blocked_on_dep = self._submit_goal(
            "Waiting on dependency.",
            now=submitted_at,
            depends_on=[dep_root],
        )
        busy_root = self._submit_goal(
            "Plant is busy.",
            now="2026-03-20T10:41:00Z",
        )
        self._open_running_run(busy_root, started_at="2026-03-20T10:41:00Z")
        plant_busy_goal = self._submit_goal(
            "Waiting on busy plant.",
            now=submitted_at,
        )

        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref="operator",
            topic="blocked hop",
            _conv_dir=self.conv_dir,
            _now=submitted_at,
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(conv_id)
        result = update_conversation(
            conv_id or "",
            _conv_dir=self.conv_dir,
            _now=submitted_at,
            post_reply_hop={
                "requested_at": submitted_at,
                "requested_by": "system",
                "reason": "automatic pressure handoff",
                "automatic": True,
                "source_goal_id": "97-chat-turn",
                "goal_id": "98-post-reply-hop",
                "source_run_id": "97-chat-r1",
                "source_reply_message_id": "msg-20260320104000-gar-ab12",
                "source_reply_recorded_at": submitted_at,
                "source_session_id": "session-1",
                "source_session_ordinal": 1,
                "source_session_turns": 8,
                "pressure": {
                    "band": "high",
                    "prompt_chars": 9000,
                    "tail_messages": 3,
                },
            },
        )
        self.assertTrue(result.ok)
        converse_goal = self._submit_goal(
            "Waiting for the active post-reply hop.",
            now=submitted_at,
            goal_type="converse",
            conversation_id=conv_id,
        )

        coord = coordinator.Coordinator(
            self.root,
            max_concurrent=0,
            max_converse=0,
            poll_interval=60,
        )
        with patch.object(coordinator, "_now_utc", return_value=now):
            coord._tick()

        self.assertEqual(self._tend_goals(), [])
        self.assertEqual(read_goal(not_before_goal, _goals_dir=self.goals_dir)["status"], "queued")
        self.assertEqual(read_goal(blocked_on_dep, _goals_dir=self.goals_dir)["status"], "queued")
        self.assertEqual(read_goal(plant_busy_goal, _goals_dir=self.goals_dir)["status"], "queued")
        self.assertEqual(read_goal(converse_goal, _goals_dir=self.goals_dir)["status"], "queued")


if __name__ == "__main__":
    unittest.main()
