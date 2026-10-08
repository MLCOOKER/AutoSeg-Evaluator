"""Tests for explicit-reference linking (``autoseg_evaluator.data.linkage``).

The scenario these exist for is the one a reviewer raised against v2: a single
``FrameOfReferenceUID`` can hold several imaging studies, structure sets and
dose distributions, which happens routinely in re-irradiation and replan data.
v2 resolved those links by walking until it found something and taking it, so
the wrong CT or the wrong dose could be used with no warning at all.

Every fixture here builds real (minimal) DICOM files and runs the full folder
scan, so the reference sequences are exercised through ``pydicom`` exactly as
they would be on clinical data.
"""

from __future__ import annotations

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

from autoseg_evaluator.core.masks import find_reference_image_folder
from autoseg_evaluator.data.linkage import (
    KIND_DOSE,
    KIND_SERIES,
    TIER_EXPLICIT,
    TIER_FOR,
    TIER_FOR_STUDY,
    TIER_OVERRIDE,
    TIER_SINGLETON,
    TIER_SOP_OVERLAP,
    collect_link_issues,
    override_key,
    resolve_dose,
    resolve_image_series,
)
from autoseg_evaluator.data.metadata import MetadataLibrary

# ---- Fixture writers ------------------------------------------------------


def _meta(sop_class_uid: str, sop_instance_uid: str) -> FileMetaDataset:
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


def _write_ct_series(
    folder: Path,
    *,
    patient_id: str,
    study_uid: str,
    for_uid: str,
    slices: int = 3,
    series_uid: str | None = None,
) -> tuple[str, list[str]]:
    """Write a CT series; returns ``(series_uid, [sop_uids])``."""
    series_uid = series_uid or generate_uid()
    sop_uids: list[str] = []
    folder.mkdir(parents=True, exist_ok=True)
    for _ in range(slices):
        sop_uid = generate_uid()
        ds = FileDataset(
            str(folder / f"{sop_uid}.dcm"),
            {},
            file_meta=_meta(CTImageStorage, sop_uid),
            preamble=b"\0" * 128,
        )
        ds.PatientID = patient_id
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.SOPInstanceUID = sop_uid
        ds.SOPClassUID = CTImageStorage
        ds.FrameOfReferenceUID = for_uid
        ds.Modality = "CT"
        ds.StudyDate = "20260101"
        _write(ds, folder / f"{sop_uid}.dcm")
        sop_uids.append(sop_uid)
    return series_uid, sop_uids


def _write_rtstruct(
    folder: Path,
    *,
    patient_id: str,
    study_uid: str,
    for_uid: str,
    label: str = "",
    ref_series_uid: str | None = None,
    ref_image_sops: list[str] | None = None,
    ref_study_uid: str | None = None,
    sop_uid: str | None = None,
) -> str:
    """Write an RTSTRUCT; returns its SOPInstanceUID.

    ``ref_series_uid`` / ``ref_image_sops`` populate the
    ``RTReferencedSeriesSequence`` and its ``ContourImageSequence``. Leaving
    both out produces a structure set that carries only a Frame of Reference —
    the shape that forces the resolver down to its weakest tier.
    """
    sop_uid = sop_uid or generate_uid()
    folder.mkdir(parents=True, exist_ok=True)
    ds = FileDataset(
        str(folder / f"{sop_uid}.dcm"),
        {},
        file_meta=_meta(RTStructureSetStorage, sop_uid),
        preamble=b"\0" * 128,
    )
    ds.PatientID = patient_id
    ds.StudyInstanceUID = study_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = RTStructureSetStorage
    ds.Modality = "RTSTRUCT"
    ds.SeriesInstanceUID = generate_uid()
    if label:
        ds.StructureSetLabel = label

    for_item = Dataset()
    for_item.FrameOfReferenceUID = for_uid
    if ref_series_uid or ref_image_sops:
        series_item = Dataset()
        if ref_series_uid:
            series_item.SeriesInstanceUID = ref_series_uid
        if ref_image_sops:
            images = []
            for image_sop in ref_image_sops:
                image_item = Dataset()
                image_item.ReferencedSOPClassUID = CTImageStorage
                image_item.ReferencedSOPInstanceUID = image_sop
                images.append(image_item)
            series_item.ContourImageSequence = images
        study_item = Dataset()
        study_item.ReferencedSOPInstanceUID = ref_study_uid or study_uid
        study_item.RTReferencedSeriesSequence = [series_item]
        for_item.RTReferencedStudySequence = [study_item]
    ds.ReferencedFrameOfReferenceSequence = [for_item]

    roi = Dataset()
    roi.ROINumber = 1
    roi.ROIName = "Parotid_L"
    roi.ReferencedFrameOfReferenceUID = for_uid
    ds.StructureSetROISequence = [roi]

    _write(ds, folder / f"{sop_uid}.dcm")
    return sop_uid


