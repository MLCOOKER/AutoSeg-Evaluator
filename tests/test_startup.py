"""A failed start is reported, not swallowed.

v3.0.0's Windows bundle, extracted to a deep folder, showed its splash screen
and vanished: a library's full path passed Windows' 260-character limit, and
the launcher has no console to print the error to.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from autoseg_evaluator import startup

#: What Windows said for v3.0.0, word for word.
TOO_LONG = "DLL load failed while importing lib: The filename or extension is too long."
DEEP = Path("C:/" + "/".join(["Paper 4 - Autocontouring Implementation"] * 4))


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


def _raised(exc: BaseException) -> BaseException:
    try:
        raise exc
    except BaseException as caught:  # noqa: BLE001 - kept with its traceback
        return caught


def test_a_path_windows_refused_as_too_long_is_recognised():
    assert startup.names_a_long_path(ImportError(TOO_LONG))
    assert not startup.names_a_long_path(ImportError("No module named 'shapely'"))


def test_it_is_recognised_wherever_it_sits_in_the_chain():
    try:
        try:
            raise ImportError(TOO_LONG)
        except ImportError as cause:
            raise RuntimeError("main window") from cause
    except RuntimeError as exc:
        assert startup.names_a_long_path(exc)


def test_a_long_path_is_explained_with_what_to_do():
    message = startup.failure_message(ImportError(TOO_LONG), DEEP, None)
    assert "260 characters" in message
    assert f"{len(str(DEEP))} characters long" in message
    assert str(DEEP) in message
    assert "C:\\AutoSegEvaluator" in message


def test_files_past_the_limit_are_counted_in_the_bundle_folders_only(tmp_path):
    deep = tmp_path / "python" / "Lib" / "site-packages"
    deep.mkdir(parents=True)
    (deep / "geos_c.dll").write_text("")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a-very-long-name-outside-the-bundle-folders.txt").write_text("")
    limit = len(str(tmp_path / "app" / "a.py"))
    assert startup.files_too_deep(tmp_path, limit) == 1
    assert startup.files_too_deep(tmp_path) == 0


def test_a_count_of_files_too_deep_explains_any_error_wording():
    """Windows can report a library behind a too-long path as not found."""
    message = startup.failure_message(ImportError("module not found"), DEEP, None, 2442)
    assert "2,442 of its files are past that limit" in message
    assert "C:\\AutoSegEvaluator" in message


def test_any_other_failure_is_shown_as_it_is():
    message = startup.failure_message(ImportError("No module named 'shapely'"), DEEP, None)
    assert message == "ImportError: No module named 'shapely'"


def test_the_log_holds_the_explanation_and_the_traceback(tmp_path):
    log = startup.write_failure_log(_raised(ImportError(TOO_LONG)), tmp_path)
    assert log == tmp_path / startup.LOG_NAME
    text = log.read_text(encoding="utf-8")
    assert "C:\\AutoSegEvaluator" in text
    assert "Traceback" in text and TOO_LONG in text


def test_a_folder_that_cannot_be_written_still_gets_a_message(tmp_path):
    missing = tmp_path / "gone"
    assert startup.write_failure_log(ImportError(TOO_LONG), missing) is None
    assert "details are saved" not in startup.failure_message(ImportError(TOO_LONG), missing, None)


def test_with_qt_running_the_failure_is_a_message_box(qapp, tmp_path, monkeypatch):
    shown = {}

    def fake_exec(box):
        shown.update(title=box.windowTitle(), text=box.text(), details=box.detailedText())
        return 0

    monkeypatch.setattr(startup, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    startup.report_failure(_raised(ImportError(TOO_LONG)))
    assert shown["title"] == startup.TITLE
    assert "C:\\AutoSegEvaluator" in shown["text"]
    assert str(tmp_path / startup.LOG_NAME) in shown["text"]
    assert TOO_LONG in shown["details"]
    assert (tmp_path / startup.LOG_NAME).is_file()


def test_main_reports_a_failure_after_the_splash_and_returns_1(qapp, monkeypatch):
    """The failure v3.0.0 hit: the main window's imports, after the splash."""
    from autoseg_evaluator import app as app_module
    from autoseg_evaluator.ui import main_window

    reported = []

    def broken(*args, **kwargs):
        raise ImportError(TOO_LONG)

    monkeypatch.setattr(main_window, "MainWindow", broken)
    monkeypatch.setattr(app_module, "report_failure", reported.append)
    monkeypatch.setattr(QApplication, "exec", lambda self: pytest.fail("the event loop ran"))
    assert app_module.main() == 1
    assert len(reported) == 1 and startup.names_a_long_path(reported[0])


def test_the_module_entry_point_reports_a_failure_to_import_the_app(monkeypatch):
    """Before Qt is running there is only the bare module to fall back on."""
    import builtins

    from autoseg_evaluator import __main__ as entry

    real_import = builtins.__import__
    reported = []

    def failing_import(name, *args, **kwargs):
        if name == "autoseg_evaluator.app":
            raise ImportError(TOO_LONG)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    monkeypatch.setattr(startup, "report_failure", reported.append)
    assert entry.main() == 1
    assert len(reported) == 1
