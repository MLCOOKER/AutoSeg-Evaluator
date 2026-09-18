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


def test_a_collision_inside_one_context_is_reported(qapp):
    """Two differing values for one organ, source and treatment context.

    Not a second course — those carry their own linkage and are handled below.
    This is a repeated structure set inside a single context, where first-wins
    silently decides which number is analysed.
    """
    widget = ReportTab()
    manager = ResultsManager()
    rows = _rows()
    rows.append(_row_for("P00", "Parotid (L)", REFERENCE, 0.5))  # same (absent) linkage
    manager.add_rows(rows)
    widget.set_results_manager(manager)
    widget.refresh()
    widget._metric_combo.setCurrentText("dice")
    widget._reference_combo.setCurrentText(REFERENCE)
    widget._challenger_combo.setCurrentText(CHALLENGER)

    assert widget._model.conflicting_observations == 1
    assert "conflicting observation(s) discarded" in widget._summary_label.text()
    assert "within a single treatment context" in widget._warning.text()
    widget.deleteLater()


def _second_course_rows():
    """P00 returns for a second course, with its own linkage and lower scores."""
    rows = _rows()
    for organ in ("Parotid (L)", "Parotid (R)"):
        for source, dice in ((REFERENCE, 0.70), (CHALLENGER, 0.66)):
            row = _row_for("P00", organ, source, dice)
            row["linkage_id"] = "course-2"
            rows.append(row)
    return rows


@pytest.fixture
def reirradiation_tab(qapp):
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(_second_course_rows())
    widget.set_results_manager(manager)
    widget.refresh()
    widget._metric_combo.setCurrentText("dice")
    widget._reference_combo.setCurrentText(REFERENCE)
    widget._challenger_combo.setCurrentText(CHALLENGER)
    yield widget
    widget.deleteLater()


def test_a_second_course_is_a_separate_case_not_a_conflict(reirradiation_tab):
    """The linkage id distinguishes the contexts, so nothing is 'conflicting'."""
    model = reirradiation_tab._model
    assert model.conflicting_observations == 0
    assert model.duplicates_collapsed == 0
    # Both contexts are stored.
    cases = model.cases("Parotid (L)", REFERENCE, "dice")
    assert ("P00", "") in cases
    assert ("P00", "course-2") in cases
    assert cases[("P00", "course-2")] == pytest.approx(0.70)


def test_a_patient_with_two_courses_is_excluded_rather_than_guessed(reirradiation_tab):
    """Two courses are not two independent observations, and picking one is
    a study-design decision the software must not make silently."""
    model = reirradiation_tab._model
    assert model.multi_case_patients("Parotid (L)", REFERENCE, "dice") == {"P00"}
    assert "P00" not in model.values("Parotid (L)", REFERENCE, "dice")

    _select(reirradiation_tab, ["Parotid (L)", "Parotid (R)"])
    row = _find(reirradiation_tab._comparison_table, "Parotid (L)")
    assert row["n pairs"] == "9"  # ten patients, P00 withheld
    assert "1 patient(s) excluded" in reirradiation_tab._warning.text()
    assert "P00" in reirradiation_tab._warning.text()
    assert "re-irradiation or a replan" in reirradiation_tab._warning.text()


def test_an_organ_untouched_by_the_second_course_keeps_every_patient(reirradiation_tab):
    """Exclusion is per organ, not cohort-wide — it applies where it applies."""
    _select(reirradiation_tab, ["Brainstem"])
    assert _find(reirradiation_tab._comparison_table, "Brainstem")["n pairs"] == "10"


def test_rows_without_a_linkage_behave_exactly_as_before(tab):
    """Sessions computed before the stamp existed must not change meaning."""
    assert all(linkage == "" for (_o, _s, _m, _p, linkage, _ref) in tab._model.observations)
    assert tab._model.multi_case_patients("Parotid (L)", REFERENCE, "dice") == set()
    assert len(tab._model.values("Parotid (L)", REFERENCE, "dice")) == PATIENTS


