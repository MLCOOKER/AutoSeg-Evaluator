"""Native planar HD, arclength-weighted HD95 and mean distance.

Physical XY coordinates must be millimetres in a common orthonormal frame.
The input ROI contract and same-plane interpretation match geometry.parse_roi.
Numerical intervals below bound discretization in exact arithmetic; GEOS and
floating-point roundoff are not enclosed by a certified interval library.
"""
from collections import Counter
import numpy as np
import shapely
from .metrics import segments, quantile
from .native_hausdorff import directed_edges_hausdorff

class AmbiguousQuantileError(RuntimeError):
    """CDF rounding could select opposite sides of a large distance gap."""

def guarded_quantile(d,w,radius,probability=.95):
    order=np.argsort(d);dist=d[order];weights=w[order]
    cum=np.cumsum(weights);total=cum[-1]
    # Positive-weight cumulative summation can drift by O(n*eps*total).
    # This diagnostic rejects a large jump close to the percentile's mass.
    # It does not claim certified bounds for arbitrary geometric roundoff.
    guard=(2*len(w)+32)*np.finfo(float).eps*total
    lo=min(np.searchsorted(cum,probability*total-guard),len(d)-1)
    hi=min(np.searchsorted(cum,probability*total+guard),len(d)-1)
    if dist[hi]-dist[lo]>2*radius+64*np.finfo(float).eps*max(1.,float(dist[-1])):
        raise AmbiguousQuantileError(
            f'Quantile {probability:g} mass is near a rounding-sensitive distance gap [{dist[lo]:.12g}, {dist[hi]:.12g}] mm; '
            'use the high-precision reference and review percentile conditioning')
    return quantile(d,w,probability)

def plane_groups(a, b, missing_plane_policy='error'):
    if missing_plane_policy not in ['error', 'exclude']:
        raise ValueError('Missing-plane policy must be error or exclude')
    for obj in [a, b]:
        for p in obj.planes.values():
            if p.geom_type not in ['Polygon', 'MultiPolygon'] or p.is_empty or not p.is_valid or not np.isfinite(p.bounds).all():
                raise ValueError('Planes must contain valid, finite, nonempty polygons')
    joint = set(a.planes) & set(b.planes)
    missing_a = set(a.planes) - joint
    missing_b = set(b.planes) - joint
    if not joint:
        raise ValueError('No common contour planes: paper distance measures are undefined')
    if (missing_a or missing_b) and missing_plane_policy == 'error':
        raise ValueError('Different plane support: explicitly select exclude to use the paper convention')
    return Counter((a.planes[z].wkb, b.planes[z].wkb) for z in joint), dict(
        joint_planes=len(joint), excluded_a_planes=len(missing_a), excluded_b_planes=len(missing_b),
        excluded_a_length_mm=sum(a.planes[z].length for z in missing_a),
        excluded_b_length_mm=sum(b.planes[z].length for z in missing_b),
        missing_plane_policy=missing_plane_policy)

def sampled_direction(source, target, max_step_mm, max_points=2000000):
    edges, lengths = segments(source)
    with np.errstate(over='ignore',divide='ignore',invalid='ignore'):
        required = np.maximum(1, np.ceil(lengths / max_step_mm))
    if not np.isfinite(required).all() or required.sum() > max_points:
        raise RuntimeError('Native distance sample limit exceeded')
    counts = required.astype(np.int64)
    total = int(counts.sum())
    if total > max_points:
        raise RuntimeError('Native distance sample limit exceeded')
    ids = np.repeat(np.arange(len(edges)), counts)
    starts = np.repeat(np.cumsum(counts)-counts, counts)
    frac = (np.arange(total)-starts+.5) / counts[ids]
    points = edges[ids, 0] + (edges[ids, 1]-edges[ids, 0]) * frac[:, None]
    weights = lengths[ids] / counts[ids]
    lines = shapely.linestrings(segments(target)[0])
    tree = shapely.STRtree(lines)
    distances = np.empty(total)
    for start in range(0, total, 65536):
        stop = min(total, start+65536)
        ix, dd = tree.query_nearest(shapely.points(points[start:stop]), return_distance=True, all_matches=False)
        distances[start+ix[0]] = dd
    return distances, weights, float(weights.max()/2)

def distance_metrics(a, b, error_mm=.001, missing_plane_policy='error', max_points=2000000):
    """Discretization intervals have half-width <= error_mm for mean/HD95.

    A point on a straight source bin is at most half the bin length from its
    midpoint. Distance to a closed target is 1-Lipschitz. This bounds both its
    arclength mean and generalized-inverse quantile under the same coupling.
    HD uses the existing continuous-segment branch-and-bound engine instead.
    """
    if not np.isfinite(error_mm) or error_mm <= 0:
        raise ValueError('Distance error must be finite and positive')
    if not isinstance(max_points, int) or max_points < 1:
        raise ValueError('max_points must be a positive integer')
    groups, meta = plane_groups(a, b, missing_plane_policy)
    result = dict(**meta, error_mm=float(error_mm), unique_plane_pairs=len(groups),
                  distance_definition='same-plane boundary distance',
                  symmetric_mean_definition='equal mean of directional arclength means',
                  hd95_definition='maximum of directional 95th arclength quantiles',
                  bound_scope='discretization only; conditional on floating-point distance evaluations')
    directional = {}
    for label, swap in [('a', False), ('b', True)]:
        ds, ws = [], []
        lower = upper = radius = 0.
        npoints = 0
        for pair, count in groups.items():
            pw, qw = pair[::-1] if swap else pair
            p, q = shapely.from_wkb(pw), shapely.from_wkb(qw)
            d, w, e = sampled_direction(p, q, 2*error_mm, max_points-npoints)
            ds.append(d); ws.append(w*count); radius = max(radius, e)
            npoints += len(d)
            lo, hi = directed_edges_hausdorff(segments(p)[0], segments(q)[0], error_mm=error_mm)
            lower = max(lower, lo); upper = max(upper, hi)
        d = np.concatenate(ds); w = np.concatenate(ws)
        mean = float(np.average(d, weights=w))
        q95 = guarded_quantile(d, w, radius, .95)
        r = dict(hd_lower_mm=lower, hd_upper_mm=upper, hd_mm=(lower+upper)/2,
                 hd95_mm=q95, hd95_lower_mm=max(0., q95-radius), hd95_upper_mm=q95+radius,
                 mean_mm=mean, mean_lower_mm=max(0., mean-radius), mean_upper_mm=mean+radius,
                 length_mm=float(w.sum()), unique_samples=npoints)
        directional[label] = r
        result.update({label+'_'+key: val for key, val in r.items()})
    for key in ['hd', 'hd95']:
        for suffix in ['mm', 'lower_mm', 'upper_mm']:
            result[key+'_'+suffix] = max(directional[d][key+'_'+suffix] for d in ['a', 'b'])
    for suffix in ['mm', 'lower_mm', 'upper_mm']:
        result['mean_'+suffix] = sum(directional[d]['mean_'+suffix] for d in ['a', 'b'])/2
    result['mean_pooled_mm'] = sum(directional[d]['mean_mm']*directional[d]['length_mm'] for d in ['a','b'])/sum(directional[d]['length_mm'] for d in ['a','b'])
    return result
