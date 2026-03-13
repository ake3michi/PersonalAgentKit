from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class DriverConfig:
    name: str
    binary: str
    default_model: str


class DriverPlugin(Protocol):
    config: DriverConfig

    def build_command(self, *, model: str) -> list[str]: ...

    def prepare_env(self, env: Mapping[str, str]) -> dict[str, str]: ...

    def parse_events(self, *, events: list[dict[str, Any]], model: str) -> dict[str, Any]: ...

    def normalize_transcript(self, *, events: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
