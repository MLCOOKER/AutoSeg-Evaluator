"""Cascading source-label resolution for RTSTRUCT files.

The DICOM `Manufacturer` tag is frequently absent for in-house models, anonymised
exports, or research tools. This module derives a useful display label using a
fallback chain — first non-empty wins:

    1. User-defined override (keyed by SOPInstanceUID, from settings.json)
    2. Manufacturer                (0008,0070)
    3. StructureSetLabel           (3006,0002)
    4. SoftwareVersions            (0018,1020)
    5. StructureSetName            (3006,0004)
    6. Filename stem
    7. Parent folder name
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ORIGIN_CUSTOM = "custom"
ORIGIN_MANUFACTURER = "mfr"
ORIGIN_LABEL = "label"
ORIGIN_SOFTWARE = "software"
ORIGIN_NAME = "name"
ORIGIN_FILENAME = "filename"
ORIGIN_FOLDER = "folder"
ORIGIN_UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceLabel:
    label: str
    origin: str  # one of the ORIGIN_* constants above


def _get_str(obj: Any, attr: str) -> str:
    """Read a DICOM attribute, coerce to a clean str (handle list-valued tags)."""
    value = getattr(obj, attr, "") if obj is not None else ""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value if v)
    return str(value).strip()


def derive_source_label(
    ds: Any,
    file_path: str | Path,
    custom_overrides: Mapping[str, str] | None = None,
) -> SourceLabel:
    """Resolve a display label for an RTSTRUCT using the fallback cascade.

    Parameters
    ----------
    ds:
        A pydicom Dataset (or any object exposing the DICOM attributes by name).
        Accepts ``None`` to support the pure-filename fallback path.
    file_path:
        Path to the RTSTRUCT file on disk — used as the lowest-priority fallback.
    custom_overrides:
        Optional mapping of SOPInstanceUID → user-defined label. If the dataset's
        SOPInstanceUID is in this mapping, that label wins outright.
    """
    sop_uid = _get_str(ds, "SOPInstanceUID")
    if custom_overrides and sop_uid in custom_overrides:
        return SourceLabel(custom_overrides[sop_uid], ORIGIN_CUSTOM)

    for attr, origin in (
        ("Manufacturer", ORIGIN_MANUFACTURER),
        ("StructureSetLabel", ORIGIN_LABEL),
        ("SoftwareVersions", ORIGIN_SOFTWARE),
        ("StructureSetName", ORIGIN_NAME),
    ):
        value = _get_str(ds, attr)
        if value:
            return SourceLabel(value, origin)

    path = Path(file_path)
    stem = path.stem
    # In modern pathlib, ".dcm" has stem == ".dcm" rather than "" — treat such
    # extension-only filenames as having no meaningful stem and fall through.
    has_meaningful_stem = bool(stem) and not stem.startswith(".")
    if has_meaningful_stem:
        return SourceLabel(stem, ORIGIN_FILENAME)

    parent = path.parent.name
    if parent:
        return SourceLabel(parent, ORIGIN_FOLDER)

    return SourceLabel("Unknown", ORIGIN_UNKNOWN)
