# Initiative Records

Use this skill when your plant needs to create or refresh one operator-
approved initiative that is larger than one bounded campaign but still needs
explicit tranche boundaries.

## Purpose

Keep one machine-readable initiative record current so later runs can answer:

- what multi-stage effort is approved
- which tranche is active now
- what the next already authorized bounded step is
- what artifact must exist before any later tranche may start

## Preconditions

Before acting, confirm all of the following:

- `CHARTER.md`, `GARDEN.md`, `plants/<plant>/memory/MEMORY.md`, and
  `plants/<plant>/knowledge/initiative-record-protocol.md` have been read
- the operator approval source is durable and specific
- the initiative can be expressed as ordered tranches with explicit successor
  conditions
- no coordinator or scheduler change is required for the mechanism itself

If any check fails, stop and record the ambiguity instead of inventing a
larger autonomy mechanism.

## Procedure

1. Read the approval source plus the design, review, or policy artifacts that
   justify the initiative shape.
2. Define the initiative boundary:
   - objective
   - scope boundary
   - non-goals
   - success checks
3. Define explicit ordered tranches:
   - one current active tranche at most
   - allowed goal types
   - whether a bounded campaign is allowed inside that tranche
   - mandatory review/evaluate stop
   - exact successor condition
4. Define `next_authorized_step` as one bounded goal shape, not a menu.
5. Record or refresh the initiative through:

```python
from system.initiatives import write_initiative_record
```

6. Keep execution provenance in the initiative `ledger`, but leave ordinary
   goal/run provenance in the original goal and run artifacts.
7. When a clean tranche-closing `evaluate` run advances to the next approved
   same-initiative `build` or `fix` tranche, submit it with:

```python
from system.submit import submit_same_initiative_code_change_with_evaluate
```

   This queues the implementation goal and its mandatory follow-on evaluate
   stop together. Do not use it for discovery/research tranches, general
   chained automation, or `closure-review`.

## Stop immediately when

- the approval source offers multiple materially different directions
- the next step needs new operator prioritization
- the work would require a new plant, scheduler lane, or hard-to-undo action
- more than one tranche appears active
- the proposed successor tranche is not already named in the record

## Environment Notes

- Prefer `rg` when available; otherwise use `find`, `grep`, and `sed`.
- Do not write to goal files, run `meta.json`, conversation files, or
  `events/coordinator.jsonl` directly.
