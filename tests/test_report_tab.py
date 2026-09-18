"""Tests for the Report tab — the UI layer over the report model.

The statistics are checked in ``test_statistics.py`` and the choice of which
numbers reach them in ``test_report_model.py``. What is checked here is what a
reader actually sees: that absence is shown as absence rather than as a smaller
sample, that an interval which does not exist is not drawn anyway, that the
banner never contradicts the table beneath it, and that a large p-value is
reported as a failure to detect rather than as agreement.
"""

from __future__ import annotations

import csv
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from autoseg_evaluator.data.results import ResultsManager  # noqa: E402
from autoseg_evaluator.ui.tabs.report import ReportTab  # noqa: E402

PATIENTS = 10
REFERENCE = "Limbus"
CHALLENGER = "MVision"
THIRD = "Radformation"
THIN_ORGAN = "Glnd Submand (R)"
ORGANS = ("Parotid (L)", "Parotid (R)", "Brainstem", THIN_ORGAN)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


def _rows():
    """A cohort with the two kinds of absence that mean different things.

    ``Radformation`` was never run on the last two patients — an absence that
    says nothing about the model. ``MVision`` ran on every patient but declined
    the submandibular gland on six of them — an absence that says a great deal,
    and which pairing would otherwise silently discard.
    """
    rows = []
    for patient in range(PATIENTS):
        for organ_index, organ in enumerate(ORGANS):
            base = 0.84 - organ_index * 0.01 + patient * 0.003
            for source, offset in (
                (REFERENCE, 0.0),
                (CHALLENGER, -(0.030 + patient * 0.001)),
                (THIRD, -0.004),
            ):
                if source == THIRD and patient >= 8:
                    continue  # not run at all
                if source == CHALLENGER and organ == THIN_ORGAN and patient >= 4:
                    continue  # ran, declined to contour
                rows.append(
                    {
                        "patient_id": f"P{patient:02d}",
                        "drawer": organ,
                        "canonical_organ": organ,
                        "comparison_mode": "vs GT",
                        "gt_source_label": "Manual",
                        "test_source_label": source,
                        "gt_roi_name": organ,
                        "metrics": {
                            "dice": round(base + offset, 6),
                            "hausdorff95": round(4.0 - offset * 40, 6),
                        },
                    }
                )
    return rows


@pytest.fixture
def tab(qapp):
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(_rows())
    widget.set_results_manager(manager)
    widget.refresh()
    widget._metric_combo.setCurrentText("dice")
    widget._reference_combo.setCurrentText(REFERENCE)
    widget._challenger_combo.setCurrentText(CHALLENGER)
    yield widget
    widget.deleteLater()


def _column(table, header: str) -> int:
    for column in range(table.columnCount()):
        if table.horizontalHeaderItem(column).text() == header:
            return column
    raise AssertionError(f"no {header!r} column")


def _find(table, organ: str, source: str | None = None) -> dict[str, str]:
    """The first row for this organ (and source), as ``{header: text}``."""
    for row in range(table.rowCount()):
        if table.item(row, 0).text() != organ:
            continue
        if source is not None and table.item(row, 1).text() != source:
            continue
        return {
            table.horizontalHeaderItem(c).text(): table.item(row, c).text()
            for c in range(table.columnCount())
        }
    raise AssertionError(f"no row for {organ} / {source}")


def _select(tab, organs) -> None:
    tab._organ_list.clearSelection()
    for index in range(tab._organ_list.count()):
        item = tab._organ_list.item(index)
        item.setSelected(item.text() in organs)


# ---- Empty and degenerate -------------------------------------------------


def test_the_tab_opens_before_anything_is_computed(qapp):
    """Tab 7 exists from launch; it must say what it needs, not look broken."""
    widget = ReportTab()
    widget.refresh()  # no results manager attached at all
    assert widget._coverage_table.rowCount() == 0
    assert widget._comparison_table.rowCount() == 0
    assert "Compute metrics first" in widget._summary_label.text()
    assert widget._methods.text() == ""
    widget.deleteLater()


def test_results_with_no_test_sources_do_not_raise(qapp):
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows([{"patient_id": "P1", "drawer": "Brainstem", "metrics": {"dice": 0.8}}])
    widget.set_results_manager(manager)
    widget.refresh()
    assert widget._comparison_table.rowCount() == 0
    widget.deleteLater()