def _write_rtdose(
    folder: Path,
    *,
    patient_id: str,
    study_uid: str,
    for_uid: str,
    summation: str = "PLAN",
    ref_structure_set_uid: str | None = None,
    ref_plan_uid: str | None = None,
    nest_structure_ref_in_plan: bool = False,
    sop_uid: str | None = None,
    filename: str | None = None,
) -> str:
    """Write an RTDOSE; returns its SOPInstanceUID."""
    sop_uid = sop_uid or generate_uid()
    folder.mkdir(parents=True, exist_ok=True)
    name = filename or f"{sop_uid}.dcm"
    ds = FileDataset(
        str(folder / name),
        {},
        file_meta=_meta(RTDoseStorage, sop_uid),
        preamble=b"\0" * 128,
    )
    ds.PatientID = patient_id
    ds.StudyInstanceUID = study_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = RTDoseStorage
    ds.Modality = "RTDOSE"
    ds.FrameOfReferenceUID = for_uid
    ds.SeriesInstanceUID = generate_uid()
    ds.DoseSummationType = summation

    if ref_structure_set_uid and not nest_structure_ref_in_plan:
        struct_item = Dataset()
        struct_item.ReferencedSOPClassUID = RTStructureSetStorage
        struct_item.ReferencedSOPInstanceUID = ref_structure_set_uid
        ds.ReferencedStructureSetSequence = [struct_item]

    if ref_plan_uid or nest_structure_ref_in_plan:
        plan_item = Dataset()
        plan_item.ReferencedSOPInstanceUID = ref_plan_uid or generate_uid()
        if nest_structure_ref_in_plan and ref_structure_set_uid:
            nested = Dataset()
            nested.ReferencedSOPInstanceUID = ref_structure_set_uid
            plan_item.ReferencedStructureSetSequence = [nested]
        ds.ReferencedRTPlanSequence = [plan_item]

    _write(ds, folder / name)
    return sop_uid


def _scan(folder: Path) -> MetadataLibrary:
    lib = MetadataLibrary()
    lib.scan_folder(str(folder))
    return lib


# ---- RTSTRUCT to image series --------------------------------------------


def test_explicit_series_reference_beats_first_match():
    """Two CT series share a FoR; the RTSTRUCT names the second one."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        series_a, _ = _write_ct_series(
            root / "ct_a", patient_id="P1", study_uid=study, for_uid=for_uid
        )
        series_b, _ = _write_ct_series(
            root / "ct_b", patient_id="P1", study_uid=study, for_uid=for_uid
        )
        rtss = _write_rtstruct(
            root,
            patient_id="P1",
            study_uid=study,
            for_uid=for_uid,
            ref_series_uid=series_b,
        )
        lib = _scan(root)

        res = resolve_image_series(lib, "P1", rtss)
        assert res.tier == TIER_EXPLICIT
        assert res.is_resolved
        assert res.target.series_instance_uid == series_b
        assert series_a != series_b
        # The public helper agrees and points at the right folder on disk.
        assert Path(find_reference_image_folder(lib, "P1", rtss)).name == "ct_b"


def test_sop_overlap_resolves_when_series_uid_absent():
    """No RTReferencedSeries UID, but the per-slice image UIDs identify it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        _write_ct_series(root / "ct_a", patient_id="P1", study_uid=study, for_uid=for_uid)
        series_b, sops_b = _write_ct_series(
            root / "ct_b", patient_id="P1", study_uid=study, for_uid=for_uid
        )
        rtss = _write_rtstruct(
            root,
            patient_id="P1",
            study_uid=study,
            for_uid=for_uid,
            ref_image_sops=sops_b[:2],
        )
        lib = _scan(root)

        res = resolve_image_series(lib, "P1", rtss)
        assert res.tier == TIER_SOP_OVERLAP
        assert res.target.series_instance_uid == series_b


