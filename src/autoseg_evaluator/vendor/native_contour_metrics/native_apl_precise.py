"""Continuous APL/NAPL with spatial pruning and high-precision clipping.

All potentially contributing capsule intersections use Decimal arithmetic on
the ORIGINAL binary64 endpoints. No vertex snapping, subdivision, GEOS buffers,
sampling, or tolerance-band alteration. IEEE-754 nextafter expands each binary64
AABB sum outward, so float pruning cannot discard a true candidate. Decimal
precision is not certified interval arithmetic; validate with higher precision.

Assistant-authored; reuses the audited capsule reference's geometric primitives.
Thus agreement with that reference checks optimization, not independent algebra.
"""
from collections import Counter
from decimal import localcontext
import numpy as np
import shapely
from .capsule_oracle import ZERO, dec, sub, dot, capsule_intervals, covered_parameter
from .metrics import segments


def edge_array(edges):
    a = np.asarray(edges, dtype=float)
    if a.size == 0:
        return np.empty((0, 2, 2))
    if a.ndim != 3 or a.shape[1:] != (2, 2) or not np.isfinite(a).all():
        raise ValueError('Edges must be finite (N,2,2) arrays in millimetres')
    return a


def directed_coverage(source_edges, target_edges, taus, precision=60):
    source, target = edge_array(source_edges), edge_array(target_edges)
    tt = np.asarray(taus, float)
    if tt.ndim != 1 or not len(tt) or not np.isfinite(tt).all() or (tt < 0).any():
        raise ValueError('Tolerances must be nonempty, finite and nonnegative')
    if not isinstance(precision, int) or precision < 40:
        raise ValueError('Decimal precision must be an integer >=40')
    stats = dict(source_edges=len(source), target_edges=len(target),
                 all_pair_threshold_tests=len(source)*len(target)*len(tt), candidate_tests=0)
    with localcontext() as ctx:
        ctx.prec = precision
        src = [(tuple(map(dec, a)), tuple(map(dec, b))) for a, b in source]
        dst = [(tuple(map(dec, a)), tuple(map(dec, b))) for a, b in target]
        radii = list(map(dec, tt))
        # A single correctly rounded +/- plus one outward ULP encloses the
        # exact sum of the two binary64 inputs. Overflow yields an unbounded
        # box and extra candidates, never an inward approximation.
        with np.errstate(over='ignore'):
            lows = [np.nextafter(target.min(axis=1)-r, -np.inf) for r in tt] if len(target) else []
            highs = [np.nextafter(target.max(axis=1)+r, np.inf) for r in tt] if len(target) else []
        matched = [ZERO for _ in tt]
        total = ZERO
        for i, (p, end) in enumerate(src):
            v = sub(end, p); speed2 = dot(v, v)
            if not speed2:
                continue
            length = speed2.sqrt(); total += length
            if not dst:
                continue
            lo, hi = source[i].min(axis=0), source[i].max(axis=0)
            for k, radius in enumerate(radii):
                candidates = np.flatnonzero(np.all(hi >= lows[k], axis=1) & np.all(lo <= highs[k], axis=1))
                stats['candidate_tests'] += len(candidates)
                intervals = []
                for j in candidates:
                    a, b = dst[j]
                    intervals.extend(capsule_intervals(p, v, a, b, radius, speed2))
                covered = covered_parameter(intervals)
                if covered < 0 or covered > 1:
                    raise ArithmeticError('Coverage outside [0,1]')
                matched[k] += length*covered
        return total, matched, stats


def apl_metrics(reference, test, taus=(3.,), precision=60):
    # Deliberately directional: reference-only planes count their full length;
    # test-only planes add no drawing length. NAPL denominator includes ALL
    # reference planes. No contours in reference means NAPL is undefined.
    groups = Counter()
    for roi in [reference, test]:
        for p in roi.planes.values():
            if p.geom_type not in ['Polygon', 'MultiPolygon'] or p.is_empty or not p.is_valid or not np.isfinite(p.bounds).all():
                raise ValueError('Planes must contain valid, finite, nonempty polygons')
    for z, p in reference.planes.items():
        q = test.planes.get(z)
        groups[p.wkb, q.wkb if q is not None else None] += 1
    # Validate even when reference is empty.
    directed_coverage([], [], taus, precision)
    with localcontext() as ctx:
        ctx.prec = precision
        total = ZERO; matched = [ZERO for _ in taus]; missing = ZERO
        stats = dict(all_pair_threshold_tests=0, candidate_tests=0)
        for (pw, qw), count in groups.items():
            p = segments(shapely.from_wkb(pw))[0]
            q = segments(shapely.from_wkb(qw))[0] if qw is not None else []
            length, covered, ss = directed_coverage(p, q, taus, precision)
            total += length*count
            if qw is None:
                missing += length*count
            matched = [a+b*count for a, b in zip(matched, covered)]
            for key in stats:
                stats[key] += ss[key]
        return [dict(tolerance_mm=float(t), apl_mm=float(total-matched[i]),
                     napl=float((total-matched[i])/total) if total else None,
                     reference_length_mm=float(total), unmatched_reference_length_mm=float(missing),
                     unmatched_reference_planes=len(set(reference.planes)-set(test.planes)),
                     test_only_planes=len(set(test.planes)-set(reference.planes)),
                     precision_digits=precision, **stats) for i, t in enumerate(taus)]


def apl_sensitivity(reference, test, tolerance_mm=3., perturbation_mm=1e-6, precision=60):
    """Optional diagnostic; does NOT change the requested metric tolerance.

    Reports how APL changes when tolerance is perturbed, not an error bound
    for uncertain contour coordinates. A long exactly-at-threshold segment
    may cause a large jump even for an otherwise correct implementation.
    """
    if not np.isfinite(perturbation_mm) or perturbation_mm <= 0:
        raise ValueError('Tolerance perturbation must be finite and positive')
    if not np.isfinite(tolerance_mm) or tolerance_mm < 0:
        raise ValueError('Tolerance must be finite and nonnegative')
    lower=max(0., tolerance_mm-perturbation_mm)
    upper=tolerance_mm+perturbation_mm
    rows=apl_metrics(reference,test,[lower,tolerance_mm,upper],precision)
    return dict(**rows[1], perturbation_mm=float(perturbation_mm),
                lower_tolerance_mm=lower,upper_tolerance_mm=upper,
                apl_at_lower_tolerance_mm=rows[0]['apl_mm'],
                apl_at_upper_tolerance_mm=rows[2]['apl_mm'],
                apl_sensitivity_span_mm=rows[0]['apl_mm']-rows[2]['apl_mm'])
