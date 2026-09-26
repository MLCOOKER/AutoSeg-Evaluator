"""Regression tests for the external audit of v3 (September 2026).

Each test reproduces one finding on real DICOM written to a temporary folder —
a CT with pixel data, structure sets with contours — and checks the fixed
behaviour. The findings that live in one module are tested beside it instead:
statistics (``test_statistics``), pairing (``test_report_model``), tolerances
(``test_metrics``, ``test_polygon_metrics``), results (``test_results``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pydicom
import pytest
import SimpleITK as sitk
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import (
    CTImageStorage,
    ExplicitVRLittleEndian,
    RTStructureSetStorage,
    generate_uid,
)
from PySide6.QtWidgets import QApplication

from autoseg_evaluator.core.masks import (
    find_reference_image_folder,
    load_reference_image,
    read_dicom_image,
)
from autoseg_evaluator.data.metadata import MetadataLibrary

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_linkage import _write_rtdose  # noqa: E402

ROWS = COLS = 24
PIXEL_MM = 1.0
SLICE_MM = 2.0


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---- Writers ----------------------------------------------------------------


def _meta(sop_class_uid: str, sop_instance_uid: str) -> FileMetaDataset:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class_uid
    meta.MediaStorageSOPInstanceUID = sop_instance_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()
    return meta


def _save(ds: FileDataset, path: Path) -> None:
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    pydicom.dcmwrite(str(path), ds)


def _write_ct(
    folder: Path,
    *,
    patient_id: str,
    study_uid: str,
    for_uid: str,
    slices: int,
    z0: float = 0.0,
    series_uid: str | None = None,
) -> tuple[str, list[str]]:
    """A CT series with pixel data; returns ``(series_uid, sop_uids)`` in z order."""
    series_uid = series_uid or generate_uid()
    folder.mkdir(parents=True, exist_ok=True)
    sops = []
    for index in range(slices):
        sop = generate_uid()
        ds = FileDataset(str(folder / f"{sop}.dcm"), {}, file_meta=_meta(CTImageStorage, sop))
        ds.preamble = b"\0" * 128
        ds.PatientID = patient_id
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.SOPInstanceUID = sop
        ds.SOPClassUID = CTImageStorage
        ds.FrameOfReferenceUID = for_uid
        ds.Modality = "CT"
        ds.StudyDate = "20260101"
        ds.InstanceNumber = index + 1
        ds.ImagePositionPatient = [0.0, 0.0, z0 + index * SLICE_MM]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.PixelSpacing = [PIXEL_MM, PIXEL_MM]
        ds.SliceThickness = SLICE_MM
        ds.Rows, ds.Columns = ROWS, COLS
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 16, 15
        ds.PixelRepresentation = 0
        ds.RescaleIntercept, ds.RescaleSlope = 0, 1
        ds.PixelData = np.zeros((ROWS, COLS), dtype=np.uint16).tobytes()
        _save(ds, folder / f"{sop}.dcm")
        sops.append(sop)
    return series_uid, sops


def _write_rtss(
    folder: Path,
    *,
    patient_id: str,
    study_uid: str,
    for_uid: str,
    series_uid: str,
    ct_sops: list[str],
    z0: float,
    manufacturer: str,
    roi_name: str = "Parotid_L",
    square: tuple[float, float, float, float] = (6.0, 6.0, 16.0, 16.0),
) -> str:
    """A structure set with one ROI (number 1): the square on every CT slice."""
    sop = generate_uid()
    folder.mkdir(parents=True, exist_ok=True)
    ds = FileDataset(str(folder / f"{sop}.dcm"), {}, file_meta=_meta(RTStructureSetStorage, sop))
    ds.preamble = b"\0" * 128
    ds.PatientID = patient_id
    ds.StudyInstanceUID = study_uid
    ds.SOPInstanceUID = sop
    ds.SOPClassUID = RTStructureSetStorage
    ds.SeriesInstanceUID = generate_uid()
    ds.Modality = "RTSTRUCT"
    ds.Manufacturer = manufacturer
    ds.StructureSetLabel = manufacturer

    series_item = Dataset()
    series_item.SeriesInstanceUID = series_uid
    images = []
    for ct_sop in ct_sops:
        image = Dataset()
        image.ReferencedSOPClassUID = CTImageStorage
        image.ReferencedSOPInstanceUID = ct_sop
        images.append(image)
    series_item.ContourImageSequence = images
    study_item = Dataset()
    study_item.ReferencedSOPClassUID = "1.2.840.10008.3.1.2.3.1"
    study_item.ReferencedSOPInstanceUID = study_uid
    study_item.RTReferencedSeriesSequence = [series_item]
    frame = Dataset()
    frame.FrameOfReferenceUID = for_uid
    frame.RTReferencedStudySequence = [study_item]
    ds.ReferencedFrameOfReferenceSequence = [frame]

    roi = Dataset()
    roi.ROINumber = 1
    roi.ROIName = roi_name
    roi.ReferencedFrameOfReferenceUID = for_uid
    ds.StructureSetROISequence = [roi]

    x0, y0, x1, y1 = square
    contours = []
    for index, ct_sop in enumerate(ct_sops):
        z = z0 + index * SLICE_MM
        contour = Dataset()
        contour.ContourGeometricType = "CLOSED_PLANAR"
        contour.NumberOfContourPoints = 4
        contour.ContourData = [x0, y0, z, x1, y0, z, x1, y1, z, x0, y1, z]
        image = Dataset()
        image.ReferencedSOPClassUID = CTImageStorage
        image.ReferencedSOPInstanceUID = ct_sop
        contour.ContourImageSequence = [image]
        contours.append(contour)
    roi_contour = Dataset()
    roi_contour.ReferencedROINumber = 1
    roi_contour.ContourSequence = contours
    ds.ROIContourSequence = [roi_contour]
    _save(ds, folder / f"{sop}.dcm")
    return sop


def _scan(root: Path) -> MetadataLibrary:
    library = MetadataLibrary()
    library.scan_folder(str(root))
    return library


def _two_courses(root: Path) -> dict[str, str]:
    """One patient, two courses on two CTs, each with a GT and a vendor contour."""
    ids: dict[str, str] = {}
    for course, z0 in (("1", 0.0), ("2", 200.0)):
        study, frame = generate_uid(), generate_uid()
        series, sops = _write_ct(
            root / f"course{course}" / "ct",
            patient_id="P1",
            study_uid=study,
            for_uid=frame,
            slices=6,
            z0=z0,
        )
        ids[f"series{course}"] = series
        for label in ("Manual", "VendorA"):
            ids[f"{label}{course}"] = _write_rtss(
                root / f"course{course}",
                patient_id="P1",
                study_uid=study,
                for_uid=frame,
                series_uid=series,
                ct_sops=sops,
                z0=z0,
                manufacturer=label,
            )
    return ids


# ---- Finding 2: two series in one folder -------------------------------------


def test_two_series_in_one_folder_are_read_as_two_images(tmp_path):
    """External audit: the CT was read by folder, and GDCM took whichever
    series it listed first. A planning CT and a CBCT exported flat into one
    folder shared one image, so one structure set was measured on the other's.
    """
    study, frame = generate_uid(), generate_uid()
    folder = tmp_path / "flat"
    planning, planning_sops = _write_ct(
        folder, patient_id="P1", study_uid=study, for_uid=frame, slices=6, z0=0.0
    )
    cbct, cbct_sops = _write_ct(
        folder, patient_id="P1", study_uid=study, for_uid=frame, slices=4, z0=-50.0
    )
    on_planning = _write_rtss(
        folder,
        patient_id="P1",
        study_uid=study,
        for_uid=frame,
        series_uid=planning,
        ct_sops=planning_sops,
        z0=0.0,
        manufacturer="Manual",
    )
    on_cbct = _write_rtss(
        folder,
        patient_id="P1",
        study_uid=study,
        for_uid=frame,
        series_uid=cbct,
        ct_sops=cbct_sops,
        z0=-50.0,
        manufacturer="VendorA",
    )
    library = _scan(tmp_path)

    # Both resolve to the same folder, which is why reading by folder failed.
    assert find_reference_image_folder(library, "P1", on_planning) == find_reference_image_folder(
        library, "P1", on_cbct
    )
    first_found = read_dicom_image(str(folder)).GetSize()[2]
    assert first_found in (6, 4)

    planning_image = load_reference_image(library, "P1", on_planning)
    cbct_image = load_reference_image(library, "P1", on_cbct)
    assert planning_image.GetSize()[2] == 6
    assert planning_image.GetOrigin()[2] == pytest.approx(0.0)
    assert cbct_image.GetSize()[2] == 4
    assert cbct_image.GetOrigin()[2] == pytest.approx(-50.0)


def test_the_worker_caches_each_series_separately(tmp_path):
    from autoseg_evaluator.workers.metrics_worker import MetricsWorker

    study, frame = generate_uid(), generate_uid()
    folder = tmp_path / "flat"
    a, a_sops = _write_ct(folder, patient_id="P1", study_uid=study, for_uid=frame, slices=6)
    b, b_sops = _write_ct(
        folder, patient_id="P1", study_uid=study, for_uid=frame, slices=3, z0=-40.0
    )
    on_a = _write_rtss(
        folder,
        patient_id="P1",
        study_uid=study,
        for_uid=frame,
        series_uid=a,
        ct_sops=a_sops,
        z0=0.0,
        manufacturer="Manual",
    )
    on_b = _write_rtss(
        folder,
        patient_id="P1",
        study_uid=study,
        for_uid=frame,
        series_uid=b,
        ct_sops=b_sops,
        z0=-40.0,
        manufacturer="VendorA",
    )
    worker = MetricsWorker(_scan(tmp_path), [], {})
    assert worker._load_ct("P1", on_a).GetSize()[2] == 6
    assert worker._load_ct("P1", on_b).GetSize()[2] == 3
    assert set(worker._ct_cache) == {("P1", a), ("P1", b)}


# ---- Finding 3: one treatment context per comparison --------------------------


def test_auto_match_offers_only_structure_sets_on_the_gts_planning_image(qapp, tmp_path):
    """External audit: a second course's structure set was matched to the first
    course's ground truth with a perfect, unflagged name score."""
    from autoseg_evaluator.ui.tabs.match_contours import MatchContoursTab

    ids = _two_courses(tmp_path)
    tab = MatchContoursTab(settings={})
    tab.set_library(_scan(tmp_path))

    tests = tab._auto_match_tests("P1", ids["Manual1"], "Parotid_L")

    assert {t.rtstruct_sop_uid for t in tests} == {ids["VendorA1"]}
    skipped = {sop for _patient, sop in tab._skipped_other_image}
    assert skipped == {ids["Manual2"], ids["VendorA2"]}
    tab.deleteLater()


