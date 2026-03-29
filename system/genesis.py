"""
Genesis: bootstrap a new garden and queue the gardener's first run.

Usage:
    python3 -m system.genesis [--garden-root PATH]

Prerequisites:
    - CHARTER.md must exist in the garden root (`pak2 init` writes a starter
      copy; otherwise add one first).
    - seeds/gardener.md must exist (ships with the system).

What genesis does:
    1. Validates prerequisites.
    2. Creates the garden directory structure.
    3. Commissions the gardener plant.
    4. Commits durable bootstrap files in the authored repo when git is
       available at the garden root.
    5. Submits the genesis goal.
    6. Stops before dispatch so `./pak2 cycle` can bring up startup surfaces and
       run the first queued work on the ordinary cycle path.

Genesis is idempotent: if the gardener already has memory (genesis already
ran successfully), it reports that and exits without doing anything.
"""

import argparse
import pathlib
import subprocess
import sys

from .garden import garden_paths
from .goals import list_goals
from .plants import (
    commission_seeded_plant,
    materialize_seed_context,
    read_plant,
    submit_initial_goal_for_plant,
)

_GENESIS_GOAL_BODY = """\
This is your genesis run. Your seed context has been loaded.
Complete the genesis steps in order.\
"""
_GENESIS_BOOTSTRAP_COMMIT_MESSAGE = "genesis: track gardener bootstrap files"
_GENESIS_BOOTSTRAP_ASSET_SECTIONS = ("skills", "knowledge")


def _missing_charter_message(root: pathlib.Path, charter: pathlib.Path) -> str:
    lines = [f"genesis: CHARTER.md not found at {charter}"]

    quickstart = root / "examples" / "charter-quickstart.md"
    if quickstart.is_file():
        lines.append(f"Fastest start: copy {quickstart} to {charter}.")

    charter_example = root / "CHARTER.md.example"
    if charter_example.is_file():
        lines.append(f"For a custom charter, start from {charter_example}.")

    if len(lines) == 1:
        lines.append("Create CHARTER.md before running genesis.")

    return "\n".join(lines)


def _staged_genesis_goal(root: pathlib.Path) -> dict | None:
    paths = garden_paths(garden_root=root)
    for goal in list_goals(_goals_dir=paths.goals_dir):
        if goal.get("status") == "closed":
            continue
        if goal.get("assigned_to") != "gardener":
            continue
        if goal.get("type") != "spike":
            continue
        if goal.get("body") != _GENESIS_GOAL_BODY:
            continue
        return goal
    return None


def _bootstrap_commit_paths(root: pathlib.Path, *, plant_name: str) -> list[pathlib.Path]:
    plant_root = root / "plants" / plant_name
    paths = [
        root / "CHARTER.md",
        plant_root / "meta.json",
        plant_root / "seed",
    ]
    for section in _GENESIS_BOOTSTRAP_ASSET_SECTIONS:
        section_root = plant_root / section
        if not section_root.is_dir():
            continue
        paths.extend(sorted(path for path in section_root.rglob("*") if path.is_file()))

    unique_paths: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for path in paths:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(path)
    return unique_paths


def _run_git(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )


def _commit_bootstrap_files(root: pathlib.Path, *, plant_name: str) -> str | None:
    if not (root / ".git").exists():
        return None

    paths = _bootstrap_commit_paths(root, plant_name=plant_name)
    if not paths:
        return None

    rel_paths = [str(path.relative_to(root)) for path in paths]
    status = _run_git(root, "status", "--short", "--", *rel_paths)
    if status.returncode != 0:
        detail = status.stderr.strip() or status.stdout.strip() or "git status failed"
        raise RuntimeError(detail)
    if not status.stdout.strip():
        return None

    staged = _run_git(root, "add", "--", *rel_paths)
    if staged.returncode != 0:
        detail = staged.stderr.strip() or staged.stdout.strip() or "git add failed"
        raise RuntimeError(detail)

    diff = _run_git(root, "diff", "--cached", "--quiet", "--exit-code", "--", *rel_paths)
    if diff.returncode == 0:
        return None
    if diff.returncode != 1:
        detail = diff.stderr.strip() or diff.stdout.strip() or "git diff --cached failed"
        raise RuntimeError(detail)

    committed = _run_git(
        root,
        "commit",
        "--only",
        "-m",
        _GENESIS_BOOTSTRAP_COMMIT_MESSAGE,
        "--",
        *rel_paths,
    )
    if committed.returncode != 0:
        detail = committed.stderr.strip() or committed.stdout.strip() or "git commit failed"
        raise RuntimeError(detail)

    head = _run_git(root, "rev-parse", "--short", "HEAD")
    if head.returncode != 0:
        detail = head.stderr.strip() or head.stdout.strip() or "git rev-parse HEAD failed"
        raise RuntimeError(detail)
    commit_id = head.stdout.strip()
    return commit_id or None


