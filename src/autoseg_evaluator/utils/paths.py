"""Portable path resolution for both development and PyInstaller-bundled runs.

The application is designed to be fully portable — settings, user-defined synonyms,
and session files live next to the executable, not in AppData or the registry.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return True if running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """Directory holding the executable (frozen) or the project root (dev).

    User-writable files (settings.json, sessions, custom synonyms) live here.
    """
    if is_frozen():
        return Path(sys.executable).parent
    # In development, walk up from this file: utils/paths.py → utils → autoseg_evaluator → src → repo root
    return Path(__file__).resolve().parents[3]


def resources_dir() -> Path:
    """Directory holding bundled read-only resources (default icons, synonyms)."""
    if is_frozen():
        # PyInstaller extracts data files to sys._MEIPASS at runtime
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "autoseg_evaluator" / "resources"
    return Path(__file__).resolve().parent.parent / "resources"


def settings_path() -> Path:
    """settings.json — auto-persisted next to the executable."""
    return app_dir() / "settings.json"


def synonyms_path() -> Path:
    """User-editable synonyms.json next to .exe; falls back to bundled defaults."""
    user_file = app_dir() / "synonyms.json"
    if user_file.exists():
        return user_file
    return resources_dir() / "synonyms.json"
