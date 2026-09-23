"""Planar contour metrics, measured on the polygons RTSTRUCT actually stores.

The second of the two metric streams. Where :mod:`autoseg_evaluator.core.metrics`
rasterises contours onto the CT lattice and measures the resulting binary masks,
this measures the contour line segments directly: no voxels, no sampling, no
snapping. Six metrics — APL, NAPL, 2D Hausdorff 100 % and 95 %, mean and median
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

from pydicom.dataset import Dataset

from autoseg_evaluator.core.contour_grid import PLANE_ALIGNMENT_BUDGET_MM, ContourGrid
from autoseg_evaluator.vendor import native_contour_metrics as _reference
from autoseg_evaluator.vendor import native_contour_metrics_fast as _fast
from autoseg_evaluator.vendor.native_contour_metrics import (
    AmbiguousQuantileError as _ReferenceAmbiguousQuantileError,
)
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
#:
#: The engine applies it by raising, which discards the whole comparison. This
#: adapter applies the same test itself instead — see :func:`_undetermined` — so
#: an undetermined quantile blanks that one metric and nothing else.
QUANTILE_GUARD_MM = 0.001

#: What the compiled engine is actually given as ``error_mm``. The argument does
#: nothing there except decide when to raise (the C++ only checks it is
#: positive), so lifting it out of reach turns the refusal off and leaves every
#: number bit-identical. The engine still reports each quantile's gap, and
#: :data:`QUANTILE_GUARD_MM` is applied to those gaps here.
_ENGINE_REFUSAL_LIFTED_MM = 1e300

#: The quantiles the guard applies to: the row column each fills, and the name
#: the engine gives its gap.
_GUARDED_QUANTILES = (
    ("poly_median_distance_mm", "median", "2D median contour distance"),
    ("poly_hd95_mm", "hd95", "2D Hausdorff 95%"),
)

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
    #: References in the file that pointed at nothing and were set aside before
    #: parsing, in words. Empty for a structure set that is internally
    #: consistent, which is most of them. See :func:`_set_aside_dangling_references`.
    references_set_aside: tuple[str, ...] = ()

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
    #: Metrics the comparison could not determine although it succeeded, keyed
    #: by row column, with the reason. Their cells are absent from ``values``;
    #: every other metric is reported as normal.
    undefined: dict[str, str] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return not self.status


# ---- Reading contours ------------------------------------------------------


def parse_structure(dataset: Any, roi_number: int, grid: ContourGrid) -> ContourRegions:
    """Read one ROI into planar regions in the grid's frame.

    Some exporters write a hole as a second ordinary ``CLOSED_PLANAR`` loop
    inside the first, instead of declaring it with a keyhole contour or
    ``CLOSEDPLANAR_XOR``. Those are composed even-odd here, without asking.

    That was an opt-in at first, on the reasoning that such a loop has
    unambiguous *geometry* and ambiguous *intent*. The reasoning was sound and
    the conclusion was wrong, because it ignored what the rest of this
    application already does: **both mask rasterisers have composed these rings
    even-odd since v1** — verified on a real structure, whose mask comes out with
    holes in it — so every published result from this software already rests on
    that interpretation. Making the polygon path stricter did not avoid the
    assumption; it made 22 of 357 structures appear in one stream and vanish from
    the other, which is a backend difference wearing the costume of a metric
    difference.

    So the interpretation is shared, and stated once, rather than offered as a
    switch that can only ever put the two streams out of step.

    What is *not* shared: rings that touch, cross or partially overlap are
    refused here and silently combined by the mask path. Those have no single
    reading, and refusing one is better than picking one — but it does mean a
    structure can carry mask metrics and no polygon metrics. None were found in
    the reference cohort.

    References that point at nothing are set aside first; see
    :func:`_set_aside_dangling_references` for which, and why that is not the
    same as ignoring them.
    """
    source, set_aside = _set_aside_dangling_references(dataset, int(roi_number), grid)
    try:
        parsed = _compat.parse_compatible(
            source, int(roi_number), grid.as_parser_grid(), allow_nested=True
        )
    except _fast_geometry.Unsupported as exc:
        raise ContoursUnavailableError(f"contours not readable: {exc}") from exc

    roi, nested = _unpack_compat(parsed)
    return ContourRegions(
        planes=dict(roi.planes),
        geometric_type=str(roi.geometric_type),
        vertices=int(roi.vertices),
        nested_planes=int(nested),
        references_set_aside=set_aside,
    )


def _set_aside_dangling_references(
    dataset: Any, roi_number: int, grid: ContourGrid
) -> tuple[Any, tuple[str, ...]]:
    """Hand the parser this ROI without the references that point at nothing.

    The parser checks two references before it will read a contour: the ROI's
    ``ReferencedFrameOfReferenceUID`` must be the image series' frame, and each
    contour's ``ContourImageSequence`` must name the slice the contour lies on.
    Both exist to catch a contour placed on the wrong image. Neither separates a
    reference that *contradicts* the image from one that names nothing in the
    data at all, and real exports produce the second kind: a structure set
    written against one copy of a CT and loaded beside another whose UIDs were
    remapped carries references to slices that are not there. On the tender H&N
    cohort that was one vendor of seven, every structure, every patient.

    A reference to nothing is unverifiable, not wrong. So it is set aside, and
    the contour's placement is established the way the mask path has always
    established it — from its coordinates — except more strictly: the parser
    still requires every contour to lie within
    :data:`~autoseg_evaluator.core.contour_grid.PLANE_ALIGNMENT_BUDGET_MM` of a
    slice plane and inside the image bounds, and the mask path requires neither.

    What is set aside, and only when it is unambiguous:

    * **A frame the structure set never declares.** A per-ROI frame must be one
      of those listed in the set's own ``ReferencedFrameOfReferenceSequence``.
      When it is none of them, and the set declares exactly one frame, and that
      frame is this image series', the ROI is read in it. There is no other
      frame it could mean.
    * **Image references of which none resolves.** Only all-or-nothing: if any
      of this ROI's references name a slice in this series, they all stay and
      the parser checks each one.

    What is still refused, with a reason naming which it was:

    * an ROI number the structure set lists more or less than once;
    * a frame the set *does* declare that is not this series' — a structure
      drawn on another image, which coordinates alone cannot place;
    * an undeclared frame in a set declaring several, or none matching;
    * references of which some resolve and some do not.

    The dataset passed in is never modified: it is cached and shared with the
    mask path. When something is set aside the parser gets a one-ROI view built
    from the same data elements, which also keeps the nested-ring path's copy of
    the dataset down to one structure instead of a hundred. The returned notes
    are for the audit record and never contain a UID.
    """
    entries = [
        r
        for r in getattr(dataset, "StructureSetROISequence", None) or []
        if int(r.ROINumber) == roi_number
    ]
    if len(entries) != 1:
        raise ContoursUnavailableError(
            f"contours not readable: ROI number {roi_number} is listed "
            f"{len(entries)} times in this structure set, so which entry it means "
            "is ambiguous"
        )

    set_aside: list[str] = []
    frame = grid.frame_of_reference_uid
    stated = str(getattr(entries[0], "ReferencedFrameOfReferenceUID", "") or "")
    if stated != frame:
        declared = {
            str(item.FrameOfReferenceUID)
            for item in getattr(dataset, "ReferencedFrameOfReferenceSequence", None) or []
            if getattr(item, "FrameOfReferenceUID", None)
        }
        if stated in declared:
            raise ContoursUnavailableError(
                "contours not readable: this structure is defined in a different "
                "Frame of Reference from the image series it is measured on, so its "
                "coordinates cannot be placed on those slices"
            )
        if declared != {frame}:
            raise ContoursUnavailableError(
                "contours not readable: this structure names a Frame of Reference "
                "its own structure set does not declare, and the set does not "
                "declare this image series' frame as its only one, so there is no "
                "single frame to read it in"
            )
        set_aside.append(
            "the structure's Frame of Reference UID is not declared by its own "
            "structure set; read in the set's only declared frame, which is the "
            "image series' frame"
        )

    items = [
        r
        for r in getattr(dataset, "ROIContourSequence", None) or []
        if int(r.ReferencedROINumber) == roi_number
    ]
    references = [
        str(getattr(ref, "ReferencedSOPInstanceUID", "") or "")
        for item in items
        for contour in getattr(item, "ContourSequence", None) or []
        for ref in getattr(contour, "ContourImageSequence", None) or []
    ]
    resolved = sum(uid in grid.sops for uid in references)
    drop_references = bool(references) and resolved == 0
    if 0 < resolved < len(references):
        raise ContoursUnavailableError(
            f"contours not readable: {len(references) - resolved} of this "
            f"structure's {len(references)} contour image references name slices "
            "that are not in this image series while the rest do, so which images "
            "it was drawn on is ambiguous"
        )
    if drop_references:
        set_aside.append(
            f"{len(references)} contour image references name no slice in this "
            "image series; each contour was placed from its coordinates instead, "
            f"within {PLANE_ALIGNMENT_BUDGET_MM:g} mm of a slice plane and inside "
            "the image bounds"
        )

    if not set_aside:
        return dataset, ()
    return _one_roi_view(entries[0], items, roi_number, frame, drop_references), tuple(set_aside)


def _one_roi_view(
    entry: Any, items: Sequence[Any], roi_number: int, frame: str, drop_references: bool
) -> Dataset:
    """A structure set holding only this ROI, sharing the original's elements.

    Elements are carried across by reference, so nothing is converted or
    copied: a view of a large structure costs a few hundred small objects, not a
    second copy of its coordinates.
    """
    view = Dataset()
    roi = Dataset()
    roi.add(entry["ROINumber"])
    roi.ReferencedFrameOfReferenceUID = frame
    view.StructureSetROISequence = [roi]

    rebuilt = []
    for item in items:
        holder = Dataset()
        holder.ReferencedROINumber = roi_number
        contours = []
        for contour in getattr(item, "ContourSequence", None) or []:
            kept = Dataset()
            for keyword in ("ContourGeometricType", "NumberOfContourPoints", "ContourData"):
                if keyword in contour:
                    kept.add(contour[keyword])
            if not drop_references and "ContourImageSequence" in contour:
                kept.add(contour["ContourImageSequence"])
            contours.append(kept)
        # An empty sequence stays absent, so the parser's own "missing contour
        # sequence" refusal still fires exactly as it would have.
        if contours:
            holder.ContourSequence = contours
        rebuilt.append(holder)
    view.ROIContourSequence = rebuilt
    return view


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


# ---- Quantiles the data do not determine ----------------------------------


def _undetermined(raw: Mapping[str, Any]) -> dict[str, str]:
    """Which reported quantiles the contours leave undetermined, and why.

    A quantile of the distance distribution is the distance within which that
    share of the boundary length lies. If the boundary has no length at any
    distance across some interval, and exactly that share lies below it, then
    every value in the interval meets the definition. Take half the boundary
    coinciding with the other contour and half 2 mm away: any median from 0 to
    2 mm is correct. A computer settles it by whether a running sum of lengths
    rounds to just under or just over one half — noise worth 2 mm.

    The compiled engine finds that interval for each direction and quantile. It
    refuses when the interval is wider than ``2 * QUANTILE_GUARD_MM``, which
    voids the whole comparison. This applies the same test with two
    differences:

    * **Per metric.** One undetermined quantile blanks that metric. The maximum,
      the mean and APL are determined whatever the quantile does.
    * **On the reported value, not on each direction.** The table shows the
      larger of the two directions. If one direction could be anything from 0
      to 2 mm and the other is exactly 2 mm, the larger is 2 mm either way, and
      reporting it is not a guess.

    The value is never chosen from inside the interval. When the reported value
    is undetermined the cell stays empty and the reason names the interval.
    """
    reasons: dict[str, str] = {}
    for column, name, label in _GUARDED_QUANTILES:
        brackets = []
        for side in ("a", "b"):
            value = float(raw[f"{side}_{name}_mm"])
            gap = float(raw[f"{side}_quantile_mass_gap_{name}_mm"])
            brackets.append((value - gap / 2, value + gap / 2))
        # The reported value is the larger direction, so it can lie anywhere
        # between the larger of the lower ends and the larger of the upper ends.
        low = max(lo for lo, _ in brackets)
        high = max(hi for _, hi in brackets)
        if high - low > 2 * QUANTILE_GUARD_MM:
            reasons[column] = (
                f"undefined: {label} could be anything from {max(low, 0.0):.3g} to "
                f"{high:.3g} mm; the distance distribution has a gap there, and "
                "which side is reported would depend on floating-point rounding"
            )
    return reasons


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
        except (AmbiguousQuantileError, _ReferenceAmbiguousQuantileError) as exc:
            # Only the reference engine still gets here: it cannot report a
            # quantile's gap without raising, so it loses the whole comparison.
            return PolygonMetrics(status=f"undefined: {exc}", engine=self.label)
        except ValueError as exc:
            return PolygonMetrics(status=f"undefined: {exc}", engine=self.label)
        except (RuntimeError, ArithmeticError) as exc:
            return PolygonMetrics(status=f"unavailable: {exc}", engine=self.label)
        values = self._row(raw)
        undefined = self._undetermined(raw)
        for column in undefined:
            values.pop(column, None)
        return PolygonMetrics(values=values, engine=self.label, detail=raw, undefined=undefined)

    def _undetermined(self, raw: dict[str, Any]) -> dict[str, str]:
        """Quantiles the data do not determine, keyed by row column.

        Only an engine that reports each quantile's gap can say; the default
        is that none are.
        """
        return {}

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
        return {
            **super().settings,
            "quantile_guard_mm": QUANTILE_GUARD_MM,
            "quantile_guard_scope": "per metric, on the reported (larger-direction) value",
        }

    def _compare(self, a: Any, b: Any, tolerance_mm: float) -> dict[str, Any]:
        return _fast.compare(
            a,
            b,
            taus=[tolerance_mm],
            error_mm=_ENGINE_REFUSAL_LIFTED_MM,
            missing_plane_policy=MISSING_PLANE_POLICY,
        )

    def _undetermined(self, raw: dict[str, Any]) -> dict[str, str]:
        return _undetermined(raw)

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


#: What each selectable metric puts in a row. APL and NAPL fill two columns
#: each because both are directional and both directions are meaningful: one is
#: the boundary an editor would have to draw, the other what they would have to
#: remove.
METRIC_COLUMNS: dict[str, tuple[str, ...]] = {
    "apl": ("poly_apl_mm", "poly_apl_reverse_mm"),
    "napl": ("poly_napl", "poly_napl_reverse"),
    "hd100": ("poly_hd100_mm",),
    "hd95": ("poly_hd95_mm",),
    "mean": ("poly_mean_distance_mm",),
    "median": ("poly_median_distance_mm",),
}

#: Emitted whenever anything is. They say what the numbers were measured over,
#: and a distance computed on three shared planes out of thirty means something
#: different from one computed on all thirty.
CONTEXT_COLUMNS: tuple[str, ...] = (
    "poly_planes_joint",
    "poly_planes_gt_only",
    "poly_planes_test_only",
)


@dataclass(frozen=True)
class PolygonConfig:
    """Which polygon metrics to report, and how to read the contours.

    Selecting a subset saves no computation: all six come out of one call and
    share the same distance distribution, so the choice is about how wide the
    results table is, not how long the run takes. Everything is computed and the
    unselected columns are dropped.
    """

    metrics: frozenset[str] = frozenset()
    tolerance_mm: float = 3.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> PolygonConfig:
        data = data or {}
        selected = data.get("metrics", {}) or {}
        if isinstance(selected, Mapping):
            chosen = {str(k) for k, v in selected.items() if v}
        else:
            chosen = {str(k) for k in selected}
        return cls(
            metrics=frozenset(chosen & set(METRIC_COLUMNS)),
            tolerance_mm=float(data.get("tolerance_mm", 3.0)),
        )

    def any_enabled(self) -> bool:
        return bool(self.metrics)

    def columns(self) -> tuple[str, ...]:
        """The row keys this configuration fills, in display order."""
        if not self.metrics:
            return ()
        chosen = [
            column
            for key, columns in METRIC_COLUMNS.items()
            if key in self.metrics
            for column in columns
        ]
        return tuple(chosen) + CONTEXT_COLUMNS

    def select(self, values: Mapping[str, float]) -> dict[str, float]:
        """Keep only what was asked for, from a full set of results."""
        return {key: values[key] for key in self.columns() if key in values}


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
    "CONTEXT_COLUMNS",
    "ENGINE_FAST",
    "ENGINE_REFERENCE",
    "ENGINE_VARIABLE",
    "METRIC_COLUMNS",
    "MISSING_PLANE_POLICY",
    "QUANTILE_GUARD_MM",
    "REFERENCE_ACCEPTANCE_SAMPLING_MM",
    "REFERENCE_SAMPLING_MM",
    "ROW_KEYS",
    "STATUS_NO_CONTOURS",
    "ContourRegions",
    "ContoursUnavailableError",
    "PolygonConfig",
    "PolygonMetrics",
    "compare_structures",
    "library_available",
    "parse_structure",
    "select_engine",
]
