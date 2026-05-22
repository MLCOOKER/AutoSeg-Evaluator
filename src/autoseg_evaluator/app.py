"""Application entry point — constructs QApplication and the main window."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from autoseg_evaluator import __version__
from autoseg_evaluator.ui.main_window import MainWindow
from autoseg_evaluator.ui.theme import apply_theme
from autoseg_evaluator.utils.settings import load_settings


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AutoSeg Evaluator")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AutoSeg Evaluator")

    settings = load_settings()
    apply_theme(app, theme=settings.get("theme", "light_blue.xml"))

    window = MainWindow(settings=settings)
    window.show()
    return app.exec()
