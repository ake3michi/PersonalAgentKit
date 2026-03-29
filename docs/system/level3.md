# The System — Agent Contract

This document is self-contained.

---

## What the system does for you

The system manages the lifecycle of goals and runs. You do not drive it —
it drives you. Specifically:

- The system dispatches your goal to you when it is ready.
- The system creates your run record before you are launched.
- The system watches your run and closes it if you go silent.
- The system closes your goal when your run finishes.
- In split-runtime-root gardens, the system may also snapshot authored-commit
  provenance for a closed run and commit the runtime tree inside
  `<runtime-root>/.git`.

You observe this through the event log. You do not cause it directly.

## Startup ordering

- `./pak2 genesis` may create plants and queue goals, but it does not dispatch
  runs.
- `./pak2 cycle` owns first dispatch. On a fresh garden with no prior run
  history, it may also ensure the filesystem reply surface exists, open one
  filesystem conversation with `started_by: "system"`, deliver one startup
  note built from recorded bootstrap facts on that surface, and record that
  note only after delivery succeeds, before ordinary queued work is
  dispatched. Before that system-started conversation receives any operator
  message, the coordinator may append additional `sender: "system"` startup
  updates for the first non-`converse` run, but only when the content is a
  direct rendering of recorded lifecycle facts. If that run creates a new
  filesystem reply note, the coordinator may also append a `sender: "garden"`
  conversation message whose content represents the note body with explicit
  provenance to the source run and original note path.

---

## What you may do

- **Persist the chosen garden name** via `system.garden.set_garden_name()`.
- **Submit goals** via `system.submit.submit_goal(data)`.
- **Submit a dedicated later-plant commissioning goal** via
  `system.submit.submit_plant_commission_goal(...)`.
- **Submit a code-changing same-initiative tranche together with its mandatory
  follow-on evaluate stop** via
  `system.submit.submit_same_initiative_code_change_with_evaluate(...)`.
- **Append a pre-dispatch supplement** via
  `system.submit.append_goal_supplement(goal_id, data)`.
- **Submit a manual retrospective** via
  `system.submit.submit_retrospective_goal(...)`. The contract is documented in
  [`docs/retrospective/level3.md`](../retrospective/level3.md).
- **Run** `pak2 publish <dir>` to materialize a clean authored export worktree
  in another directory. In the current first slice it excludes live
  garden/runtime state and stops before any commit, push, tag, or release
  action.
- **Read** any goal file in `<runtime-root>/goals/`.
- **Read** any run record in `<runtime-root>/runs/`.
- **Read** the event log at `<runtime-root>/events/coordinator.jsonl`.
- **Read** any plant record in `plants/`.
- **Run** the read-only live dashboard, or call its snapshot helpers, when you
  need a quick composite view. The current contract is documented in
  [`docs/dashboard/level3.md`](../dashboard/level3.md).
- **Read or write** your plant-local `Active Threads` artifact via
  `system.active_threads`. The contract is documented in
  [`docs/active-threads/level3.md`](../active-threads/level3.md).
- **Read or write** your plant-local `Initiative` records via
  `system.initiatives`. The contract is documented in
  [`docs/initiative/level3.md`](../initiative/level3.md).
- **Write** to your own plant's `memory/`, `skills/`, and `knowledge/`.
- **Commission** new plants via `system.plants.commission_seeded_plant()`.

## What you must never do

- Write to any goal file.
- Write to any run's `meta.json` — the system manages this.
- Append to the event log directly.
- Write to another plant's `memory/`, `skills/`, or `knowledge/`.

---

## Submitting a goal

```python
from system.submit import submit_goal

result, goal_id = submit_goal({
    "type": "fix",
    "submitted_by": "your-agent-name",
    "body": "Fix the broken state machine transition.",
})

if not result.ok:
    # result.reason is a named code (see below)
    # result.detail has human-readable context
    raise RuntimeError(result.reason)
```

`goal_id` is `None` on failure. On success it is the system-assigned ID
(e.g. `"42-fix-the-state-machine-transition"`).

When `submit_goal()` is called from a running goal, the submission API also
records `submitted_from` metadata automatically. When it is called from a
`converse` run for durable non-`converse` work, it also auto-tags the goal's
conversation origin and supplement policy.

If a later conversation turn needs to clarify that still-queued durable goal
without replacing the task entirely, use `append_goal_supplement()` instead of
rewriting the goal body. The full supplement/dispatch contract lives in
[`docs/goal-supplement/level3.md`](../goal-supplement/level3.md).

For bounded tend passes, prefer the helper below instead of a raw
`type: "tend"` payload:

