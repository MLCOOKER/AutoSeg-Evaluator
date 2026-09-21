"""Stable facade; audited numerical kernels are kept separate and unchanged."""
import numpy as np
import shapely
from shapely.geometry import Polygon, MultiPolygon
from .geometry import ROI
from .native_distance_metrics import distance_metrics
from .native_median import median_metrics
from .native_apl_precise import apl_metrics


def from_planes(planes):
    """Map shared plane identifiers to valid 2D Shapely regions in mm.

    Region boundaries include both outer rings and holes. Resolve DICOM contour
    semantics before calling this function. No repair, snapping or registration.
    """
    if not isinstance(planes, dict):
        raise TypeError('planes must be a dictionary')
    for p in planes.values():
        if not isinstance(p, (Polygon, MultiPolygon)) or p.is_empty or not p.is_valid:
            raise ValueError('Each plane must contain a valid nonempty Polygon or MultiPolygon')
        if shapely.has_z(p) or not np.isfinite(shapely.get_coordinates(p)).all():
            raise ValueError('Expected finite 2D coordinates in physical millimetres')
    return ROI(dict(planes), {}, 0, 'COMPOSED_BOUNDARIES')


def from_json(planes):
    """JSON plane list: [{id: integer, polygons: [{shell: [[x,y],...], holes: [...]}]}]."""
    out={}
    for plane in planes:
        key=plane['id']
        if type(key) is not int or key in out:
            raise ValueError('JSON plane ids must be unique integers')
        parts=[]
        for spec in plane['polygons']:
            for ring in [spec['shell'], *spec.get('holes',[])]:
                arr=np.asarray(ring, dtype=float)
                if arr.ndim!=2 or arr.shape[1]!=2 or len(arr)<3 or not np.isfinite(arr).all():
                    raise ValueError('Rings must have at least three finite [x,y] coordinates')
            parts.append(Polygon(spec['shell'],spec.get('holes',[])))
        out[key]=parts[0] if len(parts)==1 else MultiPolygon(parts)
    return from_planes(out)


def compare(reference, test, tolerances_mm=(3.,), error_mm=.001,
            missing_plane_policy='error', decimal_precision=60, max_points=2000000):
    """Compute all six metric families. Raises on undefined/ambiguous distances.

    Explicit missing_plane_policy='exclude' selects the paper convention.
    APL always retains unmatched reference-plane lengths, independently of this
    distance policy. Exception handling belongs to the caller; never replace an
    undefined result with zero. No automatic high-precision percentile fallback.
    """
    reference=from_planes(reference.planes);test=from_planes(test.planes)
    tolerances=tuple(float(t) for t in tolerances_mm)
    if not tolerances or len(set(tolerances))!=len(tolerances) or not np.isfinite(tolerances).all() or min(tolerances)<0:
        raise ValueError('Tolerances must be unique, finite, nonnegative and nonempty')
    d=distance_metrics(reference,test,error_mm,missing_plane_policy,max_points)
    m=median_metrics(reference,test,error_mm,missing_plane_policy,max_points)
    output={}
    for label,key,values in [('hd100','hd',d),('hd95','hd95',d),('mean_contour_distance','mean',d),('median_contour_distance','median',m)]:
        output[label]={name:float(values[key+'_'+name]) for name in ['mm','lower_mm','upper_mm']}
        for side,direction in [('a','reference_to_test'),('b','test_to_reference')]:
            output[label][direction]={name:float(values[side+'_'+key+'_'+name]) for name in ['mm','lower_mm','upper_mm']}
    aa=apl_metrics(reference,test,tolerances,decimal_precision)
    bb=apl_metrics(test,reference,tolerances,decimal_precision)
    return dict(schema_version='1.0',definition='Boukerroui et al. 2023 Supplement A, planar arclength',
        parameters=dict(error_mm=error_mm,decimal_precision=decimal_precision,tolerances_mm=list(tolerances),missing_plane_policy=missing_plane_policy),
        distance=output,
        apl=[dict(tolerance_mm=t,reference_to_test=a,test_to_reference=b) for t,a,b in zip(tolerances,aa,bb)],
        plane_coverage={k:d[k] for k in ['joint_planes','excluded_a_planes','excluded_b_planes','excluded_a_length_mm','excluded_b_length_mm']},
        numerical_scope='Distance bounds cover discretization conditional on floating-point geometry; Decimal APL is precision-tested, not interval-certified.')
