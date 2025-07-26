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
    KALEIDOSCOPE = 12
    FRACTAL_TREE = 13
    AUDIO_VISUALIZER = 14
    WARP_SPEED = 15
    STARGATE = 16
    CONSTELLATION = 17
    GUITAR_STRING = 18

class AnimationTarget(Enum):
    """Specifies the intended drawing area for an animation."""
    CORNER = 1
    FULL_WIDTH = 2

class AnimationDrawer:
    """A class to handle the drawing of all indicator animations."""

    STYLE_TARGETS = {
        IndicatorStyle.NONE: AnimationTarget.CORNER,
        IndicatorStyle.RIPPLE: AnimationTarget.CORNER,
        IndicatorStyle.SPINNER: AnimationTarget.CORNER,
        IndicatorStyle.APERTURE: AnimationTarget.CORNER,
        IndicatorStyle.GLITCH: AnimationTarget.CORNER,
        IndicatorStyle.CHROMA_WHEEL: AnimationTarget.CORNER,
        IndicatorStyle.FIREFLY: AnimationTarget.CORNER,
        IndicatorStyle.MATRIX: AnimationTarget.CORNER,
        IndicatorStyle.PLASMA: AnimationTarget.CORNER,
        IndicatorStyle.ROTATING_SQUARE: AnimationTarget.CORNER,
        IndicatorStyle.NEURAL_NETWORK: AnimationTarget.CORNER,
        IndicatorStyle.VOXEL_GRID: AnimationTarget.CORNER,
        IndicatorStyle.KALEIDOSCOPE: AnimationTarget.CORNER,
        IndicatorStyle.FRACTAL_TREE: AnimationTarget.CORNER,
        IndicatorStyle.AUDIO_VISUALIZER: AnimationTarget.CORNER,
        IndicatorStyle.WARP_SPEED: AnimationTarget.CORNER,
        IndicatorStyle.STARGATE: AnimationTarget.CORNER,
        IndicatorStyle.CONSTELLATION: AnimationTarget.CORNER,
        IndicatorStyle.GUITAR_STRING: AnimationTarget.FULL_WIDTH,
    }

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

    def update_state(self, indicator):
        """Updates the animation state for the current style."""
        style = indicator.style
        
        if style == IndicatorStyle.SPINNER:
            indicator.animation_state = (indicator.animation_state + 0.02) % 1.0
        elif style == IndicatorStyle.RIPPLE:
            indicator.animation_state = (indicator.animation_state + 0.04) % 1.0
        elif style == IndicatorStyle.APERTURE:
            indicator.animation_state += indicator.animation_direction * 0.02
            if not (0.0 < indicator.animation_state < 1.0):
                indicator.animation_direction *= -1
                indicator.animation_state = max(0.0, min(1.0, indicator.animation_state))
        elif style == IndicatorStyle.GLITCH:
            indicator.glitch_state = (0, 0, False)
            if random.random() < 0.1:
                offset_x = random.randint(-2, 2)
                offset_y = random.randint(-2, 2)
                inverted = random.choice([True, False])
                indicator.glitch_state = (offset_x, offset_y, inverted)
        elif style == IndicatorStyle.CHROMA_WHEEL:
            indicator.animation_state = (indicator.animation_state + 0.01) % 1.0
        elif style == IndicatorStyle.FIREFLY:
            indicator.animation_state = (indicator.animation_state + 0.02) % 1.0
            if indicator.animation_state < 0.02:
                indicator.firefly_state = (random.uniform(0.2, 0.8), random.uniform(0.2, 0.8), 0)
        elif style == IndicatorStyle.MATRIX:
            if not indicator.matrix_state or random.random() < 0.3:
                indicator.matrix_state.append([random.randint(0, 8), 0])
            for drop in indicator.matrix_state:
                drop[1] += 1
            indicator.matrix_state = [drop for drop in indicator.matrix_state if drop[1] < 10]
        elif style == IndicatorStyle.WARP_SPEED:
            if not indicator.warp_stars:
                width = indicator.activity_indicator.get_allocated_width()
                indicator.warp_stars = [{'angle': random.uniform(0, 2 * math.pi), 'dist': random.uniform(0, width/2), 'speed': random.uniform(0.1, 0.5)} for _ in range(50)]
            for star in indicator.warp_stars:
                star['dist'] += star['speed']
                if star['dist'] > indicator.activity_indicator.get_allocated_width() / 2:
                    star['dist'] = 0
                    star['angle'] = random.uniform(0, 2 * math.pi)
        elif style == IndicatorStyle.CONSTELLATION:
            if not indicator.constellation_stars:
                width = indicator.activity_indicator.get_allocated_width()
                height = indicator.activity_indicator.get_allocated_height()
                indicator.constellation_stars = [{'x': random.uniform(0, width), 'y': random.uniform(0, height), 'dx': random.uniform(-0.1, 0.1), 'dy': random.uniform(-0.1, 0.1)} for _ in range(10)]
            for star in indicator.constellation_stars:
                star['x'] += star['dx']
                star['y'] += star['dy']
                if not (0 < star['x'] < indicator.activity_indicator.get_allocated_width()): star['dx'] *= -1
                if not (0 < star['y'] < indicator.activity_indicator.get_allocated_height()): star['dy'] *= -1
        elif style == IndicatorStyle.GUITAR_STRING:
            pace = 0.008 + (indicator.cpu_load / 100.0) * 0.04
            indicator.animation_state = (indicator.animation_state + pace) % 1.0
        
        if style not in [IndicatorStyle.GLITCH, IndicatorStyle.MATRIX, IndicatorStyle.GUITAR_STRING]:
             indicator.animation_state = (indicator.animation_state + 0.02) % 1.0

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

    def draw_kaleidoscope(self, widget, cr, animation_state=0.0, **_kwargs):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cx, cy = width / 2, height / 2
        num_segments = 8
        t = animation_state * 2 * math.pi

        for i in range(num_segments):
            cr.save()
            cr.translate(cx, cy)
            cr.rotate(i * 2 * math.pi / num_segments)

            hue = (i / num_segments + animation_state) % 1.0
            r, g, b = self._hsl_to_rgb(hue, 0.9, 0.6)
            cr.set_source_rgb(r, g, b)

            p1_x = math.cos(t) * width / 4
            p1_y = math.sin(t) * height / 4
            p2_x = math.cos(t + math.pi / 2) * width / 5
            p2_y = math.sin(t + math.pi / 2) * height / 5

            cr.move_to(0, 0)
            cr.line_to(p1_x, p1_y)
            cr.line_to(p2_x, p2_y)
            cr.close_path()
            cr.fill()
            
            cr.restore()

    def draw_fractal_tree(self, widget, cr, animation_state=0.0, **_kwargs):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        t = animation_state * 2 * math.pi
        
        def draw_branch(x, y, angle, length, depth, wind_t):
            if depth == 0:
                return
            
            wind_effect = math.sin(wind_t * 2 + depth) * (0.8 / (depth + 1))
            
            x2 = x + math.cos(angle + wind_effect) * length
            y2 = y + math.sin(angle + wind_effect) * length
            
            cr.move_to(x, y)
            cr.line_to(x2, y2)
            cr.stroke()
            
            branch_angle = math.sin(t) * 0.5 + 0.7
            
            draw_branch(x2, y2, angle - branch_angle, length * 0.7, depth - 1, wind_t)
            draw_branch(x2, y2, angle + branch_angle, length * 0.7, depth - 1, wind_t)

        cr.set_source_rgb(0.9, 0.9, 0.9)
        cr.set_line_width(0.5)
        draw_branch(width / 2, height, -math.pi / 2, height / 3, 7, t)

    def draw_audio_visualizer(self, widget, cr, **_kwargs):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        num_bars = 8
        bar_width = width / num_bars
        
        for i in range(num_bars):
            bar_height = random.random() * height
            hue = i / num_bars
            r, g, b = self._hsl_to_rgb(hue, 1.0, 0.5)
            cr.set_source_rgb(r, g, b)
            cr.rectangle(i * bar_width, height - bar_height, bar_width, bar_height)
            cr.fill()

    def draw_warp_speed(self, widget, cr, warp_stars=None, **_kwargs):
        if warp_stars is None:
            return
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cx, cy = width / 2, height / 2
        
        cr.set_source_rgb(0, 0, 0)
        cr.paint()

        for star in warp_stars:
            x1 = cx + math.cos(star['angle']) * star['dist']
            y1 = cy + math.sin(star['angle']) * star['dist']
            x2 = cx + math.cos(star['angle']) * (star['dist'] + star['speed'] * 2)
            y2 = cy + math.sin(star['angle']) * (star['dist'] + star['speed'] * 2)
            
            alpha = star['dist'] / (width / 2)
            cr.set_source_rgba(1, 1, 1, alpha)
            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
            cr.stroke()

    def draw_stargate(self, widget, cr, animation_state=0.0, **_kwargs):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2
        
        for r in range(int(radius), 0, -2):
            r_offset = math.sin(r / 3.0 + animation_state * 8 * math.pi) * 2.5
            hue = (r / radius + animation_state * 2) % 1.0
            red, g, b = self._hsl_to_rgb(hue, 0.9, 0.6)
            cr.set_source_rgb(red, g, b)
            
            cr.arc(cx, cy, r + r_offset, 0, 2 * math.pi)
            cr.stroke()

    def draw_constellation(self, widget, cr, animation_state=0.0, constellation_stars=None, **_kwargs):
        if constellation_stars is None:
            return
        width = widget.get_allocated_width()
        
        cr.set_source_rgb(1, 1, 1)
        for star in constellation_stars:
            cr.arc(star['x'], star['y'], 1, 0, 2 * math.pi)
            cr.fill()

        for i, s1 in enumerate(constellation_stars):
            for s2 in constellation_stars[i+1:]:
                dist = math.sqrt((s1['x']-s2['x'])**2 + (s1['y']-s2['y'])**2)
                if dist < width / 2.5:
                    alpha = (math.sin(dist / 8 - animation_state * 4 * math.pi) + 1) / 2
                    cr.set_source_rgba(1, 1, 1, alpha * 0.3)
                    cr.move_to(s1['x'], s1['y'])
                    cr.line_to(s2['x'], s2['y'])
                    cr.stroke()

    def draw_guitar_string(self, widget, cr, animation_state=0.0, cpu_load=0.0, **_kwargs):
        # Use the provided CSS color for the background
        r = 0x1a / 255.0
        g = 0x1a / 255.0
        b = 0x1a / 255.0
        cr.set_source_rgb(r, g, b)
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cr.rectangle(0, 0, width, height)
        cr.fill()

        t = animation_state * 2 * math.pi
        
        # Non-linear scaling for CPU load. Emphasizes lower values.
        normalized_cpu = min(cpu_load, 100.0) / 100.0
        chaoticity = normalized_cpu ** 0.3

        gradient = cairo.LinearGradient(0, 0, width, 0)
        hue1 = animation_state % 1.0
        hue2 = (animation_state + 0.5) % 1.0
        r1, g1, b1 = self._hsl_to_rgb(hue1, 1.0, 0.6)
        r2, g2, b2 = self._hsl_to_rgb(hue2, 1.0, 0.6)
        gradient.add_color_stop_rgba(0, r1, g1, b1, 0.8)
        gradient.add_color_stop_rgba(1, r2, g2, b2, 0.8)

        cr.set_source(gradient)
        cr.set_line_width(1.5)

        # Define a pool of oscillators
        oscillators = [
            {'amp': 3.5, 'freq': 3, 'phase': 1.0},
            {'amp': 1.0, 'freq': 8, 'phase': 2.2},
            {'amp': 0.7, 'freq': 6, 'phase': 0.7},
            {'amp': 1.2, 'freq': 12, 'phase': 3.1},
            {'amp': 0.5, 'freq': 15, 'phase': 1.5},
        ]

        # Determine how many oscillators to use based on chaoticity
        num_oscillators = 1 + int(chaoticity * (len(oscillators) - 1))

        cr.move_to(0, height / 2)
        for x in range(width):
            final_y = 0
            for i in range(num_oscillators):
                osc = oscillators[i]
                # Modulate amplitude and frequency with chaoticity, but with a dampened effect
                amp = osc['amp'] * (1 + chaoticity * 0.5)
                freq = osc['freq'] * (1 + chaoticity * 0.25)
                
                y = amp * math.sin(x * math.pi / width * (freq + math.sin(t/2)) + t * osc['phase'])
                final_y += y

            modulator = (math.sin(t / 2) + 1) / 2
            decay = math.sin(animation_state * math.pi)
            final_y *= modulator * decay
            
            cr.line_to(x, final_y + height / 2)

        cr.stroke()