# ---- Figure sizing --------------------------------------------------------


def test_the_canvases_cannot_be_squeezed_until_the_plot_disappears(tab):
    """Seen on stderr while the app was running, with nothing visible to the user.

    Both figures use matplotlib's constrained layout, which gives up when the
    decorations no longer fit — "axes sizes collapsed to zero" — and renders an
    essentially blank figure. Inside a vertical splitter there was nothing
    stopping that. The tab scrolls, so a floor costs only a scrollbar.
    """
    from autoseg_evaluator.ui.widgets.stat_plots import MIN_CANVAS_HEIGHT, MIN_CANVAS_WIDTH

    for canvas in (tab._distribution, tab._forest):
        assert canvas.minimumHeight() >= MIN_CANVAS_HEIGHT
        assert canvas.minimumWidth() >= MIN_CANVAS_WIDTH


def test_the_figures_draw_cleanly_at_their_smallest_allowed_size(tab):
    """The floor has to actually clear the collapse, not merely exist."""
    import warnings

    _select(tab, ORGANS)
    from autoseg_evaluator.ui.widgets.stat_plots import MIN_CANVAS_HEIGHT, MIN_CANVAS_WIDTH

    for canvas in (tab._distribution, tab._forest):
        dpi = canvas.figure.dpi
        canvas.figure.set_size_inches(MIN_CANVAS_WIDTH / dpi, MIN_CANVAS_HEIGHT / dpi)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            canvas.figure.canvas.draw()
        collapsed = [w for w in caught if "collapsed to zero" in str(w.message)]
        assert not collapsed, f"{type(canvas).__name__} collapsed at the minimum size"


def test_long_organ_names_are_elided_on_the_axis(tab):
    """Rotated TG-263 names are the largest consumer of vertical space."""
    from autoseg_evaluator.ui.widgets.stat_plots import MAX_TICK_LABEL, _elide

    assert _elide("Brainstem") == "Brainstem"
    long_name = "Glnd Submandibular Superior Left"
    assert len(_elide(long_name)) == MAX_TICK_LABEL
    assert _elide(long_name).endswith("…")


# ---- Comparing across sources ---------------------------------------------


def _across_sources(tab, organ="Parotid (L)"):
    tab._axis_combo.setCurrentIndex(1)
    for index in range(tab._organ_list.count()):
        if tab._organ_list.item(index).text() == organ:
            tab._organ_list.setCurrentRow(index)
            return
    pytest.fail(f"no {organ} in the organ list")


def test_switching_axis_reshapes_the_controls(tab):
    """Across sources the list names one organ, and the challenger is every source."""
    from PySide6.QtWidgets import QAbstractItemView

    _across_sources(tab)
    assert tab._organ_list.selectionMode() is QAbstractItemView.SelectionMode.SingleSelection
    assert not tab._challenger_combo.isEnabled()
    assert "Organ to compare every source on" in tab._organ_list_label.text()

    tab._axis_combo.setCurrentIndex(0)
    assert tab._organ_list.selectionMode() is QAbstractItemView.SelectionMode.MultiSelection
    assert tab._challenger_combo.isEnabled()
    assert "correction family" in tab._organ_list_label.text()


def test_the_comparison_table_is_keyed_by_source(tab):
    _across_sources(tab)
    assert tab._comparison_table.horizontalHeaderItem(0).text() == "Source"
    labels = {
        tab._comparison_table.item(r, 0).text() for r in range(tab._comparison_table.rowCount())
    }
    assert labels == {CHALLENGER, THIRD}
    assert REFERENCE not in labels  # never compared with itself


def test_the_reading_names_the_row_not_the_disabled_challenger(tab):
    """The challenger combo is inert here; using its text would misattribute."""
    _across_sources(tab)
    tab._challenger_combo.setCurrentText(THIRD)  # inert, but set to a wrong answer
    row = _find(tab._comparison_table, CHALLENGER)
    assert row["Reading"] == f"favours {REFERENCE}"


