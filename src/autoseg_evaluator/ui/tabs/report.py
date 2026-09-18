"""Tab 7 — Statistical Report.

Answers the question a reviewer put to this software: is an observed difference
between sources large enough, and certain enough, to change what a department
does?

Three outputs, in the order they should be read.

**Coverage first.** A paired test silently uses only the patients both sources
contoured, so a model that declines the hard cases is otherwise rewarded for
declining them. Coverage is shown before any comparison, and every comparison
carries the counts it was computed from.

**Descriptives second**, led by median [Q1, Q3] with a distribution-free
interval, because the metrics are bounded and skewed and a mean describes
neither the typical case nor the failure.

**Inference last**, and deliberately understated. The Wilcoxon signed-rank test
is the headline because it is what the field uses and what the reviewer asked
for; the exact sign test sits beside it always, so direction and magnitude can
be read separately and neither can be chosen after the fact. Holm correction
applies within one declared family — this metric, this pair of sources, these
organs — and controls nothing beyond it.

At the sample sizes this tool sees, "inconclusive" is the expected answer rather
than a failure, so intervals are presented as the primary result and p-values as
secondary. Where the design cannot reach significance at all the tab says so,
instead of printing a column of 1.000 that reads as evidence of agreement.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autoseg_evaluator.core.statistics import IntervalStatus, smallest_attainable_p
from autoseg_evaluator.data.report import (
    AcquisitionReport,
    FamilyAxis,
    ReportModel,
    build_report_model,
    collect_acquisition,
    favours,
    interval_text,
)
from autoseg_evaluator.ui.widgets.stat_plots import DistributionCanvas, ForestCanvas

_ALPHA = 0.05

# ---- Column help ----------------------------------------------------------
#
# Every column carries a tooltip, because the table is read by clinicians and
# physicists rather than statisticians and several columns are routinely
# misread — a p-value as the size of a difference, "not significant" as
# evidence of agreement, an effect size of ±1.00 as strength rather than
# arithmetic. Rich text is used so Qt word-wraps; plain text renders as one
# unreadable line.


def _tip(*paragraphs: str) -> str:
    return "".join(f"<p style='margin:0 0 6px 0'>{text}</p>" for text in paragraphs)


COVERAGE_COLUMNS: list[tuple[str, str]] = [
    (
        "Organ",
        _tip(
            "The canonical organ. Drawers named differently across patients are pooled here if the organ grouping assigned them the same label."
        ),
    ),
    (
        "Source",
        _tip(
            "The auto-contouring source. Ground truth never appears: every metric already measures agreement with it."
        ),
    ),
    (
        "Coverage",
        _tip(
            "<b>Produced / attempted</b> — how often this source actually contoured this organ, out of the patients it ran on.",
            "<b>· N not run</b> means the source was not run on those patients at all. That says nothing about the model; failing to contour an organ it did attempt says a great deal.",
            "Caveat: patients where <i>no</i> source produced the organ generate no result row, so they are invisible here. The denominator is 'patients where at least one source produced it', not 'patients who have this organ'.",
        ),
    ),
    (
        "Produced",
        _tip("Patients where this source produced the organ and the metric computed successfully."),
    ),
    (
        "Not produced",
        _tip(
            "Patients where this source ran, but produced no contour for this organ.",
            "This is the number that carries information about the model — it declined a case it had the chance to attempt.",
        ),
    ),
    (
        "Not run",
        _tip(
            "Patients where this source produced nothing at all, so it was presumably never run.",
            "Missing data, not a model failure. Kept separate from 'not produced' for exactly that reason.",
        ),
    ),
]

DESCRIPTIVE_COLUMNS: list[tuple[str, str]] = [
    (
        "Organ",
        _tip(
            "The canonical organ, from the organ grouping in the Matching tab.",
            "Patients whose ROI was named differently — <i>SubmanG_R</i>, "
            "<i>Glnd_Submand_R</i> — are pooled here under one label, which is what "
            "makes a per-organ sample of ten patients possible at all.",
            "An organ the grouping could not assign falls back to its drawer name and "
            "stands alone.",
        ),
    ),
    (
        "Source",
        _tip(
            "The auto-contouring source being summarised. All sources are listed here, not only the two being compared."
        ),
    ),
    (
        "n",
        _tip(
            "Patients contributing a value. One value per patient — repeat exports are collapsed, not counted twice."
        ),
    ),
    (
        "Median [Q1, Q3]",
        _tip(
            "The typical value, with the middle half of the cohort in brackets.",
            "Reported ahead of the mean because these metrics are bounded and skewed: one failed contour moves a mean Hausdorff more than the rest of the cohort combined, so the mean describes neither the typical case nor the failure.",
        ),
    ),
    (
        "95% CI",
        _tip(
            "A range that would contain the true median in 95% of repeated cohorts. Built from the ordered values alone, assuming nothing about the distribution's shape.",
            "<b>— not estimable</b> below six patients. That is not a limitation of the software: with five or fewer values, even the full range covers only 93.75%, so no honest 95% interval can be formed.",
        ),
    ),
    (
        "Mean (SD)",
        _tip(
            "Supplementary, for comparison with vendor literature, which almost always reports mean and standard deviation. Prefer the median for these metrics."
        ),
    ),
    (
        "Min / Max",
        _tip(
            "The best and worst single patient. Worth a look — the worst case is often the one that matters clinically."
        ),
    ),
]

ACQUISITION_COLUMNS: list[tuple[str, str]] = [
    (
        "Parameter",
        _tip(
            "Acquisition and equipment parameters, for the methods section of a "
            "write-up. A Dice difference means something different at 1 mm slices "
            "than at 5 mm, and a reviewer will ask.",
            "Only equipment and geometry tags are read — never a name, an "
            "identifier, a date, an institution, a UID, or any free-text "
            "description. That is enforced by an allowlist in "
            "<i>core/acquisition.py</i>, so a tag nobody thought about cannot "
            "arrive here by accident.",
        ),
    ),
    (
        "Value across the cohort",
        _tip(
            "A single value means the whole cohort shares it. Several values are "
            "listed with how many series or structure sets carried each.",
            "Numbers are never averaged. A cohort scanned half at 2 mm and half at "
            "3 mm has no meaningful average slice thickness, and printing 2.5 mm "
            "would describe a scan nobody performed.",
            "<b>— not recorded</b> means the writer omitted the tag, which is "
            "common for reconstruction kernel and scanner software version.",
        ),
    ),
]

COMPARISON_COLUMNS: list[tuple[str, str]] = [
    (
        "Organ",
        _tip(
            "The canonical organ. Each organ is tested separately; results are never pooled across organs."
        ),
    ),
    (
        "n pairs",
        _tip(
            "Patients the test actually used — those where <b>both</b> sources produced this organ and both metrics computed.",
            "This is the real sample size for everything else in the row.",
        ),
    ),
    (
        "n chall. / n ref.",
        _tip(
            "What each source produced on its own, before pairing.",
            "When these exceed <b>n pairs</b>, patients were discarded. A source compared on four of ten patients is being judged on the four it chose to attempt — very likely the four it found easiest.",
            "Read this before you read the result.",
        ),
    ),
    (
        "HL difference",
        _tip(
            "<b>The most useful number in the row.</b> The typical difference between the two sources, in the metric's own units, signed <i>challenger minus reference</i>.",
            "So −0.036 on Dice means the challenger scores about 0.036 lower on a typical patient. Unlike a p-value, you can judge this clinically.",
            "Technically the Hodges–Lehmann estimator: the median of all pairwise averages of the paired differences, which is far less sensitive to one outlying patient than a plain mean difference.",
        ),
    ),
    (
        "95% CI (unadjusted)",
        _tip(
            "The range of differences consistent with the data. Two things to read: whether it crosses zero, and how wide it is.",
            "Width is what answers 'do I need more patients'. A narrow interval straddling zero means the difference is genuinely small; a wide one means you do not yet know.",
            "<b>Unadjusted</b> — unlike the Holm column, it is not corrected for testing several organs, so it can exclude zero while the adjusted p is not significant. That is the correction working, not a contradiction.",
        ),
    ),
    (
        "r",
        _tip(
            "How <b>consistently</b> one side wins, from −1 to +1. A value of ±1.00 means every single patient went the same way.",
            "It says nothing about <b>how much</b> — read the HL difference for that.",
            "Careful at small n: if all four patients agree, r is ±1.00 automatically. That is arithmetic, not strength of evidence.",
        ),
    ),
    (
        "zeros",
        _tip(
            "Patients where the two sources scored exactly the same.",
            "Mostly a data-quality check: many ties on a metric rounded to two decimals means the agreement is a rounding artefact rather than real. Zeros are kept in the ranking (Pratt's method), not discarded.",
        ),
    ),
    (
        "p",
        _tip(
            "If the two sources were genuinely equivalent, how often would a pattern this lopsided arise by chance alone? 0.002 is about one in five hundred.",
            "It is <b>not</b> the probability that the difference is real, and it says nothing about size.",
            "Exact Wilcoxon signed-rank, computed from the full sign-flip distribution rather than a normal approximation.",
        ),
    ),
    (
        "p (Holm)",
        _tip(
            "<b>This is the column to judge against 0.05</b>, not the raw p.",
            "Testing many organs at once means some will look significant by luck; Holm corrects for exactly that. Its value depends on how many organs are selected in the family list — a smaller, deliberately chosen family corrects less harshly.",
            "Familywise control applies within the selected organs only. Picking the best result from across several different families does not carry the guarantee.",
        ),
    ),
    (
        "Sign test",
        _tip(
            "'How <b>often</b>' rather than 'how <b>much</b>': the number of patients the challenger beat the reference on, and an exact p for that count.",
            "Shown always because the main test assumes the differences are symmetric, and this one does not assume it. Where they agree, that assumption is not doing any work; where they disagree, usually one or two patients carry the whole magnitude.",
        ),
    ),
    (
        "Reading",
        _tip(
            "The verdict in words, taken from the Holm-adjusted p.",
            "<b>'No detectable difference' is not 'no difference.'</b> At ten patients only fairly large effects are detectable, so a real but modest difference appears here as undetectable. Claiming equivalence would need a margin nobody has supplied.",
            "Direction is computed from the metric, so a lower Hausdorff and a higher Dice both read as 'better'.",
        ),
    ),
]


class ReportTab(QWidget):
    """Descriptive statistics, coverage and paired comparisons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._results: Any = None
        self._library: Any = None
        self._acquisition = AcquisitionReport()
        self._model: ReportModel = ReportModel()
        self._family: dict = {}
        self._build_ui()
        self._render_empty()

    # ---- Wiring -----------------------------------------------------------

    def set_results_manager(self, manager: Any) -> None:
        self._results = manager

    def set_library(self, library: Any) -> None:
        """The scanned DICOM library, for the acquisition section only."""
        self._library = library
        self._acquisition = collect_acquisition(library)
        self._fill_acquisition()

    def refresh(self) -> None:
        """Rebuild from the current results. Cheap — no metric is recomputed."""
        rows = self._results.rows() if self._results is not None else []
        self._model = build_report_model(rows)
        # Independent of the metric rows: a cohort has acquisition parameters
        # whether or not anything has been computed on it yet.
        self._acquisition = collect_acquisition(self._library)
        self._fill_acquisition()
        self._repopulate_controls()
        self._recompute()

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Statistical Report</b>", self))
        self._summary_label = QLabel("", self)
        self._summary_label.setStyleSheet("color: #777;")
        header.addWidget(self._summary_label, stretch=1)
        self._export_btn = QPushButton("Export CSV…", self)
        self._export_btn.setToolTip(
            "<p style='margin:0 0 6px 0'>Writes the paired comparison table — one row per "
            "organ in the current family — with full precision.</p>"
            "<p style='margin:0'>Aggregate only: no patient identifiers are written.</p>"
        )
        self._export_btn.clicked.connect(self._on_export)
        header.addWidget(self._export_btn)
        outer.addLayout(header)

        controls = QGroupBox("Comparison", self)
        form = QHBoxLayout(controls)

        left = QFormLayout()
        self._axis_combo = QComboBox(self)
        self._axis_combo.addItem("Organs — one challenger", FamilyAxis.ORGANS)
        self._axis_combo.addItem("Sources — one organ", FamilyAxis.SOURCES)
        self._axis_combo.setToolTip(
            _tip(
                "What the correction family varies. Both ask a real question and "
                "neither contains the other.",
                "<b>Organs</b> — one challenger against the reference, across the "
                "organs you select. <i>Where does this vendor differ from the one "
                "we use?</i>",
                "<b>Sources</b> — every other source against the reference, on one "
                "organ. <i>For this organ, how does each vendor compare to the one "
                "we use?</i>",
                "Varying both at once is the combination to avoid: at ten patients "
                "Holm can reject nothing in a family larger than 25, so five "
                "vendors across more than five organs would show adjusted p = 1.000 "
                "whatever the data said.",
            )
        )
        self._axis_combo.currentIndexChanged.connect(self._on_axis_changed)
        left.addRow("Compare across", self._axis_combo)
        self._metric_combo = QComboBox(self)
        self._metric_combo.setToolTip(
            "<p style='margin:0 0 6px 0'>The metric to analyse. Every table and figure on "
            "this page reports this one metric.</p>"
            "<p style='margin:0'>Direction is known to the software, so a lower Hausdorff "
            'and a higher Dice both read as "better" in the Reading column.</p>'
        )
        self._metric_combo.currentIndexChanged.connect(self._recompute)
        left.addRow("Metric", self._metric_combo)
        self._reference_combo = QComboBox(self)
        self._reference_combo.setToolTip(
            "The source to compare the others against — normally the one in current "
            "clinical use. Every metric is already measured against the ground-truth "
            "contour, so the reference here is a comparator, not the ground truth."
        )
        self._reference_combo.currentIndexChanged.connect(self._recompute)
        left.addRow("Compare against", self._reference_combo)
        self._challenger_combo = QComboBox(self)
        self._challenger_combo.setToolTip(
            "<p style='margin:0 0 6px 0'>The source being evaluated against the reference — "
            "typically the one you are considering adopting.</p>"
            "<p style='margin:0'>Differences are reported as <i>challenger minus "
            "reference</i>, so a negative Dice difference means the challenger scores "
            "lower.</p>"
        )
        self._challenger_combo.currentIndexChanged.connect(self._recompute)
        self._challenger_row_label = QLabel("Challenger", self)
        left.addRow(self._challenger_row_label, self._challenger_combo)
        form.addLayout(left, stretch=1)

        right = QVBoxLayout()
        self._organ_list_label = QLabel("Organs in the correction family", self)
        right.addWidget(self._organ_list_label)
        self._organ_list = QListWidget(self)
        self._organ_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._organ_list.setMaximumHeight(110)
        self._organ_list.setToolTip(
            "Holm correction applies across exactly these organs. A smaller, "
            "deliberately chosen family corrects less harshly and is the only tier "
            "that carries a confirmatory claim; everything else is exploratory."
        )
        self._organ_list.itemSelectionChanged.connect(self._recompute)
        right.addWidget(self._organ_list)
        buttons = QHBoxLayout()
        self._select_all_btn = QPushButton("All", self)
        self._select_all_btn.clicked.connect(lambda: self._organ_list.selectAll())
        buttons.addWidget(self._select_all_btn)
        self._select_none_btn = QPushButton("None", self)
        self._select_none_btn.clicked.connect(lambda: self._organ_list.clearSelection())
        buttons.addWidget(self._select_none_btn)
        buttons.addStretch(1)
        right.addLayout(buttons)
        form.addLayout(right, stretch=1)
        outer.addWidget(controls)

        self._warning = QLabel("", self)
        self._warning.setWordWrap(True)
        self._warning.setStyleSheet(
            "background:#FFF8E1; color:#6D4C00; padding:7px 10px; border-radius:3px;"
        )
        self._warning.setVisible(False)
        outer.addWidget(self._warning)

        body = QSplitter(Qt.Orientation.Vertical, self)

        tables = QSplitter(Qt.Orientation.Horizontal, body)
        self._coverage_table = self._make_table(COVERAGE_COLUMNS)
        tables.addWidget(self._wrap("Coverage", self._coverage_table))
        self._descriptive_table = self._make_table(DESCRIPTIVE_COLUMNS)
        tables.addWidget(self._wrap("Descriptive statistics", self._descriptive_table))
        tables.setSizes([420, 700])
        body.addWidget(tables)

        self._comparison_table = self._make_table(COMPARISON_COLUMNS)
        body.addWidget(self._wrap("Paired comparison", self._comparison_table))

        figures = QSplitter(Qt.Orientation.Horizontal, body)
        self._distribution = DistributionCanvas()
        figures.addWidget(self._wrap("Distributions", self._distribution))
        self._forest = ForestCanvas()
        figures.addWidget(self._wrap("Difference from reference", self._forest))
        body.addWidget(figures)

        body.setSizes([220, 260, 380])
        outer.addWidget(body, stretch=1)

        acquisition_row = QSplitter(Qt.Orientation.Horizontal, self)
        self._image_table = self._make_table(ACQUISITION_COLUMNS)
        acquisition_row.addWidget(self._wrap("Imaging", self._image_table))
        self._rtss_table = self._make_table(ACQUISITION_COLUMNS)
        acquisition_row.addWidget(self._wrap("Structure sets", self._rtss_table))
        acquisition_row.setSizes([560, 560])
        self._acquisition_box = QGroupBox("Acquisition parameters", self)
        acquisition_layout = QVBoxLayout(self._acquisition_box)
        acquisition_layout.setContentsMargins(6, 6, 6, 6)
        self._acquisition_note = QLabel("", self)
        self._acquisition_note.setWordWrap(True)
        self._acquisition_note.setStyleSheet("color:#777; font-size:11px;")
        acquisition_layout.addWidget(self._acquisition_note)
        acquisition_layout.addWidget(acquisition_row)
        outer.addWidget(self._acquisition_box)

        self._methods = QLabel("", self)
        self._methods.setWordWrap(True)
        self._methods.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._methods.setStyleSheet("color:#555; font-size:11px;")
        outer.addWidget(self._methods)

    @staticmethod
    def _make_table(columns: list[tuple[str, str]]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels([title for title, _tooltip in columns])
        for index, (_title, tooltip) in enumerate(columns):
            header_item = table.horizontalHeaderItem(index)
            if header_item is not None:
                header_item.setToolTip(tooltip)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        return table

    @staticmethod
    def _wrap(title: str, widget: QWidget) -> QWidget:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(widget)
        return box

    # ---- Controls ---------------------------------------------------------

    def _repopulate_controls(self) -> None:
        for combo, values in (
            (self._metric_combo, self._model.metrics()),
            (self._reference_combo, self._model.sources()),
            (self._challenger_combo, self._model.sources()),
        ):
            previous = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(values)
            if previous in values:
                combo.setCurrentText(previous)
            combo.blockSignals(False)

        sources = self._model.sources()
        if len(sources) > 1 and self._reference_combo.currentText() == (
            self._challenger_combo.currentText()
        ):
            self._challenger_combo.blockSignals(True)
            self._challenger_combo.setCurrentIndex(1)
            self._challenger_combo.blockSignals(False)

        selected = {i.text() for i in self._organ_list.selectedItems()}
        self._organ_list.blockSignals(True)
        self._organ_list.clear()
        for organ in self._model.organs():
            item = QListWidgetItem(organ)
            self._organ_list.addItem(item)
            item.setSelected(organ in selected if selected else True)
        self._organ_list.blockSignals(False)

    def _axis(self) -> FamilyAxis:
        data = self._axis_combo.currentData()
        return data if isinstance(data, FamilyAxis) else FamilyAxis.ORGANS

    def _on_axis_changed(self) -> None:
        """Reshape the organ list for its new job, then recompute once.

        Across organs the list *is* the family and takes several. Across sources
        it names the single organ every source is compared on, so multi-select
        would be meaningless — and the family becomes the other sources, which
        is fixed by the data rather than chosen.
        """
        across_sources = self._axis() is FamilyAxis.SOURCES
        self._organ_list.blockSignals(True)
        self._organ_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
            if across_sources
            else QAbstractItemView.SelectionMode.MultiSelection
        )
        if across_sources and self._organ_list.count():
            current = self._organ_list.selectedItems()
            self._organ_list.setCurrentRow(self._organ_list.row(current[0]) if current else 0)
        self._organ_list.blockSignals(False)

        self._organ_list_label.setText(
            "Organ to compare every source on"
            if across_sources
            else "Organs in the correction family"
        )
        self._challenger_combo.setEnabled(not across_sources)
        self._challenger_row_label.setEnabled(not across_sources)
        self._challenger_row_label.setText(
            "Challenger  (every other source)" if across_sources else "Challenger"
        )
        self._select_all_btn.setEnabled(not across_sources)
        self._select_none_btn.setEnabled(not across_sources)
        self._recompute()

    def _selected_organ(self) -> str:
        """The single organ the source-wise comparison runs on."""
        chosen = [i.text() for i in self._organ_list.selectedItems()]
        if chosen:
            return chosen[0]
        organs = self._model.organs()
        return organs[0] if organs else ""

    def _selected_organs(self) -> list[str]:
        chosen = [i.text() for i in self._organ_list.selectedItems()]
        return chosen or self._model.organs()

    # ---- Rendering --------------------------------------------------------

    def _render_empty(self) -> None:
        self._summary_label.setText("Compute metrics first — this reads the Results table.")
        self._methods.setText("")

    def _recompute(self) -> None:
        axis = self._axis()
        metric = self._metric_combo.currentText()
        reference = self._reference_combo.currentText()
        challenger = self._challenger_combo.currentText()
        # Across sources the coverage and descriptive tables narrow to the one
        # organ being compared on; across organs they span the declared family.
        organs = [self._selected_organ()] if axis is FamilyAxis.SOURCES else self._selected_organs()
        organs = [o for o in organs if o]

        if not metric or not self._model.sources():
            self._render_empty()
            # Drop the family as well as the tables. It is what Export writes,
            # so leaving it behind would let a cleared cohort be exported from
            # an empty-looking tab.
            self._family = {}
            self._warning.setText("")
            self._warning.setVisible(False)
            for table in (self._coverage_table, self._descriptive_table, self._comparison_table):
                table.setRowCount(0)
            self._distribution.plot({}, metric or "")
            self._forest.plot({}, metric or "", reference="", challenger="")
            return

        self._summary_label.setText(
            f"{len(self._model.patients())} patients · {len(self._model.organs())} organs · "
            f"{len(self._model.sources())} sources"
            + (
                f" · {self._model.duplicates_collapsed} duplicate observation(s) collapsed"
                if self._model.duplicates_collapsed
                else ""
            )
            + (
                f" · {self._model.conflicting_observations} conflicting observation(s) discarded"
                if self._model.conflicting_observations
                else ""
            )
        )

        self._fill_coverage(metric, organs)
        self._fill_descriptive(metric, organs)

        family: dict = {}
        if axis is FamilyAxis.SOURCES:
            if reference and organs:
                family = self._model.family_across_sources(metric, reference, organs[0])
        elif reference and challenger and reference != challenger:
            family = self._model.family(metric, reference, challenger, organs)
        self._family = family
        self._fill_comparison(metric, family, reference, challenger, axis)
        self._fill_warning(metric, family, reference, challenger, axis)

        self._distribution.plot(
            {
                organ: {
                    source: list(self._model.values(organ, source, metric).values())
                    for source in self._model.sources()
                }
                for organ in organs
            },
            metric,
        )
        self._forest.plot(
            family,
            metric,
            reference=reference,
            # Across sources the challenger is whatever each row names, so the
            # figure must not label a single one.
            challenger=None if axis is FamilyAxis.SOURCES else challenger,
            alpha=_ALPHA,
        )
        self._write_methods(metric, family, reference, challenger, axis)

    def _fill_acquisition(self) -> None:
        """Scanner and structure-set parameters, summarised over the cohort."""
        report = self._acquisition
        for table, summaries in (
            (self._image_table, report.images),
            (self._rtss_table, report.structure_sets),
        ):
            table.setRowCount(0)
            if not report.available:
                # A dozen rows of "— not recorded" is worse than nothing: it
                # reads as a cohort whose scanner is unknown rather than as a
                # cohort that has not been loaded.
                continue
            for summary in summaries:
                row = table.rowCount()
                table.insertRow(row)
                label_item = QTableWidgetItem(summary.label)
                value_item = QTableWidgetItem(summary.summary())
                if not summary.uniform and summary.values:
                    # Worth the reader's eye: a parameter that varies across the
                    # cohort cannot be stated as a single number in a paper.
                    tip = _tip(
                        f"<b>{summary.label}</b> is not uniform across the cohort.",
                        "Report the spread rather than a single figure — and "
                        "consider whether it confounds the comparison, since "
                        "contouring accuracy depends on voxel size.",
                    )
                    label_item.setToolTip(tip)
                    value_item.setToolTip(tip)
                table.setItem(row, 0, label_item)
                table.setItem(row, 1, value_item)

        if not report.available:
            self._acquisition_note.setText(
                "Load a folder on Tab 1 to read the cohort's acquisition parameters."
            )
        else:
            varying = sum(
                1
                for summary in list(report.images) + list(report.structure_sets)
                if summary.values and not summary.uniform
            )
            self._acquisition_note.setText(
                f"{report.n_series} image series and {report.n_structure_sets} structure "
                f"sets across {report.n_patients} patients."
                + (
                    f"  {varying} parameter(s) vary across the cohort — hover those rows."
                    if varying
                    else "  Every parameter is uniform across the cohort."
                )
                + "  Equipment and geometry only: no identifiers, dates, institutions or "
                "free-text descriptions are read."
            )

    def _fill_coverage(self, metric: str, organs: list[str]) -> None:
        table = self._coverage_table
        table.setRowCount(0)
        for organ in organs:
            for source in self._model.sources():
                cell = self._model.coverage(organ, metric, source)
                if cell.eligible == 0:
                    continue
                row = table.rowCount()
                table.insertRow(row)
                explanation = _tip(
                    f"<b>{source}</b> produced <b>{organ}</b> for {cell.produced} of the "
                    f"{cell.attempted} patient(s) it ran on."
                    + (
                        f" It ran on those {cell.attempted} but produced no contour for "
                        f"{cell.not_produced} of them."
                        if cell.not_produced
                        else ""
                    )
                    + (
                        f" A further {cell.source_absent} patient(s) have this organ from "
                        "another source but nothing at all from this one, so it was "
                        "presumably not run on them."
                        if cell.source_absent
                        else ""
                    )
                    + (
                        f" {cell.metric_invalid} contour(s) exist but the metric could not "
                        "be computed."
                        if cell.metric_invalid
                        else ""
                    )
                )
                for column, text in enumerate(
                    [
                        organ,
                        source,
                        cell.summary(),
                        str(cell.produced),
                        str(cell.not_produced),
                        str(cell.source_absent),
                    ]
                ):
                    item = QTableWidgetItem(text)
                    item.setToolTip(explanation)
                    table.setItem(row, column, item)

    def _fill_descriptive(self, metric: str, organs: list[str]) -> None:
        table = self._descriptive_table
        table.setRowCount(0)
        for organ in organs:
            for source in self._model.sources():
                summary = self._model.describe_cell(organ, source, metric)
                if summary is None:
                    continue
                row = table.rowCount()
                table.insertRow(row)
                ci = (
                    f"{summary.ci_low:.3f} – {summary.ci_high:.3f}"
                    if summary.ci_available
                    else "— not estimable"
                )
                for column, text in enumerate(
                    [
                        organ,
                        source,
                        str(summary.n),
                        f"{summary.median:.3f} [{summary.q1:.3f}, {summary.q3:.3f}]",
                        ci,
                        f"{summary.mean:.3f} ({summary.sd:.3f})",
                        f"{summary.minimum:.3f} / {summary.maximum:.3f}",
                    ]
                ):
                    table.setItem(row, column, QTableWidgetItem(text))

    def _fill_comparison(
        self,
        metric: str,
        family: dict,
        reference: str,
        challenger: str,
        axis: FamilyAxis = FamilyAxis.ORGANS,
    ) -> None:
        table = self._comparison_table
        header = table.horizontalHeaderItem(0)
        if header is not None:
            header.setText("Organ" if axis is FamilyAxis.ORGANS else "Source")
        table.setRowCount(0)
        for organ, result in family.items():
            row = table.rowCount()
            table.insertRow(row)
            if result is None:
                # Declared in the family, but no patient had both sources. It
                # still counts toward the Holm divisor, so it is shown rather
                # than dropped — otherwise the family appears to be smaller
                # than the correction actually applied.
                blank = ["—"] * table.columnCount()
                blank[0] = organ
                blank[1] = "0"
                blank[-1] = "not estimable: no matched patients"
                for column, cell_text in enumerate(blank):
                    item = QTableWidgetItem(cell_text)
                    item.setToolTip(
                        _tip(
                            f"<b>{organ}</b> was included in the correction family but "
                            "no patient had a contour from both sources, so no "
                            "comparison could be made.",
                            "It is still counted in the Holm divisor. A family that "
                            "quietly shrank to whatever the data supported would "
                            "correct less harshly exactly when a source produced "
                            "fewer organs.",
                        )
                    )
                    table.setItem(row, column, item)
                continue
            estimate = result.hl_estimate
            ci = interval_text(result.ci)
            sign = result.sign
            sign_text = f"{sign.n_positive}/{sign.n_nonzero} · p={sign.p_value:.3f}"
            for column, text in enumerate(
                [
                    organ,
                    str(result.n_pairs),
                    f"{result.n_a} / {result.n_b}",
                    f"{estimate:+.4f}" if estimate is not None else "—",
                    ci,
                    f"{result.effect_r:+.2f}" if result.effect_r is not None else "—",
                    str(result.n_zero),
                    f"{result.p_value:.4f}",
                    f"{result.p_adjusted:.4f}" if result.p_adjusted is not None else "—",
                    sign_text,
                    self._reading(
                        metric,
                        result,
                        reference,
                        organ if axis is FamilyAxis.SOURCES else challenger,
                    ),
                ]
            ):
                item = QTableWidgetItem(text)
                item.setToolTip(self._row_tooltip(result))
                table.setItem(row, column, item)

    @staticmethod
    def _row_tooltip(result) -> str:
        """What this row's sample size can and cannot show.

        The family-wide banner only fires when *nothing* in the family can reach
        significance. An individual organ can be far below that ceiling while
        others carry the family, so the limit is stated per row as well.
        """
        floor = smallest_attainable_p(result.n_pairs)
        notes = [
            f"Computed on <b>{result.n_pairs}</b> paired patient(s). The smallest "
            f"p-value any sample of this size could produce is <b>{floor:.4f}</b>, "
            "before correction for multiple organs."
        ]
        if result.coverage_fraction is not None and result.coverage_fraction < 0.8:
            notes.append(
                f"Only {result.coverage_fraction:.0%} of the larger source's cases could "
                "be paired, so this rests on a subset that is unlikely to be "
                "representative."
            )
        if not result.ci_agrees_with_test:
            notes.append(
                "Some paired differences are exactly zero, so the interval and the "
                "p-value need not agree on this row."
            )
        if not result.ci_available:
            notes.append("No finite 95% interval exists at this sample size, so none is shown.")
        return _tip(*notes)

    @staticmethod
    def _reading(metric: str, result, reference: str, challenger: str) -> str:
        """Words for one comparison, saying only what the data support.

        A large p means no difference was *detected*, never that the sources are
        equivalent — that would need a margin nobody has supplied.
        """
        if result.p_adjusted is None:
            return "—"
        if result.p_adjusted > _ALPHA:
            return "no detectable difference"
        side = favours(metric, result.hl_estimate or 0.0)
        if not side:
            return "differs"
        return f"favours {challenger if side == 'a' else reference}"

    def _fill_warning(
        self,
        metric: str,
        family: dict,
        reference: str,
        challenger: str,
        axis: FamilyAxis = FamilyAxis.ORGANS,
    ) -> None:
        notes: list[str] = []
        unit = axis.plural
        estimable = {organ: r for organ, r in family.items() if r is not None}
        missing = [organ for organ, r in family.items() if r is None]
        if self._model.conflicting_observations:
            notes.append(
                f"<b>{self._model.conflicting_observations} observation(s) discarded:</b> "
                "the same organ, source and metric were measured more than once "
                "within a single treatment context, with differing values. The first "
                "was kept. This is not a second course — those are separated by "
                "linkage and handled below — so check the Results tab for a repeated "
                "structure set."
            )

        excluded = sorted(
            {
                patient
                for label in family
                for patient in self._model.excluded_patients(
                    *(
                        (self._selected_organ(), metric, label, reference)
                        if axis is FamilyAxis.SOURCES
                        else (label, metric, challenger, reference)
                    )
                )
            }
        )
        if excluded:
            notes.append(
                f"<b>{len(excluded)} patient(s) excluded</b> "
                f"({', '.join(excluded[:4])}"
                + (f" and {len(excluded) - 4} more" if len(excluded) > 4 else "")
                + "): each contributed more than one treatment context — a "
                "re-irradiation or a replan — for these organs. Two courses of one "
                "patient are not two independent observations, and choosing between "
                "them is a study-design decision, so neither is used. Restrict the "
                "cohort on Tab 1 if you intend to analyse a particular course."
            )
        if missing:
            notes.append(
                f"<b>Not estimable:</b> {', '.join(missing[:4])}"
                + (f" and {len(missing) - 4} more" if len(missing) > 4 else "")
                + " had no patient contoured by both sources. They remain in the "
                "correction family, so the Holm divisor is "
                f"{len(family)}, not {len(estimable)}."
            )
        if estimable and not self._model.family_can_detect(family.values(), _ALPHA):
            smallest = min(r.n_pairs for r in estimable.values())
            notes.append(
                f"<b>This comparison cannot reach significance.</b> With {smallest} paired "
                f"observations the smallest attainable p-value is larger than the Holm "
                f"threshold for a family of {len(family)} {unit}, so no result can be "
                f"significant however the data fall. Reduce the family to a few "
                f"prespecified {unit}, or read the intervals rather than the p-values."
            )
        thin = [
            organ
            for organ, r in estimable.items()
            if r.coverage_fraction is not None and r.coverage_fraction < 0.8
        ]
        if thin:
            notes.append(
                f"<b>Partial coverage:</b> {', '.join(thin[:4])}"
                + (f" and {len(thin) - 4} more" if len(thin) > 4 else "")
                + " were compared on a subset of patients, because one source did not "
                "produce them for everyone. Models tend to fail on hard cases, so that "
                "subset is unlikely to be representative."
            )
        disconnected = [
            organ for organ, r in estimable.items() if r.ci.status is IntervalStatus.DISCONNECTED
        ]
        if disconnected:
            notes.append(
                f"<b>Disconnected confidence set</b> in {', '.join(disconnected[:4])}: "
                "exact ties make the accepted region fall into separate pieces, so the "
                "interval shown encloses them and is wider than the true set."
            )
        approximate = [organ for organ, r in estimable.items() if not r.ci.exhaustive]
        if approximate:
            notes.append(
                f"<b>Interval bracketed, not enumerated</b> in "
                f"{', '.join(approximate[:4])}: the sample was large enough that the "
                "confidence set was located by bisection, which assumes it is "
                "connected rather than establishing it."
            )
        disagreeing = [organ for organ, r in estimable.items() if not r.ci_agrees_with_test]
        if disagreeing:
            notes.append(
                f"<b>Exact ties present</b> in {', '.join(disagreeing[:4])}: some paired "
                "differences are exactly zero, so the interval and the p-value need not "
                "agree for those rows."
            )
        self._warning.setText("<br><br>".join(notes))
        self._warning.setVisible(bool(notes))

    def _write_methods(
        self,
        metric: str,
        family: dict,
        reference: str,
        challenger: str,
        axis: FamilyAxis = FamilyAxis.ORGANS,
    ) -> None:
        estimable = {organ: r for organ, r in family.items() if r is not None}
        if not estimable:
            self._methods.setText("")
            return
        sizes = sorted({r.n_pairs for r in estimable.values()})
        span = f"{sizes[0]}" if len(sizes) == 1 else f"{sizes[0]}–{sizes[-1]}"
        if axis is FamilyAxis.SOURCES:
            organ = self._selected_organ()
            opening = (
                f"<b>Methods.</b> Each of {len(family)} sources was compared with {reference} "
                f"on {metric} for {organ}, separately and pairwise"
            )
            family_clause = f"across the {len(family)} sources compared on this organ and metric"
            # Said explicitly because the figure invites the opposite reading.
            caveat = (
                " Every comparison shares the same reference arm, so the results are "
                "correlated and do not constitute comparisons between the other sources."
            )
        else:
            opening = (
                f"<b>Methods.</b> {challenger} was compared with {reference} on {metric} "
                f"for each organ separately"
            )
            family_clause = f"across the {len(family)} organs of this metric and source pair"
            caveat = ""
        self._methods.setText(
            opening + ", using the Wilcoxon signed-rank test on patients where both "
            f"produced the organ (n = {span} pairs). Zero differences were handled by Pratt's "
            "method and p-values computed exactly from the conditional sign-flip distribution. "
            "Differences are summarised by the Hodges–Lehmann estimator with a 95% confidence "
            "interval obtained by inverting the same test; these intervals are unadjusted. "
            "An exact sign test is reported alongside. Holm–Bonferroni correction was applied "
            + family_clause
            + (
                f", of which {len(family) - len(estimable)} could not be estimated and were "
                "retained in the divisor"
                if len(estimable) != len(family)
                else ""
            )
            + ", and controls the "
            "familywise error rate within that family only." + caveat + " Descriptive values "
            "are median [Q1, Q3] with a distribution-free 95% interval for the median, which "
            "is not estimable below six observations."
        )

    # ---- Export -----------------------------------------------------------

    def _on_export(self) -> None:
        if not self._family:
            QMessageBox.information(
                self, "Export", "Nothing to export yet — compute metrics and pick two sources."
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export comparison", "report.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        metric = self._metric_combo.currentText()
        reference = self._reference_combo.currentText()
        challenger = self._challenger_combo.currentText()
        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(
                    f"{self._axis().noun},metric,challenger,reference,n_pairs,n_challenger,n_reference,"
                    "n_zero,hl_difference,ci_low,ci_high,ci_status,ci_exhaustive,"
                    "rank_biserial,p_raw,p_holm,"
                    "sign_positive,sign_nonzero,sign_p,ci_agrees_with_test\n"
                )
                across_sources = self._axis() is FamilyAxis.SOURCES
                for organ, r in self._family.items():
                    row_challenger = organ if across_sources else challenger
                    if r is None:
                        handle.write(
                            f"{organ},{metric},{row_challenger},{reference},0,,,,,,,"
                            "not estimable,,,,,,\n"
                        )
                        continue
                    handle.write(
                        f"{organ},{metric},{row_challenger},{reference},{r.n_pairs},{r.n_a},"
                        f"{r.n_b},{r.n_zero},"
                        f"{'' if r.hl_estimate is None else f'{r.hl_estimate:.6f}'},"
                        f"{'' if r.ci_low is None else f'{r.ci_low:.6f}'},"
                        f"{'' if r.ci_high is None else f'{r.ci_high:.6f}'},"
                        f"{r.ci.status.value},{r.ci.exhaustive},"
                        f"{'' if r.effect_r is None else f'{r.effect_r:.4f}'},"
                        f"{r.p_value:.6f},"
                        f"{'' if r.p_adjusted is None else f'{r.p_adjusted:.6f}'},"
                        f"{r.sign.n_positive},{r.sign.n_nonzero},{r.sign.p_value:.6f},"
                        f"{r.ci_agrees_with_test}\n"
                    )
        except OSError as exc:
            QMessageBox.critical(self, "Export", f"Could not write the file:\n{exc}")


__all__ = ["ReportTab"]
