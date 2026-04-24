import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

from guake.customcommands import CustomCommands

import logging

log = logging.getLogger(__name__)


def _add_block_menu_items(menu, terminal, block):
    """Add command-block-specific items to the context menu."""
    cmd = block.command or ""
    cmd_display = cmd if len(cmd) <= 40 else cmd[:37] + "..."

    # Header showing which command
    header = Gtk.MenuItem(label=f"⌘  {cmd_display}")
    header.set_sensitive(False)
    menu.add(header)

    # Copy command
    mi = Gtk.MenuItem(label=_("Copy Command"))
    mi.connect("activate", lambda *a: _copy_to_clipboard(terminal, cmd))
    menu.add(mi)

    # Re-run command
    mi = Gtk.MenuItem(label=_("Re-run Command"))
    mi.connect("activate", lambda *a: terminal.feed_child(cmd + "\n"))
    menu.add(mi)

    # Copy output
    if block.command_row is not None and block.end_row is not None:
        output_lines = block.end_row - block.command_row - 1
        if output_lines > 0:
            mi = Gtk.MenuItem(label=_("Copy Output (%d lines)") % output_lines)
            mi.connect("activate", lambda *a: _copy_block_output(terminal, block))
            menu.add(mi)

    # Show exit code and duration
    info_parts = []
    if block.exit_code is not None:
        info_parts.append(f"exit {block.exit_code}")
    dur = block.format_duration()
    if dur:
        info_parts.append(dur)
    if info_parts:
        mi = Gtk.MenuItem(label="    ".join(info_parts))
        mi.set_sensitive(False)
        menu.add(mi)


def _copy_to_clipboard(terminal, text):
    """Copy text to the system clipboard."""
    display = terminal.get_display()
    clipboard = Gtk.Clipboard.get_default(display)
    clipboard.set_text(text, -1)
    clipboard.store()


def _copy_block_output(terminal, block):
    """Extract and copy the output of a command block."""
    import gi
    gi.require_version("Vte", "2.91")
    from gi.repository import Gio, Vte

    try:
        output_stream = Gio.MemoryOutputStream.new_resizable()
        terminal.write_contents_sync(output_stream, Vte.WriteFlags.DEFAULT, None)
        output_stream.close()
        content = output_stream.steal_as_bytes().get_data().decode('utf-8', errors='replace')
        lines = content.split('\n')

        # Extract lines between command_row and end_row
        adj = terminal.get_vadjustment()
        scrollback_offset = 0  # write_contents_sync includes all scrollback
        start = block.command_row + 1 - scrollback_offset
        end = block.end_row - scrollback_offset
        if 0 <= start < len(lines) and end <= len(lines):
            output = '\n'.join(lines[start:end]).rstrip()
            _copy_to_clipboard(terminal, output)
    except Exception as e:
        log.debug("Failed to copy block output: %s", e)


def mk_tab_context_menu(callback_object):
    """Create the context menu for a notebook tab"""
    # Store the menu in a temp variable in terminal so that popup() is happy. See:
    #   https://stackoverflow.com/questions/28465956/
    callback_object.context_menu = Gtk.Menu()
    menu = callback_object.context_menu
    mi_new_tab = Gtk.MenuItem(_("New Tab"))
    mi_new_tab.connect("activate", callback_object.on_new_tab)
    menu.add(mi_new_tab)
    mi_rename = Gtk.MenuItem(_("Rename"))
    mi_rename.connect("activate", callback_object.on_rename)
    menu.add(mi_rename)
    mi_reset_custom_colors = Gtk.MenuItem(_("Reset custom colors"))
    mi_reset_custom_colors.connect("activate", callback_object.on_reset_custom_colors)
    menu.add(mi_reset_custom_colors)
    mi_set_opacity = Gtk.MenuItem(_("Set Opacity..."))
    mi_set_opacity.connect("activate", callback_object.on_set_opacity)
    menu.add(mi_set_opacity)
    mi_close = Gtk.MenuItem(_("Close"))
    mi_close.connect("activate", callback_object.on_close)
    menu.add(mi_close)

    # Add Move to Workspace submenu
    guake = callback_object.notebook.guake
    if guake and guake.workspace_manager:
        menu.add(Gtk.SeparatorMenuItem())
        move_to_ws_item = Gtk.MenuItem(_("Move to Workspace"))
        menu.add(move_to_ws_item)
        
        submenu = Gtk.Menu()
        move_to_ws_item.set_submenu(submenu)

        page_index = callback_object.notebook.find_tab_index_by_label(callback_object)
        if page_index != -1:
            page = callback_object.notebook.get_nth_page(page_index)
            terminals = page.get_terminals()
            if terminals:
                terminal_uuid = str(terminals[0].uuid)
                log.info("Moving terminal %s to workspace", terminal_uuid)
                move_to_ws_item.connect("activate", guake.on_populate_move_to_workspace_menu, submenu, terminal_uuid)

        # Watchers submenu
        if hasattr(guake, 'notification_center'):
            menu.add(Gtk.SeparatorMenuItem())
            _add_watcher_menu_items(menu, guake, callback_object)

    menu.show_all()
    return menu


