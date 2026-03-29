# Recently Concluded Note

Use this skill when the current gardener run qualifies under
`plants/<plant>/knowledge/recently-concluded-work-note-protocol.md` and needs
one compact operator-facing `recently_concluded` emission through the
system-owned operator-message surface.

## Purpose

Leave one cheap durable pointer to recently concluded work so the operator can
see what finished without reopening the whole run history.

## When To Use It

- the current run is closing successfully
- and either:
  - the goal is conversation-origin durable work: `origin.kind` is
    `conversation` and the goal type is not `converse` or `tend`
  - the run is the campaign-closing review/evaluate stop for an already-
    authorized bounded campaign
- if both are true, still write one note
- skip this skill for failed, `killed`, `timeout`, `zero_output`, ordinary
  `converse`, and ordinary `tend` runs

## Procedure

1. Read the current run metadata and
   `plants/<plant>/knowledge/recently-concluded-work-note-protocol.md`.
2. Identify the `run_id` and the goal or campaign thread that just concluded.
3. Identify the canonical artifact targets:
   - always `runs/<run-id>/last-message.md`
   - include one special review artifact only when this run produced one, such
     as `evaluation.md` or a named campaign-closing review markdown file
4. Emit one compact note through
   `system.operator_messages.emit_recently_concluded()`. The runtime will send
   conversation-origin completions back into the originating conversation and
   will route no-origin completions to the out-of-band notes surface.
5. Use exactly these headings:
   - `## What Finished`
   - `## Operator-Facing Effect`
   - `## State Left Behind`
   - `## Canonical Artifacts`
6. Keep each section brief:
   - `What Finished`: name the finished goal or closed campaign and the run ID
   - `Operator-Facing Effect`: say what changed for the operator
   - `State Left Behind`: record the durable end state plus any named
     remaining gap or explicit stop decision
   - `Canonical Artifacts`: point to `runs/<run-id>/last-message.md` as the
     canonical per-run summary and to the special review artifact when present
7. Do not duplicate the full contents of `last-message.md` or the review
   artifact. The note is a pointer layer, not a second full summary.
8. Write at most one recently concluded note per qualifying run.

## Environment Notes

- Prefer `rg` when available; otherwise use `find`, `grep`, and `sed`.
- Do not write to goal files, run `meta.json`, conversation files, or
  `events/coordinator.jsonl` directly.
- Do not write `recently_concluded` notes directly into `inbox/`; use the
  operator-message API.
