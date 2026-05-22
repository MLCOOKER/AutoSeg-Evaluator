"""Theme application via qt-material with a graceful fallback.

Dark mode uses a high-contrast VS Code-inspired palette (deep grey background,
near-white body text, bright blue accent) rather than the qt-material default,
which renders dark-on-dark text in several widgets and is hard to read.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication


# VS Code-flavoured high-contrast dark palette. These are passed to qt-material
# as Jinja template variables; the stylesheet substitutes them throughout.
#
#   secondaryColor         — main window background
#   secondaryLightColor    — slightly lighter (toolbars, sidebars)
#   secondaryDarkColor     — slightly darker (group-box recesses)
#   primaryColor           — accent (selected tabs, links, focus highlights)
#   primaryLightColor      — hover accent
#   primaryTextColor       — text drawn on primary backgrounds (white)
#   secondaryTextColor     — body text on secondary backgrounds (light grey)
_DARK_EXTRA: dict[str, str] = {
    "density_scale": "0",
    "primaryColor": "#1f9bff",        # bright VS-Code-blue accent
    "primaryLightColor": "#5ec1ff",
    "secondaryColor": "#1e1e1e",      # editor-background grey
    "secondaryLightColor": "#2d2d30", # sidebar grey
    "secondaryDarkColor": "#141414",
    "primaryTextColor": "#ffffff",
    "secondaryTextColor": "#d4d4d4",  # near-white body text
}


def apply_theme(app: QApplication, theme: str = "light_blue.xml") -> None:
    """Apply a qt-material stylesheet. Falls back to Qt defaults if unavailable."""
    try:
        from qt_material import apply_stylesheet
    except ImportError:
        return
    try:
        kwargs: dict = {}
        if "dark" in theme.lower():
            # Override qt-material's defaults with the VS Code-inspired palette
            # so text-on-dark stays readable. ``invert_secondary=False`` is the
            # explicit form — qt-material flips text colour when this is True,
            # which we DON'T want now that we provide explicit text values.
            kwargs["extra"] = _DARK_EXTRA
            kwargs["invert_secondary"] = False
        apply_stylesheet(app, theme=theme, **kwargs)
    except Exception:
        # An invalid theme name shouldn't crash the app — fall back to the default style.
        pass
