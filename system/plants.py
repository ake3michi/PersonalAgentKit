"""
Plant store: commission, read, list, and archive plants in plants/.

Plants are context containers. They carry memory, skills, and knowledge
loaded when a goal is dispatched to them. Plants do not own goals or runs —
those live at garden level with fields linking back to the plant.
"""

import datetime
import json
import pathlib
import shutil

from .plant_commission import plant_commission_payload
from .validate import ValidationResult, validate_plant
from .events import append_event, coordinator_events_path
from .garden import garden_paths
from .submit import submit_goal

_PLANTS_DIR = pathlib.Path("plants")
_SEEDS_DIR = pathlib.Path("seeds")
_SEED_ASSET_SECTIONS = ("skills", "knowledge")


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plant_path(name: str, plants_dir: pathlib.Path) -> pathlib.Path:
    return plants_dir / name / "meta.json"


def _plant_dir(name: str, plants_dir: pathlib.Path) -> pathlib.Path:
    return plants_dir / name


def _seed_reference_path(name: str, plants_dir: pathlib.Path) -> pathlib.Path:
    return _plant_dir(name, plants_dir) / "seed"


def commission_plant(name: str, seed: str, commissioned_by: str, *,
                     _plants_dir: pathlib.Path | None = None,
                     _now: str | None = None) -> ValidationResult:
    """
    Create a new plant. Writes meta.json and creates the plant directory
    structure. Emits PlantCommissioned on success.
    """
    plants_dir = _plants_dir if _plants_dir is not None else _PLANTS_DIR
    now = _now or _now_utc()

    record = {
        "name": name,
        "seed": seed,
        "status": "active",
        "created_at": now,
        "commissioned_by": commissioned_by,
    }

    result = validate_plant(record)
    if not result.ok:
        return result

    plant_dir = plants_dir / name
    if plant_dir.exists():
        return ValidationResult.reject(
            "PLANT_ALREADY_EXISTS", f"plant '{name}' already exists"
        )

    plant_dir.mkdir(parents=True, exist_ok=True)
    (plant_dir / "memory").mkdir(exist_ok=True)
    (plant_dir / "skills").mkdir(exist_ok=True)
    (plant_dir / "knowledge").mkdir(exist_ok=True)

    _plant_path(name, plants_dir).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    events_path = coordinator_events_path(plants_dir.parent)

    append_event({
        "ts": now,
        "type": "PlantCommissioned",
        "actor": commissioned_by,
        "plant": name,
    }, path=events_path)

    return ValidationResult.accept()


def materialize_seed_context(root: pathlib.Path, *, plant_name: str, seed_name: str) -> ValidationResult:
    """
    Write the driver's seed reference file and copy seed-local skills/knowledge.
    """
    root = root.resolve()
    plants_dir = root / _PLANTS_DIR
    seed_prompt = root / _SEEDS_DIR / f"{seed_name}.md"
    if not seed_prompt.is_file():
        return ValidationResult.reject(
            "SEED_NOT_FOUND",
            f"seed prompt not found at {seed_prompt}",
        )

    plant = read_plant(plant_name, _plants_dir=plants_dir)
    if plant is None:
        return ValidationResult.reject(
            "PLANT_NOT_FOUND",
            f"plant '{plant_name}' is not commissioned",
        )

    _seed_reference_path(plant_name, plants_dir).write_text(
        f"{seed_name}\n",
        encoding="utf-8",
    )

    seed_root = root / _SEEDS_DIR / seed_name
    plant_root = _plant_dir(plant_name, plants_dir)
    for section in _SEED_ASSET_SECTIONS:
        src_dir = seed_root / section
        if not src_dir.is_dir():
            continue
        dest_dir = plant_root / section
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src in src_dir.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(src_dir)
            dest = dest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    return ValidationResult.accept()


def commission_seeded_plant(name: str, seed: str, commissioned_by: str, *,
                            _garden_root: pathlib.Path | None = None,
                            _now: str | None = None) -> ValidationResult:
    """
    Commission a plant from a seed and materialize the seed context expected
    by the first dispatched run.
    """
    root = (_garden_root or pathlib.Path(".")).resolve()
    seed_prompt = root / _SEEDS_DIR / f"{seed}.md"
    if not seed_prompt.is_file():
        return ValidationResult.reject(
            "SEED_NOT_FOUND",
            f"seed prompt not found at {seed_prompt}",
        )
    plants_dir = root / _PLANTS_DIR

    result = commission_plant(
        name,
        seed,
        commissioned_by,
        _plants_dir=plants_dir,
        _now=_now,
    )
    if not result.ok:
        return result

    return materialize_seed_context(root, plant_name=name, seed_name=seed)


