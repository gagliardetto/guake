import gi
import re
from collections import OrderedDict
import logging
import shlex
import subprocess

gi.require_version("Gtk", "3.0")
gi.require_version("GtkSource", "4")
from gi.repository import GObject, Gtk, Gdk, GLib, Pango, GtkSource

# Import AI components safely
try:
    from guake.ai import AIChatWindow, MyAIHandler
    AI_AVAILABLE = True
except ImportError:
    logging.warning("ai.py not found. AI features will be disabled.")
    AI_AVAILABLE = False
    # Define placeholder classes if ai.py is not found to prevent crashes
    class AIChatWindow: pass
    class MyAIHandler: pass


# ############################################################################
# Helper class for detecting and converting between different casing conventions
# ############################################################################
class Casing:
    # regexes
    match_surround = re.compile(r'^([_-]*)(.*?)([_-]*)$')
    match_cases = OrderedDict([
        ('case', re.compile(r'^([a-z0-9_-]*[a-z]+[a-z0-9_-]*|[a-z][A-Za-z0-9]*)$')),
        ('CASE', re.compile(r'^[A-Z0-9_-]*[A-Z]+[A-Z0-9_-]*$')),
        ('Case', re.compile(r'^([\w-]*[A-Z][a-z][\w-]*|[A-Z][a-z][A-Za-z0-9]*)$'))
    ])
    match_separators = OrderedDict([
        (None, re.compile(r'^([A-Z0-9]+|[a-z0-9]+|[A-Z][a-z][a-z0-9]*)$')),
        ('', re.compile(r'^[A-Za-z0-9]+$')),
        ('_', re.compile(r'^[\w]+$')),
        ('-', re.compile(r'^[A-Za-z0-9-]+$'))
    ])
    
    def __init__(self, case=None, separator=None, prefix='', suffix=''):
        self.case = case
        self.separator = separator
        self.prefix = prefix
        self.suffix = suffix
        
    def is_keyword(self):
        return((self.case is not None) or (self.separator is not None))
    
    def detect(self, text):
        m = Casing.match_surround.match(text)
        if (m):
            self.prefix = m.group(1)
            text = m.group(2)
            self.suffix = m.group(3)
        for (key, pattern) in Casing.match_cases.items():
            if (pattern.match(text)):
                self.case = key
                break
        for (key, pattern) in Casing.match_separators.items():
            if (pattern.match(text)):
                self.separator = key
                break
        return(self)
    
    def split(self, text):
        m = Casing.match_surround.match(text)
        if (m):
            prefix = m.group(1)
            text = m.group(2)
            suffix = m.group(3)
        if (self.separator == '_'):
            return(tuple(text.split('_')))
        elif (self.separator == '-'):
            return(tuple(text.split('-')))
        elif (self.separator == ''):
            text = re.sub(r'([a-z])([A-Z])', r'\1,\2', text)
            text = re.sub(r'([A-Z])([A-Z][a-z])', r'\1,\2', text)
            return(tuple(text.lower().split(',')))
        else:
            return((text,))
            
    def join(self, words):
        if (self.case == 'case'):
            words = map(lambda s: s.lower(), words)
        elif (self.case == 'CASE'):
            words = map(lambda s: s.upper(), words)
        elif (self.case == 'Case'):
            words = map(lambda s: s.capitalize(), words)
        if ((self.separator == '') and (self.case == 'case')):
            words = list(words)
            words[1:] = map(lambda s: s.capitalize(), words[1:])
        if (self.separator is not None):
            inner = self.separator.join(words)
        else:
            inner = ''.join(words)
        return(self.prefix+inner+self.suffix)

