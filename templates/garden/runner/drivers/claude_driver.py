from __future__ import annotations

from typing import Any, Mapping

from runner.plugin_api import DriverConfig
from runner.transcript_support import build_unrendered_event_entry, extract_claude_content_text, stringify_payload


class ClaudeDriver:
    config = DriverConfig(name="claude", binary="claude", default_model="claude-sonnet-4-6")

    def build_command(self, *, model: str) -> list[str]:
        return [
            self.config.binary,
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]

    def prepare_env(self, env: Mapping[str, str]) -> dict[str, str]:
        prepared = dict(env)
        prepared["CLAUDECODE"] = ""
        return prepared

    def parse_events(self, *, events: list[dict[str, Any]], model: str) -> dict[str, Any]:
        result_event = next((event for event in reversed(events) if event.get("type") == "result"), {})
        usage = result_event.get("usage") or {}
        return {
            "output": result_event.get("result") or "no output",
            "cost": {
                "input_tokens": int(usage.get("input_tokens", 0) or 0),
                "output_tokens": int(usage.get("output_tokens", 0) or 0),
                "cache_read_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
                "cache_write_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
                "actual_usd": result_event.get("total_cost_usd"),
                "estimated_usd": None,
                "pricing": {
                    "source": "provider-native",
                    "provider": self.config.name,
                    "model": model,
                    "version": None,
                    "retrieved_at": None,
                    "notes": None,
                },
            },
            "num_turns": result_event.get("num_turns"),
            "duration_ms": result_event.get("duration_ms"),
        }

    def normalize_transcript(self, *, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rendered: list[dict[str, Any]] = []
        pending_tool_results: dict[str, dict[str, Any]] = {}

        for index, event in enumerate(events):
            matched = False
            event_type = event.get("type")
            message = event.get("message")
            if event_type == "assistant" and isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, list):
                    text_blocks: list[str] = []
                    for block_index, block in enumerate(content):
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type")
                        if block_type == "text":
                            text = block.get("text")
                            if isinstance(text, str) and text.strip():
                                text_blocks.append(text)
                                matched = True
                        elif block_type == "tool_use":
                            tool_id = str(block.get("id") or f"tool-{index}-{block_index}")
                            entry = {
                                "order": index * 1000 + block_index,
                                "kind": "tool_activity",
                                "tool_name": str(block.get("name") or "tool_use"),
                                "status": "completed",
                                "exit_code": None,
                                "invocation": stringify_payload(block.get("input")),
                                "result_text": "",
                                "result_label": "tool_result",
                            }
                            rendered.append(entry)
                            pending_tool_results[tool_id] = entry
                            matched = True

                    if text_blocks:
                        rendered.append(
                            {
                                "order": index * 1000 + 999,
                                "kind": "assistant_message",
                                "text": "\n\n".join(text_blocks),
                            }
                        )

            if event_type == "user" and isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        tool_use_id = str(block.get("tool_use_id") or "")
                        entry = pending_tool_results.get(tool_use_id)
                        if entry is None:
                            continue
                        entry["result_text"] = extract_claude_content_text(block.get("content"))
                        matched = True

            if not matched:
                rendered.append(build_unrendered_event_entry(order=index * 1000, event=event))

        return sorted(rendered, key=lambda item: int(item.get("order", 0)))


PLUGIN = ClaudeDriver()
