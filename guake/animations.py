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

import math
import random
import cairo
from enum import Enum
from gi.repository import Gdk, Gtk

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
    NEURAL_NETWORK = 10
    VOXEL_GRID = 11

class AnimationDrawer:
    """A class to handle the drawing of all indicator animations."""

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

    def draw_spinner(self, widget, cr, animation_state=0.0, **_kwargs):
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)
        cx, cy, radius = width / 2, height / 2, min(width, height) / 2 - 2
        arc_length = math.pi * 0.8
        start_angle1 = animation_state * 2 * math.pi
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.set_line_width(1.5)
        cr.arc(cx, cy, radius, start_angle1, start_angle1 + arc_length)
        cr.stroke()
        start_angle2 = -animation_state * 2 * math.pi
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha * 0.4)
        cr.arc(cx, cy, radius, start_angle2, start_angle2 - arc_length)
        cr.stroke()

    def draw_ripple(self, widget, cr, animation_state=0.0, **_kwargs):
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)
        cx, cy = width / 2, height / 2
        ripple_radius = (width / 2) * animation_state
        ripple_alpha = color.alpha * (1.0 - animation_state)
        cr.set_source_rgba(color.red, color.green, color.blue, ripple_alpha)
        cr.set_line_width(1.5)
        cr.arc(cx, cy, ripple_radius, 0, 2 * math.pi)
        cr.stroke()
        dot_radius = width / 8
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.arc(cx, cy, dot_radius, 0, 2 * math.pi)
        cr.fill()

    def draw_aperture(self, widget, cr, animation_state=0.0, **_kwargs):
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)
        cx, cy = width / 2, height / 2
        outer_radius = min(width, height) / 2
        num_blades = 6
        ease_state = (math.sin(animation_state * math.pi - math.pi/2) + 1) / 2
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

    def draw_glitch(self, widget, cr, glitch_state=(0,0,False), **_kwargs):
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)
        size = min(width, height) / 2
        offset_x, offset_y, is_inverted = glitch_state
        x = (width - size) / 2 + offset_x
        y = (height - size) / 2 + offset_y
        if is_inverted:
            cr.set_source_rgba(1.0 - color.red, 1.0 - color.green, 1.0 - color.blue, color.alpha)
        else:
            cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.rectangle(x, y, size, size)
        cr.fill()

    def draw_chroma_wheel(self, widget, cr, animation_state=0.0, **_kwargs):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2
        num_segments = 12
        for i in range(num_segments):
            hue = (i / num_segments + animation_state) % 1.0
            r, g, b = self._hsl_to_rgb(hue, 1.0, 0.5)
            cr.set_source_rgb(r, g, b)
            start_angle = (i / num_segments) * 2 * math.pi
            end_angle = ((i + 1) / num_segments) * 2 * math.pi
            cr.move_to(cx, cy)
            cr.arc(cx, cy, radius, start_angle, end_angle)
            cr.close_path()
            cr.fill()

    def draw_firefly(self, widget, cr, animation_state=0.0, firefly_state=(0,0,0), **_kwargs):
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)
        x, y, _ = firefly_state
        alpha = math.sin(animation_state * math.pi)
        radgrad = cairo.RadialGradient(x * width, y * height, 0, x * width, y * height, width / 2)
        radgrad.add_color_stop_rgba(0, color.red, color.green, color.blue, alpha)
        radgrad.add_color_stop_rgba(1, color.red, color.green, color.blue, 0)
        cr.set_source(radgrad)
        cr.paint()

    def draw_matrix(self, widget, cr, matrix_state=None, **_kwargs):
        if matrix_state is None:
            matrix_state = []
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cr.set_source_rgb(0, 0.1, 0)
        cr.paint()
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(4)
        for drop in matrix_state:
            for i in range(10):
                char = random.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                alpha = 1.0 - (i / 10.0)
                cr.set_source_rgba(0.1, 1.0, 0.1, alpha)
                cr.move_to(drop[0] * 2, (drop[1] - i) * 2)
                cr.show_text(char)

    def draw_plasma(self, widget, cr, animation_state=0.0, **_kwargs):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        t = animation_state * 2 * math.pi
        for y in range(height):
            for x in range(width):
                v = math.sin(x / 4.0 + t) + math.sin((y / 2.0 + t) / 2.0) + math.sin((x + y + t) / 2.0)
                cx = x + 0.5 * math.sin(t / 5.0)
                cy = y + 0.5 * math.cos(t / 3.0)
                v += math.sin(math.sqrt(cx*cx + cy*cy) / 4.0 + t)
                color_val = (math.sin(v * math.pi) + 1) / 2
                r, g, b = self._hsl_to_rgb(color_val, 1.0, 0.5)
                cr.set_source_rgb(r, g, b)
                cr.rectangle(x, y, 1, 1)
                cr.fill()

    def draw_rotating_square(self, widget, cr, animation_state=0.0, **_kwargs):
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)
        cx, cy = width / 2, height / 2
        size = min(width, height) / 2
        cr.save()
        cr.translate(cx, cy)
        cr.rotate(animation_state * math.pi / 2)
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.rectangle(-size/2, -size/2, size, size)
        cr.fill()
        cr.restore()

    def draw_neural_network(self, widget, cr, animation_state=0.0, **_kwargs):
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)
        nodes = [(x*4+2, y*4+2) for x in range(4) for y in range(4)]
        for i, (x1, y1) in enumerate(nodes):
            for x2, y2 in nodes[i+1:]:
                dist = math.sqrt((x1-x2)**2 + (y1-y2)**2)
                if dist < 6:
                    alpha = (math.sin(animation_state * 2 * math.pi + dist) + 1) / 4
                    cr.set_source_rgba(color.red, color.green, color.blue, alpha)
                    cr.move_to(x1, y1)
                    cr.line_to(x2, y2)
                    cr.stroke()
        for x, y in nodes:
            alpha = (math.sin(animation_state * 2 * math.pi + x + y) + 1) / 2
            cr.set_source_rgba(color.red, color.green, color.blue, alpha)
            cr.arc(x, y, 1, 0, 2 * math.pi)
            cr.fill()

    def draw_voxel_grid(self, widget, cr, animation_state=0.0, **_kwargs):
        context = widget.get_style_context()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        color = context.get_color(Gtk.StateFlags.NORMAL)
        grid_size = 4
        for z in range(grid_size):
            for y in range(grid_size):
                for x in range(grid_size):
                    px = (x - y) * 1.5 + width / 2
                    py = (x + y) * 0.75 - z * 1.5 + height / 4
                    dist = math.sqrt((x-grid_size/2)**2 + (y-grid_size/2)**2 + (z-grid_size/2)**2)
                    alpha = (math.sin(dist - animation_state * 4 * math.pi) + 1) / 2
                    if alpha > 0.1:
                        cr.set_source_rgba(color.red, color.green, color.blue, alpha)
                        cr.rectangle(px, py, 2, 2)
                        cr.fill()