def _add_watcher_menu_items(menu, guake, tab_label):
    """Add watcher items to the tab context menu."""
    nc = guake.notification_center
    wm = nc.watcher_manager

    # Find terminal UUID and title for this tab
    page_index = tab_label.notebook.find_tab_index_by_label(tab_label)
    if page_index < 0:
        return
    page = tab_label.notebook.get_nth_page(page_index)
    terminals = page.get_terminals()
    if not terminals:
        return
    terminal_uuid = str(terminals[0].uuid)
    tab_title = tab_label.get_text()

    # "Notify on Command Complete"
    from guake.notifications import CommandCompleteWatcher, RegexWatcher
    mi = Gtk.MenuItem(label="🔔 Notify on Command Complete")
    mi.connect("activate", lambda *a: wm.add(
        CommandCompleteWatcher(terminal_uuid, tab_title)))
    menu.add(mi)

    # "Notify on Regex Match..."
    mi = Gtk.MenuItem(label="🔍 Notify on Regex Match...")
    mi.connect("activate", lambda *a: _show_regex_dialog(guake, wm, terminal_uuid, tab_title))
    menu.add(mi)

    # Show active watchers for this tab
    active = wm.get_for_terminal(terminal_uuid)
    if active:
        mi = Gtk.MenuItem(label=f"Remove Watchers ({len(active)})")
        mi.connect("activate", lambda *a: wm.remove_for_terminal(terminal_uuid))
        menu.add(mi)


def _show_regex_dialog(guake, watcher_manager, terminal_uuid, tab_title):
    """Show a dialog to enter a regex pattern for matching."""
    from guake.notifications import RegexWatcher

    dialog = Gtk.Dialog(
        title="Regex Watcher",
        transient_for=guake.window,
        modal=True,
    )
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Add Watcher", Gtk.ResponseType.ACCEPT)
    dialog.set_default_response(Gtk.ResponseType.ACCEPT)

    content = dialog.get_content_area()
    content.set_spacing(8)
    content.set_margin_start(12)
    content.set_margin_end(12)
    content.set_margin_top(8)

    label = Gtk.Label(label=f"Notify when output on '{tab_title}' matches:")
    label.set_xalign(0)
    content.add(label)

    entry = Gtk.Entry()
    entry.set_placeholder_text("e.g. error|failed|exception")
    entry.set_activates_default(True)
    content.add(entry)

    hint = Gtk.Label(label="Python regex, case-insensitive")
    hint.set_xalign(0)
    hint.get_style_context().add_class("dim-label")
    content.add(hint)

    dialog.show_all()
    response = dialog.run()
    pattern = entry.get_text().strip()
    dialog.destroy()

    if response == Gtk.ResponseType.ACCEPT and pattern:
        watcher_manager.add(RegexWatcher(terminal_uuid, pattern, tab_title))