```python
from system.submit import submit_tend_goal

result, goal_id = submit_tend_goal(
    body="Perform a bounded tend pass for the requested garden-state survey.",
    submitted_by="gardener",
    trigger_kinds=["operator_request"],
)
```

`submit_tend_goal()` deduplicates against any already queued or running tend
goal, records `trigger_kinds` plus `trigger_goal` / `trigger_run` when known,
and under the current runtime ordering defaults to:

- `priority: 6` for `operator_request`
- `priority: 4` for `post_genesis`, `run_failure`, and `queued_attention_needed`

When the trigger is `post_genesis`, the current startup contract keeps the
runtime primitive as `tend` but frames the operator-facing work as bounded
environment orientation of the local garden.

The current automatic background trigger pair built on top of this helper is
documented in [`Automatic Tend Submission`](../automatic-tend/level3.md).

For later-plant commissioning, prefer this helper:

```python
from system.submit import submit_plant_commission_goal

result, goal_id = submit_plant_commission_goal(
    submitted_by="gardener",
    plant_name="reviewer",
    seed="reviewer",
    initial_goal_type="evaluate",
    initial_goal_body="Perform the first bounded independent review.",
)
```

That helper submits an ordinary `build` goal for `gardener` with a structured
`plant_commission` payload. When the dedicated commissioning run starts, fulfill
it with `system.plants.execute_plant_commission(...)` so the plant record,
PlantCommissioned event, and first-goal handoff share one run-local timestamp.

For a bounded manual retrospective, prefer this helper:

```python
from system.submit import submit_retrospective_goal

result, goal_id = submit_retrospective_goal(
    submitted_by="gardener",
    recent_run_limit=5,
    allow_follow_up_goal=False,
)
```

That helper submits an ordinary `evaluate` goal for `gardener` with an
explicit `retrospective` payload. It does not create a new goal type or start
background automation. For the raw payload contract, see
[`Manual Retrospective`](../retrospective/level3.md).

For a clean tranche-closing `evaluate` run that is advancing to the next
approved code-changing tranche inside the same initiative, prefer this helper:

```python
from system.submit import submit_same_initiative_code_change_with_evaluate

result, goal_ids = submit_same_initiative_code_change_with_evaluate(
    submitted_by="gardener",
    implementation_goal_type="build",
    implementation_body="Execute the next approved same-initiative tranche.",
    evaluate_body="After the implementation goal closes successfully, perform the mandatory tranche-closing evaluate stop.",
    implementation_depends_on=["162-after-goal-160-re-read-the-goal-152-disc"],
)
```

That helper queues exactly two goals:

- one `build` or `fix` implementation goal
- one `evaluate` goal that depends on that implementation goal

Use it only for same-initiative code-changing tranche launches. Do not use it
for discovery/research tranches, general multi-goal chains, or `closure-review`.

If you bypass the helper and submit a raw `type: "tend"` goal, the
stable contract surface is [Goal — Engineer Reference](../goal/level2.md).
That document points at
[`schema/goal-tend.schema.json`](../../schema/goal-tend.schema.json) and the
`system.validate.validate_goal()` submission boundary that enforces raw tend
payloads.

### Fields you must provide

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | `build`, `fix`, `spike`, `tend`, `evaluate`, `research`, `converse` |
| `submitted_by` | string | Your agent name. Lowercase alphanumeric, starts with letter. |
| `body` | string | Non-empty description of the work. |

### Fields you may provide

| Field | Type | Description |
|-------|------|-------------|
| `assigned_to` | string | Plant to run this goal. Lowercase alphanumeric starting with a letter. If absent, goal waits in `queued` until assigned. |
| `priority` | integer | 1 (highest) to 10 (lowest). Default: 5. |
| `depends_on` | array | List of goal IDs that must close before this runs. |
| `not_before` | ISO 8601 UTC | Earliest dispatch time. Must not be in the past. |
| `spawn_eval` | boolean | If true on an eligible `build` / `fix` goal, the system creates exactly one evaluate goal on success. |
| `driver` | string | Execution driver. Default when the goal omits it and neither `PAK2_DEFAULT_DRIVER` nor `[defaults].driver` is set: `"codex"`. |
| `model` | string | Model identifier. Default when the goal omits it and neither `PAK2_DEFAULT_MODEL` nor `[defaults].model` is set: `"gpt-5.4"` for the resolved driver. |
| `conversation_id` | string | Required when `type` is `"converse"`. ID of the open conversation to continue. |

### Fields you must not provide

`id`, `status`, `submitted_at`, `parent_goal`, `closed_reason` — these are
system-assigned. Including them causes `SUBMISSION_REJECTED`.

---

## Your run record

