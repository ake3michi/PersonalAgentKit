import datetime
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from jsonschema import Draft202012Validator

from system.dashboard import (
    build_snapshot,
    build_render_tree,
    build_snapshot_tree,
    render_dashboard,
    validate_render_tree,
    validate_snapshot_tree,
)


def _to_epoch(ts: str) -> float:
    dt = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        for rel in ("goals", "runs", "events", "conversations"):
            (self.root / rel).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_json(self, path: pathlib.Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _write_jsonl(self, path: pathlib.Path, records: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
            encoding="utf-8",
        )

    def _write_goal(self, payload: dict) -> None:
        self._write_json(self.root / "goals" / f"{payload['id']}.json", payload)

    def _write_run(self, payload: dict, *, last_output_at: str | None = None) -> None:
        run_dir = self.root / "runs" / payload["id"]
        self._write_json(run_dir / "meta.json", payload)
        if last_output_at is not None:
            events_path = run_dir / "events.jsonl"
            events_path.write_text('{"type":"output"}\n', encoding="utf-8")
            epoch = _to_epoch(last_output_at)
            os.utime(events_path, (epoch, epoch))

    def _write_conversation(self, conv_id: str, payload: dict) -> None:
        self._write_json(self.root / "conversations" / conv_id / "meta.json", payload)

    def _load_schema(self, name: str) -> dict:
        schema_path = pathlib.Path(__file__).resolve().parent.parent / "schema" / name
        return json.loads(schema_path.read_text(encoding="utf-8"))

    def _assert_schema_valid(self, name: str, payload: dict) -> None:
        validator = Draft202012Validator(
            self._load_schema(name),
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda error: ([str(part) for part in error.path], error.message),
        )
        self.assertEqual(
            [],
            [
                f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
                for error in errors
            ],
        )

    def _build_snapshot(
        self,
        *,
        now: str,
        coordinator_pids: list[int] | None = None,
    ):
        if coordinator_pids is None:
            return build_snapshot(self.root, now=now)
        with mock.patch(
            "system.dashboard._find_coordinator_processes",
            return_value=coordinator_pids,
        ):
            return build_snapshot(self.root, now=now)

    def test_build_snapshot_derives_goal_states_and_cost_rollups(self) -> None:
        now = "2026-03-20T15:10:00Z"

        self._write_goal(
            {
                "id": "44-design-dashboard",
                "status": "closed",
                "submitted_at": "2026-03-20T14:59:22Z",
                "type": "spike",
                "assigned_to": "gardener",
                "priority": 6,
                "closed_reason": "success",
            }
        )
        self._write_goal(
            {
                "id": "45-build-dashboard",
                "status": "running",
                "submitted_at": "2026-03-20T15:04:10Z",
                "type": "build",
                "assigned_to": "gardener",
                "priority": 6,
            }
        )
        self._write_goal(
            {
                "id": "46-evaluate-dashboard",
                "status": "queued",
                "submitted_at": "2026-03-20T15:05:00Z",
                "type": "evaluate",
                "assigned_to": "gardener",
                "priority": 6,
                "depends_on": ["45-build-dashboard"],
            }
        )
        self._write_goal(
            {
                "id": "47-follow-up-fix",
                "status": "queued",
                "submitted_at": "2026-03-20T15:06:00Z",
                "type": "fix",
                "assigned_to": "gardener",
                "priority": 7,
            }
        )
        self._write_goal(
            {
                "id": "48-unassigned-research",
                "status": "queued",
                "submitted_at": "2026-03-20T15:07:00Z",
                "type": "research",
                "priority": 3,
            }
        )
        self._write_goal(
            {
                "id": "49-not-before-spike",
                "status": "queued",
                "submitted_at": "2026-03-20T15:03:00Z",
                "type": "spike",
                "assigned_to": "gardener",
                "priority": 5,
                "not_before": "2026-03-20T15:20:00Z",
            }
        )
        self._write_goal(
            {
                "id": "50-ready-fix",
                "status": "queued",
                "submitted_at": "2026-03-20T15:06:30Z",
                "type": "fix",
                "assigned_to": "pruner",
                "priority": 9,
            }
        )

        self._write_run(
            {
                "id": "45-build-dashboard-r1",
                "goal": "45-build-dashboard",
                "plant": "gardener",
                "status": "running",
                "started_at": "2026-03-20T15:04:10Z",
                "driver": "codex",
                "model": "gpt-5.4",
            },
            last_output_at="2026-03-20T15:09:30Z",
        )
        self._write_run(
            {
                "id": "43-observability-chat-r1",
                "goal": "43-observability-chat",
                "plant": "gardener",
                "status": "success",
                "started_at": "2026-03-20T14:58:42Z",
                "completed_at": "2026-03-20T14:59:49Z",
                "driver": "codex",
                "model": "gpt-5.4",
                "cost": {
                    "source": "provider",
                    "input_tokens": 1_096_119,
                    "output_tokens": 14_162,
                    "cache_read_tokens": 925_312,
                },
            }
        )
        self._write_run(
            {
                "id": "44-design-dashboard-r1",
                "goal": "44-design-dashboard",
                "plant": "gardener",
                "status": "success",
                "started_at": "2026-03-20T14:59:42Z",
                "completed_at": "2026-03-20T15:04:10Z",
                "driver": "codex",
                "model": "gpt-5.4",
                "cost": {
                    "source": "provider",
                    "input_tokens": 876_746,
                    "output_tokens": 13_338,
                    "cache_read_tokens": 830_080,
                },
            }
        )
        self._write_run(
            {
                "id": "12-old-unknown-r1",
                "goal": "12-old-unknown",
                "plant": "gardener",
                "status": "success",
                "started_at": "2026-03-20T06:14:03Z",
                "completed_at": "2026-03-20T06:15:07Z",
                "driver": "codex",
                "model": "gpt-5.4",
                "cost": {"source": "unknown"},
            }
        )

        self._write_jsonl(
            self.root / "events" / "coordinator.jsonl",
            [
                {
                    "ts": "2026-03-20T15:04:10Z",
                    "type": "RunStarted",
                    "goal": "45-build-dashboard",
                    "run": "45-build-dashboard-r1",
                },
                {
                    "ts": "2026-03-20T15:09:50Z",
                    "type": "GoalSubmitted",
                    "actor": "garden",
                    "goal": "50-ready-fix",
                    "goal_type": "fix",
                },
            ],
        )

        snapshot = build_snapshot(self.root, now=now)
        snapshot_tree = build_snapshot_tree(self.root, now=now)
        validation = validate_snapshot_tree(snapshot_tree)

        self._assert_schema_valid("dashboard-snapshot.schema.json", snapshot_tree)

        self.assertTrue(validation.ok)
        self.assertEqual(snapshot.state, "active")
        self.assertEqual(snapshot_tree["state"], snapshot.state)
        self.assertEqual(snapshot.cycle_health.work_status, "active")
        self.assertEqual(snapshot.cycle_health.running_runs, 1)
        self.assertEqual(snapshot.cycle_health.freshest_run_output_age_seconds, 30)
        self.assertEqual(snapshot.cycle_health.queued_goals, 5)
        self.assertEqual(snapshot.cycle_health.eligible_queued, 1)
        self.assertEqual(snapshot.cycle_health.blocked_queued, 4)
        self.assertEqual(snapshot.cycle_health.last_coordinator_event_age_seconds, 10)

        entries = {entry.goal_id: entry for entry in snapshot.active_work}
        self.assertEqual(entries["45-build-dashboard"].bucket, "running")
        self.assertEqual(entries["45-build-dashboard"].run_event_count, 1)
        self.assertEqual(entries["46-evaluate-dashboard"].blocked_reason, "dependency")
        self.assertEqual(entries["47-follow-up-fix"].blocked_reason, "plant_busy")
        self.assertEqual(entries["48-unassigned-research"].blocked_reason, "unassigned")
        self.assertEqual(entries["49-not-before-spike"].blocked_reason, "not_before")
        self.assertEqual(entries["50-ready-fix"].bucket, "eligible")

        self.assertEqual(snapshot.cost.today_input_tokens, 1_972_865)
        self.assertEqual(snapshot.cost.today_output_tokens, 27_500)
        self.assertEqual(snapshot.cost.today_cache_read_tokens, 1_755_392)
        self.assertEqual(snapshot.cost.unknown_completed_runs, 1)
        self.assertIsNotNone(snapshot.cost.latest_completed_run)
        self.assertEqual(snapshot.cost.latest_completed_run.run_id, "44-design-dashboard-r1")
        self.assertEqual(
            [run.run_id for run in snapshot.cost.top_input_runs],
            ["43-observability-chat-r1", "44-design-dashboard-r1"],
        )
        self.assertEqual(snapshot.cost.active_driver_models, ["codex/gpt-5.4"])

    def test_build_snapshot_emits_alerts_and_dedupes_conversation_state(self) -> None:
        now = "2026-03-20T15:10:00Z"

        self._write_goal(
            {
                "id": "45-build-dashboard",
                "status": "running",
                "submitted_at": "2026-03-20T15:00:00Z",
                "type": "build",
                "assigned_to": "gardener",
                "priority": 6,
            }
        )
        self._write_goal(
            {
                "id": "50-ready-fix",
                "status": "queued",
                "submitted_at": "2026-03-20T15:07:00Z",
                "type": "fix",
                "assigned_to": "pruner",
                "priority": 9,
            }
        )
        self._write_goal(
            {
                "id": "51-unassigned-fix",
                "status": "queued",
                "submitted_at": "2026-03-20T15:06:30Z",
                "type": "fix",
                "priority": 5,
            }
        )

        self._write_run(
            {
                "id": "45-build-dashboard-r1",
                "goal": "45-build-dashboard",
                "plant": "gardener",
                "status": "running",
                "started_at": "2026-03-20T15:00:00Z",
                "driver": "codex",
                "model": "gpt-5.4",
            },
            last_output_at="2026-03-20T15:03:30Z",
        )
        self._write_run(
            {
                "id": "52-failed-run-r1",
                "goal": "52-failed-run",
                "plant": "gardener",
                "status": "killed",
                "started_at": "2026-03-20T15:08:30Z",
                "completed_at": "2026-03-20T15:09:30Z",
                "driver": "codex",
                "model": "gpt-5.4",
                "cost": {"source": "unknown"},
            }
        )

        self._write_conversation(
            "1-hello",
            {
                "id": "1-hello",
                "status": "open",
                "channel": "filesystem",
                "channel_ref": "inbox/operator",
                "presence_model": "async",
                "participants": ["operator", "garden"],
                "started_at": "2026-03-20T05:46:26Z",
                "last_activity_at": "2026-03-20T15:09:00Z",
                "context_at": "2026-03-20T15:09:00Z",
                "session_id": "session-1",
                "compacted_through": None,
                "session_ordinal": 1,
                "session_turns": 6,
                "session_started_at": "2026-03-20T14:18:50Z",
                "checkpoint_count": 1,
                "last_checkpoint_id": "ckpt-1",
                "last_checkpoint_at": "2026-03-20T14:17:02Z",
                "last_turn_mode": "resumed",
                "last_turn_run_id": "43-observability-chat-r1",
                "pending_hop": {
                    "requested_at": "2026-03-20T15:08:59Z",
                    "requested_by": "operator",
                    "reason": "fresh session please",
                },
                "last_pressure": {
                    "band": "critical",
                    "score": 1.1,
                    "needs_hop": True,
                },
            },
        )

        self._write_jsonl(
            self.root / "events" / "coordinator.jsonl",
            [
                {
                    "ts": "2026-03-20T15:05:00Z",
                    "type": "RunStarted",
                    "goal": "45-build-dashboard",
                    "run": "45-build-dashboard-r1",
                }
            ],
        )

        snapshot = build_snapshot(self.root, now=now)

        by_subject = {(alert.subject, alert.identifier): alert for alert in snapshot.alerts}

        self.assertEqual(snapshot.alert_counts["critical"], 2)
        self.assertEqual(snapshot.alert_counts["warning"], 3)
        self.assertEqual(snapshot.alert_counts["info"], 1)

        self.assertEqual(by_subject[("goal", "50-ready-fix")].severity, "critical")
        self.assertEqual(by_subject[("run", "45-build-dashboard-r1")].severity, "critical")
        self.assertEqual(by_subject[("conversation", "1-hello")].severity, "warning")
        self.assertIn("needs session hop", by_subject[("conversation", "1-hello")].reason)
        self.assertTrue(any(alert.subject == "run" and alert.identifier == "52-failed-run-r1"
                            for alert in snapshot.alerts))
        self.assertEqual(
            len([alert for alert in snapshot.alerts if alert.subject == "conversation"]),
            1,
        )
        self.assertNotIn(("cycle", "coordinator"), by_subject)

    def test_render_dashboard_handles_missing_optional_files(self) -> None:
        now = "2026-03-20T15:10:00Z"
        self._write_conversation(
            "1-chat",
            {
                "id": "1-chat",
                "status": "open",
                "channel": "filesystem",
                "channel_ref": "inbox/operator",
                "presence_model": "async",
                "participants": ["operator", "garden"],
                "started_at": "2026-03-20T15:00:00Z",
                "last_activity_at": "2026-03-20T15:09:30Z",
                "context_at": "2026-03-20T15:09:30Z",
                "session_id": None,
                "compacted_through": None,
                "session_ordinal": 0,
                "session_turns": 0,
                "session_started_at": None,
                "checkpoint_count": 0,
                "last_checkpoint_id": None,
                "last_checkpoint_at": None,
                "last_turn_mode": None,
                "last_turn_run_id": None,
                "pending_hop": None,
                "last_pressure": None,
            },
        )

        snapshot = build_snapshot(self.root, now=now)
        snapshot_tree = build_snapshot_tree(self.root, now=now)
        render_tree = build_render_tree(snapshot, width=220, height=60)
        output = render_dashboard(snapshot, width=220, height=60)
        render_validation = validate_render_tree(render_tree)
        validation = validate_snapshot_tree(snapshot_tree)

        self._assert_schema_valid("dashboard-snapshot.schema.json", snapshot_tree)
        self._assert_schema_valid("dashboard-render.schema.json", render_tree)

        self.assertTrue(validation.ok)
        self.assertTrue(render_validation.ok)
        self.assertEqual(snapshot.state, "idle")
        self.assertEqual(snapshot_tree["conversations"][0]["mode"], "fresh-start")
        self.assertEqual(snapshot.conversations[0].mode, "fresh-start")
        self.assertEqual(render_tree["layout"], "two-column")
        self.assertEqual(
            [row["panel_keys"] for row in render_tree["rows"]],
            [
                ["cycle_health", "alerts"],
                ["active_work", "recent_activity"],
                ["conversations", "cost"],
            ],
        )
        self.assertEqual(render_tree["header"], output.splitlines()[0])
        self.assertEqual(render_tree["text_lines"], output.splitlines())
        self.assertIn("Cycle Health", output)
        self.assertIn("Active Work", output)
        self.assertIn("Conversations", output)
        self.assertIn("Cost", output)
        self.assertIn("Alerts", output)
        self.assertIn("Recent Activity", output)
        self.assertIn("queue (0 total):", output)
        self.assertIn("no active alerts", output)

    def test_build_render_tree_supports_stacked_layout_and_truncation(self) -> None:
        snapshot = build_snapshot(self.root, now="2026-03-20T15:10:00Z")
        render_tree = build_render_tree(snapshot, width=90, height=8)
        output = render_dashboard(snapshot, width=90, height=8)
        validation = validate_render_tree(render_tree)

        self._assert_schema_valid("dashboard-render.schema.json", render_tree)

        self.assertTrue(validation.ok)
        self.assertEqual(render_tree["width"], 90)
        self.assertEqual(render_tree["height_limit"], 8)
        self.assertEqual(render_tree["layout"], "stacked")
        self.assertTrue(render_tree["truncated"])
        self.assertEqual(
            [row["panel_keys"] for row in render_tree["rows"]],
            [
                ["cycle_health"],
                ["active_work"],
                ["conversations"],
                ["cost"],
                ["alerts"],
                ["recent_activity"],
            ],
        )
        self.assertEqual(render_tree["header"], render_tree["text_lines"][0])
        self.assertEqual(render_tree["text_lines"][-1], "...(truncated)")
        self.assertEqual(output, "\n".join(render_tree["text_lines"]))

    def test_render_dashboard_prioritizes_operator_questions(self) -> None:
        now = "2026-03-20T15:10:00Z"

        self._write_goal(
            {
                "id": "45-build-dashboard",
                "status": "running",
                "submitted_at": "2026-03-20T15:00:00Z",
                "type": "build",
                "assigned_to": "gardener",
                "priority": 6,
            }
        )
        self._write_goal(
            {
                "id": "46-follow-up-fix",
                "status": "queued",
                "submitted_at": "2026-03-20T15:08:00Z",
                "type": "fix",
                "assigned_to": "gardener",
                "priority": 6,
            }
        )

        self._write_run(
            {
                "id": "45-build-dashboard-r1",
                "goal": "45-build-dashboard",
                "plant": "gardener",
                "status": "running",
                "started_at": "2026-03-20T15:08:00Z",
                "driver": "codex",
                "model": "gpt-5.4",
            },
            last_output_at="2026-03-20T15:09:30Z",
        )
        self._write_run(
            {
                "id": "44-design-dashboard-r1",
                "goal": "44-design-dashboard",
                "plant": "gardener",
                "status": "success",
                "started_at": "2026-03-20T15:00:30Z",
                "completed_at": "2026-03-20T15:07:30Z",
                "driver": "codex",
                "model": "gpt-5.4",
                "cost": {
                    "source": "provider",
                    "input_tokens": 876_746,
                    "output_tokens": 13_338,
                    "cache_read_tokens": 830_080,
                },
            }
        )

        self._write_conversation(
            "1-hello",
            {
                "id": "1-hello",
                "status": "open",
                "channel": "filesystem",
                "channel_ref": "inbox/operator",
                "presence_model": "async",
                "participants": ["operator", "garden"],
                "started_at": "2026-03-20T05:46:26Z",
                "last_activity_at": "2026-03-20T15:09:00Z",
                "context_at": "2026-03-20T15:09:00Z",
                "session_id": "session-1",
                "compacted_through": None,
                "session_ordinal": 1,
                "session_turns": 6,
                "session_started_at": "2026-03-20T14:18:50Z",
                "checkpoint_count": 1,
                "last_checkpoint_id": "ckpt-1",
                "last_checkpoint_at": "2026-03-20T14:17:02Z",
                "last_turn_mode": "resumed",
                "last_turn_run_id": "43-observability-chat-r1",
                "pending_hop": None,
                "last_pressure": {
                    "band": "critical",
                    "score": 1.1,
                    "needs_hop": True,
                },
            },
        )

        self._write_jsonl(
            self.root / "events" / "coordinator.jsonl",
            [
                {
                    "ts": "2026-03-20T15:07:30Z",
                    "type": "RunFinished",
                    "goal": "44-design-dashboard",
                    "run": "44-design-dashboard-r1",
                    "run_reason": "success",
                },
                {
                    "ts": "2026-03-20T15:08:00Z",
                    "type": "GoalTransitioned",
                    "goal": "45-build-dashboard",
                    "from": "dispatched",
                    "to": "running",
                },
                {
                    "ts": "2026-03-20T15:08:00Z",
                    "type": "RunStarted",
                    "goal": "45-build-dashboard",
                    "run": "45-build-dashboard-r1",
                },
            ],
        )

        snapshot = self._build_snapshot(now=now, coordinator_pids=[1234])
        output = render_dashboard(snapshot, width=120, height=60)

        self.assertIn("intervention: recommended", output)
        self.assertIn("state=active | coord=up", output)
        self.assertIn("work: active", output)
        self.assertIn("freshest run output: 30s", output)
        self.assertIn("active:", output)
        self.assertIn("run 45-build-dashboard run=2m last-event=30s events=1", output)
        self.assertIn("queue (1 total):", output)
        self.assertIn("blk 46-follow-up-fix age=2m why=plant_busy", output)
        self.assertIn("15:08:00 goal 45-build-dashboard dispatched->running", output)
        self.assertIn("15:07:30 run 44-design-dashboard-r1 finished success", output)
        self.assertIn("latest 44-design-dashboard-r1 in=876.7k out=13.3k", output)

    def test_render_dashboard_surfaces_dashboard_invocation_events(self) -> None:
        now = "2026-03-23T02:00:02Z"

        self._write_jsonl(
            self.root / "events" / "coordinator.jsonl",
            [
                {
                    "ts": "2026-03-23T02:00:00Z",
                    "type": "DashboardInvocationStarted",
                    "actor": "operator",
                    "dashboard_invocation_id": "dash-20260323020000-abcd",
                    "dashboard_mode": "once",
                    "dashboard_refresh_seconds": 2.0,
                    "dashboard_tty": False,
                },
                {
                    "ts": "2026-03-23T02:00:01Z",
                    "type": "DashboardInvocationFinished",
                    "actor": "operator",
                    "dashboard_invocation_id": "dash-20260323020000-abcd",
                    "dashboard_mode": "once",
                    "dashboard_refresh_seconds": 2.0,
                    "dashboard_tty": False,
                    "dashboard_outcome": "success",
                    "dashboard_render_count": 1,
                    "dashboard_wall_ms": 1000,
                    "dashboard_record_path": (
                        "dashboard/invocations/dash-20260323020000-abcd.json"
                    ),
                },
            ],
        )

        snapshot = build_snapshot(self.root, now=now)
        output = render_dashboard(snapshot, width=220, height=60)

        self.assertIn("02:00:01 dashboard finished success wall=1000ms renders=1", output)
        self.assertIn("02:00:00 dashboard started mode=once", output)

    def test_render_dashboard_surfaces_hidden_lifecycle_and_evaluate_state(self) -> None:
        now = "2026-03-20T15:10:00Z"

        self._write_goal(
            {
                "id": "46-finished-eval",
                "status": "closed",
                "submitted_at": "2026-03-20T15:00:00Z",
                "type": "evaluate",
                "assigned_to": "gardener",
                "priority": 5,
                "closed_reason": "success",
            }
        )
        self._write_goal(
            {
                "id": "60-chat-hop",
                "status": "running",
                "submitted_at": "2026-03-20T15:09:00Z",
                "type": "converse",
                "assigned_to": "gardener",
                "priority": 8,
                "conversation_id": "1-hello",
            }
        )
        self._write_goal(
            {
                "id": "61-live-eval",
                "status": "running",
                "submitted_at": "2026-03-20T15:08:00Z",
                "type": "evaluate",
                "assigned_to": "pruner",
                "priority": 6,
            }
        )

        self._write_run(
            {
                "id": "60-chat-hop-r1",
                "goal": "60-chat-hop",
                "plant": "gardener",
                "status": "running",
                "started_at": "2026-03-20T15:09:10Z",
                "driver": "codex",
                "model": "gpt-5.4",
                "lifecycle": {
                    "phase": "checkpointing-hop",
                    "updated_at": "2026-03-20T15:09:50Z",
                },
            },
            last_output_at="2026-03-20T15:09:50Z",
        )
        self._write_run(
            {
                "id": "61-live-eval-r1",
                "goal": "61-live-eval",
                "plant": "pruner",
                "status": "running",
                "started_at": "2026-03-20T15:08:30Z",
                "driver": "codex",
                "model": "gpt-5.4",
            },
            last_output_at="2026-03-20T15:09:40Z",
        )

        self._write_conversation(
            "1-hello",
            {
                "id": "1-hello",
                "status": "open",
                "channel": "filesystem",
                "channel_ref": "inbox/operator",
                "presence_model": "async",
                "participants": ["operator", "garden"],
                "started_at": "2026-03-20T05:46:26Z",
                "last_activity_at": "2026-03-20T15:09:55Z",
                "context_at": "2026-03-20T15:09:55Z",
                "session_id": "session-1",
                "compacted_through": None,
                "session_ordinal": 1,
                "session_turns": 6,
                "session_started_at": "2026-03-20T14:18:50Z",
                "checkpoint_count": 1,
                "last_checkpoint_id": "ckpt-1",
                "last_checkpoint_at": "2026-03-20T14:17:02Z",
                "last_turn_mode": "resumed",
                "last_turn_run_id": "57-prior-turn-r1",
                "pending_hop": None,
                "last_pressure": {
                    "band": "medium",
                    "score": 0.6,
                    "needs_hop": False,
                },
            },
        )

        self._write_jsonl(
            self.root / "events" / "coordinator.jsonl",
            [
                {
                    "ts": "2026-03-20T15:08:30Z",
                    "type": "RunStarted",
                    "goal": "61-live-eval",
                    "run": "61-live-eval-r1",
                },
                {
                    "ts": "2026-03-20T15:09:10Z",
                    "type": "RunStarted",
                    "goal": "60-chat-hop",
                    "run": "60-chat-hop-r1",
                },
                {
                    "ts": "2026-03-20T15:09:50Z",
                    "type": "GoalClosed",
                    "actor": "system",
                    "goal": "46-finished-eval",
                    "goal_reason": "success",
                },
            ],
        )

        snapshot = build_snapshot(self.root, now=now)
        output = render_dashboard(snapshot, width=220, height=60)

        entries = {entry.goal_id: entry for entry in snapshot.active_work}
        self.assertEqual(entries["60-chat-hop"].run_lifecycle_phase, "checkpointing-hop")

        conversations = {entry.conversation_id: entry for entry in snapshot.conversations}
        self.assertEqual(conversations["1-hello"].active_phase, "checkpointing-hop")
        self.assertEqual(conversations["1-hello"].active_run_id, "60-chat-hop-r1")

        self.assertEqual(snapshot.recent_activity[0].goal_type, "evaluate")

        self.assertIn("run chat 60-chat-hop phase=checkpointing-hop", output)
        self.assertIn("run eval 61-live-eval run=1m", output)
        self.assertIn("active=checkpointing-hop", output)
        self.assertIn("15:09:50 eval 46-finished-eval closed success", output)

    def test_build_snapshot_ignores_orphaned_and_duplicate_run_activity(self) -> None:
        now = "2026-03-20T15:10:00Z"

        self._write_run(
            {
                "id": "45-build-dashboard-r1",
                "goal": "45-build-dashboard",
                "plant": "gardener",
                "status": "running",
                "started_at": "2026-03-20T15:08:00Z",
                "driver": "codex",
                "model": "gpt-5.4",
            },
            last_output_at="2026-03-20T15:09:30Z",
        )

        self._write_jsonl(
            self.root / "events" / "coordinator.jsonl",
            [
                {
                    "ts": "2026-03-20T15:07:00Z",
                    "type": "GoalSubmitted",
                    "actor": "garden",
                    "goal": "50-ready-fix",
                    "goal_type": "fix",
                },
                {
                    "ts": "2026-03-20T15:08:00Z",
                    "type": "RunStarted",
                    "goal": "45-build-dashboard",
                    "run": "45-build-dashboard-r1",
                },
                {
                    "ts": "2026-03-20T15:08:30Z",
                    "type": "RunStarted",
                    "goal": "45-build-dashboard",
                    "run": "45-build-dashboard-r1",
                },
                {
                    "ts": "2026-03-20T15:09:40Z",
                    "type": "RunFinished",
                    "goal": "ghost-goal",
                    "run": "ghost-run-r1",
                    "run_reason": "success",
                },
            ],
        )

        snapshot = build_snapshot(self.root, now=now)

        self.assertEqual(snapshot.cycle_health.last_coordinator_event_age_seconds, 120)
        self.assertEqual(
            [(event.event_type, event.run_id, event.goal_id) for event in snapshot.recent_activity],
            [
                ("RunStarted", "45-build-dashboard-r1", "45-build-dashboard"),
                ("GoalSubmitted", None, "50-ready-fix"),
            ],
        )

    def test_recent_activity_labels_post_reply_hop_events(self) -> None:
        now = "2026-03-20T15:10:00Z"

        self._write_goal(
            {
                "id": "60-chat-hop",
                "status": "closed",
                "submitted_at": "2026-03-20T15:09:00Z",
                "type": "converse",
                "assigned_to": "gardener",
                "priority": 8,
                "conversation_id": "1-hello",
                "post_reply_hop": {
                    "requested_at": "2026-03-20T15:09:00Z",
                    "requested_by": "system",
                    "reason": "automatic pressure handoff",
                    "automatic": True,
                },
                "closed_reason": "success",
            }
        )
        self._write_run(
            {
                "id": "60-chat-hop-r1",
                "goal": "60-chat-hop",
                "plant": "gardener",
                "status": "success",
                "started_at": "2026-03-20T15:09:10Z",
                "completed_at": "2026-03-20T15:09:50Z",
                "driver": "codex",
                "model": "gpt-5.4",
                "cost": {
                    "source": "provider",
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_tokens": 10,
                },
            }
        )
        self._write_jsonl(
            self.root / "events" / "coordinator.jsonl",
            [
                {
                    "ts": "2026-03-20T15:09:00Z",
                    "type": "GoalSubmitted",
                    "actor": "gardener",
                    "goal": "60-chat-hop",
                    "goal_type": "converse",
                    "goal_subtype": "post_reply_hop",
                    "conversation_id": "1-hello",
                },
                {
                    "ts": "2026-03-20T15:09:10Z",
                    "type": "RunStarted",
                    "actor": "system",
                    "goal": "60-chat-hop",
                    "run": "60-chat-hop-r1",
                    "goal_subtype": "post_reply_hop",
                    "conversation_id": "1-hello",
                },
                {
                    "ts": "2026-03-20T15:09:50Z",
                    "type": "RunFinished",
                    "actor": "system",
                    "goal": "60-chat-hop",
                    "run": "60-chat-hop-r1",
                    "run_reason": "success",
                    "goal_subtype": "post_reply_hop",
                    "conversation_id": "1-hello",
                    "hop_outcome": "checkpointed",
                    "checkpoint_id": "ckpt-1",
                },
            ],
        )

        snapshot = build_snapshot(self.root, now=now)
        output = render_dashboard(snapshot, width=220, height=60)

        self.assertEqual(snapshot.recent_activity[0].goal_subtype, "post_reply_hop")
        self.assertIn("hop 1-hello finished checkpointed ckpt-1", output)
        self.assertIn("hop 1-hello started", output)
        self.assertIn("hop 1-hello queued", output)

    def test_recent_activity_labels_dedicated_conversation_hop_and_checkpoint_events(self) -> None:
        now = "2026-03-20T15:10:00Z"

        self._write_goal(
            {
                "id": "61-chat-turn",
                "status": "closed",
                "submitted_at": "2026-03-20T15:08:30Z",
                "type": "converse",
                "assigned_to": "gardener",
                "priority": 8,
                "conversation_id": "1-hello",
                "closed_reason": "success",
            }
        )
        self._write_run(
            {
                "id": "61-chat-turn-r1",
                "goal": "61-chat-turn",
                "plant": "gardener",
                "status": "success",
                "started_at": "2026-03-20T15:08:35Z",
                "completed_at": "2026-03-20T15:08:55Z",
                "driver": "codex",
                "model": "gpt-5.4",
                "cost": {"source": "provider", "input_tokens": 50, "output_tokens": 20},
            }
        )
        self._write_goal(
            {
                "id": "62-chat-hop",
                "status": "closed",
                "submitted_at": "2026-03-20T15:08:56Z",
                "type": "converse",
                "assigned_to": "gardener",
                "priority": 8,
                "conversation_id": "1-hello",
                "post_reply_hop": {
                    "requested_at": "2026-03-20T15:08:56Z",
                    "requested_by": "system",
                    "reason": "automatic pressure handoff",
                    "automatic": True,
                },
                "closed_reason": "success",
            }
        )
        self._write_run(
            {
                "id": "62-chat-hop-r1",
                "goal": "62-chat-hop",
                "plant": "gardener",
                "status": "success",
                "started_at": "2026-03-20T15:08:57Z",
                "completed_at": "2026-03-20T15:09:30Z",
                "driver": "codex",
                "model": "gpt-5.4",
                "cost": {"source": "provider", "input_tokens": 50, "output_tokens": 20},
            }
        )
        self._write_jsonl(
            self.root / "events" / "coordinator.jsonl",
            [
                {
                    "ts": "2026-03-20T15:08:56Z",
                    "type": "GoalSubmitted",
                    "actor": "gardener",
                    "goal": "62-chat-hop",
                    "goal_type": "converse",
                    "goal_subtype": "post_reply_hop",
                    "conversation_id": "1-hello",
                },
                {
                    "ts": "2026-03-20T15:08:56Z",
                    "type": "ConversationHopQueued",
                    "actor": "system",
                    "goal": "61-chat-turn",
                    "run": "61-chat-turn-r1",
                    "conversation_id": "1-hello",
                    "hop_goal": "62-chat-hop",
                    "hop_requested_by": "system",
                    "hop_reason": "automatic pressure handoff",
                    "hop_automatic": True,
                },
                {
                    "ts": "2026-03-20T15:08:57Z",
                    "type": "RunStarted",
                    "actor": "system",
                    "goal": "62-chat-hop",
                    "run": "62-chat-hop-r1",
                    "goal_subtype": "post_reply_hop",
                    "conversation_id": "1-hello",
                },
                {
                    "ts": "2026-03-20T15:09:15Z",
                    "type": "ConversationCheckpointWritten",
                    "actor": "system",
                    "goal": "62-chat-hop",
                    "run": "62-chat-hop-r1",
                    "conversation_id": "1-hello",
                    "checkpoint_id": "ckpt-20260320150915-ab12",
                    "checkpoint_requested_by": "system",
                    "checkpoint_reason": "automatic pressure handoff",
                    "checkpoint_summary_path": "checkpoints/ckpt-20260320150915-ab12.md",
                    "source_message_id": "msg-20260320150855-gar-ab12",
                    "source_session_ordinal": 3,
                    "source_session_turns": 8,
                    "checkpoint_count": 4,
                },
                {
                    "ts": "2026-03-20T15:09:18Z",
                    "type": "RunFinished",
                    "actor": "system",
                    "goal": "62-chat-hop",
                    "run": "62-chat-hop-r1",
                    "run_reason": "success",
                    "goal_subtype": "post_reply_hop",
                    "conversation_id": "1-hello",
                    "hop_outcome": "checkpointed",
                    "checkpoint_id": "ckpt-20260320150915-ab12",
                },
                {
                    "ts": "2026-03-20T15:09:20Z",
                    "type": "ConversationHopQueueFailed",
                    "actor": "system",
                    "goal": "61-chat-turn",
                    "run": "61-chat-turn-r1",
                    "conversation_id": "1-hello",
                    "hop_requested_by": "system",
                    "hop_reason": "automatic pressure handoff",
                    "hop_automatic": True,
                    "detail": "post-reply hop goal submission failed: goal store unavailable",
                },
            ],
        )

        snapshot = build_snapshot(self.root, now=now)
        output = render_dashboard(snapshot, width=220, height=60)

        self.assertEqual(output.count("hop 1-hello queued"), 1)
        self.assertIn("checkpoint 1-hello wrote ckpt-20260320150915-ab12", output)
        self.assertIn("hop 1-hello queued", output)
        self.assertIn("hop 1-hello queue-failed", output)
        self.assertNotIn("hop 1-hello finished checkpointed", output)

    def test_build_snapshot_keeps_long_running_work_active_from_recent_run_output(self) -> None:
        now = "2026-03-20T15:10:00Z"
        self._write_goal(
            {
                "id": "71-health-check",
                "status": "running",
                "submitted_at": "2026-03-20T15:00:00Z",
                "type": "fix",
                "assigned_to": "gardener",
                "priority": 8,
            }
        )
        self._write_run(
            {
                "id": "71-health-check-r1",
                "goal": "71-health-check",
                "plant": "gardener",
                "status": "running",
                "started_at": "2026-03-20T15:00:30Z",
                "driver": "codex",
                "model": "gpt-5.4",
            },
            last_output_at="2026-03-20T15:09:40Z",
        )
        self._write_jsonl(
            self.root / "events" / "coordinator.jsonl",
            [
                {
                    "ts": "2026-03-20T15:02:00Z",
                    "type": "RunStarted",
                    "goal": "71-health-check",
                    "run": "71-health-check-r1",
                }
            ],
        )

        snapshot = self._build_snapshot(now=now, coordinator_pids=[1234])
        output = render_dashboard(snapshot, width=220, height=60)

        by_subject = {(alert.subject, alert.identifier): alert for alert in snapshot.alerts}

        self.assertEqual(snapshot.state, "active")
        self.assertEqual(snapshot.cycle_health.coordinator_process_status, "up")
        self.assertEqual(snapshot.cycle_health.work_status, "active")
        self.assertEqual(snapshot.cycle_health.coordinator_process_count, 1)
        self.assertEqual(snapshot.cycle_health.freshest_run_output_age_seconds, 20)
        self.assertEqual(snapshot.cycle_health.last_coordinator_event_age_seconds, 480)
        self.assertNotIn(("cycle", "coordinator"), by_subject)
        self.assertIn("state=active | coord=up", output)
        self.assertIn("work: active", output)
        self.assertIn("freshest run output: 20s", output)

    def test_build_snapshot_keeps_true_idle_idle_even_with_stale_event(self) -> None:
        now = "2026-03-20T15:10:00Z"
        stale_at = "2026-03-20T15:03:00Z"

        log_path = self.root / "events" / "coordinator.jsonl"
        self._write_jsonl(
            log_path,
            [
                {
                    "ts": stale_at,
                    "type": "GoalSubmitted",
                    "actor": "operator",
                    "goal": "70-make-it-so",
                    "goal_type": "converse",
                }
            ],
        )
        epoch = _to_epoch(stale_at)
        os.utime(log_path, (epoch, epoch))

        snapshot = self._build_snapshot(now=now, coordinator_pids=[4321])
        by_subject = {(alert.subject, alert.identifier): alert for alert in snapshot.alerts}

        self.assertEqual(snapshot.state, "idle")
        self.assertEqual(snapshot.cycle_health.coordinator_process_status, "up")
        self.assertEqual(snapshot.cycle_health.work_status, "idle")
        self.assertNotIn(("cycle", "coordinator"), by_subject)

    def test_build_snapshot_alerts_on_stuck_coordinator_with_eligible_work(self) -> None:
        now = "2026-03-20T15:10:00Z"
        stale_at = "2026-03-20T15:03:00Z"

        self._write_goal(
            {
                "id": "71-health-followup",
                "status": "queued",
                "submitted_at": "2026-03-20T15:08:00Z",
                "type": "fix",
                "assigned_to": "gardener",
                "priority": 8,
            }
        )
        self._write_jsonl(
            self.root / "events" / "coordinator.jsonl",
            [
                {
                    "ts": stale_at,
                    "type": "GoalSubmitted",
                    "actor": "operator",
                    "goal": "70-make-it-so",
                    "goal_type": "converse",
                }
            ],
        )

        snapshot = self._build_snapshot(now=now, coordinator_pids=[4321])
        cycle_alert = next(
            alert
            for alert in snapshot.alerts
            if alert.subject == "cycle" and alert.identifier == "coordinator"
        )

        self.assertEqual(snapshot.state, "stuck")
        self.assertEqual(snapshot.cycle_health.coordinator_process_status, "up")
        self.assertEqual(snapshot.cycle_health.work_status, "stuck")
        self.assertEqual(cycle_alert.severity, "warning")
        self.assertIn("eligible work has no recent coordinator activity", cycle_alert.reason)

    def test_build_snapshot_alerts_on_down_coordinator_with_open_work(self) -> None:
        now = "2026-03-20T15:10:00Z"
        stale_at = "2026-03-20T15:03:00Z"

        self._write_goal(
            {
                "id": "71-health-followup",
                "status": "queued",
                "submitted_at": "2026-03-20T15:08:00Z",
                "type": "fix",
                "assigned_to": "gardener",
                "priority": 8,
            }
        )
        log_path = self.root / "events" / "coordinator.jsonl"
        self._write_jsonl(
            log_path,
            [
                {
                    "ts": stale_at,
                    "type": "GoalSubmitted",
                    "actor": "operator",
                    "goal": "70-make-it-so",
                    "goal_type": "converse",
                }
            ],
        )
        epoch = _to_epoch(stale_at)
        os.utime(log_path, (epoch, epoch))

        snapshot = self._build_snapshot(now=now, coordinator_pids=[])
        cycle_alert = next(
            alert
            for alert in snapshot.alerts
            if alert.subject == "cycle" and alert.identifier == "coordinator"
        )

        self.assertEqual(snapshot.state, "stuck")
        self.assertEqual(snapshot.cycle_health.coordinator_process_status, "down")
        self.assertEqual(snapshot.cycle_health.work_status, "stuck")
        self.assertEqual(cycle_alert.severity, "critical")
        self.assertIn("process absent", cycle_alert.reason)

    def test_validate_snapshot_tree_rejects_invalid_shape_and_schema_violation(self) -> None:
        invalid_shape = validate_snapshot_tree(["not", "an", "object"])
        self.assertFalse(invalid_shape.ok)
        self.assertEqual(invalid_shape.reason, "INVALID_SHAPE")
        self.assertEqual(
            invalid_shape.detail,
            "Dashboard snapshot tree must be a JSON object",
        )

        snapshot_tree = build_snapshot_tree(self.root, now="2026-03-20T15:10:00Z")
        snapshot_tree.pop("cost")
        invalid_snapshot = validate_snapshot_tree(snapshot_tree)
        self.assertFalse(invalid_snapshot.ok)
        self.assertEqual(invalid_snapshot.reason, "INVALID_DASHBOARD_SNAPSHOT")
        self.assertIn("<root>:", invalid_snapshot.detail)
        self.assertIn("cost", invalid_snapshot.detail)

    def test_validate_snapshot_tree_rejects_when_validator_unavailable(self) -> None:
        with mock.patch(
            "system.dashboard._dashboard_snapshot_validator",
            side_effect=RuntimeError("missing validator"),
        ):
            result = validate_snapshot_tree({})

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "DASHBOARD_VALIDATOR_UNAVAILABLE")
        self.assertIn("missing validator", result.detail)

    def test_validate_render_tree_rejects_invalid_shape_schema_violation_and_layout_mismatch(self) -> None:
        invalid_shape = validate_render_tree(["not", "an", "object"])
        self.assertFalse(invalid_shape.ok)
        self.assertEqual(invalid_shape.reason, "INVALID_SHAPE")
        self.assertEqual(
            invalid_shape.detail,
            "Dashboard render tree must be a JSON object",
        )

        snapshot = build_snapshot(self.root, now="2026-03-20T15:10:00Z")
        render_tree = build_render_tree(snapshot, width=220, height=60)
        render_tree["panels"].pop("alerts")
        invalid_render = validate_render_tree(render_tree)
        self.assertFalse(invalid_render.ok)
        self.assertEqual(invalid_render.reason, "INVALID_DASHBOARD_RENDER")
        self.assertIn("panels", invalid_render.detail)
        self.assertIn("alerts", invalid_render.detail)

        row_mismatch = build_render_tree(snapshot, width=220, height=60)
        row_mismatch["rows"][0]["panel_keys"] = ["cycle_health", "cost"]
        invalid_rows = validate_render_tree(row_mismatch)
        self.assertFalse(invalid_rows.ok)
        self.assertEqual(invalid_rows.reason, "INVALID_DASHBOARD_RENDER")
        self.assertIn("rows: expected", invalid_rows.detail)

    def test_validate_render_tree_rejects_when_validator_unavailable(self) -> None:
        with mock.patch(
            "system.dashboard._dashboard_render_validator",
            side_effect=RuntimeError("missing validator"),
        ):
            result = validate_render_tree({})

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "DASHBOARD_RENDER_VALIDATOR_UNAVAILABLE")
        self.assertIn("missing validator", result.detail)


if __name__ == "__main__":
    unittest.main()
