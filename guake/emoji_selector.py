# -*- coding: utf-8; -*-
"""
A searchable emoji selector dialog.
"""
import gi
import json
import logging
from pathlib import Path

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

log = logging.getLogger(__name__)

class SearchableEmojiSelector(Gtk.Dialog):
    """
    A dialog window that allows users to search for and select an emoji.
    """
    # Class-level cache for emoji data. This ensures the JSON file is read
    # and parsed only once per application session, making subsequent openings
    # of the selector instantaneous.
    _emoji_cache = None

    def __init__(self, parent, emoji_file_path):
        """
        Initializes the emoji selector dialog.
        """
        super().__init__(title="Select an Emoji", parent=parent, flags=0)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.set_default_size(500, 600)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

        self.connect("key-press-event", self._on_key_press)

        self.selected_emoji = None
        self.emojis_data = self._load_emojis(emoji_file_path)
        self.populate_generator_id = None

        # Add custom CSS for hover effect
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .emoji-cell {
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 5px;
                margin: 2px;
            }
            .emoji-cell:hover {
                border-color: #4A90D9;
                background-color: rgba(74, 144, 217, 0.1);
            }
            .category-header {
                font-weight: bold;
                margin-top: 15px;
                margin-bottom: 5px;
                margin-left: 5px;
            }
            .subcategory-header {
                font-style: italic;
                margin-top: 10px;
                margin-bottom: 5px;
                margin-left: 10px;
                opacity: 0.8;
            }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
        self.get_content_area().add(vbox)

        search_entry = Gtk.SearchEntry()
        search_entry.set_placeholder_text("Search for an emoji...")
        search_entry.connect("search-changed", self._on_filter_changed)
        vbox.pack_start(search_entry, False, False, 0)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_vexpand(True)
        vbox.pack_start(scrolled_window, True, True, 0)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flowbox.set_max_children_per_line(10)
        scrolled_window.add(self.flowbox)

        self._populate_grid()

        self.show_all()

    def _on_key_press(self, widget, event):
        """Handles key press events for the dialog."""
        if event.keyval == Gdk.KEY_Escape:
            self.response(Gtk.ResponseType.CANCEL)
            return True  # Event handled, stop propagation
        return False  # Propagate other key presses

    def _load_emojis(self, emoji_file_path):
        """
        Loads emoji data from the specified JSON file, using a class-level cache
        to avoid reading and parsing the file more than once per session.
        """
        if SearchableEmojiSelector._emoji_cache is not None:
            log.debug("Loading emojis from cache.")
            return SearchableEmojiSelector._emoji_cache

        log.info("Loading emojis from file for the first time: %s", emoji_file_path)
        path = Path(emoji_file_path)
        if not path.exists():
            log.error("Emoji file not found at: %s", emoji_file_path)
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            SearchableEmojiSelector._emoji_cache = data.get("emojis", {})
            return SearchableEmojiSelector._emoji_cache
        except (json.JSONDecodeError, IOError) as e:
            log.error("Failed to load or parse emoji file %s: %s", emoji_file_path, e)
            return None

    def _create_widget_generator(self, filter_text=None):
        """
        A generator that yields widgets for the emoji grid. This allows for
        lazy creation of widgets, keeping the UI responsive.
        """
        if not self.emojis_data:
            yield Gtk.Label(label="Could not load emoji data.")
            return

        filter_text = filter_text.lower() if filter_text else None

        for category, subcategories in self.emojis_data.items():
            category_widgets_to_yield = []
            has_matching_emojis_in_category = False

            for subcategory, emojis in subcategories.items():
                subcategory_widgets_to_yield = []
                has_matching_emojis_in_subcategory = False

                for emoji_info in emojis:
                    if filter_text and filter_text not in emoji_info["name"].lower():
                        continue
                    
                    has_matching_emojis_in_category = True
                    has_matching_emojis_in_subcategory = True

                    button = Gtk.Button(label=emoji_info["emoji"])
                    button.set_tooltip_text(emoji_info["name"].capitalize())
                    button.set_relief(Gtk.ReliefStyle.NONE)
                    button.connect("clicked", self._on_emoji_clicked, emoji_info["emoji"])
                    
                    cell = Gtk.Box()
                    cell.get_style_context().add_class("emoji-cell")
                    cell.add(button)
                    
                    subcategory_widgets_to_yield.append(cell)

                if has_matching_emojis_in_subcategory:
                    subcategory_header = Gtk.Label(label=subcategory.replace('-', ' ').capitalize(), xalign=0)
                    subcategory_header.get_style_context().add_class("subcategory-header")
                    category_widgets_to_yield.append(subcategory_header)
                    category_widgets_to_yield.extend(subcategory_widgets_to_yield)

            if has_matching_emojis_in_category:
                category_header = Gtk.Label(label=category, xalign=0)
                category_header.get_style_context().add_class("category-header")
                yield category_header
                
                # This separator forces a line break for the category header.
                yield Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)

                for widget in category_widgets_to_yield:
                    yield widget

    def _populate_grid(self, filter_text=None):
        """
        Populates the flowbox lazily using an idle callback. This prevents the
        UI from freezing while creating a large number of emoji widgets.
        """
        if self.populate_generator_id:
            GLib.source_remove(self.populate_generator_id)
            self.populate_generator_id = None

        for child in self.flowbox.get_children():
            self.flowbox.remove(child)

        generator = self._create_widget_generator(filter_text)

        def add_chunk_of_widgets():
            # Add a chunk of widgets in each idle cycle to keep UI responsive.
            CHUNK_SIZE = 100
            for _ in range(CHUNK_SIZE):
                try:
                    widget = next(generator)
                    self.flowbox.add(widget)
                except StopIteration:
                    self.flowbox.show_all()
                    self.populate_generator_id = None
                    return False  # Stop the idle source
            self.flowbox.show_all()
            return True  # Continue the idle source

        self.populate_generator_id = GLib.idle_add(add_chunk_of_widgets)

    def _on_filter_changed(self, search_entry):
        """Callback for when the search entry text changes."""
        filter_text = search_entry.get_text().strip()
        self._populate_grid(filter_text)

    def _on_emoji_clicked(self, button, emoji):
        """Callback for when an emoji button is clicked."""
        self.selected_emoji = emoji
        self.response(Gtk.ResponseType.OK)
