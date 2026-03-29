# Run — Agent Contract

This document is self-contained.

---

## What you may do

- **Read** run records in `<runtime-root>/runs/`.
- **Read** your own run record at `<runtime-root>/runs/<your-run-id>/meta.json`.
- **Read** your own split-root runtime provenance record at
  `<runtime-root>/history/commits/<your-run-id>.json` when it exists.
- **Write** `<runtime-root>/events/coordinator.jsonl` entries via the system API — never
  by appending directly.

## What you must never do

- Write to `<runtime-root>/runs/<run-id>/meta.json` — the system manages this file.
- Modify a run record after its status is terminal.
- Set `id` or `plant` yourself — the system assigns them.

The run record is written by the system before you are launched and finalized
by the system when your run closes. Your raw output goes to
`<runtime-root>/runs/<your-run-id>/events.jsonl` via the driver. Cost, reflection, and
status are extracted from that stream by the driver at close time.
For non-`converse` runs in a readable git worktree, the system may also add a
path-level `worktree_baseline` at launch so later reviews can tell which files
were already dirty before your run began.
When the garden uses a split runtime root, the system may also write
`<runtime-root>/history/commits/<your-run-id>.json` and advance the runtime
root's nested git history after close. You do not write or edit that file.

For the current first `runtime-auto-commit` slice, the close path uses these
exact outcome codes for runtime-history capture: `committed`,
`runtime_root_not_split`, `runtime_root_outside_garden`,
`authored_repo_unavailable`, `record_write_failed`,
`runtime_repo_init_failed`, `runtime_repo_stage_failed`,
`no_runtime_changes`, `runtime_repo_diff_failed`,
`runtime_repo_commit_failed`, and `runtime_repo_head_unavailable`.
Only `committed` advances the runtime repo; the non-`committed` outcomes leave
`meta.json` immutable and explain why no runtime-history commit was published.

---

## Run ID

Your run ID is provided to you when the system launches you. Format:

```
N-slug-rN
```

Example: `42-fix-the-thing-r1` (first attempt at goal `42-fix-the-thing`).

---

## Your run directory

```
<runtime-root>/runs/<your-run-id>/
  meta.json     ← written and managed by the system
  events.jsonl  ← your raw output stream (written by the driver)
```

You read `meta.json` to understand your context. You do not write it.

---

## What meta.json looks like at launch

```json
{
  "id": "42-fix-the-thing-r1",
  "goal": "42-fix-the-thing",
  "plant": "coder",
  "status": "running",
  "started_at": "2026-03-18T12:00:00Z",
  "driver": "codex",
  "model": "gpt-5.4",
  "worktree_baseline": {
    "captured_at": "2026-03-18T12:00:00Z",
    "tracked_dirty_paths": ["system/cli.py"],
    "untracked_dirty_count": 2,
    "untracked_dirty_roots": [".runtime", "dashboard"]
  }
}
```

If `worktree_baseline` is present, treat it as launch-time provenance only.
It tells you which paths were already dirty and how much untracked churn was
already present. It does not prove which later hunks your run added, and it is
not a license to edit `meta.json`.

---

## What meta.json looks like at close — success

```json
{
  "id": "42-fix-the-thing-r1",
  "goal": "42-fix-the-thing",
  "plant": "coder",
  "status": "success",
  "started_at": "2026-03-18T12:00:00Z",
  "completed_at": "2026-03-18T12:10:00Z",
  "driver": "codex",
  "model": "gpt-5.4",
  "cost": {
    "source": "provider",
    "input_tokens": 8000,
    "output_tokens": 1200,
    "actual_usd": 0.04
  },
  "reflection": "The fix required updating the transition guard. Future similar bugs will be visible in the state machine diagram.",
  "outputs": ["system/validate.py", "tests/test_validate.py"],
  "num_turns": 4
}
```

---

## What meta.json looks like at close — failure

```json
{
  "id": "42-fix-the-thing-r1",
  "goal": "42-fix-the-thing",
  "plant": "coder",
  "status": "failure",
  "started_at": "2026-03-18T12:00:00Z",
  "completed_at": "2026-03-18T12:05:00Z",
  "driver": "codex",
  "model": "gpt-5.4",
  "cost": { "source": "unknown" },
  "failure_reason": "failure"
}
```

---

## Reflection — required for some goal types

Reflection is collected by the system after the main run completes. The
system resumes your session with a focused prompt asking what you learned.
Your answer is written into the run record by the driver.

| Goal type | Reflection required on success? |
|-----------|--------------------------------|
| `build`   | Yes |
| `fix`     | Yes |
| `evaluate`| Yes |
| `tend`    | Yes |
| `spike`   | No |
| `research`| No |

Reflection is what you learned, not what you did. Answer: "what do I now
know that I did not know before, and how would I approach this differently?"

A whitespace-only reflection is rejected as if missing.

---

## Reason codes the system may return

### failure_reason values in a closed run

| Value | Meaning |
|-------|---------|
| `failure` | Run tried and could not complete the work |
| `killed` | Watchdog or operator stopped the run |
| `timeout` | Run exceeded the time limit |
| `zero_output` | Run produced no output |
