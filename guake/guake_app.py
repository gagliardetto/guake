# -*- coding: utf-8; -*-
"""
Copyright (C) 2007-2012 Lincoln de Sousa <lincoln@minaslivre.org>
Copyright (C) 2007 Gabriel Falcão <gabrielteratos@gmail.com>

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License as
published by the Free Software Foundation; either version 2 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public
License along with this program; if not, write to the
Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor,
Boston, MA 02110-1301 USA
"""
import json
import logging
import os
import shutil
import subprocess
import time as pytime
import traceback
import uuid

from pathlib import Path
from threading import Thread
from time import sleep
from urllib.parse import quote_plus
from xml.sax.saxutils import escape as xml_escape

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Keybinder", "3.0")
from gi.repository import GLib
from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import Gtk
from gi.repository import Keybinder

from guake import gtk_version
from guake import guake_version
from guake import notifier
from guake import vte_version
from guake.about import AboutDialog
from guake.common import gladefile
from guake.common import pixmapfile
from guake.dialogs import PromptQuitDialog
from guake.dialogs import QuickTabNavigationDialog
from guake.globals import MAX_TRANSPARENCY
from guake.globals import NAME
from guake.globals import PROMPT_ALWAYS
from guake.globals import PROMPT_PROCESSES
from guake.globals import TABS_SESSION_SCHEMA_VERSION
from guake.gsettings import GSettingHandler
from guake.keybindings import Keybindings
from guake.notebook import NotebookManager
from guake.palettes import PALETTES
from guake.paths import LOCALE_DIR
from guake.paths import SCHEMA_DIR
from guake.paths import try_to_compile_glib_schemas
from guake.prefs import PrefsDialog
from guake.prefs import refresh_user_start
from guake.settings import Settings
from guake.simplegladeapp import SimpleGladeApp
from guake.theme import patch_gtk_theme
from guake.theme import select_gtk_theme
from guake.utils import BackgroundImageManager
from guake.utils import FileManager
from guake.utils import FullscreenManager
from guake.utils import HidePrevention
from guake.utils import RectCalculator
from guake.utils import TabNameUtils
from guake.utils import get_server_time
from guake.utils import save_tabs_when_changed
from guake.world_map import WorldMapView
from guake.workspaces import WorkspaceManager

log = logging.getLogger(__name__)

instance = None
RESPONSE_FORWARD = 0
RESPONSE_BACKWARD = 1

# Disable find feature until python-vte hasn't been updated
enable_find = False

# Setting gobject program name
GLib.set_prgname(NAME)

GDK_WINDOW_STATE_WITHDRAWN = 1
GDK_WINDOW_STATE_ICONIFIED = 2
GDK_WINDOW_STATE_STICKY = 8
GDK_WINDOW_STATE_ABOVE = 32