def mk_notebook_context_menu(callback_object):
    """Create the context menu for the notebook"""
    callback_object.context_menu = Gtk.Menu()
    menu = callback_object.context_menu
    mi = Gtk.MenuItem(_("New Tab"))
    mi.connect("activate", callback_object.on_new_tab)
    menu.add(mi)
    menu.add(Gtk.SeparatorMenuItem())
    mi = Gtk.MenuItem(_("Save Tabs"))
    mi.connect("activate", callback_object.on_save_tabs)
    menu.add(mi)
    mi = Gtk.MenuItem(_("Restore Tabs"))
    mi.connect("activate", callback_object.on_restore_tabs_with_dialog)
    menu.add(mi)
    menu.add(Gtk.SeparatorMenuItem())
    mi = Gtk.ImageMenuItem("gtk-preferences")
    mi.set_use_stock(True)
    mi.connect("activate", callback_object.on_show_preferences)
    menu.add(mi)
    mi = Gtk.ImageMenuItem("gtk-about")
    mi.set_use_stock(True)
    mi.connect("activate", callback_object.on_show_about)
    menu.add(mi)
    menu.add(Gtk.SeparatorMenuItem())
    mi = Gtk.MenuItem(_("Quit"))
    mi.connect("activate", callback_object.on_quit)
    menu.add(mi)
    menu.show_all()
    return menu


SEARCH_SELECTION_LENGTH = 20
FILE_SELECTION_LENGTH = 30