def test_two_series_one_for_is_ambiguous_not_guessed():
    """The v2 failure mode: two candidate series, no reference to separate them.

    The structure set carries its own StudyInstanceUID (what AI vendors emit),
    so the for+study tier cannot break the tie either.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        _write_ct_series(root / "ct_a", patient_id="P1", study_uid=study, for_uid=for_uid)
        _write_ct_series(root / "ct_b", patient_id="P1", study_uid=study, for_uid=for_uid)
        rtss = _write_rtstruct(root, patient_id="P1", study_uid=generate_uid(), for_uid=for_uid)
        lib = _scan(root)

        res = resolve_image_series(lib, "P1", rtss)
        assert res.tier == TIER_FOR
        assert res.is_ambiguous
        assert len(res.candidates) == 2
        assert not res.is_resolved
        # v2 returned the first series here; refusing to answer is the fix.
        assert find_reference_image_folder(lib, "P1", rtss) is None


def test_for_plus_study_disambiguates_two_series():
    """Two series in one FoR, but only one shares the structure set's study."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid = generate_uid()
        study_a, study_b = generate_uid(), generate_uid()
        series_a, _ = _write_ct_series(
            root / "ct_a", patient_id="P1", study_uid=study_a, for_uid=for_uid
        )
        _write_ct_series(root / "ct_b", patient_id="P1", study_uid=study_b, for_uid=for_uid)
        rtss = _write_rtstruct(root, patient_id="P1", study_uid=study_a, for_uid=for_uid)
        lib = _scan(root)

        res = resolve_image_series(lib, "P1", rtss)
        assert res.tier == TIER_FOR_STUDY
        assert res.target.series_instance_uid == series_a


def test_single_series_still_resolves_unchanged():
    """The ordinary single-CT case must behave exactly as it did in v2."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        _write_ct_series(root / "ct", patient_id="P1", study_uid=study, for_uid=for_uid)
        rtss = _write_rtstruct(root, patient_id="P1", study_uid=study, for_uid=for_uid)
        lib = _scan(root)

        res = resolve_image_series(lib, "P1", rtss)
        assert res.is_resolved
        assert Path(find_reference_image_folder(lib, "P1", rtss)).name == "ct"


def test_vendor_rtstruct_with_fresh_study_uid_still_links():
    """AI vendors reissue StudyInstanceUID; the FoR tier must still carry it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid = generate_uid()
        _write_ct_series(root / "ct", patient_id="P1", study_uid=generate_uid(), for_uid=for_uid)
        rtss = _write_rtstruct(
            root, patient_id="P1", study_uid=generate_uid(), for_uid=for_uid, label="VendorAI"
        )
        lib = _scan(root)

        res = resolve_image_series(lib, "P1", rtss)
        assert res.is_resolved
        assert res.tier == TIER_FOR


# ---- RTSTRUCT to dose -----------------------------------------------------