# ############################################################################
# Helper class to manage a GtkTextTag anchored by GtkTextMarks
# ############################################################################
class MarkTag:
    def __init__(self, view, name, start_iter, end_iter):
        self.view = view
        self.doc = self.view.get_buffer()
        self.name = name
        self.start_mark = self.doc.create_mark(None, start_iter, True)
        self.end_mark = self.doc.create_mark(None, end_iter, False)
        self.do_move_marks()

    def get_start_iter(self):
        return(self.doc.get_iter_at_mark(self.start_mark))

    def get_end_iter(self):
        return(self.doc.get_iter_at_mark(self.end_mark))

    def get_length(self):
        return(self.get_end_iter().get_offset() - self.get_start_iter().get_offset())
            
    def get_text(self):
        return(self.doc.get_text(self.get_start_iter(), self.get_end_iter(), True))

    def set_text(self, text):
        start_iter = self.get_start_iter()
        end_iter = self.get_end_iter()
        self.doc.delete(start_iter, end_iter)
        self.doc.insert(start_iter, text)

    def move_marks(self, new_start_iter=None, new_end_iter=None):
        start_iter = self.doc.get_iter_at_mark(self.start_mark)
        end_iter = self.doc.get_iter_at_mark(self.end_mark)
        if (((new_start_iter is not None) and (new_start_iter.get_offset() != start_iter.get_offset())) or
            ((new_end_iter is not None) and (new_end_iter.get_offset() != end_iter.get_offset()))):
            self.do_move_marks(new_start_iter, new_end_iter)

    def do_move_marks(self, new_start_iter=None, new_end_iter=None):
        self.remove_tag()
        if (new_start_iter is not None):
            self.doc.move_mark(self.start_mark, new_start_iter)
        if (new_end_iter is not None):
            self.doc.move_mark(self.end_mark, new_end_iter)
        start_iter = self.doc.get_iter_at_mark(self.start_mark)
        end_iter = self.doc.get_iter_at_mark(self.end_mark)
        if (start_iter.get_offset() != end_iter.get_offset()):
            self.add_tag()
            self.start_mark.set_visible(False)
        else:
            self.start_mark.set_visible(self.name != 'tracker')

    def set_capturing_gravity(self, capture):
        if (self.start_mark.get_left_gravity() != capture):
            start_iter = self.doc.get_iter_at_mark(self.start_mark)
            visible = self.start_mark.get_visible()
            self.doc.delete_mark(self.start_mark)
            self.start_mark = self.doc.create_mark(None, start_iter, capture)
            self.start_mark.set_visible(visible)
            
    def remove(self):
        self.start_mark.set_visible(False)
        self.remove_tag()
        self.doc.delete_mark(self.start_mark)
        self.doc.delete_mark(self.end_mark)

    def add_tag(self):
        tag = self.get_tag()
        if (tag is not None):
            start_iter = self.doc.get_iter_at_mark(self.start_mark)
            end_iter = self.doc.get_iter_at_mark(self.end_mark)
            self.doc.apply_tag(tag, start_iter, end_iter)

    def remove_tag(self):
        if (self.doc.get_tag_table().lookup(self.name) is not None):
            start_iter = self.doc.get_iter_at_mark(self.start_mark)
            end_iter = self.doc.get_iter_at_mark(self.end_mark)
            self.doc.remove_tag_by_name(self.name, start_iter, end_iter)

    def get_tag(self):
        tag_table = self.doc.get_tag_table()
        tag = tag_table.lookup(self.name)
        if tag is None:
            if self.name == 'multicursor':
                tag = self.doc.create_tag(self.name, background="rgba(60, 80, 120, 0.6)")
            elif self.name == 'multicursor_match':
                tag = self.doc.create_tag(self.name, underline=Pango.Underline.SINGLE)
            elif self.name == 'tracker':
                tag = None
        return tag

# ############################################################################
# Helper class to manage a single extra cursor in the document
# ############################################################################
class Cursor:
    def __init__(self, view, start_iter, end_iter):
        self.view = view
        self.doc = self.view.get_buffer()
        self.tag = MarkTag(self.view, 'multicursor', start_iter, end_iter)
        self.tracker = None
        self.casing = None
        self.clipboard = ''
        self.line_offset = None

    def save_text(self):
        self.clipboard = self.tag.get_text()
        
    def scroll_onscreen(self):
        self.view.scroll_to_mark(self.tag.end_mark, 0.0, True, 0.5, 0.5)

    def remove(self):
        self.tag.remove()
        if (self.tracker is not None):
            self.tracker.remove()

    def insert(self, start_delta, text):
        start_iter = self.doc.get_iter_at_offset(self.tag.get_start_iter().get_offset() + start_delta)
        self.tag.set_capturing_gravity(False)
        self.doc.insert(start_iter, text)
        self.tag.set_capturing_gravity(True)

    def delete(self, start_delta, end_delta):
        start_iter = self.doc.get_iter_at_offset(self.tag.get_start_iter().get_offset() + start_delta)
        end_iter = self.doc.get_iter_at_offset(self.tag.get_end_iter().get_offset() + end_delta)
        had_length = (self.tag.get_length() > 0)
        self.doc.delete(start_iter, end_iter)
        if ((self.tag.get_length() > 0) != had_length):
            self.tag.do_move_marks()

    def move(self, step_size, count, extend_selection):
        # Get original positions
        start_iter = self.tag.get_start_iter()
        end_iter = self.tag.get_end_iter()

        if extend_selection:
            # A simple, robust assumption: moving left extends from the start,
            # and moving right extends from the end.
            if count < 0: # Moving left
                self.move_iter(start_iter, step_size, count)
            else: # Moving right
                self.move_iter(end_iter, step_size, count)
            
            self.tag.move_marks(start_iter, end_iter)

        else: # Not extending selection (collapsing or moving caret)
            has_selection = start_iter.get_offset() != end_iter.get_offset()
            
            # Determine the target position after collapse/move
            new_pos = None
            if count < 0: # Moving left
                new_pos = start_iter.copy() # Collapse to start
            else: # Moving right
                new_pos = end_iter.copy() # Collapse to end
            
            # If there was no selection to collapse, then we actually move the caret.
            if not has_selection:
                 self.move_iter(new_pos, step_size, count)

            self.tag.move_marks(new_pos, new_pos)

    def move_iter(self, pos, step_size, count):
        if step_size == Gtk.MovementStep.VISUAL_POSITIONS:
            pos.backward_cursor_positions(-count) if count < 0 else pos.forward_cursor_positions(count)
        elif step_size == Gtk.MovementStep.WORDS:
            for _ in range(abs(count)):
                pos.forward_word_end() if count > 0 else pos.backward_word_start()
        elif step_size == Gtk.MovementStep.DISPLAY_LINES:
            if self.line_offset is None:
                self.line_offset = pos.get_line_offset()
            pos.set_line(pos.get_line() + count)
            pos.set_line_offset(min(self.line_offset, pos.get_chars_in_line() - 1))
        elif step_size == Gtk.MovementStep.DISPLAY_LINE_ENDS:
            pos.set_line_offset(0) if count < 0 else pos.forward_to_line_end()
        
        if step_size != Gtk.MovementStep.DISPLAY_LINES:
            self.line_offset = None

