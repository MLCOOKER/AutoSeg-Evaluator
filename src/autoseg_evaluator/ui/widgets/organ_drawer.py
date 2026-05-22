"""Organ drawer widget — one accordion section per organ.

Each drawer contains one or more patient sub-sections. A patient sub-section
holds the GT identifier and a list of test rows (one per AI source). The
visual structure mirrors the spec:

    ▼  Organ            N patients          [✂ Trunc ☐] [✕]
       ┌─ HN1   GT: manual.dcm (Varian)             [👁] [✕]
       │   ●●●●●●●○ 0.92  ProstateCTV  Limbus AI    [✕]
       │   ●●●●●●●○ 0.88  Prostate     MiM          [✕]
       └─ HN2   GT: manual.dcm (Varian)             [👁] [✕]
           ...
       [+ Add Selected →]   [Remove Red ✕]

In step 3 the widget renders and exposes signals; the actual matching /
add / remove plumbing is wired in step 4.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.ui.widgets.collapsible_box import CollapsibleBox
from autoseg_evaluator.ui.widgets.signal_bar import SignalBar


# Drag-and-drop MIME type — payload is a JSON list of organ dicts.
ORGAN_MIME_TYPE = "application/x-autoseg-organs"


# ---- Lightweight view models (display-only — full models live in step 4) -


@dataclass
class TestRow:
    """One AI-generated organ candidate for a patient's GT organ."""

    __test__ = False  # tell pytest not to collect this dataclass as a test class

    source_label: str
    organ_name: str
    rtstruct_sop_uid: str
    roi_number: int
    similarity: float  # 0.0–1.0
    below_threshold: bool = False
    # 'tg263' when both names resolved to the same canonical via the bundled
    # TG-263 dictionary; 'fuzzy' for string-similarity matches (incl. literal
    # equality not grounded in the dictionary); 'none' for zero-score matches.
    match_method: str = "fuzzy"


@dataclass
class PatientSubsection:
    """A patient's GT + list of test rows inside one organ drawer."""

    patient_id: str
    gt_rtstruct_sop_uid: str
    gt_rtstruct_filename: str
    gt_source_label: str
    gt_roi_number: int
    gt_roi_name: str
    tests: list[TestRow] = field(default_factory=list)


# ---- Drawer widget --------------------------------------------------------


