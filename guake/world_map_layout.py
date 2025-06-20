# -*- coding: utf-8; -*-
"""
Data management for the World Map layout.
Handles loading, saving, and modifying the world_map.json file.
"""
import logging
import json
import re

log = logging.getLogger(__name__)

class LayoutManager:
    def __init__(self, config_dir):
        self.layout_file = config_dir / "world_map.json"
        self.layout = {}
        self._load_layout()

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

    def synchronize(self, current_terminals_map):
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

    def filter_projects(self, filter_text, all_terminals_map, notebook):
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
    def add_project(self, title):
        if title and not any(p['title'] == title for p in self.layout['projects']):
            self.layout['projects'].append({"title": title, "terminals": [], "tags": {}, "expanded": True})
            self._save_layout()
    
    def rename_project(self, old_title, new_title):
        if not new_title or any(p['title'] == new_title for p in self.layout['projects']):
            return # Prevent empty or duplicate titles
        project = next((p for p in self.layout['projects'] if p['title'] == old_title), None)
        if project:
            project['title'] = new_title
            self._save_layout()
            
    def delete_project(self, title):
        if title == 'Uncategorized': return
        project_to_delete = next((p for p in self.layout['projects'] if p['title'] == title), None)
        if project_to_delete:
            uncategorized = next((p for p in self.layout['projects'] if p['title'] == 'Uncategorized'), None)
            if uncategorized:
                uncategorized['terminals'].extend(project_to_delete['terminals'])
            self.layout['projects'].remove(project_to_delete)
            self._save_layout()
    def toggle_project_expansion(self, title):
        project = next((p for p in self.layout['projects'] if p['title'] == title), None)
        if project:
            project['expanded'] = not project.get('expanded', True)
            self._save_layout()
    def add_new_terminal_to_project(self, title, guake_app):
        notebook = guake_app.get_notebook()
        uuids_before = {str(term.uuid) for term in notebook.iter_terminals()}
        guake_app.add_tab()
        uuids_after = {str(term.uuid) for term in notebook.iter_terminals()}
        new_uuid = (uuids_after - uuids_before).pop()
        
        project = next((p for p in self.layout['projects'] if p['title'] == title), None)
        if project:
            project['terminals'].append(new_uuid)
            self._save_layout()
            
    def move_terminal_to_project(self, terminal_uuid, target_project_title, target_index=None):
        if not terminal_uuid or not target_project_title: return
        
        # Find and remove from old project
        for p in self.layout['projects']:
            if terminal_uuid in p['terminals']:
                p['terminals'].remove(terminal_uuid)
                break
        
        # Add to new project
        target_project = next((p for p in self.layout['projects'] if p['title'] == target_project_title), None)
        if target_project:
            if target_index is not None:
                target_project['terminals'].insert(target_index, terminal_uuid)
            else:
                target_project['terminals'].append(terminal_uuid)
        self._save_layout()

    def get_project_and_index_by_terminal_uuid(self, terminal_uuid):
        """Returns the project title that contains the given terminal UUID and the index of the terminal in that project."""
        for project in self.layout.get('projects', []):
            if terminal_uuid in project.get('terminals', []):
                return project, project['terminals'].index(terminal_uuid)
        return None, None
    
    def get_project_by_title(self, title):
        """Returns the project dictionary for the given title."""
        return next((p for p in self.layout['projects'] if p['title'] == title), None)
    
    def get_project_by_terminal_uuid(self, terminal_uuid):
        """Returns the project dictionary that contains the given terminal UUID."""
        for project in self.layout.get('projects', []):
            if terminal_uuid in project.get('terminals', []):
                return project
        return None
        
    def reorder_projects(self, dragged_title, target_title):
        if dragged_title == target_title: return
        
        dragged_idx = next((i for i, p in enumerate(self.layout['projects']) if p['title'] == dragged_title), -1)
        if dragged_idx != -1:
            dragged_item = self.layout['projects'].pop(dragged_idx)
            target_idx = next((i for i, p in enumerate(self.layout['projects']) if p['title'] == target_title), -1)
            self.layout['projects'].insert(target_idx, dragged_item)
            self._save_layout()

    def handle_drop(self, drop_data, target_project_title, target_terminal_uuid=None):
        """Central dispatcher for all drop events."""
        if not drop_data: return

        if drop_data.startswith("project:"):
            dragged_project_title = drop_data.split(":", 1)[1]
            self.reorder_projects(dragged_project_title, target_project_title)
        
        elif drop_data.startswith("terminal:"):
            dragged_uuid = drop_data.split(":", 1)[1]
            target_project = next((p for p in self.layout['projects'] if p['title'] == target_project_title), None)
            if not target_project: return
            
            target_index = None
            if target_terminal_uuid and target_terminal_uuid in target_project['terminals']:
                target_index = target_project['terminals'].index(target_terminal_uuid)

            self.move_terminal_to_project(dragged_uuid, target_project_title, target_index)
    def get_project_titles(self):
        return [p['title'] for p in self.layout.get('projects', [])]
    def save(self):
        """Explicitly save the current layout to disk."""
        self._save_layout()
