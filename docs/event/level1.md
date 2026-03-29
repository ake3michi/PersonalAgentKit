# Event — What It Is

An **event** is a record that something happened.

Every meaningful action in the system — a goal being submitted, a run starting,
a goal closing — is recorded as an event. Events are never changed or deleted.
They accumulate into a complete history of everything the system has done.

---

## Why events matter

If you ever want to know what happened — when, by whom, and why — the event log
is the authoritative answer. You don't need to ask the agent. You don't need to
read logs. The events are written in a structured format that both humans and
agents can read.

---

## What an event looks like

Every event records:
- **When** it happened
- **What** happened (a named type, like "GoalSubmitted" or "RunFinished")
- **Who** caused it — the system, the operator, or a named agent

Some events include additional context: which goal was involved, what state it
moved to, or why something closed.

---

## What you need to know

- **Events are append-only.** Once written, an event never changes.
- **Every action leaves a trace.** If something happened in the system, there
  is an event for it.
- **No event is anonymous.** Every event names who caused it.
- **The event log is the truth.** If the event log says a goal closed with
  `success`, it closed with `success`.
