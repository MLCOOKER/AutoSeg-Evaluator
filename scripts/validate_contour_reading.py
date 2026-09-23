"""Check the shared contour reading against what it replaced, on a real cohort.

Usage::

    python scripts/validate_contour_reading.py <folder> [--patients N]

For every structure in every structure set under ``folder``:

2D  The new reading (strict placement + :func:`read_outlines`) against the
    vendored parser it replaced, with dangling references set aside for both.
    On everything the vendored parser accepts, the regions must be identical;
    anything it refuses that the reading accepts is listed with the rule that
    accepted it.

3D  The new mask (shared reading + half-open fill) against the previous
    continuous fill (every loop filled by scikit-image and combined by
    exclusive-or), voxel for voxel. Every structure whose mask changes is
    listed with its voxel counts and what the reading interpreted; structures
    that gain or lose a mask are listed with the reason.

Both fills are timed.

**Output is safe to share**: vendor labels (Manufacturer), structure names,
counts and timings. No patient identifiers, UIDs, dates or folder names are
printed; patients are numbered in folder order.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np
import pydicom
from pydicom.dataset import Dataset
from skimage.draw import polygon

from autoseg_evaluator.core import masks
from autoseg_evaluator.core.contour_grid import build_grid
from autoseg_evaluator.core.contour_reading import ContourReadingError
from autoseg_evaluator.core.polygon_metrics import (
    ContoursUnavailableError,
    _check_references,
    parse_structure,
)
from autoseg_evaluator.vendor.native_contour_metrics_fast import geometry, polygon_compat


def previous_fill(image, item):
    """The continuous backend before the shared reading, for comparison only."""
    sequence = item.ContourSequence
    types = {str(getattr(c, "ContourGeometricType", "")).upper() for c in sequence}
    if not types or not types <= {"CLOSED_PLANAR", "INTERPOLATED_PLANAR"}:
        return None
    size_x, size_y, size_z = image.GetSize()
    volume = np.zeros((size_z, size_y, size_x), dtype=bool)
    transform = masks._index_transform(image)
    for contour in sequence:
        data = np.asarray(getattr(contour, "ContourData", []), dtype=np.float64)
        if data.size < 9:
            continue
        idx = masks._apply_index_transform(data.reshape(-1, 3), transform)
        if float(idx[:, 2].max() - idx[:, 2].min()) > masks.PLANARITY_TOLERANCE_VOXELS:
            return None
        z = int(round(float(idx[0, 2])))
        if z < 0 or z >= size_z:
            continue
        rr, cc = polygon(idx[:, 1], idx[:, 0], shape=(size_y, size_x))
        if rr.size:
            volume[z][rr, cc] ^= True
    return volume


def on_edge_share(image, item, old, new):
    """How many of the voxels that changed have their centre exactly on an outline.

    Those are the ties the half-open rule settles: the previous fill counted a
    centre on an edge as inside for every loop it touched, and the new one
    gives it to one side. A change anywhere else would be a real difference in
    the reading and needs explaining.
    """
    from shapely.geometry import MultiLineString, Point

    transform = masks._index_transform(image)
    edges = defaultdict(list)
    for contour in item.ContourSequence:
        data = np.asarray(getattr(contour, "ContourData", []), dtype=np.float64)
        if data.size < 9:
            continue
        idx = masks._apply_index_transform(data.reshape(-1, 3), transform)
        ring = np.vstack([idx[:, :2], idx[:1, :2]])
        edges[int(round(float(idx[0, 2])))].append(ring)
    lines = {z: MultiLineString(rings) for z, rings in edges.items()}
    zs, ys, xs = np.nonzero(old ^ new)
    on = sum(
        1
        for z, y, x in zip(zs.tolist(), ys.tolist(), xs.tolist())
        if z in lines and lines[z].distance(Point(x, y)) < 1e-6
    )
    return on, len(zs)


def vendored_view(dataset, number, grid, drop_references):
    """This ROI alone, as the vendored parser was given it before the reading."""
    entry = next(r for r in dataset.StructureSetROISequence if int(r.ROINumber) == number)
    view = Dataset()
    roi = Dataset()
    roi.add(entry["ROINumber"])
    roi.ReferencedFrameOfReferenceUID = grid.frame_of_reference_uid
    view.StructureSetROISequence = [roi]
    holders = []
    for item in dataset.ROIContourSequence:
        if int(item.ReferencedROINumber) != number:
            continue
        holder = Dataset()
        holder.ReferencedROINumber = number
        kept_contours = []
        for contour in getattr(item, "ContourSequence", None) or []:
            kept = Dataset()
            for keyword in ("ContourGeometricType", "NumberOfContourPoints", "ContourData"):
                if keyword in contour:
                    kept.add(contour[keyword])
            if not drop_references and "ContourImageSequence" in contour:
                kept.add(contour["ContourImageSequence"])
            kept_contours.append(kept)
        if kept_contours:
            holder.ContourSequence = kept_contours
        holders.append(holder)
    view.ROIContourSequence = holders
    return view


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder")
    parser.add_argument("--patients", type=int, default=0, help="Stop after N patients.")
    args = parser.parse_args(argv)

    ct, rt = defaultdict(list), defaultdict(list)
    for d, _s, files in os.walk(args.folder):
        for name in files:
            path = os.path.join(d, name)
            try:
                ds = pydicom.dcmread(
                    path,
                    stop_before_pixels=True,
                    specific_tags=[
                        "Modality",
                        "FrameOfReferenceUID",
                        "ReferencedFrameOfReferenceSequence",
                    ],
                )
            except Exception:  # noqa: BLE001 — not DICOM
                continue
            if ds.Modality == "CT":
                ct[str(ds.FrameOfReferenceUID)].append(path)
            elif ds.Modality == "RTSTRUCT":
                rt[str(ds.ReferencedFrameOfReferenceSequence[0].FrameOfReferenceUID)].append(path)

    two_d = Counter()
    two_d_extended = Counter()
    two_d_problems = []
    three_d = Counter()
    changed = []
    gained, lost = [], []
    timing = Counter()
    frames = [f for f in sorted(ct) if f in rt]
    if args.patients:
        frames = frames[: args.patients]

    for index, frame in enumerate(frames):
        t_patient = time.perf_counter()
        grid = build_grid(ct[frame])
        image = masks.read_dicom_image(os.path.dirname(ct[frame][0]))
        for path in rt[frame]:
            ds = pydicom.dcmread(path)
            vendor = str(getattr(ds, "Manufacturer", "?"))[:12]
            names = {int(r.ROINumber): str(r.ROIName) for r in ds.StructureSetROISequence}
            items = {int(i.ReferencedROINumber): i for i in ds.ROIContourSequence}
            for number, name in names.items():
                label = f"#{index} {vendor}: {name}"

                # ---- 2D --------------------------------------------------
                try:
                    ours = parse_structure(ds, number, grid)
                except ContoursUnavailableError:
                    ours = None
                try:
                    drop, _ = _check_references(ds, number, grid)
                    view = vendored_view(ds, number, grid, drop)
                    theirs, _ = polygon_compat.parse_compatible(
                        view, number, grid.as_parser_grid(), allow_nested=True
                    )
                except (ContoursUnavailableError, geometry.Unsupported, StopIteration):
                    theirs = None
                if ours is not None and theirs is not None:
                    same = sorted(ours.planes) == sorted(theirs.planes) and all(
                        ours.planes[z].symmetric_difference(theirs.planes[z]).area < 1e-6
                        for z in theirs.planes
                    )
                    two_d["both read, identical" if same else "both read, DIFFERENT"] += 1
                    if not same:
                        two_d_problems.append(label)
                elif ours is not None:
                    two_d["read now, refused before"] += 1
                    for note in ours.reading_notes or ("(no interpretation recorded)",):
                        two_d_extended[note.split(":")[-1].strip()] += 1
                    two_d_problems.append(f"{label}  [extended: {'; '.join(ours.reading_notes)}]")
                elif theirs is not None:
                    two_d["REFUSED NOW, read before"] += 1
                    two_d_problems.append(f"{label}  [REGRESSION]")
                else:
                    two_d["refused by both"] += 1

                # ---- 3D --------------------------------------------------
                item = items.get(number)
                if item is None or not getattr(item, "ContourSequence", None):
                    continue
                t0 = time.perf_counter()
                old = previous_fill(image, item)
                t1 = time.perf_counter()
                try:
                    new, reading = masks._fill_structure(image, item)
                    reason = ""
                except (ContourReadingError, masks.MaskConversionError) as exc:
                    new, reading, reason = None, None, str(exc)
                t2 = time.perf_counter()
                timing["previous fill"] += t1 - t0
                timing["new fill"] += t2 - t1
                if old is None and new is None:
                    three_d["no mask either way"] += 1
                elif old is None:
                    three_d["gained a mask"] += 1
                    gained.append(label)
                elif new is None:
                    three_d["lost its mask"] += 1
                    lost.append(f"{label}  [{reason}]")
                else:
                    diff = int(np.count_nonzero(old ^ new))
                    if diff == 0:
                        three_d["identical"] += 1
                    else:
                        three_d["changed"] += 1
                        on, total = on_edge_share(image, item, old, new)
                        three_d["changed voxels, centre on an outline edge"] += on
                        three_d["changed voxels, centre NOT on an edge"] += total - on
                        changed.append(
                            (
                                diff,
                                label,
                                int(old.sum()),
                                int(new.sum()),
                                f"{on}/{total} on an edge"
                                + (f"; {'; '.join(reading.notes)}" if reading.notes else ""),
                            )
                        )
        print(f"patient {index}: {time.perf_counter() - t_patient:.0f}s", flush=True)

    print("\n=== 2D: the reading against the vendored parser ===")
    for key, n in two_d.most_common():
        print(f"  {n:>6}  {key}")
    if two_d_extended:
        print("  read now, by rule:")
        for key, n in two_d_extended.most_common():
            print(f"  {n:>8}  {key}")
    for line in two_d_problems[:40]:
        print(f"    {line}")

    print("\n=== 3D: new masks against the previous fill ===")
    for key, n in three_d.most_common():
        print(f"  {n:>6}  {key}")
    if changed:
        changed.sort(reverse=True)
        print("  changed (voxels differing, previous -> new, what the reading interpreted):")
        for diff, label, before, after, notes in changed[:60]:
            share = diff / max(before, 1)
            print(f"    {diff:>6} ({share:6.2%})  {before:>8} -> {after:<8} {label}  {notes}")
    for line in gained:
        print(f"  gained: {line}")
    for line in lost:
        print(f"  lost:   {line}")

    print("\n=== Time spent filling ===")
    for key, seconds in timing.items():
        print(f"  {key:<14} {seconds:8.1f} s")
    if timing["previous fill"]:
        print(f"  new / previous: {timing['new fill'] / timing['previous fill']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
