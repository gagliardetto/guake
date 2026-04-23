# -*- coding: utf-8; -*-
"""
Inline terminal input editor for Guake.

A GtkSourceView widget docked at the bottom of the terminal that provides
Sublime-like editing for shell commands:
- Multi-cursor (Ctrl+D to match, Ctrl+Click to add)
- Syntax highlighting (shell)
- Multi-line (Shift+Enter for newlines)
- Auto-grows with content
- Feeds commands to the terminal on Enter

Activated when the shell emits a prompt_start event (input phase).
Deactivated when the user submits a command or the shell starts executing.
"""
import logging

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkSource", "4")
from gi.repository import Gtk, Gdk, GLib, Pango, GtkSource

log = logging.getLogger(__name__)

# Maximum visible lines before the editor starts scrolling
MAX_VISIBLE_LINES = 8
# Minimum height in pixels
MIN_HEIGHT = 28


class InlineEditor(Gtk.Revealer):
    """A GtkSourceView-based command input that docks at the bottom
    of the terminal, replacing readline for shell input."""

    def __init__(self, terminal, block_model):
        super().__init__()
        self.terminal = terminal
        self.block_model = block_model
        self._active = False
        self._history_index = -1
        self._history = []

        self.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.set_transition_duration(150)

        # -- Container --
        frame = Gtk.Frame()
        frame.get_style_context().add_class("inline-editor-frame")
        self.add(frame)

        container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        frame.add(container)

        # -- Prompt label --
        self.prompt_label = Gtk.Label(label=" $ ")
        self.prompt_label.set_valign(Gtk.Align.START)
        self.prompt_label.set_margin_top(4)
        self.prompt_label.get_style_context().add_class("inline-editor-prompt")
        container.pack_start(self.prompt_label, False, False, 0)

        # -- Source view --
        self.buffer = GtkSource.Buffer()
        self.buffer.set_max_undo_levels(0)  # We handle undo ourselves
        self.view = GtkSource.View.new_with_buffer(self.buffer)
        self.view.set_show_line_numbers(False)
        self.view.set_auto_indent(True)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_left_margin(2)
        self.view.set_right_margin(4)
        self.view.set_top_margin(4)
        self.view.set_bottom_margin(4)
        self.view.set_monospace(True)

        # Shell syntax highlighting
        lang_manager = GtkSource.LanguageManager.get_default()
        language = lang_manager.get_language('sh')
        if language:
            self.buffer.set_language(language)

        # Dark scheme
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        for scheme_name in ('oblivion', 'cobalt', 'classic-dark', 'classic'):
            scheme = scheme_manager.get_scheme(scheme_name)
            if scheme:
                self.buffer.set_style_scheme(scheme)
                break

        # Font matching the terminal
        font_desc = Pango.FontDescription("Monospace 11")
        self.view.override_font(font_desc)

        # Scrolled window for multi-line support
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_max_content_height(MAX_VISIBLE_LINES * 20)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.add(self.view)
        container.pack_start(self.scroll, True, True, 0)

        # -- Run button --
        run_btn = Gtk.Button(label="⏎")
        run_btn.set_valign(Gtk.Align.START)
        run_btn.set_margin_top(2)
        run_btn.set_relief(Gtk.ReliefStyle.NONE)
        run_btn.set_tooltip_text("Run command (Enter)")
        run_btn.connect("clicked", lambda w: self._submit())
        container.pack_end(run_btn, False, False, 2)

        # -- Signals --
        self.view.connect("key-press-event", self._on_key_press)
        self.buffer.connect("changed", self._on_buffer_changed)

        # -- CSS --
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .inline-editor-frame {
                background-color: rgba(40, 42, 46, 0.95);
                border-top: 1px solid rgba(255, 255, 255, 0.15);
                border-left: none;
                border-right: none;
                border-bottom: none;
                border-radius: 0;
                padding: 2px 4px;
            }
            .inline-editor-prompt {
                color: rgba(78, 154, 6, 0.9);
                font-family: Monospace;
                font-weight: bold;
                font-size: 11pt;
            }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # ---- Activation lifecycle ----

    def activate(self):
        """Show the editor (shell is waiting for input)."""
        if self._active:
            return
        self._active = True
        self.buffer.set_text("")
        self.set_reveal_child(True)
        # Delay focus grab slightly to avoid event ordering issues
        GLib.idle_add(self._grab_focus)

    def deactivate(self):
        """Hide the editor (command is executing or alt-screen)."""
        if not self._active:
            return
        self._active = False
        self.set_reveal_child(False)

    def _grab_focus(self):
        if self._active:
            self.view.grab_focus()
        return False

    @property
    def is_active(self):
        return self._active

    # ---- Command submission ----

    def _submit(self):
        """Feed the editor content to the terminal as a command."""
        text = self.buffer.get_text(
            self.buffer.get_start_iter(),
            self.buffer.get_end_iter(),
            False,
        ).strip()

        if not text:
            # Empty input — just send Enter so the shell shows a new prompt
            self.terminal.feed_child("\n")
            return

        # Save to local history
        if not self._history or self._history[-1] != text:
            self._history.append(text)
            if len(self._history) > 500:
                self._history = self._history[-400:]
        self._history_index = -1

        # Feed to terminal
        self.terminal.feed_child(text + "\n")

        # Clear editor and deactivate (command_start event will also deactivate)
        self.buffer.set_text("")
        self.deactivate()

    # ---- Key handling ----

    def _on_key_press(self, view, event):
        keyval = event.keyval
        state = event.state & Gtk.accelerator_get_default_mod_mask()

        # Enter (without Shift) → submit
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not (state & Gdk.ModifierType.SHIFT_MASK):
            self._submit()
            return True

        # Shift+Enter → newline (handled by default GtkSourceView behavior)
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and (state & Gdk.ModifierType.SHIFT_MASK):
            return False  # Let GtkSourceView insert a newline

        # Ctrl+V / Ctrl+Shift+V → paste from clipboard
        if keyval == Gdk.KEY_v and (state & Gdk.ModifierType.CONTROL_MASK):
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            text = clipboard.wait_for_text()
            if text:
                self.buffer.insert_at_cursor(text)
            return True

        # Ctrl+C → copy if selection exists, otherwise clear + SIGINT
        if keyval == Gdk.KEY_c and (state & Gdk.ModifierType.CONTROL_MASK):
            if self.buffer.get_has_selection():
                self.view.emit("copy-clipboard")
                return True
            self.buffer.set_text("")
            self.terminal.feed_child("\x03")
            return True

        # Ctrl+A → select all
        if keyval == Gdk.KEY_a and (state & Gdk.ModifierType.CONTROL_MASK):
            self.buffer.select_range(self.buffer.get_start_iter(), self.buffer.get_end_iter())
            return True

        # Ctrl+X → cut
        if keyval == Gdk.KEY_x and (state & Gdk.ModifierType.CONTROL_MASK):
            self.view.emit("cut-clipboard")
            return True

        # Tab → pass to terminal for shell completion
        if keyval == Gdk.KEY_Tab and not state:
            self._request_completion()
            return True

        # Ctrl+D on empty editor → send EOF
        if keyval == Gdk.KEY_d and (state & Gdk.ModifierType.CONTROL_MASK):
            text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)
            if not text:
                self.terminal.feed_child("\x04")
                self.deactivate()
                return True
            return False

        # Escape → clear editor, return focus to terminal
        if keyval == Gdk.KEY_Escape:
            self.buffer.set_text("")
            self.deactivate()
            self.terminal.grab_focus()
            return True

        # Up/Down on single-line → history navigation
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and self._is_single_line():
            self._navigate_history(keyval == Gdk.KEY_Up)
            return True

        # Ctrl+Z → terminal undo passthrough
        if keyval == Gdk.KEY_z and (state & Gdk.ModifierType.CONTROL_MASK):
            self.buffer.undo()
            return True

        return False  # Let GtkSourceView handle everything else

    def _is_single_line(self):
        return self.buffer.get_line_count() <= 1

    # ---- History ----

    def _navigate_history(self, go_up):
        if not self._history:
            return

        if go_up:
            if self._history_index == -1:
                # Save current input before navigating
                self._saved_input = self.buffer.get_text(
                    self.buffer.get_start_iter(), self.buffer.get_end_iter(), False
                )
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            else:
                return  # At oldest entry
        else:
            if self._history_index == -1:
                return  # Not in history mode
            elif self._history_index < len(self._history) - 1:
                self._history_index += 1
            else:
                # Back to saved input
                self._history_index = -1
                self.buffer.set_text(getattr(self, '_saved_input', ''))
                self.view.grab_focus()
                return

        self.buffer.set_text(self._history[self._history_index])
        # Place cursor at end
        self.buffer.place_cursor(self.buffer.get_end_iter())

    # ---- Tab completion ----

    def _request_completion(self):
        """Send the current partial input + Tab to the terminal for
        shell completion. The completion result will appear in the
        terminal output (we can't easily capture it back yet)."""
        text = self.buffer.get_text(
            self.buffer.get_start_iter(),
            self.buffer.get_end_iter(),
            False,
        )
        # Clear the terminal's current input line, type our text, and press Tab
        self.terminal.feed_child("\x15")  # Ctrl+U: kill line
        self.terminal.feed_child(text)
        self.terminal.feed_child("\t")

        # Deactivate editor and let the terminal handle the completion display
        self.deactivate()

    # ---- Auto-resize ----

    def _on_buffer_changed(self, buffer):
        """Auto-resize the editor height based on content."""
        line_count = buffer.get_line_count()
        visible_lines = min(line_count, MAX_VISIBLE_LINES)
        # Approximate line height
        target_height = max(MIN_HEIGHT, visible_lines * 20)
        self.scroll.set_min_content_height(target_height)

    # ---- Public API ----

    def set_text(self, text):
        """Set the editor content programmatically."""
        self.buffer.set_text(text or "")
        self.buffer.place_cursor(self.buffer.get_end_iter())

    def get_text(self):
        """Get the current editor content."""
        return self.buffer.get_text(
            self.buffer.get_start_iter(),
            self.buffer.get_end_iter(),
            False,
        )
