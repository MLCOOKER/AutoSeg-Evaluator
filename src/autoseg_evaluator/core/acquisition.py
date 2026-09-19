"""Acquisition parameters for the report's methods section.

A published comparison of auto-contouring models has to say what it was run on:
scanner, reconstruction, voxel size. Reviewers ask for it, and a Dice difference
means something different at 1 mm slices than at 5 mm.

**This module is the PHI boundary, and it is an allowlist.** Only the tags named
in :data:`IMAGE_TAGS` and :data:`RTSS_TAGS` are ever read, and every one of them
describes equipment or geometry rather than a person. The structure sets this
software is used on come from real patients, anonymised but real, so a denylist
would be the wrong shape: it leaks whatever nobody thought of, and it leaks it
into a document intended for publication.

Three categories are deliberately absent, and :data:`NEVER_READ` names them so a
test can prove it:

*Direct identifiers* — name, ID, birth date, accession number, and every UID.

*Quasi-identifiers* — dates and times of any kind, age, sex, institution,
station name, device serial number. Each is defensible alone and a
re-identification vector in combination, and the cohorts here are ten patients
deep, where combination is easy.

*Free text* — study and series descriptions, structure set labels, comments.
These are meant to hold technical detail and routinely hold names instead.

Note that the metadata library itself *does* read two operator-name fields for
the multi-observer consensus workflow (``reviewer_name``, ``operators_name``).
They are deliberately not reachable from here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass, field
from typing import Any

# ---- The allowlist ---------------------------------------------------------

#: ``attribute -> DICOM keyword`` read from one slice of each image series.
#: Equipment and geometry only.
IMAGE_TAGS: dict[str, str] = {
    "modality": "Modality",
    "manufacturer": "Manufacturer",
    "model": "ManufacturerModelName",
    "software_versions": "SoftwareVersions",
    "slice_thickness": "SliceThickness",
    "kvp": "KVP",
    "convolution_kernel": "ConvolutionKernel",
    "patient_position": "PatientPosition",
    "rows": "Rows",
    "columns": "Columns",
}

#: Read from the structure set header. The metadata library already captures
#: these for source labelling; they are re-listed here so this module's
#: allowlist is the complete statement of what reaches the report.
RTSS_TAGS: dict[str, str] = {
    "manufacturer": "Manufacturer",
    "model": "ManufacturerModelName",
    "software_versions": "SoftwareVersions",
}

#: Never read, and asserted absent by ``test_acquisition.py``. Not exhaustive —
#: the allowlist is what makes the guarantee — but these are the ones a future
#: change is most likely to reach for.
NEVER_READ: frozenset[str] = frozenset(
    {
        "PatientName",
        "PatientID",
        "PatientBirthDate",
        "PatientAge",
        "PatientSex",
        "PatientWeight",
        "OtherPatientIDs",
        "AccessionNumber",
        "StudyDate",
        "StudyTime",
        "SeriesDate",
        "SeriesTime",
        "AcquisitionDate",
        "AcquisitionTime",
        "ContentDate",
        "ContentTime",
        "InstitutionName",
        "InstitutionAddress",
        "InstitutionalDepartmentName",
        "ReferringPhysicianName",
        "PerformingPhysicianName",
        "OperatorsName",
        "ReviewerName",
        "StationName",
        "DeviceSerialNumber",
        "StudyDescription",
        "SeriesDescription",
        "StudyInstanceUID",
        "SeriesInstanceUID",
        "SOPInstanceUID",
        "FrameOfReferenceUID",
        "StructureSetLabel",
        "StructureSetName",
        "StructureSetDescription",
    }
)


# ---- Extraction ------------------------------------------------------------


@dataclass
class ImageAcquisition:
    """Technical description of one image series. No identifiers by construction."""

    modality: str = ""
    manufacturer: str = ""
    model: str = ""
    software_versions: str = ""
    slice_thickness: float | None = None
    pixel_spacing_row: float | None = None
    pixel_spacing_col: float | None = None
    kvp: float | None = None
    convolution_kernel: str = ""
    patient_position: str = ""
    rows: int | None = None
    columns: int | None = None
    slices: int = 0

    @property
    def in_plane_mm(self) -> str:
        """``0.977 × 0.977`` — the in-plane voxel size, or empty."""
        if self.pixel_spacing_row is None or self.pixel_spacing_col is None:
            return ""
        return f"{self.pixel_spacing_row:g} × {self.pixel_spacing_col:g}"

    @property
    def matrix(self) -> str:
        if self.rows is None or self.columns is None:
            return ""
        return f"{self.rows} × {self.columns}"


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    """One tag as display text, joining multi-valued elements.

    pydicom returns ``MultiValue`` for tags like a dual-kernel
    ``ConvolutionKernel``, and it is a ``Sequence`` but not a ``list`` — so a
    list/tuple check misses it and prints ``['FC09', 'FC13']`` into the report.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, SequenceABC):
        return " / ".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip()