Your run record is at `<runtime-root>/runs/<your-run-id>/meta.json`. The system writes and
manages this file — you do not write to it. Read it to understand your
context: your run ID, goal, plant, driver, and model.

Your raw output goes to `<runtime-root>/runs/<your-run-id>/events.jsonl` via the driver.
The driver reads this stream at close to extract cost, reflection, and
status, then finalizes `meta.json`.

If the garden uses a split runtime root, the close path may also write
`<runtime-root>/history/commits/<your-run-id>.json` and commit the runtime
tree with authored-commit provenance. That provenance file is system-managed.

The runtime-history helper publishes these exact outcome codes:
`committed`, `runtime_root_not_split`, `runtime_root_outside_garden`,
`authored_repo_unavailable`, `record_write_failed`,
`runtime_repo_init_failed`, `runtime_repo_stage_failed`,
`no_runtime_changes`, `runtime_repo_diff_failed`,
`runtime_repo_commit_failed`, and `runtime_repo_head_unavailable`.
Only `committed` guarantees a new runtime-history commit. The other outcomes
explain why the close path skipped capture or stopped after a bounded failure;
they do not authorize mutating the terminal run record after close.

See [run/level3.md](../run/level3.md) for the full run record contract.

---

## Garden config

`PAK2.toml` is the garden-level config file. It can contain:

```toml
[runtime]
root = ".runtime"

[defaults]
driver = "codex"
model = "gpt-5.4"
reasoning_effort = "xhigh"

[garden]
name = "arbor"
```

`[runtime].root` selects the runtime subtree relative to the garden root. If
it is absent, the runtime falls back to the legacy layout where runtime churn
lives directly under the garden root.

`[garden].name` is the authoritative filesystem reply directory name. When it
is set to `arbor`, filesystem conversation replies go to
`<runtime-root>/inbox/arbor/`. If it is absent, the runtime falls back to the
legacy reply directory name `garden`.

Use:

```python
from system.garden import garden_paths, set_garden_name

result = set_garden_name("arbor")
if not result.ok:
    raise RuntimeError(result.reason)

paths = garden_paths()
print(paths.runtime_root)               # .runtime/
print(paths.goals_dir)                  # .runtime/goals/
print(paths.coordinator_events_path)    # .runtime/events/coordinator.jsonl
print(paths.operator_inbox_dir)         # .runtime/inbox/operator/
```

Failure modes:

- `INVALID_GARDEN_NAME` — the name is not lowercase alphanumeric with optional hyphens, starting with a letter
- `IO_ERROR` — the system could not read or write `PAK2.toml`

Reply-directory migration policy:

- if `<runtime-root>/inbox/garden/` exists and `<runtime-root>/inbox/<name>/` does not, the filesystem channel renames the legacy directory to the configured one on first reply delivery
- if both directories already exist, the runtime preserves both and writes new replies only to `<runtime-root>/inbox/<name>/`

---

## What the driver loads into your context

When you are launched, the driver assembles your prompt in this order:

1. **Garden motivation** — contents of `MOTIVATION.md`. Always present.

2. **Plant context** — one of:
   - **First blank-memory run** (your `memory/MEMORY.md` is absent or blank):
     your seed file is loaded (`seeds/<seed-name>.md`). This is your identity
     bootstrap. Seed-local `skills/` and `knowledge/` assets are copied into
     your plant directory during seeded commissioning for later normal runs.
   - **Normal run** (your `memory/MEMORY.md` has content): your `MEMORY.md`
     is loaded in full. Your `skills/` and `knowledge/` directories are
     provided as an index — filenames and first headings only. Read the
     individual files that are relevant to your current task.

3. **Run context** — your run ID, goal ID, goal type, goal priority, plant
   name, and run directory (`<runtime-root>/runs/<run-id>/`). Write run-local artifacts to
   that directory.

4. **Tend context** — for tend goals only, the trigger metadata and origin
   hints that explain why this bounded tend exists.

5. **Plant commission context** — for dedicated later-plant commissioning
   goals only, the target plant, seed, and initial-goal contract.

6. **Task** — the goal body: the specific work you are asked to do.

7. **Working directory** — `"Your working directory is <path>. Begin."`

Sections are separated by `---`. Read them in order — motivation first,
then your accumulated context, then the specific task.

---

## Reflection

Reflection is solicited as a separate run after a goal completes — you will
not be asked for it within the same run. For goal types that require it
(`build`, `fix`, `evaluate`, `tend`), the system will open a follow-up run
and ask explicitly. When that happens: state what you learned, not what you
did. Reflection must be non-empty and non-whitespace.

---

## What the system guarantees to you

