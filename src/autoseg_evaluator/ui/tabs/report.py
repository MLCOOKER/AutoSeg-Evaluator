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

from autoseg_evaluator.data.report import ReportModel, build_report_model, favours
from autoseg_evaluator.ui.widgets.stat_plots import DistributionCanvas, ForestCanvas

_ALPHA = 0.05


class ReportTab(QWidget):
    """Descriptive statistics, coverage and paired comparisons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._results: Any = None
        self._model: ReportModel = ReportModel()
        self._family: dict = {}
        self._build_ui()
        self._render_empty()

    # ---- Wiring -----------------------------------------------------------

    def set_results_manager(self, manager: Any) -> None:
        self._results = manager

    def refresh(self) -> None:
        """Rebuild from the current results. Cheap — no metric is recomputed."""
        rows = self._results.rows() if self._results is not None else []
        self._model = build_report_model(rows)
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
        self._export_btn.clicked.connect(self._on_export)
        header.addWidget(self._export_btn)
        outer.addLayout(header)

        controls = QGroupBox("Comparison", self)
        form = QHBoxLayout(controls)

        left = QFormLayout()
        self._metric_combo = QComboBox(self)
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
        self._challenger_combo.currentIndexChanged.connect(self._recompute)
        left.addRow("Challenger", self._challenger_combo)
        form.addLayout(left, stretch=1)

        right = QVBoxLayout()
        right.addWidget(QLabel("Organs in the correction family", self))
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
        select_all = QPushButton("All", self)
        select_all.clicked.connect(lambda: self._organ_list.selectAll())
        buttons.addWidget(select_all)
        clear = QPushButton("None", self)
        clear.clicked.connect(lambda: self._organ_list.clearSelection())
        buttons.addWidget(clear)
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
        self._coverage_table = self._make_table(
            ["Organ", "Source", "Coverage", "Produced", "Not produced", "Not run"]
        )
        tables.addWidget(self._wrap("Coverage", self._coverage_table))
        self._descriptive_table = self._make_table(
            ["Organ", "Source", "n", "Median [Q1, Q3]", "95% CI", "Mean (SD)", "Min / Max"]
        )
        tables.addWidget(self._wrap("Descriptive statistics", self._descriptive_table))
        tables.setSizes([420, 700])
        body.addWidget(tables)

        self._comparison_table = self._make_table(
            [
                "Organ",
                "n pairs",
                "n chall. / n ref.",
                "HL difference",
                "95% CI (unadjusted)",
                "r",
                "zeros",
                "p",
                "p (Holm)",
                "Sign test",
                "Reading",
            ]
        )
        body.addWidget(self._wrap("Paired comparison", self._comparison_table))

        figures = QSplitter(Qt.Orientation.Horizontal, body)
        self._distribution = DistributionCanvas()
        figures.addWidget(self._wrap("Distributions", self._distribution))
        self._forest = ForestCanvas()
        figures.addWidget(self._wrap("Difference from reference", self._forest))
        body.addWidget(figures)

        body.setSizes([220, 260, 380])
        outer.addWidget(body, stretch=1)

        self._methods = QLabel("", self)
        self._methods.setWordWrap(True)
        self._methods.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._methods.setStyleSheet("color:#555; font-size:11px;")
        outer.addWidget(self._methods)

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
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

    def _selected_organs(self) -> list[str]:
        chosen = [i.text() for i in self._organ_list.selectedItems()]
        return chosen or self._model.organs()

    # ---- Rendering --------------------------------------------------------

    def _render_empty(self) -> None:
        self._summary_label.setText("Compute metrics first — this reads the Results table.")
        self._methods.setText("")

    def _recompute(self) -> None:
        metric = self._metric_combo.currentText()
        reference = self._reference_combo.currentText()
        challenger = self._challenger_combo.currentText()
        organs = self._selected_organs()

        if not metric or not self._model.sources():
            self._render_empty()
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
        )

        self._fill_coverage(metric, organs)
        self._fill_descriptive(metric, organs)

        family: dict = {}
        if reference and challenger and reference != challenger:
            family = self._model.family(metric, reference, challenger, organs)
        self._family = family
        self._fill_comparison(metric, family, reference, challenger)
        self._fill_warning(metric, family, reference, challenger)

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
        self._forest.plot(family, metric, reference=reference, challenger=challenger, alpha=_ALPHA)
        self._write_methods(metric, family, reference, challenger)

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
                    table.setItem(row, column, QTableWidgetItem(text))

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

    def _fill_comparison(self, metric: str, family: dict, reference: str, challenger: str) -> None:
        table = self._comparison_table
        table.setRowCount(0)
        for organ, result in family.items():
            row = table.rowCount()
            table.insertRow(row)
            estimate = result.hl_estimate
            ci = (
                f"{result.ci_low:+.4f}, {result.ci_high:+.4f}"
                if result.ci_available
                else "— not estimable"
            )
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
                    self._reading(metric, result, reference, challenger),
                ]
            ):
                table.setItem(row, column, QTableWidgetItem(text))

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

    def _fill_warning(self, metric: str, family: dict, reference: str, challenger: str) -> None:
        notes: list[str] = []
        if family and not self._model.family_can_detect(family.values(), _ALPHA):
            smallest = min(r.n_pairs for r in family.values())
            notes.append(
                f"<b>This comparison cannot reach significance.</b> With {smallest} paired "
                f"observations the smallest attainable p-value is larger than the Holm "
                f"threshold for a family of {len(family)} organs, so no result can be "
                f"significant however the data fall. Reduce the family to a few "
                f"prespecified organs, or read the intervals rather than the p-values."
            )
        thin = [
            organ
            for organ, r in family.items()
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
        disagreeing = [organ for organ, r in family.items() if not r.ci_agrees_with_test]
        if disagreeing:
            notes.append(
                f"<b>Exact ties present</b> in {', '.join(disagreeing[:4])}: some paired "
                "differences are exactly zero, so the interval and the p-value need not "
                "agree for those rows."
            )
        self._warning.setText("<br><br>".join(notes))
        self._warning.setVisible(bool(notes))

    def _write_methods(self, metric: str, family: dict, reference: str, challenger: str) -> None:
        if not family:
            self._methods.setText("")
            return
        sizes = sorted({r.n_pairs for r in family.values()})
        span = f"{sizes[0]}" if len(sizes) == 1 else f"{sizes[0]}–{sizes[-1]}"
        self._methods.setText(
            f"<b>Methods.</b> {challenger} was compared with {reference} on {metric} for each "
            f"organ separately, using the Wilcoxon signed-rank test on patients where both "
            f"produced the organ (n = {span} pairs). Zero differences were handled by Pratt's "
            f"method and p-values computed exactly from the conditional sign-flip distribution. "
            f"Differences are summarised by the Hodges–Lehmann estimator with a 95% confidence "
            f"interval obtained by inverting the same test; these intervals are unadjusted. "
            f"An exact sign test is reported alongside. Holm–Bonferroni correction was applied "
            f"across the {len(family)} organs of this metric and source pair, and controls the "
            f"familywise error rate within that family only. Descriptive values are median "
            f"[Q1, Q3] with a distribution-free 95% interval for the median, which is not "
            f"estimable below six observations."
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
                    "organ,metric,challenger,reference,n_pairs,n_challenger,n_reference,"
                    "n_zero,hl_difference,ci_low,ci_high,rank_biserial,p_raw,p_holm,"
                    "sign_positive,sign_nonzero,sign_p,ci_agrees_with_test\n"
                )
                for organ, r in self._family.items():
                    handle.write(
                        f"{organ},{metric},{challenger},{reference},{r.n_pairs},{r.n_a},"
                        f"{r.n_b},{r.n_zero},"
                        f"{'' if r.hl_estimate is None else f'{r.hl_estimate:.6f}'},"
                        f"{'' if r.ci_low is None else f'{r.ci_low:.6f}'},"
                        f"{'' if r.ci_high is None else f'{r.ci_high:.6f}'},"
                        f"{'' if r.effect_r is None else f'{r.effect_r:.4f}'},"
                        f"{r.p_value:.6f},"
                        f"{'' if r.p_adjusted is None else f'{r.p_adjusted:.6f}'},"
                        f"{r.sign.n_positive},{r.sign.n_nonzero},{r.sign.p_value:.6f},"
                        f"{r.ci_agrees_with_test}\n"
                    )
        except OSError as exc:
            QMessageBox.critical(self, "Export", f"Could not write the file:\n{exc}")


__all__ = ["ReportTab"]
