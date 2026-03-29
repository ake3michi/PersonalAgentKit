# Live Observability Dashboard — What It Is

The Live Observability Dashboard is a read-only terminal view of the garden's
current state.

Use it when you want one screen that answers:

- is the coordinator up, and is work moving?
- what is running, ready, or blocked right now?
- do any conversations need attention or a hop?
- what changed recently?
- what has today's completed work cost?

Run it with:

```bash
pak2 dashboard --root .
```

Use `--once` to print one snapshot and exit:

```bash
pak2 dashboard --root . --once
```

---

## What it reads

The dashboard does not keep its own database. It reads the same local files the
garden already uses:

- `<runtime-root>/goals/*.json` and `<runtime-root>/runs/*/meta.json` for current work
- `<runtime-root>/runs/*/events.jsonl` mtime for run liveness
- `<runtime-root>/conversations/*/meta.json` and the latest turn record for conversation state
- `<runtime-root>/events/coordinator.jsonl` for recent activity
- `/proc` on Linux to see whether `pak2 cycle` appears to be running for this
  garden root

For its own audit trail, each dashboard session also writes:

- `<runtime-root>/events/coordinator.jsonl` start and finish events for the invocation
- `<runtime-root>/dashboard/invocations/<id>.json` with the measured wall-time cost

---

## What you need to know

- It is read-only. It does not submit goals, restart work, or repair anything.
- It is best-effort. Missing optional files and malformed JSON lines are
  skipped instead of crashing the view.
- The CLI is still terminal-only. If you need structured dashboard data, use
  the snapshot and render helpers documented in [`Level 2`](./level2.md) and
  [`Level 3`](./level3.md) instead of expecting JSON from `pak2 dashboard`.
- If you need a named accept/reject check for machine-readable dashboard
  output, use the snapshot or render validator documented in
  [`Level 2`](./level2.md) and [`Level 3`](./level3.md) instead of scraping
  terminal text.
- It shows a small live summary, not a full history:
  - up to `8` active-work rows
  - up to `5` conversation rows
  - up to `5` alerts
  - up to `8` recent activity rows
- The cost panel only totals completed runs from the current UTC day whose run
  record has `cost.source == "provider"`.
- Running the dashboard now leaves its own audit trail, but only for the
  invocation itself. It still does not mutate goals, runs, or conversations.
- The dashboard's own wall-time cost record is not included in the on-screen
  cost panel. That panel still reports garden run cost only.

---

## What it does not do

The current dashboard does not:

- provide control actions such as retry, kill, submit, or hop
- provide a CLI JSON output mode

For the engineer and agent contracts, see
[`Level 2`](./level2.md) and [`Level 3`](./level3.md).
