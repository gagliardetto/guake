# -*- coding: utf-8; -*-
"""
Command block model for Guake terminals.

Tracks command/output blocks using shell integration markers received
via a FIFO side-channel. Provides:
- Block data model (command, exit code, duration, row ranges)
- FIFO reader (GLib IO watch for shell integration events)
- Block overlay (Cairo drawing layer for visual decorations)
- Block navigation (jump between commands)
"""
import json
import logging
import os
import stat
import time
import cairo

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, Gdk, GLib, Vte, Pango

log = logging.getLogger(__name__)


# ############################################################################
# Block data model
# ############################################################################

class Block:
    """A single command block: prompt → command → output → exit."""
    __slots__ = (
        'command', 'prompt_row', 'command_row', 'end_row',
        'exit_code', 'duration', 'timestamp', 'collapsed',
    )

    def __init__(self, prompt_row):
        self.command = None
        self.prompt_row = prompt_row      # row where the prompt appeared
        self.command_row = None           # row where command execution started
        self.end_row = None               # row where the next prompt appeared
        self.exit_code = None
        self.duration = None              # seconds
        self.timestamp = time.time()
        self.collapsed = False

    @property
    def is_complete(self):
        return self.end_row is not None

    @property
    def is_running(self):
        return self.command_row is not None and self.end_row is None

    @property
    def is_success(self):
        return self.exit_code == 0

    @property
    def output_line_count(self):
        if self.command_row is not None and self.end_row is not None:
            return max(0, self.end_row - self.command_row - 1)
        return 0

    def format_duration(self):
        if self.duration is None:
            return ""
        d = self.duration
        if d < 1:
            return "<1s"
        elif d < 60:
            return f"{d}s"
        elif d < 3600:
            return f"{d // 60}m{d % 60:02d}s"
        else:
            return f"{d // 3600}h{(d % 3600) // 60:02d}m"


class BlockModel:
    """Maintains the list of command blocks for a terminal."""

    def __init__(self, terminal):
        self.terminal = terminal
        self.blocks = []
        self._current_block = None
        self._input_phase = False   # True = prompt shown, waiting for command

    @property
    def input_phase(self):
        return self._input_phase

    def _get_cursor_row(self):
        """Get the terminal's current cursor row (absolute, including scrollback)."""
        try:
            col, row = self.terminal.get_cursor_position()
            adj = self.terminal.get_vadjustment()
            return int(adj.get_value()) + row
        except Exception:
            return 0

    def on_prompt_start(self):
        """Shell emitted prompt_start — a new prompt is being shown."""
        cursor_row = self._get_cursor_row()

        # Close the previous block
        if self._current_block and not self._current_block.is_complete:
            self._current_block.end_row = cursor_row

        # Start a new block
        self._current_block = Block(prompt_row=cursor_row)
        self.blocks.append(self._current_block)
        self._input_phase = True

        # Limit block history to prevent unbounded growth
        if len(self.blocks) > 500:
            self.blocks = self.blocks[-400:]

    def on_command_start(self, command_text=None):
        """Shell emitted command_start — user submitted a command."""
        if self._current_block:
            self._current_block.command = command_text
            self._current_block.command_row = self._get_cursor_row()
        self._input_phase = False

    def on_command_end(self, exit_code=None, duration=None):
        """Shell emitted command_end — command finished executing."""
        if self._current_block:
            self._current_block.exit_code = exit_code
            self._current_block.duration = duration
        # Don't set input_phase here — wait for prompt_start

    def get_block_at_row(self, row):
        """Find the block that contains the given row."""
        for block in reversed(self.blocks):
            if block.prompt_row <= row:
                if block.end_row is None or row < block.end_row:
                    return block
        return None

    def get_prev_block(self, from_row=None):
        """Get the block before the given row (or current cursor)."""
        if from_row is None:
            from_row = self._get_cursor_row()
        for block in reversed(self.blocks):
            if block.prompt_row < from_row:
                return block
        return None

    def get_next_block(self, from_row=None):
        """Get the block after the given row (or current cursor)."""
        if from_row is None:
            from_row = self._get_cursor_row()
        for block in self.blocks:
            if block.prompt_row > from_row:
                return block
        return None

    def get_completed_blocks(self):
        """Return only completed blocks (have exit code)."""
        return [b for b in self.blocks if b.is_complete]


# ############################################################################
# FIFO reader — reads shell integration events from named pipe
# ############################################################################

