# Operator Messages — What It Is

This is the system-owned way for the gardener to leave two specific kinds of
operator-facing notes without writing raw files into the reply surface:

- `tend_survey`
- `recently_concluded`

The system decides where those notes go.

If the current goal has conversation origin metadata, the note becomes a normal
message in that originating conversation and the filesystem reply surface gets a
delivery copy.

If the current goal has no conversation origin, the note is treated as
out-of-band and written under `<runtime-root>/inbox/<garden-name>/notes/`
instead. `pak2 chat` does not treat that notes subdirectory as an ordinary chat
reply stream.

Every successful emission is also recorded in
`<runtime-root>/runs/<run-id>/operator-messages.jsonl` so the runtime has a
durable record of what was emitted, how it was routed, and where it landed.

This slice is intentionally narrow. It does not change startup status or the
startup garden-reply path, and it does not authorize a general-purpose message
writer for every future note class.

For the exact engineer and agent contracts, see
[`Level 2`](./level2.md) and [`Level 3`](./level3.md).
