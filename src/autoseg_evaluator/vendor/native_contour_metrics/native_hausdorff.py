"""Bounded continuous-segment Hausdorff, restricted to corresponding planes."""
import numpy as np
import shapely
from .metrics import segments

def directed_edges_hausdorff(source,target,error_mm=.005,max_depth=30):
    if not np.isfinite(error_mm) or error_mm<=0:raise ValueError('Hausdorff error must be finite and positive')
    source=np.asarray(source,float).reshape(-1,2,2);target=np.asarray(target,float).reshape(-1,2,2)
    if not len(source):return (0.,0.)
    if not len(target):return (float('inf'),float('inf'))
    if not np.isfinite(source).all() or not np.isfinite(target).all():raise ValueError('Nonfinite segment coordinates')
    lines=shapely.linestrings(target);tree=shapely.STRtree(lines)
    lower=sealed_upper=0.;edges=source
    for depth in range(max_depth+1):
        if len(edges)>1000000:raise RuntimeError('Hausdorff active-edge limit exceeded')
        mid=edges.mean(axis=1);w=np.linalg.norm(edges[:,1]-edges[:,0],axis=1)
        ix,dd=tree.query_nearest(shapely.points(mid),return_distance=True,all_matches=False)
        d=np.empty(len(edges));nearest=np.empty(len(edges),int);d[ix[0]]=dd;nearest[ix[0]]=ix[1]
        _,endpoint_dist=tree.query_nearest(shapely.points(edges.reshape(-1,2)),return_distance=True,all_matches=False)
        lower=max(lower,float(np.max(d)),float(np.max(endpoint_dist)))
        max_fixed=np.maximum(shapely.distance(shapely.points(edges[:,0]),lines[nearest]),shapely.distance(shapely.points(edges[:,1]),lines[nearest]))
        upper=np.minimum(d+w/2,max_fixed)
        keep=upper>lower+error_mm
        if (~keep).any():sealed_upper=max(sealed_upper,float(upper[~keep].max()))
        if not keep.any():return lower,max(lower,sealed_upper)
        if depth==max_depth:raise RuntimeError('Hausdorff subdivision depth exhausted')
        ee=edges[keep];mm=mid[keep]
        edges=np.concatenate([np.stack([ee[:,0],mm],axis=1),np.stack([mm,ee[:,1]],axis=1)])
    raise RuntimeError('Unreachable Hausdorff exit')