def test_a_contour_from_another_planning_image_cannot_be_added_by_hand(qapp, tmp_path):
    from autoseg_evaluator.ui.tabs.match_contours import MatchContoursTab

    ids = _two_courses(tmp_path)
    tab = MatchContoursTab(settings={})
    tab.set_library(_scan(tmp_path))
    tab._set_ground_truth_organs([("P1", ids["Manual1"], 1, "Parotid_L")])
    drawer = tab._drawers["Parotid_L"]
    before = len(drawer.patient_subsection("P1").tests)

    tab._add_to_drawer(drawer, [("P1", ids["VendorA2"], 1, "Parotid_L")])

    assert len(drawer.patient_subsection("P1").tests) == before
    tab.deleteLater()


def test_the_worker_refuses_a_test_drawn_on_another_planning_image(qapp, tmp_path):
    """The safety net for sessions matched before auto-match checked: the test
    would have been rasterised on the ground truth's CT and still produced
    plausible numbers."""
    from autoseg_evaluator.workers.metrics_worker import MetricsWorker

    ids = _two_courses(tmp_path)
    drawers = [
        {
            "organ_name": "Parotid_L",
            "truncate": False,
            "gt_comparison": True,
            "patients": [
                {
                    "patient_id": "P1",
                    "gt": {
                        "rtstruct_sop_uid": ids["Manual1"],
                        "source_label": "Manual",
                        "roi_number": 1,
                        "roi_name": "Parotid_L",
                    },
                    "tests": [
                        {
                            "rtstruct_sop_uid": sop,
                            "source_label": "VendorA",
                            "organ_name": "Parotid_L",
                            "roi_number": 1,
                        }
                        for sop in (ids["VendorA1"], ids["VendorA2"])
                    ],
                }
            ],
        }
    ]
    worker = MetricsWorker(_scan(tmp_path), drawers, {"geometric": {"dice": True}})
    rows: list[dict] = []
    worker.result.connect(rows.append)
    worker._do_run()

    by_test = {row["test_rtstruct_sop_uid"]: row for row in rows}
    assert by_test[ids["VendorA1"]]["metrics"]["dice"] == pytest.approx(1.0)
    assert not by_test[ids["VendorA1"]]["error"]
    assert "different planning image" in by_test[ids["VendorA2"]]["error"]
    assert "dice" not in by_test[ids["VendorA2"]]["metrics"]
    # Every row says when it was produced.
    assert all(row.get("computed_at") for row in rows)


