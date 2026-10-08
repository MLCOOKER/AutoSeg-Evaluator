"""Vendored from native_contour_metrics_fast 0.2.0.dev2, unmodified except for imports.

The kernel under ``autoseg_evaluator.vendor`` is byte-identical to the supplied
package and is pinned by ``tests/test_vendor_integrity.py``. This file is not:
its import lines were rewritten to the vendored package path. Nothing
else — no assertion, tolerance or fixture — was touched.
"""

import numpy as np
import pytest
from shapely.geometry import box,Polygon
from shapely.affinity import rotate,translate
from autoseg_evaluator.vendor.native_contour_metrics_fast.geometry import ROI,Unsupported
from autoseg_evaluator.vendor.native_contour_metrics_fast.api import compare,prepare,Prepared,direction
from autoseg_evaluator.vendor.native_contour_metrics_fast.errors import AmbiguousQuantileError
from autoseg_evaluator.vendor.native_contour_metrics_fast.polygon_compat import compose_nested,validate_nested,parse_compatible
from autoseg_evaluator.vendor.native_contour_metrics_fast.capsule_oracle import directed_coverage
from autoseg_evaluator.vendor.native_contour_metrics_fast.metrics import segments

def roi(p):return ROI({0:p},{},0,'CLOSED_PLANAR')

def test_continuous_maximum_and_quantiles_between_vertices():
    a=Prepared({0:np.array([[[0.,0.],[10.,0.]]])},{0:b'a'},{0:10.})
    b=Prepared({0:np.array([[[0.,-1.],[0.,1.]],[[10.,-1.],[10.,1.]]])},{0:b'b'},{0:4.})
    r=direction(a,b,[3.])
    assert r['hd_mm']==pytest.approx(5,abs=1e-9)
    assert r['mean_mm']==pytest.approx(2.5,abs=1e-9)
    assert r['median_mm']==pytest.approx(2.5,abs=1e-9)
    assert r['hd95_mm']==pytest.approx(4.75,abs=1e-9)
    assert r['apl'][0]['apl_mm']==pytest.approx(4,abs=1e-10)

@pytest.mark.parametrize('angle',[0,10,13,17,30,37,45,60,90])
def test_threshold_plateaus_recompute_original_edges(angle):
    p=rotate(box(2,2,8,8),angle,origin=(0,0));q=rotate(box(0,0,10,10),angle,origin=(0,0))
    r=compare(roi(p),roi(q),[2.])
    for side,a,b in [('a',p,q),('b',q,p)]:
        ll,mm=directed_coverage(segments(a)[0],segments(b)[0],[2.],100)
        assert r['apl'][0][f'apl_{side}_mm']==pytest.approx(float(ll-mm[0]),abs=1e-8)
    assert r['a_decimal_fallback_edges']>0

@pytest.mark.parametrize('fraction',[.5,.95])
def test_percentile_mass_gap_is_rejected(fraction):
    p=box(0,0,1,1) if fraction==.5 else box(0,0,9.5,9.5)
    side=1 if fraction==.5 else .5
    with pytest.raises(AmbiguousQuantileError):compare(roi(p.union(box(100,0,100+side,side))),roi(p))

def test_distinct_planes_exceed_old_uniform_sampling_budget():
    a=ROI({z:box(0,0,10+z*.001,10+z*.001) for z in range(120)},{},0,'CLOSED_PLANAR')
    b=ROI({z:translate(p,1,0) for z,p in a.planes.items()},{},0,'CLOSED_PLANAR')
    assert sum(p.length for p in a.planes.values())/.002>2000000
    r=compare(a,b,[1.])
    assert r['hd_mm']==pytest.approx(1,abs=1e-9)
    assert r['a_pieces']<10000

def test_missing_planes_keep_apl_and_exclude_distances_explicitly():
    p=box(0,0,10,10);a=ROI({0:p,1:p},{},0,'CLOSED_PLANAR');b=roi(p)
    with pytest.raises(ValueError):compare(a,b)
    r=compare(a,b,[0],missing_plane_policy='exclude')
    assert r['hd_mm']==0
    assert r['apl'][0]['apl_a_mm']==40
    assert r['apl'][0]['napl_a']==.5

@pytest.mark.parametrize('shift',[0,1e9,1e12])
def test_translation_and_symmetrization(shift):
    p=translate(box(0,0,10,10),shift,shift);q=translate(box(1,1,3,3),shift,shift)
    r=compare(roi(p),roi(q))
    assert r['mean_mm']==(r['a_mean_mm']+r['b_mean_mm'])/2
    assert r['median_mm']==pytest.approx(7,abs=1e-8)

def test_topology_three_nested_rings_and_order_independence():
    rings=[box(0,0,10,10),box(2,2,8,8),box(4,4,6,6)]
    assert compose_nested(rings).area==68
    assert compose_nested(rings[::-1]).equals(compose_nested(rings))

@pytest.mark.parametrize('other',[box(0,0,10,10),box(5,5,15,15),box(10,0,20,10),box(0,2,4,4)])
def test_topology_rejects_duplicates_partial_overlap_and_touch(other):
    with pytest.raises(Unsupported):validate_nested([box(0,0,10,10),other])

def test_disjoint_shells_preserved_and_invalid_input_rejected():
    assert compose_nested([box(0,0,1,1),box(3,0,4,1)]).area==2
    with pytest.raises(ValueError):compare(ROI({},{},0,'CLOSED_PLANAR'),roi(box(0,0,1,1)))
    with pytest.raises(ValueError):prepare(roi(Polygon([(0,0),(1,1),(0,1),(1,0)])))

@pytest.mark.parametrize('inner,expected_area',[(box(2,2,8,8),64),(box(5,5,15,15),None)])
def test_compatibility_adapter_requires_opt_in_and_preserves_dataset(inner,expected_area):
    from types import SimpleNamespace as NS
    contours=[]
    for p in [box(0,0,10,10),inner]:
        xy=np.asarray(p.exterior.coords)[:-1]
        xyz=np.column_stack([xy,np.ones(len(xy))])
        contours.append(NS(ContourGeometricType='CLOSED_PLANAR',NumberOfContourPoints=len(xy),ContourData=xyz.ravel().tolist()))
    ds=NS(StructureSetROISequence=[NS(ROINumber=7,ReferencedFrameOfReferenceUID='f')],ROIContourSequence=[NS(ReferencedROINumber=7,ContourSequence=contours)])
    grid=dict(origin=[0,0,0],basis=np.eye(3).tolist(),spacing=[1,1,1],size=[20,20,3],frame='f',sops={})
    with pytest.raises(Unsupported):parse_compatible(ds,7,grid)
    if expected_area is None:
        with pytest.raises(Unsupported):parse_compatible(ds,7,grid,allow_nested=True)
    else:
        result,audit=parse_compatible(ds,7,grid,allow_nested=True)
        assert result.planes[1].area==expected_area
        assert audit['nested_plane_count']==1
        assert audit['policy']=='explicit_legacy_nested_even_odd'
    assert all(c.ContourGeometricType=='CLOSED_PLANAR' for c in contours)
