"""
Somatic loop: the garden's responsive communication layer.

Watches channels for operator messages. For each message:
  1. Find or create a conversation
  2. Append the message to the conversation log
  3. Submit a 'converse' goal to the coordinator for dispatch
  4. Acknowledge the message (mark seen) only on success

If any step fails, the message is NOT acknowledged and will be re-delivered
on the next poll — at-least-once delivery rather than silent drops.

The coordinator dispatches converse goals like any other goal. The driver,
seeing a conversation_id on the goal, uses --resume if a session_id is
present in the conversation meta, and injects the state + activity diff
since context_at.

Runs in a thread alongside the autonomic Coordinator.
"""

import datetime
import pathlib
import threading

from .channels import Channel, FilesystemChannel
from .conversations import (
    append_message,
    find_open_conversation_for_channel,
    open_conversation,
)
from .garden import garden_paths
from .goals import submit_goal


_DEFAULT_POLL_INTERVAL = 5   # seconds — somatic is more responsive than autonomic


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SomaticLoop:
    """
    Polls channels for inbound messages and submits converse goals.
    """

    def __init__(self, root: pathlib.Path,
                 channels: list[Channel] | None = None,
                 poll_interval: int = _DEFAULT_POLL_INTERVAL,
                 on_goal_submitted=None):
        self.root                = root
        self.poll_interval       = poll_interval
        self._wakeup             = threading.Event()
        self._channels           = channels or [FilesystemChannel(root)]
        self._on_goal_submitted  = on_goal_submitted

    def wake(self) -> None:
        self._wakeup.set()

    def run(self) -> None:
        print(f"somatic: starting (poll={self.poll_interval}s)", flush=True)
        try:
            while True:
                try:
                    self._tick()
                except Exception as exc:
                    print(f"somatic: tick error: {exc}", flush=True)
                self._wakeup.wait(timeout=float(self.poll_interval))
                self._wakeup.clear()
        except KeyboardInterrupt:
            pass

    def _tick(self) -> None:
        for channel in self._channels:
            try:
                messages = channel.poll()
            except Exception as exc:
                print(f"somatic: channel {channel.name} poll error: {exc}", flush=True)
                continue
            for raw in messages:
                try:
                    self._handle(raw, channel)
                    channel.acknowledge(raw)
                except Exception as exc:
                    print(f"somatic: handle failed for message from "
                          f"{channel.name}: {exc}", flush=True)
                    # Do not acknowledge — message will be re-delivered next poll.

    def _handle(self, raw: dict, channel: Channel) -> None:
        now         = _now_utc()
        content     = raw["content"]
        channel_ref = raw.get("channel_ref", "")
        paths = garden_paths(garden_root=self.root)

        conv_id = self._find_or_create(channel, channel_ref, content, now)
        if conv_id is None:
            raise RuntimeError(
                f"could not find or create conversation for channel={channel.name} "
                f"ref={channel_ref!r}"
            )

        result, message_id = append_message(conv_id, "operator", content,
                                            channel=channel.name,
                                            _conv_dir=paths.conversations_dir,
                                            _now=now)
        if not result.ok or message_id is None:
            raise RuntimeError(f"append_message failed: {result.reason} — {result.detail}")

        result, goal_id = submit_goal({
            "type":            "converse",
            "body":            content,
            "submitted_by":    "operator",
            "assigned_to":     "gardener",
            "priority":        7,
            "conversation_id": conv_id,
            "source_message_id": message_id,
        }, _goals_dir=paths.goals_dir, _now=now)
        if not result.ok:
            raise RuntimeError(f"submit_goal failed: {result.reason} — {result.detail}")

        print(f"[{now}] somatic: message → conversation {conv_id} "
              f"(channel={channel.name})", flush=True)

        # Wake the coordinator so it dispatches immediately instead of
        # waiting for the next poll interval (up to 60s).
        if self._on_goal_submitted:
            self._on_goal_submitted()

    def _find_or_create(self, channel: Channel,
                        channel_ref: str, content: str, now: str) -> str | None:
        conv_dir = garden_paths(garden_root=self.root).conversations_dir
        conv = find_open_conversation_for_channel(
            channel.name,
            channel_ref,
            _conv_dir=conv_dir,
        )
        if conv is not None:
            return conv["id"]

        topic = content.splitlines()[0][:60].strip()
        result, conv_id = open_conversation(
            channel=channel.name,
            channel_ref=channel_ref,
            presence_model=channel.presence_model,
            topic=topic,
            _conv_dir=conv_dir,
            _now=now,
        )
        return conv_id if result.ok else None
