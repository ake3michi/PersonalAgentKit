# Goal — What It Is

A **goal** is a request for the agent to do something.

You write what you want done. The system takes care of running it, tracking
what happens, and recording the result. You don't need to know how.

---

## What a goal looks like

A goal has three things you care about:

1. **What kind of work** — is this building something new, fixing a bug,
   doing research, or a routine check-in?
2. **What to do** — a plain description of the work, written however makes
   sense to you.
3. **When and how urgent** — does it need to happen now, or can it wait?

Everything else is handled by the system.

---

## The life of a goal

A goal moves through stages:

```
You submit it → agent picks it up → agent works on it → done
```

More specifically:

| Stage | What it means |
|-------|--------------|
| **Queued** | Waiting to be picked up |
| **Dispatched** | Agent has claimed it, about to start |
| **Running** | Agent is actively working |
| **Completed** | Work finished successfully |
| **Evaluating** | A second agent is reviewing the work |
| **Closed** | Final state — nothing more will happen |

A goal always ends at **Closed**. That's the only way a goal ends.

---

## What you need to know to use goals safely

- **You don't write the goal ID** — the system assigns it.
- **You don't change a goal's status directly** — the system does that as work progresses.
- **A goal can only move forward**, never backward. If something goes wrong,
  the goal closes with a reason explaining why.
- **If you assign a goal to a plant, that plant must already exist and be active.**
- **Some goal types carry extra contracts** — for example raw `tend` goals and
  manual retrospective `evaluate` goals include structured payloads that are
  checked at submission. Dedicated later-plant commissioning also uses an
  ordinary `build` goal with a checked `plant_commission` payload.
- **One goal, potentially multiple attempts** — if a run fails, the agent may
  try again. The goal stays the same; a new run is created.

If you want a bounded cross-run synthesis pass instead of ad hoc work, see
[`Manual Retrospective`](../retrospective/level1.md).

---

## If something goes wrong

When a goal closes, it always says why:

| Reason | What happened |
|--------|--------------|
| `success` | Work completed as requested |
| `failure` | The agent tried but could not complete it |
| `cancelled` | The goal was stopped before it ran |
| `dependency_impossible` | A goal this depended on can never complete |

You can always read the goal file to see its current state, or read the event
log to see the full history of what happened.
