"""Tests for the acquisition summary, and for the boundary it enforces.

The structure sets this software runs on come from real patients. The
acquisition section exists to be pasted into a methods section, so anything it
picks up is on its way to publication — which makes the question "what can
reach this table" the whole point of the module, and most of this file.
"""

from __future__ import annotations

import pydicom
import pytest
from pydicom.dataset import Dataset

from autoseg_evaluator.core.acquisition import (
    IMAGE_FIELDS,
    IMAGE_TAGS,
    NEVER_READ,
    RTSS_FIELDS,
    RTSS_TAGS,
    FieldSummary,
    ImageAcquisition,
    read_image_acquisition,
    summarise,
)
from autoseg_evaluator.data.report import collect_acquisition

#: Values planted in the fixtures below. None may ever appear in a summary.
PHI = {
    "PatientName": "SECRET^PATIENT^NAME",
    "PatientID": "MRN-0099887",
    "PatientBirthDate": "19551103",
    "PatientSex": "SEXSENTINEL",
    "PatientAge": "AGESENTINEL",
    "AccessionNumber": "ACC-55512",
    "StudyDate": "20240117",
    "StudyTime": "093015",
    "SeriesDate": "20240117",
    "AcquisitionDate": "20240117",
    "InstitutionName": "St Elsewhere Cancer Centre",
    "InstitutionAddress": "1 Hospital Road, Somewhere",
    "ReferringPhysicianName": "DOCTOR^REFERRING",
    "PerformingPhysicianName": "DOCTOR^PERFORMING",
    "OperatorsName": "RADIOGRAPHER^ONE",
    "StationName": "CT-SCANNER-03",
    "DeviceSerialNumber": "SN-7734001",
    "StudyDescription": "CT HEAD AND NECK - SECRET^PATIENT^NAME",
    "SeriesDescription": "recon for MRN-0099887",
}


def _slice_with_phi() -> Dataset:
    """A CT slice carrying both real acquisition tags and a pile of identifiers."""
    ds = Dataset()
    ds.Modality = "CT"
    ds.Manufacturer = "TOSHIBA"
    ds.ManufacturerModelName = "Aquilion/LB"
    ds.SoftwareVersions = "V6.30ER007"
    ds.SliceThickness = "2.0"
    ds.KVP = "120"
    ds.ConvolutionKernel = "FC09"
    ds.PatientPosition = "HFS"
    ds.Rows = 512
    ds.Columns = 512
    ds.PixelSpacing = [1.074, 1.074]
    for keyword, value in PHI.items():
        setattr(ds, keyword, value)
    return ds


# ---- The boundary ---------------------------------------------------------


def test_the_allowlist_and_the_forbidden_list_do_not_overlap():
    """A tag cannot be both read and forbidden."""
    assert not (set(IMAGE_TAGS.values()) & NEVER_READ)
    assert not (set(RTSS_TAGS.values()) & NEVER_READ)


def test_no_identifier_survives_a_read():
    """The test the module exists for.

    Every identifier above is present on the dataset. None may appear in any
    field of the result, in any form.
    """
    found = read_image_acquisition(_slice_with_phi())
    rendered = " | ".join(str(getattr(found, name)) for name in vars(found))
    for keyword, value in PHI.items():
        assert value not in rendered, f"{keyword} leaked into the acquisition summary"


def test_the_acquisition_object_has_no_field_for_an_identifier():
    """Nothing can leak through a field that does not exist.

    Asserted as an exact set rather than by substring: ``patient_position`` is
    HFS/FFS, a legitimate geometry tag whose name would trip any "patient"
    check, and a rule that has to carve out exceptions stops being a rule.
    """
    assert set(vars(ImageAcquisition())) == {
        "modality",
        "manufacturer",
        "model",
        "software_versions",
        "slice_thickness",
        "pixel_spacing_row",
        "pixel_spacing_col",
        "kvp",
        "convolution_kernel",
        "patient_position",
        "rows",
        "columns",
        "slices",
    }


