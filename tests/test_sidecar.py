"""The audit sidecar: what it records, and when it records nothing.

A results table holds one value per metric, which is the right shape for
reading and the wrong shape for reproducing. These check that the detail behind
each number survives to a file, that a record can be tied back to the row it
came from, and — the part most likely to go wrong quietly — that a run which
never collected detail produces no file rather than a file full of nulls.
"""

from __future__ import annotations

import json

from autoseg_evaluator.data import sidecar


def _row(**overrides):
    row = {
        "patient_id": "P01",
        "linkage_id": "course-1",
        "drawer": "Parotid_L",
        "canonical_organ": "parotid",
        "comparison_mode": "gt",
        "gt_source_label": "Manual",
        "gt_roi_name": "Parotid_L",
        "test_source_label": "VendorA",
        "test_organ": "Parotid_L",
        "truncated": False,
        "metrics": {"dice": 0.85},
        "error": "",
        "audit": {
            "mask": {
                "rasteriser_backend": "continuous",
                "voxel_spacing_mm": [1.367, 1.367, 2.0],
                "gt_to_test": {"max_mm": 12.77, "mean_mm": 1.33},
                "test_to_gt": {"max_mm": 5.89, "mean_mm": 0.95},
            },
            "polygon": {
                "engine": "fast",
                "version": "0.2.0.dev2",
                "tolerance_mm": 3.0,
                "measurements": {"hd_mm": 19.93, "a_missing_length_mm": 63.5},
            },
        },
    }
    row.update(overrides)
    return row


def test_a_record_carries_both_streams_and_says_which_comparison_it_is(tmp_path):
    target = tmp_path / "results.audit.json"

    written = sidecar.write(target, [_row()], settings={"polygon_tolerance_mm": 3.0})

    assert written == target
    doc = json.loads(target.read_text(encoding="utf-8"))
    record = doc["records"][0]
    # Identity, or the numbers have no subject and cannot be rejoined to the CSV.
    assert record["patient_id"] == "P01"
    assert record["test_source_label"] == "VendorA"
    assert record["linkage_id"] == "course-1"
    assert set(record) >= {"mask", "polygon"}
    assert doc["settings"]["polygon_tolerance_mm"] == 3.0
    assert doc["comparisons"] == 1


def test_both_directions_survive_where_the_table_keeps_one(tmp_path):
    """The asymmetry is usually the finding.

    A ground truth reaching 12.8 mm from anything the test drew, against a test
    staying within 5.9 mm of the ground truth, is under-segmentation — and the
    symmetric maximum the table shows conceals which side it came from.
    """
    target = tmp_path / "r.audit.json"
    sidecar.write(target, [_row()])

    mask = json.loads(target.read_text(encoding="utf-8"))["records"][0]["mask"]
    assert mask["gt_to_test"]["max_mm"] != mask["test_to_gt"]["max_mm"]
    assert mask["gt_to_test"]["mean_mm"] != mask["test_to_gt"]["mean_mm"]


def test_a_run_that_kept_no_detail_writes_no_file(tmp_path):
    """Silence, not an empty file.

    Detail is collected during the run and cannot be recovered afterwards, so a
    run without it has nothing to say. A file of nulls would look like a failed
    export rather than an option left off.
    """
    target = tmp_path / "results.audit.json"
    plain = _row()
    plain.pop("audit")

    assert sidecar.write(target, [plain]) is None
    assert not target.exists()
    assert not sidecar.has_detail([plain])


def test_rows_without_detail_are_skipped_rather_than_padded(tmp_path):
    target = tmp_path / "r.audit.json"
    plain = _row(patient_id="P02")
    plain.pop("audit")

    sidecar.write(target, [_row(), plain])

    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc["comparisons"] == 1
    assert [r["patient_id"] for r in doc["records"]] == ["P01"]


def test_truncation_is_recorded_as_a_mask_stream_fact(tmp_path):
    """It changes the mask numbers and nothing else.

    The polygon stream's shared-plane rule already excludes what truncation
    removes, so recording it without saying whose it is would imply both
    streams moved.
    """
    target = tmp_path / "r.audit.json"
    sidecar.write(
        target,
        [_row(truncated=True, truncated_slices=3, truncated_extent_mm=6.0)],
    )

    doc = json.loads(target.read_text(encoding="utf-8"))
    record = doc["records"][0]
    assert record["mask_truncation"]["applied"] is True
    assert record["mask_truncation"]["slices_removed"] == 3
    assert "truncation" in doc["notes"]
    assert "mask stream only" in doc["notes"]["truncation"]


def test_the_sidecar_sits_beside_the_export_it_describes():
    assert sidecar.path_for("results.csv").name == "results.audit.json"
    assert sidecar.path_for("/tmp/cohort/run3.csv").name == "run3.audit.json"


def test_an_error_travels_with_the_record(tmp_path):
    target = tmp_path / "r.audit.json"
    sidecar.write(target, [_row(error="DVH: no dose for this structure set")])

    record = json.loads(target.read_text(encoding="utf-8"))["records"][0]
    assert record["error"].startswith("DVH:")


def test_the_document_says_what_each_stream_measured_over(tmp_path):
    """Two Hausdorffs in one file need their measures stated, not inferred."""
    target = tmp_path / "r.audit.json"
    sidecar.write(target, [_row()])

    notes = json.loads(target.read_text(encoding="utf-8"))["notes"]
    assert "mm^2" in notes["mask_measure"]
    assert "arc length" in notes["polygon_measure"]
    assert "shared contour planes" in notes["polygon_measure"]
