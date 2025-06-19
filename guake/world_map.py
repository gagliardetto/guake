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
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

log = logging.getLogger(__name__)


class WorldMapView(Gtk.ScrolledWindow):
    """
    A view that displays terminals in a grid layout for visual organization.
    """

    def __init__(self, guake_app):
        """
        Initializes the World Map view.
        :param guake_app: The main Guake application instance.
        """
        super().__init__()
        self.guake_app = guake_app
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.grid = Gtk.Grid()
        self.grid.set_column_spacing(15)
        self.grid.set_row_spacing(15)
        self.grid.set_margin_top(15)
        self.grid.set_margin_bottom(15)
        self.grid.set_margin_start(15)
        self.grid.set_margin_end(15)

        self.add(self.grid)

        log.info("WorldMapView initialized")

    def populate_map(self):
        """
        Clears the grid and repopulates it with previews of all open terminals.
        This function is called each time the map is shown to ensure it's up-to-date.
        """
        # Clear existing children from the grid
        for child in self.grid.get_children():
            self.grid.remove(child)

        log.debug("Populating World Map with terminal previews.")
        notebook = self.guake_app.get_notebook()
        
        # This will be a list of tuples: [(terminal_widget, page_index), ...]
        # to correctly handle split terminals within a single tab.
        terminals_with_pages = []
        for i in range(notebook.get_n_pages()):
             page = notebook.get_nth_page(i)
             # A page (tab) can contain multiple terminals if it's split.
             # We iterate through them and add each one to our list.
             for term in page.iter_terminals():
                 terminals_with_pages.append((term, i))


        # Define grid dimensions (e.g., 4 columns, can be made configurable)
        cols = 4
        for i, (terminal, page_num) in enumerate(terminals_with_pages):
            # We pass the page_num so we know which tab to switch back to.
            preview = self._create_terminal_preview(terminal, page_num)
            row, col = divmod(i, cols)
            self.grid.attach(preview, col, row, 1, 1)

        self.show_all()

    def _create_terminal_preview(self, terminal, page_num):
        """
        Creates a clickable preview widget for a single terminal.
        :param terminal: The Vte.Terminal widget.
        :param page_num: The page number (index) of the terminal's tab.
        :return: A Gtk.Widget representing the preview.
        """
        notebook = self.guake_app.get_notebook()
        # Get the title from the tab, not the individual terminal
        title = notebook.get_tab_label_text(notebook.get_nth_page(page_num))
        if not title:
             title = f"Terminal {page_num + 1}"

        button = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(5)
        box.set_margin_bottom(5)
        box.set_margin_start(5)
        box.set_margin_end(5)

        label = Gtk.Label.new(title)
        label.set_ellipsize(True)

        # Placeholder for the actual terminal preview.
        # This is where we will later implement snapshotting the terminal content.
        preview_area = Gtk.Frame()
        preview_area.set_shadow_type(Gtk.ShadowType.IN)
        preview_area.set_size_request(220, 130)
        
        try:
            cwd = terminal.get_current_directory()
        except:
            cwd = "N/A" # Handle cases where directory might not be available
            
        preview_label = Gtk.Label.new(f"Preview of Tab {page_num + 1}\nCWD: {cwd}")
        preview_label.set_justify(Gtk.Justification.CENTER)
        preview_label.set_line_wrap(True)
        preview_area.add(preview_label)

        box.pack_start(label, False, False, 0)
        box.pack_start(preview_area, True, True, 0)

        button.add(box)
        button.connect("clicked", self.on_preview_clicked, page_num)

        return button

    def on_preview_clicked(self, widget, page_num):
        """
        Handles a click on a terminal preview.
        It switches to the corresponding tab and toggles back to the main view.
        :param widget: The Gtk.Button that was clicked.
        :param page_num: The page number to switch to.
        """
        log.debug(f"Terminal preview for page {page_num} clicked. Switching view.")

        # Switch to the selected tab in the notebook
        self.guake_app.get_notebook().set_current_page(page_num)

        # Re-call the accelerator function to toggle the view back to the notebook
        self.guake_app.accel_world_map_navigation()