def test_a_consensus_fuses_only_raters_on_one_planning_image(qapp, tmp_path):
    """External audit: the builder chose a majority frame of reference and then
    fused every rater anyway, other courses included."""
    from autoseg_evaluator.ui.tabs.build_consensus import BuildConsensusTab

    ids = _two_courses(tmp_path)
    tab = BuildConsensusTab(settings={})
    tab.set_library(_scan(tmp_path))
    buckets = {
        "Parotid_L": [
            (ids["Manual1"], 1, "Parotid_L"),
            (ids["VendorA1"], 1, "Parotid_L"),
            (ids["VendorA2"], 1, "Parotid_L"),
        ]
    }
    dropped: list[str] = []

    entry = tab._build_synthetic_entry("P1", buckets, dropped)

    assert entry.constituent_groups[1] == [(ids["Manual1"], 1), (ids["VendorA1"], 1)]
    assert entry.referenced_series_uids == {ids["series1"]}
    assert len(dropped) == 1 and "different planning image" in dropped[0]
    tab.deleteLater()


# ---- Findings 8 and 10: the Match Contours viewer ------------------------------


def test_the_viewer_uses_the_dose_the_metrics_use(qapp, tmp_path):
    """External audit: the viewer picked "same frame, PLAN preferred, first
    found" by its own rule and could show a dose the DVH never used."""
    from autoseg_evaluator.data.linkage import KIND_DOSE, override_key
    from autoseg_evaluator.ui.tabs.match_contours import MatchContoursTab

    ids = _two_courses(tmp_path)
    course = tmp_path / "course1"
    frame = pydicom.dcmread(str(course / f"{ids['Manual1']}.dcm"))
    frame_uid = frame.ReferencedFrameOfReferenceSequence[0].FrameOfReferenceUID
    study = frame.StudyInstanceUID
    first = _write_rtdose(course, patient_id="P1", study_uid=study, for_uid=frame_uid)
    second = _write_rtdose(course, patient_id="P1", study_uid=study, for_uid=frame_uid)
    library = _scan(tmp_path)
    tab = MatchContoursTab(settings={})
    tab.set_library(library)

    # Two PLAN doses on one frame: the metrics refuse to guess, so the viewer
    # shows none rather than whichever it found first.
    assert tab._find_dose_path_for_viz("P1", ids["Manual1"]) is None
    library.link_overrides[override_key("P1", ids["Manual1"], KIND_DOSE)] = second
    assert tab._find_dose_path_for_viz("P1", ids["Manual1"]).endswith(f"{second}.dcm")
    assert first != second
    tab.deleteLater()


