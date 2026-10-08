"""Suite-wide hooks.

Qt is taken down at the end of the session, before Python's own shutdown does
it. Left to interpreter exit, the QApplication and the widgets the tests leave
behind are destroyed in whatever order the garbage collector reaches them, and
on CI runners that intermittently crashed the process after every test had
passed: a segfault on Ubuntu, a bare exit code 1 on Windows.
"""

from __future__ import annotations

import gc

import pytest


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover - Qt is a hard dependency
        return
    app = QApplication.instance()
    if app is None:
        return
    # Run what is still queued - matplotlib's idle redraws, chiefly - while
    # its widgets exist. A redraw queued for a canvas a test already deleted
    # raises here, harmlessly; at interpreter exit it has nothing to raise to.
    try:
        QCoreApplication.processEvents()
    except RuntimeError:
        pass
    # deleteLater, not close: closing a main window can ask about an unsaved
    # session, and a modal dialog would hang a headless run. Only the deletions
    # are delivered, so nothing queued above runs against a deleted widget.
    for widget in QApplication.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
