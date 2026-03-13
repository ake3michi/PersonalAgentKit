#!/usr/bin/env python3
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from runner.transcript import normalize_transcript_entries, render_transcript


def assert_contains(text: str, needle: str, message: str):
    if needle not in text:
        raise AssertionError(f"{message}: missing {needle!r}")


def build_meta(driver: str) -> dict:
    return {
        "run_id": f"run-{driver}",
        "goal_file": "goals/001-demo.md",
        "status": "success",
        "started_at": "2026-03-12T00:00:00Z",
        "completed_at": "2026-03-12T00:01:00Z",
        "driver": driver,
        "cost": {
            "actual_usd": None,
            "estimated_usd": 0.01,
        },
    }


def verify_codex_contract():
    events = [
        {"type": "item.started", "item": {"id": "todo1", "type": "todo_list", "items": [{"text": "Inspect", "completed": False}]}},
        {"type": "item.completed", "item": {"id": "msg1", "type": "agent_message", "text": "Inspecting the workspace."}},
        {
            "type": "item.completed",
            "item": {
                "id": "cmd1",
                "type": "command_execution",
                "command": "/bin/bash -lc 'printf \"hello\\n\"'",
                "aggregated_output": "hello\n",
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "change1",
                "type": "file_change",
                "changes": [{"path": "/tmp/demo.txt", "kind": "update"}],
                "status": "completed",
            },
        },
        {"type": "item.updated", "item": {"id": "todo1", "type": "todo_list", "items": [{"text": "Inspect", "completed": True}]}},
        {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 7}},
    ]
    entries = normalize_transcript_entries(events=events, driver="codex")
    assert [entry["kind"] for entry in entries] == [
        "todo_list",
        "assistant_message",
        "tool_activity",
        "file_change",
        "todo_list",
        "unrendered_event",
    ]
    assert entries[2]["tool_name"] == "command_execution"
    assert entries[3]["changes"][0]["path"] == "/tmp/demo.txt"
    assert entries[5]["raw_event_type"] == "turn.completed"

    transcript = render_transcript(meta=build_meta("codex"), entries=entries)
    assert_contains(transcript, "## Assistant Message", "codex transcript should render assistant messages")
    assert_contains(transcript, "## Tool Activity", "codex transcript should render tool activity")
    assert_contains(transcript, "- Tool: `command_execution`", "codex transcript should preserve command execution identity")
    assert_contains(transcript, "Output summary (from `aggregated_output`)", "codex transcript should label output summaries")
    assert_contains(transcript, "## File Change", "codex transcript should render file changes")
    assert_contains(transcript, "## Todo List Update", "codex transcript should render todo snapshots")
    assert_contains(transcript, "## Unrendered Event", "codex transcript should surface unsupported raw events")
    assert_contains(transcript, "- Raw event type: `turn.completed`", "placeholder should name the raw event type")
    assert_contains(
        transcript,
        "Coverage gap: this event is not rendered here. See `events.jsonl` for the raw record.",
        "placeholder should point readers back to raw evidence",
    )


def verify_claude_contract():
    events = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hidden"},
                    {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "/tmp/demo.txt"}},
                    {"type": "text", "text": "Reading the file now."},
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "line 1\nline 2\n"}
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "The file is present and readable."},
                ],
            },
        },
        {"type": "system", "subtype": "init", "tools": ["Read"]},
        {"type": "result", "result": "done"},
    ]
    entries = normalize_transcript_entries(events=events, driver="claude")
    assert [entry["kind"] for entry in entries] == [
        "tool_activity",
        "assistant_message",
        "assistant_message",
        "unrendered_event",
        "unrendered_event",
    ]
    assert entries[0]["tool_name"] == "Read"
    assert entries[0]["result_text"] == "line 1\nline 2\n"
    assert entries[3]["raw_event_type"] == "system"
    assert entries[4]["raw_event_type"] == "result"

    transcript = render_transcript(meta=build_meta("claude"), entries=entries)
    assert_contains(transcript, "## Assistant Message", "claude transcript should render assistant messages")
    assert_contains(transcript, "## Tool Activity", "claude transcript should render tool activity")
    assert_contains(transcript, "- Tool: `Read`", "claude transcript should preserve tool name")
    assert_contains(transcript, "Output summary (from `tool_result`)", "claude transcript should label tool-result summaries")
    assert_contains(transcript, "## Unrendered Event", "claude transcript should surface coverage gaps")
    assert_contains(transcript, "- Raw event type: `system`", "claude placeholder should name the raw event type")
    if "## File Change" in transcript or "## Todo List Update" in transcript:
        raise AssertionError("claude transcript should not invent file_change or todo_list entries without first-class evidence")


def verify_unknown_event_placeholder_without_payload_claims():
    events = [
        {"type": "item.completed", "item": {"id": "msg1", "type": "agent_message", "text": "Still rendering known events."}},
        {"type": "mystery.event", "payload": {"secret": "opaque"}},
    ]

    entries = normalize_transcript_entries(events=events, driver="codex")
    assert [entry["kind"] for entry in entries] == ["assistant_message", "unrendered_event"]
    assert entries[1]["raw_event_type"] == "mystery.event"

    transcript = render_transcript(meta=build_meta("codex"), entries=entries)
    assert_contains(transcript, "Still rendering known events.", "recognized events should still render normally")
    assert_contains(transcript, "- Raw event type: `mystery.event`", "unknown raw event should be surfaced")
    if "opaque" in transcript or "secret" in transcript:
        raise AssertionError("placeholder should not pretend to understand or restate the raw payload")


def main():
    verify_codex_contract()
    verify_claude_contract()
    verify_unknown_event_placeholder_without_payload_claims()
    print("verify_run_transcript_normalization: ok")


if __name__ == "__main__":
    main()
