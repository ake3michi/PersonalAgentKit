import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from system.active_threads import read_active_threads, write_active_threads
from system.validate import validate_active_threads


def _sample_active_threads() -> dict:
    return {
        "schema_version": 1,
        "captured_at": "2026-03-23T03:00:00Z",
        "captured_by_run": "285-after-goal-281-finishes-design-and-land-r1",
        "plant": "gardener",
        "summary": "Three active threads are currently being tracked.",
        "threads": [
            {
                "id": "backlog-closeout",
                "title": "Finish the Definition-of-Done backlog",
                "state": "active",
                "priority": "primary",
                "last_changed_at": "2026-03-23T02:39:38Z",
                "summary": "Only the base-tend clean proof remains before the later dashboard review.",
                "current_focus": "Item 1's deferred base-tend clean proof now leads the backlog.",
                "next_step": "Queue the standalone clean-environment proof for base tend metadata and lifecycle events.",
                "related_thread_ids": ["automatic-tend-contract"],
                "evidence": [
                    "runs/279-after-goal-277-finishes-perform-a-bounde-r1/evaluation.md"
                ],
            },
            {
                "id": "automatic-tend-contract",
                "title": "Track the narrowed automatic-tend contract",
                "state": "near_done",
                "priority": "secondary",
                "last_changed_at": "2026-03-23T02:45:20Z",
                "summary": "Goal 281 clarified the post-goal264 timing semantics without changing runtime behavior.",
                "current_focus": "Keep the explicit same-pass selected/claimed non-trigger rule legible.",
                "next_step": "Treat this as a watched policy thread unless new evidence reopens it.",
                "related_thread_ids": ["backlog-closeout"],
                "evidence": [
                    "runs/281-after-goal-279-finishes-update-the-autom-r1/last-message.md"
                ],
            },
        ],
        "recent_updates": [
            {
                "ts": "2026-03-23T02:39:38Z",
                "summary": "Run 279 closed the remaining post-goal264 coverage debt and put item 1 back in front.",
                "thread_ids": ["backlog-closeout", "automatic-tend-contract"],
                "evidence": [
                    "runs/279-after-goal-277-finishes-perform-a-bounde-r1/evaluation.md"
                ],
            }
        ],
    }


