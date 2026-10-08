"""Vendored from native_contour_metrics 0.1.0, unmodified except for imports.

The kernel under ``autoseg_evaluator.vendor`` is byte-identical to the supplied
package and is pinned by ``tests/test_vendor_integrity.py``. This file is not:
its import lines were rewritten to the vendored package path. Nothing
else — no assertion, tolerance or fixture — was touched.
"""

from decimal import Decimal as D
import numpy as np
import pytest
from shapely.geometry import box, Polygon
from shapely.affinity import rotate, translate
from autoseg_evaluator.vendor.native_contour_metrics.geometry import ROI
from autoseg_evaluator.vendor.native_contour_metrics.metrics import segments
from autoseg_evaluator.vendor.native_contour_metrics.native_median import median_metrics
from autoseg_evaluator.vendor.native_contour_metrics.native_distance_metrics import AmbiguousQuantileError
from autoseg_evaluator.vendor.native_contour_metrics.native_apl_precise import apl_metrics, directed_coverage, apl_sensitivity
from autoseg_evaluator.vendor.native_contour_metrics.capsule_oracle import directed_coverage as exhaustive
from autoseg_evaluator.vendor.native_contour_metrics.segment_distance_oracle import build_distribution, quantile

def roi(p): return ROI({0:p},{},0,'CLOSED_PLANAR')
def empty(): return ROI({},{},0,'CLOSED_PLANAR')

def test_analytical_median_not_vertex_median():
    d=build_distribution([([[[0,0],[10,0]]],[[[0,-1],[0,1]],[[10,-1],[10,1]]],1)])
    lo,hi=quantile(d,D('.5'))
    assert float(lo)<=2.5<=float(hi)

def test_median_uses_length_weighted_planes():
    a=ROI({0:box(0,0,10,10),1:box(100,0,101,1)},{},0,'CLOSED_PLANAR')
    b=ROI({0:box(0,0,10,10),1:box(100,10,101,11)},{},0,'CLOSED_PLANAR')
    assert median_metrics(a,b)['median_mm']==0

def test_median_identity_and_paper_symmetry():
    p=box(0,0,10,10);q=box(1,1,3,3)
    assert median_metrics(roi(p),roi(p))['median_mm']==0
    r=median_metrics(roi(p),roi(q))
    assert r['median_mm']==max(r['a_median_mm'],r['b_median_mm'])
    assert abs(r['a_median_mm']-r['b_median_mm'])>1

def test_median_collinear_vertices_do_not_change_definition():
    p=box(0,0,10,10);q=box(1,1,3,3)
    pp=Polygon([(0,0),(2,0),(6,0),(10,0),(10,10),(0,10)])
    a=median_metrics(roi(p),roi(q));b=median_metrics(roi(pp),roi(q))
    assert abs(a['median_mm']-b['median_mm'])<=.002

def test_median_mass_gap_fails_closed():
    b=box(0,0,1,1);a=b.union(box(100,0,101,1))
    d=build_distribution([(segments(a)[0],segments(b)[0],1)])
    assert quantile(d,D('.5'))==(D(0),D(0))
    with pytest.raises(AmbiguousQuantileError):median_metrics(roi(a),roi(b))

def test_missing_planes_conventions():
    p=box(0,0,1,1);a=ROI({0:p,1:p},{},0,'CLOSED_PLANAR');b=roi(p)
    with pytest.raises(ValueError):median_metrics(a,b)
    assert median_metrics(a,b,missing_plane_policy='exclude')['median_mm']==0
    r=apl_metrics(a,b,[0])[0]
    assert r['apl_mm']==4 and r['napl']==.5 and r['unmatched_reference_planes']==1
    assert apl_metrics(b,a,[0])[0]['apl_mm']==0

@pytest.mark.parametrize('tau,expected',[(0,40),(1.999999,40),(2,16),(3,0)])
def test_apl_analytical_threshold(tau,expected):
    r=apl_metrics(roi(box(0,0,10,10)),roi(box(2,2,8,8)),[tau])[0]
    assert r['apl_mm']==pytest.approx(expected,abs=1e-10)
    assert r['napl']==pytest.approx(expected/40,abs=1e-12)

@pytest.mark.parametrize('angle',[0,10,13,17,30,37,45,60,90])
def test_rotated_threshold_uses_original_endpoints(angle):
    a=rotate(box(2,2,8,8),angle,origin=(0,0));b=rotate(box(0,0,10,10),angle,origin=(0,0))
    e,f=segments(a)[0],segments(b)[0]
    x,m,_=directed_coverage(e,f,[2],60);y,n=exhaustive(e,f,[2],100)
    assert float(x-m[0])==pytest.approx(float(y-n[0]),abs=1e-10)

def test_pruning_encloses_near_threshold_and_large_coordinates():
    for shift in [0,1e9,1e12]:
        a=translate(box(0,0,10,10),shift,shift);b=translate(box(2,2,8,8),shift,shift)
        for t in [np.nextafter(2.,0),2.,np.nextafter(2.,np.inf)]:
            x,m,_=directed_coverage(segments(a)[0],segments(b)[0],[t])
            y,n=exhaustive(segments(a)[0],segments(b)[0],[t],100)
            assert float(x-m[0])==pytest.approx(float(y-n[0]),abs=1e-10)

def test_apl_empty_reference_and_missing_target():
    p=roi(box(0,0,1,1))
    assert apl_metrics(empty(),p)[0]['napl'] is None
    r=apl_metrics(p,empty())[0]
    assert r['apl_mm']==4 and r['napl']==1
    assert apl_metrics(empty(),empty())[0]['apl_mm']==0

@pytest.mark.parametrize('tau',[-1,np.nan,np.inf])
def test_invalid_tolerance_rejected(tau):
    with pytest.raises(ValueError):apl_metrics(empty(),empty(),[tau])

def test_candidate_pruning_matches_exhaustive_random_edges():
    rng=np.random.default_rng(612)
    for i in range(8):
        a=rng.normal(size=(9,2,2))*5;b=rng.normal(size=(13,2,2))*5
        x,m,_=directed_coverage(a,b,[0,.01,1,3])
        y,n=exhaustive(a,b,[0,.01,1,3],100)
        assert abs(float(x-y))<1e-10
        assert np.max(np.abs(np.array(list(map(float,m)))-np.array(list(map(float,n)))))<1e-10

def test_tolerance_sensitivity_is_separate_from_metric_value():
    r=apl_sensitivity(roi(box(2,2,8,8)),roi(box(0,0,10,10)),2,1e-6)
    assert r['apl_mm']==0
    assert r['apl_sensitivity_span_mm']==24
    assert r['tolerance_mm']==2
