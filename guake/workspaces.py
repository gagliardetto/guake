# -*- coding: utf-8; -*-
"""
Manages the sidebar for workspace navigation.
"""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gio, Gdk

import logging

log = logging.getLogger(__name__)


class WorkspaceManager:
    """
    Creates and manages the sidebar widget for workspaces.
    """

    def __init__(self, guake_app):
        """
        Initializes the WorkspaceManager.
        """
        self.guake_app = guake_app
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.widget.get_style_context().add_class("sidebar")

        self._build_header()
        self._build_workspace_list()

    def _build_header(self):
        """
        Builds the header of the sidebar with a title and a menu button.
        """
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_box.set_margin_top(6)
        header_box.set_margin_bottom(6)
        header_box.set_margin_start(6)
        header_box.set_margin_end(6)

        # Hamburger menu button
        menu_icon = Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON)
        menu_button = Gtk.MenuButton(image=menu_icon)

        # Create a popover menu for the button
        menu = Gio.Menu()
        menu.append("Add workspace", "app.add_workspace")
        menu_button.set_menu_model(menu)

        # Define the action for adding a workspace
        add_action = Gio.SimpleAction.new("add_workspace", None)
        add_action.connect("activate", self.on_add_workspace)
        
        # Create an action group, add the action, and insert it into the window
        app_action_group = Gio.SimpleActionGroup()
        app_action_group.add_action(add_action)
        self.guake_app.window.insert_action_group("app", app_action_group)

        title = Gtk.Label(label="Workspaces")
        title.set_halign(Gtk.Align.CENTER)
        title.set_hexpand(True)
        # Add a style class for theming
        title.get_style_context().add_class("sidebar-title")

        header_box.pack_start(menu_button, False, False, 0)
        header_box.pack_start(title, True, True, 0)

        self.widget.pack_start(header_box, False, False, 0)
        self.widget.pack_start(Gtk.Separator(), False, False, 0)

    def _build_workspace_list(self):
        """
        Builds the listbox that will contain the workspaces.
        """
        self.workspace_listbox = Gtk.ListBox()
        self.workspace_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

        # Populate with dummy data for now
        for i in range(5):
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=f"Workspace {i + 1}", xalign=0)
            label.set_margin_top(8)
            label.set_margin_bottom(8)
            label.set_margin_start(12)
            label.set_margin_end(12)
            row.add(label)
            self.workspace_listbox.add(row)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.add(self.workspace_listbox)

        self.widget.pack_start(scrolled_window, True, True, 0)

    def on_add_workspace(self, action, param):
        """
        Placeholder callback for the 'Add workspace' action.
        """
        log.info("Add workspace action triggered.")
        # In a real implementation, this would open a dialog or add a new workspace directly.