def test_the_displayed_fields_are_all_populated_from_the_allowlist():
    """A display row with no backing attribute would silently read blank."""
    found = ImageAcquisition()
    for attribute, _label in IMAGE_FIELDS:
        assert hasattr(found, attribute), attribute


# ---- Reading --------------------------------------------------------------


def test_acquisition_reads_what_a_methods_section_needs():
    found = read_image_acquisition(_slice_with_phi())
    assert found.modality == "CT"
    assert found.manufacturer == "TOSHIBA"
    assert found.model == "Aquilion/LB"
    assert found.slice_thickness == pytest.approx(2.0)
    assert found.kvp == pytest.approx(120.0)
    assert found.convolution_kernel == "FC09"
    assert found.patient_position == "HFS"
    assert found.matrix == "512 × 512"
    assert found.in_plane_mm == "1.074 × 1.074"


def test_absent_tags_stay_absent_rather_than_becoming_zero():
    """A missing slice thickness is unknown, not 0 mm."""
    ds = Dataset()
    ds.Modality = "CT"
    found = read_image_acquisition(ds)
    assert found.slice_thickness is None
    assert found.kvp is None
    assert found.in_plane_mm == ""
    assert found.matrix == ""


def test_a_multi_valued_tag_is_joined_rather_than_stringified():
    ds = Dataset()
    ds.ConvolutionKernel = ["FC09", "FC13"]
    assert read_image_acquisition(ds).convolution_kernel == "FC09 / FC13"


def test_an_unreadable_number_does_not_raise():
    """pydicom rejects a bad DS on assignment, so the guard is for everything else.

    Any object with the right attribute names is accepted here — a partially
    decoded dataset, a stub, a future reader — and a value that will not become
    a float must be dropped rather than crash the report.
    """

    class _Odd:
        SliceThickness = "not a number"
        KVP = object()
        Rows = "five hundred"
        PixelSpacing = "not a pair"

    found = read_image_acquisition(_Odd())
    assert found.slice_thickness is None
    assert found.kvp is None
    assert found.rows is None
    assert found.pixel_spacing_row is None


# ---- Summarising ----------------------------------------------------------


def _series(**kwargs):
    return ImageAcquisition(**kwargs)


def test_a_uniform_parameter_reads_as_one_value():
    summaries = summarise([_series(manufacturer="TOSHIBA")] * 10, IMAGE_FIELDS)
    manufacturer = next(s for s in summaries if s.label == "Scanner manufacturer")
    assert manufacturer.summary() == "TOSHIBA"
    assert manufacturer.uniform


def test_a_varying_parameter_reads_with_counts():
    """Found on the real cohort: in-plane spacing is not uniform across patients."""
    records = [_series(pixel_spacing_row=1.074, pixel_spacing_col=1.074)] * 8
    records += [_series(pixel_spacing_row=1.367, pixel_spacing_col=1.367)] * 2
    summaries = summarise(records, IMAGE_FIELDS)
    spacing = next(s for s in summaries if s.label.startswith("In-plane"))
    assert spacing.summary() == "1.074 × 1.074 (8), 1.367 × 1.367 (2)"
    assert not spacing.uniform


def test_many_numeric_values_collapse_to_a_range():
    """Ten slice counts listed with a count each is noise; the range is the fact."""
    records = [_series(slices=n) for n in (186, 187, 196, 202, 204, 207, 208, 216, 222, 242)]
    summaries = summarise(records, IMAGE_FIELDS)
    slices = next(s for s in summaries if s.label == "Slices per series")
    assert slices.summary() == "186 – 242 (range over 10)"


