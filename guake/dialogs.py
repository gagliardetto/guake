import gi
import re

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
gi.require_version("Gio", "2.0")
from gi.repository import Gio
from gi.repository import Vte
from gi.repository import Pango

class RenameDialog(Gtk.Dialog):
    def __init__(self, window, current_name):
        super().__init__(
            _("Rename tab"),
            window,
            Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
            (
                Gtk.STOCK_CANCEL,
                Gtk.ResponseType.REJECT,
                Gtk.STOCK_OK,
                Gtk.ResponseType.ACCEPT,
            ),
        )
        self.entry = Gtk.Entry()
        self.entry.set_text(current_name)
        self.entry.set_property("can-default", True)
        self.entry.show()

        vbox = Gtk.VBox()
        vbox.set_border_width(6)
        vbox.show()

        self.set_size_request(300, -1)
        self.vbox.pack_start(vbox, True, True, 0)
        self.set_border_width(4)
        self.set_default_response(Gtk.ResponseType.ACCEPT)
        self.add_action_widget(self.entry, Gtk.ResponseType.ACCEPT)
        self.entry.reparent(vbox)

    def get_text(self):
        return self.entry.get_text()


class PromptQuitDialog(Gtk.MessageDialog):

    """Prompts the user whether to quit/close a tab."""

    def __init__(self, parent, procs, tabs, notebooks):
        super().__init__(
            parent,
            Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
            Gtk.MessageType.QUESTION,
            Gtk.ButtonsType.YES_NO,
        )

        if tabs == -1:
            primary_msg = _("Do you want to close the tab?")
            tab_str = ""
            notebooks_str = ""
        else:
            primary_msg = _("Do you really want to quit Guake?")
            if tabs == 1:
                tab_str = _(" and one tab open")
            else:
                tab_str = _(" and {0} tabs open").format(tabs)
            if notebooks > 1:
                notebooks_str = _(" on {0} workspaces").format(notebooks)
            else:
                notebooks_str = ""

        if not procs:
            proc_str = _("There are no processes running")
        elif len(procs) == 1:
            proc_str = _("There is a process still running")
        else:
            proc_str = _("There are {0} processes still running").format(len(procs))

        if procs:
            proc_list = "\n\n" + "\n".join(f"{name} ({pid})" for pid, name in procs)
        else:
            proc_list = ""

        self.set_markup(primary_msg)
        self.format_secondary_markup(f"<b>{proc_str}{tab_str}{notebooks_str}.</b>{proc_list}")

    def quit(self):
        """Run the "are you sure" dialog for quitting Guake"""
        # Stop an open "close tab" dialog from obstructing a quit
        response = self.run() == Gtk.ResponseType.YES
        self.destroy()
        # Keep Guake focussed after dismissing tab-close prompt
        # if tab == -1:
        #     self.window.present()
        return response

    def close_tab(self):
        response = self.run() == Gtk.ResponseType.YES
        self.destroy()
        # Keep Guake focussed after dismissing tab-close prompt
        # if tab == -1:
        #     self.window.present()
        return response


class PromptResetColorsDialog(Gtk.MessageDialog):

    """Prompts the user whether to reset tab colors."""

    def __init__(self, parent):
        super().__init__(
            parent,
            Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
            Gtk.MessageType.QUESTION,
            Gtk.ButtonsType.YES_NO,
        )

        primary_msg = _("Do you want to reset custom colors for this tab?")

        self.set_markup(primary_msg)

    def reset_tab_custom_colors(self):
        """Run the "are you sure" dialog for resetting tab colors"""
        # Stop an open "close tab" dialog from obstructing a quit
        response = self.run() == Gtk.ResponseType.YES
        self.destroy()
        # Keep Guake focussed after dismissing tab-close prompt
        # if tab == -1:
        #     self.window.present()
        return response


