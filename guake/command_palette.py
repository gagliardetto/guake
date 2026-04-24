# -*- coding: utf-8; -*-
"""
Command palette for Guake — Ctrl+Shift+P to open.

A VS Code-style quick command launcher that provides instant access to
all Guake actions, workspace/tab switching, and settings.
"""
import logging

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

log = logging.getLogger(__name__)


class CommandPalette(Gtk.Window):
    """A floating command palette that filters and executes commands."""

    def __init__(self, guake_app):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.guake = guake_app
        self._commands = []
        self._filtered = []
        self._selected_index = 0

        # Window setup
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_modal(True)
        self.set_transient_for(guake_app.window)
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)

        # Main container
        frame = Gtk.Frame()
        frame.get_style_context().add_class("command-palette")
        self.add(frame)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        frame.add(vbox)

        # Search entry
        self._entry = Gtk.SearchEntry()
        self._entry.set_placeholder_text("Type a command...")
        self._entry.get_style_context().add_class("palette-entry")
        self._entry.connect("search-changed", self._on_search_changed)
        self._entry.connect("key-press-event", self._on_key_press)
        self._entry.connect("activate", self._on_activate)
        vbox.pack_start(self._entry, False, False, 0)

        # Separator
        sep = Gtk.Separator()
        sep.get_style_context().add_class("palette-separator")
        vbox.pack_start(sep, False, False, 0)

        # Results list
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_max_content_height(400)
        self._scroll.set_propagate_natural_height(True)
        vbox.pack_start(self._scroll, True, True, 0)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.connect("row-activated", self._on_row_activated)
        self._scroll.add(self._listbox)

        # CSS
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .command-palette {
                background-color: rgba(24, 26, 32, 0.98);
                border: 1px solid rgba(100, 160, 255, 0.25);
                border-radius: 10px;
                padding: 0;
            }
            .palette-entry {
                background-color: transparent;
                border: none;
                border-radius: 10px 10px 0 0;
                color: rgba(255, 255, 255, 0.9);
                font-size: 11pt;
                padding: 10px 14px;
                caret-color: #6EC1E4;
            }
            .palette-entry:focus {
                box-shadow: none;
                border: none;
            }
            .palette-separator {
                background-color: rgba(255, 255, 255, 0.06);
                min-height: 1px;
                margin: 0 8px;
            }
            .command-palette list {
                background-color: transparent;
                padding: 4px;
            }
            .command-palette list row {
                border-radius: 6px;
                padding: 0;
                margin: 1px 0;
                transition: background-color 80ms ease;
            }
            .command-palette list row:hover {
                background-color: rgba(255, 255, 255, 0.04);
            }
            .command-palette list row:selected {
                background-color: rgba(100, 160, 255, 0.15);
            }
            .palette-row {
                padding: 6px 10px;
            }
            .palette-cmd-name {
                color: rgba(255, 255, 255, 0.9);
                font-size: 10pt;
            }
            .palette-cmd-category {
                color: rgba(255, 255, 255, 0.3);
                font-size: 8pt;
            }
            .palette-cmd-shortcut {
                color: rgba(100, 160, 255, 0.5);
                font-size: 8pt;
                font-family: Monospace;
            }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.connect("focus-out-event", lambda w, e: self.dismiss())

    def show_palette(self):
        """Show the command palette, build commands, focus entry."""
        self._commands = self._build_commands()
        self._entry.set_text("")
        self._filter("")
        self._position_window()
        self.show_all()
        self._entry.grab_focus()

    def dismiss(self):
        """Hide the palette."""
        self.hide()
        # Return focus to terminal
        try:
            nb = self.guake.get_notebook()
            if nb:
                t = nb.get_current_terminal()
                if t:
                    t.grab_focus()
        except Exception:
            pass

    def _position_window(self):
        """Center the palette near the top of the Guake window."""
        parent = self.guake.window
        parent_alloc = parent.get_allocation()
        width = min(500, parent_alloc.width - 60)
        self.set_size_request(width, -1)

        # Get parent window position
        _, px, py = parent.get_window().get_origin()
        x = px + (parent_alloc.width - width) // 2
        y = py + 40
        self.move(x, y)

    def _build_commands(self):
        """Build the list of available commands."""
        cmds = []

        # Workspace switching
        if self.guake.workspace_manager:
            for ws in self.guake.workspace_manager.get_all_workspaces():
                icon = ws.get("icon", "")
                name = ws.get("name", "")
                ws_id = ws["id"]
                cmds.append({
                    "name": f"{icon}  {name}" if icon else name,
                    "category": "Workspace",
                    "action": lambda wid=ws_id: self.guake.switch_to_workspace(wid),
                })

        # Tab switching
        nb = self.guake.get_notebook()
        if nb:
            for i in range(nb.get_n_pages()):
                page = nb.get_nth_page(i)
                tab_label = nb.get_tab_label(page)
                if tab_label and hasattr(tab_label, 'get_text'):
                    title = tab_label.get_text()
                    cmds.append({
                        "name": title,
                        "category": "Tab",
                        "action": lambda idx=i: nb.set_current_page(idx),
                    })

        # General commands
        cmds.extend([
            {"name": "New Tab", "category": "Tabs", "shortcut": "Ctrl+Shift+T",
             "action": lambda: self.guake.accel_add(None, None)},
            {"name": "Close Tab", "category": "Tabs",
             "action": lambda: self.guake.close_tab()},
            {"name": "Rename Tab", "category": "Tabs",
             "action": self._rename_current_tab},
            {"name": "Split Horizontal", "category": "Terminal", "shortcut": "Ctrl+Shift+O",
             "action": lambda: self.guake.accel_split_horizontal(None, None)},
            {"name": "Split Vertical", "category": "Terminal", "shortcut": "Ctrl+Shift+E",
             "action": lambda: self.guake.accel_split_vertical(None, None)},
            {"name": "Toggle Fullscreen", "category": "Window", "shortcut": "F11",
             "action": lambda: self.guake.fullscreen()},
            {"name": "New Workspace", "category": "Workspace",
             "action": lambda: self.guake.workspace_manager.on_add_workspace(None) if self.guake.workspace_manager else None},
            {"name": "Save Tabs", "category": "Session",
             "action": lambda: self.guake.save_tabs()},
            {"name": "Restore Tabs", "category": "Session",
             "action": lambda: self.guake.restore_tabs()},
            {"name": "Reset Terminal", "category": "Terminal",
             "action": self._reset_terminal},
            {"name": "Preferences", "category": "Settings",
             "action": lambda: self.guake.show_prefs(None, None)},
            {"name": "About", "category": "Help",
             "action": lambda: self.guake.show_about(None, None)},
            {"name": "Quit", "category": "App", "shortcut": "Ctrl+Shift+Q",
             "action": lambda: self.guake.accel_quit(None, None)},
        ])

        return cmds

    def _rename_current_tab(self):
        nb = self.guake.get_notebook()
        if nb:
            page = nb.get_nth_page(nb.get_current_page())
            tab_label = nb.get_tab_label(page)
            if tab_label and hasattr(tab_label, 'on_rename'):
                tab_label.on_rename(None)

    def _reset_terminal(self):
        nb = self.guake.get_notebook()
        if nb:
            t = nb.get_current_terminal()
            if t:
                t.reset(True, True)

    def _filter(self, query):
        """Filter commands by query and rebuild the list."""
        query_lower = query.lower().strip()

        # Clear existing rows
        for child in self._listbox.get_children():
            self._listbox.remove(child)

        if query_lower:
            self._filtered = [
                c for c in self._commands
                if query_lower in c["name"].lower()
                or query_lower in c.get("category", "").lower()
            ]
        else:
            self._filtered = list(self._commands)

        for cmd in self._filtered[:20]:  # limit visible results
            row = self._create_row(cmd, query_lower)
            self._listbox.add(row)

        self._listbox.show_all()

        # Select first row
        if self._filtered:
            first = self._listbox.get_row_at_index(0)
            if first:
                self._listbox.select_row(first)
        self._selected_index = 0

    def _create_row(self, cmd, query):
        """Create a listbox row for a command."""
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.get_style_context().add_class("palette-row")

        # Category badge
        cat = Gtk.Label(label=cmd.get("category", ""))
        cat.get_style_context().add_class("palette-cmd-category")
        cat.set_size_request(60, -1)
        cat.set_xalign(0)
        box.pack_start(cat, False, False, 0)

        # Command name
        name_label = Gtk.Label(label=cmd["name"])
        name_label.get_style_context().add_class("palette-cmd-name")
        name_label.set_xalign(0)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_hexpand(True)
        box.pack_start(name_label, True, True, 0)

        # Shortcut (if any)
        shortcut = cmd.get("shortcut")
        if shortcut:
            sc_label = Gtk.Label(label=shortcut)
            sc_label.get_style_context().add_class("palette-cmd-shortcut")
            box.pack_end(sc_label, False, False, 0)

        row.add(box)
        return row

    def _on_search_changed(self, entry):
        self._filter(entry.get_text())

    def _on_key_press(self, entry, event):
        if event.keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        if event.keyval == Gdk.KEY_Down:
            self._move_selection(1)
            return True
        if event.keyval == Gdk.KEY_Up:
            self._move_selection(-1)
            return True
        return False

    def _on_activate(self, entry):
        """Enter pressed — execute selected command."""
        selected = self._listbox.get_selected_row()
        if selected:
            self._execute_row(selected)

    def _on_row_activated(self, listbox, row):
        self._execute_row(row)

    def _execute_row(self, row):
        idx = row.get_index()
        if 0 <= idx < len(self._filtered):
            cmd = self._filtered[idx]
            self.dismiss()
            GLib.idle_add(cmd["action"])

    def _move_selection(self, direction):
        """Move selection up or down."""
        n = len(self._filtered)
        if n == 0:
            return
        self._selected_index = max(0, min(n - 1, self._selected_index + direction))
        row = self._listbox.get_row_at_index(self._selected_index)
        if row:
            self._listbox.select_row(row)
            # Scroll to visible
            adj = self._scroll.get_vadjustment()
            alloc = row.get_allocation()
            if alloc.y < adj.get_value():
                adj.set_value(alloc.y)
            elif alloc.y + alloc.height > adj.get_value() + adj.get_page_size():
                adj.set_value(alloc.y + alloc.height - adj.get_page_size())
