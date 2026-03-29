"""
Channel abstraction: presence model, polling, and message delivery.

Each channel knows its presence model (sync|async) and whether the user
is currently reachable. The somatic loop uses channels to poll for inbound
messages and deliver garden responses.

All channels normalise to the same message dict:
  {channel, channel_ref, sender, content, file (optional)}

The FilesystemChannel watches <runtime-root>/inbox/operator/ for
operator-written .md files and delivers responses to
<runtime-root>/inbox/<garden_name>/. The inbox is bidirectional: each party
owns a subdirectory.

Seen-tracking and acknowledge:
  poll() returns messages without marking them seen. The caller must call
  acknowledge(msg) after successfully processing each message. This prevents
  silent message loss if processing fails — the message will be re-delivered
  on the next poll. Empty files are auto-acknowledged in poll() since they
  carry no content to process.
"""

import datetime
import pathlib
from abc import ABC, abstractmethod

from .garden import filesystem_reply_dir, garden_paths


class Channel(ABC):
    name: str
    presence_model: str  # "sync" | "async"

    def available(self) -> bool:
        """True if the operator is reachable for a proactive push right now."""
        # async channels are always reachable (async = deliver when ready)
        # sync channels need an active presence signal (subclasses override)
        return self.presence_model == "async"

    @abstractmethod
    def poll(self) -> list[dict]:
        """
        Return new inbound messages since last acknowledgement, each as a
        normalised dict. Does NOT mark messages seen — call acknowledge()
        after successfully processing each message.
        """
        ...

    @abstractmethod
    def acknowledge(self, msg: dict) -> None:
        """Mark a message as processed so it is not returned by future polls."""
        ...

    @abstractmethod
    def send(self, conversation_id: str, content: str, **kwargs) -> None:
        """Deliver a response to the operator through this channel."""
        ...


class FilesystemChannel(Channel):
    """
    Operator writes .md files to <runtime-root>/inbox/operator/.
    Garden responds by writing .md files to <runtime-root>/inbox/<garden_name>/,
    where <runtime-root> comes from PAK2.toml [runtime].root and the default
    garden_name comes from PAK2.toml [garden].name.
    Presence model: async — no reliable online signal from filesystem alone.

    Seen messages are tracked in inbox/.seen to survive restarts.
    channel_ref is the stable inbox directory path (e.g. "inbox/operator"),
    not an individual file — all messages from one directory thread into
    the same conversation.
    """
    name = "filesystem"
    presence_model = "async"

    def __init__(self, root: pathlib.Path, garden_name: str | None = None):
        self.root        = root
        self.garden_name = garden_name
        paths = garden_paths(garden_root=root)
        self._inbox_in   = paths.operator_inbox_dir
        self._seen_file  = paths.inbox_seen_path

    def _seen(self) -> set:
        if not self._seen_file.exists():
            return set()
        return set(self._seen_file.read_text(encoding="utf-8").splitlines())

    def _mark_seen(self, name: str) -> None:
        seen = self._seen()
        seen.add(name)
        self._seen_file.parent.mkdir(parents=True, exist_ok=True)
        self._seen_file.write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")

    def poll(self) -> list[dict]:
        if not self._inbox_in.exists():
            return []
        seen = self._seen()
        messages = []
        for f in sorted(self._inbox_in.iterdir()):
            if f.suffix != ".md" or f.name in seen or not f.is_file():
                continue
            content = f.read_text(encoding="utf-8", errors="ignore").strip()
            if not content:
                # Auto-acknowledge empty files — nothing to process.
                self._mark_seen(f.name)
                continue
            messages.append({
                "channel":     self.name,
                "channel_ref": str(self._inbox_in.relative_to(self.root)),
                "sender":      "operator",
                "content":     content,
                "file":        f.name,
            })
        return messages

    def acknowledge(self, msg: dict) -> None:
        """Mark this message as processed. Uses the 'file' field from poll()."""
        fname = msg.get("file")
        if fname:
            self._mark_seen(fname)

    def send(self, conversation_id: str, content: str, **kwargs) -> None:
        inbox_out = filesystem_reply_dir(
            self.root,
            garden_name=self.garden_name,
            ensure=True,
        )
        now_arg = kwargs.get("now")
        if isinstance(now_arg, str) and now_arg.strip():
            now = datetime.datetime.fromisoformat(
                now_arg.strip().replace("Z", "+00:00")
            ).strftime("%Y%m%dT%H%M%SZ")
        else:
            now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = conversation_id.replace("/", "-")[:40]
        out = inbox_out / f"{now}-{slug}.md"
        out.write_text(content, encoding="utf-8")
