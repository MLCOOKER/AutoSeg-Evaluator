"""Vendored from native_contour_metrics 0.1.0, unmodified except for imports.

The kernel under ``autoseg_evaluator.vendor`` is byte-identical to the supplied
package and is pinned by ``tests/test_vendor_integrity.py``. This file is not:
its import lines were rewritten to the vendored package path. Nothing
else — no assertion, tolerance or fixture — was touched.
"""

import math
import numpy as np
import pytest
from scipy.integrate import quad
from shapely.geometry import Polygon, box
from autoseg_evaluator.vendor.native_contour_metrics.geometry import ROI
from autoseg_evaluator.vendor.native_contour_metrics.metrics import segments
from autoseg_evaluator.vendor.native_contour_metrics.native_distance_metrics import distance_metrics
from autoseg_evaluator.vendor.native_contour_metrics.segment_distance_oracle import build_distribution, quantile, summarize

def roi(p,z=0):return ROI({z:p},{},0,'CLOSED_PLANAR')

def test_maximum_between_vertices_and_arclength_quantile():
    source=[[[0,0],[10,0]]]
    target=[[[0,-1],[0,1]],[[10,-1],[10,1]]]
    d=build_distribution([(source,target,1)])
    lo,hi=quantile(d)
    assert float(d['hd'])==5
    assert float(d['mean'])==2.5
    assert float(lo)<=4.75<=float(hi)

def test_endpoint_distance_antiderivative():
    source=[[[0,0],[10,0]]];target=[[[0,3],[0,3]]]
    d=build_distribution([(source,target,1)])
    expected=(10*math.sqrt(109)+9*math.asinh(10/3))/20
    assert float(d['mean'])==pytest.approx(expected,abs=1e-12)
    assert float(d['hd'])==pytest.approx(math.sqrt(109),abs=1e-12)
    lo,hi=quantile(d)
    assert float(lo)<=math.sqrt(9+9.5**2)<=float(hi)

def test_arclength_mixture_not_equal_plane_weighting():
    d=build_distribution([([[[0,0],[10,0]]],[[[0,3],[10,3]]],1),
                          ([[[0,0],[2,0]]],[[[0,9],[2,9]]],4)])
    assert float(d['length'])==18
    assert float(d['mean'])==pytest.approx(102/18)
    lo,hi=quantile(d)
    assert float(lo)<=9<=float(hi)

@pytest.mark.parametrize('kind',['identity','shift','nested','disjoint','hole'])
def test_native_bounds_against_reference(kind):
    a=box(0,0,10,10)
    b={'identity':a,'shift':box(1,0,11,10),'nested':box(2,2,8,8),
       'disjoint':box(13,17,16,21),'hole':a.difference(box(2,2,4,4))}[kind]
    actual=distance_metrics(roi(a),roi(b))
    reference=summarize([(segments(a)[0],segments(b)[0],1)])
    for direction in ['a_','b_','']:
        for metric in ['hd','hd95','mean']:
            key=direction+metric
            assert actual[key+'_lower_mm']-1e-10<=reference[key+'_mm']<=actual[key+'_upper_mm']+1e-10

def test_symmetric_mean_convention_and_nested_exact_values():
    r=distance_metrics(roi(box(-3,-3,3,3)),roi(box(-5,-5,5,5)))
    outer=(12+2*(math.sqrt(8)+2*math.asinh(1)))/10
    assert r['a_mean_mm']==pytest.approx(2,abs=.001)
    assert r['mean_mm']==pytest.approx((2+outer)/2,abs=.001)
    assert abs(r['mean_mm']-r['mean_pooled_mm'])>.01

def test_missing_planes_are_an_explicit_convention():
    a=ROI({0:box(0,0,2,2),1:box(20,20,30,30)},{},0,'CLOSED_PLANAR')
    b=roi(box(0,0,2,2))
    with pytest.raises(ValueError,match='Different plane support'):distance_metrics(a,b)
    r=distance_metrics(a,b,missing_plane_policy='exclude')
    assert r['hd_mm']==0 and r['mean_mm']==0 and r['hd95_mm']==0
    assert r['excluded_a_planes']==1 and r['excluded_a_length_mm']==40
    with pytest.raises(ValueError,match='No common'):distance_metrics(roi(box(0,0,1,1),2),b,missing_plane_policy='exclude')

@pytest.mark.parametrize('error',[0,-1,float('nan'),float('inf')])
def test_bad_error_rejected(error):
    with pytest.raises(ValueError):distance_metrics(roi(box(0,0,1,1)),roi(box(0,0,1,1)),error_mm=error)

def test_resource_limit_and_empty_invalid_inputs():
    p=roi(box(0,0,1,1))
    with pytest.raises(RuntimeError,match='sample limit'):distance_metrics(p,p,max_points=1)
    with pytest.raises(ValueError):distance_metrics(ROI({},{},0,'CLOSED_PLANAR'),p)
    with pytest.raises(ValueError):distance_metrics(roi(Polygon([(0,0),(1,1),(0,1),(1,0)])),p)

def test_exact_95_percent_mass_gap_is_rejected_not_silently_scored():
    from autoseg_evaluator.vendor.native_contour_metrics.native_distance_metrics import AmbiguousQuantileError
    big=box(0,0,9.5,9.5)
    source=big.union(box(100,0,100.5,.5))
    # Exactly 38/40 mm coincides; generalized-inverse HD95 is zero.
    reference=summarize([(segments(source)[0],segments(big)[0],1)])
    assert reference['a_hd95_mm']==0
    with pytest.raises(AmbiguousQuantileError):distance_metrics(roi(source),roi(big))

def test_extreme_precision_request_fails_before_integer_overflow():
    p=roi(box(0,0,1,1))
    with pytest.raises(RuntimeError,match='sample limit'):distance_metrics(p,p,error_mm=1e-320)

def brute_distance(point,target):
    a=target[:,0];v=target[:,1]-a
    u=np.clip(np.sum((point-a)*v,axis=1)/np.sum(v*v,axis=1),0,1)
    return float(np.linalg.norm(point-(a+u[:,None]*v),axis=1).min())

def test_reference_mean_against_separate_numerical_quadrature():
    # A different integrand implementation: brute-force NumPy projection,
    # scipy QUADPACK integration, no GEOS distance or envelope evaluation.
    rng=np.random.default_rng(231)
    for _ in range(4):
        p=rng.normal(size=(5,2))*3;q=rng.normal(size=(6,2))*4
        src=np.stack([p,np.roll(p,-1,axis=0)],axis=1)
        dst=np.stack([q,np.roll(q,-1,axis=0)],axis=1)
        reference=build_distribution([(src,dst,1)])
        total=length=0.
        for a,b in src:
            ell=np.linalg.norm(b-a)
            value,err=quad(lambda t:brute_distance(a+t*(b-a),dst),0,1,epsabs=1e-9,limit=500,points=np.linspace(0,1,31))
            total+=ell*value;length+=ell
        assert float(reference['mean'])==pytest.approx(total/length,abs=2e-8)
