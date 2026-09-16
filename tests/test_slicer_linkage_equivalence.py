"""Pin our RTSTRUCT→series traversal against SlicerRT's.

3D Slicer, via the SlicerRT extension, is the most widely exercised open-source
implementation of DICOM-RT reference resolution, so it is the natural oracle
for the tag traversal itself. ``_slicer_referenced_series_instance_uid`` below
is a direct port of::

    vtkSlicerDicomRtReader::GetReferencedSeriesInstanceUID()
    SlicerRT/DicomRtImportExport/Logic/vtkSlicerDicomRtReader.cxx

which walks ``ReferencedFrameOfReferenceSequence`` →
``RTReferencedStudySequence`` → ``RTReferencedSeriesSequence`` →
``SeriesInstanceUID``, calling ``gotoFirstItem()`` at each level.

Porting it rather than driving Slicer itself is deliberate: Slicer is a
multi-gigabyte GUI application whose headless extension installation is
fragile, and this repository already validates against external references by
re-implementing them (``test_platipy_equivalence``, ``test_metrics_equivalence``,
``test_staple_equivalence``). The port should be confirmed once by hand against
a real headless Slicer run before being relied on.

Two things this file establishes:

1. Where SlicerRT gives an answer, we give the same answer.
2. Where we deliberately differ — ``gotoFirstItem()`` silently picks the first
   of several referenced series, while we report an ambiguity — the difference
   is pinned so it cannot regress into an accidental divergence.

Dose linking has no counterpart here on purpose. SlicerRT resolves dose via
``ReferencedRTPlanSequence`` (an RTPLAN dependency this project does not take),
and its DVH module performs no automatic dose↔structure pairing at all —
``vtkSlicerDoseVolumeHistogramModuleLogic::ComputeDvh`` requires the user to
select both nodes. There is therefore no oracle to test dose linking against;
that behaviour is covered by the synthetic fixtures in ``test_linkage.py``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pydicom
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

from autoseg_evaluator.data.linkage import (
    TIER_EXPLICIT,
    resolve_image_series,
)
from tests.test_linkage import (
    _scan,
    _write,
    _write_ct_series,
    _write_rtstruct,
)

# ---- The oracle -----------------------------------------------------------


def _slicer_referenced_series_instance_uid(ds) -> str:
    """Port of ``vtkSlicerDicomRtReader::GetReferencedSeriesInstanceUID()``.

    Mirrors the C++ control flow exactly, including its use of the *first*
    item at each sequence level and its empty-string return on any missing
    level. No fallback: SlicerRT has none in this function.
    """
    for_seq = getattr(ds, "ReferencedFrameOfReferenceSequence", None)
    if not for_seq:
        return ""
    study_seq = getattr(for_seq[0], "RTReferencedStudySequence", None)
    if not study_seq:
        return ""
    series_seq = getattr(study_seq[0], "RTReferencedSeriesSequence", None)
    if not series_seq:
        return ""
    return str(getattr(series_seq[0], "SeriesInstanceUID", "") or "")


def _write_multi_series_rtstruct(
    folder: Path,
    *,
    patient_id: str,
    study_uid: str,
    for_uid: str,
    ref_series_uids: list[str],
) -> str:
    """An RTSTRUCT referencing several series — the case where we diverge."""
    sop_uid = generate_uid()
    ds = pydicom.dataset.FileDataset(
        str(folder / f"{sop_uid}.dcm"),
        {},
        file_meta=pydicom.dataset.FileMetaDataset(),
        preamble=b"\0" * 128,
    )
    ds.file_meta.MediaStorageSOPClassUID = pydicom.uid.RTStructureSetStorage
    ds.file_meta.MediaStorageSOPInstanceUID = sop_uid
    ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    ds.file_meta.ImplementationClassUID = generate_uid()

    ds.PatientID = patient_id
    ds.StudyInstanceUID = study_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = pydicom.uid.RTStructureSetStorage
    ds.Modality = "RTSTRUCT"
    ds.SeriesInstanceUID = generate_uid()

    series_items = []
    for series_uid in ref_series_uids:
        item = Dataset()
        item.SeriesInstanceUID = series_uid
        series_items.append(item)
    study_item = Dataset()
    study_item.ReferencedSOPInstanceUID = study_uid
    study_item.RTReferencedSeriesSequence = series_items
    for_item = Dataset()
    for_item.FrameOfReferenceUID = for_uid
    for_item.RTReferencedStudySequence = [study_item]
    ds.ReferencedFrameOfReferenceSequence = [for_item]

    roi = Dataset()
    roi.ROINumber = 1
    roi.ROIName = "Parotid_L"
    ds.StructureSetROISequence = [roi]

    _write(ds, folder / f"{sop_uid}.dcm")
    return sop_uid


# ---- Agreement ------------------------------------------------------------


def test_agrees_with_slicer_on_an_explicit_reference():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        _write_ct_series(root / "ct_a", patient_id="P1", study_uid=study, for_uid=for_uid)
        series_b, sops_b = _write_ct_series(
            root / "ct_b", patient_id="P1", study_uid=study, for_uid=for_uid
        )
        rtss_sop = _write_rtstruct(
            root,
            patient_id="P1",
            study_uid=study,
            for_uid=for_uid,
            ref_series_uid=series_b,
            ref_image_sops=sops_b,
        )
        lib = _scan(root)

        ours = resolve_image_series(lib, "P1", rtss_sop)
        theirs = _slicer_referenced_series_instance_uid(
            pydicom.dcmread(str(root / f"{rtss_sop}.dcm"))
        )
        assert theirs == series_b
        assert ours.is_resolved
        assert ours.target.series_instance_uid == theirs


def test_agrees_with_slicer_across_several_structure_sets():
    """Multi-vendor shape: several structure sets over one reference CT."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        series, sops = _write_ct_series(
            root / "ct", patient_id="P1", study_uid=study, for_uid=for_uid
        )
        sops_written = []
        for label in ("VendorA", "VendorB", "Manual"):
            sops_written.append(
                _write_rtstruct(
                    root,
                    patient_id="P1",
                    # Vendors commonly reissue the study UID on export.
                    study_uid=generate_uid(),
                    for_uid=for_uid,
                    label=label,
                    ref_series_uid=series,
                    ref_image_sops=sops,
                )
            )
        lib = _scan(root)

        for rtss_sop in sops_written:
            ours = resolve_image_series(lib, "P1", rtss_sop)
            theirs = _slicer_referenced_series_instance_uid(
                pydicom.dcmread(str(root / f"{rtss_sop}.dcm"))
            )
            assert theirs == series
            assert ours.target.series_instance_uid == theirs
            assert ours.tier == TIER_EXPLICIT


