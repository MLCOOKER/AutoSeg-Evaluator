"""Say why the application could not start, instead of vanishing.

The Windows bundle starts through ``pythonw.exe``, which has no console, so an
exception during start-up used to end the process silently after the splash
screen. v3.0.0 did exactly that when it was extracted to a deep folder: Windows
refused to load a library whose full path passed its 260-character limit.

This module uses the standard library only, so it still works when the failure
is a third-party package that will not load. It writes ``startup-error.log``
next to the launcher, then shows a message box: Qt's when Qt is running,
Windows' own when it is not, and on other platforms prints to stderr, where a
terminal shows it.
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

#: Windows' limit on a full path, unless long paths are enabled machine-wide.
WINDOWS_MAX_PATH = 260

#: ERROR_FILENAME_EXCED_RANGE: "The filename or extension is too long."
_WINERROR_TOO_LONG = 206

LOG_NAME = "startup-error.log"
TITLE = "AutoSeg Evaluator could not start"


def install_dir() -> Path:
    """The folder holding the launcher: the bundle's top folder, or the repository."""
    from autoseg_evaluator.utils.paths import app_dir

    return app_dir()


def _chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def names_a_long_path(exc: BaseException) -> bool:
    """Whether Windows refused a path for being too long, anywhere in the chain."""
    for link in _chain(exc):
        if getattr(link, "winerror", None) == _WINERROR_TOO_LONG:
            return True
        if "filename or extension is too long" in str(link).lower():
            return True
    return False


def files_too_deep(folder: Path, limit: int = WINDOWS_MAX_PATH - 1) -> int:
    """How many of the bundle's files have full paths longer than ``limit`` here.

    Only the bundle's own ``python`` and ``app`` folders are counted, so a
    source checkout, which has neither, counts none. Windows does not always
    say "too long": a library behind a too-long path can also be reported as
    not found, so the count is the evidence that does not depend on wording.
    """
    count = 0
    for part in ("python", "app"):
        top = os.path.join(os.path.abspath(folder), part)
        # The extended-length prefix lets the walk reach what Windows refuses.
        walk = "\\\\?\\" + top if sys.platform == "win32" else top
        for dirpath, _dirs, files in os.walk(walk):
            for name in files:
                full = os.path.join(dirpath, name)
                if len(full) - (len(walk) - len(top)) > limit:
                    count += 1
    return count


def failure_message(exc: BaseException, folder: Path, log: Path | None, too_deep: int = 0) -> str:
    """What the user is told: the cause when it is known, and what to do."""
    where = f"\n\nThe details are saved in {log}." if log else ""
    if too_deep or names_a_long_path(exc):
        count = (
            f" {too_deep:,} of its files are past that limit in this folder, whose own path is"
            if too_deep
            else " This folder's path is"
        )
        return (
            "Windows could not open one of AutoSeg Evaluator's files, because the "
            f"file's full path is longer than Windows allows ({WINDOWS_MAX_PATH} "
            f"characters).{count} {len(str(folder))} characters long:\n\n"
            f"{folder}\n\n"
            "Move the folder somewhere with a short path, such as "
            "C:\\AutoSegEvaluator, and start it again." + where
        )
    return f"{type(exc).__name__}: {exc}" + where


def write_failure_log(exc: BaseException, folder: Path, too_deep: int = 0) -> Path | None:
    """Write ``startup-error.log`` in ``folder``; ``None`` if it cannot be written."""
    log = folder / LOG_NAME
    try:
        from autoseg_evaluator import __version__
    except Exception:  # noqa: BLE001 - the version is a nicety here
        __version__ = "unknown"
    text = (
        f"{TITLE}\n"
        f"Time: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"Version: {__version__}\n"
        f"Folder: {folder} ({len(str(folder))} characters)\n"
        f"Python: {sys.version.split()[0]} at {sys.executable}\n\n"
        f"{failure_message(exc, folder, None, too_deep)}\n\n"
        + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    )
    try:
        log.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return log


def report_failure(exc: BaseException) -> None:
    """Log the failure, then show it in whatever way this process still can."""
    try:
        folder = install_dir()
    except Exception:  # noqa: BLE001 - fall back to where the interpreter is
        folder = Path(sys.executable).resolve().parent
    too_deep = files_too_deep(folder) if sys.platform == "win32" else 0
    log = write_failure_log(exc, folder, too_deep)
    message = failure_message(exc, folder, log, too_deep)
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if _show_with_qt(message, details):
        return
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)  # MB_ICONERROR
            return
        except Exception:  # noqa: BLE001 - stderr below is the last resort
            pass
    print(f"{TITLE}\n\n{message}\n\n{details}", file=sys.stderr)


def _show_with_qt(message: str, details: str) -> bool:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except Exception:  # noqa: BLE001 - Qt may be the thing that failed
        return False
    if QApplication.instance() is None:
        return False
    box = QMessageBox(QMessageBox.Icon.Critical, TITLE, message)
    box.setDetailedText(details)
    box.exec()
    return True
