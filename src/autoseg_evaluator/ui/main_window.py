"""Main application window — the 6-tab workflow shell."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QGuiApplication, QKeySequence
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
from autoseg_evaluator.data.organ_index import build_organ_index
from autoseg_evaluator.data.results import ResultsManager
from autoseg_evaluator.data.session import (
    DEFAULT_SUFFIX,
    build_session_dict,
    load_session_file,
    save_session,
)
from autoseg_evaluator.data.synonyms import flatten_synonyms, load_synonyms
from autoseg_evaluator.ui.dialogs.organ_labels import OrganLabelsDialog
from autoseg_evaluator.ui.tabs.build_consensus import BuildConsensusTab
from autoseg_evaluator.ui.tabs.compute import ComputeTab
from autoseg_evaluator.ui.tabs.load_data import LoadDataTab
from autoseg_evaluator.ui.tabs.match_contours import MatchContoursTab
from autoseg_evaluator.ui.tabs.qualitative import QualitativeTab
from autoseg_evaluator.ui.tabs.report import ReportTab
from autoseg_evaluator.ui.tabs.results import ResultsTab
from autoseg_evaluator.ui.theme import apply_theme
from autoseg_evaluator.utils.paths import synonyms_path
from autoseg_evaluator.utils.settings import save_settings
from autoseg_evaluator.workers.metrics_worker import MetricsWorker

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
        # Canonical organ grouping for the loaded cohort; rebuilt on load.
        self._organ_index: Any = None
        # Organ labels the user has given in Label Organs. Deliberately NOT in
        # settings: they describe one cohort's naming, not a preference, and
        # writing them the moment a dialog closes would both leak them into
        # unrelated folders and record work the user never chose to keep. They
        # live here until a session is saved, which is how drawers and matches
        # already behave.
        self._organ_assignments: dict[str, str] = {}
        self._metrics_thread: QThread | None = None
        self._metrics_worker: MetricsWorker | None = None
        # Threads from a previous run that had not stopped when detached, kept
        # with their worker until they finish: destroying a running QThread
        # aborts the application.
        self._retiring: list[tuple[QThread, MetricsWorker | None]] = []

        self.setWindowTitle(f"AutoSeg Evaluator  v{__version__}")
        self._restore_geometry()
        self._build_menus()

        self._tabs = QTabWidget(self)
        self._load_tab = LoadDataTab(settings=self._settings, parent=self)
        self._consensus_tab = BuildConsensusTab(settings=self._settings, parent=self)
        self._match_tab = MatchContoursTab(settings=self._settings, parent=self)
        self._qualitative_tab = QualitativeTab(parent=self)
        self._qualitative_tab.set_settings(self._settings)
        self._qualitative_tab.set_drawers_provider(self._match_tab.session_state)
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
        # Scroll-wrapped like the other tabs so the window can always shrink to
        # fit a small screen (the viewer still fills the viewport via
        # setWidgetResizable; scrollbars appear only when the window is smaller
        # than the tab's minimum content size).
        self._qual_page = _wrap_scroll(self._qualitative_tab)
        self._tabs.addTab(self._qual_page, "4. Qualitative Assessment")
        self._tabs.addTab(_wrap_scroll(self._compute_tab), "5. Compute")
        self._tabs.addTab(_wrap_scroll(self._results_tab), "6. Results")
        self._report_tab = ReportTab()
        self._tabs.addTab(_wrap_scroll(self._report_tab), "7. Report")
        active = min(int(self._settings.get("active_tab", 0)), self._tabs.count() - 1)
        self._tabs.setCurrentIndex(max(0, active))
        self._results_tab.set_results_manager(self._results)
        self._report_tab.set_results_manager(self._results)
        # Discarding the results must empty the report too. Without this the
        # Report tab keeps rendering the previous cohort — and exporting it.
        self._results_tab.cleared.connect(self._report_tab.refresh)

        # Wire cross-tab signals
        self._load_tab.set_folder_change_guard(self._confirm_folder_change)
        self._load_tab.libraryLoaded.connect(self._on_library_loaded)
        self._load_tab.overridesChanged.connect(self._on_overrides_changed)
        self._load_tab.linkOverridesChanged.connect(self._on_link_overrides_changed)
        self._consensus_tab.consensusGenerated.connect(self._on_consensus_generated)
        self._consensus_tab.observerLabelsChanged.connect(self._on_observer_labels_changed)
        self._match_tab.replacementRulesChanged.connect(self._on_replacement_rules_changed)
        self._match_tab.templateChanged.connect(self._on_template_changed)
        self._match_tab.organLabelsRequested.connect(self._on_label_organs)
        self._compute_tab.metricConfigChanged.connect(self._on_metric_config_changed)
        self._compute_tab.computeRequested.connect(self._on_compute_requested)
        self._compute_tab.cancelRequested.connect(self._on_compute_cancel_requested)
        self._qualitative_tab.qualitativeScored.connect(self._on_qualitative_scored)
        self._qualitative_tab.assessmentLockChanged.connect(self._on_assessment_lock_changed)

        self.setCentralWidget(self._tabs)

    def _rebuild_organ_index(self) -> None:
        """Re-derive canonical organ groups for the loaded cohort.

        Cheap — a dictionary lookup per distinct ROI name — and re-run whenever
        the cohort or the user's organ answers change. The result is handed to
        the ResultsManager, which applies it at read time, so relabelling an
        organ never means recomputing a metric.
        """
        if self._library is None:
            self._results.set_organ_index(None)
            return
        pending = getattr(self, "_pending_organ_assignments", None)
        if pending is not None:
            # A session restore triggers a rescan; its labels land here once
            # the library exists rather than being applied to an empty one.
            self._organ_assignments = dict(pending)
            self._pending_organ_assignments = None
        try:
            synonyms = flatten_synonyms(load_synonyms(synonyms_path()))
        except Exception:  # noqa: BLE001 — a bad dictionary must not block loading
            synonyms = {}
        index = build_organ_index(
            self._library,
            synonyms_flat=synonyms,
            manual=dict(self._organ_assignments),
        )
        self._organ_index = index
        self._results.set_organ_index(index)
        self._match_tab.set_organ_index(index)
        self._results_tab.refresh()
        self._report_tab.refresh()

    def _on_label_organs(self) -> None:
        """Open Label Organs and apply whatever the user decides.

        Labelling changes no drawer and recomputes no metric — the index is
        rebuilt and the results table is re-read, so existing rows simply
        re-label.
        """
        if self._organ_index is None:
            QMessageBox.information(
                self,
                "Label Organs",
                "Load a folder and run Auto-Match first — organ labels apply to drawers.",
            )
            return
        drawers = self._match_tab.drawers()
        if not drawers:
            QMessageBox.information(self, "Label Organs", "There are no drawers to label yet.")
            return
        dialog = OrganLabelsDialog(
            self._organ_index,
            drawers,
            dict(self._organ_assignments),
            parent=self,
        )
        if dialog.exec() != OrganLabelsDialog.DialogCode.Accepted:
            return
        self._organ_assignments = dialog.labels()
        self._rebuild_organ_index()
        self.statusBar().showMessage(
            f"{len(self._organ_assignments)} organ label(s) applied — "
            f"save the session to keep them.",
            8000,
        )

    def _on_library_loaded(self, library: Any) -> None:
        """Stash the loaded library so Match Contours / Compute tabs can read it."""
        self._library = library
        self._consensus_tab.set_library(library)
        self._match_tab.set_library(library)
        self._report_tab.set_library(library)
        self._qualitative_tab.set_library(library)
        self._compute_tab.set_library(library)
        self._rebuild_organ_index()
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

    def _on_link_overrides_changed(self, overrides: dict[str, str]) -> None:
        """User settled a data link in Tab 1 — keep it for the next launch.

        Also written into the session on save, so the answers travel with the
        cohort rather than only with this machine's settings.
        """
        self._settings["link_overrides"] = dict(overrides)
        save_settings(self._settings)

    def _on_observer_labels_changed(self, labels: list) -> None:
        self._settings["consensus_observer_labels"] = list(labels)
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
        self._settings["compute_polygon"] = dict(config.get("polygon", {}))
        self._settings["audit"] = dict(config.get("audit", {}))
        self._settings["staple"] = dict(config.get("staple", {}))
        save_settings(self._settings)

    # ---- Qualitative assessment ------------------------------------------

    def _on_qualitative_scored(self, payload: dict) -> None:
        """Record one Likert score into the results store and refresh Tab 6."""
        self._results.upsert_qualitative_score(
            patient_id=payload["patient_id"],
            drawer=payload["drawer"],
            source_label=payload["source_label"],
            roi_name=payload["roi_name"],
            roi_number=payload["roi_number"],
            is_gt=payload["is_gt"],
            rater=payload["rater"],
            score=payload["score"],
            blinded=payload["blinded"],
            rtstruct_sop_uid=payload.get("rtstruct_sop_uid", ""),
            # Blank for a score restored from a session that predates the time
            # being recorded; the results then show it blank rather than now.
            scored_at=payload.get("scored_at"),
        )
        self._results_tab.refresh()
        self._report_tab.refresh()

    def _on_assessment_lock_changed(self, locked: bool) -> None:
        """Lock the other tabs during a (blinded) assessment to prevent leakage.

        The qualitative tab itself stays enabled; its own Unlock button is the
        only way out. Session save/load stay available via the File menu so a
        rater can checkpoint mid-stack.
        """
        q_index = self._tabs.indexOf(self._qual_page)
        for i in range(self._tabs.count()):
            if i != q_index:
                self._tabs.setTabEnabled(i, not locked)
        if locked:
            self._tabs.setCurrentIndex(q_index)

    # ---- Compute lifecycle -----------------------------------------------

    def _on_compute_requested(self, config: dict) -> None:
        """Spin up the background metrics worker and pipe progress into Tab 3."""
        if self._library is None:
            QMessageBox.warning(self, "Compute", "Load a folder before running metric computation.")
            return
        drawers_state = self._match_tab.session_state()
        if not drawers_state:
            QMessageBox.information(
                self,
                "Compute",
                "Configure at least one drawer in Tab 2 before computing metrics.",
            )
            return
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
        if not self._confirm_replace_results():
            return
        # Detach from the previous run, which has finished: Compute All is
        # disabled while one is running.
        self._teardown_metrics_thread()

        self._compute_tab.progress_panel().begin(total_tasks)

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

        self._compute_tab.set_running(True)
        self._metrics_thread.start()

    def _confirm_replace_results(self) -> bool:
        """Clear the previous computation's rows, once the user agrees.

        One computation per results table: rows are never updated or merged, so
        every row in a table came from one run with one set of settings. An
        external audit found two runs at different tolerances sharing columns,
        labelled with whichever tolerance was set last. Likert scores are not
        part of a computation and are kept.
        """
        count = self._results.computed_row_count()
        if count == 0:
            return True
        answer = self._ask_replace_results(count)
        if answer == "cancel":
            return False
        if answer == "export" and not self._results_tab.export_with_dialog():
            return False
        self._results.clear_computed()
        self._results_tab.refresh()
        self._report_tab.refresh()
        return True

    def _ask_replace_results(self, count: int) -> str:
        """``"export"``, ``"replace"`` or ``"cancel"``. Separate so tests can answer."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Replace results")
        box.setText(f"Computing again replaces the {count} result row(s) already in Results.")
        box.setInformativeText(
            "Every row in a results table comes from one computation, with one set "
            "of settings. Likert scores are kept, and appear on the new rows.\n\n"
            "Export the current results first if you need them."
        )
        export = box.addButton("Export, then replace…", QMessageBox.ButtonRole.AcceptRole)
        replace = box.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is export:
            return "export"
        if clicked is replace:
            return "replace"
        return "cancel"

    # ---- Changing cohort -------------------------------------------------

    def _has_cohort_work(self) -> bool:
        """Whether anything the user produced for the loaded cohort would be lost."""
        return bool(
            self._results.computed_row_count()
            or self._qualitative_tab.has_scores()
            or self._organ_assignments
            or self._match_tab.session_state()
        )

    def _work_summary(self) -> str:
        parts = []
        drawers = len(self._match_tab.session_state())
        if drawers:
            parts.append(f"{drawers} matched drawer(s)")
        rows = self._results.computed_row_count()
        if rows:
            parts.append(f"{rows} result row(s)")
        if self._qualitative_tab.has_scores():
            parts.append("the Likert scores")
        if self._organ_assignments:
            parts.append(f"{len(self._organ_assignments)} organ label(s)")
        return ", ".join(parts)

    def _confirm_folder_change(self, folder: str) -> bool:
        """Before a different folder loads: keep, or knowingly discard, the work.

        Loading another folder used to keep the previous cohort's results,
        scores, organ labels and session path, so its report could show the
        old results and Save could overwrite the old session with the new
        cohort. A rescan of the same folder keeps everything.
        """
        current = getattr(self._library, "root_folder", "") if self._library is not None else ""
        if current and _same_folder(current, folder):
            return True
        if not self._confirm_discard_work(
            "Load a different folder",
            "Loading a different folder starts a new piece of work.",
        ):
            return False
        self._discard_cohort_work()
        return True

    def _confirm_discard_work(self, title: str, text: str) -> bool:
        """Ask before discarding the current cohort's work; offer to save it."""
        if not self._has_cohort_work():
            return True
        answer = self._ask_discard_work(title, text, self._work_summary())
        if answer == "cancel":
            return False
        if answer == "save":
            return self._on_save_session()
        return True

    def _ask_discard_work(self, title: str, text: str, summary: str) -> str:
        """``"save"``, ``"discard"`` or ``"cancel"``. Separate so tests can answer."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        box.setInformativeText(
            f"The current work — {summary} — will be cleared. Save the session "
            "first to keep it; a saved session holds all of it, results included."
        )
        save = box.addButton("Save session, then continue…", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Continue without saving", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        if clicked is discard:
            return "discard"
        return "cancel"

    def _discard_cohort_work(self) -> None:
        """Forget the cohort's results, scores, organ labels and session file."""
        self._results.clear()
        self._organ_assignments = {}
        self._pending_organ_assignments = None
        self._qualitative_tab.reset()
        self._current_session_path = None
        self._results_tab.refresh()
        self._report_tab.refresh()

    def _on_compute_cancel_requested(self) -> None:
        if self._metrics_worker is not None:
            self._metrics_worker.cancel()

    def _on_metrics_progress(self, current: int, total: int, state: dict) -> None:
        self._compute_tab.progress_panel().update_progress(current, total, state)

    def _on_metric_result(self, row: dict) -> None:
        self._results.add_row(row)
        self._results_tab.refresh()
        self._report_tab.refresh()

    def _on_metrics_finished(self, errors: int) -> None:
        cancelled = bool(self._metrics_worker and self._metrics_worker._cancelled)
        self._compute_tab.progress_panel().finish(cancelled=cancelled, errors=errors)
        self._compute_tab.set_running(False)
        self.statusBar().showMessage(
            f"Compute finished — {len(self._results)} total result row(s), {errors} error(s).",
            8000,
        )

    def _on_metrics_error(self, message: str) -> None:
        self._compute_tab.progress_panel().finish(cancelled=False, errors=0)
        self._compute_tab.set_running(False)
        QMessageBox.critical(self, "Compute", f"Metric computation failed:\n\n{message}")

    def _teardown_metrics_thread(self, *, wait_ms: int = 3000) -> None:
        """Detach from the previous run and let its thread end safely.

        An external audit found a thread that did not stop within three seconds
        scheduled for deletion anyway — Qt aborts when a running QThread is
        destroyed. Now the previous run's signals are disconnected first, so a
        late row cannot land in the next run's results, and a thread still
        running is kept, with its worker, until it reports finished.
        """
        thread, worker = self._metrics_thread, self._metrics_worker
        self._metrics_thread = None
        self._metrics_worker = None
        if worker is not None:
            worker.cancel()
            for signal, slot in (
                (worker.progress, self._on_metrics_progress),
                (worker.result, self._on_metric_result),
                (worker.finished, self._on_metrics_finished),
                (worker.error, self._on_metrics_error),
            ):
                with contextlib.suppress(RuntimeError, TypeError):  # already disconnected
                    signal.disconnect(slot)
        if thread is None:
            return
        if thread.isRunning():
            thread.quit()
            if not thread.wait(wait_ms):
                self._retiring.append((thread, worker))
                thread.finished.connect(lambda t=thread: self._retire(t))
                return
        thread.deleteLater()

    def _retire(self, thread: QThread) -> None:
        """Release a thread that outlived its run, now that it has finished."""
        self._retiring = [(t, w) for t, w in self._retiring if t is not thread]
        thread.deleteLater()

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
        self._action_theme_light.triggered.connect(lambda: self._on_theme_changed(LIGHT_THEME))
        theme_group.addAction(self._action_theme_light)
        theme_menu.addAction(self._action_theme_light)

        self._action_theme_dark = QAction("Dark", self, checkable=True)
        self._action_theme_dark.setData(DARK_THEME)
        self._action_theme_dark.setChecked(current_theme == DARK_THEME)
        self._action_theme_dark.triggered.connect(lambda: self._on_theme_changed(DARK_THEME))
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

    def _on_save_session(self) -> bool:
        if self._current_session_path is None:
            return self._on_save_session_as()
        return self._write_session_to(self._current_session_path)

    def _on_save_session_as(self) -> bool:
        start_dir = self._session_start_dir()
        suggested = self._suggested_session_filename()
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save Session As",
            str(start_dir / suggested),
            f"Session files (*{DEFAULT_SUFFIX});;All files (*)",
        )
        if not path_str:
            return False
        path = Path(path_str)
        if not str(path).lower().endswith(DEFAULT_SUFFIX.lower()):
            # Ensure the suggested suffix is appended
            path = Path(str(path) + DEFAULT_SUFFIX)
        return self._write_session_to(path)

    def _write_session_to(self, path: Path) -> bool:
        if self._library is None:
            QMessageBox.warning(self, "Save Session", "Load a folder before saving a session.")
            return False
        data = build_session_dict(
            folder=self._library.root_folder,
            drawers_state=self._match_tab.session_state(),
            replacement_rules=list(self._settings.get("replacement_rules", []) or []),
            template=dict(self._settings.get("last_template", {}) or {}),
            consensus_groups=self._consensus_tab.session_state(),
            qualitative=self._qualitative_tab.session_state(),
            link_overrides=dict(getattr(self._library, "link_overrides", {}) or {}),
            organ_assignments=dict(self._organ_assignments),
            # The computed table, so scoring can continue in a later session
            # without computing again.
            results=self._results.session_state(),
        )
        try:
            save_session(path, data)
        except OSError as exc:
            QMessageBox.critical(self, "Save Session", f"Could not save session:\n{exc}")
            return False
        self._current_session_path = path
        self.statusBar().showMessage(f"Session saved to {path}", 5000)
        return True

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
        # A session replaces the work in progress; the user decides about it
        # before anything is changed.
        if not self._confirm_discard_work(
            "Load Session", "Opening a session replaces the current work."
        ):
            return
        self._discard_cohort_work()

        # Pull rules + template back into settings before triggering the scan,
        # so the auto-match pipeline uses the session's values.
        self._settings["replacement_rules"] = list(data.get("replacement_rules", []) or [])
        self._settings["last_template"] = dict(data.get("last_template", {}) or {})
        # Set before the scan starts: Tab 1 applies these to the library the
        # moment the scan finishes, so the restored answers are already in
        # place by the time any tab resolves a link.
        self._settings["link_overrides"] = dict(data.get("link_overrides", {}) or {})
        self._pending_organ_assignments = dict(data.get("organ_assignments", {}) or {})
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
        # The results table computed in an earlier session, so scoring can
        # carry on without computing again. The scores that sit on its rows are
        # re-sent by the qualitative restore below.
        restored_rows = self._results.apply_session_state(data.get("results"))
        if restored_rows:
            msg_lines.append(f"{restored_rows} result row(s) restored.")
        # Restore the qualitative run (rater list, options, and every rater's
        # scores / position), rebuilding its stack from the drawers just
        # applied. It lands on the Tab 4 setup panel (unlocked) so the user can
        # resume any rater. If it was mid-assessment, surface Tab 4 so they see
        # it; otherwise show the restored matches.
        qualitative = data.get("qualitative", {}) or {}
        self._qualitative_tab.apply_session_state(qualitative)
        orphaned = self._qualitative_tab.orphaned_score_count()
        if orphaned:
            warnings.append(
                f"{orphaned} Likert score(s) are for contours no longer in the drawers. "
                "They are kept, saved with the session, and listed in Results as rows of "
                "their own."
            )
            msg_lines.append(warnings[-1])
        if warnings:
            QMessageBox.information(self, "Load Session", "\n".join(msg_lines))
        self._results_tab.refresh()
        self._report_tab.refresh()
        if qualitative.get("started"):
            self._tabs.setCurrentWidget(self._qual_page)
        else:
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
        width = int(win.get("width", 1400))
        height = int(win.get("height", 900))
        x = int(win.get("x", 100))
        y = int(win.get("y", 100))

        # Clamp to the screen's available work area (excludes the macOS menu
        # bar / Dock and the Windows taskbar) so the window never opens larger
        # than the display or off-screen. A saved size/position from a bigger
        # monitor — or the 1400×900 default on a small laptop — would otherwise
        # run past the edge; on macOS you then can't drag the title bar out from
        # under the menu bar to recover it.
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            width, height, x, y = _clamp_to_available(
                width, height, x, y, (geo.x(), geo.y(), geo.width(), geo.height())
            )

        self.resize(width, height)
        self.move(x, y)
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
        # A running computation stops at its next structure; wait for it rather
        # than let Qt destroy a running thread on the way out.
        if self._metrics_worker is not None:
            self._metrics_worker.cancel()
        running = [self._metrics_thread] if self._metrics_thread is not None else []
        for thread in running + [t for t, _w in self._retiring]:
            if thread.isRunning():
                thread.quit()
                thread.wait()
        super().closeEvent(event)


def _same_folder(a: str, b: str) -> bool:
    """Whether two paths name the same folder, however they are written."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _clamp_to_available(
    width: int, height: int, x: int, y: int, avail: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """Clamp a window rect to a screen's available ``(x, y, w, h)`` work area.

    Caps the size to the work area and shifts the position so the whole window
    stays on-screen. Pure (no Qt) so it can be unit-tested.
    """
    ax, ay, aw, ah = avail
    width = min(width, aw)
    height = min(height, ah)
    x = min(max(x, ax), ax + aw - width)
    y = min(max(y, ay), ay + ah - height)
    return width, height, x, y


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
