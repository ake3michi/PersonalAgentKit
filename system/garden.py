"""
Garden-level configuration helpers.

`PAK2.toml` is the durable system-readable home for garden-wide defaults and
the chosen garden name used by filesystem replies.
"""

from __future__ import annotations

import os
import pathlib
import re
import tomllib
from dataclasses import dataclass

from .validate import ValidationResult


CONFIG_FILE_NAME = "PAK2.toml"
DEFAULT_GARDEN_NAME = "garden"
LEGACY_FILESYSTEM_REPLY_DIR = DEFAULT_GARDEN_NAME
_GOALS_DIRNAME = "goals"
_RUNS_DIRNAME = "runs"
_EVENTS_DIRNAME = "events"
_CONVERSATIONS_DIRNAME = "conversations"
_INBOX_DIRNAME = "inbox"
_OPERATOR_DIRNAME = "operator"
_DASHBOARD_DIRNAME = "dashboard"
_DASHBOARD_INVOCATIONS_DIRNAME = "invocations"
_PLANTS_DIRNAME = "plants"
_SEEDS_DIRNAME = "seeds"
_MOTIVATION_NAME = "MOTIVATION.md"
_RUNTIME_SECTION = "runtime"
_RUNTIME_ROOT_KEY = "root"

_ENV_GARDEN_ROOT = "PAK2_GARDEN_ROOT"
_GARDEN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_SECTION_PATTERN = re.compile(r"^\[(?P<section>[^\]]+)\]\s*$")
_MEMORY_HEADING_PATTERN = re.compile(r"^#\s+(?P<name>.+?)\s*$")
_RUNTIME_DIRNAMES = frozenset(
    {
        _GOALS_DIRNAME,
        _RUNS_DIRNAME,
        _EVENTS_DIRNAME,
        _CONVERSATIONS_DIRNAME,
        _INBOX_DIRNAME,
        _DASHBOARD_DIRNAME,
    }
)
_GARDEN_ROOT_MARKERS = (
    CONFIG_FILE_NAME,
    "GARDEN.md",
    "CHARTER.md",
    _MOTIVATION_NAME,
)


@dataclass(frozen=True, slots=True)
class GardenPaths:
    garden_root: pathlib.Path
    runtime_root: pathlib.Path
    goals_dir: pathlib.Path
    runs_dir: pathlib.Path
    events_dir: pathlib.Path
    coordinator_events_path: pathlib.Path
    conversations_dir: pathlib.Path
    inbox_dir: pathlib.Path
    operator_inbox_dir: pathlib.Path
    inbox_seen_path: pathlib.Path
    dashboard_dir: pathlib.Path
    dashboard_invocations_dir: pathlib.Path
    plants_dir: pathlib.Path
    seeds_dir: pathlib.Path
    motivation_path: pathlib.Path


def _base_root(garden_root: pathlib.Path | None = None) -> pathlib.Path:
    if garden_root is not None:
        return pathlib.Path(garden_root)
    env_root = os.environ.get(_ENV_GARDEN_ROOT)
    if env_root:
        return pathlib.Path(env_root)
    return pathlib.Path(".")


def _resolve_root(garden_root: pathlib.Path | None = None) -> pathlib.Path:
    return _base_root(garden_root).resolve()


def garden_root_path(*, garden_root: pathlib.Path | None = None) -> pathlib.Path:
    return _base_root(garden_root)


def runtime_root_path(*, garden_root: pathlib.Path | None = None) -> pathlib.Path:
    root = garden_root_path(garden_root=garden_root)
    configured = read_runtime_root_setting(garden_root=garden_root)
    if configured is None:
        return root
    return root / configured


def garden_paths(*, garden_root: pathlib.Path | None = None) -> GardenPaths:
    root = garden_root_path(garden_root=garden_root)
    runtime_root = runtime_root_path(garden_root=garden_root)
    inbox_dir = runtime_root / _INBOX_DIRNAME
    dashboard_dir = runtime_root / _DASHBOARD_DIRNAME
    events_dir = runtime_root / _EVENTS_DIRNAME
    return GardenPaths(
        garden_root=root,
        runtime_root=runtime_root,
        goals_dir=runtime_root / _GOALS_DIRNAME,
        runs_dir=runtime_root / _RUNS_DIRNAME,
        events_dir=events_dir,
        coordinator_events_path=events_dir / "coordinator.jsonl",
        conversations_dir=runtime_root / _CONVERSATIONS_DIRNAME,
        inbox_dir=inbox_dir,
        operator_inbox_dir=inbox_dir / _OPERATOR_DIRNAME,
        inbox_seen_path=inbox_dir / ".seen",
        dashboard_dir=dashboard_dir,
        dashboard_invocations_dir=dashboard_dir / _DASHBOARD_INVOCATIONS_DIRNAME,
        plants_dir=root / _PLANTS_DIRNAME,
        seeds_dir=root / _SEEDS_DIRNAME,
        motivation_path=root / _MOTIVATION_NAME,
    )


def _config_path(garden_root: pathlib.Path | None = None) -> pathlib.Path:
    return _resolve_root(garden_root) / CONFIG_FILE_NAME