def test_the_forest_names_no_single_challenger(tab):
    """Writing one vendor's name across a figure of several would be a misstatement."""
    _across_sources(tab)
    axes = tab._forest.figure.axes[0]
    assert "the source in each row" in axes.get_title()
    assert f"(each source − {REFERENCE})" in axes.get_xlabel()
    assert CHALLENGER not in axes.get_title()
    assert sorted(t.get_text().split()[0] for t in axes.get_yticklabels()) == sorted(
        [CHALLENGER, THIRD]
    )


def test_the_forest_footer_warns_that_rows_share_an_arm(tab):
    """Consistency down the column is the shared reference, not corroboration."""
    _across_sources(tab)
    footer = " ".join(t.get_text() for t in tab._forest.figure.texts)
    assert "shares the same reference arm" in footer
    assert "do not compare the sources with each other" in footer

    tab._axis_combo.setCurrentIndex(0)
    footer = " ".join(t.get_text() for t in tab._forest.figure.texts)
    assert "shares the same reference arm" not in footer


def test_the_methods_paragraph_describes_the_source_family(tab):
    _across_sources(tab)
    methods = tab._methods.text()
    assert "Each of 2 sources was compared with Limbus" in methods
    assert "for Parotid (L)" in methods
    assert "across the 2 sources compared on this organ and metric" in methods
    assert "do not constitute comparisons between the other sources" in methods


def test_the_source_family_corrects_across_sources_not_organs(tab):
    """Two challengers is a family of two, however many organs exist.

    Checked against the stored values rather than the table text: the columns
    show four decimals, and 2 x 0.001953 rounds to 0.0039 rather than 0.0040.
    """
    _across_sources(tab)
    smallest = min(r.p_value for r in tab._family.values())
    row = next(r for r in tab._family.values() if r.p_value == smallest)
    assert row.p_adjusted == pytest.approx(2 * smallest)
    assert len(tab._family) == 2  # the two non-reference sources


def test_export_across_sources_keys_rows_by_source(tab, tmp_path, monkeypatch):
    _across_sources(tab)
    target = tmp_path / "by_source.csv"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "CSV"))
    )
    tab._export_btn.click()

    with open(target, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["source"] for r in rows] == [CHALLENGER, THIRD]
    # The challenger column names the row, not the inert combo.
    assert [r["challenger"] for r in rows] == [CHALLENGER, THIRD]
    assert all(r["reference"] == REFERENCE for r in rows)


def test_switching_back_restores_the_organ_family(tab):
    """The axis is a view, not a one-way door."""
    _across_sources(tab)
    assert tab._comparison_table.horizontalHeaderItem(0).text() == "Source"

    tab._axis_combo.setCurrentIndex(0)
    _select(tab, ORGANS)
    assert tab._comparison_table.horizontalHeaderItem(0).text() == "Organ"
    assert _find(tab._comparison_table, "Parotid (L)")["n pairs"] == "10"


# ---- Acquisition section --------------------------------------------------


class _Acq:
    def __init__(self, **kwargs):
        from autoseg_evaluator.core.acquisition import ImageAcquisition

        self.acquisition = ImageAcquisition(**kwargs)
        self.files = ["s.dcm"] * 208


class _Struct:
    def __init__(self, manufacturer, rois):
        self.manufacturer = manufacturer
        self.manufacturer_model_name = ""
        self.software_versions = "1.0"
        self.organs = list(range(rois))
        self.is_synthetic_consensus = False


def _fake_library(spacings=(1.074, 1.074)):
    ctx = type(
        "Ctx",
        (),
        {
            "image_series": [
                _Acq(
                    modality="CT",
                    manufacturer="TOSHIBA",
                    slice_thickness=2.0,
                    pixel_spacing_row=s,
                    pixel_spacing_col=s,
                )
                for s in spacings
            ],
            "rtstructs": [_Struct("Limbus AI", 48), _Struct("MVision", 52)],
        },
    )()
    patient = type("P", (), {"contexts": [ctx]})()
    return type("Lib", (), {"patients": {"P1": patient}})()


