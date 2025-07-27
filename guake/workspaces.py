# -*- coding: utf-8; -*-
"""
Manages the sidebar for workspace navigation, state, and persistence.
"""
import gi
import json
import uuid
from pathlib import Path
import os
import shutil
from datetime import datetime
import subprocess

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gio, Gdk, GLib

from guake.utils import save_tabs_when_changed
from .emoji_selector import SearchableEmojiSelector

import logging

log = logging.getLogger(__name__)

DEFAULT_WORKSPACES_CONFIG = {
    "version": "1.0",
    "settings": {
        "default_workspace_name": "New Workspace",
        "default_workspace_icon": "💡",
        "default_workspace_color_bg": "#FFFFFF",
        "default_workspace_color_fg": "#000000",
    },
    "active_workspace": None,
    "workspaces": [],
}

DND_TARGET = [Gtk.TargetEntry.new("GTK_LIST_BOX_ROW", Gtk.TargetFlags.SAME_APP, 0)]
ZERO_UUID = "00000000-0000-0000-0000-000000000000"


class WorkspaceManager:
    """
    Creates and manages the sidebar widget for workspaces.
    """

    def __init__(self, guake_app):
        """
        Initializes the WorkspaceManager.
        """
        self.guake_app = guake_app
        self.config_path = self.guake_app.get_xdg_config_directory() / "workspaces.json"
        self.emoji_recently_used = self.guake_app.get_xdg_config_directory() / "emoji_recently_used.json"
        self.workspaces_data = {}
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.widget.get_style_context().add_class("sidebar")
        self.is_dropping = False
        self._git_status_cache = {}
        self._refresh_timer_id = None

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .git-status-clean { color: #26A269; }
            .git-status-dirty { color: #FF7800; }
            .git-status-untracked { color: #F6D32D; }
            .git-status-nogit { opacity: 0.4; }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._load_data()
        self._build_header()
        self._build_workspace_list()
        self._start_refresh_timer()

    def get_xdg_config_directory(self):
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "~/.config")
        return Path(xdg_config_home, "guake").expanduser()

    def _load_data(self):
        """Loads workspace data from workspaces.json, without validation."""
        if self.config_path.exists():
            try:
                with self.config_path.open("r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
                if isinstance(loaded_data, dict) and "workspaces" in loaded_data:
                    self.workspaces_data = loaded_data
                    log.info("Workspaces loaded from %s (pre-validation)", self.config_path)
                else:
                    log.warning("workspaces.json is malformed. Using default config.")
                    self.workspaces_data = DEFAULT_WORKSPACES_CONFIG
            except (json.JSONDecodeError, IOError) as e:
                log.error("Failed to load or parse workspaces file: %s. Using default config.", e)
                self.workspaces_data = DEFAULT_WORKSPACES_CONFIG
        else:
            log.info("No workspaces.json found, using default config.")
            self.workspaces_data = DEFAULT_WORKSPACES_CONFIG

    def validate_loaded_workspaces(self, existing_terminal_uuids):
        """
        Validates and cleans the loaded workspace data against a provided list of
        existing terminal UUIDs. This should be called after tabs are restored.
        """
        log.info("Validating loaded workspace data...")
        all_terminal_uuids = set(existing_terminal_uuids)
        
        terminal_to_workspace_map = {}
        workspaces = self.workspaces_data.get("workspaces", [])
        
        for ws in workspaces:
            terminals_to_keep = []
            for term_uuid in ws.get("terminals", []):
                if term_uuid in terminal_to_workspace_map:
                    log.warning(
                        "Terminal %s already belongs to workspace %s, but also found in workspace %s; removing from workspace %s",
                        term_uuid, terminal_to_workspace_map[term_uuid], ws.get("name"), ws.get("name")
                    )
                    continue
                
                if term_uuid not in all_terminal_uuids:
                    log.warning("Terminal %s from workspace '%s' not found in session; dropping.", term_uuid, ws.get("name"))
                    continue
                
                terminal_to_workspace_map[term_uuid] = ws.get("name")
                terminals_to_keep.append(term_uuid)
            
            ws["terminals"] = terminals_to_keep
            
            active_terminal = ws.get("active_terminal")
            if active_terminal and active_terminal not in terminals_to_keep:
                log.warning("Active terminal %s for workspace '%s' is not valid; resetting.", active_terminal, ws.get("name"))
                ws["active_terminal"] = None
        
        log.info("Workspace validation complete.")
        self.save_workspaces()
        self._build_workspace_list()

    def save_workspaces(self):
        """Saves current workspace data to workspaces.json."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            data_to_save = self.workspaces_data.copy()
            with self.config_path.open("w", encoding="utf-8") as f:
                json.dump(data_to_save, f, indent=2)
            log.info("Workspaces saved to %s", self.config_path)
        except IOError as e:
            log.error("Failed to save workspaces file: %s", e)

    def reconcile_orphan_tabs(self, all_session_uuids=None):
        """
        Reconciles terminal states with workspaces.
        - During startup session restore, it cleans up non-existent terminals from workspaces.
        - During normal operation, it finds newly created terminals (orphans) and assigns
          them to the "No workspace" group.
        """
        if all_session_uuids is None:
            all_terminal_uuids = {str(t.uuid) for t in self.guake_app.get_notebook().iter_terminals()}
            is_startup_restore = False
        else:
            all_terminal_uuids = all_session_uuids
            is_startup_restore = True

        # First, clean up any "ghost" terminals from our workspace data that no longer exist.
        # This is safe to do in both startup and runtime scenarios.
        for ws in self.workspaces_data.get("workspaces", []):
            if ws.get('id') != ZERO_UUID:
                terminals = ws.get("terminals", [])
                ws["terminals"] = [tid for tid in terminals if tid in all_terminal_uuids]

        # Only search for and reassign orphans during normal runtime. During startup,
        # terminals are restored but not yet assigned, so they would all be incorrectly
        # flagged as orphans.
        if not is_startup_restore:
            assigned_regular_uuids = set()
            for ws in self.workspaces_data.get("workspaces", []):
                if ws.get('id') != ZERO_UUID:
                    assigned_regular_uuids.update(ws.get("terminals", []))

            orphan_uuids = all_terminal_uuids - assigned_regular_uuids

            if orphan_uuids:
                log.info("Found %d orphan tabs. Assigning to 'No workspace'.", len(orphan_uuids))
                zero_workspace = self.get_workspace_by_id(ZERO_UUID)
                if not zero_workspace:
                    zero_workspace = {
                        "id": ZERO_UUID, "name": "No workspace", "terminals": [],
                        "icon": "❓", "is_pinned": False, "is_special": True,
                    }
                    self.workspaces_data.setdefault("workspaces", []).insert(0, zero_workspace)
                
                existing_zero_terminals = set(zero_workspace.get("terminals", []))
                existing_zero_terminals.update(orphan_uuids)
                zero_workspace["terminals"] = list(existing_zero_terminals)

        self.save_workspaces()
        self._build_workspace_list()


    def _build_header(self):
        """
        Builds the header of the sidebar with a title and an expanded menu button.
        """
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_box.set_margin_top(6)
        header_box.set_margin_bottom(6)
        header_box.set_margin_start(6)
        header_box.set_margin_end(6)

        menu_icon = Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        menu_button = Gtk.MenuButton(image=menu_icon)

        menu = Gio.Menu()
        menu.append("New Tab", "app.new_tab")
        menu.append_section(None, Gio.Menu())
        menu.append("Save Tabs", "app.save_tabs")
        menu.append("Restore Tabs", "app.restore_tabs")
        menu.append_section(None, Gio.Menu())
        menu.append("Settings", "app.preferences")
        menu.append("Info", "app.about")
        menu.append_section(None, Gio.Menu())
        menu.append("Quit", "app.quit")
        menu_button.set_menu_model(menu)

        app_action_group = Gio.SimpleActionGroup()
        actions = {
            "new_tab": self.guake_app.accel_add,
            "save_tabs": lambda a, p: self.guake_app.save_tabs(),
            "restore_tabs": lambda a, p: self.guake_app.restore_tabs(),
            "preferences": self.guake_app.show_prefs,
            "about": self.guake_app.show_about,
            "quit": self.guake_app.accel_quit,
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            app_action_group.add_action(action)
        self.guake_app.window.insert_action_group("app", app_action_group)

        title = Gtk.Label(label="Workspaces")
        title.set_halign(Gtk.Align.CENTER)
        title.set_hexpand(True)
        title.get_style_context().add_class("sidebar-title")
        
        add_ws_button = Gtk.Button(image=Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON), relief=Gtk.ReliefStyle.NONE)
        add_ws_button.connect("clicked", self.on_add_workspace)

        header_box.pack_start(menu_button, False, False, 0)
        header_box.pack_start(title, True, True, 0)
        header_box.pack_end(add_ws_button, False, False, 0)

        self.widget.pack_start(header_box, False, False, 0)
        self.widget.pack_start(Gtk.Separator(), False, False, 0)

    def _build_workspace_list(self):
        """
        Builds the listbox that will contain the workspaces from the loaded data,
        separating pinned workspaces and enabling drag-and-drop.
        """
        if hasattr(self, "scrolled_window"):
            self.widget.remove(self.scrolled_window)
            self.scrolled_window.destroy()

        self.workspace_listbox = Gtk.ListBox()
        self.workspace_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.workspace_listbox.connect("row-activated", self.on_workspace_activated)

        self.workspace_listbox.drag_dest_set(Gtk.DestDefaults.ALL, DND_TARGET, Gdk.DragAction.MOVE)
        self.workspace_listbox.connect("drag-motion", self.on_drag_motion)
        self.workspace_listbox.connect("drag-drop", self.on_drag_drop)
        self.workspace_listbox.connect("drag-data-received", self.on_drag_data_received)

        all_workspaces = self.workspaces_data.get("workspaces", [])
        
        no_workspace = self.get_workspace_by_id(ZERO_UUID)
        if no_workspace and no_workspace.get("terminals"):
            row = self.create_workspace_row(no_workspace, is_pinned=False)
            self.workspace_listbox.add(row)
            if any(w.get('id') != ZERO_UUID for w in all_workspaces):
                separator_row = Gtk.ListBoxRow()
                separator_row.set_selectable(False)
                separator = Gtk.Separator()
                separator.set_margin_top(5)
                separator.set_margin_bottom(5)
                separator_row.add(separator)
                self.workspace_listbox.add(separator_row)

        regular_workspaces = [w for w in all_workspaces if w.get("id") != ZERO_UUID]
        pinned_workspaces = sorted(
            [w for w in regular_workspaces if w.get("is_pinned")],
            key=lambda w: w.get("updated_at", ""),
            reverse=True,
        )
        unpinned_workspaces = [w for w in regular_workspaces if not w.get("is_pinned")]

        if pinned_workspaces:
            pinned_header = Gtk.ListBoxRow()
            pinned_header.set_selectable(False)
            header_label = Gtk.Label(label="📌 Pinned", xalign=0)
            header_label.get_style_context().add_class("dim-label")
            pinned_header.add(header_label)
            self.workspace_listbox.add(pinned_header)

            for ws_data in pinned_workspaces:
                row = self.create_workspace_row(ws_data, is_pinned=True)
                self.workspace_listbox.add(row)

            if unpinned_workspaces:
                separator_row = Gtk.ListBoxRow()
                separator_row.set_selectable(False)
                separator = Gtk.Separator()
                separator.set_margin_top(5)
                separator.set_margin_bottom(5)
                separator_row.add(separator)
                self.workspace_listbox.add(separator_row)

        for ws_data in unpinned_workspaces:
            row = self.create_workspace_row(ws_data, is_pinned=False)
            self.workspace_listbox.add(row)

        active_workspace_id = self.workspaces_data.get("active_workspace")
        if active_workspace_id:
            for row in self.workspace_listbox.get_children():
                if row.get_name() == active_workspace_id:
                    self.workspace_listbox.select_row(row)
                    break

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.add(self.workspace_listbox)
        self.scrolled_window.show_all()

        self.widget.pack_start(self.scrolled_window, True, True, 0)

    def _create_workspace_context_menu(self, ws_data):
        menu = Gtk.Menu()
        rename_item = Gtk.MenuItem(label="Rename")
        delete_item = Gtk.MenuItem(label="Delete")
        pin_label = "Unpin" if ws_data.get("is_pinned") else "Pin"
        pin_item = Gtk.MenuItem(label=pin_label)

        rename_item.connect("activate", self.on_rename_workspace, ws_data["id"])
        delete_item.connect("activate", self.on_delete_workspace, ws_data["id"])
        pin_item.connect("activate", self.on_pin_workspace, ws_data["id"])

        menu.append(rename_item)
        menu.append(delete_item)
        menu.append(pin_item)
        menu.show_all()
        return menu

    def on_row_right_click(self, widget, event, ws_data):
        if event.button == 3:
            if not ws_data.get("is_special"):
                menu = self._create_workspace_context_menu(ws_data)
                menu.popup_at_pointer(event)
                return True
        return False

    def create_workspace_row(self, ws_data, is_pinned):
        """Creates a Gtk.ListBoxRow for a single workspace."""
        list_box_row = Gtk.ListBoxRow()
        list_box_row.set_name(ws_data["id"])

        event_box = Gtk.EventBox()
        list_box_row.add(event_box)
        event_box.set_visible_window(False)

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row_box.set_margin_top(4)
        row_box.set_margin_bottom(4)
        row_box.set_margin_start(8)
        row_box.set_margin_end(8)
        event_box.add(row_box)

        event_box.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        event_box.connect("enter-notify-event", self.on_row_enter)
        event_box.connect("leave-notify-event", self.on_row_leave)
        event_box.connect("button-press-event", self.on_row_right_click, ws_data)

        is_special = ws_data.get("is_special", False)
        if not is_pinned and not is_special:
            event_box.connect("drag-begin", self.on_row_drag_begin)
            event_box.connect("drag-end", self.on_row_drag_end)
            event_box.connect("drag-data-get", self.on_row_drag_data_get)
            event_box.connect("drag-data-delete", self.on_row_drag_data_delete)
            event_box.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, DND_TARGET, Gdk.DragAction.MOVE)

        label_text = f"{ws_data.get('icon', '')} {ws_data['name']}"
        label = Gtk.Label(label=label_text, xalign=0)
        label.set_hexpand(True)
        row_box.pack_start(label, True, True, 0)

        tab_count = len(ws_data.get("terminals", []))
        count_label = Gtk.Label(label=str(tab_count))
        count_label.get_style_context().add_class("dim-label")
        row_box.pack_start(count_label, False, False, 0)

        if not is_special:
            status = self._git_status_cache.get(ws_data["id"], 'no-git')
            icon_name, tooltip = self._get_git_icon_and_tooltip(status)
            git_icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
            git_icon.set_tooltip_text(tooltip)
            git_icon.get_style_context().add_class(f"git-status-{status}")
            row_box.pack_end(git_icon, False, False, 0)

        return list_box_row

    def on_row_enter(self, widget, event):
        """Change cursor to a hand pointer."""
        display = widget.get_display()
        hand_cursor = Gdk.Cursor.new_for_display(display, Gdk.CursorType.HAND2)
        widget.get_window().set_cursor(hand_cursor)

    def on_row_leave(self, widget, event):
        """Change cursor back to default."""
        widget.get_window().set_cursor(None)

    def on_row_drag_begin(self, widget, context):
        """Select the row when a drag operation begins. `widget` is the EventBox."""
        self.is_dropping = False
        row = widget.get_parent()
        self.workspace_listbox.select_row(row)

    def on_row_drag_end(self, widget, context):
        """Reset the dropping flag when the drag operation ends."""
        self.is_dropping = False

    def on_drag_motion(self, listbox, context, x, y, timestamp):
        """Highlight rows that are valid drop targets."""
        drop_row = listbox.get_row_at_y(y)
        target_is_valid = False
        if drop_row and drop_row.get_name():
            ws = next((w for w in self.workspaces_data["workspaces"] if w["id"] == drop_row.get_name()), None)
            if ws and not ws.get("is_pinned") and not ws.get("is_special"):
                target_is_valid = True
        if target_is_valid:
            listbox.drag_highlight_row(drop_row)
            Gdk.drag_status(context, Gdk.DragAction.MOVE, timestamp)
        else:
            listbox.drag_unhighlight_row()
        return target_is_valid

    def on_drag_drop(self, listbox, context, x, y, timestamp):
        """Handles the drag-drop signal, returning True to allow the drop."""
        drop_row = listbox.get_row_at_y(y)
        if drop_row and drop_row.get_name():
            target_atom = Gdk.Atom.intern(DND_TARGET[0].target, False)
            listbox.drag_get_data(context, target_atom, timestamp)
            return True
        return False

    def on_row_drag_data_get(self, widget, context, selection, info, timestamp):
        """Set the drag data to the row's name (workspace ID). `widget` is the EventBox."""
        row = widget.get_parent()
        target_atom = Gdk.Atom.intern(DND_TARGET[0].target, False)
        selection.set(target_atom, 8, row.get_name().encode('utf-8'))

    def on_row_drag_data_delete(self, widget, context):
        """Handle the deletion of the data from the source. `widget` is the EventBox."""
        pass

    def on_drag_data_received(self, widget, context, x, y, selection, info, timestamp):
        """Handle the drop and reorder the workspaces. `widget` is the ListBox."""
        if self.is_dropping: 
            context.finish(False, False, timestamp)
            return
        self.is_dropping = True
        
        try:
            dragged_ws_id = selection.get_data().decode('utf-8')
            drop_row = widget.get_row_at_y(y)
            if not drop_row or not drop_row.get_name() or not dragged_ws_id:
                raise ValueError("Invalid drop target or dragged ID")

            drop_ws_id = drop_row.get_name()
            all_workspaces = self.workspaces_data["workspaces"]

            dragged_ws = next(w for w in all_workspaces if w["id"] == dragged_ws_id)
            if dragged_ws.get("is_pinned") or dragged_ws.get("is_special"):
                raise ValueError("Cannot drag pinned or special workspaces")

            unpinned = [w for w in all_workspaces if not w.get("is_pinned") and not w.get("is_special")]
            drag_idx = unpinned.index(dragged_ws)
            
            drop_ws = next(w for w in all_workspaces if w["id"] == drop_ws_id)
            if drop_ws.get("is_pinned") or drop_ws.get("is_special"):
                raise ValueError("Cannot drop onto pinned or special workspaces")
            
            drop_idx = unpinned.index(drop_ws)
            
            moved_item = unpinned.pop(drag_idx)
            unpinned.insert(drop_idx, moved_item)

            pinned = [w for w in all_workspaces if w.get("is_pinned")]
            special = [w for w in all_workspaces if w.get("is_special")]
            self.workspaces_data["workspaces"] = special + pinned + unpinned
            
            self.save_workspaces()
            GLib.idle_add(self._build_workspace_list)
            context.finish(True, True, timestamp)
        except (StopIteration, ValueError) as e:
            log.error("Error during DnD reorder: %s", e)
            context.finish(False, False, timestamp)

    def on_workspace_activated(self, listbox, row):
        """Handles the activation of a workspace from the sidebar."""
        workspace_id = row.get_name()
        if not workspace_id:
            return

        if self.workspaces_data.get("active_workspace") == workspace_id:
            return

        self.workspaces_data["active_workspace"] = workspace_id
        # Save the active workspace change immediately to prevent race conditions.
        self.save_workspaces()
        
        log.info("Switching to workspace %s", workspace_id)
        # The switch will trigger on_tab_switch, which will set the active_terminal
        # and trigger another save.
        self.guake_app.switch_to_workspace(workspace_id)
        listbox.select_row(row)
    def update_workspace_list_selection(self, workspace_id):
        """Updates the workspace list selection to the specified workspace ID."""
        for row in self.workspace_listbox.get_children():
            if row.get_name() == workspace_id:
                self.workspace_listbox.select_row(row)
                return
        log.warning("Workspace %s not found in the list.", workspace_id)

    def add_terminal_to_workspace(self, terminal_uuid, workspace_id):
        """Adds a terminal to a specific workspace and saves the state."""
        ws = self.get_workspace_by_id(workspace_id)
        if ws:
            ws.setdefault("terminals", []).append(terminal_uuid)
            ws["active_terminal"] = terminal_uuid
            self.save_workspaces()
            self.guake_app.save_tabs()
            self._build_workspace_list()

    def add_terminal_to_active_workspace(self, terminal_uuid):
        active_ws = self.get_active_workspace()
        if not active_ws or active_ws.get("is_special"):
            target_ws = next((w for w in self.get_all_workspaces() if not w.get("is_special")), None)
            if not target_ws:
                target_ws = self.on_add_workspace(None, None, activate=True)
            else:
                self.workspaces_data["active_workspace"] = target_ws["id"]
            active_ws = target_ws

        if active_ws:
            active_ws.setdefault("terminals", []).append(terminal_uuid)
            active_ws["active_terminal"] = terminal_uuid
            self.save_workspaces()
            self._build_workspace_list()

    def remove_terminal_from_active_workspace(self, terminal_uuid):
        active_ws = self.get_active_workspace()
        if active_ws and terminal_uuid in active_ws.get("terminals", []):
            terminals = active_ws["terminals"]
            idx = terminals.index(terminal_uuid)
            terminals.pop(idx)

            if active_ws.get("active_terminal") == terminal_uuid:
                if terminals:
                    # If the closed tab was the last one, select the new last tab.
                    # Otherwise, select the tab that is now at the same index.
                    new_idx = min(idx, len(terminals) - 1)
                    active_ws["active_terminal"] = terminals[new_idx]
                else:
                    active_ws["active_terminal"] = None
            self.save_workspaces()
            self._build_workspace_list()

    def set_active_terminal_for_active_workspace(self, terminal_uuid):
        active_ws = self.get_active_workspace()
        if not active_ws:
            log.warning("No active workspace found to set active terminal.")
            return
        log.info("Setting active terminal %s for workspace %s", terminal_uuid, active_ws["id"])
        if active_ws:
            # check that the terminal_uuid is in the workspace's terminals
            if terminal_uuid not in active_ws.get("terminals", []):
                log.warning("WARN: Terminal %s not found in active workspace %s terminals.", terminal_uuid, active_ws["id"])
                return
            active_ws["active_terminal"] = terminal_uuid
            self.save_workspaces()

    def update_terminal_order_for_active_workspace(self, list_of_uuids):
        active_ws = self.get_active_workspace()
        if active_ws:
            active_ws["terminals"] = list_of_uuids
            self.save_workspaces()

    def move_terminal_to_workspace(self, terminal_uuid, target_workspace_id):
        source_ws = next((w for w in self.get_all_workspaces() if terminal_uuid in w.get("terminals", [])), None)
        target_ws = self.get_workspace_by_id(target_workspace_id)

        if source_ws and target_ws and source_ws != target_ws:
            terminals = source_ws["terminals"]
            idx = terminals.index(terminal_uuid)
            terminals.pop(idx)
            if source_ws.get("active_terminal") == terminal_uuid:
                if terminals:
                    # This logic is now consistent with remove_terminal_from_active_workspace.
                    # It selects the next terminal, or the new last one if the removed
                    # terminal was the last one, matching the behavior of closing a tab.
                    new_idx = min(idx, len(terminals) - 1)
                    source_ws["active_terminal"] = terminals[new_idx]
                else:
                    source_ws["active_terminal"] = None

            target_ws.setdefault("terminals", []).append(terminal_uuid)
            self.save_workspaces()
            self._build_workspace_list()

    def get_all_workspaces(self):
        return self.workspaces_data.get("workspaces", [])

    def get_workspace_by_id(self, workspace_id):
        return next((w for w in self.get_all_workspaces() if w["id"] == workspace_id), None)

    def get_active_workspace(self):
        active_id = self.workspaces_data.get("active_workspace")
        return self.get_workspace_by_id(active_id) if active_id else None

    @save_tabs_when_changed
    def on_add_workspace(self, widget=None, activate=False):
        """Adds a new workspace to the data and UI."""
        settings = self.workspaces_data["settings"]
        new_ws = {
            "id": str(uuid.uuid4()), "name": settings["default_workspace_name"], "terminals": [],
            "tags": {}, "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z", "icon": settings["default_workspace_icon"],
            "color_bg": settings["default_workspace_color_bg"], "color_fg": settings["default_workspace_color_fg"],
            "is_pinned": False, "active_terminal": None,
        }
        self.workspaces_data["workspaces"].append(new_ws)
        if activate:
            self.workspaces_data["active_workspace"] = new_ws["id"]
        self.save_workspaces()
        self._build_workspace_list()
        return new_ws

    def on_add_terminal_to_workspace(self, button, workspace_id):
        """Callback to add a new terminal tab to a specific workspace."""
        self.guake_app.add_tab_to_workspace(workspace_id)

    def on_choose_emoji(self, button, icon_entry):
        """Opens the searchable emoji selector."""
        emoji_file = self.guake_app.settings.general.get_string("emoji-file")
        if not emoji_file:
            log.error("Emoji file path not configured in settings.")
            # Optionally, show an error dialog to the user
            error_dialog = Gtk.MessageDialog(
                parent=self.guake_app.window,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Emoji file not configured",
                secondary_text="Please set the 'emoji-file' path in Guake's settings."
            )
            error_dialog.run()
            error_dialog.destroy()
            return

        dialog = SearchableEmojiSelector(self.guake_app.window, emoji_file, self.emoji_recently_used)
        response = dialog.run()

        if response == Gtk.ResponseType.OK and dialog.selected_emoji:
            icon_entry.set_text(dialog.selected_emoji)
        
        dialog.destroy()

    @save_tabs_when_changed
    def on_rename_workspace(self, menu_item, workspace_id):
        """Opens a dialog to rename the workspace and change its icon."""
        ws = self.get_workspace_by_id(workspace_id)
        if not ws: return

        dialog = Gtk.Dialog(title="Edit Workspace", parent=self.guake_app.window, flags=0,
                            buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK))
        dialog.set_default_size(350, 100)
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin=10)
        dialog.get_content_area().add(grid)

        name_entry = Gtk.Entry(text=ws["name"])
        icon_entry = Gtk.Entry(text=ws.get("icon", ""), max_length=2)
        
        icon_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon_box.pack_start(icon_entry, True, True, 0)
        
        emoji_button = Gtk.Button.new_with_label("😀")
        emoji_button.connect("clicked", self.on_choose_emoji, icon_entry)
        icon_box.pack_start(emoji_button, False, False, 0)

        grid.attach(Gtk.Label(label="Name:", xalign=0), 0, 0, 1, 1)
        grid.attach(name_entry, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Icon:", xalign=0), 0, 1, 1, 1)
        grid.attach(icon_box, 1, 1, 1, 1)
        
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b".error { border: 1px solid red; border-radius: 4px; }")
        name_entry.get_style_context().add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        
        name_entry.connect("activate", lambda entry: dialog.response(Gtk.ResponseType.OK))
        name_entry.connect("changed", lambda entry: entry.get_style_context().remove_class("error"))

        dialog.show_all()

        while True:
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                name_text = name_entry.get_text().strip()
                if not name_text:
                    name_entry.get_style_context().add_class("error")
                    continue
                else:
                    ws["name"] = name_text
                    ws["icon"] = icon_entry.get_text()
                    ws["updated_at"] = datetime.utcnow().isoformat() + "Z"
                    self.save_workspaces()
                    self._build_workspace_list()
                    break
            else:
                break
        
        dialog.destroy()

    @save_tabs_when_changed
    def on_delete_workspace(self, menu_item, workspace_id):
        """Opens a confirmation dialog to delete the workspace."""
        ws = self.get_workspace_by_id(workspace_id)
        if not ws: return

        dialog = Gtk.MessageDialog(parent=self.guake_app.window, flags=0, message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.YES_NO, text=f"Are you sure you want to delete '{ws['name']}'?")
        if dialog.run() == Gtk.ResponseType.YES:
            if self.workspaces_data["active_workspace"] == workspace_id:
                self.workspaces_data["active_workspace"] = None
            
            if ws.get("terminals"):
                remaining_workspaces = [w for w in self.get_all_workspaces() if w["id"] != workspace_id]
                if remaining_workspaces:
                    first_ws = remaining_workspaces[0]
                    first_ws.setdefault("terminals", []).extend(ws["terminals"])

            self.workspaces_data["workspaces"] = [w for w in self.workspaces_data["workspaces"] if w["id"] != workspace_id]
            
            if not self.workspaces_data["active_workspace"] and self.workspaces_data["workspaces"]:
                self.workspaces_data["active_workspace"] = self.workspaces_data["workspaces"][0]["id"]
                self.guake_app.switch_to_workspace(self.workspaces_data["active_workspace"])

            self.save_workspaces()
            self._build_workspace_list()
        dialog.destroy()

    @save_tabs_when_changed
    def on_pin_workspace(self, menu_item, workspace_id):
        """Toggles the pinned state of a workspace."""
        ws = self.get_workspace_by_id(workspace_id)
        if ws:
            ws["is_pinned"] = not ws.get("is_pinned", False)
            ws["updated_at"] = datetime.utcnow().isoformat() + "Z"
            self.save_workspaces()
            self._build_workspace_list()

    def _start_refresh_timer(self):
        """Starts the periodic refresh timer if it's not already running."""
        if self._refresh_timer_id is None:
            log.info("Starting workspace git status refresh timer (15s).")
            self._refresh_timer_id = GLib.timeout_add_seconds(15, self._timed_refresh)

    def _stop_refresh_timer(self):
        """Stops the periodic refresh timer if it is running."""
        if self._refresh_timer_id is not None:
            log.info("Stopping workspace git status refresh timer.")
            GLib.source_remove(self._refresh_timer_id)
            self._refresh_timer_id = None

    def _timed_refresh(self):
        """The callback for the GLib timer to periodically refresh data."""
        log.debug("Timed workspace git status refresh triggered.")
        self._update_git_status_cache()
        self._build_workspace_list()
        return True

    def _update_git_status_cache(self):
        """Updates the internal cache of workspace git statuses using a more efficient directory-based approach."""
        log.debug("Updating workspace git status cache.")
        
        all_terminals = list(self.guake_app.notebook_manager.iter_terminals())
        unique_dirs = set()
        for term in all_terminals:
            try:
                unique_dirs.add(term.get_current_directory())
            except Exception:
                continue

        dir_status_cache = {}
        # Sort directories by path length to process parents before children
        sorted_dirs = sorted(list(unique_dirs), key=len)

        for directory in sorted_dirs:
            # Hierarchical check: if a parent is dirty, this one is too.
            parent = Path(directory).parent
            while str(parent) != parent.root:
                if str(parent) in dir_status_cache and dir_status_cache[str(parent)] == 'dirty':
                    dir_status_cache[directory] = 'dirty'
                    break
                parent = parent.parent
            else: # No dirty parent found
                dir_status_cache[directory] = self._get_git_status(directory)

        # Now, determine the status for each workspace
        for ws in self.get_all_workspaces():
            if ws.get("is_special"):
                continue
            
            statuses = set()
            for term_uuid_str in ws.get("terminals", []):
                terminal = self.guake_app.notebook_manager.get_terminal_by_uuid(uuid.UUID(term_uuid_str))
                if terminal:
                    try:
                        cwd = terminal.get_current_directory()
                        if cwd in dir_status_cache:
                            statuses.add(dir_status_cache[cwd])
                    except Exception:
                        statuses.add('no-git')
            
            if 'dirty' in statuses:
                self._git_status_cache[ws['id']] = 'dirty'
            elif 'untracked' in statuses:
                self._git_status_cache[ws['id']] = 'untracked'
            elif 'clean' in statuses:
                self._git_status_cache[ws['id']] = 'clean'
            else:
                self._git_status_cache[ws['id']] = 'no-git'

    def _get_git_status(self, directory):
        """Checks git status, distinguishing between modified and untracked files."""
        if not directory or not os.path.isdir(directory): return "no-git"
        path = directory
        try:
            while path != os.path.dirname(path):
                if os.path.isdir(os.path.join(path, '.git')):
                    result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=False, cwd=path)
                    if result.returncode != 0: return "no-git"
                    output = result.stdout.strip()
                    if not output: return "clean"
                    has_modified = any(not line.startswith('??') for line in output.splitlines())
                    has_untracked = any(line.startswith('??') for line in output.splitlines())
                    if has_modified: return "dirty"
                    if has_untracked: return "untracked"
                    return "clean"
                path = os.path.dirname(path)
            return "no-git"
        except (FileNotFoundError, Exception) as e:
            log.warning(f"Could not get git status for {directory}: {e}")
            return "no-git"

    def _get_git_icon_and_tooltip(self, status):
        if status == 'clean': return "emblem-ok-symbolic", "Git: Clean"
        elif status == 'dirty': return "emblem-synchronizing-symbolic", "Git: Uncommitted changes"
        elif status == 'untracked': return "document-new-symbolic", "Git: Untracked files"
        else: return "emblem-important-symbolic", "Not a Git repository"
