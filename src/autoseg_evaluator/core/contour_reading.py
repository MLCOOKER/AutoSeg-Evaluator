"""How a structure's outlines become regions — one reading, for both metric streams.

An RTSTRUCT stores outlines, not regions. Before anything can be measured, the
outlines on each slice have to be read as an area: which loops are islands,
which are holes, what a loop that touches or crosses itself encloses. The 3D
stream then fills that area onto voxels and the 2D stream measures its outline.

Both streams call :func:`read_outlines`, so they cannot disagree about what a
contour *is* — only about how they measure it. Before this existed they read
loops by different rules: the mask path combined every loop on a slice by
exclusive-or, and the polygon path used a stricter parser that refused some of
what the mask path accepted. A difference between a 2D and a 3D number could
come from either cause, and nothing said which.

**The rules**, strongest evidence of intent first:

* ``CLOSEDPLANAR_XOR`` declares that overlaps cancel. That is what is done.
* Separate ``CLOSED_PLANAR`` loops on one slice:

  - apart: separate islands;
  - one inside another: a hole, alternating with depth (an island inside a
    hole is inside again). This is how most exporters write a hole;
  - touching, at an edge or a point, without overlapping: merged;
  - the same loop drawn twice, or two loops that partially overlap: **refused**.
    Union (both areas are tissue) and exclusive-or (the overlap is a hole) are
    both plausible there, they give different tissue, and the file does not
    say which was meant.

* One outline that touches or crosses itself: read as the region it encloses
  **if** the even-odd and non-zero winding rules agree on that region, which
  means the outline describes exactly one region. Lines that enclose nothing (a
  spike out and back) are dropped. If the two rules disagree, the outline goes
  around some area twice and is **refused**.
* An outline enclosing no area at all is dropped.

Placement — which slice an outline belongs to, and whether it lies on it — is
not decided here. Each stream checks placement its own way first, because the
two measure on different things: the 2D stream needs an outline within 0.001 mm
of a slice plane, the 3D stream fills the nearest slice.

**Units.** Points may be in any in-plane unit, provided ``area_tolerance`` is
given in that unit squared. The 2D stream reads in millimetres; the 3D stream
reads in voxel units so the filled coordinates are exactly the ones read, with
no round trip through a rescale that could move an outline off a voxel centre.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

#: Below this, an overlap between two loops is a touch, not an overlap, and a
#: region is empty. Square millimetres; the vendored parser uses the same.
AREA_TOLERANCE_MM2 = 1e-8

#: Declared as closed outlines, combined by the nesting rules above.
CLOSED_TYPES = frozenset({"CLOSED_PLANAR", "INTERPOLATED_PLANAR"})
#: Declared as closed outlines whose overlaps cancel.
XOR_TYPE = "CLOSEDPLANAR_XOR"


class ContourReadingError(ValueError):
    """A structure whose outlines have no single reading.

    The message says which rule refused it, and where, and is shown to the
    user. It never contains a UID.
    """


@dataclass(frozen=True)
class Outline:
    """One closed outline, already assigned to a slice by the caller."""

    slice_index: int
    #: ``(N, 2)`` in-plane coordinates.
    points: np.ndarray
    geometric_type: str


@dataclass(frozen=True)
class ContourReading:
    """A structure read as regions, with a record of what had to be decided."""

    #: Slice index to a ``Polygon`` or ``MultiPolygon``, in the input's units.
    regions: dict[int, Any] = field(default_factory=dict)
    geometric_type: str = "CLOSED_PLANAR"
    #: Slices where a loop inside another was read as a hole.
    hole_slices: int = 0
    #: Slices where loops that touch without overlapping were merged.
    merged_slices: int = 0
    #: Outlines that touch or cross themselves, read as their one region.
    repaired_outlines: int = 0
    #: Outlines enclosing no area, dropped.
    empty_outlines: int = 0

    @property
    def notes(self) -> tuple[str, ...]:
        """What was interpreted rather than read as declared, in words."""
        notes = []
        if self.hole_slices:
            notes.append(f"{self.hole_slices} slice(s): a loop inside a loop read as a hole")
        if self.merged_slices:
            notes.append(f"{self.merged_slices} slice(s): loops touching each other merged")
        if self.repaired_outlines:
            notes.append(
                f"{self.repaired_outlines} outline(s) touching or crossing themselves, "
                "read as the one region both fill rules agree on"
            )
        if self.empty_outlines:
            notes.append(f"{self.empty_outlines} outline(s) enclosing no area, dropped")
        return tuple(notes)


def read_outlines(
    outlines: Iterable[Outline], *, area_tolerance: float = AREA_TOLERANCE_MM2
) -> ContourReading:
    """Read one structure's outlines into regions, or refuse with the reason."""
    outlines = list(outlines)
    if not outlines:
        return ContourReading()

    types = {str(o.geometric_type).strip().upper() for o in outlines}
    if types <= CLOSED_TYPES:
        declared_xor = False
    elif types == {XOR_TYPE}:
        declared_xor = True
    elif types <= CLOSED_TYPES | {XOR_TYPE}:
        raise ContourReadingError(
            "the structure mixes CLOSED_PLANAR and CLOSEDPLANAR_XOR contours, which "
            "declare different rules for combining loops"
        )
    else:
        other = ", ".join(sorted(types - CLOSED_TYPES - {XOR_TYPE}))
        raise ContourReadingError(f"contour type {other} is not a closed outline")

    by_slice: dict[int, list[Any]] = defaultdict(list)
    repaired = empty = 0
    for outline in outlines:
        region, how = _outline_region(outline, area_tolerance)
        if how == "empty":
            empty += 1
            continue
        repaired += how == "repaired"
        by_slice[int(outline.slice_index)].append(region)

    regions: dict[int, Any] = {}
    hole_slices = merged_slices = 0
    for z in sorted(by_slice):
        loops = by_slice[z]
        if len(loops) == 1:
            composed = loops[0]
        else:
            if not declared_xor:
                holes, merged = _classify(loops, z, area_tolerance)
                hole_slices += holes
                merged_slices += merged
            # Apart, nested and touching loops all compose correctly under
            # exclusive-or once partial overlaps and duplicates are refused:
            # nesting gives depth parity, and loops sharing only a boundary
            # have nothing to cancel, so they merge.
            composed = loops[0]
            for loop in loops[1:]:
                composed = composed.symmetric_difference(loop)
        composed = _polygonal(composed)
        if not composed.is_valid:
            raise ContourReadingError(
                f"the loops on slice {z} combine into an invalid region "
                f"({shapely.is_valid_reason(composed)})"
            )
        if composed.area > area_tolerance:
            regions[z] = composed

    return ContourReading(
        regions=regions,
        geometric_type=XOR_TYPE if declared_xor else "CLOSED_PLANAR",
        hole_slices=hole_slices,
        merged_slices=merged_slices,
        repaired_outlines=repaired,
        empty_outlines=empty,
    )