def test_the_acquisition_section_is_empty_until_a_folder_is_loaded(tab):
    assert tab._image_table.rowCount() == 0
    assert "Load a folder on Tab 1" in tab._acquisition_note.text()


def test_the_acquisition_section_reports_scanner_and_geometry(tab):
    tab.set_library(_fake_library())
    rows = {
        tab._image_table.item(r, 0).text(): tab._image_table.item(r, 1).text()
        for r in range(tab._image_table.rowCount())
    }
    assert rows["Scanner manufacturer"] == "TOSHIBA"
    assert rows["Slice thickness (mm)"] == "2"
    assert rows["In-plane pixel spacing (mm)"] == "1.074 × 1.074"
    assert rows["Reconstruction kernel"] == "— not recorded"

    assert not hasattr(tab, "_rtss_table")  # structure-set parameters removed


def test_a_parameter_that_varies_is_flagged_for_the_reader(tab):
    """Found on the real cohort: in-plane spacing differs between patients."""
    tab.set_library(_fake_library(spacings=(1.074, 1.367)))
    for r in range(tab._image_table.rowCount()):
        if tab._image_table.item(r, 0).text().startswith("In-plane"):
            assert "(1)" in tab._image_table.item(r, 1).text()
            assert "not uniform across the cohort" in tab._image_table.item(r, 1).toolTip()
            break
    else:
        pytest.fail("no in-plane spacing row")
    assert "parameter(s) vary across the cohort" in tab._acquisition_note.text()


def test_a_uniform_cohort_says_so(tab):
    """One scanner, one contouring source, one of everything."""
    ctx = type(
        "Ctx",
        (),
        {
            "image_series": [_Acq(modality="CT", manufacturer="TOSHIBA", slice_thickness=2.0)],
            "rtstructs": [_Struct("Limbus AI", 48)],
        },
    )()
    patient = type("P", (), {"contexts": [ctx]})()
    tab.set_library(type("Lib", (), {"patients": {"P1": patient}})())
    assert "Every parameter is uniform" in tab._acquisition_note.text()


def test_the_note_states_the_privacy_boundary(tab):
    """The section is written to be pasted into a paper; say what it cannot leak."""
    tab.set_library(_fake_library())
    note = tab._acquisition_note.text()
    assert "no identifiers, dates, institutions or free-text descriptions" in note


def test_the_acquisition_section_survives_a_library_with_nothing_in_it(tab):
    tab.set_library(type("Lib", (), {"patients": {}})())
    assert tab._image_table.rowCount() == 0
    assert "Load a folder on Tab 1" in tab._acquisition_note.text()


# ---- Layout and the metric selector ---------------------------------------


def test_every_section_spans_the_full_width(tab):
    """Side-by-side panes squeezed eleven-column tables into half the window.

    The tab lives in a scroll area, so vertical space is free and horizontal
    space is not; nothing is laid out beside anything else any more.
    """
    from PySide6.QtWidgets import QSplitter

    assert tab.findChildren(QSplitter) == []


def test_columns_are_evenly_spaced(tab):
    """Content-sized columns shifted every time the data did.

    Two tables stacked above one another could not be read across, because
    neither agreed with the other on where a column started.
    """
    from PySide6.QtWidgets import QHeaderView

    for table in (
        tab._coverage_table,
        tab._descriptive_table,
        tab._comparison_table,
        tab._image_table,
    ):
        header = table.horizontalHeader()
        assert header.sectionResizeMode(0) is QHeaderView.ResizeMode.Stretch


