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

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gio, Gdk

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
        self.workspaces_data = {}
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.widget.get_style_context().add_class("sidebar")

        self.load_workspaces()
        self._build_header()
        self._build_workspace_list()

    def get_xdg_config_directory(self):
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "~/.config")
        return Path(xdg_config_home, "guake").expanduser()

    def load_workspaces(self):
        """Loads workspace data from workspaces.json, with validation."""
        if self.config_path.exists():
            try:
                with self.config_path.open("r", encoding="utf-8") as f:
                    loaded_data = json.load(f)

                # Validate that the loaded data is a dictionary with the expected key
                if isinstance(loaded_data, dict) and "workspaces" in loaded_data:
                    self.workspaces_data = loaded_data
                    log.info("Workspaces loaded from %s", self.config_path)
                else:
                    log.warning("workspaces.json is malformed. A backup will be created and settings reset.")
                    # Backup the malformed file before overwriting
                    backup_path = self.config_path.with_name(f"{self.config_path.name}.bak")
                    try:
                        shutil.copy(self.config_path, backup_path)
                        log.info("Malformed workspaces file backed up to %s", backup_path)
                    except Exception as backup_error:
                        log.error("Could not create backup of workspaces file: %s", backup_error)

                    self.workspaces_data = DEFAULT_WORKSPACES_CONFIG
                    self.save_workspaces()

            except (json.JSONDecodeError, IOError) as e:
                log.error("Failed to load or parse workspaces file: %s. Using default config.", e)
                self.workspaces_data = DEFAULT_WORKSPACES_CONFIG
        else:
            log.info("No workspaces.json found, using default config.")
            self.workspaces_data = DEFAULT_WORKSPACES_CONFIG
            self.save_workspaces()

    def save_workspaces(self):
        """Saves current workspace data to workspaces.json."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with self.config_path.open("w", encoding="utf-8") as f:
                json.dump(self.workspaces_data, f, indent=2)
            log.info("Workspaces saved to %s", self.config_path)
        except IOError as e:
            log.error("Failed to save workspaces file: %s", e)

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
        menu.append("Add Workspace", "app.add_workspace")
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
            "add_workspace": self.on_add_workspace,
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

        header_box.pack_start(menu_button, False, False, 0)
        header_box.pack_start(title, True, True, 0)

        self.widget.pack_start(header_box, False, False, 0)
        self.widget.pack_start(Gtk.Separator(), False, False, 0)

    def _build_workspace_list(self):
        """
        Builds the listbox that will contain the workspaces from the loaded data,
        separating pinned workspaces.
        """
        if hasattr(self, "scrolled_window"):
            self.widget.remove(self.scrolled_window)
            self.scrolled_window.destroy()

        self.workspace_listbox = Gtk.ListBox()
        self.workspace_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.workspace_listbox.connect("row-activated", self.on_workspace_activated)

        all_workspaces = self.workspaces_data.get("workspaces", [])
        pinned_workspaces = sorted(
            [w for w in all_workspaces if w.get("is_pinned")],
            key=lambda w: w.get("updated_at", ""),
            reverse=True,
        )
        unpinned_workspaces = [w for w in all_workspaces if not w.get("is_pinned")]

        if pinned_workspaces:
            pinned_header = Gtk.ListBoxRow()
            pinned_header.set_selectable(False)
            header_label = Gtk.Label(label="📌 Pinned", xalign=0)
            header_label.get_style_context().add_class("dim-label")
            pinned_header.add(header_label)
            self.workspace_listbox.add(pinned_header)

            for ws_data in pinned_workspaces:
                row = self.create_workspace_row(ws_data)
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
            row = self.create_workspace_row(ws_data)
            self.workspace_listbox.add(row)

        # After populating, select the active row
        active_workspace_id = self.workspaces_data.get("active_workspace")
        if active_workspace_id:
            for row in self.workspace_listbox.get_children():
                if row.get_name() == active_workspace_id:
                    self.workspace_listbox.select_row(row)
                    break

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_window.add(self.workspace_listbox)
        self.scrolled_window.show_all()

        self.widget.pack_start(self.scrolled_window, True, True, 0)

    def create_workspace_row(self, ws_data):
        """Creates a Gtk.ListBoxRow for a single workspace."""
        list_box_row = Gtk.ListBoxRow()
        list_box_row.set_name(ws_data["id"])  # Store ID on the widget for later retrieval
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row_box.set_margin_top(4)
        row_box.set_margin_bottom(4)
        row_box.set_margin_start(8)
        row_box.set_margin_end(8)

        add_icon = Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        add_button = Gtk.Button(image=add_icon, relief=Gtk.ReliefStyle.NONE)
        add_button.connect("clicked", self.on_add_terminal_to_workspace, ws_data["id"])

        label_text = f"{ws_data.get('icon', '')} {ws_data['name']}"
        label = Gtk.Label(label=label_text, xalign=0)
        label.set_hexpand(True)

        pin_icon = Gtk.Image.new_from_icon_name("pin-symbolic", Gtk.IconSize.MENU)
        pin_icon.set_visible(ws_data.get("is_pinned", False))

        ws_menu_icon = Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        ws_menu_button = Gtk.MenuButton(image=ws_menu_icon, relief=Gtk.ReliefStyle.NONE)
        ws_menu = Gtk.Menu()
        rename_item = Gtk.MenuItem(label="Rename")
        delete_item = Gtk.MenuItem(label="Delete")
        pin_label = "Unpin" if ws_data.get("is_pinned", False) else "Pin"
        pin_item = Gtk.MenuItem(label=pin_label)

        rename_item.connect("activate", self.on_rename_workspace, ws_data["id"])
        delete_item.connect("activate", self.on_delete_workspace, ws_data["id"])
        pin_item.connect("activate", self.on_pin_workspace, ws_data["id"])

        ws_menu.append(rename_item)
        ws_menu.append(delete_item)
        ws_menu.append(pin_item)
        ws_menu.show_all()
        ws_menu_button.set_popup(ws_menu)

        row_box.pack_start(add_button, False, False, 0)
        row_box.pack_start(label, True, True, 0)
        row_box.pack_start(pin_icon, False, False, 0)
        row_box.pack_start(ws_menu_button, False, False, 0)
        list_box_row.add(row_box)

        return list_box_row

    def on_workspace_activated(self, listbox, row):
        """Handles the activation of a workspace from the sidebar."""
        workspace_id = row.get_name()
        if not workspace_id:  # Ignore headers or separators
            return

        log.info("Activating workspace %s", workspace_id)
        ws = next((w for w in self.workspaces_data["workspaces"] if w["id"] == workspace_id), None)
        if not ws:
            return

        self.workspaces_data["active_workspace"] = workspace_id
        self.save_workspaces()

        # TODO: This logic needs to be fully implemented in guake_app.py
        # For now, this is a conceptual implementation.
        # 1. Tell guake_app to switch to the notebook associated with this workspace.
        # self.guake_app.switch_to_notebook_for_workspace(workspace_id)

        # 2. Focus the active terminal or create a new one.
        active_terminal_id = ws.get("active_terminal")
        if active_terminal_id:
            log.debug("Focusing active terminal: %s", active_terminal_id)
            # success = self.guake_app.focus_terminal_by_uuid(active_terminal_id)
            # if not success:
            #     self.guake_app.add_tab() # And update active_terminal
        else:
            log.debug("No active terminal, creating a new one.")
            # new_term = self.guake_app.add_tab(return_terminal=True)
            # if new_term:
            #     ws["active_terminal"] = new_term.uuid
            #     self.save_workspaces()
        listbox.select_row(row)

    def on_add_workspace(self, action, param):
        """Adds a new workspace to the data and UI."""
        log.info("Add workspace action triggered.")
        settings = self.workspaces_data["settings"]
        new_ws = {
            "id": str(uuid.uuid4()),
            "name": settings["default_workspace_name"],
            "terminals": [],
            "tags": {},
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "icon": settings["default_workspace_icon"],
            "color_bg": settings["default_workspace_color_bg"],
            "color_fg": settings["default_workspace_color_fg"],
            "is_pinned": False,
            "active_terminal": None,
        }
        self.workspaces_data["workspaces"].append(new_ws)
        self.save_workspaces()
        self._build_workspace_list()

    def on_add_terminal_to_workspace(self, button, workspace_id):
        """Callback to add a new terminal tab to a specific workspace."""
        log.info("Adding new terminal to workspace %s", workspace_id)
        # This needs to be coordinated with Guake's main logic
        self.guake_app.add_tab()

    def on_rename_workspace(self, menu_item, workspace_id):
        """Opens a dialog to rename the workspace and change its icon."""
        ws = next((w for w in self.workspaces_data["workspaces"] if w["id"] == workspace_id), None)
        if not ws:
            return

        dialog = Gtk.Dialog(
            title="Edit Workspace",
            parent=self.guake_app.window,
            flags=0,
            buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK),
        )
        dialog.set_default_size(350, 100)
        content_area = dialog.get_content_area()
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin=10)
        content_area.add(grid)

        # Name entry
        name_label = Gtk.Label(label="Name:", xalign=0)
        name_entry = Gtk.Entry(text=ws["name"])
        name_entry.connect("activate", lambda _: dialog.response(Gtk.ResponseType.OK))

        # Icon entry
        icon_label = Gtk.Label(label="Icon:", xalign=0)
        icon_entry = Gtk.Entry(text=ws.get("icon", ""))
        icon_entry.set_max_length(2)  # For single emoji/character

        # Add red border style for validation
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b".error { border: 1px solid red; border-radius: 4px; }")
        name_entry.get_style_context().add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        name_entry.connect("changed", lambda entry: entry.get_style_context().remove_class("error"))

        grid.attach(name_label, 0, 0, 1, 1)
        grid.attach(name_entry, 1, 0, 1, 1)
        grid.attach(icon_label, 0, 1, 1, 1)
        grid.attach(icon_entry, 1, 1, 1, 1)

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

    def on_delete_workspace(self, menu_item, workspace_id):
        """Opens a confirmation dialog to delete the workspace."""
        ws = next((w for w in self.workspaces_data["workspaces"] if w["id"] == workspace_id), None)
        if not ws:
            return

        dialog = Gtk.MessageDialog(
            parent=self.guake_app.window,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Are you sure you want to delete '{ws['name']}'?",
        )
        response = dialog.run()
        if response == Gtk.ResponseType.YES:
            self.workspaces_data["workspaces"] = [
                w for w in self.workspaces_data["workspaces"] if w["id"] != workspace_id
            ]
            self.save_workspaces()
            self._build_workspace_list()
        dialog.destroy()

    def on_pin_workspace(self, menu_item, workspace_id):
        """Toggles the pinned state of a workspace."""
        ws = next((w for w in self.workspaces_data["workspaces"] if w["id"] == workspace_id), None)
        if ws:
            ws["is_pinned"] = not ws.get("is_pinned", False)
            ws["updated_at"] = datetime.utcnow().isoformat() + "Z"
            self.save_workspaces()
            self._build_workspace_list()
