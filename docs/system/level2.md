# The System — Engineer Reference

## Architecture

The system is built from a set of core modules in `system/`:

| Module | Responsibility |
|--------|---------------|
| `validate.py` | Schema validation for goals, events, runs, plants, and dashboard invocation records. Never raises. |
| `events.py` | Append to and read from `<runtime-root>/events/coordinator.jsonl`. |
| `goals.py` | Goal store: submit, read, list, and transition goals in `<runtime-root>/goals/`. |
| `runs.py` | Run store: open, read, close, and list runs in `<runtime-root>/runs/`. |
| `plants.py` | Plant store: commission, archive, read, and list plants in `plants/`. |
| `active_threads.py` | Read/write helper for the plant-local `Active Threads` artifact at `plants/<plant>/memory/active-threads.json`. The contract is documented in [`docs/active-threads/level2.md`](../active-threads/level2.md). |
| `operator_messages.py` | Validated gardener-owned emission surface for `tend_survey` and `recently_concluded`, with run-local durable records and reply-surface separation. The contract is documented in [`docs/operator-message/level2.md`](../operator-message/level2.md). |
| `submit.py` | Public API surface. Re-exports `submit_goal`, `append_goal_supplement`, `submit_tend_goal`, `submit_plant_commission_goal`, and `submit_retrospective_goal`. The supplement/dispatch contract is documented in [`docs/goal-supplement/level2.md`](../goal-supplement/level2.md), the current automatic background tend trigger pair is documented in [`docs/automatic-tend/level2.md`](../automatic-tend/level2.md), the later-plant commissioning helper is documented in [`docs/plant/level2.md`](../plant/level2.md), and the manual retrospective helper is documented in [`docs/retrospective/level2.md`](../retrospective/level2.md). |
| `plant_commission.py` | Constants and helpers for the dedicated later-plant commissioning goal payload, body rendering, and prompt context. |
| `retrospective.py` | Constants and helpers for the manual retrospective submission contract. |
| `export_surface.py` | Shared authored-export helper used by `pak2 init` and the clean authored-export surface. |
| `coordinator.py` | Reconcile loop: dispatch, watchdog, goal close, and the current automatic tend submission policy for failed runs and queued-attention review. |
| `driver.py` | Driver orchestration: resolves driver/model settings, builds prompt/context, routes standard vs. conversation dispatch, launches the selected backend plugin, and finalizes the run record. |
| `driver_plugins.py` | Driver plugin registry and built-in backends. Resolves default driver/model/reasoning-effort settings and owns backend-specific launch/parsing behavior. |
| `runtime_history.py` | Split-runtime-root provenance helper: captures authored-commit provenance at run close and commits runtime churn inside the runtime root's nested git repo. |
| `dashboard.py` | Read-only snapshot/renderer pair for the live observability dashboard. The current capability contract is documented in [`docs/dashboard/level2.md`](../dashboard/level2.md). |
| `dashboard_invocations.py` | Dashboard invocation observability: emits start/finish events and writes one measured wall-time cost record per completed dashboard CLI session. |

All state is on disk. No in-memory state is authoritative. A cold restart
produces the same behavior as a warm one.

The current operator-facing read-only composite surface over those files is the
[`Live Observability Dashboard`](../dashboard/level2.md).

The current operator-facing manual retrospective surface is documented in
[`Manual Retrospective`](../retrospective/level2.md).

For plant-local current-work tracking without reconstructing from all of those
files, see [`Active Threads`](../active-threads/level2.md).

## File layout