# ############################################################################
# Undo/Redo Action Classes
# ############################################################################
class UndoAction:
    """Base class for an undoable action."""
    def undo(self): pass
    def redo(self): pass

class InsertAction(UndoAction):
    """Action for inserting text."""
    def __init__(self, buffer, offset, text):
        self.buffer = buffer
        self.offset = offset
        self.text = text

    def undo(self):
        start = self.buffer.get_iter_at_offset(self.offset)
        end = self.buffer.get_iter_at_offset(self.offset + len(self.text))
        self.buffer.delete(start, end)

    def redo(self):
        start = self.buffer.get_iter_at_offset(self.offset)
        self.buffer.insert(start, self.text)

class DeleteAction(UndoAction):
    """Action for deleting text."""
    def __init__(self, buffer, offset, text):
        self.buffer = buffer
        self.offset = offset
        self.text = text

    def undo(self):
        start = self.buffer.get_iter_at_offset(self.offset)
        self.buffer.insert(start, self.text)

    def redo(self):
        start = self.buffer.get_iter_at_offset(self.offset)
        end = self.buffer.get_iter_at_offset(self.offset + len(self.text))
        self.buffer.delete(start, end)

class CompositeAction(UndoAction):
    """A collection of actions to be treated as a single undo/redo step."""
    def __init__(self):
        self.actions = []

    def add(self, action):
        self.actions.append(action)

    def undo(self):
        for action in reversed(self.actions):
            action.undo()

    def redo(self):
        for action in self.actions:
            action.redo()