class Guake(SimpleGladeApp):

    """Guake main class. Handles specialy the main window."""

    def __init__(self):
        def load_schema():
            log.info("Loading Gnome schema from: %s", SCHEMA_DIR)

            return Gio.SettingsSchemaSource.new_from_directory(
                SCHEMA_DIR, Gio.SettingsSchemaSource.get_default(), False
            )

        try:
            schema_source = load_schema()
        except GLib.Error:  # pylint: disable=catching-non-exception
            log.exception("Unable to load the GLib schema, try to compile it")
            try_to_compile_glib_schemas()
            schema_source = load_schema()
        self.settings = Settings(schema_source)
        self.accel_group = None

        if (
            "schema-version" not in self.settings.general.keys()
            or self.settings.general.get_string("schema-version") != guake_version()
        ):
            log.exception("Schema from old guake version detected, regenerating schema")
            try:
                try_to_compile_glib_schemas()
            except subprocess.CalledProcessError:
                log.exception("Schema in non user-editable location, attempting to continue")
            schema_source = load_schema()
            self.settings = Settings(schema_source)
            self.settings.general.set_string("schema-version", guake_version())

        log.info("Language previously loaded from: %s", LOCALE_DIR)

        super().__init__(gladefile("guake.glade"))

        # Add CSS provider for custom sidebar styling
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"""
        .sidebar {
            background-color: #2E3436; /* Opaque dark color */
        }
        .sidebar GtkLabel, .sidebar .button {
            color: #EEEEEC;
        }
        .sidebar .sidebar-title {
            font-weight: bold;
        }
        .sidebar GtkListBoxRow:hover {
            background-color: #555753;
        }
        .sidebar GtkListBoxRow:selected {
            background-color: #4E9A06;
        }
        .sidebar .dim-label {
            opacity: 0.7;
            font-size: small;
        }
        """
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        select_gtk_theme(self.settings)
        patch_gtk_theme(self.get_widget("window-root").get_style_context(), self.settings)
        self.add_callbacks(self)

        log.info("Guake Terminal %s", guake_version())
        log.info("VTE %s", vte_version())
        log.info("Gtk %s", gtk_version())

        self.hidden = True
        self.forceHide = False
        self.mouse_in_hot_edge = False
        self.is_restoring_session = False
        self.adding_tab_to_workspace_id = None
        self.sidebar_last_opened_time = 0.0

        # trayicon!
        img = pixmapfile("guake-tray.png")
        try:
            try:
                gi.require_version("AyatanaAppIndicator3", "0.1")
                from gi.repository import (
                    AyatanaAppIndicator3 as appindicator,
                )
            except (ValueError, ImportError):
                gi.require_version("AppIndicator3", "0.1")
                from gi.repository import (
                    AppIndicator3 as appindicator,
                )
        except (ValueError, ImportError):
            self.tray_icon = Gtk.StatusIcon()
            self.tray_icon.set_from_file(img)
            self.tray_icon.set_tooltip_text("Guake Terminal")
            self.tray_icon.connect("popup-menu", self.show_menu)
            self.tray_icon.connect("activate", self.show_hide)
        else:
            self.tray_icon = appindicator.Indicator.new(
                "guake-indicator", "guake-tray", appindicator.IndicatorCategory.APPLICATION_STATUS
            )
            self.tray_icon.set_icon_full("guake-tray", "Guake Terminal")
            self.tray_icon.set_status(appindicator.IndicatorStatus.ACTIVE)
            menu = self.get_widget("tray-menu")
            show = Gtk.MenuItem.new_with_label("Show")
            show.set_sensitive(True)
            show.connect("activate", self.show_hide)
            show.show()
            menu.prepend(show)
            self.tray_icon.set_menu(menu)

        self.display_tab_names = 0

        # important widgets
        self.window = self.get_widget("window-root")
        self.window.set_name("guake-terminal")
        self.window.set_keep_above(True)
        self.mainframe = self.get_widget("mainframe")
        self.sidebar_revealer = self.get_widget("sidebar_revealer")
        self.sidebar_hide_timer = None

        # Set sidebar width
        sidebar_width_fraction = self.settings.general.get_int("sidebar-width-fraction")
        screen_width = self.window.get_screen().get_width()
        sidebar_child = self.sidebar_revealer.get_child()
        if sidebar_child:
            sidebar_child.set_size_request(screen_width / sidebar_width_fraction, -1)

        self.mainframe.remove(self.get_widget("notebook-teminals"))

        self.pending_restore_page_split = []
        self._failed_restore_page_split = []

        self.background_image_manager = BackgroundImageManager(self.window)
        self.fullscreen_manager = FullscreenManager(self.settings, self.window, self)
        self.fm = FileManager()

        # Initialize NotebookManager BEFORE WorkspaceManager
        self.notebook_manager = NotebookManager(
            self.window,
            self.mainframe,
            False,
            self.terminal_spawned,
            self.page_deleted,
        )
        self.notebook_manager.connect("notebook-created", self.notebook_created)
        self.notebook_manager.set_workspace(0)
        self.set_tab_position()

        # Remove the dummy box from the glade file and add our workspace manager
        old_sidebar_content = self.sidebar_revealer.get_child()
        if old_sidebar_content:
            self.sidebar_revealer.remove(old_sidebar_content)

        # Initialize WorkspaceManager AFTER NotebookManager
        self.workspace_manager = WorkspaceManager(self)
        self.sidebar_revealer.add(self.workspace_manager.widget)
        self.workspace_manager.widget.show_all()

        self.update_visual()
        self.window.get_screen().connect("composited-changed", self.update_visual)

        self.prev_accel_search_terminal_time = 0.0
        self.losefocus_time = 0
        self.prev_showhide_time = 0
        self.transparency_toggled = False
        self.default_window_title = self.window.get_title()

        self.window.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self.window.connect("motion-notify-event", self.on_window_motion)
        self.window.connect("focus-out-event", self.on_window_losefocus)
        self.window.connect("focus-in-event", self.on_window_takefocus)

        self.world_map_view = WorldMapView(self)
        self.world_map_visible = False

        def destroy(*args):
            self.hide()
            return True

        def window_event(*args):
            return self.window_event(*args)

        self.window.connect("delete-event", destroy)
        self.window.connect("window-state-event", window_event)

        self.window.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.window.set_type_hint(Gdk.WindowTypeHint.NORMAL)

        GSettingHandler(self)
        Keybinder.init()
        self.hotkeys = Keybinder
        Keybindings(self)
        self.load_config()

        if self.settings.general.get_boolean("start-fullscreen"):
            self.fullscreen()

        refresh_user_start(self.settings)

        if self.settings.general.get_boolean("restore-tabs-startup"):
            self.restore_tabs(suppress_notify=True)
        else:
            self.workspace_manager.reconcile_orphan_tabs()

        initial_workspace_id = self.workspace_manager.workspaces_data.get("active_workspace")
        if initial_workspace_id and self.workspace_manager.get_workspace_by_id(initial_workspace_id):
            self.switch_to_workspace(initial_workspace_id)
        elif self.workspace_manager.get_all_workspaces():
            first_ws_id = self.workspace_manager.get_all_workspaces()[0]["id"]
            self.workspace_manager.workspaces_data["active_workspace"] = first_ws_id
            self.workspace_manager.save_workspaces()
            self.switch_to_workspace(first_ws_id)
        else:
            if not self.get_notebook().get_n_pages() > 0:
                self.add_tab()

        if self.settings.general.get_boolean("use-popup"):
            key = self.settings.keybindingsGlobal.get_string("show-hide")
            keyval, mask = Gtk.accelerator_parse(key)
            label = Gtk.accelerator_get_label(keyval, mask)
            filename = pixmapfile("guake-notification.png")
            notifier.showMessage(
                "Guake Terminal",
                f"Guake is now running,\npress <b>{xml_escape(label)}</b> to use it.",
                filename,
            )

        log.info("Guake initialized")

    def get_notebook(self):
        return self.notebook_manager.get_current_notebook()

    def notebook_created(self, nm, notebook, key):
        notebook.attach_guake(self)
        notebook.connect("page-reordered", self.on_page_reorder)
        notebook.connect("page-removed", self.on_tab_closed)
        notebook.connect("switch-page", self.on_tab_switch)
        if hasattr(notebook, "right_click_menu"):
            self.populate_tab_context_menu(notebook.right_click_menu)

    def on_tab_switch(self, notebook, page, page_num):
        """Callback for when the active tab is changed."""
        terminal = notebook.get_current_terminal()
        if terminal and self.workspace_manager:
            self.workspace_manager.set_active_terminal_for_active_workspace(str(terminal.uuid))

    def update_visual(self, user_data=None):
        screen = self.window.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.window.unrealize()
            self.window.set_visual(visual)
            self.window.set_app_paintable(True)
            self.window.transparency = True
            self.window.realize()
            if self.window.get_property("visible"):
                self.hide()
                self.show()
        else:
            log.warning("System doesn't support transparency")
            self.window.transparency = False
            self.window.set_visual(screen.get_system_visual())

    def _load_palette(self):
        colorRGBA = Gdk.RGBA(0, 0, 0, 0)
        paletteList = []
        for color in self.settings.styleFont.get_string("palette").split(":"):
            colorRGBA.parse(color)
            paletteList.append(colorRGBA.copy())
        return paletteList

    def _get_background_color(self, palette_list):
        if len(palette_list) > 16:
            bg_color = palette_list[17]
        else:
            bg_color = Gdk.RGBA(0, 0, 0, 0.9)
        return self._apply_transparency_to_color(bg_color)

    def _apply_transparency_to_color(self, bg_color):
        transparency = self.settings.styleBackground.get_int("transparency")
        bg_color.alpha = 1 / 100 * transparency if not self.transparency_toggled else 1
        return bg_color

    def set_background_color_from_settings(self, terminal_uuid=None):
        self.set_colors_from_settings(terminal_uuid)

    def get_bgcolor(self):
        palette_list = self._load_palette()
        return self._get_background_color(palette_list)

    def get_fgcolor(self):
        palette_list = self._load_palette()
        return palette_list[16] if len(palette_list) > 16 else Gdk.RGBA(0, 0, 0, 0)

    def set_colors_from_settings(self, terminal_uuid=None):
        bg_color = self.get_bgcolor()
        font_color = self.get_fgcolor()
        palette_list = self._load_palette()
        terminals = self.get_notebook().iter_terminals()
        if terminal_uuid:
            terminals = [t for t in terminals if t.uuid == terminal_uuid]
        for i in terminals:
            i.set_color_foreground(font_color)
            i.set_color_bold(font_color)
            i.set_colors(font_color, bg_color, palette_list[:16])

    def set_colors_from_settings_on_page(self, current_terminal_only=False, page_num=None):
        bg_color = self.get_bgcolor()
        font_color = self.get_fgcolor()
        palette_list = self._load_palette()
        if current_terminal_only:
            terminal = self.get_notebook().get_current_terminal()
            terminal.set_color_foreground(font_color)
            terminal.set_color_bold(font_color)
            terminal.set_colors(font_color, bg_color, palette_list[:16])
        else:
            if page_num is None:
                page_num = self.get_notebook().get_current_page()
            for terminal in self.get_notebook().get_nth_page(page_num).iter_terminals():
                terminal.set_color_foreground(font_color)
                terminal.set_color_bold(font_color)
                terminal.set_colors(font_color, bg_color, palette_list[:16])

    def reset_terminal_custom_colors(self, current_terminal=False, current_page=False, terminal_uuid=None):
        terminals = []
        if current_terminal:
            terminals.append(self.get_notebook().get_current_terminal())
        if current_page:
            page_num = self.get_notebook().get_current_page()
            terminals.extend(self.get_notebook().get_nth_page(page_num).iter_terminals())
        if terminal_uuid:
            terminals.extend(t for t in self.get_notebook().iter_terminals() if t.uuid == terminal_uuid)
        if not terminals:
            terminals = list(self.get_notebook().iter_terminals())
        for i in terminals:
            i.reset_custom_colors()

    def set_bgcolor(self, bgcolor, current_terminal_only=False):
        if isinstance(bgcolor, str):
            c = Gdk.RGBA()
            c.parse("#" + bgcolor)
            bgcolor = c
        bgcolor = self._apply_transparency_to_color(bgcolor)
        if current_terminal_only:
            self.get_notebook().get_current_terminal().set_color_background_custom(bgcolor)
        else:
            page_num = self.get_notebook().get_current_page()
            for terminal in self.get_notebook().get_nth_page(page_num).iter_terminals():
                terminal.set_color_background_custom(bgcolor)

    def set_fgcolor(self, fgcolor, current_terminal_only=False):
        if isinstance(fgcolor, str):
            c = Gdk.RGBA()
            c.parse("#" + fgcolor)
            fgcolor = c
        if current_terminal_only:
            self.get_notebook().get_current_terminal().set_color_foreground_custom(fgcolor)
        else:
            page_num = self.get_notebook().get_current_page()
            for terminal in self.get_notebook().get_nth_page(page_num).iter_terminals():
                terminal.set_color_foreground_custom(fgcolor)

    def change_palette_name(self, palette_name):
        if isinstance(palette_name, str) and palette_name in PALETTES:
            self.settings.styleFont.set_string("palette", PALETTES[palette_name])
            self.settings.styleFont.set_string("palette-name", palette_name)
            self.set_colors_from_settings()

    def execute_command(self, command, tab=None):
        if not self.get_notebook().has_page():
            self.add_tab()
        if not command.endswith("\n"):
            command += "\n"
        self.get_notebook().get_current_terminal().feed_child(command)

    def execute_command_by_uuid(self, tab_uuid, command):
        if not command.endswith("\n"):
            command += "\n"
        try:
            tab_uuid = uuid.UUID(tab_uuid)
            page_index = next(
                index for index, t in enumerate(self.get_notebook().iter_terminals()) if t.get_uuid() == tab_uuid
            )
            terminals = self.get_notebook().get_terminals_for_page(page_index)
            for current_vte in terminals:
                current_vte.feed_child(command)
        except (ValueError, StopIteration):
            pass

    def on_window_losefocus(self, window, event):
        if not HidePrevention(self.window).may_hide():
            return
        def hide_window_callback():
            if window.get_property("visible") and self.settings.general.get_boolean("window-losefocus"):
                self.losefocus_time = get_server_time(self.window)
                log.info("Hiding on focus lose")
                self.hide()
        if self.settings.general.get_boolean("lazy-losefocus"):
            self.lazy_losefocus_time = get_server_time(self.window)
            def losefocus_callback():
                sleep(0.3)
                if not (self.window.get_property("has-toplevel-focus") and (self.takefocus_time - self.lazy_losefocus_time) > 0):
                    if self.window.get_property("visible"):
                        GLib.idle_add(hide_window_callback)
            Thread(target=losefocus_callback, daemon=True).start()
        else:
            hide_window_callback()

    def on_window_takefocus(self, window, event):
        self.takefocus_time = get_server_time(self.window)

    def show_menu(self, status_icon, button, activate_time):
        menu = self.get_widget("tray-menu")
        menu.popup(None, None, None, Gtk.StatusIcon.position_menu, button, activate_time)

    def show_about(self, *args):
        self.hide()
        AboutDialog()

    def show_prefs(self, *args):
        self.hide()
        PrefsDialog(self.settings).show()

    def is_iconified(self):
        return bool(self.window.get_state() & Gdk.WindowState.ICONIFIED) if self.window else False

    def window_event(self, window, event):
        self.fullscreen_manager.set_window_state(event.new_window_state)

    def show_hide(self, *args):
        if self.forceHide:
            self.forceHide = False
            return
        if not HidePrevention(self.window).may_hide() or not self.win_prepare():
            return
        if not self.window.get_property("visible"):
            self.show()
            server_time = get_server_time(self.window)
            self.window.get_window().focus(server_time)
            self.set_terminal_focus()
        elif self.settings.general.get_boolean("window-refocus") and not (self.window.get_window().get_state() & Gdk.WindowState.FOCUSED):
            server_time = get_server_time(self.window)
            self.window.get_window().focus(server_time)
            self.set_terminal_focus()
        else:
            self.hide()

    def get_visibility(self):
        return 0 if self.hidden else 1

    def show_focus(self, *args):
        self.win_prepare()
        self.show()
        self.set_terminal_focus()

    def win_prepare(self, *args):
        event_time = self.hotkeys.get_current_event_time()
        if self.losefocus_time and (event_time - self.losefocus_time) < 10:
            self.losefocus_time = 0
            return False
        if self.prev_showhide_time and event_time and (event_time - self.prev_showhide_time) < 65:
            return False
        self.prev_showhide_time = event_time
        return True

    def restore_pending_terminal_split(self):
        self.pending_restore_page_split, self._failed_restore_page_split = self._failed_restore_page_split, []
        for root, box, panes in self.pending_restore_page_split:
            if self.window.get_property("visible") and root.get_notebook() == self.notebook_manager.get_current_notebook():
                root.restore_box_layout(box, panes)
            else:
                self._failed_restore_page_split.append((root, box, panes))

    def show(self):
        self.hidden = False
        window_rect = RectCalculator.set_final_window_rect(self.settings, self.window)
        self.window.stick()
        if not self.get_notebook().has_page():
            self.add_tab()
        self.window.set_keep_below(False)
        self.window.move(window_rect.x, window_rect.y)
        if not self.fullscreen_manager.is_fullscreen():
            self.settings.general.triggerOnChangedValue(self.settings.general, "window-height")
        time = get_server_time(self.window)
        self.window.present()
        self.window.deiconify()
        self.window.show()
        self.window.get_window().focus(time)
        self.window.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.window.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        self.settings.styleFont.triggerOnChangedValue(self.settings.styleFont, "color")
        self.settings.styleBackground.triggerOnChangedValue(self.settings.styleBackground, "color")
        self.restore_pending_terminal_split()
        self.execute_hook("show")

    def hide_from_remote(self):
        self.forceHide = True
        self.hide()

    def show_from_remote(self):
        self.forceHide = True
        self.show()

    def hide(self):
        if not HidePrevention(self.window).may_hide():
            return
        self.hidden = True
        self.get_widget("window-root").unstick()
        self.window.hide()
        self.notebook_manager.get_current_notebook().popover.hide()

    def force_move_if_shown(self):
        if not self.hidden:
            self.hide()
            self.show()

    def load_config(self, terminal_uuid=None):
        user_data = {"terminal_uuid": terminal_uuid} if terminal_uuid else {}
        for s in [self.settings.general, self.settings.style, self.settings.styleFont, self.settings.styleBackground]:
            for key in s.list_keys():
                s.triggerOnChangedValue(s, key, user_data)

    def accel_search_terminal(self, *args):
        nb = self.get_notebook()
        term = nb.get_current_terminal()
        box = nb.get_nth_page(nb.find_page_index_by_terminal(term))
        current_time = pytime.time()
        if current_time - self.prev_accel_search_terminal_time < 0.3:
            return
        self.prev_accel_search_terminal_time = current_time
        if box.is_search_box_visible():
            if box.search_entry.has_focus():
                box.hide_search_box()
            else:
                box.search_entry.grab_focus()
        else:
            box.show_search_box()

    def accel_quit(self, *args):
        procs = self.notebook_manager.get_running_fg_processes()
        tabs = self.notebook_manager.get_n_pages()
        notebooks = self.notebook_manager.get_n_notebooks()
        prompt_cfg = self.settings.general.get_boolean("prompt-on-quit")
        prompt_tab_cfg = self.settings.general.get_int("prompt-on-close-tab")
        if prompt_cfg or (prompt_tab_cfg == PROMPT_PROCESSES and procs) or (prompt_tab_cfg == PROMPT_ALWAYS):
            if PromptQuitDialog(self.window, procs, tabs, notebooks).quit():
                Gtk.main_quit()
        else:
            Gtk.main_quit()

    def accel_reset_terminal(self, *args):
        HidePrevention(self.window).prevent()
        self.get_notebook().get_current_terminal().reset(True, True)
        HidePrevention(self.window).allow()
        return True

    def accel_zoom_in(self, *args):
        font, size = self.settings.styleFont.get_string("style").rsplit(" ", 1)
        new_size = int(size) + 1
        self.settings.styleFont.set_string("style", f"{font} {new_size}")
        for term in self.get_notebook().iter_terminals():
            term.set_font_scale(new_size / (new_size - 1))
        return True

    def accel_zoom_out(self, *args):
        font, size = self.settings.styleFont.get_string("style").rsplit(" ", 1)
        new_size = int(size) - 1
        self.settings.styleFont.set_string("style", f"{font} {new_size}")
        for term in self.get_notebook().iter_terminals():
            term.set_font_scale((new_size - 1) / new_size)
        return True

    def accel_increase_height(self, *args):
        height = self.settings.general.get_int("window-height")
        self.settings.general.set_int("window-height", min(height + 2, 100))
        return True

    def accel_decrease_height(self, *args):
        height = self.settings.general.get_int("window-height")
        self.settings.general.set_int("window-height", max(height - 2, 0))
        return True

    def accel_increase_transparency(self, *args):
        transparency = self.settings.styleBackground.get_int("transparency")
        self.settings.styleBackground.set_int("transparency", max(transparency - 2, 0))
        return True

    def accel_decrease_transparency(self, *args):
        transparency = self.settings.styleBackground.get_int("transparency")
        self.settings.styleBackground.set_int("transparency", min(transparency + 2, MAX_TRANSPARENCY))
        return True

    def accel_toggle_transparency(self, *args):
        self.transparency_toggled = not self.transparency_toggled
        self.settings.styleBackground.triggerOnChangedValue(self.settings.styleBackground, "transparency")
        return True

    def accel_add(self, *args):
        """Callback to add a new tab. Called by the accel key."""
        self.add_tab()
        return True

    @save_tabs_when_changed
    def add_tab(self, directory=None, open_tab_cwd=False):
        position = 1 + self.get_notebook().get_current_page() if self.settings.general.get_boolean("new-tab-after") else None
        self.get_notebook().new_page_with_focus(directory, position=position, open_tab_cwd=open_tab_cwd)

    def add_tab_to_workspace(self, workspace_id):
        self.adding_tab_to_workspace_id = workspace_id
        self.add_tab()
        self.adding_tab_to_workspace_id = None

    def accel_add_home(self, *args):
        self.add_tab(os.environ["HOME"])
        return True

    def accel_add_cwd(self, *args):
        self.add_tab(open_tab_cwd=True)
        return True

    def accel_prev(self, *args):
        nb = self.get_notebook()
        nb.set_current_page(nb.get_n_pages() - 1 if nb.get_current_page() == 0 else nb.get_current_page() - 1)
        return True

    def accel_next(self, *args):
        nb = self.get_notebook()
        nb.set_current_page(0 if nb.get_current_page() + 1 == nb.get_n_pages() else nb.get_current_page() + 1)
        return True

    def accel_move_tab_left(self, *args):
        pos = self.get_notebook().get_current_page()
        if pos > 0:
            self.move_tab(pos, pos - 1)
        return True

    def accel_move_tab_right(self, *args):
        pos = self.get_notebook().get_current_page()
        if pos < self.get_notebook().get_n_pages() - 1:
            self.move_tab(pos, pos + 1)
        return True

    @save_tabs_when_changed
    def move_tab(self, old_tab_pos, new_tab_pos):
        nb = self.get_notebook()
        nb.reorder_child(nb.get_nth_page(old_tab_pos), new_tab_pos)
        nb.set_current_page(new_tab_pos)

    def gen_accel_switch_tabN(self, N):
        return lambda *args: (self.get_notebook().set_current_page(N) if 0 <= N < self.get_notebook().get_n_pages() else None, True)[1]

    def accel_switch_tab_last(self, *args):
        self.get_notebook().set_current_page(self.get_notebook().get_n_pages() - 1)
        return True

    def accel_rename_current_tab(self, *args):
        page = self.get_notebook().get_nth_page(self.get_notebook().get_current_page())
        self.get_notebook().get_tab_label(page).on_rename(None)
        return True

    def accel_quick_tab_navigation(self, *args):
        HidePrevention(self.window).prevent()
        dialog = QuickTabNavigationDialog(self.window, self.notebook_manager)
        if dialog.run() == Gtk.ResponseType.OK and dialog.get_selected_page() is not None:
            self.get_notebook().set_current_page(dialog.get_selected_page())
        dialog.destroy()
        HidePrevention(self.window).allow()
        return True

    def accel_copy_clipboard(self, *args):
        self.get_notebook().get_current_terminal().copy_clipboard()
        return True

    def accel_paste_clipboard(self, *args):
        self.get_notebook().get_current_terminal().paste_clipboard()
        return True

    def accel_select_all(self, *args):
        self.get_notebook().get_current_terminal().select_all()
        return True

    def accel_toggle_hide_on_lose_focus(self, *args):
        return True

    def accel_toggle_fullscreen(self, *args):
        self.fullscreen_manager.toggle()
        return True

    def on_window_motion(self, widget, event):
        sidebar_width = self.sidebar_revealer.get_allocated_width()
        hot_edge_width = 1
        if event.x < hot_edge_width:
            if not self.mouse_in_hot_edge:
                self.mouse_in_hot_edge = True
                if self.sidebar_hide_timer:
                    GLib.source_remove(self.sidebar_hide_timer)
                    self.sidebar_hide_timer = None
                if not self.sidebar_revealer.get_reveal_child():
                    self.sidebar_revealer.set_reveal_child(True)
                    self.sidebar_last_opened_time = pytime.time()
        elif event.x > sidebar_width + hot_edge_width:
            self.mouse_in_hot_edge = False
            if self.sidebar_revealer.get_reveal_child() and not self.sidebar_hide_timer:
                time_since_open = pytime.time() - self.sidebar_last_opened_time
                wait_for_min_open_time = max(0, 1.0 - time_since_open)
                wait_from_now = max(0.3, wait_for_min_open_time)
                self.sidebar_hide_timer = GLib.timeout_add(int(wait_from_now * 1000), self.hide_sidebar_timeout)
        elif event.x > hot_edge_width:
            self.mouse_in_hot_edge = False

    def hide_sidebar_timeout(self):
        if not self.mouse_in_hot_edge:
            self.sidebar_revealer.set_reveal_child(False)
        self.sidebar_hide_timer = None
        return False

    def fullscreen(self):
        self.fullscreen_manager.fullscreen()

    def unfullscreen(self):
        self.fullscreen_manager.unfullscreen()

    def recompute_tabs_titles(self):
        if not self.settings.general.get_boolean("use-vte-titles"):
            return
        for terminal in self.get_notebook().iter_terminals():
            page_num = self.get_notebook().page_num(terminal.get_parent())
            self.get_notebook().rename_page(page_num, self.compute_tab_title(terminal), False)

    def load_cwd_guake_yaml(self, vte) -> dict:
        if not self.settings.general.get_boolean("load-guake-yml"):
            return {}
        try:
            return self.fm.read_yaml(str(Path(vte.get_current_directory()) / ".guake.yml")) or {}
        except Exception:
            return {}

    def compute_tab_title(self, vte):
        guake_yml = self.load_cwd_guake_yaml(vte)
        if "title" in guake_yml:
            return guake_yml["title"]
        vte_title = vte.get_window_title() or "Terminal"
        try:
            current_directory = vte.get_current_directory()
            if self.display_tab_names == 1 and vte_title.endswith(current_directory):
                parts = current_directory.split("/")
                vte_title = vte_title[:-len(current_directory)] + "/".join([s[:1] for s in parts[:-1]] + [parts[-1]])
            elif self.display_tab_names == 2:
                vte_title = current_directory.split("/")[-1] or "(root)"
        except OSError:
            pass
        return TabNameUtils.shorten(vte_title, self.settings)

    def check_if_terminal_directory_changed(self, term):
        @save_tabs_when_changed
        def terminal_directory_changed(self): pass
        current_directory = term.get_current_directory()
        if current_directory != term.directory:
            term.directory = current_directory
            terminal_directory_changed(self)

    def on_terminal_title_changed(self, vte, term):
        if not term.get_parent(): return
        self.check_if_terminal_directory_changed(term)
        box = term.get_parent().get_root_box()
        if not self.settings.general.get_boolean("use-vte-titles"): return
        nb = self.get_notebook()
        page_num = nb.page_num(box)
        if not getattr(box, "custom_label_set", False):
            title = self.compute_tab_title(vte)
            nb.rename_page(page_num, title, False)
            self.update_window_title(title)
        else:
            self.update_window_title(nb.get_tab_text_page(box) or "")

    def update_window_title(self, title):
        self.window.set_title(title if self.settings.general.get_boolean("set-window-title") else self.default_window_title)

    def close_tab(self, *args):
        self.get_notebook().delete_page_current(prompt=self.settings.general.get_int("prompt-on-close-tab"))

    def rename_tab_uuid(self, term_uuid, new_text, user_set=True):
        try:
            term_uuid = uuid.UUID(term_uuid)
            page_index = next(i for i, t in enumerate(self.get_notebook().iter_terminals()) if t.get_uuid() == term_uuid)
            self.get_notebook().rename_page(page_index, new_text, user_set)
        except (ValueError, StopIteration):
            pass

    def get_index_from_uuid(self, term_uuid):
        try:
            term_uuid = uuid.UUID(term_uuid)
            return next(i for i, t in enumerate(self.get_notebook().iter_terminals()) if t.get_uuid() == term_uuid)
        except (ValueError, StopIteration):
            return -1

    def rename_current_tab(self, new_text, user_set=False):
        self.get_notebook().rename_page(self.get_notebook().get_current_page(), new_text, user_set)

    def terminal_spawned(self, notebook, terminal, pid):
        self.load_config(terminal_uuid=terminal.uuid)
        terminal.handler_ids.append(terminal.connect("window-title-changed", self.on_terminal_title_changed, terminal))
        terminal.directory = terminal.get_current_directory()
        if hasattr(self, 'workspace_manager') and self.workspace_manager and not self.is_restoring_session:
            if self.adding_tab_to_workspace_id:
                self.workspace_manager.add_terminal_to_workspace(str(terminal.uuid), self.adding_tab_to_workspace_id)
            else:
                self.workspace_manager.add_terminal_to_active_workspace(str(terminal.uuid))

    def find_tab(self, directory=None):
        HidePrevention(self.window).prevent()
        search_text = Gtk.TextView()
        dialog = Gtk.Dialog("Find", self.window, Gtk.DialogFlags.DESTROY_WITH_PARENT,
                            ("Forward", RESPONSE_FORWARD, "Backward", RESPONSE_BACKWARD, Gtk.STOCK_CANCEL, Gtk.ResponseType.NONE))
        dialog.vbox.pack_end(search_text, True, True, 0)
        dialog.buffer = search_text.get_buffer()
        dialog.connect("response", self._dialog_response_callback)
        search_text.show()
        search_text.grab_focus()
        dialog.show_all()

    def _dialog_response_callback(self, dialog, response_id):
        if response_id not in (RESPONSE_FORWARD, RESPONSE_BACKWARD):
            dialog.destroy()
            HidePrevention(self.window).allow()
            return
        start, end = dialog.buffer.get_bounds()
        search_string = start.get_text(end, True)
        current_term = self.get_notebook().get_current_terminal()
        current_term.search_set_gregex()
        current_term.search_get_gregex()

    def page_deleted(self, *args):
        if not self.get_notebook().has_page():
            self.hide()
            self.add_tab()
        else:
            self.set_terminal_focus()
        self.was_deleted_tab = True
        self.display_tab_names = self.settings.general.get_int("display-tab-names")
        self.recompute_tabs_titles()

    def set_terminal_focus(self):
        self.get_notebook().set_current_page(self.get_notebook().get_current_page())

    def get_selected_uuidtab(self):
        return str(self.get_notebook().get_current_terminal().get_uuid())

    def open_link_under_terminal_cursor(self, *args):
        current_term = self.get_notebook().get_current_terminal()
        if current_term:
            url = current_term.get_link_under_terminal_cursor()
            current_term.browse_link_under_cursor(url)

    def search_on_web(self, *args):
        current_term = self.get_notebook().get_current_terminal()
        if current_term.get_has_selection():
            guake_clipboard = Gtk.Clipboard.get_default(self.window.get_display())
            search_query = quote_plus(guake_clipboard.wait_for_text() or "")
            if search_query:
                Gtk.show_uri(self.window.get_screen(), f"https://www.google.com/search?q={search_query}&safe=off", get_server_time(self.window))
        return True

    def set_tab_position(self, *args):
        pos = Gtk.PositionType.TOP if self.settings.general.get_boolean("tab-ontop") else Gtk.PositionType.BOTTOM
        self.get_notebook().set_tab_pos(pos)

    def execute_hook(self, event_name):
        hook = self.settings.hooks.get_string(event_name)
        if hook:
            try:
                subprocess.Popen(hook.split())
            except Exception as e:
                log.error("hook execution failed! %s", e)

    @save_tabs_when_changed
    def on_page_reorder(self, notebook, child, page_num):
        if self.workspace_manager:
            visible_pages = [notebook.get_nth_page(i) for i in range(notebook.get_n_pages()) if notebook.get_nth_page(i).get_visible()]
            new_uuid_order = [str(list(page.iter_terminals())[0].uuid) for page in visible_pages if list(page.iter_terminals())]
            self.workspace_manager.update_terminal_order_for_active_workspace(new_uuid_order)

    def get_xdg_config_directory(self):
        return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config"), "guake").expanduser()

    def save_tabs(self, filename="session.json"):
        config = {"schema_version": TABS_SESSION_SCHEMA_VERSION, "timestamp": int(pytime.time()), "workspace": {}}
        for key, nb in self.notebook_manager.get_notebooks().items():
            tabs = []
            for index in range(nb.get_n_pages()):
                try:
                    page = nb.get_nth_page(index)
                    panes = []
                    page.save_box_layout(page.child, panes)
                    tabs.append({"panes": panes, "label": nb.get_tab_text_index(index), "custom_label_set": getattr(page, "custom_label_set", False)})
                except FileNotFoundError: pass
            config["workspace"][key] = [tabs]
        config_dir = self.get_xdg_config_directory()
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / filename).write_text(json.dumps(config, ensure_ascii=False, indent=4), encoding="utf-8")

    def restore_tabs(self, filename="session.json", suppress_notify=False):
        session_file = self.get_xdg_config_directory() / filename
        if not session_file.exists(): return
        try:
            config = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            shutil.copy(session_file, f"{session_file}.bak")
            return
        if config.get("schema_version", 0) > TABS_SESSION_SCHEMA_VERSION: return

        # 1. Get all UUIDs from session file first
        all_session_uuids = set()
        for key, frames in config.get("workspace", {}).items():
            for tabs in frames:
                for tab in tabs:
                    if tab.get("panes"):
                        for pane in tab["panes"]:
                            if pane.get("uuid"):
                                all_session_uuids.add(pane["uuid"])

        # 2. Hide notebook and set restoring flag
        self.is_restoring_session = True
        notebook = self.get_notebook()
        notebook.hide()

        v = self.settings.general.get_boolean("save-tabs-when-changed")
        self.settings.general.set_boolean("save-tabs-when-changed", False)

        active_workspace_id = self.workspace_manager.workspaces_data.get("active_workspace")
        self.workspace_manager.workspaces_data["active_workspace"] = None

        try:
            # 3. Create all tabs
            for key, frames in config.get("workspace", {}).items():
                nb = self.notebook_manager.get_notebook(int(key))
                for _ in range(nb.get_n_pages()): nb.delete_page(0)
                for tabs in frames:
                    for tab in tabs:
                        if tab.get("panes"):
                            box, _, _ = nb.new_page_with_focus(label=tab["label"], user_set=tab.get("custom_label_set", False), empty=True)
                            box.restore_box_layout(box.child, tab["panes"])
                        else:
                            nb.new_page_with_focus(tab.get("directory"), tab.get("label"), tab.get("custom_label_set", False))
        except (KeyError, IndexError, TypeError) as e:
            log.warning("Failed to restore tabs: %s", e, exc_info=True)
        finally:
            self.settings.general.set_boolean("save-tabs-when-changed", v)
            self.workspace_manager.workspaces_data["active_workspace"] = active_workspace_id

        if self.settings.general.get_boolean("restore-tabs-notify") and not suppress_notify:
            notifier.showMessage("Guake Terminal", "Your tabs have been restored!", pixmapfile("guake-notification.png"))
        
        # 4. After restoring, reconcile tabs with workspaces using the UUID list
        if self.workspace_manager:
            self.workspace_manager.reconcile_orphan_tabs(all_session_uuids)

        # 5. Show notebook, unset flag, and switch to active workspace
        notebook.show()
        self.is_restoring_session = False
        if active_workspace_id and self.workspace_manager.get_workspace_by_id(active_workspace_id):
            self.switch_to_workspace(active_workspace_id)
        elif self.workspace_manager.get_all_workspaces():
             first_ws = next((ws for ws in self.workspace_manager.get_all_workspaces() if not ws.get("is_special")), None)
             if first_ws:
                first_ws_id = first_ws["id"]
                self.workspace_manager.workspaces_data["active_workspace"] = first_ws_id
                self.workspace_manager.save_workspaces()
                self.switch_to_workspace(first_ws_id)

    def load_background_image(self, filename):
        self.background_image_manager.load_from_file(filename)

    def accel_world_map_navigation(self, *args):
        current_notebook = self.notebook_manager.get_current_notebook()
        if self.world_map_visible:
            self.mainframe.remove(self.world_map_view)
            self.mainframe.pack_start(current_notebook, True, True, 0)
            current_notebook.show()
            self.world_map_visible = False
            self.set_terminal_focus()
        else:
            self.world_map_view.refresh_view(force_data_update=False)
            self.mainframe.remove(current_notebook)
            self.mainframe.pack_start(self.world_map_view, True, True, 0)
            self.world_map_view.show()
            self.world_map_visible = True
            self.world_map_view.grab_focus()
        return True

    def switch_to_workspace(self, workspace_id):
        if not workspace_id or not self.workspace_manager: return
        workspace = self.workspace_manager.get_workspace_by_id(workspace_id)
        if not workspace: return
        
        terminals_in_ws = workspace.get("terminals", [])
        notebook = self.get_notebook()
        all_pages = [(i, notebook.get_nth_page(i)) for i in range(notebook.get_n_pages())]
        page_map = {str(list(p.iter_terminals())[0].uuid): (i, p) for i, p in all_pages if list(p.iter_terminals())}

        for _, page in all_pages: page.hide()

        first_visible_page_idx = -1
        page_to_focus_idx = -1
        
        for term_uuid in terminals_in_ws:
            if term_uuid in page_map:
                idx, page = page_map[term_uuid]
                page.show()
                if first_visible_page_idx == -1: first_visible_page_idx = idx
                if term_uuid == workspace.get("active_terminal"): page_to_focus_idx = idx
        
        if first_visible_page_idx == -1:
            if workspace.get("is_special"): # Don't add tabs to special workspace automatically
                return
            self.add_tab()
        else:
            notebook.set_current_page(page_to_focus_idx if page_to_focus_idx != -1 else first_visible_page_idx)
            self.set_terminal_focus()

    def on_tab_closed(self, notebook, child, page_num):
        terminals_in_page = list(child.iter_terminals())
        if terminals_in_page and self.workspace_manager:
            for term in terminals_in_page:
                self.workspace_manager.remove_terminal_from_active_workspace(str(term.uuid))
            active_ws = self.workspace_manager.get_active_workspace()
            if active_ws:
                self.switch_to_workspace(active_ws["id"])

    def populate_tab_context_menu(self, menu):
        # Find and remove existing "Send to workspace" menu to prevent duplication
        for item in menu.get_children():
            if item.get_label() == "Send to workspace":
                menu.remove(item)

        send_to_menu_item = Gtk.MenuItem(label="Send to workspace")
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(send_to_menu_item)
        
        submenu = Gtk.Menu()
        send_to_menu_item.set_submenu(submenu)
        
        send_to_menu_item.connect("activate", self.on_populate_send_to_menu, submenu)
        menu.show_all()

    def on_populate_send_to_menu(self, menu_item, submenu):
        for child in submenu.get_children(): submenu.remove(child)
        current_terminal = self.get_notebook().get_current_terminal()
        if not current_terminal: return

        workspaces = self.workspace_manager.get_all_workspaces()
        for ws in workspaces:
            if ws.get("is_special"): continue # Don't allow sending to special workspaces
            ws_item = Gtk.MenuItem(label=f"{ws.get('icon', '')} {ws['name']}")
            ws_item.connect("activate", self.on_send_terminal_to_workspace, str(current_terminal.uuid), ws["id"])
            submenu.append(ws_item)
        submenu.show_all()

    def on_populate_move_to_workspace_menu(self, menu_item, submenu, terminal_uuid):
        # Clear existing items
        for child in submenu.get_children():
            submenu.remove(child)
            
        workspaces = self.workspace_manager.get_all_workspaces()
        for ws in workspaces:
            if ws.get("is_special"): continue
            ws_item = Gtk.MenuItem(label=f"{ws.get('icon', '')} {ws['name']}")
            ws_item.connect("activate", self.on_send_terminal_to_workspace, terminal_uuid, ws["id"])
            submenu.append(ws_item)
        submenu.show_all()

    @save_tabs_when_changed
    def on_send_terminal_to_workspace(self, menu_item, terminal_uuid, target_workspace_id):
        log.debug("Sending terminal %s to workspace %s", terminal_uuid, target_workspace_id)
        self.workspace_manager.move_terminal_to_workspace(terminal_uuid, target_workspace_id)
        active_ws_id = self.workspace_manager.workspaces_data.get("active_workspace")
        self.switch_to_workspace(active_ws_id)