```
<runtime-root>/goals/                          one JSON file per goal
<runtime-root>/events/coordinator.jsonl        append-only event log
<runtime-root>/runs/<run-id>/meta.json         run record (system-managed)
<runtime-root>/runs/<run-id>/events.jsonl      raw agent output stream (driver-managed)
<runtime-root>/runs/<run-id>/operator-messages.jsonl validated operator-message emission records
<runtime-root>/history/commits/<run-id>.json   split-root runtime provenance record for a closed run
PAK2.toml                                      garden-wide config: [runtime].root + defaults + [garden].name
<runtime-root>/dashboard/invocations/<id>.json dashboard invocation cost record
plants/<name>/meta.json                        plant record
plants/<name>/memory/                          plant's persistent context
plants/<name>/memory/active-threads.json       optional current-work artifact
plants/<name>/skills/                          plant's proven capabilities
plants/<name>/knowledge/                       plant's domain expertise
seeds/<seed>.md                                seed prompt loaded on a plant's first blank-memory run
seeds/<seed>/skills/                           seed-local skills copied into plants/<name>/skills/ during seeded commissioning
seeds/<seed>/knowledge/                        seed-local knowledge copied into plants/<name>/knowledge/ during seeded commissioning
MOTIVATION.md                                  garden-wide identity, loaded for every run
```

Code should resolve those runtime locations through `system.garden.garden_paths()`
instead of manually stitching `root / "goals"` and similar joins. When
`PAK2.toml` omits `[runtime].root`, the runtime keeps the legacy layout with
`runtime_root == garden_root`. When `[runtime].root = ".runtime"` (the current
init default), runtime churn moves under `.runtime/` while authored paths like
`plants/`, `seeds/`, and `MOTIVATION.md` stay at the garden root.

In the current first `runtime-auto-commit` slice, only terminal run closure
captures runtime git history. When the runtime root is split and the garden
root has a git `HEAD`, `close_run()` writes
`<runtime-root>/history/commits/<run-id>.json`, initializes `<runtime-root>/.git`
on first use, and commits the runtime tree with the authored `HEAD` commit plus
an authored-tree `clean`/`dirty` marker. Dashboard-only runtime churn is not
yet auto-committed by this slice.

`close_run()` drives that helper through the following named outcome contract:

| Outcome code | Contract |
|-------------|----------|
| `committed` | Runtime-history capture completed and the nested runtime repo advanced to a new commit. |
| `runtime_root_not_split` | Runtime-history auto-commit is inactive because the runtime root still matches the garden root. |
| `runtime_root_outside_garden` | The resolved runtime root is not under the garden root, so the helper stops before reading authored provenance. |
| `authored_repo_unavailable` | The authored repo `HEAD` or authored-tree clean/dirty probe failed, so runtime-history capture never starts. |
| `record_write_failed` | Writing `<runtime-root>/history/commits/<run-id>.json` failed after provenance was gathered. |
| `runtime_repo_init_failed` | The runtime record was written, but `git init` or required runtime-repo identity configuration failed. |
| `runtime_repo_stage_failed` | The runtime record was written, but `git add -A -- .` in the runtime root failed. |
| `no_runtime_changes` | The runtime record was written and staged, but there was no new cached diff to commit for this capture. |
| `runtime_repo_diff_failed` | The runtime record was written and staged, but `git diff --cached --quiet --exit-code` failed unexpectedly. |
| `runtime_repo_commit_failed` | The runtime-history commit command failed after staging found changes. |
| `runtime_repo_head_unavailable` | The runtime-history commit succeeded, but reading back the runtime repo `HEAD` failed. |

For any attempted but non-`committed` outcome, `close_run()` keeps the run
closed and logs the runtime-history failure detail to stderr instead of
rewriting the terminal run record.

## Startup contract

Fresh-garden startup is intentionally split:

For first-time use, the current public fast path is `./pak2 init` -> `cd` ->
`./pak2 genesis` -> `./pak2 cycle`, with `./pak2 chat` in another terminal and
`./pak2 dashboard` as an optional read-only view.

- `./pak2 init` copies the authored template surface, writes a ready-to-run
  `CHARTER.md` from `examples/charter-quickstart.md`, and still carries
  `CHARTER.md.example` plus `examples/` as the customization surface.
- `./pak2 genesis` validates prerequisites, creates runtime directories,
  commissions `gardener`, commits any newly materialized durable source-side
  bootstrap scaffold (commissioned gardener metadata/seed assets, plus
  `CHARTER.md` when that file is present but not yet tracked) when the garden
  root is a git repo, queues the first gardener goal, and stops before
  dispatch.