class MultiCursorUndoAction(UndoAction):
    """An action that also saves and restores the state of all cursors."""
    def __init__(self, dialog):
        self.dialog = dialog
        self.actions = []

        # Save pre-action state (offsets)
        insert_mark = self.dialog.doc.get_mark("insert")
        selection_bound_mark = self.dialog.doc.get_mark("selection_bound")
        self.primary_before = (insert_mark.get_buffer().get_iter_at_mark(insert_mark).get_offset(),
                               selection_bound_mark.get_buffer().get_iter_at_mark(selection_bound_mark).get_offset())
        # Use a dictionary to map cursor objects to their state for robustness
        self.secondary_before = {c: (c.tag.get_start_iter().get_offset(), c.tag.get_end_iter().get_offset()) for c in self.dialog.cursors}

    def add(self, action):
        self.actions.append(action)

    def save_post_state(self):
        # Save post-action state (offsets)
        insert_mark = self.dialog.doc.get_mark("insert")
        selection_bound_mark = self.dialog.doc.get_mark("selection_bound")
        self.primary_after = (insert_mark.get_buffer().get_iter_at_mark(insert_mark).get_offset(),
                              selection_bound_mark.get_buffer().get_iter_at_mark(selection_bound_mark).get_offset())
        # Use a dictionary to map cursor objects to their state for robustness
        self.secondary_after = {c: (c.tag.get_start_iter().get_offset(), c.tag.get_end_iter().get_offset()) for c in self.dialog.cursors}

    def undo(self):
        for action in reversed(self.actions):
            action.undo()
        
        # Restore cursors from saved offsets
        primary_insert_iter = self.dialog.doc.get_iter_at_offset(self.primary_before[0])
        primary_select_iter = self.dialog.doc.get_iter_at_offset(self.primary_before[1])
        self.dialog.doc.move_mark_by_name("insert", primary_insert_iter)
        self.dialog.doc.move_mark_by_name("selection_bound", primary_select_iter)

        # Restore secondary cursors from the dictionary
        for cursor, (start_offset, end_offset) in self.secondary_before.items():
            if cursor in self.dialog.cursors: # Check if cursor still exists
                start_iter = self.dialog.doc.get_iter_at_offset(start_offset)
                end_iter = self.dialog.doc.get_iter_at_offset(end_offset)
                cursor.tag.move_marks(start_iter, end_iter)
        
        self.dialog.view.scroll_to_mark(self.dialog.doc.get_mark("insert"), 0.0, True, 0.5, 0.5)

    def redo(self):
        for action in self.actions:
            action.redo()
        
        # Restore cursors from saved offsets
        primary_insert_iter = self.dialog.doc.get_iter_at_offset(self.primary_after[0])
        primary_select_iter = self.dialog.doc.get_iter_at_offset(self.primary_after[1])
        self.dialog.doc.move_mark_by_name("insert", primary_insert_iter)
        self.dialog.doc.move_mark_by_name("selection_bound", primary_select_iter)

        # Restore secondary cursors from the dictionary
        for cursor, (start_offset, end_offset) in self.secondary_after.items():
            if cursor in self.dialog.cursors: # Check if cursor still exists
                start_iter = self.dialog.doc.get_iter_at_offset(start_offset)
                end_iter = self.dialog.doc.get_iter_at_offset(end_offset)
                cursor.tag.move_marks(start_iter, end_iter)
            
        self.dialog.view.scroll_to_mark(self.dialog.doc.get_mark("insert"), 0.0, True, 0.5, 0.5)


# ############################################################################
# Undo/Redo Manager
# ############################################################################
class UndoManager:
    """Manages the undo/redo stack for a Gtk.TextBuffer."""
    def __init__(self, buffer):
        self.buffer = buffer
        self.undo_stack = []
        self.redo_stack = []
        self.undo_lock = False
        self.current_action_group = None
        self.editor = None # Will be monkey-patched

        self.buffer.connect("begin-user-action", self._on_begin_user_action)
        self.buffer.connect_after("end-user-action", self._on_end_user_action)
        self.buffer.connect("insert-text", self._on_insert_text)
        self.buffer.connect("delete-range", self._on_delete_range)

    def _on_begin_user_action(self, buf):
        if self.undo_lock: return
        
        if self.editor and self.editor.cursors:
            self.current_action_group = MultiCursorUndoAction(self.editor)
        else:
            self.current_action_group = CompositeAction()

    def _on_end_user_action(self, buf):
        if self.undo_lock: return
        if self.current_action_group and self.current_action_group.actions:
            if isinstance(self.current_action_group, MultiCursorUndoAction):
                self.current_action_group.save_post_state()

            self.undo_stack.append(self.current_action_group)
            self.redo_stack.clear()
            # Notify editor to update sensitivity
            if self.editor:
                self.editor.update_undo_redo_sensitivity()
        self.current_action_group = None

    def _on_insert_text(self, buf, iter, text, length):
        if self.undo_lock or self.current_action_group is None: return
        action = InsertAction(self.buffer, iter.get_offset(), text)
        self.current_action_group.add(action)

    def _on_delete_range(self, buf, start_iter, end_iter):
        if self.undo_lock or self.current_action_group is None: return
        text = self.buffer.get_text(start_iter, end_iter, False)
        action = DeleteAction(self.buffer, start_iter.get_offset(), text)
        self.current_action_group.add(action)

    def undo(self):
        if not self.undo_stack: return False
        action = self.undo_stack.pop()
        self.undo_lock = True
        action.undo()
        self.undo_lock = False
        self.redo_stack.append(action)
        return True

    def redo(self):
        if not self.redo_stack: return False
        action = self.redo_stack.pop()
        self.undo_lock = True
        action.redo()
        self.undo_lock = False
        self.undo_stack.append(action)
        return True

