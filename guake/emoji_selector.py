# -*- coding: utf-8; -*-
"""
A searchable emoji selector dialog.
"""
import gi
import json
import logging
from pathlib import Path
import re

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

log = logging.getLogger(__name__)

# --- Constants ---
DEBOUNCE_DELAY = 300  # milliseconds
MAX_RECENT_EMOJIS = 20

class SearchableEmojiSelector(Gtk.Dialog):
    """
    A dialog window that allows users to search for and select an emoji.
    Features:
    - Lazy loading of emojis for fast startup.
    - Debounced search to avoid UI lag.
    - Token-based search in categories, subcategories, and emoji names.
    - "Recently Used" section for quick access.
    - Full keyboard navigation.
    - Caches emoji file to avoid re-reading from disk.
    """
    _emoji_cache = None

    def __init__(self, parent, emoji_file_path, history_file_path):
        """
        Initializes the emoji selector dialog.
        
        :param parent: The parent window.
        :param emoji_file_path: Path to the main emoji JSON file.
        :param history_file_path: Path to the file for storing recently used emojis.
        """
        super().__init__(title="Select an Emoji", parent=parent, flags=0)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.set_default_size(500, 600)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

        # --- Instance variables ---
        self.history_file_path = Path(history_file_path)
        self.selected_emoji = None
        self.emojis_data = self._load_emojis(emoji_file_path)
        self.recent_emojis = self._load_recent_emojis()
        self.populate_generator_id = None
        self.debounce_timer_id = None

        # --- Event connections ---
        self.connect("key-press-event", self._on_key_press)

        # --- UI Setup ---
        self._setup_css()
        
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
        self.get_content_area().add(vbox)

        # Search Entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search for an emoji...")
        self.search_entry.connect("search-changed", self._on_filter_changed)
        vbox.pack_start(self.search_entry, False, False, 0)

        # Scrolled Window and ListBox
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_vexpand(True)
        vbox.pack_start(scrolled_window, True, True, 0)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled_window.add(self.listbox)

        self._populate_list()

        self.show_all()
        self.search_entry.grab_focus() # Set initial focus

    def _setup_css(self):
        """Loads custom CSS for the dialog's widgets."""
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .emoji-button {
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 5px;
                margin: 2px;
                font-size: 1.5em;
            }
            .emoji-button:focus {
                border-color: #4A90D9;
                background-color: rgba(74, 144, 217, 0.2);
            }
            .category-header-row {
                background-color: alpha(@theme_bg_color, 0.5);
                margin-top: 10px;
            }
            .category-header-label {
                font-weight: bold;
                margin: 5px;
                font-size: 1.1em;
            }
            .no-results-label {
                font-style: italic;
                opacity: 0.7;
                margin-top: 20px;
            }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # --- Event Handlers ---

    def _on_key_press(self, widget, event):
        """Handles key press events for the dialog, specifically the ESC key."""
        if event.keyval == Gdk.KEY_Escape:
            self.response(Gtk.ResponseType.CANCEL)
            return True
        return False

    def _on_filter_changed(self, search_entry):
        """Debounces search input before triggering a filter operation."""
        if self.debounce_timer_id:
            GLib.source_remove(self.debounce_timer_id)
        
        self.debounce_timer_id = GLib.timeout_add(
            DEBOUNCE_DELAY, self._perform_filter
        )

    def _perform_filter(self):
        """Triggers the repopulation of the list based on the search query."""
        filter_text = self.search_entry.get_text().strip()
        self._populate_list(filter_text)
        self.debounce_timer_id = None
        return False # Do not run timer again

    def _on_emoji_clicked(self, button, emoji_info):
        """Callback for when an emoji button is clicked."""
        self.selected_emoji = emoji_info["emoji"]
        self._add_to_recents(emoji_info)
        self.response(Gtk.ResponseType.OK)

    # --- Data Loading and Persistence ---

    def _load_emojis(self, emoji_file_path):
        """Loads emoji data from JSON, using a class-level cache."""
        if SearchableEmojiSelector._emoji_cache is not None:
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

    def _load_recent_emojis(self):
        """Loads the list of recently used emojis from its JSON file."""
        if not self.history_file_path.exists():
            return []
        try:
            with self.history_file_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning("Could not load emoji history: %s", e)
            return []

    def _save_recent_emojis(self):
        """Saves the list of recently used emojis to its JSON file."""
        try:
            self.history_file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.history_file_path.open("w", encoding="utf-8") as f:
                json.dump(self.recent_emojis, f)
        except IOError as e:
            log.error("Could not save emoji history: %s", e)

    def _add_to_recents(self, emoji_info):
        """Adds a selected emoji to the top of the recents list and saves."""
        # Remove if already present to avoid duplicates and move it to the top
        self.recent_emojis = [e for e in self.recent_emojis if e["emoji"] != emoji_info["emoji"]]
        
        self.recent_emojis.insert(0, emoji_info)
        self.recent_emojis = self.recent_emojis[:MAX_RECENT_EMOJIS]
        self._save_recent_emojis()

    # --- UI Population and Filtering ---

    def _populate_list(self, filter_text=None):
        """Populates the ListBox lazily based on the filter text."""
        if self.populate_generator_id:
            GLib.source_remove(self.populate_generator_id)
        
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        generator = self._create_widget_generator(filter_text)

        def add_chunk_of_widgets():
            # Add a limited number of widgets per idle cycle to keep UI responsive
            CHUNK_SIZE = 10
            for _ in range(CHUNK_SIZE):
                try:
                    widget = next(generator)
                    if widget:
                        self.listbox.add(widget)
                except StopIteration:
                    # If the generator is exhausted, check if any results were added
                    if not self.listbox.get_children():
                        label = Gtk.Label(label="No emojis found.")
                        label.get_style_context().add_class("no-results-label")
                        self.listbox.add(label)
                    
                    self.listbox.show_all()
                    self.populate_generator_id = None
                    return False # Stop the idle source
            self.listbox.show_all()
            return True # Continue the idle source

        self.populate_generator_id = GLib.idle_add(add_chunk_of_widgets)

    def _create_emoji_flowbox(self, emojis):
        """Creates a FlowBox populated with emoji buttons."""
        flowbox = Gtk.FlowBox()
        flowbox.set_valign(Gtk.Align.START)
        flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        flowbox.set_max_children_per_line(10)
        
        for emoji_info in emojis:
            button = Gtk.Button(label=emoji_info["emoji"])
            button.set_tooltip_text(emoji_info["name"].capitalize())
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("emoji-button")
            button.connect("clicked", self._on_emoji_clicked, emoji_info)
            flowbox.add(button)
        return flowbox

    def _tokenize_and_match(self, query_tokens, text_to_search):
        """Checks if all tokens from the query are present in the text."""
        return all(token in text_to_search for token in query_tokens)

    def _create_widget_generator(self, filter_text=None):
        """A generator that yields ListBoxRows for the emoji list."""
        if not self.emojis_data:
            yield Gtk.Label(label="Could not load emoji data.")
            return

        query_tokens = re.split(r'\s+', filter_text.lower()) if filter_text else []

        # --- Recently Used Section (only shown when not searching) ---
        if not filter_text and self.recent_emojis:
            header_row = Gtk.ListBoxRow()
            header_row.set_selectable(False)
            header_row.get_style_context().add_class("category-header-row")
            header_label = Gtk.Label(label="Recently Used", xalign=0)
            header_label.get_style_context().add_class("category-header-label")
            header_row.add(header_label)
            yield header_row
            
            emoji_row = Gtk.ListBoxRow()
            emoji_row.set_selectable(False)
            emoji_row.add(self._create_emoji_flowbox(self.recent_emojis))
            yield emoji_row

        # --- All Emojis Section ---
        for category, subcategories in self.emojis_data.items():
            emojis_in_category = []
            
            for subcategory, emojis in subcategories.items():
                for emoji_info in emojis:
                    # Create a single searchable string with all relevant info
                    searchable_text = (
                        f"{category} {subcategory} {emoji_info['name']}"
                    ).lower().replace('-', ' ')
                    
                    if not query_tokens or self._tokenize_and_match(query_tokens, searchable_text):
                        emojis_in_category.append(emoji_info)
            
            if emojis_in_category:
                # Category Header Row
                header_row = Gtk.ListBoxRow()
                header_row.set_selectable(False)
                header_row.get_style_context().add_class("category-header-row")
                header_label = Gtk.Label(label=category, xalign=0)
                header_label.get_style_context().add_class("category-header-label")
                header_row.add(header_label)
                yield header_row
                
                # Emojis FlowBox Row
                emoji_row = Gtk.ListBoxRow()
                emoji_row.set_selectable(False)
                emoji_row.add(self._create_emoji_flowbox(emojis_in_category))
                yield emoji_row
