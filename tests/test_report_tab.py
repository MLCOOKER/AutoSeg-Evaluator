"""Tests for the Report tab — the UI layer over the report model.

The statistics are checked in ``test_statistics.py`` and the choice of which
numbers reach them in ``test_report_model.py``. What is checked here is what a
reader actually sees: that absence is shown as absence rather than as a smaller
sample, that an interval which does not exist is not drawn anyway, that the
banner never contradicts the table beneath it, and that a large p-value is
reported as a failure to detect rather than as agreement.
"""

from __future__ import annotations

import os
import re
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


def _row_organ(table, row: int) -> str:
    """The organ a row belongs to, blank display column notwithstanding.

    The descriptive table names each organ once per group, so a reader carries
    it down the block; the organ stays on every cell as item data so code can
    do the same without parsing the gaps.
    """
    from autoseg_evaluator.ui.tabs.report import ORGAN_ROLE

    item = table.item(row, 0)
    return str(item.data(ORGAN_ROLE) or item.text())


def _find(table, organ: str, source: str | None = None) -> dict[str, str]:
    """The first row for this organ (and source), as ``{header: text}``."""
    for row in range(table.rowCount()):
        if _row_organ(table, row) != organ:
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

    unrun = _find(tab._coverage_table, "Parotid (L)", THIRD)
    assert unrun["Coverage"] == "8 / 8 · 2 not run"

    # The breakdown columns are gone: produced-versus-not-run cannot be
    # determined from metric rows alone, so the cell text says what is knowable
    # and no column claims more.
    headers = [
        tab._coverage_table.horizontalHeaderItem(c).text()
        for c in range(tab._coverage_table.columnCount())
    ]
    assert headers == [
        "Organ",
        "Source",
        "Coverage",
        "Truncated",
        "Test-only PTV overlap",
        "Contour Discontinuity",
    ]


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


def test_a_result_does_not_move_when_other_organs_are_shown(tab):
    """The reported defect: one organ selected was significant, four were not.

    The p-value depended on a list widget, which is not a property of the data.
    """
    _select(tab, ORGANS)
    wide = _find(tab._comparison_table, "Parotid (L)")["p"]

    _select(tab, ["Parotid (L)"])
    assert tab._comparison_table.rowCount() == 1
    alone = _find(tab._comparison_table, "Parotid (L)")["p"]

    assert alone == wide


def test_there_is_no_adjusted_p_column(tab):
    _select(tab, ORGANS)
    headers = [
        tab._comparison_table.horizontalHeaderItem(c).text()
        for c in range(tab._comparison_table.columnCount())
    ]
    assert "p" in headers
    assert not any("Holm" in header for header in headers)


def test_the_banner_never_contradicts_the_table_beneath_it(tab):
    """Regression: the ceiling is judged on the family's most favourable member.

    Judging it on the thinnest member announced that nothing could reach
    significance while a ten-patient organ sat two rows below at a
    Holm-adjusted p of 0.008.
    """
    _select(tab, ORGANS)
    column = _column(tab._comparison_table, "p")
    values = [
        float(tab._comparison_table.item(row, column).text())
        for row in range(tab._comparison_table.rowCount())
        if tab._comparison_table.item(row, column).text() != "—"
    ]
    assert min(values) <= 0.05  # something is significant on screen
    assert "Nothing here can reach significance" not in tab._warning.text()


def test_partial_coverage_is_called_out_where_it_occurs(tab):
    _select(tab, ORGANS)
    assert "Partial coverage" in tab._warning.text()
    assert THIN_ORGAN in tab._warning.text()

    _select(tab, ["Parotid (L)", "Parotid (R)"])
    assert "Partial coverage" not in tab._warning.text()


def test_the_methods_paragraph_names_the_test_and_the_reporting_rule(tab):
    _select(tab, ["Parotid (L)", "Parotid (R)"])
    methods = tab._methods.text()
    assert "Wilcoxon signed-rank" in methods
    assert "Pratt" in methods
    assert "Hodges–Lehmann" in methods
    assert "2 organs is treated as a separate question" in methods
    assert "unadjusted" in methods
    assert "selected subset" not in methods  # removed from the report, October 2026
    assert "applied across them. Descriptive values" in methods
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


def _export_pdf(tab, tmp_path, monkeypatch, name="report.pdf"):
    target = tmp_path / name
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "PDF"))
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    tab._export_btn.click()
    return target


def test_export_writes_a_pdf_of_the_whole_page(tab, tmp_path, monkeypatch):
    """A table on its own loses the selections it was computed under."""
    _select(tab, ["Parotid (L)", "Parotid (R)"])
    target = _export_pdf(tab, tmp_path, monkeypatch)

    assert target.exists()
    assert target.read_bytes().startswith(b"%PDF")
    assert target.stat().st_size > 20_000  # the figures are in there


def test_the_export_carries_no_patient_identifiers(tab, tmp_path):
    """The report is aggregate by construction; the export must stay that way.

    These structure sets come from real, albeit anonymised, patients, and a
    document leaving the application is the last place to start naming them.
    """
    from pathlib import Path

    _select(tab, ORGANS)
    html = tab._pdf_html(Path(tmp_path))
    for patient in range(PATIENTS):
        assert f"P{patient:02d}" not in html
    assert "patient_id" not in html


def test_the_export_records_what_it_was_produced_under(tab, tmp_path):
    """Which metric, which ground truth, which sources — a year later this is
    most of what makes the tables readable."""
    from pathlib import Path

    from autoseg_evaluator.ui.tabs.report import _spaced

    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    html = tab._pdf_html(Path(tmp_path))

    # Section and field labels are letter-spaced for the report idiom, so the
    # document is checked for what it renders rather than for the source words.
    for label in ("Ground truth", "Metric", "Compared against", "Cohort"):
        assert _spaced(label) in html, label
    for section in ("Coverage", "Descriptive statistics", "Paired comparison"):
        assert _spaced(section) in html, section
    assert REFERENCE in html and CHALLENGER in html
    assert "Dice" in html
    assert "Methods" in html or _spaced("Notes & interpretation") in html


def test_the_export_carries_the_masthead_and_provenance(tab, tmp_path):
    """A figure or table that escapes the document should still be traceable."""
    from pathlib import Path

    from autoseg_evaluator import __version__

    _select(tab, ORGANS)
    html = tab._pdf_html(Path(tmp_path))
    assert "banner.png" in html
    assert (Path(tmp_path) / "banner.png").exists()  # drawn, not shipped
    assert __version__ in html
    assert "AutoSeg Evaluator" in html  # the sign-off names what produced it


