# Conversations — What They Are

A **conversation** is how you talk to the garden.

You write a message — in a file, through a chat interface, whatever the channel
supports — and the garden reads it, thinks, and writes back. The exchange is
persistent: the garden remembers what was said, and each new message picks up
where the last one left off.

---

## What makes conversations different from goals

Goals are fire-and-forget: you describe work, the system does it, you get a
result. Conversations are back-and-forth: the garden responds to what you said,
you respond to what the garden said, and the thread continues.

Under the hood, each message in a conversation triggers a `converse` goal.
But you don't need to think about that — from your perspective, you're just
talking to the garden.

---

## Channels

A **channel** is how messages travel. The first channel is the filesystem:

- You write a `.md` file to `<runtime-root>/inbox/operator/`
- The garden reads it, processes it, and writes a reply to
  `<runtime-root>/inbox/<garden-name>/`

Future channels (Slack, chat UI, API) will work the same way from the garden's
perspective. The conversation is the same regardless of how messages arrive.

---

## Context stays fresh

The garden does not respond based on stale information. Each time you send a
message, the garden automatically sees:

- What changed in its memory, skills, and knowledge since your last message
- What the autonomous system has been doing (goals submitted, runs completed)

You don't need to brief the garden on what happened between messages. It already
knows.

---

## Multiple conversations

You can have more than one open conversation. Each one has its own context.
If you're exploring an idea in one conversation and kicking off a build in
another, they don't interfere — but they both draw from the same shared state,
so knowledge gained in one surfaces in the other on the next exchange.

---

## What you need to know to use conversations safely

- **One conversation per channel endpoint.** The filesystem channel has one
  conversation thread — messages from `<runtime-root>/inbox/operator/`
  continue the same thread.
- **Fresh gardens may start with one system-opened thread.** On the first
  `pak2 cycle`, the filesystem channel can receive a system-started startup
  note, short system progress updates about the first durable run, and an
  in-thread representation of that run's first filesystem reply with source
  provenance before you send anything. Your first later message still
  continues that same thread.
- **You don't close conversations manually** — the system closes them when they
  are concluded, or you can leave them open indefinitely.
- **Replies appear in `<runtime-root>/inbox/<garden-name>/`**, named with a
  timestamp.
- **Chat shows session status out of band** — current mode, lineage, and
  context pressure are surfaced separately from the garden's reply text.
- **You can request a fresh session** with `/hop` in `pak2 chat`
  or `pak2 hop` from the CLI. The garden replies first, then writes a
  checkpoint so the following turn can start fresh.
- **The garden may not respond immediately** — `converse` goals are dispatched
  like any other goal and run when a plant is free.