def test_the_viewer_builds_a_consensus_ground_truth_from_its_raters(qapp, tmp_path):
    """External audit: a consensus entry has no file, and the viewer tried to
    read its empty path as a DICOM file."""
    from autoseg_evaluator.ui.tabs.build_consensus import BuildConsensusTab
    from autoseg_evaluator.ui.tabs.match_contours import MatchContoursTab
    from autoseg_evaluator.ui.widgets.organ_drawer import PatientSubsection

    ids = _two_courses(tmp_path)
    library = _scan(tmp_path)
    builder = BuildConsensusTab(settings={})
    builder.set_library(library)
    entry = builder._build_synthetic_entry(
        "P1",
        {"Parotid_L": [(ids["Manual1"], 1, "Parotid_L"), (ids["VendorA1"], 1, "Parotid_L")]},
    )
    assert library.register_synthetic_consensus("P1", entry.frame_of_reference_uid, entry)

    tab = MatchContoursTab(settings={})
    tab.set_library(library)
    sub = PatientSubsection(
        patient_id="P1",
        gt_rtstruct_sop_uid=entry.sop_instance_uid,
        gt_rtstruct_filename="",
        gt_source_label=entry.source_label,
        gt_roi_number=1,
        gt_roi_name="Parotid_L",
        tests=[],
    )

    ct, gt_mask, _tests, _dose = tab._load_visualization_data("P1", sub, truncate=False)

    assert ct.GetSize()[2] == 6
    assert int(sitk.GetArrayViewFromImage(gt_mask).sum()) > 0
    tab.deleteLater()
    builder.deleteLater()