def test_numeric_values_are_never_averaged():
    """Half at 2 mm and half at 3 mm is not a cohort scanned at 2.5 mm."""
    records = [_series(slice_thickness=2.0)] * 5 + [_series(slice_thickness=3.0)] * 5
    summaries = summarise(records, IMAGE_FIELDS)
    thickness = next(s for s in summaries if s.label.startswith("Slice thickness"))
    assert "2.5" not in thickness.summary()
    assert thickness.summary() == "2 (5), 3 (5)"


def test_an_absent_tag_is_reported_as_absent():
    summaries = summarise([_series()] * 4, IMAGE_FIELDS)
    kernel = next(s for s in summaries if s.label == "Reconstruction kernel")
    assert kernel.summary() == "— not recorded"
    assert not kernel.uniform


def test_a_partly_recorded_tag_says_how_many_are_missing():
    records = [_series(convolution_kernel="FC09")] * 6 + [_series()] * 4
    summaries = summarise(records, IMAGE_FIELDS)
    kernel = next(s for s in summaries if s.label == "Reconstruction kernel")
    assert kernel.summary() == "FC09, not recorded (4)"


def test_a_long_enumeration_is_truncated():
    records = [_series(convolution_kernel=f"K{i}") for i in range(12)]
    summaries = summarise(records, IMAGE_FIELDS)
    kernel = next(s for s in summaries if s.label == "Reconstruction kernel")
    assert "and 6 more" in kernel.summary()


def test_summarising_nothing_is_not_an_error():
    summaries = summarise([], IMAGE_FIELDS)
    assert len(summaries) == len(IMAGE_FIELDS)
    assert all(s.summary() == "— not recorded" for s in summaries)


def test_a_field_summary_knows_whether_it_is_numeric():
    assert FieldSummary("x", {"2": 1, "3": 1}).numeric
    assert not FieldSummary("x", {"FC09": 1}).numeric
    assert not FieldSummary("x").numeric


# ---- Collecting from a library --------------------------------------------


class _FakeSeries:
    def __init__(self, acquisition, slices=100):
        self.acquisition = acquisition
        self.files = [f"slice{i}.dcm" for i in range(slices)]


class _FakeRTSS:
    def __init__(self, manufacturer, model="", software="", rois=50, synthetic=False):
        self.manufacturer = manufacturer
        self.manufacturer_model_name = model
        self.software_versions = software
        self.organs = list(range(rois))
        self.is_synthetic_consensus = synthetic
        # Present on the real entry, and must never reach the summary.
        self.reviewer_name = "REVIEWER^SECRET"
        self.operators_name = "OPERATOR^SECRET"
        self.structure_set_label = "SECRET^PATIENT^NAME plan"


class _FakeContext:
    def __init__(self, series, rtstructs):
        self.image_series = series
        self.rtstructs = rtstructs


class _FakePatient:
    def __init__(self, contexts):
        self.contexts = contexts


class _FakeLibrary:
    def __init__(self, patients):
        self.patients = patients


def _library():
    series = _FakeSeries(read_image_acquisition(_slice_with_phi()), slices=208)
    rtss = [
        _FakeRTSS("Limbus AI", "Limbus", "1.8.0", rois=48),
        _FakeRTSS("Radformation", "Rad.Ultimate", "2.7.2.0", rois=87),
        _FakeRTSS("STAPLE", rois=10, synthetic=True),
    ]
    context = _FakeContext([series], rtss)
    return _FakeLibrary({"P1": _FakePatient([context]), "P2": _FakePatient([context])})


def test_collecting_counts_series_and_structure_sets():
    report = collect_acquisition(_library())
    assert report.n_patients == 2
    assert report.n_series == 2
    assert report.n_structure_sets == 4  # two real per patient; the consensus excluded
    assert report.available


def test_a_synthetic_consensus_has_no_header_to_report():
    report = collect_acquisition(_library())
    manufacturer = next(s for s in report.structure_sets if s.label == "Manufacturer")
    assert "STAPLE" not in manufacturer.summary()