def _build_reirradiation(
    root: Path, *, explicit: bool, same_study: bool = False
) -> tuple[str, str, str, str]:
    """Two courses inside ONE Frame of Reference — the reviewer's scenario.

    With ``same_study=True`` both courses also share a StudyInstanceUID (a
    replan filed under the original study), which is the shape where every
    automatic tier runs out and the only correct answer is to ask the user.

    Returns ``(rtss_1, dose_1, rtss_2, dose_2)`` SOP UIDs.
    """
    for_uid = generate_uid()
    study_1 = generate_uid()
    study_2 = study_1 if same_study else generate_uid()

    series_1, sops_1 = _write_ct_series(
        root / "course1", patient_id="P1", study_uid=study_1, for_uid=for_uid
    )
    rtss_1 = _write_rtstruct(
        root / "course1",
        patient_id="P1",
        study_uid=study_1,
        for_uid=for_uid,
        label="Course1",
        ref_series_uid=series_1 if explicit else None,
        ref_image_sops=sops_1 if explicit else None,
    )
    dose_1 = _write_rtdose(
        root / "course1",
        patient_id="P1",
        study_uid=study_1,
        for_uid=for_uid,
        ref_structure_set_uid=rtss_1 if explicit else None,
    )

    series_2, sops_2 = _write_ct_series(
        root / "course2", patient_id="P1", study_uid=study_2, for_uid=for_uid
    )
    rtss_2 = _write_rtstruct(
        root / "course2",
        patient_id="P1",
        study_uid=study_2,
        for_uid=for_uid,
        label="Course2",
        ref_series_uid=series_2 if explicit else None,
        ref_image_sops=sops_2 if explicit else None,
    )
    dose_2 = _write_rtdose(
        root / "course2",
        patient_id="P1",
        study_uid=study_2,
        for_uid=for_uid,
        ref_structure_set_uid=rtss_2 if explicit else None,
    )
    return rtss_1, dose_1, rtss_2, dose_2


def test_reirradiation_each_structure_set_gets_its_own_dose():
    """Two courses, one FoR, two PLAN doses — each RTSTRUCT pairs correctly.

    This is the case v2 got wrong: ``_load_dose`` keyed on PatientID alone and
    returned the first PLAN dose it walked to, for both courses.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rtss_1, dose_1, rtss_2, dose_2 = _build_reirradiation(root, explicit=True)
        lib = _scan(root)

        res_1 = resolve_dose(lib, "P1", rtss_1)
        res_2 = resolve_dose(lib, "P1", rtss_2)
        assert res_1.tier == TIER_EXPLICIT and res_2.tier == TIER_EXPLICIT
        assert res_1.target.sop_instance_uid == dose_1
        assert res_2.target.sop_instance_uid == dose_2
        assert dose_1 != dose_2

        # Each course also resolves to its own CT, not the first one scanned.
        folder_1 = Path(find_reference_image_folder(lib, "P1", rtss_1)).name
        folder_2 = Path(find_reference_image_folder(lib, "P1", rtss_2)).name
        assert {folder_1, folder_2} == {"course1", "course2"}
        assert folder_1 != folder_2


def test_reirradiation_without_references_is_ambiguous():
    """Same two courses, but no explicit references: refuse rather than guess."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rtss_1, _, rtss_2, _ = _build_reirradiation(root, explicit=False, same_study=True)
        lib = _scan(root)

        for rtss in (rtss_1, rtss_2):
            dres = resolve_dose(lib, "P1", rtss)
            assert dres.is_ambiguous, "two PLAN doses in one FoR must not auto-resolve"
            assert len(dres.candidates) == 2


