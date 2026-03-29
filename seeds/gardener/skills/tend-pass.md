# Tend Pass

Use this skill when the gardener is assigned a `tend` goal in the quickstart garden.

## Purpose

Survey current goals, runs, plants, conversations, and recent events from persisted memory, then decide whether any new work is actually justified.

## Rollout Notes

- This garden uses bounded tend passes only. There is no periodic or idle-time auto-tend loop.
- Read the current run metadata carefully. Tend runs now surface trigger and priority context explicitly.
- Runtime observability now emits `TendStarted` and `TendFinished` around the generic run lifecycle.
- If you ever need to queue another tend for a concrete reason, use `system.submit.submit_tend_goal()` instead of a raw `type: "tend"` submission so trigger metadata is preserved.
- Under the current runtime ordering, operator-requested tends use `priority: 6`; post-genesis and other background tends use `priority: 4`.

## Procedure

1. Read `CHARTER.md`, `GARDEN.md`, and `plants/gardener/memory/MEMORY.md`.
2. Inspect `events/coordinator.jsonl`, `goals/`, `runs/*/meta.json`, `conversations/`, `inbox/`, and plant records.
3. Distinguish active work from stalled work using current UTC time and the latest event timestamps.
4. Update memory only with durable facts, operator constraints, and reusable operating knowledge.
5. Emit one brief operator-facing state note through
   `system.operator_messages.emit_tend_survey()`. The runtime will route
   conversation-origin tends back into the originating conversation and will
   route no-origin/background tends to the out-of-band notes surface.
6. Submit no new goals unless there is a concrete problem, missing capability, or justified next step worth the cost. When the survey identifies one clear, already authorized, non-speculative next step, submit that bounded follow-up goal during the tend run instead of only reporting it to the operator. If the handoff comes from a completed bounded review or tend artifact that already names the successor, use `plants/gardener/skills/artifact-driven-successor-handoff.md` so the new goal copies the source artifact's scope and stop boundary instead of broadening the thread. That one follow-up may explicitly start a bounded campaign under `plants/gardener/knowledge/bounded-campaign-mode.md`, but the tend run still submits at most one goal.
7. Record the key checkpoint artifacts early; a long tend run may still end up finalized as `killed`.

## Environment Notes

- `rg` is unavailable in this workspace. Use `find`, `grep`, and `sed`.
- Do not write to goal files, run `meta.json`, conversation files, or `events/coordinator.jsonl` directly.
- Do not write `tend_survey` notes directly into `inbox/`; use the operator-message API.
