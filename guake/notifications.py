# -*- coding: utf-8; -*-
"""
Notification center and watchers for Guake.

Watchers are rules attached to terminals that fire notifications:
  - CommandComplete: notifies when the running command finishes
  - RegexMatch: notifies when terminal output matches a pattern

The NotificationCenter is a drawer that stores and displays notifications.
"""
import logging
import re
import time
import uuid

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango, Gio

log = logging.getLogger(__name__)


# ############################################################################
# Notification model
# ############################################################################

class Notification:
    """A single notification entry."""
    __slots__ = ('id', 'title', 'body', 'source_tab', 'timestamp', 'seen', 'watcher_type')

    def __init__(self, title, body="", source_tab="", watcher_type="", terminal_uuid=""):
        self.id = str(uuid.uuid4())[:8]
        self.title = title
        self.body = body
        self.source_tab = source_tab
        self.terminal_uuid = terminal_uuid
        self.timestamp = time.time()
        self.seen = False
        self.watcher_type = watcher_type

    def age_str(self):
        d = int(time.time() - self.timestamp)
        if d < 60:
            return "now"
        elif d < 3600:
            return f"{d // 60}m ago"
        else:
            return f"{d // 3600}h ago"


# ############################################################################
# Watchers
# ############################################################################

class Watcher:
    """Base class for terminal watchers."""
    def __init__(self, terminal_uuid, tab_title=""):
        self.id = str(uuid.uuid4())[:8]
        self.terminal_uuid = terminal_uuid
        self.tab_title = tab_title
        self.active = True

    @property
    def type_label(self):
        return "watcher"

    @property
    def description(self):
        return ""


class CommandCompleteWatcher(Watcher):
    """Fires when the current/next command finishes on the terminal."""
    def __init__(self, terminal_uuid, tab_title="", once=True):
        super().__init__(terminal_uuid, tab_title)
        self.once = once  # if True, auto-remove after first fire

    @property
    def type_label(self):
        return "cmd-complete"

    @property
    def description(self):
        return f"Notify when command finishes on '{self.tab_title}'"


class RegexWatcher(Watcher):
    """Fires when terminal output matches a regex pattern."""
    def __init__(self, terminal_uuid, pattern, tab_title=""):
        super().__init__(terminal_uuid, tab_title)
        self.pattern = pattern
        try:
            self.compiled = re.compile(pattern, re.IGNORECASE)
        except re.error:
            self.compiled = None
            log.warning("Invalid regex pattern: %s", pattern)

    @property
    def type_label(self):
        return "regex"

    @property
    def description(self):
        return f"Notify when /{self.pattern}/ matches on '{self.tab_title}'"


# ############################################################################
# Watcher Manager
# ############################################################################

class WatcherManager:
    """Manages all active watchers and dispatches events."""

    def __init__(self, notification_center):
        self.notification_center = notification_center
        self.watchers = []  # list of Watcher

    def add(self, watcher):
        self.watchers.append(watcher)
        log.info("Watcher added: %s — %s", watcher.type_label, watcher.description)

    def remove(self, watcher_id):
        self.watchers = [w for w in self.watchers if w.id != watcher_id]

    def remove_for_terminal(self, terminal_uuid):
        self.watchers = [w for w in self.watchers if w.terminal_uuid != terminal_uuid]

    def get_for_terminal(self, terminal_uuid):
        return [w for w in self.watchers if w.terminal_uuid == terminal_uuid and w.active]

    def on_command_end(self, terminal_uuid, command, exit_code, tab_title=""):
        """Called when a command finishes on any terminal."""
        to_remove = []
        for w in self.watchers:
            if not w.active or w.terminal_uuid != terminal_uuid:
                continue
            if isinstance(w, CommandCompleteWatcher):
                status = "✓" if exit_code == 0 else f"✗ {exit_code}"
                self.notification_center.add_notification(Notification(
                    title=f"Command finished ({status})",
                    body=command or "(no command)",
                    source_tab=tab_title or w.tab_title,
                    watcher_type="cmd-complete",
                    terminal_uuid=terminal_uuid,
                ))
                if w.once:
                    to_remove.append(w.id)

        for wid in to_remove:
            self.remove(wid)

    def on_terminal_output(self, terminal_uuid, text, tab_title=""):
        """Called when new output appears on a terminal. Check regex watchers."""
        for w in self.watchers:
            if not w.active or w.terminal_uuid != terminal_uuid:
                continue
            if isinstance(w, RegexWatcher) and w.compiled:
                match = w.compiled.search(text)
                if match:
                    self.notification_center.add_notification(Notification(
                        title=f"Pattern matched: /{w.pattern}/",
                        body=match.group(0)[:100],
                        source_tab=tab_title or w.tab_title,
                        watcher_type="regex",
                        terminal_uuid=terminal_uuid,
                    ))


