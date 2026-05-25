"""Main application window — the 4-tab workflow shell."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QTabWidget,
    QWidget,
)

from autoseg_evaluator import __version__
from autoseg_evaluator.data.results import ResultsManager
from autoseg_evaluator.data.session import (
    DEFAULT_SUFFIX,
    build_session_dict,
    load_session_file,
    save_session,
)
from autoseg_evaluator.workers.metrics_worker import MetricsWorker
from autoseg_evaluator.ui.tabs.build_consensus import BuildConsensusTab
from autoseg_evaluator.ui.tabs.compute import ComputeTab
from autoseg_evaluator.ui.tabs.load_data import LoadDataTab
from autoseg_evaluator.ui.tabs.match_contours import MatchContoursTab
from autoseg_evaluator.ui.tabs.results import ResultsTab
from autoseg_evaluator.ui.theme import apply_theme
from autoseg_evaluator.utils.settings import save_settings


# qt-material ships several themes; we expose one light and one dark default.
LIGHT_THEME = "light_blue.xml"
DARK_THEME = "dark_blue.xml"


class MainWindow(QMainWindow):
    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__()
        self._settings = settings
        self._library: Any | None = None  # populated when Tab 1 loads a folder
        self._current_session_path: Path | None = None
        # When non-None, the next ``libraryLoaded`` triggers a session restore.
        self._pending_session_restore: dict[str, Any] | None = None
        # Background metric computation
        self._results = ResultsManager()
        self._metrics_thread: QThread | None = None
        self._metrics_worker: MetricsWorker | None = None

        self.setWindowTitle(f"AutoSeg Evaluator  v{__version__}")
        self._restore_geometry()
        self._build_menus()

        self._tabs = QTabWidget(self)
        self._load_tab = LoadDataTab(settings=self._settings, parent=self)
        self._consensus_tab = BuildConsensusTab(settings=self._settings, parent=self)
        self._match_tab = MatchContoursTab(settings=self._settings, parent=self)
        self._compute_tab = ComputeTab(settings=self._settings, parent=self)
        self._results_tab = ResultsTab()

        # Each tab is wrapped in a QScrollArea so that small windows (or
        # half-screen layouts on a single monitor) can still reach every
        # control by scrolling, instead of clipping the bottom of the panel.
        # Inner widgets (drawers, results table) still keep their own
        # scrolling when their content overflows.
        self._tabs.addTab(_wrap_scroll(self._load_tab), "1. Load Data")
        self._tabs.addTab(_wrap_scroll(self._consensus_tab), "2. Build Consensus GT (Optional)")
        self._tabs.addTab(_wrap_scroll(self._match_tab), "3. Match Contours")
        self._tabs.addTab(_wrap_scroll(self._compute_tab), "4. Compute")
        self._tabs.addTab(_wrap_scroll(self._results_tab), "5. Results")
        self._tabs.setCurrentIndex(int(self._settings.get("active_tab", 0)))
        self._results_tab.set_results_manager(self._results)

        # Wire cross-tab signals
        self._load_tab.libraryLoaded.connect(self._on_library_loaded)
        self._load_tab.overridesChanged.connect(self._on_overrides_changed)
        self._consensus_tab.consensusGenerated.connect(self._on_consensus_generated)
        self._match_tab.replacementRulesChanged.connect(self._on_replacement_rules_changed)
        self._match_tab.templateChanged.connect(self._on_template_changed)
        self._compute_tab.metricConfigChanged.connect(self._on_metric_config_changed)
        self._compute_tab.computeRequested.connect(self._on_compute_requested)
        self._compute_tab.cancelRequested.connect(self._on_compute_cancel_requested)

        self.setCentralWidget(self._tabs)

    def _on_library_loaded(self, library: Any) -> None:
        """Stash the loaded library so Match Contours / Compute tabs can read it."""
        self._library = library
        self._consensus_tab.set_library(library)
        self._match_tab.set_library(library)
        self._compute_tab.set_library(library)
        # Persist updated last_folder right away — survives crashes mid-session
        save_settings(self._settings)
        # If a Load Session is in progress, this scan completion is the trigger
        # to apply the saved drawer state.
        self._on_library_loaded_post_session()

    def _on_consensus_generated(self) -> None:
        """User clicked Generate Consensus GT — refresh downstream tabs so the
        synthetic RTSSes appear in their trees."""
        if self._library is not None:
            self._match_tab.set_library(self._library)
            self._compute_tab.set_library(self._library)

    def _on_overrides_changed(self, overrides: dict[str, str]) -> None:
        self._settings["custom_source_labels"] = dict(overrides)
        save_settings(self._settings)

    def _on_replacement_rules_changed(self, rules: list) -> None:
        self._settings["replacement_rules"] = list(rules)
        save_settings(self._settings)

    def _on_template_changed(self, template: dict) -> None:
        self._settings["last_template"] = dict(template)
        save_settings(self._settings)

    def _on_metric_config_changed(self, config: dict) -> None:
        """Persist Tab 3's metric configuration so it survives across launches."""
        self._settings["compute_geometric"] = dict(config.get("geometric", {}))
        # Note: tolerances are shared with the GT-similarity setting structure,
        # so keep the existing key under "tolerances".
        existing_tol = dict(self._settings.get("tolerances", {}) or {})
        existing_tol.update(config.get("tolerances", {}))
        self._settings["tolerances"] = existing_tol
        self._settings["dvh"] = dict(config.get("dvh", {}))
        save_settings(self._settings)

    # ---- Compute lifecycle -----------------------------------------------

    def _on_compute_requested(self, config: dict) -> None:
        """Spin up the background metrics worker and pipe progress into Tab 3."""
        if self._library is None:
            QMessageBox.warning(
                self, "Compute", "Load a folder before running metric computation."
            )
            return
        drawers_state = self._match_tab.session_state()
        if not drawers_state:
            QMessageBox.information(
                self,
                "Compute",
                "Configure at least one drawer in Tab 2 before computing metrics.",
            )
            return
        # Tear down any prior run before starting a new one
        self._teardown_metrics_thread()
        total_tasks = sum(
            len(p.get("tests", []) or [])
            for d in drawers_state
            for p in d.get("patients", []) or []
        )
        if total_tasks == 0:
            QMessageBox.information(
                self,
                "Compute",
                "No test rows to compute — add some tests to your drawers first.",
            )
            return

        self._compute_tab.progress_panel().begin(total_tasks)

        # Stamp the active tolerances on the ResultsManager so the Surface
        # Dice / APL column headers (and CSV export) carry the τ value
        # next to the metric name. Stops users accidentally merging CSVs
        # computed at different tolerances in Excel.
        tol = (config.get("tolerances") or {})
        self._results.set_tolerances(
            sd_tau_mm=float(tol.get("surface_dice_tau_mm")) if tol.get("surface_dice_tau_mm") is not None else None,
            apl_tau_mm=float(tol.get("apl_tolerance_mm")) if tol.get("apl_tolerance_mm") is not None else None,
        )

        self._metrics_worker = MetricsWorker(self._library, drawers_state, config)
        self._metrics_thread = QThread(self)
        self._metrics_worker.moveToThread(self._metrics_thread)

        self._metrics_thread.started.connect(self._metrics_worker.run)
        self._metrics_worker.progress.connect(self._on_metrics_progress)
        self._metrics_worker.result.connect(self._on_metric_result)
        self._metrics_worker.finished.connect(self._on_metrics_finished)
        self._metrics_worker.error.connect(self._on_metrics_error)
        # Cleanup
        self._metrics_worker.finished.connect(self._metrics_thread.quit)
        self._metrics_worker.error.connect(self._metrics_thread.quit)

        self._metrics_thread.start()

    def _on_compute_cancel_requested(self) -> None:
        if self._metrics_worker is not None:
            self._metrics_worker.cancel()

    def _on_metrics_progress(self, current: int, total: int, state: dict) -> None:
        self._compute_tab.progress_panel().update_progress(current, total, state)

    def _on_metric_result(self, row: dict) -> None:
        self._results.add_row(row)
        self._results_tab.refresh()

    def _on_metrics_finished(self, errors: int) -> None:
        cancelled = bool(self._metrics_worker and self._metrics_worker._cancelled)
        self._compute_tab.progress_panel().finish(cancelled=cancelled, errors=errors)
        self.statusBar().showMessage(
            f"Compute finished — {len(self._results)} total result row(s), {errors} error(s).",
            8000,
        )

    def _on_metrics_error(self, message: str) -> None:
        self._compute_tab.progress_panel().finish(cancelled=False, errors=0)
        QMessageBox.critical(self, "Compute", f"Metric computation failed:\n\n{message}")

    def _teardown_metrics_thread(self) -> None:
        if self._metrics_thread is not None:
            if self._metrics_thread.isRunning():
                if self._metrics_worker is not None:
                    self._metrics_worker.cancel()
                self._metrics_thread.quit()
                self._metrics_thread.wait(3000)
            self._metrics_thread.deleteLater()
            self._metrics_thread = None
        self._metrics_worker = None

    def results_manager(self) -> ResultsManager:
        """Expose the accumulated results so Tab 4 (step 9) can render them."""
        return self._results

    # ---- Menu bar + Save/Load Session -----------------------------------

    def _build_menus(self) -> None:
        bar = self.menuBar()
        if bar is None:
            return
        file_menu = bar.addMenu("&File")

        self._action_load_session = QAction("Load Session…", self)
        self._action_load_session.setShortcut(QKeySequence("Ctrl+O"))
        self._action_load_session.triggered.connect(self._on_load_session)
        file_menu.addAction(self._action_load_session)

        self._action_save_session = QAction("Save Session", self)
        self._action_save_session.setShortcut(QKeySequence("Ctrl+S"))
        self._action_save_session.triggered.connect(self._on_save_session)
        file_menu.addAction(self._action_save_session)

        self._action_save_session_as = QAction("Save Session As…", self)
        self._action_save_session_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self._action_save_session_as.triggered.connect(self._on_save_session_as)
        file_menu.addAction(self._action_save_session_as)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # ---- View menu --------------------------------------------------
        # Exclusive Light/Dark toggle; the choice persists in settings.json
        # under the existing "theme" key (qt-material stylesheet filename).
        view_menu = bar.addMenu("&View")
        theme_menu = view_menu.addMenu("Theme")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)

        current_theme = str(self._settings.get("theme", LIGHT_THEME))

        self._action_theme_light = QAction("Light", self, checkable=True)
        self._action_theme_light.setData(LIGHT_THEME)
        self._action_theme_light.setChecked(current_theme == LIGHT_THEME)
        self._action_theme_light.triggered.connect(
            lambda: self._on_theme_changed(LIGHT_THEME)
        )
        theme_group.addAction(self._action_theme_light)
        theme_menu.addAction(self._action_theme_light)

        self._action_theme_dark = QAction("Dark", self, checkable=True)
        self._action_theme_dark.setData(DARK_THEME)
        self._action_theme_dark.setChecked(current_theme == DARK_THEME)
        self._action_theme_dark.triggered.connect(
            lambda: self._on_theme_changed(DARK_THEME)
        )
        theme_group.addAction(self._action_theme_dark)
        theme_menu.addAction(self._action_theme_dark)

    def _on_theme_changed(self, theme: str) -> None:
        """Switch the qt-material stylesheet live and persist the choice."""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return
        apply_theme(app, theme=theme)
        self._settings["theme"] = theme
        save_settings(self._settings)

    def _on_save_session(self) -> None:
        if self._current_session_path is None:
            self._on_save_session_as()
            return
        self._write_session_to(self._current_session_path)

    def _on_save_session_as(self) -> None:
        start_dir = self._session_start_dir()
        suggested = self._suggested_session_filename()
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save Session As",
            str(start_dir / suggested),
            f"Session files (*{DEFAULT_SUFFIX});;All files (*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix == "":
            path = path.with_suffix(DEFAULT_SUFFIX[1:].split(".", 1)[0])  # safeguard
        if not str(path).lower().endswith(DEFAULT_SUFFIX.lower()):
            # Ensure the suggested suffix is appended
            path = Path(str(path) + DEFAULT_SUFFIX)
        self._write_session_to(path)
        self._current_session_path = path

    def _write_session_to(self, path: Path) -> None:
        if self._library is None:
            QMessageBox.warning(
                self, "Save Session", "Load a folder before saving a session."
            )
            return
        data = build_session_dict(
            folder=self._library.root_folder,
            drawers_state=self._match_tab.session_state(),
            replacement_rules=list(self._settings.get("replacement_rules", []) or []),
            template=dict(self._settings.get("last_template", {}) or {}),
            consensus_groups=self._consensus_tab.session_state(),
        )
        try:
            save_session(path, data)
        except OSError as exc:
            QMessageBox.critical(self, "Save Session", f"Could not save session:\n{exc}")
            return
        self._current_session_path = path
        self.statusBar().showMessage(f"Session saved to {path}", 5000)

    def _on_load_session(self) -> None:
        start_dir = self._session_start_dir()
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Load Session",
            str(start_dir),
            f"Session files (*{DEFAULT_SUFFIX});;All files (*)",
        )
        if not path_str:
            return
        try:
            data = load_session_file(Path(path_str))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Load Session", f"Could not read session:\n{exc}")
            return

        # Pull rules + template back into settings before triggering the scan,
        # so the auto-match pipeline uses the session's values.
        self._settings["replacement_rules"] = list(data.get("replacement_rules", []) or [])
        self._settings["last_template"] = dict(data.get("last_template", {}) or {})
        save_settings(self._settings)
        # Refresh the in-tab settings reference so dialogs pre-populate from disk.
        self._match_tab.set_settings(self._settings)

        folder = str(data.get("folder", "") or "")
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(
                self,
                "Load Session",
                "The folder recorded in this session no longer exists. "
                "Load a folder manually in Tab 1, then re-open this session.",
            )
            return

        self._current_session_path = Path(path_str)
        # Defer drawer restore until Tab 1's scan completes
        self._pending_session_restore = data
        self._load_tab.load_folder(folder)

    def _on_library_loaded_post_session(self) -> None:
        """If a session load is in flight, restore drawers now that scan is done."""
        if self._pending_session_restore is None:
            return
        data = self._pending_session_restore
        self._pending_session_restore = None
        # Consensus groups must be restored BEFORE drawers so the synthetic
        # RTSSes exist in the library by the time the drawer-restore code
        # tries to resolve their SOPInstanceUIDs.
        consensus_groups = data.get("consensus_groups", []) or []
        if consensus_groups:
            self._consensus_tab.apply_session_state(consensus_groups)
            # Refresh downstream tabs so the synthetic RTSSes appear in their trees.
            if self._library is not None:
                self._match_tab.set_library(self._library)
                self._compute_tab.set_library(self._library)
        drawers_state = data.get("drawers", []) or []
        applied, missing, warnings = self._match_tab.apply_session_state(drawers_state)
        msg_lines = [f"Session restored: {applied} patient sub-section(s) loaded."]
        if missing:
            msg_lines.append(f"{missing} sub-section(s) could not be restored.")
        if warnings:
            shown = warnings[:5]
            msg_lines.extend(shown)
            if len(warnings) > 5:
                msg_lines.append(f"…and {len(warnings) - 5} more warnings.")
        self.statusBar().showMessage(msg_lines[0], 8000)
        if warnings:
            QMessageBox.information(self, "Load Session", "\n".join(msg_lines))
        # Jump to Tab 2 so the user sees the restored work
        self._tabs.setCurrentWidget(self._match_tab)

    def _session_start_dir(self) -> Path:
        if self._current_session_path is not None:
            return self._current_session_path.parent
        folder = self._settings.get("last_folder", "") or ""
        return Path(folder) if folder else Path.home()

    def _suggested_session_filename(self) -> str:
        if self._library is not None and self._library.root_folder:
            return f"{Path(self._library.root_folder).name}{DEFAULT_SUFFIX}"
        return f"session{DEFAULT_SUFFIX}"

    def _restore_geometry(self) -> None:
        win = self._settings.get("window", {})
        self.resize(int(win.get("width", 1400)), int(win.get("height", 900)))
        self.move(int(win.get("x", 100)), int(win.get("y", 100)))
        if win.get("maximized"):
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings["window"] = {
            "width": self.width(),
            "height": self.height(),
            "x": self.x(),
            "y": self.y(),
            "maximized": self.isMaximized(),
        }
        self._settings["active_tab"] = self._tabs.currentIndex()
        save_settings(self._settings)
        super().closeEvent(event)


def _wrap_scroll(widget: QWidget) -> QScrollArea:
    """Wrap ``widget`` in a frameless QScrollArea so the tab stays usable when
    the window is smaller than the tab's natural size.

    ``setWidgetResizable(True)`` makes the inner widget grow to the scroll
    area's width — without it, the wrapped tab would be pinned to its
    minimum size hint and never expand horizontally.
    """
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    return area