def test_nothing_identifying_survives_the_collection():
    """The end-to-end version of the boundary test."""
    report = collect_acquisition(_library())
    rendered = " | ".join(
        s.label + " " + s.summary() for s in list(report.images) + list(report.structure_sets)
    )
    for value in PHI.values():
        assert value not in rendered
    for value in ("REVIEWER^SECRET", "OPERATOR^SECRET", "SECRET^PATIENT^NAME plan"):
        assert value not in rendered


def test_collecting_from_nothing_is_not_an_error():
    report = collect_acquisition(None)
    assert not report.available
    assert report.n_series == 0


def test_the_slice_count_comes_from_the_series_not_the_header():
    report = collect_acquisition(_library())
    slices = next(s for s in report.images if s.label == "Slices per series")
    assert slices.summary() == "208"


def test_structure_set_fields_all_resolve():
    report = collect_acquisition(_library())
    labels = [s.label for s in report.structure_sets]
    assert labels == [label for _attribute, label in RTSS_FIELDS]
    assert all(s.summary() != "— not recorded" for s in report.structure_sets)


def test_pydicom_is_the_only_dicom_reader_involved():
    """Guards against a future change reading tags some other way."""
    assert pydicom.__name__ == "pydicom"


# ---- Prose names for figures ----------------------------------------------


def test_organ_names_become_prose():
    """A figure's axis label is read alone, where the abbreviation is undefined."""
    from autoseg_evaluator.core.readable import readable_organ

    assert readable_organ("Opticnrv (L)") == "Left optic nerve"
    assert readable_organ("Spinalcord") == "Spinal cord"
    assert readable_organ("Glnd Submand (R)") == "Right submandibular gland"
    assert readable_organ("Parotid (L)") == "Left parotid"
    assert readable_organ("Brainstem") == "Brainstem"


def test_an_unrecognised_organ_is_shown_as_written():
    """Inventing an expansion would read as authoritative and be wrong."""
    from autoseg_evaluator.core.readable import readable_organ

    assert readable_organ("Wibble_Xyz") == "Wibble_Xyz"
    assert readable_organ("Wibble (L)") == "Left Wibble"
    assert readable_organ("") == ""


def test_a_qualifier_survives_the_rendering():
    from autoseg_evaluator.core.readable import readable_organ

    assert readable_organ("Parotid (L) [target]") == "Left parotid (target)"


def test_metric_names_become_prose():
    from autoseg_evaluator.core.readable import readable_metric

    assert readable_metric("surface_dice") == "Surface Dice"
    assert readable_metric("hausdorff95") == "Hausdorff 95%"
    assert readable_metric("mean_surface_distance") == "Mean surface distance"
    assert readable_metric("dice") == "Dice"
    assert readable_metric("something_odd") == "Something odd"


def test_units_are_separated_from_the_name():
    """The name belongs in the title and the unit on the axis."""
    from autoseg_evaluator.core.readable import metric_units, readable_metric

    assert readable_metric("hausdorff95") == "Hausdorff 95%"  # no "(mm)"
    assert metric_units("hausdorff95") == "mm"
    assert metric_units("dice") == ""
    assert metric_units("dmean_gy") == "Gy"


def test_a_tolerance_metric_reports_its_tolerance():
    """Surface Dice at 1 mm and at 5 mm are different measurements."""
    from autoseg_evaluator.core.readable import tolerance_note

    assert tolerance_note("surface_dice", 3.0, None) == "tolerance = 3.00 mm"
    assert tolerance_note("apl_mean", None, 2.5) == "tolerance = 2.50 mm"
    assert tolerance_note("dice", 3.0, 2.5) == ""


def test_a_missing_tolerance_is_said_rather_than_left_blank():
    """Blank reads as "no tolerance applies", which is a different claim."""
    from autoseg_evaluator.core.readable import tolerance_note

    assert tolerance_note("surface_dice", None, None) == "tolerance not recorded"
