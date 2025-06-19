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

        # Apply CSS for opaque background and drop highlighting
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .world-map-view {
                background-color: @theme_base_color;
            }
            .drop-highlight {
                background-color: @theme_selected_bg_color;
            }
        """)
        self.get_style_context().add_class("world-map-view")
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.layout_file = self.guake_app.get_xdg_config_directory() / "world_map.json"
        self._load_layout()

        # Allow the widget to receive focus and handle key events.
        self.set_can_focus(True)
        self.connect("key-press-event", self.on_key_press)

        # --- Main Layout Structure ---
        # A vertical box to hold the header and the main content area
        self.root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self.root_box)

        # Header bar with "New Division" button
        header_bar = Gtk.Box(spacing=6, margin=10)
        self.root_box.pack_start(header_bar, False, False, 0)
        new_division_button = Gtk.Button.new_with_label("New Division")
        new_division_button.connect("clicked", self.on_new_division_clicked)
        header_bar.pack_start(new_division_button, False, False, 0)
        
        # main_box will contain all the division frames
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin=20)
        self.root_box.pack_start(self.main_box, True, True, 0)

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

        for division in self.layout["divisions"]:
            if division["title"] == "Uncategorized":
                uncategorized_division = division
            
            terminals_in_division = [uuid for uuid in division["terminals"] if uuid in current_terminal_uuids]
            division["terminals"] = terminals_in_division
            all_layout_uuids.update(terminals_in_division)

        if uncategorized_division is None:
            uncategorized_division = {"title": "Uncategorized", "terminals": []}
            self.layout["divisions"].append(uncategorized_division)

        new_uuids = current_terminal_uuids - all_layout_uuids
        if new_uuids:
            uncategorized_division["terminals"].extend(list(new_uuids))
            log.debug("Added new terminals to Uncategorized: %s", new_uuids)

    def populate_map(self):
        """
        Clears and rebuilds the entire world map view based on the current layout.
        """
        all_terminals_map = {}
        notebook = self.guake_app.get_notebook()
        for i in range(notebook.get_n_pages()):
             page = notebook.get_nth_page(i)
             for term in page.iter_terminals():
                 all_terminals_map[str(term.uuid)] = (term, i)

        self._synchronize_layout(set(all_terminals_map.keys()))

        for child in self.main_box.get_children():
            self.main_box.remove(child)

        DND_TARGET = [Gtk.TargetEntry.new('text/plain', Gtk.TargetFlags.SAME_APP, 0)]

        for division in self.layout["divisions"]:
            division_frame = Gtk.Frame(shadow_type=Gtk.ShadowType.ETCHED_IN)

            # --- Custom Frame Header with Menu ---
            frame_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=division["title"])
            frame_header.pack_start(label, True, True, 0)
            
            menu_button = Gtk.MenuButton()
            popover = Gtk.Popover()
            menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            popover.add(menu_box)
            menu_button.set_popover(popover)

            rename_button = Gtk.ModelButton(label="Rename")
            rename_button.connect("clicked", self.on_rename_division_clicked, division)
            menu_box.pack_start(rename_button, True, True, 0)
            frame_header.pack_end(menu_button, False, False, 0)
            division_frame.set_label_widget(frame_header)
            
            self.main_box.pack_start(division_frame, False, False, 0)

            grid = Gtk.Grid(column_spacing=15, row_spacing=15, margin=15)
            division_frame.add(grid)
            
            grid.drag_dest_set(Gtk.DestDefaults.ALL, DND_TARGET, Gdk.DragAction.MOVE)
            grid.connect("drag-data-received", self.on_grid_drop, division)

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

        DND_TARGET = [Gtk.TargetEntry.new('text/plain', Gtk.TargetFlags.SAME_APP, 0)]
        button.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, DND_TARGET, Gdk.DragAction.MOVE)
        button.connect("drag-data-get", self.on_drag_data_get, str(terminal.uuid))

        button.drag_dest_set(Gtk.DestDefaults.ALL, DND_TARGET, Gdk.DragAction.MOVE)
        button.connect("drag-motion", self.on_item_drag_motion)
        button.connect("drag-leave", self.on_item_drag_leave)
        button.connect("drag-data-received", self.on_item_drop, str(terminal.uuid))

        label = Gtk.Label.new(title)
        label.set_ellipsize(True)

        preview_area = Gtk.Frame(shadow_type=Gtk.ShadowType.IN)
        preview_area.set_size_request(220, 130)
        
        try:
            cwd = terminal.get_current_directory()
        except:
            cwd = "N/A"
        preview_label = Gtk.Label(label=f"CWD: {cwd}", justify=Gtk.Justification.CENTER, wrap=True)
        preview_area.add(preview_label)

        box.pack_start(label, False, False, 0)
        box.pack_start(preview_area, True, True, 0)

        return button

    def _show_text_input_dialog(self, title, message, current_text=""):
        """Helper to show a dialog that asks for text input."""
        dialog = Gtk.Dialog(title=title, transient_for=self.guake_app.window, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)

        content_area = dialog.get_content_area()
        content_area.set_spacing(10)
        content_area.set_margin_top(10)
        content_area.set_margin_bottom(10)
        content_area.set_margin_start(10)
        content_area.set_margin_end(10)
        
        label = Gtk.Label(label=message)
        content_area.add(label)

        entry = Gtk.Entry()
        entry.set_text(current_text)
        entry.set_activates_default(True)
        content_area.add(entry)
        
        dialog.set_default(dialog.get_widget_for_response(Gtk.ResponseType.OK))
        dialog.show_all()
        
        response = dialog.run()
        text = entry.get_text()
        dialog.destroy()

        if response == Gtk.ResponseType.OK and text:
            return text
        return None

    def on_new_division_clicked(self, widget):
        """Handles the 'New Division' button click."""
        new_title = self._show_text_input_dialog("Create New Division", "Enter the name for the new division:")
        if new_title:
            self.layout["divisions"].append({"title": new_title, "terminals": []})
            self._save_layout()
            self.populate_map()

    def on_rename_division_clicked(self, widget, division):
        """Handles the 'Rename' menu item click for a division."""
        new_title = self._show_text_input_dialog("Rename Division", "Enter the new name:", division["title"])
        if new_title:
            division["title"] = new_title
            self._save_layout()
            self.populate_map()

    def on_drag_data_get(self, widget, context, selection_data, info, timestamp, terminal_uuid):
        """Provides the UUID of the terminal being dragged."""
        selection_data.set_text(terminal_uuid, -1)

    def on_item_drag_motion(self, widget, context, x, y, timestamp):
        """Adds a highlight class when dragging over a valid drop target."""
        widget.get_style_context().add_class('drop-highlight')
        return False

    def on_item_drag_leave(self, widget, context, timestamp):
        """Removes the highlight class when the drag leaves a target."""
        widget.get_style_context().remove_class('drop-highlight')
    
    def _move_terminal_in_layout(self, dragged_uuid, target_division, target_index=None):
        """Helper function to move a terminal's UUID in the layout data structure."""
        if not dragged_uuid: return

        # Remove from old position
        for division in self.layout['divisions']:
            if dragged_uuid in division['terminals']:
                division['terminals'].remove(dragged_uuid)
                break
        
        # Add to new position
        if target_index is not None:
            target_division['terminals'].insert(target_index, dragged_uuid)
        else: # Append to the end
            target_division['terminals'].append(dragged_uuid)
        
        self._save_layout()
        self.populate_map()

    def on_grid_drop(self, widget, context, x, y, selection_data, info, timestamp, target_division):
        """Handles dropping a terminal onto the empty space of a division grid."""
        dragged_uuid = selection_data.get_text()
        self._move_terminal_in_layout(dragged_uuid, target_division)

    def on_item_drop(self, widget, context, x, y, selection_data, info, timestamp, target_uuid):
        """Handles dropping a terminal onto another terminal preview."""
        self.on_item_drag_leave(widget, context, timestamp) # Remove highlight
        dragged_uuid = selection_data.get_text()

        if not dragged_uuid or dragged_uuid == target_uuid: return

        # Find the target division and index
        target_division = None
        target_idx = -1
        for d in self.layout['divisions']:
            if target_uuid in d['terminals']:
                target_division = d
                target_idx = d['terminals'].index(target_uuid)
                break
        
        if target_division:
            self._move_terminal_in_layout(dragged_uuid, target_division, target_idx)

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