class BlockFIFOReader:
    """Reads block events from a shell integration FIFO."""

    def __init__(self, fifo_path, block_model, on_event_callback=None):
        self.fifo_path = fifo_path
        self.block_model = block_model
        self.on_event = on_event_callback
        self._fd = None
        self._watch_id = None
        self._buffer = ""

    def start(self):
        """Open the FIFO and start watching for events."""
        if not os.path.exists(self.fifo_path):
            return False
        try:
            # O_RDWR keeps the FIFO open even when no writer is connected
            # O_NONBLOCK prevents blocking on read
            self._fd = os.open(self.fifo_path, os.O_RDWR | os.O_NONBLOCK)
            self._watch_id = GLib.io_add_watch(
                self._fd,
                GLib.PRIORITY_DEFAULT,
                GLib.IOCondition.IN | GLib.IOCondition.HUP,
                self._on_data_available,
            )
            log.info("Block FIFO reader started: %s", self.fifo_path)
            return True
        except OSError as e:
            log.error("Failed to open block FIFO %s: %s", self.fifo_path, e)
            return False

    def stop(self):
        """Stop watching and close the FIFO."""
        if self._watch_id is not None:
            GLib.source_remove(self._watch_id)
            self._watch_id = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        # Clean up the FIFO file
        try:
            if os.path.exists(self.fifo_path):
                os.unlink(self.fifo_path)
        except OSError:
            pass

    def _on_data_available(self, fd, condition):
        """GLib IO watch callback — read and parse events from the FIFO."""
        if condition & GLib.IOCondition.HUP:
            # Writer disconnected, but we keep the FIFO open (O_RDWR)
            return True

        try:
            data = os.read(fd, 4096)
            if not data:
                return True
            self._buffer += data.decode('utf-8', errors='replace')
        except (OSError, BlockingIOError):
            return True

        # Process complete lines
        while '\n' in self._buffer:
            line, self._buffer = self._buffer.split('\n', 1)
            line = line.strip()
            if line:
                self._process_event(line)

        return True

    def _process_event(self, line):
        """Parse a JSON event line and update the block model."""
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            log.debug("Invalid block event JSON: %s", line[:100])
            return

        event_type = event.get("event")
        if event_type == "prompt_start":
            self.block_model.on_prompt_start()
        elif event_type == "command_start":
            self.block_model.on_command_start(event.get("command"))
        elif event_type == "command_end":
            self.block_model.on_command_end(
                exit_code=event.get("exit_code"),
                duration=event.get("duration"),
            )
        else:
            log.debug("Unknown block event type: %s", event_type)
            return

        # Notify the UI
        if self.on_event:
            self.on_event(event_type)


# ############################################################################
# FIFO lifecycle management
# ############################################################################

