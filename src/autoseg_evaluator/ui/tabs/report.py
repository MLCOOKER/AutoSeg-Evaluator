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
be read separately and neither can be chosen after the fact.

**Each organ is its own question**, and its p-value is reported unadjusted. An
earlier design corrected across whichever organs were selected, which made the
divisor a view setting: narrowing the list made a result significant and
widening it took the result away, on the same data. A correction dialled by a
list widget invites the very selection it exists to prevent. What multiplicity
costs is stated instead — the tab says how many rows would fall below 0.05 by
chance — and every comparison is shown, which is what makes reporting
uncorrected p-values defensible.

At the sample sizes this tool sees, "inconclusive" is the expected answer rather
than a failure, so intervals are presented as the primary result and p-values as
secondary. Where the design cannot reach significance at all the tab says so,
instead of printing a column of 1.000 that reads as evidence of agreement.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PySide6.QtCore import QRect, QSizeF, Qt
from PySide6.QtGui import QColor, QPageLayout, QPageSize, QPdfWriter, QPen, QTextDocument
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

from autoseg_evaluator import __version__
from autoseg_evaluator.core.readable import (
    metric_units,
    readable_metric,
    tolerance_note,
)
from autoseg_evaluator.core.statistics import IntervalStatus, smallest_attainable_p
from autoseg_evaluator.data.report import (
    AcquisitionReport,
    FamilyAxis,
    ReportModel,
    build_report_model,
    collect_acquisition,
    expected_false_positives,
    favours,
    interval_text,
    metric_direction,
)
from autoseg_evaluator.ui.widgets.stat_plots import (
    DistributionCanvas,
    ForestCanvas,
    PairedCanvas,
)

_ALPHA = 0.05

