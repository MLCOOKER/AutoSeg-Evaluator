"""Tab 4 — Qualitative (Likert) Assessment.

A grader scores each matched contour on the 5-point MD Anderson Likert scale.
Each **grader has their own configuration**, chosen when they are added and
fixed thereafter:

* **mode** — *blinded* (one contour at a time, source hidden) or *transparent*
  (the active contour plus the other sources for that organ on the CT, with
  source labels + per-source visibility toggles),
* **include GT** — whether the manual/ground-truth contour is in their stack,
* **randomize** — drawer order vs a (group-aware) random order.

The tab has a setup panel (add graders + see their config + Start) and an
assessment panel (the multiplanar viewer in a swipe "card", the Likert buttons,
a progress bar, Back and Unlock). Scores are emitted via
:pysig:`qualitativeScored`; while an assessment runs,
:pysig:`assessmentLockChanged(True)` asks the main window to lock the other
tabs (no blinded-data leakage). The rating stack is built by
:func:`autoseg_evaluator.core.likert.build_rating_stack`; CT/mask loading reuses
the same data layer as the metrics worker.
"""

from __future__ import annotations

import html
import random
from collections.abc import Callable
from typing import Any

import numpy as np
import SimpleITK as sitk
from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.core.likert import (
    LIKERT_SCALE,
    QualitativeItem,
    build_rating_stack,
)
from autoseg_evaluator.core.masks import (
    extract_mask_for_roi,
    find_reference_image_series,
    read_image_series,
    read_rtstruct,
)
from autoseg_evaluator.core.staple import StapleConfig, staple_from_structures
from autoseg_evaluator.data.linkage import consensus_constituents
from autoseg_evaluator.ui.widgets._palette import GT_COLOR, color_for_index
from autoseg_evaluator.ui.widgets.multiplanar_viewer import MultiPlanarViewer, Overlay
from autoseg_evaluator.utils.timestamps import now_local

BLINDED = "blinded"
TRANSPARENT = "transparent"


def _default_config() -> dict[str, Any]:
    return {"mode": BLINDED, "include_gt": False, "randomize": False, "seed": 0}


class _CardHost(QWidget):
    """Holds a single free-positioned child (the swipe card), kept full-size."""

    def __init__(self, child: QWidget) -> None:
        super().__init__()
        self._child = child
        child.setParent(self)
        self.setMinimumSize(320, 320)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._child.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(event)