def create_block_fifo(terminal_uuid):
    """Create a named pipe for shell integration events.
    Returns the FIFO path, or None on failure."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/tmp/guake-{os.getuid()}")
    fifo_dir = os.path.join(runtime_dir, "guake-blocks")

    try:
        os.makedirs(fifo_dir, mode=0o700, exist_ok=True)
    except OSError as e:
        log.error("Cannot create FIFO directory %s: %s", fifo_dir, e)
        return None

    fifo_path = os.path.join(fifo_dir, f"block-{terminal_uuid}")

    # Remove stale FIFO
    try:
        if os.path.exists(fifo_path):
            os.unlink(fifo_path)
    except OSError:
        pass

    try:
        os.mkfifo(fifo_path, mode=0o600)
        return fifo_path
    except OSError as e:
        log.error("Cannot create FIFO %s: %s", fifo_path, e)
        return None


# ############################################################################
# Block overlay — draws visual decorations on top of the terminal
# ############################################################################

class BlockOverlay(Gtk.DrawingArea):
    """Transparent overlay that draws block separators, exit badges,
    and duration labels over the terminal."""

    def __init__(self, terminal, block_model):
        super().__init__()
        self.terminal = terminal
        self.block_model = block_model

        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self.set_hexpand(True)
        self.set_vexpand(True)

        # Pure drawing layer — no input events, everything passes through
        # to the terminal underneath via set_overlay_pass_through(True)
        self.set_can_focus(False)
        self.set_sensitive(False)
        self.connect("draw", self._on_draw)

        # Redraw when terminal scrolls
        adj = self.terminal.get_vadjustment()
        adj.connect("value-changed", lambda a: self.queue_draw())

    def _get_char_metrics(self):
        """Get the terminal's character cell size in pixels."""
        try:
            alloc = self.terminal.get_allocation()
            cols = self.terminal.get_column_count()
            rows = self.terminal.get_row_count()
            if cols <= 0 or rows <= 0:
                return 8, 16
            char_w = alloc.width / cols
            char_h = alloc.height / rows
            return char_w, char_h
        except Exception:
            return 8, 16

    def _row_to_y(self, absolute_row):
        """Convert an absolute terminal row to a Y pixel coordinate
        relative to the visible area."""
        adj = self.terminal.get_vadjustment()
        visible_top = adj.get_value()
        _, char_h = self._get_char_metrics()
        return (absolute_row - visible_top) * char_h

    def _on_draw(self, widget, cr):
        """Draw block decorations."""
        if not self.block_model.blocks:
            return False

        alloc = widget.get_allocation()
        width = alloc.width
        height = alloc.height
        _, char_h = self._get_char_metrics()

        adj = self.terminal.get_vadjustment()
        visible_top = int(adj.get_value())
        visible_bottom = visible_top + int(adj.get_page_size())

        # Set up font for labels
        cr.select_font_face("Monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(10)

        for block in self.block_model.blocks:
            if not block.is_complete:
                # Draw running indicator for active block
                if block.is_running:
                    y = self._row_to_y(block.command_row)
                    if 0 <= y <= height:
                        elapsed = int(time.time() - block.timestamp)
                        self._draw_running_badge(cr, y, width, elapsed)
                continue

            # Skip blocks entirely outside visible area
            if block.end_row < visible_top or block.prompt_row > visible_bottom:
                continue

            # Separator line at block boundary
            sep_y = self._row_to_y(block.end_row)
            if 0 <= sep_y <= height:
                self._draw_separator(cr, sep_y, width)

            # Exit badge on the command row
            cmd_y = self._row_to_y(block.prompt_row)
            if 0 <= cmd_y <= height:
                self._draw_exit_badge(cr, cmd_y, char_h, block)
                self._draw_duration(cr, cmd_y, char_h, width, block)

            # Background tint for failed blocks
            if block.exit_code and block.exit_code != 0:
                self._draw_error_tint(cr, block, width, height, visible_top, visible_bottom)

        return False  # Allow event pass-through

    def _draw_separator(self, cr, y, width):
        """Draw a thin dotted separator line."""
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.set_line_width(1)
        cr.set_dash([3, 3])
        cr.move_to(0, y)
        cr.line_to(width, y)
        cr.stroke()
        cr.set_dash([])

    def _draw_exit_badge(self, cr, y, char_h, block):
        """Draw a success/failure badge in the left gutter."""
        badge_x = 3
        badge_y = y + char_h * 0.7

        if block.exit_code == 0:
            cr.set_source_rgba(0.15, 0.65, 0.4, 0.9)  # green
            text = "✓"
        else:
            cr.set_source_rgba(0.9, 0.3, 0.25, 0.9)   # red
            text = "✗"

        cr.move_to(badge_x, badge_y)
        cr.show_text(text)

    def _draw_duration(self, cr, y, char_h, width, block):
        """Draw the command duration label at the right edge."""
        dur_text = block.format_duration()
        if not dur_text:
            return

        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_font_size(9)
        extents = cr.text_extents(dur_text)
        cr.move_to(width - extents.width - 8, y + char_h * 0.7)
        cr.show_text(dur_text)
        cr.set_font_size(10)

    def _draw_running_badge(self, cr, y, width, elapsed):
        """Draw a pulsing indicator for a running command."""
        cr.set_source_rgba(0.3, 0.6, 1.0, 0.7)
        cr.move_to(3, y + 12)
        cr.show_text("●")

        if elapsed > 0:
            cr.set_source_rgba(1, 1, 1, 0.3)
            cr.set_font_size(9)
            time_text = f"⟳ {elapsed}s" if elapsed < 60 else f"⟳ {elapsed // 60}m"
            extents = cr.text_extents(time_text)
            cr.move_to(width - extents.width - 8, y + 12)
            cr.show_text(time_text)
            cr.set_font_size(10)

    def _draw_error_tint(self, cr, block, width, height, visible_top, visible_bottom):
        """Draw a very faint red background over failed block output."""
        if block.command_row is None or block.end_row is None:
            return
        y_start = max(self._row_to_y(block.command_row), 0)
        y_end = min(self._row_to_y(block.end_row), height)
        if y_end > y_start:
            cr.set_source_rgba(0.8, 0.15, 0.1, 0.06)
            cr.rectangle(0, y_start, width, y_end - y_start)
            cr.fill()

    def refresh(self):
        """Schedule a redraw."""
        self.queue_draw()
