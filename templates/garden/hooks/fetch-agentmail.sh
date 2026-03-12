#!/usr/bin/env bash
# interval: 300

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

export REPO_ROOT
export AGENTMAIL_API_KEY_FILE="${AGENTMAIL_API_KEY_FILE:-$REPO_ROOT/secrets/agentmail-api-key.txt}"
export AGENTMAIL_INBOX_DIR="${AGENTMAIL_INBOX_DIR:-$REPO_ROOT/inbox}"
export AGENTMAIL_CONFIG_FILE="${AGENTMAIL_CONFIG_FILE:-$REPO_ROOT/config/agentmail.env}"
export AGENTMAIL_BASE_URL="${AGENTMAIL_BASE_URL:-https://api.agentmail.to/v0}"
export AGENTMAIL_INBOX_ID="${AGENTMAIL_INBOX_ID:-}"

if [[ -z "$AGENTMAIL_INBOX_ID" && -f "$AGENTMAIL_CONFIG_FILE" ]]; then
  # Persistent non-secret tool config belongs in config/.
  # Source only when the caller did not explicitly override AGENTMAIL_INBOX_ID.
  # shellcheck disable=SC1090
  source "$AGENTMAIL_CONFIG_FILE"
  export AGENTMAIL_INBOX_ID="${AGENTMAIL_INBOX_ID:-}"
fi

if [[ -z "$AGENTMAIL_INBOX_ID" && -x "$REPO_ROOT/hooks/setup-agentmail.sh" ]]; then
  if [[ -s "$AGENTMAIL_API_KEY_FILE" ]]; then
    "$REPO_ROOT/hooks/setup-agentmail.sh"
    if [[ -f "$AGENTMAIL_CONFIG_FILE" ]]; then
      # shellcheck disable=SC1090
      source "$AGENTMAIL_CONFIG_FILE"
      export AGENTMAIL_INBOX_ID="${AGENTMAIL_INBOX_ID:-}"
    fi
  elif [[ -f "$AGENTMAIL_API_KEY_FILE" ]]; then
    echo "fetch-agentmail: Agentmail API key file is empty; leaving hook inert" >&2
  else
    echo "fetch-agentmail: Agentmail API key file not configured; leaving hook inert" >&2
  fi
fi

python3 - <<'PY'
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OPERATOR_OUTBOX_RE = re.compile(r"^(?P<nnn>\d{3,})-to-(?P<recipient>[a-z0-9-]+)\.md$")


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or "unknown-sender"


def read_known_message_ids(inbox_dir: Path) -> set[str]:
    known: set[str] = set()
    for path in inbox_dir.glob("*.md"):
        try:
            lines = path.read_text().splitlines()
        except Exception:
            continue
        if not lines or lines[0].strip() != "---":
            continue
        for line in lines[1:40]:
            if line.strip() == "---":
                break
            if line.startswith("message_id:"):
                known.add(line.split(":", 1)[1].strip().strip('"'))
                break
    return known


