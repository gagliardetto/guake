import logging
import os
import time

import gi

gi.require_version("Vte", "2.91")  # vte-0.42
gi.require_version("Gtk", "3.0")
from gi.repository import GObject
from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import Gtk, GLib, Pango
from gi.repository import Vte

from guake.callbacks import MenuHideCallback
from guake.callbacks import TerminalContextMenuCallbacks
from guake.dialogs import PromptResetColorsDialog
from guake.dialogs import RenameDialog
from guake.globals import PCRE2_MULTILINE
from guake.menus import mk_tab_context_menu
from guake.menus import mk_terminal_context_menu
from guake.utils import HidePrevention
from guake.utils import TabNameUtils
from guake.utils import get_server_time
from guake.utils import save_tabs_when_changed

log = logging.getLogger(__name__)

# TODO remove calls to guake


class TerminalHolder:
    UP = 0
    DOWN = 1
    RIGHT = 2
    LEFT = 3

    def get_terminals(self):
        raise NotImplementedError

    def iter_terminals(self):
        raise NotImplementedError

    def replace_child(self, old, new):
        raise NotImplementedError

    def get_guake(self):
        raise NotImplementedError

    def get_window(self):
        raise NotImplementedError

    def get_settings(self):
        raise NotImplementedError

    def get_root_box(self):
        raise NotImplementedError

    def get_notebook(self):
        raise NotImplementedError

    def remove_dead_child(self, child):
        raise NotImplementedError


def _find_ancestor(widget, method_name):
    """Walk up the widget tree to find the nearest TerminalHolder ancestor
    that has the given method. Skips plain GTK containers (like Gtk.Overlay)
    which may have identically-named methods (e.g. Gtk.Widget.get_settings,
    Gtk.Widget.get_window) that return the wrong type."""
    parent = widget.get_parent()
    while parent is not None:
        if isinstance(parent, TerminalHolder) and hasattr(parent, method_name):
            return parent
        parent = parent.get_parent()
    return None


class TerminalHolderChild:
    """Mixin for widgets that live inside a TerminalHolder hierarchy
    (TerminalBox, DualTerminalBox). Provides parent-traversal methods
    that safely skip intermediate GTK containers.

    NOTE: get_window and get_settings conflict with Gtk.Widget methods
    of the same name. Since Gtk.Box/Gtk.Paned come first in the MRO,
    the Gtk.Widget versions would shadow these. Subclasses MUST
    explicitly define get_window and get_settings to override Gtk.Widget.
    """

    def get_guake(self):
        a = _find_ancestor(self, 'get_guake')
        return a.get_guake() if a else None

    def get_root_box(self):
        a = _find_ancestor(self, 'get_root_box')
        return a.get_root_box() if a else None

    def get_notebook(self):
        a = _find_ancestor(self, 'get_notebook')
        return a.get_notebook() if a else None

    def _get_holder_window(self):
        """Get the Guake application window (not Gdk.Window)."""
        a = _find_ancestor(self, 'get_window')
        return a.get_window() if a else None

    def _get_holder_settings(self):
        """Get Guake Settings (not Gtk.Settings)."""
        a = _find_ancestor(self, 'get_settings')
        return a.get_settings() if a else None

import cairo
import random
import string
from copy import deepcopy

