"""Tab 2 — Match Contours.

Layout:
    ┌─────────────────────────────────────────────────────────────────────┐
    │ Auto-match workflow                                                 │
    │  [1. Replacement Rules]  →  [2. Define Template]  →  [3. Run Match] │
    │ ──────────────────────────────────────────────────────────────────── │
    │ Loaded Contours          │  Matched Organs   [Remove All Poor Matches]│
    │ (LoadedContoursTree)     │  (scrollable accordion of OrganDrawers)  │
    │                          │                                          │
    │ [Set Ground Truth] [Add Selected]                                   │
    └─────────────────────────────────────────────────────────────────────┘

Step 4 wires the interaction logic:

* "Set Ground Truth" creates an OrganDrawer for each selected organ and
  immediately auto-matches the closest-named organ from every OTHER RTSS
  file in the patient's imaging context (no threshold gating — just the
  best string match per file). Below-threshold matches still get a ⚠ flag.
* "Add Selected" adds the tree-selected organs to the currently focused
  drawer. It is disabled when no drawer is focused.
* Drag-and-drop from the tree onto a drawer adds tests to that drawer.
* Per-drawer "Remove Poor Matches" and the toolbar "Remove All Poor Matches"
  strip below-threshold tests.
* Removing a drawer / patient sub-section / test row cleans up the ✓ marks
  on the left-tree organs.

Step 5 will replace the difflib-based similarity used here with the full
rapidfuzz + synonyms + replacement-rules pipeline.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.core.matching import (
    ReplacementRule,
    best_match,
    similarity,
)
from autoseg_evaluator.core.masks import (
    extract_mask_for_roi,
    find_reference_image_folder,
    read_dicom_image,
    read_rtstruct,
    truncate_to_gt_z_extent,
)
from autoseg_evaluator.data.metadata import (
    MetadataLibrary,
    OrganEntry,
    RTSTRUCTEntry,
)
from autoseg_evaluator.data.synonyms import flatten_synonyms, load_synonyms
from autoseg_evaluator.ui.dialogs.replacement_rules import ReplacementRulesDialog
from autoseg_evaluator.ui.dialogs.template import TemplateDialog
from autoseg_evaluator.ui.dialogs.visualization import VisualizationWindow
from autoseg_evaluator.ui.widgets.loaded_contours_tree import LoadedContoursTree
from autoseg_evaluator.ui.widgets.organ_drawer import (
    OrganDrawer,
    PatientSubsection,
    TestRow,
)
from autoseg_evaluator.utils.paths import synonyms_path


_STATUS_VISIBLE_MS = 6000


class MatchContoursTab(QWidget):
    """The Match Contours screen — left panel (tree) + right panel (drawers)."""

    # Workflow strip signals (kept for back-compat; the tab now handles them itself).
    replacementRulesRequested = Signal()
    defineTemplateRequested = Signal()
    runAutoMatchRequested = Signal()
    # Settings-change signals — MainWindow listens to persist to settings.json.
    replacementRulesChanged = Signal(list)
    templateChanged = Signal(dict)

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._library: MetadataLibrary | None = None
        self._settings: dict[str, Any] = settings if settings is not None else {}
        self._drawers: dict[str, OrganDrawer] = {}  # organ_name → drawer
        self._focused_drawer: OrganDrawer | None = None
        self._synonyms_flat: dict[str, str] = flatten_synonyms(load_synonyms(synonyms_path()))

        self._build_ui()

    # ---- Public API -------------------------------------------------------

    def set_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings

    def set_library(self, library: MetadataLibrary | None) -> None:
        """Inject the metadata library (called by MainWindow after Tab 1 loads)."""
        self._library = library
        # Reset everything: previously-built drawers reference SOPInstanceUIDs
        # from the prior cohort and aren't meaningful against the new one.
        self._reset_drawers()
        if library is None:
            self._tree.clear()
            self._set_empty_state(True)
            return
        self._tree.populate(library)
        self._set_empty_state(False)

    def drawer_for_organ(self, organ_name: str) -> OrganDrawer | None:
        return self._drawers.get(organ_name)

    def all_drawers(self) -> list[OrganDrawer]:
        return [self._drawers[k] for k in sorted(self._drawers)]

    # ---- Session snapshot / restore --------------------------------------

    def session_state(self) -> list[dict[str, Any]]:
        """Return a JSON-friendly snapshot of every drawer's contents."""
        out: list[dict[str, Any]] = []
        for organ_name in sorted(self._drawers):
            drawer = self._drawers[organ_name]
            patients = []
            for sub in drawer.all_subsections():
                patients.append(
                    {
                        "patient_id": sub.patient_id,
                        "gt": {
                            "rtstruct_sop_uid": sub.gt_rtstruct_sop_uid,
                            "rtstruct_filename": sub.gt_rtstruct_filename,
                            "source_label": sub.gt_source_label,
                            "roi_number": sub.gt_roi_number,
                            "roi_name": sub.gt_roi_name,
                        },
                        "tests": [
                            {
                                "rtstruct_sop_uid": t.rtstruct_sop_uid,
                                "source_label": t.source_label,
                                "organ_name": t.organ_name,
                                "roi_number": t.roi_number,
                                "similarity": float(t.similarity),
                                "below_threshold": bool(t.below_threshold),
                                "match_method": str(t.match_method),
                            }
                            for t in sub.tests
                        ],
                    }
                )
            out.append(
                {
                    "organ_name": drawer.organ_name(),
                    "truncate": drawer.truncate_enabled(),
                    "gt_comparison": drawer.gt_comparison_enabled(),
                    "staple_consensus": drawer.staple_consensus_enabled(),
                    "staple_include_gt": drawer.staple_include_gt(),
                    "expanded": drawer.isExpanded(),
                    "patients": patients,
                }
            )
        return out

    def apply_session_state(
        self, drawers_state: list[dict[str, Any]]
    ) -> tuple[int, int, list[str]]:
        """Rebuild drawers from a saved snapshot against the currently loaded library.

        Returns ``(applied, missing, warnings)`` — number of patient sub-sections
        successfully restored, number skipped, and a list of human-readable
        warnings for each skip.
        """
        if self._library is None:
            return 0, 0, ["No library loaded; cannot restore session."]

        # Wipe existing drawers so the restore is deterministic
        self._reset_drawers()
        self._tree.clear_marks()

        applied = 0
        missing = 0
        warnings: list[str] = []

        for d in drawers_state or []:
            organ_name = str(d.get("organ_name", "") or "").strip()
            if not organ_name:
                continue
            drawer = self.add_drawer(organ_name)
            drawer.set_truncate(bool(d.get("truncate", False)))
            drawer.set_gt_comparison(bool(d.get("gt_comparison", True)))
            drawer.set_staple_consensus(bool(d.get("staple_consensus", False)))
            drawer.set_staple_include_gt(bool(d.get("staple_include_gt", True)))

            for p in d.get("patients", []) or []:
                patient_id = str(p.get("patient_id", "") or "")
                gt = p.get("gt", {}) or {}
                gt_sop = str(gt.get("rtstruct_sop_uid", "") or "")
                try:
                    gt_roi = int(gt.get("roi_number", 0) or 0)
                except (TypeError, ValueError):
                    gt_roi = 0
                rtss = _find_rtstruct(self._library, patient_id, gt_sop)
                if rtss is None or _find_organ(rtss, gt_roi) is None:
                    missing += 1
                    warnings.append(
                        f"{patient_id}/{organ_name}: GT RTSS ({_short_sop(gt_sop)}) "
                        f"or ROI #{gt_roi} not present in the loaded folder."
                    )
                    continue

                tests: list[TestRow] = []
                for t in p.get("tests", []) or []:
                    t_sop = str(t.get("rtstruct_sop_uid", "") or "")
                    try:
                        t_roi = int(t.get("roi_number", 0) or 0)
                    except (TypeError, ValueError):
                        t_roi = 0
                    test_rtss = _find_rtstruct(self._library, patient_id, t_sop)
                    if test_rtss is None or _find_organ(test_rtss, t_roi) is None:
                        warnings.append(
                            f"{patient_id}/{organ_name}: test "
                            f"({t.get('source_label', '?')}) not found — skipped."
                        )
                        continue
                    try:
                        sim = float(t.get("similarity", 0.0))
                    except (TypeError, ValueError):
                        sim = 0.0
                    tests.append(
                        TestRow(
                            source_label=str(t.get("source_label", test_rtss.source_label)),
                            organ_name=str(t.get("organ_name", "")),
                            rtstruct_sop_uid=t_sop,
                            roi_number=t_roi,
                            similarity=sim,
                            below_threshold=bool(t.get("below_threshold", False)),
                            match_method=str(t.get("match_method", "fuzzy")),
                        )
                    )

                subsection = PatientSubsection(
                    patient_id=patient_id,
                    gt_rtstruct_sop_uid=gt_sop,
                    gt_rtstruct_filename=str(
                        gt.get("rtstruct_filename", rtss.filename)
                    ),
                    gt_source_label=str(gt.get("source_label", rtss.source_label)),
                    gt_roi_number=gt_roi,
                    gt_roi_name=str(gt.get("roi_name", "")),
                    tests=tests,
                )
                drawer.add_patient(subsection)
                applied += 1

            if d.get("expanded", False):
                drawer.expand()
            else:
                drawer.collapse()

        self._resync_tree_marks()
        return applied, missing, warnings

    def add_drawer(self, organ_name: str) -> OrganDrawer:
        """Create or fetch the drawer for ``organ_name`` (strict literal match).

        Drawer keys use the exact ROI name as the GT presents it. Slightly
        different GT names (e.g. "Brainstem" vs "Brain Stem") produce
        separate drawers; this is intentional to avoid false merges of
        anatomically distinct organs whose names look superficially similar
        after normalisation (``OpticNerve_L`` / ``OpticNerve_R`` etc.).
        """
        if organ_name in self._drawers:
            return self._drawers[organ_name]
        drawer = OrganDrawer(organ_name, parent=self._drawers_container)
        drawer.removeRequested.connect(lambda name=organ_name: self._on_remove_drawer(name))
        drawer.removePatientRequested.connect(
            lambda pid, name=organ_name: self._on_remove_patient(name, pid)
        )
        drawer.removeTestRequested.connect(
            lambda pid, sop, roi, name=organ_name: self._on_remove_test(name, pid, sop, roi)
        )
        drawer.removeRedRequested.connect(lambda name=organ_name: self._on_remove_red(name))
        drawer.addSelectedRequested.connect(lambda name=organ_name: self._on_drawer_add_selected(name))
        drawer.visualizeRequested.connect(
            lambda pid, name=organ_name: self._on_visualize(name, pid)
        )
        drawer.organsDropped.connect(
            lambda organs, name=organ_name: self._on_drop_on_drawer(name, organs)
        )
        drawer.focusChanged.connect(
            lambda focused, d=drawer: self._on_drawer_focus_changed(d, focused)
        )
        drawer.headerWidget().mousePressEvent = self._make_focus_grabber(drawer)  # type: ignore[assignment]
        self._drawers[organ_name] = drawer
        self._reinsert_drawers_sorted()
        return drawer

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        outer.addWidget(self._build_workflow_strip())
        outer.addWidget(_h_divider())

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([400, 800])
        outer.addWidget(splitter, stretch=1)

        # Transient status line for warnings ("no GT for patient X in drawer Y").
        self._status_label = QLabel("", self)
        self._status_label.setStyleSheet("color: #d96b00;")
        self._status_label.setWordWrap(True)
        outer.addWidget(self._status_label)
        self._status_clear_timer = QTimer(self)
        self._status_clear_timer.setSingleShot(True)
        self._status_clear_timer.timeout.connect(lambda: self._status_label.setText(""))

    def _build_workflow_strip(self) -> QWidget:
        bar = QFrame(self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(QLabel("<b>Auto-match workflow:</b>"))

        self._replacement_rules_btn = QPushButton("1. Replacement Rules…")
        self._replacement_rules_btn.clicked.connect(self._on_replacement_rules_clicked)
        layout.addWidget(self._replacement_rules_btn)

        layout.addWidget(QLabel("→"))

        self._template_btn = QPushButton("2. Define Template…")
        self._template_btn.clicked.connect(self._on_define_template_clicked)
        layout.addWidget(self._template_btn)

        layout.addWidget(QLabel("→"))

        self._run_match_btn = QPushButton("3. Run Auto-Match")
        self._run_match_btn.clicked.connect(self._on_run_auto_match_clicked)
        layout.addWidget(self._run_match_btn)

        layout.addStretch(1)
        return bar

    def _build_left_panel(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(QLabel("<b>Loaded Contours</b>"))

        self._tree = LoadedContoursTree(container)
        layout.addWidget(self._tree, stretch=1)

        action_row = QHBoxLayout()
        self._set_gt_btn = QPushButton("Set Ground Truth ▶")
        self._set_gt_btn.setToolTip(
            "Create or extend an organ drawer using the selected items as ground truth. "
            "Tests are auto-matched from the patient's other RTSS files."
        )
        self._set_gt_btn.clicked.connect(self._on_set_ground_truth_clicked)
        action_row.addWidget(self._set_gt_btn)

        self._add_selected_btn = QPushButton("Add Selected (focus a drawer)")
        self._add_selected_btn.setToolTip(
            "Add the selected organs to the focused drawer. "
            "Focus a drawer first by clicking its header."
        )
        self._add_selected_btn.setEnabled(False)
        self._add_selected_btn.clicked.connect(self._on_add_selected_clicked)
        action_row.addWidget(self._add_selected_btn)
        layout.addLayout(action_row)

        return container

    def _build_right_panel(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Matched Organs</b>"))
        header.addStretch(1)
        self._remove_all_red_btn = QPushButton("Remove All Poor Matches")
        self._remove_all_red_btn.setToolTip(
            "Remove every test row currently below the similarity threshold, across all drawers."
        )
        self._remove_all_red_btn.clicked.connect(self._on_remove_all_red)
        header.addWidget(self._remove_all_red_btn)
        layout.addLayout(header)

        self._scroll = QScrollArea(container)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._drawers_container = QWidget()
        self._drawers_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._drawers_layout = QVBoxLayout(self._drawers_container)
        self._drawers_layout.setContentsMargins(4, 4, 4, 4)
        self._drawers_layout.setSpacing(6)

        self._drawers_empty_label = QLabel(
            "<i>No drawers yet. Set a Ground Truth on the left, or run "
            "auto-match from the workflow strip above.</i>"
        )
        self._drawers_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drawers_empty_label.setStyleSheet("color: #888; padding: 24px;")
        self._drawers_layout.addWidget(self._drawers_empty_label)
        self._drawers_layout.addStretch(1)

        self._scroll.setWidget(self._drawers_container)
        layout.addWidget(self._scroll, stretch=1)

        self._loaded_empty_label = QLabel(
            "<i>Load a folder in Tab 1 to begin matching.</i>", container
        )
        self._loaded_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loaded_empty_label.setStyleSheet("color: #888; padding: 24px;")
        layout.addWidget(self._loaded_empty_label)
        self._set_empty_state(True)

        return container

    # ---- Drawer plumbing --------------------------------------------------

    def _resync_tree_marks(self) -> None:
        """Authoritatively recompute loaded-contours ticks from current drawer state.

        Every drawer mutation (add, remove, GT reassign, session restore,
        poor-match cleanup, drag-drop, …) ends with a call to this, so the
        tree's ✓ marks always reflect what is *actually* in the drawers right
        now. Cheaper and more correct than threading per-organ mark/unmark
        calls through every code path.
        """
        marks: set[tuple[str, str, int]] = set()
        for drawer in self._drawers.values():
            for triple in drawer.all_assigned_organs():
                marks.add(triple)
        self._tree.set_marks(marks)

    def _reset_drawers(self) -> None:
        for organ_name in list(self._drawers):
            drawer = self._drawers.pop(organ_name)
            self._drawers_layout.removeWidget(drawer)
            drawer.deleteLater()
        self._focused_drawer = None
        self._add_selected_btn.setText("Add Selected (focus a drawer)")
        self._add_selected_btn.setEnabled(False)
        self._reinsert_drawers_sorted()

    def _reinsert_drawers_sorted(self) -> None:
        while self._drawers_layout.count():
            item = self._drawers_layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._drawers_empty_label and w not in self._drawers.values():
                w.setParent(None)
        if self._drawers:
            for organ in sorted(self._drawers):
                self._drawers_layout.addWidget(self._drawers[organ])
            self._drawers_empty_label.setVisible(False)
        else:
            self._drawers_layout.addWidget(self._drawers_empty_label)
            self._drawers_empty_label.setVisible(True)
        self._drawers_layout.addStretch(1)

    def _set_empty_state(self, no_data: bool) -> None:
        self._loaded_empty_label.setVisible(no_data)
        self._scroll.setVisible(not no_data)
        for btn in (
            self._set_gt_btn,
            self._remove_all_red_btn,
            self._replacement_rules_btn,
            self._template_btn,
            self._run_match_btn,
        ):
            btn.setEnabled(not no_data)
        # Add Selected enable state depends on focus, not on data-loaded state.
        self._add_selected_btn.setEnabled(
            (not no_data) and self._focused_drawer is not None
        )

    # ---- Focus tracking ---------------------------------------------------

    def _on_drawer_focus_changed(self, drawer: OrganDrawer, focused: bool) -> None:
        if focused:
            if self._focused_drawer is not None and self._focused_drawer is not drawer:
                self._focused_drawer.set_focused(False)
            self._focused_drawer = drawer
            self._add_selected_btn.setText(f"Add Selected → {drawer.organ_name()}")
            self._add_selected_btn.setEnabled(True)
        else:
            if self._focused_drawer is drawer:
                self._focused_drawer = None
            self._add_selected_btn.setText("Add Selected (focus a drawer)")
            self._add_selected_btn.setEnabled(False)

    def _make_focus_grabber(self, drawer: OrganDrawer):
        """Header mousePressEvent: click toggles FOCUS ONLY (does not change expansion).

        Only the disclosure arrow on the header expands/collapses the drawer.
        """

        def handler(event):
            if event.button() == Qt.MouseButton.LeftButton:
                drawer.set_focused(not drawer.is_focused())
                event.accept()  # consume so nothing else acts on this click
            else:
                event.ignore()

        return handler

    # ---- GT / Add Selected handlers --------------------------------------

    def _on_set_ground_truth_clicked(self) -> None:
        if self._library is None:
            return
        organs = self._tree.selected_organs()
        if not organs:
            self._show_status("Select at least one organ in the loaded contours tree first.")
            return
        self._set_ground_truth_organs(organs)

    def _set_ground_truth_organs(
        self, organs: list[tuple[str, str, int, str]]
    ) -> None:
        """Set GT for each ``(patient_id, sop_uid, roi_number, roi_name)`` tuple.

        Called both by the manual button click and by Run Auto-Match (which
        builds the tuples from the template + cohort).
        """
        if self._library is None:
            return
        skipped: list[str] = []
        new_subsections = 0
        for patient_id, sop_uid, roi_number, roi_name in organs:
            rtss = _find_rtstruct(self._library, patient_id, sop_uid)
            if rtss is None:
                skipped.append(f"{patient_id}/{roi_name} (RTSTRUCT not found)")
                continue
            drawer = self.add_drawer(roi_name)
            existing = drawer.patient_subsection(patient_id)
            if existing is not None:
                subsection = PatientSubsection(
                    patient_id=patient_id,
                    gt_rtstruct_sop_uid=sop_uid,
                    gt_rtstruct_filename=rtss.filename,
                    gt_source_label=rtss.source_label,
                    gt_roi_number=roi_number,
                    gt_roi_name=roi_name,
                    tests=list(existing.tests),
                )
                drawer.update_patient(subsection)
            else:
                auto_tests = self._auto_match_tests(patient_id, sop_uid, roi_name)
                subsection = PatientSubsection(
                    patient_id=patient_id,
                    gt_rtstruct_sop_uid=sop_uid,
                    gt_rtstruct_filename=rtss.filename,
                    gt_source_label=rtss.source_label,
                    gt_roi_number=roi_number,
                    gt_roi_name=roi_name,
                    tests=auto_tests,
                )
                drawer.add_patient(subsection)
                new_subsections += 1
            # New drawers stay collapsed; the user expands them on demand.
        self._resync_tree_marks()
        bits: list[str] = []
        if new_subsections:
            bits.append(f"Set {new_subsections} ground truth(s) and auto-matched tests.")
        if skipped:
            bits.append("Skipped: " + ", ".join(skipped))
        if bits:
            self._show_status(" ".join(bits))

    def _auto_match_tests(
        self, patient_id: str, gt_sop_uid: str, gt_roi_name: str
    ) -> list[TestRow]:
        """For each non-GT RTSTRUCT in this patient's contexts, pick the closest-named organ.

        Uses the rapidfuzz pipeline with the user's replacement rules and the
        bundled synonym dictionary. No threshold filter — every other RTSTRUCT
        contributes exactly one test row (its best match). Below-threshold
        matches still receive the ⚠ flag so the user can curate them out.
        """
        if self._library is None:
            return []
        patient = self._library.patients.get(patient_id)
        if patient is None:
            return []
        threshold = self._similarity_threshold()
        rules = self._replacement_rules()
        tests: list[TestRow] = []
        for ctx in patient.contexts:
            for rtss in ctx.rtstructs:
                if rtss.sop_instance_uid == gt_sop_uid:
                    continue
                if not rtss.organs:
                    continue
                chosen, match = best_match(
                    gt_roi_name,
                    rtss.organs,
                    key=lambda o: o.roi_name,
                    rules=rules,
                    synonyms_flat=self._synonyms_flat,
                )
                if chosen is None:
                    continue
                tests.append(
                    TestRow(
                        source_label=rtss.source_label,
                        organ_name=chosen.roi_name,
                        rtstruct_sop_uid=rtss.sop_instance_uid,
                        roi_number=chosen.roi_number,
                        similarity=match.score,
                        below_threshold=match.score < threshold,
                        match_method=match.method,
                    )
                )
        return tests

    def _on_add_selected_clicked(self) -> None:
        organs = self._tree.selected_organs()
        if not organs:
            self._show_status("Select at least one organ in the loaded contours tree first.")
            return
        if self._focused_drawer is None:
            self._show_status(
                "Focus a drawer first (click its header) to choose where to add the selected organs."
            )
            return
        self._add_to_drawer(self._focused_drawer, organs)

    def _on_drawer_add_selected(self, organ_name: str) -> None:
        """The 'Add Selected' button inside a drawer footer — route to that drawer."""
        organs = self._tree.selected_organs()
        if not organs:
            self._show_status("Select at least one organ in the loaded contours tree first.")
            return
        drawer = self._drawers.get(organ_name)
        if drawer is None:
            return
        self._add_to_drawer(drawer, organs)

    def _on_drop_on_drawer(self, organ_name: str, organs: list) -> None:
        """Drag-and-drop drop on a specific drawer — equivalent to focused-drawer Add."""
        drawer = self._drawers.get(organ_name)
        if drawer is None:
            return
        self._add_to_drawer(drawer, organs)

    def _add_to_drawer(self, target: OrganDrawer, organs: list) -> None:
        """Add ``organs`` as test rows to ``target``."""
        if self._library is None or target is None:
            return
        skipped: list[str] = []
        added = 0
        for patient_id, sop_uid, roi_number, roi_name in organs:
            sub = target.patient_subsection(patient_id)
            if sub is None:
                skipped.append(
                    f"{patient_id}/{roi_name} (no GT for this patient in drawer "
                    f"'{target.organ_name()}')"
                )
                continue
            rtss = _find_rtstruct(self._library, patient_id, sop_uid)
            if rtss is None:
                skipped.append(f"{patient_id}/{roi_name} (RTSTRUCT not found)")
                continue
            if any(
                t.rtstruct_sop_uid == sop_uid and t.roi_number == roi_number for t in sub.tests
            ):
                skipped.append(f"{patient_id}/{roi_name} (already added)")
                continue
            match = similarity(
                roi_name,
                sub.gt_roi_name,
                rules=self._replacement_rules(),
                synonyms_flat=self._synonyms_flat,
            )
            below = match.score < self._similarity_threshold()
            sub.tests.append(
                TestRow(
                    source_label=rtss.source_label,
                    organ_name=roi_name,
                    rtstruct_sop_uid=sop_uid,
                    roi_number=roi_number,
                    similarity=match.score,
                    below_threshold=below,
                    match_method=match.method,
                )
            )
            target.update_patient(sub)
            added += 1
        self._resync_tree_marks()
        if skipped:
            extra = "" if added == 0 else f" (added {added})"
            self._show_status(f"Skipped: {', '.join(skipped)}{extra}")

    # ---- Remove handlers --------------------------------------------------

    def _on_remove_drawer(self, organ_name: str) -> None:
        drawer = self._drawers.pop(organ_name, None)
        if drawer is None:
            return
        if self._focused_drawer is drawer:
            self._focused_drawer = None
            self._add_selected_btn.setText("Add Selected → (auto)")
        self._drawers_layout.removeWidget(drawer)
        drawer.deleteLater()
        self._reinsert_drawers_sorted()
        self._resync_tree_marks()

    def _on_remove_patient(self, organ_name: str, patient_id: str) -> None:
        drawer = self._drawers.get(organ_name)
        if drawer is None:
            return
        if drawer.patient_subsection(patient_id) is None:
            return
        drawer.remove_patient(patient_id)
        # Auto-cleanup: if the drawer is now empty, remove the drawer too.
        if drawer.patient_count() == 0:
            self._on_remove_drawer(organ_name)
        else:
            self._resync_tree_marks()

    def _on_remove_test(
        self, organ_name: str, patient_id: str, sop_uid: str, roi_number: int
    ) -> None:
        drawer = self._drawers.get(organ_name)
        if drawer is None:
            return
        if drawer.remove_test(patient_id, sop_uid, roi_number):
            self._resync_tree_marks()

    def _on_remove_red(self, organ_name: str) -> None:
        drawer = self._drawers.get(organ_name)
        if drawer is None:
            return
        if drawer.remove_poor_matches():
            self._resync_tree_marks()

    def _on_remove_all_red(self) -> None:
        total = 0
        for drawer in self._drawers.values():
            total += len(drawer.remove_poor_matches())
        if total:
            self._resync_tree_marks()
            self._show_status(f"Removed {total} poor-match test row{'s' if total != 1 else ''}.")
        else:
            self._show_status("No poor-match test rows to remove.")

    def _on_visualize(self, organ_name: str, patient_id: str) -> None:
        """Open a slice-viewer popup showing the GT + every test contour for one patient."""
        if self._library is None:
            return
        drawer = self._drawers.get(organ_name)
        if drawer is None:
            return
        sub = drawer.patient_subsection(patient_id)
        if sub is None:
            self._show_status(
                f"Cannot visualize: no GT for patient {patient_id} in '{organ_name}'."
            )
            return
        # CT + mask loading happens synchronously — show a status while it works
        self._show_status(f"Loading visualization for {patient_id} / {organ_name}…")
        try:
            ct, gt_mask, test_pairs = self._load_visualization_data(
                patient_id, sub, drawer.truncate_enabled()
            )
        except Exception as exc:  # noqa: BLE001
            self._show_status(f"Visualization failed: {exc}")
            return
        if gt_mask is None:
            self._show_status(
                f"Visualization failed: GT mask for {patient_id} / {organ_name} could not be loaded."
            )
            return
        gt_label = f"{sub.gt_source_label} — {sub.gt_roi_name}"
        title = f"Visualize  ·  {patient_id}  ·  {organ_name}"
        dialog = VisualizationWindow(
            ct, gt_mask, gt_label, test_pairs, title=title, parent=self
        )
        # Clear status now that the dialog is up
        self._show_status("")
        dialog.exec()

    def _load_visualization_data(
        self, patient_id: str, sub: PatientSubsection, truncate: bool
    ) -> tuple:
        """Load the CT + GT + every available test mask for one patient subsection."""
        folder = find_reference_image_folder(
            self._library, patient_id, sub.gt_rtstruct_sop_uid
        )
        if folder is None:
            raise RuntimeError(
                f"No reference image folder found for patient {patient_id}."
            )
        ct = read_dicom_image(folder)
        gt_path = self._rtstruct_path(patient_id, sub.gt_rtstruct_sop_uid)
        if gt_path is None:
            raise RuntimeError("GT RTSTRUCT file not found in loaded folder.")
        gt_rtss = read_rtstruct(gt_path)
        gt_mask = extract_mask_for_roi(ct, gt_rtss, sub.gt_roi_number)
        test_pairs: list[tuple[str, object]] = []
        for t in sub.tests:
            path = self._rtstruct_path(patient_id, t.rtstruct_sop_uid)
            if path is None:
                continue
            try:
                ds = read_rtstruct(path)
                mask = extract_mask_for_roi(ct, ds, t.roi_number)
            except Exception:  # noqa: BLE001 — skip a broken test instead of failing the dialog
                continue
            if mask is None:
                continue
            if truncate and gt_mask is not None:
                mask, _ = truncate_to_gt_z_extent(mask, gt_mask)
            label = f"{t.source_label} — {t.organ_name}"
            test_pairs.append((label, mask))
        return ct, gt_mask, test_pairs

    def _rtstruct_path(self, patient_id: str, sop_uid: str) -> str | None:
        patient = self._library.patients.get(patient_id) if self._library else None
        if patient is None:
            return None
        for ctx in patient.contexts:
            for rtss in ctx.rtstructs:
                if rtss.sop_instance_uid == sop_uid:
                    return rtss.file_path
        return None

    # ---- Workflow strip handlers -----------------------------------------

    def _on_replacement_rules_clicked(self) -> None:
        existing = self._settings.get("replacement_rules", []) or []
        dialog = ReplacementRulesDialog(existing, parent=self)
        if dialog.exec() != ReplacementRulesDialog.DialogCode.Accepted:
            return
        rules = dialog.rules()
        self._settings["replacement_rules"] = rules
        self.replacementRulesChanged.emit(rules)
        self._show_status(f"Saved {len(rules)} replacement rule(s).")

    def _on_define_template_clicked(self) -> None:
        existing = self._settings.get("last_template", {}) or {}
        dialog = TemplateDialog(existing, parent=self)
        if dialog.exec() != TemplateDialog.DialogCode.Accepted:
            return
        template = dialog.template()
        self._settings["last_template"] = template
        self.templateChanged.emit(template)
        organs = template.get("organs", []) or []
        self._show_status(
            f"Template saved: {len(organs)} organ(s), "
            f"similarity threshold {template.get('similarity_threshold', 0.6):.2f}."
        )

    def _on_run_auto_match_clicked(self) -> None:
        if self._library is None:
            self._show_status("Load a folder first.")
            return
        template = self._settings.get("last_template", {}) or {}
        organs: list[str] = [o for o in template.get("organs", []) or [] if str(o).strip()]
        gt_mfr = str(template.get("gt_manufacturer", "") or "").strip().lower()
        gt_filename = str(template.get("gt_filename", "") or "").strip().lower()
        if not organs:
            self._show_status(
                "No template defined. Click '2. Define Template…' first to specify "
                "the organs and GT identifier."
            )
            return
        if not gt_mfr and not gt_filename:
            self._show_status(
                "Template needs a Manufacturer or filename criterion to identify "
                "the GT RTSS file. Edit the template and try again."
            )
            return

        rules = self._replacement_rules()
        gt_tuples: list[tuple[str, str, int, str]] = []
        no_gt_patients: list[str] = []
        missing_organs: list[str] = []

        for patient_id, patient in self._library.patients.items():
            gt_rtss = _find_gt_rtss(patient, gt_mfr, gt_filename)
            if gt_rtss is None:
                no_gt_patients.append(patient_id)
                continue
            for organ_name in organs:
                chosen, match = best_match(
                    organ_name,
                    gt_rtss.organs,
                    key=lambda o: o.roi_name,
                    rules=rules,
                    synonyms_flat=self._synonyms_flat,
                )
                if chosen is None or match.score == 0.0:
                    missing_organs.append(f"{patient_id}/{organ_name}")
                    continue
                gt_tuples.append(
                    (patient_id, gt_rtss.sop_instance_uid, chosen.roi_number, chosen.roi_name)
                )

        if not gt_tuples:
            self._show_status("Auto-match found no matching GTs — check the template criteria.")
            return

        # Reuse the existing GT-setting machinery (which runs test auto-match per patient).
        self._set_ground_truth_organs(gt_tuples)

        # Surface a concise summary on top of whatever _set_ground_truth_organs reports.
        n_patients_done = len({t[0] for t in gt_tuples})
        bits = [f"Auto-match: set {len(gt_tuples)} GT(s) across {n_patients_done} patient(s)."]
        if no_gt_patients:
            bits.append(
                f"No GT RTSS found for: {', '.join(no_gt_patients[:5])}"
                + ("…" if len(no_gt_patients) > 5 else "")
            )
        if missing_organs:
            bits.append(
                f"Skipped: {', '.join(missing_organs[:5])}"
                + ("…" if len(missing_organs) > 5 else "")
            )
        self._show_status(" ".join(bits))

    # ---- Helpers ---------------------------------------------------------

    def _replacement_rules(self) -> list[ReplacementRule]:
        return [
            ReplacementRule(find=str(r.get("find", "")), replace=str(r.get("replace", "")))
            for r in self._settings.get("replacement_rules", []) or []
            if str(r.get("find", "")).strip()
        ]

    def _similarity_threshold(self) -> float:
        """Threshold used to flag below-threshold test matches with ⚠.

        Prefers the active template's threshold; otherwise falls back to the
        global default in settings.json.
        """
        template = self._settings.get("last_template", {}) or {}
        candidates = [
            template.get("similarity_threshold"),
            (self._settings.get("tolerances", {}) or {}).get("similarity_threshold"),
            0.6,
        ]
        for c in candidates:
            try:
                return float(c)
            except (TypeError, ValueError):
                continue
        return 0.6

    def _show_status(self, message: str) -> None:
        self._status_label.setText(message)
        self._status_clear_timer.start(_STATUS_VISIBLE_MS)


# ---- Module-level helpers -------------------------------------------------


def _find_rtstruct(library: MetadataLibrary, patient_id: str, sop_uid: str) -> RTSTRUCTEntry | None:
    patient = library.patients.get(patient_id)
    if patient is None:
        return None
    for ctx in patient.contexts:
        for rtss in ctx.rtstructs:
            if rtss.sop_instance_uid == sop_uid:
                return rtss
    return None


def _find_organ(rtss: RTSTRUCTEntry, roi_number: int) -> OrganEntry | None:
    for organ in rtss.organs:
        if organ.roi_number == roi_number:
            return organ
    return None


def _short_sop(sop: str) -> str:
    """Truncate a SOPInstanceUID for human-readable warnings."""
    if not sop:
        return "(none)"
    if len(sop) <= 16:
        return sop
    return f"{sop[:8]}…{sop[-6:]}"


def _find_gt_rtss(patient, gt_mfr: str, gt_filename: str) -> RTSTRUCTEntry | None:
    """Return the first RTSS in ``patient`` whose Manufacturer or filename matches.

    Either substring may be empty (the criterion is then ignored). When both
    are non-empty, ANY match wins (OR semantics).
    """
    for ctx in patient.contexts:
        for rtss in ctx.rtstructs:
            if gt_mfr and gt_mfr in (rtss.manufacturer or "").lower():
                return rtss
            if gt_filename and gt_filename in rtss.filename.lower():
                return rtss
    return None


def _h_divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line