class ActiveThreadsValidationTests(unittest.TestCase):
    def _assert_rejection(self, data: dict, reason: str) -> None:
        result = validate_active_threads(data)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, reason)

    def test_validate_active_threads_accepts_valid_artifact(self) -> None:
        result = validate_active_threads(_sample_active_threads())
        self.assertTrue(result.ok, result.detail)

    def test_validate_active_threads_rejects_non_object_shape(self) -> None:
        result = validate_active_threads([])

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_ACTIVE_THREADS_SHAPE")

    def test_validate_active_threads_rejects_unknown_top_level_field(self) -> None:
        data = _sample_active_threads()
        data["unexpected"] = True

        self._assert_rejection(data, "UNKNOWN_ACTIVE_THREADS_FIELD")

    def test_validate_active_threads_rejects_missing_required_field(self) -> None:
        data = _sample_active_threads()
        del data["summary"]

        self._assert_rejection(data, "MISSING_REQUIRED_FIELD")

    def test_validate_active_threads_rejects_invalid_schema_version(self) -> None:
        data = _sample_active_threads()
        data["schema_version"] = 2

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_SCHEMA_VERSION")

    def test_validate_active_threads_rejects_invalid_timestamp(self) -> None:
        data = _sample_active_threads()
        data["captured_at"] = "2026-03-23 03:00:00"

        self._assert_rejection(data, "INVALID_TIMESTAMP")

    def test_validate_active_threads_rejects_invalid_run_id(self) -> None:
        data = _sample_active_threads()
        data["captured_by_run"] = "bad-run"

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_RUN")

    def test_validate_active_threads_rejects_invalid_plant_name(self) -> None:
        data = _sample_active_threads()
        data["plant"] = "Garden!"

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_PLANT")

    def test_validate_active_threads_rejects_empty_summary(self) -> None:
        data = _sample_active_threads()
        data["summary"] = ""

        self._assert_rejection(data, "EMPTY_ACTIVE_THREADS_SUMMARY")

    def test_validate_active_threads_rejects_non_object_thread(self) -> None:
        data = _sample_active_threads()
        data["threads"][0] = "not-a-thread"

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_THREAD")

    def test_validate_active_threads_rejects_unknown_thread_field(self) -> None:
        data = _sample_active_threads()
        data["threads"][0]["unexpected"] = True

        self._assert_rejection(data, "UNKNOWN_ACTIVE_THREADS_THREAD_FIELD")

    def test_validate_active_threads_rejects_invalid_thread_id(self) -> None:
        data = _sample_active_threads()
        data["threads"][0]["id"] = "Bad Thread"

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_THREAD_ID")

    def test_validate_active_threads_rejects_duplicate_thread_ids(self) -> None:
        data = _sample_active_threads()
        data["threads"][1]["id"] = "backlog-closeout"

        self._assert_rejection(data, "DUPLICATE_ACTIVE_THREAD_ID")

    def test_validate_active_threads_rejects_empty_thread_field(self) -> None:
        data = _sample_active_threads()
        data["threads"][0]["title"] = ""

        self._assert_rejection(data, "EMPTY_ACTIVE_THREADS_THREAD_FIELD")

    def test_validate_active_threads_rejects_invalid_thread_state(self) -> None:
        data = _sample_active_threads()
        data["threads"][0]["state"] = "done"

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_THREAD_STATE")

    def test_validate_active_threads_rejects_invalid_thread_priority(self) -> None:
        data = _sample_active_threads()
        data["threads"][0]["priority"] = "urgent"

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_THREAD_PRIORITY")

    def test_validate_active_threads_rejects_invalid_thread_relation_shape(self) -> None:
        data = _sample_active_threads()
        data["threads"][0]["related_thread_ids"] = "automatic-tend-contract"

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_THREAD_RELATION")

    def test_validate_active_threads_rejects_invalid_thread_evidence(self) -> None:
        data = _sample_active_threads()
        data["threads"][0]["evidence"] = []

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_THREAD_EVIDENCE")

    def test_validate_active_threads_rejects_self_referential_thread(self) -> None:
        data = _sample_active_threads()
        data["threads"][0]["related_thread_ids"] = ["backlog-closeout"]

        self._assert_rejection(data, "SELF_REFERENTIAL_ACTIVE_THREAD")

    def test_validate_active_threads_rejects_unknown_related_thread(self) -> None:
        data = _sample_active_threads()
        data["threads"][0]["related_thread_ids"] = ["missing-thread"]

        self._assert_rejection(data, "UNKNOWN_ACTIVE_THREAD_RELATION")

    def test_validate_active_threads_rejects_non_object_recent_update(self) -> None:
        data = _sample_active_threads()
        data["recent_updates"][0] = "not-an-update"

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_UPDATE")

    def test_validate_active_threads_rejects_unknown_recent_update_field(self) -> None:
        data = _sample_active_threads()
        data["recent_updates"][0]["unexpected"] = True

        self._assert_rejection(data, "UNKNOWN_ACTIVE_THREADS_UPDATE_FIELD")

    def test_validate_active_threads_rejects_empty_recent_update_summary(self) -> None:
        data = _sample_active_threads()
        data["recent_updates"][0]["summary"] = ""

        self._assert_rejection(data, "EMPTY_ACTIVE_THREADS_UPDATE_SUMMARY")

    def test_validate_active_threads_rejects_invalid_recent_update_thread_ids(self) -> None:
        data = _sample_active_threads()
        data["recent_updates"][0]["thread_ids"] = []

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_UPDATE_THREAD_IDS")

    def test_validate_active_threads_rejects_invalid_recent_update_evidence(self) -> None:
        data = _sample_active_threads()
        data["recent_updates"][0]["evidence"] = []

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_UPDATE_EVIDENCE")

    def test_validate_active_threads_accepts_recent_update_for_closed_thread(self) -> None:
        data = _sample_active_threads()
        data["recent_updates"][0]["thread_ids"] = ["missing-thread"]

        result = validate_active_threads(data)

        self.assertTrue(result.ok, result.detail)

    def test_validate_active_threads_rejects_invalid_recent_update_thread_id(self) -> None:
        data = _sample_active_threads()
        data["recent_updates"][0]["thread_ids"] = ["Bad Thread"]

        self._assert_rejection(data, "INVALID_ACTIVE_THREADS_UPDATE_THREAD_IDS")


class ActiveThreadsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.plants_dir = self.root / "plants"
        (self.plants_dir / "gardener" / "memory").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _load_event_schema(self) -> dict:
        schema_path = pathlib.Path(__file__).resolve().parent.parent / "schema" / "event.schema.json"
        return json.loads(schema_path.read_text(encoding="utf-8"))

    def _assert_event_schema_valid(self, payload: dict) -> None:
        validator = Draft202012Validator(
            self._load_event_schema(),
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

    def _read_event_log(self) -> list[dict]:
        events_path = self.root / "events" / "coordinator.jsonl"
        if not events_path.exists():
            return []
        return [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_write_and_read_active_threads_round_trip(self) -> None:
        data = _sample_active_threads()

        with patch.dict(os.environ, {}, clear=True):
            result = write_active_threads("gardener", data, _plants_dir=self.plants_dir)
            stored = read_active_threads("gardener", _plants_dir=self.plants_dir)
            events = self._read_event_log()

        self.assertTrue(result.ok, result.detail)
        self.assertEqual(stored, data)
        self.assertEqual(
            [event["type"] for event in events],
            ["ActiveThreadsRefreshStarted", "ActiveThreadsRefreshFinished"],
        )

        started, finished = events
        self._assert_event_schema_valid(started)
        self._assert_event_schema_valid(finished)
        self.assertEqual(started["actor"], "gardener")
        self.assertEqual(started["plant"], "gardener")
        self.assertEqual(started["goal"], "285-after-goal-281-finishes-design-and-land")
        self.assertEqual(started["run"], data["captured_by_run"])
        self.assertEqual(
            started["active_threads_path"],
            "plants/gardener/memory/active-threads.json",
        )
        self.assertEqual(finished["active_threads_outcome"], "success")

    def test_write_active_threads_rejects_mismatched_plant(self) -> None:
        data = _sample_active_threads()
        data["plant"] = "orchard"

        with patch.dict(os.environ, {}, clear=True):
            result = write_active_threads("gardener", data, _plants_dir=self.plants_dir)
            events = self._read_event_log()

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_ACTIVE_THREADS_PLANT")
        self.assertIsNone(read_active_threads("gardener", _plants_dir=self.plants_dir))
        self.assertEqual(
            [event["type"] for event in events],
            ["ActiveThreadsRefreshStarted", "ActiveThreadsRefreshFinished"],
        )

        started, finished = events
        self._assert_event_schema_valid(started)
        self._assert_event_schema_valid(finished)
        self.assertEqual(finished["active_threads_outcome"], "validation_rejected")
        self.assertEqual(finished["active_threads_reason"], "INVALID_ACTIVE_THREADS_PLANT")
        self.assertIn("does not match target plant", finished["detail"])

    def test_write_active_threads_records_io_error_outcome(self) -> None:
        data = _sample_active_threads()

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
        ):
            result = write_active_threads("gardener", data, _plants_dir=self.plants_dir)

        events = self._read_event_log()

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "IO_ERROR")
        self.assertIsNone(read_active_threads("gardener", _plants_dir=self.plants_dir))
        self.assertEqual(
            [event["type"] for event in events],
            ["ActiveThreadsRefreshStarted", "ActiveThreadsRefreshFinished"],
        )

        started, finished = events
        self._assert_event_schema_valid(started)
        self._assert_event_schema_valid(finished)
        self.assertEqual(finished["active_threads_outcome"], "io_error")
        self.assertEqual(finished["active_threads_reason"], "IO_ERROR")
        self.assertIn("disk full", finished["detail"])

    def test_write_active_threads_rejects_non_object_payload_without_crashing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = write_active_threads("gardener", [], _plants_dir=self.plants_dir)

        events = self._read_event_log()

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "INVALID_ACTIVE_THREADS_SHAPE")
        self.assertEqual(
            [event["type"] for event in events],
            ["ActiveThreadsRefreshStarted", "ActiveThreadsRefreshFinished"],
        )

        started, finished = events
        self._assert_event_schema_valid(started)
        self._assert_event_schema_valid(finished)
        self.assertNotIn("goal", started)
        self.assertNotIn("run", started)
        self.assertEqual(finished["active_threads_outcome"], "validation_rejected")
        self.assertEqual(finished["active_threads_reason"], "INVALID_ACTIVE_THREADS_SHAPE")


if __name__ == "__main__":
    unittest.main()