def test_reirradiation_with_distinct_studies_resolves_without_references():
    """Separate studies per course are enough — the for+study tier settles it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rtss_1, dose_1, rtss_2, dose_2 = _build_reirradiation(root, explicit=False)
        lib = _scan(root)

        assert resolve_dose(lib, "P1", rtss_1).target.sop_instance_uid == dose_1
        assert resolve_dose(lib, "P1", rtss_2).target.sop_instance_uid == dose_2
        assert collect_link_issues(lib) == []


def test_nested_structure_reference_inside_plan_sequence_is_read():
    """Some writers nest ReferencedStructureSetSequence inside the plan ref.

    Reading it is still plan-file-free — the UID sits in the dose object.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        series, sops = _write_ct_series(root, patient_id="P1", study_uid=study, for_uid=for_uid)
        rtss = _write_rtstruct(
            root,
            patient_id="P1",
            study_uid=study,
            for_uid=for_uid,
            ref_series_uid=series,
            ref_image_sops=sops,
        )
        other = _write_rtdose(root, patient_id="P1", study_uid=study, for_uid=for_uid)
        dose = _write_rtdose(
            root,
            patient_id="P1",
            study_uid=study,
            for_uid=for_uid,
            ref_structure_set_uid=rtss,
            nest_structure_ref_in_plan=True,
        )
        lib = _scan(root)

        res = resolve_dose(lib, "P1", rtss)
        assert res.tier == TIER_EXPLICIT
        assert res.target.sop_instance_uid == dose
        assert res.target.sop_instance_uid != other
        # The plan UID is recorded but never used to resolve anything.
        assert res.target.referenced_plan_uids


def test_plan_summation_breaks_a_tie_within_one_tier():
    """v2 preferred PLAN over BEAM; that preference is retained as a tie-break."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        _write_ct_series(root, patient_id="P1", study_uid=study, for_uid=for_uid)
        rtss = _write_rtstruct(root, patient_id="P1", study_uid=study, for_uid=for_uid)
        _write_rtdose(root, patient_id="P1", study_uid=study, for_uid=for_uid, summation="BEAM")
        plan_dose = _write_rtdose(
            root, patient_id="P1", study_uid=study, for_uid=for_uid, summation="PLAN"
        )
        lib = _scan(root)

        res = resolve_dose(lib, "P1", rtss)
        assert res.is_resolved
        assert res.target.sop_instance_uid == plan_dose


def test_single_dose_resolves_even_across_frames_of_reference():
    """One dose and nothing to contradict it: keep v2's permissive behaviour."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        study = generate_uid()
        rtss_for, dose_for = generate_uid(), generate_uid()
        _write_ct_series(root, patient_id="P1", study_uid=study, for_uid=rtss_for)
        rtss = _write_rtstruct(root, patient_id="P1", study_uid=study, for_uid=rtss_for)
        dose = _write_rtdose(root, patient_id="P1", study_uid=study, for_uid=dose_for)
        lib = _scan(root)

        res = resolve_dose(lib, "P1", rtss)
        assert res.tier == TIER_SINGLETON
        assert res.target.sop_instance_uid == dose


# ---- Overrides ------------------------------------------------------------


def test_user_override_wins_over_every_automatic_tier():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        series_a, _ = _write_ct_series(
            root / "ct_a", patient_id="P1", study_uid=study, for_uid=for_uid
        )
        series_b, sops_b = _write_ct_series(
            root / "ct_b", patient_id="P1", study_uid=study, for_uid=for_uid
        )
        rtss = _write_rtstruct(
            root,
            patient_id="P1",
            study_uid=study,
            for_uid=for_uid,
            ref_series_uid=series_b,
            ref_image_sops=sops_b,
        )
        lib = _scan(root)
        assert resolve_image_series(lib, "P1", rtss).target.series_instance_uid == series_b

        lib.link_overrides[override_key("P1", rtss, KIND_SERIES)] = series_a
        res = resolve_image_series(lib, "P1", rtss)
        assert res.tier == TIER_OVERRIDE
        assert res.target.series_instance_uid == series_a