def _read_garden_config(garden_root: pathlib.Path | None = None) -> dict:
    path = _config_path(garden_root)
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def read_garden_defaults(*, garden_root: pathlib.Path | None = None) -> dict:
    data = _read_garden_config(garden_root)
    defaults = data.get("defaults", {})
    return defaults if isinstance(defaults, dict) else {}


def read_runtime_root_setting(*, garden_root: pathlib.Path | None = None) -> str | None:
    data = _read_garden_config(garden_root)
    runtime = data.get(_RUNTIME_SECTION, {})
    if not isinstance(runtime, dict):
        return None
    value = runtime.get(_RUNTIME_ROOT_KEY)
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    path = pathlib.Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return value


def read_garden_name(*, garden_root: pathlib.Path | None = None) -> str | None:
    data = _read_garden_config(garden_root)
    garden = data.get("garden", {})
    if not isinstance(garden, dict):
        return None
    name = garden.get("name")
    if not isinstance(name, str):
        return None
    name = name.strip()
    return name or None


def resolve_garden_name(*, garden_root: pathlib.Path | None = None) -> str:
    return read_garden_name(garden_root=garden_root) or DEFAULT_GARDEN_NAME


def read_garden_display_name(*, garden_root: pathlib.Path | None = None) -> str | None:
    configured = read_garden_name(garden_root=garden_root)
    if configured:
        return configured

    memory_path = (
        _resolve_root(garden_root)
        / _PLANTS_DIRNAME
        / "gardener"
        / "memory"
        / "MEMORY.md"
    )
    try:
        lines = memory_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        match = _MEMORY_HEADING_PATTERN.match(stripped)
        if not match:
            break
        name = match.group("name").strip()
        if name.startswith("`") and name.endswith("`") and len(name) >= 2:
            name = name[1:-1].strip()
        return name or None

    return None


def resolve_garden_display_name(*, garden_root: pathlib.Path | None = None) -> str:
    return read_garden_display_name(garden_root=garden_root) or DEFAULT_GARDEN_NAME


def discover_garden_root(path: pathlib.Path) -> pathlib.Path:
    current = pathlib.Path(path).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in _GARDEN_ROOT_MARKERS):
            return candidate
    if current.name in _RUNTIME_DIRNAMES:
        return current.parent
    return current


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _upsert_toml_string_key(text: str, *, section: str, key: str, value: str) -> str:
    line_value = f"{key} = {_toml_string(value)}"
    if not text:
        return f"[{section}]\n{line_value}\n"

    lines = text.splitlines(keepends=True)
    start = None
    end = len(lines)
    for idx, line in enumerate(lines):
        match = _SECTION_PATTERN.match(line.strip())
        if not match:
            continue
        if start is None and match.group("section") == section:
            start = idx
            continue
        if start is not None:
            end = idx
            break

    if start is None:
        suffix = text
        if suffix and not suffix.endswith("\n"):
            suffix += "\n"
        if suffix and not suffix.endswith("\n\n"):
            suffix += "\n"
        return suffix + f"[{section}]\n{line_value}\n"

    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for idx in range(start + 1, end):
        if key_pattern.match(lines[idx]):
            newline = "\n" if lines[idx].endswith("\n") else ""
            lines[idx] = f"{line_value}{newline}"
            return "".join(lines)

    lines.insert(start + 1, f"{line_value}\n")
    return "".join(lines)


def set_garden_name(name: str, *, garden_root: pathlib.Path | None = None) -> ValidationResult:
    name = str(name).strip()
    if not _GARDEN_NAME_PATTERN.match(name):
        return ValidationResult.reject(
            "INVALID_GARDEN_NAME",
            "garden name must be lowercase alphanumeric with hyphens, "
            f"starting with a letter, got: {name!r}",
        )

    path = _config_path(garden_root)
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        return ValidationResult.reject("IO_ERROR", str(exc))

    updated = _upsert_toml_string_key(existing, section="garden", key="name", value=name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return ValidationResult.reject("IO_ERROR", str(exc))

    return ValidationResult.accept()


def filesystem_reply_dir(root: pathlib.Path, *, garden_name: str | None = None,
                         ensure: bool = False) -> pathlib.Path:
    root = pathlib.Path(root).resolve()
    garden_name = garden_name or resolve_garden_name(garden_root=root)
    inbox_root = garden_paths(garden_root=root).inbox_dir
    target_dir = inbox_root / garden_name

    if not ensure:
        return target_dir

    inbox_root.mkdir(parents=True, exist_ok=True)
    legacy_dir = inbox_root / LEGACY_FILESYSTEM_REPLY_DIR

    # Explicit migration choice:
    # - if only the legacy directory exists, rename it into the configured name
    # - if both exist already, preserve both and write new replies to target_dir
    if (garden_name != LEGACY_FILESYSTEM_REPLY_DIR
            and legacy_dir.exists()
            and legacy_dir.is_dir()
            and not target_dir.exists()):
        legacy_dir.rename(target_dir)
        return target_dir

    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir
