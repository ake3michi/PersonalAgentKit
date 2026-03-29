"""
pak2 CLI — entry point for all garden management commands.

Usage:
  pak2 init <dir>             Create a new garden from the pak2 template
  pak2 publish [--root DIR] <dir>
                              Materialize a clean authored export worktree
  pak2 genesis [--root DIR]   Initialize the garden and queue the first gardener goal
  pak2 cycle   [--root DIR]   Run the coordinator loop (Ctrl+C to stop)
  pak2 retrospective          Submit a manual gardener retrospective
  pak2 dashboard [--root DIR] Read-only live observability dashboard
  pak2 chat    [--root DIR]   Interactive chat with the garden
  pak2 hop     [--root DIR]   Request a fresh-session hop
  pak2 status  [--root DIR]   Print current goal and run state
  pak2 submit  [--root DIR]   Submit a goal (interactive or from args)
"""

import argparse
import json
import os
import pathlib
import select
import shutil
import subprocess
import sys
import threading
import time

from .export_surface import (
    BOOTSTRAP_CHARTER_SOURCE as _BOOTSTRAP_CHARTER_SOURCE,
    EXPORTABLE_TEMPLATE_INCLUDES as _TEMPLATE_INCLUDES,
    GARDEN_CONFIG as _GARDEN_CONFIG,
    materialize_bootstrap_charter,
    materialize_export_surface,
)

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - non-POSIX fallback
    termios = None
    tty = None

# The directory that contains the pak2 template (same dir as this file's parent)
_TEMPLATE_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _root_arg(args) -> pathlib.Path:
    return pathlib.Path(args.root).resolve()


def _list_open_conversations(root: pathlib.Path) -> list[dict]:
    from .conversations import list_conversations
    from .garden import garden_paths

    convs = list_conversations(
        status="open",
        _conv_dir=garden_paths(garden_root=root).conversations_dir,
    )
    return sorted(convs, key=lambda conv: conv.get("last_activity_at", ""), reverse=True)


def _resolve_open_conversation(root: pathlib.Path, conv_id: str | None = None) -> dict | None:
    from .conversations import read_conversation
    from .garden import garden_paths

    conv_dir = garden_paths(garden_root=root).conversations_dir

    if conv_id:
        conv = read_conversation(conv_id, _conv_dir=conv_dir)
        if conv and conv.get("status") == "open":
            return conv
        return None
    open_convs = _list_open_conversations(root)
    return open_convs[0] if open_convs else None


def _conversation_status_line(root: pathlib.Path, conv: dict | None) -> str | None:
    if not conv:
        return None

    from .conversations import read_latest_conversation_turn
    from .garden import garden_paths

    turn = read_latest_conversation_turn(
        conv["id"],
        _conv_dir=garden_paths(garden_root=root).conversations_dir,
    ) or {}
    pressure = turn.get("pressure") or conv.get("last_pressure") or {}
    mode = turn.get("mode") or conv.get("last_turn_mode")
    if not mode:
        mode = "resumed" if conv.get("session_id") else (
            "fresh-handoff" if conv.get("last_checkpoint_id") else "fresh-start"
        )
    lineage = (turn.get("lineage") or {}).get("label")
    if not lineage:
        session_ordinal = int(conv.get("session_ordinal") or 0)
        session_turns = int(conv.get("session_turns") or 0)
        if conv.get("session_id"):
            lineage = f"session {session_ordinal or 1} turn {session_turns}"
        elif conv.get("last_checkpoint_id"):
            next_session = session_ordinal + 1 if session_ordinal else 1
            lineage = f"session {next_session} via {conv['last_checkpoint_id']}"
        else:
            lineage = f"session {session_ordinal + 1 if session_ordinal else 1}"

    parts = [f"mode={mode}", f"lineage={lineage}"]
    if pressure:
        parts.append(f"pressure={pressure.get('band', 'unknown')}")
        if pressure.get("provider_input_tokens") is not None:
            parts.append(f"input={pressure['provider_input_tokens']}")
        parts.append(f"tail={pressure.get('tail_messages', 0)}m")
    hop = turn.get("hop") or {}
    if hop.get("performed") and hop.get("checkpoint_id"):
        parts.append(f"hop={hop['checkpoint_id']}")
    elif conv.get("pending_hop"):
        parts.append("hop=requested")
    return "[context: " + " | ".join(parts) + "]"


def _filesystem_channel_ref(root: pathlib.Path) -> str:
    from .garden import garden_paths

    paths = garden_paths(garden_root=root)
    return str(paths.operator_inbox_dir.relative_to(root))


def _filesystem_reply_slug(conversation_id: str) -> str:
    return conversation_id.replace("/", "-")[:40]


def _read_delivered_filesystem_message(root: pathlib.Path, conversation_id: str) -> str | None:
    from .garden import filesystem_reply_dir

    reply_dir = filesystem_reply_dir(root)
    if not reply_dir.exists():
        return None

    slug = _filesystem_reply_slug(conversation_id)
    matches = sorted(reply_dir.glob(f"*-{slug}.md"))
    if not matches:
        return None

    try:
        return matches[-1].read_text(encoding="utf-8")
    except OSError:
        return None


def _build_cycle_startup_message(root: pathlib.Path) -> str | None:
    from .garden import garden_paths
    from .goals import list_goals
    from .runs import list_runs

    paths = garden_paths(garden_root=root)
    if list_runs(_runs_dir=paths.runs_dir):
        return None

    plant_meta = paths.plants_dir / "gardener" / "meta.json"
    if not plant_meta.exists():
        return None

    gardener_goals = sorted(
        (
            goal for goal in list_goals(_goals_dir=paths.goals_dir)
            if goal.get("assigned_to") == "gardener"
        ),
        key=lambda record: str(record.get("submitted_at") or ""),
    )
    if not gardener_goals:
        return None

    first_goal = gardener_goals[0]
    if first_goal.get("status") != "queued":
        return None

    return "\n".join(
        [
            "System startup note from recorded bootstrap facts:",
            "- Gardener commissioned.",
            f"- First gardener goal `{first_goal['id']}` is queued.",
            "- No runs have started yet.",
        ]
    )


def _append_cycle_startup_message(conv_id: str, message: str, *,
                                  conv_dir: pathlib.Path,
                                  now: str) -> bool:
    from .conversations import append_message

    result, _ = append_message(
        conv_id,
        "system",
        message,
        channel="filesystem",
        _conv_dir=conv_dir,
        _now=now,
    )
    if result.ok:
        return True

    print(
        f"startup: failed to record startup message for {conv_id}: "
        f"{result.reason} — {result.detail}",
        file=sys.stderr,
    )
    return False


def _ensure_filesystem_startup_conversation(root: pathlib.Path) -> str | None:
    from .channels import FilesystemChannel
    from .conversations import (
        find_open_conversation_for_channel,
        open_conversation,
        read_messages,
    )
    from .garden import filesystem_reply_dir, garden_paths
    import datetime as _dt

    paths = garden_paths(garden_root=root)
    channel_ref = _filesystem_channel_ref(root)
    existing = find_open_conversation_for_channel(
        "filesystem",
        channel_ref,
        _conv_dir=paths.conversations_dir,
    )
    if existing is not None and read_messages(
        existing["id"],
        _conv_dir=paths.conversations_dir,
        limit=1,
    ):
        return existing["id"]
    if existing is not None and existing.get("started_by") != "system":
        return existing["id"]

    conv_id = existing["id"] if existing is not None else None
    delivered_message = (
        _read_delivered_filesystem_message(root, conv_id)
        if conv_id
        else None
    )
    message = delivered_message or _build_cycle_startup_message(root)
    if not message:
        return conv_id

    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        filesystem_reply_dir(root, ensure=True)
    except OSError as exc:
        print(
            f"startup: failed to prepare filesystem reply surface: {exc}",
            file=sys.stderr,
        )
        return conv_id

    if conv_id is None:
        result, conv_id = open_conversation(
            channel="filesystem",
            channel_ref=channel_ref,
            presence_model=FilesystemChannel.presence_model,
            topic="startup",
            started_by="system",
            _conv_dir=paths.conversations_dir,
            _now=now,
        )
        if not result.ok or conv_id is None:
            print(
                f"startup: failed to open filesystem startup conversation: {result.reason}",
                file=sys.stderr,
            )
            return None

    if delivered_message is None:
        try:
            FilesystemChannel(root).send(conv_id, message)
        except Exception as exc:
            print(
                f"startup: failed to deliver filesystem startup message for {conv_id}: {exc}",
                file=sys.stderr,
            )
            return conv_id

    _append_cycle_startup_message(
        conv_id,
        message,
        conv_dir=paths.conversations_dir,
        now=now,
    )
    return conv_id


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def cmd_init(args):
    dest = pathlib.Path(args.dir).resolve()
    if dest.exists() and any(dest.iterdir()):
        print(f"error: {dest} already exists and is not empty", file=sys.stderr)
        sys.exit(1)

    dest.mkdir(parents=True, exist_ok=True)
    materialize_export_surface(
        _TEMPLATE_ROOT,
        dest,
        driver=args.default_driver,
        model=args.default_model,
        reasoning_effort=args.default_reasoning_effort,
    )
    bootstrap_charter = materialize_bootstrap_charter(_TEMPLATE_ROOT, dest)

    # Initialise as a git repo
    subprocess.run(["git", "init", str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "commit", "-m", "init: new garden from pak2 template"],
        check=True,
    )

    print(f"Garden created at {dest}")
    print(
        "Bootstrap charter: "
        f"{bootstrap_charter} (from {dest / _BOOTSTRAP_CHARTER_SOURCE})"
    )
    print(f"Completion contract: {dest / 'DONE.md'}")
    print(
        "Customize from "
        f"{dest / 'CHARTER.md.example'} or {dest / 'examples'} if needed."
    )
    print(f"Next: review {bootstrap_charter} and {dest / 'DONE.md'}, then run:")
    print(f"  cd {dest}")
    print("  ./pak2 genesis")
    print("  ./pak2 cycle")


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

