"""Unit tests for the source-label cascade."""

from __future__ import annotations

from types import SimpleNamespace

from autoseg_evaluator.core.source_labels import (
    ORIGIN_CUSTOM,
    ORIGIN_FILENAME,
    ORIGIN_FOLDER,
    ORIGIN_LABEL,
    ORIGIN_MANUFACTURER,
    ORIGIN_NAME,
    ORIGIN_SOFTWARE,
    ORIGIN_UNKNOWN,
    derive_source_label,
)


def _ds(**fields):
    """Build a stand-in for a pydicom Dataset using attribute access."""
    return SimpleNamespace(**fields)


def test_custom_override_wins():
    ds = _ds(SOPInstanceUID="1.2.3", Manufacturer="Varian")
    result = derive_source_label(ds, "any.dcm", custom_overrides={"1.2.3": "MyLabel"})
    assert result.label == "MyLabel"
    assert result.origin == ORIGIN_CUSTOM


def test_manufacturer_used_when_present():
    ds = _ds(Manufacturer="Limbus AI", StructureSetLabel="ignored")
    result = derive_source_label(ds, "any.dcm")
    assert result.label == "Limbus AI"
    assert result.origin == ORIGIN_MANUFACTURER


def test_falls_through_to_structure_set_label():
    ds = _ds(Manufacturer="", StructureSetLabel="AutoContour v3")
    result = derive_source_label(ds, "any.dcm")
    assert result.label == "AutoContour v3"
    assert result.origin == ORIGIN_LABEL


def test_falls_through_to_software_versions():
    ds = _ds(SoftwareVersions="Eclipse 16.1")
    result = derive_source_label(ds, "any.dcm")
    assert result.label == "Eclipse 16.1"
    assert result.origin == ORIGIN_SOFTWARE


def test_software_versions_can_be_list():
    ds = _ds(SoftwareVersions=["Eclipse", "16.1"])
    result = derive_source_label(ds, "any.dcm")
    assert "Eclipse" in result.label
    assert "16.1" in result.label
    assert result.origin == ORIGIN_SOFTWARE


def test_falls_through_to_structure_set_name():
    ds = _ds(StructureSetName="Approved Plan A")
    result = derive_source_label(ds, "any.dcm")
    assert result.label == "Approved Plan A"
    assert result.origin == ORIGIN_NAME


def test_falls_back_to_filename_stem():
    ds = _ds()  # all DICOM tags absent
    result = derive_source_label(ds, "/some/dir/inhouse_unet_v2.dcm")
    assert result.label == "inhouse_unet_v2"
    assert result.origin == ORIGIN_FILENAME


def test_falls_back_to_folder_name_when_no_stem():
    # A bare ".dcm" has empty stem — folder name should be used instead.
    ds = _ds()
    result = derive_source_label(ds, "/data/AI_VendorX/.dcm")
    assert result.label == "AI_VendorX"
    assert result.origin == ORIGIN_FOLDER


def test_unknown_when_everything_empty():
    ds = _ds()
    result = derive_source_label(ds, "")
    assert result.label == "Unknown"
    assert result.origin == ORIGIN_UNKNOWN


def test_whitespace_only_manufacturer_is_skipped():
    ds = _ds(Manufacturer="   ", StructureSetLabel="RealLabel")
    result = derive_source_label(ds, "any.dcm")
    assert result.label == "RealLabel"
    assert result.origin == ORIGIN_LABEL


def test_none_dataset_uses_filename():
    result = derive_source_label(None, "/x/y/manual.dcm")
    assert result.label == "manual"
    assert result.origin == ORIGIN_FILENAME
