"""
Runtime-history capture for split runtime roots.

The first runtime-auto-commit slice is intentionally narrow:

- it activates only when the configured runtime root is separate from the
  garden root
- it captures runtime history at run finalization
- it records the authored HEAD commit plus authored tree cleanliness, excluding
  runtime-root churn itself
- it commits the runtime tree inside a nested git repository rooted at the
  runtime root
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from dataclasses import dataclass

from .garden import discover_garden_root, garden_paths

_DEFAULT_RUNTIME_GIT_IDENTITY = {
    "user.name": "pak2-runtime",
    "user.email": "runtime@local.invalid",
}
_HISTORY_DIR = pathlib.Path("history") / "commits"


@dataclass(frozen=True, slots=True)
class RuntimeHistoryCaptureResult:
    attempted: bool
    committed: bool
    reason: str
    detail: str | None = None
    commit_id: str | None = None
    authored_commit: str | None = None
    authored_tree_state: str | None = None
    record_path: pathlib.Path | None = None


def _run_git(args: list[str], *, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=127,
            stdout="",
            stderr="git executable not found",
        )


def _git_stdout(args: list[str], *, cwd: pathlib.Path) -> tuple[str | None, str | None]:
    completed = _run_git(args, cwd=cwd)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"git {' '.join(args)} failed"
        return None, detail
    return completed.stdout.strip(), None


def _resolve_paths(*, garden_root: pathlib.Path | None, runs_dir: pathlib.Path | None):
    if garden_root is not None:
        root = discover_garden_root(pathlib.Path(garden_root).resolve())
    elif runs_dir is not None:
        root = discover_garden_root(pathlib.Path(runs_dir).resolve())
    else:
        root = discover_garden_root(pathlib.Path(".").resolve())
    return garden_paths(garden_root=root)


def _runtime_root_relative_to_garden(paths) -> pathlib.Path | None:
    try:
        return paths.runtime_root.resolve().relative_to(paths.garden_root.resolve())
    except ValueError:
        return None


def _read_authored_provenance(*, garden_root: pathlib.Path, runtime_rel: pathlib.Path) -> tuple[str | None, str | None, str | None]:
    authored_commit, detail = _git_stdout(["rev-parse", "HEAD"], cwd=garden_root)
    if authored_commit is None:
        return None, None, detail

    exclude = runtime_rel.as_posix()
    status, detail = _git_stdout(
        [
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            ".",
            f":(exclude){exclude}",
            f":(exclude){exclude}/**",
        ],
        cwd=garden_root,
    )
    if status is None:
        return None, None, detail

    tree_state = "dirty" if status else "clean"
    return authored_commit, tree_state, None


def _ensure_runtime_repo(runtime_root: pathlib.Path) -> str | None:
    runtime_root.mkdir(parents=True, exist_ok=True)

    if not (runtime_root / ".git").exists():
        completed = _run_git(["init", "-q"], cwd=runtime_root)
        if completed.returncode != 0:
            return completed.stderr.strip() or completed.stdout.strip() or "git init failed"

    for key, value in _DEFAULT_RUNTIME_GIT_IDENTITY.items():
        existing, _ = _git_stdout(["config", "--get", key], cwd=runtime_root)
        if existing:
            continue
        completed = _run_git(["config", key, value], cwd=runtime_root)
        if completed.returncode != 0:
            return completed.stderr.strip() or completed.stdout.strip() or f"git config {key} failed"

    return None


def _write_record(
    *,
    record_path: pathlib.Path,
    run_id: str,
    goal_id: str,
    run_status: str,
    completed_at: str,
    authored_commit: str,
    authored_tree_state: str,
) -> str | None:
    payload = {
        "schema_version": 1,
        "record_kind": "run-finalization",
        "run_id": run_id,
        "goal_id": goal_id,
        "run_status": run_status,
        "completed_at": completed_at,
        "authored_commit": authored_commit,
        "authored_tree_state": authored_tree_state,
    }
    try:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return str(exc)
    return None


def capture_runtime_history_for_run(
    *,
    run_id: str,
    goal_id: str,
    run_status: str,
    completed_at: str,
    garden_root: pathlib.Path | None = None,
    runs_dir: pathlib.Path | None = None,
) -> RuntimeHistoryCaptureResult:
    paths = _resolve_paths(garden_root=garden_root, runs_dir=runs_dir)
    if paths.runtime_root.resolve() == paths.garden_root.resolve():
        return RuntimeHistoryCaptureResult(
            attempted=False,
            committed=False,
            reason="runtime_root_not_split",
        )

    runtime_rel = _runtime_root_relative_to_garden(paths)
    if runtime_rel is None:
        return RuntimeHistoryCaptureResult(
            attempted=False,
            committed=False,
            reason="runtime_root_outside_garden",
        )

    authored_commit, authored_tree_state, detail = _read_authored_provenance(
        garden_root=paths.garden_root.resolve(),
        runtime_rel=runtime_rel,
    )
    if authored_commit is None or authored_tree_state is None:
        return RuntimeHistoryCaptureResult(
            attempted=False,
            committed=False,
            reason="authored_repo_unavailable",
            detail=detail,
        )

    record_path = paths.runtime_root / _HISTORY_DIR / f"{run_id}.json"
    detail = _write_record(
        record_path=record_path,
        run_id=run_id,
        goal_id=goal_id,
        run_status=run_status,
        completed_at=completed_at,
        authored_commit=authored_commit,
        authored_tree_state=authored_tree_state,
    )
    if detail is not None:
        return RuntimeHistoryCaptureResult(
            attempted=True,
            committed=False,
            reason="record_write_failed",
            detail=detail,
            authored_commit=authored_commit,
            authored_tree_state=authored_tree_state,
            record_path=record_path,
        )

    detail = _ensure_runtime_repo(paths.runtime_root)
    if detail is not None:
        return RuntimeHistoryCaptureResult(
            attempted=True,
            committed=False,
            reason="runtime_repo_init_failed",
            detail=detail,
            authored_commit=authored_commit,
            authored_tree_state=authored_tree_state,
            record_path=record_path,
        )

    completed = _run_git(["add", "-A", "--", "."], cwd=paths.runtime_root)
    if completed.returncode != 0:
        return RuntimeHistoryCaptureResult(
            attempted=True,
            committed=False,
            reason="runtime_repo_stage_failed",
            detail=completed.stderr.strip() or completed.stdout.strip() or "git add failed",
            authored_commit=authored_commit,
            authored_tree_state=authored_tree_state,
            record_path=record_path,
        )

    completed = _run_git(["diff", "--cached", "--quiet", "--exit-code"], cwd=paths.runtime_root)
    if completed.returncode == 0:
        return RuntimeHistoryCaptureResult(
            attempted=True,
            committed=False,
            reason="no_runtime_changes",
            authored_commit=authored_commit,
            authored_tree_state=authored_tree_state,
            record_path=record_path,
        )
    if completed.returncode != 1:
        return RuntimeHistoryCaptureResult(
            attempted=True,
            committed=False,
            reason="runtime_repo_diff_failed",
            detail=completed.stderr.strip() or completed.stdout.strip() or "git diff --cached failed",
            authored_commit=authored_commit,
            authored_tree_state=authored_tree_state,
            record_path=record_path,
        )

    record_rel = record_path.relative_to(paths.runtime_root)
    message = "\n".join(
        [
            f"runtime: capture {run_id} ({run_status})",
            "",
            f"Authored-Commit: {authored_commit}",
            f"Authored-Tree: {authored_tree_state}",
            f"Source-Run: {run_id}",
            f"Source-Goal: {goal_id}",
            f"Completed-At: {completed_at}",
            f"Record-Path: {record_rel.as_posix()}",
        ]
    )
    completed = _run_git(["commit", "-q", "-m", message], cwd=paths.runtime_root)
    if completed.returncode != 0:
        return RuntimeHistoryCaptureResult(
            attempted=True,
            committed=False,
            reason="runtime_repo_commit_failed",
            detail=completed.stderr.strip() or completed.stdout.strip() or "git commit failed",
            authored_commit=authored_commit,
            authored_tree_state=authored_tree_state,
            record_path=record_path,
        )

    commit_id, detail = _git_stdout(["rev-parse", "HEAD"], cwd=paths.runtime_root)
    if commit_id is None:
        return RuntimeHistoryCaptureResult(
            attempted=True,
            committed=False,
            reason="runtime_repo_head_unavailable",
            detail=detail,
            authored_commit=authored_commit,
            authored_tree_state=authored_tree_state,
            record_path=record_path,
        )

    return RuntimeHistoryCaptureResult(
        attempted=True,
        committed=True,
        reason="committed",
        commit_id=commit_id,
        authored_commit=authored_commit,
        authored_tree_state=authored_tree_state,
        record_path=record_path,
    )


__all__ = [
    "RuntimeHistoryCaptureResult",
    "capture_runtime_history_for_run",
]
