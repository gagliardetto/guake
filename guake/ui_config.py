# -*- coding: utf-8; -*-
"""
UI customization config for Guake.

Reads from ~/.config/guake/ui_config.json. All values have sensible
defaults — the file doesn't need to exist. Values are validated:
unknown keys ignored, out-of-range values fall back to defaults.
"""
import json
import logging
import os

log = logging.getLogger(__name__)

_DEFAULTS = {
    # Sidebar
    "sidebar_font_size": 9.5,
    "sidebar_header_font_size": 11,
    "sidebar_badge_font_size": 8,
    "sidebar_section_font_size": 8,
    # Tabs
    "tab_title_font_size": 9,
    "tab_command_font_size": 7.5,
    "tab_status_font_size": 7,
    "tab_max_chars": 18,
    "tab_cmd_max_chars": 16,
    "tab_min_width": 80,
}

_RANGES = {k: (4, 30) if "font" in k else (4, 200) if "chars" in k else (20, 500)
           for k in _DEFAULTS}

_config = None


def _config_path():
    return os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
        "guake", "ui_config.json"
    )


def get(key):
    """Get a UI config value, loading from disk on first call."""
    global _config
    if _config is None:
        _load()
    return _config.get(key, _DEFAULTS.get(key))


def _load():
    """Load and validate config from disk."""
    global _config
    _config = dict(_DEFAULTS)
    path = _config_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        if not isinstance(user, dict):
            log.warning("UI config not a dict, using defaults")
            return
        for key, val in user.items():
            if key not in _DEFAULTS:
                continue
            if not isinstance(val, (int, float)):
                log.warning("UI config '%s' = %r not numeric, skipping", key, val)
                continue
            lo, hi = _RANGES.get(key, (0, 9999))
            if val < lo or val > hi:
                log.warning("UI config '%s' = %s out of [%s,%s], using default %s",
                            key, val, lo, hi, _DEFAULTS[key])
                continue
            _config[key] = val
        log.info("Loaded UI config from %s", path)
    except Exception as e:
        log.warning("Bad UI config (%s), using defaults: %s", path, e)


def reload():
    """Force reload on next get() call."""
    global _config
    _config = None
