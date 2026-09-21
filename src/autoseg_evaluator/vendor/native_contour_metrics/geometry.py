"""Strict common-plane parser; no silent repair, interpolation or registration."""
from dataclasses import dataclass
import numpy as np
import shapely
from shapely.geometry import Polygon

@dataclass
class ROI:
    planes: dict
    rings: dict
    vertices: int
    geometric_type: str
    world_rings: dict | None = None

class Unsupported(ValueError):pass

def parse_roi(ds, number, grid):
    rr=[r for r in ds.StructureSetROISequence if int(r.ROINumber)==number]
    if len(rr)!=1 or str(rr[0].ReferencedFrameOfReferenceUID)!=grid['frame']:raise Unsupported('Frame mismatch or ambiguous ROI')
    items=[r for r in ds.ROIContourSequence if int(r.ReferencedROINumber)==number]
    if len(items)!=1 or not getattr(items[0],'ContourSequence',None):raise Unsupported('Missing contour sequence')
    origin=np.asarray(grid['origin']);basis=np.asarray(grid['basis']);spacing=np.asarray(grid['spacing']);size=grid['size']
    types={str(c.ContourGeometricType) for c in items[0].ContourSequence}
    if types not in [{'CLOSED_PLANAR'},{'CLOSEDPLANAR_XOR'}]:raise Unsupported('Unsupported or mixed geometric type')
    typ=next(iter(types));rings={};polys={};world_rings={};nvertices=0
    for ci in items[0].ContourSequence:
        a=np.asarray(ci.ContourData,float)
        if len(a)%3 or len(a)<9 or not np.isfinite(a).all():raise Unsupported('Invalid coordinates')
        xyz=a.reshape(-1,3)
        if len(xyz)!=int(ci.NumberOfContourPoints):raise Unsupported('Point count mismatch')
        local=(xyz-origin)@basis;index=local/spacing;z=int(np.floor(index[0,2]+.5))
        if z<0 or z>=size[2] or np.max(abs(local[:,2]-z*spacing[2]))>1e-3:raise Unsupported('Off-grid or nonplanar contour')
        if (index[:,:2]<-.5).any() or (index[:,:2]>np.asarray(size[:2])-.5).any():raise Unsupported('Out of CT bounds')
        for ref in getattr(ci,'ContourImageSequence',[]):
            if grid['sops'].get(str(ref.ReferencedSOPInstanceUID))!=z:raise Unsupported('Referenced CT plane mismatch')
        p=Polygon(local[:,:2])
        if not p.is_valid or p.area<=0:raise Unsupported('Invalid/degenerate ring: '+str(shapely.is_valid_reason(p)))
        rings.setdefault(z,[]).append(index[:,:2]);world_rings.setdefault(z,[]).append(xyz.copy());polys.setdefault(z,[]).append(p);nvertices+=len(xyz)
    result={}
    for z,ps in polys.items():
        if typ=='CLOSEDPLANAR_XOR':
            p=ps[0]
            for q in ps[1:]:p=p.symmetric_difference(q)
        else:
            for i,p in enumerate(ps):
                for q in ps[i+1:]:
                    if p.intersection(q).area>1e-8:raise Unsupported('Ambiguous overlapping CLOSED_PLANAR loops')
            p=shapely.union_all(ps)
        if not p.is_valid:raise Unsupported('Invalid composed region')
        if not p.is_empty:result[z]=p
    return ROI(result,rings,nvertices,typ,world_rings)