# ---- Smaller findings -------------------------------------------------------


def test_undo_brings_back_a_drawer_removed_directly(qapp, tmp_path):
    """External audit: the snapshot was taken after the drawer was removed, so
    Undo restored a state that already lacked it."""
    from autoseg_evaluator.ui.tabs.match_contours import MatchContoursTab

    ids = _two_courses(tmp_path)
    tab = MatchContoursTab(settings={})
    tab.set_library(_scan(tmp_path))
    tab._set_ground_truth_organs([("P1", ids["Manual1"], 1, "Parotid_L")])

    tab._on_remove_drawer("Parotid_L")
    assert "Parotid_L" not in tab._drawers
    tab._on_undo_clicked()
    assert tab._drawers["Parotid_L"].patient_subsection("P1") is not None

    # Removing the last patient removes the drawer too; one Undo restores both,
    # rather than first an empty drawer.
    tab._on_remove_patient("Parotid_L", "P1")
    assert "Parotid_L" not in tab._drawers
    tab._on_undo_clicked()
    assert tab._drawers["Parotid_L"].patient_subsection("P1") is not None
    tab.deleteLater()


def test_the_consensus_uid_is_the_same_in_every_run():
    """``hash()`` is salted per process, so the UID used to change between runs."""
    from autoseg_evaluator.ui.tabs.build_consensus import BuildConsensusTab

    uid = BuildConsensusTab._mint_synthetic_uid(None, "P1")
    assert uid == BuildConsensusTab._mint_synthetic_uid(None, "P1")
    # Pinned: sha256("P1|consensus") mod 10**18.
    import hashlib

    expected = int(hashlib.sha256(b"P1|consensus").hexdigest(), 16) % (10**18)
    assert uid == f"AUTOSEG.SYNTHETIC.{expected}"
