# Definition of Done

A primitive, capability, or protocol is not complete until every item on this
list is satisfied. There are no exceptions. Partial completion is not a state -
something is either done or it is in progress.

This contract applies to contributors of all kinds: human, engineer, agent.

---

## The Checklist

### Specification
- [ ] The concept is named and that name is used consistently everywhere
- [ ] The schema is defined (machine-readable, self-validating)
- [ ] Valid states and transitions are enumerated (if stateful)
- [ ] Failure modes are named - not handled yet, but named

### Enforcement
- [ ] A validator exists that enforces the schema at the system boundary
- [ ] Nothing in the system bypasses the validator
- [ ] Invalid input produces a rejection with a named reason code, not an exception

### Verification
- [ ] Tests exist for the happy path
- [ ] Tests exist for each named failure mode
- [ ] Tests exist for boundary conditions (empty, maximum, malformed)
- [ ] All tests pass in a clean environment with no dependencies on prior state

### Documentation
- [ ] **Level 1** - one page, no jargon. What is this? What does it do for me?
      What do I need to know to use it without breaking it?
- [ ] **Level 2** - engineer reference. How does it work? What are the invariants?
      What are the failure modes and how are they surfaced? Link to schema and tests.
- [ ] **Level 3** - agent contract. Schema definitions. Valid states and transitions.
      What the agent may emit, read, and must never touch. Self-contained:
      an agent must be able to follow this without any other context.

### Integration
- [ ] The system uses this in production (not just in tests)
- [ ] Cost (compute, tokens, wall time) is recorded if the capability incurs any
- [ ] The event log records when this is invoked and what the outcome was

### Review
- [ ] Reviewed by at least one participant who did not write it
- [ ] All findings are either addressed or explicitly deferred with a named reason
- [ ] Review is recorded: a commit, a finding file, or an inline note - not verbal

### Provenance
- [ ] The git commit that introduced it has a one-line message naming what it adds
- [ ] If it replaces something, the replaced thing is removed in the same or
      immediately following commit - not left as dead code

---

## On Failure Modes

Naming a failure mode means giving it a string code (e.g. `INVALID_TRANSITION`,
`SCHEMA_VIOLATION`, `MISSING_REQUIRED_FIELD`) and deciding at which layer it is
handled. Named failure modes belong in the Level 3 documentation.

"An exception was raised" is not a named failure mode.

---

## On Documentation Currency

Documentation that describes a past version of the system is worse than no
documentation - it actively misleads. When a primitive changes, its
documentation changes in the same commit. If that is not possible, the
documentation is marked `[STALE - updated in #NNN]` until the commit lands.

---

## On Agents Following This Contract

An agent completing a goal that introduces a new capability is responsible for
satisfying this checklist before marking the goal complete. The gardener, during
each tend cycle, checks that all capabilities in the system satisfy this
checklist. Capabilities that fail the check are flagged - not silently left.
