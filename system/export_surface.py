"""
Helpers for materializing the clean authored export surface shared by
`pak2 publish` and `pak2 init`.
"""

from __future__ import annotations

import pathlib
import shutil


# Files and directories copied into a fresh garden or clean publish export.
EXPORTABLE_TEMPLATE_INCLUDES = [
    "system",
    "seeds",
    "docs",
    "schema",
    "tests",
    "pak2",
    "README.md",
    "LICENSE",
    "MOTIVATION.md",
    "GARDEN.md",
    "DONE.md",
    "PAK2.toml.example",
    "CHARTER.md.example",
    "examples",
]

EXPORT_IGNORE_PATTERNS = ("__pycache__", "*.pyc", "*.pyo")
GARDEN_GITIGNORE = """\
__pycache__/
*.pyc
*.pyo
.runtime/
"""
GARDEN_CONFIG = "PAK2.toml"
DEFAULT_RUNTIME_ROOT = ".runtime"
BOOTSTRAP_CHARTER_PATH = pathlib.Path("CHARTER.md")
BOOTSTRAP_CHARTER_SOURCE = pathlib.Path("examples") / "charter-quickstart.md"


def write_garden_config(dest: pathlib.Path, *,
                        driver: str | None,
                        model: str | None,
                        reasoning_effort: str | None) -> pathlib.Path:
    lines = [
        "[runtime]",
        f'root = "{DEFAULT_RUNTIME_ROOT}"',
    ]
    if any((driver, model, reasoning_effort)):
        lines.extend(["", "[defaults]"])
    if driver:
        lines.append(f'driver = "{driver}"')
    if model:
        lines.append(f'model = "{model}"')
    if reasoning_effort:
        lines.append(f'reasoning_effort = "{reasoning_effort}"')

    path = dest / GARDEN_CONFIG
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def materialize_bootstrap_charter(source_root: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    source = source_root / BOOTSTRAP_CHARTER_SOURCE
    if not source.is_file():
        raise FileNotFoundError(f"missing bootstrap charter template: {source}")

    target = dest / BOOTSTRAP_CHARTER_PATH
    shutil.copy2(source, target)
    return target


def _copy_export_item(src: pathlib.Path, dest: pathlib.Path) -> None:
    if src.is_dir():
        shutil.copytree(
            src,
            dest,
            ignore=shutil.ignore_patterns(*EXPORT_IGNORE_PATTERNS),
            dirs_exist_ok=True,
        )
        return
    shutil.copy2(src, dest)


def materialize_export_surface(source_root: pathlib.Path,
                               dest: pathlib.Path,
                               *,
                               driver: str | None = None,
                               model: str | None = None,
                               reasoning_effort: str | None = None) -> None:
    source_root = pathlib.Path(source_root).resolve()
    dest = pathlib.Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    for name in EXPORTABLE_TEMPLATE_INCLUDES:
        src = source_root / name
        if not src.exists():
            continue
        _copy_export_item(src, dest / name)

    (dest / ".gitignore").write_text(GARDEN_GITIGNORE, encoding="utf-8")
    write_garden_config(
        dest,
        driver=driver,
        model=model,
        reasoning_effort=reasoning_effort,
    )
