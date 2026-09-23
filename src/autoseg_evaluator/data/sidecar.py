"""The audit sidecar — how each number was produced, beside the numbers.

A results table holds one value per metric. That is the right shape for reading
and the wrong shape for reproducing: it cannot say which of two directions the
symmetric figure came from, what the measurement was weighted by, which voxel
lattice or which contour planes it was taken over, or which engine and settings
produced it. Roughly forty fields per comparison, against a dozen columns.

So they go beside the export rather than into it. The suppliers of both metric
streams require the numerical settings to travel with the numbers, and a record
nobody can find afterwards does not satisfy that.

**Optional, and collected only when asked.** The detail has to be captured
during the run — it cannot be reconstructed from a finished table — so enabling
it is a decision made before computing, not at export time.

Nothing here is patient-identifying: structure names, source labels and UIDs
already present in the exported table, plus geometry and settings.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"

#: Row fields that identify which comparison a record belongs to. Without them a
#: record is a set of numbers with no subject; the CSV can be rejoined on them.
_IDENTITY_KEYS = (
    "patient_id",
    "linkage_id",
    "drawer",
    "canonical_organ",
    "comparison_mode",
    "gt_source_label",
    "gt_rtstruct_sop_uid",
    "gt_roi_name",
    "gt_roi_number",
    "test_source_label",
    "test_rtstruct_sop_uid",
    "test_organ",
    "test_roi_number",
)


def has_detail(rows: Iterable[Mapping[str, Any]]) -> bool:
    """Whether any row carries audit detail worth writing."""
    return any(row.get("audit") for row in rows)


def build(rows: Iterable[Mapping[str, Any]], *, settings: Mapping[str, Any] | None = None) -> dict:
    """Assemble the sidecar for a set of result rows.

    Rows without audit detail are skipped rather than written as empty records:
    a run where the detail was not requested should produce no sidecar, not a
    file full of nulls that looks like a failed one.
    """
    records = []
    for row in rows:
        detail = row.get("audit")
        if not detail:
            continue
        record = {key: row.get(key) for key in _IDENTITY_KEYS if row.get(key) not in (None, "")}
        # Truncation belongs to the mask stream alone; the polygon stream's
        # plane rule already excludes what truncation removes. Recorded here
        # because it changes the mask numbers and nothing else.
        if row.get("truncated"):
            record["mask_truncation"] = {
                "applied": True,
                "slices_removed": row.get("truncated_slices"),
                "extent_removed_mm": row.get("truncated_extent_mm"),
            }
        for stream in ("mask", "polygon"):
            if detail.get(stream):
                record[stream] = detail[stream]
        if row.get("error"):
            record["error"] = row["error"]
        records.append(record)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "comparisons": len(records),
        "settings": dict(settings or {}),
        "notes": {
            "directions": (
                "Both directions are kept. The table reports one symmetric value; "
                "which direction it came from is often the finding."
            ),
            "mask_measure": "surface area, mm^2; distances quantised to the voxel lattice",
            "polygon_measure": "arc length, mm; distances on shared contour planes only",
            "truncation": (
                "Applies to the mask stream only. The polygon stream's shared-plane "
                "rule already excludes contours outside the ground truth's planes."
            ),
            "contour_reading": (
                "Both streams read each structure's loops into regions by one set of "
                "rules. Where the reading had to interpret (a loop inside a loop read "
                "as a hole, touching loops merged, an outline touching or crossing "
                "itself read as its one region, an outline with no area dropped), "
                "it is recorded per structure under contour_reading, in both the mask "
                "and polygon records."
            ),
            "references_set_aside": (
                "Polygon stream only. A structure set's frame or image references "
                "that named nothing in the loaded data were set aside, and the "
                "contours were placed from their coordinates. Each still had to "
                "lie within 0.001 mm of a slice plane and inside the image bounds. "
                "The mask stream reads neither reference."
            ),
        },
        "records": records,
    }


def write(
    target: Path | str,
    rows: Iterable[Mapping[str, Any]],
    *,
    settings: Mapping[str, Any] | None = None,
) -> Path | None:
    """Write the sidecar beside an export. Returns the path, or ``None``.

    ``None`` means there was nothing to record — the run did not collect detail —
    which the caller should report as such rather than as a failure.
    """
    rows = list(rows)
    if not has_detail(rows):
        return None
    path = Path(target)
    path.write_text(
        json.dumps(build(rows, settings=settings), indent=2, default=str), encoding="utf-8"
    )
    return path


def path_for(csv_path: Path | str) -> Path:
    """The sidecar path for a given export: ``results.csv`` → ``results.audit.json``."""
    path = Path(csv_path)
    return path.with_suffix(".audit.json")


__all__ = ["SCHEMA_VERSION", "build", "has_detail", "path_for", "write"]