def read_image_acquisition(dataset: Any) -> ImageAcquisition:
    """Pull the allowlisted acquisition tags from one image slice.

    Reads through :data:`IMAGE_TAGS` rather than naming attributes inline, so
    the allowlist is not merely documentation — it is the code path. Anything
    absent stays at its default.
    """
    found = ImageAcquisition()
    for attribute, keyword in IMAGE_TAGS.items():
        raw = getattr(dataset, keyword, None)
        if raw is None:
            continue
        if attribute in {"slice_thickness", "kvp"}:
            setattr(found, attribute, _number(raw))
        elif attribute in {"rows", "columns"}:
            number = _number(raw)
            setattr(found, attribute, int(number) if number is not None else None)
        else:
            setattr(found, attribute, _text(raw))

    # PixelSpacing is a two-element list, so it does not fit the loop above.
    spacing = getattr(dataset, "PixelSpacing", None)
    if spacing is not None:
        try:
            found.pixel_spacing_row = _number(spacing[0])
            found.pixel_spacing_col = _number(spacing[1])
        except (IndexError, TypeError):
            pass
    return found


# ---- Cohort summary --------------------------------------------------------


@dataclass
class FieldSummary:
    """One acquisition field across the cohort."""

    label: str
    values: dict[str, int] = field(default_factory=dict)
    """``value -> how many series (or structure sets) carried it``."""

    missing: int = 0

    #: Beyond this many distinct values, a numeric field is reported as a range.
    #: Ten different slice counts listed with a count each is noise; "186-242"
    #: is the fact. Categorical fields are always enumerated, because "TOSHIBA
    #: to VARIAN" would be meaningless.
    MAX_ENUMERATED = 4

    @property
    def uniform(self) -> bool:
        return len(self.values) == 1 and not self.missing

    @property
    def numeric(self) -> bool:
        """Whether every recorded value parses as a number."""
        if not self.values:
            return False
        try:
            for value in self.values:
                float(value)
        except ValueError:
            return False
        return True

    def summary(self) -> str:
        """The cohort's value, or its spread, as a reader needs to see it.

        A single value is reported plainly. A handful are listed with counts,
        because "the cohort was scanned at 2 mm" and "most of it was" are
        different claims and only one of them can be written in a paper.

        Many numeric values collapse to a range rather than a list. What is
        never done is averaging them: a cohort scanned half at 2 mm and half at
        3 mm has no meaningful mean slice thickness, and 2.5 mm would describe a
        scan nobody performed.
        """
        if not self.values:
            return "— not recorded"

        if self.numeric and len(self.values) > self.MAX_ENUMERATED:
            numbers = sorted(float(v) for v in self.values)
            total = sum(self.values.values())
            text = f"{numbers[0]:g} – {numbers[-1]:g} (range over {total})"
        else:
            ordered = sorted(self.values.items(), key=lambda item: (-item[1], item[0]))
            shown = ordered[: self.MAX_ENUMERATED + 2]
            parts = [
                f"{value} ({count})" if len(self.values) > 1 else value for value, count in shown
            ]
            text = ", ".join(parts)
            if len(ordered) > len(shown):
                text += f", and {len(ordered) - len(shown)} more"

        if self.missing:
            text += f", not recorded ({self.missing})"
        return text


def _format(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


#: Display order and labels for the image table.
IMAGE_FIELDS: Sequence[tuple[str, str]] = (
    ("modality", "Modality"),
    ("manufacturer", "Scanner manufacturer"),
    ("model", "Scanner model"),
    ("software_versions", "Scanner software"),
    ("in_plane_mm", "In-plane pixel spacing (mm)"),
    ("slice_thickness", "Slice thickness (mm)"),
    ("matrix", "Acquisition matrix"),
    ("kvp", "Tube voltage (kVp)"),
    ("convolution_kernel", "Reconstruction kernel"),
    ("patient_position", "Patient position"),
    ("slices", "Slices per series"),
)

#: Display order and labels for the structure-set table.
RTSS_FIELDS: Sequence[tuple[str, str]] = (
    ("manufacturer", "Manufacturer"),
    ("model", "Model"),
    ("software_versions", "Software version"),
    ("roi_count", "Structures per set"),
)


def summarise(records: Iterable[Any], fields: Sequence[tuple[str, str]]) -> list[FieldSummary]:
    """Collapse a cohort's records into one row per field.

    Numeric spreads are kept as distinct values with counts rather than reduced
    to a mean. A cohort scanned half at 2 mm and half at 3 mm has no meaningful
    "average slice thickness", and printing 2.5 would invent a scan nobody did.
    """
    collected = list(records)
    summaries: list[FieldSummary] = []
    for attribute, label in fields:
        summary = FieldSummary(label=label)
        for record in collected:
            text = _format(getattr(record, attribute, None))
            if text:
                summary.values[text] = summary.values.get(text, 0) + 1
            else:
                summary.missing += 1
        summaries.append(summary)
    return summaries


__all__ = [
    "IMAGE_FIELDS",
    "IMAGE_TAGS",
    "NEVER_READ",
    "RTSS_FIELDS",
    "RTSS_TAGS",
    "FieldSummary",
    "ImageAcquisition",
    "read_image_acquisition",
    "summarise",
]