#: Where the masthead logo lives.
_ASSET_DIR = Path(__file__).resolve().parents[2] / "assets"

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
            "When these exceed <b>n pairs</b>, patients were discarded: the comparison runs only on the patients both sources contoured.",
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
            "<b>Unadjusted</b>, like the p-value beside it: this interval describes this organ and is not widened for the other rows on screen.",
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
            "If these two sources were genuinely equivalent on this organ, how "
            "often would a pattern this lopsided arise by chance alone? 0.002 is "
            "about one in five hundred.",
            "It is <b>not</b> the probability that the difference is real, and it "
            "says nothing about size.",
            "<b>Unadjusted, and per organ.</b> Each row answers its own question — "
            "for this organ and this metric, do these two sources differ? — and "
            "that answer does not change because another organ is on screen.",
            "Multiplicity still costs something when you scan many rows for the "
            "ones below 0.05; the note under the table says how many would look "
            "significant by chance.",
            "Exact Wilcoxon signed-rank, from the full sign-flip distribution "
            "rather than a normal approximation.",
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
            "The verdict in words, taken from this row's p-value.",
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
        self._figures_btn = QPushButton("Save figures…", self)
        self._figures_btn.setToolTip(
            _tip(
                "Writes every figure on this page to a folder as PNG, at print "
                "resolution rather than screen resolution.",
                "Filenames carry the metric, the sources and the organ, so a folder "
                "of them stays identifiable once they are out of here.",
            )
        )
        self._figures_btn.clicked.connect(self._on_save_figures)
        header.addWidget(self._figures_btn)
        self._export_btn = QPushButton("Export PDF…", self)
        self._export_btn.setToolTip(
            _tip(
                "Writes everything on this page to one PDF: the selections it was "
                "produced under, the tables, the figures, the warnings and the "
                "methods paragraph.",
                "A table on its own loses the conditions it was computed under, and "
                "those conditions are most of what makes it readable a year later. "
                "Aggregate throughout — no patient identifiers are written.",
            )
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
                "Varying both at once would put a great many comparisons on one "
                "screen, and scanning that many for the ones below 0.05 turns a set "
                "of separate questions into a search.",
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
            "Which organs to show. Each is analysed on its own, so adding or "
            "removing one changes what is displayed and nothing about the "
            "comparisons themselves."
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
        paired_box = QGroupBox("Paired differences", self)
        paired_layout = QVBoxLayout(paired_box)
        paired_layout.setContentsMargins(6, 6, 6, 6)
        paired_controls = QHBoxLayout()
        paired_controls.addWidget(QLabel("Show pairs for", self))
        self._paired_combo = QComboBox(self)
        self._paired_combo.setToolTip(
            _tip(
                "Which row of the comparison table to open up. Across organs "
                "these are the organs; across sources they are the sources.",
                "The distribution figure shows what each source produced, but "
                "not which two points came from the same patient — and that is "
                "the whole basis of a paired test. Two sources can have "
                "near-identical distributions while every patient moved the same "
                "way, and identical distributions while the movement was noise. "
                "The summary looks the same; the conclusion is opposite.",
            )
        )
        self._paired_combo.currentIndexChanged.connect(self._draw_paired)
        paired_controls.addWidget(self._paired_combo, stretch=1)
        paired_layout.addLayout(paired_controls)
        self._paired = PairedCanvas()
        paired_layout.addWidget(self._paired)
        outer.addWidget(paired_box)

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
        self._sort_check = QCheckBox("Sort rows by effect size", self)
        self._sort_check.setToolTip(
            _tip(
                "Off by default, and that is deliberate. The standing order is "
                "anatomical (or vendor) and does not move when the data do, so a "
                "reader comparing this figure with the same figure on another "
                "metric finds each row in the same place.",
                "Sorting by effect makes the largest difference easiest to see, at "
                "the cost of every row shifting whenever the metric changes.",
            )
        )
        self._sort_check.toggled.connect(self._recompute)
        forest_controls = QHBoxLayout()
        forest_controls.addWidget(self._relative_check)
        forest_controls.addWidget(self._sort_check)
        forest_controls.addStretch(1)
        forest_layout.addLayout(forest_controls)
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
            self._paired_combo.clear()
            self._paired.plot([], metric or "", reference="", challenger="")
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
            display=readable_metric(metric),
            units=metric_units(metric),
        )
        scales = None
        if self._relative_check.isChecked():
            scales = {
                label: self._reference_median(label, metric, reference, axis, organs)
                for label in family
            }
        tolerance = tolerance_note(metric, *self._tolerances())
        metric_name = readable_metric(metric)
        if axis is FamilyAxis.SOURCES:
            organ_name = organs[0] if organs else ""
            forest_title = f"{organ_name}: {metric_name}"
            forest_subtitle = f"Each source minus baseline {reference}"
        else:
            forest_title = f"{metric_name}: {challenger} versus {reference}"
            forest_subtitle = f"Paired Hodges–Lehmann difference, {challenger} − {reference}"
        if tolerance:
            forest_subtitle = f"{forest_subtitle} · {tolerance}"
        self._repopulate_paired(family)
        self._forest.plot(
            family,
            metric,
            scales=scales,
            units=metric_units(metric),
            title=forest_title,
            subtitle=forest_subtitle,
            sort_by_effect=self._sort_check.isChecked(),
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

    def _tolerances(self) -> tuple[float | None, float | None]:
        """Surface-Dice and APL tolerances for the loaded results, if recorded.

        Surface Dice at 1 mm and at 5 mm are different measurements, so a figure
        that omits which was used cannot be compared with another.
        """
        if self._results is None:
            return (None, None)
        try:
            return tuple(self._results.tolerances())
        except (AttributeError, TypeError, ValueError):
            return (None, None)

    def _repopulate_paired(self, family: dict) -> None:
        """Offer the rows that actually have a comparison to open up."""
        combo = self._paired_combo
        previous = combo.currentText()
        available = [label for label, result in family.items() if result is not None]
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(available)
        if previous in available:
            combo.setCurrentText(previous)
        combo.blockSignals(False)
        combo.setEnabled(len(available) > 1)
        self._draw_paired()

    def _draw_paired(self) -> None:
        """The chosen row, patient by patient."""
        metric = self._selected_metric()
        reference = self._reference_combo.currentText()
        label = self._paired_combo.currentText()
        axis = self._axis()
        if not metric or not reference or not label:
            self._paired.plot([], metric or "", reference=reference, challenger="")
            return
        if axis is FamilyAxis.SOURCES:
            organ, challenger = self._selected_organ(), label
        else:
            organ, challenger = label, self._challenger_combo.currentText()
        if not organ or not challenger or challenger == reference:
            self._paired.plot([], metric, reference=reference, challenger=challenger)
            return

        tolerance = tolerance_note(metric, *self._tolerances())
        subtitle = f"Each line is one patient · {challenger} against {reference}"
        if tolerance:
            subtitle = f"{subtitle} · {tolerance}"
        self._paired.plot(
            self._model.paired_values(organ, metric, reference, challenger),
            metric,
            display=readable_metric(metric),
            reference=reference,
            challenger=challenger,
            organ=organ,
            units=metric_units(metric),
            subtitle=subtitle,
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
                for column, text in enumerate([organ if first else "", source, cell.summary()]):
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
                # Shown rather than dropped: a declared comparison that could
                # not be made is a result, and silently omitting the row would
                # leave the reader believing it was never asked for.
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
                            "The row is kept so the question it represents stays "
                            "visible; dropping it would leave no trace that the "
                            "comparison was asked for.",
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
        if result.p_value > _ALPHA:
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
                + " had no patient contoured by both sources, so no comparison "
                "could be made. The rows are shown rather than omitted."
            )
        if estimable and not self._model.family_can_detect(family.values(), _ALPHA):
            largest = max(r.n_pairs for r in estimable.values())
            notes.append(
                f"<b>Nothing here can reach significance.</b> The largest comparison "
                f"shown has {largest} paired patient(s), and below six the smallest "
                f"attainable p-value exceeds 0.05 — so no row can be significant "
                f"however the contours look. Read the effect sizes and intervals "
                f"instead, and treat the p-values as unusable at this sample size."
            )
        thin_rows = [r for r in estimable.values() if r.n_pairs < 6]
        if thin_rows and len(thin_rows) != len(estimable):
            notes.append(
                f"<b>{len(thin_rows)} of {len(estimable)} {unit} have fewer than six "
                "paired patients</b>, which cannot produce a p-value at or below 0.05 "
                "whatever the data show. Their p-values are not evidence of similarity."
            )
        if len(estimable) > 1:
            expected = expected_false_positives(len(estimable), _ALPHA)
            notes.append(
                f"<b>{len(estimable)} comparisons are shown, each answering its own "
                f"question.</b> p-values are per {axis.noun} and unadjusted, so a row "
                "does not change because another is displayed. If you scan the table "
                f"for rows below 0.05, expect about <b>{expected:.1f}</b> to appear "
                "there by chance even if every source performed identically — so "
                "report every row, not only the ones that crossed."
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
        # Equal counts are not equal patients. Two rows both reading n = 8 can
        # rest on different eights, and across a table the counts matching makes
        # the comparison look like a comparison.
        if len(estimable) > 1:
            if axis is FamilyAxis.SOURCES:
                organ = self._selected_organ()
                members = {label: (organ, label) for label in estimable}
            else:
                members = {label: (label, challenger) for label in estimable}
            sets = self._model.pairing_sets(metric, reference, members)
            distinct = {frozenset(patients) for patients in sets.values()}
            if len(distinct) > 1:
                sizes = {len(patients) for patients in sets.values()}
                notes.append(
                    "<b>The rows do not all use the same patients.</b> Each comparison "
                    "runs on the patients that pair for it, so a row is sound on its own "
                    "but two rows are not measured on the same cohort"
                    + (
                        " — and their counts match, which makes that easy to miss."
                        if len(sizes) == 1
                        else "."
                    )
                    + " The paired view below always shows the patients belonging to the "
                    "row it names."
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
            family_clause = f"{len(family)} organs"
            caveat = ""
        self._methods.setText(
            opening + ", using the Wilcoxon signed-rank test on patients where both "
            f"produced the organ (n = {span} pairs). Zero differences were handled by Pratt's "
            "method and p-values computed exactly from the conditional sign-flip distribution. "
            "Differences are summarised by the Hodges–Lehmann estimator with a 95% confidence "
            "interval obtained by inverting the same test. "
            "An exact sign test is reported alongside. "
            f"Each of the {family_clause} is treated as a separate question — for that "
            "organ and metric, is there evidence that the two sources differ? — so "
            "p-values are reported <b>unadjusted</b> and no multiplicity correction is "
            "applied across them"
            + (
                f". {len(family) - len(estimable)} could not be estimated and are reported as such"
                if len(estimable) != len(family)
                else ""
            )
            + ". Every comparison is reported rather than a selected subset, which is what "
            "makes that defensible." + caveat + " Descriptive values "
            "are median [Q1, Q3] with a distribution-free 95% interval for the median, which "
            "is not estimable below six observations."
        )

    # ---- Export -----------------------------------------------------------

    # ---- Saving -----------------------------------------------------------

    def _figure_stem(self) -> str:
        """A filename stem that says what the figures are of."""
        parts = [
            self._selected_metric() or "metric",
            self._reference_combo.currentText() or "reference",
        ]
        if self._axis() is FamilyAxis.SOURCES:
            parts.append(self._selected_organ() or "organ")
        else:
            parts.append(self._challenger_combo.currentText() or "challenger")
        cleaned = ["".join(c if c.isalnum() else "_" for c in part).strip("_") for part in parts]
        return "_".join(part for part in cleaned if part) or "report"

    def _on_save_figures(self) -> None:
        if not self._model.organs():
            QMessageBox.information(
                self, "Save figures", "Compute metrics first — there is nothing to draw."
            )
            return
        folder = QFileDialog.getExistingDirectory(self, "Save figures to folder")
        if not folder:
            return
        stem = self._figure_stem()
        written: list[str] = []
        try:
            for name, canvas in self._figures():
                target = Path(folder) / f"{stem}_{name}.png"
                # 200 dpi rather than the screen's, so a figure dropped into a
                # manuscript is not a blurry screenshot of one.
                canvas.figure.savefig(target, dpi=200, bbox_inches="tight", facecolor="white")
                written.append(target.name)
        except OSError as exc:
            QMessageBox.critical(self, "Save figures", f"Could not write the figures:\n{exc}")
            return
        QMessageBox.information(self, "Save figures", "Written:\n" + "\n".join(written))

    def _figures(self) -> list[tuple[str, Any]]:
        return [
            ("distributions", self._distribution),
            ("paired", self._paired),
            ("forest", self._forest),
        ]

    # ---- Export -----------------------------------------------------------

    def _on_export(self) -> None:
        """The whole page as one PDF.

        Everything visible goes in, in the order it is read on screen. A table
        exported on its own loses the selections it was produced under — which
        ground truth, which metric, which sources, which organs — and those are
        most of what makes it interpretable to someone who was not driving the
        software at the time, including its author months later.
        """
        if not self._model.organs():
            QMessageBox.information(
                self, "Export", "Nothing to export yet — compute metrics first."
            )
            return
        path_text, _ = QFileDialog.getSaveFileName(
            self, "Export report", f"{self._figure_stem()}.pdf", "PDF files (*.pdf)"
        )
        if not path_text:
            return
        try:
            self._write_pdf(Path(path_text))
        except OSError as exc:
            QMessageBox.critical(self, "Export", f"Could not write the file:\n{exc}")
            return
        QMessageBox.information(self, "Export", f"Written to {Path(path_text).name}.")

    def _write_pdf(self, target: Path) -> None:
        """Render the page to ``target`` through Qt's own PDF writer.

        HTML into a QTextDocument rather than drawing to a painter: the tables
        keep real typography and reflow to the page, and figures embed as images
        at their drawn resolution. No new dependency, and nothing here has to
        know about page breaks.
        """
        writer = QPdfWriter(str(target))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageOrientation(QPageLayout.Orientation.Landscape)
        writer.setResolution(150)
        writer.setTitle("AutoSeg Evaluator — statistical report")

        document = QTextDocument()
        document.setDefaultStyleSheet(_PDF_STYLE)
        with TemporaryDirectory() as scratch:
            document.setHtml(self._pdf_html(Path(scratch)))
            document.setPageSize(QSizeF(writer.width(), writer.height()))
            document.print_(writer)

    def _pdf_html(self, scratch: Path) -> str:
        """The page as HTML, with the figures written beside it as PNGs.

        Laid out as a report rather than as a dump of the screen: a masthead,
        the conditions it was produced under, then the sections in reading
        order, then a sign-off saying what produced it and when. The figures go
        in exactly as drawn — they have their own typography, and restyling them
        to match the page would trade legibility for a matching palette.
        """
        axis = self._axis()
        metric = self._selected_metric()
        reference = self._reference_combo.currentText()
        challenger = self._challenger_combo.currentText()
        tolerance = tolerance_note(metric, *self._tolerances())
        produced = datetime.now().strftime("%d %B %Y · %H:%M")

        fixed_label = "Challenger" if axis is FamilyAxis.ORGANS else "Organ"
        fixed_value = (challenger if axis is FamilyAxis.ORGANS else self._selected_organ()) or "—"

        sections = ["Coverage", "Descriptive statistics", "Paired comparison"]
        if self._acquisition.available:
            sections.append("Acquisition")

        parts = [_masthead_html(produced), "<hr/>", "<h1>Auto-contouring evaluation report</h1>"]
        parts.append("<p class='subtitle'>" + "  ·  ".join(sections) + "</p>")
        parts.append(
            _panel_html(
                [
                    ("Metric", readable_metric(metric) or "—"),
                    ("Tolerance", tolerance.replace("tolerance = ", "") if tolerance else "n/a"),
                    ("Ground truth", self._ground_truth_combo.currentText() or "—"),
                ],
                [
                    ("Compared against", reference or "—"),
                    (fixed_label, fixed_value),
                    ("Compared across", axis.plural),
                ],
                [("Cohort", self._summary_label.text() or "—")],
            )
        )

        parts.append(_section("Coverage") + _table_html(self._coverage_table))
        parts.append(
            _section("Descriptive statistics")
            + f"<p class='note'>{self._descriptive_note.text()}</p>"
            + _table_html(self._descriptive_table)
        )
        parts.append(_section("Paired comparison") + _table_html(self._comparison_table))

        for name, canvas in self._figures():
            image = scratch / f"{name}.png"
            canvas.figure.savefig(image, dpi=150, bbox_inches="tight", facecolor="white")
            parts.append(_section(name) + f"<img src='{image.as_uri()}' width='940'/>")

        if self._acquisition.available:
            parts.append(
                _section("Acquisition parameters")
                + f"<p class='note'>{self._acquisition_note.text()}</p>"
                + _table_html(self._image_table)
            )

        comments = []
        if self._warning.text():
            for note in self._warning.text().split("<br><br>"):
                comments.append(("Caution", note))
        if self._methods.text():
            comments.append(("Methods", self._methods.text()))
        if comments:
            parts.append(_section("Notes & interpretation") + _comments_html(comments))

        parts.append(_signoff_html(produced))
        return "<html><body>" + "".join(parts) + "</body></html>"


#: The PDF follows a clinical-report idiom rather than the screen's: spaced
#: small-caps section labels, a serif title, a teal rule under a masthead, and a
#: sign-off block. It is the visual language of a document that gets printed,
#: filed and read months later, which is what this one is for.
#:
#: The figures are exempt. They carry their own typography, chosen for what they
#: have to show, and restyling them to match a page would cost legibility for
#: the sake of a matching palette.
_INK = "#1B2A32"
_ACCENT = "#15606E"
_MUTED = "#6B7B85"
_RULE = "#C9D6DC"
_PANEL = "#E8EFF2"
_ROW_TINT = "#F5F8F9"

_PDF_STYLE = f"""
body {{ color: {_INK}; font-family: "Segoe UI", Calibri, Arial, sans-serif; }}
td, th, p {{ font-family: "Segoe UI", Calibri, Arial, sans-serif; }}
h1 {{ font-family: Georgia, "Times New Roman", serif; font-size: 19pt;
      color: {_INK}; margin: 2px 0 2px 0; font-weight: normal; }}
p.subtitle {{ font-family: Georgia, "Times New Roman", serif; font-style: italic;
              color: {_ACCENT}; font-size: 9.5pt; margin: 0 0 14px 0; }}
p.masthead {{ color: {_ACCENT}; font-size: 12pt; font-weight: bold; margin: 0; }}
p.masthead-sub {{ color: {_MUTED}; font-size: 7.5pt; margin: 3px 0 0 0; }}
p.section {{ color: {_ACCENT}; font-size: 8pt; font-weight: bold;
             margin: 16px 0 5px 0; }}
p.note {{ color: {_MUTED}; font-size: 7.5pt; margin: 0 0 5px 0; }}
p.methods {{ color: {_INK}; font-size: 8pt; margin: 0; }}
p.sign-name {{ font-family: Georgia, "Times New Roman", serif; font-style: italic;
               color: {_ACCENT}; font-size: 16pt; margin: 14px 0 0 0; }}
p.sign-role {{ color: {_INK}; font-size: 8.5pt; font-weight: bold; margin: 3px 0 0 0; }}
p.sign-meta {{ font-family: Georgia, "Times New Roman", serif; font-style: italic;
               color: {_MUTED}; font-size: 7.5pt; margin: 2px 0 0 0; }}
table.data {{ border-collapse: collapse; width: 100%; font-size: 7.5pt; }}
table.data th {{ background-color: {_ACCENT}; color: #FFFFFF; font-size: 7pt;
                 padding: 5px 6px; text-align: left; border: 1px solid {_ACCENT}; }}
table.data td {{ padding: 4px 6px; border: 1px solid {_RULE}; }}
table.panel {{ border-collapse: collapse; width: 100%; margin: 0 0 4px 0; }}
table.panel td {{ padding: 9px 12px; font-size: 8pt;
                  background-color: {_PANEL}; }}
table.masthead {{ border-collapse: collapse; width: 100%; }}
table.comments {{ border-collapse: collapse; width: 100%; }}
table.comments td {{ padding: 4px 0 8px 0; vertical-align: top; }}
td.comment-label {{ color: {_ACCENT}; font-size: 7pt; font-weight: bold; width: 22%; }}
td.comment-body {{ color: {_INK}; font-size: 8pt; }}
"""


def _spaced(text: str) -> str:
    """``RESULTS`` -> ``R E S U L T S``.

    The report idiom this follows letter-spaces its small-caps labels, and
    QTextDocument's CSS subset has no ``letter-spacing``. Spacing the characters
    is the same effect by other means; it costs nothing because these labels are
    never read as words, only recognised as section markers.
    """
    return "   ".join(" ".join(word) for word in str(text).upper().split())


def _section(title: str) -> str:
    return f"<p class='section'>{_spaced(title)}</p>"


def _masthead_html(produced: str) -> str:
    """Product name left, logo right, above a rule."""
    logo = _ASSET_DIR / "icon.png"
    badge = f"<img src='{logo.as_uri()}' width='52' height='52'/>" if logo.exists() else ""
    return (
        "<table class='masthead'><tr>"
        "<td>"
        f"<p class='masthead'>{_spaced('AutoSeg Evaluator')}</p>"
        f"<p class='masthead-sub'>Auto-contouring evaluation · version {__version__} · "
        f"generated {produced}</p>"
        "</td>"
        f"<td align='right'>{badge}</td>"
        "</tr></table>"
    )


def _panel_html(*columns: list[tuple[str, str]]) -> str:
    """The conditions block: spaced small-caps labels over their values.

    These are what make the tables interpretable later — which metric, which
    ground truth, which sources — so they sit above the data rather than in a
    footnote under it.
    """
    cells = []
    for column in columns:
        entries = "".join(
            f"<p class='section' style='margin:0 0 2px 0'>{_spaced(label)}</p>"
            f"<p style='margin:0 0 8px 0; font-size:8.5pt'>{value}</p>"
            for label, value in column
        )
        cells.append(f"<td>{entries}</td>")
    return "<table class='panel'><tr>" + "".join(cells) + "</tr></table>"


def _comments_html(entries: list[tuple[str, str]]) -> str:
    """Label on the left, prose on the right — the interpretation idiom."""
    rows = "".join(
        f"<tr><td class='comment-label'>{_spaced(label)}</td>"
        f"<td class='comment-body'>{body}</td></tr>"
        for label, body in entries
    )
    return f"<table class='comments'>{rows}</table>"


def _signoff_html(produced: str) -> str:
    """Who produced it and from what — the report's provenance, not a signature.

    Deliberately not styled as an authorising signature: nothing here has been
    reviewed by a person, and a document that looks signed invites the reader to
    assume it was.
    """
    return (
        "<hr/>"
        f"<p class='sign-name'>AutoSeg Evaluator</p>"
        f"<p class='sign-role'>Generated report · version {__version__}</p>"
        "<p class='sign-meta'>Produced automatically from the computed metrics. "
        "Not reviewed or approved by a person.</p>"
    )


def _table_html(table: QTableWidget) -> str:
    """One Qt table as an HTML table, blanks and all.

    Blank cells are kept blank: the organ column is deliberately empty on
    continuation rows, and filling it back in for the PDF would undo the
    grouping the table exists to show.
    """
    headers = [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())]
    rows = []
    for row in range(table.rowCount()):
        cells = []
        for column in range(table.columnCount()):
            item = table.item(row, column)
            value = item.text() if item is not None else ""
            weight = " style='font-weight:bold'" if item is not None and item.font().bold() else ""
            cells.append(f"<td{weight}>{value}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<table class='data'><tr>"
        + "".join(f"<th>{header}</th>" for header in headers)
        + "</tr>"
        + "".join(rows)
        + "</table>"
    )


__all__ = ["ReportTab"]
