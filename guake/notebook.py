# -*- coding: utf-8; -*-
"""
Copyright (C) 2007-2018 Guake authors

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

from guake.about import AboutDialog
from guake.boxes import RootTerminalBox
from guake.boxes import TabLabelEventBox
from guake.boxes import TerminalBox
from guake.callbacks import MenuHideCallback
from guake.callbacks import NotebookScrollCallback
from guake.dialogs import PromptQuitDialog
from guake.globals import PROMPT_ALWAYS
from guake.globals import PROMPT_PROCESSES
from guake.menus import mk_notebook_context_menu
from guake.prefs import PrefsDialog
from guake.utils import HidePrevention
from guake.utils import gdk_is_x11_display
from guake.utils import get_process_name
from guake.utils import save_tabs_when_changed

import gi
import os
import uuid
import math
import random
import cairo
from enum import Enum

gi.require_version("Gtk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import GObject
from gi.repository import Gdk
from gi.repository import Gtk
from gi.repository import Wnck
from guake.terminal import GuakeTerminal

import logging
import posix

log = logging.getLogger(__name__)

class IndicatorStyle(Enum):
    """Enumeration for the different indicator animation styles."""
    NONE = 0
    RIPPLE = 1
    SPINNER = 2
    APERTURE = 3
    GLITCH = 4
    CHROMA_WHEEL = 5
    FIREFLY = 6
    MATRIX = 7
    PLASMA = 8
    ROTATING_SQUARE = 9

class TabLabelWithIndicator(TabLabelEventBox):
    """A TabLabelEventBox that includes a blinking indicator for running processes."""
    def __init__(self, notebook, text, settings):
        super().__init__(notebook, text, settings)
        
        original_label = self.get_child()
        if original_label:
            self.remove(original_label)

        self.overlay = Gtk.Overlay()
        self.add(self.overlay)
        
        if original_label:
            self.overlay.add(original_label)

        # Configurable animation style, read from settings as requested
        self.style = IndicatorStyle(settings.general.get_int("tab-process-status-animation"))

        # Redesigned activity indicator
        self.activity_indicator = Gtk.DrawingArea()
        self.activity_indicator.set_size_request(16, 16)
        self.activity_indicator.set_halign(Gtk.Align.END)
        self.activity_indicator.set_valign(Gtk.Align.START)
        self.activity_indicator.set_margin_top(2) 
        self.activity_indicator.set_margin_end(2)
        self.activity_indicator.get_style_context().add_class("tab-activity-indicator")
        self.activity_indicator.connect("draw", self.on_draw_indicator)
        
        self.overlay.add_overlay(self.activity_indicator)
        self.overlay.set_overlay_pass_through(self.activity_indicator, True)

        # Animation state for the new indicator design
        self.animation_state = 0.0
        self.animation_timer_id = None
        self.animation_direction = 1
        self.glitch_state = (0, 0, False)
        self.firefly_state = (0, 0, 0) # x, y, alpha
        self.matrix_state = []
        
        self.show_all()
        self.activity_indicator.hide()

    def _animate_indicator(self):
        """Callback to drive the indicator animation."""
        if self.style == IndicatorStyle.SPINNER:
            self.animation_state = (self.animation_state + 0.02) % 1.0
        elif self.style == IndicatorStyle.RIPPLE:
            self.animation_state = (self.animation_state + 0.04) % 1.0
        elif self.style == IndicatorStyle.APERTURE:
            self.animation_state += self.animation_direction * 0.02
            if not (0.0 < self.animation_state < 1.0):
                self.animation_direction *= -1
                self.animation_state = max(0.0, min(1.0, self.animation_state))
        elif self.style == IndicatorStyle.GLITCH:
            self.glitch_state = (0, 0, False)
            if random.random() < 0.1:
                offset_x = random.randint(-2, 2)
                offset_y = random.randint(-2, 2)
                inverted = random.choice([True, False])
                self.glitch_state = (offset_x, offset_y, inverted)
        elif self.style == IndicatorStyle.CHROMA_WHEEL:
            self.animation_state = (self.animation_state + 0.01) % 1.0
        elif self.style == IndicatorStyle.FIREFLY:
            self.animation_state = (self.animation_state + 0.02) % 1.0
            if self.animation_state < 0.02: # Reset position at the start of a cycle
                self.firefly_state = (random.uniform(0.2, 0.8), random.uniform(0.2, 0.8), 0)
        elif self.style == IndicatorStyle.MATRIX:
            if not self.matrix_state or random.random() < 0.3:
                self.matrix_state.append([random.randint(0, 8), 0])
            for drop in self.matrix_state:
                drop[1] += 1
            self.matrix_state = [drop for drop in self.matrix_state if drop[1] < 10]
        elif self.style in [IndicatorStyle.PLASMA, IndicatorStyle.ROTATING_SQUARE]:
            self.animation_state = (self.animation_state + 0.02) % 1.0

        self.activity_indicator.queue_draw()
        return True

    def _hsl_to_rgb(self, h, s, l):
        """Converts HSL color value to RGB. Assumes h, s, and l are in [0, 1]."""
        if s == 0:
            r = g = b = l
        else:
            def hue2rgb(p, q, t):
                if t < 0: t += 1
                if t > 1: t -= 1
                if t < 1/6: return p + (q - p) * 6 * t
                if t < 1/2: return q
                if t < 2/3: return p + (q - p) * (2/3 - t) * 6
                return p

            q = l * (1 + s) if l < 0.5 else l + s - l * s
            p = 2 * l - q
            r = hue2rgb(p, q, h + 1/3)
            g = hue2rgb(p, q, h)
            b = hue2rgb(p, q, h - 1/3)
        return r, g, b

    def _draw_spinner(self, widget, cr):
        """Draws the 'orbital spinner' with two counter-rotating arcs."""
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        
        color = context.get_color(Gtk.StateFlags.NORMAL)
        
        cx = width / 2
        cy = height / 2
        radius = min(width, height) / 2 - 2
        
        arc_length = math.pi * 0.8

        start_angle1 = self.animation_state * 2 * math.pi
        end_angle1 = start_angle1 + arc_length
        
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.set_line_width(1.5)
        cr.arc(cx, cy, radius, start_angle1, end_angle1)
        cr.stroke()

        start_angle2 = -self.animation_state * 2 * math.pi
        end_angle2 = start_angle2 - arc_length
        
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha * 0.4)
        cr.set_line_width(1.5)
        cr.arc(cx, cy, radius, start_angle2, end_angle2)
        cr.stroke()

    def _draw_ripple(self, widget, cr):
        """Draws a central dot with an expanding, fading ripple."""
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        
        color = context.get_color(Gtk.StateFlags.NORMAL)
        
        cx = width / 2
        cy = height / 2

        ripple_radius = (width / 2) * self.animation_state
        ripple_alpha = color.alpha * (1.0 - self.animation_state)
        
        cr.set_source_rgba(color.red, color.green, color.blue, ripple_alpha)
        cr.set_line_width(1.5)
        cr.arc(cx, cy, ripple_radius, 0, 2 * math.pi)
        cr.stroke()

        dot_radius = width / 8
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.arc(cx, cy, dot_radius, 0, 2 * math.pi)
        cr.fill()

    def _draw_aperture(self, widget, cr):
        """Draws a set of blades that form a closing and opening aperture."""
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)

        cx = width / 2
        cy = height / 2
        outer_radius = min(width, height) / 2
        num_blades = 6
        
        ease_state = (math.sin(self.animation_state * math.pi - math.pi/2) + 1) / 2
        inner_radius = outer_radius * ease_state

        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)

        for i in range(num_blades):
            angle = i * (2 * math.pi / num_blades)
            
            cr.save()
            cr.translate(cx, cy)
            cr.rotate(angle)

            blade_angle = (2 * math.pi / num_blades) * 1.1 
            cr.move_to(0, 0)
            cr.line_to(outer_radius, 0)
            cr.arc_negative(0, 0, outer_radius, 0, blade_angle)
            cr.close_path()
            cr.fill()
            cr.restore()
        
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.arc(cx, cy, inner_radius, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

    def _draw_glitch(self, widget, cr):
        """Draws a square that randomly jumps and inverts its color."""
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)

        size = min(width, height) / 2
        offset_x, offset_y, is_inverted = self.glitch_state

        x = (width - size) / 2 + offset_x
        y = (height - size) / 2 + offset_y

        if is_inverted:
            cr.set_source_rgba(1.0 - color.red, 1.0 - color.green, 1.0 - color.blue, color.alpha)
        else:
            cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)

        cr.rectangle(x, y, size, size)
        cr.fill()

    def _draw_chroma_wheel(self, widget, cr):
        """Draws a rotating, multi-colored wheel for a hypnotic effect."""
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        
        cx = width / 2
        cy = height / 2
        radius = min(width, height) / 2
        num_segments = 12

        for i in range(num_segments):
            hue = (i / num_segments + self.animation_state) % 1.0
            
            r, g, b = self._hsl_to_rgb(hue, 1.0, 0.5)
            cr.set_source_rgb(r, g, b)
            
            start_angle = (i / num_segments) * 2 * math.pi
            end_angle = ((i + 1) / num_segments) * 2 * math.pi

            cr.move_to(cx, cy)
            cr.arc(cx, cy, radius, start_angle, end_angle)
            cr.close_path()
            cr.fill()

    def _draw_firefly(self, widget, cr):
        """Draws a soft, pulsing light that appears at random locations."""
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)

        x, y, _ = self.firefly_state
        alpha = math.sin(self.animation_state * math.pi)

        radgrad = cairo.RadialGradient(x * width, y * height, 0,
                                       x * width, y * height, width / 2)
        radgrad.add_color_stop_rgba(0, color.red, color.green, color.blue, alpha)
        radgrad.add_color_stop_rgba(1, color.red, color.green, color.blue, 0)
        
        cr.set_source(radgrad)
        cr.paint()

    def _draw_matrix(self, widget, cr):
        """Draws green characters 'raining' down the indicator area."""
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        
        cr.set_source_rgb(0, 0.1, 0)
        cr.paint()

        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(4)
        
        for drop in self.matrix_state:
            for i in range(10):
                char = random.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                alpha = 1.0 - (i / 10.0)
                cr.set_source_rgba(0.1, 1.0, 0.1, alpha)
                cr.move_to(drop[0] * 2, (drop[1] - i) * 2)
                cr.show_text(char)
    
    def _draw_plasma(self, widget, cr):
        """Draws a classic 90s plasma effect."""
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        t = self.animation_state * 2 * math.pi

        for y in range(height):
            for x in range(width):
                v = math.sin(x / 4.0 + t)
                v += math.sin((y / 2.0 + t) / 2.0)
                v += math.sin((x + y + t) / 2.0)
                cx = x + 0.5 * math.sin(t / 5.0)
                cy = y + 0.5 * math.cos(t / 3.0)
                v += math.sin(math.sqrt(cx*cx + cy*cy) / 4.0 + t)
                
                color_val = (math.sin(v * math.pi) + 1) / 2
                
                hue = color_val
                r, g, b = self._hsl_to_rgb(hue, 1.0, 0.5)
                cr.set_source_rgb(r, g, b)
                cr.rectangle(x, y, 1, 1)
                cr.fill()

    def _draw_rotating_square(self, widget, cr):
        """Draws a rotating pixel-art style square."""
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)
        
        cx = width / 2
        cy = height / 2
        size = min(width, height) / 2

        cr.save()
        cr.translate(cx, cy)
        cr.rotate(self.animation_state * math.pi / 2)
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.rectangle(-size/2, -size/2, size, size)
        cr.fill()
        cr.restore()

    def on_draw_indicator(self, widget, cr):
        """Dispatches to the appropriate drawing function based on the selected style."""
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()

        if self.style == IndicatorStyle.SPINNER:
            self._draw_spinner(widget, cr)
        elif self.style == IndicatorStyle.RIPPLE:
            self._draw_ripple(widget, cr)
        elif self.style == IndicatorStyle.APERTURE:
            self._draw_aperture(widget, cr)
        elif self.style == IndicatorStyle.GLITCH:
            self._draw_glitch(widget, cr)
        elif self.style == IndicatorStyle.CHROMA_WHEEL:
            self._draw_chroma_wheel(widget, cr)
        elif self.style == IndicatorStyle.FIREFLY:
            self._draw_firefly(widget, cr)
        elif self.style == IndicatorStyle.MATRIX:
            self._draw_matrix(widget, cr)
        elif self.style == IndicatorStyle.PLASMA:
            self._draw_plasma(widget, cr)
        elif self.style == IndicatorStyle.ROTATING_SQUARE:
            self._draw_rotating_square(widget, cr)

        return False

    def set_activity(self, is_active):
        """Controls the visibility and animation of the activity indicator."""
        self.activity_indicator.set_visible(is_active)
        if is_active and self.style != IndicatorStyle.NONE:
            if self.animation_timer_id is None:
                interval = 100 if self.style in [IndicatorStyle.GLITCH, IndicatorStyle.MATRIX] else 33
                self.animation_timer_id = GObject.timeout_add(interval, self._animate_indicator)
        else:
            if self.animation_timer_id is not None:
                GObject.source_remove(self.animation_timer_id)
                self.animation_timer_id = None
                self.animation_state = 0.0


class TerminalNotebook(Gtk.Notebook):
    def __init__(self, *args, **kwargs):
        Gtk.Notebook.__init__(self, *args, **kwargs)
        self.last_terminal_focused = None

        self.set_name("notebook-teminals")
        self.set_tab_pos(Gtk.PositionType.BOTTOM)
        self.set_property("show-tabs", True)
        self.set_property("enable-popup", False)
        self.set_property("scrollable", True)
        self.set_property("show-border", False)
        self.set_property("visible", True)
        self.set_property("has-focus", True)
        self.set_property("can-focus", True)
        self.set_property("is-focus", True)
        self.set_property("expand", True)

        if GObject.signal_lookup("terminal-spawned", TerminalNotebook) == 0:
            GObject.signal_new(
                "terminal-spawned",
                TerminalNotebook,
                GObject.SignalFlags.RUN_LAST,
                GObject.TYPE_NONE,
                (GObject.TYPE_PYOBJECT, GObject.TYPE_INT),
            )
            GObject.signal_new(
                "page-deleted",
                TerminalNotebook,
                GObject.SignalFlags.RUN_LAST,
                GObject.TYPE_NONE,
                (),
            )

        self.scroll_callback = NotebookScrollCallback(self)
        self.add_events(Gdk.EventMask.SCROLL_MASK)
        self.connect("scroll-event", self.scroll_callback.on_scroll)
        self.notebook_on_button_press_id = self.connect(
            "button-press-event", self.on_button_press, None
        )

        # Action box
        self.pin_button = Gtk.ToggleButton(
            image=Gtk.Image.new_from_icon_name("view-pin-symbolic", Gtk.IconSize.MENU),
            visible=False,
        )
        self.pin_button.connect("clicked", self.on_pin_clicked)
        self.new_page_button = Gtk.Button(
            image=Gtk.Image.new_from_icon_name("tab-new-symbolic", Gtk.IconSize.MENU),
            visible=True,
        )
        self.new_page_button.connect("clicked", self.on_new_tab)

        self.tab_selection_button = Gtk.Button(
            image=Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU),
            visible=True,
        )
        self.popover = Gtk.Popover()
        self.popover_window = None
        self.tab_selection_button.connect("clicked", self.on_tab_selection)

        self.action_box = Gtk.Box(visible=True)
        self.action_box.pack_start(self.pin_button, 0, 0, 0)
        self.action_box.pack_start(self.new_page_button, 0, 0, 0)
        self.action_box.pack_start(self.tab_selection_button, 0, 0, 0)
        self.set_action_widget(self.action_box, Gtk.PackType.END)

        self.workspace_indicator = Gtk.Label()
        self.workspace_indicator.set_margin_start(10)
        self.workspace_indicator.show()
        self.set_action_widget(self.workspace_indicator, Gtk.PackType.START)

    def update_workspace_indicator(self, workspace_data):
        if workspace_data:
            icon = workspace_data.get('icon', '')
            name = workspace_data.get('name', '')
            self.workspace_indicator.set_text(f"{icon} {name}")
        else:
            self.workspace_indicator.set_text("")

    def attach_guake(self, guake):
        self.guake = guake

        self.guake.settings.general.onChangedValue("window-losefocus", self.on_lose_focus_toggled)
        self.pin_button.set_visible(self.guake.settings.general.get_boolean("window-losefocus"))

    def on_button_press(self, target, event, user_data):
        if event.button == 3:
            menu = mk_notebook_context_menu(self)
            menu.connect("hide", MenuHideCallback(self.guake.window).on_hide)

            try:
                menu.popup_at_pointer(event)
            except AttributeError:
                # Gtk 3.18 fallback ("'Menu' object has no attribute 'popup_at_pointer'")
                menu.popup(None, None, None, None, event.button, event.time)
        elif (
            event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS
            and event.button == 1
            and event.window.get_height() < 60
        ):
            # event.window.get_height() reports the height of the clicked frame
            self.new_page_with_focus()

        return False

    def on_pin_clicked(self, user_data=None):
        hide_prevention = HidePrevention(self.guake.window)
        if self.pin_button.get_active():
            hide_prevention.prevent()
        else:
            hide_prevention.allow()

    def on_lose_focus_toggled(self, settings, key, user_data=None):
        self.pin_button.set_visible(settings.get_boolean(key))

    @save_tabs_when_changed
    def on_new_tab(self, user_data):
        self.new_page_with_focus()

    def on_tab_selection(self, user_data):
        """Construct the tab selection popover

        Since we did not use Gtk.ListStore to store tab information, we will construct the
        tab selection popover content each time when user click them.
        """

        # Remove previous window
        if self.popover_window:
            self.popover.remove(self.popover_window)

        # This makes the list's background transparent
        # ref: epiphany
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"#popover-window list { border-style: none; background-color: transparent; }"
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Construct popover properties
        BOX_HEIGHT = 30
        LISTBOX_MARGIN = 12
        self.popover_window = Gtk.ScrolledWindow(name="popover-window")
        self.popover_listbox = Gtk.ListBox()
        self.popover_listbox.set_property("margin", LISTBOX_MARGIN)
        self.popover_window.add_with_viewport(self.popover_listbox)

        max_height = (
            self.guake.window.get_allocation().height - BOX_HEIGHT
            if self.guake
            else BOX_HEIGHT * 10
        )
        height = BOX_HEIGHT * self.get_n_pages() + LISTBOX_MARGIN * 4
        self.popover_window.set_min_content_height(min(max_height, height))
        self.popover_window.set_min_content_width(325)
        self.popover.add(self.popover_window)

        # Construct content
        current_term = self.get_current_terminal()
        selected_row = 0
        for i in range(self.get_n_pages()):
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            box.set_size_request(200, BOX_HEIGHT)
            label = Gtk.Label(self.get_tab_text_index(i))
            label.set_xalign(0.0)
            box.pack_start(label, 0, 0, 5)
            row.add(box)
            setattr(row, "page_index", i)
            self.popover_listbox.add(row)
            if current_term in self.get_terminals_for_page(i):
                self.popover_listbox.select_row(row)
                selected_row = i

        # Signal
        self.popover_listbox.connect("row-activated", self.on_popover_tab_select)

        # Show popup
        self.popover.set_position(Gtk.PositionType.TOP)
        self.popover.set_relative_to(user_data)
        self.popover.show_all()
        try:
            # For GTK >= 3.22
            self.popover.popup()
        except AttributeError:
            pass

        # Adjust scrollor
        while Gtk.events_pending():
            Gtk.main_iteration()

        if selected_row:
            adj = self.popover_window.get_vadjustment()
            v = adj.get_upper() - adj.get_page_size()
            part = v / self.get_n_pages()
            adj.set_value(part * (selected_row + 1))

    def on_popover_tab_select(self, list_box, row):
        page_index = getattr(row, "page_index", -1)
        if page_index != -1:
            self.set_current_page(page_index)
            self.get_terminals_for_page(page_index)[0].grab_focus()

    def set_tabbar_visible(self, v):
        self.set_property("show-tabs", v)

    def set_last_terminal_focused(self, terminal):
        self.last_terminal_focused = terminal

    def get_focused_terminal(self):
        for terminal in self.iter_terminals():
            if terminal.has_focus():
                return terminal

    def get_current_terminal(self):
        # TODO NOTEBOOK the name of this method should
        # be changed, for now it returns the last focused terminal
        return self.last_terminal_focused

    def get_terminals_for_page(self, index):
        page = self.get_nth_page(index)
        return page.get_terminals()

    def get_terminals(self):
        terminals = []
        for page in self.iter_pages():
            terminals += page.get_terminals()
        return terminals

    def get_running_fg_processes(self):
        processes = []
        for page in self.iter_pages():
            processes += self.get_running_fg_processes_page(page)
        return processes

    def get_running_fg_processes_page(self, page):
        processes = []
        for terminal in page.get_terminals():
            pty = terminal.get_pty()
            if not pty:
                continue
            fdpty = pty.get_fd()
            term_pid = terminal.pid
            try:
                fgpid = posix.tcgetpgrp(fdpty)
                log.debug("found running pid: %s", fgpid)
                if fgpid not in (-1, term_pid):
                    processes.append((fgpid, get_process_name(fgpid)))
            except OSError:
                log.debug(
                    "Cannot retrieve any pid from terminal %s, looks like it is already dead",
                    terminal,
                )
        return processes

    def has_page(self):
        return self.get_n_pages() > 0

    def iter_terminals(self):
        for page in self.iter_pages():
            if page is not None:
                for t in page.iter_terminals():
                    yield t

    def iter_tabs(self):
        for page_num in range(self.get_n_pages()):
            yield self.get_tab_label(self.get_nth_page(page_num))

    def iter_pages(self):
        for page_num in range(self.get_n_pages()):
            yield self.get_nth_page(page_num)

    def delete_page(self, page_num, kill=True, prompt=0):
        log.debug("Deleting page index %s", page_num)
        if page_num >= self.get_n_pages() or page_num < 0:
            log.error("Can not delete page %s no such index", page_num)
            return

        page = self.get_nth_page(page_num)
        # TODO NOTEBOOK it would be nice if none of the "ui" stuff
        # (PromptQuitDialog) would be in here
        procs = self.get_running_fg_processes_page(page)
        if prompt == PROMPT_ALWAYS or (prompt == PROMPT_PROCESSES and procs):
            # TODO NOTEBOOK remove call to guake
            if not PromptQuitDialog(self.guake.window, procs, -1, None).close_tab():
                return

        for terminal in page.get_terminals():
            if kill:
                terminal.kill()
            terminal.destroy()

        if self.get_nth_page(page_num) is page:
            # NOTE: GitHub issue #1438
            # Previous line `terminal.destroy()` will finally called `on_terminal_exited`,
            # and called `RootTerminalBox.remove_dead_child`, then called `remove_page`.
            #
            # But in some cases (e.g. #1438), it will not remove the page by
            # `terminal.destory() chain`.
            #
            # Check this by compare same page_num page with previous saved page instance,
            # and remove the page if it really didn't remove it.
            self.remove_page(page_num)

    @save_tabs_when_changed
    def remove_page(self, page_num):
        super().remove_page(page_num)
        # focusing the first terminal on the previous page
        if self.get_current_page() > -1:
            page = self.get_nth_page(self.get_current_page())
            if page.get_terminals():
                page.get_terminals()[0].grab_focus()

        self.hide_tabbar_if_one_tab()
        self.emit("page-deleted")

    def delete_page_by_label(self, label, kill=True, prompt=0):
        self.delete_page(self.find_tab_index_by_label(label), kill, prompt)

    def delete_page_current(self, kill=True, prompt=0):
        self.delete_page(self.get_current_page(), kill, prompt)

    def new_page(self, directory=None, position=None, empty=False, open_tab_cwd=False, terminal_uuid=None):
        terminal_box = TerminalBox()
        if empty:
            terminal = None
        else:
            terminal = self.terminal_spawn(directory, open_tab_cwd, terminal_uuid=terminal_uuid)
            terminal_box.set_terminal(terminal)
        root_terminal_box = RootTerminalBox(self.guake, self)
        root_terminal_box.set_child(terminal_box)
        page_num = self.insert_page(
            root_terminal_box, None, position if position is not None else -1
        )
        self.set_tab_reorderable(root_terminal_box, True)
        root_terminal_box.show_all()
        # this is needed because self.window.show_all() results in showing every
        # thing which includes the scrollbar too
        self.guake.settings.general.triggerOnChangedValue(
            self.guake.settings.general, "use-scrollbar"
        )
        # this is needed to initially set the last_terminal_focused,
        # one could also call terminal.get_parent().on_terminal_focus()
        if not empty:
            self.terminal_attached(terminal)
        self.hide_tabbar_if_one_tab()

        if self.guake:
            # Attack background image draw callback to root terminal box
            root_terminal_box.connect_after("draw", self.guake.background_image_manager.draw)
        return root_terminal_box, page_num, terminal

    def hide_tabbar_if_one_tab(self):
        """Hide the tab bar if hide-tabs-if-one-tab is true and there is only one
        notebook page"""
        if self.guake.settings.general.get_boolean("window-tabbar"):
            if self.guake.settings.general.get_boolean("hide-tabs-if-one-tab"):
                self.set_property("show-tabs", self.get_n_pages() > 1)
            else:
                self.set_property("show-tabs", True)

    def terminal_spawn(self, directory=None, open_tab_cwd=False, terminal_uuid=None):
        terminal = GuakeTerminal(self.guake)
        if terminal_uuid:
            if isinstance(terminal_uuid, str):
                terminal.uuid = uuid.UUID(terminal_uuid)
            else:
                terminal.uuid = terminal_uuid
        terminal.grab_focus()
        terminal.connect(
            "key-press-event",
            lambda x, y: self.guake.accel_group.activate(x, y) if self.guake.accel_group else False,
        )
        if not isinstance(directory, str):
            directory = os.environ["HOME"]
            try:
                if self.guake.settings.general.get_boolean("open-tab-cwd") or open_tab_cwd:
                    # Do last focused terminal still alive?
                    active_terminal = self.get_current_terminal()
                    if not active_terminal:
                        # If not alive, can we get any focused terminal?
                        active_terminal = self.get_focused_terminal()
                    directory = os.path.expanduser("~")
                    if active_terminal:
                        # If found, we will use its directory as new terminal's directory
                        directory = active_terminal.get_current_directory()
            except BaseException:
                pass
        log.info("Spawning new terminal at %s", directory)
        terminal.spawn_sync_pid(directory)
        return terminal

    def terminal_attached(self, terminal):
        terminal.emit("focus", Gtk.DirectionType.TAB_FORWARD)
        self.emit("terminal-spawned", terminal, terminal.pid)

    def new_page_with_focus(
        self,
        directory=None,
        label=None,
        user_set=False,
        position=None,
        empty=False,
        open_tab_cwd=False,
        terminal_uuid=None,
    ):
        box, page_num, terminal = self.new_page(
            directory,
            position=position,
            empty=empty,
            open_tab_cwd=open_tab_cwd,
            terminal_uuid=terminal_uuid,
        )
        self.set_current_page(page_num)
        if not label:
            self.rename_page(page_num, self.guake.compute_tab_title(terminal), False)
        else:
            self.rename_page(page_num, label, user_set)
        if terminal is not None:
            terminal.grab_focus()
        return box, page_num, terminal

    @save_tabs_when_changed
    def rename_page(self, page_index, new_text, user_set=False):
        """Rename an already added page by its index. Use user_set to define
        if the rename was triggered by the user (eg. rename dialog) or by
        an update from the vte (eg. vte:window-title-changed)
        """
        page = self.get_nth_page(page_index)
        if not getattr(page, "custom_label_set", False) or user_set:
            old_widget = self.get_tab_label(page)
            if isinstance(old_widget, TabLabelWithIndicator):
                old_widget.set_text(new_text)
            else:
                label = TabLabelWithIndicator(self, new_text, self.guake.settings)
                label.add_events(Gdk.EventMask.SCROLL_MASK)
                label.connect("scroll-event", self.scroll_callback.on_scroll)
                self.set_tab_label(page, label)

            if user_set:
                setattr(page, "custom_label_set", new_text != "-")

    def find_tab_index_by_label(self, eventbox):
        for index, tab_eventbox in enumerate(self.iter_tabs()):
            if eventbox is tab_eventbox:
                return index
        return -1

    def find_page_index_by_terminal(self, terminal):
        for index, page in enumerate(self.iter_pages()):
            for t in page.iter_terminals():
                if t is terminal:
                    return index
        return -1

    def get_tab_text_index(self, index):
        return self.get_tab_label(self.get_nth_page(index)).get_text()

    def get_tab_text_page(self, page):
        return self.get_tab_label(page).get_text()

    def on_show_preferences(self, user_data):
        self.guake.hide()
        PrefsDialog(self.guake.settings).show()

    def on_show_about(self, user_data):
        self.guake.hide()
        AboutDialog()

    def on_quit(self, user_data):
        self.guake.accel_quit()

    def on_save_tabs(self, user_data):
        self.guake.save_tabs()

    def on_restore_tabs(self, user_data):
        self.guake.restore_tabs()

    def on_restore_tabs_with_dialog(self, user_data):
        dialog = Gtk.MessageDialog(
            parent=self.guake.window,
            flags=Gtk.DialogFlags.MODAL,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            message_format=_(
                "You are going to restore *all* the tabs!\n"
                "which means all your terminals & pages "
                "will be replaced.\n\nDo you want to continue?"
            ),
        )
        dialog.connect("response", self.restore_tabs_dialog_response)
        dialog.show()

    def restore_tabs_dialog_response(self, widget, response_id):
        widget.destroy()
        if response_id == Gtk.ResponseType.OK:
            self.guake.restore_tabs()


class NotebookManager(GObject.Object):
    def __init__(
        self,
        window,
        notebook_parent,
        workspaces_enabled,
        terminal_spawned_cb,
        page_deleted_cb,
    ):
        GObject.Object.__init__(self)
        if not GObject.signal_lookup("notebook-created", self):
            GObject.signal_new(
                "notebook-created",
                self,
                GObject.SignalFlags.RUN_LAST,
                GObject.TYPE_NONE,
                (GObject.TYPE_PYOBJECT, GObject.TYPE_INT),
            )
        self.current_notebook = 0
        self.notebooks = {}
        self.window = window
        self.notebook_parent = notebook_parent
        self.terminal_spawned_cb = terminal_spawned_cb
        self.page_deleted_cb = page_deleted_cb
        if workspaces_enabled and gdk_is_x11_display(Gdk.Display.get_default()):
            # NOTE: Wnck didn't support non-X11 display backend, so we need to check if the display
            #       is X11 or not, if not, it will not able to enable workspace-specific-tab-sets
            #
            # TODO: Is there anyway to support this in non-X11 display backend?
            self.screen = Wnck.Screen.get_default()
            self.screen.connect("active-workspace-changed", self.__workspace_changed_cb)

    def __workspace_changed_cb(self, screen, previous_workspace):
        self.set_workspace(self.screen.get_active_workspace().get_number())

    def get_notebook(self, workspace_index: int):
        if not self.has_notebook_for_workspace(workspace_index):
            self.notebooks[workspace_index] = TerminalNotebook()
            self.emit("notebook-created", self.notebooks[workspace_index], workspace_index)
            self.notebooks[workspace_index].connect("terminal-spawned", self.terminal_spawned_cb)
            self.notebooks[workspace_index].connect("page-deleted", self.page_deleted_cb)
            log.info("created fresh notebook for workspace %d", self.current_notebook)

            # add a tab if there is none
            if not self.notebooks[workspace_index].has_page():
                self.notebooks[workspace_index].new_page_with_focus(None)

        return self.notebooks[workspace_index]

    def get_current_notebook(self):
        return self.get_notebook(self.current_notebook)

    def has_notebook_for_workspace(self, workspace_index):
        return workspace_index in self.notebooks

    def set_workspace(self, index: int):
        self.notebook_parent.remove(self.get_current_notebook())
        self.current_notebook = index
        log.info("current workspace is %d", self.current_notebook)
        notebook = self.get_current_notebook()
        self.notebook_parent.add(notebook)
        if self.window.get_property("visible") and notebook.last_terminal_focused is not None:
            notebook.last_terminal_focused.grab_focus()

        # Restore pending page terminal split
        notebook.guake.restore_pending_terminal_split()

        # Restore config to workspace
        notebook.guake.load_config()

    def set_notebooks_tabbar_visible(self, v):
        for nb in self.iter_notebooks():
            nb.set_tabbar_visible(v)

    def get_notebooks(self):
        return self.notebooks

    def get_terminals(self):
        terminals = []
        for k in self.notebooks:
            terminals += self.notebooks[k].get_terminals()
        return terminals

    def iter_terminals(self):
        for k in self.notebooks:
            for t in self.notebooks[k].iter_terminals():
                yield t

    def get_terminal_by_uuid(self, terminal_uuid):
        for t in self.iter_terminals():
            if t.uuid == terminal_uuid:
                return t
        return None

    def iter_pages(self):
        for k in self.notebooks:
            for t in self.notebooks[k].iter_pages():
                yield t

    def iter_notebooks(self):
        for k in self.notebooks:
            yield self.notebooks[k]

    def get_n_pages(self):
        n = 0
        for k in self.notebooks:
            n += self.notebooks[k].get_n_pages()
        return n

    def get_n_notebooks(self):
        return len(self.notebooks.keys())

    def get_running_fg_processes(self):
        processes = []
        for k in self.notebooks:
            processes += self.notebooks[k].get_running_fg_processes()
        return processes