# ############################################################################
# Notification Center UI
# ############################################################################

class NotificationCenter:
    """Floating popup that shows notifications with a badge counter."""

    def __init__(self, guake_app):
        self.guake = guake_app
        self.notifications = []
        self._on_change_callbacks = []

        # Watcher manager
        self.watcher_manager = WatcherManager(self)

        # -- Bell button (goes in sidebar toolbar or tab bar) --
        self.bell_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        self.bell_button.get_style_context().add_class("notification-bell")
        self._bell_icon = Gtk.Image.new_from_icon_name(
            "dialog-information-symbolic", Gtk.IconSize.SMALL_TOOLBAR)
        self._bell_badge = Gtk.Label(label="")
        self._bell_badge.get_style_context().add_class("notification-badge")
        self._bell_badge.set_no_show_all(True)
        self._bell_badge.set_halign(Gtk.Align.END)
        self._bell_badge.set_valign(Gtk.Align.START)

        bell_overlay = Gtk.Overlay()
        bell_overlay.add(self._bell_icon)
        bell_overlay.add_overlay(self._bell_badge)
        self.bell_button.add(bell_overlay)
        self.bell_button.connect("clicked", self._toggle_popup)
        self.bell_button.set_tooltip_text("Notifications")

        # -- Popup window --
        self._popup = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._popup.set_decorated(False)
        self._popup.set_skip_taskbar_hint(True)
        self._popup.set_skip_pager_hint(True)
        self._popup.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)

        frame = Gtk.Frame()
        frame.get_style_context().add_class("notification-popup")
        self._popup.add(frame)

        popup_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        frame.add(popup_box)

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.set_margin_top(10)
        header.set_margin_bottom(6)
        header.set_margin_start(14)
        header.set_margin_end(10)

        title = Gtk.Label(label="Notifications")
        title.get_style_context().add_class("notification-title")
        title.set_xalign(0)
        title.set_hexpand(True)
        header.pack_start(title, True, True, 0)

        mark_all = Gtk.Button(label="Mark all read", relief=Gtk.ReliefStyle.NONE)
        mark_all.get_style_context().add_class("notif-action-btn")
        mark_all.connect("clicked", lambda w: self._mark_all_seen())
        header.pack_end(mark_all, False, False, 0)

        clear_btn = Gtk.Button(label="Clear", relief=Gtk.ReliefStyle.NONE)
        clear_btn.get_style_context().add_class("notif-action-btn")
        clear_btn.connect("clicked", lambda w: self._clear_all())
        header.pack_end(clear_btn, False, False, 0)

        close_btn = Gtk.Button(
            image=Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU),
            relief=Gtk.ReliefStyle.NONE)
        close_btn.get_style_context().add_class("notif-action-btn")
        close_btn.set_tooltip_text("Close")
        close_btn.connect("clicked", lambda w: self._hide_popup())
        header.pack_end(close_btn, False, False, 0)

        popup_box.pack_start(header, False, False, 0)
        popup_box.pack_start(Gtk.Separator(), False, False, 0)

        # Notification list
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_min_content_height(100)
        self._scroll.set_max_content_height(400)
        self._scroll.set_propagate_natural_height(True)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._scroll.add(self._listbox)
        popup_box.pack_start(self._scroll, True, True, 0)

        # Empty state
        self._empty_label = Gtk.Label(label="No notifications yet")
        self._empty_label.get_style_context().add_class("notification-empty")
        self._empty_label.set_margin_top(30)
        self._empty_label.set_margin_bottom(30)
        popup_box.pack_start(self._empty_label, False, False, 0)

        # Active watchers section
        self._watchers_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        popup_box.pack_end(self._watchers_box, False, False, 0)

        self._popup.set_size_request(360, -1)
        self._popup.connect("focus-out-event", lambda w, e: self._hide_popup())

        # CSS
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .notification-popup {
                background-color: rgba(24, 26, 32, 0.98);
                border: 1px solid rgba(100, 160, 255, 0.2);
                border-radius: 10px;
            }
            .notification-title {
                font-weight: bold;
                font-size: 12pt;
                color: rgba(255, 255, 255, 0.8);
            }
            .notification-bell {
                opacity: 0.5;
                padding: 1px 4px;
                border-radius: 4px;
                min-width: 0;
                min-height: 0;
            }
            .notification-bell:hover {
                opacity: 1.0;
                background-color: rgba(255, 255, 255, 0.08);
            }
            .notification-bell-active {
                opacity: 1.0;
            }
            .notification-badge {
                background-color: #E01B24;
                color: white;
                font-size: 7pt;
                font-weight: bold;
                border-radius: 8px;
                padding: 0px 4px;
                min-width: 12px;
                min-height: 12px;
            }
            .notif-action-btn {
                opacity: 0.5;
                font-size: 9pt;
                padding: 2px 8px;
                border-radius: 4px;
                min-width: 0;
                min-height: 0;
            }
            .notif-action-btn:hover {
                opacity: 1.0;
                background-color: rgba(255, 255, 255, 0.08);
            }
            .notification-empty {
                color: rgba(255, 255, 255, 0.2);
                font-size: 10pt;
            }
            .notification-row {
                padding: 8px 14px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            }
            .notification-row-seen {
                opacity: 0.45;
            }
            .notification-row-title {
                color: rgba(255, 255, 255, 0.9);
                font-size: 10pt;
                font-weight: bold;
            }
            .notification-row-body {
                color: rgba(255, 255, 255, 0.5);
                font-size: 9pt;
                font-family: Monospace;
            }
            .notification-row-meta {
                color: rgba(255, 255, 255, 0.25);
                font-size: 8.5pt;
            }
            .notification-type-cmd-complete .notification-row-title {
                color: #6EC1E4;
            }
            .notification-type-regex .notification-row-title {
                color: #F6D32D;
            }
            .watcher-section {
                border-top: 1px solid rgba(255, 255, 255, 0.06);
                padding: 8px 14px;
            }
            .watcher-label {
                font-size: 8.5pt;
                font-weight: bold;
                color: rgba(255, 255, 255, 0.3);
                letter-spacing: 1px;
            }
            .watcher-item {
                font-size: 9pt;
                color: rgba(255, 255, 255, 0.5);
                padding: 2px 0;
            }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def add_notification(self, notification):
        """Add a notification and update the UI."""
        self.notifications.insert(0, notification)
        if len(self.notifications) > 100:
            self.notifications = self.notifications[:100]
        self._update_badge()
        if self._popup.get_visible():
            self._rebuild_list()
        self._send_desktop_notification(notification)

    def _send_desktop_notification(self, n):
        """Send a system desktop notification."""
        try:
            from guake import notifier
            from guake.common import pixmapfile
            notifier.showMessage(
                n.title,
                f"{n.body}\n— {n.source_tab}",
                pixmapfile("guake-notification.png"),
            )
        except Exception as e:
            log.debug("Desktop notification failed: %s", e)

    def _update_badge(self):
        """Update the bell badge with unseen count."""
        unseen = sum(1 for n in self.notifications if not n.seen)
        if unseen > 0:
            self._bell_badge.set_text(str(unseen))
            self._bell_badge.show()
            self.bell_button.get_style_context().add_class("notification-bell-active")
        else:
            self._bell_badge.hide()
            self.bell_button.get_style_context().remove_class("notification-bell-active")

    def _toggle_popup(self, widget=None):
        """Show/hide the notification popup anchored to the bell button."""
        if self._popup.get_visible():
            self._hide_popup()
        else:
            self._show_popup()

    def _show_popup(self):
        """Position and show the popup near the bell button."""
        self._rebuild_list()
        self._rebuild_watchers()

        # Position near the bell button
        parent = self.guake.window
        _, px, py = parent.get_window().get_origin()
        parent_alloc = parent.get_allocation()

        # Anchor top-right area of the window
        x = px + parent_alloc.width - 380
        y = py + 40

        self._popup.move(x, y)
        self._popup.show_all()
        self._update_badge()

    def _hide_popup(self):
        self._popup.hide()

    def _mark_all_seen(self):
        for n in self.notifications:
            n.seen = True
        self._update_badge()
        self._rebuild_list()

    def _clear_all(self):
        self.notifications.clear()
        self._update_badge()
        self._rebuild_list()

    def _rebuild_list(self):
        """Rebuild the notification listbox."""
        for child in self._listbox.get_children():
            self._listbox.remove(child)

        if not self.notifications:
            self._empty_label.show()
            self._scroll.hide()
        else:
            self._empty_label.hide()
            self._scroll.show()

        for n in self.notifications[:50]:
            row = self._create_notification_row(n)
            self._listbox.add(row)
        self._listbox.show_all()

    def _create_notification_row(self, n):
        """Create a row widget for a notification."""
        row = Gtk.ListBoxRow()
        row.set_selectable(True)

        # Wrap in EventBox for click handling
        event_box = Gtk.EventBox()
        event_box.connect("button-press-event",
                          lambda w, e, tuuid=n.terminal_uuid, nid=n.id: self._on_notification_click(tuuid, nid))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.get_style_context().add_class("notification-row")
        box.get_style_context().add_class(f"notification-type-{n.watcher_type}")
        if n.seen:
            box.get_style_context().add_class("notification-row-seen")

        # Title
        title = Gtk.Label(label=n.title, xalign=0)
        title.get_style_context().add_class("notification-row-title")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(title, False, False, 0)

        # Body
        if n.body:
            body = Gtk.Label(label=n.body, xalign=0)
            body.get_style_context().add_class("notification-row-body")
            body.set_ellipsize(Pango.EllipsizeMode.END)
            body.set_max_width_chars(35)
            box.pack_start(body, False, False, 0)

        # Meta line: source tab + age
        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        source = Gtk.Label(label=n.source_tab, xalign=0)
        source.get_style_context().add_class("notification-row-meta")
        meta_box.pack_start(source, True, True, 0)

        age = Gtk.Label(label=n.age_str(), xalign=1)
        age.get_style_context().add_class("notification-row-meta")
        meta_box.pack_end(age, False, False, 0)

        if not n.seen:
            mark_btn = Gtk.Button(label="✓", relief=Gtk.ReliefStyle.NONE)
            mark_btn.get_style_context().add_class("toolbar-btn")
            mark_btn.set_tooltip_text("Mark as seen")
            mark_btn.connect("clicked", lambda w, nid=n.id: self._mark_seen(nid))
            meta_box.pack_end(mark_btn, False, False, 0)

        box.pack_start(meta_box, False, False, 0)
        event_box.add(box)
        row.add(event_box)
        return row

    def _on_notification_click(self, terminal_uuid, notification_id):
        """Navigate to the terminal that triggered this notification."""
        # Mark as seen
        self._mark_seen(notification_id)
        # Navigate and close popup
        if terminal_uuid:
            self._navigate_to_terminal(terminal_uuid)
        self._hide_popup()

    def _navigate_to_terminal(self, terminal_uuid):
        """Switch to the workspace and tab containing this terminal."""
        if not self.guake or not self.guake.workspace_manager:
            return

        # Find which workspace contains this terminal
        for ws in self.guake.workspace_manager.get_all_workspaces():
            if terminal_uuid in ws.get("terminals", []):
                # Switch to workspace
                self.guake.switch_to_workspace(ws["id"])
                # Find and focus the tab
                nb = self.guake.get_notebook()
                if nb:
                    for i in range(nb.get_n_pages()):
                        page = nb.get_nth_page(i)
                        for t in page.iter_terminals():
                            if str(t.uuid) == terminal_uuid:
                                nb.set_current_page(i)
                                t.grab_focus()
                                return
                return

    def _mark_seen(self, notification_id):
        for n in self.notifications:
            if n.id == notification_id:
                n.seen = True
                break
        self._update_badge()
        self._rebuild_list()

    def _rebuild_watchers(self):
        """Show active watchers at the bottom of the drawer."""
        for child in self._watchers_box.get_children():
            self._watchers_box.remove(child)

        watchers = self.watcher_manager.watchers
        if not watchers:
            return

        sep = Gtk.Separator()
        self._watchers_box.pack_start(sep, False, False, 0)

        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        section.get_style_context().add_class("watcher-section")

        header = Gtk.Label(label=f"ACTIVE WATCHERS ({len(watchers)})", xalign=0)
        header.get_style_context().add_class("watcher-label")
        section.pack_start(header, False, False, 0)

        for w in watchers:
            row_event = Gtk.EventBox()
            row_event.connect("button-press-event",
                              lambda eb, ev, tuuid=w.terminal_uuid: self._on_watcher_click(tuuid))

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            icon = "🔔" if isinstance(w, CommandCompleteWatcher) else "🔍"
            label = Gtk.Label(label=f"{icon} {w.description}", xalign=0)
            label.get_style_context().add_class("watcher-item")
            label.set_ellipsize(Pango.EllipsizeMode.END)
            row.pack_start(label, True, True, 0)

            remove_btn = Gtk.Button(label="×", relief=Gtk.ReliefStyle.NONE)
            remove_btn.get_style_context().add_class("toolbar-btn")
            remove_btn.connect("clicked", lambda w, wid=w.id: self._remove_watcher(wid))
            row.pack_end(remove_btn, False, False, 0)

            row_event.add(row)
            section.pack_start(row_event, False, False, 0)

        self._watchers_box.pack_start(section, False, False, 0)
        self._watchers_box.show_all()

    def _remove_watcher(self, watcher_id):
        self.watcher_manager.remove(watcher_id)
        self._rebuild_watchers()

    def _on_watcher_click(self, terminal_uuid):
        """Navigate to the watched terminal."""
        if terminal_uuid:
            self._navigate_to_terminal(terminal_uuid)
        self._hide_popup()