# ############################################################################
# Main Editor Dialog
# ############################################################################
class TextEditorDialog(Gtk.Dialog):
    def __init__(self, parent=None, ai_handler=None):
        super().__init__(
            title="Text Editor",
            parent=parent,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT, # REMOVED MODAL FLAG
        )
        self.ai_handler = ai_handler
        self.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        run_button = self.add_button("Run", Gtk.ResponseType.OK)
        
        # Style the run button to be green
        style_context = run_button.get_style_context()
        self.css_provider = Gtk.CssProvider()
        self.css_provider.load_from_data(b"""
            .suggested-action { background-color: #4CAF50; color: white; }
            .ai-chat-window {
                background-color: rgba(45, 45, 45, 0.95);
                color: white;
            }
            .ai-chat-header {
                background-color: rgba(255, 255, 255, 0.05);
                padding: 8px;
                font-weight: bold;
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            }
            .chat-close-button {
                background: none;
                border: none;
                padding: 0;
            }
            .user-message {
                color: #e0e0e0;
                font-size: small;
            }
            .bot-message {
                color: #a5d6a7; /* A light green for the bot */
                font-size: small;
            }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        run_button.get_style_context().add_class("suggested-action")

        screen = Gdk.Screen.get_default()
        screen_width = screen.get_width()
        screen_height = screen.get_height()
        four_fifths_width = screen_width // 5 * 4
        one_third_height = screen_height // 3
        self.set_default_size(four_fifths_width, one_third_height)

        # -- Logging Setup --
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        
        # Main layout box
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_content_area().add(main_vbox)

        # -- Toolbar --
        toolbar = Gtk.Toolbar()
        main_vbox.pack_start(toolbar, False, False, 0)
        
        self.undo_button = Gtk.ToolButton.new(None, "Undo")
        self.undo_button.set_icon_name("edit-undo-symbolic")
        self.undo_button.connect("clicked", self.undo)
        toolbar.insert(self.undo_button, -1)

        self.redo_button = Gtk.ToolButton.new(None, "Redo")
        self.redo_button.set_icon_name("edit-redo-symbolic")
        self.redo_button.connect("clicked", self.redo)
        toolbar.insert(self.redo_button, -1)
        
        toolbar.insert(Gtk.SeparatorToolItem(), -1)

        format_button = Gtk.ToolButton.new(None, "Format")
        format_button.set_icon_name("edit-indent-symbolic")
        format_button.connect("clicked", self.format_content)
        toolbar.insert(format_button, -1)

        toolbar.insert(Gtk.SeparatorToolItem(), -1)

        ask_ai_button = Gtk.ToolButton.new(None, "Ask AI")
        ask_ai_button.set_icon_name("view-reveal-symbolic")
        ask_ai_button.connect("clicked", self.toggle_ai_window)
        toolbar.insert(ask_ai_button, -1)
        if not self.ai_handler:
            ask_ai_button.set_sensitive(False)

        # -- Editor Setup --
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        main_vbox.pack_start(scrolled_window, True, True, 0)

        self.buffer = GtkSource.Buffer()
        self.view = GtkSource.View.new_with_buffer(self.buffer)
        self.view.set_show_line_numbers(True)
        self.view.set_auto_indent(True)
        self.view.set_highlight_current_line(True)
        
        lang_manager = GtkSource.LanguageManager.get_default()
        language = lang_manager.get_language('sh')
        if language:
            self.buffer.set_language(language)

        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = scheme_manager.get_scheme('oblivion')
        if not scheme:
            logging.warning("Could not find 'oblivion' theme, falling back to 'classic'.")
            scheme = scheme_manager.get_scheme('classic')
        if scheme:
            self.buffer.set_style_scheme(scheme)

        self.doc = self.buffer
        scrolled_window.add(self.view)
        
        # -- AI Chat Window --
        self.ai_chat_window = None

        # -- Undo/Redo Manager --
        self.undo_manager = UndoManager(self.buffer)
        self.undo_manager.editor = self

        # -- Feature Implementation --
        self._handlers = []
        self._in_user_action = False
        self._is_modifying_programmatically = False
        self._user_actions = []
        self._handled_paste = False
        self.clipboard = ''
        self.cursors = []
        self.matches = []
        self.tracker = None
        
        self.keymap = {
            '<Primary>d': self.match_cursor,
            '<Primary><Shift>d': self.match_cursor_fuzzy,
            '<Primary>u': self.unmatch_cursor,
            '<Primary>Up': self.column_select_up,
            '<Primary>Down': self.column_select_down,
            'Escape': self.clear_cursors,
            '<Primary>z': self.undo,
            '<Primary>y': self.redo,
            '<Primary><Shift>z': self.redo,
            '<Primary>i': self.format_content,
        }
        self.compile_keymap()
        self._hook_view_handlers()
        
        self.update_undo_redo_sensitivity()
        self.connect("destroy", self.on_destroy)
        self.show_all()

    def on_destroy(self, widget):
        if self.ai_chat_window:
            self.ai_chat_window.destroy()

    def toggle_ai_window(self, widget):
        if self.ai_chat_window is None and self.ai_handler and AI_AVAILABLE:
            self.ai_chat_window = AIChatWindow(self, self.ai_handler)
        
        if self.ai_chat_window:
            if self.ai_chat_window.get_visible():
                self.ai_chat_window.hide()
            else:
                self.ai_chat_window.show_all()

    def update_undo_redo_sensitivity(self):
        self.undo_button.set_sensitive(len(self.undo_manager.undo_stack) > 0)
        self.redo_button.set_sensitive(len(self.undo_manager.redo_stack) > 0)

    def undo(self, widget=None):
        if self.undo_manager.undo():
            self.update_undo_redo_sensitivity()

    def redo(self, widget=None):
        if self.undo_manager.redo():
            self.update_undo_redo_sensitivity()
        
    def set_initial_content(self, text):
        if not isinstance(text, str):
            text = str(text)
        self.buffer.set_text(text)
        self.undo_manager.undo_stack.clear()
        self.undo_manager.redo_stack.clear()
        self.update_undo_redo_sensitivity()

    def get_raw_content(self):
        start_iter = self.buffer.get_start_iter()
        end_iter = self.buffer.get_end_iter()
        return self.buffer.get_text(start_iter, end_iter, True)

    def get_escaped_content(self):
        """
        Returns the buffer content safely quoted for execution as a single
        shell command argument.
        """
        text = self.get_raw_content()
        # shlex.quote will handle all necessary escaping, including newlines,
        # to pass the entire script as a single argument to a shell like `bash -c`.
        return shlex.quote(text)

    def insert_at_cursor(self, text):
        """
        Programmatically inserts text at the primary cursor and all secondary cursors,
        making the action a single, undoable event. Replaces any active selections.
        """
        self._is_modifying_programmatically = True
        self.buffer.begin_user_action()

        # Collect all cursor positions (primary and secondary)
        all_selections = []
        # Add secondary cursors
        for cursor in self.cursors:
            all_selections.append(self.order_iters((cursor.tag.get_start_iter(), cursor.tag.get_end_iter())))
        # Add primary cursor
        all_selections.append(self.order_iters(self.get_selection_iters()))

        # Remove duplicates (in case primary selection overlaps with a secondary cursor)
        unique_selections = []
        seen_offsets = set()
        for start, end in all_selections:
            if start and end:
                offsets = (start.get_offset(), end.get_offset())
                if offsets not in seen_offsets:
                    unique_selections.append((start, end))
                    seen_offsets.add(offsets)

        # Sort all selections in reverse order by start position
        sorted_selections = sorted(unique_selections, key=lambda s: s[0].get_offset(), reverse=True)

        # Perform insertions
        for start, end in sorted_selections:
            self.buffer.delete(start, end)
            self.buffer.insert(start, text)

        self.buffer.end_user_action()
        self._is_modifying_programmatically = False

    def format_content(self, widget=None):
        """Formats the entire buffer content using an external tool (shfmt)."""
        try:
            original_content = self.get_raw_content()
            # Use shfmt to format the code. The '-i 2' flag sets indentation to 2 spaces.
            # The '-s' flag simplifies the code where possible.
            process = subprocess.run(
                ['shfmt', '-i', '2', '-s'],
                input=original_content,
                capture_output=True,
                text=True,
                check=True
            )
            formatted_content = process.stdout
            
            # Replace the entire buffer content in a single undoable action
            self.buffer.begin_user_action()
            self.buffer.set_text(formatted_content)
            self.buffer.end_user_action()

        except FileNotFoundError:
            logging.error("The 'shfmt' command is not installed or not in your PATH.")
        except subprocess.CalledProcessError as e:
            logging.error(f"Error while formatting: {e.stderr}")

    def compile_keymap(self):
        new_keymap = {}
        for (combo, action) in self.keymap.items():
            keyval, mods = Gtk.accelerator_parse(combo)
            new_keymap[(keyval, mods)] = action
        self.keymap = new_keymap
    
    def _hook_view_handlers(self):
        self.add_handler(self.view, 'event', self.on_event)
        self.add_handler(self.view, 'move-cursor', self.mc_move_cursor)
        self.add_handler(self.view, 'copy-clipboard', self.mc_save_clipboard)
        self.add_handler(self.view, 'cut-clipboard', self.mc_save_clipboard)
        self.add_handler(self.view, 'paste-clipboard', self.mc_paste_clipboard)

    def _hook_document_handlers(self):
        self.add_handler(self.doc, 'delete-range', self.delete)
        self.add_handler(self.doc, 'insert-text', self.insert)
        self.add_handler(self.doc, 'begin-user-action', self.begin_user_action)
        self.add_handler(self.doc, 'end-user-action', self.end_user_action)
        
    def _unhook_document_handlers(self):
        self.remove_handlers(self.doc)

    def add_handler(self, obj, signal, handler, when=None):
        if (when == 'after'):
            self._handlers.append((obj, obj.connect_after(signal, handler)))
        else:
            self._handlers.append((obj, obj.connect(signal, handler)))

    def remove_handlers(self, remove_obj=None):
        kept = []
        for (obj, handler_id) in self._handlers:
            if ((remove_obj is None) or (remove_obj == obj)):
                if obj.handler_is_connected(handler_id):
                    obj.disconnect(handler_id)
            else:
                kept.append((obj, handler_id))
        self._handlers = kept

    def on_event(self, view, event):
        if event.type == Gdk.EventType.KEY_PRESS:
            return self.on_key_press(view, event)
        elif event.type == Gdk.EventType.BUTTON_PRESS:
            if (event.get_state()[1] & Gdk.ModifierType.CONTROL_MASK):
                (x, y) = self.view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
                found, pos = self.view.get_iter_at_location(x, y)
                if found:
                    self.add_cursor(pos, pos)
                return True
            else:
                self.clear_cursors()
        return False

    def on_key_press(self, view, event):
        keyval = event.keyval
        mods = event.state & Gtk.accelerator_get_default_mod_mask()
        if (keyval, mods) in self.keymap:
            self.keymap[(keyval, mods)]()
            return True
        return False

    def order_iters(self, iters):
        if iters is None or iters[0] is None or iters[1] is None:
            return (None, None)
        return (iters[1], iters[0]) if iters[0].get_offset() > iters[1].get_offset() else iters

    def get_selection_iters(self):
        insert_iter = self.doc.get_iter_at_mark(self.doc.get_mark("insert"))
        selection_bound_iter = self.doc.get_iter_at_mark(self.doc.get_mark("selection_bound"))
        return (insert_iter, selection_bound_iter)
    
    def match_cursor_fuzzy(self): self.match_cursor(fuzzy=True)
    def match_cursor(self, fuzzy=False):
        (sel_start, sel_end) = self.order_iters(self.get_selection_iters())
        if not sel_start: return
        text = self.doc.get_text(sel_start, sel_end, True)
        if not text: return
        
        if self.cursors:
            search_start = self.cursors[-1].tag.get_end_iter()
        else:
            self.tag_all_matches(text, fuzzy)
            search_start = sel_end
        
        search_end = sel_start if search_start.get_offset() < sel_start.get_offset() else None
        match = self.get_next_match(text, search_start, search_end, fuzzy)
        
        if (match is None and search_start.get_offset() >= sel_end.get_offset()):
            match = self.get_next_match(text, self.doc.get_start_iter(), sel_start, fuzzy)
        
        if match:
            self.add_cursor(match[0], match[1])
            self.cursors[-1].scroll_onscreen()

    def tag_all_matches(self, text, fuzzy):
        (sel_start, sel_end) = self.order_iters(self.get_selection_iters())
        if not sel_start: return
        start_iter = self.doc.get_start_iter()
        while True:
            match = self.get_next_match(text, start_iter, None, fuzzy)
            if not match: break
            start_iter = match[1]
            if match[0].get_offset() == sel_start.get_offset(): continue
            self.matches.append(MarkTag(self.view, 'multicursor_match', match[0], match[1]))
    
    def clear_matches(self):
        for match in self.matches: match.remove()
        self.matches = []

    def get_next_match(self, text, search_start, search_end, fuzzy):
        if fuzzy:
            flags = Gtk.TextSearchFlags.CASE_INSENSITIVE
            casing = Casing().detect(text)
            words = casing.split(text)
            alternatives = {text, Casing('lower', '_').join(words), Casing('lower', '-').join(words), Casing('lower', '').join(words)}
            
            earliest = None
            for alt in alternatives:
                if not alt: continue
                match = search_start.forward_search(alt, flags, search_end)
                if match and (earliest is None or match[0].get_offset() < earliest[0].get_offset()):
                    earliest = match
            return earliest
        else:
            return search_start.forward_search(text, 0, search_end)
    
    def unmatch_cursor(self):
        self.remove_cursor(-1)
        if self.cursors:
            self.cursors[-1].scroll_onscreen()
        else:
            self.view.scroll_to_mark(self.doc.get_insert(), 0.0, True, 0.5, 0.5)
    
    def column_select_up(self): self.column_select(-1)
    def column_select_down(self): self.column_select(1)
    def column_select(self, line_delta):
        (sel_start, sel_end) = self.order_iters(self.get_selection_iters())
        if not sel_start: return
        
        sel_line = sel_start.get_line()
        min_line = max_line = sel_line
        for cursor in self.cursors:
            line = cursor.tag.get_start_iter().get_line()
            min_line, max_line = min(line, min_line), max(line, max_line)
        
        start_line = None
        if line_delta < 0 and max_line == sel_line: start_line = min_line
        elif line_delta > 0 and min_line == sel_line: start_line = max_line
        
        if start_line is None:
            self.unmatch_cursor()
            return
            
        line = start_line + line_delta
        start_iter = sel_start.copy()
        start_iter.set_line(line)
        start_iter.set_line_offset(min(sel_start.get_line_offset(), start_iter.get_chars_in_line()))
        
        end_iter = sel_end.copy()
        end_iter.set_line(line + (sel_end.get_line() - sel_start.get_line()))
        end_iter.set_line_offset(min(sel_end.get_line_offset(), end_iter.get_chars_in_line()))
        
        if start_iter.get_line() != start_line:
            self.add_cursor(start_iter, end_iter)
            self.cursors[-1].scroll_onscreen()
    
    def add_cursor(self, start_iter, end_iter):
        if not self.cursors: self._hook_document_handlers()
        self.cursors.append(Cursor(self.view, start_iter, end_iter))

    def remove_cursor(self, index):
        if self.cursors:
            self.cursors[index].remove()
            del self.cursors[index]
            if not self.cursors: self._unhook_document_handlers()

    def clear_cursors(self, *args):
        if self.cursors:
            while self.cursors: self.remove_cursor(-1)
        self.clear_matches()
    
    def insert(self, doc, start, text, length):
        if self._in_user_action and not self._is_modifying_programmatically:
            (sel_start, sel_end) = self.order_iters(self.get_selection_iters())
            if not sel_start: return
            start_delta = start.get_offset() - sel_start.get_offset()
            self.store_user_action(self.mc_insert, (start_delta, text))

    def delete(self, doc, start, end):
        if self._in_user_action and not self._is_modifying_programmatically:
            (start, end) = self.order_iters((start, end))
            if not start: return
            (sel_start, sel_end) = self.order_iters(self.get_selection_iters())
            if not sel_start: return
            start_delta = start.get_offset() - sel_start.get_offset()
            end_delta = end.get_offset() - sel_end.get_offset()
            self.store_user_action(self.mc_delete, (start_delta, end_delta))

    def begin_user_action(self, doc=None):
        self._user_actions = []
        self._in_user_action = True

    def store_user_action(self, action, args):
        if self._in_user_action:
            self._user_actions.append((action, args))

    def end_user_action(self, doc=None):
        if self._in_user_action:
            self._in_user_action = False
            
            actions_to_run = self._user_actions[:]
            self._user_actions = []

            if not actions_to_run:
                return

            self.clear_matches()
            
            self._is_modifying_programmatically = True
            
            for action, args in actions_to_run:
                action(*args)
            
            self._is_modifying_programmatically = False
        
    def mc_insert(self, start_delta, text):
        sorted_cursors = sorted(self.cursors, key=lambda c: c.tag.get_start_iter().get_offset(), reverse=True)
        if self._handled_paste and text == self.clipboard:
            for cursor in sorted_cursors:
                cursor.insert(start_delta, cursor.clipboard or text)
        else:
            for cursor in sorted_cursors:
                cursor.insert(start_delta, text)
        self._handled_paste = False

    def mc_delete(self, start_delta, end_delta):
        sorted_cursors = sorted(self.cursors, key=lambda c: c.tag.get_start_iter().get_offset(), reverse=True)
        for cursor in sorted_cursors:
            cursor.delete(start_delta, end_delta)

    def mc_move_cursor(self, view, step_size, count, extend_selection):
        self.clear_matches()
        if step_size in (Gtk.MovementStep.BUFFER_ENDS, Gtk.MovementStep.PAGES):
            self.clear_cursors()
            return
        for cursor in self.cursors:
            cursor.move(step_size, count, extend_selection)
            
    def mc_save_clipboard(self, view):
        (sel_start, sel_end) = self.order_iters(self.get_selection_iters())
        if not sel_start: return
        self.clipboard = self.doc.get_text(sel_start, sel_end, True)
        for cursor in self.cursors: cursor.save_text()
            
    def mc_paste_clipboard(self, view):
        self._handled_paste = True
