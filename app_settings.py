"""
Persist dashboard-controlled settings under data/ (gitignored).

Discord notification toggle: when off, main.py skips all Discord webhook sends
(but keeps scraping and job history).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_ROOT, "data")
_SETTINGS_PATH = os.path.join(_DATA, "app_settings.json")

_DEFAULT: Dict[str, Any] = {"discord_notifications_enabled": True}


def ensure_data_dir() -> None:
    os.makedirs(_DATA, mode=0o700, exist_ok=True)


def load_settings() -> Dict[str, Any]:
    ensure_data_dir()
    if not os.path.isfile(_SETTINGS_PATH):
        return dict(_DEFAULT)
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read app_settings.json: %s", e)
        return dict(_DEFAULT)
    out = dict(_DEFAULT)
    if isinstance(data.get("discord_notifications_enabled"), bool):
        out["discord_notifications_enabled"] = data["discord_notifications_enabled"]
    return out


def is_discord_notifications_enabled() -> bool:
    return bool(load_settings().get("discord_notifications_enabled", True))


def set_discord_notifications_enabled(enabled: bool) -> Dict[str, Any]:
    ensure_data_dir()
    cur = load_settings()
    cur["discord_notifications_enabled"] = bool(enabled)
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2, ensure_ascii=False)
    return cur
