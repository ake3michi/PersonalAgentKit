# Plant — Engineer Reference

## What a plant is

A plant is a named agent instance within a garden. It is a context container:
it holds the memory, skills, and knowledge that get loaded when a goal is
dispatched to it. Plants do not own goals or runs — those live at garden level,
with the `assigned_to` field on a goal and the `plant` field on a run linking
back to the plant.

## Schema

See [`schema/plant.schema.json`](../../schema/plant.schema.json).

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Identity within the garden. Lowercase alphanumeric with hyphens, starts with letter. Unique. |
| `seed` | string | The seed name this plant was commissioned from. Records origin in `meta.json`; first-run bootstrap uses `plants/{name}/seed`. |
| `status` | string | `active` or `archived`. |
| `created_at` | ISO 8601 UTC | When the plant was commissioned. |
| `commissioned_by` | string | Agent or operator that created this plant. |

## Directory structure

```
plants/{name}/
  meta.json     ← plant record (this schema)
  seed          ← seed reference the driver reads while memory is still blank
  memory/       ← persistent context across runs
  memory/active-threads.json ← optional canonical current-work tracker
  skills/       ← plant-specific proven capabilities
  knowledge/    ← domain expertise specific to this plant's role
```

Plants do not have their own `<runtime-root>/goals/`, `<runtime-root>/runs/`,
or `<runtime-root>/inbox/` subtrees. Those runtime artifacts live in the
shared garden runtime root, while plant-local authored state stays under
`plants/{name}/`. The garden presents as a single identity to the operator —
plants are internal.

## Lifecycle

| Status | Meaning |
|--------|---------|
| `active` | Available for goal dispatch |
| `archived` | Retired; will not receive new goals |

The only valid transition is `active → archived`. Archival is not deletion —
the plant directory and its history remain.

## Context loading

When a goal is dispatched to a plant, the driver constructs the agent context
from:

1. Garden `MOTIVATION.md` — shared identity and principles (loaded for all plants)
2. Plant `memory/` — what this plant has experienced
3. Plant `skills/` — what this plant knows how to do
4. Plant `knowledge/` — what this plant knows about its domain

If `memory/MEMORY.md` is blank, the driver bootstraps from `plants/{name}/seed`
and loads `seeds/{seed-name}.md`. After the plant has written memory, later
runs load memory/skills/knowledge instead and do not re-enter seed bootstrap
from `plants/{name}/seed`.

Normal later-plant commissioning should first submit a dedicated gardener
`build` goal through `system.submit.submit_plant_commission_goal()`. When that
goal runs, apply the contract with `system.plants.execute_plant_commission()`.
That execution helper:

1. commissions the plant through the shared seeded helper
2. writes `plants/{name}/seed`
3. copies `seeds/{seed}/skills/` and `seeds/{seed}/knowledge/`
4. queues the new plant's first bounded goal through the normal goal
   submission boundary with an automatic dependency on the commissioning goal

The lower-level `system.plants.commission_seeded_plant()` and
`system.plants.submit_initial_goal_for_plant()` helpers still exist beneath
that dedicated goal surface for tests and carefully bounded manual use.

The current template ships two built-in seeds:

- `gardener` — the executive faculty used at genesis
- `reviewer` — a minimal specialist for bounded review/evaluate work

## The gardener

The gardener is always the first plant. It is commissioned at garden genesis
with `commissioned_by: "operator"` (or the genesis process). All other plants
are commissioned by the gardener, on demand, when the garden needs a new kind
of specialist.

## Failure modes

| Reason code | When it occurs |
|-------------|---------------|
| `MISSING_REQUIRED_FIELD` | A required field is absent |
| `INVALID_PLANT_NAME` | `name` does not match the plant-name pattern |
| `INVALID_COMMISSIONED_BY` | `commissioned_by` does not match the actor pattern |
| `INVALID_STATUS` | `status` is not `active` or `archived` |
| `INVALID_TIMESTAMP` | `created_at` is not ISO 8601 UTC |
| `MISSING_SEED` | `seed` is empty |
| `SEED_NOT_FOUND` | `commission_seeded_plant()` or `materialize_seed_context()` cannot find `seeds/{seed}.md` |
| `PLANT_ALREADY_EXISTS` | `commission_plant()` called with a name that exists |
| `PLANT_NOT_FOUND` | `archive_plant()` called with unknown name |
| `INVALID_TRANSITION` | Archiving a plant that is already archived |
| `INVALID_SHAPE` | Record is not a JSON object |

## Validation

`system/validate.py:validate_plant(data)` validates the plant record written by
`system.plants.commission_plant()`. The seeded commissioning path in
`system.plants.commission_seeded_plant()` builds on that validator and then
checks that the referenced seed prompt exists before writing the driver-facing
seed reference file. The dedicated later-plant commissioning goal surface is
validated earlier at goal submission time via
`system.validate.validate_goal()` and the
[`schema/goal-plant-commission.schema.json`](../../schema/goal-plant-commission.schema.json)
payload contract.

## Tests

`tests/test_validate.py` — `PlantValidationTests`.
`tests/test_plants.py` — `SeededPlantCommissioningTests`,
`InitialPlantGoalSubmissionTests`, and `SeededPromptBootstrapTests`.
`tests/test_plant_commission.py` — dedicated commissioning goal submission,
execution, and prompt-context coverage.