class RootTerminalBox(Gtk.Box, TerminalHolder):
    def __init__(self, guake, parent_notebook):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.guake = guake
        self.notebook = parent_notebook
        self.child = None
        self.last_terminal_focused = None

        # Internal overlay for terminal + search bar + block decorations
        self._overlay = Gtk.Overlay()
        self._overlay.set_hexpand(True)
        self._overlay.set_vexpand(True)
        self.pack_start(self._overlay, True, True, 0)
        self._overlay.show()

        # Block support (initialized lazily on first terminal focus)
        self.block_model = None
        self.block_overlay = None
        self.inline_editor = None
        self._block_fifo_reader = None
        self._blocks_initialized = False

        self.searchstring = None
        self.searchre = None
        self._add_search_box()

    def add_overlay(self, widget):
        """Delegate to the internal overlay."""
        self._overlay.add_overlay(widget)

    def set_overlay_pass_through(self, widget, pass_through):
        """Delegate to the internal overlay."""
        self._overlay.set_overlay_pass_through(widget, pass_through)

    def _add_search_box(self):
        """--------------------------------------|
        | Revealer                            |
        | |-----------------------------------|
        | | Frame                             |
        | | |---------------------------------|
        | | | HBox                            |
        | | | |---| |-------| |----| |------| |
        | | | | x | | Entry | |Prev| | Next | |
        | | | |---| |-------| |----| |------| |
        --------------------------------------|
        """
        self.search_revealer = Gtk.Revealer()
        self.search_frame = Gtk.Frame(name="search-frame")
        self.search_box = Gtk.HBox()

        # Search
        self.search_close_btn = Gtk.Button()
        self.search_close_btn.set_can_focus(False)
        close_icon = Gio.ThemedIcon(name="window-close-symbolic")
        close_image = Gtk.Image.new_from_gicon(close_icon, Gtk.IconSize.BUTTON)
        self.search_close_btn.set_image(close_image)
        self.search_entry = Gtk.SearchEntry()
        self.search_prev_btn = Gtk.Button()
        self.search_prev_btn.set_can_focus(False)
        prev_icon = Gio.ThemedIcon(name="go-up-symbolic")
        prev_image = Gtk.Image.new_from_gicon(prev_icon, Gtk.IconSize.BUTTON)
        self.search_prev_btn.set_image(prev_image)
        self.search_next_btn = Gtk.Button()
        self.search_next_btn.set_can_focus(False)
        next_icon = Gio.ThemedIcon(name="go-down-symbolic")
        next_image = Gtk.Image.new_from_gicon(next_icon, Gtk.IconSize.BUTTON)
        self.search_next_btn.set_image(next_image)

        # Pack into box
        self.search_box.pack_start(self.search_prev_btn, False, False, 0)
        self.search_box.pack_start(self.search_next_btn, False, False, 0)
        self.search_box.pack_start(self.search_entry, False, False, 0)
        self.search_box.pack_start(self.search_close_btn, False, False, 0)

        # Add into frame
        self.search_frame.add(self.search_box)

        # Frame
        self.search_frame.set_margin_end(12)
        self.search_frame.get_style_context().add_class("background")
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"#search-frame border {" b"    padding: 5px 5px 5px 5px;" b"    border: none;" b"}"
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Add to revealer
        self.search_revealer.add(self.search_frame)
        self.search_revealer.set_transition_duration(500)
        self.search_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.search_revealer.set_valign(Gtk.Align.START)
        self.search_revealer.set_halign(Gtk.Align.END)

        # Welcome to the overlay
        self.add_overlay(self.search_revealer)

        # Events
        self.search_entry.connect("key-press-event", self.on_search_entry_keypress)
        self.search_entry.connect("changed", self.set_search)
        self.search_entry.connect("activate", self.do_search)
        self.search_entry.connect("focus-in-event", self.on_search_entry_focus_in)
        self.search_entry.connect("focus-out-event", self.on_search_entry_focus_out)
        self.search_next_btn.connect("clicked", self.on_search_next_clicked)
        self.search_prev_btn.connect("clicked", self.on_search_prev_clicked)
        self.search_close_btn.connect("clicked", self.close_search_box)
        self.search_prev = True

        # Search revealer visible
        def search_revealer_show_cb(widget):
            if not widget.get_child_revealed():
                widget.hide()

        self.search_revealer.hide()
        self.search_revealer_show_cb_id = self.search_revealer.connect(
            "show", search_revealer_show_cb
        )

    def get_terminals(self):
        return self.get_child().get_terminals()

    def iter_terminals(self):
        if self.get_child() is not None:
            for t in self.get_child().iter_terminals():
                yield t

    def replace_child(self, old, new):
        self._overlay.remove(old)
        self.set_child(new)

    def set_child(self, terminal_holder):
        if isinstance(terminal_holder, TerminalHolder):
            self.child = terminal_holder
            self._overlay.add(self.child)
        else:
            raise RuntimeError(f"Error adding (RootTerminalBox.add({type(terminal_holder)}))")

    def get_child(self):
        return self.child

    def get_guake(self):
        return self.guake

    def get_window(self):
        return self.guake.window

    def get_settings(self):
        return self.guake.settings

    def get_root_box(self):
        return self

    def save_box_layout(self, box, panes: list):
        """Save box layout with pre-order traversal, it should result `panes` with
        a full binary tree in list.
        """
        if not box:
            panes.append({"type": None, "directory": None})
            return
        if isinstance(box, DualTerminalBox):
            btype = "dual" + ("_h" if box.orient is DualTerminalBox.ORIENT_V else "_v")
            panes.append({"type": btype, "directory": None})
            self.save_box_layout(box.get_child1(), panes)
            self.save_box_layout(box.get_child2(), panes)
        elif isinstance(box, TerminalBox):
            btype = "term"
            directory = box.terminal.get_current_directory()
            panes.append(
                {
                    "type": btype,
                    "directory": directory,
                    "custom_colors": box.terminal.get_custom_colors_dict(),
                    "uuid": str(box.terminal.uuid),
                }
            )

    def restore_box_layout(self, box, panes: list):
        """Restore box layout by `panes`"""
        if not panes or not isinstance(panes, list):
            return
        if not box or not isinstance(box, TerminalBox):
            # Should only called on TerminalBox
            return

        cur = panes.pop(0)
        if cur["type"].startswith("dual"):
            while True:
                if self.guake:
                    # If Guake are not visible, we should pending the restore, then do the
                    # restore when Guake is visible again.
                    #
                    # Otherwise we will stuck in the infinite loop, since new DualTerminalBox
                    # cannot get any allocation when Guake is invisible
                    if (
                        not self.guake.window.get_property("visible")
                        or self.get_notebook()
                        is not self.guake.notebook_manager.get_current_notebook()
                    ):
                        panes.insert(0, cur)
                        self.guake._failed_restore_page_split.append((self, box, panes))
                        return

                # UI didn't update, wait for it
                alloc = box.get_allocation()
                if alloc.width == 1 and alloc.height == 1:
                    time.sleep(0.01)
                else:
                    break

                # Waiting for UI update..
                while Gtk.events_pending():
                    Gtk.main_iteration()

            if cur["type"].endswith("v"):
                box = box.split_v_no_save()
            else:
                box = box.split_h_no_save()
            self.restore_box_layout(box.get_child1(), panes)
            self.restore_box_layout(box.get_child2(), panes)
        else:
            if box.terminal:
                term = box.terminal
                # Remove signal handler from terminal
                for i in term.handler_ids:
                    term.disconnect(i)
                term.handler_ids = []
                box.remove(box.scroll)
                box.remove(term)
                box.unset_terminal()

            # Replace term in the TerminalBox
            term = self.get_notebook().terminal_spawn(cur["directory"], terminal_uuid=cur.get("uuid"))
            term.set_custom_colors_from_dict(cur.get("custom_colors", None))
            box.set_terminal(term)
            self.get_notebook().terminal_attached(term)

    def set_last_terminal_focused(self, terminal):
        self.last_terminal_focused = terminal
        self.get_notebook().set_last_terminal_focused(terminal)
        if not self._blocks_initialized and terminal:
            self._setup_blocks(terminal)

    def ensure_blocks_initialized(self):
        """Eagerly initialize block support if a terminal is available.
        Called after the page is fully set up and visible."""
        if self._blocks_initialized:
            return
        terminals = list(self.iter_terminals())
        if terminals:
            log.info("Eagerly initializing blocks for terminal %s", terminals[0].uuid)
            self._setup_blocks(terminals[0])
        else:
            log.debug("ensure_blocks_initialized: no terminals found yet")

    def _setup_blocks(self, terminal):
        """Initialize block model, overlay, and inline editor for this terminal.
        Called once when the first terminal gets focus."""
        self._blocks_initialized = True
        self._input_phase = False   # True when shell is at a prompt

        # Load inline editor config
        self._editor_mode = "hover"  # default: "hover", "always", "disabled"
        try:
            config_path = self.guake.get_xdg_config_directory() / "inline_editor.json"
            if config_path.exists():
                import json
                cfg = json.loads(config_path.read_text())
                self._editor_mode = cfg.get("mode", "hover")
        except Exception as e:
            log.debug("Could not load inline editor config: %s", e)

        try:
            from guake.blocks import BlockModel, BlockOverlay, BlockFIFOReader
            from guake.inline_editor import InlineEditor

            self.block_model = BlockModel(terminal)
            self._blocks_terminal_uuid = str(terminal.uuid)
            log.info("BlockModel created for terminal %s", terminal.uuid)

            # Block overlay — draws directly on the terminal's draw signal
            self.block_overlay = BlockOverlay(terminal, self.block_model)

            # Inline editor (docked at bottom, hidden by default)
            if self._editor_mode != "disabled":
                self.inline_editor = InlineEditor(terminal, self.block_model)
                self.pack_end(self.inline_editor, False, False, 0)
                self.inline_editor.show_all()
                log.info("InlineEditor created (mode=%s)", self._editor_mode)

            # Bottom-edge hover detection for "hover" mode
            if self._editor_mode == "hover":
                terminal.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
                terminal.connect("motion-notify-event", self._on_terminal_motion)
                self._editor_hide_timer = None

            # FIFO reader for shell integration events
            fifo_path = getattr(terminal, 'block_fifo_path', None)
            if fifo_path:
                self._block_fifo_reader = BlockFIFOReader(
                    fifo_path, self.block_model, self._on_block_event
                )
                self._block_fifo_reader.start()
                log.info("FIFO reader started: %s", fifo_path)

                # Self-test: write a test event to verify the pipeline works
                def _fifo_self_test():
                    try:
                        # Write directly to the existing fd (O_RDWR)
                        reader = self._block_fifo_reader
                        if reader and reader._fd is not None:
                            os.write(reader._fd, b'{"event":"prompt_start"}\n')
                            log.info("FIFO self-test: wrote test event to fd %d", reader._fd)
                        else:
                            log.warning("FIFO self-test: no fd available")
                    except Exception as e:
                        log.warning("FIFO self-test failed: %s", e)
                    return False
                GLib.timeout_add(800, _fifo_self_test)
            else:
                log.warning("No block_fifo_path on terminal %s — shell integration won't work", terminal.uuid)

            # Wire terminal output to regex watchers
            self._last_cursor_row = 0
            terminal.connect("contents-changed", self._on_terminal_contents_changed)

            log.info("Block support initialized for terminal %s (mode=%s)", terminal.uuid, self._editor_mode)
        except Exception as e:
            log.warning("Could not initialize block support: %s", e, exc_info=True)

    def _on_terminal_contents_changed(self, terminal):
        """Forward new terminal output to regex watchers."""
        guake = self.get_guake()
        if not guake or not hasattr(guake, 'notification_center'):
            return
        wm = guake.notification_center.watcher_manager
        # Quick check: any regex watchers for this terminal?
        tuuid = self._blocks_terminal_uuid
        if not any(hasattr(w, 'compiled') and w.terminal_uuid == tuuid
                   for w in wm.watchers):
            return

        # Get cursor position and read new lines
        try:
            col, row = terminal.get_cursor_position()
            if row <= self._last_cursor_row:
                self._last_cursor_row = row
                return
            # Read text from last known row to current row
            text = terminal.get_text_range(
                self._last_cursor_row, 0, row, -1, None)[0]
            self._last_cursor_row = row
            if text and text.strip():
                nb = self.get_notebook()
                tab_label = nb.get_tab_label(self) if nb else None
                tab_title = tab_label.get_text() if tab_label and hasattr(tab_label, 'get_text') else ""
                wm.on_terminal_output(tuuid, text.strip(), tab_title)
        except Exception:
            pass  # VTE API can throw during rapid updates

    def _on_terminal_motion(self, terminal, event):
        """Detect mouse at bottom edge of terminal to reveal inline editor."""
        if not self._input_phase or not self.inline_editor:
            return False

        alloc = terminal.get_allocation()
        edge_height = 8  # pixels from bottom edge

        if event.y >= alloc.height - edge_height:
            # Mouse at bottom edge — show editor
            if self._editor_hide_timer:
                GLib.source_remove(self._editor_hide_timer)
                self._editor_hide_timer = None
            if not self.inline_editor.is_active:
                self.inline_editor.activate()
        else:
            # Mouse away from bottom — reset dismissed flag so next hover works
            self.inline_editor.reset_dismissed()
            # Schedule hide (unless editor has focus)
            if self.inline_editor.is_active and not self.inline_editor.view.has_focus():
                if not self._editor_hide_timer:
                    self._editor_hide_timer = GLib.timeout_add(
                        400, self._hide_editor_timeout)
        return False

    def _hide_editor_timeout(self):
        self._editor_hide_timer = None
        if self.inline_editor and self.inline_editor.is_active:
            if not self.inline_editor.view.has_focus():
                self.inline_editor.deactivate()
        return False

    def _on_block_event(self, event_type):
        """Handle block events from the shell integration FIFO."""
        log.info("Block event: %s (terminal %s, block cmd=%s)",
                 event_type,
                 getattr(self, '_blocks_terminal_uuid', '?'),
                 getattr(self.block_model._current_block, 'command', None) if self.block_model._current_block else None)
        # Update tab label with command status
        self._update_tab_command_status(event_type)

        if event_type == "prompt_start":
            self._input_phase = True
            if self.inline_editor:
                self.inline_editor.reset_dismissed()
                if self._editor_mode == "always":
                    self.inline_editor.activate()
            if self.block_overlay:
                self.block_overlay.refresh()

        elif event_type == "command_start":
            self._input_phase = False
            if self.inline_editor:
                self.inline_editor.deactivate()
            if self.block_overlay:
                self.block_overlay.refresh()

        elif event_type == "command_end":
            if self.block_overlay:
                self.block_overlay.refresh()

    def _update_tab_command_status(self, event_type):
        """Push block event info to the tab label and watcher manager."""
        notebook = self.get_notebook()
        if not notebook or not self.block_model:
            return
        page_num = notebook.page_num(self)
        if page_num < 0:
            return
        tab_label = notebook.get_tab_label(self)

        current = self.block_model._current_block

        # Update tab label
        has_method = tab_label and hasattr(tab_label, 'set_running_command')
        if event_type in ("command_start", "command_end"):
            log.info("Tab update: event=%s page=%d has_method=%s cmd=%s label_type=%s",
                     event_type, page_num, has_method,
                     current.command if current else None,
                     type(tab_label).__name__)

        # Update tab label
        if tab_label and hasattr(tab_label, 'set_running_command'):
            if event_type == "command_start" and current:
                tab_label.set_running_command(current.command or "")
            elif event_type == "command_end" and current:
                tab_label.set_command_result(
                    current.command or "",
                    current.exit_code if current.exit_code is not None else 0)

        # Notify watcher manager
        if event_type == "command_end" and current:
            guake = self.get_guake()
            if guake and hasattr(guake, 'notification_center'):
                terminal = list(self.iter_terminals())
                if terminal:
                    tab_title = tab_label.get_text() if tab_label and hasattr(tab_label, 'get_text') else ""
                    guake.notification_center.watcher_manager.on_command_end(
                        str(terminal[0].uuid),
                        current.command or "",
                        current.exit_code if current.exit_code is not None else 0,
                        tab_title,
                    )

    def _cleanup_blocks(self):
        """Clean up block support resources."""
        if self._block_fifo_reader:
            self._block_fifo_reader.stop()
            self._block_fifo_reader = None
        if self.block_overlay:
            self.block_overlay.cleanup()
            self.block_overlay = None
        if self.inline_editor:
            self.inline_editor.deactivate()
            self.inline_editor = None

    def get_last_terminal_focused(self, terminal):
        return self.last_terminal_focused

    def get_notebook(self):
        return self.notebook

    def remove_dead_child(self, child):
        self._cleanup_blocks()
        page_num = self.get_notebook().page_num(self)
        self.get_notebook().remove_page(page_num)

    def block_notebook_on_button_press_id(self):
        GObject.signal_handler_block(
            self.get_notebook(), self.get_notebook().notebook_on_button_press_id
        )

    def unblock_notebook_on_button_press_id(self):
        GObject.signal_handler_unblock(
            self.get_notebook(), self.get_notebook().notebook_on_button_press_id
        )

    def show_search_box(self):
        if not self.is_search_box_visible():
            GObject.signal_handler_block(self.search_revealer, self.search_revealer_show_cb_id)
            self.search_revealer.set_visible(True)
            self.search_revealer.set_reveal_child(True)
            GObject.signal_handler_unblock(self.search_revealer, self.search_revealer_show_cb_id)
            # XXX: Mestery line to avoid Gtk-CRITICAL stuff
            # (guake:22694): Gtk-CRITICAL **: 18:04:57.345:
            # gtk_widget_event: assertion 'WIDGET_REALIZED_FOR_EVENT (widget, event)' failed
            self.search_entry.realize()

            self.search_entry.select_region(0, -1)
            self.search_entry.set_position(-1)
            self.search_entry.set_text(self.searchstring or "")
            self.search_entry.set_icon_from_icon_name(
                Gtk.EntryIconPosition.PRIMARY, "edit-find-symbolic"
            )
            self.search_entry.set_icon_from_icon_name(
                Gtk.EntryIconPosition.SECONDARY, "edit-clear-symbolic"
            )
            self.search_entry.set_icon_activatable(
                Gtk.EntryIconPosition.SECONDARY, True
            )
            self.search_entry.set_icon_sensitive(
                Gtk.EntryIconPosition.SECONDARY, True
            )
            self.search_entry.set_icon_tooltip_text(
                Gtk.EntryIconPosition.SECONDARY, "Clear search"
            )
            self.search_entry.set_icon_tooltip_text(
                Gtk.EntryIconPosition.PRIMARY, "Search"
            )
            self.search_entry.grab_focus()

    def hide_search_box(self):
        if self.is_search_box_visible():
            self.search_revealer.set_reveal_child(False)
            self.last_terminal_focused.grab_focus()
            self.last_terminal_focused.unselect_all()
            # if the last search wasn't a valid regex, clear the search box
            # if self.searchstring != "" and self.searchre is None:
            #     self.searchstring = ""
            #     self.search_entry.set_text("")
            #     self.search_entry.set_icon_from_icon_name(
            #         Gtk.EntryIconPosition.PRIMARY, "edit-find-symbolic"
            #     )
            #     self.search_entry.get_style_context().remove_class("error")
    def is_search_box_visible(self):
        # NOTE: the the revelaer might not be visible, but the child is revealed
        #       so we need to check both
        return self.search_revealer.get_child_revealed() or self.search_revealer.get_reveal_child()

    def close_search_box(self, event):
        self.hide_search_box()

    def on_search_entry_focus_in(self, event, user_data):
        self.block_notebook_on_button_press_id()

    def on_search_entry_focus_out(self, event, user_data):
        self.unblock_notebook_on_button_press_id()

    def on_search_prev_clicked(self, widget):
        term = self.last_terminal_focused
        result = term.search_find_previous()
        if not result:
            term.search_find_previous()

    def on_search_next_clicked(self, widget):
        term = self.last_terminal_focused
        result = term.search_find_next()
        if not result:
            term.search_find_next()

    def on_search_entry_keypress(self, widget, event):
        key = Gdk.keyval_name(event.keyval)
        if key == "Escape":
            self.hide_search_box()
        elif key == "Return":
            # Combine with Shift?
            if event.state & Gdk.ModifierType.SHIFT_MASK:
                self.search_prev = False
                self.do_search(None)
            else:
                self.search_prev = True

    def reset_term_search(self, term):
        term.search_set_regex(None, 0)
        term.search_find_next()

    def set_search(self, widget):
        term = self.last_terminal_focused
        text = self.search_entry.get_text()
        if not text:
            self.reset_term_search(term)
            return

        if text != self.searchstring:
            self.reset_term_search(term)

            # Set search regex on term
            self.searchstring = text
            
            # try to compile the regex; if fail, highlight the search entry in red
            try:
                self.searchre = Vte.Regex.new_for_search(
                    text, -1, Vte.REGEX_FLAGS_DEFAULT | PCRE2_MULTILINE
                )
                self.search_entry.get_style_context().remove_class("error")
                # change search icon to search icon
                self.search_entry.set_icon_from_icon_name(
                    Gtk.EntryIconPosition.PRIMARY, "edit-find-symbolic"
                )
            except GLib.Error:
                self.searchre = None
                self.search_entry.get_style_context().add_class("error")
                # change search icon to error icon
                self.search_entry.set_icon_from_icon_name(
                    Gtk.EntryIconPosition.PRIMARY, "dialog-error-symbolic"
                )
            term.search_set_regex(self.searchre, 0)
        self.do_search(None)

    def do_search(self, widget):
        if self.search_prev:
            self.on_search_prev_clicked(None)
        else:
            self.on_search_next_clicked(None)


