"""Explicit legacy nested-CLOSED_PLANAR compatibility; never a silent default.

Nesting identifies a possible even/odd region, not proof of vendor intent.
Use only after adopting that interpretation for a known export convention.
"""
import copy
import numpy as np
from shapely.geometry import Polygon
from .geometry import parse_roi,Unsupported

def validate_nested(polygons):
    for p in polygons:
        if not p.is_valid or p.is_empty:raise Unsupported('Invalid ring')
    nesting=0
    for i,p in enumerate(polygons):
        for q in polygons[i+1:]:
            if not p.boundary.disjoint(q.boundary):raise Unsupported('Touching, duplicate or crossing CLOSED_PLANAR boundaries')
            if p.contains(q) or q.contains(p):nesting+=1
            elif not p.disjoint(q):raise Unsupported('Partial CLOSED_PLANAR overlap')
    return nesting

def compose_nested(polygons):
    validate_nested(polygons)
    p=polygons[0]
    for q in polygons[1:]:p=p.symmetric_difference(q)
    if not p.is_valid:raise Unsupported('Invalid nested composition')
    return p

def parse_compatible(ds,number,grid,allow_nested=False):
    try:return parse_roi(ds,number,grid),dict(policy='strict',nested_plane_count=0)
    except Unsupported as e:
        if not allow_nested or 'Ambiguous overlapping CLOSED_PLANAR loops' not in str(e):raise
    items=[r for r in ds.ROIContourSequence if int(r.ReferencedROINumber)==number]
    rings={}
    for c in items[0].ContourSequence:
        if c.ContourGeometricType!='CLOSED_PLANAR':raise Unsupported('Compatibility requires all CLOSED_PLANAR contours')
        xyz=np.asarray(c.ContourData,float).reshape(-1,3)
        local=(xyz-np.asarray(grid['origin']))@np.asarray(grid['basis'])
        z=int(np.floor(local[0,2]/grid['spacing'][2]+.5))
        rings.setdefault(z,[]).append(Polygon(local[:,:2]))
    counts=[validate_nested(ps) for ps in rings.values()]
    # Only this temporary adapter copy changes interpretation; the original
    # dataset, source files and frozen parser remain untouched.
    temp=copy.deepcopy(ds)
    item=next(r for r in temp.ROIContourSequence if int(r.ReferencedROINumber)==number)
    for c in item.ContourSequence:c.ContourGeometricType='CLOSEDPLANAR_XOR'
    return parse_roi(temp,number,grid),dict(policy='explicit_legacy_nested_even_odd',nested_plane_count=sum(n>0 for n in counts),
        note='Compatibility interpretation requiring vendor/export confirmation; not implied by CLOSED_PLANAR alone')
