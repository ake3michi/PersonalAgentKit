# Plant — What It Is

## What is a plant?

A plant is a specialist within a garden. When the garden needs to get work
done, it doesn't do everything itself — it has plants that each focus on a
particular kind of work. One plant might handle coding, another research,
another auditing.

From the outside, you interact with the garden as a single identity. Plants
are an internal concern. You do not send messages to individual plants, and
you do not need to know which plant is doing what.

## What does a plant do?

When a goal is assigned to a plant, the system loads that plant's context —
everything it has learned and built up over time — and hands that context to
the agent when it runs. The agent is not starting from scratch on every goal.
It carries forward what it knows.

## What does a plant carry?

- **Memory** — what it has experienced and learned across previous runs.
- **Skills** — proven capabilities it has codified for reuse.
- **Knowledge** — domain expertise specific to its role: facts, patterns,
  and understanding that don't belong to the whole garden.

Some plants may also keep one small current-work file alongside memory when
they need a quick answer to "what are we trying to do right now?" See
[`Active Threads`](../active-threads/level1.md).

## How does a plant come into being?

The first plant — the gardener — is created when the garden starts up for
the first time. Other plants are commissioned by the gardener when the garden
needs a new kind of specialist. Plants are created on demand, not in advance.

Normal commissioning starts from a seed. The system records which seed the
plant should use on its first blank-memory run, copies any seed-local skills
or knowledge into the new plant, and then queues that plant's first goal
through the same goal system everything else uses. For later plants, the
normal path is a dedicated gardener `build` goal whose whole purpose is
commissioning, so the planting step has its own visible goal/run history.
This repo currently ships the gardener seed plus one minimal specialist
starter seed, `reviewer`, for bounded independent review work.

## What happens when a plant is no longer needed?

It is archived. An archived plant no longer receives new goals, but its
history and what it learned remain on record. Nothing is deleted.