class TerminalBox(Gtk.Box, TerminalHolderChild, TerminalHolder):

    """A box to group the terminal and a scrollbar."""

    MINIMAP_REFRESH_MS = 250  # max refresh rate for minimap content
    MINIMAP_ROW_HEIGHT = 2

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.terminal = None
        self._minimap_refresh_timer_id = None
        self._minimap_line_lengths = None   # list of int — length of each line
        self._minimap_content_surface = None  # cached Cairo surface for content layer
        self._minimap_col_count = 80        # terminal column count at last refresh

    def set_terminal(self, terminal):
        """Packs the terminal widget."""
        if self.terminal is not None:
            raise RuntimeError("TerminalBox: terminal already set")
        self.terminal = terminal
        self.terminal.handler_ids.append(
            self.terminal.connect("grab-focus", self.on_terminal_focus)
        )
        self.terminal.handler_ids.append(
            self.terminal.connect("button-press-event", self.on_button_press, None)
        )
        self.terminal.handler_ids.append(
            self.terminal.connect("child-exited", self.on_terminal_exited)
        )
        self.pack_start(self.terminal, True, True, 0)
        self.terminal.show()
        self.add_scroll_bar()

        self.minimap.connect("draw", self.on_draw_minimap)

    def add_scroll_bar(self):
        """Packs the scrollbar."""
        adj = self.terminal.get_vadjustment()
        self.scroll = Gtk.Scrollbar.new(Gtk.Orientation.VERTICAL, adj)
        self.scroll.show()

        # Minimap setup
        self.minimap = Gtk.DrawingArea()
        self.minimap.set_size_request(60, 100)
        self.minimap.add_events(Gdk.EventMask.SCROLL_MASK)
        self.minimap.set_sensitive(True)
        self.minimap.connect("scroll-event", self.on_scroll_minimap)
        self.minimap.show()

        container = Gtk.HBox()  # Container to hold both scrollbar and minimap
        container.pack_start(self.minimap, False, False, 0)
        container.pack_start(self.scroll, False, False, 0)
        container.show()

        self.pack_start(container, False, False, 0)  # Pack container instead of just the scrollbar

        self.terminal.handler_ids.append(
            self.terminal.connect("scroll-event", self.__scroll_event_cb),
        )
        self.terminal.handler_ids.append(
            self.terminal.connect("contents-changed", self.on_terminal_content_changed, self.minimap)
        )
    
    def on_scroll_minimap(self, widget, event):
        adj = self.terminal.get_vadjustment()
        value = adj.get_value()
        step = adj.get_step_increment()

        if event.direction == Gdk.ScrollDirection.UP:
            value -= step
        elif event.direction == Gdk.ScrollDirection.DOWN:
            value += step
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            has_deltas, dx, dy = event.get_scroll_deltas()
            if has_deltas:
                value += dy * step

        # Ensure the value falls within the valid range
        adj.set_value(min(max(value, adj.get_lower()), adj.get_upper() - adj.get_page_size()))
        
        return True  # Stop event propagation


    def on_draw_minimap(self, widget, cr):
        m_width = widget.get_allocated_width()
        m_height = widget.get_allocated_height()

        # Draw cached content surface (only regenerated on content change)
        if self._minimap_content_surface:
            cr.set_source_surface(self._minimap_content_surface, 0, 0)
            cr.paint()

        # Draw the scrolling viewfinder (cheap — just a rectangle)
        self._draw_viewfinder(cr, m_width, m_height)

    def _rebuild_content_surface(self):
        """Rebuild the cached minimap content surface from line-length data.

        Shows ALL terminal lines compressed into the minimap height (like VS Code).
        Each pixel row may represent multiple terminal lines — the bar shows the
        max line length in that group. Only rebuilt on content change, NOT on scroll.
        """
        if not self._minimap_line_lengths or not self.minimap:
            self._minimap_content_surface = None
            return

        m_width = self.minimap.get_allocated_width()
        m_height = self.minimap.get_allocated_height()
        if m_width <= 0 or m_height <= 0:
            return

        rh = self.MINIMAP_ROW_HEIGHT
        visible_rows = max(int(m_height / rh), 1)
        col_count = max(self._minimap_col_count, 1)
        total_lines = len(self._minimap_line_lengths)

        # Create an offscreen surface
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, m_width, m_height)
        scr = cairo.Context(surface)

        # How many terminal lines does each minimap pixel-row represent?
        lines_per_row = max(total_lines / visible_rows, 1.0)

        for row in range(min(visible_rows, total_lines)):
            # Determine which terminal lines map to this minimap row
            src_start = int(row * lines_per_row)
            src_end = min(int((row + 1) * lines_per_row), total_lines)
            if src_start >= total_lines:
                break

            group = self._minimap_line_lengths[src_start:src_end]
            if not group:
                continue

            max_len = max(group)
            if max_len == 0:
                continue

            # Bar width proportional to max line length in group
            bar_w = max(1, int((max_len / col_count) * m_width))
            y = row * rh

            # Brightness reflects density (how many non-empty lines in group)
            non_empty = sum(1 for l in group if l > 0)
            density = non_empty / len(group)
            intensity = 0.2 + 0.6 * density
            scr.set_source_rgba(0.1, intensity, 0.2, 0.85)
            scr.rectangle(0, y, bar_w, max(rh - 1, 1))
            scr.fill()

        self._minimap_content_surface = surface

    def _draw_viewfinder(self, cr, width, height):
        adj = self.terminal.get_vadjustment()
        first_visible_row = adj.get_value()
        page_size = adj.get_page_size()

        # Use the same line count as the content surface for consistent mapping
        total_lines = len(self._minimap_line_lengths) if self._minimap_line_lengths else 0
        if total_lines <= 0:
            return

        rh = self.MINIMAP_ROW_HEIGHT
        visible_rows = max(int(height / rh), 1)

        # Content occupies this many pixels (may be less than full height)
        content_pixel_height = min(total_lines, visible_rows) * rh

        # Map terminal scroll position to content pixel space
        scale = content_pixel_height / total_lines
        vf_top = first_visible_row * scale
        vf_height = max(page_size * scale, 4)

        cr.set_source_rgba(1, 1, 1, 0.25)
        cr.rectangle(0, vf_top, width, vf_height)
        cr.fill()

    def get_minimap_row_height(self):
        return self.MINIMAP_ROW_HEIGHT

    def on_terminal_content_changed(self, terminal, minimap):
        """Debounced handler: schedule a minimap refresh instead of reading
        the entire terminal buffer on every character of output."""
        # Skip refresh entirely for hidden terminals (other workspaces)
        root_box = self.get_root_box()
        if root_box and not root_box.get_visible():
            return
        if self._minimap_refresh_timer_id is None:
            self._minimap_refresh_timer_id = GLib.timeout_add(
                self.MINIMAP_REFRESH_MS, self._do_minimap_refresh
            )

    def _do_minimap_refresh(self):
        """Read terminal content and extract line lengths for the minimap.
        Runs at most once per MINIMAP_REFRESH_MS."""
        self._minimap_refresh_timer_id = None
        if not self.terminal:
            return False

        # Skip if page was hidden between schedule and execution
        root_box = self.get_root_box()
        if root_box and not root_box.get_visible():
            return False

        try:
            output_stream = Gio.MemoryOutputStream.new_resizable()
            self.terminal.write_contents_sync(output_stream, Vte.WriteFlags.DEFAULT, None)
            output_stream.close()
            written_data = output_stream.steal_as_bytes()
            raw_content = written_data.get_data().decode('utf-8', errors='replace')
        except Exception as e:
            log.debug("Minimap content read failed: %s", e)
            return False

        # Extract only line lengths (compact int list — no text stored)
        t_width = self.terminal.get_column_count()
        self._minimap_col_count = t_width
        lengths = []
        for line in raw_content.split('\n'):
            stripped = line.rstrip('\x00')
            if len(stripped) > t_width:
                # Account for wrapped lines
                for i in range(0, len(stripped), t_width):
                    lengths.append(min(len(stripped) - i, t_width))
            else:
                lengths.append(len(stripped))
        self._minimap_line_lengths = lengths

        # Rebuild the cached content surface and trigger a redraw
        self._rebuild_content_surface()
        self.minimap.queue_draw()
        return False  # one-shot timer

    def __scroll_event_cb(self, widget, event):
        # Adjust scrolling speed when adding "shift" or "shift + ctrl"
        adj = self.scroll.get_adjustment()
        page_size = adj.get_page_size()
        if (
            event.get_state() & Gdk.ModifierType.SHIFT_MASK
            and event.get_state() & Gdk.ModifierType.CONTROL_MASK
        ):
            # Ctrl + Shift + Mouse Scroll (4 pages)
            adj.set_page_increment(page_size * 40)
        elif event.get_state() & Gdk.ModifierType.SHIFT_MASK:
            # Shift + Mouse Scroll (1 page)
            adj.set_page_increment(page_size * 10)
        else:
            # Mouse Scroll
            adj.set_page_increment(page_size)

        # Invalidate the existing minimap drawing so the viewfinder will be redrawn
        self.minimap.queue_draw()

    def get_terminal(self):
        return self.terminal

    def get_terminals(self):
        if self.terminal is not None:
            return [self.terminal]
        return []

    def iter_terminals(self):
        if self.terminal is not None:
            yield self.terminal

    def replace_child(self, old, new):
        log.warning("replace_child called on TerminalBox, which has no children to replace")
        pass

    def unset_terminal(self, *args):
        if self._minimap_refresh_timer_id is not None:
            GLib.source_remove(self._minimap_refresh_timer_id)
            self._minimap_refresh_timer_id = None
        self._minimap_line_lengths = None
        self._minimap_content_surface = None
        self.terminal = None

    def split_h(self, split_percentage: int = 50):
        return self.split(DualTerminalBox.ORIENT_V, split_percentage)

    def split_v(self, split_percentage: int = 50):
        return self.split(DualTerminalBox.ORIENT_H, split_percentage)

    def split_h_no_save(self, split_percentage: int = 50):
        return self.split_no_save(DualTerminalBox.ORIENT_V, split_percentage)

    def split_v_no_save(self, split_percentage: int = 50):
        return self.split_no_save(DualTerminalBox.ORIENT_H, split_percentage)

    @save_tabs_when_changed
    def split(self, orientation, split_percentage: int = 50):
        self.split_no_save(orientation, split_percentage)

    def split_no_save(self, orientation, split_percentage: int = 50):
        notebook = self.get_notebook()
        parent = self.get_parent()  # RootTerminalBox

        if orientation == DualTerminalBox.ORIENT_H:
            position = self.get_allocation().width * ((100 - split_percentage) / 100)
        else:
            position = self.get_allocation().height * ((100 - split_percentage) / 100)

        terminal_box = TerminalBox()
        terminal = notebook.terminal_spawn()
        terminal_box.set_terminal(terminal)
        dual_terminal_box = DualTerminalBox(orientation)
        dual_terminal_box.set_position(position)
        parent.replace_child(self, dual_terminal_box)
        dual_terminal_box.set_child_first(self)
        dual_terminal_box.set_child_second(terminal_box)
        terminal_box.show()
        dual_terminal_box.show()
        if self.terminal is not None:
            # preserve font and font_scale in the new terminal
            terminal.set_font(self.terminal.font)
            terminal.font_scale = self.terminal.font_scale
        notebook.terminal_attached(terminal)

        return dual_terminal_box

    # Override Gtk.Widget.get_window/get_settings which would shadow the mixin
    def get_window(self):
        return self._get_holder_window()

    def get_settings(self):
        return self._get_holder_settings()

    def remove_dead_child(self, child):
        log.warning("remove_dead_child called on TerminalBox, which has no child to remove")

    def on_terminal_focus(self, *args):
        root = self.get_root_box()
        if root:
            root.set_last_terminal_focused(self.terminal)

    def on_terminal_exited(self, terminal, status):
        ancestor = _find_ancestor(self, 'remove_dead_child')
        if ancestor:
            ancestor.remove_dead_child(self)

    def on_button_press(self, target, event, user_data):
        if event.button == 3:
            # First send to background process if handled, do nothing else
            if (
                not event.get_state() & Gdk.ModifierType.SHIFT_MASK
                and Vte.Terminal.do_button_press_event(self.terminal, event)
            ):
                return True

            # Detect which command block was right-clicked (if any)
            clicked_block = None
            root = self.get_root_box()
            if root and root.block_model:
                adj = self.terminal.get_vadjustment()
                alloc = self.terminal.get_allocation()
                rows = self.terminal.get_row_count()
                if rows > 0 and alloc.height > 0:
                    char_h = alloc.height / rows
                    absolute_row = int(adj.get_value() + event.y / char_h)
                    clicked_block = root.block_model.get_block_at_row(absolute_row)

            menu = mk_terminal_context_menu(
                self.terminal,
                self.get_window(),
                self.get_settings(),
                TerminalContextMenuCallbacks(
                    self.terminal,
                    self.get_window(),
                    self.get_settings(),
                    self.get_root_box().get_notebook(),
                ),
                clicked_block=clicked_block,
            )
            menu.connect("hide", MenuHideCallback(self.get_window()).on_hide)
            HidePrevention(self.get_window()).prevent()
            try:
                menu.popup_at_pointer(event)
            except AttributeError:
                # Gtk 3.18 fallback ("'Menu' object has no attribute 'popup_at_pointer'")
                menu.popup(None, None, None, None, event.button, event.time)
            self.terminal.grab_focus()
            return True
        self.terminal.grab_focus()
        return False


