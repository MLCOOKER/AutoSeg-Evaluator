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

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QStyledItemDelegate,
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
    metric_direction,
)
from autoseg_evaluator.ui.widgets.stat_plots import DistributionCanvas, ForestCanvas

_ALPHA = 0.05

#: Rows a section shows before it starts scrolling. Below this a table sizes to
#: its contents, so a four-row table costs four rows of page rather than a fixed
#: block of empty grid.
MAX_VISIBLE_ROWS = 24

#: The organ a row belongs to, carried on every cell even where the label is
#: blanked for readability. Decluttering the display must not declutter the
#: data — anything reading the table back still needs to know the group.
ORGAN_ROLE = int(Qt.ItemDataRole.UserRole)

#: Marks the first row of an organ's block, in coverage and descriptives.
GROUP_START_ROLE = int(Qt.ItemDataRole.UserRole) + 1

#: Marks the best median within an organ. Bold rather than coloured: a wash
#: behind one narrow column proved invisible on a real display, and weight is
#: legible in greyscale and to a colour-blind reader without a legend.
BEST_ROLE = int(Qt.ItemDataRole.UserRole) + 2

#: Column holding the median, which is the performance figure of the row.
MEDIAN_COLUMN = 3


class _GroupRuleDelegate(QStyledItemDelegate):
    """Draws a hairline above the first row of each organ block.

    The organ is named once per group rather than on every row, which removes
    most of the clutter but also removes the only cue for where one organ ends
    and the next begins. The rule restores that cue, and unlike the label it
    stays visible when the group's first row has scrolled out of view.
    """

    def paint(self, painter, option, index) -> None:  # noqa: N802 — Qt override
        super().paint(painter, option, index)
        if not index.data(GROUP_START_ROLE) or index.row() == 0:
            return
        painter.save()
        painter.setPen(QPen(QColor(0x9A, 0xA3, 0xAD), 1))
        rect = QRect(option.rect)
        painter.drawLine(rect.topLeft(), rect.topRight())
        painter.restore()


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
        #: Every observation, across every ground truth.
        self._all: ReportModel = ReportModel()
        #: The one ground truth currently on screen. Everything reads this.
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
        self._all = build_report_model(rows)
        self._model = self._all
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
        self._ground_truth_combo = QComboBox(self)
        self._ground_truth_combo.setToolTip(
            _tip(
                "What every metric on this page was measured against.",
                "A cohort can hold more than one: the manual contours, and a "
                "multi-observer STAPLE consensus built on Tab 2. The same contour "
                "measured against both is two different measurements, so only one "
                "is shown at a time.",
                "A consensus is selected by default where one exists — it is what "
                "the cohort was built to be measured against.",
            )
        )
        self._ground_truth_combo.currentIndexChanged.connect(self._on_ground_truth_changed)
        left.addRow("Ground truth", self._ground_truth_combo)
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

        # One section per row, each spanning the window. Side-by-side panes
        # squeezed eleven-column tables and two figures into half the width
        # each, which is where the "bunched up" reading came from; the tab is
        # inside a scroll area, so height is free and width is not.
        self._coverage_table = self._make_table(COVERAGE_COLUMNS)
        self._coverage_table.setItemDelegate(_GroupRuleDelegate(self._coverage_table))
        outer.addWidget(self._wrap("Coverage", self._coverage_table))

        self._descriptive_table = self._make_table(DESCRIPTIVE_COLUMNS)
        self._descriptive_table.setItemDelegate(_GroupRuleDelegate(self._descriptive_table))
        descriptive_box = QGroupBox("Descriptive statistics", self)
        descriptive_layout = QVBoxLayout(descriptive_box)
        descriptive_layout.setContentsMargins(6, 6, 6, 6)
        self._descriptive_note = QLabel("", self)
        self._descriptive_note.setWordWrap(True)
        self._descriptive_note.setStyleSheet("color:#777; font-size:11px;")
        descriptive_layout.addWidget(self._descriptive_note)
        descriptive_layout.addWidget(self._descriptive_table)
        outer.addWidget(descriptive_box)

        self._comparison_table = self._make_table(COMPARISON_COLUMNS)
        outer.addWidget(self._wrap("Paired comparison", self._comparison_table))

        self._distribution = DistributionCanvas()
        outer.addWidget(self._wrap("Distributions", self._distribution))
        self._forest = ForestCanvas()
        # The toggle belongs beside the figure it rescales, not among the
        # controls that choose what is compared — it changes how one plot is
        # drawn, nothing about the analysis.
        forest_box = QGroupBox("Difference from reference", self)
        forest_layout = QVBoxLayout(forest_box)
        forest_layout.setContentsMargins(6, 6, 6, 6)
        self._relative_check = QCheckBox("Show as % of the reference's median", self)
        self._relative_check.setToolTip(
            _tip(
                "Rescales this figure so each row is a percentage of the "
                "reference's own median for that organ, instead of the metric's "
                "raw units.",
                "Raw units are the default because they are what a clinician "
                "judges and what goes in a paper. They make organs incomparable "
                "on an unbounded metric though: the same 25% degradation is "
                "10 mm on bowel and 0.4 mm on a cochlea, and on one shared axis "
                "the cochlea collapses onto zero.",
                "Bounded metrics — Dice, surface Dice — do not have this "
                "problem, so leaving it off is usually right for them.",
                "A row whose reference median is zero has no relative form and "
                "is omitted; the figure footer says how many.",
            )
        )
        self._relative_check.toggled.connect(self._recompute)
        forest_layout.addWidget(self._relative_check)
        forest_layout.addWidget(self._forest)
        outer.addWidget(forest_box)

        self._acquisition_box = QGroupBox("Acquisition parameters", self)
        acquisition_layout = QVBoxLayout(self._acquisition_box)
        acquisition_layout.setContentsMargins(6, 6, 6, 6)
        self._acquisition_note = QLabel("", self)
        self._acquisition_note.setWordWrap(True)
        self._acquisition_note.setStyleSheet("color:#777; font-size:11px;")
        acquisition_layout.addWidget(self._acquisition_note)
        self._image_table = self._make_table(ACQUISITION_COLUMNS)
        acquisition_layout.addWidget(self._image_table)
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
        # Equal widths across the row. Content-sized columns gave a ragged left
        # edge that changed every time the data did, which made two tables
        # stacked above each other impossible to read across.
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    @staticmethod
    def _fit_table(table: QTableWidget, max_rows: int = MAX_VISIBLE_ROWS) -> None:
        """Size a table to its contents, up to a ceiling, then let it scroll.

        A fixed height wastes the page on a four-row table and hides rows on a
        forty-row one. Both minimum and maximum are set to the fitted value so
        the surrounding layout cannot stretch it back out.
        """
        header = table.horizontalHeader().height()
        rows = table.rowCount()
        row_height = table.rowHeight(0) if rows else table.verticalHeader().defaultSectionSize()
        visible = min(rows, max_rows) if rows else 0
        # Two pixels of frame, and half a row of headroom when scrolling so the
        # cut-off row reads as "there is more" rather than as the end.
        height = header + visible * row_height + 4
        if rows > max_rows:
            height += row_height // 2
        height = max(height, header + row_height + 4)
        table.setMinimumHeight(height)
        table.setMaximumHeight(height)

    @staticmethod
    def _wrap(title: str, widget: QWidget) -> QWidget:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(widget)
        return box

    # ---- Controls ---------------------------------------------------------

    def _repopulate_controls(self) -> None:
        self._repopulate_ground_truths()
        self._repopulate_metrics()
        for combo, values in (
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

    def _on_ground_truth_changed(self) -> None:
        """Re-project, then rebuild the metric list before recomputing.

        The two references need not carry the same metrics — a consensus
        comparison adds per-vendor agreement metrics and may omit others — so a
        selector built for one reference offers metrics the other does not have
        and hides metrics it does.
        """
        self._model = self._all.for_ground_truth(self._ground_truth_combo.currentText())
        self._repopulate_metrics()
        self._recompute()

    def _repopulate_ground_truths(self) -> None:
        """Offer every reference the cohort holds, consensus first."""
        combo = self._ground_truth_combo
        previous = combo.currentText()
        available = self._all.ground_truths()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(available)
        if previous in available:
            combo.setCurrentText(previous)
        elif available:
            combo.setCurrentText(self._all.preferred_ground_truth())
        combo.blockSignals(False)
        # One reference is not a choice, so do not present it as one.
        self._ground_truth_combo.setEnabled(len(available) > 1)
        self._model = self._all.for_ground_truth(combo.currentText())

    def _repopulate_metrics(self) -> None:
        """Fill the metric selector, grouped and with unselectable headings.

        Geometric and dosimetric metrics answer different questions on
        different scales. In one flat list a reader scrolls from Dice to D95
        without the change of subject registering, and the tab gives no other
        signal that it happened.
        """
        combo = self._metric_combo
        previous = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        selectable: list[str] = []
        for family, metrics in self._model.metrics_by_family():
            combo.addItem(f"— {family} —")
            heading = combo.model().item(combo.count() - 1)
            if heading is not None:
                heading.setEnabled(False)
                font = heading.font()
                font.setBold(True)
                heading.setFont(font)
            for metric in metrics:
                combo.addItem(metric)
                selectable.append(metric)
        if previous in selectable:
            combo.setCurrentText(previous)
        elif selectable:
            combo.setCurrentText(selectable[0])
        combo.blockSignals(False)

    def _selected_metric(self) -> str:
        """The chosen metric, or empty when a heading somehow ends up current."""
        text = self._metric_combo.currentText()
        return "" if text.startswith("— ") else text

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
        self._model = self._all.for_ground_truth(self._ground_truth_combo.currentText())
        axis = self._axis()
        metric = self._selected_metric()
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

        ground_truth = self._model.active_ground_truth or self._ground_truth_combo.currentText()
        self._summary_label.setText(
            f"{len(self._model.patients())} patients · {len(self._model.organs())} organs · "
            f"{len(self._model.sources())} sources"
            + (f" · vs {ground_truth}" if ground_truth else "")
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
        scales = None
        if self._relative_check.isChecked():
            scales = {
                label: self._reference_median(label, metric, reference, axis, organs)
                for label in family
            }
        self._forest.plot(
            family,
            metric,
            scales=scales,
            reference=reference,
            # Across sources the challenger is whatever each row names, so the
            # figure must not label a single one.
            challenger=None if axis is FamilyAxis.SOURCES else challenger,
            alpha=_ALPHA,
        )
        self._write_methods(metric, family, reference, challenger, axis)

    def _fill_acquisition(self) -> None:
        """Scanner parameters, summarised over the cohort."""
        report = self._acquisition
        table = self._image_table
        table.setRowCount(0)
        # A dozen rows of "— not recorded" is worse than nothing: it reads as a
        # cohort whose scanner is unknown rather than one not yet loaded.
        if report.available:
            for summary in report.images:
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
        # Always fitted, so an empty table collapses to its header rather
        # than keeping an unbounded maximum height.
        self._fit_table(table)

        if not report.available:
            self._acquisition_note.setText(
                "Load a folder on Tab 1 to read the cohort's acquisition parameters."
            )
        else:
            varying = sum(1 for summary in report.images if summary.values and not summary.uniform)
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

    def _reference_median(
        self,
        label: str,
        metric: str,
        reference: str,
        axis: FamilyAxis,
        organs: list[str],
    ) -> float:
        """The denominator for one forest row in relative mode.

        Always the *reference* source's median on the organ in question, so
        every row is a percentage of the same thing it is being compared with.
        Across sources the organ is fixed, so every row shares one denominator.
        """
        organ = organs[0] if axis is FamilyAxis.SOURCES and organs else label
        summary = self._model.describe_cell(organ, reference, metric)
        return 0.0 if summary is None else float(summary.median)

    def _fill_coverage(self, metric: str, organs: list[str]) -> None:
        table = self._coverage_table
        table.setRowCount(0)
        for organ in organs:
            # Only the sources that have something to say about this organ, so
            # "first of the group" means the first row actually drawn.
            present = [
                (source, self._model.coverage(organ, metric, source))
                for source in self._model.sources()
            ]
            present = [(source, cell) for source, cell in present if cell.eligible]
            for position, (source, cell) in enumerate(present):
                first = position == 0
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
                        organ if first else "",
                        source,
                        cell.summary(),
                        str(cell.produced),
                        str(cell.not_produced),
                        str(cell.source_absent),
                    ]
                ):
                    item = QTableWidgetItem(text)
                    item.setData(ORGAN_ROLE, organ)
                    item.setToolTip(explanation)
                    if first:
                        item.setData(GROUP_START_ROLE, True)
                        if column == 0:
                            font = item.font()
                            font.setBold(True)
                            item.setFont(font)
                    table.setItem(row, column, item)
        self._fit_table(table)

    def _fill_descriptive(self, metric: str, organs: list[str]) -> None:
        table = self._descriptive_table
        table.setRowCount(0)
        direction = metric_direction(metric)
        for organ in organs:
            summaries = {
                source: self._model.describe_cell(organ, source, metric)
                for source in self._model.sources()
            }
            present = {s: d for s, d in summaries.items() if d is not None}
            # The best median in this organ, marked in bold. Within the organ
            # only — a Dice excellent for a cochlea is poor for a parotid, so a
            # single winner across the table would rank organs, not sources.
            # An undirected metric has no winner and none is marked.
            best_source = ""
            if direction and len(present) > 1:
                best_source = (max if direction > 0 else min)(
                    present, key=lambda source: present[source].median
                )
            for position, (source, summary) in enumerate(present.items()):
                row = table.rowCount()
                table.insertRow(row)
                ci = (
                    f"{summary.ci_low:.3f} – {summary.ci_high:.3f}"
                    if summary.ci_available
                    else "— not estimable"
                )
                # The organ is named once per group. Repeating it down every row
                # is the bulk of the clutter and carries no information — the
                # eye needs the boundary, not the label six times.
                first = position == 0
                for column, text in enumerate(
                    [
                        organ if first else "",
                        source,
                        str(summary.n),
                        f"{summary.median:.3f} [{summary.q1:.3f}, {summary.q3:.3f}]",
                        ci,
                        f"{summary.mean:.3f} ({summary.sd:.3f})",
                        f"{summary.minimum:.3f} / {summary.maximum:.3f}",
                    ]
                ):
                    item = QTableWidgetItem(text)
                    item.setData(ORGAN_ROLE, organ)
                    item.setToolTip(self._descriptive_tooltip(organ, source, metric, direction))
                    if source == best_source and column == MEDIAN_COLUMN:
                        item.setData(BEST_ROLE, True)
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                    if first:
                        # A rule above the first row of each group, so the
                        # boundary survives scrolling past the organ's name.
                        item.setData(GROUP_START_ROLE, True)
                        if column == 0:
                            font = item.font()
                            font.setBold(True)
                            item.setFont(font)
                    table.setItem(row, column, item)
        self._fit_table(table)
        self._descriptive_note.setText(
            f"Every source, on {metric}"
            + (
                f", where {'higher' if direction > 0 else 'lower'} is better. "
                "The best median within each organ is shown in bold."
                if direction
                else " — a metric with no better or worse direction, since it is best at "
                "a target rather than at an extreme, so no result is marked best."
            )
        )

    @staticmethod
    def _descriptive_tooltip(organ: str, source: str, metric: str, direction: int) -> str:
        if not direction:
            reading = (
                f"<b>{metric}</b> has no better or worse direction — it is best at a "
                "target rather than at an extreme."
            )
        else:
            better = "higher" if direction > 0 else "lower"
            reading = f"On <b>{metric}</b>, {better} is better."
        return _tip(f"<b>{source}</b> on <b>{organ}</b>.", reading)

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
        self._fit_table(table)

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
        metric = self._selected_metric()
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
