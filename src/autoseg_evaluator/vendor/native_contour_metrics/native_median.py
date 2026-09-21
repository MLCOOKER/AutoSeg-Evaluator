"""Paper A.7/A.11 median: arclength quantiles, maximum of directions.

Assistant-authored implementation, not copied from Gooding's code. Bounds
cover midpoint discretization, conditional on floating-point distances.
Ambiguous CDF plateaus raise instead of silently selecting a distant branch.
"""
import numpy as np
import shapely
from .native_distance_metrics import plane_groups, sampled_direction, guarded_quantile


def median_metrics(a, b, error_mm=.001, missing_plane_policy='error', max_points=2000000):
    if not np.isfinite(error_mm) or error_mm <= 0:
        raise ValueError('Distance error must be finite and positive')
    if not isinstance(max_points, int) or max_points < 1:
        raise ValueError('max_points must be a positive integer')
    groups, meta = plane_groups(a, b, missing_plane_policy)
    result = dict(**meta, error_mm=float(error_mm),
                  definition='maximum of directional arclength medians on common planes',
                  quantile_convention='infimum of t with CDF(t) >= 0.5',
                  bound_scope='discretization only; conditional on floating-point distances')
    for label, swap in [('a', False), ('b', True)]:
        ds, ws = [], []
        radius = 0.
        npoints = 0
        for pair, count in groups.items():
            pw, qw = pair[::-1] if swap else pair
            d, w, r = sampled_direction(shapely.from_wkb(pw), shapely.from_wkb(qw),
                                         2*error_mm, max_points-npoints)
            ds.append(d); ws.append(w*count)
            radius = max(radius, r); npoints += len(d)
        d, w = np.concatenate(ds), np.concatenate(ws)
        value = guarded_quantile(d, w, radius, .5)
        result.update({label+'_median_mm': value,
                       label+'_median_lower_mm': max(0., value-radius),
                       label+'_median_upper_mm': value+radius,
                       label+'_length_mm': float(w.sum()), label+'_unique_samples': npoints})
    for suffix in ['mm', 'lower_mm', 'upper_mm']:
        result['median_'+suffix] = max(result[s+'_median_'+suffix] for s in ['a', 'b'])
    return result
