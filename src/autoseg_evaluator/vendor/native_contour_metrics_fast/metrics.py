"""Unchanged segment extraction and weighted quantile helpers."""
import numpy as np
import shapely

def segments(p):
    rows=[]
    for line in shapely.get_parts(p.boundary):
        a=shapely.get_coordinates(line)
        rows.extend(np.stack([a[:-1],a[1:]],axis=1))
    a=np.asarray(rows).reshape(-1,2,2)
    lengths=np.linalg.norm(a[:,1]-a[:,0],axis=1)
    return a[lengths>0],lengths[lengths>0]

def quantile(d,w,p=.95):
    if len(d)==0 or not w.sum():return float('nan')
    order=np.argsort(d);cum=np.cumsum(w[order]);i=min(np.searchsorted(cum,p*cum[-1]),len(order)-1)
    return float(d[order[i]])
