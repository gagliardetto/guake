# -*- coding: utf-8; -*-
"""
UI customization config for Guake.

Reads from ~/.config/guake/ui_config.json. All values have sensible
defaults — the file doesn't need to exist. Users edit the JSON to
customize font sizes, and changes take effect on next Guake restart.

Example ~/.config/guake/ui_config.json:
{
    "sidebar_font_size": 9.5,
    "sidebar_header_font_size": 11,
    "sidebar_badge_font_size": 8,
    "tab_title_font_size": 9,
    "tab_command_font_size": 7.5,
    "tab_status_font_size": 7,
    "tab_max_chars": 18,
    "tab_cmd_max_chars": 16
}
"""
import json
import logging
import os

log = logging.getLogger(__name__)

_DEFAULTS = {
    # Sidebar
    "sidebar_font_size": 9.5,       # workspace name
    "sidebar_header_font_size": 11,  # "Workspaces" title
    "sidebar_badge_font_size": 8,    # tab count badge
    "sidebar_section_font_size": 8,  # "PINNED" header

    # Tabs
    "tab_title_font_size": 9,        # tab title (top line)
    "tab_command_font_size": 7.5,    # command text (bottom line)
    "tab_status_font_size": 7,       # exit code badge
    "tab_max_chars": 18,             # max chars for tab title
    "tab_cmd_max_chars": 16,         # max chars for command text
    "tab_min_width": 80,             # minimum tab width in pixels
}

_config = None


def get(key):
    """Get a UI config value, loading from disk on first call."""
    global _config
    if _config is None:
        _config = dict(_DEFAULTS)
        config_path = os.path.join(
            os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
            "guake", "ui_config.json"
        )
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    user = json.load(f)
                _config.update(user)
                log.info("Loaded UI config from %s", config_path)
            except Exception as e:
                log.debug("Could not load UI config: %s", e)
    return _config.get(key, _DEFAULTS.get(key))


def reload():
    """Force reload on next get() call."""
    global _config
    _config = None
