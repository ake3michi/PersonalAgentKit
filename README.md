# PersonalAgentKit

An autonomous AI agent that names itself, builds its own faculties, and
grows over time. Runs on top of [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

## Quick start

Prerequisites: `bash`, `python3`, `jq`, `git`, Claude Code CLI.

```bash
# Copy the kit
cp -r PersonalAgentKit my-agent && cd my-agent

# Fill in who you are and what the agent is for
edit shared/charter.md

# Bootstrap and plant
./personalagentkit-genesis
```

Genesis takes a few minutes. The agent will name itself, write its first
memory, and leave you a message in `coordinator/inbox/`.

## Start the cycle

```bash
cd coordinator
./scripts/personalagentkit cycle
```

The agent tends itself every 10 minutes, assessing state and deciding what
to do next. Monitor with `./scripts/personalagentkit watch`.

## Communicating with your agent

The agent writes messages to `coordinator/inbox/` as `NNN-to-{yourname}.md`.
To reply, write `coordinator/inbox/NNN-reply.md`.

## Email (optional)

For email communication, sign up at [agentmail.to](https://agentmail.to)
and place your API key at `coordinator/secrets/agentmail-api-key.txt`. The
agent will find the skill documentation at `shared/skills/agentmail.md`
and configure itself.

## License

MIT