- `./pak2 cycle` owns first dispatch. Before any run has started, it may first
  ensure the filesystem reply surface exists, then open one filesystem
  conversation with `started_by: "system"`, deliver a compact startup note
  from recorded bootstrap facts on that surface, record that note in
  conversation history only after delivery succeeds, and then continue into
  the ordinary coordinator path. During the same startup window, before that
  system-started conversation has received an operator message, the
  coordinator may append short play-by-play updates for the first durable run,
  but only from already-recorded lifecycle facts. If that run writes a new
  filesystem reply note, the coordinator may also append one `sender: "garden"`
  conversation message per new note so the substantive reply is readable
  in-thread with source run and note-path provenance.

For gardener-authored tend survey and recently concluded notes outside that
startup path, use the validated [`Operator Messages`](../operator-message/level2.md)
surface instead of raw inbox writes.

This keeps the startup message honest: it only appears once a real reply
surface exists, and it does not pretend the operator opened the thread.
That staged startup contract is the current owned surface, not a claim that
richer optional launch contracts are invalid. A tmux-assisted or otherwise
orchestrated startup can be added later once the product owns the needed
process-control behavior.

## Authored export contract (maintainer-facing)

`pak2 publish <dir>` remains the bounded authored-export surface for
maintainer/internal handoff work. It is not part of the first-run path.

- It exports the same authored surface `pak2 init` copies for a fresh garden:
  `system/`, `seeds/`, `docs/`, `schema/`, `tests/`, `pak2`, `README.md`,
  `LICENSE`, `MOTIVATION.md`, `GARDEN.md`, `DONE.md`, `PAK2.toml.example`,
  `CHARTER.md.example`, and `examples/`.
- It also writes fresh export-local artifacts into the destination:
  `.gitignore` and `PAK2.toml`.
- It keeps `examples/charter-quickstart.md` in the published source surface
  and leaves root `CHARTER.md` materialization to `pak2 init`.
- It excludes live or garden-local paths such as `.runtime/`, `goals/`,
  `runs/`, `events/`, `conversations/`, `inbox/`, `dashboard/`, and
  `plants/`.
- The destination may be empty or may already be a git checkout. For an
  existing checkout, the command preserves `.git` and replaces the rest of the
  worktree contents with the clean export.
- A non-empty destination without `.git` is rejected instead of being cleaned.
- The current first slice is filesystem-only: it does not run `git init`,
  `git add`, `git commit`, `git push`, create a tag, or publish a release.
- The current first slice also stops at this bounded authored export surface.
  Beyond the shipped authored files plus export-local `.gitignore` and
  `PAK2.toml`, it does not yet add packaging metadata or additional public
  assets beyond that bounded surface.

## The reconcile loop

`coordinator.reconcile()` is a single pass. The coordinator calls it on a
timer (default: every 60 seconds). Each pass does three things, in order:

### 1. Dispatch queued goals

For each goal in `queued` status:

```
queued → dispatched           (GoalTransitioned emitted)
open_run(goal, plant, ...)    (run record created, RunStarted emitted)
dispatched → running          (GoalTransitioned emitted)
_dispatch_fn(goal, run_id)    (agent subprocess launched)
```

The plant is resolved from the goal's `assigned_to` field. Goals without
an explicit `assigned_to` are skipped (added to `summary["skipped"]`) and
remain in `queued` status until assigned.

If `open_run()` fails, the goal is rolled back to `queued`.

### 2. Watchdog

For each goal in `running` status, find its active run. If
`<runtime-root>/runs/<run-id>/events.jsonl` exists, use that file's mtime as the liveness
signal. If it does not exist, fall back to the run record's `started_at`.
If the elapsed time exceeds the watchdog threshold (default: 300 seconds),
the run is closed with `status: "killed"` and the goal is closed with
`closed_reason: "failure"`.

### 3. Close completed goals

For each goal in `running` status whose run has reached a terminal status
(`success`, `failure`, `killed`, `timeout`, `zero_output`), transition the goal:

```
running → completed    (GoalTransitioned emitted)
completed → closed     (GoalTransitioned + GoalClosed emitted)
```

`closed_reason` is `"success"` if the run succeeded, `"failure"` otherwise.