def mk_terminal_context_menu(terminal, window, settings, callback_object, clicked_block=None):
    """Create the context menu for a terminal."""
    # Store the menu in a temp variable in terminal so that popup() is happy. See:
    #   https://stackoverflow.com/questions/28465956/
    terminal.context_menu = Gtk.Menu()
    menu = terminal.context_menu

    # Block-specific items (if right-clicked inside a command block)
    if clicked_block and clicked_block.is_complete:
        _add_block_menu_items(menu, terminal, clicked_block)
        menu.add(Gtk.SeparatorMenuItem())
    
    customcommands = CustomCommands(settings, callback_object)
    if customcommands.should_load():
        submen = customcommands.build_menu()
        if submen:
            mi = Gtk.MenuItem(_("Custom Commands"))
            mi.set_submenu(submen)
            menu.add(mi)
            menu.add(Gtk.SeparatorMenuItem())

    # add editor
    mi = Gtk.MenuItem(_("Edit command..."))
    mi.connect("activate", callback_object.on_edit_command)
    menu.add(mi)
    menu.add(Gtk.SeparatorMenuItem())

    mi = Gtk.MenuItem(_("Copy"))
    mi.connect("activate", callback_object.on_copy_clipboard)
    menu.add(mi)
    if get_link_under_cursor(terminal) is not None:
        mi = Gtk.MenuItem(_("Copy URL"))
        mi.connect("activate", callback_object.on_copy_url_clipboard)
        menu.add(mi)
    mi = Gtk.MenuItem(_("Paste"))
    mi.connect("activate", callback_object.on_paste_clipboard)
    # check if clipboard has text, if not disable the paste menuitem
    clipboard = Gtk.Clipboard.get_default(window.get_display())
    mi.set_sensitive(clipboard.wait_is_text_available())
    menu.add(mi)
    menu.add(Gtk.SeparatorMenuItem())
    mi = Gtk.MenuItem(_("Copy content to clipboard"))
    mi.connect("activate", callback_object.on_save_to_clipboard)
    menu.add(mi)
    mi = Gtk.MenuItem(_("Save content..."))
    mi.connect("activate", callback_object.on_save_to_file)
    menu.add(mi)

    mi = Gtk.MenuItem(_("Copy CWD"))
    mi.connect("activate", callback_object.on_copy_cwd)
    menu.add(mi)

    menu.add(Gtk.SeparatorMenuItem())
    mi = Gtk.MenuItem(_("Toggle Fullscreen"))
    mi.connect("activate", callback_object.on_toggle_fullscreen)
    menu.add(mi)
    menu.add(Gtk.SeparatorMenuItem())
    mi = Gtk.MenuItem(_("Split ―"))
    mi.connect("activate", callback_object.on_split_horizontal)
    menu.add(mi)
    mi = Gtk.MenuItem(_("Split |"))
    mi.connect("activate", callback_object.on_split_vertical)
    menu.add(mi)
    mi = Gtk.MenuItem(_("Close terminal"))
    mi.connect("activate", callback_object.on_close_terminal)
    menu.add(mi)
    menu.add(Gtk.SeparatorMenuItem())
    mi = Gtk.MenuItem(_("Reset terminal"))
    mi.connect("activate", callback_object.on_reset_terminal)
    menu.add(mi)
    # TODO SEARCH uncomment menu.add()
    mi = Gtk.MenuItem(_("Find..."))
    mi.connect("activate", callback_object.on_find)
    # menu.add(mi)
    menu.add(Gtk.SeparatorMenuItem())
    mi = Gtk.MenuItem(_("Open link..."))
    mi.connect("activate", callback_object.on_open_link)
    link = get_link_under_cursor(terminal)
    # TODO CONTEXTMENU this is a mess Quick open should also be sensible
    # if the text in the selection is a url the current terminal
    # implementation does not support this at the moment
    if link:
        if len(link) >= FILE_SELECTION_LENGTH:
            mi.set_label(_("Open Link: {!s}...").format(link[: FILE_SELECTION_LENGTH - 3]))
        else:
            mi.set_label(_("Open Link: {!s}").format(link))
        mi.set_sensitive(True)
    else:
        mi.set_sensitive(False)
    menu.add(mi)
    mi = Gtk.MenuItem(_("Search on Web"))
    mi.connect("activate", callback_object.on_search_on_web)
    selection = get_current_selection(terminal, window)
    if selection:
        search_text = selection.rstrip()
        if len(search_text) > SEARCH_SELECTION_LENGTH:
            search_text = search_text[: SEARCH_SELECTION_LENGTH - 3] + "..."
        mi.set_label(_("Search on Web: '%s'") % search_text)
        mi.set_sensitive(True)
    else:
        mi.set_sensitive(False)
    menu.add(mi)
    mi = Gtk.MenuItem(_("Quick Open..."))
    mi.connect("activate", callback_object.on_quick_open)
    if selection:
        filename = get_filename_under_cursor(terminal, selection)
        if filename:
            filename_str = str(filename)
            if len(filename_str) > FILE_SELECTION_LENGTH:
                mi.set_label(
                    _("Quick Open: {!s}...").format(filename_str[: FILE_SELECTION_LENGTH - 3])
                )
            else:
                mi.set_label(_("Quick Open: {!s}").format(filename_str))
            mi.set_sensitive(True)
        else:
            mi.set_sensitive(False)
    else:
        mi.set_sensitive(False)
    menu.add(mi)

    menu.add(Gtk.SeparatorMenuItem())
    mi = Gtk.ImageMenuItem("gtk-preferences")
    mi.set_use_stock(True)
    mi.connect("activate", callback_object.on_show_preferences)
    menu.add(mi)
    mi = Gtk.ImageMenuItem("gtk-about")
    mi.set_use_stock(True)
    mi.connect("activate", callback_object.on_show_about)
    menu.add(mi)
    menu.add(Gtk.SeparatorMenuItem())
    mi = Gtk.ImageMenuItem(_("Quit"))
    mi.connect("activate", callback_object.on_quit)
    menu.add(mi)
    menu.show_all()
    return menu


def get_current_selection(terminal, window):
    if terminal.get_has_selection():
        terminal.copy_clipboard()
        clipboard = Gtk.Clipboard.get_default(window.get_display())
        return clipboard.wait_for_text()
    return None


def get_filename_under_cursor(terminal, selection):
    filename, _1, _2 = terminal.is_file_on_local_server(selection)
    log.info("Current filename under cursor: %s", filename)
    if filename:
        return filename
    return None


def get_link_under_cursor(terminal):
    link = terminal.found_link
    log.info("Current link under cursor: %s", link)
    if link:
        return link
    return None
