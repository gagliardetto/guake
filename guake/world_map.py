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
import re
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

log = logging.getLogger(__name__)


class WorldMapView(Gtk.ScrolledWindow):
    """
    A view that displays terminals in a grid layout for visual organization.
    Supports named projects, tags, filtering, and drag-and-drop.
    """

    def __init__(self, guake_app):
        """
        Initializes the World Map view.
        :param guake_app: The main Guake application instance.
        """
        super().__init__()
        self.guake_app = guake_app
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .world-map-view { background-color: @theme_base_color; }
            .drop-highlight { background-color: @theme_selected_bg_color; }
            .empty-project-grid { min-height: 80px; }
        """)
        self.get_style_context().add_class("world-map-view")
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.layout_file = self.guake_app.get_xdg_config_directory() / "world_map.json"
        self._load_layout()

        self.set_can_focus(True)
        self.connect("key-press-event", self.on_key_press)

        self.root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self.root_box)

        header_bar = Gtk.Box(spacing=6, margin=10)
        self.root_box.pack_start(header_bar, False, False, 0)
        
        new_project_button = Gtk.Button.new_with_label("New Project")
        new_project_button.connect("clicked", self.on_new_project_clicked)
        header_bar.pack_start(new_project_button, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Filter by name, CWD, or tag:value...")
        self.search_entry.connect("search-changed", self.on_filter_changed)
        header_bar.pack_start(self.search_entry, True, True, 0)
        
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin=20)
        self.root_box.pack_start(self.main_box, True, True, 0)

        log.info("WorldMapView initialized")

    def _load_layout(self):
        """Loads the project and terminal layout from a JSON file."""
        load_success = False
        if self.layout_file.exists():
            try:
                with open(self.layout_file, "r", encoding="utf-8") as f:
                    self.layout = json.load(f)
                
                # Migration from old 'divisions' key to 'projects'
                if "divisions" in self.layout and "projects" not in self.layout:
                    self.layout["projects"] = self.layout.pop("divisions")
                    log.info("Migrated layout from 'divisions' to 'projects'.")

                # Validate that the loaded layout has the required structure
                if "projects" in self.layout and isinstance(self.layout["projects"], list):
                    # Ensure all project dictionaries have the necessary keys for safety
                    for p in self.layout.get("projects", []):
                        p.setdefault("tags", {})
                        p.setdefault("terminals", [])
                    log.info("World Map layout loaded from %s", self.layout_file)
                    load_success = True
                else:
                    log.warning("Layout file %s is malformed. Resetting to default.", self.layout_file)

            except (json.JSONDecodeError, IOError) as e:
                log.error("Failed to load or parse world map layout: %s. Resetting to default.", e)
        
        if not load_success:
            # Create a default layout if loading failed or file doesn't exist
            self.layout = {"projects": [{"title": "Uncategorized", "terminals": [], "tags": {}}]}


    def _save_layout(self):
        """Saves the current layout to a JSON file."""
        try:
            with open(self.layout_file, "w", encoding="utf-8") as f:
                json.dump(self.layout, f, indent=4)
            log.debug("World Map layout saved.")
        except IOError as e:
            log.error("Failed to save world map layout: %s", e)

    def _synchronize_layout(self, current_terminals_map):
        """Ensures the layout is consistent with currently open terminals."""
        current_terminal_uuids = set(current_terminals_map.keys())
        all_layout_uuids = set()
        uncategorized_project = None

        for project in self.layout["projects"]:
            if project["title"] == "Uncategorized":
                uncategorized_project = project
            
            project["terminals"] = [uuid for uuid in project["terminals"] if uuid in current_terminal_uuids]
            all_layout_uuids.update(project["terminals"])

        if uncategorized_project is None:
            uncategorized_project = {"title": "Uncategorized", "terminals": [], "tags": {}}
            self.layout["projects"].append(uncategorized_project)

        new_uuids = current_terminal_uuids - all_layout_uuids
        if new_uuids:
            uncategorized_project["terminals"].extend(list(new_uuids))
            log.debug("Added new terminals to Uncategorized: %s", new_uuids)

    def _filter_projects(self, filter_text, all_terminals_map):
        """Filters projects and their terminals based on search query."""
        if not filter_text:
            return self.layout["projects"]

        filtered_projects = []
        # Simple parser for 'key:value' or '#tag'
        tag_match = re.search(r'(tag:|#)(\w+)(?::(\S+))?', filter_text)
        
        for project in self.layout["projects"]:
            matched_terminals = []
            
            if tag_match:
                tag_key = tag_match.group(2)
                tag_value = tag_match.group(3) if tag_match.group(3) else '*'
                
                project_tags = project.get('tags', {})
                if tag_key in project_tags:
                    if tag_value == '*' or project_tags[tag_key] == tag_value:
                         matched_terminals = project["terminals"]

            else: # General text search
                for uuid in project["terminals"]:
                    if uuid in all_terminals_map:
                        terminal, page_num = all_terminals_map[uuid]
                        notebook = self.guake_app.get_notebook()
                        title = notebook.get_tab_label_text(notebook.get_nth_page(page_num)) or ""
                        try:
                            cwd = terminal.get_current_directory() or ""
                        except Exception:
                            cwd = ""

                        if (filter_text.lower() in project['title'].lower() or
                            filter_text.lower() in title.lower() or
                            filter_text.lower() in cwd.lower()):
                            matched_terminals.append(uuid)

            if matched_terminals or (not tag_match and filter_text.lower() in project['title'].lower()):
                # If the project title itself matches, show all its terminals
                terminals_to_show = project["terminals"] if not tag_match and filter_text.lower() in project['title'].lower() else matched_terminals
                filtered_projects.append({**project, "terminals": terminals_to_show})

        return filtered_projects

    def on_filter_changed(self, search_entry):
        """Callback for when the search text changes."""
        self.populate_map()

    def populate_map(self):
        """Clears and rebuilds the entire world map view based on the current layout and filters."""
        all_terminals_map = {}
        notebook = self.guake_app.get_notebook()
        for i in range(notebook.get_n_pages()):
             page = notebook.get_nth_page(i)
             for term in page.iter_terminals():
                 all_terminals_map[str(term.uuid)] = (term, i)

        self._synchronize_layout(all_terminals_map)
        
        filter_text = self.search_entry.get_text()
        visible_projects = self._filter_projects(filter_text, all_terminals_map)

        for child in self.main_box.get_children():
            self.main_box.remove(child)

        DND_TARGET = [Gtk.TargetEntry.new('text/plain', Gtk.TargetFlags.SAME_APP, 0)]

        for project in visible_projects:
            project_frame = Gtk.Frame(shadow_type=Gtk.ShadowType.ETCHED_IN)

            frame_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            
            event_box = Gtk.EventBox()
            label = Gtk.Label(label=project["title"])
            event_box.add(label)
            event_box.connect("button-press-event", self.on_project_label_clicked, project)
            frame_header.pack_start(event_box, True, True, 0)
            
            menu_button = Gtk.MenuButton()
            popover = Gtk.Popover()
            menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            popover.add(menu_box)
            menu_button.set_popover(popover)

            rename_button = Gtk.ModelButton(label="Rename")
            rename_button.connect("clicked", self.on_rename_project_clicked, project)
            menu_box.pack_start(rename_button, True, True, 0)
            frame_header.pack_end(menu_button, False, False, 0)
            project_frame.set_label_widget(frame_header)
            
            self.main_box.pack_start(project_frame, False, False, 0)

            grid = Gtk.Grid(column_spacing=15, row_spacing=15, margin=15)
            if not project["terminals"]:
                grid.get_style_context().add_class("empty-project-grid")

            project_frame.add(grid)
            
            grid.drag_dest_set(Gtk.DestDefaults.ALL, DND_TARGET, Gdk.DragAction.MOVE)
            grid.connect("drag-data-received", self.on_grid_drop, project)

            cols = 4
            for i, term_uuid in enumerate(project["terminals"]):
                if term_uuid in all_terminals_map:
                    terminal, page_num = all_terminals_map[term_uuid]
                    preview = self._create_terminal_preview(terminal, page_num)
                    row, col = divmod(i, cols)
                    grid.attach(preview, col, row, 1, 1)

        self.show_all()

    def _create_terminal_preview(self, terminal, page_num):
        notebook = self.guake_app.get_notebook()
        title = notebook.get_tab_label_text(notebook.get_nth_page(page_num)) or f"Terminal {page_num + 1}"
        button = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=5)
        button.add(box)
        
        # Use 'clicked' for the primary (left-click) action.
        # This signal is only emitted after a press AND release, and is
        # ignored if a drag operation is started.
        button.connect("clicked", self.on_preview_clicked, terminal, page_num)
        
        # Use 'button-press-event' ONLY for the secondary (right-click) action.
        button.connect("button-press-event", self.on_preview_right_click, terminal)
        
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

    def on_project_label_clicked(self, widget, event, project):
        """Handles clicks on the project title label."""
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self.on_rename_project_clicked(widget, project)
            return True
        return False

    def on_new_project_clicked(self, widget):
        new_title = self._show_text_input_dialog("Create New Project", "Enter the name for the new project:")
        if new_title:
            self.layout["projects"].append({"title": new_title, "terminals": [], "tags": {}})
            self._save_layout()
            self.populate_map()

    def on_rename_project_clicked(self, widget, project):
        new_title = self._show_text_input_dialog("Rename Project", "Enter the new name:", project["title"])
        if new_title:
            project["title"] = new_title
            self._save_layout()
            self.populate_map()

    def on_drag_data_get(self, widget, context, selection_data, info, timestamp, terminal_uuid):
        selection_data.set_text(terminal_uuid, -1)

    def on_item_drag_motion(self, widget, context, x, y, timestamp):
        widget.get_style_context().add_class('drop-highlight')
        return False

    def on_item_drag_leave(self, widget, context, timestamp):
        widget.get_style_context().remove_class('drop-highlight')
    
    def _move_terminal_in_layout(self, dragged_uuid, target_project, target_index=None):
        if not dragged_uuid: return
        for project in self.layout['projects']:
            if dragged_uuid in project['terminals']:
                project['terminals'].remove(dragged_uuid)
                break
        if target_index is not None:
            target_project['terminals'].insert(target_index, dragged_uuid)
        else:
            target_project['terminals'].append(dragged_uuid)
        self._save_layout()
        self.populate_map()

    def on_grid_drop(self, widget, context, x, y, selection_data, info, timestamp, target_project):
        dragged_uuid = selection_data.get_text()
        self._move_terminal_in_layout(dragged_uuid, target_project)

    def on_item_drop(self, widget, context, x, y, selection_data, info, timestamp, target_uuid):
        self.on_item_drag_leave(widget, context, timestamp)
        dragged_uuid = selection_data.get_text()
        if not dragged_uuid or dragged_uuid == target_uuid: return
        target_project = None
        target_idx = -1
        for p in self.layout['projects']:
            if target_uuid in p['terminals']:
                target_project = p
                target_idx = p['terminals'].index(target_uuid)
                break
        if target_project:
            self._move_terminal_in_layout(dragged_uuid, target_project, target_idx)

    def on_preview_clicked(self, widget, terminal, page_num):
        """Handles a left-click on a terminal preview."""
        log.debug(f"Terminal preview for page {page_num} left-clicked. Switching view.")
        self.guake_app.get_notebook().set_current_page(page_num)
        self.guake_app.accel_world_map_navigation()
        terminal.grab_focus()

    def on_preview_right_click(self, widget, event, terminal):
        """Handles right-clicks on a terminal preview to show a context menu."""
        if event.button == 3:
            log.debug(f"Terminal preview for {terminal.uuid} right-clicked. Showing context menu.")
            self.show_preview_context_menu(widget, event, terminal)
            return True # Consume the right-click event
        return False # Do not consume other events (like left-click)

    def show_preview_context_menu(self, widget, event, terminal):
        """Creates and displays the context menu for a terminal preview."""
        menu = Gtk.Menu()
        
        # Create the "Send to Project" main menu item
        send_to_item = Gtk.MenuItem(label="Send to Project")
        
        # Create the submenu that will be populated with project names
        send_to_submenu = Gtk.Menu()
        send_to_item.set_submenu(send_to_submenu)

        project_titles = self.get_project_titles()

        if not project_titles:
            send_to_item.set_sensitive(False)
        else:
            terminal_uuid = str(terminal.uuid)
            for title in project_titles:
                project_item = Gtk.MenuItem(label=title)
                project_item.connect(
                    "activate",
                    self.on_send_to_project_activated,
                    terminal_uuid, 
                    title
                )
                send_to_submenu.append(project_item)
        
        menu.append(send_to_item)
        menu.show_all()
        menu.popup_at_pointer(event)

    def on_send_to_project_activated(self, widget, terminal_uuid, project_title):
        """Callback that moves a terminal to the selected project."""
        log.debug(f"Sending terminal {terminal_uuid} to project '{project_title}'")
        self.move_terminal_to_project(terminal_uuid, project_title)

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            log.debug("Escape key pressed, closing World Map View.")
            self.guake_app.accel_world_map_navigation()
            return True
        return False
        
    def get_project_titles(self):
        """Returns a list of all project titles."""
        self._load_layout() 
        return [p['title'] for p in self.layout.get('projects', [])]

    def move_terminal_to_project(self, terminal_uuid, target_project_title):
        """Moves a given terminal to a new project."""
        if not terminal_uuid or not target_project_title:
            return

        self._load_layout()

        source_project_title = ""
        for project in self.layout.get("projects", []):
            if terminal_uuid in project.get("terminals", []):
                source_project_title = project["title"]
                project["terminals"].remove(terminal_uuid)
                break
                
        if source_project_title == target_project_title:
            return

        for project in self.layout.get("projects", []):
            if project["title"] == target_project_title:
                # Ensure 'terminals' key exists before trying to append
                if 'terminals' not in project:
                    project['terminals'] = []
                project['terminals'].append(terminal_uuid)
                break
        
        self._save_layout()
        # Repopulate the map to reflect the change visually
        self.populate_map()
        log.info(f"Moved terminal {terminal_uuid} to project '{target_project_title}'")