The current coordinator also submits bounded tend goals around this lifecycle:

- after the current pass has already selected and claimed any ordinary
  dispatch work, it may queue a tend for `queued_attention_needed`
- after a failed background run or watchdog kill, it may queue a tend for
  `run_failure`

See [`Automatic Tend Submission`](../automatic-tend/level2.md) for the exact
trigger and non-trigger contract.

## Driver

`driver.dispatch(goal, run_id)` is the production dispatch function. The
coordinator calls it by default; tests may inject a different function via
`_dispatch_fn`.

`system/driver.py` is the orchestration layer, not a Claude-specific backend
implementation. Backend-specific command construction and event parsing live in
`system/driver_plugins.py`.

Driver selection is resolved in this order:

1. `goal["driver"]`
2. `PAK2_DEFAULT_DRIVER`
3. `[defaults].driver` from `PAK2.toml`
4. fallback `"codex"`

`model` and `reasoning_effort` follow the same goal -> env -> `PAK2.toml`
default override chain, then fall back to the selected plugin's built-in
defaults. The built-in plugins currently register:

- `codex` with default model `gpt-5.4` and default reasoning effort `xhigh`
- `claude` with default model `claude-opus-4-6` and no default reasoning-effort override

### What the driver does

1. **Resolves routing and configuration**:
   - ordinary goals dispatch through the standard prompt/launch/finalize path
   - goals with `conversation_id` dispatch through the conversation handler
   - goals with `post_reply_hop` dispatch through the post-reply-hop path
   - the selected driver plugin determines backend command format, session
     continuity semantics, and event parsing

2. **Builds the prompt** from garden-wide and plant-specific context:
   - `MOTIVATION.md` is always prepended.
   - **First blank-memory run** (plant has no `MEMORY.md`, or it is blank):
     the plant's seed file is loaded as system context.
     Seed-local `skills/` and `knowledge/` assets are copied into the plant
     directory during seeded commissioning so later normal runs inherit them.
   - **Normal run** (plant has memory): `memory/MEMORY.md`, all `.md` files
     in `skills/`, and all `.md` files in `knowledge/` are loaded.
   - For eligible conversation-origin durable goals, the driver first freezes
     `<runtime-root>/runs/<run-id>/dispatch-packet.json` and then injects its supplements into
     the prompt. See [`docs/goal-supplement/level2.md`](../goal-supplement/level2.md).
   - Run ID, goal ID, goal type, goal priority, and task body are always appended.
   - Tend goals also receive a `# Tend Context` block with trigger metadata.
   - Dedicated later-plant commissioning goals also receive a
     `# Plant Commission Context` block with the target plant, seed, and first
     bounded-goal handoff contract.

3. **Launches the selected backend subprocess** through
   `get_driver_plugin(driver_name).build_launch_command(...)`.
   The prompt is passed via stdin. Stdout is written directly to
   `<runtime-root>/runs/<run-id>/events.jsonl`. Stderr is forwarded to the coordinator's
   own stderr for visibility. Current built-in launch shapes are:

   - `codex`: `codex exec [resume] --json --model <model> --output-last-message <path> ...`
   - `claude`: `claude --model <model> --output-format stream-json --verbose ... [--resume <session_id>]`

4. **Finalizes the run record**:
   - parses backend artifacts through the selected plugin's `parse_events()`,
     `parse_session_id()`, and `extract_last_text()` helpers
   - for conversation runs, records the reply, updates conversation
     continuity state, and delivers the reply through the channel
   - for successful goal types that require reflection, continues the same
     backend session/thread through `build_reflection_command(...)` before
     calling `close_run()`
   - in split-runtime-root mode, the close path also records one runtime
     provenance artifact and advances the nested runtime git history for the
     just-closed run

### Prompt structure

The assembled prompt contains, in order:

1. `# Garden Motivation` — `MOTIVATION.md`
2. Plant context (one of):
   - **First blank-memory run**: the plant's seed file
   - **Normal**: `# Your Memory` (full), then `# Your Skills` and
     `# Your Knowledge` as indexes (filenames + headings, not full content)