def submit_initial_goal_for_plant(*,
                                  plant_name: str,
                                  goal_type: str,
                                  submitted_by: str,
                                  body: str,
                                  priority: int | None = None,
                                  depends_on: list[str] | None = None,
                                  driver: str | None = None,
                                  model: str | None = None,
                                  reasoning_effort: str | None = None,
                                  _goals_dir: pathlib.Path | None = None,
                                  _now: str | None = None):
    """
    Queue a commissioned plant's first bounded goal through the normal goal
    submission path.
    """
    payload = {
        "type": goal_type,
        "submitted_by": submitted_by,
        "assigned_to": plant_name,
        "body": body,
    }
    if priority is not None:
        payload["priority"] = priority
    if depends_on:
        payload["depends_on"] = list(depends_on)
    if driver:
        payload["driver"] = driver
    if model:
        payload["model"] = model
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    return submit_goal(payload, _goals_dir=_goals_dir, _now=_now)


def execute_plant_commission(goal: dict, *, commissioned_by: str,
                             _garden_root: pathlib.Path | None = None,
                             _goals_dir: pathlib.Path | None = None,
                             _now: str | None = None):
    """
    Fulfill a dedicated later-plant commissioning goal with one shared timestamp
    for the plant record, commission event, and first-goal handoff.
    """
    payload = plant_commission_payload(goal)
    if payload is None:
        return ValidationResult.reject(
            "MISSING_PLANT_COMMISSION_PAYLOAD",
            "goal.plant_commission is required",
        ), None

    initial_goal = payload.get("initial_goal")
    if not isinstance(initial_goal, dict):
        return ValidationResult.reject(
            "INVALID_PLANT_COMMISSION_INITIAL_GOAL",
            "goal.plant_commission.initial_goal must be a JSON object",
        ), None

    root = (_garden_root or pathlib.Path(".")).resolve()
    goals_dir = _goals_dir if _goals_dir is not None else garden_paths(garden_root=root).goals_dir
    now = _now or _now_utc()

    result = commission_seeded_plant(
        payload["plant_name"],
        payload["seed"],
        commissioned_by,
        _garden_root=root,
        _now=now,
    )
    if not result.ok:
        return result, None

    return submit_initial_goal_for_plant(
        plant_name=payload["plant_name"],
        goal_type=initial_goal["type"],
        submitted_by=commissioned_by,
        body=initial_goal["body"],
        priority=initial_goal.get("priority"),
        depends_on=[goal["id"]],
        driver=initial_goal.get("driver"),
        model=initial_goal.get("model"),
        reasoning_effort=initial_goal.get("reasoning_effort"),
        _goals_dir=goals_dir,
        _now=now,
    )


def read_plant(name: str, *, _plants_dir: pathlib.Path | None = None) -> dict | None:
    """Return plant record or None if not found."""
    plants_dir = _plants_dir if _plants_dir is not None else _PLANTS_DIR
    p = _plant_path(name, plants_dir)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_plants(status: str | None = None,
                *, _plants_dir: pathlib.Path | None = None) -> list[dict]:
    """Return all plants, optionally filtered by status."""
    plants_dir = _plants_dir if _plants_dir is not None else _PLANTS_DIR
    if not plants_dir.exists():
        return []
    plants = []
    for p in sorted(plants_dir.iterdir()):
        if p.is_dir():
            meta = p / "meta.json"
            if meta.exists():
                try:
                    record = json.loads(meta.read_text(encoding="utf-8"))
                    if status is None or record.get("status") == status:
                        plants.append(record)
                except (json.JSONDecodeError, OSError):
                    pass
    return plants


def archive_plant(name: str, *, actor: str = "system",
                  _plants_dir: pathlib.Path | None = None,
                  _now: str | None = None) -> ValidationResult:
    """Archive a plant. Archived plants will not receive new goal dispatches."""
    plants_dir = _plants_dir if _plants_dir is not None else _PLANTS_DIR
    plant = read_plant(name, _plants_dir=plants_dir)
    if plant is None:
        return ValidationResult.reject("PLANT_NOT_FOUND", name)
    if plant["status"] == "archived":
        return ValidationResult.reject(
            "INVALID_TRANSITION", f"plant '{name}' is already archived"
        )

    now = _now or _now_utc()
    plant["status"] = "archived"
    _plant_path(name, plants_dir).write_text(
        json.dumps(plant, indent=2) + "\n", encoding="utf-8"
    )
    events_path = coordinator_events_path(plants_dir.parent)

    append_event({
        "ts": now,
        "type": "PlantArchived",
        "actor": actor,
        "plant": name,
    }, path=events_path)

    return ValidationResult.accept()
