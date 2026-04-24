# -*- coding: utf-8; -*-
"""
Copyright (C) 2007-2013 Guake authors

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
import code
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import uuid

from enum import IntEnum
from pathlib import Path
from typing import Optional
from typing import Tuple
from urllib.parse import unquote
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")  # vte-0.38

from gi.repository import GLib
from gi.repository import Gdk
from gi.repository import Gtk
from gi.repository import Pango
from gi.repository import Vte

from guake.common import clamp
from guake.globals import QUICK_OPEN_MATCHERS
from guake.globals import TERMINAL_MATCH_EXPRS
from guake.globals import TERMINAL_MATCH_TAGS

log = logging.getLogger(__name__)

# ############################################################################
# Paste security scanner — detects homoglyphs, invisible chars, bidi attacks
# Based on tirith's terminal deception checks
# ############################################################################

import unicodedata

# Known homoglyph mappings: visually similar to Latin but from other scripts
_HOMOGLYPHS = {
    # Cyrillic → Latin
    '\u0430': ('a', 'Cyrillic'), '\u0435': ('e', 'Cyrillic'),
    '\u043e': ('o', 'Cyrillic'), '\u0440': ('p', 'Cyrillic'),
    '\u0441': ('c', 'Cyrillic'), '\u0443': ('y', 'Cyrillic'),
    '\u0445': ('x', 'Cyrillic'), '\u0456': ('i', 'Cyrillic'),
    '\u0458': ('j', 'Cyrillic'), '\u04bb': ('h', 'Cyrillic'),
    '\u0455': ('s', 'Cyrillic'), '\u0452': ('d', 'Cyrillic'),
    '\u0410': ('A', 'Cyrillic'), '\u0412': ('B', 'Cyrillic'),
    '\u0415': ('E', 'Cyrillic'), '\u041a': ('K', 'Cyrillic'),
    '\u041c': ('M', 'Cyrillic'), '\u041d': ('H', 'Cyrillic'),
    '\u041e': ('O', 'Cyrillic'), '\u0420': ('P', 'Cyrillic'),
    '\u0421': ('C', 'Cyrillic'), '\u0422': ('T', 'Cyrillic'),
    '\u0425': ('X', 'Cyrillic'),
    # Greek → Latin
    '\u03b1': ('a', 'Greek'), '\u03b5': ('e', 'Greek'),
    '\u03b9': ('i', 'Greek'), '\u03bf': ('o', 'Greek'),
    '\u0391': ('A', 'Greek'), '\u0392': ('B', 'Greek'),
    '\u0395': ('E', 'Greek'), '\u0397': ('H', 'Greek'),
    '\u0399': ('I', 'Greek'), '\u039a': ('K', 'Greek'),
    '\u039c': ('M', 'Greek'), '\u039d': ('N', 'Greek'),
    '\u039f': ('O', 'Greek'), '\u03a1': ('P', 'Greek'),
    '\u03a4': ('T', 'Greek'), '\u03a7': ('X', 'Greek'),
    '\u0396': ('Z', 'Greek'),
    # Armenian
    '\u0578': ('n', 'Armenian'), '\u0561': ('u', 'Armenian'),
}

# Dangerous invisible/control characters
_ZERO_WIDTH = {
    '\u200b': 'zero-width space',
    '\u200c': 'zero-width non-joiner',
    '\u200d': 'zero-width joiner',
    '\ufeff': 'byte order mark / zero-width no-break space',
}

_BIDI_CONTROLS = {
    '\u202a': 'left-to-right embedding',
    '\u202b': 'right-to-left embedding',
    '\u202c': 'pop directional formatting',
    '\u202d': 'left-to-right override',
    '\u202e': 'right-to-left override',
    '\u2066': 'left-to-right isolate',
    '\u2067': 'right-to-left isolate',
    '\u2068': 'first strong isolate',
    '\u2069': 'pop directional isolate',
}

_INVISIBLE_MATH = {
    '\u2061': 'function application',
    '\u2062': 'invisible times',
    '\u2063': 'invisible separator',
    '\u2064': 'invisible plus',
}

_INVISIBLE_WHITESPACE = {
    '\u2000': 'en quad',
    '\u2001': 'em quad',
    '\u2002': 'en space',
    '\u2003': 'em space',
    '\u2004': 'three-per-em space',
    '\u2005': 'four-per-em space',
    '\u2006': 'six-per-em space',
    '\u2007': 'figure space',
    '\u2008': 'punctuation space',
    '\u2009': 'thin space',
    '\u200a': 'hair space',
    '\u205f': 'medium mathematical space',
    '\u00a0': 'non-breaking space',
}

_HANGUL_FILLERS = {
    '\u3164': 'hangul filler',
    '\u115f': 'hangul choseong filler',
    '\u1160': 'hangul jungseong filler',
}

# Hidden line breaks — look like spaces but act as newlines
_LINE_SEPARATORS = {
    '\u2028': 'line separator',
    '\u2029': 'paragraph separator',
}

# Deprecated Unicode formatting characters
_DEPRECATED_FORMAT = {
    '\u206a': 'inhibit symmetric swapping',
    '\u206b': 'activate symmetric swapping',
    '\u206c': 'inhibit Arabic form shaping',
    '\u206d': 'activate Arabic form shaping',
    '\u206e': 'national digit shapes',
    '\u206f': 'nominal digit shapes',
}

# Interlinear annotation characters — can hide text between visible chars
_INTERLINEAR = {
    '\ufff9': 'interlinear annotation anchor',
    '\ufffa': 'interlinear annotation separator',
    '\ufffb': 'interlinear annotation terminator',
}

# Other invisible/deceptive characters
_OTHER_INVISIBLE = {
    '\u00ad': ('soft_hyphen', 'medium', 'Soft hyphen (invisible, changes word boundaries)'),
    '\u2060': ('word_joiner', 'medium', 'Word joiner (invisible no-break)'),
    '\u034f': ('combining_grapheme', 'medium', 'Combining grapheme joiner'),
    '\ufffc': ('object_replacement', 'medium', 'Object replacement character'),
}


class PasteThreat:
    """A single suspicious element found in pasted text."""
    __slots__ = ('category', 'severity', 'char', 'codepoint', 'description', 'position')

    def __init__(self, category, severity, char, description, position):
        self.category = category      # 'homoglyph', 'bidi', 'zero_width', 'control', etc.
        self.severity = severity      # 'critical', 'high', 'medium'
        self.char = char
        self.codepoint = f"U+{ord(char):04X}"
        self.description = description
        self.position = position


def scan_paste(text):
    """Scan pasted text for security threats. Returns list of PasteThreat.

    Checks (based on tirith's terminal deception rules):
    1. Homoglyphs — Cyrillic/Greek/Armenian chars that look like Latin
    2. Bidi controls — can reorder displayed text (Trojan Source)
    3. Zero-width chars — invisible chars that can hide in commands
    4. ANSI escapes — can manipulate terminal display
    5. Control chars — CR/BS can overwrite visible text
    6. Unicode tags — U+E0000-U+E007F encode hidden ASCII
    7. Invisible math operators — U+2061-U+2064
    8. Invisible whitespace — en/em space, figure space, etc.
    9. Hangul fillers — invisible Korean placeholders
    10. Math alphanumeric symbols — U+1D400-U+1D7FF steganography
    11. Hidden multiline — suspicious commands on hidden lines
    """
    if not text:
        return []

    threats = []

    # Check ASCII-only attacks first (these work even with pure ASCII text)
    # ANSI escapes
    for i, char in enumerate(text):
        if ord(char) == 0x1b:
            threats.append(PasteThreat(
                'ansi_escape', 'high', char,
                "ANSI escape sequence start",
                i))
        elif ord(char) < 0x20 and ord(char) not in (0x09, 0x0a, 0x0d):
            threats.append(PasteThreat(
                'control_char', 'high', char,
                f"Control character 0x{ord(char):02X}",
                i))
        elif char == '\r' and i + 1 < len(text) and text[i + 1] != '\n':
            threats.append(PasteThreat(
                'control_char', 'high', char,
                "Bare carriage return (can overwrite displayed text)",
                i))

    # Hidden multiline (works on ASCII too)
    lines = text.split('\n')
    if len(lines) > 1:
        suspicious_prefixes = [
            'curl ', 'wget ', 'bash', '/bin/', 'sudo ', 'rm ',
            'chmod ', 'eval ', 'exec ', '> /', '>> /', '| sh',
            'python', 'node ', 'perl ', 'ruby ',
        ]
        for line_num, line in enumerate(lines[1:], start=2):
            trimmed = line.strip()
            if trimmed and any(trimmed.startswith(p) or p in trimmed for p in suspicious_prefixes):
                threats.append(PasteThreat(
                    'hidden_multiline', 'high', '\n',
                    f"Hidden command on line {line_num}: {trimmed[:60]}",
                    text.index('\n')))
                break

    # If pure ASCII and no control/multiline issues, skip Unicode checks
    if text.isascii():
        return threats

    for i, char in enumerate(text):
        cp = ord(char)

        # 1. Known homoglyphs (mixed into ASCII context)
        if char in _HOMOGLYPHS:
            looks_like, script = _HOMOGLYPHS[char]
            if _is_near_ascii(text, i):
                threats.append(PasteThreat(
                    'homoglyph', 'high', char,
                    f"{script} '{char}' looks like Latin '{looks_like}'",
                    i))
            continue

        # 2. Bidi controls (always critical)
        if char in _BIDI_CONTROLS:
            threats.append(PasteThreat(
                'bidi', 'critical', char,
                f"Bidi control: {_BIDI_CONTROLS[char]}",
                i))
            continue

        # 3. Zero-width characters
        if char in _ZERO_WIDTH:
            if char in ('\u200c', '\u200d') and _is_joining_context(text, i):
                continue
            threats.append(PasteThreat(
                'zero_width', 'high', char,
                f"Invisible: {_ZERO_WIDTH[char]}",
                i))
            continue

        # 4. Unicode tags (hidden ASCII)
        if 0xE0001 <= cp <= 0xE007F:
            hidden_ascii = chr(cp - 0xE0000) if 0x20 <= (cp - 0xE0000) <= 0x7E else '?'
            threats.append(PasteThreat(
                'unicode_tag', 'critical', char,
                f"Unicode tag encoding hidden '{hidden_ascii}'",
                i))
            continue

        # 5. Invisible math operators
        if char in _INVISIBLE_MATH:
            threats.append(PasteThreat(
                'invisible_math', 'medium', char,
                f"Invisible math: {_INVISIBLE_MATH[char]}",
                i))
            continue

        # 6. Invisible whitespace
        if char in _INVISIBLE_WHITESPACE:
            threats.append(PasteThreat(
                'invisible_ws', 'medium', char,
                f"Invisible whitespace: {_INVISIBLE_WHITESPACE[char]}",
                i))
            continue

        # 7. Hangul fillers
        if char in _HANGUL_FILLERS:
            threats.append(PasteThreat(
                'hangul_filler', 'medium', char,
                f"Invisible: {_HANGUL_FILLERS[char]}",
                i))
            continue

        # 8. Mathematical alphanumeric symbols (steganography)
        if 0x1D400 <= cp <= 0x1D7FF:
            if _is_near_ascii(text, i):
                name = unicodedata.name(char, f'U+{cp:04X}')
                threats.append(PasteThreat(
                    'math_symbol', 'high', char,
                    f"Math alphanumeric: {name}",
                    i))
            continue

        # 9. Variation selectors (steganographic encoding)
        if (0xFE00 <= cp <= 0xFE0F) or (0xE0100 <= cp <= 0xE01EF):
            threats.append(PasteThreat(
                'variation_selector', 'medium', char,
                f"Variation selector",
                i))
            continue

        # 10. Hidden line separators (look like spaces, act as newlines)
        if char in _LINE_SEPARATORS:
            threats.append(PasteThreat(
                'line_separator', 'high', char,
                f"Hidden line break: {_LINE_SEPARATORS[char]}",
                i))
            continue

        # 11. Deprecated format characters
        if char in _DEPRECATED_FORMAT:
            threats.append(PasteThreat(
                'deprecated_format', 'medium', char,
                f"Deprecated format: {_DEPRECATED_FORMAT[char]}",
                i))
            continue

        # 12. Interlinear annotations (can hide text)
        if char in _INTERLINEAR:
            threats.append(PasteThreat(
                'interlinear', 'high', char,
                f"Annotation: {_INTERLINEAR[char]}",
                i))
            continue

        # 13. Other invisible/deceptive characters
        if char in _OTHER_INVISIBLE:
            cat, sev, desc = _OTHER_INVISIBLE[char]
            threats.append(PasteThreat(cat, sev, char, desc, i))
            continue

        # 14. Excessive combining characters (zalgo text — obscures underlying chars)
        if unicodedata.category(char).startswith('M'):  # Mark category
            # Count consecutive combining chars
            count = 0
            j = i
            while j < len(text) and unicodedata.category(text[j]).startswith('M'):
                count += 1
                j += 1
            if count >= 3:  # 3+ combining marks on one base = suspicious
                threats.append(PasteThreat(
                    'zalgo', 'medium', char,
                    f"Excessive combining marks ({count} stacked)",
                    i))
            continue

        # 15. Private Use Area (no standard meaning — can hide anything)
        if (0xE000 <= cp <= 0xF8FF) or (0xF0000 <= cp <= 0xFFFFD) or (0x100000 <= cp <= 0x10FFFD):
            threats.append(PasteThreat(
                'private_use', 'medium', char,
                f"Private Use Area character",
                i))
            continue

    return threats


def _is_near_ascii(text, pos):
    """Check if position is within the same word as ASCII letters."""
    start = pos
    while start > 0 and not text[start - 1] in ' \t\n:;,()[]{}"\'=>|&<':
        start -= 1
    end = pos
    while end < len(text) and not text[end] in ' \t\n:;,()[]{}"\'=>|&<':
        end += 1
    return any(c.isascii() and c.isalpha() for c in text[start:end])


def _is_joining_context(text, pos):
    """Check if ZWJ/ZWNJ is between joining-script characters (legit use)."""
    joining_scripts = {'ARABIC', 'SYRIAC', 'DEVANAGARI', 'BENGALI', 'TAMIL',
                       'TELUGU', 'KANNADA', 'MALAYALAM', 'THAI', 'TIBETAN', 'MYANMAR'}
    def _script_of(c):
        name = unicodedata.name(c, '')
        for s in joining_scripts:
            if s in name:
                return s
        return None

    before = _script_of(text[pos - 1]) if pos > 0 else None
    after = _script_of(text[pos + 1]) if pos + 1 < len(text) else None
    return before is not None and before == after


# Keep backward compat — old name used by paste_clipboard
def detect_homoglyphs(text):
    """Legacy wrapper. Returns list of (char, name, script, position) tuples."""
    threats = scan_paste(text)
    return [(t.char, t.description, t.category, t.position) for t in threats]

libutempter = None
try:
    # this allow to run some commands that requires libuterm to
    # be injected in current process, as: wall
    from atexit import register as at_exit_call
    from ctypes import cdll

    libutempter = cdll.LoadLibrary("libutempter.so.0")
    if libutempter is not None:
        # We absolutely need to remove the old tty from the utmp !!!
        at_exit_call(libutempter.utempter_remove_added_record)
except Exception as e:
    libutempter = None
    sys.stderr.write("[WARN] ===================================================================\n")
    sys.stderr.write("[WARN] Unable to load the library libutempter !\n")
    sys.stderr.write(
        "[WARN] Some feature might not work:\n"
        "[WARN]  - 'exit' command might freeze the terminal instead of closing the tab\n"
        "[WARN]  - the 'wall' command is known to work badly\n"
    )
    sys.stderr.write("[WARN] Error: " + str(e) + "\n")
    sys.stderr.write(
        "[WARN] ===================================================================²\n"
    )


def halt(loc):
    code.interact(local=loc)


__all__ = ["GuakeTerminal"]

# pylint: enable=anomalous-backslash-in-string


class DropTargets(IntEnum):
    URIS = 0
    TEXT = 1


class GuakeTerminal(Vte.Terminal):

    """Just a vte.Terminal with some properties already set."""

    def __init__(self, guake, terminal_uuid=None):
        super().__init__()
        self.guake = guake
        self.configure_terminal()
        self.add_matches()
        self.handler_ids = []
        self.handler_ids.append(self.connect("button-press-event", self.button_press))
        self.connect("child-exited", self.on_child_exited)  # Call on_child_exited, don't remove it
        self.connect("selection-changed", self.copy_on_select)
        self.matched_value = ""
        self.font_scale_index = 0
        self._pid = None
        self.found_link = None
        if terminal_uuid:
            self.uuid = uuid.UUID(terminal_uuid) if isinstance(terminal_uuid, str) else terminal_uuid
        else:
            self.uuid = uuid.uuid4()

        # Custom colors
        self.custom_bgcolor = None
        self.custom_fgcolor = None
        self.custom_palette = None

        self.setup_drag_and_drop()

        self.ENVV_EXCLUDE_LIST = ["GDK_BACKEND"]
        self.envv = [f"{i}={os.environ[i]}" for i in os.environ if i not in self.ENVV_EXCLUDE_LIST]
        self.envv.append(f"GUAKE_TAB_UUID={self.uuid}")

        # Block model FIFO for shell integration
        self.block_fifo_path = None
        try:
            from guake.blocks import create_block_fifo
            self.block_fifo_path = create_block_fifo(str(self.uuid))
            if self.block_fifo_path:
                self.envv.append(f"GUAKE_BLOCK_FIFO={self.block_fifo_path}")
        except Exception as e:
            log.warning("Could not create block FIFO: %s", e)

        self.is_running_process = False
        self.last_exit_status = 0

    def setup_drag_and_drop(self):
        self.targets = Gtk.TargetList()
        self.targets.add_uri_targets(DropTargets.URIS)
        self.targets.add_text_targets(DropTargets.TEXT)
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.drag_dest_set_target_list(self.targets)
        self.connect("drag-data-received", self.on_drag_data_received)

    def get_uuid(self):
        return self.uuid

    @property
    def pid(self):
        return self._pid

    @pid.setter
    def pid(self, pid):
        self._pid = pid

    def feed_child(self, resolved_cmdline):
        if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) >= (0, 42):
            encoded = resolved_cmdline.encode("utf-8")
            try:
                super().feed_child_binary(encoded)
            except TypeError:
                # The doc doest not say clearly at which version the feed_child* function has lost
                # the "len" parameter :(
                super().feed_child(resolved_cmdline, len(resolved_cmdline))
        else:
            super().feed_child(resolved_cmdline, len(resolved_cmdline))

    def execute_command(self, command):
        if command[-1] != "\n":
            command += "\n"
        self.feed_child(command)

    def clear_input(self):
        """
        Clears the current input line by sending Ctrl-U (kill line) to the shell.
        Falls back to Ctrl-C + Ctrl-U if a more aggressive clear is needed.
        """
        # Send Ctrl-U (kill line) to clear the current input
        self.feed_child("\x15")


    def copy_clipboard(self):
        if self.get_has_selection():
            super().copy_clipboard()
        elif self.matched_value:
            guake_clipboard = Gtk.Clipboard.get_default(self.guake.window.get_display())
            guake_clipboard.set_text(self.matched_value, len(self.matched_value))

    def paste_clipboard(self):
        """Override paste to check for security threats before pasting."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text()
        if text:
            threats = scan_paste(text)
            if threats:
                if not self._show_paste_warning(text, threats):
                    return
        super().paste_clipboard()

    def paste_primary(self):
        """Override primary (middle-click) paste with security check."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        text = clipboard.wait_for_text()
        if text:
            threats = scan_paste(text)
            if threats:
                if not self._show_paste_warning(text, threats):
                    return
        super().paste_primary()

    def _show_paste_warning(self, text, threats):
        """Show a warning dialog about security threats in pasted text.
        Returns True if user chooses to paste anyway, False to cancel."""

        # Group by severity
        critical = [t for t in threats if t.severity == 'critical']
        high = [t for t in threats if t.severity == 'high']

        if critical:
            severity_text = "CRITICAL"
            msg_type = Gtk.MessageType.ERROR
        elif high:
            severity_text = "HIGH"
            msg_type = Gtk.MessageType.WARNING
        else:
            severity_text = "MEDIUM"
            msg_type = Gtk.MessageType.WARNING

        dialog = Gtk.MessageDialog(
            transient_for=self.guake.window,
            modal=True,
            message_type=msg_type,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Paste security warning ({severity_text})",
        )

        # Category labels
        cat_names = {
            'homoglyph': 'Homoglyph (lookalike character)',
            'bidi': 'Bidi control (text direction attack)',
            'zero_width': 'Zero-width invisible character',
            'ansi_escape': 'ANSI escape sequence',
            'control_char': 'Control character',
            'unicode_tag': 'Unicode tag (hidden ASCII)',
            'invisible_math': 'Invisible math operator',
            'invisible_ws': 'Invisible whitespace',
            'hangul_filler': 'Hangul filler',
            'math_symbol': 'Math alphanumeric symbol',
            'variation_selector': 'Variation selector',
            'hidden_multiline': 'Hidden multiline command',
            'line_separator': 'Hidden line separator',
            'deprecated_format': 'Deprecated format character',
            'interlinear': 'Interlinear annotation (hidden text)',
            'soft_hyphen': 'Soft hyphen (invisible)',
            'word_joiner': 'Word joiner (invisible)',
            'combining_grapheme': 'Combining grapheme joiner',
            'object_replacement': 'Object replacement character',
            'zalgo': 'Excessive combining marks (zalgo)',
            'private_use': 'Private Use Area character',
        }

        # Build secondary text with proper Pango markup
        markup_parts = []
        seen_cats = set()
        for t in threats[:15]:
            cat_label = cat_names.get(t.category, t.category)
            if t.category not in seen_cats:
                markup_parts.append(
                    f"\n<b>{GLib.markup_escape_text(cat_label)}</b>")
                seen_cats.add(t.category)
            desc_escaped = GLib.markup_escape_text(t.description)
            markup_parts.append(
                f"  <tt>{GLib.markup_escape_text(t.codepoint)}</tt>: {desc_escaped}")

        if len(threats) > 15:
            markup_parts.append(f"\n  <i>...and {len(threats) - 15} more</i>")

        preview = text[:200] + ("..." if len(text) > 200 else "")
        preview_escaped = GLib.markup_escape_text(preview)

        secondary = (
            "\n".join(markup_parts)
            + f"\n\n<b>Text preview:</b>\n<tt>{preview_escaped}</tt>"
        )

        dialog.format_secondary_markup(secondary)

        dialog.add_button("Cancel Paste", Gtk.ResponseType.CANCEL)
        dialog.add_button("Paste Anyway", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)

        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.ACCEPT

    def copy_on_select(self, event):
        if self.guake.settings.general.get_boolean("copy-on-select") and self.get_has_selection():
            self.copy_clipboard()

    def configure_terminal(self):
        """Sets all customized properties on the terminal"""
        client = self.guake.settings.general
        word_chars = client.get_string("word-chars")
        if word_chars:
            self.set_word_char_exceptions(word_chars)
        self.set_audible_bell(client.get_boolean("use-audible-bell"))
        self.set_sensitive(True)

        cursor_blink_mode = self.guake.settings.style.get_int("cursor-blink-mode")
        self.set_property("cursor-blink-mode", cursor_blink_mode)

        if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) >= (0, 50):
            self.set_allow_hyperlink(True)

        if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) >= (0, 52):
            try:
                self.set_cell_height_scale(
                    self.guake.settings.styleFont.get_double("cell-height-scale")
                )
            except:  # pylint: disable=bare-except
                log.error("set_cell_height_scale not supported by your version of VTE")
            try:
                self.set_cell_width_scale(
                    self.guake.settings.styleFont.get_double("cell-width-scale")
                )
            except:  # pylint: disable=bare-except
                log.error("set_cell_width_scale not supported by your version of VTE")

        if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) >= (0, 56):
            try:
                self.set_bold_is_bright(self.guake.settings.styleFont.get_boolean("bold-is-bright"))
            except:  # pylint: disable=bare-except
                log.error("set_bold_is_bright not supported by your version of VTE")

        # TODO PORT is this still the case with the newer vte version?
        # -- Ubuntu has a patch to libvte which disables mouse scrolling in apps
        # -- like vim and less by default. If this is the case, enable it back.
        if hasattr(self, "set_alternate_screen_scroll"):
            self.set_alternate_screen_scroll(True)

        self.set_can_default(True)
        self.set_can_focus(True)

    def add_matches(self):
        """Adds all regular expressions declared in
        guake.globals.TERMINAL_MATCH_EXPRS to the terminal to make vte
        highlight text that matches them.
        """
        try:
            # NOTE: PCRE2_UTF | PCRE2_NO_UTF_CHECK | PCRE2_MULTILINE
            # reference from vte/bindings/vala/app.vala, flags = 0x40080400u
            # also ref: https://mail.gnome.org/archives/commits-list/2016-September/msg06218.html
            VTE_REGEX_FLAGS = 0x40080400
            for expr in TERMINAL_MATCH_EXPRS:
                tag = self.match_add_regex(
                    Vte.Regex.new_for_match(expr, len(expr), VTE_REGEX_FLAGS), 0
                )
                self.match_set_cursor_name(tag, "hand")

            for _useless, match, _otheruseless in QUICK_OPEN_MATCHERS:
                tag = self.match_add_regex(
                    Vte.Regex.new_for_match(match, len(match), VTE_REGEX_FLAGS), 0
                )
                self.match_set_cursor_name(tag, "hand")
        except (
            GLib.Error,
            AttributeError,
        ):  # pylint: disable=catching-non-exception
            try:
                compile_flag = 0
                if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) >= (0, 44):
                    compile_flag = GLib.RegexCompileFlags.MULTILINE
                for expr in TERMINAL_MATCH_EXPRS:
                    tag = self.match_add_gregex(GLib.Regex.new(expr, compile_flag, 0), 0)
                    self.match_set_cursor_type(tag, Gdk.CursorType.HAND2)

                for _useless, match, _otheruseless in QUICK_OPEN_MATCHERS:
                    tag = self.match_add_gregex(GLib.Regex.new(match, compile_flag, 0), 0)
                    self.match_set_cursor_type(tag, Gdk.CursorType.HAND2)
            except GLib.Error as err:  # pylint: disable=catching-non-exception
                log.error(
                    "ERROR: PCRE2 does not seems to be enabled on your system. "
                    "Quick Edit and other Ctrl+click features are disabled. "
                    "Please update your VTE package or contact your distribution to ask "
                    "to enable regular expression support in VTE. Exception: '%s'",
                    str(err),
                )

    def get_current_directory(self):
        directory = os.path.expanduser("~")
        if self.pid is not None:
            try:
                cwd = os.readlink(f"/proc/{self.pid}/cwd")
            except Exception:
                return directory
            if os.path.exists(cwd):
                directory = cwd
        return directory

    def is_file_on_local_server(self, text) -> Tuple[Optional[Path], Optional[int], Optional[int]]:
        """Test if the provided text matches a file on local server

        Supports:
         - absolute path
         - relative path (using current working directory)
         - file:line syntax
         - file:line:colum syntax

        Args:
            text (str): candidate for file search

        Returns
            - Tuple(None, None, None) if the provided text does not match anything
            - Tuple(file path, None, None) if only a file path is found
            - Tuple(file path, linenumber, None) if line number is found
            - Tuple(file path, linenumber, columnnumber) if line and column numbers are found
        """
        lineno = None
        colno = None
        py_func = None
        # "<File>:<line>:<col>"
        m = re.compile(r"(.*)\:(\d+)\:(\d+)$").match(text)
        if m:
            text = m.group(1)
            lineno = m.group(2)
            colno = m.group(3)
        else:
            # "<File>:<line>"
            m = re.compile(r"(.*)\:(\d+)$").match(text)
            if m:
                text = m.group(1)
                lineno = m.group(2)
            else:
                # "<File>::<python_function>"
                m = re.compile(r"^(.*)\:\:([a-zA-Z0-9\_]+)$").match(text)
                if m:
                    text = m.group(1)
                    py_func = m.group(2).strip()

        def find_lineno(text, pt, lineno, py_func):
            if lineno:
                return lineno
            if not py_func:
                return
            with pt.open() as f:
                for i, line in enumerate(f.readlines()):
                    if line.startswith(f"def {py_func}"):
                        return i + 1
                        break

        pt = Path(text)
        log.debug("checking file existance: %r", pt)
        try:
            if pt.exists():
                lineno = find_lineno(text, pt, lineno, py_func)
                log.info("File exists: %r, line=%r", pt.absolute().as_posix(), lineno)
                return (pt, lineno, colno)
            log.debug("No file found matching: %r", text)
            cwd = self.get_current_directory()
            pt = Path(cwd) / pt
            log.debug("checking file existance: %r", pt)
            if pt.exists():
                lineno = find_lineno(text, pt, lineno, py_func)
                log.info("File exists: %r, line=%r", pt.absolute().as_posix(), lineno)
                return (pt, lineno, colno)
            log.debug("file does not exist: %s", str(pt))
        except OSError:
            log.debug("not a file name: %r", text)
        return (None, None, None)

    def button_press(self, terminal, event):
        """Handles the button press event in the terminal widget. If
        any match string is caught, another application is open to
        handle the matched resource uri.
        """
        self.matched_value = ""
        if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) >= (0, 46):
            matched_string = self.match_check_event(event)
        else:
            matched_string = self.match_check(
                int(event.x / self.get_char_width()),
                int(event.y / self.get_char_height()),
            )

        self.found_link = None

        if event.button == 1 and (event.get_state() & Gdk.ModifierType.CONTROL_MASK):
            if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) > (0, 50):
                s = self.hyperlink_check_event(event)
            else:
                s = None
            if s is not None:
                self._on_ctrl_click_matcher((s, None))
            elif self.get_has_selection():
                self.quick_open()
            elif matched_string and matched_string[0]:
                self._on_ctrl_click_matcher(matched_string)
        elif event.button == 3 and matched_string:
            self.found_link = self.handleTerminalMatch(matched_string)
            self.matched_value = matched_string[0]

    def on_child_exited(self, target, status, *user_data):
        self.last_exit_status = status
        if None not in (libutempter, self.get_pty()):
            libutempter.utempter_remove_record(self.get_pty().get_fd())

    def on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        if info == DropTargets.URIS:
            uris = data.get_uris()
            for uri in uris:
                path = Path(unquote(urlparse(uri).path))
                self.feed_child(shlex.quote(str(path.absolute())) + " ")
        elif info == DropTargets.TEXT:
            text = data.get_text()
            if text:
                self.feed_child(text)

    def quick_open(self):
        self.copy_clipboard()
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text()
        if not text:
            return
        (fp, lo, co) = self.is_file_on_local_server(text)
        self._execute_quick_open(fp, lo)

    def _on_ctrl_click_matcher(self, matched_string):
        value, tag = matched_string
        found_matcher = False
        log.debug("matched string: %s", matched_string)
        # First searching in additional matchers
        use_quick_open = self.guake.settings.general.get_boolean("quick-open-enable")
        if use_quick_open:
            found_matcher = self._find_quick_matcher(value)
        if not found_matcher:
            self.found_link = self.handleTerminalMatch(matched_string)
            if self.found_link:
                self.browse_link_under_cursor()

    def _find_quick_matcher(self, value):
        for _useless, _otheruseless, extractor in QUICK_OPEN_MATCHERS:
            g = re.compile(extractor).match(value)
            if g and g.groups():
                filename = g.group(1).strip()
                if len(g.groups()) >= 2:
                    line_number = g.group(2)
                else:
                    line_number = None
                log.info("Quick action executed filename=%s, line=%s", filename, line_number)
                (filepath, ln, _) = self.is_file_on_local_server(filename)
                if ln:
                    line_number = ln
                if not filepath:
                    continue
                if line_number is None:
                    line_number = "1"
                self._execute_quick_open(filepath, line_number)
                return True
        return False

    def _execute_quick_open(self, filepath, line_number):
        if not filepath:
            return
        cmdline = self.guake.settings.general.get_string("quick-open-command-line")
        if not line_number:
            line_number = ""
        else:
            line_number = str(line_number)
        logging.debug("Opening file %s at line %s", filepath, line_number)
        resolved_cmdline = cmdline % {"file_path": filepath, "line_number": line_number}
        logging.debug("Command line: %s", resolved_cmdline)
        quick_open_in_current_terminal = self.guake.settings.general.get_boolean(
            "quick-open-in-current-terminal"
        )
        if quick_open_in_current_terminal:
            logging.debug("Executing it in current tab")
            if resolved_cmdline[-1] != "\n":
                resolved_cmdline += "\n"
            self.feed_child(resolved_cmdline)
        else:
            resolved_cmdline += " &"
            logging.debug("Executing it independently")
            subprocess.call(resolved_cmdline, shell=True)

    def handleTerminalMatch(self, matched_string):
        value, tag = matched_string
        log.debug("found tag: %r, item: %r", tag, value)
        if tag in TERMINAL_MATCH_TAGS:
            if TERMINAL_MATCH_TAGS[tag] == "schema":
                # value here should not be changed, it is right and
                # ready to be used.
                pass
            elif TERMINAL_MATCH_TAGS[tag] == "http":
                value = f"http://{value}"
            elif TERMINAL_MATCH_TAGS[tag] == "https":
                value = f"https://{value}"
            elif TERMINAL_MATCH_TAGS[tag] == "ftp":
                value = f"ftp://{value}"
            elif TERMINAL_MATCH_TAGS[tag] == "email":
                value = f"mailto:{value}"

        if value:
            return value

    def get_link_under_terminal_cursor(self):
        cursor_position = self.get_cursor_position()
        matched_string = self.match_check(cursor_position.column, cursor_position.row)
        link = self.handleTerminalMatch(matched_string)
        if link:
            return link

    def get_link_under_cursor(self):
        return self.found_link

    def browse_link_under_cursor(self, url=None):
        # TODO move the call to xdg-open to guake.utils
        if not self.found_link and url is None:
            return
        url = url if url is not None else self.found_link
        log.debug("Opening link: %s", url)
        cmd = ["xdg-open", url]
        with subprocess.Popen(cmd, shell=False):
            pass

    def set_font(self, font):
        self.font = font
        self.set_font_scale_index(self.font_scale)

    def set_font_scale_index(self, scale_index):
        self.font_scale_index = clamp(scale_index, -6, 12)

        font = Pango.FontDescription(self.font.to_string())
        scale_factor = 2 ** (self.font_scale_index / 6)
        new_size = int(scale_factor * font.get_size())

        if font.get_size_is_absolute():
            font.set_absolute_size(new_size)
        else:
            font.set_size(new_size)

        super().set_font(font)

    font_scale = property(fset=set_font_scale_index, fget=lambda self: self.font_scale_index)

    def increase_font_size(self):
        self.font_scale += 1

    def decrease_font_size(self):
        self.font_scale -= 1

    def kill(self):
        pid = self.pid
        threading.Thread(target=self.delete_shell, args=(pid,)).start()

    def delete_shell(self, pid):
        """Kill the shell with SIGHUP

        NOTE: Leave it alone, DO NOT USE os.waitpid

        > sys:1: Warning: GChildWatchSource: Exit status of a child process was requested but
                 ECHILD was received by waitpid(). See the documentation of
                 g_child_watch_source_new() for possible causes.

        g_child_watch_source_new() documentation:
            https://developer.gnome.org/glib/stable/glib-The-Main-Event-Loop.html#g-child-watch-source-new

        On POSIX platforms, the following restrictions apply to this API due to limitations
        in POSIX process interfaces:
            ...
            * the application must not wait for pid to exit by any other mechanism,
              including waitpid(pid, ...) or a second child-watch source for the same pid
            ...
        For this reason, we should not call os.waitpid(pid, ...), leave it to OS
        """
        try:
            os.kill(pid, signal.SIGHUP)
        except OSError:
            pass

    _rc_integration_checked = False  # class-level: only check once per session

    @classmethod
    def _ensure_shell_integration_in_rc(cls):
        """Add shell integration source line to .zshrc or .bashrc if not present."""
        if cls._rc_integration_checked:
            return
        cls._rc_integration_checked = True

        shell = os.environ.get("SHELL", "/bin/bash")
        import guake as guake_pkg
        data_dir = os.path.join(os.path.dirname(guake_pkg.__file__), "data")

        if "zsh" in shell:
            rc_file = os.path.expanduser("~/.zshrc")
            script = os.path.join(data_dir, "shell-integration.zsh")
        else:
            rc_file = os.path.expanduser("~/.bashrc")
            script = os.path.join(data_dir, "shell-integration.bash")

        if not os.path.exists(script):
            return

        marker = "# Guake shell integration"
        source_line = f'{marker}\n[ -n "$GUAKE_BLOCK_FIFO" ] && source "{script}"\n'

        try:
            if os.path.exists(rc_file):
                content = open(rc_file, "r").read()
                if marker in content:
                    return  # already present
            with open(rc_file, "a") as f:
                f.write(f"\n{source_line}")
            log.info("Added shell integration to %s", rc_file)
        except Exception as e:
            log.debug("Could not add shell integration to %s: %s", rc_file, e)

    def spawn_sync_pid(self, directory):

        argv = []
        user_shell = self.guake.settings.general.get_string("default-shell")
        if user_shell and os.path.exists(user_shell):
            argv.append(user_shell)
        else:
            try:
                argv.append(os.environ["SHELL"])
            except KeyError:
                argv.append("/usr/bin/bash")

        login_shell = self.guake.settings.general.get_boolean("use-login-shell")
        if login_shell:
            argv.append("--login")

        log.debug('Spawn command: "%s"', " ".join(argv))

        pid = self.spawn_sync(
            Vte.PtyFlags.DEFAULT,
            directory,
            argv,
            self.envv,
            GLib.SpawnFlags(Vte.SPAWN_NO_PARENT_ENVV),
            None,
            None,
            None,
        )

        try:
            tuple_type = gi._gi.ResultTuple  # pylint: disable=c-extension-no-member
        except:  # pylint: disable=bare-except
            tuple_type = tuple
        if isinstance(pid, (tuple, tuple_type)):
            # Return a tuple in 2.91
            # https://lazka.github.io/pgi-docs/Vte-2.91/classes/Terminal.html#Vte.Terminal.spawn_sync
            pid = pid[1]
        if not isinstance(pid, int):
            raise TypeError("pid must be an int")

        if libutempter is not None:
            libutempter.utempter_add_record(self.get_pty().get_fd(), os.uname()[1])
        self.pid = pid

        # Auto-add shell integration to .zshrc/.bashrc (one-time, persists)
        if self.block_fifo_path:
            self._ensure_shell_integration_in_rc()

        return pid

    def set_color_foreground(self, font_color, *args, **kwargs):
        real_fgcolor = self.custom_fgcolor if self.custom_fgcolor else font_color
        super().set_color_foreground(real_fgcolor, *args, **kwargs)

    def set_color_background(self, bgcolor, *args, **kwargs):
        real_bgcolor = self.custom_bgcolor if self.custom_bgcolor else bgcolor
        super().set_color_background(real_bgcolor, *args, **kwargs)

    def set_color_bold(self, font_color, *args, **kwargs):
        real_fgcolor = self.custom_fgcolor if self.custom_fgcolor else font_color
        super().set_color_bold(real_fgcolor, *args, **kwargs)

    def set_colors(self, font_color, bg_color, palette_list, *args, **kwargs):
        real_bgcolor = self.custom_bgcolor if self.custom_bgcolor else bg_color
        real_fgcolor = self.custom_fgcolor if self.custom_fgcolor else font_color
        real_palette = self.custom_palette if self.custom_palette else palette_list
        super().set_colors(real_fgcolor, real_bgcolor, real_palette, *args, **kwargs)

    def set_color_foreground_custom(self, fgcolor, *args, **kwargs):
        """Sets custom foreground color for this terminal"""
        self.custom_fgcolor = fgcolor
        super().set_color_foreground(self.custom_fgcolor, *args, **kwargs)

    def set_color_background_custom(self, bgcolor, *args, **kwargs):
        """Sets custom background color for this terminal"""
        self.custom_bgcolor = bgcolor
        super().set_color_background(self.custom_bgcolor, *args, **kwargs)

    def reset_custom_colors(self):
        self.custom_fgcolor = None
        self.custom_bgcolor = None
        self.custom_palette = None

    @staticmethod
    def _color_to_list(color):
        """This method is used for serialization."""
        if color is None:
            return None
        return [color.red, color.green, color.blue, color.alpha]

    @staticmethod
    def _color_from_list(color_list):
        """This method is used for deserialization."""
        return Gdk.RGBA(
            red=color_list[0],
            green=color_list[1],
            blue=color_list[2],
            alpha=color_list[3],
        )

    def get_custom_colors_dict(self):
        """Returns dictionary of custom colors."""
        return {
            "fg_color": self._color_to_list(self.custom_fgcolor),
            "bg_color": self._color_to_list(self.custom_bgcolor),
            "palette": [self._color_to_list(col) for col in self.custom_palette]
            if self.custom_palette
            else None,
        }

    def set_custom_colors_from_dict(self, colors_dict):
        if not isinstance(colors_dict, dict):
            return

        bg_color = colors_dict.get("bg_color", None)
        if isinstance(bg_color, list):
            self.custom_bgcolor = self._color_from_list(bg_color)
        else:
            self.custom_bgcolor = None

        fg_color = colors_dict.get("fg_color", None)
        if isinstance(fg_color, list):
            self.custom_fgcolor = self._color_from_list(fg_color)
        else:
            self.custom_fgcolor = None

        palette = colors_dict.get("palette", None)
        if isinstance(palette, list):
            self.custom_palette = [self._color_from_list(col) for col in palette]
        else:
            self.custom_palette = None

    def get_input_content(self):
        """
        Returns the input line buffer of the terminal. This method uses a
        heuristic to locate the start of the command. It searches backwards
        from the cursor to find the last line containing a '' character,
        which it assumes is the prompt. It then returns all text from that
        point to the cursor, correctly capturing multi-line commands.
        """
        try:
            cursor_pos = self.get_cursor_position()
            start_row = -1
            start_col = -1

            # Search backwards from the cursor's row for the prompt marker.
            for row in range(cursor_pos.row, -1, -1):
                if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) < (0, 50):
                    line_tuple = self.get_text_range(row, 0, row, self.get_column_count(), lambda *a: True)
                else:
                    line_tuple = self.get_text_range(row, 0, row, self.get_column_count())

                if line_tuple and line_tuple[0]:
                    line_text = line_tuple[0].rstrip('\x00')
                    prompt_offset = line_text.rfind('')
                    if prompt_offset != -1:
                        start_row = row
                        start_col = prompt_offset + 1
                        break

            # If a prompt marker was found, get the text from there to the cursor.
            if start_row != -1:
                if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) < (0, 50):
                    command_text_tuple = self.get_text_range(
                        start_row, start_col,
                        cursor_pos.row, cursor_pos.column,
                        lambda *a: True
                    )
                else:
                    command_text_tuple = self.get_text_range(
                        start_row, start_col,
                        cursor_pos.row, cursor_pos.column
                    )

                if command_text_tuple and command_text_tuple[0]:
                    return command_text_tuple[0].lstrip()

            # Fallback: If no prompt marker is found, use the original simple heuristic.
            if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) < (0, 50):
                text_tuple = self.get_text_range(
                    cursor_pos.row, 0, cursor_pos.row, cursor_pos.column, lambda *a: True
                )
            else:
                text_tuple = self.get_text_range(
                    cursor_pos.row, 0, cursor_pos.row, cursor_pos.column
                )

            if text_tuple and text_tuple[0]:
                return text_tuple[0]

        except GLib.Error as e:
            log.error("Could not get terminal input content: %s", e)

        return ""
