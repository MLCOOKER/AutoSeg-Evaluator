"""Tab 2 — Build Consensus GT (Optional).

This tab lets the user combine 2+ RTSSes that share a source label (e.g. multiple
manual contours from different clinicians, or repeat exports from the same TPS)
into a single STAPLE-derived synthetic ground truth, *before* the GT/test
matching workflow on the Match Contours tab.

The tab is greyed out unless the library contains at least one patient with
two or more RTSSes sharing a source label. When active, the user:

1. Selects an eligible group (patient + source-label combination) from the
   left panel.
2. Reviews the auto-matched organ drawers on the right. Each drawer lists
   the 2+ contributing ROIs (one from each RTSS in the group), grouped by
   the existing Levenshtein+cosine matcher + TG-263 dictionary.
3. Edits the drawers if the auto-match got something wrong (drag-drop and
   per-row remove buttons, same UX as Match Contours).
4. Clicks **Generate Consensus GT** — a synthetic RTSS entry is registered
   in the library with manufacturer ``STAPLE Consensus``. The Match
   Contours tab can then designate this synthetic entry as the GT just
   like any other RTSS.

Single-rater organs (only one of the RTSSes contains them) are excluded
silently per the spec — STAPLE requires at least 2 raters per organ.

The tab does not replace Tab 3's existing per-drawer "vs STAPLE" mode — the
two features answer different questions:

* This tab: "Build a consensus ground truth from N manual contours."
* Tab 3 STAPLE: "Treat all contours (GT + tests) as raters and report each
  against the resulting consensus."

Both can co-exist in different drawers; only mixing them on the same drawer
is disallowed (handled by Match Contours).
"""

from __future__ import annotations

import csv
import gc
from collections import defaultdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QAction, QDrag, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.core.masks import (
    extract_mask_for_roi,
    find_reference_image_folder,
    read_dicom_image,
    read_rtstruct,
)
from autoseg_evaluator.core.matching import ReplacementRule, similarity
from autoseg_evaluator.core.metrics import compute_geometric_metrics
from autoseg_evaluator.data.metadata import (
    MetadataLibrary,
    OrganEntry,
    RTSTRUCTEntry,
)
from autoseg_evaluator.data.results import metric_display_label
from autoseg_evaluator.data.synonyms import flatten_synonyms, load_synonyms
from autoseg_evaluator.utils.paths import synonyms_path

# Source label assigned to every synthetic consensus RTSS we generate.
CONSENSUS_SOURCE_LABEL = "STAPLE Consensus"

# Default per-patient organ-matching similarity threshold.
_DEFAULT_THRESHOLD = 0.60

# Drag-and-drop payload type for moving an unmatched ROI onto an organ bucket.
_CONSENSUS_ROI_MIME = "application/x-autoseg-consensus-roi"


def _encode_roi(sop: str, roi: int) -> bytes:
    return f"{sop}\x1f{roi}".encode()


def _decode_roi(raw: bytes) -> tuple[str, int]:
    sop, roi = bytes(raw).decode().split("\x1f")
    return sop, int(roi)


class _RoiDragLabel(QFrame):
    """An unmatched-ROI row that can be dragged onto an organ bucket."""

    def __init__(self, sop: str, roi: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sop = sop
        self._roi = roi
        self._press_pos = None

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._press_pos is None:
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < (
            QApplication.startDragDistance()
        ):
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_CONSENSUS_ROI_MIME, _encode_roi(self._sop, self._roi))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self._press_pos = None