def test_comparing_a_source_with_itself_produces_nothing(tab):
    tab._challenger_combo.setCurrentText(REFERENCE)
    assert tab._comparison_table.rowCount() == 0
    assert tab._methods.text() == ""


# ---- What the tables say --------------------------------------------------


def test_the_ground_truth_is_not_offered_as_a_comparator(tab):
    """Every metric is already measured against it — it cannot be a side."""
    offered = [tab._reference_combo.itemText(i) for i in range(tab._reference_combo.count())]
    assert offered == [REFERENCE, CHALLENGER, THIRD]
    assert "Manual" not in offered


def test_coverage_separates_a_declined_organ_from_an_unrun_patient(tab):
    """The distinction that decides whether an absence is a finding."""
    declined = _find(tab._coverage_table, THIN_ORGAN, CHALLENGER)
    assert declined["Coverage"] == "4 / 10"
    assert declined["Not produced"] == "6"
    assert declined["Not run"] == "0"

    unrun = _find(tab._coverage_table, "Parotid (L)", THIRD)
    assert unrun["Coverage"] == "8 / 8 · 2 not run"
    assert unrun["Not produced"] == "0"
    assert unrun["Not run"] == "2"


def test_a_comparison_reports_how_much_the_pairing_discarded(tab):
    """Ten reference contours against four challenger contours is four pairs."""
    _select(tab, ORGANS)
    thin = _find(tab._comparison_table, THIN_ORGAN)
    assert thin["n pairs"] == "4"
    assert thin["n chall. / n ref."] == "4 / 10"

    full = _find(tab._comparison_table, "Parotid (L)")
    assert full["n pairs"] == "10"
    assert full["n chall. / n ref."] == "10 / 10"


def test_an_interval_that_does_not_exist_is_not_drawn_anyway(tab):
    """At four pairs no shift is rejectable, so the accepted set is unbounded.

    Printing an interval would be a fabrication, and printing the estimate
    alone would read as a precise result. Both tables say so instead — and the
    comparison table names *which* absence it is, because "unbounded" and "a
    single accepted point" are opposite situations that a bare dash merged.
    """
    _select(tab, ORGANS)
    assert (
        _find(tab._comparison_table, THIN_ORGAN)["95% CI (unadjusted)"] == "— unbounded at this n"
    )
    assert _find(tab._descriptive_table, THIN_ORGAN, CHALLENGER)["95% CI"] == "— not estimable"
    # A well-populated organ does get one.
    assert "—" not in _find(tab._comparison_table, "Parotid (L)")["95% CI (unadjusted)"]


def test_a_large_p_is_reported_as_a_failure_to_detect(tab):
    """Never as equivalence — that would need a margin nobody has supplied."""
    _select(tab, ORGANS)
    readings = {
        tab._comparison_table.item(row, 0).text(): tab._comparison_table.item(
            row, _column(tab._comparison_table, "Reading")
        ).text()
        for row in range(tab._comparison_table.rowCount())
    }
    assert readings[THIN_ORGAN] == "no detectable difference"
    for text in readings.values():
        assert "equivalent" not in text.lower()
        assert "no difference" not in text.lower()


def test_the_reading_follows_the_metric_direction_not_the_sign(tab):
    """A smaller Hausdorff is better, so the same loser stays the loser."""
    _select(tab, ["Parotid (L)"])
    assert _find(tab._comparison_table, "Parotid (L)")["Reading"] == f"favours {REFERENCE}"

    tab._metric_combo.setCurrentText("hausdorff95")
    on_hd = _find(tab._comparison_table, "Parotid (L)")
    assert on_hd["HL difference"].startswith("+")  # the challenger's distance is larger
    assert on_hd["Reading"] == f"favours {REFERENCE}"


# ---- The correction family ------------------------------------------------


def test_a_smaller_declared_family_corrects_less_harshly(tab):
    """Which is exactly why the family is chosen, not inferred from the view."""
    _select(tab, ORGANS)
    wide = float(_find(tab._comparison_table, "Parotid (L)")["p (Holm)"])

    _select(tab, ["Parotid (L)", "Parotid (R)"])
    assert tab._comparison_table.rowCount() == 2
    narrow = float(_find(tab._comparison_table, "Parotid (L)")["p (Holm)"])

    assert narrow < wide
    assert float(_find(tab._comparison_table, "Parotid (L)")["p"]) <= narrow


