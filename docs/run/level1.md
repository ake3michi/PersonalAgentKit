# Run — What It Is

A **run** is the record of one attempt to complete a goal.

When the agent works on a goal, it creates a run. The run captures what
happened: how long it took, what it cost, what files were changed, and — when
the work finishes — what the agent learned.

---

## One goal, possibly multiple runs

If a run fails, the agent may try again. Each attempt is a separate run.
The goal stays the same. The history of all attempts is preserved.

---

## What a run records

| What | Why it matters |
|------|---------------|
| **Status** | Did it succeed, fail, or get interrupted? |
| **Cost** | How much compute and money did this use? |
| **Outputs** | What files were produced or changed? |
| **Reflection** | What did the agent learn? (for work that should improve over time) |

---

## Run statuses

| Status | What happened |
|--------|--------------|
| `running` | Work is in progress |
| `success` | Work completed |
| `failure` | Agent tried but could not complete |
| `killed` | Run was stopped (by a watchdog or the operator) |
| `timeout` | Run exceeded its time limit |
| `zero_output` | Run finished but produced nothing |

---

## What you need to know

- **Runs are never edited.** Once a run completes, its record is permanent.
  The result is what it is.
- **Cost is always recorded.** Even if the system can't get an exact figure,
  it will say so explicitly — it won't silently omit it.
- **Every run is identified by its goal and attempt number.** If goal `042`
  is attempted twice, you'll see runs `042-fix-the-thing-r1` and
  `042-fix-the-thing-r2`.
