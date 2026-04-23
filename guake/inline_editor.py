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
import os

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkSource", "4")
from gi.repository import Gtk, Gdk, GLib, Pango, GtkSource

from guake.editor import Cursor, MarkTag, Casing

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
        self._user_dismissed = False
        self._history_index = -1
        self._history = []
        self._shell_history = []  # loaded from history file
        self._shell_history_loaded = False
        self._history_mtime = 0   # mtime of last loaded history file
        self._history_path = None # path to the history file
        self._ghost_text = None   # current ghost suggestion (suffix only)
        self._inhibit_ghost = False  # prevent re-entrant ghost updates

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

        # Ghost text tag for autocomplete suggestions (Fish-style)
        self._ghost_tag = self.buffer.create_tag(
            "ghost-completion",
            foreground="rgba(255,255,255,0.3)",
            editable=False,
        )

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
        self.view.connect("button-press-event", self._on_button_press)
        self.buffer.connect("changed", self._on_buffer_changed)

        # -- Multi-cursor state --
        self.cursors = []
        self.matches = []
        self.clipboard = ''
        self._handled_paste = False
        self._mc_handlers = []
        self._in_user_action = False
        self._is_modifying = False
        self._user_actions = []
        self.doc = self.buffer

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
        if self._active or self._user_dismissed:
            return
        # Reload history if the file has been modified since last load
        self._maybe_reload_history()
        self._active = True
        self.clear_cursors()
        self._clear_ghost()
        self.buffer.set_text("")
        self.set_reveal_child(True)
        GLib.idle_add(self._grab_focus)

    def deactivate(self):
        """Hide the editor (command is executing or alt-screen)."""
        if not self._active:
            return
        self._active = False
        self._clear_ghost()
        self.clear_cursors()
        self.set_reveal_child(False)
        # Return focus to terminal (guard against destroyed widget)
        try:
            if self.terminal and self.terminal.get_parent():
                self.terminal.grab_focus()
        except Exception:
            pass

    def dismiss(self):
        """User explicitly dismissed the editor (Escape).
        Won't reappear until next prompt_start resets the flag."""
        self._user_dismissed = True
        self.deactivate()

    def reset_dismissed(self):
        """Called on prompt_start to allow hover-trigger again."""
        self._user_dismissed = False

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
        # Get only user-typed text, not ghost suggestion
        text = self._get_user_text().strip()

        # Clear ghost before clearing buffer
        self._clear_ghost()

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
        if not self._active:
            return False  # Editor hidden — don't intercept anything
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
            self._clear_ghost()
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

        # Tab → accept ghost completion if present, else shell completion
        if keyval == Gdk.KEY_Tab and not state:
            if self._ghost_text:
                self._accept_ghost()
                return True
            self._request_completion()
            return True

        # Right arrow at end of line → accept ghost completion
        if keyval == Gdk.KEY_Right and not state:
            insert = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            user_end = self._get_user_text_end()
            if insert.compare(user_end) >= 0 and self._ghost_text:
                self._accept_ghost()
                return True

        # Ctrl+D → match selection and add cursor (EOF only on empty editor without selection)
        if keyval == Gdk.KEY_d and state == Gdk.ModifierType.CONTROL_MASK:
            text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)
            if not text:
                self.terminal.feed_child("\x04")
                self.deactivate()
                return True
            if self.buffer.get_has_selection():
                self.match_cursor()
                return True
            return False

        # Ctrl+Shift+D → fuzzy match cursor
        if keyval == Gdk.KEY_D and state == (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            if self.buffer.get_has_selection():
                self.match_cursor(fuzzy=True)
                return True
            return False

        # Ctrl+U → unmatch last cursor
        if keyval == Gdk.KEY_u and state == Gdk.ModifierType.CONTROL_MASK:
            if self.cursors:
                self.unmatch_cursor()
                return True
            return False

        # Ctrl+Shift+Up/Down → column select
        if keyval == Gdk.KEY_Up and state == (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            self.column_select(-1)
            return True
        if keyval == Gdk.KEY_Down and state == (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            self.column_select(1)
            return True

        # Escape → clear ghost first, then cursors, then dismiss
        if keyval == Gdk.KEY_Escape:
            if self._ghost_text:
                self._clear_ghost()
                return True
            if self.cursors:
                self.clear_cursors()
                return True
            self.buffer.set_text("")
            self.dismiss()
            return True

        # Up/Down on single-line → history navigation
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and self._is_single_line():
            self._clear_ghost()
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
        text = self._get_user_text()
        self._clear_ghost()
        # Clear the terminal's current input line, type our text, and press Tab
        self.terminal.feed_child("\x15")  # Ctrl+U: kill line
        self.terminal.feed_child(text)
        self.terminal.feed_child("\t")

        # Deactivate editor and let the terminal handle the completion display
        self.deactivate()

    # ---- Auto-resize ----

    def _on_buffer_changed(self, buffer):
        """Auto-resize the editor height and update ghost completion."""
        line_count = buffer.get_line_count()
        visible_lines = min(line_count, MAX_VISIBLE_LINES)
        target_height = max(MIN_HEIGHT, visible_lines * 20)
        self.scroll.set_min_content_height(target_height)

        # Update ghost text completion (skip if we're inserting ghost text)
        if not self._inhibit_ghost:
            GLib.idle_add(self._update_ghost)

    # ---- Shell history loading ----

    def _maybe_reload_history(self):
        """Reload history if the file has been modified since last load."""
        if not self._shell_history_loaded:
            self._load_shell_history()
            return
        if self._history_path:
            try:
                mtime = os.path.getmtime(self._history_path)
                if mtime > self._history_mtime:
                    self._load_shell_history()
            except OSError:
                pass

    def _load_shell_history(self):
        """Load command history from the shell's history file."""
        self._shell_history_loaded = True
        history_entries = []

        # Try multiple history files in order of preference
        home = os.path.expanduser("~")
        candidates = [
            (os.path.join(home, ".zsh_history"), "zsh"),
            (os.path.join(home, ".bash_history"), "bash"),
            (os.path.join(home, ".local", "share", "fish", "fish_history"), "fish"),
        ]

        for path, shell_type in candidates:
            if os.path.exists(path):
                try:
                    history_entries = self._parse_history_file(path, shell_type)
                    self._history_path = path
                    self._history_mtime = os.path.getmtime(path)
                    log.info("Loaded %d history entries from %s", len(history_entries), path)
                    break
                except Exception as e:
                    log.debug("Failed to read history file %s: %s", path, e)

        # Deduplicate preserving most-recent order (last occurrence wins)
        seen = {}
        for entry in history_entries:
            seen[entry] = True
        self._shell_history = list(seen.keys())

    def _parse_history_file(self, path, shell_type):
        """Parse a shell history file, handling shell-specific formats."""
        entries = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                if shell_type == "zsh":
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        # Zsh extended history: ": timestamp:0;command"
                        if line.startswith(": ") and ";" in line:
                            line = line.split(";", 1)[1]
                        entries.append(line)
                elif shell_type == "fish":
                    for line in f:
                        line = line.strip()
                        if line.startswith("- cmd: "):
                            entries.append(line[7:])
                else:
                    # Bash — one command per line
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(line)
        except Exception as e:
            log.debug("Error parsing history file %s: %s", path, e)
        return entries

    # ---- Ghost text completion (Fish-style) ----

    def _get_user_text(self):
        """Get only the user-typed text (excluding ghost suggestion)."""
        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        full = self.buffer.get_text(start, end, False)
        if self._ghost_text and full.endswith(self._ghost_text):
            return full[:-len(self._ghost_text)]
        return full

    def _get_user_text_end(self):
        """Get the iter position where user text ends (before ghost)."""
        if self._ghost_text:
            end = self.buffer.get_end_iter()
            end.backward_chars(len(self._ghost_text))
            return end
        return self.buffer.get_end_iter()

    def _clear_ghost(self):
        """Remove any ghost text from the buffer."""
        if self._ghost_text:
            self._inhibit_ghost = True
            user_end = self._get_user_text_end()
            buf_end = self.buffer.get_end_iter()
            self.buffer.delete(user_end, buf_end)
            self._ghost_text = None
            self._inhibit_ghost = False

    def _update_ghost(self):
        """Find the best matching history entry and show it as ghost text."""
        if not self._active:
            return False

        # Get user text without existing ghost
        user_text = self._get_user_text()

        # Clear old ghost first
        self._clear_ghost()

        # Only suggest if there's some input (at least 2 chars)
        if len(user_text) < 2:
            return False

        # Don't suggest during history navigation
        if self._history_index != -1:
            return False

        # Search recent history (reverse order — most recent first)
        suggestion = None
        # Check local session history first, then shell history
        for history in (reversed(self._history), reversed(self._shell_history)):
            for entry in history:
                if entry.startswith(user_text) and entry != user_text:
                    suggestion = entry
                    break
            if suggestion:
                break

        if suggestion:
            # Insert the suffix as ghost text
            suffix = suggestion[len(user_text):]
            self._inhibit_ghost = True
            end = self.buffer.get_end_iter()
            self.buffer.insert(end, suffix)
            # Apply ghost tag to the suffix
            ghost_start = self.buffer.get_end_iter()
            ghost_start.backward_chars(len(suffix))
            ghost_end = self.buffer.get_end_iter()
            self.buffer.apply_tag(self._ghost_tag, ghost_start, ghost_end)
            # Move cursor back to before the ghost text
            self.buffer.place_cursor(ghost_start)
            self._ghost_text = suffix
            self._inhibit_ghost = False

        return False  # one-shot idle

    def _accept_ghost(self):
        """Accept the ghost suggestion — make it real text."""
        if not self._ghost_text:
            return
        suffix = self._ghost_text
        self._ghost_text = None
        self._inhibit_ghost = True
        # Remove the ghost tag from the text (keep the text, remove styling)
        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        self.buffer.remove_tag(self._ghost_tag, start, end)
        # Place cursor at end
        self.buffer.place_cursor(self.buffer.get_end_iter())
        self._inhibit_ghost = False

    # ---- Public API ----

    def set_text(self, text):
        """Set the editor content programmatically."""
        self.buffer.set_text(text or "")
        self.buffer.place_cursor(self.buffer.get_end_iter())

    def get_text(self):
        """Get the current editor content (excluding ghost suggestion)."""
        return self._get_user_text()

    # ---- Ctrl+Click ----

    def _on_button_press(self, view, event):
        if not self._active:
            return False
        if event.type == Gdk.EventType.BUTTON_PRESS:
            state = event.state & Gtk.accelerator_get_default_mod_mask()
            if state & Gdk.ModifierType.CONTROL_MASK:
                x, y = self.view.window_to_buffer_coords(
                    Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
                found, pos = self.view.get_iter_at_location(x, y)
                if found:
                    self.add_cursor(pos, pos)
                return True
            else:
                self.clear_cursors()
        return False

    # ---- Multi-cursor infrastructure ----

    def order_iters(self, iters):
        if iters is None or iters[0] is None or iters[1] is None:
            return (None, None)
        return (iters[1], iters[0]) if iters[0].get_offset() > iters[1].get_offset() else iters

    def get_selection_iters(self):
        insert_iter = self.doc.get_iter_at_mark(self.doc.get_mark("insert"))
        sel_iter = self.doc.get_iter_at_mark(self.doc.get_mark("selection_bound"))
        return (insert_iter, sel_iter)

    def add_cursor(self, start_iter, end_iter):
        if not self.cursors:
            self._hook_mc_handlers()
        self.cursors.append(Cursor(self.view, start_iter, end_iter))

    def remove_cursor(self, index):
        if self.cursors:
            self.cursors[index].remove()
            del self.cursors[index]
            if not self.cursors:
                self._unhook_mc_handlers()

    def clear_cursors(self):
        while self.cursors:
            self.remove_cursor(-1)
        self.clear_matches()

    def clear_matches(self):
        for m in self.matches:
            m.remove()
        self.matches = []

    # ---- Match cursor (Ctrl+D) ----

    def match_cursor(self, fuzzy=False):
        sel_start, sel_end = self.order_iters(self.get_selection_iters())
        if not sel_start:
            return
        text = self.doc.get_text(sel_start, sel_end, True)
        if not text:
            return

        if self.cursors:
            search_start = self.cursors[-1].tag.get_end_iter()
        else:
            self.tag_all_matches(text, fuzzy)
            search_start = sel_end

        search_end = sel_start if search_start.get_offset() < sel_start.get_offset() else None
        match = self._find_next(text, search_start, search_end, fuzzy)

        if match is None and search_start.get_offset() >= sel_end.get_offset():
            match = self._find_next(text, self.doc.get_start_iter(), sel_start, fuzzy)

        if match:
            self.add_cursor(match[0], match[1])
            self.cursors[-1].scroll_onscreen()

    def tag_all_matches(self, text, fuzzy):
        sel_start, sel_end = self.order_iters(self.get_selection_iters())
        if not sel_start:
            return
        start_iter = self.doc.get_start_iter()
        while True:
            match = self._find_next(text, start_iter, None, fuzzy)
            if not match:
                break
            start_iter = match[1]
            if match[0].get_offset() == sel_start.get_offset():
                continue
            self.matches.append(MarkTag(self.view, 'multicursor_match', match[0], match[1]))

    def _find_next(self, text, search_start, search_end, fuzzy):
        if fuzzy:
            flags = Gtk.TextSearchFlags.CASE_INSENSITIVE
            casing = Casing().detect(text)
            words = casing.split(text)
            alternatives = {text, Casing('lower', '_').join(words),
                            Casing('lower', '-').join(words),
                            Casing('lower', '').join(words)}
            earliest = None
            for alt in alternatives:
                if not alt:
                    continue
                match = search_start.forward_search(alt, flags, search_end)
                if match and (earliest is None or match[0].get_offset() < earliest[0].get_offset()):
                    earliest = match
            return earliest
        else:
            return search_start.forward_search(text, 0, search_end)

    def unmatch_cursor(self):
        self.remove_cursor(-1)
        if self.cursors:
            self.cursors[-1].scroll_onscreen()

    # ---- Column select (Ctrl+Shift+Up/Down) ----

    def column_select(self, line_delta):
        sel_start, sel_end = self.order_iters(self.get_selection_iters())
        if not sel_start:
            return

        sel_line = sel_start.get_line()
        min_line = max_line = sel_line
        for cursor in self.cursors:
            line = cursor.tag.get_start_iter().get_line()
            min_line, max_line = min(line, min_line), max(line, max_line)

        start_line = None
        if line_delta < 0 and max_line == sel_line:
            start_line = min_line
        elif line_delta > 0 and min_line == sel_line:
            start_line = max_line

        if start_line is None:
            self.unmatch_cursor()
            return

        line = start_line + line_delta
        start_iter = sel_start.copy()
        start_iter.set_line(line)
        start_iter.set_line_offset(min(sel_start.get_line_offset(),
                                       max(start_iter.get_chars_in_line() - 1, 0)))
        end_iter = sel_end.copy()
        end_iter.set_line(line + (sel_end.get_line() - sel_start.get_line()))
        end_iter.set_line_offset(min(sel_end.get_line_offset(),
                                     max(end_iter.get_chars_in_line() - 1, 0)))

        if start_iter.get_line() != start_line:
            self.add_cursor(start_iter, end_iter)
            self.cursors[-1].scroll_onscreen()

    # ---- Multi-cursor document signal handlers ----

    def _hook_mc_handlers(self):
        h = []
        h.append((self.doc, self.doc.connect('insert-text', self._mc_on_insert)))
        h.append((self.doc, self.doc.connect('delete-range', self._mc_on_delete)))
        h.append((self.doc, self.doc.connect('begin-user-action', self._mc_begin)))
        h.append((self.doc, self.doc.connect('end-user-action', self._mc_end)))
        h.append((self.view, self.view.connect('move-cursor', self._mc_move)))
        self._mc_handlers = h

    def _unhook_mc_handlers(self):
        for obj, hid in self._mc_handlers:
            if obj.handler_is_connected(hid):
                obj.disconnect(hid)
        self._mc_handlers = []

    def _mc_begin(self, doc=None):
        self._user_actions = []
        self._in_user_action = True

    def _mc_on_insert(self, doc, start, text, length):
        if self._in_user_action and not self._is_modifying:
            sel_start, sel_end = self.order_iters(self.get_selection_iters())
            if not sel_start:
                return
            delta = start.get_offset() - sel_start.get_offset()
            self._user_actions.append(('insert', delta, text))

    def _mc_on_delete(self, doc, start, end):
        if self._in_user_action and not self._is_modifying:
            start, end = self.order_iters((start, end))
            if not start:
                return
            sel_start, sel_end = self.order_iters(self.get_selection_iters())
            if not sel_start:
                return
            sd = start.get_offset() - sel_start.get_offset()
            ed = end.get_offset() - sel_end.get_offset()
            self._user_actions.append(('delete', sd, ed))

    def _mc_end(self, doc=None):
        if not self._in_user_action:
            return
        self._in_user_action = False
        actions = self._user_actions[:]
        self._user_actions = []
        if not actions:
            return

        self.clear_matches()
        self._is_modifying = True
        sorted_cursors = sorted(self.cursors,
                                key=lambda c: c.tag.get_start_iter().get_offset(),
                                reverse=True)
        for action_type, *args in actions:
            if action_type == 'insert':
                delta, text = args
                for cursor in sorted_cursors:
                    cursor.insert(delta, text)
            elif action_type == 'delete':
                sd, ed = args
                for cursor in sorted_cursors:
                    cursor.delete(sd, ed)
        self._is_modifying = False

    def _mc_move(self, view, step_size, count, extend_selection):
        self.clear_matches()
        if step_size in (Gtk.MovementStep.BUFFER_ENDS, Gtk.MovementStep.PAGES):
            self.clear_cursors()
            return
        for cursor in self.cursors:
            cursor.move(step_size, count, extend_selection)