def next_nnn(inbox_dir: Path) -> int:
    highest = 0
    for path in inbox_dir.glob("*.md"):
        match = re.match(r"^(\d{3,})-", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    parsed: dict[str, object] = {}
    for raw_line in parts[1].splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if not value:
            parsed[key.strip()] = ""
            continue
        try:
            parsed[key.strip()] = json.loads(value)
        except Exception:
            parsed[key.strip()] = value.strip('"')
    return parsed, parts[2].lstrip("\n")


def operator_identity(repo_root: Path) -> tuple[str, str]:
    charter = repo_root.parent / "shared" / "charter.md"
    if not charter.exists():
        return "", ""

    first_name = ""
    email = ""
    in_operator = False
    for raw_line in charter.read_text().splitlines():
        line = raw_line.strip()
        if raw_line.startswith("## "):
            if line == "## Operator":
                in_operator = True
                continue
            if in_operator:
                break
        if not in_operator or not line:
            continue
        if not first_name:
            token = re.split(r"[^a-z0-9-]+", line.lower(), maxsplit=1)[0]
            first_name = token
            continue
        if line.lower().startswith("email:"):
            email = line.split(":", 1)[1].strip().lower()
            break
    return first_name, email


def pending_operator_outbox(repo_root: Path) -> list[Path]:
    inbox_dir = repo_root / "inbox"
    if not inbox_dir.is_dir():
        return []

    first_name, _ = operator_identity(repo_root)
    aliases = {"operator"}
    if first_name:
        aliases.add(first_name)

    pending: list[Path] = []
    for path in sorted(inbox_dir.glob("*.md")):
        match = OPERATOR_OUTBOX_RE.match(path.name)
        if match is None or match.group("recipient") not in aliases:
            continue
        reply = inbox_dir / f"{match.group('nnn')}-reply.md"
        if not reply.exists():
            pending.append(path)
    return pending


def sender_is_operator(repo_root: Path, sender: str) -> bool:
    first_name, email = operator_identity(repo_root)
    lowered = sender.lower()
    if email and email in lowered:
        return True
    if first_name and first_name in lowered:
        return True
    return False


def matching_sent_thread_records(inbox_dir: Path, thread_id: str) -> list[tuple[Path, str]]:
    matches: list[tuple[Path, str]] = []
    for path in sorted(inbox_dir.glob("*.md")):
        try:
            frontmatter, body = split_frontmatter(path.read_text())
        except Exception:
            continue
        labels = frontmatter.get("labels")
        if not isinstance(labels, list) or "sent" not in labels:
            continue
        if frontmatter.get("thread_id") != thread_id:
            continue
        matches.append((path, body.rstrip()))
    return matches


def map_reply_target(repo_root: Path, sender: str, labels: list[object], thread_id: str) -> str | None:
    if "sent" in labels or not sender_is_operator(repo_root, sender):
        return None

    pending = pending_operator_outbox(repo_root)
    if not pending:
        return None

    inbox_dir = repo_root / "inbox"
    if thread_id:
        sent_records = matching_sent_thread_records(inbox_dir, thread_id)
        if sent_records:
            pending_by_body: dict[str, list[Path]] = {}
            for path in pending:
                try:
                    pending_by_body.setdefault(path.read_text().rstrip(), []).append(path)
                except Exception:
                    continue
            matched_paths: list[Path] = []
            for _, sent_body in sent_records:
                matched_paths.extend(pending_by_body.get(sent_body, []))
            unique = {path for path in matched_paths}
            if len(unique) == 1:
                match = next(iter(unique))
                nnn = match.name.split("-", 1)[0]
                reply = inbox_dir / f"{nnn}-reply.md"
                if not reply.exists():
                    return nnn
                return None

    if len(pending) == 1:
        nnn = pending[0].name.split("-", 1)[0]
        reply = inbox_dir / f"{nnn}-reply.md"
        if not reply.exists():
            return nnn
    return None


def request_json(url: str, api_key: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


repo_root = Path(os.environ["REPO_ROOT"])
api_key_file = Path(os.environ["AGENTMAIL_API_KEY_FILE"])
inbox_dir = Path(os.environ["AGENTMAIL_INBOX_DIR"])
inbox_id = os.environ["AGENTMAIL_INBOX_ID"].strip()

if not inbox_id:
    print("fetch-agentmail: no inbox id available after config/setup", file=sys.stderr)
    sys.exit(0)

if not api_key_file.exists():
    print(
        f"fetch-agentmail: missing API key file: {api_key_file}",
        file=sys.stderr,
    )
    sys.exit(0)

api_key = api_key_file.read_text().strip()
if not api_key:
    print(
        f"fetch-agentmail: API key file is empty: {api_key_file}",
        file=sys.stderr,
    )
    sys.exit(0)

inbox_dir.mkdir(parents=True, exist_ok=True)
known_ids = read_known_message_ids(inbox_dir)
counter = next_nnn(inbox_dir)

base = os.environ["AGENTMAIL_BASE_URL"].rstrip("/") + "/inboxes"
quoted_inbox = urllib.parse.quote(inbox_id, safe="@")

try:
    listing = request_json(f"{base}/{quoted_inbox}/messages", api_key)
except urllib.error.HTTPError as exc:
    print(f"fetch-agentmail: agentmail HTTP error: {exc.code}", file=sys.stderr)
    sys.exit(2)
except Exception as exc:
    print(f"fetch-agentmail: request failed: {exc}", file=sys.stderr)
    sys.exit(2)

messages = listing.get("messages") or []
new_count = 0

for message in sorted(messages, key=lambda item: item.get("created_at", "")):
    message_id = (message.get("message_id") or "").strip()
    if not message_id or message_id in known_ids:
        continue

    detail = {}
    try:
        quoted_message = urllib.parse.quote(message_id, safe="")
        detail = request_json(f"{base}/{quoted_inbox}/messages/{quoted_message}", api_key)
    except Exception:
        detail = {}

    sender = str(detail.get("from") or message.get("from") or "unknown sender")
    subject = str(detail.get("subject") or message.get("subject") or "")
    created_at = str(detail.get("created_at") or message.get("created_at") or "")
    labels = detail.get("labels") or message.get("labels") or []
    thread_id = str(detail.get("thread_id") or message.get("thread_id") or "")
    body = (
        detail.get("text")
        or detail.get("body_text")
        or detail.get("body")
        or message.get("preview")
        or ""
    )
    if not isinstance(body, str):
        body = json.dumps(body, indent=2)

    reply_target = map_reply_target(repo_root, sender, labels, thread_id)
    if reply_target:
        filename = f"{reply_target}-reply.md"
    else:
        filename = f"{counter:03d}-from-{slugify(sender)}.md"
    content = "\n".join(
        [
            "---",
            f"message_id: {json.dumps(message_id)}",
            f"from: {json.dumps(sender)}",
            f"subject: {json.dumps(subject)}",
            f"created_at: {json.dumps(created_at)}",
            f"labels: {json.dumps(labels)}",
            f"thread_id: {json.dumps(thread_id)}",
            'source: "agentmail"',
            "---",
            "",
            body.rstrip(),
            "",
        ]
    )
    (inbox_dir / filename).write_text(content)
    print(f"fetch-agentmail: wrote {filename}")
    known_ids.add(message_id)
    if not reply_target:
        counter += 1
    new_count += 1

sys.exit(1 if new_count else 0)
PY
