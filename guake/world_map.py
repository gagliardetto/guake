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
import cairo

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

log = logging.getLogger(__name__)


class TerminalMinimap(Gtk.DrawingArea):
    """
    A widget that draws a miniature, text-based preview of a terminal.
    This is much faster than taking a full graphical snapshot.
    """
    def __init__(self, terminal, bg_color, fg_color):
        super().__init__()
        self.terminal = terminal
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.connect("draw", self.on_draw_minimap)
        self.set_size_request(220, 130)

    def get_minimap_row_height(self):
        # A small, efficient font size for the minimap text
        return 4 

    def on_draw_minimap(self, widget, cr):
        """The main drawing function for the minimap, inspired by user's logic."""
        m_width = widget.get_allocated_width()
        m_height = widget.get_allocated_height()
        row_height = self.get_minimap_row_height()

        # Set background
        cr.set_source_rgba(self.bg_color.red, self.bg_color.green, self.bg_color.blue, self.bg_color.alpha)
        cr.paint()
        
        # Set text properties
        cr.set_source_rgba(self.fg_color.red, self.fg_color.green, self.fg_color.blue, 1)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(row_height - 1)

        try:
            terminal_content, _ = self.terminal.get_text(None, None, True)
            if not terminal_content:
                return
        except Exception:
            # Failsafe if terminal is not ready
            return

        t_width_chars = self.terminal.get_column_count()
        if t_width_chars <= 0: return

        # Draw the text line by line
        y_pos = row_height
        for line in terminal_content.split('\n'):
            for i in range(0, len(line), t_width_chars):
                if y_pos > m_height: break
                chunk = line[i:i+t_width_chars]
                cr.move_to(1, y_pos)
                cr.show_text(chunk.replace('\x00', ''))
                y_pos += row_height
            if y_pos > m_height: break
        
        return False


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
            .drop-highlight {
                border: 2px solid #33D17A;
                box-shadow: 0 0 8px alpha(#33D17A, 0.7);
            }
            .empty-project-grid { min-height: 80px; }
            .tag-button, .hashtag-button {
                padding: 2px 6px;
                border-radius: 12px;
                font-size: small;
            }
            .project-frame-collapsed {
                background-color: alpha(@theme_bg_color, 0.5);
                border-radius: 4px;
            }
            .terminal-preview-button {
                border: 1px solid alpha(@theme_fg_color, 0.2);
                background-color: @theme_bg_color;
                box-shadow: 0 1px 3px alpha(black, 0.1);
            }
            .terminal-preview-button:hover {
                background-color: @theme_hover_bg_color;
                border-color: @theme_selected_bg_color;
            }
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

        # Widget bookkeeping for performance
        self.project_widgets = {}
        self.terminal_widgets = {}

        log.info("WorldMapView initialized")

    def _load_layout(self):
        """Loads the project and terminal layout from a JSON file."""
        load_success = False
        if self.layout_file.exists():
            try:
                with open(self.layout_file, "r", encoding="utf-8") as f:
                    self.layout = json.load(f)
                
                if "divisions" in self.layout and "projects" not in self.layout:
                    self.layout["projects"] = self.layout.pop("divisions")
                    log.info("Migrated layout from 'divisions' to 'projects'.")

                if "projects" in self.layout and isinstance(self.layout["projects"], list):
                    for p in self.layout.get("projects", []):
                        p.setdefault("tags", {})
                        p.setdefault("terminals", [])
                        p.setdefault("expanded", True) 
                    log.info("World Map layout loaded from %s", self.layout_file)
                    load_success = True
                else:
                    log.warning("Layout file %s is malformed. Resetting to default.", self.layout_file)

            except (json.JSONDecodeError, IOError) as e:
                log.error("Failed to load or parse world map layout: %s. Resetting to default.", e)
        
        if not load_success:
            self.layout = {"projects": [{"title": "Uncategorized", "terminals": [], "tags": {}, "expanded": True}]}

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
            uncategorized_project = {"title": "Uncategorized", "terminals": [], "tags": {}, "expanded": True}
            self.layout["projects"].append(uncategorized_project)

        new_uuids = current_terminal_uuids - all_layout_uuids
        if new_uuids:
            uncategorized_project["terminals"].extend(list(new_uuids))
            self._save_layout()
            log.debug("Added new terminals to Uncategorized: %s", new_uuids)

    def _filter_projects(self, filter_text, all_terminals_map):
        """Filters projects and their terminals based on search query."""
        if not filter_text:
            return self.layout["projects"]

        filtered_projects = []
        lower_filter = filter_text.lower()
        tag_match = re.search(r'(tag:|#)(\w+)(?::(\S+))?', filter_text)
        
        for project in self.layout["projects"]:
            matched_terminals = []
            
            project_tags = project.get('tags', {})
            project_title_match = lower_filter in project['title'].lower()
            tag_content_match = any(lower_filter in f"{k}:{v}".lower() for k, v in project_tags.items())

            if tag_match:
                tag_key, tag_value = tag_match.group(2), tag_match.group(3) or '*'
                if tag_key in project_tags and (tag_value == '*' or project_tags[tag_key] == tag_value):
                    matched_terminals = project["terminals"]
            else:
                for uuid in project["terminals"]:
                    if uuid in all_terminals_map:
                        terminal, page_num = all_terminals_map[uuid]
                        notebook = self.guake_app.get_notebook()
                        title = (notebook.get_tab_label_text(notebook.get_nth_page(page_num)) or "").lower()
                        try:
                            cwd = (terminal.get_current_directory() or "").lower()
                        except Exception:
                            cwd = ""
                        if (project_title_match or tag_content_match or
                            lower_filter in title or lower_filter in cwd):
                            matched_terminals.append(uuid)
            
            if matched_terminals or project_title_match or tag_content_match:
                terminals_to_show = project["terminals"] if project_title_match or tag_content_match and not matched_terminals else matched_terminals
                filtered_projects.append({**project, "terminals": terminals_to_show})

        return filtered_projects

    def on_filter_changed(self, search_entry):
        self.populate_map()

    def populate_map(self):
        """Clears and rebuilds the entire world map view based on the current layout and filters."""
        self.project_widgets = {}
        self.terminal_widgets = {}

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
            is_expanded = project.get("expanded", True)
            project_frame = Gtk.Frame(shadow_type=Gtk.ShadowType.ETCHED_IN)
            if not is_expanded:
                project_frame.get_style_context().add_class("project-frame-collapsed")

            project_frame.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, DND_TARGET, Gdk.DragAction.MOVE)
            project_frame.connect("drag-data-get", self.on_project_drag_data_get, project)
            project_frame.drag_dest_set(Gtk.DestDefaults.ALL, DND_TARGET, Gdk.DragAction.MOVE)
            project_frame.connect("drag-data-received", self.on_any_drop_on_project_frame, project)
            project_frame.connect("drag-motion", self.on_item_drag_motion)
            project_frame.connect("drag-leave", self.on_item_drag_leave)
            
            self.main_box.pack_start(project_frame, False, False, 0)
            
            frame_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=5)
            project_frame.add(frame_vbox)

            frame_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            frame_vbox.pack_start(frame_header, False, False, 0)
            
            arrow_icon = "pan-down-symbolic" if is_expanded else "pan-end-symbolic"
            expander_button = Gtk.Button.new_from_icon_name(arrow_icon, Gtk.IconSize.BUTTON)
            expander_button.set_relief(Gtk.ReliefStyle.NONE)
            expander_button.connect("clicked", self.on_toggle_expand_clicked, project['title'])
            frame_header.pack_start(expander_button, False, False, 0)

            event_box = Gtk.EventBox()
            label = Gtk.Label(label=project["title"], xalign=0)
            event_box.add(label)
            event_box.connect("button-press-event", self.on_project_label_clicked, project)
            frame_header.pack_start(event_box, True, True, 0)
            
            add_terminal_button = Gtk.Button.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
            add_terminal_button.set_tooltip_text("Add a new terminal to this project")
            add_terminal_button.set_relief(Gtk.ReliefStyle.NONE)
            add_terminal_button.connect("clicked", self.on_add_terminal_clicked, project)
            frame_header.pack_end(add_terminal_button, False, False, 0)
            
            menu_button = Gtk.MenuButton()
            popover = Gtk.Popover.new(menu_button)
            menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, margin=5)
            popover.add(menu_box)
            menu_button.set_popover(popover)
            
            rename_button = Gtk.ModelButton(label="Rename")
            rename_button.connect("clicked", self.on_rename_project_clicked, project)
            menu_box.pack_start(rename_button, False, False, 0)

            delete_button = Gtk.ModelButton(label="Delete Project")
            delete_button.connect("clicked", self.on_delete_project_clicked, project)
            menu_box.pack_start(delete_button, False, False, 0)
            menu_box.show_all()
            frame_header.pack_end(menu_button, False, False, 0)
            
            grid = Gtk.Grid(column_spacing=15, row_spacing=15, margin_top=10)
            self.project_widgets[project['title']] = {'frame': project_frame, 'grid': grid}

            if is_expanded:
                content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                frame_vbox.pack_start(content_box, True, True, 0)

                tags_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                content_box.pack_start(tags_box, False, False, 0)
                
                for key, value in project.get("tags", {}).items():
                    tag_widget = self._create_tag_widget(project, key, value)
                    tags_box.pack_start(tag_widget, False, False, 0)

                add_tag_button = Gtk.Button(label="#")
                add_tag_button.get_style_context().add_class("hashtag-button")
                add_tag_button.set_relief(Gtk.ReliefStyle.NONE)
                add_tag_button.set_tooltip_text("Add a new tag")
                add_tag_button.connect("clicked", self.on_add_tag_clicked, project)
                tags_box.pack_start(add_tag_button, False, False, 0)

                if not project["terminals"]:
                    grid.get_style_context().add_class("empty-project-grid")
                content_box.pack_start(grid, True, True, 0)
                
                grid.drag_dest_set(Gtk.DestDefaults.ALL, DND_TARGET, Gdk.DragAction.MOVE)
                grid.connect("drag-data-received", self.on_terminal_drop_on_grid, project)
                self._redraw_project_grid(project, all_terminals_map)
            else:
                num_terminals = len(project.get("terminals", []))
                if num_terminals > 0:
                    hidden_label = Gtk.Label(label=f"({num_terminals} terminals hidden)")
                    hidden_label.set_halign(Gtk.Align.START)
                    hidden_label.set_margin_start(10)
                    frame_vbox.pack_start(hidden_label, False, False, 0)

        self.show_all()

    def _redraw_project_grid(self, project, all_terminals_map):
        """Redraws only the terminal grid for a specific project. Much faster."""
        if project['title'] not in self.project_widgets: return
        grid = self.project_widgets[project['title']]['grid']
        
        # Clear existing previews from this grid
        for child in grid.get_children():
            grid.remove(child)

        cols = 4
        for i, term_uuid in enumerate(project.get("terminals", [])):
            if term_uuid in all_terminals_map:
                if term_uuid not in self.terminal_widgets:
                    terminal, page_num = all_terminals_map[term_uuid]
                    self.terminal_widgets[term_uuid] = self._create_terminal_preview(terminal, page_num)
                
                preview_widget = self.terminal_widgets[term_uuid]
                # Ensure widget is not parented elsewhere before attaching
                if preview_widget.get_parent():
                    preview_widget.get_parent().remove(preview_widget)

                row, col = divmod(i, cols)
                grid.attach(preview_widget, col, row, 1, 1)
        grid.show_all()

    def _create_tag_widget(self, project, key, value):
        tag_button = Gtk.Button(label=f"#{key}:{value}")
        tag_button.get_style_context().add_class("tag-button")
        tag_button.connect("clicked", self.on_tag_clicked, project, key)
        tag_button.connect("button-press-event", self.on_tag_right_click, project, key)
        return tag_button

    def _create_terminal_preview(self, terminal, page_num):
        notebook = self.guake_app.get_notebook()
        title = notebook.get_tab_label_text(notebook.get_nth_page(page_num)) or f"Terminal {page_num + 1}"
        button = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=5)
        button.add(box)
        
        button.get_style_context().add_class("terminal-preview-button")
        
        button.connect("clicked", self.on_preview_clicked, terminal, page_num)
        button.connect("button-press-event", self.on_preview_right_click, terminal)
        
        DND_TARGET = [Gtk.TargetEntry.new('text/plain', Gtk.TargetFlags.SAME_APP, 0)]
        button.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, DND_TARGET, Gdk.DragAction.MOVE)
        button.connect("drag-data-get", self.on_terminal_drag_data_get, str(terminal.uuid))
        button.drag_dest_set(Gtk.DestDefaults.ALL, DND_TARGET, Gdk.DragAction.MOVE)
        button.connect("drag-motion", self.on_item_drag_motion)
        button.connect("drag-leave", self.on_item_drag_leave)
        button.connect("drag-data-received", self.on_terminal_drop_on_item, str(terminal.uuid))
        
        label = Gtk.Label.new(title)
        label.set_ellipsize(True)
        
        bg_color = self.guake_app.get_bgcolor()
        fg_color = self.guake_app.get_fgcolor()
        preview_area = TerminalMinimap(terminal, bg_color, fg_color)

        box.pack_start(label, False, False, 0)
        box.pack_start(preview_area, True, True, 0)
        return button

    def _show_text_input_dialog(self, title, message, current_text=""):
        dialog = Gtk.Dialog(title=title, transient_for=self.guake_app.window, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)
        content_area = dialog.get_content_area()
        content_area.set_spacing(10)
        content_area.set_margin_top(10); content_area.set_margin_bottom(10)
        content_area.set_margin_start(10); content_area.set_margin_end(10)
        label = Gtk.Label(label=message)
        content_area.add(label)
        entry = Gtk.Entry(text=current_text, activates_default=True)
        content_area.add(entry)
        dialog.set_default(dialog.get_widget_for_response(Gtk.ResponseType.OK))
        dialog.show_all()
        response = dialog.run()
        text = entry.get_text()
        dialog.destroy()
        if response == Gtk.ResponseType.OK and text:
            return text
        return None
        
    def on_add_tag_clicked(self, widget, project):
        tag_string = self._show_text_input_dialog(f"Add Tag to {project['title']}", "Enter tag as 'key:value':")
        if tag_string and ':' in tag_string:
            key, value = tag_string.split(':', 1)
            project.setdefault("tags", {})[key.strip()] = value.strip()
            self._save_layout()
            self.populate_map()

    def on_tag_clicked(self, widget, project, key):
        current_value = project["tags"][key]
        tag_string = self._show_text_input_dialog(f"Edit Tag in {project['title']}", "Edit tag 'key:value':", f"{key}:{current_value}")
        if tag_string and ':' in tag_string:
            new_key, new_value = tag_string.split(':', 1)
            del project["tags"][key]
            project["tags"][new_key.strip()] = new_value.strip()
            self._save_layout()
            self.populate_map()

    def on_tag_right_click(self, widget, event, project, key):
        if event.button == 3:
            dialog = Gtk.MessageDialog(transient_for=self.guake_app.window, flags=0, message_type=Gtk.MessageType.QUESTION,
                                     buttons=Gtk.ButtonsType.YES_NO, text=f"Delete tag '#{key}'?")
            dialog.format_secondary_text(f"Are you sure you want to remove this tag from the '{project['title']}' project?")
            response = dialog.run()
            if response == Gtk.ResponseType.YES:
                del project["tags"][key]
                self._save_layout()
                self.populate_map()
            dialog.destroy()
            return True
        return False

    def on_project_label_clicked(self, widget, event, project):
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self.on_rename_project_clicked(widget, project)
            return True
        return False

    def on_new_project_clicked(self, widget):
        new_title = self._show_text_input_dialog("Create New Project", "Enter the name for the new project:")
        if new_title:
            self.layout["projects"].append({"title": new_title, "terminals": [], "tags": {}, "expanded": True})
            self._save_layout()
            self.populate_map()

    def on_rename_project_clicked(self, widget, project):
        new_title = self._show_text_input_dialog("Rename Project", "Enter the new name:", project["title"])
        if new_title:
            # Find the actual project in the layout to modify it
            for p in self.layout['projects']:
                if p['title'] == project['title']:
                    p['title'] = new_title
                    break
            self._save_layout()
            self.populate_map()

    def on_delete_project_clicked(self, widget, project_to_delete):
        if project_to_delete['title'] == 'Uncategorized':
             dialog = Gtk.MessageDialog(transient_for=self.guake_app.window, flags=0, message_type=Gtk.MessageType.ERROR,
                                         buttons=Gtk.ButtonsType.OK, text="Cannot delete the default 'Uncategorized' project.")
             dialog.run()
             dialog.destroy()
             return

        dialog = Gtk.MessageDialog(transient_for=self.guake_app.window, flags=0, message_type=Gtk.MessageType.QUESTION,
                                 buttons=Gtk.ButtonsType.YES_NO, text=f"Delete project '{project_to_delete['title']}'?")
        dialog.format_secondary_text("All terminals in this project will be moved to 'Uncategorized'. This cannot be undone.")
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            uncategorized_project = next((p for p in self.layout['projects'] if p['title'] == 'Uncategorized'), None)
            if uncategorized_project:
                uncategorized_project['terminals'].extend(project_to_delete['terminals'])
            self.layout['projects'][:] = [p for p in self.layout['projects'] if p['title'] != project_to_delete['title']]
            self._save_layout()
            self.populate_map()

    def on_toggle_expand_clicked(self, widget, project_title):
        """Toggles the expanded state of a project."""
        project_to_toggle = next((p for p in self.layout['projects'] if p['title'] == project_title), None)
        if project_to_toggle:
            project_to_toggle['expanded'] = not project_to_toggle.get('expanded', True)
            self._save_layout()
            self.populate_map()
        else:
            log.warning(f"Could not find project '{project_title}' to toggle expansion.")

    def on_add_terminal_clicked(self, widget, project):
        """Adds a new terminal and assigns it to the specified project."""
        notebook = self.guake_app.get_notebook()
        
        uuid_before = {str(term.uuid) for term in notebook.iter_terminals()}
        self.guake_app.add_tab()
        uuid_after = {str(term.uuid) for term in notebook.iter_terminals()}
        new_uuid_set = uuid_after - uuid_before
        
        if new_uuid_set:
            new_uuid = new_uuid_set.pop()
            for p in self.layout['projects']:
                if p['title'] == project['title']:
                    p['terminals'].append(new_uuid)
                    break
            self._save_layout()
            self.populate_map() 
            log.debug(f"Added new terminal {new_uuid} to project '{project['title']}'")
        else:
            log.warning("Could not identify new terminal after creation.")

    def on_project_drag_data_get(self, widget, context, selection_data, info, timestamp, project):
        selection_data.set_text(f"project:{project['title']}", -1)
    
    def on_terminal_drag_data_get(self, widget, context, selection_data, info, timestamp, terminal_uuid):
        selection_data.set_text(f"terminal:{terminal_uuid}", -1)

    def on_item_drag_motion(self, widget, context, x, y, timestamp):
        widget.get_style_context().add_class('drop-highlight')
        return False

    def on_item_drag_leave(self, widget, context, timestamp):
        widget.get_style_context().remove_class('drop-highlight')
    
    def _move_terminal_in_layout(self, dragged_uuid, target_project, target_index=None):
        if not dragged_uuid: return
        
        source_project_title = ""
        for p in self.layout['projects']:
            if dragged_uuid in p['terminals']:
                source_project_title = p['title']
                p['terminals'].remove(dragged_uuid)
                break
        
        if target_index is not None:
            target_project['terminals'].insert(target_index, dragged_uuid)
        else:
            target_project['terminals'].append(dragged_uuid)
        
        self._save_layout()
        
        # Optimized Redraw
        all_terminals_map = {str(term.uuid): (term, self.guake_app.get_notebook().page_num(term.get_parent().get_root_box())) for term in self.guake_app.get_notebook().iter_terminals()}
        source_project = next((p for p in self.layout['projects'] if p['title'] == source_project_title), None)
        if source_project:
            self._redraw_project_grid(source_project, all_terminals_map)
        if source_project_title != target_project['title']:
            self._redraw_project_grid(target_project, all_terminals_map)

    def on_any_drop_on_project_frame(self, widget, context, x, y, selection_data, info, timestamp, target_project):
        """Dispatcher for any drop on a project frame."""
        widget.get_style_context().remove_class('drop-highlight')
        data = selection_data.get_text()
        if not data: return

        if data.startswith("project:"):
            dragged_project_title = data.split(":", 1)[1]
            if dragged_project_title == target_project['title']: return

            dragged_idx = next((i for i, p in enumerate(self.layout['projects']) if p['title'] == dragged_project_title), -1)
            
            if dragged_idx != -1:
                dragged_item = self.layout['projects'].pop(dragged_idx)
                target_idx = next((i for i, p in enumerate(self.layout['projects']) if p['title'] == target_project['title']), -1)
                self.layout['projects'].insert(target_idx, dragged_item)
                self._save_layout()
                self.populate_map()

        elif data.startswith("terminal:"):
            dragged_uuid = data.split(":", 1)[1]
            self._move_terminal_in_layout(dragged_uuid, target_project)

    def on_terminal_drop_on_grid(self, widget, context, x, y, selection_data, info, timestamp, target_project):
        data = selection_data.get_text()
        if data and data.startswith("terminal:"):
            dragged_uuid = data.split(":", 1)[1]
            self._move_terminal_in_layout(dragged_uuid, target_project)

    def on_terminal_drop_on_item(self, widget, context, x, y, selection_data, info, timestamp, target_uuid):
        self.on_item_drag_leave(widget, context, timestamp)
        data = selection_data.get_text()
        if not data or not data.startswith("terminal:"): return

        dragged_uuid = data.split(":", 1)[1]
        if dragged_uuid == target_uuid: return

        target_project, target_idx = None, -1
        for p in self.layout['projects']:
            if target_uuid in p['terminals']:
                target_project, target_idx = p, p['terminals'].index(target_uuid)
                break
        if target_project:
            self._move_terminal_in_layout(dragged_uuid, target_project, target_idx)

    def on_preview_clicked(self, widget, terminal, page_num):
        log.debug(f"Terminal preview for page {page_num} left-clicked. Switching view.")
        self.guake_app.get_notebook().set_current_page(page_num)
        self.guake_app.accel_world_map_navigation()
        terminal.grab_focus()

    def on_preview_right_click(self, widget, event, terminal):
        if event.button == 3:
            log.debug(f"Terminal preview for {terminal.uuid} right-clicked. Showing context menu.")
            self.show_preview_context_menu(widget, event, terminal)
            return True
        return False

    def show_preview_context_menu(self, widget, event, terminal):
        menu = Gtk.Menu()
        send_to_item = Gtk.MenuItem(label="Send to Project")
        send_to_submenu = Gtk.Menu()
        send_to_item.set_submenu(send_to_submenu)
        project_titles = self.get_project_titles()
        if not project_titles:
            send_to_item.set_sensitive(False)
        else:
            terminal_uuid = str(terminal.uuid)
            for title in project_titles:
                project_item = Gtk.MenuItem(label=title)
                project_item.connect("activate", self.on_send_to_project_activated, terminal_uuid, title)
                send_to_submenu.append(project_item)
        menu.append(send_to_item)
        menu.show_all()
        menu.popup_at_pointer(event)

    def on_send_to_project_activated(self, widget, terminal_uuid, project_title):
        log.debug(f"Sending terminal {terminal_uuid} to project '{project_title}'")
        self._move_terminal_in_layout(terminal_uuid, next(p for p in self.layout['projects'] if p['title'] == project_title))

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            log.debug("Escape key pressed, closing World Map View.")
            self.guake_app.accel_world_map_navigation()
            return True
        return False
        
    def get_project_titles(self):
        self._load_layout() 
        return [p['title'] for p in self.layout.get('projects', [])]

    def move_terminal_to_project(self, terminal_uuid, target_project_title):
        if not terminal_uuid or not target_project_title: return
        self._load_layout()
        
        target_project = next((p for p in self.layout['projects'] if p['title'] == target_project_title), None)
        if not target_project: return

        self._move_terminal_in_layout(terminal_uuid, target_project)
        log.info(f"Moved terminal {terminal_uuid} to project '{target_project_title}'")
