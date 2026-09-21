"""Vendored from native_contour_metrics 0.1.0, unmodified except for imports.

The kernel under ``autoseg_evaluator.vendor`` is byte-identical to the supplied
package and is pinned by ``tests/test_vendor_integrity.py``. This file is not:
its import lines were rewritten to the vendored package path. Nothing
else — no assertion, tolerance or fixture — was touched.
"""

import pytest
from autoseg_evaluator.vendor.native_contour_metrics.capsule_oracle import score_planes

def edges(vertices):return list(zip(vertices,vertices[1:]+vertices[:1]))
def square(x,y,s):return edges([(x,y),(x+s,y),(x+s,y+s),(x,y+s)])

def test_analytic_shift():
    r=score_planes({0:square(0,0,10)},{0:square(1,0,10)},[.5,1])
    assert r[0]['apl_a_mm']==pytest.approx(20)
    assert r[0]['contour_dice']==pytest.approx(.5)
    assert r[1]['apl_a_mm']==0 and r[1]['contour_dice']==1

def test_endpoint_disks_and_nested_distance():
    r=score_planes({0:square(2,2,6)},{0:square(0,0,10)},[2])
    assert r[0]['apl_a_mm']==0
    assert r[0]['apl_b_mm']==pytest.approx(16)
    assert r[0]['contour_dice']==pytest.approx(.75)

def test_missing_empty_identity():
    a={0:square(0,0,4)}
    assert score_planes(a,a,[0])[0]['contour_dice']==1
    r=score_planes(a,{1:square(0,0,4)},[3])[0]
    assert r['apl_a_mm']==16 and r['contour_dice']==0
    assert score_planes({}, {},[1])[0]['contour_dice'] is None
