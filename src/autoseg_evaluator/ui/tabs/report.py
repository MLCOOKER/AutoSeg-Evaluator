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

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PySide6.QtCore import QMarginsF, QPointF, QRect, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QPen,
    QTextDocument,
    QTextTable,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
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
from autoseg_evaluator.ui.deferred_refresh import DeferredRefresh
from autoseg_evaluator.ui.widgets.stat_plots import (
    DistributionCanvas,
    ForestCanvas,
    PairedCanvas,
    SidewaysScroll,
)

_ALPHA = 0.05

#: A4 landscape at 150 dpi less the printer's margins. Only a fallback: the real
#: geometry comes from the writer, and this is what the HTML is built against
#: when it is produced on its own, without a page to print onto.
_PAGE_WIDTH = 1754
_PAGE_HEIGHT = 1240

#: Where the packaged artwork lives.
_ASSET_DIR = Path(__file__).resolve().parents[2] / "assets"

#: The coverage table's Truncated column, from ``ReportModel.truncation``.
_TRUNCATION_TEXT = {True: "yes", False: "no", None: "partly"}

#: What the paired comparison is, in two sentences above its table. The methods
#: paragraph at the foot of the tab says it in full; this is for the reader who
#: looks at the table first.
PAIRED_TEST_NOTE = (
    "Each row is an exact, two-sided Wilcoxon signed-rank test on the per-patient "
    "differences (challenger minus reference), using the patients where both "
    "sources produced the organ. The difference shown is the Hodges–Lehmann "
    "estimate with its 95% confidence interval, an exact sign test is given "
    "alongside, and p-values are not adjusted for multiple comparisons."
)

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


#: Room added to each column beyond its widest entry, so a number does not sit
#: against the next column's rule.
COLUMN_PADDING = 14


class _CompactTable(QTableWidget):
    """A table only as wide as its columns, each as wide as its widest entry.

    Stretched across the window, a table with a few short columns spread its
    numbers so far apart that a row no longer read as one row. In a window
    narrower than the table it scrolls sideways within itself, and grows by the
    scroll bar's height while it does, so the bottom row is not hidden.

    Its preferred size comes from its contents only, never from its current
    size, so a layout sizing it from that cannot feed back into it.
    """

    def __init__(self, rows: int, columns: int, parent: QWidget | None = None) -> None:
        super().__init__(rows, columns, parent)
        self._fitted = QSize()
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.horizontalScrollBar().rangeChanged.connect(self._apply_height)

    def fit(self, max_rows: int) -> None:
        """Size to the contents: every row up to ``max_rows``, then scroll.

        A fixed height wastes the page on a four-row table and hides rows on a
        forty-row one.
        """
        self.resizeColumnsToContents()
        header = self.horizontalHeader()
        for column in range(self.columnCount()):
            if not self.isColumnHidden(column):
                header.resizeSection(column, header.sectionSize(column) + COLUMN_PADDING)

        rows = self.rowCount()
        row_height = self.rowHeight(0) if rows else self.verticalHeader().defaultSectionSize()
        visible = min(rows, max_rows) if rows else 0
        # Two pixels of frame, and half a row of headroom when scrolling so the
        # cut-off row reads as "there is more" rather than as the end.
        height = header.height() + visible * row_height + 4
        if rows > max_rows:
            height += row_height // 2
        height = max(height, header.height() + row_height + 4)

        width = header.length() + 2 * self.frameWidth()
        if rows > max_rows:
            width += self.verticalScrollBar().sizeHint().width()
        self._fitted = QSize(width, height)
        self.updateGeometry()
        self._apply_height()

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt override
        return self._fitted if self._fitted.isValid() else super().sizeHint()

    def _apply_height(self) -> None:
        if not self._fitted.isValid():
            return
        height = self._fitted.height()
        if self.horizontalScrollBar().maximum() > 0:
            height += self.horizontalScrollBar().sizeHint().height()
        self.setFixedHeight(height)


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
        "Truncated",
        _tip(
            "Whether this organ's test contours were cut to the ground truth's craniocaudal extent before being measured — the drawer's <b>Truncate</b> setting on the Match Contours tab.",
            "<b>partly</b> means drawers pooled under this organ were set differently.",
        ),
    ),
    (
        "Test-only PTV overlap",
        _tip(
            "How many of this source's contours of this organ overlap the PTV where the ground truth's does not, out of the cases where both overlaps were measured.",
            "Any overlap counts. The PTV is every structure typed PTV in the ground truth's structure set, combined. A test contour that misses an overlap the ground truth has is not counted here; the PTV overlap difference shows it.",
            "Shown only when PTV overlap was computed. <b>—</b>: not measured for this organ and source, as with a consensus ground truth.",
        ),
    ),
]

