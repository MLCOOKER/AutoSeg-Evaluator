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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
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
    QLabel,
    QListWidget,
    QListWidgetItem,
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
        # Warnings raised during eligibility detection (e.g. duplicate observer
        # label on one patient). Surfaced in the status line on each refresh.
        self._eligibility_warnings: list[str] = []
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

        # ---- Main split (groups on left, drawers on right) ----
        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)

        # Groups list
        group_pane = QFrame(self._splitter)
        group_layout = QVBoxLayout(group_pane)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.addWidget(QLabel("<b>Eligible patients</b>", group_pane))
        group_layout.addWidget(
            QLabel(
                "<span style='color:#666; font-size: 9pt'>"
                "Each row = one patient with 2+ observers. "
                "Click to inspect the organ matches on the right.</span>",
                group_pane,
            )
        )
        self._groups_list = QListWidget(group_pane)
        # ExtendedSelection so the user can Ctrl/Shift-click multiple groups
        # to compute inter-observer metrics across several patients at once.
        # The drawer-preview panel still tracks the *current* item (the last
        # one clicked) so single-group browsing still works.
        self._groups_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._groups_list.currentItemChanged.connect(self._on_group_selected)
        group_layout.addWidget(self._groups_list, stretch=1)
        self._splitter.addWidget(group_pane)

        # Drawers area
        drawers_pane = QFrame(self._splitter)
        drawers_layout = QVBoxLayout(drawers_pane)
        drawers_layout.setContentsMargins(0, 0, 0, 0)
        drawers_layout.addWidget(
            QLabel("<b>Organ groupings for the selected group</b>", drawers_pane)
        )
        drawers_layout.addWidget(
            QLabel(
                "<span style='color:#666; font-size: 9pt'>"
                "Each line = one organ that has contours in 2+ of the selected RTSSes "
                "(single-rater organs are excluded — STAPLE needs ≥2 raters). "
                "Auto-matched via Levenshtein + cosine + TG-263 dictionary.</span>",
                drawers_pane,
            )
        )
        self._drawers_scroll = QScrollArea(drawers_pane)
        self._drawers_scroll.setWidgetResizable(True)
        self._drawers_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._drawers_container = QWidget()
        self._drawers_v = QVBoxLayout(self._drawers_container)
        self._drawers_v.setContentsMargins(4, 4, 4, 4)
        self._drawers_v.setSpacing(4)
        self._drawers_v.addStretch(1)
        self._drawers_scroll.setWidget(self._drawers_container)
        drawers_layout.addWidget(self._drawers_scroll, stretch=1)
        self._splitter.addWidget(drawers_pane)

        self._splitter.setSizes([300, 700])
        outer.addWidget(self._splitter, stretch=1)

        # ---- Footer (threshold + actions) ----
        footer = QHBoxLayout()

        # Threshold spinbox — two ROIs across raters are grouped together
        # only if their similarity score is at or above this value. Default
        # 0.60 matches Tab 3's auto-match default. Higher values demand more
        # name agreement; lower values fold near-matches together.
        footer.addWidget(QLabel("Match threshold:", self))
        self._threshold_spin = QDoubleSpinBox(self)
        self._threshold_spin.setDecimals(2)
        self._threshold_spin.setRange(0.0, 1.0)
        self._threshold_spin.setSingleStep(0.05)
        self._threshold_spin.setValue(0.60)
        self._threshold_spin.setToolTip(
            "Two ROI names are grouped together if their hybrid Levenshtein + "
            "cosine similarity (with TG-263 dictionary) meets or exceeds this "
            "value. Lower → more aggressive grouping (more false positives). "
            "Higher → stricter (more false negatives). Default 0.60 matches "
            "the Match Contours auto-match default."
        )
        # Re-cluster all groups when the threshold changes so the drawers
        # update live.
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
        self._generate_selected_btn = QPushButton("Generate Consensus GT for SELECTED groups", self)
        self._generate_selected_btn.setToolTip(
            "Register a synthetic STAPLE-consensus RTSS for ONLY the groups "
            "currently highlighted on the left (Ctrl/Shift-click for "
            "multi-select). Existing consensus entries for other groups are "
            "left untouched. Re-running on a selected group replaces its "
            f"existing entry in place. Source label: '{CONSENSUS_SOURCE_LABEL}'."
        )
        self._generate_selected_btn.clicked.connect(self._on_generate_selected_clicked)
        footer.addWidget(self._generate_selected_btn)
        self._generate_btn = QPushButton("Generate Consensus GT for ALL groups", self)
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
        if self._eligibility_warnings:
            status += f"  ⚠ {len(self._eligibility_warnings)} labelling warning(s)."
            self._groups_list.setToolTip("\n".join(self._eligibility_warnings))
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
        self._eligibility_warnings = []
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
                    self._eligibility_warnings.append(
                        f"{pid}: {len(rs)} RTSSes labelled '{label}' — using the first. "
                        "Give each observer a distinct label in Tab 1."
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
        threshold = float(self._threshold_spin.value())
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
        # Drop single-rater buckets, then key the result by the most
        # frequent ROI name within each bucket (so the drawer header
        # reads as something meaningful to the user).
        kept: dict[str, list[tuple[str, int, str]]] = {}
        for _rep, members in zip(bucket_names, bucket_members):
            if len(members) < 2:
                continue
            name_counts: dict[str, int] = defaultdict(int)
            for _sop, _roi, n in members:
                name_counts[n] += 1
            display = max(name_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
            # If two buckets ended up with the same display name (rare), suffix
            # the second so they don't collapse in the drawer dict.
            if display in kept:
                suffix = 2
                while f"{display} #{suffix}" in kept:
                    suffix += 1
                display = f"{display} #{suffix}"
            kept[display] = members
        self._group_drawers[patient_id] = kept

    def _on_threshold_changed(self, _value: float) -> None:
        """Re-cluster every eligible group whenever the threshold spinbox moves."""
        for pid in list(self._group_drawers.keys()):
            self._auto_match_group(pid)
        # Re-render the currently-selected group's drawers if any.
        current = self._groups_list.currentItem()
        if current is not None:
            self._on_group_selected(current, None)

    # ---- Drawer rendering -------------------------------------------------

    def _on_group_selected(self, current: QListWidgetItem | None, _previous) -> None:
        self._clear_drawers()
        if current is None:
            return
        pid = current.data(Qt.ItemDataRole.UserRole)
        if not pid:
            return
        drawers = self._group_drawers.get(str(pid), {})
        if not drawers:
            placeholder = QLabel(
                "<i>No organs match across 2+ of this patient's observers. "
                "STAPLE requires at least 2 raters per organ.</i>",
                self._drawers_container,
            )
            placeholder.setStyleSheet("color: #888; padding: 12px;")
            self._drawers_v.insertWidget(self._drawers_v.count() - 1, placeholder)
            return
        # Render one row per organ bucket
        for canon, raters in sorted(drawers.items()):
            self._drawers_v.insertWidget(
                self._drawers_v.count() - 1,
                self._build_organ_row(canon, raters),
            )

    def _build_organ_row(
        self,
        canonical_name: str,
        raters: list[tuple[str, int, str]],
    ) -> QWidget:
        """Compact row showing the organ + its contributing RTSSes."""
        row = QFrame(self._drawers_container)
        row.setObjectName("ConsensusOrganRow")
        row.setFrameShape(QFrame.Shape.StyledPanel)
        row.setStyleSheet(
            "QFrame#ConsensusOrganRow { border: 1px solid #ddd; border-radius: 4px; padding: 6px; }"
        )
        layout = QVBoxLayout(row)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        header = QLabel(
            f"<b>{canonical_name}</b>  <span style='color:#888'>({len(raters)} raters)</span>",
            row,
        )
        layout.addWidget(header)
        for sop_uid, roi_number, roi_name in raters:
            # Identify each contributing rater by its source label (= observer
            # name) — consistent across patients. Filename is shown as a
            # secondary hint in case two observers were mislabelled.
            observer = self._source_label_for(sop_uid)
            filename = self._rtstruct_filename_for(sop_uid)
            line = QLabel(
                f"<span style='color:#444'>• <b>{observer}</b>: {roi_name}</span>"
                f"   <span style='color:#888; font-size: 9pt'>"
                f"({filename}, ROI #{roi_number})</span>",
                row,
            )
            layout.addWidget(line)
        return row

    def _clear_drawers(self) -> None:
        # Remove every widget except the trailing stretch
        while self._drawers_v.count() > 1:
            item = self._drawers_v.takeAt(0)
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
                skipped.append(f"{pid}: no shared FrameOfReferenceUID")
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
        # roi_number starting from 1.
        organs: list[OrganEntry] = []
        constituent_groups: dict[int, list[tuple[str, int]]] = {}
        for synthetic_roi_number, (_canon, raters) in enumerate(sorted(buckets.items()), start=1):
            # Display name: use the most common original ROI name across raters.
            name_counts: dict[str, int] = defaultdict(int)
            for _sop, _roi, roi_name in raters:
                name_counts[roi_name] += 1
            display_name = max(name_counts.items(), key=lambda kv: kv[1])[0]
            organs.append(OrganEntry(roi_number=synthetic_roi_number, roi_name=display_name))
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
            "every rater is identified by its observer label. Single-rater "
            "organs are excluded (STAPLE needs ≥2 raters).</li>"
            "<li>Optionally compute <b>inter-observer variability</b> first — the "
            "pairwise matrix labels raters by observer consistently across "
            "patients.</li>"
            "<li>Click <b>Generate Consensus GT for ALL patients</b>. One "
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