class _ConfigDialog(QDialog):
    """Modal prompt for one grader's assessment configuration."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Configuration for {name}")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>Assessment configuration for {name}</b>"))
        lay.addWidget(QLabel("This is fixed once the grader is added."))

        mode_box = QGroupBox("Mode")
        mb = QHBoxLayout(mode_box)
        self._blinded = QRadioButton("Blinded")
        self._blinded.setChecked(True)
        self._blinded.setToolTip("One contour at a time, source hidden.")
        self._transparent = QRadioButton("Transparent")
        self._transparent.setToolTip("All sources for the organ shown, with labels + toggles.")
        mb.addWidget(self._blinded)
        mb.addWidget(self._transparent)
        mb.addStretch(1)
        lay.addWidget(mode_box)

        self._include_gt = QCheckBox("Include the manual / ground-truth contour in the stack")
        self._randomize = QCheckBox("Randomize presentation order (group-aware: by organ)")
        lay.addWidget(self._include_gt)
        lay.addWidget(self._randomize)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def config(self) -> dict[str, Any]:
        return {
            "mode": BLINDED if self._blinded.isChecked() else TRANSPARENT,
            "include_gt": self._include_gt.isChecked(),
            "randomize": self._randomize.isChecked(),
            "seed": random.randrange(1_000_000),
        }


class QualitativeTab(QWidget):
    """The qualitative-assessment workflow tab."""

    qualitativeScored = Signal(dict)
    assessmentLockChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library: Any | None = None
        self._drawers_provider: Callable[[], list[dict[str, Any]]] = lambda: []

        # Superset of every ratable contour (include_gt=True), shared by all
        # graders; each grader's order is a permutation/subset of it.
        self._all_items: list[QualitativeItem] = []
        self._items_by_id: dict[str, QualitativeItem] = {}
        # Per-grader state
        self._rater_config: dict[str, dict[str, Any]] = {}  # name -> config
        self._rater_order: dict[str, list[str]] = {}  # name -> [item_id]
        self._rater_scores: dict[str, dict[str, int]] = {}  # name -> {item_id: score}
        # name -> {item_id: when it was scored}. Scoring can span several
        # sessions, so each score keeps its own time.
        self._rater_score_times: dict[str, dict[str, str]] = {}
        # Scores restored from a session whose contour is no longer among the
        # drawers: a drawer renamed or removed, a test row taken out, a folder
        # that changed. Kept, saved again, and shown as rows of their own. A
        # restore once dropped them, and the next save lost them for good.
        self._orphaned_scores: dict[str, dict[str, int]] = {}
        self._orphaned_times: dict[str, dict[str, str]] = {}
        self._rater_pos: dict[str, int] = {}  # name -> cursor
        self._active_rater: str | None = None
        self._started = False
        self._source_color: dict[str, int] = {}

        self._settings: dict[str, Any] = {}
        # Loading caches (bounded to a couple of patients)
        self._ct_cache: dict[tuple[str, str], sitk.Image] = {}
        self._mask_cache: dict[tuple[str, int], np.ndarray] = {}
        self._anim: QPropertyAnimation | None = None

        self._build_ui()

    # ---- Wiring from MainWindow ------------------------------------------

    def set_library(self, library: Any) -> None:
        self._library = library
        self._ct_cache.clear()
        self._mask_cache.clear()
        if not self._started:
            self._refresh_stack_summary()

    def set_settings(self, settings: dict[str, Any]) -> None:
        """The app settings, read for the STAPLE parameters a consensus is built with."""
        self._settings = settings

    def set_drawers_provider(self, provider: Callable[[], list[dict[str, Any]]]) -> None:
        self._drawers_provider = provider
        if not self._started:
            self._refresh_stack_summary()

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_setup_panel())
        self._stack.addWidget(self._build_assessment_panel())
        outer.addWidget(self._stack)

    def _build_setup_panel(self) -> QWidget:
        panel = QWidget()
        cols = QHBoxLayout(panel)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("<h2>Qualitative Assessment</h2>"))
        title_row.addStretch(1)
        help_btn = QPushButton("Help")
        help_btn.clicked.connect(self._on_help_clicked)
        title_row.addWidget(help_btn)
        lay.addLayout(title_row)
        lay.addWidget(
            QLabel(
                "Add one or more graders. Each grader's mode and options are chosen when "
                "they are added and cannot be changed afterwards. Highlight a grader and "
                "click Start to begin or resume them."
            )
        )

        rater_box = QGroupBox("Graders")
        rb = QVBoxLayout(rater_box)
        add_row = QHBoxLayout()
        self._rater_input = QLineEdit()
        self._rater_input.setPlaceholderText("Grader name…")
        self._rater_input.returnPressed.connect(self._on_add_rater)
        add_btn = QPushButton("Add grader…")
        add_btn.clicked.connect(self._on_add_rater)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._on_remove_rater)
        add_row.addWidget(self._rater_input, stretch=1)
        add_row.addWidget(add_btn)
        add_row.addWidget(remove_btn)
        rb.addLayout(add_row)
        self._rater_list = QListWidget()
        self._rater_list.setMaximumHeight(120)
        self._rater_list.itemSelectionChanged.connect(self._update_config_display)
        rb.addWidget(self._rater_list)
        lay.addWidget(rater_box)

        cfg_box = QGroupBox("Selected grader configuration")
        cb = QVBoxLayout(cfg_box)
        self._config_display = QLabel("<i>Select a grader to see their configuration.</i>")
        self._config_display.setWordWrap(True)
        cb.addWidget(self._config_display)
        lay.addWidget(cfg_box)

        self._summary_label = QLabel("")
        lay.addWidget(self._summary_label)
        start_row = QHBoxLayout()
        start_row.addStretch(1)
        self._start_btn = QPushButton("Start assessment ▶")
        self._start_btn.clicked.connect(self._on_start)
        start_row.addWidget(self._start_btn)
        lay.addLayout(start_row)
        lay.addStretch(1)

        cols.addWidget(left, stretch=3)
        cols.addWidget(self._build_likert_reference(), stretch=2)
        return panel

    def _build_likert_reference(self) -> QWidget:
        """Read-only reference table of the MD Anderson 5-point Likert scale."""
        box = QGroupBox("MD Anderson 5-point Likert scale")
        v = QVBoxLayout(box)
        table = QTableWidget(len(LIKERT_SCALE), 3)
        table.setHorizontalHeaderLabels(["Score", "Rating", "Definition"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setWordWrap(True)
        for r, point in enumerate(LIKERT_SCALE):  # already ordered 5 → 1
            score = QTableWidgetItem(str(point.value))
            score.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(r, 0, score)
            table.setItem(r, 1, QTableWidgetItem(point.label))
            table.setItem(r, 2, QTableWidgetItem(point.explanation))
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.resizeRowsToContents()
        v.addWidget(table, stretch=1)
        cite = QLabel(
            "<small>Definitions from Baroudi et al., “Automated Contouring and Planning in "
            "Radiation Therapy: What Is ‘Clinically Acceptable’?” <i>Cancers</i> 2023.</small>"
        )
        cite.setWordWrap(True)
        cite.setStyleSheet("color: #888;")
        v.addWidget(cite)
        return box

    def _build_assessment_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setSpacing(6)

        header = QHBoxLayout()
        self._header_label = QLabel("")
        header.addWidget(self._header_label, stretch=1)
        self._progress = QProgressBar()
        self._progress.setFormat("%v / %m rated")
        self._progress.setMinimumWidth(220)
        header.addWidget(self._progress)
        lay.addLayout(header)

        mid = QHBoxLayout()
        self._viewer = MultiPlanarViewer()
        self._card_host = _CardHost(self._viewer)
        mid.addWidget(self._card_host, stretch=1)
        self._source_panel = QGroupBox("Contours on this CT")
        self._source_panel_lay = QVBoxLayout(self._source_panel)
        self._source_panel.setMaximumWidth(260)
        self._source_panel.setVisible(False)
        mid.addWidget(self._source_panel)
        lay.addLayout(mid, stretch=1)

        likert_row = QHBoxLayout()
        likert_row.addWidget(QLabel("Score:"))
        self._likert_buttons: list[QPushButton] = []
        for point in sorted(LIKERT_SCALE, key=lambda p: p.value):
            btn = QPushButton(f"{point.value}\n{point.label}")
            btn.setToolTip(f"{point.value} — {point.label}: {point.explanation}")
            btn.setMinimumHeight(48)
            btn.clicked.connect(lambda _checked=False, v=point.value: self._apply_score(v))
            likert_row.addWidget(btn, stretch=1)
            self._likert_buttons.append(btn)
        lay.addLayout(likert_row)

        nav = QHBoxLayout()
        self._back_btn = QPushButton("◀ Back")
        self._back_btn.clicked.connect(self._go_back)
        nav.addWidget(self._back_btn)
        self._status_label = QLabel("")
        nav.addWidget(self._status_label, stretch=1)
        unlock_btn = QPushButton("🔓 Unlock tabs / exit")
        unlock_btn.setToolTip(
            "Leave the assessment to access other tabs. Progress is kept; you can resume."
        )
        unlock_btn.clicked.connect(self._on_unlock)
        nav.addWidget(unlock_btn)
        lay.addLayout(nav)
        return panel

    # ---- Setup-panel handlers --------------------------------------------

    def _on_help_clicked(self) -> None:
        QMessageBox.information(
            self.window(),
            "Qualitative Assessment — workflow",
            "<p><b>What this tab does</b></p>"
            "<p>Each matched contour is scored on the 5-point MD Anderson Likert scale "
            "(5 = use-as-is … 1 = unusable; see the reference table on the right).</p>"
            "<p><b>How to use it</b></p>"
            "<ol>"
            "<li><b>Add a grader</b> — you'll be prompted for that grader's configuration "
            "(it is fixed once added):"
            "<ul>"
            "<li><i>Blinded</i> — one contour at a time, source hidden.</li>"
            "<li><i>Transparent</i> — every source for the organ shown on the CT with "
            "labels and per-source visibility toggles. The contour being graded is "
            "named below the viewer and in bold in the list.</li>"
            "<li><i>Include GT</i> — also rate the manual / ground-truth contour.</li>"
            "<li><i>Randomize</i> — random presentation order (kept organ-by-organ).</li>"
            "</ul></li>"
            "<li>Highlight a grader to see their configuration, then click <b>Start</b>.</li>"
            "<li>Score each contour with the 1–5 buttons; use <b>Back</b> to revisit (scores "
            "are kept). The image swipes to the next contour after each score.</li>"
            "<li>Viewer controls: cycle <b>Axial / Coronal / Sagittal</b>, ctrl+scroll to "
            "zoom, the <b>Level/Window</b> sliders for contrast, and contour opacity / "
            "thickness.</li>"
            "</ol>"
            "<p><b>While grading, the other tabs are locked</b> to avoid blinded-data "
            "leakage — click <b>Unlock tabs / exit</b> to leave; your progress is kept.</p>"
            "<p><b>Multiple graders</b> are scored in turn; each grader's scores appear in "
            "their own <i>Likert</i> column in the Results tab. <b>Save Session</b> at any "
            "time and resume later — graders, their configs and all scores are restored.</p>",
        )

    def _on_add_rater(self) -> None:
        name = self._rater_input.text().strip()
        if not name:
            return
        if name in self._current_raters():
            self._rater_input.clear()
            QMessageBox.information(self, "Add grader", f"A grader named {name!r} already exists.")
            return
        dlg = _ConfigDialog(name, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return  # cancelled — grader not added
        self._rater_config[name] = dlg.config()
        self._rater_list.addItem(name)
        self._rater_input.clear()
        self._select_rater_in_list(name)
        self._update_config_display()
        self._refresh_stack_summary()

    def _on_remove_rater(self) -> None:
        for item in self._rater_list.selectedItems():
            name = item.text()
            self._rater_list.takeItem(self._rater_list.row(item))
            self._rater_config.pop(name, None)
            self._rater_order.pop(name, None)
            self._rater_scores.pop(name, None)
            self._rater_score_times.pop(name, None)
            self._orphaned_scores.pop(name, None)
            self._orphaned_times.pop(name, None)
            self._rater_pos.pop(name, None)
        self._update_config_display()
        self._refresh_stack_summary()

    def _current_raters(self) -> list[str]:
        return [self._rater_list.item(i).text() for i in range(self._rater_list.count())]

    def _selected_rater(self) -> str | None:
        items = self._rater_list.selectedItems()
        return items[0].text() if items else None

    def _select_rater_in_list(self, name: str) -> None:
        for i in range(self._rater_list.count()):
            if self._rater_list.item(i).text() == name:
                self._rater_list.setCurrentRow(i)
                return

    def _update_config_display(self) -> None:
        rater = self._selected_rater()
        cfg = self._rater_config.get(rater) if rater else None
        if not cfg:
            self._config_display.setText("<i>Select a grader to see their configuration.</i>")
            return
        mode = "Blinded" if cfg["mode"] == BLINDED else "Transparent"
        gt = "included" if cfg["include_gt"] else "excluded"
        order = "randomized" if cfg["randomize"] else "drawer order"
        scored = len(self._rater_scores.get(rater, {}))
        total = len(self._rater_order.get(rater, []))
        progress = f"{scored} / {total} rated" if total else "not started"
        self._config_display.setText(
            f"<b>{rater}</b><br>Mode: {mode}<br>Ground truth: {gt}<br>"
            f"Order: {order}<br>Progress: {progress}"
        )

    def _refresh_stack_summary(self) -> None:
        n = len(build_rating_stack(self._drawers_provider() or [], include_gt=True))
        raters = self._current_raters()
        msg = f"<b>{len(raters)}</b> grader(s) configured · <b>{n}</b> contour(s) available."
        if not (n and raters):
            msg += "  <i>Add at least one grader and match contours first.</i>"
        elif self._started:
            msg += "  <i>Assessment in progress — highlight a grader and Start to resume.</i>"
        self._summary_label.setText(msg)
        self._start_btn.setText(
            "Start / resume assessment ▶" if self._started else "Start assessment ▶"
        )
        self._start_btn.setEnabled(bool(n) and bool(raters))

    # ---- Start / lifecycle -----------------------------------------------

    def _active_mode(self) -> str:
        return (self._rater_config.get(self._active_rater) or {}).get("mode", BLINDED)

    def _on_start(self) -> None:
        raters = self._current_raters()
        if not raters:
            QMessageBox.information(self, "Qualitative Assessment", "Add at least one grader.")
            return
        superset = build_rating_stack(self._drawers_provider() or [], include_gt=True)
        if not superset:
            QMessageBox.information(self, "Qualitative Assessment", "Match some contours first.")
            return
        # (Re)build the shared superset + colours from the current drawers.
        self._all_items = superset
        self._items_by_id = {it.item_id: it for it in superset}
        self._assign_source_colors(superset)
        # A score held aside at restore rejoins its grader once its contour is
        # back among the drawers, so it is not asked for again.
        for rater, held in list(self._orphaned_scores.items()):
            times = self._orphaned_times.get(rater, {})
            for item_id in [i for i in held if i in self._items_by_id]:
                self._rater_scores.setdefault(rater, {})[item_id] = held.pop(item_id)
                if item_id in times:
                    self._rater_score_times.setdefault(rater, {})[item_id] = times.pop(item_id)
        selected = self._selected_rater() or (self._active_rater if self._started else None)
        if selected not in raters:
            selected = raters[0]
        self._ensure_rater_built(selected)
        self._active_rater = selected
        self._started = True
        self._enter_assessment()

    def _ensure_rater_built(self, rater: str) -> None:
        """Build a grader's order from their own config (kept if already built)."""
        if rater in self._rater_order:
            return
        cfg = self._rater_config.get(rater) or _default_config()
        order_items = build_rating_stack(
            self._drawers_provider() or [],
            include_gt=bool(cfg["include_gt"]),
            randomize=bool(cfg["randomize"]),
            seed=int(cfg.get("seed", 0) or 0),
        )
        self._rater_order[rater] = [it.item_id for it in order_items]
        self._rater_scores.setdefault(rater, {})
        self._rater_pos.setdefault(rater, 0)

    def _assign_source_colors(self, items: list[QualitativeItem]) -> None:
        self._source_color = {}
        for it in items:
            if not it.is_gt and it.source_label not in self._source_color:
                self._source_color[it.source_label] = len(self._source_color)

    def _enter_assessment(self) -> None:
        self._stack.setCurrentIndex(1)
        self._source_panel.setVisible(self._active_mode() == TRANSPARENT)
        self._update_progress()
        self.assessmentLockChanged.emit(True)
        self._load_current(animate=None)

    def _on_unlock(self) -> None:
        resp = QMessageBox.question(
            self,
            "Unlock tabs",
            "Leave the assessment and unlock the other tabs?\n\n"
            "Your progress is saved — you can resume from where you left off.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.assessmentLockChanged.emit(False)
        self._stack.setCurrentIndex(0)
        self._update_config_display()
        self._refresh_stack_summary()

    # ---- Rating ----------------------------------------------------------

    def _current_item(self) -> QualitativeItem | None:
        rater = self._active_rater
        if rater is None:
            return None
        order = self._rater_order.get(rater, [])
        pos = self._rater_pos.get(rater, 0)
        if 0 <= pos < len(order):
            return self._items_by_id.get(order[pos])
        return None

    def _apply_score(self, score: int) -> None:
        item = self._current_item()
        rater = self._active_rater
        if item is None or rater is None:
            return
        scores = self._rater_scores[rater]
        was_complete = len(scores) == len(self._rater_order[rater])
        scores[item.item_id] = int(score)
        scored_at = now_local()
        self._rater_score_times.setdefault(rater, {})[item.item_id] = scored_at
        self.qualitativeScored.emit(
            {
                "patient_id": item.patient_id,
                "drawer": item.organ_name,
                "source_label": item.source_label,
                "rtstruct_sop_uid": item.rtstruct_sop_uid,
                "roi_name": item.roi_name,
                "roi_number": item.roi_number,
                "is_gt": item.is_gt,
                "rater": rater,
                "score": int(score),
                "blinded": self._active_mode() == BLINDED,
                "scored_at": scored_at,
            }
        )
        now_complete = len(scores) == len(self._rater_order[rater])
        if now_complete and not was_complete:
            self._update_progress()
            self._on_rater_complete()
            return
        self._rater_pos[rater] = min(self._rater_pos[rater] + 1, len(self._rater_order[rater]) - 1)
        self._update_progress()
        self._load_current(animate="left")

    def _go_back(self) -> None:
        rater = self._active_rater
        if rater is None:
            return
        if self._rater_pos[rater] <= 0:
            self._status_label.setText("Already at the first contour.")
            return
        self._rater_pos[rater] -= 1
        self._update_progress()
        self._load_current(animate="right")

    def _on_rater_complete(self) -> None:
        raters = self._current_raters()
        rater = self._active_rater
        idx = raters.index(rater) if rater in raters else -1
        if 0 <= idx < len(raters) - 1:
            nxt = raters[idx + 1]
            QMessageBox.information(
                self, "Grader complete", f"{rater} has finished. Starting {nxt}."
            )
            self._ensure_rater_built(nxt)
            self._active_rater = nxt
            self._enter_assessment()
        else:
            QMessageBox.information(
                self, "Assessment complete", "All graders have finished. Unlocking the tabs."
            )
            self.assessmentLockChanged.emit(False)
            self._stack.setCurrentIndex(0)
            self._update_config_display()
            self._refresh_stack_summary()

    def _update_progress(self) -> None:
        rater = self._active_rater
        if rater is None:
            return
        total = len(self._rater_order.get(rater, []))
        rated = len(self._rater_scores.get(rater, {}))
        self._progress.setRange(0, total)
        self._progress.setValue(rated)
        mode = "Blinded" if self._active_mode() == BLINDED else "Transparent"
        self._header_label.setText(f"<b>Grader:</b> {rater} &nbsp;·&nbsp; <b>Mode:</b> {mode}")

    # ---- Loading + rendering ---------------------------------------------

    def _load_current(self, *, animate: str | None) -> None:
        item = self._current_item()
        if item is None:
            return
        try:
            self._render_item(item)
        except Exception as exc:  # noqa: BLE001 — never let a bad load break the run
            self._status_label.setText(f"Could not load contour: {exc}")
            return
        prior = self._rater_scores.get(self._active_rater or "", {}).get(item.item_id)
        status = f"Patient {item.patient_id} · {item.organ_name}" + (
            f" · previous score {prior}" if prior is not None else ""
        )
        if self._active_mode() == TRANSPARENT:
            # Every outline on screen is already labelled with its source, so
            # naming the one being graded reveals nothing new. It removes doubt
            # about which outline the score is for: with several sources in
            # similar colours, a score given to the wrong one could never be
            # found afterwards. Blinded mode shows one contour and names none.
            self._status_label.setText(
                f"<b>Rating: {html.escape(_contour_label(item))}</b>"
                f" &nbsp;·&nbsp; {html.escape(status)}"
            )
        else:
            self._status_label.setText(status)
        if animate in ("left", "right"):
            self._swipe(animate)

    def _render_item(self, item: QualitativeItem) -> None:
        ct = self._get_ct(item.patient_id, item.rtstruct_sop_uid)
        if ct is None:
            raise RuntimeError("reference CT not found")
        if self._active_mode() == TRANSPARENT:
            # Show only the contours for THIS organ on this CT (every source /
            # vendor for the organ), not every organ the patient has.
            siblings = [
                it
                for it in self._all_items
                if it.patient_id == item.patient_id and it.organ_name == item.organ_name
            ]
        else:
            siblings = [item]
        overlays: list[Overlay] = []
        for it in siblings:
            mask = self._get_mask(it, ct)
            if mask is None:
                continue
            color = (
                GT_COLOR
                if it.is_gt
                else color_for_index(self._source_color.get(it.source_label, 0))
            )
            overlays.append(
                Overlay(
                    label=_contour_label(it),
                    color=color,
                    mask=mask,
                    active=it.item_id == item.item_id,
                )
            )
        self._viewer.set_data(ct, overlays)
        if self._active_mode() == TRANSPARENT:
            self._rebuild_source_panel(overlays)

    def _rebuild_source_panel(self, overlays: list[Overlay]) -> None:
        while self._source_panel_lay.count():
            w = self._source_panel_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for idx, ov in enumerate(overlays):
            cb = QCheckBox(ov.label + ("  (rating)" if ov.active else ""))
            cb.setChecked(True)
            # The contour being graded stands out from the others in the list.
            # Set in the widget's own style sheet, which the theme cannot
            # override.
            weight = " font-weight: bold;" if ov.active else ""
            cb.setStyleSheet(f"color: {ov.color};{weight}")
            cb.toggled.connect(lambda checked, i=idx: self._viewer.set_overlay_visible(i, checked))
            self._source_panel_lay.addWidget(cb)
        self._source_panel_lay.addStretch(1)

    def _swipe(self, direction: str) -> None:
        w = self._card_host.width() or 1
        start = QPoint(-w if direction == "right" else w, 0)
        self._viewer.move(start)
        self._anim = QPropertyAnimation(self._viewer, b"pos", self)
        self._anim.setDuration(220)
        self._anim.setStartValue(start)
        self._anim.setEndValue(QPoint(0, 0))
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    # ---- Data loading + cache --------------------------------------------

    def _get_ct(self, patient_id: str, gt_sop_uid: str) -> sitk.Image | None:
        if self._library is None:
            return None
        series = find_reference_image_series(self._library, patient_id, gt_sop_uid)
        if series is None:
            return None
        # Keyed by the resolved image series, not by patient or folder: a
        # patient with two planning CTs would otherwise have the first one shown
        # for every structure set that followed, and a folder can hold both.
        key = (patient_id, series.series_instance_uid)
        if key in self._ct_cache:
            return self._ct_cache[key]
        if len(self._ct_cache) >= 2:
            self._ct_cache.pop(next(iter(self._ct_cache)))
            self._mask_cache.clear()
        image = read_image_series(series)
        self._ct_cache[key] = image
        return image

    def _get_mask(self, item: QualitativeItem, ct: sitk.Image) -> np.ndarray | None:
        key = (item.rtstruct_sop_uid, item.roi_number)
        if key in self._mask_cache:
            return self._mask_cache[key]
        entry = self._rtstruct_entry(item.patient_id, item.rtstruct_sop_uid)
        if entry is None:
            return None
        try:
            if entry.is_synthetic_consensus:
                # A consensus ground truth has no file: built from its raters
                # the way Compute builds it, or the rater would be shown nothing.
                mask_img = self._consensus_mask(item.patient_id, entry, item.roi_number, ct)
            else:
                rtss = read_rtstruct(entry.file_path)
                mask_img = extract_mask_for_roi(ct, rtss, item.roi_number)
        except Exception:  # noqa: BLE001
            return None
        if mask_img is None:
            return None
        arr = sitk.GetArrayFromImage(mask_img) > 0
        self._mask_cache[key] = arr
        return arr

    def _consensus_mask(self, patient_id: str, entry: Any, roi_number: int, ct: sitk.Image):
        structures = []
        for sop, roi in consensus_constituents(self._library, patient_id, entry, roi_number):
            path = self._rtstruct_path(patient_id, sop)
            if path:
                structures.append((read_rtstruct(path), roi))
        config = StapleConfig.from_dict(self._settings.get("staple", {}) or {})
        result = staple_from_structures(ct, structures, config)
        return result.consensus_mask if result is not None else None

    def _rtstruct_entry(self, patient_id: str, sop_uid: str) -> Any | None:
        if self._library is None:
            return None
        patient = self._library.patients.get(patient_id)
        if patient is None:
            return None
        for ctx in patient.contexts:
            for rtss in ctx.rtstructs:
                if rtss.sop_instance_uid == sop_uid:
                    return rtss
        return None

    def _rtstruct_path(self, patient_id: str, sop_uid: str) -> str | None:
        entry = self._rtstruct_entry(patient_id, sop_uid)
        return entry.file_path if entry is not None else None

    # ---- Session save / restore ------------------------------------------

    def session_state(self) -> dict[str, Any]:
        raters = self._current_raters()
        return {
            "started": self._started,
            "active_rater": self._active_rater,
            "raters": raters,
            "rater_config": {r: dict(self._rater_config.get(r, {})) for r in raters},
            "per_rater": {
                r: {
                    "order": list(self._rater_order.get(r, [])),
                    # Scores whose contour is no longer in the drawers are saved
                    # again with the rest, so a later session can still use them.
                    "scores": {
                        **self._orphaned_scores.get(r, {}),
                        **self._rater_scores.get(r, {}),
                    },
                    "scored_at": {
                        **self._orphaned_times.get(r, {}),
                        **self._rater_score_times.get(r, {}),
                    },
                    "index": int(self._rater_pos.get(r, 0)),
                }
                for r in raters
            },
        }

    def orphaned_score_count(self) -> int:
        """Scores kept from a session whose contour is no longer in the drawers."""
        return sum(len(scores) for scores in self._orphaned_scores.values())

    def has_scores(self) -> bool:
        return any(self._rater_scores.values()) or any(self._orphaned_scores.values())

    def reset(self) -> None:
        """Forget every grader and score, for loading a different cohort."""
        self._rater_list.clear()
        self._rater_config = {}
        self._rater_order = {}
        self._rater_scores = {}
        self._rater_score_times = {}
        self._orphaned_scores = {}
        self._orphaned_times = {}
        self._rater_pos = {}
        self._all_items = []
        self._items_by_id = {}
        self._active_rater = None
        self._started = False
        self._stack.setCurrentIndex(0)
        self._update_config_display()
        self._refresh_stack_summary()

    def apply_session_state(self, data: dict[str, Any]) -> None:
        """Restore a saved qualitative run: graders, their fixed configs, scores.

        Repopulates the setup panel (graders + per-grader config), restores each
        grader's scores / position, and re-emits every saved score so the prior
        grades reappear in the Results tab. Lands on the setup panel (unlocked)
        so the user can highlight any grader and Start to resume.
        """
        if not data:
            return
        raters = list(data.get("raters", []) or [])
        configs = data.get("rater_config", {}) or {}

        # Restore graders + their fixed configurations.
        self._rater_list.clear()
        self._rater_config = {}
        for name in raters:
            self._rater_list.addItem(name)
            cfg = configs.get(name) or _default_config()
            self._rater_config[name] = {
                "mode": str(cfg.get("mode", BLINDED)),
                "include_gt": bool(cfg.get("include_gt", False)),
                "randomize": bool(cfg.get("randomize", False)),
                "seed": int(cfg.get("seed", 0) or 0),
            }

        per_rater = data.get("per_rater", {}) or {}
        self._rater_order = {}
        self._rater_scores = {}
        self._rater_score_times = {}
        self._orphaned_scores = {}
        self._orphaned_times = {}
        self._rater_pos = {}

        superset = (
            build_rating_stack(self._drawers_provider() or [], include_gt=True)
            if data.get("started")
            else []
        )
        self._all_items = superset
        self._items_by_id = {it.item_id: it for it in superset}
        self._assign_source_colors(superset)
        # Every saved score is kept. Those whose contour is among the restored
        # drawers resume as before; the rest are held aside rather than dropped,
        # which is what once happened when drawers failed to restore.
        for rater in raters:
            rs = per_rater.get(rater, {}) or {}
            times = {str(k): str(v) for k, v in (rs.get("scored_at", {}) or {}).items()}
            for item_id, value in (rs.get("scores", {}) or {}).items():
                if item_id in self._items_by_id:
                    self._rater_scores.setdefault(rater, {})[item_id] = int(value)
                    if item_id in times:
                        self._rater_score_times.setdefault(rater, {})[item_id] = times[item_id]
                else:
                    self._orphaned_scores.setdefault(rater, {})[item_id] = int(value)
                    if item_id in times:
                        self._orphaned_times.setdefault(rater, {})[item_id] = times[item_id]

        if not superset:
            self._started = bool(data.get("started")) and bool(raters)
            self._update_config_display()
            self._refresh_stack_summary()
            self._emit_restored_scores()
            return

        for rater in raters:
            rs = per_rater.get(rater, {}) or {}
            order = [iid for iid in rs.get("order", []) if iid in self._items_by_id]
            if not order:
                cfg = self._rater_config[rater]
                order = [
                    it.item_id
                    for it in build_rating_stack(
                        self._drawers_provider() or [],
                        include_gt=cfg["include_gt"],
                        randomize=cfg["randomize"],
                        seed=cfg["seed"],
                    )
                ]
            self._rater_order[rater] = order
            self._rater_scores.setdefault(rater, {})
            self._rater_pos[rater] = max(0, min(int(rs.get("index", 0) or 0), len(order) - 1))

        active = str(data.get("active_rater") or "")
        if active not in raters:
            active = raters[0] if raters else ""
        self._active_rater = active or None
        self._started = bool(raters)
        if self._active_rater:
            self._select_rater_in_list(self._active_rater)
        self._stack.setCurrentIndex(0)
        self._update_config_display()
        self._refresh_stack_summary()
        # Re-emit saved scores so the Results tab shows the previously graded
        # contours again (they aren't stored in the metric results).
        self._emit_restored_scores()

    def _emit_restored_scores(self) -> None:
        for rater, scores in self._rater_scores.items():
            blinded = self._rater_config.get(rater, {}).get("mode", BLINDED) == BLINDED
            times = self._rater_score_times.get(rater, {})
            for item_id, score in scores.items():
                item = self._items_by_id.get(item_id)
                if item is None:
                    continue
                self.qualitativeScored.emit(
                    {
                        "patient_id": item.patient_id,
                        "drawer": item.organ_name,
                        "source_label": item.source_label,
                        "rtstruct_sop_uid": item.rtstruct_sop_uid,
                        "roi_name": item.roi_name,
                        "roi_number": item.roi_number,
                        "is_gt": item.is_gt,
                        "rater": rater,
                        "score": int(score),
                        "blinded": blinded,
                        # Blank for scores saved before times were recorded.
                        "scored_at": times.get(item_id, ""),
                    }
                )
        # Scores whose contour is no longer in the drawers still reach the
        # results, as rows of their own, with what the saved identity and the
        # loaded data can still say about the contour.
        for rater, scores in self._orphaned_scores.items():
            blinded = self._rater_config.get(rater, {}).get("mode", BLINDED) == BLINDED
            times = self._orphaned_times.get(rater, {})
            for item_id, score in scores.items():
                patient_id, drawer, sop_uid, roi_number = _split_item_id(item_id)
                entry = self._rtstruct_entry(patient_id, sop_uid)
                roi_name = ""
                if entry is not None:
                    roi_name = next(
                        (o.roi_name for o in entry.organs if int(o.roi_number) == roi_number), ""
                    )
                self.qualitativeScored.emit(
                    {
                        "patient_id": patient_id,
                        "drawer": drawer,
                        "source_label": entry.source_label if entry is not None else "",
                        "rtstruct_sop_uid": sop_uid,
                        "roi_name": roi_name,
                        "roi_number": roi_number,
                        "is_gt": False,
                        "rater": rater,
                        "score": int(score),
                        "blinded": blinded,
                        "scored_at": times.get(item_id, ""),
                    }
                )


def _contour_label(item: QualitativeItem) -> str:
    """``VendorA — Parotid_L``, or ``ground truth — Parotid_L`` for the GT.

    One wording for the viewer's legend, the side panel and the transparent
    mode's status line, so the name a grader reads is the name they see drawn.
    """
    source = "ground truth" if item.is_gt else item.source_label
    return f"{source} — {item.roi_name}"


def _split_item_id(item_id: str) -> tuple[str, str, str, int]:
    """``patient|drawer|structure set|roi`` back into its parts.

    The inverse of :attr:`QualitativeItem.item_id`. The structure set and ROI
    are split from the right, since a drawer name is free text.
    """
    head, _, roi = str(item_id).rpartition("|")
    head, _, sop = head.rpartition("|")
    patient, _, drawer = head.partition("|")
    try:
        number = int(roi)
    except ValueError:
        number = 0
    return patient, drawer, sop, number
