# Recently Concluded Work Note Protocol

Status: template protocol for gardener-local lightweight operator-facing notes
about recently concluded durable work.

## Why this exists

- Successful conversation-origin durable goals already leave run-local
  evidence, but the operator can still end up asking what just finished.
- Campaign-closing reviews often close a thread cleanly without leaving one
  obvious operator-facing pointer to the outcome.
- The runtime now owns a low-cost emission surface for this note class, so the
  gardener no longer needs a raw inbox write path here.

## Trigger Rule

Emit exactly one compact `recently_concluded` operator message when the current
run is closing successfully and either of the following is true:

- the goal is conversation-origin durable work: `origin.kind` is
  `conversation` and the goal type is not `converse` or `tend`
- the run is the campaign-closing review/evaluate stop for an already-
  authorized bounded campaign

If both triggers apply, still write one note, not two.

Routing is system-owned for this note class:

- if the current goal has conversation origin, the note is appended to that
  originating conversation as the canonical human record and gets a reply-copy
  delivery on the top-level reply surface
- if the current goal has no origin, the note is written out of band under
  `inbox/<garden-name>/notes/`

Do not use this protocol for:

- ordinary `converse` replies
- ordinary `tend` state notes
- failed, `killed`, `timeout`, or `zero_output` runs

## Required Note Shape

The note is an index, not a replacement for the run summary. Keep it compact
and include these fixed headings exactly:

- `## What Finished`
- `## Operator-Facing Effect`
- `## State Left Behind`
- `## Canonical Artifacts`

## Artifact Rules

Under `## Canonical Artifacts`:

- always point to `runs/<run-id>/last-message.md` and label it the canonical
  per-run human summary
- if the run also produced one special review artifact, such as
  `evaluation.md` or a named campaign-closing review markdown file, point to
  that artifact on its own line and say what it contains
- do not turn the note into an exhaustive file inventory; point only to the
  run summary and the one special review artifact when present

## Scope Boundary

- Each qualifying run writes at most one note. A later campaign-closing
  review may still write its own note even if earlier qualifying runs in the
  same thread already did.
- The note does not replace `runs/<run-id>/last-message.md`; it points to it.
- This remains gardener-local policy, but the actual emission goes through the
  bounded runtime-owned operator-message contract for this note class.
