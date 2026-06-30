"""Tests for ResultsManager + ResultsTab table rendering and CSV export."""

from __future__ import annotations

import csv
import math
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from autoseg_evaluator.data.results import (
    CANONICAL_METRIC_COLUMNS,
    META_COLUMNS,
    ResultsManager,
    metric_display_label,
)
from autoseg_evaluator.ui.tabs.results import ResultsTab


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _row(
    drawer="Prostate",
    patient_id="HN1",
    gt_source="Varian",
    test_source="Limbus",
    test_organ="Prostate",
    truncated=False,
    similarity=0.95,
    error="",
    metrics=None,
) -> dict:
    return {
        "drawer": drawer,
        "patient_id": patient_id,
        "gt_rtstruct_filename": "manual.dcm",
        "gt_source_label": gt_source,
        "gt_roi_name": "Prostate",
        "gt_roi_number": 1,
        "test_rtstruct_filename": "limbus.dcm",
        "test_source_label": test_source,
        "test_organ": test_organ,
        "test_roi_number": 2,
        "truncated": truncated,
        "similarity": similarity,
        "metrics": dict(metrics or {}),
        "error": error,
    }


# ---- Qualitative (Likert) rows -------------------------------------------


def _score(mgr, *, rater, score, blinded, source="Limbus", roi_number=2, drawer="Prostate"):
    mgr.upsert_qualitative_score(
        patient_id="HN1",
        drawer=drawer,
        source_label=source,
        roi_name="Prostate",
        roi_number=roi_number,
        is_gt=False,
        rater=rater,
        score=score,
        blinded=blinded,
    )


def test_qualitative_overlays_onto_existing_metric_row():
    mgr = ResultsManager()
    mgr.add_row(_row(test_source="Limbus", test_organ="Prostate", metrics={"dice": 0.9}))
    _score(mgr, rater="Alice", score=4, blinded=True)
    _score(mgr, rater="Bob", score=3, blinded=True)
    rows = mgr.rows()
    assert len(rows) == 1  # merged onto the one metric row, not a separate row
    m = rows[0]["metrics"]
    assert m["dice"] == 0.9  # original metric preserved
    assert m["likert_Alice"] == 4
    assert m["likert_Bob"] == 3
    assert m["qualitative_assessed"] is True
    assert m["qualitative_blinded"] is True  # single blinded flag, not per-rater
    assert rows[0].get("comparison_mode", "") != "Qualitative"


def test_unassessed_metric_row_is_marked_qualitative_false():
    mgr = ResultsManager()
    mgr.add_row(_row(test_source="Limbus", test_organ="Prostate", metrics={"dice": 0.9}))
    mgr.add_row(_row(test_source="Radformation", test_organ="Prostate", metrics={"dice": 0.8}))
    _score(mgr, rater="Alice", score=4, blinded=True, source="Limbus", roi_number=2)
    rows = mgr.rows()
    by_src = {r["test_source_label"]: r["metrics"] for r in rows}
    assert by_src["Limbus"]["qualitative_assessed"] is True
    assert by_src["Radformation"]["qualitative_assessed"] is False  # not rated


def test_qualitative_synthetic_row_when_no_metric_row():
    mgr = ResultsManager()
    _score(mgr, rater="Alice", score=4, blinded=False)
    rows = mgr.rows()
    assert len(rows) == 1
    assert rows[0]["comparison_mode"] == "Qualitative"
    assert rows[0]["metrics"]["likert_Alice"] == 4
    assert rows[0]["metrics"]["qualitative_assessed"] is True
    assert rows[0]["metrics"]["qualitative_blinded"] is False


def test_qualitative_rerating_overwrites_in_place():
    mgr = ResultsManager()
    _score(mgr, rater="Alice", score=4, blinded=True)
    _score(mgr, rater="Alice", score=2, blinded=False)  # Alice re-rates same contour
    rows = mgr.rows()
    assert len(rows) == 1
    assert rows[0]["metrics"]["likert_Alice"] == 2
    assert rows[0]["metrics"]["qualitative_blinded"] is False


def test_qualitative_blinded_is_a_single_column():
    mgr = ResultsManager()
    _score(mgr, rater="Alice", score=4, blinded=True)
    _score(mgr, rater="Bob", score=5, blinded=True)
    cols = mgr.metric_columns()
    assert cols.count("qualitative_blinded") == 1
    assert "likert_Alice" in cols and "likert_Bob" in cols


def test_qualitative_columns_sit_immediately_before_first_dose_column():
    mgr = ResultsManager()
    mgr.add_row(_row(metrics={"dice": 0.9, "dmin_gy": 10.0, "dmean_gy": 20.0}))
    _score(mgr, rater="Alice", score=4, blinded=True)
    cols = mgr.metric_columns()
    dose_at = cols.index("dmin_gy")
    assert cols.index("likert_Alice") < dose_at
    assert cols.index("qualitative_assessed") < dose_at
    assert cols.index("qualitative_blinded") < dose_at
    # qualitative columns are the last thing before the dose block
    assert cols[dose_at - 1] == "qualitative_blinded"