def test_the_banner_never_contradicts_the_table_beneath_it(tab):
    """Regression: the ceiling is judged on the family's most favourable member.

    Judging it on the thinnest member announced that nothing could reach
    significance while a ten-patient organ sat two rows below at a
    Holm-adjusted p of 0.008.
    """
    _select(tab, ORGANS)
    column = _column(tab._comparison_table, "p (Holm)")
    adjusted = [
        float(tab._comparison_table.item(row, column).text())
        for row in range(tab._comparison_table.rowCount())
        if tab._comparison_table.item(row, column).text() != "—"
    ]
    assert min(adjusted) <= 0.05  # something is significant on screen
    assert "cannot reach significance" not in tab._warning.text()


def test_partial_coverage_is_called_out_where_it_occurs(tab):
    _select(tab, ORGANS)
    assert "Partial coverage" in tab._warning.text()
    assert THIN_ORGAN in tab._warning.text()

    _select(tab, ["Parotid (L)", "Parotid (R)"])
    assert "Partial coverage" not in tab._warning.text()


def test_the_methods_paragraph_names_the_test_and_the_family(tab):
    _select(tab, ["Parotid (L)", "Parotid (R)"])
    methods = tab._methods.text()
    assert "Wilcoxon signed-rank" in methods
    assert "Pratt" in methods
    assert "Hodges–Lehmann" in methods
    assert "across the 2 organs" in methods
    assert "unadjusted" in methods  # the intervals are not corrected
    assert CHALLENGER in methods and REFERENCE in methods


# ---- Figures --------------------------------------------------------------


def test_the_forest_draws_one_row_per_comparison(tab):
    _select(tab, ["Parotid (L)", "Parotid (R)", "Brainstem"])
    axes = tab._forest.figure.axes[0]
    labels = [label.get_text() for label in axes.get_yticklabels()]
    assert len(labels) == 3
    assert all("n=10" in label for label in labels)


def test_the_figures_survive_losing_their_comparison(tab):
    tab._challenger_combo.setCurrentText(REFERENCE)
    assert tab._forest.figure.axes  # redrawn, not left stale or crashed
    assert tab._distribution.figure.axes


# ---- Export ---------------------------------------------------------------


def test_export_writes_one_row_per_family_member(tab, tmp_path, monkeypatch):
    _select(tab, ["Parotid (L)", "Parotid (R)"])
    target = tmp_path / "comparison.csv"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "CSV"))
    )
    tab._export_btn.click()

    with open(target, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["organ"] for r in rows] == ["Parotid (L)", "Parotid (R)"]
    assert all(r["challenger"] == CHALLENGER and r["reference"] == REFERENCE for r in rows)
    assert all(r["n_pairs"] == "10" for r in rows)
    assert all(float(r["p_holm"]) >= float(r["p_raw"]) for r in rows)


def test_the_exported_comparison_carries_no_patient_identifiers(tab, tmp_path, monkeypatch):
    """The report is aggregate by construction; the export must stay that way.

    These structure sets come from real, albeit anonymised, patients — a
    per-patient column here would carry that back out of the application.
    """
    _select(tab, ORGANS)
    target = tmp_path / "comparison.csv"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "CSV"))
    )
    tab._export_btn.click()

    text = target.read_text(encoding="utf-8")
    for patient in range(PATIENTS):
        assert f"P{patient:02d}" not in text
    header = text.splitlines()[0].split(",")
    assert not any("patient" in column for column in header)


def test_export_with_nothing_to_export_says_so(tab, monkeypatch):
    tab._challenger_combo.setCurrentText(REFERENCE)  # no comparison possible
    told: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda _p, _t, text, *a, **k: told.append(text))
    )
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: pytest.fail("should not have asked for a path")),
    )
    tab._export_btn.click()
    assert told and "Nothing to export" in told[0]


def test_a_cancelled_save_dialog_writes_nothing(tab, tmp_path, monkeypatch):
    _select(tab, ["Parotid (L)"])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    tab._export_btn.click()
    assert list(tmp_path.iterdir()) == []


# ---- Stale state ----------------------------------------------------------


def test_clearing_the_results_empties_the_report(tab, tmp_path, monkeypatch):
    """Discarding results must not leave the previous cohort on screen.

    The Results tab's ``cleared`` signal was emitted but connected to nothing,
    so the whole report — coverage, descriptives, comparisons, figures and the
    methods paragraph — stayed live after the rows behind it were discarded.
    """
    assert tab._comparison_table.rowCount() > 0
    tab._results.clear()
    tab.refresh()

    assert tab._coverage_table.rowCount() == 0
    assert tab._descriptive_table.rowCount() == 0
    assert tab._comparison_table.rowCount() == 0
    assert tab._methods.text() == ""
    assert tab._warning.text() == ""
    assert "Compute metrics first" in tab._summary_label.text()


