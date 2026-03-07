---
name: agentmail
description: Send and receive email via the Agentmail API
user-invocable: false
personalagentkit-source: native
personalagentkit-trust: reviewed
---
# Skill: Send Email via Agentmail API

## Purpose

Send an email to a recipient using the agentmail.to API. This is the portable,
raw-API version of email sending — no garden-specific scripts required. Any
garden can adapt this pattern.

## Prerequisites

- API key stored in `secrets/agentmail-api-key.txt` (readable by the garden agent)
- Inbox address: `[your-inbox]@agentmail.to` (shared across all gardens)

## Send a message

```bash
API_KEY=$(cat /path/to/secrets/agentmail-api-key.txt)
INBOX_ID="yourinbox@agentmail.to"

curl -s -X POST "https://api.agentmail.to/v0/inboxes/${INBOX_ID}/messages/send" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "to": ["recipient@example.com"],
    "subject": "Your subject here",
    "text": "Plain text body here."
  }'
```

Or inline in a shell script:

```bash
API_KEY=$(cat secrets/agentmail-api-key.txt)
curl -s -X POST "https://api.agentmail.to/v0/inboxes/yourinbox@agentmail.to/messages/send" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"to\": [\"[operator@email.com]\"], \"subject\": \"[garden] subject\", \"text\": \"Body text.\"}"
```

## Read messages (check for replies)

```bash
API_KEY=$(cat secrets/agentmail-api-key.txt)
INBOX_ID="yourinbox@agentmail.to"

curl -s "https://api.agentmail.to/v0/inboxes/${INBOX_ID}/messages" \
  -H "Authorization: Bearer ${API_KEY}" | python3 -m json.tool
```

The response is **wrapped**: `{"count": N, "messages": [...]}` — not a flat
list. Extract with `.messages[]` (jq) or `response["messages"]` (Python).

Each message has: `message_id`, `from`, `subject`, `preview`, `labels`,
`created_at`. Use `message_id` (not `id`) to fetch full message content.

**`from` field format**: The `from` field is a display string like
`"[Operator Name] <[operator@email.com]>"`, not a bare email address.
Filtering with exact equality against an address fails; use a substring
or `in` check (e.g., `"[operator@email.com]" in msg["from"].lower()`).

## Fetch a single message

```bash
# Message IDs are RFC 5322 headers like <CAB0dzF8...@mail.gmail.com>
# containing <, >, + — they MUST be URL-encoded before use in a path.
# Python: urllib.parse.quote(msg_id, safe='')
# curl:   use --data-urlencode or encode manually

MSG_ID_ENCODED=$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$MESSAGE_ID")

curl -s "https://api.agentmail.to/v0/inboxes/${INBOX_ID}/messages/${MSG_ID_ENCODED}" \
  -H "Authorization: Bearer ${API_KEY}" | python3 -m json.tool
```

## Reply to a message (thread into existing conversation)

**Use this when replying to a [Operator] message** — it sets correct `In-Reply-To` and
`References` email headers so the reply lands in the same thread in his email client.
Using `/messages/send` instead creates a new thread every time.

```bash
API_KEY=$(cat secrets/agentmail-api-key.txt)
INBOX_ID="[your-inbox]@agentmail.to"

# MESSAGE_ID is from the inbox file frontmatter (e.g., the message_id of [Operator]'s message)
# It MUST be URL-encoded — contains <, >, @ characters
MSG_ID_ENCODED=$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$MESSAGE_ID")

curl -s -X POST "https://api.agentmail.to/v0/inboxes/${INBOX_ID}/messages/${MSG_ID_ENCODED}/reply" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"Reply body here.\"}"
```

The reply endpoint infers recipients from the original message. You can override with
`to`, `cc`, `bcc`, or add `reply_all: true` to reply to all recipients.

**Where to find `MESSAGE_ID`**: The `check-inbox` script writes inbox files with
`message_id:` in the frontmatter. Use that value as the anchor for the reply.

Response contains `message_id` and `thread_id` of the new reply message.

## Verification (genesis test)

On garden genesis, verify email capability works before proceeding:

```bash
# Send a test email to confirm the skill is operational
API_KEY=$(cat secrets/agentmail-api-key.txt)
curl -s -X POST "https://api.agentmail.to/v0/inboxes/yourinbox@agentmail.to/messages/send" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"to\": [\"[operator@email.com]\"], \"subject\": \"[garden] email capability confirmed\", \"text\": \"Test send from genesis run. Email skill is operational.\"}"
```

## Notes

- API key should be placed in `secrets/agentmail-api-key.txt` within the garden
- All gardens share the single inbox `[your-inbox]@agentmail.to`
- Subject prefix convention: `[garden-name]` for identification when multiple gardens share a channel
- Operator email: `[operator@email.com]`
