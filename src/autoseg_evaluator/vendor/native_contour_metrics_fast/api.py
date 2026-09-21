"""Experimental compiled continuous-segment metric API (separate from v0.1).

Returns analytic floating evaluations, not the old sampling intervals. APL
fragile segments are recomputed with the original-coordinate Decimal routine.
Platform-specific libraries are selected by the package loader.
"""
from pathlib import Path
from dataclasses import dataclass
from collections import Counter
import ctypes,math
import numpy as np
import shapely
from .metrics import segments
from .errors import AmbiguousQuantileError
from .capsule_oracle import directed_coverage

from .loader import library

@dataclass
class Prepared:
    edges:dict
    keys:dict
    lengths:dict

def prepare(roi):
    edges={};keys={};lengths={}
    for z,p in roi.planes.items():
        if p.geom_type not in ['Polygon','MultiPolygon'] or p.is_empty or not p.is_valid or shapely.has_z(p) or not np.isfinite(p.bounds).all():
            raise ValueError('Expected valid finite 2D polygon regions')
        e,_=segments(p);edges[z]=np.ascontiguousarray(e,np.float64);keys[z]=p.wkb
        lengths[z]=math.fsum(math.hypot(*(b-a)) for a,b in e)
    return Prepared(edges,keys,lengths)

def direction(a,b,taus,error_mm=.001):
    common=set(a.edges)&set(b.edges)
    if not common:raise ValueError('No common planes')
    grouped=Counter();lookup={}
    for z in common:
        key=(a.keys[z],b.keys[z]);grouped[key]+=1;lookup[key]=(a.edges[z],b.edges[z])
    src=[];tgt=[];groups=[];s0=t0=0
    for key,count in grouped.items():
        p,q=lookup[key];src.append(p);tgt.append(q);groups.append([s0,len(p),t0,len(q),count]);s0+=len(p);t0+=len(q)
    src=np.ascontiguousarray(np.concatenate(src));tgt=np.ascontiguousarray(np.concatenate(tgt));groups=np.array(groups,np.int64)
    taus=np.ascontiguousarray(taus,dtype=np.float64)
    output=np.zeros(9+len(taus));flags=np.zeros((len(src),len(taus)),np.uint8)
    status=library().ncm_direction(src,len(src),tgt,len(tgt),groups,len(groups),taus,len(taus),error_mm,output,flags)
    if status:raise RuntimeError(f'Compiled native calculation failed, status={status}')
    for k,label in [(7,'median'),(8,'HD95')]:
        if output[k]>2*error_mm:raise AmbiguousQuantileError(f'{label} lies near a CDF mass gap ({output[k]:g} mm)')
    fallbacks=int(np.count_nonzero(flags));fallback_edges=int(np.count_nonzero(flags.any(axis=1)))
    for i in np.flatnonzero(flags.any(axis=1)):
        g=groups[np.searchsorted(groups[:,0],i,side='right')-1];target=tgt[g[2]:g[2]+g[3]]
        kk=np.flatnonzero(flags[i]);length,matched=directed_coverage(src[i:i+1],target,taus[kk],precision=60)
        for k,m in zip(kk,matched):output[9+k]+=float(length-m)*int(g[4])
    missing=math.fsum(a.lengths[z] for z in set(a.edges)-common)
    total=math.fsum(a.lengths.values())
    return dict(hd_mm=output[1],mean_mm=output[2],median_mm=output[3],hd95_mm=output[4],
        length_mm=total,common_length_mm=output[0],missing_length_mm=missing,
        apl=[dict(tolerance_mm=t,apl_mm=output[9+i]+missing,napl=(output[9+i]+missing)/total) for i,t in enumerate(taus)],
        pieces=int(output[5]),candidate_segments=int(output[6]),decimal_fallbacks=fallbacks,decimal_fallback_edges=fallback_edges,
        quantile_mass_gap_median_mm=output[7],quantile_mass_gap_hd95_mm=output[8])

def compare(a,b,taus=(3.,),error_mm=.001,missing_plane_policy='error'):
    if not isinstance(a,Prepared):a=prepare(a)
    if not isinstance(b,Prepared):b=prepare(b)
    tt=np.asarray(taus,float)
    if tt.ndim!=1 or not len(tt) or not np.isfinite(tt).all() or (tt<0).any():raise ValueError('Invalid tolerances')
    if not np.isfinite(error_mm) or error_mm<=0:raise ValueError('Invalid numerical error setting')
    if missing_plane_policy not in ['error','exclude']:raise ValueError('Invalid missing-plane policy')
    if set(a.edges)!=set(b.edges) and missing_plane_policy=='error':raise ValueError('Different plane support')
    aa=direction(a,b,tt,error_mm);bb=direction(b,a,tt,error_mm)
    result={}
    for side,d in [('a',aa),('b',bb)]:
        result.update({side+'_'+key:value for key,value in d.items() if key!='apl'})
    for key in ['hd_mm','hd95_mm','median_mm']:result[key]=max(aa[key],bb[key])
    result['mean_mm']=(aa['mean_mm']+bb['mean_mm'])/2
    result['apl']=[dict(tolerance_mm=float(t),apl_a_mm=float(aa['apl'][i]['apl_mm']),apl_b_mm=float(bb['apl'][i]['apl_mm']),
                        napl_a=float(aa['apl'][i]['napl']),napl_b=float(bb['apl'][i]['napl'])) for i,t in enumerate(tt)]
    result.update(joint_planes=len(set(a.edges)&set(b.edges)),excluded_a_planes=len(set(a.edges)-set(b.edges)),excluded_b_planes=len(set(b.edges)-set(a.edges)),
                  calculation='compiled continuous-segment distance envelope; no dense uniform sampling',
                  bound_scope='analytic floating evaluation; no certified roundoff interval')
    return result
