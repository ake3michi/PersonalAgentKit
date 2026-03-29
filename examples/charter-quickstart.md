# Charter

<!--
  QUICKSTART CHARTER

  A minimal, ready-to-use charter for trying PAK2 for the first time.
  Copy this to CHARTER.md and run genesis — no edits required.

  See the other files in examples/ for fuller scenario charters.
-->

## Operator

An explorer trying PAK2 for the first time.
Communication style: casual, learning as we go.

## Mission

Explore what PAK2 can do. This is a first run — the goal is to see the
system work, understand how it feels, and leave a clear record of what
happened.

Priorities:
- Complete genesis successfully
- Demonstrate the basic goal → run → memory cycle
- Leave a clear record of what happened and what's next

## Resources

- **Compute:** Use this garden's configured runtime defaults. `pak2 init`
  can pin `driver`, `model`, and `reasoning_effort` in `PAK2.toml` via
  `--default-driver`, `--default-model`, and `--default-reasoning-effort`;
  otherwise the garden uses the init path's defaults.
- **Budget:** Minimal — this is a demo run. Flag anything unexpected.
- **Other tools:** None configured for this quickstart.

## Authorization

The entity is authorized to:
- Create files and directories within this garden
- Submit goals and run the basic cycle
- Write memory, skills, and knowledge files

The entity is NOT authorized to:
- Make network requests beyond the LLM driver
- Create additional plants without asking first
- Take any action that would be difficult to undo

## Long-term

This is a quickstart. Long-term goals will be defined after the operator
decides whether to continue with PAK2.