- **Your goal will be dispatched.** If the coordinator is running, queued
  goals are dispatched on the next reconcile pass.
- **Your run record exists before you start.** The system writes it before
  calling the dispatch function. You will never be launched without a run ID.
- **If you go silent, you will be closed.** The watchdog uses
  `<runtime-root>/runs/<your-run-id>/events.jsonl` mtime as its liveness signal, falling
  back to `started_at` until that file exists, and closes silent runs as
  `killed`. You do not need to handle this case — it is handled for you.
- **Every transition is recorded.** You can reconstruct the full history of
  any goal or run from the event log alone.

---

## Reading the event log

The event log at `<runtime-root>/events/coordinator.jsonl` is append-only. Read it
sequentially. The last `GoalTransitioned` event for a goal is its current
state.

See [event/level3.md](../event/level3.md) for the full event contract.

---

## Conversations

For back-and-forth exchanges with the operator, use the conversation system.
See [conversation/level3.md](../conversation/level3.md) for the full contract.

Summary: submit a `converse` goal with `conversation_id` set, or use
`system.conversations.append_message()` to add a message to an existing
conversation. The somatic loop handles inbound messages automatically — you
only need to interact with conversations when your goal explicitly involves one.
Within a converse run, default to delegating substantive work into a normal
non-`converse` goal instead of doing it inline.

Do not use `append_message()` as a bypass for gardener-authored `tend_survey`
or `recently_concluded` notes. Those two classes must go through
[`Operator Messages`](../operator-message/level3.md) in this slice.

---

## Rejection reason codes

Goal submission rejections come from `submit_goal()` and
`system.validate.validate_goal()`. For raw `type: "tend"` submissions,
[Goal — Engineer Reference](../goal/level2.md) is the authoritative contract
surface; the tend-specific rows below are a convenience summary.

| Code | What to do |
|------|-----------|
| `MISSING_REQUIRED_FIELD` | Add the missing field |
| `SUBMISSION_REJECTED` | Remove the system-assigned field you included |
| `INVALID_TYPE` | Use: `build`, `fix`, `spike`, `tend`, `evaluate`, `research`, `converse` |
| `INVALID_PRIORITY` | Use an integer from 1 to 10 |
| `EMPTY_BODY` | Provide a non-empty body |
| `INVALID_SUBMITTED_BY` | Use lowercase alphanumeric starting with a letter |
| `INVALID_DEPENDS_ON_FORMAT` | Each entry must be a valid goal ID |
| `INVALID_TIMESTAMP` | Use ISO 8601 UTC: `2026-03-18T12:00:00Z` |
| `NOT_BEFORE_BEFORE_SUBMITTED` | `not_before` must not be in the past |
| `MISSING_TEND_PAYLOAD` | Add the required `tend` object to raw `type: "tend"` submissions |
| `INVALID_TEND_PAYLOAD` | Make `tend` a JSON object |
| `UNKNOWN_TEND_FIELD` | Remove unsupported keys from `tend` |
| `MISSING_TEND_TRIGGER_KINDS` | Add `tend.trigger_kinds` |
| `INVALID_TEND_TRIGGER_KINDS` | Use a non-empty array of unique trigger strings |
| `INVALID_TEND_TRIGGER_KIND` | Use a known tend trigger kind |
| `INVALID_TEND_TRIGGER_GOAL` | Use a valid goal ID in `tend.trigger_goal` |
| `INVALID_TEND_TRIGGER_RUN` | Use a valid run ID in `tend.trigger_run` |
| `RETROSPECTIVE_REQUIRES_EVALUATE` | Submit retrospective payloads only on `evaluate` goals |
| `INVALID_RETROSPECTIVE_PAYLOAD` | Make `retrospective` a JSON object |
| `UNKNOWN_RETROSPECTIVE_FIELD` | Remove unsupported keys from `retrospective` |
| `MISSING_RETROSPECTIVE_WINDOW` | Add `retrospective.window` |
| `INVALID_RETROSPECTIVE_WINDOW` | Use a known retrospective window policy |
| `MISSING_RETROSPECTIVE_RECENT_RUN_LIMIT` | Add `retrospective.recent_run_limit` |
| `INVALID_RETROSPECTIVE_RECENT_RUN_LIMIT` | Use an integer from `3` to `10` |
| `MISSING_RETROSPECTIVE_ACTION_BOUNDARY` | Add `retrospective.action_boundary` |
| `INVALID_RETROSPECTIVE_ACTION_BOUNDARY` | Use a known retrospective action boundary |
| `CONVERSATION_NOT_FOUND` | The conversation_id does not exist or is closed |
| `EMPTY_CONTENT` | Message content must not be empty or whitespace-only |
