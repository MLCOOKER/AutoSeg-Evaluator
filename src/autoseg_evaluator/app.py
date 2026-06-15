"""Application entry point — constructs QApplication and the main window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from autoseg_evaluator import __version__
from autoseg_evaluator.utils.settings import load_settings

_ASSETS = Path(__file__).resolve().parent / "assets"
# Splash is shown at this logical width (the asset is larger for HiDPI crispness).
_SPLASH_WIDTH = 560


def _asset(name: str) -> str:
    return str(_ASSETS / name)


def app_icon() -> QIcon:
    """The window / taskbar / dock icon.

    Prefers the multi-resolution ``.ico`` on Windows (crisp small sizes in the
    taskbar); the 512px ``.png`` is used on macOS / Linux, where Qt drives the
    dock / window icon directly.
    """
    name = "icon.ico" if sys.platform == "win32" else "icon.png"
    return QIcon(_asset(name))


def _set_windows_app_id() -> None:
    """Give Windows an explicit AppUserModelID so the taskbar shows *our* icon.

    Without this, Windows groups the app under the python(w).exe interpreter's
    icon instead of ours. No-op on macOS / Linux.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Rusanov.AutoSegEvaluator")
    except Exception:  # noqa: BLE001 — cosmetic only; never block startup
        pass


def main() -> int:
    _set_windows_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName("AutoSeg Evaluator")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AutoSeg Evaluator")
    # Lets Linux desktops (esp. Wayland/GNOME) tie the window to the installed
    # autoseg-evaluator.desktop entry so the taskbar shows its icon. Harmless
    # on Windows / macOS.
    app.setDesktopFileName("autoseg-evaluator")
    app.setWindowIcon(app_icon())

    # Show the splash before the heavy imports (SimpleITK, the UI tabs, the
    # ~17k-entry TG-263 dictionary) so the user sees something immediately.
    pixmap = QPixmap(_asset("splash.png"))
    if not pixmap.isNull():
        pixmap = pixmap.scaledToWidth(_SPLASH_WIDTH, Qt.TransformationMode.SmoothTransformation)
    splash = QSplashScreen(pixmap)
    splash.show()
    splash.showMessage(
        f"Loading… v{__version__}",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        Qt.GlobalColor.white,
    )
    app.processEvents()

    # Deferred so the splash is already on screen while these load.
    from autoseg_evaluator.ui.main_window import MainWindow
    from autoseg_evaluator.ui.theme import apply_theme

    settings = load_settings()
    apply_theme(app, theme=settings.get("theme", "light_blue.xml"))

    window = MainWindow(settings=settings)
    window.show()
    splash.finish(window)
    return app.exec()