def test_the_banner_runs_the_full_measure_on_a_hard_edge(qapp):
    """A band the width of the page, filled where the artwork cannot reach.

    The artwork's aspect is set by its wordmark, so at a readable height it
    covers only part of the measure; the rest is filled rather than faded, and
    every edge is hard. Checked at the pixels because the seam between the two
    is the point: it is invisible only because the artwork is set flush right,
    where its own edge is black.
    """
    from autoseg_evaluator.ui.tabs.report import _banner_image

    banner = _banner_image(1200)
    assert banner is not None
    assert banner.width() == 1200
    # Thin enough to introduce the page rather than take it over.
    assert banner.height() < 200

    def grey(x, y):
        colour = banner.pixelColor(x, y)
        return (colour.red() + colour.green() + colour.blue()) / 3

    wide, tall = banner.width() - 1, banner.height() - 1
    # The filled side is flat black to the corner — a hard edge, not a wash to
    # white. The right corners are artwork and carry its lighting.
    for corner in ((0, 0), (0, tall)):
        assert grey(*corner) < 20, corner

    # The fill meets the artwork without a seam: nothing along the join is
    # brighter than the black either side of it.
    join = next(x for x in range(wide) if grey(x, tall // 2) > 20)
    assert all(grey(x, y) < 20 for x in range(join - 3, join) for y in (0, tall // 2, tall))

    # ... and the artwork itself is there, on the right. Sampled rather than
    # probed once: the wordmark is white lettering on black, so a single point
    # can land on a glyph or between two and say nothing either way.
    right = [grey(x, y) for x in range(wide // 2, wide, 20) for y in (tall // 3, tall // 2)]
    assert max(right) > 200


def test_letter_spaced_labels_keep_their_words_apart(qapp):
    """``COMPARED AGAINST`` must not close up into one run of letters.

    HTML collapses runs of whitespace, so the word gap has to be made of
    non-breaking spaces; with ordinary ones the rendered label is unreadable
    while the source still looks right.
    """
    from autoseg_evaluator.ui.tabs.report import _spaced

    spaced = _spaced("Compared against")
    assert "&#160;" in spaced
    assert spaced.startswith("C O M P A R E D")


def test_the_exported_tables_take_the_middle_of_the_page(tab, tmp_path):
    """Sized to their contents the tables left most of the page white; across
    the full width their columns drifted apart. Each takes the centre, with
    its label and note on the same measure. The conditions panel is layout
    rather than data, and still spans the page.
    """
    from pathlib import Path

    from autoseg_evaluator.ui.tabs.report import _TABLE_WIDTH_PERCENT

    _select(tab, ORGANS)
    html = tab._pdf_html(Path(tmp_path), width=1600, height=1100)
    assert 50 <= _TABLE_WIDTH_PERCENT < 100
    # The central share, or more where a table's columns need it.
    tables = re.findall(r"<table class='data' width='(\d+)%' align='center'>", html)
    captions = re.findall(r"<table class='caption' width='(\d+)%' align='center'", html)
    assert len(tables) == 3
    assert all(_TABLE_WIDTH_PERCENT <= int(width) <= 100 for width in tables)
    assert captions == tables  # each heading on its table's measure
    assert "<table class='panel' width='100%'>" in html
    # No figure may outgrow its share of the page, or the leftover prints as a
    # gap above the next page break.
    for width, height in re.findall(r"<img [^>]*width='(\d+)' height='(\d+)'", html):
        assert int(width) <= 1600
        assert int(height) <= 1100


def test_export_with_nothing_to_export_says_so(qapp, monkeypatch):
    widget = ReportTab()
    widget.refresh()  # no results at all
    tab = widget
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


def test_saving_the_figures_writes_one_file_each(tab, tmp_path, monkeypatch):
    _select(tab, ORGANS)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path))
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    tab._figures_btn.click()

    written = sorted(p.name for p in tmp_path.iterdir())
    assert len(written) == 3
    assert any(name.endswith("_distributions.png") for name in written)
    assert any(name.endswith("_paired.png") for name in written)
    assert any(name.endswith("_forest.png") for name in written)
    # The stem says what they are of, so a folder of them stays identifiable.
    assert all(name.startswith("dice_Limbus_MVision") for name in written)


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
    # And the p column says plainly that it is per organ and uncorrected.
    assert "unadjusted" in tips["p"].lower()


def test_no_tooltip_mentions_a_correction_that_is_not_applied(tab):
    """Holm was removed; leaving it in the help would describe other software."""
    widgets = [
        tab._metric_combo,
        tab._reference_combo,
        tab._challenger_combo,
        tab._organ_list,
        tab._axis_combo,
        tab._ground_truth_combo,
        tab._export_btn,
        tab._figures_btn,
        tab._relative_check,
        tab._sort_check,
    ]
    texts = [w.toolTip() for w in widgets]
    for table in (tab._coverage_table, tab._descriptive_table, tab._comparison_table):
        texts += [table.horizontalHeaderItem(c).toolTip() for c in range(table.columnCount())]
    assert not any("holm" in text.lower() for text in texts)


def test_a_coverage_cell_explains_its_own_shorthand(tab):
    """`8 / 8 · 2 not run` is unreadable without being told what it means."""
    for row in range(tab._coverage_table.rowCount()):
        if (
            _row_organ(tab._coverage_table, row) == "Parotid (L)"
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


def test_an_unestimable_organ_changes_no_other_row(unmatched_tab):
    """It is shown so the reader sees it, and affects nothing else."""
    _select(unmatched_tab, ["Parotid (L)", "Parotid (R)"])
    without = _find(unmatched_tab._comparison_table, "Parotid (L)")["p"]

    _select(unmatched_tab, ["Parotid (L)", "Parotid (R)", "Cochlea (L)"])
    with_unestimable = _find(unmatched_tab._comparison_table, "Parotid (L)")["p"]

    assert with_unestimable == without
    assert "Not estimable" in unmatched_tab._warning.text()


def test_the_methods_paragraph_admits_the_unestimable_member(unmatched_tab):
    _select(unmatched_tab, ["Parotid (L)", "Parotid (R)", "Cochlea (L)"])
    methods = unmatched_tab._methods.text()
    assert "3 organs is treated as a separate question" in methods
    assert "1 could not be estimated and are reported as such" in methods


def test_an_unestimable_organ_exports_as_a_row_not_a_gap(unmatched_tab, tmp_path):
    """Omitting it would leave no trace the comparison was asked for."""
    from pathlib import Path

    _select(unmatched_tab, ["Parotid (L)", "Cochlea (L)"])
    html = unmatched_tab._pdf_html(Path(tmp_path))
    assert "Cochlea (L)" in html
    assert "not estimable: no matched patients" in html


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

    def canvas_width(canvas):
        return canvas.minimumWidth()

    # The distribution has a fixed floor; the forest sizes itself to its rows,
    # so its own minimum is the size that has to survive.
    for canvas, width, height in (
        (tab._distribution, MIN_CANVAS_WIDTH, MIN_CANVAS_HEIGHT),
        (tab._forest, canvas_width(tab._forest), tab._forest.minimumHeight()),
    ):
        dpi = canvas.figure.dpi
        canvas.figure.set_size_inches(width / dpi, height / dpi)
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
    caption = tab._forest.figure.get_supxlabel()
    assert "the source in each row" in caption
    assert CHALLENGER not in axes.get_title(loc="left")
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


def test_the_source_rows_are_unadjusted_too(tab):
    """Same rule on both axes: a row answers its own question."""
    _across_sources(tab)
    assert len(tab._family) == 2  # the two non-reference sources
    assert all(r.p_adjusted is None for r in tab._family.values())


def test_export_across_sources_names_the_organ_and_the_sources(tab, tmp_path):
    """The source-wise export has to say which organ it is about."""
    from pathlib import Path

    _across_sources(tab, "Parotid (L)")
    html = tab._pdf_html(Path(tmp_path))
    assert "Parotid (L)" in html
    assert CHALLENGER in html and THIRD in html
    # The fixed side is labelled as the organ, not as a challenger.
    assert ">Organ</th>" in html


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


def _shown(tab, qapp, width):
    tab.resize(width, 900)
    tab.show()
    for _ in range(5):
        qapp.processEvents()


def _room_for_every_table(tab) -> int:
    """A window wider than the widest table, whatever the fonts make that."""
    return tab._comparison_table.sizeHint().width() + 200


def test_tables_are_only_as_wide_as_their_columns(tab, qapp):
    """Stretched across the window, short columns spread a row too far apart.

    Each column is as wide as its widest entry, so nothing is cut off, and the
    table keeps to the left of its section at that width.
    """
    _select(tab, ORGANS)
    _shown(tab, qapp, _room_for_every_table(tab))

    for table in (tab._coverage_table, tab._descriptive_table, tab._comparison_table):
        header = table.horizontalHeader()
        for column in range(table.columnCount()):
            if table.isColumnHidden(column):
                continue  # the PTV overlap count, when no overlap was measured
            assert header.sectionSize(column) >= table.sizeHintForColumn(column)
            assert header.sectionSize(column) >= header.sectionSizeHint(column)
        assert table.width() == table.sizeHint().width()
        assert table.x() < 30
        assert not table.horizontalScrollBar().isVisible()
    coverage = tab._coverage_table
    assert coverage.width() < coverage.parentWidget().width() / 2


def test_a_table_wider_than_the_window_scrolls_without_hiding_a_row(tab, qapp):
    """Narrower than its columns, a table scrolls sideways within itself.

    The tab keeps the window's width, and the table grows by the scroll bar's
    height so the bar does not cover its bottom row.
    """
    _select(tab, ORGANS)
    wide = _room_for_every_table(tab)
    _shown(tab, qapp, wide)
    table = tab._comparison_table
    fitted = table.height()

    _shown(tab, qapp, 700)
    bar = table.horizontalScrollBar()
    assert table.width() < table.sizeHint().width()
    assert bar.isVisible()
    assert table.height() == fitted + bar.sizeHint().height()

    _shown(tab, qapp, wide)
    assert not bar.isVisible()
    assert table.height() == fitted
    tab.hide()


def test_bold_cells_keep_their_rules_in_the_export(qapp):
    """A style on a cell replaced its border, so two bold cells, one above the
    other, printed with no rule between them.

    Rendered, not inspected as markup: a column of bold cells must cross as
    many rules as a column of plain ones.
    """
    from PySide6.QtCore import QSizeF
    from PySide6.QtGui import QColor, QImage, QPainter, QTextDocument
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

    from autoseg_evaluator.ui.tabs.report import _PDF_STYLE, _RULE, _table_html

    def rules_crossed(bold: bool) -> int:
        table = QTableWidget(4, 1)
        table.setHorizontalHeaderLabels(["Median"])
        for row in range(4):
            item = QTableWidgetItem(f"0.8{row}")
            font = item.font()
            font.setBold(bold)
            item.setFont(font)
            table.setItem(row, 0, item)
        document = QTextDocument()
        document.setDefaultStyleSheet(_PDF_STYLE)
        document.setDocumentMargin(0)
        document.setHtml(_table_html(table))
        document.setPageSize(QSizeF(400, 1000))
        size = document.size().toSize()
        image = QImage(size, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        document.drawContents(painter)
        painter.end()
        rule = QColor(_RULE)

        def on_rule(x: int, y: int) -> bool:
            pixel = image.pixelColor(x, y)
            return (
                abs(pixel.red() - rule.red())
                + abs(pixel.green() - rule.green())
                + abs(pixel.blue() - rule.blue())
                < 60
            )

        # The table is centred: find its left edge from the header's fill,
        # then go down the cell padding just inside it, left of the text,
        # where only rules cross.
        white = QColor("white")
        left = next(x for x in range(image.width()) if image.pixelColor(x, 3) != white)
        crossings, inside = 0, False
        for y in range(image.height()):
            here = on_rule(left + 3, y)
            crossings += here and not inside
            inside = here
        table.deleteLater()
        return crossings

    plain = rules_crossed(bold=False)
    assert plain >= 4  # one between each pair of rows, and the bottom edge
    assert rules_crossed(bold=True) == plain


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
        ("— 3D mask —", False),
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


# ---- Declutter, separators and the relative forest ------------------------


def test_the_organ_is_named_once_per_group(tab):
    """Six rows of "Lens (L)" carry the label five times too many."""
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    table = tab._descriptive_table
    labelled = [table.item(r, 0).text() for r in range(table.rowCount()) if table.item(r, 0).text()]
    assert sorted(labelled) == sorted(ORGANS)  # once each, not once per source


def test_a_blank_organ_cell_still_knows_its_organ(tab):
    """Decluttering the display must not declutter the data."""
    from autoseg_evaluator.ui.tabs.report import ORGAN_ROLE

    _select(tab, ORGANS)
    table = tab._descriptive_table
    for row in range(table.rowCount()):
        assert table.item(row, 0).data(ORGAN_ROLE) in ORGANS


def test_each_group_is_marked_for_the_separator_rule(tab):
    """The label is gone, so a rule has to carry the boundary instead."""
    from autoseg_evaluator.ui.tabs.report import GROUP_START_ROLE

    _select(tab, ORGANS)
    table = tab._descriptive_table
    starts = sum(1 for r in range(table.rowCount()) if table.item(r, 0).data(GROUP_START_ROLE))
    assert starts == len(ORGANS)


def test_the_descriptive_note_states_the_direction(tab):
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    assert "higher is better" in tab._descriptive_note.text()
    tab._metric_combo.setCurrentText("hausdorff95")
    assert "lower is better" in tab._descriptive_note.text()


def test_an_undirected_metric_says_it_has_no_direction(qapp):
    """A reader comparing two sources needs to know which way is up."""
    rows = []
    for patient in range(4):
        for source in (REFERENCE, CHALLENGER):
            row = _row_for(f"P{patient}", "Parotid (L)", source, 0.8)
            row["metrics"] = {"volume_ratio": 1.1}
            rows.append(row)
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(rows)
    widget.set_results_manager(manager)
    widget.refresh()
    assert "no better or worse direction" in widget._descriptive_note.text()
    widget.deleteLater()


def test_the_distributions_plot_separates_organ_groups(tab):
    """With several sources per organ the clusters run together."""
    _select(tab, ORGANS)
    axes = tab._distribution.figure.axes[0]
    # The IQR bars are vertical too, so the separator is identified by its own
    # colour and hairline width rather than by being vertical.
    boundaries = sorted(
        float(line.get_xdata()[0])
        for line in axes.get_lines()
        if str(line.get_color()).upper() == "#C8CDD4"
    )
    assert boundaries == [0.5, 1.5, 2.5]  # four organs, three gaps


def test_the_forest_axis_switches_to_percentages(tab):
    _select(tab, ORGANS)
    tab._metric_combo.setCurrentText("hausdorff95")
    raw = tab._forest.figure.axes[0].get_xlabel()
    assert "%" not in raw

    tab._relative_check.setChecked(True)
    relative = tab._forest.figure.axes[0].get_xlabel()
    assert f"% of {REFERENCE}'s median" in relative


def test_relative_mode_makes_organs_on_different_scales_comparable(qapp):
    """The whole reason for the toggle.

    Three organs degrade by the same 25%, on Hausdorff scales spanning 27x. In
    raw units the small organ collapses onto zero; as a percentage the three
    land together.
    """
    scale = {"Bowel": 40.0, "Parotid (L)": 6.0, "Cochlea (L)": 1.5}
    rows = []
    for organ, base in scale.items():
        for patient in range(10):
            for source, mult in ((REFERENCE, 1.00), (CHALLENGER, 1.25)):
                row = _row_for(f"P{patient:02d}", organ, source, 0.8)
                row["metrics"] = {"hausdorff95": base * mult + patient * 0.05}
                rows.append(row)
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(rows)
    widget.set_results_manager(manager)
    widget.refresh()
    widget._metric_combo.setCurrentText("hausdorff95")
    widget._reference_combo.setCurrentText(REFERENCE)
    widget._challenger_combo.setCurrentText(CHALLENGER)
    _select(widget, list(scale))

    def estimates(tab):
        """The plotted point for each row, read off the marker collections."""
        found = []
        for collection in tab._forest.figure.axes[0].collections:
            offsets = collection.get_offsets()
            found.extend(float(point[0]) for point in offsets)
        return found

    raw = estimates(widget)
    widget._relative_check.setChecked(True)
    relative = estimates(widget)

    # Raw: the same 25% degradation reads as 0.375 mm and 10 mm, a 27x spread,
    # so the small organ collapses onto zero on a shared axis.
    assert max(raw) / min(raw) > 20
    # Relative: the three land together. Not exactly equal, because each is
    # divided by its own reference *median*, which carries the per-patient drift.
    assert max(relative) / min(relative) < 1.2
    assert all(20.0 < value < 30.0 for value in relative)
    widget.deleteLater()


def test_the_relative_toggle_sits_with_the_figure_it_rescales(tab):
    """It changes how one plot is drawn, not what is being compared.

    Among the family controls it read as an analysis choice, which it is not.
    """
    from PySide6.QtWidgets import QGroupBox

    box = tab._relative_check.parentWidget()
    while box is not None and not isinstance(box, QGroupBox):
        box = box.parentWidget()
    assert box is not None
    assert box.title() == "Difference from reference"
    assert tab._forest in box.findChildren(type(tab._forest))


# ---- Coverage grouping and the best-median mark ---------------------------


def test_the_coverage_table_groups_organs_too(tab):
    """Same treatment as the descriptive table: named once, ruled between."""
    from autoseg_evaluator.ui.tabs.report import GROUP_START_ROLE

    _select(tab, ORGANS)
    table = tab._coverage_table
    labelled = [table.item(r, 0).text() for r in range(table.rowCount()) if table.item(r, 0).text()]
    assert sorted(labelled) == sorted(ORGANS)
    starts = sum(1 for r in range(table.rowCount()) if table.item(r, 0).data(GROUP_START_ROLE))
    assert starts == len(ORGANS)
    # And the organ is still recoverable from every row.
    for row in range(table.rowCount()):
        assert _row_organ(table, row) in ORGANS


def test_the_best_median_in_each_organ_is_bold(tab):
    """Bold rather than colour: weight survives greyscale and colour blindness.

    In this fixture Limbus has the highest Dice in every organ.
    """
    from autoseg_evaluator.ui.tabs.report import BEST_ROLE, MEDIAN_COLUMN

    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    table = tab._descriptive_table
    marked = {
        (_row_organ(table, r), table.item(r, 1).text())
        for r in range(table.rowCount())
        if table.item(r, MEDIAN_COLUMN).data(BEST_ROLE)
    }
    assert marked == {(organ, REFERENCE) for organ in ORGANS}
    for organ, source in marked:
        for row in range(table.rowCount()):
            if _row_organ(table, row) == organ and table.item(row, 1).text() == source:
                assert table.item(row, MEDIAN_COLUMN).font().bold()


def test_the_best_mark_follows_the_metric_direction(tab):
    """On Hausdorff the winner is the smallest, not the largest."""
    from autoseg_evaluator.ui.tabs.report import BEST_ROLE, MEDIAN_COLUMN

    _select(tab, ["Parotid (L)"])
    table = tab._descriptive_table

    def winner():
        for row in range(table.rowCount()):
            if table.item(row, MEDIAN_COLUMN).data(BEST_ROLE):
                return table.item(row, 1).text()
        return ""

    tab._metric_combo.setCurrentText("dice")
    on_dice = winner()
    medians_dice = {
        table.item(r, 1).text(): float(table.item(r, MEDIAN_COLUMN).text().split(" ")[0])
        for r in range(table.rowCount())
    }
    assert on_dice == max(medians_dice, key=medians_dice.get)

    tab._metric_combo.setCurrentText("hausdorff95")
    medians_hd = {
        table.item(r, 1).text(): float(table.item(r, MEDIAN_COLUMN).text().split(" ")[0])
        for r in range(table.rowCount())
    }
    assert winner() == min(medians_hd, key=medians_hd.get)


def test_an_undirected_metric_marks_no_winner(qapp):
    """Best is undefined when the metric is best at a target, not an extreme."""
    from autoseg_evaluator.ui.tabs.report import BEST_ROLE, MEDIAN_COLUMN

    rows = []
    for patient in range(4):
        for source, value in ((REFERENCE, 1.4), (CHALLENGER, 0.8)):
            row = _row_for(f"P{patient}", "Parotid (L)", source, 0.8)
            row["metrics"] = {"volume_ratio": value}
            rows.append(row)
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(rows)
    widget.set_results_manager(manager)
    widget.refresh()

    table = widget._descriptive_table
    assert not any(table.item(r, MEDIAN_COLUMN).data(BEST_ROLE) for r in range(table.rowCount()))
    assert "no result is marked best" in widget._descriptive_note.text()
    widget.deleteLater()


def test_the_note_explains_the_bold(tab):
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    assert "best median within each organ is shown in bold" in tab._descriptive_note.text()


def test_spacing_between_slices_is_no_longer_reported(tab):
    tab.set_library(_fake_library())
    labels = [tab._image_table.item(r, 0).text() for r in range(tab._image_table.rowCount())]
    assert not any("Spacing between slices" in label for label in labels)
    assert "Slice thickness (mm)" in labels


# ---- Forest presentation ---------------------------------------------------


def test_the_forest_height_follows_the_row_count(tab):
    """A three-row comparison should not reserve a fixed slab of figure."""
    _select(tab, ["Parotid (L)"])
    one = tab._forest.minimumHeight()
    _select(tab, ORGANS)
    four = tab._forest.minimumHeight()
    assert four > one
    assert tab._forest.minimumHeight() == tab._forest.maximumHeight()


def test_the_forest_uses_the_curated_organ_names(tab):
    """The same names the Results table shows, not a second rendering of them."""
    _select(tab, ORGANS)
    labels = [t.get_text().split("   n=")[0] for t in tab._forest.figure.axes[0].get_yticklabels()]
    assert sorted(labels) == sorted(ORGANS)


def test_the_across_organs_title_names_the_pair(tab):
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    axes = tab._forest.figure.axes[0]
    assert axes.get_title(loc="left") == f"Dice: {CHALLENGER} versus {REFERENCE}"


def test_the_across_sources_title_names_the_organ(tab):
    """Without it the figure does not say which organ it describes."""
    _across_sources(tab, "Parotid (L)")
    axes = tab._forest.figure.axes[0]
    assert axes.get_title(loc="left") == "Parotid (L): Dice"


def test_the_caption_sits_below_the_axis(tab):
    """Reserved space, not floating over the figure corner."""
    _select(tab, ORGANS)
    caption = tab._forest.figure.get_supxlabel()
    assert "Filled marker" in caption
    assert "uncorrected" in caption


def test_rows_keep_a_stable_order_unless_sorting_is_asked_for(tab):
    """A reader comparing two metrics should find each row in the same place."""
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    on_dice = [t.get_text() for t in tab._forest.figure.axes[0].get_yticklabels()]
    tab._metric_combo.setCurrentText("hausdorff95")
    on_hd = [t.get_text() for t in tab._forest.figure.axes[0].get_yticklabels()]
    assert [x.split("   n=")[0] for x in on_dice] == [x.split("   n=")[0] for x in on_hd]

    tab._sort_check.setChecked(True)
    sorted_rows = [t.get_text() for t in tab._forest.figure.axes[0].get_yticklabels()]
    assert sorted_rows != on_hd


def test_a_tolerance_metric_states_its_tolerance(qapp):
    """Surface Dice at 1 mm and at 5 mm are different measurements."""
    rows = []
    for patient in range(8):
        for source, value in ((REFERENCE, 0.90), (CHALLENGER, 0.84)):
            row = _row_for(f"P{patient}", "Parotid (L)", source, 0.8)
            # The tolerance travels in the key, as the worker writes it.
            row["metrics"] = {"surface_dice@3mm": value + patient * 0.002}
            rows.append(row)
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(rows)
    widget.set_results_manager(manager)
    widget.refresh()
    widget._reference_combo.setCurrentText(REFERENCE)
    widget._challenger_combo.setCurrentText(CHALLENGER)
    _select(widget, ["Parotid (L)"])

    axes = widget._forest.figure.axes[0]
    assert axes.get_title(loc="left").startswith("Surface Dice:")
    subtitle = " ".join(t.get_text() for t in axes.texts)
    assert "tolerance = 3.00 mm" in subtitle
    widget.deleteLater()


def test_a_metric_needing_no_tolerance_does_not_claim_one(tab):
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ["Parotid (L)"])
    subtitle = " ".join(t.get_text() for t in tab._forest.figure.axes[0].texts)
    assert "tolerance" not in subtitle


# ---- Paired-difference view ------------------------------------------------


def _paired_lines(tab):
    """The per-patient connecting lines, excluding grid and median marks."""
    axes = tab._paired.figure.axes[0]
    return [
        line
        for line in axes.get_lines()
        if len(line.get_xdata()) == 2 and list(line.get_xdata()) == [0, 1]
    ]


def test_the_paired_view_draws_one_line_per_patient(tab):
    """The distribution figure cannot show which two points share a patient."""
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    tab._paired_combo.setCurrentText("Parotid (L)")

    axes = tab._paired.figure.axes[0]
    assert axes.get_title(loc="left") == "Parotid (L): Dice"
    assert [t.get_text() for t in axes.get_xticklabels()] == [REFERENCE, CHALLENGER]
    assert len(_paired_lines(tab)) == PATIENTS


def test_the_paired_view_shows_exactly_what_the_test_analysed(tab):
    """A thin organ has four pairs, not ten, and the figure says four."""
    _select(tab, ORGANS)
    tab._paired_combo.setCurrentText(THIN_ORGAN)
    assert len(_paired_lines(tab)) == 4
    assert "4 patients" in tab._paired.figure.get_supxlabel()


def test_line_colour_follows_the_metric_direction(tab):
    """A patient whose Dice fell and whose Hausdorff fell did not move the same way."""
    _select(tab, ORGANS)
    tab._paired_combo.setCurrentText("Parotid (L)")

    tab._metric_combo.setCurrentText("dice")
    on_dice = {line.get_color() for line in _paired_lines(tab)}
    tab._metric_combo.setCurrentText("hausdorff95")
    on_hd = {line.get_color() for line in _paired_lines(tab)}

    # The challenger is worse on both metrics in this fixture, so both read the
    # same colour despite the raw values moving in opposite directions.
    assert on_dice == on_hd
    assert len(on_dice) == 1


def test_the_caption_counts_the_directions(tab):
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    tab._paired_combo.setCurrentText("Parotid (L)")
    caption = tab._paired.figure.get_supxlabel()
    assert f"0 improved with {CHALLENGER}, {PATIENTS} worsened" in caption


def test_an_undirected_metric_leaves_the_lines_uncoloured(qapp):
    rows = []
    for patient in range(6):
        for source, value in ((REFERENCE, 1.1), (CHALLENGER, 0.9)):
            row = _row_for(f"P{patient}", "Parotid (L)", source, 0.8)
            row["metrics"] = {"volume_ratio": value + patient * 0.01}
            rows.append(row)
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(rows)
    widget.set_results_manager(manager)
    widget.refresh()
    widget._reference_combo.setCurrentText(REFERENCE)
    widget._challenger_combo.setCurrentText(CHALLENGER)
    _select(widget, ["Parotid (L)"])

    assert "no better or worse direction" in widget._paired.figure.get_supxlabel()
    widget.deleteLater()


def test_the_paired_selector_follows_the_axis(tab):
    """Across organs it lists organs; across sources it lists sources."""
    _select(tab, ORGANS)
    offered = [tab._paired_combo.itemText(i) for i in range(tab._paired_combo.count())]
    assert sorted(offered) == sorted(ORGANS)

    _across_sources(tab, "Parotid (L)")
    offered = [tab._paired_combo.itemText(i) for i in range(tab._paired_combo.count())]
    assert sorted(offered) == sorted([CHALLENGER, THIRD])
    assert tab._paired.figure.axes[0].get_title(loc="left").startswith("Parotid (L):")


def test_the_paired_view_matches_the_model(tab):
    """Drawn from the same call the test consumes, not a parallel selection."""
    _select(tab, ORGANS)
    tab._paired_combo.setCurrentText("Parotid (L)")
    pairs = tab._model.paired_values("Parotid (L)", "dice", REFERENCE, CHALLENGER)
    drawn = sorted(tuple(line.get_ydata()) for line in _paired_lines(tab))
    assert drawn == sorted((before, after) for _p, before, after in pairs)


def test_an_empty_paired_view_says_why(tab):
    tab._challenger_combo.setCurrentText(REFERENCE)
    axes = tab._paired.figure.axes[0]
    assert any("The challenger is the reference" in t.get_text() for t in axes.texts)


# ---- Which patients, and on what scale -------------------------------------


def test_the_distribution_stars_an_organ_with_uneven_coverage(tab):
    """A bare n would claim a coverage the sources do not share."""
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    axes = tab._distribution.figure.axes[0]
    labels = {t.get_text().split("\n")[0]: t.get_text() for t in axes.get_xticklabels()}

    # One source declined this organ on six patients, another was never run on two.
    assert labels[THIN_ORGAN].endswith("(n=10*)")
    assert labels["Parotid (L)"].endswith("(n=10*)")
    assert "*" in tab._distribution.figure.get_supxlabel()
    assert "did not all contour the same number" in tab._distribution.figure.get_supxlabel()


def test_an_evenly_covered_cohort_gets_no_star(qapp):
    rows = []
    for patient in range(6):
        for source in (REFERENCE, CHALLENGER):
            rows.append(_row_for(f"P{patient}", "Parotid (L)", source, 0.8))
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(rows)
    widget.set_results_manager(manager)
    widget.refresh()
    labels = [t.get_text() for t in widget._distribution.figure.axes[0].get_xticklabels()]
    assert labels == ["Parotid (L)\n(n=6)"]
    assert "*" not in widget._distribution.figure.get_supxlabel()
    widget.deleteLater()


def test_the_distribution_axis_carries_the_units(tab):
    tab._metric_combo.setCurrentText("hausdorff95")
    _select(tab, ORGANS)
    assert "(mm)" in tab._distribution.figure.axes[0].get_ylabel()
    tab._metric_combo.setCurrentText("dice")
    assert "(mm)" not in tab._distribution.figure.axes[0].get_ylabel()


def test_the_distribution_widens_with_the_organ_count(tab):
    """Twenty organs at a fixed width stack the points into a line."""
    _select(tab, ["Parotid (L)"])
    narrow = tab._distribution.minimumWidth()
    _select(tab, ORGANS)
    assert tab._distribution.minimumWidth() > narrow


def test_a_wide_figure_scrolls_inside_its_section(tab):
    """Many organs widen the distributions; the tab and its tables do not follow.

    Placed straight in the tab, the figure's width became the tab's, so every
    table stretched to it and the whole tab scrolled sideways to read a row.
    """
    _select(tab, ["Parotid (L)"])
    narrow_tab = tab.minimumSizeHint().width()
    # Thirty organs: a figure far wider than anything else the tab holds.
    tab._distribution.plot(
        {f"Organ {i}": {"Vendor A": [0.8, 0.9], "Vendor B": [0.7, 0.85]} for i in range(30)},
        "dice",
    )
    wide_figure = tab._distribution.minimumWidth()
    assert wide_figure > 2 * narrow_tab
    assert tab.minimumSizeHint().width() == narrow_tab
    assert tab._distribution_scroll.minimumSizeHint().width() < wide_figure


def test_the_difference_figure_is_never_clipped(tab):
    """Its height follows its rows, so its scroll area has to follow it too."""
    for organs in (["Parotid (L)"], ORGANS):
        _select(tab, organs)
        bar = tab._forest_scroll.horizontalScrollBar().sizeHint().height()
        assert tab._forest_scroll.sizeHint().height() == tab._forest.maximumHeight() + bar


def test_a_figure_area_does_not_follow_the_figure_it_holds(tab):
    """The loop that crashed the tab on a restored session.

    A matplotlib canvas reports its current size as its preferred one. An area
    sized from that grew the canvas by a scroll bar's height, which grew the
    area, and so on until the stack overflowed.
    """
    _select(tab, ORGANS)
    for area, canvas in (
        (tab._distribution_scroll, tab._distribution),
        (tab._paired_scroll, tab._paired),
        (tab._forest_scroll, tab._forest),
    ):
        before = area.sizeHint().height()
        width, height = canvas.figure.get_size_inches()
        canvas.figure.set_size_inches(width, height + 3.0, forward=False)
        assert area.sizeHint().height() == before


def test_the_difference_area_resizes_with_its_rows_on_screen(tab, qapp):
    """Including rows drawn while the tab was hidden, as a restored session draws them.

    A figure redrawn behind a hidden tab asks nothing of its parent's layout, so
    an area that waited for a layout event kept its old height and clipped it.
    """

    def settle() -> None:
        for _ in range(5):
            qapp.processEvents()

    def fits() -> bool:
        bar = tab._forest_scroll.horizontalScrollBar().sizeHint().height()
        return tab._forest_scroll.height() == tab._forest.maximumHeight() + bar

    tab.resize(1400, 900)
    _select(tab, ["Parotid (L)"])
    tab.show()
    settle()
    try:
        tab.hide()
        _select(tab, ORGANS)  # more rows, drawn while hidden
        tab.show()
        settle()
        assert fits()
        _select(tab, ["Parotid (L)"])  # fewer rows, drawn on screen
        settle()
        assert fits()
    finally:
        tab.hide()


def test_the_paired_medians_say_their_unit(tab):
    tab._metric_combo.setCurrentText("hausdorff95")
    _select(tab, ORGANS)
    tab._paired_combo.setCurrentText("Parotid (L)")
    labels = [text.get_text() for text in tab._paired.figure.axes[0].texts]
    medians = [label for label in labels if label.startswith("median")]
    assert medians and all(label.endswith(" mm") for label in medians)


def test_a_dose_difference_has_its_statistic_unit():
    """``dmean_gy_diff`` is in Gy; an axis without the unit reads as dimensionless."""
    from autoseg_evaluator.core.readable import metric_units, readable_metric

    assert metric_units("dmean_gy_diff") == "Gy"
    assert metric_units("d2cc_gy_diff") == "Gy"
    assert metric_units("v20gy_cc_diff") == "cc"
    assert readable_metric("dmean_gy_diff") == "Mean dose difference from ground truth"
    assert readable_metric("d2cc_gy_diff") == "D2cc difference from ground truth"
    # Dimensionless measures stay bare rather than gaining a made-up unit.
    assert metric_units("dice") == ""
    assert metric_units("poly_napl@1mm") == ""


def test_the_paired_plot_imposes_no_scale(tab):
    """It exists to show movement; a metric's full range flattens it."""
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    tab._paired_combo.setCurrentText("Parotid (L)")
    low, high = tab._paired.figure.axes[0].get_ylim()
    assert low > 0.1 and high < 0.99  # autoscaled to the data, not 0–1


def test_the_distribution_caption_explains_the_marks(tab):
    """A vertical bar could be a range, an error bar or an interval."""
    _select(tab, ORGANS)
    caption = tab._distribution.figure.get_supxlabel()
    assert "interquartile range (25th–75th percentile)" in caption
    assert "wide tick is the median" in caption
    assert "not the paired-comparison sample" in caption


def test_a_bounded_metric_shows_its_whole_range(tab):
    """Ten Dice values in 0.78–0.86 autoscale into an axis that reads as a chasm."""
    tab._metric_combo.setCurrentText("dice")
    _select(tab, ORGANS)
    low, high = tab._distribution.figure.axes[0].get_ylim()
    assert low <= 0.0
    assert high >= 1.0


def test_an_unbounded_metric_keeps_zero_in_view(tab):
    """Zero is a perfect contour; an axis starting at 3 mm hides how far off everything is."""
    tab._metric_combo.setCurrentText("hausdorff95")
    _select(tab, ORGANS)
    low, _high = tab._distribution.figure.axes[0].get_ylim()
    assert low <= 0.0


def _split_cohorts():
    """Equal counts, different patients — the case that is easy to miss."""
    rows = []
    for patient in range(10):
        pid = f"P{patient:02d}"
        rows.append(_row_for(pid, "Parotid (L)", REFERENCE, 0.85 + patient * 0.002))
        if pid != "P00":
            rows.append(_row_for(pid, "Parotid (L)", CHALLENGER, 0.82 + patient * 0.002))
        if pid != "P09":
            rows.append(_row_for(pid, "Parotid (L)", THIRD, 0.84 + patient * 0.002))
    return rows


def test_rows_with_equal_counts_on_different_patients_are_flagged(qapp):
    """Matching counts make two rows look like a comparison when they are not."""
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(_split_cohorts())
    widget.set_results_manager(manager)
    widget.refresh()
    widget._metric_combo.setCurrentText("dice")
    widget._reference_combo.setCurrentText(REFERENCE)
    widget._axis_combo.setCurrentIndex(1)
    widget._organ_list.setCurrentRow(0)

    counts = {
        widget._comparison_table.item(r, 0).text(): widget._comparison_table.item(r, 1).text()
        for r in range(widget._comparison_table.rowCount())
    }
    assert counts == {CHALLENGER: "9", THIRD: "9"}  # identical counts…
    sets = widget._model.pairing_sets(
        "dice",
        REFERENCE,
        {CHALLENGER: ("Parotid (L)", CHALLENGER), THIRD: ("Parotid (L)", THIRD)},
    )
    assert sets[CHALLENGER] != sets[THIRD]  # …different patients

    warning = widget._warning.text()
    assert "do not all use the same patients" in warning
    assert "counts match, which makes that easy to miss" in warning
    widget.deleteLater()


def test_rows_on_one_cohort_are_not_flagged(tab):
    """The note must not fire when every row really does share its patients."""
    _select(tab, ["Parotid (L)", "Parotid (R)"])
    assert "do not all use the same patients" not in tab._warning.text()


def test_the_paired_view_draws_the_row_it_names(qapp):
    """Its patients are the row's patients, not the other row's."""
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(_split_cohorts())
    widget.set_results_manager(manager)
    widget.refresh()
    widget._metric_combo.setCurrentText("dice")
    widget._reference_combo.setCurrentText(REFERENCE)
    widget._axis_combo.setCurrentIndex(1)
    widget._organ_list.setCurrentRow(0)

    for source in (CHALLENGER, THIRD):
        widget._paired_combo.setCurrentText(source)
        drawn = {
            tuple(line.get_ydata())
            for line in widget._paired.figure.axes[0].get_lines()
            if len(line.get_xdata()) == 2 and list(line.get_xdata()) == [0, 1]
        }
        expected = {
            (before, after)
            for _p, before, after in widget._model.paired_values(
                "Parotid (L)", "dice", REFERENCE, source
            )
        }
        assert drawn == expected, source
    assert "may not be the same patients as another row" in widget._paired.figure.get_supxlabel()
    widget.deleteLater()


def test_the_paired_comparison_says_which_test_it_ran(tab, tmp_path):
    """Above the table, on screen and in the PDF, for a reader who starts there."""
    from pathlib import Path

    _select(tab, ORGANS)
    note = tab._comparison_note.text()
    assert "Wilcoxon signed-rank" in note
    assert "Hodges–Lehmann" in note
    assert "sign test" in note
    assert "not adjusted" in note

    html = tab._pdf_html(Path(tmp_path), width=1600, height=1100)
    paired = html.index("P A I R E D")
    assert html.index(note) > paired
    assert html.index(note) < html.index("<table class='data'", paired)


# ---- The PDF as printed ------------------------------------------------------


def _truncation_rows():
    """Parotid (L) truncated throughout, Brainstem never, Parotid (R) only in part."""
    rows = []
    for patient in range(3):
        for organ, truncated in (
            ("Parotid (L)", True),
            ("Parotid (R)", patient == 0),
            ("Brainstem", False),
        ):
            for source in (REFERENCE, CHALLENGER):
                row = _row_for(f"P{patient:02d}", organ, source, 0.8)
                row["truncated"] = truncated
                rows.append(row)
    return rows


def test_the_model_knows_which_organs_were_truncated():
    from autoseg_evaluator.data.report import build_report_model

    model = build_report_model(_truncation_rows())
    assert model.truncation("Parotid (L)") is True
    assert model.truncation("Brainstem") is False
    # Pooled drawers set differently: neither answer would be true.
    assert model.truncation("Parotid (R)") is None


def test_the_coverage_table_says_which_organs_were_truncated(qapp):
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(_truncation_rows())
    widget.set_results_manager(manager)
    widget.refresh()
    widget._metric_combo.setCurrentText("dice")
    _select(widget, ["Parotid (L)", "Parotid (R)", "Brainstem"])

    table = widget._coverage_table
    column = _column(table, "Truncated")
    said = {}
    for row in range(table.rowCount()):
        organ = table.item(row, 0).text()
        if organ:
            said[organ] = table.item(row, column).text()
        else:
            assert table.item(row, column).text() == ""  # once per organ, like its name
    assert said == {"Parotid (L)": "yes", "Parotid (R)": "partly", "Brainstem": "no"}
    widget.deleteLater()


def test_the_pdf_counts_excluded_patients_without_naming_them(reirradiation_tab, tmp_path):
    """The screen names them so they can be found; the export promises no identifiers."""
    from pathlib import Path

    _select(reirradiation_tab, ["Parotid (L)", "Parotid (R)"])
    assert "P00" in reirradiation_tab._warning.text()

    html = reirradiation_tab._pdf_html(Path(tmp_path))
    assert "1 patient(s) excluded" in html
    assert "re-irradiation or a replan" in html
    assert "P00" not in html
    assert "Tab 1" not in html


def test_a_note_pointing_at_the_screen_is_reworded_for_paper(tab, tmp_path):
    from pathlib import Path

    _select(tab, ORGANS)
    assert "paired view below" in tab._warning.text()

    html = tab._pdf_html(Path(tmp_path))
    assert "paired view below" not in html
    assert "paired-differences figure shows the patients" in html


def test_the_printed_acquisition_note_has_nothing_to_hover(tab, tmp_path):
    from pathlib import Path

    tab.set_library(_fake_library(spacings=(1.074, 1.367)))
    _select(tab, ORGANS)
    assert "hover those rows" in tab._acquisition_note.text()

    html = tab._pdf_html(Path(tmp_path))
    assert "hover" not in html
    assert "shown as each value with its count, or as a range" in html


def _laid_out(tab, tmp_path, body_height):
    """The PDF's document as exported, on pages ``body_height`` pixels tall.

    The writer is returned too: it is the document's paint device.
    """
    from PySide6.QtCore import QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter

    writer = QPdfWriter(str(tmp_path / "layout.pdf"))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageOrientation(QPageLayout.Orientation.Landscape)
    writer.setResolution(150)
    body = QSizeF(writer.width(), body_height)
    return tab._paginated(tmp_path, body, writer, "today"), writer


def test_the_pdf_never_splits_a_group_a_heading_or_a_figure(tab, tmp_path):
    """Pages shorter than the tallest table, so at least that one breaks.

    An organ's rows sit on one page, so none starts a page unlabelled; each
    part of a table is closed off on its own page; a heading is on the page of
    the rows it introduces; and a figure is on the page of its heading.
    """
    from PySide6.QtGui import QTextTable

    def tables(document):
        return [
            f
            for f in document.rootFrame().childFrames()
            if isinstance(f, QTextTable) and f.format().headerRowCount()
        ]

    _select(tab, ORGANS)
    # The page is sized from the tables rather than fixed: rows print shorter
    # with Linux's fonts, and there a fixed 700 px page held every table whole,
    # so nothing below was tested.
    whole, _writer = _laid_out(tab, tmp_path, 100_000)
    tallest = max(whole.documentLayout().frameBoundingRect(t).height() for t in tables(whole))
    height = int(0.65 * tallest)
    document, _writer = _laid_out(tab, tmp_path, height)
    layout = document.documentLayout()

    def page(block):
        return int(layout.blockBoundingRect(block).top() // height)

    def first(table, row):
        return table.cellAt(row, 0).firstCursorPosition().block()

    frames = [f for f in document.rootFrame().childFrames() if isinstance(f, QTextTable)]
    parts = 0
    for index, frame in enumerate(frames):
        if frame.format().headerRowCount():
            parts += 1
            assert first(frame, 1).text(), "a part of a table starts mid-group"
            pages = {page(first(frame, row)) for row in range(1, frame.rows())}
            assert len(pages) == 1, "a part of a table runs over a page"
        elif frame.rows() == 1 and frame.columns() == 1:
            cell = frame.cellAt(0, 0)
            last = cell.lastCursorPosition().block()
            if "\ufffc" in last.text():  # a figure: its heading and its image
                assert page(cell.firstCursorPosition().block()) == page(last)
            elif index + 1 < len(frames) and frames[index + 1].format().headerRowCount():
                assert page(cell.firstCursorPosition().block()) == page(first(frames[index + 1], 1))
    assert parts > len(tables(whole))  # the short page did split the tables
    assert document.pageCount() > 3


def test_the_pdf_prints_type_at_its_stated_size(tab, tmp_path):
    """Laid out for the screen and printed at 150 dpi, 7.5 pt came out at 4.8 pt."""
    from PySide6.QtGui import QTextTable

    _select(tab, ORGANS)
    document, writer = _laid_out(tab, tmp_path, 1000)
    table = next(
        f
        for f in document.rootFrame().childFrames()
        if isinstance(f, QTextTable) and f.format().headerRowCount()
    )
    line = table.cellAt(1, 0).firstCursorPosition().block().layout().lineAt(0).height()
    assert line >= 9 / 72 * writer.logicalDpiY()  # a 9 pt line, at the writer's resolution


def test_every_page_is_numbered_and_every_later_page_headed(tab, tmp_path, monkeypatch):
    """Recorded as painted: without installed fonts the PDF's text cannot be read back."""
    from PySide6.QtGui import QPainter

    from autoseg_evaluator.ui.tabs import report as report_module

    painted: list[str] = []

    class Recording(QPainter):
        def drawText(self, *args):  # noqa: N802 — Qt override
            painted.append(args[-1] if isinstance(args[-1], str) else "")
            return super().drawText(*args)

    monkeypatch.setattr(report_module, "QPainter", Recording)
    _select(tab, ORGANS)
    target = _export_pdf(tab, tmp_path, monkeypatch)
    assert target.exists()

    folios = [text for text in painted if text.startswith("Page ")]
    pages = len(folios)
    assert pages > 1
    assert folios == [f"Page {index + 1} of {pages}" for index in range(pages)]
    headers = [text for text in painted if text.startswith("Auto-contouring evaluation report")]
    assert len(headers) == pages - 1  # the first page has the masthead instead
    # Painted shortened to its band if need be, so the full title is checked.
    title = tab._running_title()
    assert "Dice" in title and REFERENCE in title and CHALLENGER in title


def test_the_printed_forest_takes_the_page_width(qapp):
    """On screen it is as wide as the tab, which printed at half a page."""
    from autoseg_evaluator.ui.widgets.stat_plots import ForestCanvas

    canvas = ForestCanvas()
    canvas._resize_for(20)
    width, height = canvas.print_size(10.5, 6.0)
    assert width == 10.5
    assert height <= 6.0
    assert (height - canvas.CHROME_INCHES) / 20 >= canvas.PRINT_ROW_INCHES - 1e-9

    canvas._resize_for(3)
    assert canvas.print_size(10.5, 6.0)[1] < 6.0  # a short forest stays short
    canvas.deleteLater()


def test_printing_a_figure_leaves_the_tab_as_it_was(tab):
    _select(tab, ORGANS)
    canvas = tab._distribution
    axes = canvas.figure.axes[0]
    size = tuple(canvas.figure.get_size_inches())
    angles = [label.get_rotation() for label in axes.get_xticklabels()]

    with canvas.printing(8.0, 5.0):
        assert tuple(canvas.figure.get_size_inches()) == (8.0, 5.0)
        assert {label.get_rotation() for label in axes.get_xticklabels()} == {90.0}

    assert tuple(canvas.figure.get_size_inches()) == size
    assert [label.get_rotation() for label in axes.get_xticklabels()] == angles


@pytest.fixture
def one_source_tab(qapp):
    widget = ReportTab()
    manager = ResultsManager()
    manager.add_rows(
        [_row_for(f"P{p:02d}", organ, CHALLENGER, 0.8) for p in range(6) for organ in ORGANS]
    )
    widget.set_results_manager(manager)
    widget.refresh()
    widget._metric_combo.setCurrentText("dice")
    yield widget
    widget.deleteLater()


def _figure_text(canvas) -> str:
    return " ".join(text.get_text() for axes in canvas.figure.axes for text in axes.texts)


def test_one_test_source_says_why_there_is_no_comparison(one_source_tab):
    """The ground truth is never a comparator, so one source has nothing to pair with.

    Said where the comparison would be, rather than as an empty table and a
    paired figure claiming no patient had both sources.
    """
    tab = one_source_tab
    assert tab._descriptive_table.rowCount() == len(ORGANS)  # still described
    assert tab._comparison_table.rowCount() == 0
    assert tab._comparison_table.isHidden()
    note = tab._comparison_note.text()
    assert f"Only one test source ({CHALLENGER})" in note
    assert "Wilcoxon" not in note
    assert "Only one test source" in _figure_text(tab._forest)
    assert "Only one test source" in _figure_text(tab._paired)
    assert "No patient has both sources" not in _figure_text(tab._paired)


def test_one_test_source_exports_the_reason_and_no_empty_comparison(
    one_source_tab, tmp_path, monkeypatch
):
    """An empty table once stopped the page-break check with an error."""
    from pathlib import Path

    from autoseg_evaluator.ui.tabs.report import _spaced

    tab = one_source_tab
    html = tab._pdf_html(Path(tmp_path))
    assert f"Only one test source ({CHALLENGER})" in html
    assert _spaced("Paired comparison") in html
    assert "Wilcoxon" not in html
    assert ">HL difference</th>" not in html  # no empty comparison table
    assert "paired.png" not in html and "forest.png" not in html
    assert "distributions.png" in html

    target = _export_pdf(tab, tmp_path, monkeypatch)
    assert target.read_bytes().startswith(b"%PDF")


def test_the_reference_as_its_own_challenger_says_so(tab, tmp_path):
    from pathlib import Path

    _select(tab, ORGANS)
    tab._challenger_combo.setCurrentText(REFERENCE)
    assert tab._comparison_table.isHidden()
    assert "Choose a different source" in tab._comparison_note.text()

    html = tab._pdf_html(Path(tmp_path))
    assert "the challenger and the reference were the same source" in html
    assert "Choose" not in html  # nothing to choose on paper

    tab._challenger_combo.setCurrentText(CHALLENGER)
    assert not tab._comparison_table.isHidden()
    assert "Wilcoxon signed-rank" in tab._comparison_note.text()