class _OrganBucketFrame(QFrame):
    """An organ bucket that accepts dropped unmatched ROIs."""

    _BASE_STYLE = "QFrame#ConsensusOrganRow { border: 1px solid #ddd; border-radius: 3px; }"
    _HOVER_STYLE = (
        "QFrame#ConsensusOrganRow { border: 2px solid #4a90d9; "
        "border-radius: 3px; background: #eef5fc; }"
    )

    def __init__(self, on_drop, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_drop = on_drop  # callable(sop: str, roi: int)
        self.setObjectName("ConsensusOrganRow")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(self._BASE_STYLE)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(_CONSENSUS_ROI_MIME):
            event.acceptProposedAction()
            self.setStyleSheet(self._HOVER_STYLE)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.setStyleSheet(self._BASE_STYLE)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.setStyleSheet(self._BASE_STYLE)
        data = event.mimeData().data(_CONSENSUS_ROI_MIME)
        if not data:
            return
        sop, roi = _decode_roi(data)
        self._on_drop(sop, roi)
        event.acceptProposedAction()


class BuildConsensusTab(QWidget):
    """Generate synthetic STAPLE-consensus RTSSes from same-source-label groups.

    Emits :attr:`consensusGenerated` after registering new synthetic RTSSes
    in the library so that downstream tabs (Match Contours, Compute) can
    refresh their tree views.
    """

    consensusGenerated = Signal()
    # Emitted (with the new list of observer labels) when the user changes the
    # observer selection, so MainWindow can persist it to settings.json.
    observerLabelsChanged = Signal(list)

    def __init__(
        self, settings: dict[str, Any] | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._settings = settings or {}
        self._library: MetadataLibrary | None = None
        # v2.4 model: the consensus unit is the PATIENT. Each manual observer
        # is identified by a DISTINCT source label (assigned in Tab 1); the
        # user selects which labels are observers (``consensus_observer_labels``
        # in settings) and the group for a patient = its RTSSes whose source
        # label is in that set. So group_drawers is keyed by patient_id:
        #   group_drawers[patient_id] = {organ_name: [(sop_uid, roi_number, roi_name), ...]}
        self._group_drawers: dict[str, dict[str, list[tuple[str, int, str]]]] = {}
        # Per-patient "unmatched" ROIs — contours that didn't cluster into a
        # 2+ rater bucket. Surfaced in a tray so the user can manually assign
        # them (Phase 3). Shape mirrors a bucket: [(sop_uid, roi_number, name)].
        self._unmatched: dict[str, list[tuple[str, int, str]]] = {}
        # Patients the user has manually edited (moved an ROI). Such patients
        # are LOCKED from threshold re-clustering until "Reset to auto-match".
        self._manual_edited: set[str] = set()
        # Per-patient match threshold — each patient keeps its own value. The
        # footer spinbox edits the currently-selected patient's threshold.
        self._patient_thresholds: dict[str, float] = {}
        # Per-bucket "representative" name — the seed organ name each bucket was
        # clustered against. The displayed match score is each rater's
        # similarity to THIS (the actual clustering decision), not to the
        # most-frequent display name. Keyed: pid → {bucket_display_name → rep}.
        self._bucket_representatives: dict[str, dict[str, str]] = {}
        # The patient currently shown in the drawer panel (for re-render).
        self._current_pid: str | None = None
        # Warnings raised during eligibility detection (e.g. duplicate observer
        # label on one patient), keyed by patient id. Surfaced in the status
        # line and as a banner on the patient's organ-groupings column.
        self._eligibility_warnings: dict[str, list[str]] = {}
        self._synonyms_flat = self._load_synonyms()
        self._build_ui()
        self._refresh_state()

    # ---- Public API -------------------------------------------------------

    def set_settings(self, settings: dict[str, Any]) -> None:
        """Inject the live settings dict (MainWindow shares one instance)."""
        self._settings = settings if settings is not None else {}

    def set_library(self, library: MetadataLibrary | None) -> None:
        """Called by MainWindow after a folder scan completes."""
        self._library = library
        self._group_drawers.clear()
        # A new cohort invalidates manual edits / unmatched trays from the old.
        self._unmatched.clear()
        self._manual_edited.clear()
        self._patient_thresholds.clear()
        self._bucket_representatives.clear()
        self._current_pid = None
        self._refresh_state()

    # ---- Observer-label selection ----------------------------------------

    def _all_source_labels(self) -> list[str]:
        """Sorted distinct non-synthetic source labels across the library."""
        if self._library is None:
            return []
        labels: set[str] = set()
        for patient in self._library.patients.values():
            for ctx in patient.contexts:
                for r in ctx.rtstructs:
                    if not r.is_synthetic_consensus and r.source_label:
                        labels.add(r.source_label)
        return sorted(labels)

    def _selected_observer_labels(self) -> list[str]:
        """The labels the user designated as manual observers (persisted).

        Intersected with the labels actually present so a stale selection
        from a previous cohort doesn't leak in.
        """
        stored = self._settings.get("consensus_observer_labels", []) or []
        available = set(self._all_source_labels())
        return [s for s in stored if s in available]

    def _set_selected_observer_labels(self, labels: list[str]) -> None:
        self._settings["consensus_observer_labels"] = list(labels)

    def _source_label_for(self, sop_uid: str) -> str:
        """Source label (= observer identity) for a real RTSS SOP UID."""
        if self._library is None:
            return "?"
        for patient in self._library.patients.values():
            for ctx in patient.contexts:
                for r in ctx.rtstructs:
                    if r.sop_instance_uid == sop_uid:
                        return r.source_label or "?"
        return "?"

    def replacement_rules(self) -> list[ReplacementRule]:
        rules = list(self._settings.get("replacement_rules", []) or [])
        return [
            ReplacementRule(find=str(r.get("find", "")), replace=str(r.get("replace", "")))
            for r in rules
            if r.get("find")
        ]

    # ---- Session save / restore ------------------------------------------

    def session_state(self) -> list[dict[str, Any]]:
        """Snapshot every currently-registered synthetic consensus for saving.

        Returns a list of dicts shaped like (session schema v4)::

            [{
                "patient_id": ...,
                "observer_labels": ["Observer A", "Observer B", ...],  # raters
                "for_uid": ...,                # FrameOfReferenceUID
                "synthetic_sop_uid": ...,      # the synthetic UID we minted
                "organs": [                    # one per synthetic ROI
                    {"roi_number": 1,
                     "roi_name": "Parotid_L",
                     "constituents": [
                         {"sop_uid": "...", "roi_number": 14}, ...
                     ]},
                    ...
                ],
            }, ...]

        ``observer_labels`` is informational (the synthetic entry is rebuilt
        from ``constituents``); pre-v4 sessions carried a single
        ``source_label`` instead, which :meth:`apply_session_state` still
        tolerates. The next restore re-creates the same synthetic RTSSes by
        calling :meth:`apply_session_state` with this list AFTER the library
        has been rescanned.
        """
        out: list[dict[str, Any]] = []
        if self._library is None:
            return out
        for pid, for_uid, syn in self._library.synthetic_consensus_entries():
            organs_payload: list[dict[str, Any]] = []
            organ_by_roi_number = {o.roi_number: o for o in syn.organs}
            constituent_sops: set[str] = set()
            for roi_number, constituents in syn.constituent_groups.items():
                organ = organ_by_roi_number.get(roi_number)
                if organ is None:
                    continue
                organs_payload.append(
                    {
                        "roi_number": int(organ.roi_number),
                        "roi_name": str(organ.roi_name),
                        "constituents": [
                            {"sop_uid": str(sop), "roi_number": int(roi)}
                            for sop, roi in constituents
                        ],
                    }
                )
                constituent_sops.update(str(sop) for sop, _roi in constituents)
            observer_labels = sorted({self._source_label_for(sop) for sop in constituent_sops})
            out.append(
                {
                    "patient_id": pid,
                    "observer_labels": observer_labels,
                    "for_uid": for_uid,
                    "synthetic_sop_uid": syn.sop_instance_uid,
                    "organs": organs_payload,
                }
            )
        return out

    def apply_session_state(self, consensus_groups: list[dict[str, Any]]) -> int:
        """Re-register synthetic RTSSes from a session payload.

        Returns the number of entries successfully restored. Silently
        skips entries whose patient or constituent UIDs no longer exist
        in the current library — same defensive policy as the drawer
        restore path on the Match Contours tab.
        """
        if self._library is None or not consensus_groups:
            return 0
        # Clear any pre-existing synthetic entries so a re-load is deterministic.
        self._library.clear_synthetic_consensus()
        restored = 0
        for group in consensus_groups:
            patient_id = str(group.get("patient_id", "") or "")
            if not patient_id or patient_id not in self._library.patients:
                continue
            for_uid = str(group.get("for_uid", "") or "")
            synthetic_sop = str(group.get("synthetic_sop_uid", "") or "")
            # Synthetic entries always carry the consensus source label. The
            # payload's observer_labels (v4) / source_label (pre-v4) are
            # informational only — the entry is rebuilt from constituents.
            if not synthetic_sop:
                continue
            # Validate constituents and build OrganEntry + constituent_groups.
            organs: list[OrganEntry] = []
            constituent_groups: dict[int, list[tuple[str, int]]] = {}
            for organ_payload in group.get("organs", []) or []:
                roi_number = int(organ_payload.get("roi_number", 0) or 0)
                roi_name = str(organ_payload.get("roi_name", "") or "")
                if not roi_number or not roi_name:
                    continue
                constituents: list[tuple[str, int]] = []
                for c in organ_payload.get("constituents", []) or []:
                    c_sop = str(c.get("sop_uid", "") or "")
                    c_roi = int(c.get("roi_number", 0) or 0)
                    if c_sop and c_roi:
                        constituents.append((c_sop, c_roi))
                if len(constituents) < 2:
                    continue
                organs.append(OrganEntry(roi_number=roi_number, roi_name=roi_name))
                constituent_groups[roi_number] = constituents
            if not organs:
                continue
            entry = RTSTRUCTEntry(
                sop_instance_uid=synthetic_sop,
                file_path="",
                manufacturer=CONSENSUS_SOURCE_LABEL,
                source_label=CONSENSUS_SOURCE_LABEL,
                source_origin="synthetic",
                frame_of_reference_uid=for_uid,
                study_instance_uid="",
                organs=organs,
                is_synthetic_consensus=True,
                constituent_groups=constituent_groups,
            )
            if self._library.register_synthetic_consensus(patient_id, for_uid, entry):
                restored += 1
        return restored

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ---- Toolbar (help + status) ----
        toolbar = QHBoxLayout()
        title = QLabel("<b>Build Consensus GT (Optional)</b>", self)
        toolbar.addWidget(title)
        toolbar.addSpacing(8)
        self._status_label = QLabel("", self)
        self._status_label.setStyleSheet("color: #666;")
        toolbar.addWidget(self._status_label, stretch=1)
        # Observer selection lives in a popup (keeps the tab compact). Button
        # text shows how many labels are currently selected as observers.
        self._observers_btn = QPushButton("Manual observers…", self)
        self._observers_btn.setToolTip(
            "Choose which source labels represent manual observers. Each "
            "selected label is treated as one rater in the STAPLE consensus."
        )
        self._observers_btn.clicked.connect(self._on_select_observers_clicked)
        toolbar.addWidget(self._observers_btn)
        self._help_btn = QPushButton("Help", self)
        self._help_btn.setToolTip("Show a workflow explanation for this tab")
        self._help_btn.clicked.connect(self._on_help_clicked)
        toolbar.addWidget(self._help_btn)
        outer.addLayout(toolbar)

        # ---- Empty-state placeholder ----
        self._empty_label = QLabel(
            "<i>Click <b>Manual observers…</b> above to choose which source "
            "labels are manual observers. This tab then builds a STAPLE "
            "consensus per patient from the observers that have contours in "
            "that patient (2+ required). If no labels appear, load data in "
            "Tab 1 and give each observer a distinct source label.</i>",
            self,
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet("color: #888; padding: 36px;")
        outer.addWidget(self._empty_label)

        # ---- Main split: 3 independent columns ----
        #   [ Eligible patients | Organ groupings | Unmatched tray ]
        # Each column has its own scroll zone.
        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)

        # 1) Eligible patients (narrow)
        group_pane = QFrame(self._splitter)
        group_layout = QVBoxLayout(group_pane)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(2)
        group_layout.addWidget(QLabel("<b>Eligible patients</b>", group_pane))
        self._groups_list = QListWidget(group_pane)
        self._groups_list.setToolTip(
            "Each row = one patient with 2+ observers. Click to inspect its "
            "organ matches. Ctrl/Shift-click to select several for "
            "inter-observer metrics."
        )
        self._groups_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._groups_list.currentItemChanged.connect(self._on_group_selected)
        group_layout.addWidget(self._groups_list, stretch=1)
        self._splitter.addWidget(group_pane)

        # 2) Organ groupings (own scroll)
        drawers_pane = QFrame(self._splitter)
        drawers_layout = QVBoxLayout(drawers_pane)
        drawers_layout.setContentsMargins(0, 0, 0, 0)
        drawers_layout.setSpacing(2)
        drawers_layout.addWidget(QLabel("<b>Organ groupings</b>", drawers_pane))
        self._drawers_scroll = QScrollArea(drawers_pane)
        self._drawers_scroll.setWidgetResizable(True)
        self._drawers_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._drawers_container = QWidget()
        self._drawers_v = QVBoxLayout(self._drawers_container)
        self._drawers_v.setContentsMargins(3, 3, 3, 3)
        self._drawers_v.setSpacing(3)
        self._drawers_v.addStretch(1)
        self._drawers_scroll.setWidget(self._drawers_container)
        drawers_layout.addWidget(self._drawers_scroll, stretch=1)
        self._splitter.addWidget(drawers_pane)

        # 3) Unmatched tray (own scroll) — drop target for drag-and-drop.
        unmatched_pane = QFrame(self._splitter)
        unmatched_layout = QVBoxLayout(unmatched_pane)
        unmatched_layout.setContentsMargins(0, 0, 0, 0)
        unmatched_layout.setSpacing(2)
        unmatched_layout.addWidget(QLabel("<b>Unmatched</b>", unmatched_pane))
        self._unmatched_scroll = QScrollArea(unmatched_pane)
        self._unmatched_scroll.setWidgetResizable(True)
        self._unmatched_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._unmatched_container = QWidget()
        self._unmatched_v = QVBoxLayout(self._unmatched_container)
        self._unmatched_v.setContentsMargins(3, 3, 3, 3)
        self._unmatched_v.setSpacing(3)
        self._unmatched_v.addStretch(1)
        self._unmatched_scroll.setWidget(self._unmatched_container)
        unmatched_layout.addWidget(self._unmatched_scroll, stretch=1)
        self._splitter.addWidget(unmatched_pane)

        self._splitter.setSizes([190, 380, 280])
        outer.addWidget(self._splitter, stretch=1)

        # ---- Footer (threshold + actions) ----
        footer = QHBoxLayout()

        # Threshold spinbox — two ROIs across raters are grouped together
        # only if their similarity score is at or above this value. Default
        # 0.60 matches Tab 3's auto-match default. Higher values demand more
        # name agreement; lower values fold near-matches together.
        footer.addWidget(QLabel("Match threshold (this patient):", self))
        self._threshold_spin = QDoubleSpinBox(self)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setRange(0.0, 1.0)
        self._threshold_spin.setSingleStep(0.05)
        self._threshold_spin.setValue(_DEFAULT_THRESHOLD)
        self._threshold_spin.setToolTip(
            "Two ROI names are grouped together if their hybrid Levenshtein + "
            "cosine similarity (with TG-263 dictionary) meets or exceeds this "
            "value. This threshold applies to the CURRENTLY SELECTED patient "
            "only — each patient keeps its own. Lower → more aggressive "
            "grouping; higher → stricter. Default 0.60."
        )
        # Re-cluster only the current patient when its threshold changes.
        self._threshold_spin.valueChanged.connect(self._on_threshold_changed)
        footer.addWidget(self._threshold_spin)

        footer.addSpacing(12)

        self._inter_manual_btn = QPushButton(
            "Compute inter-observer variability for group(s)…", self
        )
        self._inter_manual_btn.setToolTip(
            "Select one or more groups on the left (Ctrl/Shift-click), then "
            "click this to compute pairwise geometric metrics between every "
            "pair of raters for every matched organ in each selected group. "
            "Choose which metrics to compute + tolerance values in the "
            "settings dialog that opens first. Useful for quantifying "
            "inter-observer agreement before the consensus is generated "
            "(ICRU Report 91)."
        )
        self._inter_manual_btn.clicked.connect(self._on_inter_manual_clicked)
        footer.addWidget(self._inter_manual_btn)

        footer.addStretch(1)
        self._generate_selected_btn = QPushButton("Generate STAPLE for selected patients", self)
        self._generate_selected_btn.setToolTip(
            "Register a synthetic STAPLE-consensus RTSS for ONLY the groups "
            "currently highlighted on the left (Ctrl/Shift-click for "
            "multi-select). Existing consensus entries for other groups are "
            "left untouched. Re-running on a selected group replaces its "
            f"existing entry in place. Source label: '{CONSENSUS_SOURCE_LABEL}'."
        )
        self._generate_selected_btn.clicked.connect(self._on_generate_selected_clicked)
        footer.addWidget(self._generate_selected_btn)
        self._generate_btn = QPushButton("Generate STAPLE for all patients", self)
        self._generate_btn.setToolTip(
            "Register one synthetic STAPLE-consensus RTSS per eligible group. "
            "Wipes any previously-generated synthetic entries first, then "
            "regenerates from scratch. Generated entries appear in "
            f"subsequent tabs with source label '{CONSENSUS_SOURCE_LABEL}'."
        )
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        footer.addWidget(self._generate_btn)
        outer.addLayout(footer)

    # ---- State management -------------------------------------------------

    def _on_select_observers_clicked(self) -> None:
        """Open the observer-selection popup; persist + refresh on accept."""
        labels = self._all_source_labels()
        if not labels:
            self._show_status_msg(
                "Manual observers",
                "No source labels in the current library. Load data in Tab 1 first.",
            )
            return
        dlg = _ObserverSelectionDialog(labels, self._selected_observer_labels(), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dlg.selected_labels()
        self._set_selected_observer_labels(chosen)
        self.observerLabelsChanged.emit(chosen)
        self._group_drawers.clear()
        self._refresh_state()

    def _refresh_state(self) -> None:
        """Detect eligible patients, populate the lists, drive grey/active states."""
        has_any_labels = bool(self._all_source_labels())
        n_selected = len(self._selected_observer_labels())
        self._observers_btn.setEnabled(has_any_labels)
        self._observers_btn.setText(
            f"Manual observers ({n_selected})…" if has_any_labels else "Manual observers…"
        )

        eligible = self._eligible_groups()
        active = bool(eligible)
        self._empty_label.setVisible(not active)
        self._splitter.setVisible(active)
        self._generate_btn.setEnabled(active)
        self._generate_selected_btn.setEnabled(active)
        self._inter_manual_btn.setEnabled(active)
        self._groups_list.clear()
        if not active:
            if not has_any_labels:
                self._status_label.setText("Load data in Tab 1 to begin.")
            elif not self._selected_observer_labels():
                self._status_label.setText("Click 'Manual observers…' to choose observers.")
            else:
                self._status_label.setText("No patient has 2+ of the selected observers.")
            self._clear_drawers()
            return
        for pid, members in eligible.items():
            n = len(members)
            item = QListWidgetItem(f"{pid}   ({n} observer{'s' if n != 1 else ''})")
            item.setData(Qt.ItemDataRole.UserRole, pid)
            item.setToolTip("\n".join(f"• {r.source_label}  ({r.filename})" for r in members))
            self._groups_list.addItem(item)
        status = f"{len(eligible)} eligible patient(s) — select one to inspect organ matches."
        n_warn = sum(len(v) for v in self._eligibility_warnings.values())
        if n_warn:
            status += f"  ⚠ {n_warn} labelling warning(s)."
            self._groups_list.setToolTip(
                "\n".join(
                    f"{pid}: {m}" for pid, msgs in self._eligibility_warnings.items() for m in msgs
                )
            )
        else:
            self._groups_list.setToolTip("")
        self._status_label.setText(status)
        # Auto-match all patients so drawers populate; select the first.
        for pid in eligible:
            self._auto_match_group(pid)
        self._groups_list.setCurrentRow(0)

    def _eligible_groups(self) -> dict[str, list[RTSTRUCTEntry]]:
        """Return ``{patient_id: [observer RTSSes]}`` for the consensus workflow.

        v2.4 model: a patient is eligible when it has 2+ RTSSes whose source
        label is in the user-selected observer set. Each observer is expected
        to contribute exactly one RTSS per patient (distinct label per
        observer). If a label appears more than once for a patient, that's a
        Tab-1 labelling error — we keep the first and record a warning rather
        than silently double-counting a rater (which would bias STAPLE).

        Synthetic consensus entries are excluded so we never chain
        consensus-of-consensus.
        """
        out: dict[str, list[RTSTRUCTEntry]] = {}
        self._eligibility_warnings = {}
        if self._library is None:
            return out
        observers = set(self._selected_observer_labels())
        if not observers:
            return out
        for pid, patient in self._library.patients.items():
            by_label: dict[str, list[RTSTRUCTEntry]] = defaultdict(list)
            for ctx in patient.contexts:
                for r in ctx.rtstructs:
                    if r.is_synthetic_consensus:
                        continue
                    if r.source_label in observers:
                        by_label[r.source_label].append(r)
            members: list[RTSTRUCTEntry] = []
            for label, rs in by_label.items():
                if len(rs) > 1:
                    self._eligibility_warnings.setdefault(pid, []).append(
                        f"Observer '{label}' has {len(rs)} files for this patient — "
                        "using the first; the other's contours are excluded. Give "
                        "each observer a distinct label in Tab 1."
                    )
                members.append(rs[0])
            if len(members) >= 2:
                # Stable order so rater rows / matrix columns are deterministic.
                out[pid] = sorted(members, key=lambda r: r.source_label)
        return out

    # ---- Auto-match -------------------------------------------------------

    def _auto_match_group(self, patient_id: str) -> None:
        """Group ROIs across the patient's observer RTSSes into organ buckets.

        Uses **best-score-first threshold clustering**: for each RTSS, every
        organ is pre-scored against every existing bucket, then organs are
        processed in order of *descending* best-bucket score. An ROI joins
        its highest-scoring still-available bucket if the score meets the
        user-configured threshold; otherwise it seeds a new bucket.
        Single-rater buckets are dropped silently — STAPLE requires ≥2
        raters per organ.

        Why best-score-first (and not naive first-come-first-served):
        prevents the "ghost bucket" failure where, say, ``A_Carotid_L``
        from RTSS-2 floods into an existing ``Parotid_L`` bucket (because
        both share the ``_L`` suffix and lots of characters → fuzzy
        ~0.65) BEFORE the true ``Parotid_L`` from RTSS-2 ever gets a
        chance to claim its perfect-match bucket. By processing the 1.0
        match first, ``Parotid_L`` reclaims its bucket and the
        ``A_Carotid_L`` (now finding no above-threshold target) becomes
        its own bucket — or stays single-rater and gets dropped from
        the final consensus, which is the correct behaviour.
        """
        rtsses = self._eligible_groups().get(patient_id, [])
        if not rtsses:
            self._group_drawers[patient_id] = {}
            return
        rules = self.replacement_rules()
        threshold = self._threshold_for(patient_id)
        # Buckets: list of (representative_name, [(sop_uid, roi_number, roi_name), ...])
        bucket_names: list[str] = []
        bucket_members: list[list[tuple[str, int, str]]] = []
        for r in rtsses:
            # Prevent the same RTSS from contributing to one bucket twice
            # (e.g. left/right of the same organ if both score above threshold
            # against a single representative — we want one rater per RTSS per
            # organ for STAPLE).
            rtss_used_buckets: set[int] = set()

            # Pre-score every organ in this RTSS against every existing
            # bucket. Tuple shape: (organ, [(bucket_idx, score), ...
            # sorted descending]).
            organ_scores: list[tuple[Any, str, list[tuple[int, float]]]] = []
            for organ in r.organs:
                name = str(organ.roi_name or "").strip()
                if not name:
                    continue
                scores: list[tuple[int, float]] = []
                for i, rep in enumerate(bucket_names):
                    s = similarity(name, rep, rules=rules, synonyms_flat=self._synonyms_flat).score
                    scores.append((i, s))
                scores.sort(key=lambda kv: kv[1], reverse=True)
                organ_scores.append((organ, name, scores))

            # Process organs in order of descending best-bucket score so
            # high-confidence matches (e.g. exact-canonical 1.0 hits) win
            # their preferred bucket before noisier near-matches steal it.
            organ_scores.sort(
                key=lambda triple: triple[2][0][1] if triple[2] else -1.0,
                reverse=True,
            )

            for organ, name, scores in organ_scores:
                assigned = False
                for bucket_idx, score in scores:
                    if bucket_idx in rtss_used_buckets:
                        continue
                    if score < threshold:
                        break  # scores are sorted desc — no later one will qualify
                    bucket_members[bucket_idx].append(
                        (r.sop_instance_uid, int(organ.roi_number), name)
                    )
                    rtss_used_buckets.add(bucket_idx)
                    assigned = True
                    break
                if not assigned:
                    bucket_names.append(name)
                    bucket_members.append([(r.sop_instance_uid, int(organ.roi_number), name)])
                    rtss_used_buckets.add(len(bucket_names) - 1)
        # Key 2+ rater buckets by their most frequent ROI name; collect
        # single-rater (unclustered) ROIs into the unmatched tray so the
        # user can manually assign them (Phase 3) instead of losing them.
        kept: dict[str, list[tuple[str, int, str]]] = {}
        reps: dict[str, str] = {}
        unmatched: list[tuple[str, int, str]] = []
        for rep, members in zip(bucket_names, bucket_members):
            if len(members) < 2:
                unmatched.extend(members)
                continue
            # Title the bucket by its REPRESENTATIVE — the seed name it was
            # clustered against. This keeps one canonical name per bucket:
            #   * the displayed match scores are each rater's similarity to the
            #     title (so the title's own member always reads 1.00), and
            #   * the Tab 3 synthetic-organ name equals the title (see
            #     _build_synthetic_entry).
            display = rep
            # If two buckets somehow share a seed name (very rare), suffix the
            # second so they don't collapse in the drawer dict.
            if display in kept:
                suffix = 2
                while f"{display} #{suffix}" in kept:
                    suffix += 1
                display = f"{display} #{suffix}"
            kept[display] = members
            reps[display] = rep
        self._group_drawers[patient_id] = kept
        self._bucket_representatives[patient_id] = reps
        self._unmatched[patient_id] = unmatched

    def _threshold_for(self, pid: str) -> float:
        """The match threshold for a patient (its own value, or the default)."""
        return self._patient_thresholds.get(pid, _DEFAULT_THRESHOLD)

    def _on_threshold_changed(self, value: float) -> None:
        """Apply the spinbox value to the CURRENT patient only and re-cluster it.

        Each patient keeps its own threshold, so adjusting one patient never
        disturbs another. A manually-edited (locked) patient stores the new
        value but is not re-clustered (its hand-curated grouping is preserved
        until 'Reset to auto-match').
        """
        pid = self._current_pid
        if pid is None:
            return
        self._patient_thresholds[pid] = float(value)
        if pid not in self._manual_edited:
            self._auto_match_group(pid)
        self._rerender_current()

    # ---- Manual editing (Phase 3) ----------------------------------------

    def _rerender_current(self) -> None:
        current = self._groups_list.currentItem()
        if current is not None:
            self._on_group_selected(current, None)

    def _find_in_buckets(self, pid: str, sop: str, roi: int) -> tuple[str, int] | None:
        """Return (organ_name, index) of the ROI within the patient's buckets."""
        for organ, members in self._group_drawers.get(pid, {}).items():
            for i, (s, r, _n) in enumerate(members):
                if s == sop and r == roi:
                    return organ, i
        return None

    def _evict_roi(self, pid: str, sop: str, roi: int) -> None:
        """Move an ROI out of its organ bucket into the unmatched tray."""
        loc = self._find_in_buckets(pid, sop, roi)
        if loc is None:
            return
        organ, idx = loc
        member = self._group_drawers[pid][organ].pop(idx)
        if not self._group_drawers[pid][organ]:
            del self._group_drawers[pid][organ]
            self._bucket_representatives.get(pid, {}).pop(organ, None)
        self._unmatched.setdefault(pid, []).append(member)
        self._manual_edited.add(pid)
        self._rerender_current()

    def _assign_roi(self, pid: str, sop: str, roi: int, organ_name: str) -> None:
        """Move an ROI from the unmatched tray into an organ bucket.

        ``organ_name`` may be an existing bucket or a brand-new organ name.
        A no-op if the target bucket already holds a contour from the same
        observer (one rater per organ for a valid STAPLE).
        """
        tray = self._unmatched.get(pid, [])
        member = None
        for i, (s, r, _n) in enumerate(tray):
            if s == sop and r == roi:
                member = tray.pop(i)
                break
        if member is None:
            return
        bucket = self._group_drawers.setdefault(pid, {}).setdefault(organ_name, [])
        if any(s == sop for s, _r, _n in bucket):
            # Same observer already in this organ — put it back, warn.
            tray.append(member)
            self._show_status_msg(
                "Assign ROI",
                f"'{self._source_label_for(sop)}' already has a contour in "
                f"'{organ_name}'. One rater per organ is required for STAPLE.",
            )
            return
        bucket.append(member)
        # A newly-created bucket is its own representative (the name the user
        # gave it); an existing bucket keeps its seed representative.
        self._bucket_representatives.setdefault(pid, {}).setdefault(organ_name, organ_name)
        self._manual_edited.add(pid)
        self._rerender_current()

    def _reset_patient_to_auto(self, pid: str) -> None:
        """Discard manual edits for a patient and re-run auto-match."""
        self._manual_edited.discard(pid)
        self._auto_match_group(pid)
        self._rerender_current()

    # ---- Drawer rendering -------------------------------------------------

    def _on_group_selected(self, current: QListWidgetItem | None, _previous) -> None:
        self._clear_drawers()
        self._clear_unmatched()
        if current is None:
            return
        pid = current.data(Qt.ItemDataRole.UserRole)
        if not pid:
            return
        pid = str(pid)
        self._current_pid = pid
        # Reflect this patient's own threshold in the footer spinbox without
        # firing _on_threshold_changed (which would re-cluster).
        self._threshold_spin.blockSignals(True)
        self._threshold_spin.setValue(self._threshold_for(pid))
        self._threshold_spin.blockSignals(False)
        drawers = self._group_drawers.get(pid, {})
        tray = self._unmatched.get(pid, [])

        # ---- Middle column: warning + edit banners + organ buckets ----
        warnings = self._eligibility_warnings.get(pid, [])
        if warnings:
            self._drawers_v.insertWidget(
                self._drawers_v.count() - 1, self._build_warning_banner(warnings)
            )
        if pid in self._manual_edited:
            self._drawers_v.insertWidget(self._drawers_v.count() - 1, self._build_edit_banner(pid))
        if not drawers:
            placeholder = QLabel(
                "<i>No organs match across 2+ of this patient's observers.</i>",
                self._drawers_container,
            )
            placeholder.setStyleSheet("color: #888; padding: 8px;")
            placeholder.setWordWrap(True)
            self._drawers_v.insertWidget(self._drawers_v.count() - 1, placeholder)
        else:
            for canon, raters in sorted(drawers.items()):
                self._drawers_v.insertWidget(
                    self._drawers_v.count() - 1,
                    self._build_organ_row(pid, canon, raters),
                )

        # ---- Right column: unmatched tray, grouped by observer ----
        if not tray:
            empty = QLabel("<i>Nothing unmatched.</i>", self._unmatched_container)
            empty.setStyleSheet("color: #aaa; padding: 8px;")
            self._unmatched_v.insertWidget(self._unmatched_v.count() - 1, empty)
        else:
            hint = QLabel(
                "<span style='color:#888; font-size: 9pt'>Drag an item onto an "
                "organ, or use its Assign menu.</span>",
                self._unmatched_container,
            )
            hint.setWordWrap(True)
            self._unmatched_v.insertWidget(self._unmatched_v.count() - 1, hint)
            by_observer: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
            for sop_uid, roi_number, roi_name in tray:
                by_observer[self._source_label_for(sop_uid)].append((sop_uid, roi_number, roi_name))
            for observer in sorted(by_observer):
                obs_header = QLabel(f"<b>{observer}</b>", self._unmatched_container)
                obs_header.setStyleSheet("color: #555; margin-top: 4px;")
                self._unmatched_v.insertWidget(self._unmatched_v.count() - 1, obs_header)
                # Sort each observer's unmatched organs alphabetically by name.
                for sop_uid, roi_number, roi_name in sorted(
                    by_observer[observer], key=lambda t: t[2].lower()
                ):
                    self._unmatched_v.insertWidget(
                        self._unmatched_v.count() - 1,
                        self._build_unmatched_item(pid, sop_uid, roi_number, roi_name),
                    )

    def _build_warning_banner(self, warnings: list[str]) -> QWidget:
        bar = QFrame(self._drawers_container)
        bar.setStyleSheet(
            "QFrame { background: #fde8e8; border: 1px solid #f5c2c2; border-radius: 3px; }"
        )
        v = QVBoxLayout(bar)
        v.setContentsMargins(6, 3, 6, 3)
        v.setSpacing(1)
        for w in warnings:
            lab = QLabel(f"⚠ {w}", bar)
            lab.setWordWrap(True)
            lab.setStyleSheet("color: #a33;")
            v.addWidget(lab)
        return bar

    def _build_edit_banner(self, pid: str) -> QWidget:
        bar = QFrame(self._drawers_container)
        bar.setStyleSheet(
            "QFrame { background: #fff3cd; border: 1px solid #ffe69c; border-radius: 3px; }"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 2, 6, 2)
        h.setSpacing(4)
        h.addWidget(QLabel("<b>✎ Edited</b> — auto-match off.", bar))
        h.addStretch(1)
        reset = QPushButton("Reset", bar)
        reset.setToolTip("Discard manual edits and re-run auto-match for this patient.")
        reset.clicked.connect(lambda _checked=False, p=pid: self._reset_patient_to_auto(p))
        h.addWidget(reset)
        return bar

    def _roi_line_html(self, sop_uid: str, roi_name: str) -> str:
        observer = self._source_label_for(sop_uid)
        return f"<span style='color:#444'><b>{observer}</b>: {roi_name}</span>"

    def _build_organ_row(
        self,
        pid: str,
        canonical_name: str,
        raters: list[tuple[str, int, str]],
    ) -> QWidget:
        """Compact organ bucket: header + a removable line per rater. Drop target."""
        row = _OrganBucketFrame(
            lambda s, r, p=pid, o=canonical_name: self._assign_roi(p, s, r, o),
            self._drawers_container,
        )
        layout = QVBoxLayout(row)
        layout.setContentsMargins(5, 2, 4, 3)
        layout.setSpacing(1)
        n = len(raters)
        if n < 2:
            header = QLabel(
                f"<b>{canonical_name}</b>  "
                f"<span style='color:#c47f00; font-size: 8pt'>⚠ {n} rater</span>",
                row,
            )
        else:
            header = QLabel(
                f"<b>{canonical_name}</b>  "
                f"<span style='color:#999; font-size: 8pt'>{n} raters</span>",
                row,
            )
        layout.addWidget(header)
        rules = self.replacement_rules()
        # The bucket was clustered against its representative (seed) name — the
        # scores below are each rater's similarity to THAT, i.e. the actual
        # auto-match decision (falls back to the display name if unknown).
        representative = self._bucket_representatives.get(self._current_pid or "", {}).get(
            canonical_name, canonical_name
        )
        for sop_uid, roi_number, roi_name in raters:
            line = QHBoxLayout()
            line.setSpacing(2)
            lbl = QLabel(self._roi_line_html(sop_uid, roi_name), row)
            line.addWidget(lbl, stretch=1)
            score = similarity(
                roi_name, representative, rules=rules, synonyms_flat=self._synonyms_flat
            ).score
            colour = "#2a7" if score >= 0.8 else ("#888" if score >= 0.6 else "#c47f00")
            score_lbl = QLabel(f"<span style='color:{colour}'>{score:.2f}</span>", row)
            score_lbl.setToolTip(
                f"Auto-match score: similarity of '{roi_name}' to the bucket's "
                f"representative name '{representative}'."
            )
            line.addWidget(score_lbl)
            remove = QPushButton("X", row)
            remove.setFixedSize(18, 18)
            remove.setToolTip("Remove this contour (→ Unmatched).")
            remove.clicked.connect(
                lambda _checked=False, s=sop_uid, r=roi_number: self._evict_roi(pid, s, r)
            )
            line.addWidget(remove)
            layout.addLayout(line)
        return row

    def _build_unmatched_item(
        self, pid: str, sop_uid: str, roi_number: int, roi_name: str
    ) -> QWidget:
        """A draggable unmatched-ROI row with an Assign-to menu fallback.

        Grouped under an observer header, so the row shows only the ROI name.
        The text label is made transparent to mouse events so a press-drag on
        it reaches the draggable frame underneath (the Assign button keeps its
        own clicks).
        """
        item = _RoiDragLabel(sop_uid, roi_number, self._unmatched_container)
        item.setObjectName("UnmatchedItem")
        item.setFrameShape(QFrame.Shape.StyledPanel)
        item.setStyleSheet(
            "QFrame#UnmatchedItem { border: 1px dashed #bbb; border-radius: 3px; "
            "background: #fafafa; }"
        )
        item.setToolTip("Drag onto an organ bucket, or use Assign.")
        item.setCursor(Qt.CursorShape.OpenHandCursor)
        layout = QHBoxLayout(item)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(2)
        name_lbl = QLabel(roi_name, item)
        name_lbl.setStyleSheet("color: #444;")
        name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(name_lbl, stretch=1)
        assign = QPushButton("Assign ▾", item)
        assign.setToolTip("Add this contour to an organ bucket.")
        menu = QMenu(assign)
        for organ in sorted(self._group_drawers.get(pid, {})):
            act = QAction(organ, menu)
            act.triggered.connect(
                lambda _checked=False, s=sop_uid, r=roi_number, o=organ: self._assign_roi(
                    pid, s, r, o
                )
            )
            menu.addAction(act)
        if self._group_drawers.get(pid):
            menu.addSeparator()
        new_act = QAction("New organ…", menu)
        new_act.triggered.connect(
            lambda _checked=False, s=sop_uid, r=roi_number, nm=roi_name: self._assign_to_new_organ(
                pid, s, r, nm
            )
        )
        menu.addAction(new_act)
        assign.setMenu(menu)
        layout.addWidget(assign)
        return item

    def _assign_to_new_organ(self, pid: str, sop: str, roi: int, suggested: str) -> None:
        name, ok = QInputDialog.getText(
            self.window() or self,
            "New organ",
            "Organ name for the new bucket:",
            text=suggested,
        )
        if ok and name.strip():
            self._assign_roi(pid, sop, roi, name.strip())

    def _clear_drawers(self) -> None:
        self._clear_layout(self._drawers_v)

    def _clear_unmatched(self) -> None:
        self._clear_layout(self._unmatched_v)

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        # Remove every widget except the trailing stretch.
        while layout.count() > 1:
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _rtstruct_filename_for(self, sop_uid: str) -> str:
        if self._library is None:
            return "?"
        for patient in self._library.patients.values():
            for ctx in patient.contexts:
                for r in ctx.rtstructs:
                    if r.sop_instance_uid == sop_uid:
                        return r.filename
        return "?"

    # ---- Actions ----------------------------------------------------------

    def _on_generate_clicked(self) -> None:
        """Generate synthetic consensus RTSSes for EVERY eligible patient.

        Wipes any previously-generated synthetic entries first so the
        result reflects the current group-drawer state without leftovers
        from a stale configuration (e.g. a patient no longer eligible after
        the user changed the observer selection).
        """
        if self._library is None or not self._group_drawers:
            QMessageBox.information(
                self,
                "Generate Consensus GT",
                "No eligible patients to generate consensus for.",
            )
            return
        self._library.clear_synthetic_consensus()
        pids = list(self._group_drawers.keys())
        created, skipped = self._register_consensus_for(pids)
        self._report_generation_result(created, skipped, scope="ALL")

    def _on_generate_selected_clicked(self) -> None:
        """Generate synthetic consensus only for patients highlighted on the left.

        Does NOT wipe existing synthetic entries — so other patients'
        consensus RTSSes remain intact. Per-patient regeneration replaces
        the existing entry in place via the deterministic
        ``_mint_synthetic_uid`` token.
        """
        if self._library is None or not self._group_drawers:
            self._show_status_msg(
                "Generate Consensus GT",
                "No eligible patients to generate consensus for.",
            )
            return
        selected_pids: list[str] = []
        for item in self._groups_list.selectedItems():
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                selected_pids.append(str(data))
        if not selected_pids:
            self._show_status_msg(
                "Generate Consensus GT",
                "No patients selected — Ctrl/Shift-click one or more on the left first.",
            )
            return
        created, skipped = self._register_consensus_for(selected_pids)
        self._report_generation_result(created, skipped, scope="SELECTED")

    def _register_consensus_for(self, pids: list[str]) -> tuple[int, list[str]]:
        """Register synthetic consensus RTSSes for the given patient ids.

        Returns ``(created_count, skipped_messages)``. Re-registration of
        an existing patient replaces the entry in place because
        ``_mint_synthetic_uid`` is deterministic.
        """
        created = 0
        skipped: list[str] = []
        for pid in pids:
            buckets = self._group_drawers.get(pid, {})
            if not buckets:
                skipped.append(f"{pid}: no matched organs")
                continue
            entry = self._build_synthetic_entry(pid, buckets)
            if entry is None:
                skipped.append(f"{pid}: no organ with 2+ raters (or no shared FrameOfReferenceUID)")
                continue
            ok = self._library.register_synthetic_consensus(
                pid, entry.frame_of_reference_uid, entry
            )
            if ok:
                created += 1
            else:
                skipped.append(f"{pid}: register failed")
        return created, skipped

    def _report_generation_result(self, created: int, skipped: list[str], scope: str) -> None:
        msg = [
            f"Generated {created} consensus RTSS entr{'y' if created == 1 else 'ies'} "
            f"({scope} patients)."
        ]
        if skipped:
            msg.append("Skipped:")
            msg.extend(f"  • {s}" for s in skipped)
        QMessageBox.information(self, "Generate Consensus GT", "\n".join(msg))
        if created:
            self.consensusGenerated.emit()

    def _build_synthetic_entry(
        self,
        patient_id: str,
        buckets: dict[str, list[tuple[str, int, str]]],
    ) -> RTSTRUCTEntry | None:
        """Assemble the synthetic RTSTRUCTEntry representing this patient's consensus."""
        if self._library is None:
            return None
        # Only organs with 2+ raters are valid STAPLE inputs. After manual
        # edits a bucket can drop below 2 — exclude those here.
        buckets = {organ: raters for organ, raters in buckets.items() if len(raters) >= 2}
        if not buckets:
            return None
        # All constituent RTSSes must share a FrameOfReferenceUID for STAPLE to
        # work. The observers could in principle span contexts — pick the FoR
        # with the most contributors here.
        for_uid_counts: dict[str, int] = defaultdict(int)
        constituent_uids = {sop for raters in buckets.values() for (sop, _, _) in raters}
        patient = self._library.patients.get(patient_id)
        if patient is None:
            return None
        for ctx in patient.contexts:
            for r in ctx.rtstructs:
                if r.sop_instance_uid in constituent_uids:
                    for_uid_counts[ctx.frame_of_reference_uid] += 1
        if not for_uid_counts:
            return None
        chosen_for = max(for_uid_counts.items(), key=lambda kv: kv[1])[0]
        # Build OrganEntry list — one synthetic ROI per bucket, with a new
        # roi_number starting from 1. The organ NAME is the bucket title (its
        # representative), so the name shown in Tab 3 matches the Tab 2 bucket
        # title exactly.
        organs: list[OrganEntry] = []
        constituent_groups: dict[int, list[tuple[str, int]]] = {}
        for synthetic_roi_number, (canon, raters) in enumerate(sorted(buckets.items()), start=1):
            organs.append(OrganEntry(roi_number=synthetic_roi_number, roi_name=canon))
            constituent_groups[synthetic_roi_number] = [
                (sop, int(roi_num)) for (sop, roi_num, _name) in raters
            ]
        synthetic_sop = self._mint_synthetic_uid(patient_id)
        return RTSTRUCTEntry(
            sop_instance_uid=synthetic_sop,
            file_path="",
            manufacturer=CONSENSUS_SOURCE_LABEL,
            source_label=CONSENSUS_SOURCE_LABEL,
            source_origin="synthetic",
            frame_of_reference_uid=chosen_for,
            study_instance_uid="",
            organs=organs,
            is_synthetic_consensus=True,
            constituent_groups=constituent_groups,
        )

    def _mint_synthetic_uid(self, patient_id: str) -> str:
        """Generate a deterministic UID for a patient's synthetic consensus RTSS.

        Not a real DICOM UID — uses our own prefix so it can never collide
        with anything pydicom would read off disk. The same patient always
        produces the same UID (one consensus per patient in the v2.4 model),
        so re-running Generate replaces the existing entry in place via
        ``MetadataLibrary.register_synthetic_consensus`` rather than minting
        a sibling that leaves a stale orphan behind.
        """
        token = f"{patient_id}|consensus"
        return f"AUTOSEG.SYNTHETIC.{abs(hash(token)) % (10**18)}"

    # ---- Inter-manual metrics --------------------------------------------

    def _on_inter_manual_clicked(self) -> None:
        """Open settings → compute for every selected group → show results.

        Workflow:

        1. Resolve every selected group on the left list (Ctrl/Shift-click).
           Fall back to the current item if no multi-selection exists yet.
        2. Open a settings dialog so the user can pick which geometric
           metrics to compute and override the Surface Dice / APL
           tolerance values for this run.
        3. Run the pairwise computation under a QProgressDialog showing
           one tick per (group × organ) — cancellable.
        4. Display the merged result rows in the existing results dialog
           with an additional Patient + Source-label column.
        """
        if self._library is None:
            return
        selected_items = self._groups_list.selectedItems()
        if not selected_items:
            current = self._groups_list.currentItem()
            if current is None:
                self._show_status_msg(
                    "Inter-observer variability",
                    "Select one or more patients on the left first "
                    "(Ctrl/Shift-click for multi-select).",
                )
                return
            selected_items = [current]
        selected_pids: list[str] = []
        for item in selected_items:
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                selected_pids.append(str(data))

        # Settings dialog — defaults from Tab 4's tolerances + all geom on.
        settings_dlg = _InterObserverSettingsDialog(
            n_groups=len(selected_pids),
            default_sd_tau_mm=float(
                (self._settings.get("tolerances") or {}).get("surface_dice_tau_mm", 3.0)
            ),
            default_apl_tau_mm=float(
                (self._settings.get("tolerances") or {}).get("apl_tolerance_mm", 3.0)
            ),
            parent=self,
        )
        if settings_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        config = settings_dlg.metric_config()

        # Pre-flight bucket count for the progress bar denominator. We count
        # (group × organ) pairs because that's where the per-organ rasterisation
        # + metric loop sits — finer-grained ticks would dwarf the bar with
        # micro-updates between rater pairs.
        total_units = 0
        for pid in selected_pids:
            total_units += len(self._group_drawers.get(pid, {}))
        if total_units == 0:
            self._show_status_msg(
                "Inter-observer variability",
                "No matched organs to evaluate across the selected patient(s).",
            )
            return

        progress = QProgressDialog(
            "Loading CTs and computing pairwise metrics…",
            "Cancel",
            0,
            total_units,
            self,
        )
        progress.setWindowTitle("Inter-observer variability")
        progress.setMinimumDuration(0)
        progress.setValue(0)
        # Match the cancel behaviour we use elsewhere — modal, parent-bound.
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        # CRUCIAL: by default QProgressDialog auto-resets on cancel (hides
        # itself + resets to max+1), so the next ``setValue()`` call from
        # a per-organ tick re-shows the dialog and the user sees a "new
        # dialog opens at 12/20". Disable both behaviours so cancel sticks.
        progress.setAutoReset(False)
        progress.setAutoClose(False)

        cancel_check = progress.wasCanceled  # pass the bound method as a closure
        all_rows: list[dict[str, Any]] = []
        cancelled = False
        units_done = 0
        for pid in selected_pids:
            if cancel_check():
                cancelled = True
                break
            buckets = self._group_drawers.get(pid, {})
            if not buckets:
                continue
            group_rows = self._compute_inter_manual_rows(
                pid,
                buckets,
                config,
                # Bind loop variables as default args so the lambda captures
                # their values at the current iteration, not by reference
                # (removes the B023 footgun outright).
                progress_cb=lambda done_in_group, _u=units_done, _p=pid: self._tick_progress(
                    progress, _u + done_in_group, total_units, _p
                ),
                cancel_check=cancel_check,
            )
            # Stamp every row with the patient id so the merged results table
            # can distinguish which patient produced it. Rater identity is
            # already carried by rater_a / rater_b (the observer source labels).
            for r in group_rows:
                r["patient_id"] = pid
            all_rows.extend(group_rows)
            units_done += len(buckets)
            if not cancel_check():
                progress.setValue(units_done)
            QApplication.processEvents()
            if cancel_check():
                cancelled = True
                break
        progress.close()

        if cancelled and not all_rows:
            return
        if not all_rows:
            self._show_status_msg(
                "Inter-observer variability",
                "Could not load CTs or rasterise any constituent masks for "
                "the selected group(s). Check the Load Data tab for missing files.",
            )
            return
        dlg = _InterManualMetricsDialog(
            all_rows,
            n_groups=len(selected_pids),
            cancelled=cancelled,
            sd_tau_mm=float(config["tolerances"]["surface_dice_tau_mm"]),
            apl_tau_mm=float(config["tolerances"]["apl_tolerance_mm"]),
            parent=self,
        )
        dlg.exec()

    def _tick_progress(
        self,
        progress: QProgressDialog,
        done: int,
        total: int,
        pid: str,
    ) -> None:
        """Advance the QProgressDialog and pump events so Cancel stays live.

        Skip the setValue call once cancel has been requested so we don't
        re-show a hidden dialog or step past a final value the user
        already dismissed.
        """
        if progress.wasCanceled():
            QApplication.processEvents()
            return
        progress.setValue(min(done, total))
        progress.setLabelText(
            f"Computing inter-observer metrics — {pid}\n{done} / {total} organ buckets done"
        )
        QApplication.processEvents()

    def _show_status_msg(self, title: str, message: str) -> None:
        """QMessageBox wrapper parented to the main window for reliability.

        The same QScrollArea-parent quirk that breaks Tab 3's QMessageBox
        also applies here; using ``self.window()`` as the parent (the
        top-level main window) makes the dialog appear reliably.
        """
        QMessageBox.information(self.window() or self, title, message)

    def _compute_inter_manual_rows(
        self,
        patient_id: str,
        buckets: dict[str, list[tuple[str, int, str]]],
        config: dict[str, Any] | None = None,
        progress_cb=None,
        cancel_check=None,
    ) -> list[dict[str, Any]]:
        """Build the pairwise-metric row list for the inter-manual dialog.

        Returns one row per (organ × pair-of-raters). Each row dict has
        ``organ``, ``rater_a``, ``rater_b``, ``metrics`` (a dict mirroring
        :func:`compute_geometric_metrics`'s output). Empty list on
        catastrophic failure (no CT, no library).

        ``config`` is forwarded to ``compute_geometric_metrics``; if None,
        a sensible default (all metrics on, 3 mm tolerances) is used.

        ``progress_cb`` is invoked with the number of completed organ
        buckets so callers can drive a QProgressDialog.

        ``cancel_check`` is a zero-arg callable returning ``True`` when
        the user has requested cancellation. Checked before each organ
        AND between each pairwise comparison so a cancel during a long
        run takes effect within a second or two.
        """
        if self._library is None:
            return []
        # Locate the patient's reference image folder via any constituent's RTSS.
        any_sop = next(
            (sop for ms in buckets.values() for (sop, _r, _n) in ms),
            None,
        )
        if any_sop is None:
            return []
        folder = find_reference_image_folder(self._library, patient_id, any_sop)
        if folder is None:
            return []
        try:
            ct = read_dicom_image(folder)
        except Exception:  # noqa: BLE001
            return []

        # Cache rasterised masks per (sop, roi) to avoid double work when
        # the same RTSS appears in multiple organs (it always does).
        mask_cache: dict[tuple[str, int], Any] = {}
        rtss_cache: dict[str, Any] = {}

        if config is None:
            config = {
                "geometric": {
                    "dice": True,
                    "hausdorff100": True,
                    "hausdorff95": True,
                    "mean_surface_distance": True,
                    "surface_dice": True,
                    "apl_mean": False,
                    "apl_total": False,
                    "volume": True,
                    "com_offset": True,
                },
                "tolerances": {
                    "surface_dice_tau_mm": 3.0,
                    "apl_tolerance_mm": 3.0,
                },
            }

        out: list[dict[str, Any]] = []
        organs_done = 0
        for organ_name, members in sorted(buckets.items()):
            if cancel_check is not None and cancel_check():
                return out
            # Build masks for every rater contributing to this organ.
            rater_masks: list[tuple[str, Any]] = []  # (filename, sitk_mask)
            for sop_uid, roi_number, _roi_name in members:
                key = (sop_uid, roi_number)
                if key not in mask_cache:
                    if sop_uid not in rtss_cache:
                        path = self._rtstruct_file_path(sop_uid)
                        if path is None:
                            mask_cache[key] = None
                            continue
                        try:
                            rtss_cache[sop_uid] = read_rtstruct(path)
                        except Exception:  # noqa: BLE001
                            mask_cache[key] = None
                            continue
                    try:
                        mask_cache[key] = extract_mask_for_roi(ct, rtss_cache[sop_uid], roi_number)
                    except Exception:  # noqa: BLE001
                        mask_cache[key] = None
                m = mask_cache[key]
                if m is None:
                    continue
                # Rater identity = source label (= observer name), consistent
                # across patients. This is what shows in the matrix columns.
                rater_masks.append((self._source_label_for(sop_uid), m))
            # Pairwise comparisons — also cancellable so a click during a
            # long rater stack (e.g. 5 raters = 10 pairs per organ)
            # doesn't have to wait for the full quadratic loop to finish.
            cancel_now = False
            for i in range(len(rater_masks)):
                if cancel_check is not None and cancel_check():
                    cancel_now = True
                    break
                for j in range(i + 1, len(rater_masks)):
                    if cancel_check is not None and cancel_check():
                        cancel_now = True
                        break
                    name_a, mask_a = rater_masks[i]
                    name_b, mask_b = rater_masks[j]
                    try:
                        metrics = compute_geometric_metrics(mask_a, mask_b, config)
                    except Exception as exc:  # noqa: BLE001
                        metrics = {"error": f"{type(exc).__name__}: {exc}"}
                    out.append(
                        {
                            "organ": organ_name,
                            "rater_a": name_a,
                            "rater_b": name_b,
                            "metrics": metrics,
                        }
                    )
                if cancel_now:
                    break
            # Drop this organ's rasterised masks from the cache before
            # moving to the next organ. ``mask_cache`` keys are
            # (sop_uid, roi_number) — each ROI belongs to exactly one
            # organ bucket, so cached masks are never reused across
            # organs. Holding them inflates peak RAM linearly with
            # (raters × organs); evicting per-organ caps it at
            # (raters × 1). For a 5-rater × 12-organ patient that's
            # ~250 MB peak instead of ~3 GB.
            for sop_uid, roi_number, _roi_name in members:
                mask_cache.pop((sop_uid, roi_number), None)
            rater_masks.clear()
            # SimpleITK images hold C++-backed memory outside Python's
            # refcount system; hint the GC after each organ so peak
            # actually drops between iterations.
            gc.collect()
            # Bail out cleanly after the cache eviction so a mid-organ
            # cancel still releases this organ's masks before we leave.
            if cancel_now:
                return out
            organs_done += 1
            if progress_cb is not None:
                progress_cb(organs_done)
                # Pump events so the progress bar repaints + cancel button
                # remains live during a long run.
                QApplication.processEvents()
        return out

    def _rtstruct_file_path(self, sop_uid: str) -> str | None:
        if self._library is None:
            return None
        for patient in self._library.patients.values():
            for ctx in patient.contexts:
                for r in ctx.rtstructs:
                    if r.sop_instance_uid == sop_uid:
                        return r.file_path or None
        return None

    # ---- Help -------------------------------------------------------------

    def _on_help_clicked(self) -> None:
        QMessageBox.information(
            self,
            "Build Consensus GT — workflow",
            "<p><b>What this tab does</b></p>"
            "<p>When several <b>manual observers</b> have each contoured the same "
            "patients, this tab combines their structures into a single "
            "STAPLE-derived <b>synthetic ground truth</b> per patient, which you "
            "can use in the downstream Match Contours / Compute workflow.</p>"
            "<p><b>How to use it</b></p>"
            "<ol>"
            "<li>In Tab 1 → <i>Manage Source Labels</i>, give <b>each observer a "
            "distinct source label</b> (e.g. 'Observer A', 'Observer B'). The "
            "<i>Select similar</i> helper makes it quick to tag one observer's "
            "files across all patients.</li>"
            "<li>On this tab, tick those labels in <b>Manual observers</b> at the "
            "top. Each ticked label is treated as one rater.</li>"
            "<li>Each patient with 2+ of the selected observers appears on the "
            "left. Click one to inspect the auto-matched organs on the right — "
            "every rater is identified by its observer label.</li>"
            "<li>Fix any mismatches: <b>Remove</b> a contour from an organ to "
            "send it to the <b>Unmatched</b> tray, and <b>Assign</b> an "
            "unmatched contour to the correct organ (or a new one). Editing a "
            "patient locks it from the threshold slider — use <b>Reset to "
            "auto-match</b> to undo. Organs with fewer than 2 raters are "
            "excluded from the consensus.</li>"
            "<li>Optionally compute <b>inter-observer variability</b> first — the "
            "pairwise matrix labels raters by observer consistently across "
            "patients.</li>"
            "<li>Click <b>Generate STAPLE for all patients</b>. One "
            "synthetic RTSS per patient appears in Tab 3 with source label "
            f"<i>'{CONSENSUS_SOURCE_LABEL}'</i> and can be designated as the GT.</li>"
            "</ol>"
            "<p><b>What happens at compute time</b></p>"
            "<p>When the Compute worker encounters a synthetic GT, it rasterises each "
            "constituent's ROI, runs STAPLE on the stack, and uses the resulting "
            "binary consensus as the reference for every metric. No DICOM file is "
            "written to disk — the consensus lives only in memory.</p>"
            "<p>The STAPLE parameters used here are the <b>same ones configured "
            "on Tab 4 (Compute)</b> — max iterations, confidence weight, and the "
            "adaptive bounding-box foreground-ratio band. So if you tweak those "
            "for Tab 3's <i>vs STAPLE</i> mode, the GT consensus generated here "
            "uses the same settings. Reset-to-defaults on Tab 4 applies to both.</p>"
            "<p><b>Note:</b> A drawer using a synthetic consensus GT cannot also "
            "enable Tab 3's per-drawer 'vs STAPLE' mode — that would compute STAPLE "
            "on a STAPLE result, which is methodologically meaningless.</p>",
        )

    # ---- Synonyms ----------------------------------------------------------

    def _load_synonyms(self) -> dict[str, str]:
        try:
            raw = load_synonyms(synonyms_path())
            return flatten_synonyms(raw)
        except Exception:  # noqa: BLE001
            return {}


# ---- Observer-selection dialog ------------------------------------------


class _ObserverSelectionDialog(QDialog):
    """Popup to pick which source labels are manual observers.

    A checkbox per source label (in a scroll area for large cohorts), plus
    Select-all / Select-none helpers. Returns the chosen labels via
    :meth:`selected_labels` after Accept.
    """

    def __init__(
        self,
        all_labels: list[str],
        selected: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select manual observers")
        self.resize(380, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Tick the source labels that represent manual observers. Each "
                "ticked label is treated as one rater in the STAPLE consensus, "
                "consistently across all patients.",
                self,
            )
        )
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.StyledPanel)
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(3)
        self._checks: list[QCheckBox] = []
        chosen = set(selected)
        for label in all_labels:
            cb = QCheckBox(label, container)
            cb.setChecked(label in chosen)
            v.addWidget(cb)
            self._checks.append(cb)
        v.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        helper_row = QHBoxLayout()
        all_btn = QPushButton("Select all", self)
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("Select none", self)
        none_btn.clicked.connect(lambda: self._set_all(False))
        helper_row.addWidget(all_btn)
        helper_row.addWidget(none_btn)
        helper_row.addStretch(1)
        layout.addLayout(helper_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, checked: bool) -> None:
        for cb in self._checks:
            cb.setChecked(checked)

    def selected_labels(self) -> list[str]:
        return [cb.text() for cb in self._checks if cb.isChecked()]


# ---- Inter-manual metrics dialog ----------------------------------------


# Display order matches the canonical results-table convention. The
# labels for tolerance-dependent metrics (Surface Dice, APL) are computed
# at dialog-construction time from the user-supplied τ values; the static
# block below is the fallback when no tolerance is provided.
_INTER_MANUAL_COLUMN_KEYS: tuple[str, ...] = (
    "dice",
    "surface_dice",
    "hausdorff100",
    "hausdorff95",
    "mean_surface_distance",
    "apl_mean",
    "apl_total",
    "volume_gt_cc",
    "volume_test_cc",
    "volume_diff_cc",
    "volume_ratio",
    "com_offset_mm",
)


def _inter_manual_columns(
    sd_tau_mm: float | None, apl_tau_mm: float | None
) -> tuple[tuple[str, str], ...]:
    """Build the (key, header) list with tolerance values baked into the labels.

    Surface Dice and APL are tolerance-dependent — by including the τ
    value in the header, two CSV exports computed at different
    tolerances can be told apart at a glance in Excel.
    """
    static_overrides = {
        "hausdorff100": "HD 100% (mm)",
        "hausdorff95": "HD 95% (mm)",
        "mean_surface_distance": "MSD (mm)",
        "volume_gt_cc": "Vol A (cc)",
        "volume_test_cc": "Vol B (cc)",
        "volume_diff_cc": "Vol diff (cc)",
        "volume_ratio": "Vol ratio",
        "com_offset_mm": "COM offset (mm)",
    }
    out = []
    for key in _INTER_MANUAL_COLUMN_KEYS:
        if key in static_overrides:
            label = static_overrides[key]
        else:
            label = metric_display_label(key, sd_tau_mm=sd_tau_mm, apl_tau_mm=apl_tau_mm)
        out.append((key, label))
    return tuple(out)


class _InterManualTable(QTableWidget):
    """QTableWidget subclass with Excel-friendly Ctrl+C semantics.

    When the user has the entire table selected (Ctrl+A), the headers
    are prepended to the clipboard payload as the first line — so a paste
    into Excel lands a complete sheet with column labels. For partial
    selections only the cell rectangle is copied (matches Excel's own
    behaviour).
    """

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_selection_to_clipboard()
            event.accept()
            return
        super().keyPressEvent(event)

    def _copy_selection_to_clipboard(self) -> None:
        ranges = self.selectedRanges()
        if not ranges:
            return
        top = min(r.topRow() for r in ranges)
        bottom = max(r.bottomRow() for r in ranges)
        left = min(r.leftColumn() for r in ranges)
        right = max(r.rightColumn() for r in ranges)
        all_rows = top == 0 and bottom == self.rowCount() - 1
        all_cols = left == 0 and right == self.columnCount() - 1
        lines: list[str] = []
        if all_rows and all_cols:
            headers = []
            for c in range(left, right + 1):
                item = self.horizontalHeaderItem(c)
                headers.append(item.text() if item is not None else "")
            lines.append("\t".join(headers))
        for r in range(top, bottom + 1):
            cells = []
            for c in range(left, right + 1):
                item = self.item(r, c)
                cells.append(item.text() if item is not None else "")
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))


