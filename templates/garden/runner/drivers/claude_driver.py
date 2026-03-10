from __future__ import annotations

from typing import Any, Mapping

from runner.plugin_api import DriverConfig


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


PLUGIN = ClaudeDriver()
