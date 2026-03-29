# Initiative Records

## What this is

`Initiative Records` are plant-local files for operator-approved work that is
too large for one bounded goal chain but still needs clear stop points.

The canonical artifact path is:

`plants/<plant>/memory/initiatives/<initiative-id>.json`

Each record keeps one approved initiative legible in one place:

- what outcome is approved
- which tranche is active now
- what the next already authorized step is
- what must happen before a later tranche may start

## What it changes

It does not create a new scheduler lane or a new runtime primitive.

Ordinary goals and runs remain the atomic audited work unit. The initiative
record is the gardener-local governance layer that says how those bounded goals
fit together.

## How it stays bounded

- only one tranche may be active at a time
- each tranche must end in a review or `evaluate` stop
- later tranches do not start automatically unless the closing artifact and the
  record both point to the same successor

## Relationship to campaign mode

Bounded campaign mode remains the short inner execution tool.

An initiative may allow one tranche to use campaign mode, but campaign mode
does not become the outer container and its existing ceilings still apply.

## Audit trail

Use `system.initiatives.write_initiative_record(...)` to write the record.
That helper validates the plant-local artifact under `plants/<plant>/...` and
emits `InitiativeRefreshStarted` / `InitiativeRefreshFinished` to
`<runtime-root>/events/coordinator.jsonl`.