3. `# Your Current Run` — run ID, goal ID, type, priority, plant, run directory
4. `# Tend Context` — only for tend goals; trigger metadata and origin hints
5. `# Plant Commission Context` — only for dedicated later-plant
   commissioning goals; target plant, seed, and initial-goal contract
6. `# Task` — the goal body
7. `"Your working directory is <path>. Begin."`

Skills and knowledge are indexed rather than injected in full, so the agent
reads only the files relevant to its current task.

### Plugin parsing and cost extraction

`driver.py` does not hard-code any backend event schema. It asks the selected
plugin to parse `events.jsonl` and return `(cost, status)`, plus any session
continuity identifier or final text needed elsewhere.

Current built-in behavior:

- `claude` parses the CLI `result` event for `usage`, `total_cost_usd`,
  subtype-based success/failure, `session_id`, and the final assistant text
- `codex` parses the latest `turn.completed` event for provider token usage,
  uses `thread.started.thread_id` as the continuity identifier, and reads the
  final text from the CLI-written `last-message.md` / `reflection.md` sidecar
  file

If plugin lookup fails or the backend CLI is missing, `_launch()` writes a
JSON error line to `events.jsonl` and the run finalizes as a failure with
`cost.source == "unknown"`.

### Reflection

Reflection is solicited by the driver after a successful run of a goal type
that requires it. The driver extracts the current backend continuity ID from
the run artifacts, launches one plugin-specific continuation command with a
focused reflection prompt, reads the resulting text, and passes that
reflection into `close_run()`. This is part of the same run close path, not a
separate goal or run.

### Execution model

`driver.dispatch()` is synchronous — it blocks until the subprocess exits.
The coordinator calls it inline during `_phase_dispatch`, so reconcile()
blocks for the full duration of each agent run. Goals are dispatched one at
a time, sequentially. Concurrency is deferred.

## Failure modes

| Reason code | Source | When it occurs |
|-------------|--------|---------------|
| `INVALID_GARDEN_NAME` | `garden.py` | `set_garden_name()` receives a malformed name |
| `GOAL_NOT_FOUND` | `goals.py` | `transition_goal` called with unknown goal ID |
| `RUN_NOT_FOUND` | `runs.py` | `close_run` called with unknown run ID |
| `INVALID_TRANSITION` | `goals.py`, `runs.py` | State machine rejects the requested transition |
| `MISSING_CLOSED_REASON` | `goals.py` | `closed_reason` absent when closing a goal |
| `IO_ERROR` | `events.py` | File write failed |

Validation failure modes (schema-level) are in the Level 2 docs for
[goal](../goal/level2.md), [event](../event/level2.md), and
[run](../run/level2.md).

`PAK2.toml` is also the authoritative runtime source for the runtime root and
filesystem reply directory name. `[runtime].root` selects the runtime subtree
relative to the garden root, and `[garden].name` selects the reply directory
under `<runtime-root>/inbox/`. When a non-default name is configured, the
runtime renames a legacy `inbox/garden/` directory on first reply delivery
only if the target directory does not already exist. If both exist, it
preserves both and writes future replies to the configured directory.

## Running the coordinator

```bash
python3 -m system.coordinator
```

Or call `reconcile()` directly in tests (all state paths are injectable).

## Tests

`tests/test_events.py` — 11 tests
`tests/test_goals.py` — 23 tests
`tests/test_runs.py` — 19 tests
`tests/test_coordinator.py` — 11 tests
`tests/test_plants.py` — 17 tests
`tests/test_driver.py` — 33 tests

Run all: `python3 -m unittest discover -s tests`

## Invariants

- The event log is never read to determine current state during reconcile.
  State comes from goal and run files. Events are the audit trail only.
- Every state transition emits at least one event.
- The system manages `<runtime-root>/runs/<run-id>/meta.json`. Agents do not write it.
  Raw agent output goes to `<runtime-root>/runs/<run-id>/events.jsonl` via the driver.
- A goal in `closed` status is never transitioned again.
- Plants do not own goals or runs. Both live at garden level with fields
  linking to the plant.