def test_a_short_table_does_not_reserve_a_tall_block(tab):
    """A four-row table should cost four rows of page, not a fixed slab."""
    _select(tab, ["Parotid (L)"])
    comparison = tab._comparison_table
    assert comparison.rowCount() == 1
    row_height = comparison.rowHeight(0)
    header = comparison.horizontalHeader().height()
    assert comparison.maximumHeight() <= header + 2 * row_height + 8


def test_a_long_table_stops_growing_and_scrolls(qapp):
    """Past the cap the section scrolls rather than pushing the page down."""
    from autoseg_evaluator.ui.tabs.report import MAX_VISIBLE_ROWS

    rows = []
    for organ_index in range(40):
        for patient in range(3):
            for source in (REFERENCE, CHALLENGER):
                rows.append(_row_for(f"P{patient}", f"Organ {organ_index:02d}", source, 0.8))
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(rows)
    widget.set_results_manager(manager)
    widget.refresh()

    table = widget._coverage_table
    assert table.rowCount() == 80  # forty organs, two sources
    row_height = table.rowHeight(0)
    header = table.horizontalHeader().height()
    ceiling = header + MAX_VISIBLE_ROWS * row_height + row_height
    assert table.maximumHeight() <= ceiling
    assert table.maximumHeight() == table.minimumHeight()
    widget.deleteLater()


def _dose_rows():
    rows = _rows()
    for row in rows:
        row["metrics"]["dmean_gy"] = 30.0
        row["metrics"]["D95_gy"] = 28.0
    return rows


@pytest.fixture
def dose_tab(qapp):
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(_dose_rows())
    widget.set_results_manager(manager)
    widget.refresh()
    yield widget
    widget.deleteLater()


def test_the_metric_selector_groups_geometric_and_dosimetric(dose_tab):
    """In one flat list a reader slides from Dice to D95 without noticing."""
    combo = dose_tab._metric_combo
    entries = [(combo.itemText(i), combo.model().item(i).isEnabled()) for i in range(combo.count())]
    assert entries == [
        ("— Geometric —", False),
        ("dice", True),
        ("hausdorff95", True),
        ("— Dosimetric —", False),
        ("D95_gy", True),
        ("dmean_gy", True),
    ]


def test_a_group_heading_cannot_be_chosen(dose_tab):
    """Disabled in the popup, and guarded in case it is current anyway."""
    dose_tab._metric_combo.setCurrentIndex(0)  # the heading
    assert dose_tab._selected_metric() == ""
    dose_tab._metric_combo.setCurrentText("dmean_gy")
    assert dose_tab._selected_metric() == "dmean_gy"


def test_the_selector_opens_on_a_real_metric(dose_tab):
    assert dose_tab._selected_metric() == "dice"


def test_switching_to_a_dose_metric_recomputes(dose_tab):
    dose_tab._reference_combo.setCurrentText(REFERENCE)
    dose_tab._challenger_combo.setCurrentText(CHALLENGER)
    dose_tab._metric_combo.setCurrentText("dmean_gy")
    _select(dose_tab, ["Parotid (L)"])
    row = _find(dose_tab._comparison_table, "Parotid (L)")
    # Both sources were given the same dose, so there is nothing to detect.
    assert row["Reading"] == "no detectable difference"
    assert "dmean_gy" in dose_tab._methods.text()


def _with_consensus():
    """The same cohort, also compared against a Tab 2 consensus.

    Each vendor agrees with the consensus differently than it does with the
    manual contours — which is the whole reason the choice of reference is a
    choice. A constant shift applied to every vendor would leave the paired
    differences identical and the switch would look inert.
    """
    shift = {REFERENCE: 0.06, CHALLENGER: 0.02, THIRD: 0.04}
    rows = _rows()
    for row in list(rows):
        consensus = dict(row)
        consensus["comparison_mode"] = "Multi-observer STAPLE"
        consensus["gt_source_label"] = "STAPLE Consensus"
        consensus["metrics"] = {
            "dice": min(1.0, row["metrics"]["dice"] + shift[row["test_source_label"]]),
            "staple_sensitivity": 0.9,
            "mean_entropy": 0.2,
        }
        rows.append(consensus)
    return rows


