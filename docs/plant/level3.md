# Plant — Agent Contract

This document is self-contained.

---

## What you may do

- **Read** any plant record at `plants/{name}/meta.json`.
- **Read** any plant's `memory/`, `skills/`, and `knowledge/` directories.
- **Write** to your own plant's `memory/`, `skills/`, and `knowledge/`.
- **Submit** a dedicated later-plant commissioning goal via
  `system.submit.submit_plant_commission_goal()`.
- **Execute** that commissioning contract during the dedicated run via
  `system.plants.execute_plant_commission()`.
- **Archive** a plant via `system.plants.archive_plant()`.

## What you must never do

- Write to another plant's `memory/`, `skills/`, or `knowledge/`.
- Modify a plant's `meta.json` directly.
- Create a plant directory by hand.
- Delete a plant directory.

---

## Your identity

You are a plant. Your name is provided to you when the system launches you,
along with your run ID and goal. Your context at launch is:

1. Garden `MOTIVATION.md` — why this garden exists and its principles.
2. Your `memory/` — what you have experienced and learned.
3. Your `skills/` — capabilities you have codified.
4. Your `knowledge/` — domain expertise you have accumulated.

On your first blank-memory run, the driver may read `plants/{your-name}/seed`
and load `seeds/{seed-name}.md`. After your `memory/MEMORY.md` has content,
future runs use memory instead of re-reading the seed.

---

## Commissioning a plant

When the garden needs a new specialist, do not plant it inline inside some
other unrelated goal. Submit a dedicated commissioning goal instead:

```python
from system.submit import submit_plant_commission_goal

result, goal_id = submit_plant_commission_goal(
    submitted_by="gardener",
    plant_name="reviewer",
    seed="reviewer",
    initial_goal_type="evaluate",
    initial_goal_body="Perform the first bounded independent review.",
    _goals_dir=root_path / "goals",
)

if not result.ok:
    raise RuntimeError(result.reason)
```

When that dedicated gardener `build` goal is dispatched, fulfill it through the
seeded helper workflow:

```python
from system.plants import execute_plant_commission

result, first_goal_id = execute_plant_commission(
    goal=current_goal,
    commissioned_by="gardener",
    _garden_root=root_path,
)

if not result.ok:
    # result.reason is a named code
    raise RuntimeError(result.reason)
```

### Required `plant_commission` fields

| Field | Type | Description |
|-------|------|-------------|
| `plant_name` | string | Unique identity. Lowercase alphanumeric with hyphens, starts with letter. |
| `seed` | string | Which seed defines this plant's role. `seeds/{seed}.md` must exist. |
| `initial_goal.type` | string | One of `build`, `fix`, `spike`, `evaluate`, `research`. |
| `initial_goal.body` | string | Non-empty first bounded task for the new plant. |

Commission only when a concrete goal requires it. Do not commission plants
speculatively.

The low-level `commission_seeded_plant()` and
`submit_initial_goal_for_plant()` helpers still exist beneath this surface, but
the normal later-plant path is the dedicated commissioning goal so operators
can see planting as its own goal/run.

## Queueing the first goal

If you are already inside the dedicated commissioning run and need the lower
level helper shape, `execute_plant_commission()` wraps the queueing step for
you. It commissions the plant and then submits the initial goal with an
automatic dependency on the current commissioning goal. The older lower-level
queue helper still exists:

```python
from system.plants import submit_initial_goal_for_plant

result, goal_id = submit_initial_goal_for_plant(
    plant_name="code-surgeon",
    goal_type="build",
    submitted_by="gardener",
    body="Implement the first bounded code task.",
    _goals_dir=root_path / "goals",
)

if not result.ok:
    raise RuntimeError(result.reason)
```

This still submits a normal goal record and emits the normal `GoalSubmitted`
event. It does not bypass the queue.

The current repo ships one built-in specialist starter seed, `reviewer`, for
bounded review/evaluate work. Use another seed only after you have created it
explicitly under `seeds/`.

---

## Reading a plant

```python
from system.plants import read_plant

plant = read_plant("code-surgeon")
# Returns None if not found
```

---

## Archiving a plant

```python
from system.plants import archive_plant

result = archive_plant("code-surgeon")
```

Archive a plant when it has been inactive and its role is no longer needed.
Archival is not deletion — history is preserved.

---

## Your memory

Your memory lives at `plants/{your-name}/memory/`. Write to it to persist
what you learn across runs. Structure it so that it is useful to a future
version of yourself reading it cold.

If you need one compact current-work record separate from `MEMORY.md`, use the
plant-local `Active Threads` artifact at
`plants/{your-name}/memory/active-threads.json`. The write/read helper and
schema contract live in [`docs/active-threads/level3.md`](../active-threads/level3.md).

Your memory is yours alone. You do not write to other plants' memory.

---

## Reason codes

| Code | What to do |
|------|-----------|
| `SEED_NOT_FOUND` | Create or choose a seed with `seeds/{seed}.md` present |
| `PLANT_ALREADY_EXISTS` | Choose a different name |
| `PLANT_NOT_FOUND` | Check the name; use `list_plants()` to see what exists |
| `INVALID_PLANT_NAME` | Use lowercase alphanumeric with hyphens, starting with a letter |
| `INVALID_COMMISSIONED_BY` | Use a valid lowercase agent name for `commissioned_by` |
| `MISSING_SEED` | Provide a non-empty seed name |
| `PLANT_COMMISSION_REQUIRES_BUILD` | Submit `plant_commission` payloads only on `build` goals |
| `PLANT_COMMISSION_REQUIRES_GARDENER` | Assign dedicated commissioning goals to `gardener` |
| `INVALID_PLANT_COMMISSION_PAYLOAD` | Make `plant_commission` a JSON object |
| `UNKNOWN_PLANT_COMMISSION_FIELD` | Remove unsupported keys from `plant_commission` |
| `MISSING_PLANT_COMMISSION_PLANT_NAME` | Add `plant_commission.plant_name` |
| `INVALID_PLANT_COMMISSION_PLANT_NAME` | Use a valid lowercase plant name |
| `MISSING_PLANT_COMMISSION_SEED` | Add `plant_commission.seed` |
| `INVALID_PLANT_COMMISSION_SEED` | Use a valid lowercase seed name |
| `MISSING_PLANT_COMMISSION_INITIAL_GOAL` | Add `plant_commission.initial_goal` |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL` | Make `plant_commission.initial_goal` a JSON object |
| `UNKNOWN_PLANT_COMMISSION_INITIAL_GOAL_FIELD` | Remove unsupported keys from `plant_commission.initial_goal` |
| `MISSING_PLANT_COMMISSION_INITIAL_GOAL_TYPE` | Add `plant_commission.initial_goal.type` |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL_TYPE` | Use one of `build`, `fix`, `spike`, `evaluate`, `research` |
| `MISSING_PLANT_COMMISSION_INITIAL_GOAL_BODY` | Add a non-empty `plant_commission.initial_goal.body` |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL_PRIORITY` | Use an integer from `1` to `10` |
| `INVALID_PLANT_COMMISSION_INITIAL_GOAL_REASONING_EFFORT` | Use one of `low`, `medium`, `high`, `xhigh` |
| `INVALID_TRANSITION` | Plant is already archived |
| `UNKNOWN_ASSIGNED_PLANT` | Commission the plant before assigning a goal to it |
| `ASSIGNED_PLANT_INACTIVE` | Reassign the goal or reactivate/replace the archived plant |
| `MISSING_REQUIRED_FIELD` | Add the missing field |