class SaveTerminalDialog(Gtk.FileChooserDialog):
    def __init__(self, terminal, window):
        super().__init__(
            _("Save to..."),
            window,
            Gtk.FileChooserAction.SAVE,
            (
                Gtk.STOCK_CANCEL,
                Gtk.ResponseType.CANCEL,
                Gtk.STOCK_SAVE,
                Gtk.ResponseType.OK,
            ),
        )
        self.set_default_response(Gtk.ResponseType.OK)
        self.terminal = terminal
        self.parent_window = window

    def run(self):
        vte_terminal = self.terminal  # Assuming self.terminal is a VTE Terminal object
 
        filter = Gtk.FileFilter()
        filter.set_name(_("All files"))
        filter.add_pattern("*")
        self.add_filter(filter)
        filter = Gtk.FileFilter()
        filter.set_name(_("Text and Logs"))
        filter.add_pattern("*.log")
        filter.add_pattern("*.txt")
        self.add_filter(filter)

        response = super().run()
        if response == Gtk.ResponseType.OK:
            # Get the filename
            filename = self.get_filename()
            # Create a new stream to write to
            output_stream = Gio.MemoryOutputStream.new_resizable()

            # Write the contents of the terminal to the stream
            flags = Vte.WriteFlags.DEFAULT
            vte_terminal.write_contents_sync(
                output_stream, flags, None
            )
            # Close the stream
            output_stream.close()

            # Steal the data as GLib.Bytes
            written_data = output_stream.steal_as_bytes()

            # Create a Gio.File object for the destination
            file = Gio.File.new_for_path(filename)

            # Write the contents
            file.replace_contents_bytes_async(
                written_data,
                None,
                False,
                Gio.FileCreateFlags.REPLACE_DESTINATION,
                None,
                lambda file, result: file.replace_contents_finish(result),
            )
        self.destroy()


class MyListBoxRow(Gtk.ListBoxRow):
    def __init__(self, tab_label, tab_cwd, page_index, workspace_id, workspace_name):
        super().__init__()
        self.page_index = page_index
        self.workspace_id = workspace_id
        self.workspace_name = workspace_name

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(20)
        box.set_margin_end(20)

        label = Gtk.Label()
        label.set_markup(f"<span font_desc='Iosevka, Arial, Helvetica, sans-serif Bold 15'>{tab_label}</span>")
        label.set_xalign(0)
        
        ws_label = Gtk.Label()
        ws_label.set_markup(f"<small>{workspace_name}</small>")
        ws_label.set_xalign(0)
        ws_label.set_size_request(150, -1)

        cwd = Gtk.Label()
        cwd.set_markup(f"<span font_desc='Iosevka Term, Arial, Helvetica, sans-serif 15'>{tab_cwd}</span>")
        cwd.set_xalign(1)
        cwd.set_hexpand(True)
        cwd.set_ellipsize(Pango.EllipsizeMode.START)

        box.pack_start(label, True, True, 0)
        box.pack_start(ws_label, False, False, 0)
        box.pack_start(cwd, True, True, 0)

        self.add(box)

