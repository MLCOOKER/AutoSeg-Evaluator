"""Integration tests for MetadataLibrary with synthetic DICOM files.

These tests build minimal valid DICOM datasets in a temp directory using pydicom,
then run the full folder scan to verify FrameOfReferenceUID-based grouping,
RTSTRUCT detection, source label cascade, and the issue list.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import (
    CTImageStorage,
    ExplicitVRLittleEndian,
    RTDoseStorage,
    RTStructureSetStorage,
    generate_uid,
)

from autoseg_evaluator.data.metadata import MetadataLibrary


def _new_file_meta(sop_class_uid: str, sop_instance_uid: str) -> FileMetaDataset:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class_uid
    meta.MediaStorageSOPInstanceUID = sop_instance_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()
    return meta


def _write(ds: FileDataset, path: Path) -> None:
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    pydicom.dcmwrite(str(path), ds)


def _write_ct_slice(
    folder: Path,
    *,
    patient_id: str,
    study_uid: str,
    series_uid: str,
    for_uid: str,
    sop_uid: str | None = None,
    study_date: str = "20260101",
) -> Path:
    sop_uid = sop_uid or generate_uid()
    meta = _new_file_meta(CTImageStorage, sop_uid)
    ds = FileDataset(str(folder / f"{sop_uid}.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientID = patient_id
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = CTImageStorage
    ds.FrameOfReferenceUID = for_uid
    ds.Modality = "CT"
    ds.StudyDate = study_date
    path = folder / f"{sop_uid}.dcm"
    _write(ds, path)
    return path


def _write_rtstruct(
    folder: Path,
    *,
    patient_id: str,
    study_uid: str,
    for_uid: str,
    manufacturer: str = "",
    structure_set_label: str = "",
    organs: list[str] | None = None,
    filename: str | None = None,
) -> Path:
    sop_uid = generate_uid()
    meta = _new_file_meta(RTStructureSetStorage, sop_uid)
    fname = filename or f"{sop_uid}.dcm"
    ds = FileDataset(str(folder / fname), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientID = patient_id
    ds.StudyInstanceUID = study_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = RTStructureSetStorage
    ds.Modality = "RTSTRUCT"
    ds.SeriesInstanceUID = generate_uid()
    if manufacturer:
        ds.Manufacturer = manufacturer
    if structure_set_label:
        ds.StructureSetLabel = structure_set_label

    # ReferencedFrameOfReferenceSequence carries the FoR for RTSTRUCT
    for_item = Dataset()
    for_item.FrameOfReferenceUID = for_uid
    ds.ReferencedFrameOfReferenceSequence = [for_item]

    organs = organs or []
    structure_set_seq = []
    for idx, name in enumerate(organs, start=1):
        roi_item = Dataset()
        roi_item.ROINumber = idx
        roi_item.ROIName = name
        roi_item.ReferencedFrameOfReferenceUID = for_uid
        structure_set_seq.append(roi_item)
    ds.StructureSetROISequence = structure_set_seq

    path = folder / fname
    _write(ds, path)
    return path


def _write_rtdose(
    folder: Path,
    *,
    patient_id: str,
    study_uid: str,
    for_uid: str,
) -> Path:
    sop_uid = generate_uid()
    meta = _new_file_meta(RTDoseStorage, sop_uid)
    ds = FileDataset(str(folder / f"{sop_uid}.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientID = patient_id
    ds.StudyInstanceUID = study_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = RTDoseStorage
    ds.Modality = "RTDOSE"
    ds.FrameOfReferenceUID = for_uid
    ds.SeriesInstanceUID = generate_uid()
    ds.DoseSummationType = "PLAN"
    path = folder / f"{sop_uid}.dcm"
    _write(ds, path)
    return path


# ---- Tests ----------------------------------------------------------------


def test_scan_single_patient_single_context():
    """One patient with CT + 2 RTSTRUCTs (different sources) sharing one FoR."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        study_uid = generate_uid()
        for_uid = generate_uid()

        _write_ct_slice(folder, patient_id="P1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid)
        _write_rtstruct(
            folder,
            patient_id="P1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Varian",
            organs=["Prostate", "Bladder"],
            filename="manual.dcm",
        )
        _write_rtstruct(
            folder,
            patient_id="P1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Limbus AI",
            organs=["Prostate", "Bladder", "Rectum"],
            filename="limbus.dcm",
        )

        lib = MetadataLibrary()
        lib.scan_folder(str(folder))

        assert set(lib.patients) == {"P1"}
        patient = lib.patients["P1"]
        assert len(patient.contexts) == 1
        ctx = patient.contexts[0]
        assert ctx.frame_of_reference_uid == for_uid
        assert len(ctx.image_series) == 1
        assert len(ctx.rtstructs) == 2
        sources = sorted(r.source_label for r in ctx.rtstructs)
        assert sources == ["Limbus AI", "Varian"]


def test_scan_groups_rtstruct_into_correct_context_via_for_uid():
    """An RTSTRUCT with a different StudyUID but same FoR groups with the CT."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        ct_study = generate_uid()
        ai_study = generate_uid()  # different study UID
        for_uid = generate_uid()   # same FoR

        _write_ct_slice(folder, patient_id="P1", study_uid=ct_study, series_uid=generate_uid(), for_uid=for_uid)
        _write_rtstruct(
            folder,
            patient_id="P1",
            study_uid=ai_study,  # vendor put it in a new study
            for_uid=for_uid,
            manufacturer="VendorX",
            organs=["Heart"],
        )

        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        patient = lib.patients["P1"]
        assert len(patient.contexts) == 1  # FoR groups them despite different StudyUIDs
        ctx = patient.contexts[0]
        assert {ct_study, ai_study} == ctx.study_instance_uids
        assert len(ctx.rtstructs) == 1
        assert len(ctx.image_series) == 1


def test_scan_separates_contexts_with_different_for_uids():
    """Two CTs with different FoR UIDs (e.g. planning + adaptive) create separate contexts."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for_planning = generate_uid()
        for_adaptive = generate_uid()

        _write_ct_slice(
            folder,
            patient_id="P1",
            study_uid=generate_uid(),
            series_uid=generate_uid(),
            for_uid=for_planning,
            study_date="20260101",
        )
        _write_ct_slice(
            folder,
            patient_id="P1",
            study_uid=generate_uid(),
            series_uid=generate_uid(),
            for_uid=for_adaptive,
            study_date="20260315",
        )

        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        patient = lib.patients["P1"]
        assert len(patient.contexts) == 2
        for_uids = {c.frame_of_reference_uid for c in patient.contexts}
        assert for_uids == {for_planning, for_adaptive}


def test_source_label_cascade_picks_label_when_manufacturer_missing():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        study_uid = generate_uid()
        for_uid = generate_uid()
        _write_ct_slice(folder, patient_id="P1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid)
        _write_rtstruct(
            folder,
            patient_id="P1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="",  # absent
            structure_set_label="AutoContour v3.1",
            organs=["Lung"],
            filename="export.dcm",
        )
        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        rtss = lib.all_rtstructs()[0]
        assert rtss.source_label == "AutoContour v3.1"
        assert rtss.source_origin == "label"


def test_source_label_falls_through_to_filename_for_inhouse_models():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        study_uid = generate_uid()
        for_uid = generate_uid()
        _write_ct_slice(folder, patient_id="P1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid)
        _write_rtstruct(
            folder,
            patient_id="P1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="",
            structure_set_label="",
            organs=["Liver"],
            filename="inhouse_unet_v2.dcm",
        )
        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        rtss = lib.all_rtstructs()[0]
        assert rtss.source_label == "inhouse_unet_v2"
        assert rtss.source_origin == "filename"


def test_custom_override_via_scan():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        study_uid = generate_uid()
        for_uid = generate_uid()
        _write_ct_slice(folder, patient_id="P1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid)
        rtss_path = _write_rtstruct(
            folder,
            patient_id="P1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Varian",
            organs=["Cord"],
        )
        # Read its SOPInstanceUID so we can target the override
        ds = pydicom.dcmread(str(rtss_path), stop_before_pixels=True)
        sop = str(ds.SOPInstanceUID)

        lib = MetadataLibrary()
        lib.scan_folder(str(folder), custom_overrides={sop: "MyResearchModel"})
        rtss = lib.all_rtstructs()[0]
        assert rtss.source_label == "MyResearchModel"
        assert rtss.source_origin == "custom"


def test_rtdose_detected_with_summation_type():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        study_uid = generate_uid()
        for_uid = generate_uid()
        _write_ct_slice(folder, patient_id="P1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid)
        _write_rtdose(folder, patient_id="P1", study_uid=study_uid, for_uid=for_uid)
        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        ctx = lib.patients["P1"].contexts[0]
        assert len(ctx.rtdoses) == 1
        assert ctx.rtdoses[0].dose_summation_type == "PLAN"


def test_issue_for_rtstruct_without_matching_ct():
    """An RTSTRUCT with a FoR that no CT references should raise a warning."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        study_uid = generate_uid()
        ct_for = generate_uid()
        orphan_for = generate_uid()
        _write_ct_slice(folder, patient_id="P1", study_uid=study_uid, series_uid=generate_uid(), for_uid=ct_for)
        _write_rtstruct(
            folder,
            patient_id="P1",
            study_uid=study_uid,
            for_uid=orphan_for,  # references a FoR with no CT
            manufacturer="OrphanVendor",
            organs=["?"],
        )
        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        # An issue should be reported
        warnings = [i for i in lib.issues if i.severity == "warning"]
        assert any("no matching CT" in i.message for i in warnings)


def test_anonymisation_alias_merge_via_shared_for_uid():
    """A vendor anonymises PatientID but keeps FoR — should merge into the real patient."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        study_uid = generate_uid()
        for_uid = generate_uid()

        # Real CT + normal RTSTRUCT both under PatientID="HN1"
        _write_ct_slice(
            folder, patient_id="HN1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid
        )
        _write_rtstruct(
            folder,
            patient_id="HN1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Limbus AI Inc.",
            organs=["Prostate", "Bladder"],
            filename="normal.dcm",
        )
        # Vendor "E" — same FoR, different PatientID (anonymised)
        _write_rtstruct(
            folder,
            patient_id="ZZZ_AC_HN1",  # the smoking gun
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="E",
            organs=["Prostate", "Rectum"],
            filename="ZZZ_AC_HN1_E.dcm",
        )

        lib = MetadataLibrary()
        lib.scan_folder(str(folder))

        # Only one patient should remain — HN1 — with both RTSTRUCTs inside it
        assert set(lib.patients) == {"HN1"}
        patient = lib.patients["HN1"]
        assert "ZZZ_AC_HN1" in patient.merged_aliases
        assert len(patient.contexts) == 1
        ctx = patient.contexts[0]
        assert len(ctx.rtstructs) == 2
        sources = sorted(r.source_label for r in ctx.rtstructs)
        assert sources == ["E", "Limbus AI Inc."]

        # And an issue should be reported describing the merge
        merge_issues = [i for i in lib.issues if "Anonymisation mismatch" in i.message]
        assert len(merge_issues) == 1
        assert "ZZZ_AC_HN1" in merge_issues[0].message
        assert "HN1" in merge_issues[0].message


def test_merge_picks_patient_with_images_as_canonical():
    """If multiple PatientIDs share a FoR, the one with image series wins."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        study_uid = generate_uid()
        for_uid = generate_uid()

        # Alphabetically the 'AAA_' patient sorts first, but only 'HN1' has the CT.
        _write_ct_slice(
            folder, patient_id="HN1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid
        )
        _write_rtstruct(
            folder,
            patient_id="AAA_alias",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Vendor",
            organs=["Lung"],
        )

        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        assert set(lib.patients) == {"HN1"}
        assert "AAA_alias" in lib.patients["HN1"].merged_aliases


def test_merge_is_transitive_across_multiple_aliases():
    """Three PatientIDs sharing a FoR collapse into one canonical entry."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        study_uid = generate_uid()
        for_uid = generate_uid()

        _write_ct_slice(
            folder, patient_id="HN1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid
        )
        _write_rtstruct(
            folder,
            patient_id="ZZZ_HN1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Vendor1",
            organs=["A"],
        )
        _write_rtstruct(
            folder,
            patient_id="ANON_HN1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Vendor2",
            organs=["B"],
        )
        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        assert set(lib.patients) == {"HN1"}
        assert lib.patients["HN1"].merged_aliases == {"ZZZ_HN1", "ANON_HN1"}
        assert len(lib.patients["HN1"].contexts[0].rtstructs) == 2


def test_merge_does_not_collapse_unrelated_patients():
    """Distinct FoR UIDs across patients must NOT merge."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for_a = generate_uid()
        for_b = generate_uid()
        _write_ct_slice(
            folder,
            patient_id="HN1",
            study_uid=generate_uid(),
            series_uid=generate_uid(),
            for_uid=for_a,
        )
        _write_ct_slice(
            folder,
            patient_id="HN2",
            study_uid=generate_uid(),
            series_uid=generate_uid(),
            for_uid=for_b,
        )
        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        assert set(lib.patients) == {"HN1", "HN2"}
        assert not lib.patients["HN1"].merged_aliases
        assert not lib.patients["HN2"].merged_aliases


def test_summary_aggregates_correctly():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for_uid = generate_uid()
        study_uid = generate_uid()
        _write_ct_slice(folder, patient_id="P1", study_uid=study_uid, series_uid=generate_uid(), for_uid=for_uid)
        _write_rtstruct(
            folder,
            patient_id="P1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Varian",
            organs=["Prostate", "Bladder"],
        )
        _write_rtstruct(
            folder,
            patient_id="P1",
            study_uid=study_uid,
            for_uid=for_uid,
            manufacturer="Limbus AI",
            organs=["Prostate", "Rectum"],  # Prostate is duplicated
        )
        lib = MetadataLibrary()
        lib.scan_folder(str(folder))
        s = lib.summary()
        assert s["patients"] == 1
        assert s["rtstructs"] == 2
        assert s["total_organs"] == 4  # 2 + 2
        assert s["unique_organs"] == 3  # Prostate, Bladder, Rectum
        assert set(s["sources"]) == {"Varian", "Limbus AI"}