def test_a_cleared_report_cannot_export_the_previous_cohort(tab, monkeypatch):
    """The family is what Export writes, so it has to go with the tables."""
    _select(tab, ["Parotid (L)"])
    assert tab._family

    tab._results.clear()
    tab.refresh()

    told: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda _p, _t, text, *a, **k: told.append(text))
    )
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: pytest.fail("offered to export a cleared cohort")),
    )
    tab._export_btn.click()
    assert told and "Nothing to export" in told[0]


def test_the_results_tab_clear_signal_reaches_the_report(qapp, monkeypatch):
    """The wiring itself, not just the tab's own behaviour."""
    from autoseg_evaluator.ui.main_window import MainWindow

    window = MainWindow({})
    window._results.add_rows(_rows())
    window._report_tab.refresh()
    assert window._report_tab._model.organs()

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    window._results_tab.set_results_manager(window._results)
    window._results_tab._on_clear_clicked()

    assert window._report_tab._model.organs() == []
    assert window._report_tab._comparison_table.rowCount() == 0
    window.close()


# ---- Tooltips -------------------------------------------------------------


def test_every_column_carries_help(tab):
    """The table is read by clinicians, not statisticians. No bare columns."""
    for table in (tab._coverage_table, tab._descriptive_table, tab._comparison_table):
        for column in range(table.columnCount()):
            header = table.horizontalHeaderItem(column)
            tip = header.toolTip()
            assert tip, f"{header.text()!r} has no tooltip"
            assert len(tip) > 60, f"{header.text()!r} tooltip is too thin to help"


def test_every_control_carries_help(tab):
    for widget in (
        tab._metric_combo,
        tab._reference_combo,
        tab._challenger_combo,
        tab._organ_list,
        tab._export_btn,
    ):
        assert widget.toolTip(), f"{widget} has no tooltip"


def test_the_help_corrects_the_three_standard_misreadings(tab):
    """Each tooltip has a job: these are the readings that would mislead."""
    tips = {
        tab._comparison_table.horizontalHeaderItem(
            c
        ).text(): tab._comparison_table.horizontalHeaderItem(c).toolTip()
        for c in range(tab._comparison_table.columnCount())
    }
    # A p-value is not the size of a difference.
    assert "not" in tips["p"].lower() and "probability that the difference is real" in tips["p"]
    # "Not significant" is not evidence of agreement.
    assert "is not 'no difference.'" in tips["Reading"]
    # An effect size of +/-1.00 at small n is arithmetic, not strength.
    assert "arithmetic" in tips["r"]
    # And the column to judge against 0.05 is named explicitly.
    assert "0.05" in tips["p (Holm)"]


def test_a_coverage_cell_explains_its_own_shorthand(tab):
    """`8 / 8 · 2 not run` is unreadable without being told what it means."""
    for row in range(tab._coverage_table.rowCount()):
        if (
            tab._coverage_table.item(row, 0).text() == "Parotid (L)"
            and tab._coverage_table.item(row, 1).text() == THIRD
        ):
            tip = tab._coverage_table.item(row, 2).toolTip()
            assert "produced <b>Parotid (L)</b> for 8" in tip
            assert "presumably not run" in tip
            return
    pytest.fail("no Radformation / Parotid (L) coverage row")


def test_a_thin_row_states_the_p_value_it_cannot_beat(tab):
    """The family banner fires only when nothing can reach significance.

    An individual organ can sit far below that ceiling while others carry the
    family, so the limit is stated per row too.
    """
    _select(tab, ORGANS)
    for row in range(tab._comparison_table.rowCount()):
        if tab._comparison_table.item(row, 0).text() == THIN_ORGAN:
            tip = tab._comparison_table.item(row, 0).toolTip()
            assert "4</b> paired patient(s)" in tip
            assert "0.1250" in tip  # smallest attainable p at n = 4
            assert "unlikely to be" in tip  # the coverage caveat
            assert "No finite 95% interval" in tip
            return
    pytest.fail(f"no {THIN_ORGAN} comparison row")


# ---- Family integrity (external review) -----------------------------------