def test_qualitative_column_labels():
    assert metric_display_label("likert_Alice") == "Likert — Alice"
    assert metric_display_label("qualitative_assessed") == "Qualitative"
    assert metric_display_label("qualitative_blinded") == "Blinded"


def test_qualitative_columns_appear_in_csv(tmp_path):
    mgr = ResultsManager()
    _score(mgr, rater="Alice", score=4, blinded=True)
    out = tmp_path / "q.csv"
    mgr.export_csv(out)
    with out.open(encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert "Likert — Alice" in header
    assert "Qualitative" in header
    assert "Blinded" in header


# ---- ResultsManager ------------------------------------------------------


def test_results_manager_starts_empty():
    rm = ResultsManager()
    assert len(rm) == 0
    assert rm.rows() == []
    # Even with no rows, the canonical metric columns are always present so
    # the table layout stays stable across runs.
    assert rm.metric_columns() == list(CANONICAL_METRIC_COLUMNS)


def test_results_manager_add_and_metric_columns():
    rm = ResultsManager()
    rm.add_row(_row(metrics={"dice": 0.95, "hd95": 5.0}))
    rm.add_row(_row(metrics={"dice": 0.80, "surface_dice": 0.7}))
    cols = rm.metric_columns()
    # Canonical columns always come first in their declared order. Unknown
    # metrics (here: "hd95") are appended at the end.
    assert cols[: len(CANONICAL_METRIC_COLUMNS)] == list(CANONICAL_METRIC_COLUMNS)
    assert cols[len(CANONICAL_METRIC_COLUMNS) :] == ["hd95"]
    # Sanity-check that the canonical block has the expected order
    assert cols.index("dice") < cols.index("surface_dice") < cols.index("hausdorff100")


def test_metric_display_label_dvh_variants():
    """All four DVH key shapes render with the right unit suffix."""
    assert metric_display_label("d95_gy") == "D95 (Gy)"
    assert metric_display_label("d2cc_gy") == "D2cc (Gy)"
    # Non-integer cc value (D0.1cc is a common cord/chiasm constraint)
    assert metric_display_label("d0.1cc_gy") == "D0.1cc (Gy)"
    assert metric_display_label("v20gy_cc") == "V20Gy (cc)"


def test_dvh_diff_column_labels():
    """``{base}_diff`` reuses the base label + ``Δ vs GT`` suffix."""
    assert metric_display_label("d2cc_gy_diff") == "D2cc (Gy) Δ vs GT"
    assert metric_display_label("dmean_gy_diff") == "Dmean (Gy) Δ vs GT"
    assert metric_display_label("v20gy_cc_diff") == "V20Gy (cc) Δ vs GT"


def test_dvh_diff_columns_sort_after_absolutes():
    """Every absolute DVH column comes first, then the Δ-vs-GT columns."""
    rm = ResultsManager()
    rm.add_row(
        _row(
            metrics={
                "d95_gy": 55.0,
                "d2cc_gy": 50.0,
                "v20gy_cc": 25.0,
                "d95_gy_diff": -1.0,
                "d2cc_gy_diff": 0.5,
                "v20gy_cc_diff": 2.0,
                "dmean_gy_diff": 1.2,
            }
        )
    )
    dynamic = rm.metric_columns()[len(CANONICAL_METRIC_COLUMNS) :]
    # Absolutes first (family order), then all diffs.
    assert dynamic == [
        "d95_gy",
        "d2cc_gy",
        "v20gy_cc",
        "d95_gy_diff",
        "d2cc_gy_diff",
        "v20gy_cc_diff",
        "dmean_gy_diff",
    ]


def test_dvh_dynamic_columns_sort_into_three_blocks():
    """D% (descending), D-cc (ascending), V-Gy (ascending) — distinct blocks."""
    rm = ResultsManager()
    rm.add_row(
        _row(
            metrics={
                "v40gy_cc": 5.0,
                "d2_gy": 60.0,
                "d2cc_gy": 50.0,
                "d95_gy": 55.0,
                "v20gy_cc": 25.0,
                "d0.1cc_gy": 52.0,
            }
        )
    )
    dynamic_cols = rm.metric_columns()[len(CANONICAL_METRIC_COLUMNS) :]
    assert dynamic_cols == [
        "d95_gy",
        "d2_gy",
        "d0.1cc_gy",
        "d2cc_gy",
        "v20gy_cc",
        "v40gy_cc",
    ]


def test_results_manager_clear():
    rm = ResultsManager()
    rm.add_row(_row())
    rm.add_row(_row())
    assert len(rm) == 2
    rm.clear()
    assert len(rm) == 0


def test_results_manager_rows_returns_copy():
    rm = ResultsManager()
    rm.add_row(_row())
    snapshot = rm.rows()
    snapshot[0]["drawer"] = "MUTATED"
    assert rm.rows()[0]["drawer"] == "Prostate"  # original untouched


def test_export_csv_writes_expected_headers_and_rows(tmp_path):
    rm = ResultsManager()
    rm.add_row(_row(metrics={"dice": 0.95, "hd95": 5.0}))
    rm.add_row(_row(test_source="MiM", metrics={"dice": 0.80, "surface_dice": 0.7}))
    path = tmp_path / "out.csv"
    n = rm.export_csv(path)
    assert n == 2
    assert path.exists()

    with path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    headers = rows[0]
    # Meta labels first, in META_COLUMNS order; canonical metric columns next
    # (always — even when empty, shown with their display labels); then any
    # dynamic metric columns.
    expected_meta = [label for _, label in META_COLUMNS]
    assert headers[: len(expected_meta)] == expected_meta
    canonical_block = headers[
        len(expected_meta) : len(expected_meta) + len(CANONICAL_METRIC_COLUMNS)
    ]
    assert canonical_block == [metric_display_label(k) for k in CANONICAL_METRIC_COLUMNS]
    assert headers[len(expected_meta) + len(CANONICAL_METRIC_COLUMNS) :] == ["hd95"]

    # First data row: drawer in column 0, "Limbus" present
    assert rows[1][0] == "Prostate"
    assert "Limbus" in rows[1]
    # The "Surface Dice" column should be EMPTY for the first row (only second row has it)
    sd_idx = headers.index(metric_display_label("surface_dice"))
    assert rows[1][sd_idx] == ""
    assert rows[2][sd_idx] == "0.7"


def test_export_csv_formats_floats_consistently(tmp_path):
    rm = ResultsManager()
    rm.add_row(_row(metrics={"dice": 0.9876543, "hd95": math.nan}))
    path = tmp_path / "out.csv"
    rm.export_csv(path)
    with path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    headers = rows[0]
    dice_idx = headers.index(metric_display_label("dice"))
    hd_idx = headers.index("hd95")  # unknown key — falls through unchanged
    # 6-significant-figure float
    assert rows[1][dice_idx] == "0.987654"
    # NaN renders as empty cell
    assert rows[1][hd_idx] == ""


def test_export_csv_handles_bool_truncated_column(tmp_path):
    rm = ResultsManager()
    rm.add_row(_row(truncated=True))
    rm.add_row(_row(truncated=False))
    path = tmp_path / "out.csv"
    rm.export_csv(path)
    with path.open("r", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    truncated_label = next(label for k, label in META_COLUMNS if k == "truncated")
    idx = rows[0].index(truncated_label)
    assert rows[1][idx] == "True"
    assert rows[2][idx] == "False"


# ---- ResultsTab UI -------------------------------------------------------


def test_results_tab_starts_empty(qapp):
    tab = ResultsTab()
    assert tab._table.rowCount() == 0
    assert not tab._export_btn.isEnabled()
    assert not tab._clear_btn.isEnabled()


def test_results_tab_refresh_populates_table(qapp):
    rm = ResultsManager()
    rm.add_row(_row(metrics={"dice": 0.95, "hd95": 5.0}))
    rm.add_row(_row(test_source="MiM", metrics={"dice": 0.80}))
    tab = ResultsTab()
    tab.set_results_manager(rm)
    assert tab._table.rowCount() == 2
    # Headers include meta + metric columns
    headers = [tab._table.horizontalHeaderItem(c).text() for c in range(tab._table.columnCount())]
    assert "Drawer" in headers
    assert metric_display_label("dice") in headers
    assert "hd95" in headers  # unknown key — display label falls through unchanged
    assert tab._export_btn.isEnabled()
    assert tab._clear_btn.isEnabled()


def test_results_tab_highlights_error_rows(qapp):
    rm = ResultsManager()
    rm.add_row(_row())  # clean row
    rm.add_row(_row(error="DVH failed"))  # error row
    tab = ResultsTab()
    tab.set_results_manager(rm)
    # Row 1 (error) should have the error background colour on its cells
    cell_clean = tab._table.item(0, 0)
    cell_err = tab._table.item(1, 0)
    assert cell_clean.background().color() != cell_err.background().color()


def test_results_tab_clear_button_empties_manager(qapp, monkeypatch):
    rm = ResultsManager()
    rm.add_row(_row())
    rm.add_row(_row())
    tab = ResultsTab()
    tab.set_results_manager(rm)
    # Auto-accept the confirmation dialog
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    tab._on_clear_clicked()
    assert len(rm) == 0
    assert tab._table.rowCount() == 0


def test_results_tab_append_row_refreshes(qapp):
    """When MainWindow forwards a worker row, the table updates immediately."""
    rm = ResultsManager()
    tab = ResultsTab()
    tab.set_results_manager(rm)
    assert tab._table.rowCount() == 0
    rm.add_row(_row(metrics={"dice": 0.9}))
    tab.append_row({})  # triggers refresh
    assert tab._table.rowCount() == 1


def test_results_tab_export_button_writes_file(qapp, tmp_path, monkeypatch):
    rm = ResultsManager()
    rm.add_row(_row(metrics={"dice": 0.9}))
    tab = ResultsTab()
    tab.set_results_manager(rm)
    target = tmp_path / "out.csv"
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        lambda *a, **kw: (str(target), "CSV files (*.csv)"),
    )
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.StandardButton.Ok)
    tab._on_export_clicked()
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert "Drawer" in text
    assert metric_display_label("dice") in text
