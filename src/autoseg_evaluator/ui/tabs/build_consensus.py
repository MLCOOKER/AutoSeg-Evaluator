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

import time
from collections import defaultdict
from typing import Any

import csv
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
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

    def __init__(self, settings: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings or {}
        self._library: MetadataLibrary | None = None
        # group_key = (patient_id, source_label) — the unit of consensus building.
        # group_drawers[group_key] = {organ_canonical_name: [(rtss_sop_uid, roi_number, roi_name), ...]}
        self._group_drawers: dict[tuple[str, str], dict[str, list[tuple[str, int, str]]]] = {}
        self._synonyms_flat = self._load_synonyms()
        self._build_ui()
        self._refresh_state()

    # ---- Public API -------------------------------------------------------

    def set_library(self, library: MetadataLibrary | None) -> None:
        """Called by MainWindow after a folder scan completes."""
        self._library = library
        self._group_drawers.clear()
        self._refresh_state()

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

        Returns a list of dicts shaped like::

            [{
                "patient_id": ...,
                "source_label": ...,           # the manual-rater label
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

        The next session restore re-creates the same synthetic RTSSes by
        calling :meth:`apply_session_state` with this list AFTER the
        library has been rescanned.
        """
        out: list[dict[str, Any]] = []
        if self._library is None:
            return out
        for pid, for_uid, syn in self._library.synthetic_consensus_entries():
            organs_payload: list[dict[str, Any]] = []
            organ_by_roi_number = {o.roi_number: o for o in syn.organs}
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
            out.append(
                {
                    "patient_id": pid,
                    "source_label": syn.source_label,
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
            source_label = str(group.get("source_label", "") or CONSENSUS_SOURCE_LABEL)
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
                source_label=source_label,
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
        self._help_btn = QPushButton("Help", self)
        self._help_btn.setToolTip("Show a workflow explanation for this tab")
        self._help_btn.clicked.connect(self._on_help_clicked)
        toolbar.addWidget(self._help_btn)
        outer.addLayout(toolbar)

        # ---- Empty-state placeholder ----
        self._empty_label = QLabel(
            "<i>This tab activates when the loaded folder contains two or more RTSSes "
            "sharing a source label (e.g. multiple manual contours). Use Manage Source "
            "Labels in Tab 1 to retag files if needed.</i>",
            self,
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet("color: #888; padding: 48px;")
        outer.addWidget(self._empty_label)

        # ---- Main split (groups on left, drawers on right) ----
        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)

        # Groups list
        group_pane = QFrame(self._splitter)
        group_layout = QVBoxLayout(group_pane)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.addWidget(QLabel("<b>Eligible groups</b>", group_pane))
        group_layout.addWidget(
            QLabel(
                "<span style='color:#666; font-size: 9pt'>"
                "Each row = one patient + source label with 2+ RTSSes. "
                "Click to inspect the organ matches on the right.</span>",
                group_pane,
            )
        )
        self._groups_list = QListWidget(group_pane)
        self._groups_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._groups_list.currentItemChanged.connect(self._on_group_selected)
        group_layout.addWidget(self._groups_list, stretch=1)
        self._splitter.addWidget(group_pane)

        # Drawers area
        drawers_pane = QFrame(self._splitter)
        drawers_layout = QVBoxLayout(drawers_pane)
        drawers_layout.setContentsMargins(0, 0, 0, 0)
        drawers_layout.addWidget(QLabel("<b>Organ groupings for the selected group</b>", drawers_pane))
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

        self._auto_btn = QPushButton("Re-run auto-match for selected group", self)
        self._auto_btn.setToolTip(
            "Re-applies the Levenshtein + cosine matcher to regroup ROIs across "
            "the selected group's RTSSes. Useful after editing replacement rules."
        )
        self._auto_btn.clicked.connect(self._on_auto_match_clicked)
        footer.addWidget(self._auto_btn)

        self._inter_manual_btn = QPushButton("Show inter-manual metrics for selected group…", self)
        self._inter_manual_btn.setToolTip(
            "Compute pairwise Dice, Hausdorff, mean surface distance, and "
            "volume difference between every pair of raters for every "
            "matched organ in the selected group, then open a popup table. "
            "Useful for quantifying inter-observer agreement before the "
            "consensus is generated (ICRU Report 91)."
        )
        self._inter_manual_btn.clicked.connect(self._on_inter_manual_clicked)
        footer.addWidget(self._inter_manual_btn)

        footer.addStretch(1)
        self._generate_btn = QPushButton("Generate Consensus GT for ALL groups", self)
        self._generate_btn.setToolTip(
            "Register one synthetic STAPLE-consensus RTSS per eligible group. "
            "Generated entries appear in subsequent tabs with source label "
            f"'{CONSENSUS_SOURCE_LABEL}'."
        )
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        footer.addWidget(self._generate_btn)
        outer.addLayout(footer)

    # ---- State management -------------------------------------------------

    def _refresh_state(self) -> None:
        """Detect eligible groups, populate the left list, drive grey/active states."""
        eligible = self._eligible_groups()
        active = bool(eligible)
        self._empty_label.setVisible(not active)
        self._splitter.setVisible(active)
        self._auto_btn.setEnabled(active)
        self._generate_btn.setEnabled(active)
        self._inter_manual_btn.setEnabled(active)
        self._groups_list.clear()
        if not active:
            self._status_label.setText("No eligible groups in the current library.")
            self._clear_drawers()
            return
        for (pid, label), rtsses in eligible.items():
            item = QListWidgetItem(f"{pid}   {label}   ({len(rtsses)} RTSS)")
            item.setData(Qt.ItemDataRole.UserRole, (pid, label))
            item.setToolTip(
                "\n".join(f"• {r.filename}" for r in rtsses)
            )
            self._groups_list.addItem(item)
        self._status_label.setText(
            f"{len(eligible)} eligible group(s) — select one to inspect its organ matches."
        )
        # Auto-select first group + auto-match all groups so drawers populate.
        for (pid, label), _ in eligible.items():
            self._auto_match_group(pid, label)
        self._groups_list.setCurrentRow(0)

    def _eligible_groups(self) -> dict[tuple[str, str], list[RTSTRUCTEntry]]:
        """Return ``{(patient_id, source_label): [RTSTRUCTEntry, …]}`` for groups with 2+ RTSSes.

        Excludes synthetic consensus entries from consideration so we don't
        chain consensus-of-consensus accidentally.
        """
        out: dict[tuple[str, str], list[RTSTRUCTEntry]] = {}
        if self._library is None:
            return out
        for pid, patient in self._library.patients.items():
            grouped: dict[str, list[RTSTRUCTEntry]] = defaultdict(list)
            for ctx in patient.contexts:
                for r in ctx.rtstructs:
                    if r.is_synthetic_consensus:
                        continue
                    if not r.source_label:
                        continue
                    grouped[r.source_label].append(r)
            for label, rs in grouped.items():
                if len(rs) >= 2:
                    out[(pid, label)] = rs
        return out

    # ---- Auto-match -------------------------------------------------------

    def _auto_match_group(self, patient_id: str, source_label: str) -> None:
        """Group ROIs across the RTSSes in (patient_id, source_label) into organ buckets.

        Uses **similarity-threshold clustering**: each ROI is compared against
        the existing buckets via the hybrid Levenshtein + cosine matcher (with
        TG-263 dictionary + user replacement rules). An ROI joins a bucket if
        its best similarity to any bucket member meets the user-configured
        threshold; otherwise it seeds a new bucket. Single-rater buckets are
        dropped silently — STAPLE requires ≥2 raters per organ.

        Compared to the previous canonical-key approach this lets near-matches
        cluster together (e.g. ``Brainstem`` and ``BrainStem_PRV0`` at
        threshold 0.85), and the user can tune aggressiveness via the spinbox.
        """
        rtsses = self._eligible_groups().get((patient_id, source_label), [])
        if not rtsses:
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
            for organ in r.organs:
                name = str(organ.roi_name or "").strip()
                if not name:
                    continue
                best_idx = -1
                best_score = -1.0
                for i, rep in enumerate(bucket_names):
                    if i in rtss_used_buckets:
                        continue
                    s = similarity(
                        name, rep, rules=rules, synonyms_flat=self._synonyms_flat
                    ).score
                    if s > best_score:
                        best_score = s
                        best_idx = i
                if best_idx >= 0 and best_score >= threshold:
                    bucket_members[best_idx].append(
                        (r.sop_instance_uid, int(organ.roi_number), name)
                    )
                    rtss_used_buckets.add(best_idx)
                else:
                    bucket_names.append(name)
                    bucket_members.append(
                        [(r.sop_instance_uid, int(organ.roi_number), name)]
                    )
                    rtss_used_buckets.add(len(bucket_names) - 1)
        # Drop single-rater buckets, then key the result by the most
        # frequent ROI name within each bucket (so the drawer header
        # reads as something meaningful to the user).
        kept: dict[str, list[tuple[str, int, str]]] = {}
        for rep, members in zip(bucket_names, bucket_members):
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
        self._group_drawers[(patient_id, source_label)] = kept

    def _on_threshold_changed(self, _value: float) -> None:
        """Re-cluster every eligible group whenever the threshold spinbox moves."""
        for (pid, label) in list(self._group_drawers.keys()):
            self._auto_match_group(pid, label)
        # Re-render the currently-selected group's drawers if any.
        current = self._groups_list.currentItem()
        if current is not None:
            self._on_group_selected(current, None)

    # ---- Drawer rendering -------------------------------------------------

    def _on_group_selected(self, current: QListWidgetItem | None, _previous) -> None:
        self._clear_drawers()
        if current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        drawers = self._group_drawers.get(tuple(key), {})
        if not drawers:
            placeholder = QLabel(
                "<i>No organs match across 2+ of this group's RTSSes. "
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
            filename = self._rtstruct_filename_for(sop_uid)
            line = QLabel(
                f"<span style='color:#444'>• {roi_name}</span>"
                f"   <span style='color:#888; font-size: 9pt'>({filename}, ROI #{roi_number})</span>",
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

    def _on_auto_match_clicked(self) -> None:
        current = self._groups_list.currentItem()
        if current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        pid, label = key
        self._auto_match_group(pid, label)
        # Re-render selected
        self._on_group_selected(current, None)

    def _on_generate_clicked(self) -> None:
        if self._library is None or not self._group_drawers:
            QMessageBox.information(
                self,
                "Generate Consensus GT",
                "No eligible groups to generate consensus for.",
            )
            return
        # Clear any previously-generated synthetic entries before re-registering.
        self._library.clear_synthetic_consensus()
        created = 0
        skipped: list[str] = []
        for (pid, label), buckets in self._group_drawers.items():
            if not buckets:
                skipped.append(f"{pid}/{label}: no matched organs")
                continue
            entry = self._build_synthetic_entry(pid, label, buckets)
            if entry is None:
                skipped.append(f"{pid}/{label}: no shared FrameOfReferenceUID")
                continue
            ok = self._library.register_synthetic_consensus(pid, entry.frame_of_reference_uid, entry)
            if ok:
                created += 1
            else:
                skipped.append(f"{pid}/{label}: register failed")
        msg = [f"Generated {created} consensus RTSS entr{'y' if created == 1 else 'ies'}."]
        if skipped:
            msg.append("Skipped:")
            msg.extend(f"  • {s}" for s in skipped)
        QMessageBox.information(self, "Generate Consensus GT", "\n".join(msg))
        if created:
            self.consensusGenerated.emit()

    def _build_synthetic_entry(
        self,
        patient_id: str,
        source_label: str,
        buckets: dict[str, list[tuple[str, int, str]]],
    ) -> RTSTRUCTEntry | None:
        """Assemble the synthetic RTSTRUCTEntry that represents this group's consensus."""
        if self._library is None:
            return None
        # All constituent RTSSes must share a FrameOfReferenceUID for STAPLE to work.
        # The eligible_groups detector groups by (patient, source_label) but a
        # single source label could span contexts — pick the FoR with the
        # most contributors here.
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
        for synthetic_roi_number, (canon, raters) in enumerate(sorted(buckets.items()), start=1):
            # Display name: use the most common original ROI name across raters.
            name_counts: dict[str, int] = defaultdict(int)
            for _sop, _roi, roi_name in raters:
                name_counts[roi_name] += 1
            display_name = max(name_counts.items(), key=lambda kv: kv[1])[0]
            organs.append(OrganEntry(roi_number=synthetic_roi_number, roi_name=display_name))
            constituent_groups[synthetic_roi_number] = [
                (sop, int(roi_num)) for (sop, roi_num, _name) in raters
            ]
        synthetic_sop = self._mint_synthetic_uid(patient_id, source_label)
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

    def _mint_synthetic_uid(self, patient_id: str, source_label: str) -> str:
        """Generate a deterministic-ish UID for a synthetic RTSS.

        Not a real DICOM UID — uses our own prefix so it can never collide
        with anything pydicom would read off disk. Same input produces the
        same UID within a session, which keeps the cache stable when the
        user re-runs Generate.
        """
        token = f"{patient_id}|{source_label}|{int(time.time() // 60)}"
        return f"AUTOSEG.SYNTHETIC.{abs(hash(token)) % (10 ** 18)}"

    # ---- Inter-manual metrics --------------------------------------------

    def _on_inter_manual_clicked(self) -> None:
        """Compute pairwise rater metrics for the selected group's organs.

        Loads the patient's CT, rasterises each constituent's mask, then
        runs the existing geometric metric pipeline on every (rater A,
        rater B) pair within each organ bucket. Results are shown in a
        modal dialog with a table view + CSV-friendly copy.

        Synchronous in the UI thread for simplicity — a typical group
        (3–5 raters × ~10 organs) finishes in a few seconds. A wait
        cursor is set so the user knows the app is working.
        """
        if self._library is None:
            return
        current = self._groups_list.currentItem()
        if current is None:
            QMessageBox.information(
                self,
                "Inter-manual metrics",
                "Select a group on the left first.",
            )
            return
        pid, source_label = current.data(Qt.ItemDataRole.UserRole)
        buckets = self._group_drawers.get((pid, source_label), {})
        if not buckets:
            QMessageBox.information(
                self,
                "Inter-manual metrics",
                "No matched organs to evaluate for this group.",
            )
            return

        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rows = self._compute_inter_manual_rows(pid, buckets)
        finally:
            QGuiApplication.restoreOverrideCursor()

        if not rows:
            QMessageBox.warning(
                self,
                "Inter-manual metrics",
                "Could not load the CT or rasterise any constituent masks "
                "for this group. Check the Load Data tab for missing files.",
            )
            return
        dlg = _InterManualMetricsDialog(rows, pid, source_label, parent=self)
        dlg.exec()

    def _compute_inter_manual_rows(
        self,
        patient_id: str,
        buckets: dict[str, list[tuple[str, int, str]]],
    ) -> list[dict[str, Any]]:
        """Build the pairwise-metric row list for the inter-manual dialog.

        Returns one row per (organ × pair-of-raters). Each row dict has
        ``organ``, ``rater_a``, ``rater_b``, ``metrics`` (a dict mirroring
        :func:`compute_geometric_metrics`'s output). Empty list on
        catastrophic failure (no CT, no library).
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

        # Use the same metric config the Compute tab would: all geometric
        # metrics on, default tolerances.
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
        for organ_name, members in sorted(buckets.items()):
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
                        mask_cache[key] = extract_mask_for_roi(
                            ct, rtss_cache[sop_uid], roi_number
                        )
                    except Exception:  # noqa: BLE001
                        mask_cache[key] = None
                m = mask_cache[key]
                if m is None:
                    continue
                rater_masks.append((self._rtstruct_filename_for(sop_uid), m))
            # Pairwise comparisons
            for i in range(len(rater_masks)):
                for j in range(i + 1, len(rater_masks)):
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
            "<p>When you have <b>2 or more RTSS files</b> for the same patient that "
            "share a source label (e.g. multiple manual contours from different "
            "clinicians, or repeat exports from one TPS), this tab combines them "
            "into a single STAPLE-derived <b>synthetic ground truth</b> that you can "
            "use in the downstream Match Contours / Compute workflow.</p>"
            "<p><b>How to use it</b></p>"
            "<ol>"
            "<li>Load your data in Tab 1. If your manual contours don't already "
            "share a label, use <i>Manage Source Labels</i> to tag them with a "
            "common label (e.g. 'Manual').</li>"
            "<li>Switch to this tab — eligible groups appear on the left.</li>"
            "<li>Click a group to inspect the auto-matched organs on the right. "
            "Single-rater organs are excluded automatically (STAPLE needs ≥2 raters).</li>"
            "<li>Click <b>Generate Consensus GT for ALL groups</b>. Each group's "
            "synthetic RTSS appears in Tab 3 with source label "
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


# ---- Inter-manual metrics dialog ----------------------------------------


# Display order matches the canonical results-table convention; "?" cells
# render as empty strings when the underlying metric wasn't computed.
_INTER_MANUAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("dice", "Dice"),
    ("surface_dice", "Surface Dice"),
    ("hausdorff100", "HD 100% (mm)"),
    ("hausdorff95", "HD 95% (mm)"),
    ("mean_surface_distance", "MSD (mm)"),
    ("volume_gt_cc", "Vol A (cc)"),
    ("volume_test_cc", "Vol B (cc)"),
    ("volume_diff_cc", "Vol diff (cc)"),
    ("volume_ratio", "Vol ratio"),
    ("com_offset_mm", "COM offset (mm)"),
)


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
    """Modal popup showing pairwise rater metrics for a single group.

    The table has one row per (organ × rater-A × rater-B) tuple. Three
    paths get the data out:

    - **Ctrl+A → Ctrl+C** copies as TSV with column headers prepended
      (Excel-friendly — paste directly into a sheet).
    - **Copy all to clipboard** button does the same in one click.
    - **Export CSV…** button writes a CSV file via the standard save dialog.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        patient_id: str,
        source_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            f"Inter-manual metrics — {patient_id} / {source_label}"
        )
        self.resize(1100, 600)
        self._patient_id = patient_id
        self._source_label = source_label
        layout = QVBoxLayout(self)
        info = QLabel(
            f"<b>Patient:</b> {patient_id}  &nbsp; <b>Source label:</b> {source_label}  "
            f"&nbsp; <b>Comparisons:</b> {len(rows)}<br>"
            "<span style='color:#666'>Pairwise rater comparison BEFORE the STAPLE "
            "consensus is computed. Ctrl+A → Ctrl+C copies the table with column "
            "headers; Export CSV writes a file.</span>",
            self,
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        layout.addWidget(info)

        meta_headers = ["Organ", "Rater A", "Rater B"]
        metric_headers = [label for _, label in _INTER_MANUAL_COLUMNS]
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
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

        was_sorted = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        for r_idx, row in enumerate(rows):
            metrics = row.get("metrics") or {}
            self._set_cell(r_idx, 0, str(row.get("organ", "")))
            self._set_cell(r_idx, 1, str(row.get("rater_a", "")))
            self._set_cell(r_idx, 2, str(row.get("rater_b", "")))
            for col_offset, (key, _label) in enumerate(_INTER_MANUAL_COLUMNS):
                value = metrics.get(key, "")
                self._set_cell(r_idx, 3 + col_offset, self._fmt(value))
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
            self._table.horizontalHeaderItem(c).text() if self._table.horizontalHeaderItem(c) else ""
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
        suggested = (
            f"inter_manual_{self._patient_id}_{self._source_label}.csv".replace(" ", "_")
        )
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export inter-manual metrics",
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
            QMessageBox.critical(
                self, "Export CSV", f"Could not write file:\n{exc}"
            )
            return
        QMessageBox.information(
            self, "Export CSV", f"Exported {len(rows)} row(s) to {path}."
        )