class OrganDrawer(CollapsibleBox):
    """A drawer for one organ, holding 1+ patient sub-sections.

    Signals
    -------
    removeRequested()
        User clicked the [✕] on the drawer header.
    truncateChanged(bool)
        User toggled the truncate checkbox in the header.
    visualizeRequested(str)
        User clicked [👁] on a patient sub-section. Carries the patient_id.
    removePatientRequested(str)
        User clicked [✕] on a patient sub-section. Carries the patient_id.
    removeTestRequested(str, str, int)
        User clicked [✕] on a test row. Carries
        ``(patient_id, rtstruct_sop_uid, roi_number)``.
    addSelectedRequested()
        User clicked the "Add Selected" button (target is this drawer).
    removeRedRequested()
        User clicked "Remove Red" on this drawer.
    focusChanged(bool)
        Emitted with True when this drawer becomes the focused target
        (used in step 4 for focus-aware Add Selected routing).
    """

    removeRequested = Signal()
    truncateChanged = Signal(bool)
    gtComparisonChanged = Signal(bool)
    stapleConsensusChanged = Signal(bool)
    visualizeRequested = Signal(str)
    removePatientRequested = Signal(str)
    removeTestRequested = Signal(str, str, int)
    addSelectedRequested = Signal()
    removeRedRequested = Signal()
    focusChanged = Signal(bool)
    organsDropped = Signal(list)
    """Emitted when organ items are dropped onto this drawer.

    Payload is a list of ``(patient_id, rtstruct_sop_uid, roi_number, roi_name)`` tuples.
    """

    def __init__(self, organ_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._organ_name = organ_name
        self._patients: dict[str, PatientSubsection] = {}
        self._patient_widgets: dict[str, QWidget] = {}
        self._focused = False

        self._build_header()
        self._build_content()

        # Accept drag-and-drop of organ items from the loaded-contours tree.
        self.setAcceptDrops(True)

    # ---- Public API -------------------------------------------------------

    def organ_name(self) -> str:
        return self._organ_name

    def patient_ids(self) -> list[str]:
        return list(self._patients.keys())

    def set_focused(self, focused: bool) -> None:
        """Visually mark this drawer as the active drop/add target."""
        if self._focused == focused:
            return
        self._focused = focused
        self._apply_focus_styling()
        self._update_add_button_label()
        self.focusChanged.emit(focused)

    def _apply_focus_styling(self) -> None:
        """Apply a strong visual cue (border + tinted background) when focused."""
        container = self.headerContainer()
        if self._focused:
            container.setStyleSheet(
                "QFrame#CollapsibleBoxHeader {"
                "  background-color: rgba(33, 150, 243, 55);"
                "  border: 2px solid #2196F3;"
                "  border-radius: 4px;"
                "}"
            )
        else:
            container.setStyleSheet("")

    def is_focused(self) -> bool:
        return self._focused

    def truncate_enabled(self) -> bool:
        return self._truncate_check.isChecked()

    def set_truncate(self, enabled: bool) -> None:
        self._truncate_check.setChecked(enabled)

    def gt_comparison_enabled(self) -> bool:
        """Whether the drawer reports metrics for the designated-GT vs tests path."""
        return self._gt_compare_check.isChecked()

    def set_gt_comparison(self, enabled: bool) -> None:
        self._gt_compare_check.setChecked(enabled)

    def staple_consensus_enabled(self) -> bool:
        """Whether the drawer additionally reports metrics against a STAPLE consensus."""
        return self._staple_check.isChecked()

    def set_staple_consensus(self, enabled: bool) -> None:
        self._staple_check.setChecked(enabled)

    def add_patient(self, subsection: PatientSubsection) -> None:
        """Append a patient sub-section to the drawer."""
        if subsection.patient_id in self._patients:
            self.update_patient(subsection)
            return
        self._patients[subsection.patient_id] = subsection
        widget = self._build_patient_section(subsection)
        self._patient_widgets[subsection.patient_id] = widget
        # Insert above the action buttons row
        self._patients_layout.addWidget(widget)
        self._refresh_count()
        self._refresh_content_height()

    def update_patient(self, subsection: PatientSubsection) -> None:
        """Replace an existing patient sub-section with new data, preserving its position."""
        if subsection.patient_id not in self._patients:
            self.add_patient(subsection)
            return
        old_widget = self._patient_widgets.pop(subsection.patient_id)
        original_index = self._patients_layout.indexOf(old_widget)
        self._patients_layout.removeWidget(old_widget)
        old_widget.deleteLater()
        new_widget = self._build_patient_section(subsection)
        self._patient_widgets[subsection.patient_id] = new_widget
        self._patients[subsection.patient_id] = subsection
        if original_index >= 0:
            self._patients_layout.insertWidget(original_index, new_widget)
        else:
            self._patients_layout.addWidget(new_widget)
        self._refresh_content_height()

    def remove_patient(self, patient_id: str) -> None:
        if patient_id not in self._patients:
            return
        self._patients.pop(patient_id)
        widget = self._patient_widgets.pop(patient_id)
        self._patients_layout.removeWidget(widget)
        widget.deleteLater()
        self._refresh_count()
        self._refresh_content_height()

    def patient_count(self) -> int:
        return len(self._patients)

    def patient_subsection(self, patient_id: str) -> PatientSubsection | None:
        return self._patients.get(patient_id)

    def all_subsections(self) -> list[PatientSubsection]:
        return list(self._patients.values())

    def all_assigned_organs(self) -> list[tuple[str, str, int]]:
        """Return ``(patient_id, sop_uid, roi_number)`` for every GT and test in this drawer."""
        out: list[tuple[str, str, int]] = []
        for sub in self._patients.values():
            out.append((sub.patient_id, sub.gt_rtstruct_sop_uid, sub.gt_roi_number))
            for test in sub.tests:
                out.append((sub.patient_id, test.rtstruct_sop_uid, test.roi_number))
        return out

    def remove_poor_matches(self) -> list[tuple[str, str, int]]:
        """Strip every below-threshold test row from every patient sub-section.

        Returns the list of removed ``(patient_id, sop_uid, roi_number)`` tuples so
        callers can unmark them in the loaded-contours tree.
        """
        removed: list[tuple[str, str, int]] = []
        changed_patients: list[str] = []
        for pid, sub in self._patients.items():
            kept = [t for t in sub.tests if not t.below_threshold]
            if len(kept) == len(sub.tests):
                continue
            for t in sub.tests:
                if t.below_threshold:
                    removed.append((pid, t.rtstruct_sop_uid, t.roi_number))
            sub.tests = kept
            changed_patients.append(pid)
        # Re-render every changed sub-section by replacing its widget
        for pid in changed_patients:
            self.update_patient(self._patients[pid])
        return removed

    def remove_test(self, patient_id: str, rtstruct_sop_uid: str, roi_number: int) -> bool:
        sub = self._patients.get(patient_id)
        if sub is None:
            return False
        before = len(sub.tests)
        sub.tests = [
            t
            for t in sub.tests
            if not (t.rtstruct_sop_uid == rtstruct_sop_uid and t.roi_number == roi_number)
        ]
        if len(sub.tests) == before:
            return False
        self.update_patient(sub)
        return True

    # ---- Header construction ---------------------------------------------

    def _build_header(self) -> None:
        header = QWidget(self)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        name = QLabel(self._organ_name, header)
        font = name.font()
        font.setWeight(QFont.Weight.DemiBold)
        font.setPointSize(font.pointSize() + 1)
        name.setFont(font)
        layout.addWidget(name)

        self._count_label = QLabel("0 patients", header)
        self._count_label.setStyleSheet("color: #888;")
        layout.addWidget(self._count_label)

        layout.addStretch(1)

        self._truncate_check = QCheckBox("Truncate", header)
        self._truncate_check.setToolTip(
            "Zero out test-mask slices outside the GT's craniocaudal extent. "
            "Useful for structures the AI may legitimately extend beyond the "
            "manual GT (spinal cord, rectum). Applied before STAPLE if both "
            "modes are on."
        )
        self._truncate_check.toggled.connect(self.truncateChanged)
        layout.addWidget(self._truncate_check)

        self._gt_compare_check = QCheckBox("vs GT", header)
        self._gt_compare_check.setChecked(True)
        self._gt_compare_check.setToolTip(
            "Compute metrics for every test contour against the designated GT. "
            "Default mode."
        )
        self._gt_compare_check.toggled.connect(self.gtComparisonChanged)
        layout.addWidget(self._gt_compare_check)

        self._staple_check = QCheckBox("vs STAPLE", header)
        self._staple_check.setToolTip(
            "Also compute metrics against a STAPLE consensus contour derived "
            "from every contour in the drawer (GT + tests, treated as raters). "
            "Adds per-rater STAPLE sensitivity/specificity columns and a "
            "consensus-uncertainty summary row per patient."
        )
        self._staple_check.toggled.connect(self.stapleConsensusChanged)
        layout.addWidget(self._staple_check)

        self._remove_btn = QToolButton(header)
        self._remove_btn.setText("✕")
        self._remove_btn.setAutoRaise(True)
        self._remove_btn.setToolTip("Remove this drawer")
        self._remove_btn.clicked.connect(self.removeRequested)
        layout.addWidget(self._remove_btn)

        self.setHeaderWidget(header)

    # ---- Content construction --------------------------------------------

    def _build_content(self) -> None:
        outer = QVBoxLayout()
        outer.setContentsMargins(16, 2, 4, 4)
        outer.setSpacing(2)

        self._patients_layout = QVBoxLayout()
        self._patients_layout.setSpacing(2)
        outer.addLayout(self._patients_layout)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._add_selected_btn = QPushButton("+ Add Selected →", self)
        self._add_selected_btn.clicked.connect(self.addSelectedRequested)
        actions.addWidget(self._add_selected_btn)

        self._remove_red_btn = QPushButton("Remove Poor Matches", self)
        self._remove_red_btn.setToolTip(
            "Remove test rows below the similarity threshold in this drawer only."
        )
        self._remove_red_btn.clicked.connect(self.removeRedRequested)
        actions.addWidget(self._remove_red_btn)
        outer.addLayout(actions)

        self.setContentLayout(outer)
        self._update_add_button_label()

    def _build_patient_section(self, sub: PatientSubsection) -> QWidget:
        section = QFrame(self._content_area)
        section.setObjectName("PatientSubsection")
        section.setFrameShape(QFrame.Shape.StyledPanel)
        section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Compact label padding inside the section so rows are dense.
        section.setStyleSheet(
            "QFrame#PatientSubsection { padding: 0; }"
            "QFrame#PatientSubsection QLabel { padding: 0; margin: 0; }"
            "QFrame#PatientSubsection QToolButton { padding: 0; margin: 0; }"
        )

        layout = QVBoxLayout(section)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(0)

        # Header row for this patient: HN1   GT: manual.dcm (Varian)   [👁] [✕]
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)

        pid_label = QLabel(f"<b>{sub.patient_id}</b>")
        header_row.addWidget(pid_label)

        gt_label = QLabel(
            f"GT: <code>{sub.gt_rtstruct_filename}</code> · "
            f"<i>{sub.gt_roi_name}</i> · "
            f"<span style='color: #666'>[{sub.gt_source_label}]</span>"
        )
        gt_label.setTextFormat(Qt.TextFormat.RichText)
        header_row.addWidget(gt_label, stretch=1)

        viz_btn = QToolButton(section)
        viz_btn.setText("👁")
        viz_btn.setAutoRaise(True)
        viz_btn.setToolTip("Visualize contours for this patient")
        viz_btn.clicked.connect(lambda _=False, pid=sub.patient_id: self.visualizeRequested.emit(pid))
        header_row.addWidget(viz_btn)

        rm_btn = QToolButton(section)
        rm_btn.setText("✕")
        rm_btn.setAutoRaise(True)
        rm_btn.setToolTip("Remove this patient from the drawer")
        rm_btn.clicked.connect(
            lambda _=False, pid=sub.patient_id: self.removePatientRequested.emit(pid)
        )
        header_row.addWidget(rm_btn)

        layout.addLayout(header_row)

        # Test rows — sorted highest similarity first so poorest matches sink to the bottom
        if not sub.tests:
            empty_lbl = QLabel("  <i>(no test matches yet — auto-match or add manually)</i>", section)
            empty_lbl.setTextFormat(Qt.TextFormat.RichText)
            empty_lbl.setStyleSheet("color: #888;")
            layout.addWidget(empty_lbl)
        for test in sorted(sub.tests, key=lambda t: t.similarity, reverse=True):
            layout.addWidget(self._build_test_row(section, sub.patient_id, test))

        return section

    def _build_test_row(self, parent: QWidget, patient_id: str, test: TestRow) -> QWidget:
        row = QFrame(parent)
        row.setObjectName("TestRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 0, 2, 0)
        row_layout.setSpacing(4)

        bar = SignalBar(test.similarity, test.below_threshold, parent=row)
        row_layout.addWidget(bar)

        score = QLabel(f"{test.similarity:.2f}", row)
        score.setStyleSheet("font-family: monospace; font-weight: bold;")
        score.setMinimumWidth(40)
        row_layout.addWidget(score)

        if test.match_method == "tg263":
            badge = QLabel("TG-263", row)
            badge.setStyleSheet(
                "background-color: #E3F2FD; color: #0D47A1; "
                "border-radius: 3px; padding: 0 4px; font-size: 9pt; font-weight: bold;"
            )
            badge.setToolTip(
                "Both names resolved to the same TG-263 canonical — dictionary-grounded match."
            )
            row_layout.addWidget(badge)
        elif test.match_method == "fuzzy" and test.similarity > 0:
            badge = QLabel("fuzzy", row)
            badge.setStyleSheet(
                "background-color: #FFF3E0; color: #6D4C00; "
                "border-radius: 3px; padding: 0 4px; font-size: 9pt;"
            )
            badge.setToolTip(
                "Match found via string similarity (Levenshtein + cosine) — not in the "
                "TG-263 dictionary. Consider adding a replacement rule if this recurs."
            )
            row_layout.addWidget(badge)

        if test.below_threshold:
            warn = QLabel("⚠", row)
            warn.setStyleSheet("color: #d96b00;")
            row_layout.addWidget(warn)

        organ = QLabel(test.organ_name, row)
        organ.setMinimumWidth(140)
        row_layout.addWidget(organ)

        source = QLabel(f"<span style='color: #666'>{test.source_label}</span>", row)
        source.setTextFormat(Qt.TextFormat.RichText)
        row_layout.addWidget(source, stretch=1)

        rm = QToolButton(row)
        rm.setText("✕")
        rm.setAutoRaise(True)
        rm.setToolTip("Remove this test from the drawer")
        rm.clicked.connect(
            lambda _=False,
            pid=patient_id,
            sop=test.rtstruct_sop_uid,
            roi=test.roi_number: self.removeTestRequested.emit(pid, sop, roi)
        )
        row_layout.addWidget(rm)

        return row

    # ---- Helpers ---------------------------------------------------------

    def _refresh_count(self) -> None:
        n = len(self._patients)
        word = "patient" if n == 1 else "patients"
        self._count_label.setText(f"{n} {word}")

    def _refresh_content_height(self) -> None:
        """Recompute the open-height target so newly added/removed rows fit."""
        if not self.isExpanded():
            return
        self._content_area.setMaximumHeight(self._content_area.layout().totalSizeHint().height())

    def _update_add_button_label(self) -> None:
        if self._focused:
            self._add_selected_btn.setText(f"+ Add Selected → {self._organ_name}")
        else:
            self._add_selected_btn.setText("+ Add Selected →")

    # ---- Drag-and-drop ---------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(ORGAN_MIME_TYPE):
            event.acceptProposedAction()
            self._set_drop_highlight(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(ORGAN_MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_drop_highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._set_drop_highlight(False)
        if not event.mimeData().hasFormat(ORGAN_MIME_TYPE):
            event.ignore()
            return
        try:
            payload = json.loads(bytes(event.mimeData().data(ORGAN_MIME_TYPE)).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            event.ignore()
            return
        organs = [
            (
                str(d.get("patient_id", "")),
                str(d.get("rtstruct_sop_uid", "")),
                int(d.get("roi_number", 0) or 0),
                str(d.get("roi_name", "")),
            )
            for d in payload
            if d.get("patient_id") and d.get("rtstruct_sop_uid")
        ]
        if not organs:
            event.ignore()
            return
        self.organsDropped.emit(organs)
        event.acceptProposedAction()

    def _set_drop_highlight(self, on: bool) -> None:
        self.setProperty("dropHover", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)


