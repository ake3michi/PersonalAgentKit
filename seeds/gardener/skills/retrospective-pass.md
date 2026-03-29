# Retrospective Pass

Use this skill when the gardener is assigned an `evaluate` goal whose purpose
is a retrospective: a bounded, cross-run synthesis pass over prior work.

## Purpose

Retrospective is a gardener-local protocol on top of `evaluate`. It reads a
bounded run window and synthesizes what the garden should learn from the
pattern across those runs. It is broader than ordinary per-run reflection and
less action-oriented than `tend`.

## When To Use It

- The operator explicitly asks for a retrospective.
- A bounded follow-up `evaluate` goal is justified after a design, audit, or
  cluster of related runs where cross-run synthesis is likely to be useful.
- Do not use retrospective for a live garden-state survey, operational triage,
  or memory maintenance. Use `tend` for that.
- Do not use retrospective for lessons from one run only. The normal run
  reflection already covers that.

## Window Selection

1. If the goal body names an explicit window, use it.
2. Otherwise, if a prior retrospective `evaluate` run is clearly identifiable,
   use the substantive closed runs since that retrospective as the default
   window.
3. Otherwise, fall back to a small recent window of substantive closed runs.
   Default target: the most recent `3-5` closed runs that are broad enough to
   show a pattern without becoming an unbounded audit.
4. State the chosen window explicitly in `evaluation.md`.

## Default Exclusions

- Exclude goal type `converse` by default. Conversation turns are the somatic
  layer, not the primary evidence set for retrospective synthesis.
- Exclude goal type `tend` by default. Tends are operational surveys, not the
  substantive work a retrospective is usually trying to assess.
- Treat prior retrospective `evaluate` runs as window boundaries rather than
  primary evidence unless the goal explicitly asks for comparison between
  retrospectives.

## How It Differs From Nearby Protocols

### Versus Reflection

- Reflection is per-run and answers what one run taught.
- Retrospective is cross-run and answers what pattern is emerging across a
  bounded window of work.
- Retrospective may consume prior reflections as evidence, but it does not
  replace the normal run reflection required for successful `evaluate` runs.

### Versus Tend

- `tend` surveys current garden state, may update memory, and may submit
  justified goals.
- Retrospective is primarily analytical. It looks backward across completed
  runs rather than surveying live queue health.
- Retrospective should stay mostly observe-only unless the goal text explicitly
  authorizes one bounded follow-up action.

## Required `evaluation.md` Sections

Write `evaluation.md` in the run directory with these sections:

- `## Window`
- `## Evidence Reviewed`
- `## Surprises`
- `## Recurring Patterns`
- `## Cost And Throughput`
- `## Strategy Assessment`
- `## Action Boundary`
- `## Recommended Next Step`

If cost or throughput is not materially relevant in the chosen window, say so
briefly in `## Cost And Throughput` rather than omitting the section.

## Default No-Action Rule

- Do not update memory.
- Do not write knowledge files.
- Do not introduce or depend on a shared-knowledge store.
- Do not submit goals by default.
- Do not collapse retrospective into a hidden `tend`.

Exception:

- If the goal text explicitly authorizes action and the recommendation is
  concrete, you may submit at most one bounded follow-up goal.
- Record that authorization and the decision in `## Action Boundary`.
- If no follow-up is justified, say so explicitly in
  `## Recommended Next Step`.

## Environment Notes

- Prefer `rg` when available; otherwise use `find`, `grep`, and `sed`.
- Do not write to goal files, run `meta.json`, conversation files, or
  `events/coordinator.jsonl` directly.
