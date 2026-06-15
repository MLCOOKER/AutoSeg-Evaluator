"""The packaged window-icon and splash assets exist and load."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from autoseg_evaluator.app import _ASSETS, app_icon  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_assets_present():
    for name in ("icon.png", "icon.ico", "splash.png"):
        assert (_ASSETS / name).is_file(), f"missing packaged asset: {name}"


def test_app_icon_loads(qapp):
    icon = app_icon()
    assert not icon.isNull(), "window/taskbar icon failed to load"
    # A real pixmap can be rendered at a typical small icon size.
    assert not icon.pixmap(64, 64).isNull()


def test_splash_loads(qapp):
    pm = QPixmap(str(_ASSETS / "splash.png"))
    assert not pm.isNull(), "splash image failed to load"
    assert pm.width() > 0 and pm.height() > 0