#: Where the coverage table holds the PTV overlap count, hidden when no case had
#: its overlap measured.
COVERAGE_PTV_COLUMN = len(COVERAGE_COLUMNS) - 1

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
        #: The cautions and the acquisition note as the PDF prints them: without
        #: pointers to the screen, and without patient identifiers.
        self._printed_notes: list[str] = []
        self._printed_acquisition_note = ""
        #: Why no paired comparison can be made, on screen and on paper; empty
        #: when one can.
        self._unavailable: tuple[str, str] = ("", "")
        self._build_ui()
        self._render_empty()
        self._deferred = DeferredRefresh(self, self.refresh)

    # ---- Wiring -----------------------------------------------------------

    def set_results_manager(self, manager: Any) -> None:
        self._results = manager

    def set_library(self, library: Any) -> None:
        """The scanned DICOM library, for the acquisition section only."""
        self._library = library
        self._acquisition = collect_acquisition(library)
        self._fill_acquisition()

    def request_refresh(self) -> None:
        """Bring the report up to date: soon if visible, else when next shown.

        For changes that come in streams — result rows, Likert scores. No metric
        is recomputed by a refresh, but every statistic and figure on the page
        is, which takes seconds on a large cohort; nobody reads a hidden page.
        """
        self._deferred.request()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._deferred.shown()

    def refresh(self) -> None:
        """Rebuild from the current results, now. No metric is recomputed."""
        self._deferred.settled()
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
        descriptive_layout.addWidget(self._descriptive_table, alignment=Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(descriptive_box)

        self._comparison_table = self._make_table(COMPARISON_COLUMNS)
        comparison_box = QGroupBox("Paired comparison", self)
        comparison_layout = QVBoxLayout(comparison_box)
        comparison_layout.setContentsMargins(6, 6, 6, 6)
        self._comparison_note = QLabel(PAIRED_TEST_NOTE, self)
        self._comparison_note.setWordWrap(True)
        self._comparison_note.setStyleSheet("color:#777; font-size:11px;")
        comparison_layout.addWidget(self._comparison_note)
        comparison_layout.addWidget(self._comparison_table, alignment=Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(comparison_box)

        # Each figure scrolls sideways inside its own section. The distributions
        # widen with the organ count, and placed straight in the tab that width
        # became the tab's, stretching every table and caption to match it.
        self._distribution = DistributionCanvas()
        self._distribution_scroll = SidewaysScroll(self._distribution, self)
        outer.addWidget(self._wrap("Distributions", self._distribution_scroll))
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
        self._paired_scroll = SidewaysScroll(self._paired, self)
        paired_layout.addWidget(self._paired_scroll)
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
        self._forest_scroll = SidewaysScroll(self._forest, self)
        forest_layout.addWidget(self._forest_scroll)
        outer.addWidget(forest_box)

        self._acquisition_box = QGroupBox("Acquisition parameters", self)
        acquisition_layout = QVBoxLayout(self._acquisition_box)
        acquisition_layout.setContentsMargins(6, 6, 6, 6)
        self._acquisition_note = QLabel("", self)
        self._acquisition_note.setWordWrap(True)
        self._acquisition_note.setStyleSheet("color:#777; font-size:11px;")
        acquisition_layout.addWidget(self._acquisition_note)
        self._image_table = self._make_table(ACQUISITION_COLUMNS)
        acquisition_layout.addWidget(self._image_table, alignment=Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(self._acquisition_box)

        self._methods = QLabel("", self)
        self._methods.setWordWrap(True)
        self._methods.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._methods.setStyleSheet("color:#555; font-size:11px;")
        outer.addWidget(self._methods)

    @staticmethod
    def _make_table(columns: list[tuple[str, str]]) -> _CompactTable:
        table = _CompactTable(0, len(columns))
        table.setHorizontalHeaderLabels([title for title, _tooltip in columns])
        for index, (_title, tooltip) in enumerate(columns):
            header_item = table.horizontalHeaderItem(index)
            if header_item is not None:
                header_item.setToolTip(tooltip)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        return table

    @staticmethod
    def _fit_table(table: _CompactTable, max_rows: int = MAX_VISIBLE_ROWS) -> None:
        """Size a table to its contents, up to a ceiling of rows, then scroll."""
        table.fit(max_rows)

    @staticmethod
    def _wrap(title: str, widget: QWidget) -> QWidget:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 6, 6, 6)
        # A table keeps to the left at its own width; a figure fills the row.
        if isinstance(widget, _CompactTable):
            layout.addWidget(widget, alignment=Qt.AlignmentFlag.AlignLeft)
        else:
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
            self._unavailable = ("", "")
            self._comparison_note.setText(PAIRED_TEST_NOTE)
            self._comparison_table.setHidden(False)
            self._printed_notes = []
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
        self._unavailable = self._nothing_to_compare(axis, reference, challenger)
        # Said where the comparison would be, rather than left as an empty
        # table and two blank figures for the reader to puzzle over.
        self._comparison_note.setText(self._unavailable[0] or PAIRED_TEST_NOTE)
        self._comparison_table.setHidden(bool(self._unavailable[0]))
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
        tolerance = tolerance_note(metric)
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
            empty=self._unavailable[0] or "Nothing to compare",
        )
        self._write_methods(metric, family, reference, challenger, axis)

    def _nothing_to_compare(
        self, axis: FamilyAxis, reference: str, challenger: str
    ) -> tuple[str, str]:
        """Why no paired comparison can be made, as ``(on screen, on paper)``.

        Both empty when one can. The ground truth is never a comparator — every
        metric already measures agreement with it — so a cohort with one test
        source has nothing to pair that source with.
        """
        sources = self._model.sources()
        if len(sources) == 1:
            alone = (
                f"Only one test source ({sources[0]}), and a paired comparison needs "
                f"two, so none was made. The coverage, descriptive statistics and "
                f"distributions describe {sources[0]} on its own."
            )
            return alone, alone
        if axis is FamilyAxis.ORGANS and reference and reference == challenger:
            return (
                "The challenger is the reference. Choose a different source to compare.",
                "No paired comparison was made: the challenger and the reference were "
                "the same source.",
            )
        return "", ""

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
            self._printed_acquisition_note = ""
        else:
            varying = sum(1 for summary in report.images if summary.values and not summary.uniform)
            counted = (
                f"{report.n_series} image series and {report.n_structure_sets} structure "
                f"sets across {report.n_patients} patients."
            )
            scope = (
                "  Equipment and geometry only: no identifiers, dates, institutions or "
                "free-text descriptions are read."
            )
            uniform = "  Every parameter is uniform across the cohort."
            self._acquisition_note.setText(
                counted
                + (
                    f"  {varying} parameter(s) vary across the cohort — hover those rows."
                    if varying
                    else uniform
                )
                + scope
            )
            # Nothing to hover on paper; the varying rows show their spread.
            self._printed_acquisition_note = (
                counted
                + (
                    f"  {varying} parameter(s) vary across the cohort, shown as each "
                    "value with its count, or as a range."
                    if varying
                    else uniform
                )
                + scope
            )

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
            self._paired.plot(
                [],
                metric or "",
                reference=reference,
                challenger="",
                **({"empty": self._unavailable[0]} if self._unavailable[0] else {}),
            )
            return
        if axis is FamilyAxis.SOURCES:
            organ, challenger = self._selected_organ(), label
        else:
            organ, challenger = label, self._challenger_combo.currentText()
        if not organ or not challenger or challenger == reference:
            self._paired.plot([], metric, reference=reference, challenger=challenger)
            return

        tolerance = tolerance_note(metric)
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
                cut = _TRUNCATION_TEXT[self._model.truncation(organ)] if first else ""
                counted = self._model.ptv_only_overlap(organ, source)
                ptv = f"{counted[0]} of {counted[1]}" if counted else "—"
                for column, text in enumerate(
                    [organ if first else "", source, cell.summary(), cut, ptv]
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
        # Only where the overlap was computed: a column of dashes would suggest
        # a measurement that was never asked for.
        table.setColumnHidden(COVERAGE_PTV_COLUMN, not self._model.measured_ptv_overlap())
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
        # The PDF's version of each note. Most read the same on paper; a few
        # point at the screen (a view below, another tab) or name patients,
        # which the export promises not to do, and are reworded for it.
        printed: list[str] = []

        def add(note: str, on_paper: str | None = None) -> None:
            notes.append(note)
            printed.append(note if on_paper is None else on_paper)

        unit = axis.plural
        estimable = {organ: r for organ, r in family.items() if r is not None}
        missing = [organ for organ, r in family.items() if r is None]
        if self._model.conflicting_observations:
            discarded = (
                f"<b>{self._model.conflicting_observations} observation(s) discarded:</b> "
                "the same organ, source and metric were measured more than once "
                "within a single treatment context, with differing values. The first "
                "was kept. "
            )
            add(
                discarded + "This is not a second course — those are separated by "
                "linkage and handled below — so check the Results tab for a repeated "
                "structure set.",
                discarded + "This is not a second course, which is analysed as a case of "
                "its own; it suggests a structure set that was loaded twice.",
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
            reason = (
                ": each contributed more than one treatment context — a "
                "re-irradiation or a replan — for these organs, or the two sources "
                "were assessed on different ones. Two courses of one patient are not "
                "two independent observations, and choosing between them is a "
                "study-design decision, so neither is used."
            )
            add(
                f"<b>{len(excluded)} patient(s) excluded</b> "
                f"({', '.join(excluded[:4])}"
                + (f" and {len(excluded) - 4} more" if len(excluded) > 4 else "")
                + ")"
                + reason
                + " Restrict the cohort on Tab 1 if you intend to analyse a particular course.",
                # Counted, not named: the export carries no patient identifiers.
                f"<b>{len(excluded)} patient(s) excluded</b>" + reason,
            )
        if missing:
            add(
                f"<b>Not estimable:</b> {', '.join(missing[:4])}"
                + (f" and {len(missing) - 4} more" if len(missing) > 4 else "")
                + " had no patient contoured by both sources, so no comparison "
                "could be made. The rows are shown rather than omitted."
            )
        if estimable and not self._model.family_can_detect(family.values(), _ALPHA):
            largest = max(r.n_pairs for r in estimable.values())
            add(
                f"<b>Nothing here can reach significance.</b> The largest comparison "
                f"shown has {largest} paired patient(s), and below six the smallest "
                f"attainable p-value exceeds 0.05 — so no row can be significant "
                f"however the contours look. Read the effect sizes and intervals "
                f"instead, and treat the p-values as unusable at this sample size."
            )
        thin_rows = [r for r in estimable.values() if r.n_pairs < 6]
        if thin_rows and len(thin_rows) != len(estimable):
            add(
                f"<b>{len(thin_rows)} of {len(estimable)} {unit} have fewer than six "
                "paired patients</b>, which cannot produce a p-value at or below 0.05 "
                "whatever the data show. Their p-values are not evidence of similarity."
            )
        if len(estimable) > 1:
            expected = expected_false_positives(len(estimable), _ALPHA)
            add(
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
            add(
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
                same = (
                    "<b>The rows do not all use the same patients.</b> Each comparison "
                    "runs on the patients that pair for it, so a row is sound on its own "
                    "but two rows are not measured on the same cohort"
                    + (
                        " — and their counts match, which makes that easy to miss."
                        if len(sizes) == 1
                        else "."
                    )
                )
                add(
                    same + " The paired view below always shows the patients belonging "
                    "to the row it names.",
                    same + " The paired-differences figure shows the patients of the one "
                    "row it names.",
                )
        disconnected = [
            organ for organ, r in estimable.items() if r.ci.status is IntervalStatus.DISCONNECTED
        ]
        if disconnected:
            add(
                f"<b>Disconnected confidence set</b> in {', '.join(disconnected[:4])}: "
                "exact ties make the accepted region fall into separate pieces, so the "
                "interval shown encloses them and is wider than the true set."
            )
        approximate = [organ for organ, r in estimable.items() if not r.ci.exhaustive]
        if approximate:
            add(
                f"<b>Interval bracketed, not enumerated</b> in "
                f"{', '.join(approximate[:4])}: the sample was large enough that the "
                "confidence set was located by bisection, which assumes it is "
                "connected rather than establishing it."
            )
        disagreeing = [organ for organ, r in estimable.items() if not r.ci_agrees_with_test]
        if disagreeing:
            add(
                f"<b>Exact ties present</b> in {', '.join(disagreeing[:4])}: some paired "
                "differences are exactly zero, so the interval and the p-value need not "
                "agree for those rows."
            )
        self._warning.setText("<br><br>".join(notes))
        self._warning.setVisible(bool(notes))
        self._printed_notes = printed

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
            + "."
            + caveat
            + " Descriptive values "
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
        keep real typography and reflow to the page, and figures embed as
        images. No new dependency.

        The document is laid out on the writer itself, so a point size prints at
        that size. Laid out for the screen and printed at the writer's
        resolution, every size came out at 96/150 of itself, and 7.5 pt tables
        printed at 4.8 pt. Each page is then painted here rather than by
        ``QTextDocument.print_``, which is what puts a running header and a page
        number on it.
        """
        writer = QPdfWriter(str(target))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageOrientation(QPageLayout.Orientation.Landscape)
        writer.setPageMargins(_PAGE_MARGINS_MM, QPageLayout.Unit.Millimeter)
        writer.setResolution(_PDF_DPI)
        writer.setTitle("AutoSeg Evaluator — statistical report")

        band = round(_RUNNING_BAND_MM / 25.4 * _PDF_DPI)
        body = QSizeF(writer.width(), writer.height() - 2 * band)
        produced = datetime.now().strftime("%d %B %Y · %H:%M")
        with TemporaryDirectory() as scratch:
            document = self._paginated(Path(scratch), body, writer, produced)
            _paint_pages(document, writer, body, band, self._running_title(), produced)

    def _running_title(self) -> str:
        """What the head of every page after the first says."""
        metric = readable_metric(self._selected_metric()) or "—"
        reference = self._reference_combo.currentText() or "—"
        if self._axis() is FamilyAxis.SOURCES:
            subject = f"{self._selected_organ() or '—'}: every source against {reference}"
        else:
            subject = f"{self._challenger_combo.currentText() or '—'} against {reference}"
        return f"Auto-contouring evaluation report  ·  {metric}  ·  {subject}"

    def _paginated(self, scratch: Path, body: QSizeF, device: Any, produced: str) -> QTextDocument:
        """The document laid out on ``device``, with its page breaks repaired.

        QTextDocument breaks a page wherever the next line does not fit. That
        cut an organ's rows apart, leaving its second source unlabelled at the
        top of the next page; left a table's heading at the foot of one page and
        the table on the next; and put each figure's heading under the figure
        before it, because the image moved on and its heading did not. So the
        document is laid out, checked, and the first such break pushed to the
        next page, until none remains.
        """
        figures = self._figure_images(scratch, body.width(), body.height(), device.logicalDpiY())
        pagination = _Pagination()
        document = QTextDocument()
        document.documentLayout().setPaintDevice(device)
        document.setDefaultStyleSheet(_PDF_STYLE)
        for _attempt in range(_PAGINATION_PASSES):
            units: list[_Unit] = []
            document.setHtml(
                self._pdf_html(
                    scratch,
                    round(body.width()),
                    round(body.height()),
                    produced=produced,
                    figures=figures,
                    pagination=pagination,
                    units=units,
                    device=device,
                )
            )
            document.setPageSize(body)
            if not pagination.repair(document, units, body.height()):
                break
        return document

    def _pdf_html(
        self,
        scratch: Path,
        width: int = _PAGE_WIDTH,
        height: int = _PAGE_HEIGHT,
        *,
        produced: str | None = None,
        figures: dict[str, tuple[Path, int, int]] | None = None,
        pagination: _Pagination | None = None,
        units: list[_Unit] | None = None,
        device: Any = None,
    ) -> str:
        """The page as HTML, with the figures written beside it as PNGs.

        Laid out as a report rather than as a dump of the screen: the banner,
        the conditions it was produced under, then the sections in reading
        order, then a sign-off saying what produced it and when. The figures go
        in as drawn — they have their own typography, and restyling them to
        match the page would trade legibility for a matching palette.

        ``width`` and ``height`` are one page's body in device pixels. The
        banner and the figures fill the measure; each data table takes the
        central ``_TABLE_WIDTH_PERCENT`` of it, or more where its columns need
        it. ``pagination`` says which tables, headings and figures start a new
        page, and ``units`` receives what each top-level table is, in order, so
        the laid-out document can be checked against it.
        """
        axis = self._axis()
        metric = self._selected_metric()
        reference = self._reference_combo.currentText()
        challenger = self._challenger_combo.currentText()
        tolerance = tolerance_note(metric)
        produced = produced or datetime.now().strftime("%d %B %Y · %H:%M")
        if figures is None:
            figures = self._figure_images(scratch, width, height, _PDF_DPI)
        build = _Builder(width, pagination or _Pagination(), [] if units is None else units, device)

        fixed_label = "Challenger" if axis is FamilyAxis.ORGANS else "Organ"
        fixed_value = (challenger if axis is FamilyAxis.ORGANS else self._selected_organ()) or "—"

        sections = ["Coverage", "Descriptive statistics", "Paired comparison"]
        if self._acquisition.available:
            sections.append("Acquisition")

        parts = [_banner_html(scratch, width, build.image_scale)]
        parts.append("<h1>Auto-contouring evaluation report</h1>")
        parts.append("<p class='subtitle'>" + "  ·  ".join(sections) + "</p>")
        parts.append(f"<p class='provenance'>version {__version__} · generated {produced}</p>")
        parts.append(
            build.panel(
                _panel_html(
                    [
                        ("Metric", readable_metric(metric) or "—"),
                        (
                            "Tolerance",
                            tolerance.replace("tolerance = ", "") if tolerance else "n/a",
                        ),
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
        )

        parts.append(build.table("Coverage", self._coverage_table))
        parts.append(
            build.table(
                "Descriptive statistics", self._descriptive_table, self._descriptive_note.text()
            )
        )
        if self._unavailable[1]:
            # Said in place of the section, with no empty table or figures.
            parts.append(build.heading("Paired comparison", self._unavailable[1]))
        else:
            parts.append(
                build.table(
                    "Paired comparison", self._comparison_table, self._comparison_note.text()
                )
            )

        for name, _canvas in self._figures():
            if name in figures:
                parts.append(build.figure(name, _FIGURE_TITLES.get(name, name), *figures[name]))

        if self._acquisition.available:
            parts.append(
                build.table(
                    "Acquisition parameters", self._image_table, self._printed_acquisition_note
                )
            )

        comments = [("Caution", note) for note in self._printed_notes]
        if self._methods.text():
            comments.append(("Methods", self._methods.text()))
        if comments:
            parts.append(build.comments("Notes & interpretation", comments))

        parts.append(_signoff_html(produced))
        return "<html><body>" + "".join(parts) + "</body></html>"

    def _figure_images(
        self, scratch: Path, width: float, height: float, dpi: float
    ) -> dict[str, tuple[Path, int, int]]:
        """Each figure drawn for the page: ``{name: (png, width, height)}``, in pixels.

        Drawn at the size it prints at rather than scaled to fit, so its type
        prints at the size it was set in. The height allowed leaves room for its
        heading, so the two share a page.
        """
        free_width = width / dpi
        free_height = height / dpi - _FIGURE_HEADING_INCHES
        images: dict[str, tuple[Path, int, int]] = {}
        for name, canvas in self._figures():
            if self._unavailable[1] and name in _COMPARISON_FIGURES:
                continue  # nothing to compare: the section says so instead
            target = scratch / f"{name}.png"
            _save_for_print(canvas, target, *canvas.print_size(free_width, free_height), dpi)
            drawn = QImage(str(target))
            if drawn.isNull():
                continue
            scale = min(1.0, width / drawn.width(), free_height * dpi / drawn.height())
            images[name] = (target, round(drawn.width() * scale), round(drawn.height() * scale))
        return images


#: The PDF follows a clinical-report idiom rather than the screen's: a banner
#: dissolving into the page, spaced small-caps section labels, a serif title and
#: a sign-off block. It is the visual language of a document that gets printed,
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

#: How much of the page width each data table takes, centred. Sized to their
#: contents the tables sat at the left edge with most of the page white beside
#: them; across the full width, a few short columns drifted too far apart.
_TABLE_WIDTH_PERCENT = 75

#: The PDF's resolution. Text is vector at any value; this sets the figures'
#: pixels and the unit the layout measures in.
_PDF_DPI = 150

#: Page margins in millimetres: left, top, right, bottom.
_PAGE_MARGINS_MM = QMarginsF(12, 10, 12, 10)

#: The bands holding the running header and the footer, in millimetres, and
#: their type.
_RUNNING_BAND_MM = 8
_RUNNING_PT = 8.5
_RUNNING_FONT = "Segoe UI"

#: The most layout-and-repair passes made. Each pass moves one page break, so a
#: report needs about as many as it has pages; this only bounds a pathological
#: case, which then keeps the breaks where they fell.
_PAGINATION_PASSES = 60

#: The room a figure's heading takes above it, in inches: the section's top
#: margin, the heading and the space under it. A figure is drawn no taller than
#: a page less this, so the two always fit on one.
_FIGURE_HEADING_INCHES = 0.75

#: The figures that show a paired comparison, left out when none can be made.
_COMPARISON_FIGURES = frozenset({"paired", "forest"})

#: What each figure is called in the document. The keys are the filename stems
#: used when the figures are saved on their own, which are not titles.
_FIGURE_TITLES = {
    "distributions": "Distributions",
    "paired": "Paired differences",
    "forest": "Forest plot",
}

#: The band lifted out of the splash artwork for the masthead: wordmark,
#: strapline, dice and head. Measured against the shipped 1000x563 image, so it
#: moves if that artwork is replaced.
_BANNER_CROP_TOP = 85
_BANNER_CROP_HEIGHT = 325

#: How tall the masthead band is, as a fraction of the page width. Kept thin
#: enough that it introduces the first page rather than taking it over.
_BANNER_HEIGHT_SHARE = 0.125

#: What the band is filled with where the artwork does not reach. The artwork is
#: set flush right rather than left, which looks like a choice about where the
#: logo goes and is really about the seam: its left edge is pure black and
#: disappears into this fill, while its right edge carries the lit rim of the
#: head and would show as a hard line against it.
_BANNER_FILL = "#000000"

_PDF_STYLE = f"""
body {{ color: {_INK}; font-family: "Segoe UI", Calibri, Arial, sans-serif; font-size: 10.5pt; }}
td, th, p {{ font-family: "Segoe UI", Calibri, Arial, sans-serif; }}
p.banner {{ margin: 0 0 12px 0; }}
h1 {{ font-family: Georgia, "Times New Roman", serif; font-size: 22pt;
      color: {_INK}; margin: 6px 0 2px 0; font-weight: normal; }}
p.subtitle {{ font-family: Georgia, "Times New Roman", serif; font-style: italic;
              color: {_ACCENT}; font-size: 11pt; margin: 0 0 3px 0; }}
p.provenance {{ color: {_MUTED}; font-size: 9pt; margin: 0 0 12px 0; }}
p.section {{ color: {_ACCENT}; font-size: 10pt; font-weight: bold;
             margin: 18px 0 6px 0; }}
p.note {{ color: {_MUTED}; font-size: 10pt; margin: 0 0 6px 0; }}
p.plate {{ margin: 0; }}
p.sign-name {{ font-family: Georgia, "Times New Roman", serif; font-style: italic;
               color: {_ACCENT}; font-size: 16pt; margin: 14px 0 0 0; }}
p.sign-role {{ color: {_INK}; font-size: 10pt; font-weight: bold; margin: 3px 0 0 0; }}
p.sign-meta {{ font-family: Georgia, "Times New Roman", serif; font-style: italic;
               color: {_MUTED}; font-size: 9pt; margin: 3px 0 0 0; }}
table.data {{ border-collapse: collapse; font-size: 9pt; }}
table.data th {{ background-color: {_ACCENT}; color: #FFFFFF; font-size: 9pt;
                 padding: 6px 9px; text-align: left; border: 1px solid {_ACCENT}; }}
table.data td {{ padding: 5px 9px; border: 1px solid {_RULE}; }}
table.caption {{ margin: 18px 0 0 0; }}
table.figure {{ margin: 18px 0 0 0; }}
table.caption p.section {{ margin: 0 0 6px 0; }}
table.caption p.note {{ margin: 0; }}
table.caption td {{ padding: 0 0 8px 0; }}
table.figure p.section {{ margin: 0 0 6px 0; }}
table.panel {{ border-collapse: collapse; margin: 0 0 3px 0; }}
table.panel td {{ padding: 12px 16px; font-size: 10.5pt;
                  background-color: {_PANEL}; }}
table.comments {{ border-collapse: collapse; }}
table.comments td {{ padding: 4px 0 10px 0; vertical-align: top; }}
td.comment-label {{ color: {_ACCENT}; font-size: 9pt; font-weight: bold; }}
td.comment-body {{ color: {_INK}; font-size: 10.5pt; }}
"""

#: Word gap inside a letter-spaced label. HTML collapses runs of whitespace, so
#: ordinary spaces would close ``COMPARED AGAINST`` up into one unreadable run.
_WORD_GAP = "&#160;&#160;&#160;"


def _spaced(text: str) -> str:
    """``RESULTS`` -> ``R E S U L T S``.

    The report idiom this follows letter-spaces its small-caps labels, and
    QTextDocument's CSS subset has no ``letter-spacing``. Spacing the characters
    is the same effect by other means; it costs nothing because these labels are
    never read as words, only recognised as section markers.
    """
    return _WORD_GAP.join(" ".join(word) for word in str(text).upper().split())


def _section(title: str) -> str:
    return f"<p class='section'>{_spaced(title)}</p>"


def _banner_image(width: int) -> QImage | None:
    """The masthead band: the artwork flush right on a black field.

    Drawn here rather than shipped as a second file so it follows the page
    width, and so the artwork stays a single asset: the splash screen and this
    are the same image. The artwork cannot span the measure at a readable height
    — the wordmark sets its aspect — so the rest of the band is filled, and
    every edge of it is hard.
    """
    source = _ASSET_DIR / "splash.png"
    art = QImage(str(source)) if source.exists() else QImage()
    if art.isNull():
        return None
    band = art.copy(QRect(0, _BANNER_CROP_TOP, art.width(), _BANNER_CROP_HEIGHT))
    height = max(1, round(width * _BANNER_HEIGHT_SHARE))
    band = band.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)

    canvas = QImage(width, height, QImage.Format.Format_RGB32)
    canvas.fill(QColor(_BANNER_FILL))
    painter = QPainter(canvas)
    painter.drawImage(width - band.width(), 0, band)
    painter.end()
    return canvas


def _banner_html(scratch: Path, width: int, scale: float = 1.0) -> str:
    """The banner as an ``<img>``, or nothing at all if the artwork is missing.

    A missing asset costs the document its masthead and nothing else — the title
    underneath still says what the report is. Drawn at ``width`` device pixels
    and sized by ``scale`` into the units the layout reads (``_Builder``).
    """
    banner = _banner_image(width)
    if banner is None:
        return ""
    target = scratch / "banner.png"
    if not banner.save(str(target)):
        return ""
    return (
        f"<p class='banner'><img src='{target.as_uri()}' "
        f"width='{round(banner.width() * scale)}' height='{round(banner.height() * scale)}'/></p>"
    )


def _panel_html(*columns: list[tuple[str, str]]) -> str:
    """The conditions block: spaced small-caps labels over their values.

    These are what make the tables interpretable later — which metric, which
    ground truth, which sources — so they sit above the data rather than in a
    footnote under it.
    """
    share = 100 // max(1, len(columns))
    cells = []
    for column in columns:
        entries = "".join(
            f"<p class='section' style='margin:0 0 2px 0'>{_spaced(label)}</p>"
            f"<p style='margin:0 0 8px 0; font-size:10.5pt'>{value}</p>"
            for label, value in column
        )
        cells.append(f"<td width='{share}%'>{entries}</td>")
    return "<table class='panel' width='100%'><tr>" + "".join(cells) + "</tr></table>"


def _comments_html(entries: list[tuple[str, str]]) -> str:
    """Label on the left, prose on the right — the interpretation idiom."""
    rows = "".join(
        f"<tr><td class='comment-label' width='14%'>{_spaced(label)}</td>"
        f"<td class='comment-body' width='86%'>{body}</td></tr>"
        for label, body in entries
    )
    return f"<table class='comments' width='100%'>{rows}</table>"


def _signoff_html(produced: str) -> str:
    """Who produced it and from what — the report's provenance, not a signature.

    Deliberately not styled as an authorising signature, and it claims nothing
    about review: it names the software, its version and when it ran, which is
    all this document can honestly attest to.
    """
    return (
        "<hr/>"
        "<p class='sign-name'>AutoSeg Evaluator</p>"
        f"<p class='sign-role'>Generated report · version {__version__}</p>"
        f"<p class='sign-meta'>Produced automatically from the computed metrics "
        f"on {produced}.</p>"
    )


def _table_html(
    table: QTableWidget,
    width_percent: int | None = _TABLE_WIDTH_PERCENT,
    rows: Sequence[int] | None = None,
    *,
    page_break: bool = False,
) -> str:
    """One Qt table, or ``rows`` of it, as an HTML table, blanks and all.

    Blank cells are kept blank: the organ column is deliberately empty on
    continuation rows, and filling it back in for the PDF would undo the
    grouping the table exists to show. Every part of a table split across
    pages carries the header row.

    ``width`` as an attribute, not as CSS: QTextDocument's stylesheet subset
    ignores a percentage width on a table. ``None`` leaves it out, which sizes
    the table to its contents.
    """
    # A column hidden on the tab is left out of the page too.
    columns = [c for c in range(table.columnCount()) if not table.isColumnHidden(c)]
    headers = [table.horizontalHeaderItem(column).text() for column in columns]
    lines = []
    for row in range(table.rowCount()) if rows is None else rows:
        cells = []
        for column in columns:
            item = table.item(row, column)
            value = item.text() if item is not None else ""
            # Bold as markup inside the cell, not as a style on it: an inline
            # style on a cell replaces the stylesheet's border, so two bold
            # cells one above the other lost the rule between them.
            if item is not None and item.font().bold():
                value = f"<b>{value}</b>"
            cells.append(f"<td>{value}</td>")
        lines.append("<tr>" + "".join(cells) + "</tr>")
    width = "" if width_percent is None else f" width='{width_percent}%'"
    start = _PAGE_BREAK if page_break else ""
    return (
        f"<table class='data'{width} align='center'{start}><thead><tr>"
        + "".join(f"<th>{header}</th>" for header in headers)
        + "</tr></thead>"
        + "".join(lines)
        + "</table>"
    )


#: Starts a top-level table on a new page.
_PAGE_BREAK = " style='page-break-before: always'"


def _table_width(table: QTableWidget, page_width: float, device: Any) -> int:
    """The percentage of the page a table takes.

    The central ``_TABLE_WIDTH_PERCENT``, or as much more as its columns need
    to sit on one line each. Held to the central share regardless, a wide table
    wrapped its cells and doubled its rows' height.
    """
    if page_width <= 0:
        return _TABLE_WIDTH_PERCENT
    probe = QTextDocument()
    if device is not None:
        probe.documentLayout().setPaintDevice(device)
    probe.setDefaultStyleSheet(_PDF_STYLE)
    probe.setDocumentMargin(0)
    probe.setHtml(_table_html(table, None))
    needed = math.ceil(100 * probe.idealWidth() / page_width) + 1
    return max(_TABLE_WIDTH_PERCENT, min(100, needed))


def _starts_group(table: QTableWidget, row: int) -> bool:
    """Whether ``row`` begins a group: it names its organ, or its parameter."""
    item = table.item(row, 0)
    return item is not None and bool(item.text().strip())


@dataclass(frozen=True)
class _Unit:
    """What one top-level table of the PDF is, so its place can be checked.

    ``kind`` is ``panel``, ``caption`` (a section heading and note, which
    belongs with what follows), ``rows`` (a data table, or the part of one on a
    page), ``figure`` (a heading and its image), or ``comments``. A ``rows``
    unit carries the source rows it holds and, as positions among them, the
    rows that start a group.
    """

    kind: str
    name: str = ""
    rows: tuple[int, ...] = ()
    groups: tuple[int, ...] = ()
    header_rows: int = 1

    def spans(self) -> list[tuple[int, int]]:
        """Each group as (first, last) positions among ``rows``; none for an empty table."""
        if not self.rows:
            return []
        starts = list(self.groups) or [0]
        ends = [start - 1 for start in starts[1:]] + [len(self.rows) - 1]
        return list(zip(starts, ends, strict=True))


@dataclass
class _Pagination:
    """Which parts of the PDF start a new page, found by checking a layout.

    ``pushed`` holds ``(kind, name)`` of headings and figures moved to the next
    page; ``breaks`` the source rows at which a table is split, the rest of it
    starting a new page with its header row repeated.
    """

    pushed: set[tuple[str, str]] = field(default_factory=set)
    breaks: dict[str, set[int]] = field(default_factory=dict)

    def repair(self, document: QTextDocument, units: list[_Unit], page_height: float) -> bool:
        """Move the first misplaced part to the next page; False if none is.

        A group of rows, a heading with the first group it introduces, and a
        figure with its heading are each kept on one page. Anything already
        moved once and still split is taller than a page, and stays as it is.
        """
        frames = [f for f in document.rootFrame().childFrames() if isinstance(f, QTextTable)]
        if page_height <= 0 or len(frames) != len(units):
            return False
        layout = document.documentLayout()

        def page(y: float) -> int:
            return int(y // page_height)

        def top(table: QTextTable, row: int) -> float:
            block = table.cellAt(row, 0).firstCursorPosition().block()
            return layout.blockBoundingRect(block).top()

        def bottom(table: QTextTable, row: int) -> float:
            block = table.cellAt(row, 0).lastCursorPosition().block()
            return layout.blockBoundingRect(block).bottom()

        for index, (frame, unit) in enumerate(zip(frames, units, strict=True)):
            if unit.kind == "figure":
                if page(top(frame, 0)) != page(bottom(frame, 0) - 1) and self._push(unit):
                    return True
            elif unit.kind == "caption":
                # A heading belongs with what it introduces: its own text, and
                # the first group of the table that follows it.
                end = page(bottom(frame, 0) - 1)
                following = units[index + 1] if index + 1 < len(units) else None
                if following is not None and following.kind == "rows" and following.rows:
                    last = following.spans()[0][1]
                    end = page(top(frames[index + 1], following.header_rows + last))
                if page(top(frame, 0)) != end and self._push(unit):
                    return True
            elif unit.kind == "rows" and unit.header_rows:
                # Where a table runs onto the next page, it is split there into
                # a table of its own, at the start of the group the break falls
                # in. That keeps each organ's rows on one page, and each page's
                # part of the table closed off and headed, where QTextDocument
                # carried its column rules down to the foot of the page.
                # Rows never split, so comparing row tops finds every break.
                starts = [first for first, _last in unit.spans()]
                for position in range(1, len(unit.rows)):
                    row = unit.header_rows + position
                    if page(top(frame, row)) == page(top(frame, row - 1)):
                        continue
                    group = max(start for start in starts if start <= position)
                    if group == 0:
                        # The first group already starts this part: it is
                        # longer than a page, or it is kept with the heading.
                        continue
                    self.breaks.setdefault(unit.name, set()).add(unit.rows[group])
                    return True
        return False

    def _push(self, unit: _Unit) -> bool:
        key = (unit.kind, unit.name)
        if key in self.pushed:
            return False
        self.pushed.add(key)
        return True


class _Builder:
    """Writes the PDF's top-level tables, and records what each one is.

    Every top-level table goes through here, so the recorded units line up one
    for one with the tables of the laid-out document.
    """

    def __init__(self, width: float, pagination: _Pagination, units: list[_Unit], device: Any):
        self._width = width
        self._pagination = pagination
        self._units = units
        self._device = device
        #: Device pixels to the units an ``<img>`` size is read in. Laid out on
        #: a printer, QTextDocument takes those as 96 dpi pixels and scales
        #: them up to the device, which drew the banner half again too wide.
        self.image_scale = 96 / device.logicalDpiY() if device is not None else 1.0

    def _start(self, kind: str, name: str) -> str:
        return _PAGE_BREAK if (kind, name) in self._pagination.pushed else ""

    def panel(self, html: str) -> str:
        self._units.append(_Unit("panel"))
        return html

    def heading(self, title: str, note: str) -> str:
        """A section heading and note with nothing beneath them."""
        self._units.append(_Unit("caption", title))
        return (
            f"<table class='caption' width='{_TABLE_WIDTH_PERCENT}%' align='center' border='0' "
            f"cellspacing='0' cellpadding='0'{self._start('caption', title)}><tr><td>"
            f"{_section(title)}<p class='note'>{note}</p></td></tr></table>"
        )

    def table(self, title: str, table: QTableWidget, note: str = "") -> str:
        """A table, with its section heading and note above it on its measure.

        The heading and note go in a one-cell table of the table's width and
        alignment, so all three share a left edge. A table split across pages
        starts each part on a new page, at the start of a group.
        """
        width = _table_width(table, self._width, self._device)
        caption = _section(title) + (f"<p class='note'>{note}</p>" if note else "")
        parts = [
            f"<table class='caption' width='{width}%' align='center' border='0' "
            f"cellspacing='0' cellpadding='0'{self._start('caption', title)}>"
            f"<tr><td>{caption}</td></tr></table>"
        ]
        self._units.append(_Unit("caption", title))
        breaks = self._pagination.breaks.get(title, set())
        segments: list[list[int]] = [[]]
        for row in range(table.rowCount()):
            if row in breaks and segments[-1]:
                segments.append([])
            segments[-1].append(row)
        for index, rows in enumerate(segments):
            groups = tuple(i for i, row in enumerate(rows) if i == 0 or _starts_group(table, row))
            self._units.append(_Unit("rows", title, tuple(rows), groups))
            parts.append(_table_html(table, width, rows, page_break=index > 0))
        return "".join(parts)

    def figure(self, name: str, title: str, image: Path, width: int, height: int) -> str:
        """A figure and its heading in one cell, so they never part at a page break."""
        self._units.append(_Unit("figure", name))
        return (
            f"<table class='figure' width='100%' border='0' cellspacing='0' "
            f"cellpadding='0'{self._start('figure', name)}><tr><td>{_section(title)}"
            f"<p class='plate' align='center'><img src='{image.as_uri()}' "
            f"width='{round(width * self.image_scale)}' "
            f"height='{round(height * self.image_scale)}'/></p></td></tr></table>"
        )

    def comments(self, title: str, entries: list[tuple[str, str]]) -> str:
        self._units.append(_Unit("caption", title))
        heading = (
            "<table class='caption' width='100%' border='0' cellspacing='0' "
            f"cellpadding='0'{self._start('caption', title)}><tr><td>{_section(title)}"
            "</td></tr></table>"
        )
        rows = tuple(range(len(entries)))
        self._units.append(_Unit("rows", title, rows, rows, header_rows=0))
        return heading + _comments_html(entries)


def _save_for_print(canvas: Any, target: Path, width: float, height: float, dpi: float) -> None:
    """Save a figure as it prints, at ``width`` × ``height`` inches.

    The canvas makes its print adjustments for the save and undoes them after,
    so the tab is left as it was.
    """
    with canvas.printing(width, height):
        canvas.figure.savefig(target, dpi=dpi, bbox_inches="tight", facecolor="white")


def _paint_pages(
    document: QTextDocument,
    writer: QPdfWriter,
    body: QSizeF,
    band: int,
    title: str,
    produced: str,
) -> None:
    """Paint each page: its share of the document, a running header and a folio.

    The first page carries the masthead in place of the running header. The
    footer is on every page, so a loose page still says which report it is from
    and where it goes.
    """
    pages = document.pageCount()
    font = QFont(_RUNNING_FONT)
    font.setPointSizeF(_RUNNING_PT)
    title = QFontMetricsF(font, writer).elidedText(
        title, Qt.TextElideMode.ElideRight, body.width() * 0.75
    )
    left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    head = QRectF(0, 0, body.width(), band * 0.7)
    foot = QRectF(0, band + body.height() + band * 0.3, body.width(), band * 0.7)
    rule = QPen(QColor(_RULE), 1)
    painter = QPainter(writer)
    try:
        for index in range(pages):
            if index:
                writer.newPage()
            painter.save()
            painter.translate(0, band - index * body.height())
            document.drawContents(
                painter, QRectF(0, index * body.height(), body.width(), body.height())
            )
            painter.restore()

            painter.setFont(font)
            painter.setPen(rule)
            painter.drawLine(QPointF(0, foot.top()), QPointF(body.width(), foot.top()))
            if index:
                painter.drawLine(QPointF(0, head.bottom()), QPointF(body.width(), head.bottom()))
            painter.setPen(QColor(_MUTED))
            if index:
                painter.drawText(head, left, title)
                painter.drawText(head, right, produced)
            painter.drawText(foot, left, f"AutoSeg Evaluator {__version__}")
            painter.drawText(foot, right, f"Page {index + 1} of {pages}")
    finally:
        painter.end()


__all__ = ["ReportTab"]
