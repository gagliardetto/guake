import gi
import re

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

class TextEditorDialog(Gtk.Dialog):
    """
    A text editor dialog with some Sublime Text-like features.
    - Ctrl+Click: Add a new cursor/selection.
    - Ctrl+D: Select the next occurrence of the selected text.
    """
    def __init__(self, parent=None):
        super().__init__(
            title="Text Editor",
            parent=parent,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        self.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        self.set_default_size(800, 600)

        # -- Editor Setup --
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        self.get_content_area().add(scrolled_window)

        self.view = Gtk.TextView()
        self.view.set_monospace(True)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.buffer = self.view.get_buffer()
        scrolled_window.add(self.view)

        # -- Feature Implementation --
        self.selections = [] # Stores (start_mark, end_mark) for each selection
        self.is_editing_programmatically = False
        self.last_keyval = None
        self.deleted_text_cache = ""

        # Create a tag for styling the secondary selections
        self.selection_tag = self.buffer.create_tag(
            "secondary_selection",
            background="rgba(60, 80, 120, 0.6)"
        )
        # Create a tag for styling multiple cursors
        self.cursor_tag = self.buffer.create_tag(
            "secondary_cursor",
            background="rgba(255, 255, 255, 0.8)"
        )

        # Connect signals
        self.view.connect("key-press-event", self.on_key_press)
        self.view.connect("button-press-event", self.on_button_press)
        self.buffer.connect("begin-user-action", self.on_begin_user_action)
        self.buffer.connect("end-user-action", self.on_end_user_action)
        self.buffer.connect_after("insert-text", self.on_insert_text)
        # Connect 'delete-range' with a handler to cache text *before* deletion
        self.buffer.connect("delete-range", self.on_before_delete_range)
        self.buffer.connect_after("delete-range", self.on_after_delete_range)


        self.show_all()

    def on_button_press(self, widget, event):
        """Handles Ctrl+Click to add a new cursor."""
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:
            state = event.state
            if state & Gdk.ModifierType.CONTROL_MASK:
                x, y = event.x, event.y
                iter_at_click = self.view.get_iter_at_location(int(x), int(y))[1]

                start_mark = self.buffer.create_mark(None, iter_at_click, True)
                end_mark = self.buffer.create_mark(None, iter_at_click, True)
                self.selections.append((start_mark, end_mark))

                self.update_selection_tags()
                return True
        return False

    def on_key_press(self, widget, event):
        """Handles Ctrl+D for multi-selection and tracks deletion keys."""
        self.last_keyval = event.keyval
        state = event.state

        if state & Gdk.ModifierType.CONTROL_MASK and self.last_keyval == Gdk.KEY_d:
            self.add_next_selection()
            return True

        if self.last_keyval in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right):
            if len(self.selections) > 1:
                self.clear_selections()
        
        return False

    def add_next_selection(self):
        """Finds and selects the next occurrence of the current selection."""
        buffer = self.buffer
        selection_bounds = buffer.get_selection_bounds()
        has_selection = bool(selection_bounds)

        if has_selection:
            start, end = selection_bounds
        else:
            cursor_iter = buffer.get_iter_at_mark(buffer.get_insert())
            if not cursor_iter.starts_word():
                cursor_iter.backward_word_start()
            
            start = cursor_iter.copy()
            cursor_iter.forward_word_end()
            end = cursor_iter.copy()
            buffer.select_range(start, end)
            
            selection_bounds = buffer.get_selection_bounds()
            has_selection = bool(selection_bounds)
            if has_selection:
                start, end = selection_bounds

        if has_selection:
            if not self.selections:
                start_mark = buffer.create_mark(None, start, True)
                end_mark = buffer.create_mark(None, end, False)
                self.selections.append((start_mark, end_mark))

            # Correctly get the iterator from the mark
            last_selection_end_mark = self.selections[-1][1]
            last_selection_end = buffer.get_iter_at_mark(last_selection_end_mark)
            search_text = buffer.get_text(start, end, False)
            
            next_match = last_selection_end.forward_search(search_text, 0, None)
            if next_match:
                match_start, match_end = next_match
                
                start_mark = buffer.create_mark(None, match_start, True)
                end_mark = buffer.create_mark(None, match_end, False)
                self.selections.append((start_mark, end_mark))
                
                buffer.select_range(match_start, match_end)
                self.view.scroll_to_mark(buffer.get_insert(), 0.1, True, 0.5, 0.5)

        self.update_selection_tags()

    def update_selection_tags(self):
        """Applies tags to all stored selections."""
        buffer = self.buffer
        buffer.remove_tag_by_name("secondary_selection", buffer.get_start_iter(), buffer.get_end_iter())
        buffer.remove_tag_by_name("secondary_cursor", buffer.get_start_iter(), buffer.get_end_iter())

        for start_mark, end_mark in self.selections:
            start_iter = buffer.get_iter_at_mark(start_mark)
            end_iter = buffer.get_iter_at_mark(end_mark)
            
            if start_iter.equal(end_iter):
                cursor_end_iter = start_iter.copy()
                if not cursor_end_iter.is_end():
                    cursor_end_iter.forward_char()
                buffer.apply_tag(self.cursor_tag, start_iter, cursor_end_iter)
            else:
                buffer.apply_tag(self.selection_tag, start_iter, end_iter)

    def clear_selections(self):
        """Removes all but the primary selection."""
        for start_mark, end_mark in self.selections:
            self.buffer.delete_mark(start_mark)
            self.buffer.delete_mark(end_mark)
        self.selections = []
        self.update_selection_tags()

    def on_begin_user_action(self, buffer):
        pass

    def on_end_user_action(self, buffer):
        self.last_keyval = None

    def on_insert_text(self, buffer, location, text, length):
        """Handle text insertion for multiple cursors."""
        if self.is_editing_programmatically or len(self.selections) <= 1:
            return

        self.is_editing_programmatically = True
        buffer.begin_user_action()
        
        start_del = location.copy()
        start_del.backward_chars(length)
        buffer.delete(start_del, location)

        for start_mark, end_mark in reversed(self.selections):
            start = buffer.get_iter_at_mark(start_mark)
            end = buffer.get_iter_at_mark(end_mark)
            buffer.delete(start, end)
            buffer.insert(start, text)

        buffer.end_user_action()
        self.is_editing_programmatically = False
        self.update_selection_tags()

    def on_before_delete_range(self, buffer, start, end):
        """Cache the text that is about to be deleted."""
        if not self.is_editing_programmatically and len(self.selections) > 1:
            self.deleted_text_cache = buffer.get_text(start, end, False)

    def on_after_delete_range(self, buffer, start, end):
        """Handle text deletion for multiple cursors after the fact."""
        if self.is_editing_programmatically or len(self.selections) <= 1:
            return

        self.is_editing_programmatically = True
        buffer.begin_user_action()

        # Undo the original deletion by re-inserting the cached text
        buffer.insert(start, self.deleted_text_cache)

        # Replicate the deletion across all selections
        for start_mark, end_mark in reversed(self.selections):
            s_iter = buffer.get_iter_at_mark(start_mark)
            e_iter = buffer.get_iter_at_mark(end_mark)

            if not s_iter.equal(e_iter):
                buffer.delete(s_iter, e_iter)
            else:
                if self.last_keyval == Gdk.KEY_BackSpace:
                    if not s_iter.is_start():
                        s_iter.backward_char()
                        buffer.delete(s_iter, e_iter)
                elif self.last_keyval == Gdk.KEY_Delete:
                    if not e_iter.is_end():
                        e_iter.forward_char()
                        buffer.delete(s_iter, e_iter)

        buffer.end_user_action()
        self.is_editing_programmatically = False
        self.update_selection_tags()
