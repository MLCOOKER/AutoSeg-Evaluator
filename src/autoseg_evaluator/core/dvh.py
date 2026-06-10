"""Dose-Volume Histogram (DVH) metrics — thin wrapper around dicompylercore.

For each (RTSTRUCT, RTDOSE, ROI number) triple this module computes the
user-requested Dmean / Dmax / Dmin plus arbitrary D@volume(%), D@volume(cc),
and V@dose(Gy) points. Values are returned in Gy (doses) and cc (volumes).

``dicompylercore`` does the heavy lifting (dose-grid → mask resampling,
DVH curve construction, statistic extraction). All errors are surfaced as
``DVHError`` so the worker can write a clean error row instead of crashing
the batch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


class DVHError(Exception):
    """Raised when DVH computation fails for a specific structure/dose pair."""


@dataclass
class DVHConfig:
    include_dmean: bool = True
    include_dmax: bool = True
    include_dmin: bool = False
    d_at_volumes_pct: list[float] = field(default_factory=list)
    d_at_volumes_cc: list[float] = field(default_factory=list)
    v_at_doses_gy: list[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DVHConfig:
        return cls(
            include_dmean=bool(data.get("include_dmean", True)),
            include_dmax=bool(data.get("include_dmax", True)),
            include_dmin=bool(data.get("include_dmin", False)),
            d_at_volumes_pct=[float(x) for x in data.get("d_at_volumes_pct", []) or []],
            d_at_volumes_cc=[float(x) for x in data.get("d_at_volumes_cc", []) or []],
            v_at_doses_gy=[float(x) for x in data.get("v_at_doses_gy", []) or []],
        )

    def any_enabled(self) -> bool:
        return (
            self.include_dmean
            or self.include_dmax
            or self.include_dmin
            or bool(self.d_at_volumes_pct)
            or bool(self.d_at_volumes_cc)
            or bool(self.v_at_doses_gy)
        )

    def output_keys(self) -> list[str]:
        """Names of every output metric this config produces, in display order."""
        keys: list[str] = []
        if self.include_dmin:
            keys.append("dmin_gy")
        if self.include_dmean:
            keys.append("dmean_gy")
        if self.include_dmax:
            keys.append("dmax_gy")
        for v in self.d_at_volumes_pct:
            keys.append(f"d{_fmt_num(v)}_gy")
        for v in self.d_at_volumes_cc:
            keys.append(f"d{_fmt_num(v)}cc_gy")
        for d in self.v_at_doses_gy:
            keys.append(f"v{_fmt_num(d)}gy_cc")
        return keys


def compute_dvh_metrics(
    rtstruct_ds,
    rtdose_ds,
    roi_number: int,
    config: DVHConfig,
    z_extent_mm: tuple[float, float] | None = None,
) -> dict[str, float]:
    """Return the DVH metrics for one structure against one dose grid.

    Parameters
    ----------
    rtstruct_ds:
        A pydicom Dataset of the RTSTRUCT containing the ROI.
    rtdose_ds:
        A pydicom Dataset of the RTDOSE the structure is evaluated against.
    roi_number:
        ROINumber within the RTSTRUCT's StructureSetROISequence.
    config:
        Which metrics to extract.
    z_extent_mm:
        Optional ``(z_lo, z_hi)`` physical range (mm). When given, the ROI's
        contour planes outside this craniocaudal range are dropped before the
        DVH is computed — the contour-space equivalent of the test mask's
        cranio-caudal truncation, so the DVH describes the same range as the
        geometric comparison. The dataset is restored afterwards. ``None``
        (the default) computes the DVH on the structure as drawn — the
        behaviour validated bit-for-bit against dicompyler-core.

    Raises
    ------
    DVHError
        If dicompylercore can't compute a DVH for the structure (e.g. the
        structure has no contours, lies outside the dose grid, etc.).
    """
    if not config.any_enabled():
        return {}
    try:
        from dicompylercore import dvhcalc
    except ImportError as exc:  # pragma: no cover — only triggered without dicompyler-core
        raise DVHError(f"dicompyler-core not installed: {exc}") from exc

    # Optionally truncate the ROI's contour planes to ``z_extent_mm`` for the
    # duration of this call (restored in ``finally``). The worker runs
    # single-threaded, so temporarily filtering the cached dataset is safe and
    # avoids deep-copying the whole RTSS per row.
    roi_contour = _find_roi_contour(rtstruct_ds, roi_number) if z_extent_mm is not None else None
    saved_sequence = None
    if roi_contour is not None and hasattr(roi_contour, "ContourSequence"):
        saved_sequence = roi_contour.ContourSequence
        lo, hi = z_extent_mm
        roi_contour.ContourSequence = [
            c
            for c in saved_sequence
            if (_contour_plane_z(c) is not None and lo <= _contour_plane_z(c) <= hi)
        ]

    try:
        # dicompyler derives slice thickness from the gap between adjacent
        # contour planes, so a structure contoured on a *single* slice gets
        # thickness 0 → volume 0 → no DVH (a known dicompyler-core limitation).
        # For that case we pass an explicit thickness — the dose grid's
        # z-spacing — so single-slice OARs (and structures truncated down to one
        # plane) still yield dose statistics.
        thickness = _single_plane_thickness(rtstruct_ds, rtdose_ds, roi_number)
        try:
            dvh = dvhcalc.get_dvh(
                rtstruct_ds, rtdose_ds, roi_number, calculate_full_volume=True, thickness=thickness
            )
        except Exception as exc:  # noqa: BLE001 — surface anything as a clean DVHError
            raise DVHError(f"dvhcalc.get_dvh failed: {exc}") from exc
        if dvh is None or getattr(dvh, "volume", 0) == 0:
            raise DVHError(f"No DVH curve for ROI #{roi_number} (empty or outside dose grid).")

        out: dict[str, float] = {}
        # dicompylercore stores doses in the dose grid's native units (typically Gy).
        if config.include_dmin:
            out["dmin_gy"] = _safe(dvh.min)
        if config.include_dmean:
            out["dmean_gy"] = _safe(dvh.mean)
        if config.include_dmax:
            out["dmax_gy"] = _safe(dvh.max)

        # dicompyler-core 0.5.6's ``DVH.statistic`` rejects ``D{X}%`` syntax
        # (``%`` isn't a valid attribute char and the method bails with
        # AttributeError). The bare-number form ``D{X}`` is interpreted as
        # percentage and returns the dose to the hottest X% — which is what
        # we want. ``D{X}cc`` returns the dose to the hottest X cc (useful
        # for small OARs where a fixed % is noisy). ``V{X}Gy`` still needs
        # its unit suffix because ``V{X}cc`` / ``V{X}%`` mean different
        # things.
        for v_pct in config.d_at_volumes_pct:
            key = f"d{_fmt_num(v_pct)}_gy"
            out[key] = _statistic_value(dvh, f"D{_fmt_num(v_pct)}")
        for v_cc in config.d_at_volumes_cc:
            key = f"d{_fmt_num(v_cc)}cc_gy"
            out[key] = _statistic_value(dvh, f"D{_fmt_num(v_cc)}cc")
        for d_gy in config.v_at_doses_gy:
            key = f"v{_fmt_num(d_gy)}gy_cc"
            out[key] = _statistic_value(dvh, f"V{_fmt_num(d_gy)}Gy")
    finally:
        if saved_sequence is not None:
            roi_contour.ContourSequence = saved_sequence
    return out


def _safe(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _statistic_value(dvh, expression: str) -> float:
    """Pull a single DVH point (e.g. ``D95%`` or ``V20Gy``) via dvh.statistic()."""
    try:
        stat = dvh.statistic(expression)
    except Exception:  # noqa: BLE001 — dvh.statistic raises various ValueError subclasses
        return math.nan
    return _safe(getattr(stat, "value", stat))


def _fmt_num(value: float) -> str:
    """Format a number for inclusion in a metric key — drops trailing ``.0``."""
    if isinstance(value, (int, float)) and float(value).is_integer():
        return str(int(value))
    return str(value)


def _single_plane_thickness(rtstruct_ds, rtdose_ds, roi_number: int) -> float | None:
    """Thickness to pass ``get_dvh`` for single-slice ROIs (else ``None``).

    Returns ``None`` when the ROI spans 2+ contour planes — dicompyler then
    infers the thickness from the inter-plane gap as usual. For a single-plane
    ROI (where that inference yields 0 → no DVH) it returns the dose grid's
    z-spacing as an explicit slab thickness so a DVH can still be computed.
    Returns ``None`` if the plane count or dose spacing can't be determined,
    in which case behaviour is unchanged from plain dicompyler.
    """
    n_planes = _contour_plane_count(rtstruct_ds, roi_number)
    if n_planes is None or n_planes >= 2:
        return None
    return _dose_z_spacing(rtdose_ds)


def _find_roi_contour(rtstruct_ds, roi_number: int):
    """Return the ROIContourSequence item for ``roi_number`` (or ``None``)."""
    for rc in getattr(rtstruct_ds, "ROIContourSequence", []) or []:
        if int(getattr(rc, "ReferencedROINumber", -1)) == int(roi_number):
            return rc
    return None


def _contour_plane_z(contour_item) -> float | None:
    """Z coordinate (mm) of a CLOSED_PLANAR contour item (all points share z)."""
    data = getattr(contour_item, "ContourData", None)
    if data and len(data) >= 3:
        return float(data[2])
    return None


def _contour_plane_count(rtstruct_ds, roi_number: int) -> int | None:
    """Number of distinct z-planes the ROI is contoured on (``None`` if unknown)."""
    try:
        roi_contours = rtstruct_ds.ROIContourSequence
    except AttributeError:
        return None
    for roi_contour in roi_contours:
        if int(getattr(roi_contour, "ReferencedROINumber", -1)) != int(roi_number):
            continue
        zs: set[float] = set()
        for item in getattr(roi_contour, "ContourSequence", []) or []:
            data = getattr(item, "ContourData", None)
            if data and len(data) >= 3:
                # ContourData is a flat [x0,y0,z0, x1,y1,z1, …] list; all points
                # in a CLOSED_PLANAR contour share one z. Round to fold float noise.
                zs.add(round(float(data[2]), 2))
        return len(zs)
    return None


def _dose_z_spacing(rtdose_ds) -> float | None:
    """Z-spacing (mm) of the dose grid from its GridFrameOffsetVector."""
    offsets = getattr(rtdose_ds, "GridFrameOffsetVector", None)
    if offsets is None or len(offsets) < 2:
        return None
    spacing = abs(float(offsets[1]) - float(offsets[0]))
    return spacing if spacing > 0 else None