class DualTerminalBox(Gtk.Paned, TerminalHolderChild, TerminalHolder):

    ORIENT_H = 0
    ORIENT_V = 1

    def __init__(self, orientation):
        super().__init__()

        self.orient = orientation
        if orientation is DualTerminalBox.ORIENT_H:
            self.set_orientation(orientation=Gtk.Orientation.HORIZONTAL)
        else:
            self.set_orientation(orientation=Gtk.Orientation.VERTICAL)

    def set_child_first(self, terminal_holder):
        if isinstance(terminal_holder, TerminalHolder):
            self.add1(terminal_holder)
        else:
            log.error("DualTerminalBox.set_child_first: expected TerminalHolder, got %s", type(terminal_holder))

    def set_child_second(self, terminal_holder):
        if isinstance(terminal_holder, TerminalHolder):
            self.add2(terminal_holder)
        else:
            log.error("DualTerminalBox.set_child_second: expected TerminalHolder, got %s", type(terminal_holder))

    def get_terminals(self):
        return self.get_child1().get_terminals() + self.get_child2().get_terminals()

    def iter_terminals(self):
        for t in self.get_child1().iter_terminals():
            yield t
        for t in self.get_child2().iter_terminals():
            yield t

    def replace_child(self, old, new):
        if self.get_child1() is old:
            self.remove(old)
            self.set_child_first(new)
        elif self.get_child2() is old:
            self.remove(old)
            self.set_child_second(new)
        else:
            log.error("DualTerminalBox.replace_child: unknown child widget")

    # Override Gtk.Widget.get_window/get_settings which would shadow the mixin
    def get_window(self):
        return self._get_holder_window()

    def get_settings(self):
        return self._get_holder_settings()

    def grab_box_terminal_focus(self, box):
        if isinstance(box, DualTerminalBox):
            try:
                next(box.iter_terminals()).grab_focus()
            except StopIteration:
                log.error("Both panes are empty")
        else:
            box.get_terminal().grab_focus()

    @save_tabs_when_changed
    def remove_dead_child(self, child):
        if self.get_child1() is child:
            living_child = self.get_child2()
            self.remove(living_child)
            self.get_parent().replace_child(self, living_child)
            self.grab_box_terminal_focus(living_child)
        elif self.get_child2() is child:
            living_child = self.get_child1()
            self.remove(living_child)
            self.get_parent().replace_child(self, living_child)
            self.grab_box_terminal_focus(living_child)
        else:
            log.error("DualTerminalBox.remove_dead_child: unknown child widget")


