"""Vendored from native_contour_metrics 0.1.0, unmodified except for imports.

The kernel under ``autoseg_evaluator.vendor`` is byte-identical to the supplied
package and is pinned by ``tests/test_vendor_integrity.py``. This file is not:
its import lines were rewritten to the vendored package path. Nothing
else — no assertion, tolerance or fixture — was touched.
"""

import pytest
from shapely.geometry import box,Polygon
from autoseg_evaluator.vendor.native_contour_metrics import compare,from_planes,from_json,AmbiguousQuantileError

def test_six_metrics_and_missing_plane_policy():
    a=from_planes({0:box(0,0,10,10),1:box(0,0,10,10)})
    b=from_planes({0:box(2,2,8,8)})
    with pytest.raises(ValueError,match='Different plane support'):compare(a,b)
    r=compare(a,b,[2.],missing_plane_policy='exclude')
    assert set(r['distance'])=={'hd100','hd95','mean_contour_distance','median_contour_distance'}
    assert r['apl'][0]['reference_to_test']['apl_mm']==56
    assert r['apl'][0]['reference_to_test']['napl']==.7
    assert r['plane_coverage']['excluded_a_planes']==1

def test_json_holes_and_multicomponents():
    shape=[dict(id=0,polygons=[dict(shell=[[0,0],[10,0],[10,10],[0,10]],holes=[[[2,2],[4,2],[4,4],[2,4]]]),dict(shell=[[20,0],[21,0],[21,1],[20,1]])])]
    a=from_json(shape);r=compare(a,a,[0.])
    assert r['distance']['hd100']['mm']==0
    assert r['apl'][0]['reference_to_test']['apl_mm']==0

def test_no_common_planes_is_not_zero():
    with pytest.raises(ValueError,match='No common'):compare(from_planes({0:box(0,0,1,1)}),from_planes({1:box(0,0,1,1)}),missing_plane_policy='exclude')

@pytest.mark.parametrize('taus',[[],[-1],[float('nan')],[1,1]])
def test_invalid_tolerances(taus):
    a=from_planes({0:box(0,0,1,1)})
    with pytest.raises(ValueError):compare(a,a,taus)

def test_reject_3d_input_and_duplicate_json_ids():
    with pytest.raises(ValueError):from_planes({0:Polygon([(0,0,0),(1,0,0),(0,1,0)])})
    with pytest.raises(ValueError):from_json([dict(id=0,polygons=[dict(shell=[[0,0],[1,0],[0,1]])])]*2)

def test_uncertain_quantile_is_not_silently_replaced():
    b=box(0,0,1,1);a=b.union(box(100,0,101,1))
    with pytest.raises(AmbiguousQuantileError):compare(from_planes({0:a}),from_planes({0:b}))