def genesis(root: pathlib.Path) -> None:
    """
    Bootstrap a garden at `root`, queue the first gardener goal, and stop
    before dispatch. Prints status to stdout.
    """
    root = root.resolve()
    paths = garden_paths(garden_root=root)

    # ------------------------------------------------------------------
    # Validate prerequisites
    # ------------------------------------------------------------------
    charter = root / "CHARTER.md"
    if not charter.exists():
        sys.exit(_missing_charter_message(root, charter))

    seed = paths.seeds_dir / "gardener.md"
    if not seed.exists():
        sys.exit(
            f"genesis: seeds/gardener.md not found at {seed}\n"
            f"This file ships with the system and must be present."
        )

    # ------------------------------------------------------------------
    # Idempotency check: has genesis already run?
    # ------------------------------------------------------------------
    memory = paths.plants_dir / "gardener" / "memory" / "MEMORY.md"
    if memory.exists() and memory.read_text().strip():
        print("genesis: gardener already has memory — genesis has already run.")
        plant = read_plant("gardener", _plants_dir=paths.plants_dir)
        if plant:
            print(f"  Plant: {plant.get('name')}  status: {plant.get('status')}")
        return

    staged_goal = _staged_genesis_goal(root)
    if staged_goal is not None:
        print(
            "genesis: initialization already staged — "
            f"genesis goal {staged_goal['id']} is {staged_goal['status']}."
        )
        return

    # ------------------------------------------------------------------
    # Create garden directory structure
    # ------------------------------------------------------------------
    print(f"genesis: initializing garden at {root}")
    for path in (
        paths.goals_dir,
        paths.runs_dir,
        paths.plants_dir,
        paths.events_dir,
        paths.inbox_dir,
        paths.conversations_dir,
        paths.operator_inbox_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Commission the gardener plant
    # ------------------------------------------------------------------
    result = commission_seeded_plant(
        "gardener", "gardener", "operator",
        _garden_root=root,
    )
    if not result.ok and result.reason != "PLANT_ALREADY_EXISTS":
        sys.exit(f"genesis: failed to commission gardener: {result.reason}")
    if result.reason == "PLANT_ALREADY_EXISTS":
        refresh = materialize_seed_context(root, plant_name="gardener", seed_name="gardener")
        if not refresh.ok:
            sys.exit(f"genesis: failed to refresh gardener seed context: {refresh.reason}")
    print("genesis: gardener plant commissioned")

    try:
        bootstrap_commit = _commit_bootstrap_files(root, plant_name="gardener")
    except RuntimeError as exc:
        sys.exit(f"genesis: failed to commit bootstrap files: {exc}")
    if bootstrap_commit:
        print(f"genesis: bootstrap files committed ({bootstrap_commit})")

    # ------------------------------------------------------------------
    # Submit the genesis goal
    # ------------------------------------------------------------------
    result, goal_id = submit_initial_goal_for_plant(
        plant_name="gardener",
        goal_type="spike",
        submitted_by="operator",
        body=_GENESIS_GOAL_BODY,
        _goals_dir=paths.goals_dir,
    )
    if not result.ok:
        sys.exit(f"genesis: failed to submit genesis goal: {result.reason}")
    print(f"genesis: genesis goal submitted ({goal_id})")
    print(
        "genesis: initialization complete — run `./pak2 cycle` "
        "to start startup messaging and dispatch queued work"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap a new garden and queue the gardener's first goal."
    )
    parser.add_argument(
        "--garden-root",
        default=".",
        metavar="PATH",
        help="Path to the garden root (default: current directory)",
    )
    args = parser.parse_args()
    genesis(pathlib.Path(args.garden_root))


if __name__ == "__main__":
    main()