def test_stale_override_falls_through_instead_of_failing():
    """An override naming absent data must not brick a reloaded session."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        _write_ct_series(root / "ct", patient_id="P1", study_uid=study, for_uid=for_uid)
        rtss = _write_rtstruct(root, patient_id="P1", study_uid=study, for_uid=for_uid)
        lib = _scan(root)

        lib.link_overrides[override_key("P1", rtss, KIND_SERIES)] = generate_uid()
        res = resolve_image_series(lib, "P1", rtss)
        assert res.is_resolved
        assert res.tier != TIER_OVERRIDE


# ---- Ingest hygiene -------------------------------------------------------


def test_duplicate_sop_instance_uid_is_ingested_once():
    """A dose exported to two paths is one object, not two dose candidates."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        _write_ct_series(root, patient_id="P1", study_uid=study, for_uid=for_uid)
        rtss = _write_rtstruct(root, patient_id="P1", study_uid=study, for_uid=for_uid)
        shared_sop = generate_uid()
        for name in ("dose_copy_a.dcm", "dose_copy_b.dcm"):
            _write_rtdose(
                root,
                patient_id="P1",
                study_uid=study,
                for_uid=for_uid,
                sop_uid=shared_sop,
                filename=name,
            )
        lib = _scan(root)

        doses = [d for ctx in lib.patients["P1"].contexts for d in ctx.rtdoses]
        assert len(doses) == 1, "same SOPInstanceUID twice must not become two doses"
        assert resolve_dose(lib, "P1", rtss).is_resolved


# ---- Linkage components ---------------------------------------------------


def test_linkage_ids_separate_the_two_courses():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rtss_1, dose_1, rtss_2, dose_2 = _build_reirradiation(root, explicit=True)
        lib = _scan(root)

        by_uid = {}
        for ctx in lib.patients["P1"].contexts:
            for entry in list(ctx.rtstructs) + list(ctx.rtdoses):
                by_uid[entry.sop_instance_uid] = entry.linkage_id

        assert by_uid[rtss_1] == by_uid[dose_1]
        assert by_uid[rtss_2] == by_uid[dose_2]
        assert by_uid[rtss_1] != by_uid[rtss_2], "two courses must not share a linkage"
        assert by_uid[rtss_1].startswith("link:")


# ---- Issue collection -----------------------------------------------------


def test_collect_link_issues_reports_both_kinds():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _build_reirradiation(root, explicit=False, same_study=True)
        lib = _scan(root)

        issues = collect_link_issues(lib)
        kinds = {i.kind for i in issues}
        assert KIND_SERIES in kinds
        assert KIND_DOSE in kinds
        assert all(i.severity == "error" for i in issues)
        # Every ambiguity offers the user the candidates to choose between.
        for issue in issues:
            assert len(issue.candidates) == 2

        # With DVH metrics switched off the dose links are not worth blocking.
        no_dose = collect_link_issues(lib, include_dose=False)
        assert {i.kind for i in no_dose} == {KIND_SERIES}


def test_clean_dataset_produces_no_issues():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for_uid, study = generate_uid(), generate_uid()
        series, sops = _write_ct_series(root, patient_id="P1", study_uid=study, for_uid=for_uid)
        rtss = _write_rtstruct(
            root,
            patient_id="P1",
            study_uid=study,
            for_uid=for_uid,
            ref_series_uid=series,
            ref_image_sops=sops,
        )
        _write_rtdose(
            root,
            patient_id="P1",
            study_uid=study,
            for_uid=for_uid,
            ref_structure_set_uid=rtss,
        )
        lib = _scan(root)

        assert collect_link_issues(lib) == []


def test_resolving_an_ambiguity_clears_its_issue():
    """The Tab 1 gate: choosing a candidate must make the blocker go away."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rtss_1, dose_1, rtss_2, dose_2 = _build_reirradiation(root, explicit=False, same_study=True)
        lib = _scan(root)
        assert collect_link_issues(lib)

        series_uids = [
            s.series_instance_uid for ctx in lib.patients["P1"].contexts for s in ctx.image_series
        ]
        for rtss, dose in ((rtss_1, dose_1), (rtss_2, dose_2)):
            lib.link_overrides[override_key("P1", rtss, KIND_DOSE)] = dose
        for rtss, series_uid in zip((rtss_1, rtss_2), series_uids, strict=False):
            lib.link_overrides[override_key("P1", rtss, KIND_SERIES)] = series_uid

        assert collect_link_issues(lib) == []
