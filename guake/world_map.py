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
import os
import subprocess

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
from guake.world_map_widgets import TerminalMinimap
from guake.world_map_layout import LayoutManager

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
            .git-status-clean { color: #26A269; }
            .git-status-dirty { color: #FF7800; }
            .git-status-untracked { color: #F6D32D; }
            .git-status-nogit { opacity: 0.4; }
        """)
        self.get_style_context().add_class("world-map-view")
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.layout = LayoutManager( self.guake_app.get_xdg_config_directory())

        self.set_can_focus(True)
        self.connect("key-press-event", self.on_key_press)

        self.root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self.root_box)

        header_bar = Gtk.Box(spacing=6, margin=10)
        self.root_box.pack_start(header_bar, False, False, 0)
        
        new_project_button = Gtk.Button.new_with_label("New Project")
        new_project_button.connect("clicked", self.on_new_project_clicked)
        header_bar.pack_start(new_project_button, False, False, 0)

        self.toggle_all_button = Gtk.Button.new_with_label("Collapse All")
        self.toggle_all_button.connect("clicked", self.on_toggle_all_clicked)
        header_bar.pack_start(self.toggle_all_button, False, False, 0)

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


    def on_filter_changed(self, search_entry):
        self.populate_map()

    def populate_map(self):
        """Clears and rebuilds the entire world map view based on the current layout and filters."""
        # Update the toggle all button's label based on the current state
        if hasattr(self, 'toggle_all_button'):
            is_any_expanded = any(p.get('expanded', True) for p in self.layout.layout.get('projects', []))
            if is_any_expanded:
                self.toggle_all_button.set_label("Collapse All")
            else:
                self.toggle_all_button.set_label("Expand All")
        
        # Clear all existing widgets from the main container
        for child in self.main_box.get_children():
            self.main_box.remove(child)

        # Clear widget trackers. Recreating widgets every time is simpler and more robust
        # than trying to manage a cache of potentially destroyed widgets.
        self.project_widgets = {}
        self.terminal_widgets = {}

        all_terminals_map = {}
        notebook = self.guake_app.get_notebook()
        for i in range(notebook.get_n_pages()):
             page = notebook.get_nth_page(i)
             for term in page.iter_terminals():
                 all_terminals_map[str(term.uuid)] = (term, i)

        self.layout.synchronize(all_terminals_map)
        
        filter_text = self.search_entry.get_text()
        visible_projects = self.layout.filter_projects(filter_text, all_terminals_map, self.guake_app.get_notebook())


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
            
            flowbox = Gtk.FlowBox(
                valign=Gtk.Align.START,
                selection_mode=Gtk.SelectionMode.NONE,
                column_spacing=15,
                row_spacing=15,
                margin_top=10
            )
            self.project_widgets[project['title']] = {'frame': project_frame, 'flowbox': flowbox}

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
                    flowbox.get_style_context().add_class("empty-project-grid")
                content_box.pack_start(flowbox, True, True, 0)
                
                flowbox.drag_dest_set(Gtk.DestDefaults.ALL, DND_TARGET, Gdk.DragAction.MOVE)
                flowbox.connect("drag-data-received", self.on_terminal_drop_on_grid, project)
                self._redraw_project_grid(project, all_terminals_map)
            else:
                num_terminals = len(project.get("terminals", []))
                if num_terminals > 0:
                    hidden_label = Gtk.Label(label=f"({num_terminals} terminals hidden)")
                    hidden_label.set_halign(Gtk.Align.CENTER)
                    frame_vbox.pack_start(hidden_label, False, False, 0)

        self.show_all()

    def _redraw_project_grid(self, project, all_terminals_map):
        """Redraws the terminal flowbox for a specific project."""
        project_title = project['title']
        if project_title not in self.project_widgets:
            return

        flowbox = self.project_widgets[project_title]['flowbox']
        
        # Clear existing previews from the flowbox
        for child in flowbox.get_children():
            flowbox.remove(child)

        # Re-create and add a fresh preview widget for each terminal
        for term_uuid in project.get("terminals", []):
            if term_uuid in all_terminals_map:
                terminal, page_num = all_terminals_map[term_uuid]
                preview_widget = self._create_terminal_preview(terminal, page_num)
                # We can still cache the widget for the duration of this one `populate_map` call,
                # though its utility is now limited. It mainly prevents re-creating a preview
                # if the same terminal appeared multiple times in the layout (which shouldn't happen).
                self.terminal_widgets[term_uuid] = preview_widget
                flowbox.add(preview_widget)
        flowbox.show_all()

    def _create_tag_widget(self, project, key, value):
        tag_button = Gtk.Button(label=f"#{key}:{value}")
        tag_button.get_style_context().add_class("tag-button")
        tag_button.connect("clicked", self.on_tag_clicked, project, key)
        tag_button.connect("button-press-event", self.on_tag_right_click, project, key)
        return tag_button

    def _get_git_status(self, directory):
        """
        Checks git status, distinguishing between modified and untracked files.
        Returns: 'clean', 'dirty', 'untracked', or 'no-git'.
        """
        if not directory or not os.path.isdir(directory):
            return "no-git"

        path = directory
        try:
            # Traverse up to find the .git directory
            while path != os.path.dirname(path):  # Stop at root
                if os.path.isdir(os.path.join(path, '.git')):
                    # Found repo root, run command from there
                    result = subprocess.run(
                        ['git', 'status', '--porcelain'],
                        capture_output=True, text=True, check=False, cwd=path
                    )
                    if result.returncode != 0:
                        return "no-git"  # Git command failed
                    
                    output = result.stdout.strip()
                    if not output:
                        return "clean" # All committed

                    has_modified = False
                    has_untracked = False
                    for line in output.splitlines():
                        if line.startswith('??'):
                            has_untracked = True
                        else:
                            has_modified = True
                    
                    if has_modified:
                        return "dirty"      # Uncommitted changes to tracked files
                    if has_untracked:
                        return "untracked"  # New files, never committed
                    
                    return "clean" # Should not be reached if output is not empty
                path = os.path.dirname(path)
            return "no-git"  # No .git directory found
        except (FileNotFoundError, Exception) as e:
            log.warning(f"Could not get git status for {directory}: {e}")
            return "no-git"

    def _summarize_path(self, path):
        """Shortens a long path for display."""
        if not path:
            return ""
        home = os.path.expanduser("~")
        if path.startswith(home):
            path = "~" + path[len(home):]
        
        parts = path.split(os.sep)
        if len(parts) > 4:
            path = os.sep.join([parts[0], '...', parts[-2], parts[-1]])
        
        return path

    def _create_terminal_preview(self, terminal, page_num):
        notebook = self.guake_app.get_notebook()
        terminal_name = notebook.get_tab_text_index(page_num) or "Terminal"
        title = f"{terminal_name} (#{page_num + 1})"
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
        
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        label = Gtk.Label.new(title)
        label.set_ellipsize(True)
        label.set_xalign(0)
        header_box.pack_start(label, True, True, 0)
        
        try:
            cwd = terminal.get_current_directory()
        except Exception:
            cwd = None

        # Git Status Icon
        git_status = self._get_git_status(cwd)
        if git_status == 'clean':
            icon_name = "emblem-ok-symbolic"
            tooltip = "Git: Clean"
        elif git_status == 'dirty':
            icon_name = "emblem-synchronizing-symbolic"
            tooltip = "Git: Uncommitted changes to tracked files"
        elif git_status == 'untracked':
            icon_name = "document-new-symbolic"
            tooltip = "Git: Untracked files"
        else: # 'no-git'
            icon_name = "emblem-important-symbolic"
            tooltip = "Not a Git repository"
        
        git_icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        git_icon.set_tooltip_text(tooltip)
        
        style_context = git_icon.get_style_context()
        style_context.add_class(f"git-status-{git_status}")

        header_box.pack_end(git_icon, False, False, 0)

        bg_color = self.guake_app.get_bgcolor()
        fg_color = self.guake_app.get_fgcolor()
        preview_area = TerminalMinimap(terminal, bg_color, fg_color)

        # CWD Path Label
        path_label = Gtk.Label()
        summarized_path = self._summarize_path(cwd)
        path_label.set_markup(f"<small>{summarized_path}</small>")
        path_label.set_ellipsize(True)
        path_label.set_xalign(0)
        
        box.pack_start(header_box, False, False, 0)
        box.pack_start(preview_area, True, True, 0)
        box.pack_start(path_label, False, False, 0)
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
            self.layout.save()
            self.populate_map()

    def on_tag_clicked(self, widget, project, key):
        current_value = project["tags"][key]
        tag_string = self._show_text_input_dialog(f"Edit Tag in {project['title']}", "Edit tag 'key:value':", f"{key}:{current_value}")
        if tag_string and ':' in tag_string:
            new_key, new_value = tag_string.split(':', 1)
            del project["tags"][key]
            project["tags"][new_key.strip()] = new_value.strip()
            self.layout.save()
            self.populate_map()

    def on_tag_right_click(self, widget, event, project, key):
        if event.button == 3:
            dialog = Gtk.MessageDialog(transient_for=self.guake_app.window, flags=0, message_type=Gtk.MessageType.QUESTION,
                                     buttons=Gtk.ButtonsType.YES_NO, text=f"Delete tag '#{key}'?")
            dialog.format_secondary_text(f"Are you sure you want to remove this tag from the '{project['title']}' project?")
            response = dialog.run()
            if response == Gtk.ResponseType.YES:
                del project["tags"][key]
                self.layout.save()
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
            self.layout.add_project(new_title)
            self.populate_map()

    def on_rename_project_clicked(self, widget, project):
        new_title = self._show_text_input_dialog("Rename Project", "Enter the new name:", project["title"])
        if new_title:
            self.layout.rename_project(project["title"], new_title)
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
            self.layout.delete_project(project_to_delete['title'])
            log.info(f"Deleted project '{project_to_delete['title']}'")
            self.populate_map()

    def on_toggle_all_clicked(self, widget):
        """Collapses or expands all projects at once."""
        is_any_expanded = any(p.get('expanded', True) for p in self.layout.layout.get('projects', []))

        for project in self.layout.layout.get('projects', []):
            project['expanded'] = not is_any_expanded
        
        self.layout.save()
        self.populate_map()

    def on_toggle_expand_clicked(self, widget, project_title):
        """Toggles the expanded state of a project."""
        self.layout.toggle_project_expansion(project_title)
        self.populate_map()

    def on_add_terminal_clicked(self, widget, project):
        """Adds a new terminal and assigns it to the specified project."""
        self.layout.add_new_terminal_to_project(project['title'], self.guake_app)
        self.populate_map()
        log.info(f"Added new terminal to project '{project['title']}'")

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
        
        source_project, _ = self.layout.get_project_and_index_by_terminal_uuid(dragged_uuid)
        if not source_project:
            log.warning(f"Could not find source project for terminal {dragged_uuid}")
            return

        self.layout.move_terminal_to_project(dragged_uuid, target_project['title'], target_index)
        log.info(f"Moved terminal {dragged_uuid} from project '{source_project['title']}' to '{target_project['title']}'")

        # The "optimized" partial redraw was buggy. A full populate is safer and ensures UI consistency.
        self.populate_map()

    def on_any_drop_on_project_frame(self, widget, context, x, y, selection_data, info, timestamp, target_project):
        """Dispatcher for any drop on a project frame."""
        widget.get_style_context().remove_class('drop-highlight')
        data = selection_data.get_text()
        if not data: return

        if data.startswith("project:"):
            dragged_project_title = data.split(":", 1)[1]
            if dragged_project_title == target_project['title']: return

            log.debug(f"Dropped project '{dragged_project_title}' onto project '{target_project['title']}'")
            self.layout.reorder_projects(dragged_project_title, target_project['title'])
            self.populate_map()

        elif data.startswith("terminal:"):
            dragged_uuid = data.split(":", 1)[1]
            log.debug(f"Dropped terminal '{dragged_uuid}' onto project frame '{target_project['title']}'")
            self._move_terminal_in_layout(dragged_uuid, target_project)

    def on_terminal_drop_on_grid(self, widget, context, x, y, selection_data, info, timestamp, target_project):
        data = selection_data.get_text()
        if data and data.startswith("terminal:"):
            dragged_uuid = data.split(":", 1)[1]
            log.debug(f"Dropped terminal '{dragged_uuid}' onto grid for project '{target_project['title']}'")
            self._move_terminal_in_layout(dragged_uuid, target_project)
            return True

    def on_terminal_drop_on_item(self, widget, context, x, y, selection_data, info, timestamp, target_uuid):
        self.on_item_drag_leave(widget, context, timestamp)
        data = selection_data.get_text()
        if not data or not data.startswith("terminal:"): return

        dragged_uuid = data.split(":", 1)[1]
        if dragged_uuid == target_uuid: return

        target_project, target_idx = self.layout.get_project_and_index_by_terminal_uuid(target_uuid)
        if not target_project: return
        
        log.debug(f"Dropped terminal '{dragged_uuid}' onto terminal '{target_uuid}'")
        self._move_terminal_in_layout(dragged_uuid, target_project, target_idx)
        return True

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
        project_titles = self.layout.get_project_titles()
        if not project_titles:
            send_to_item.set_sensitive(False)
        else:
            terminal_uuid = str(terminal.uuid)
            for title in project_titles:
                project_item = Gtk.MenuItem(label=title)
                project_item.connect("activate", self.on_send_to_project_activated, terminal_uuid, title)
                send_to_submenu.append(project_item)
        menu.append(send_to_item)
        menu.attach_to_widget(widget, None)
        menu.show_all()
        menu.popup_at_widget(widget, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, event)

    def on_send_to_project_activated(self, widget, terminal_uuid, project_title):
        log.debug(f"Sending terminal {terminal_uuid} to project '{project_title}'")
        project = self.layout.get_project_by_title(project_title)
        self._move_terminal_in_layout(terminal_uuid, project)

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            log.debug("Escape key pressed, closing World Map View.")
            self.guake_app.accel_world_map_navigation()
            return True
        return False
        

    def move_terminal_to_project(self, terminal_uuid, target_project_title):
        if not terminal_uuid or not target_project_title: return
        
        target_project = self.layout.get_project_by_title(target_project_title)
        if not target_project: return

        self._move_terminal_in_layout(terminal_uuid, target_project)
        log.info(f"Moved terminal {terminal_uuid} to project '{target_project_title}'")
