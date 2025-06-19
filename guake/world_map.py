# -*- coding: utf-8; -*-
"""
Copyright (C) 2024 Your Name <your.email@example.com>

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License as
published by the Free Software Foundation; either version 2 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.
"""
import logging
import json
import os
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

log = logging.getLogger(__name__)


class WorldMapView(Gtk.ScrolledWindow):
    """
    A view that displays terminals in a grid layout for visual organization.
    Supports named divisions and drag-and-drop for terminals.
    """

    def __init__(self, guake_app):
        """
        Initializes the World Map view.
        :param guake_app: The main Guake application instance.
        """
        super().__init__()
        self.guake_app = guake_app
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.layout_file = self.guake_app.get_xdg_config_directory() / "world_map.json"
        self._load_layout()

        # Allow the widget to receive focus and handle key events.
        self.set_can_focus(True)
        self.connect("key-press-event", self.on_key_press)

        # The main container is a box that will hold division frames vertically
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.main_box.set_margin_top(20)
        self.main_box.set_margin_bottom(20)
        self.main_box.set_margin_start(20)
        self.main_box.set_margin_end(20)
        self.add(self.main_box)

        log.info("WorldMapView initialized")

    def _load_layout(self):
        """Loads the division and terminal layout from a JSON file."""
        if self.layout_file.exists():
            try:
                with open(self.layout_file, "r", encoding="utf-8") as f:
                    self.layout = json.load(f)
                log.info("World Map layout loaded from %s", self.layout_file)
                return
            except (json.JSONDecodeError, IOError) as e:
                log.error("Failed to load world map layout: %s", e)
        
        # Default layout if file doesn't exist or is invalid
        self.layout = {"divisions": [{"title": "Uncategorized", "terminals": []}]}

    def _save_layout(self):
        """Saves the current layout to a JSON file."""
        try:
            with open(self.layout_file, "w", encoding="utf-8") as f:
                json.dump(self.layout, f, indent=4)
            log.debug("World Map layout saved.")
        except IOError as e:
            log.error("Failed to save world map layout: %s", e)

    def _synchronize_layout(self, current_terminal_uuids):
        """
        Ensures the layout is consistent with currently open terminals.
        - Removes closed terminals from the layout.
        - Adds new, untracked terminals to the 'Uncategorized' division.
        """
        all_layout_uuids = set()
        uncategorized_division = None

        # Find the 'Uncategorized' division and collect all UUIDs in the layout
        for division in self.layout["divisions"]:
            if division["title"] == "Uncategorized":
                uncategorized_division = division
            
            # Use a list comprehension to filter out non-existent terminals
            terminals_in_division = [uuid for uuid in division["terminals"] if uuid in current_terminal_uuids]
            division["terminals"] = terminals_in_division
            all_layout_uuids.update(terminals_in_division)

        # If no 'Uncategorized' division exists, create one
        if uncategorized_division is None:
            uncategorized_division = {"title": "Uncategorized", "terminals": []}
            self.layout["divisions"].append(uncategorized_division)

        # Add any new terminals to the 'Uncategorized' list
        new_uuids = current_terminal_uuids - all_layout_uuids
        if new_uuids:
            uncategorized_division["terminals"].extend(list(new_uuids))
            log.debug("Added new terminals to Uncategorized: %s", new_uuids)

    def populate_map(self):
        """
        Clears and rebuilds the entire world map view based on the current layout.
        """
        # 1. Get all current terminals and create a lookup map
        all_terminals_map = {}
        notebook = self.guake_app.get_notebook()
        for i in range(notebook.get_n_pages()):
             page = notebook.get_nth_page(i)
             for term in page.iter_terminals():
                 all_terminals_map[str(term.uuid)] = (term, i)

        # 2. Sync layout with currently running terminals
        self._synchronize_layout(set(all_terminals_map.keys()))

        # 3. Rebuild the UI from scratch
        for child in self.main_box.get_children():
            self.main_box.remove(child)

        DND_TARGET = [Gtk.TargetEntry.new('text/plain', Gtk.TargetFlags.SAME_APP, 0)]

        for division in self.layout["divisions"]:
            division_frame = Gtk.Frame()
            division_frame.set_label(division["title"])
            division_frame.set_label_align(0.05, 0.5)
            division_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
            self.main_box.pack_start(division_frame, False, False, 0)

            grid = Gtk.Grid(column_spacing=15, row_spacing=15, margin=15)
            division_frame.add(grid)
            
            # Make the grid a drop target
            grid.drag_dest_set(Gtk.DestDefaults.ALL, DND_TARGET, Gdk.DragAction.MOVE)
            grid.connect("drag-data-received", self.on_drag_data_received, division)

            cols = 4
            for i, term_uuid in enumerate(division["terminals"]):
                if term_uuid in all_terminals_map:
                    terminal, page_num = all_terminals_map[term_uuid]
                    preview = self._create_terminal_preview(terminal, page_num)
                    row, col = divmod(i, cols)
                    grid.attach(preview, col, row, 1, 1)

        self.show_all()

    def _create_terminal_preview(self, terminal, page_num):
        """
        Creates a clickable, draggable preview widget for a single terminal.
        """
        notebook = self.guake_app.get_notebook()
        title = notebook.get_tab_label_text(notebook.get_nth_page(page_num)) or f"Terminal {page_num + 1}"

        button = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=5)
        button.add(box)
        button.connect("clicked", self.on_preview_clicked, terminal, page_num)

        # Make the button a drag source
        DND_TARGET = [Gtk.TargetEntry.new('text/plain', Gtk.TargetFlags.SAME_APP, 0)]
        button.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, DND_TARGET, Gdk.DragAction.MOVE)
        button.connect("drag-data-get", self.on_drag_data_get, str(terminal.uuid))

        label = Gtk.Label.new(title)
        label.set_ellipsize(True)

        preview_area = Gtk.Frame(shadow_type=Gtk.ShadowType.IN)
        preview_area.set_size_request(220, 130)
        
        try:
            cwd = terminal.get_current_directory()
        except:
            cwd = "N/A"
            
        preview_label = Gtk.Label(label=f"Preview of Tab {page_num + 1}\nCWD: {cwd}", justify=Gtk.Justification.CENTER, wrap=True)
        preview_area.add(preview_label)

        box.pack_start(label, False, False, 0)
        box.pack_start(preview_area, True, True, 0)

        return button

    def on_drag_data_get(self, widget, context, selection_data, info, timestamp, terminal_uuid):
        """Provides the UUID of the terminal being dragged."""
        selection_data.set_text(terminal_uuid, -1)
        log.debug(f"Drag started for terminal UUID: {terminal_uuid}")

    def on_drag_data_received(self, widget, context, x, y, selection_data, info, timestamp, target_division):
        """Handles a terminal preview being dropped onto a division grid."""
        dropped_uuid = selection_data.get_text()
        if not dropped_uuid:
            return

        log.debug(f"Terminal {dropped_uuid} dropped on division '{target_division['title']}'")

        # Find and remove the UUID from its old division
        source_division_title = ""
        for division in self.layout["divisions"]:
            if dropped_uuid in division["terminals"]:
                source_division_title = division["title"]
                division["terminals"].remove(dropped_uuid)
                break
        
        # Add the UUID to the new division, if it's actually a different division
        if source_division_title != target_division["title"]:
            target_division["terminals"].append(dropped_uuid)

        # Save the new layout and refresh the entire view
        self._save_layout()
        self.populate_map()

    def on_preview_clicked(self, widget, terminal, page_num):
        """Handles a click on a terminal preview."""
        log.debug(f"Terminal preview for page {page_num} clicked. Switching view.")
        self.guake_app.get_notebook().set_current_page(page_num)
        self.guake_app.accel_world_map_navigation()
        terminal.grab_focus()

    def on_key_press(self, widget, event):
        """Closes the view if the Escape key is pressed."""
        if event.keyval == Gdk.KEY_Escape:
            log.debug("Escape key pressed, closing World Map View.")
            self.guake_app.accel_world_map_navigation()
            return True
        return False
