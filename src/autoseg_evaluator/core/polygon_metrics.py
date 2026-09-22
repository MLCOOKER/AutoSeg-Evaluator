"""Planar contour metrics, measured on the polygons RTSTRUCT actually stores.

The second of the two metric streams. Where :mod:`autoseg_evaluator.core.metrics`
rasterises contours onto the CT lattice and measures the resulting binary masks,
this measures the contour line segments directly: no voxels, no sampling, no
snapping. Six metrics — APL, NAPL, Hausdorff 100 % and 95 %, mean and median
contour distance — to the definitions in Boukerroui et al. (2023) Supplement A.

This module is the boundary between the application and two vendored kernels.
Everything the suppliers call integration responsibility lives here: choosing an
engine, resolving contour topology, putting both structures in one frame, and
turning an exception into something a results table can show.

**Undefined is reported, never substituted.** Both kernels raise rather than
returning a plausible number, and that is the property worth preserving. A
comparison with no shared planes has no distance — not a distance of zero — and
a quantile the kernel refuses as ambiguous is a refusal, not a gap to fill. Each
becomes a status string beside empty cells, so a cohort summary can see that the
row was attempted and could not be answered.

Two engines are available and they agree to about 1e-12 mm on APL:

``fast``
    The compiled continuous-envelope engine, and the default. Roughly 60-150x
    quicker, and the only one that finishes on structures the size of a brain.

``reference``
    Pure Python. The audit trail, the differential check against the compiled
    engine, and what runs on any platform with no compiled library. Slower, and
    it exhausts its sampling budget on the largest structures.

``AUTOSEG_POLYGON_ENGINE`` forces one or the other; otherwise the compiled
engine is used where a library exists and the reference engine where it does
not. The engine that produced a number is recorded beside it either way.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from autoseg_evaluator.core.contour_grid import ContourGrid
from autoseg_evaluator.vendor import native_contour_metrics as _reference
from autoseg_evaluator.vendor import native_contour_metrics_fast as _fast
from autoseg_evaluator.vendor.native_contour_metrics_fast import geometry as _fast_geometry
from autoseg_evaluator.vendor.native_contour_metrics_fast import polygon_compat as _compat
from autoseg_evaluator.vendor.native_contour_metrics_fast.errors import AmbiguousQuantileError
from autoseg_evaluator.vendor.native_contour_metrics_fast.platforms import NativeLibraryError

ENGINE_FAST = "fast"
ENGINE_REFERENCE = "reference"
ENGINE_VARIABLE = "AUTOSEG_POLYGON_ENGINE"

#: Distance metrics are defined on the planes both structures reach. Planes only
#: one of them contours are excluded and counted, which is the convention the
#: paper uses and both kernels require to be requested explicitly.
MISSING_PLANE_POLICY = "exclude"

#: Both kernels take an ``error_mm``, and it does not mean the same thing in
#: each. Passing one value to both is a mistake that costs three orders of
#: magnitude, so they have separate constants named for what they actually do.
#:
#: In the compiled engine it is *only* the width at which a quantile sitting on
#: a rounding-sensitive gap is refused rather than resolved arbitrarily. It is
#: not a sampling step and not a speed control: measured across 0.001 to 0.5 it
#: changes neither the cost nor the result by a single bit. The strictest useful
#: value is therefore free, and correct.
QUANTILE_GUARD_MM = 0.001

#: In the reference engine the same argument is the *sampling step*: the kernel
#: walks each boundary in bins of ``2 * error_mm`` and the cost is inversely
#: proportional to it. At the compiled engine's 0.001 a single parotid pair took
#: 29.7 seconds against 34 milliseconds, and a structure the size of a brain
#: exhausts the two-million-sample budget outright.
#:
#: 0.02 mm keeps the discretisation interval to +/-0.02 mm — still an order of
#: magnitude finer than the voxel the mask metrics are quantised to — while
#: leaving this engine usable as the platform fallback it has to be. Raise the
#: precision deliberately when using it as the differential oracle, where the
#: run is one pair and the wait is the point.
REFERENCE_SAMPLING_MM = 0.02

#: What the reference engine's own published acceptance was recorded at. Its
#: agreement with the audited continuous values is a property of this step, not
#: of the engine: at the 0.02 mm it runs at in production it disagrees by up to
#: 7e-3 mm, which is inside its own stated interval and outside the suppliers'
#: 1e-3 mm threshold. Validating it against published values therefore has to
#: use this, or it measures the sampling step and calls the result a defect.
REFERENCE_ACCEPTANCE_SAMPLING_MM = 0.001

#: Status text for a comparison the metrics cannot describe at all. A consensus
#: is born as a binary mask and has no contours, so availability is a property
#: of the *pair*: both sides must be native RTSTRUCT.
STATUS_NO_CONTOURS = "unavailable: a consensus ground truth has no contours"


class ContoursUnavailableError(RuntimeError):
    """This ROI's contours cannot be read as planar regions.

    Distinct from a metric being undefined: nothing was measured because nothing
    could be interpreted, and the reason names which of the two it was.
    """


@dataclass(frozen=True)
class ContourRegions:
    """One structure as composed 2D regions, keyed by slice index."""

    planes: dict[int, Any]
    geometric_type: str
    vertices: int
    #: Planes whose topology was resolved by composing nested rings rather than
    #: read from an explicit XOR declaration. Zero for most structures.
    nested_planes: int = 0

    @property
    def empty(self) -> bool:
        return not self.planes


@dataclass(frozen=True)
class PolygonMetrics:
    """What one ROI pair produced, or why it produced nothing."""

    values: dict[str, float] = field(default_factory=dict)
    #: Empty when the comparison succeeded; otherwise why it did not.
    status: str = ""
    engine: str = ""
    #: The engine's full output, for the audit sidecar. Not for the table.
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return not self.status


# ---- Reading contours ------------------------------------------------------


def parse_structure(
    dataset: Any,
    roi_number: int,
    grid: ContourGrid,
    *,
    allow_nested: bool = False,
) -> ContourRegions:
    """Read one ROI into planar regions in the grid's frame.

    ``allow_nested`` turns on composition of nested ordinary ``CLOSED_PLANAR``
    loops as holes. It is off by default and belongs to the exporter, not to the
    structure: DICOM documents a hole through a keyhole contour or an explicit
    ``CLOSEDPLANAR_XOR``, so a nested plain loop has unambiguous *geometry* and
    ambiguous *intent*. The guarded parser still refuses anything that touches,
    crosses or partially overlaps.

    Worth knowing when deciding: this application's mask rasterisers have always
    composed these rings even-odd. Leaving it off here makes the two streams
    disagree about 22 of 357 structures on the reference cohort; turning it on
    makes them agree.
    """
    try:
        parsed = _compat.parse_compatible(
            dataset, int(roi_number), grid.as_parser_grid(), allow_nested=allow_nested
        )
    except _fast_geometry.Unsupported as exc:
        raise ContoursUnavailableError(f"contours not readable: {exc}") from exc

    roi, nested = _unpack_compat(parsed)
    return ContourRegions(
        planes=dict(roi.planes),
        geometric_type=str(roi.geometric_type),
        vertices=int(roi.vertices),
        nested_planes=int(nested),
    )


def _unpack_compat(parsed: Any) -> tuple[Any, int]:
    """The compat parser reports an audit policy alongside the ROI.

    Its exact shape is the supplier's to choose, so this reads it defensively
    rather than pinning a tuple layout we would then have to track.
    """
    if isinstance(parsed, tuple):
        roi = parsed[0]
        policy = parsed[1] if len(parsed) > 1 else None
    else:
        roi, policy = parsed, None
    nested = 0
    if isinstance(policy, Mapping):
        nested = int(policy.get("nested_plane_count", policy.get("nested_planes", 0)) or 0)
    elif isinstance(policy, int):
        nested = int(policy)
    return roi, nested


# ---- Engines ---------------------------------------------------------------


class _Engine:
    """One kernel, behind the shape the worker uses."""

    name = ""
    version = ""

    def prepare(self, regions: ContourRegions) -> Any:
        raise NotImplementedError

    def _compare(self, a: Any, b: Any, tolerance_mm: float) -> dict[str, Any]:
        raise NotImplementedError

    def _row(self, raw: dict[str, Any]) -> dict[str, float]:
        raise NotImplementedError

    def compare(self, a: Any, b: Any, tolerance_mm: float) -> PolygonMetrics:
        """Measure one pair, converting a documented refusal into a status.

        Only the failures the suppliers document are caught. Anything else is a
        defect in this integration and should surface as one rather than be
        filed as an unavailable metric.
        """
        try:
            raw = self._compare(a, b, float(tolerance_mm))
        except AmbiguousQuantileError as exc:
            return PolygonMetrics(status=f"undefined: {exc}", engine=self.label)
        except ValueError as exc:
            return PolygonMetrics(status=f"undefined: {exc}", engine=self.label)
        except (RuntimeError, ArithmeticError) as exc:
            return PolygonMetrics(status=f"unavailable: {exc}", engine=self.label)
        return PolygonMetrics(values=self._row(raw), engine=self.label, detail=raw)

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}"

    @property
    def settings(self) -> dict[str, Any]:
        """What the numbers were measured under, for the audit record."""
        return {"engine": self.name, "version": self.version}


class _FastEngine(_Engine):
    name = ENGINE_FAST

    def __init__(self) -> None:
        self.version = str(_fast.__version__)

    def prepare(self, regions: ContourRegions) -> Any:
        roi = _fast.ROI(dict(regions.planes), {}, regions.vertices, regions.geometric_type)
        return _fast.prepare(roi)

    @property
    def settings(self) -> dict[str, Any]:
        return {**super().settings, "quantile_guard_mm": QUANTILE_GUARD_MM}

    def _compare(self, a: Any, b: Any, tolerance_mm: float) -> dict[str, Any]:
        return _fast.compare(
            a,
            b,
            taus=[tolerance_mm],
            error_mm=QUANTILE_GUARD_MM,
            missing_plane_policy=MISSING_PLANE_POLICY,
        )

    def _row(self, raw: dict[str, Any]) -> dict[str, float]:
        apl = raw["apl"][0]
        return {
            "poly_apl_mm": float(apl["apl_a_mm"]),
            "poly_napl": float(apl["napl_a"]),
            "poly_apl_reverse_mm": float(apl["apl_b_mm"]),
            "poly_napl_reverse": float(apl["napl_b"]),
            "poly_hd100_mm": float(raw["hd_mm"]),
            "poly_hd95_mm": float(raw["hd95_mm"]),
            "poly_mean_distance_mm": float(raw["mean_mm"]),
            "poly_median_distance_mm": float(raw["median_mm"]),
            "poly_planes_joint": int(raw["joint_planes"]),
            "poly_planes_gt_only": int(raw["excluded_a_planes"]),
            "poly_planes_test_only": int(raw["excluded_b_planes"]),
        }


class _ReferenceEngine(_Engine):
    name = ENGINE_REFERENCE

    def __init__(self, sampling_mm: float = REFERENCE_SAMPLING_MM) -> None:
        self.version = str(_reference.__version__)
        self.sampling_mm = float(sampling_mm)

    def prepare(self, regions: ContourRegions) -> Any:
        # Nothing to precompute; this engine samples afresh on every call.
        return _reference.from_planes(dict(regions.planes))

    def _compare(self, a: Any, b: Any, tolerance_mm: float) -> dict[str, Any]:
        return _reference.compare(
            a,
            b,
            tolerances_mm=(tolerance_mm,),
            error_mm=self.sampling_mm,
            missing_plane_policy=MISSING_PLANE_POLICY,
        )

    @property
    def settings(self) -> dict[str, Any]:
        return {**super().settings, "sampling_mm": self.sampling_mm}

    def _row(self, raw: dict[str, Any]) -> dict[str, float]:
        distance = raw["distance"]
        apl = raw["apl"][0]
        forward, reverse = apl["reference_to_test"], apl["test_to_reference"]
        coverage = raw["plane_coverage"]
        napl = forward["napl"]
        napl_reverse = reverse["napl"]
        row = {
            "poly_apl_mm": float(forward["apl_mm"]),
            "poly_apl_reverse_mm": float(reverse["apl_mm"]),
            "poly_hd100_mm": float(distance["hd100"]["mm"]),
            "poly_hd95_mm": float(distance["hd95"]["mm"]),
            "poly_mean_distance_mm": float(distance["mean_contour_distance"]["mm"]),
            "poly_median_distance_mm": float(distance["median_contour_distance"]["mm"]),
            "poly_planes_joint": int(coverage["joint_planes"]),
            "poly_planes_gt_only": int(coverage["excluded_a_planes"]),
            "poly_planes_test_only": int(coverage["excluded_b_planes"]),
        }
        # NAPL is undefined against an empty reference; the kernel says so with
        # ``None`` and that is not a zero.
        if napl is not None:
            row["poly_napl"] = float(napl)
        if napl_reverse is not None:
            row["poly_napl_reverse"] = float(napl_reverse)
        return row


def library_available() -> bool:
    """Whether a compiled library exists for this process."""
    try:
        return _fast.loader.library_path().is_file()
    except NativeLibraryError:
        return False
    except AttributeError:  # pragma: no cover - loader shape changed upstream
        return False


def select_engine(preferred: str | None = None, *, sampling_mm: float | None = None) -> _Engine:
    """Resolve which kernel to use, honouring the override and availability.

    ``sampling_mm`` overrides the reference engine's step, for the two callers
    that need a different one: the acceptance suite, which has to meet the
    suppliers' published threshold, and a differential check being used as an
    oracle. It means nothing to the compiled engine, which has no step.

    An explicit request for the compiled engine on a platform with no library is
    an error rather than a silent downgrade: someone who set the variable wants
    to know. An *unset* variable falls back quietly, because that is the
    behaviour that keeps the feature working on a platform we do not ship a
    binary for.
    """
    requested = (preferred or os.environ.get(ENGINE_VARIABLE) or "").strip().lower()
    if requested == ENGINE_REFERENCE:
        return _ReferenceEngine(sampling_mm or REFERENCE_SAMPLING_MM)
    if requested == ENGINE_FAST:
        if not library_available():
            raise NativeLibraryError(
                f"{ENGINE_VARIABLE}={ENGINE_FAST} was requested but no compiled "
                "library is packaged for this platform."
            )
        return _FastEngine()
    if requested:
        raise ValueError(
            f"{ENGINE_VARIABLE} must be {ENGINE_FAST!r} or {ENGINE_REFERENCE!r}, not {requested!r}"
        )
    if library_available():
        return _FastEngine()
    return _ReferenceEngine(sampling_mm or REFERENCE_SAMPLING_MM)


def compare_structures(
    reference: ContourRegions,
    test: ContourRegions,
    *,
    tolerance_mm: float,
    engine: _Engine | None = None,
) -> PolygonMetrics:
    """Measure one ROI pair. Convenience over prepare-then-compare.

    The worker should prepare each structure once and reuse it across every
    comparison that structure takes part in; this exists for the single-pair
    case and for tests, where the preparation cost is not worth the bookkeeping.
    """
    active = engine or select_engine()
    if reference.empty or test.empty:
        return PolygonMetrics(
            status="undefined: one structure has no contours on any plane",
            engine=active.label,
        )
    return active.compare(
        active.prepare(reference), active.prepare(test), tolerance_mm=tolerance_mm
    )


#: The columns a successful comparison fills, in the order they are shown.
ROW_KEYS: Sequence[str] = (
    "poly_apl_mm",
    "poly_napl",
    "poly_apl_reverse_mm",
    "poly_napl_reverse",
    "poly_hd100_mm",
    "poly_hd95_mm",
    "poly_mean_distance_mm",
    "poly_median_distance_mm",
    "poly_planes_joint",
    "poly_planes_gt_only",
    "poly_planes_test_only",
)


__all__ = [
    "ENGINE_FAST",
    "ENGINE_REFERENCE",
    "ENGINE_VARIABLE",
    "MISSING_PLANE_POLICY",
    "QUANTILE_GUARD_MM",
    "REFERENCE_ACCEPTANCE_SAMPLING_MM",
    "REFERENCE_SAMPLING_MM",
    "ROW_KEYS",
    "STATUS_NO_CONTOURS",
    "ContourRegions",
    "ContoursUnavailableError",
    "PolygonMetrics",
    "compare_structures",
    "library_available",
    "parse_structure",
    "select_engine",
]