# ---- Deliberate divergence ------------------------------------------------


def test_we_flag_what_slicer_silently_takes_the_first_of():
    """A structure set referencing two series.

    ``gotoFirstItem()`` makes SlicerRT return the first referenced series with
    no indication that a second exists. We treat the same file as ambiguous
    and hand both candidates to the user. This is the one intended behavioural
    difference between the two implementations.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        series_a, _ = _write_ct_series(
            root / "ct_a", patient_id="P1", study_uid=study, for_uid=for_uid
        )
        series_b, _ = _write_ct_series(
            root / "ct_b", patient_id="P1", study_uid=study, for_uid=for_uid
        )
        rtss_sop = _write_multi_series_rtstruct(
            root,
            patient_id="P1",
            study_uid=study,
            for_uid=for_uid,
            ref_series_uids=[series_a, series_b],
        )
        lib = _scan(root)

        theirs = _slicer_referenced_series_instance_uid(
            pydicom.dcmread(str(root / f"{rtss_sop}.dcm"))
        )
        assert theirs == series_a, "SlicerRT takes the first referenced series"

        ours = resolve_image_series(lib, "P1", rtss_sop)
        assert ours.is_ambiguous
        assert not ours.is_resolved
        assert {s.series_instance_uid for s in ours.candidates} == {series_a, series_b}


def test_slicer_oracle_returns_nothing_without_the_study_level():
    """Guards the port itself: SlicerRT has no fallback when a level is absent.

    Our resolver does — it drops to the per-slice image UIDs — so the two are
    expected to disagree here, and that disagreement is the point.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        series, sops = _write_ct_series(
            root / "ct", patient_id="P1", study_uid=study, for_uid=for_uid
        )
        rtss_sop = _write_rtstruct(
            root,
            patient_id="P1",
            study_uid=study,
            for_uid=for_uid,
            ref_image_sops=sops,
        )
        lib = _scan(root)

        ds = pydicom.dcmread(str(root / f"{rtss_sop}.dcm"))
        # The series level exists but carries no SeriesInstanceUID.
        assert _slicer_referenced_series_instance_uid(ds) == ""
        assert resolve_image_series(lib, "P1", rtss_sop).target.series_instance_uid == series
