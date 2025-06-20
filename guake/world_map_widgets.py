# -*- coding: utf-8; -*-
"""
UI Components for the World Map view.
"""
import logging
import gi
import cairo

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

log = logging.getLogger(__name__)


class TerminalMinimap(Gtk.DrawingArea):
    """
    A widget that draws a miniature, text-based preview of a terminal.
    This is much faster than taking a full graphical snapshot.
    """
    def __init__(self, terminal, bg_color, fg_color):
        super().__init__()
        self.terminal = terminal
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.connect("draw", self.on_draw_minimap)
        self.set_size_request(220, 130)

    def get_minimap_row_height(self):
        # A small, efficient font size for the minimap text
        return 4 

    def on_draw_minimap(self, widget, cr):
        """The main drawing function for the minimap, inspired by user's logic."""
        m_width = widget.get_allocated_width()
        m_height = widget.get_allocated_height()
        row_height = self.get_minimap_row_height()

        # Set background
        cr.set_source_rgba(self.bg_color.red, self.bg_color.green, self.bg_color.blue, self.bg_color.alpha)
        cr.paint()
        
        # Set text properties
        cr.set_source_rgba(self.fg_color.red, self.fg_color.green, self.fg_color.blue, 1)
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(row_height - 1)

        try:
            terminal_content, _ = self.terminal.get_text(None, None, True)
            if not terminal_content:
                return
        except Exception:
            # Failsafe if terminal is not ready
            return

        t_width_chars = self.terminal.get_column_count()
        if t_width_chars <= 0: return

        # Draw the text line by line
        y_pos = row_height
        for line in terminal_content.split('\n'):
            for i in range(0, len(line), t_width_chars):
                if y_pos > m_height: break
                chunk = line[i:i+t_width_chars]
                cr.move_to(1, y_pos)
                cr.show_text(chunk.replace('\x00', ''))
                y_pos += row_height
            if y_pos > m_height: break
        
        return False
