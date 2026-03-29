# Manual Retrospective — What It Is

A manual retrospective is a way to ask the garden for a bounded look back over
recent work.

It does not create a new runtime goal type. It submits an ordinary
`evaluate` goal for the `gardener` plant, using the existing retrospective
protocol in `plants/gardener/skills/retrospective-pass.md`.

---

## What it does for you

- Queues one explicit retrospective goal instead of waiting for background
  automation.
- Records the retrospective contract on the goal itself:
  - what window-selection rule to use
  - how many recent substantive runs to fall back to
  - whether one bounded follow-up goal is allowed
- Leaves execution, cost recording, and ordinary goal tracking to the normal
  system.

---

## How to use it

```bash
pak2 retrospective --root .
```

Useful flags:

- `--recent-run-limit N` sets the fallback cap when no earlier retrospective
  boundary exists. Valid range: `3-10`. Default: `5`.
- `--allow-follow-up-goal` changes the action boundary from observe-only to
  "at most one bounded follow-up goal".

The command prints `Submitted: <goal-id>` on success.

---

## What it does not do

- It does not introduce a `retrospective` runtime goal type.
- It does not start a scheduler or background cadence.
- It does not update memory or knowledge files at submission time.
- It does not run the retrospective immediately. It only queues the goal.

---

## If something goes wrong

- If the retrospective contract is malformed, submission is rejected with a
  named reason code.
- If `gardener` does not exist or is inactive, ordinary goal assignment
  validation rejects the submission.

For the exact field contract and rejection codes, see
[`Level 2`](./level2.md) and [`Level 3`](./level3.md).