@pytest.fixture
def consensus_tab(qapp):
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(_with_consensus())
    widget.set_results_manager(manager)
    widget.refresh()
    yield widget
    widget.deleteLater()


def test_the_tab_opens_on_the_consensus_when_one_exists(consensus_tab):
    """User ruling: a consensus built on Tab 2 outranks the manual contours."""
    assert consensus_tab._ground_truth_combo.currentText() == "STAPLE Consensus"
    assert consensus_tab._ground_truth_combo.isEnabled()
    assert "vs STAPLE Consensus" in consensus_tab._summary_label.text()


def test_both_references_are_offered(consensus_tab):
    offered = [
        consensus_tab._ground_truth_combo.itemText(i)
        for i in range(consensus_tab._ground_truth_combo.count())
    ]
    assert offered == ["STAPLE Consensus", "Manual"]


def test_switching_reference_changes_the_numbers(consensus_tab):
    consensus_tab._metric_combo.setCurrentText("dice")
    consensus_tab._reference_combo.setCurrentText(REFERENCE)
    consensus_tab._challenger_combo.setCurrentText(CHALLENGER)
    _select(consensus_tab, ["Parotid (L)"])
    against_consensus = _find(consensus_tab._comparison_table, "Parotid (L)")["HL difference"]

    consensus_tab._ground_truth_combo.setCurrentText("Manual")
    _select(consensus_tab, ["Parotid (L)"])
    against_manual = _find(consensus_tab._comparison_table, "Parotid (L)")["HL difference"]

    assert "vs Manual" in consensus_tab._summary_label.text()
    # Same contours, different reference — the comparison is a different one.
    assert against_consensus != against_manual


def test_the_consensus_is_never_offered_as_a_comparator(consensus_tab):
    """It is the reference, so it cannot also be one of the things compared."""
    offered = [
        consensus_tab._reference_combo.itemText(i)
        for i in range(consensus_tab._reference_combo.count())
    ]
    assert "STAPLE Consensus" not in offered
    assert offered == [REFERENCE, CHALLENGER, THIRD]


def test_per_vendor_consensus_metrics_are_offered(consensus_tab):
    """Sensitivity against the consensus describes a vendor, so it is comparable."""
    offered = [
        consensus_tab._metric_combo.itemText(i) for i in range(consensus_tab._metric_combo.count())
    ]
    assert "staple_sensitivity" in offered
    assert "mean_entropy" not in offered  # describes the build, not a vendor


def test_a_single_reference_cohort_gets_no_pointless_choice(tab):
    assert tab._ground_truth_combo.currentText() == "Manual"
    assert not tab._ground_truth_combo.isEnabled()


def test_the_metric_list_follows_the_ground_truth(consensus_tab):
    """The two references need not carry the same metrics.

    A consensus comparison adds per-vendor agreement metrics and, in this
    cohort, carries no Hausdorff. A selector built once for the opening
    reference offers metrics the other does not have, and hides metrics it does.
    """
    consensus_tab._ground_truth_combo.setCurrentText("STAPLE Consensus")
    offered = [
        consensus_tab._metric_combo.itemText(i) for i in range(consensus_tab._metric_combo.count())
    ]
    assert "staple_sensitivity" in offered
    assert "hausdorff95" not in offered

    consensus_tab._ground_truth_combo.setCurrentText("Manual")
    offered = [
        consensus_tab._metric_combo.itemText(i) for i in range(consensus_tab._metric_combo.count())
    ]
    assert "hausdorff95" in offered
    assert "staple_sensitivity" not in offered


def test_a_metric_shared_by_both_references_survives_the_switch(consensus_tab):
    """Switching reference should not silently reset what is being analysed."""
    consensus_tab._metric_combo.setCurrentText("dice")
    consensus_tab._ground_truth_combo.setCurrentText("Manual")
    assert consensus_tab._selected_metric() == "dice"


