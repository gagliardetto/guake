import gi
import logging

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

# ############################################################################
# AI Chat Window Class
# ############################################################################
class AIChatWindow(Gtk.Window):
    def __init__(self, parent, ai_handler):
        super().__init__(title="AI Assistant", transient_for=parent, modal=False)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_default_size(350, 450)
        self.ai_handler = ai_handler
        self.editor_dialog = parent

        # --- UI Construction ---
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_vbox)
        main_vbox.get_style_context().add_class('ai-chat-window')

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.get_style_context().add_class('ai-chat-header')
        title = Gtk.Label(label="AI Assistant")
        header.pack_start(title, True, True, 0)

        close_button = Gtk.Button()
        close_button.set_image(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON))
        close_button.get_style_context().add_class('chat-close-button')
        close_button.connect("clicked", lambda w: self.hide())
        header.pack_end(close_button, False, False, 0)
        main_vbox.pack_start(header, False, False, 0)

        # Chat history
        chat_scrolled_window = Gtk.ScrolledWindow()
        chat_scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        chat_scrolled_window.set_vexpand(True)
        chat_scrolled_window.set_border_width(6)
        self.chat_history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        chat_scrolled_window.add(self.chat_history_box)
        main_vbox.pack_start(chat_scrolled_window, True, True, 0)

        # Input area
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        input_box.set_border_width(6)
        self.chat_input_entry = Gtk.Entry()
        self.chat_input_entry.set_placeholder_text("Ask something...")
        self.chat_input_entry.connect("activate", self.on_send_chat_message)
        input_box.pack_start(self.chat_input_entry, True, True, 0)

        send_button = Gtk.Button.new_with_mnemonic("_Send")
        send_button.connect("clicked", self.on_send_chat_message)
        input_box.pack_start(send_button, False, False, 0)
        main_vbox.pack_start(input_box, False, False, 0)

        self.connect("delete-event", self._on_delete_event)

    def _on_delete_event(self, widget, event):
        self.hide()
        return True

    def on_send_chat_message(self, widget):
        prompt = self.chat_input_entry.get_text()
        if not prompt.strip():
            return

        self.add_chat_message(f"You: {prompt}", "user-message")
        self.chat_input_entry.set_text("")
        
        # Use the AI handler to get a response
        if self.ai_handler:
            self.ai_handler.process_prompt(prompt, self.editor_dialog, self)

    def add_chat_message(self, text, style_class):
        label = Gtk.Label(label=text, xalign=0, yalign=0)
        label.set_line_wrap(True)
        label.get_style_context().add_class(style_class)
        self.chat_history_box.pack_start(label, False, False, 0)
        self.show_all()

        adj = self.chat_history_box.get_parent().get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper() - adj.get_page_size()))

# ############################################################################
# Example AI Handler
# ############################################################################
class MyAIHandler:
    def process_prompt(self, prompt, editor, chat_window):
        # This is where you would integrate with a real AI/LLM API
        logging.info(f"AI Handler received prompt: '{prompt}'")
        
        # Example: Read content from the editor
        editor_content = editor.get_raw_content()
        logging.info(f"Current editor content has {len(editor_content)} characters.")

        # Simulate a delayed response
        response = f"I've processed your request about '{prompt}'. The editor currently contains {len(editor_content)} characters."
        GLib.timeout_add(1000, lambda: chat_window.add_chat_message(f"AI: {response}", "bot-message"))

        # Example: Modify the editor content
        if "insert hello" in prompt.lower():
            editor.buffer.insert_at_cursor("hello world")