def _unmatched_rows():
    """An organ both sources produce, but never for the same patient."""
    rows = _rows()
    for patient in range(5):
        rows.append(_row_for(f"P{patient:02d}", "Cochlea (L)", REFERENCE, 0.62 + patient * 0.01))
    for patient in range(5, 10):
        rows.append(_row_for(f"P{patient:02d}", "Cochlea (L)", CHALLENGER, 0.58 + patient * 0.01))
    return rows


def _row_for(patient, organ, source, dice):
    return {
        "patient_id": patient,
        "drawer": organ,
        "canonical_organ": organ,
        "comparison_mode": "vs GT",
        "gt_source_label": "Manual",
        "test_source_label": source,
        "gt_roi_name": organ,
        "metrics": {"dice": dice},
    }


@pytest.fixture
def unmatched_tab(qapp):
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(_unmatched_rows())
    widget.set_results_manager(manager)
    widget.refresh()
    widget._metric_combo.setCurrentText("dice")
    widget._reference_combo.setCurrentText(REFERENCE)
    widget._challenger_combo.setCurrentText(CHALLENGER)
    yield widget
    widget.deleteLater()


def test_an_unestimable_organ_still_appears_in_the_table(unmatched_tab):
    """Raised in external review: silent family reduction.

    Both sources produced Cochlea (L), but never for the same patient, so no
    pairing exists. Dropping the row would make the family look smaller than
    the correction actually applied.
    """
    _select(unmatched_tab, ["Parotid (L)", "Parotid (R)", "Cochlea (L)"])
    row = _find(unmatched_tab._comparison_table, "Cochlea (L)")
    assert row["n pairs"] == "0"
    assert row["Reading"] == "not estimable: no matched patients"
    assert unmatched_tab._comparison_table.rowCount() == 3


def test_the_holm_divisor_keeps_the_unestimable_member(unmatched_tab):
    """The reduction is not cosmetic: it changes the adjusted p-values."""
    _select(unmatched_tab, ["Parotid (L)", "Parotid (R)"])
    without = float(_find(unmatched_tab._comparison_table, "Parotid (L)")["p (Holm)"])

    _select(unmatched_tab, ["Parotid (L)", "Parotid (R)", "Cochlea (L)"])
    with_unestimable = float(_find(unmatched_tab._comparison_table, "Parotid (L)")["p (Holm)"])

    assert with_unestimable > without  # a family of three, not two
    assert "Not estimable" in unmatched_tab._warning.text()
    assert "Holm divisor is 3, not 2" in unmatched_tab._warning.text()


def test_the_methods_paragraph_admits_the_unestimable_member(unmatched_tab):
    _select(unmatched_tab, ["Parotid (L)", "Parotid (R)", "Cochlea (L)"])
    methods = unmatched_tab._methods.text()
    assert "across the 3 organs" in methods
    assert "1 could not be estimated and were retained in the divisor" in methods


def test_an_unestimable_organ_exports_as_a_row_not_a_gap(unmatched_tab, tmp_path, monkeypatch):
    _select(unmatched_tab, ["Parotid (L)", "Cochlea (L)"])
    target = tmp_path / "comparison.csv"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "CSV"))
    )
    unmatched_tab._export_btn.click()

    with open(target, encoding="utf-8", newline="") as handle:
        rows = {r["organ"]: r for r in csv.DictReader(handle)}
    assert set(rows) == {"Parotid (L)", "Cochlea (L)"}
    assert rows["Cochlea (L)"]["n_pairs"] == "0"
    assert rows["Cochlea (L)"]["ci_status"] == "not estimable"
    assert rows["Cochlea (L)"]["p_holm"] == ""


def test_a_conflicting_repeat_is_reported_not_silently_dropped(qapp):
    """Raised in external review: the observation key is unsafe.

    Two courses under one patient identifier collapse to one observation. That
    is a different event from a duplicated export and has to be visible.
    """
    widget = ReportTab()
    manager = ResultsManager()
    rows = _rows()
    rows.append(_row_for("P00", "Parotid (L)", REFERENCE, 0.5))  # a second course
    manager.add_rows(rows)
    widget.set_results_manager(manager)
    widget.refresh()
    widget._metric_combo.setCurrentText("dice")
    widget._reference_combo.setCurrentText(REFERENCE)
    widget._challenger_combo.setCurrentText(CHALLENGER)

    assert widget._model.conflicting_observations == 1
    assert "conflicting observation(s) discarded" in widget._summary_label.text()
    assert "keyed on patient identifier" in widget._warning.text()
    widget.deleteLater()