class QuickTabNavigationDialog(Gtk.Dialog):
    def __init__(self, window, notebook_manager, workspace_manager):
        super().__init__(
            _("Quick Tab Navigation"),
            window,
            Gtk.DialogFlags.MODAL,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
             Gtk.STOCK_OK, Gtk.ResponseType.OK)
        )

        self.notebook_manager = notebook_manager
        self.workspace_manager = workspace_manager
        self.entry = Gtk.Entry()
        self.list_box = Gtk.ListBox()
        self.selected_item = None

        screen = Gdk.Screen.get_default()
        screen_width = screen.get_width()
        screen_height = screen.get_height()

        four_fifths_width = screen_width // 5 * 4
        one_third_height = screen_height // 3
        row_height = 30

        min_height = one_third_height + row_height

        self.set_default_size(four_fifths_width, -1)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.add(self.list_box)
        scrolled_window.set_min_content_height(min_height)

        self.entry.set_placeholder_text("Search or filter tabs (w:workspace)...")
        self.entry.set_hexpand(True)

        box = self.get_content_area()
        box.add(self.entry)
        box.add(scrolled_window)

        self.entry.connect("changed", self.on_entry_changed)
        self.entry.connect("key-press-event", self.on_entry_key_press)
        self.list_box.connect("key-press-event", self.on_key_press_on_row)
        self.list_box.connect("row-selected", self.on_row_selected)
        self.list_box.connect("row-activated", self.on_row_activated)

        self.populate_list()
        self.show_all()
        self.visible_rows = []
        self.update_visible_rows()

    def populate_list(self):
        term_to_ws = {}
        for ws in self.workspace_manager.get_all_workspaces():
            for term_uuid in ws.get("terminals", []):
                term_to_ws[term_uuid] = ws

        page_index = 0
        for notebook in self.notebook_manager.iter_notebooks():
            for terminal in notebook.iter_terminals():
                tab_label = notebook.get_tab_label(notebook.get_nth_page(page_index)).get_text()  
                tab_cwd = terminal.get_current_directory()
                
                ws = term_to_ws.get(str(terminal.uuid))
                ws_name = ws['name'] if ws else "Unknown"
                ws_id = ws['id'] if ws else None

                row = MyListBoxRow(tab_label, tab_cwd, page_index, ws_id, ws_name)
                self.list_box.add(row)
                page_index += 1

    def on_entry_changed(self, widget):
        full_filter_text = widget.get_text().lower()
        
        ws_filter_match = re.search(r'w:(\S*)', full_filter_text)
        ws_filter = ws_filter_match.group(1) if ws_filter_match else None
        
        text_filter = re.sub(r'w:\S*\s*', '', full_filter_text).strip()

        for row in self.list_box.get_children():
            hbox = row.get_child()
            label, ws_label, cwd = hbox.get_children()
            label_text = label.get_text().lower()
            cwd_text = cwd.get_text().lower()
            ws_text = row.workspace_name.lower()

            ws_match = not ws_filter or ws_filter in ws_text
            text_match = not text_filter or text_filter in label_text or text_filter in cwd_text

            if ws_match and text_match:
                row.show()
            else:
                row.hide()
        self.update_visible_rows()

    def update_visible_rows(self):
        self.visible_rows = [row for row in self.list_box.get_children() if row.is_visible()]
        
    def on_key_press_on_row(self, widget, event):
        selected_row = self.list_box.get_selected_row()
        if event.keyval == Gdk.KEY_Return:
            self.on_row_activated(self.list_box, selected_row)
        elif event.keyval == Gdk.KEY_Up:
            if selected_row and self.visible_rows and selected_row == self.visible_rows[0]:
                self.list_box.unselect_row(selected_row)
                self.entry.grab_focus()
                self.entry.set_position(-1)

    def on_row_selected(self, listbox, row):
        if row:
            self.selected_item = {'page_index': row.page_index, 'workspace_id': row.workspace_id}
    
    def get_selection(self):
        return self.selected_item

    def on_row_activated(self, listbox, row):
        self.selected_item = {'page_index': row.page_index, 'workspace_id': row.workspace_id}
        self.response(Gtk.ResponseType.OK)

    def on_entry_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Return:
            if len(self.visible_rows) == 1:
                self.list_box.select_row(self.visible_rows[0])
                self.on_row_activated(self.list_box, self.visible_rows[0])
        elif event.keyval == Gdk.KEY_Down:
            if self.visible_rows:
                self.list_box.select_row(self.visible_rows[0])
                self.list_box.grab_focus()

class NewWorkspacePlaceholder(Gtk.Box):
    def __init__(self, guake_app, workspace_id):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.guake_app = guake_app
        self.workspace_id = workspace_id

        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .new-workspace-placeholder {
                background-color: rgba(0, 0, 0, 0.2);
                border-radius: 8px;
                padding: 20px;
            }
        """)
        self.get_style_context().add_class("new-workspace-placeholder")
        self.get_style_context().add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        
        label = Gtk.Label(label="This is a new workspace; what would you like to do next?")
        self.pack_start(label, False, False, 10)

        new_term_button = Gtk.Button.new_with_label("Create a new terminal")
        new_term_button.connect("clicked", self.on_create_terminal_clicked)
        self.pack_start(new_term_button, False, False, 0)

    def on_create_terminal_clicked(self, widget):
        self.guake_app.add_tab_to_workspace(self.workspace_id)