class TabLabelEventBox(Gtk.EventBox):
    def __init__(self, notebook, text, settings):
        super().__init__()
        self.notebook = notebook
        from guake.ui_config import get as ui

        # Two-line vertical layout
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, visible=True)

        # Top line: title (always fully visible)
        self.label = Gtk.Label(label=text, visible=True)
        self.label.set_xalign(0)
        self.label.get_style_context().add_class("tab-title")
        self.box.pack_start(self.label, False, False, 0)

        # Bottom line: command + status
        self._cmd_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3, visible=True)
        self._cmd_label = Gtk.Label(label="", visible=True)
        self._cmd_label.set_xalign(0)
        self._cmd_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._cmd_label.set_max_width_chars(int(ui('tab_cmd_max_chars')))
        self._cmd_label.get_style_context().add_class("tab-cmd")

        self._status_label = Gtk.Label(label="", visible=False)
        self._status_label.get_style_context().add_class("tab-status")

        self._spinner = Gtk.Spinner()
        self._spinner.set_size_request(10, 10)
        self._spinner.set_no_show_all(True)

        self._cmd_box.pack_start(self._cmd_label, True, True, 0)
        self._cmd_box.pack_end(self._status_label, False, False, 0)
        self._cmd_box.pack_end(self._spinner, False, False, 0)
        self.box.pack_start(self._cmd_box, False, False, 0)

        # Close button (overlaid top-right)
        overlay = Gtk.Overlay()
        overlay.add(self.box)

        self.close_button = Gtk.Button(
            image=Gtk.Image.new_from_icon_name("window-close", Gtk.IconSize.MENU),
            relief=Gtk.ReliefStyle.NONE,
        )
        self.close_button.set_halign(Gtk.Align.END)
        self.close_button.set_valign(Gtk.Align.START)
        self.close_button.connect("clicked", self.on_close)
        settings.general.bind(
            "tab-close-buttons", self.close_button, "visible", Gio.SettingsBindFlags.GET
        )
        overlay.add_overlay(self.close_button)
        overlay.set_overlay_pass_through(self.close_button, False)

        self.add(overlay)
        self.connect("button-press-event", self.on_button_press, self.label)

        # Completion time tracking for tooltip
        self._completed_at = None
        self._completed_cmd = None
        self._completed_exit = None
        self.connect("query-tooltip", self._on_query_tooltip)

    def _on_query_tooltip(self, widget, x, y, keyboard, tooltip):
        """Show how long ago the last command completed."""
        if self._completed_at is None:
            return False
        elapsed = int(time.time() - self._completed_at)
        if elapsed < 5:
            ago = "just now"
        elif elapsed < 60:
            ago = f"{elapsed}s ago"
        elif elapsed < 3600:
            ago = f"{elapsed // 60}m {elapsed % 60}s ago"
        else:
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            ago = f"{h}h {m}m ago"

        status = "✓" if self._completed_exit == 0 else f"✗ {self._completed_exit}"
        cmd = (self._completed_cmd or "")[:60]
        tooltip.set_text(f"{status} {cmd} — {ago}")
        return True

    # ---- Command status updates (called from block events) ----

    # Command type indicators — shown instead of spinner for known commands
    _CMD_INDICATORS = {
        # (prefix, indicator, css_class)
        'ssh ': ('SSH', 'cmd-badge-ssh'),
        'ssh -': ('SSH', 'cmd-badge-ssh'),
        'scp ': ('SCP', 'cmd-badge-ssh'),
        'sftp ': ('SFTP', 'cmd-badge-ssh'),
        'rsync ': ('SYNC', 'cmd-badge-ssh'),
        'docker ': ('🐳', 'cmd-badge-docker'),
        'docker-compose ': ('🐳', 'cmd-badge-docker'),
        'podman ': ('🐳', 'cmd-badge-docker'),
        'kubectl ': ('☸', 'cmd-badge-k8s'),
        'helm ': ('☸', 'cmd-badge-k8s'),
        'git ': ('GIT', 'cmd-badge-git'),
        'npm ': ('NPM', 'cmd-badge-node'),
        'yarn ': ('YARN', 'cmd-badge-node'),
        'pnpm ': ('PNPM', 'cmd-badge-node'),
        'node ': ('NODE', 'cmd-badge-node'),
        'python ': ('PY', 'cmd-badge-python'),
        'python3 ': ('PY', 'cmd-badge-python'),
        'pip ': ('PIP', 'cmd-badge-python'),
        'go ': ('GO', 'cmd-badge-go'),
        'cargo ': ('RUST', 'cmd-badge-rust'),
        'make': ('MAKE', 'cmd-badge-build'),
        'cmake': ('CMAKE', 'cmd-badge-build'),
        'gradle': ('GRADLE', 'cmd-badge-build'),
        'mvn ': ('MVN', 'cmd-badge-build'),
        'vim ': ('VIM', 'cmd-badge-editor'),
        'nvim ': ('VIM', 'cmd-badge-editor'),
        'nano ': ('NANO', 'cmd-badge-editor'),
        'emacs': ('EMACS', 'cmd-badge-editor'),
        'htop': ('HTOP', 'cmd-badge-monitor'),
        'top': ('TOP', 'cmd-badge-monitor'),
        'watch ': ('WATCH', 'cmd-badge-monitor'),
        'tail ': ('TAIL', 'cmd-badge-monitor'),
        'less ': ('LESS', 'cmd-badge-monitor'),
        'man ': ('MAN', 'cmd-badge-monitor'),
        'sudo ': ('SUDO', 'cmd-badge-sudo'),
        'su ': ('SU', 'cmd-badge-sudo'),
        'curl ': ('CURL', 'cmd-badge-net'),
        'wget ': ('WGET', 'cmd-badge-net'),
        'ping ': ('PING', 'cmd-badge-net'),
        'nmap ': ('NMAP', 'cmd-badge-net'),
        'mysql': ('SQL', 'cmd-badge-db'),
        'psql': ('SQL', 'cmd-badge-db'),
        'redis-cli': ('REDIS', 'cmd-badge-db'),
        'mongosh': ('MONGO', 'cmd-badge-db'),
    }

    def _get_cmd_indicator(self, command_text):
        """Return (indicator_text, css_class) for a command, or None for generic spinner."""
        cmd = command_text.strip().lower()
        # Check prefixes (longer matches first)
        for prefix, (indicator, css_class) in sorted(
                self._CMD_INDICATORS.items(), key=lambda x: -len(x[0])):
            if cmd.startswith(prefix):
                return indicator, css_class
        return None, None

    def set_running_command(self, command_text):
        """Show a running command with contextual indicator."""
        self._completed_at = None  # clear tooltip while running
        self.set_has_tooltip(False)
        cmd = self._truncate_cmd(command_text)
        self._cmd_label.set_text(cmd)
        self._cmd_label.get_style_context().remove_class("cmd-success")
        self._cmd_label.get_style_context().remove_class("cmd-fail")
        self._cmd_label.get_style_context().add_class("cmd-running")
        self.get_style_context().add_class("tab-running")
        self._status_label.get_style_context().remove_class("status-ok")
        self._status_label.get_style_context().remove_class("status-fail")

        indicator, _ = self._get_cmd_indicator(command_text)
        if indicator:
            # Show badge instead of spinner
            self._spinner.stop()
            self._spinner.hide()
            self._status_label.set_text(indicator)
            self._status_label.get_style_context().add_class("status-running")
            self._status_label.show()
        else:
            # Generic spinner for unknown commands
            self._status_label.hide()
            self._spinner.show()
            self._spinner.start()

    def set_command_result(self, command_text, exit_code):
        """Show finished command with success/failure indicator."""
        cmd = self._truncate_cmd(command_text)
        self._cmd_label.set_text(cmd)
        self._spinner.stop()
        self._spinner.hide()
        self._cmd_label.get_style_context().remove_class("cmd-running")
        self.get_style_context().remove_class("tab-running")
        self._status_label.get_style_context().remove_class("status-running")

        if exit_code == 0:
            self._cmd_label.get_style_context().remove_class("cmd-fail")
            self._cmd_label.get_style_context().add_class("cmd-success")
            self._status_label.set_text("✓")
            self._status_label.get_style_context().remove_class("status-fail")
            self._status_label.get_style_context().add_class("status-ok")
        else:
            self._cmd_label.get_style_context().remove_class("cmd-success")
            self._cmd_label.get_style_context().add_class("cmd-fail")
            self._status_label.set_text(f"✗ {exit_code}")
            self._status_label.get_style_context().remove_class("status-ok")
            self._status_label.get_style_context().add_class("status-fail")
        self._status_label.show()

        # Store completion time for tooltip
        self._completed_at = time.time()
        self._completed_cmd = command_text
        self._completed_exit = exit_code
        self.set_has_tooltip(True)

    def clear_command(self):
        """Clear the command line (idle prompt)."""
        self._cmd_label.set_text("")
        self._cmd_label.get_style_context().remove_class("cmd-running")
        self._cmd_label.get_style_context().remove_class("cmd-success")
        self._cmd_label.get_style_context().remove_class("cmd-fail")
        self.get_style_context().remove_class("tab-running")
        self._status_label.get_style_context().remove_class("status-running")
        self._status_label.hide()
        self._spinner.stop()
        self._spinner.hide()

    def _truncate_cmd(self, text):
        """Get just the command name from a full command line."""
        if not text:
            return ""
        # Take first line, first ~30 chars
        first_line = text.split('\n')[0].strip()
        return first_line

    def set_text(self, text):
        self.label.set_text(text)

    def get_text(self):
        return self.label.get_text()

    def grab_focus_on_last_focused_terminal(self):
        server_time = get_server_time(self.notebook.guake.window)
        self.notebook.guake.window.get_window().focus(server_time)
        self.notebook.get_current_terminal().grab_focus()

    def on_button_press(self, target, event, user_data):
        if event.button == 3:
            menu = mk_tab_context_menu(self)
            menu.connect("hide", MenuHideCallback(self.get_toplevel()).on_hide)
            HidePrevention(self.get_toplevel()).prevent()
            try:
                menu.popup_at_pointer(event)
            except AttributeError:
                # Gtk 3.18 fallback ("'Menu' object has no attribute 'popup_at_pointer'")
                menu.popup(None, None, None, None, event.button, event.get_time())
            return True
        if event.button == 2:
            prompt_cfg = self.notebook.guake.settings.general.get_int("prompt-on-close-tab")
            self.notebook.delete_page_by_label(self, prompt=prompt_cfg)
            return True
        if event.button == 1 and event.type == Gdk.EventType._2BUTTON_PRESS:
            self.on_rename(None)

        return False

    @save_tabs_when_changed
    def on_new_tab(self, user_data):
        self.notebook.new_page_with_focus()

    @save_tabs_when_changed
    def on_rename(self, user_data):
        HidePrevention(self.get_toplevel()).prevent()
        dialog = RenameDialog(self.notebook.guake.window, self.label.get_text())
        r = dialog.run()
        if r == Gtk.ResponseType.ACCEPT:
            new_text = TabNameUtils.shorten(dialog.get_text(), self.notebook.guake.settings)
            page_num = self.notebook.find_tab_index_by_label(self)
            self.notebook.rename_page(page_num, new_text, True)
        dialog.destroy()
        HidePrevention(self.get_toplevel()).allow()

        self.grab_focus_on_last_focused_terminal()

    @save_tabs_when_changed
    def on_reset_custom_colors(self, user_data):
        HidePrevention(self.get_toplevel()).prevent()
        if PromptResetColorsDialog(self.notebook.guake.window).reset_tab_custom_colors():
            page_num = self.notebook.find_tab_index_by_label(self)
            for t in self.notebook.get_nth_page(page_num).iter_terminals():
                t.reset_custom_colors()
            self.notebook.guake.set_colors_from_settings_on_page(page_num=page_num)
        HidePrevention(self.get_toplevel()).allow()

        self.grab_focus_on_last_focused_terminal()

    @save_tabs_when_changed
    def on_set_opacity(self, user_data):
        """Opens a dialog with a slider to set per-tab background opacity."""
        HidePrevention(self.get_toplevel()).prevent()

        page_num = self.notebook.find_tab_index_by_label(self)
        page = self.notebook.get_nth_page(page_num) if page_num != -1 else None
        terminals = list(page.iter_terminals()) if page else []

        # Get the current opacity from the first terminal (or global default)
        current_alpha = 1.0
        if terminals:
            t = terminals[0]
            if t.custom_bgcolor:
                current_alpha = t.custom_bgcolor.alpha
            else:
                current_alpha = self.notebook.guake.get_bgcolor().alpha

        dialog = Gtk.Dialog(
            title="Set Tab Opacity",
            parent=self.notebook.guake.window,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.set_default_size(350, -1)

        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(5)

        label = Gtk.Label(xalign=0)
        label.set_markup("Background opacity for this tab:")
        box.pack_start(label, False, False, 0)

        adjustment = Gtk.Adjustment(
            value=current_alpha * 100,
            lower=0, upper=100,
            step_increment=5, page_increment=10,
        )
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment)
        scale.set_digits(0)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.add_mark(0, Gtk.PositionType.BOTTOM, "0%")
        scale.add_mark(50, Gtk.PositionType.BOTTOM, "50%")
        scale.add_mark(100, Gtk.PositionType.BOTTOM, "100%")

        # Live preview: update terminal opacity as the slider moves
        def on_value_changed(scale):
            alpha = scale.get_value() / 100.0
            bg = self.notebook.guake.get_bgcolor()
            bg.alpha = alpha
            for t in terminals:
                t.set_color_background_custom(bg)

        scale.connect("value-changed", on_value_changed)
        box.pack_start(scale, False, False, 0)

        reset_btn = Gtk.Button.new_with_label("Reset to default")
        def on_reset(btn):
            default_alpha = self.notebook.guake.get_bgcolor().alpha
            scale.set_value(default_alpha * 100)
        reset_btn.connect("clicked", on_reset)
        box.pack_start(reset_btn, False, False, 5)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            # Keep the custom opacity (already applied via live preview)
            pass
        else:
            # Revert: reset custom bgcolor and re-apply from settings
            for t in terminals:
                t.custom_bgcolor = None
            self.notebook.guake.set_colors_from_settings_on_page(page_num=page_num)

        dialog.destroy()
        HidePrevention(self.get_toplevel()).allow()
        self.grab_focus_on_last_focused_terminal()

    def on_close(self, user_data):
        prompt_cfg = self.notebook.guake.settings.general.get_int("prompt-on-close-tab")
        self.notebook.delete_page_by_label(self, prompt=prompt_cfg)