# ---- Descriptive heatmap --------------------------------------------------


def _shades(tab, column=3):
    """``{(organ, source): rgb}`` for the shaded median column."""
    table = tab._descriptive_table
    found = {}
    for row in range(table.rowCount()):
        item = table.item(row, column)
        colour = item.background().color()
        found[(table.item(row, 0).text(), table.item(row, 1).text())] = (
            colour.red(),
            colour.green(),
            colour.blue(),
        )
    return found


def test_the_best_source_and_the_worst_shade_differently(tab):
    """Higher Dice is better, so the top median gets the best shade."""
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ["Parotid (L)"])
    shades = _shades(tab)
    # Limbus has the highest Dice in the fixture, MVision the lowest.
    assert shades[("Parotid (L)", REFERENCE)] != shades[("Parotid (L)", CHALLENGER)]


def test_the_scale_flips_for_a_lower_is_better_metric(tab):
    """A source cannot be best on Dice and best on Hausdorff with the same shade."""
    _select(tab, ["Parotid (L)"])
    tab._metric_combo.setCurrentText("dice")
    on_dice = _shades(tab)
    tab._metric_combo.setCurrentText("hausdorff95")
    on_hd = _shades(tab)
    # In the fixture the same source wins on both metrics, so the shade of the
    # winner must be the same colour despite the raw values moving opposite ways.
    assert on_dice[("Parotid (L)", REFERENCE)] == on_hd[("Parotid (L)", REFERENCE)]


def test_an_undirected_metric_is_not_shaded(qapp):
    """Signed volume difference is best at a target, not at an extreme."""
    rows = []
    for patient in range(4):
        for source, value in ((REFERENCE, 2.0), (CHALLENGER, -3.0)):
            row = _row_for(f"P{patient}", "Parotid (L)", source, 0.8)
            row["metrics"] = {"volume_diff_cc": value}
            rows.append(row)
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(rows)
    widget.set_results_manager(manager)
    widget.refresh()

    from PySide6.QtCore import Qt as _Qt

    table = widget._descriptive_table
    assert table.rowCount() == 2
    for row in range(table.rowCount()):
        # An unset background is a NoBrush brush; its colour is opaque black,
        # so the brush style is what says "nothing was painted here".
        assert table.item(row, 3).background().style() == _Qt.BrushStyle.NoBrush
    widget.deleteLater()


def test_shading_is_per_organ_not_across_the_table(tab):
    """A Dice excellent for one organ can be poor for another.

    A scale spanning the whole table would rank organs rather than sources,
    which is not the comparison anyone is making here.
    """
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    shades = _shades(tab)
    # The best source in each organ gets the identical "best" shade, even
    # though the organs sit at different absolute Dice levels.
    best = {shades[(organ, REFERENCE)] for organ in ORGANS if (organ, REFERENCE) in shades}
    assert len(best) == 1


def test_the_shading_is_explained_where_it_is_applied(tab):
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ["Parotid (L)"])
    tip = tab._descriptive_table.item(0, 3).toolTip()
    assert "higher is better" in tip
    assert "never spans organs" in tip


# ---- Distribution legend --------------------------------------------------


def test_the_distribution_legend_sits_outside_the_axes(tab):
    """Inside, it covers the points — there is no free corner with rotated labels."""
    _select(tab, ORGANS)
    axes = tab._distribution.figure.axes[0]
    legend = axes.get_legend()
    assert legend is not None
    # Anchored past the right-hand edge of the axes.
    assert legend.get_bbox_to_anchor().x0 > axes.get_window_extent().x1 - 1


def test_an_empty_acquisition_table_collapses_to_its_header(tab):
    """It is skipped when unloaded, so the fit has to run outside that branch."""
    assert tab._image_table.rowCount() == 0
    assert tab._image_table.maximumHeight() == tab._image_table.minimumHeight()
    assert tab._image_table.maximumHeight() < 200
