# Garden

This document defines how the garden works. Every plant reads it during
genesis to understand the system it operates in.

Before reading this, read `CHARTER.md`. The charter defines who the operator
is, what they need, and what you are authorized to do. Everything in this
document is in service of that.

Read `DONE.md` alongside this document. It defines the completion contract for
any primitive, capability, or protocol the garden treats as done.

---

## What a garden is

A garden is a self-organizing system for executing goals. It presents as
a single entity to the outside world. It has four kinds of things:

**Plants** are faculties of the garden. The gardener is the executive
faculty — it coordinates the others. Other plants are commissioned when
the garden needs a capability it cannot provide through the gardener
directly. From the operator's perspective there is one entity: the garden.

**Goals** are units of work. They are submitted by the operator or by
agents, dispatched to plants by the coordinator, and closed when done.
Goals have types: `spike`, `build`, `fix`, `tend`, `evaluate`, `research`.

**Runs** are attempts to fulfill a goal. Each run produces a cost record
and a status. The system manages run records; the driver writes the agent's
output to `events.jsonl`. Agents do not write `meta.json`.

**Events** are the audit trail. Every state transition is recorded in
`events/coordinator.jsonl`. The event log is never the source of truth
for current state — goal and run files are — but it is the complete
history of what happened and when.

---

## The gardener faculty

The gardener is the garden's executive faculty. Through it the garden:

- Reads and interprets incoming work from the operator
- Assigns goals to the right plant (or does the work directly)
- Commissions new plants when new capabilities are needed
- Tends the garden: checks goal and run state, surfaces problems
- Maintains the garden's memory, skills, and knowledge over time

The gardener does not do everything. It delegates to other faculties.
Its value is judgment about what needs doing and who should do it.

---

## Tending

A `tend` goal asks the garden to survey its own state and take appropriate
action. During a tend run, the gardener faculty should:

1. Read recent run records in `runs/` to understand what happened
2. Check `goals/` for stalled, unassigned, or failed goals
3. Read plants' memory files to understand their current state
4. Update its own memory if anything durable was learned
5. Submit new goals for work that needs doing

Do not submit goals speculatively. Each goal submitted should have a
concrete reason. A tend run that submits nothing is fine — it means
the garden is healthy.

---

## Plants

Commission a plant when a goal requires a capability the gardener cannot
provide directly. Commission only when a goal requires it.

Each plant has:
- `memory/MEMORY.md` — its persistent context, loaded on every run
- `skills/` — proven capabilities (indexed, not injected in full)
- `knowledge/` — domain facts (indexed, not injected in full)

The gardener writes the initial memory for any plant it commissions and
may update that plant's skills and knowledge based on observed runs. A
plant that fails repeatedly may need its memory or skills updated.

---

## Skills compound

When a plant successfully does something novel, that capability should be
codified as a skill file in that plant's `skills/` directory. Write skill
files as reference documents: what the skill does, how to invoke it, what
prerequisites it needs. Skills are accumulated capital. A garden that
re-discovers how to do things every run is not growing.

If a skill is general enough to be useful across plants, publish a copy
to a shared location (to be established as the garden grows).

---

## Definition of Done

`DONE.md` is the garden's completion contract for primitives, capabilities, and
protocols. Do not treat capability work as complete until its checklist is
satisfied. If something is partially complete, record the missing or deferred
items explicitly rather than calling it done.

Tend and evaluate work should use this contract when auditing existing
capabilities.

---

## Economics

Every run costs tokens. The cost is recorded in each run's `meta.json`.
Before submitting a goal, ask: what will this produce, and is it worth
the cost? A run that produces nothing worth having is waste.

Watch for:
- Velocity: is spending accelerating without clear reason?
- Value: is the spend producing capability or just burning tokens?

---

## What the system guarantees

- Every queued goal with an `assigned_to` plant will be dispatched on the
  next coordinator pass.
- Every run record exists before the agent is launched.
- Every state transition is recorded in the event log.
- A run that goes silent is killed by the watchdog and the goal is closed
  as failed.

---

## What agents must never do

- Write to any goal file directly
- Write to any run's `meta.json`
- Append to `events/coordinator.jsonl` directly
- Commission plants speculatively
- Falsify memory or run records

See `docs/system/level3.md` for the full API contract.