class _InterManualMetricsDialog(QDialog):
    """Modal popup showing pairwise rater metrics for one OR MORE groups.

    The table has one row per (group × organ × rater-A × rater-B) tuple,
    with leading Patient + Source-label columns when multiple groups are
    merged into a single view. Three paths get the data out:

    - **Ctrl+A → Ctrl+C** copies as TSV with column headers prepended
      (Excel-friendly — paste directly into a sheet).
    - **Copy all to clipboard** button does the same in one click.
    - **Export CSV…** button writes a CSV file via the standard save dialog.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        n_groups: int = 1,
        cancelled: bool = False,
        sd_tau_mm: float | None = None,
        apl_tau_mm: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Inter-observer variability — {n_groups} group(s)")
        self.resize(1200, 600)
        self._n_groups = n_groups
        # Resolve column labels with the τ values baked in so headers
        # read e.g. "Surface Dice @ 3.00 mm" / "Mean APL @ 3.00 mm".
        self._inter_manual_columns = _inter_manual_columns(sd_tau_mm, apl_tau_mm)
        layout = QVBoxLayout(self)
        info_html = f"<b>Groups:</b> {n_groups}  &nbsp; <b>Comparisons:</b> {len(rows)}"
        if sd_tau_mm is not None or apl_tau_mm is not None:
            tol_bits = []
            if sd_tau_mm is not None:
                tol_bits.append(f"Surface Dice τ = {sd_tau_mm:.2f} mm")
            if apl_tau_mm is not None:
                tol_bits.append(f"APL τ = {apl_tau_mm:.2f} mm")
            info_html += "  &nbsp; <b>" + " · ".join(tol_bits) + "</b>"
        if cancelled:
            info_html += (
                "  &nbsp; <span style='color:#d96b00'><b>Cancelled — partial results</b></span>"
            )
        info_html += (
            "<br><span style='color:#666'>Pairwise rater comparison BEFORE the STAPLE "
            "consensus is computed. Ctrl+A → Ctrl+C copies the table with column "
            "headers; Export CSV writes a file.</span>"
        )
        info = QLabel(info_html, self)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        layout.addWidget(info)

        # Rater A / Rater B carry the observer source labels, so no separate
        # source-label column is needed (v2.4 — distinct label per observer).
        meta_headers = ["Patient", "Organ", "Rater A", "Rater B"]
        metric_headers = [label for _, label in self._inter_manual_columns]
        all_headers = meta_headers + metric_headers
        self._table = _InterManualTable(self)
        self._table.setColumnCount(len(all_headers))
        self._table.setRowCount(len(rows))
        self._table.setHorizontalHeaderLabels(all_headers)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        was_sorted = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        for r_idx, row in enumerate(rows):
            metrics = row.get("metrics") or {}
            self._set_cell(r_idx, 0, str(row.get("patient_id", "")))
            self._set_cell(r_idx, 1, str(row.get("organ", "")))
            self._set_cell(r_idx, 2, str(row.get("rater_a", "")))
            self._set_cell(r_idx, 3, str(row.get("rater_b", "")))
            for col_offset, (key, _label) in enumerate(self._inter_manual_columns):
                value = metrics.get(key, "")
                self._set_cell(r_idx, 4 + col_offset, self._fmt(value))
        self._table.setSortingEnabled(was_sorted)
        layout.addWidget(self._table, stretch=1)

        # Footer — actions + close
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        copy_btn = QPushButton("Copy all to clipboard (TSV)", self)
        copy_btn.setToolTip(
            "Copy every row + column-header row to the clipboard as tab-"
            "separated values. Paste directly into Excel."
        )
        copy_btn.clicked.connect(self._on_copy_all)
        btns.addButton(copy_btn, QDialogButtonBox.ButtonRole.ActionRole)
        export_btn = QPushButton("Export CSV…", self)
        export_btn.setToolTip("Write this table to a CSV file.")
        export_btn.clicked.connect(self._on_export_csv)
        btns.addButton(export_btn, QDialogButtonBox.ButtonRole.ActionRole)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    @staticmethod
    def _fmt(value: Any) -> str:
        import math as _math

        if value is None or value == "":
            return ""
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, float):
            if _math.isnan(value):
                return ""
            return f"{value:.4g}"
        return str(value)

    def _set_cell(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, col, item)

    def _all_rows_as_lists(self) -> tuple[list[str], list[list[str]]]:
        """Return (headers, [row_cells, ...]) for the whole table."""
        n_cols = self._table.columnCount()
        n_rows = self._table.rowCount()
        headers = [
            self._table.horizontalHeaderItem(c).text()
            if self._table.horizontalHeaderItem(c)
            else ""
            for c in range(n_cols)
        ]
        rows: list[list[str]] = []
        for r in range(n_rows):
            rows.append(
                [
                    self._table.item(r, c).text() if self._table.item(r, c) is not None else ""
                    for c in range(n_cols)
                ]
            )
        return headers, rows

    def _on_copy_all(self) -> None:
        headers, rows = self._all_rows_as_lists()
        lines = ["\t".join(headers)] + ["\t".join(r) for r in rows]
        QApplication.clipboard().setText("\n".join(lines))

    def _on_export_csv(self) -> None:
        suggested = f"inter_observer_variability_{self._n_groups}groups.csv".replace(" ", "_")
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export inter-observer variability",
            suggested,
            "CSV files (*.csv);;All files (*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
        headers, rows = self._all_rows_as_lists()
        try:
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(rows)
        except OSError as exc:
            QMessageBox.critical(self, "Export CSV", f"Could not write file:\n{exc}")
            return
        QMessageBox.information(self, "Export CSV", f"Exported {len(rows)} row(s) to {path}.")


# ---- Inter-observer settings dialog ------------------------------------


class _InterObserverSettingsDialog(QDialog):
    """Modal pre-flight settings for the inter-observer computation.

    Lets the user pick which geometric metrics to compute and override
    the Surface Dice / APL tolerance values for this run. Defaults are
    inherited from Tab 4's tolerances (passed in by the caller) plus
    all geometric metrics on (except APL, which is off by default to
    match Tab 4's geometric defaults). Returns the config dict via
    :meth:`metric_config` after Accept.
    """

    def __init__(
        self,
        n_groups: int,
        default_sd_tau_mm: float = 3.0,
        default_apl_tau_mm: float = 3.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Inter-observer variability — settings")
        self.resize(420, 400)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"<b>{n_groups} group(s) selected.</b>  "
                "Pick which metrics to compute and override the tolerance "
                "values for this run.",
                self,
            )
        )

        # Metric checkboxes — sensible default set (all geom on except APL,
        # which is expensive and rarely needed for inter-observer studies).
        metrics_box = QGroupBox("Metrics to compute", self)
        metrics_layout = QVBoxLayout(metrics_box)
        self._metric_boxes: dict[str, QCheckBox] = {}
        defaults = {
            "dice": True,
            "surface_dice": True,
            "hausdorff100": True,
            "hausdorff95": True,
            "mean_surface_distance": True,
            "apl_mean": False,
            "apl_total": False,
            "volume": True,
            "com_offset": True,
        }
        labels = {
            "dice": "Dice",
            "surface_dice": "Surface Dice (uses τ below)",
            "hausdorff100": "Hausdorff 100%",
            "hausdorff95": "Hausdorff 95%",
            "mean_surface_distance": "Mean Surface Distance",
            "apl_mean": "Mean APL (uses τ below)",
            "apl_total": "Total APL (uses τ below)",
            "volume": "Volume + diff/ratio",
            "com_offset": "Centre-of-mass offset",
        }
        for key, default_on in defaults.items():
            cb = QCheckBox(labels[key], metrics_box)
            cb.setChecked(default_on)
            metrics_layout.addWidget(cb)
            self._metric_boxes[key] = cb
        layout.addWidget(metrics_box)

        # Tolerance overrides — only relevant when surface_dice / apl
        # are enabled, but we always show them so the user can pre-set.
        tol_box = QGroupBox("Tolerances", self)
        tol_form = QFormLayout(tol_box)
        self._sd_tau_spin = QDoubleSpinBox(tol_box)
        self._sd_tau_spin.setRange(0.0, 100.0)
        self._sd_tau_spin.setSingleStep(0.1)
        self._sd_tau_spin.setDecimals(2)
        self._sd_tau_spin.setSuffix(" mm")
        self._sd_tau_spin.setValue(default_sd_tau_mm)
        tol_form.addRow("Surface Dice τ:", self._sd_tau_spin)
        self._apl_tau_spin = QDoubleSpinBox(tol_box)
        self._apl_tau_spin.setRange(0.0, 100.0)
        self._apl_tau_spin.setSingleStep(0.1)
        self._apl_tau_spin.setDecimals(2)
        self._apl_tau_spin.setSuffix(" mm")
        self._apl_tau_spin.setValue(default_apl_tau_mm)
        tol_form.addRow("APL τ:", self._apl_tau_spin)
        layout.addWidget(tol_box)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def metric_config(self) -> dict[str, Any]:
        """Return the ``config`` dict accepted by ``compute_geometric_metrics``."""
        return {
            "geometric": {key: cb.isChecked() for key, cb in self._metric_boxes.items()},
            "tolerances": {
                "surface_dice_tau_mm": float(self._sd_tau_spin.value()),
                "apl_tolerance_mm": float(self._apl_tau_spin.value()),
            },
        }
