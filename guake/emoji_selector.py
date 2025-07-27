# -*- coding: utf-8; -*-
"""
A searchable emoji selector dialog.
"""
import gi
import json
import logging
from pathlib import Path

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

log = logging.getLogger(__name__)

class SearchableEmojiSelector(Gtk.Dialog):
    """
    A dialog window that allows users to search for and select an emoji.
    """

    def __init__(self, parent, emoji_file_path):
        """
        Initializes the emoji selector dialog.
        """
        super().__init__(title="Select an Emoji", parent=parent, flags=0)
        self.add_button("Abort", Gtk.ResponseType.CANCEL)
        self.set_default_size(500, 600)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

        self.selected_emoji = None
        self.emojis_data = self._load_emojis(emoji_file_path)

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

    def _load_emojis(self, emoji_file_path):
        """Loads emoji data from the specified JSON file."""
        path = Path(emoji_file_path)
        if not path.exists():
            log.error("Emoji file not found at: %s", emoji_file_path)
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("emojis", {})
        except (json.JSONDecodeError, IOError) as e:
            log.error("Failed to load or parse emoji file %s: %s", emoji_file_path, e)
            return None

    def _populate_grid(self, filter_text=None):
        """Populates the flowbox with emojis, optionally filtered by text."""
        # Clear existing children
        for child in self.flowbox.get_children():
            self.flowbox.remove(child)

        if not self.emojis_data:
            self.flowbox.add(Gtk.Label(label="Could not load emoji data."))
            return

        filter_text = filter_text.lower() if filter_text else None

        for category, subcategories in self.emojis_data.items():
            category_widgets = []
            has_matching_emojis_in_category = False

            for subcategory, emojis in subcategories.items():
                subcategory_widgets = []
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
                    
                    subcategory_widgets.append(cell)

                if has_matching_emojis_in_subcategory:
                    # Add subcategory header if it has content
                    subcategory_header = Gtk.Label(label=subcategory.replace('-', ' ').capitalize(), xalign=0)
                    subcategory_header.get_style_context().add_class("subcategory-header")
                    category_widgets.append(subcategory_header)
                    category_widgets.extend(subcategory_widgets)

            if has_matching_emojis_in_category:
                # Add category header if it has content
                category_header = Gtk.Label(label=category, xalign=0)
                category_header.get_style_context().add_class("category-header")
                self.flowbox.add(category_header)
                
                # Use a separator widget to force headers onto their own line in FlowBox
                separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                separator.set_size_request(self.get_allocated_width(), 1)
                self.flowbox.add(separator)

                for widget in category_widgets:
                    self.flowbox.add(widget)

        self.flowbox.show_all()


    def _on_filter_changed(self, search_entry):
        """Callback for when the search entry text changes."""
        filter_text = search_entry.get_text().strip()
        self._populate_grid(filter_text)

    def _on_emoji_clicked(self, button, emoji):
        """Callback for when an emoji button is clicked."""
        self.selected_emoji = emoji
        self.response(Gtk.ResponseType.OK)

