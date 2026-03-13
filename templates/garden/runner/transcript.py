from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runner.host import available_plugins


def parse_events(events_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not events_path.exists():
        return events
    for line in events_path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def load_meta(meta_path: Path) -> dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def detect_driver(driver: str | None, events: list[dict[str, Any]]) -> str | None:
    if isinstance(driver, str) and driver:
        return driver
    event_types = {event.get("type") for event in events}
    if "item.completed" in event_types or "item.updated" in event_types or "item.started" in event_types:
        return "codex"
    if "assistant" in event_types or "user" in event_types or "result" in event_types:
        return "claude"
    return None


def normalize_transcript_entries(*, events: list[dict[str, Any]], driver: str | None) -> list[dict[str, Any]]:
    resolved_driver = detect_driver(driver, events)
    if resolved_driver is None:
        return []
    plugin = available_plugins().get(resolved_driver)
    if plugin is None:
        return []
    return plugin.normalize_transcript(events=events)


def markdown_quote(text: str) -> str:
    return "\n".join(f"> {line}" for line in (text or "").splitlines()) or "> "


def code_block(language: str, text: str) -> str:
    return f"```{language}\n{text or ''}\n```"


def summarize_output(output: str, *, label: str) -> str:
    if not output:
        return "_Output summary: no aggregated output was captured._\n_Labeled summary. `events.jsonl` remains the raw evidence source._"

    lines = output.splitlines()
    excerpt = "\n".join(lines[:8])
    if len(lines) > 8:
        return (
            f"Output summary (truncated excerpt from `{label}`):\n\n"
            + code_block("text", excerpt)
            + "\n_Truncated. See `events.jsonl` for the full raw captured output._"
        )
    return (
        f"Output summary (from `{label}`):\n\n"
        + code_block("text", excerpt)
        + "\n_Labeled summary. `events.jsonl` remains the raw evidence source._"
    )


def render_entry(entry: dict[str, Any]) -> str:
    kind = entry.get("kind")
    if kind == "assistant_message":
        return "## Assistant Message\n\n" + markdown_quote(str(entry.get("text") or ""))

    if kind == "tool_activity":
        details = [
            "## Tool Activity",
            "",
            f"- Tool: `{entry.get('tool_name') or 'unknown'}`",
            f"- Status: `{entry.get('status') or 'unknown'}`",
        ]
        if entry.get("exit_code") is not None:
            details.append(f"- Exit code: `{entry.get('exit_code')}`")
        details.append("- Invocation:")
        details.append("")
        details.append(code_block("text", str(entry.get("invocation") or "")))
        details.append("")
        details.append(summarize_output(str(entry.get("result_text") or ""), label=str(entry.get("result_label") or "result")))
        return "\n".join(details)

    if kind == "file_change":
        changes = entry.get("changes")
        if not isinstance(changes, list):
            changes = []
        body = "\n".join(
            f"- `{str(change.get('kind') or 'update')}` `{str(change.get('path') or '')}`"
            for change in changes
            if isinstance(change, dict)
        )
        return "## File Change\n\n" + body

    if kind == "todo_list":
        items = entry.get("items")
        if not isinstance(items, list):
            items = []
        body = "\n".join(
            f"- [{'x' if bool(item.get('completed')) else ' '}] {str(item.get('text') or '')}"
            for item in items
            if isinstance(item, dict)
        )
        return "## Todo List Update\n\n" + body

    if kind == "unrendered_event":
        details = [
            "## Unrendered Event",
            "",
            f"- Raw event type: `{entry.get('raw_event_type') or 'unknown'}`",
        ]
        if entry.get("raw_item_type"):
            details.append(f"- Raw item type: `{entry.get('raw_item_type')}`")
        details.append("- Coverage gap: this event is not rendered here. See `events.jsonl` for the raw record.")
        return "\n".join(details)

    return ""


def render_transcript(*, meta: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    cost = meta.get("cost") if isinstance(meta.get("cost"), dict) else {}
    usd = cost.get("actual_usd")
    if usd is None:
        usd = cost.get("estimated_usd")

    header = [
        "# Run Transcript",
        "",
        "_Derived from `events.jsonl` and `meta.json`. `README.md` remains the first reader entry, and `events.jsonl` remains the raw evidence source._",
        "",
        "_Scope: this transcript covers only the current `events.jsonl` in this run directory. If the run was resumed, prior attempts remain under `attempts/`._",
        "",
        f"- Run id: `{meta.get('run_id') or ''}`",
        f"- Goal file: `{meta.get('goal_file') or ''}`",
        f"- Status: `{meta.get('status') or 'unknown'}`",
    ]
    if meta.get("started_at"):
        header.append(f"- Started: `{meta['started_at']}`")
    if meta.get("completed_at"):
        header.append(f"- Completed: `{meta['completed_at']}`")
    if usd is not None:
        header.append(f"- Cost: `{usd}` USD")

    body = [render_entry(entry) for entry in entries]
    rendered_body = "\n\n".join(section for section in body if section)
    if not rendered_body:
        rendered_body = "_No supported transcript entries were derived from `events.jsonl`._"

    footer = [
        "## Evidence",
        "",
        "- `README.md` for the reader-oriented entry point",
        "- `events.jsonl` for the raw event evidence",
        "- `meta.json` for structured run status and cost",
    ]

    return "\n".join(header) + "\n\n## Chronological Transcript\n\n" + rendered_body + "\n\n" + "\n".join(footer) + "\n"


def write_run_transcript(*, run_dir: Path) -> int:
    meta_path = run_dir / "meta.json"
    events_path = run_dir / "events.jsonl"
    transcript_path = run_dir / "transcript.md"

    meta = load_meta(meta_path)
    status = str(meta.get("status") or "unknown")
    if status == "running":
        return 0

    events = parse_events(events_path)
    entries = normalize_transcript_entries(events=events, driver=meta.get("driver"))
    transcript_path.write_text(render_transcript(meta=meta, entries=entries), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["render"])
    parser.add_argument("run_dir")
    args = parser.parse_args()
    if args.command == "render":
        return write_run_transcript(run_dir=Path(args.run_dir))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
