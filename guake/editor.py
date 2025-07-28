import gi
import re
from collections import OrderedDict

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk, Gdk, GLib, Pango

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
        start_iter = self.tag.get_start_iter()
        end_iter = self.tag.get_end_iter()
        if (extend_selection):
            sel_start = self.doc.get_iter_at_mark(self.doc.get_insert())
            sel_end = self.doc.get_iter_at_mark(self.doc.get_selection_bound())
            sel_delta = sel_start.get_offset() - sel_end.get_offset()
            move_end = (count > 0)
            if (sel_delta != 0):
                move_end = (sel_delta > 0)
            if (move_end):
                self.move_iter(end_iter, step_size, count)
            else:
                self.move_iter(start_iter, step_size, count)
        else:
            # This block handles all non-extending moves.
            # If there is a selection, collapse it in the direction of movement.
            if start_iter.get_offset() != end_iter.get_offset():
                if count < 0:
                    end_iter = start_iter.copy()
                else: # Handles count > 0 and count == 0
                    start_iter = end_iter.copy()
            
            # Now, move the (now collapsed) cursor.
            # Since start_iter and end_iter are the same, we only need to move one
            # and then sync the other.
            self.move_iter(start_iter, step_size, count)
            end_iter = start_iter.copy()

        self.tag.move_marks(start_iter, end_iter)

    def move_iter(self, pos, step_size, count):
        if step_size == Gtk.MovementStep.LOGICAL_POSITIONS:
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
# Main Editor Dialog
# ############################################################################
class TextEditorDialog(Gtk.Dialog):
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
        self.buffer = self.view.get_buffer()
        self.doc = self.buffer # Alias for compatibility with inspiration code
        scrolled_window.add(self.view)

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
            'Escape': self.clear_cursors
        }
        self.compile_keymap()
        self._hook_view_handlers()
        self.show_all()

    def set_text(self, text):
        """
        Safely sets the buffer text, ensuring it's a string.
        This method should be used by external code to populate the editor
        to avoid TypeErrors if the content is not a string.
        """
        if not isinstance(text, str):
            text = str(text)
        self.buffer.set_text(text)

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
        self.add_handler(self.doc, 'end-user-action', self.end_user_action, 'after')
        
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
            
            self.doc.begin_user_action()
            for action, args in actions_to_run:
                action(*args)
            self.doc.end_user_action()
            
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