def _path_is_within(path: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _publish_destination_conflicts_with_source(source_root: pathlib.Path,
                                               dest: pathlib.Path) -> bool:
    source_root = source_root.resolve()
    dest = dest.resolve()

    if dest == source_root:
        return True
    if _path_is_within(source_root, dest):
        return True
    for name in _TEMPLATE_INCLUDES:
        src = source_root / name
        if src.exists() and _path_is_within(dest, src.resolve()):
            return True
    return False


def _remove_tree_entry(path: pathlib.Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink()


def _prepare_publish_destination(source_root: pathlib.Path, dest: pathlib.Path) -> bool:
    if _publish_destination_conflicts_with_source(source_root, dest):
        raise RuntimeError(
            "destination must be separate from the source garden and outside the exported source surface"
        )

    if dest.exists() and not dest.is_dir():
        raise RuntimeError("destination exists and is not a directory")

    if not dest.exists():
        dest.mkdir(parents=True, exist_ok=True)
        return False

    existing_entries = list(dest.iterdir())
    if not existing_entries:
        return False

    git_marker = dest / ".git"
    if not git_marker.exists():
        raise RuntimeError(
            "destination exists and is not empty; use an empty directory or an existing git checkout"
        )

    for entry in existing_entries:
        if entry.name == ".git":
            continue
        _remove_tree_entry(entry)
    return True


def cmd_publish(args):
    root = _root_arg(args)
    dest = pathlib.Path(args.dir).resolve()

    try:
        preserved_git = _prepare_publish_destination(root, dest)
        materialize_export_surface(root, dest)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        print(f"publish: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Publish worktree created at {dest}")
    print(
        "Quickstart charter example: "
        f"{dest / _BOOTSTRAP_CHARTER_SOURCE}"
    )
    print(f"Custom charter template: {dest / 'CHARTER.md.example'}")
    print("`pak2 init` materializes `CHARTER.md` in each new garden.")
    print(f"Config: {dest / _GARDEN_CONFIG}")
    if preserved_git:
        print("Preserved destination .git metadata and replaced the rest of the worktree.")
    else:
        print("No git metadata was created or modified.")
    print("No git push, tag, or release action was performed.")


# ---------------------------------------------------------------------------
# genesis
# ---------------------------------------------------------------------------

def cmd_genesis(args):
    root = _root_arg(args)
    from .genesis import genesis
    genesis(root)


# ---------------------------------------------------------------------------
# cycle
# ---------------------------------------------------------------------------

def cmd_cycle(args):
    root  = _root_arg(args)
    max_c = args.max_concurrent
    poll  = args.poll_interval
    from .coordinator import Coordinator
    from .somatic import SomaticLoop
    import threading

    print(f"Starting garden cycle at {root}")
    startup_tracking_enabled = _build_cycle_startup_message(root) is not None

    # Coordinator must be created first so we can wire the wake bridge in both
    # directions: somatic wakes the coordinator on new messages, and converse
    # completion wakes somatic to recheck for follow-up operator messages.
    coord = Coordinator(root, max_concurrent=max_c, poll_interval=poll)

    startup_conv_id = _ensure_filesystem_startup_conversation(root)
    if startup_tracking_enabled and startup_conv_id:
        coord.set_startup_conversation(startup_conv_id)

    somatic = SomaticLoop(root, on_goal_submitted=coord.wake)
    coord.on_converse_finished = somatic.wake
    somatic_thread = threading.Thread(target=somatic.run, daemon=True, name="somatic")
    somatic_thread.start()

    coord.run()   # blocks until KeyboardInterrupt


# ---------------------------------------------------------------------------
# message
# ---------------------------------------------------------------------------

def cmd_message(args):
    root    = _root_arg(args)
    content = args.body
    from .garden import garden_paths

    inbox = garden_paths(garden_root=root).operator_inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    msg_file = inbox / f"{now}-message.md"
    msg_file.write_text(content, encoding="utf-8")
    print(f"Message written to {msg_file.relative_to(root)}")
    print("The somatic loop will pick it up on its next tick.")


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

_CHAT_WATCH_INTERVAL_SECONDS = 0.25
_CHAT_ESCAPE_READ_TIMEOUT_SECONDS = 0.01
_CHAT_EDITOR_MAX_VISIBLE_LINES = 4


def _chat_label(text: str, *, color: int, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[1;{color}m{text}\033[0m"


def _format_chat_message_block(prefix: str, content: str, *, continuation_width: int) -> str:
    body = content.rstrip("\n")
    lines = body.split("\n") if body else [""]
    continuation_prefix = " " * continuation_width
    rendered = [f"{prefix} {lines[0]}"]
    rendered.extend(f"{continuation_prefix}{line}" for line in lines[1:])
    return "\n".join(rendered)


class _ChatLineEditor:
    def __init__(self) -> None:
        self._buffer: list[str] = []
        self.cursor = 0
        self._preferred_column: int | None = None

    @property
    def text(self) -> str:
        return "".join(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()
        self.cursor = 0
        self._preferred_column = None

    def _reset_preferred_column(self) -> None:
        self._preferred_column = None

    def _lines_with_starts(self) -> tuple[list[str], list[int]]:
        lines = self.text.split("\n")
        starts: list[int] = []
        offset = 0
        for line in lines:
            starts.append(offset)
            offset += len(line) + 1
        return lines, starts

    def _cursor_line_and_column(self) -> tuple[int, int]:
        lines, starts = self._lines_with_starts()
        for line_index, start in enumerate(starts):
            line_end = start + len(lines[line_index])
            if self.cursor <= line_end:
                return line_index, self.cursor - start
        last_index = len(lines) - 1
        return last_index, len(lines[last_index])

    def _move_vertical(self, delta: int) -> None:
        lines, starts = self._lines_with_starts()
        line_index, column = self._cursor_line_and_column()
        target_line = line_index + delta
        if target_line < 0 or target_line >= len(lines):
            return
        self.cursor = starts[target_line] + min(column, len(lines[target_line]))
        self._preferred_column = column

    def _visual_rows(
        self,
        available_width: int,
    ) -> tuple[list[str], list[tuple[int, int, int]], int, int]:
        width = max(available_width, 1)
        lines, _ = self._lines_with_starts()
        cursor_line, cursor_column = self._cursor_line_and_column()

        rows: list[str] = []
        row_meta: list[tuple[int, int, int]] = []
        cursor_row = 0
        cursor_row_column = 0

        for line_index, line in enumerate(lines):
            segment_starts = list(range(0, len(line), width)) or [0]
            if line_index == cursor_line:
                if not line:
                    cursor_segment_index = 0
                elif cursor_column >= len(line):
                    cursor_segment_index = len(segment_starts) - 1
                else:
                    cursor_segment_index = cursor_column // width
            else:
                cursor_segment_index = -1

            for segment_index, start in enumerate(segment_starts):
                segment = line[start:start + width]
                rows.append(segment)
                row_meta.append((line_index, start, len(segment)))
                if line_index == cursor_line and segment_index == cursor_segment_index:
                    cursor_row = len(rows) - 1
                    cursor_row_column = cursor_column - start

        return rows or [""], row_meta or [(0, 0, 0)], cursor_row, cursor_row_column

    def _move_visual(self, delta: int, *, available_width: int) -> None:
        _, row_meta, cursor_row, cursor_row_column = self._visual_rows(available_width)
        target_row = cursor_row + delta
        if target_row < 0 or target_row >= len(row_meta):
            return

        preferred_column = (
            self._preferred_column
            if self._preferred_column is not None
            else cursor_row_column
        )
        lines, starts = self._lines_with_starts()
        line_index, segment_start, segment_length = row_meta[target_row]
        self.cursor = starts[line_index] + segment_start + min(preferred_column, segment_length)
        self._preferred_column = preferred_column

    def render_view(
        self,
        width: int,
        prompt_width: int,
        *,
        max_lines: int,
    ) -> tuple[list[str], int, int]:
        available = max(width - prompt_width, 1)
        rows, _, cursor_row, cursor_row_column = self._visual_rows(available)

        if len(rows) <= max_lines:
            first_visible_row = 0
        else:
            max_first_visible_row = len(rows) - max_lines
            first_visible_row = min(
                max(cursor_row - max_lines + 1, 0),
                max_first_visible_row,
            )

        last_visible_row = min(first_visible_row + max_lines, len(rows))
        visible_lines = rows[first_visible_row:last_visible_row]
        cursor_display_column = prompt_width + cursor_row_column
        return visible_lines or [""], cursor_row - first_visible_row, cursor_display_column

    def handle_key(self, key: str, available_width: int | None = None) -> tuple[str, str | None]:
        if key == "\r":
            text = self.text
            self.clear()
            return "submit", text

        if key == "\n":
            self._buffer.insert(self.cursor, "\n")
            self.cursor += 1
            self._reset_preferred_column()
            return "continue", None

        if key == "\x04":
            if self.cursor < len(self._buffer):
                del self._buffer[self.cursor]
                self._reset_preferred_column()
                return "continue", None
            if self._buffer:
                return "continue", None
            return "eof", None

        if key in ("\x7f", "\b"):
            if self.cursor > 0:
                self.cursor -= 1
                del self._buffer[self.cursor]
                self._reset_preferred_column()
            return "continue", None

        if key in ("\x01", "\x1b[H", "\x1bOH", "\x1b[1~", "\x1b[7~"):
            _, starts = self._lines_with_starts()
            line_index, _ = self._cursor_line_and_column()
            self.cursor = starts[line_index]
            self._reset_preferred_column()
            return "continue", None

        if key in ("\x05", "\x1b[F", "\x1bOF", "\x1b[4~", "\x1b[8~"):
            lines, starts = self._lines_with_starts()
            line_index, _ = self._cursor_line_and_column()
            self.cursor = starts[line_index] + len(lines[line_index])
            self._reset_preferred_column()
            return "continue", None

        if key in ("\x02", "\x1b[D"):
            if self.cursor > 0:
                self.cursor -= 1
            self._reset_preferred_column()
            return "continue", None

        if key in ("\x06", "\x1b[C"):
            if self.cursor < len(self._buffer):
                self.cursor += 1
            self._reset_preferred_column()
            return "continue", None

        if key in ("\x10", "\x1b[A"):
            if available_width is not None:
                self._move_visual(-1, available_width=max(available_width, 1))
            else:
                self._move_vertical(-1)
            return "continue", None

        if key in ("\x0e", "\x1b[B"):
            if available_width is not None:
                self._move_visual(1, available_width=max(available_width, 1))
            else:
                self._move_vertical(1)
            return "continue", None

        if key in ("\x1b[3~",):
            if self.cursor < len(self._buffer):
                del self._buffer[self.cursor]
                self._reset_preferred_column()
            return "continue", None

        if key.startswith("\x1b"):
            return "continue", None

        if key.isprintable():
            self._buffer.insert(self.cursor, key)
            self.cursor += 1
            self._reset_preferred_column()
        return "continue", None


class _ChatTTYUI:
    def __init__(self, *,
                 garden_label: str,
                 prompt_label: str = "you",
                 in_stream=None,
                 out_stream=None,
                 use_ansi: bool | None = None) -> None:
        self.garden_label = garden_label
        self.prompt_label = prompt_label
        self.in_stream = in_stream or sys.stdin
        self.out_stream = out_stream or sys.stdout
        self.use_ansi = (
            self.out_stream.isatty()
            if use_ansi is None and hasattr(self.out_stream, "isatty")
            else bool(use_ansi)
        )
        self.editor = _ChatLineEditor()
        self._lock = threading.RLock()
        self._prompt_visible = False
        self._prompt_line_count = 0
        self._prompt_cursor_row = 0
        self._fd: int | None = None
        self._saved_termios = None

    @property
    def prompt_prefix(self) -> str:
        return f"{self.prompt_label}: "

    @property
    def prompt_prefix_display(self) -> str:
        return _chat_label(self.prompt_label, color=34, enabled=self.use_ansi) + ": "

    @property
    def garden_prefix_display(self) -> str:
        return _chat_label(self.garden_label, color=32, enabled=self.use_ansi) + ":"

    @property
    def operator_prefix_display(self) -> str:
        return _chat_label(self.prompt_label, color=34, enabled=self.use_ansi) + ":"

    def __enter__(self):
        if termios is None or tty is None:
            return self
        self._fd = self.in_stream.fileno()
        self._saved_termios = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        if hasattr(termios, "ICRNL"):
            attrs = termios.tcgetattr(self._fd)
            attrs[0] &= ~termios.ICRNL
            termios.tcsetattr(self._fd, termios.TCSADRAIN, attrs)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is None or self._saved_termios is None or termios is None:
            return
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved_termios)
        self._fd = None
        self._saved_termios = None

    def _write(self, text: str) -> None:
        self.out_stream.write(text)
        self.out_stream.flush()

    def _clear_prompt_locked(self) -> None:
        if not self._prompt_visible:
            return
        if self.use_ansi:
            lines_below_cursor = max(self._prompt_line_count - self._prompt_cursor_row - 1, 0)
            if lines_below_cursor > 0:
                self._write(f"\033[{lines_below_cursor}B")
            for line_index in range(self._prompt_line_count):
                self._write("\r\033[2K")
                if line_index < self._prompt_line_count - 1:
                    self._write("\033[1A")
        else:
            self._write("\r")
        self._prompt_visible = False
        self._prompt_line_count = 0
        self._prompt_cursor_row = 0

    def _redraw_prompt_locked(self) -> None:
        if self._prompt_visible:
            self._clear_prompt_locked()
        width = shutil.get_terminal_size(fallback=(120, 40)).columns
        visible_lines, cursor_row, cursor_column = self.editor.render_view(
            width,
            len(self.prompt_prefix),
            max_lines=_CHAT_EDITOR_MAX_VISIBLE_LINES,
        )
        continuation_prefix = " " * len(self.prompt_prefix)
        rendered_lines = [
            (
                f"{self.prompt_prefix_display}{line}"
                if index == 0
                else f"{continuation_prefix}{line}"
            )
            for index, line in enumerate(visible_lines)
        ]
        if self.use_ansi:
            self._write("\r")
            self._write(rendered_lines[0])
            for line in rendered_lines[1:]:
                self._write("\n")
                self._write(line)
            lines_below_cursor = len(rendered_lines) - cursor_row - 1
            if lines_below_cursor > 0:
                self._write(f"\033[{lines_below_cursor}A")
            self._write("\r")
            if cursor_column > 0:
                self._write(f"\033[{cursor_column}C")
        else:
            self._write("\r" + rendered_lines[0])
            for line in rendered_lines[1:]:
                self._write("\n" + line)
        self._prompt_visible = True
        self._prompt_line_count = len(rendered_lines)
        self._prompt_cursor_row = cursor_row

    def redraw_prompt(self) -> None:
        with self._lock:
            self._redraw_prompt_locked()

    def print_note(self, text: str, *, redraw_prompt: bool = True) -> None:
        with self._lock:
            self._clear_prompt_locked()
            body = text.rstrip("\n")
            self._write((body + "\n") if body else "\n")
            if redraw_prompt:
                self._redraw_prompt_locked()

    def print_reply(self, content: str, *, status_line: str | None = None) -> None:
        with self._lock:
            self._clear_prompt_locked()
            if status_line:
                self._write(status_line.rstrip("\n") + "\n")
            self._write(f"{self.garden_prefix_display}\n")
            body = content.rstrip("\n")
            if body:
                self._write(body + "\n")
            self._write("\n")
            self._redraw_prompt_locked()

    def print_sent_message(self, content: str, *, waiting_note: str | None = None) -> None:
        with self._lock:
            self._clear_prompt_locked()
            self._write(
                _format_chat_message_block(
                    self.operator_prefix_display,
                    content,
                    continuation_width=len(self.prompt_prefix),
                )
                + "\n"
            )
            if waiting_note:
                self._write(waiting_note.rstrip("\n") + "\n")
            self._write("\n")
            self._redraw_prompt_locked()

    def _read_key(self) -> str | None:
        if self._fd is None:
            return None
        ready, _, _ = select.select([self._fd], [], [], _CHAT_WATCH_INTERVAL_SECONDS)
        if not ready:
            return None
        data = os.read(self._fd, 1)
        if not data:
            return "\x04"
        key = data.decode("utf-8", errors="ignore")
        if key != "\x1b":
            return key

        sequence = key
        while True:
            ready, _, _ = select.select(
                [self._fd],
                [],
                [],
                _CHAT_ESCAPE_READ_TIMEOUT_SECONDS,
            )
            if not ready:
                return sequence
            chunk = os.read(self._fd, 1)
            if not chunk:
                return sequence
            sequence += chunk.decode("utf-8", errors="ignore")
            if sequence.endswith("~") or sequence[-1].isalpha():
                return sequence

    def read_message(self) -> str:
        self.redraw_prompt()
        while True:
            key = self._read_key()
            if key is None:
                continue
            width = shutil.get_terminal_size(fallback=(120, 40)).columns
            action, payload = self.editor.handle_key(
                key,
                max(width - len(self.prompt_prefix), 1),
            )
            if action == "submit":
                with self._lock:
                    self._clear_prompt_locked()
                return payload or ""
            if action == "eof":
                with self._lock:
                    self._clear_prompt_locked()
                raise EOFError
            self.redraw_prompt()

def cmd_chat(args):
    root = _root_arg(args)
    import datetime as _dt

    from .conversations import request_conversation_hop
    from .garden import (
        filesystem_reply_dir,
        garden_paths,
        resolve_garden_display_name,
    )

    paths = garden_paths(garden_root=root)
    inbox_in = paths.operator_inbox_dir
    inbox_in.mkdir(parents=True, exist_ok=True)

    garden_label = resolve_garden_display_name(garden_root=root)
    color_output = bool(hasattr(sys.stdout, "isatty") and sys.stdout.isatty())
    tty_mode = bool(
        termios is not None
        and tty is not None
        and hasattr(sys.stdin, "isatty")
        and hasattr(sys.stdout, "isatty")
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )
    ui = _ChatTTYUI(garden_label=garden_label) if tty_mode else None

    seen = {f.name for f in filesystem_reply_dir(root).glob("*.md")}
    stop = threading.Event()

    def print_reply(content: str, *, status_line: str | None = None) -> None:
        if ui is not None:
            ui.print_reply(content, status_line=status_line)
            return

        if status_line:
            print(f"\n{status_line}")
        print(f"\n{_chat_label(garden_label, color=32, enabled=color_output)}:")
        body = content.rstrip("\n")
        if body:
            print(body)
        print()

    def print_new_responses():
        for f in sorted(filesystem_reply_dir(root).glob("*.md")):
            if f.name in seen:
                continue
            seen.add(f.name)
            status_line = _conversation_status_line(root, _resolve_open_conversation(root))
            print_reply(
                f.read_text(encoding="utf-8"),
                status_line=status_line,
            )

    print(f"=== {garden_label} chat ===")
    if tty_mode:
        print(
            "Use the visible multi-line draft area below. Edit with Left/Right, "
            "Up/Down, Home/End, Backspace, and Delete. Long lines wrap in place. "
            "Press Enter to send or Ctrl-J to add a newline."
        )
    else:
        print("Type a message and press Enter. The garden will respond shortly.")
    print("Use '/status' to inspect context pressure or '/hop [reason]' to request a fresh-session hop.")
    print("Type 'exit' or Ctrl-D to quit.\n")

    conv_root = paths.conversations_dir
    conv_dirs = sorted(
        [d for d in conv_root.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    ) if conv_root.exists() else []
    if conv_dirs:
        msgs_path = conv_dirs[0] / "messages.jsonl"
        if msgs_path.exists():
            conv = _resolve_open_conversation(root)
            status_line = _conversation_status_line(root, conv)
            if status_line:
                print(status_line)
                print()
            print("--- recent history ---")
            for line in msgs_path.read_text(encoding="utf-8").splitlines():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sender = message.get("sender", "?")
                content = str(message.get("content", "")).rstrip("\n")
                if sender == "operator":
                    print(
                        _format_chat_message_block(
                            f"{_chat_label('you', color=34, enabled=color_output)}:",
                            content,
                            continuation_width=len("you: "),
                        )
                        + "\n"
                    )
                else:
                    print(f"{_chat_label(garden_label, color=32, enabled=color_output)}:")
                    print(f"{content}\n")
            print("--- end history ---\n")

    def watcher():
        while not stop.wait(_CHAT_WATCH_INTERVAL_SECONDS):
            try:
                print_new_responses()
            except Exception:
                pass

    thread = threading.Thread(target=watcher, daemon=True, name="chat-watcher")
    thread.start()

    try:
        if ui is not None:
            with ui:
                while True:
                    try:
                        msg = ui.read_message()
                    except (EOFError, KeyboardInterrupt):
                        ui.print_note("bye", redraw_prompt=False)
                        break

                    stripped = msg.strip()
                    is_single_line = "\n" not in msg
                    if not stripped:
                        continue
                    if is_single_line and stripped in ("exit", "quit"):
                        ui.print_note("bye", redraw_prompt=False)
                        break
                    if is_single_line and stripped == "/status":
                        status_line = _conversation_status_line(root, _resolve_open_conversation(root))
                        ui.print_note(status_line or "(no open conversation)")
                        continue
                    if is_single_line and stripped.startswith("/hop"):
                        conv = _resolve_open_conversation(root)
                        if conv is None:
                            ui.print_note("(no open conversation to hop)")
                            continue
                        reason = stripped[4:].strip() or "operator requested session hop"
                        result = request_conversation_hop(
                            conv["id"],
                            requested_by="operator",
                            reason=reason,
                            _conv_dir=paths.conversations_dir,
                        )
                        if result.ok:
                            ui.print_note(
                                f"(fresh-session hop requested for {conv['id']}; the next reply "
                                "will be sent before a checkpoint is written)"
                            )
                        else:
                            ui.print_note(f"(hop request failed: {result.reason})")
                        continue

                    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    (inbox_in / f"{now}-message.md").write_text(msg, encoding="utf-8")
                    ui.print_sent_message(msg)
                    time.sleep(0.05)
                    print_new_responses()
        else:
            while True:
                try:
                    prompt = _chat_label("you", color=34, enabled=color_output) + ": "
                    msg = input(prompt)
                except (EOFError, KeyboardInterrupt):
                    print("\nbye")
                    break

                stripped = msg.strip()
                if not stripped:
                    continue
                if stripped in ("exit", "quit"):
                    print("bye")
                    break
                if stripped == "/status":
                    status_line = _conversation_status_line(root, _resolve_open_conversation(root))
                    print(status_line or "(no open conversation)")
                    continue
                if stripped.startswith("/hop"):
                    conv = _resolve_open_conversation(root)
                    if conv is None:
                        print("(no open conversation to hop)")
                        continue
                    reason = stripped[4:].strip() or "operator requested session hop"
                    result = request_conversation_hop(
                        conv["id"],
                        requested_by="operator",
                        reason=reason,
                        _conv_dir=paths.conversations_dir,
                    )
                    if result.ok:
                        print(
                            f"(fresh-session hop requested for {conv['id']}; the next reply "
                            "will be sent before a checkpoint is written)"
                        )
                    else:
                        print(f"(hop request failed: {result.reason})")
                    continue

                now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                (inbox_in / f"{now}-message.md").write_text(msg, encoding="utf-8")
                print(f"(sent - waiting for {garden_label}...)")
                time.sleep(0.05)
                print_new_responses()
    finally:
        stop.set()
        thread.join(timeout=0.1)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args):
    root = _root_arg(args)
    from .goals import list_goals
    from .coordinator import _list_all_runs
    from .garden import garden_paths

    paths = garden_paths(garden_root=root)
    goals_dir = paths.goals_dir
    runs_dir = paths.runs_dir

    goals = list_goals(_goals_dir=goals_dir)
    runs  = _list_all_runs(runs_dir)

    # Group runs by goal
    runs_by_goal: dict[str, list] = {}
    for r in runs:
        goal_id = r.get("goal") or r.get("goal_id")
        if goal_id:
            runs_by_goal.setdefault(goal_id, []).append(r)

    open_statuses = {"queued", "dispatched", "running", "completed", "evaluating"}
    open_goals   = [g for g in goals if g["status"] in open_statuses]
    closed_goals = [g for g in goals if g["status"] == "closed"]
    active_runs  = [r for r in runs if r.get("status") == "running"]

    # Garden state summary
    if not goals:
        print("Garden has no goals yet.")
        return
    elif active_runs:
        print(f"Garden active — {len(active_runs)} run(s) in progress\n")
    elif open_goals:
        print(f"Garden waiting — {len(open_goals)} goal(s) queued\n")
    else:
        print("Garden idle\n")

    if open_goals:
        print("=== Active / queued ===")
        for g in sorted(open_goals, key=lambda g: g["submitted_at"]):
            plant = g.get("assigned_to", "(unassigned)")
            pri   = g.get("priority", 5)
            print(f"  [{g['status']:12}] {g['id']}  plant={plant}  pri={pri}")
            for r in runs_by_goal.get(g["id"], []):
                run_id = r.get("id") or r.get("run_id", "?")
                print(f"    run {run_id}  status={r.get('status', '?')}")

    if closed_goals:
        print(f"\n=== Closed ({len(closed_goals)}) ===")
        for g in sorted(closed_goals, key=lambda g: g["submitted_at"])[-10:]:
            reason = g.get("closed_reason", "?")
            print(f"  [{reason:8}] {g['id']}")
        if len(closed_goals) > 10:
            print(f"  ... and {len(closed_goals) - 10} more")

    open_convs = _list_open_conversations(root)
    if open_convs:
        print(f"\n=== Conversations ({len(open_convs)}) ===")
        for conv in open_convs[:5]:
            status_line = _conversation_status_line(root, conv)
            print(f"  {conv['id']}")
            if status_line:
                print(f"    {status_line}")


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------

def cmd_dashboard(args):
    root = _root_arg(args)
    from .dashboard import build_snapshot, render_dashboard
    from .dashboard_invocations import (
        finish_dashboard_invocation,
        start_dashboard_invocation,
    )
    import time

    refresh = float(args.refresh)
    if refresh <= 0:
        print("error: --refresh must be greater than 0", file=sys.stderr)
        sys.exit(1)

    tty_mode = sys.stdout.isatty()
    once = bool(args.once or not tty_mode)
    mode = "once" if once else "live"
    result, invocation = start_dashboard_invocation(
        root,
        mode=mode,
        refresh_seconds=refresh,
        tty=tty_mode,
    )
    if not result.ok or invocation is None:
        print(
            (
                "error: failed to record dashboard invocation start: "
                f"{result.reason} — {result.detail}"
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    render_count = 0
    outcome = "success"
    error_detail = None

    try:
        while True:
            size = shutil.get_terminal_size(fallback=(120, 40))
            snapshot = build_snapshot(root)
            output = render_dashboard(
                snapshot,
                width=size.columns,
                height=size.lines if tty_mode else None,
            )
            render_count += 1
            if once:
                print(output)
                return

            print("\033[2J\033[H", end="")
            print(output, flush=True)
            time.sleep(refresh)
    except KeyboardInterrupt:
        outcome = "interrupted"
        error_detail = None
        if tty_mode:
            print()
    except Exception as exc:
        outcome = "failure"
        error_detail = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        result = finish_dashboard_invocation(
            invocation,
            outcome=outcome,
            render_count=render_count,
            error_detail=error_detail,
        )
        if not result.ok:
            print(
                (
                    "error: failed to record dashboard invocation finish: "
                    f"{result.reason} — {result.detail}"
                ),
                file=sys.stderr,
            )
            if outcome == "success":
                raise SystemExit(1)


# ---------------------------------------------------------------------------
# hop
# ---------------------------------------------------------------------------

def cmd_hop(args):
    root = _root_arg(args)
    from .conversations import request_conversation_hop
    from .garden import garden_paths

    conv = _resolve_open_conversation(root, args.conversation)
    if conv is None:
        print("error: no matching open conversation", file=sys.stderr)
        sys.exit(1)

    if not conv.get("session_id"):
        if conv.get("last_checkpoint_id"):
            print(
                f"Conversation {conv['id']} is already waiting for a fresh session from "
                f"{conv['last_checkpoint_id']}."
            )
            return
        print(f"Conversation {conv['id']} has no active session to hop.")
        return

    reason = args.reason or "operator requested session hop"
    result = request_conversation_hop(
        conv["id"],
        requested_by="operator",
        reason=reason,
        _conv_dir=garden_paths(garden_root=root).conversations_dir,
    )
    if not result.ok:
        print(f"error: {result.reason} — {result.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Requested fresh-session hop for {conv['id']}.")
    status_line = _conversation_status_line(root, _resolve_open_conversation(root, conv["id"]))
    if status_line:
        print(status_line)


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------

def cmd_submit(args):
    root = _root_arg(args)
    from .goals import submit_goal
    from .garden import garden_paths

    data = {
        "type":         args.type,
        "body":         args.body,
        "submitted_by": args.submitted_by,
    }
    if args.assign:
        data["assigned_to"] = args.assign
    if args.priority:
        data["priority"] = args.priority
    if args.not_before:
        data["not_before"] = args.not_before
    if args.depends_on:
        data["depends_on"] = args.depends_on
    if args.driver:
        data["driver"] = args.driver
    if args.model:
        data["model"] = args.model
    if args.reasoning_effort:
        data["reasoning_effort"] = args.reasoning_effort

    result, goal_id = submit_goal(data, _goals_dir=garden_paths(garden_root=root).goals_dir)
    if not result.ok:
        print(f"error: {result.reason} — {result.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Submitted: {goal_id}")


def cmd_retrospective(args):
    root = _root_arg(args)
    from .garden import garden_paths
    from .submit import submit_retrospective_goal

    result, goal_id = submit_retrospective_goal(
        submitted_by=args.submitted_by,
        recent_run_limit=args.recent_run_limit,
        allow_follow_up_goal=args.allow_follow_up_goal,
        priority=args.priority,
        driver=args.driver,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        _goals_dir=garden_paths(garden_root=root).goals_dir,
    )
    if not result.ok:
        print(f"error: {result.reason} — {result.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Submitted: {goal_id}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    from .coordinator import _DEFAULT_MAX_CONCURRENT, _DEFAULT_POLL_INTERVAL

    parser = argparse.ArgumentParser(
        prog="pak2",
        description="Personal Agent Kit 2 — garden management CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Create a new garden from the pak2 template")
    p_init.add_argument("dir", help="Destination directory")
    p_init.add_argument("--default-driver",
                        help="Garden default driver written to PAK2.toml")
    p_init.add_argument("--default-model",
                        help="Garden default model written to PAK2.toml")
    p_init.add_argument("--default-reasoning-effort",
                        choices=["low", "medium", "high", "xhigh"],
                        help="Garden default reasoning effort written to PAK2.toml")
    p_init.set_defaults(func=cmd_init)

    # publish
    p_pub = sub.add_parser(
        "publish",
        help="Materialize a clean authored export worktree",
    )
    p_pub.add_argument("dir", help="Destination directory or existing git checkout")
    p_pub.add_argument("--root", default=".", help="Garden root to export (default: .)")
    p_pub.set_defaults(func=cmd_publish)

    # genesis
    p_gen = sub.add_parser(
        "genesis",
        help="Initialize the garden and queue the first gardener goal",
    )
    p_gen.add_argument("--root", default=".", help="Garden root (default: .)")
    p_gen.set_defaults(func=cmd_genesis)

    # cycle
    p_cyc = sub.add_parser("cycle", help="Run the coordinator loop")
    p_cyc.add_argument("--root", default=".", help="Garden root (default: .)")
    p_cyc.add_argument("--max-concurrent", type=int, default=_DEFAULT_MAX_CONCURRENT,
                       metavar="N",
                       help=f"Max concurrent runs (default: {_DEFAULT_MAX_CONCURRENT})")
    p_cyc.add_argument("--poll-interval", type=int, default=_DEFAULT_POLL_INTERVAL,
                       metavar="SECS",
                       help=f"Poll interval in seconds (default: {_DEFAULT_POLL_INTERVAL})")
    p_cyc.set_defaults(func=cmd_cycle)

    # status
    p_st = sub.add_parser("status", help="Show current goal and run state")
    p_st.add_argument("--root", default=".", help="Garden root (default: .)")
    p_st.set_defaults(func=cmd_status)

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Read-only live observability dashboard")
    p_dash.add_argument("--root", default=".", help="Garden root (default: .)")
    p_dash.add_argument("--refresh", type=float, default=2.0,
                        metavar="SECS",
                        help="Refresh interval in seconds for live mode (default: 2)")
    p_dash.add_argument("--once", action="store_true",
                        help="Render one snapshot and exit")
    p_dash.set_defaults(func=cmd_dashboard)

    # chat
    p_chat = sub.add_parser("chat", help="Interactive chat with the garden")
    p_chat.add_argument("--root", default=".", help="Garden root (default: .)")
    p_chat.set_defaults(func=cmd_chat)

    # hop
    p_hop = sub.add_parser("hop", help="Request a fresh-session hop")
    p_hop.add_argument("--root", default=".", help="Garden root (default: .)")
    p_hop.add_argument("--conversation", help="Conversation ID (defaults to latest open)")
    p_hop.add_argument("--reason", help="Reason recorded with the hop request")
    p_hop.set_defaults(func=cmd_hop)

    # message
    p_msg = sub.add_parser("message", help="Send a message to the garden")
    p_msg.add_argument("--root", default=".", help="Garden root (default: .)")
    p_msg.add_argument("body", help="Message text")
    p_msg.set_defaults(func=cmd_message)

    # submit
    p_sub = sub.add_parser("submit", help="Submit a goal")
    p_sub.add_argument("--root", default=".", help="Garden root (default: .)")
    p_sub.add_argument("--type", required=True,
                       choices=["build", "fix", "spike", "tend", "evaluate", "research"])
    p_sub.add_argument("--body", required=True, help="Goal body text")
    p_sub.add_argument("--assign", help="Plant to assign to")
    p_sub.add_argument("--submitted-by", default="operator")
    p_sub.add_argument("--priority", type=int, choices=range(1, 11), metavar="1-10")
    p_sub.add_argument("--not-before", metavar="ISO8601")
    p_sub.add_argument("--depends-on", nargs="+", metavar="GOAL_ID")
    p_sub.add_argument("--driver", help="Execution driver override (e.g. claude, codex)")
    p_sub.add_argument("--model", help="Model override for the selected driver")
    p_sub.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh"],
                       help="Reasoning effort override for compatible drivers")
    p_sub.set_defaults(func=cmd_submit)

    # retrospective
    p_ret = sub.add_parser(
        "retrospective",
        help="Submit a manual retrospective evaluate goal for gardener",
    )
    p_ret.add_argument("--root", default=".", help="Garden root (default: .)")
    p_ret.add_argument("--submitted-by", default="operator")
    p_ret.add_argument("--recent-run-limit", type=int, default=5, metavar="N",
                       help="Fallback substantive-run cap when no prior retrospective exists (default: 5)")
    p_ret.add_argument("--allow-follow-up-goal", action="store_true",
                       help="Authorize at most one bounded follow-up goal from the retrospective")
    p_ret.add_argument("--priority", type=int, choices=range(1, 11), metavar="1-10")
    p_ret.add_argument("--driver", help="Execution driver override (e.g. claude, codex)")
    p_ret.add_argument("--model", help="Model override for the selected driver")
    p_ret.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh"],
                       help="Reasoning effort override for compatible drivers")
    p_ret.set_defaults(func=cmd_retrospective)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
