import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import Draft202012Validator

from system import cli
from system.validate import validate_dashboard_invocation


class DashboardInvocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name) / "garden"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _dashboard_args(self, *, refresh: float = 2.0, once: bool = True) -> SimpleNamespace:
        return SimpleNamespace(root=str(self.root), refresh=refresh, once=once)

    def _load_schema(self) -> dict:
        schema_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "schema"
            / "dashboard-invocation.schema.json"
        )
        return json.loads(schema_path.read_text(encoding="utf-8"))

    def _assert_schema_valid(self, payload: dict) -> None:
        validator = Draft202012Validator(
            self._load_schema(),
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

    def _valid_record(self) -> dict:
        return {
            "id": "dash-20260323020000-abcd",
            "actor": "operator",
            "started_at": "2026-03-23T02:00:00Z",
            "completed_at": "2026-03-23T02:00:01Z",
            "root": str(self.root),
            "mode": "once",
            "refresh_seconds": 2.0,
            "tty": False,
            "render_count": 1,
            "outcome": "success",
            "cost": {
                "source": "measured",
                "wall_ms": 1000,
            },
        }

    def test_cmd_dashboard_records_once_invocation_event_and_cost(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "system.dashboard_invocations._now_utc",
                side_effect=["2026-03-23T02:00:00Z", "2026-03-23T02:00:01Z"],
            ),
            patch("system.dashboard_invocations._random_suffix", return_value="abcd"),
            patch.object(cli.shutil, "get_terminal_size", return_value=os.terminal_size((120, 40))),
            contextlib.redirect_stdout(stdout),
        ):
            cli.cmd_dashboard(self._dashboard_args())

        output = stdout.getvalue()
        self.assertIn("Cycle Health", output)
        self.assertIn("Recent Activity", output)

        events_path = self.root / "events" / "coordinator.jsonl"
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        expected_actor = os.getenv("PAK2_CURRENT_PLANT") or "operator"
        self.assertEqual(
            [event["type"] for event in events],
            ["DashboardInvocationStarted", "DashboardInvocationFinished"],
        )
        started, finished = events
        self.assertEqual(started["actor"], expected_actor)
        self.assertEqual(started["dashboard_invocation_id"], "dash-20260323020000-abcd")
        self.assertEqual(started["dashboard_mode"], "once")
        self.assertEqual(started["dashboard_refresh_seconds"], 2.0)
        self.assertFalse(started["dashboard_tty"])
        self.assertEqual(finished["actor"], expected_actor)
        self.assertEqual(finished["dashboard_outcome"], "success")
        self.assertEqual(finished["dashboard_render_count"], 1)
        self.assertEqual(finished["dashboard_wall_ms"], 1000)

        record_path = self.root / finished["dashboard_record_path"]
        self.assertEqual(
            record_path,
            self.root / "dashboard" / "invocations" / "dash-20260323020000-abcd.json",
        )
        self.assertTrue(record_path.exists())

        record = json.loads(record_path.read_text(encoding="utf-8"))
        result = validate_dashboard_invocation(record)
        self.assertTrue(result.ok, result)
        self._assert_schema_valid(record)
        self.assertEqual(record["actor"], expected_actor)
        self.assertEqual(record["cost"]["wall_ms"], 1000)
        self.assertEqual(record["render_count"], 1)
        self.assertEqual(record["outcome"], "success")
        self.assertEqual(record["mode"], "once")
        self.assertFalse(record["tty"])

    def test_cmd_dashboard_records_split_runtime_root_invocation_under_runtime_root(self) -> None:
        (self.root / "PAK2.toml").write_text(
            "[runtime]\nroot = \".runtime\"\n",
            encoding="utf-8",
        )
        stdout = io.StringIO()
        with (
            patch(
                "system.dashboard_invocations._now_utc",
                side_effect=["2026-03-24T23:10:00Z", "2026-03-24T23:10:01Z"],
            ),
            patch("system.dashboard_invocations._random_suffix", return_value="wxyz"),
            patch.object(cli.shutil, "get_terminal_size", return_value=os.terminal_size((120, 40))),
            contextlib.redirect_stdout(stdout),
        ):
            cli.cmd_dashboard(self._dashboard_args())

        output = stdout.getvalue()
        self.assertIn("root=", output)

        events_path = self.root / ".runtime" / "events" / "coordinator.jsonl"
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            [event["type"] for event in events],
            ["DashboardInvocationStarted", "DashboardInvocationFinished"],
        )
        finished = events[-1]
        self.assertEqual(
            finished["dashboard_record_path"],
            "dashboard/invocations/dash-20260324231000-wxyz.json",
        )

        record_path = self.root / ".runtime" / finished["dashboard_record_path"]
        self.assertTrue(record_path.exists())
        self.assertFalse(
            (self.root / "dashboard" / "invocations" / "dash-20260324231000-wxyz.json").exists()
        )

        record = json.loads(record_path.read_text(encoding="utf-8"))
        result = validate_dashboard_invocation(record)
        self.assertTrue(result.ok, result)
        self._assert_schema_valid(record)

    def test_validate_dashboard_invocation_rejects_non_object(self) -> None:
        result = validate_dashboard_invocation(["not", "an", "object"])
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_SHAPE")

    def test_validate_dashboard_invocation_rejects_unknown_field(self) -> None:
        record = self._valid_record()
        record["unexpected"] = True

        result = validate_dashboard_invocation(record)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "UNKNOWN_DASHBOARD_INVOCATION_FIELD")

    def test_validate_dashboard_invocation_rejects_missing_required_field(self) -> None:
        record = self._valid_record()
        del record["cost"]

        result = validate_dashboard_invocation(record)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "MISSING_REQUIRED_FIELD")
        self.assertIn("cost", result.detail)

    def test_validate_dashboard_invocation_rejects_invalid_payload(self) -> None:
        record = self._valid_record()
        record["render_count"] = 0

        result = validate_dashboard_invocation(record)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_DASHBOARD_INVOCATION")


if __name__ == "__main__":
    unittest.main()
