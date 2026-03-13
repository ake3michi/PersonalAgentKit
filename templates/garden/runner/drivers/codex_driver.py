from __future__ import annotations

import json
from typing import Any, Mapping

from runner.plugin_api import DriverConfig
from runner.transcript_support import build_unrendered_event_entry, normalize_todo_items


CODEX_PRICING_VERSION = "openai-api-pricing-2026-03-09"


def resolve_codex_pricing(model: str) -> tuple[float, float, float, str, str | None]:
    if model == "gpt-5.1-codex-mini":
        return (0.25, 0.025, 2.00, "gpt-5.1-codex-mini", None)
    if model == "codex-mini-latest":
        return (1.50, 0.375, 6.00, "codex-mini-latest", None)
    if model in {"gpt-5-codex", "gpt-5.1-codex", "gpt-5.1-codex-max", "gpt-5"}:
        return (1.25, 0.125, 10.00, model, None)
    if model == "gpt-5.4":
        return (
            1.25,
            0.125,
            10.00,
            "gpt-5",
            "Estimated using gpt-5 pricing as the local alias for gpt-5.4.",
        )
    return (
        1.25,
        0.125,
        10.00,
        "gpt-5",
        f"Estimated using fallback gpt-5 pricing because no exact local pricing entry exists for {model}.",
    )


class CodexDriver:
    config = DriverConfig(name="codex", binary="codex", default_model="gpt-5.4")

    def build_command(self, *, model: str) -> list[str]:
        command = [self.config.binary, "exec", "-"]
        if model:
            command.extend(["--model", model])
        command.extend(["--dangerously-bypass-approvals-and-sandbox", "--json"])
        return command

    def prepare_env(self, env: Mapping[str, str]) -> dict[str, str]:
        return dict(env)

    def parse_events(self, *, events: list[dict[str, Any]], model: str) -> dict[str, Any]:
        turn_events = [event for event in events if event.get("type") == "turn.completed"]
        input_tokens = sum(int((event.get("usage") or {}).get("input_tokens", 0) or 0) for event in turn_events)
        output_tokens = sum(int((event.get("usage") or {}).get("output_tokens", 0) or 0) for event in turn_events)
        cache_read_tokens = sum(
            int((event.get("usage") or {}).get("cached_input_tokens", 0) or 0) for event in turn_events
        )
        duration_ms = sum(int(event.get("duration_ms", 0) or 0) for event in turn_events) or None

        output = "no output"
        for event in reversed(events):
            item = event.get("item")
            if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
                output = item.get("text") or "no output"
                break

        input_rate, cached_rate, output_rate, pricing_model, pricing_notes = resolve_codex_pricing(model)
        estimated_cost = (
            input_tokens * input_rate + cache_read_tokens * cached_rate + output_tokens * output_rate
        ) / 1_000_000

        return {
            "output": output,
            "cost": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": 0,
                "actual_usd": None,
                "estimated_usd": round(estimated_cost, 6),
                "pricing": {
                    "source": "local-estimate",
                    "provider": self.config.name,
                    "model": pricing_model,
                    "version": CODEX_PRICING_VERSION,
                    "retrieved_at": None,
                    "notes": pricing_notes,
                },
            },
            "num_turns": len(turn_events),
            "duration_ms": duration_ms,
        }

    def normalize_transcript(self, *, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rendered: list[dict[str, Any]] = []
        todo_snapshots: dict[str, str] = {}

        for index, event in enumerate(events):
            matched = False
            event_type = event.get("type")
            item = event.get("item")

            if event_type == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
                rendered.append(
                    {
                        "order": index,
                        "kind": "assistant_message",
                        "text": str(item.get("text") or ""),
                    }
                )
                matched = True

            if event_type == "item.completed" and isinstance(item, dict) and item.get("type") == "command_execution":
                rendered.append(
                    {
                        "order": index,
                        "kind": "tool_activity",
                        "tool_name": "command_execution",
                        "status": str(item.get("status") or "unknown"),
                        "exit_code": item.get("exit_code"),
                        "invocation": str(item.get("command") or ""),
                        "result_text": str(item.get("aggregated_output") or ""),
                        "result_label": "aggregated_output",
                    }
                )
                matched = True

            if event_type == "item.completed" and isinstance(item, dict) and item.get("type") == "file_change":
                rendered.append(
                    {
                        "order": index,
                        "kind": "file_change",
                        "changes": item.get("changes") if isinstance(item.get("changes"), list) else [],
                    }
                )
                matched = True

            if event_type in {"item.started", "item.updated", "item.completed"} and isinstance(item, dict) and item.get("type") == "todo_list":
                todo_id = str(item.get("id") or f"todo-{index}")
                todo_items = normalize_todo_items(item.get("items"))
                snapshot = json.dumps(todo_items, sort_keys=True)
                if todo_snapshots.get(todo_id) == snapshot:
                    matched = True
                else:
                    todo_snapshots[todo_id] = snapshot
                    rendered.append(
                        {
                            "order": index,
                            "kind": "todo_list",
                            "items": todo_items,
                        }
                    )
                    matched = True

            if not matched:
                rendered.append(build_unrendered_event_entry(order=index, event=event))

        return rendered


PLUGIN = CodexDriver()
