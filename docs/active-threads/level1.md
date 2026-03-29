# Active Threads

## What this is

`Active Threads` is a small plant-local file that keeps the current work in one
place.

Instead of reconstructing the live state from scattered goals, runs,
conversation notes, and memory, the plant keeps one canonical artifact at:

`plants/<plant>/memory/active-threads.json`

That file answers three questions quickly:

- what threads are active now
- how those threads relate
- what changed recently

## When to use it

Use it when a plant has more than one meaningful thread in flight, or when a
status/progress answer would otherwise require rereading a lot of history.

It is especially useful when work is nearly done but still has one or two
important follow-ups that should stay visible.

## What it contains

The artifact stores:

- a short top-level summary
- a small list of current threads
- one relation list per thread
- a compact recent-update log

Each thread also records its current focus, the next expected step, and the
artifacts that justify that view.

## What it is not

- not a periodic self-start loop
- not a replacement for goals, runs, or the event log
- not a long-term narrative history

Those existing records still matter. `Active Threads` is the cheap current-work
index on top of them.

## Operator-facing effect

If this file is kept current, the garden can answer "where are we?" from one
artifact instead of re-deriving the answer from the whole garden history.

The artifact itself stays plant-local under `plants/<plant>/...`, but each
refresh also leaves a start/finish event in
`<runtime-root>/events/coordinator.jsonl`, so you can see when the tracker was
updated and whether the refresh succeeded.