def _outline_region(outline: Outline, area_tolerance: float) -> tuple[Any, str]:
    """The region one outline encloses: ``(region, "ok" | "repaired" | "empty")``."""
    points = np.asarray(outline.points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ContourReadingError(
            f"an outline on slice {outline.slice_index} has coordinates that are not finite numbers"
        )
    if len(points) < 3:
        return None, "empty"
    ring = Polygon(points)
    if ring.is_valid:
        return (None, "empty") if ring.area <= area_tolerance else (ring, "ok")

    # It touches or crosses itself. Split the drawing into the faces it
    # bounds, and ask both fill rules which faces are inside.
    closed = np.vstack([points, points[:1]])
    faces = list(polygonize(unary_union(LineString(closed))))
    windings = [_winding(points, face.representative_point().coords[0]) for face in faces]
    if any(w != 0 and w % 2 == 0 for w in windings):
        raise ContourReadingError(
            f"an outline on slice {outline.slice_index} goes around the same area twice, "
            "so whether that area is inside depends on the fill rule"
        )
    inside = [face for face, w in zip(faces, windings) if w % 2]
    region = _polygonal(unary_union(inside)) if inside else Polygon()
    if region.area <= area_tolerance:
        return None, "empty"
    return region, "repaired"


def _winding(points: np.ndarray, at: tuple[float, float]) -> int:
    """How many times the closed outline winds around a point (signed)."""
    x0, y0 = points[:, 0], points[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    px, py = at
    side = (x1 - x0) * (py - y0) - (px - x0) * (y1 - y0)
    up = (y0 <= py) & (y1 > py) & (side > 0)
    down = (y1 <= py) & (y0 > py) & (side < 0)
    return int(np.count_nonzero(up) - np.count_nonzero(down))


def _classify(loops: list[Any], z: int, area_tolerance: float) -> tuple[int, int]:
    """Check every pair of loops that meet; refuse overlaps and duplicates.

    Returns whether this slice has a hole, and whether it has a merge, as 0/1.
    Area-based, so a loop sitting a rounding error across another's boundary is
    judged by how much of it is outside, not by an exact predicate.
    """
    tree = shapely.STRtree(loops)
    left, right = tree.query(loops, predicate="intersects")
    hole = merged = 0
    for a, b in zip(left.tolist(), right.tolist()):
        if a >= b:
            continue
        p, q = loops[a], loops[b]
        if p.intersection(q).area <= area_tolerance:
            merged = 1
            continue
        q_outside_p = q.difference(p).area
        p_outside_q = p.difference(q).area
        if q_outside_p <= area_tolerance and p_outside_q <= area_tolerance:
            raise ContourReadingError(f"the same loop is drawn twice on slice {z}")
        if q_outside_p <= area_tolerance or p_outside_q <= area_tolerance:
            hole = 1
            continue
        raise ContourReadingError(
            f"two loops on slice {z} partially overlap; whether the overlap is tissue "
            "or a hole is not stated"
        )
    return hole, merged


def _polygonal(geometry: Any) -> Any:
    """Keep only the areal part; set operations can leave lines and points."""
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    parts = [g for g in getattr(geometry, "geoms", []) if isinstance(g, (Polygon, MultiPolygon))]
    if not parts:
        return Polygon()
    return unary_union(parts)


__all__ = [
    "AREA_TOLERANCE_MM2",
    "CLOSED_TYPES",
    "XOR_TYPE",
    "ContourReading",
    "ContourReadingError",
    "Outline",
    "read_outlines",
]
