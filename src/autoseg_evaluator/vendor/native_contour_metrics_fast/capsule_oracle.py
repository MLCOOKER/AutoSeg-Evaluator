"""Slow audit oracle: Decimal segment/capsule intersections, no GEOS distances.

Each target segment's closed Euclidean tau-neighbourhood is a rectangle plus
two endpoint disks. Intersect a source segment with these three sets in its
parameter t, union intervals, and multiply covered t-length by source length.
This computes threshold coverage without sampling or nearest-neighbour trees.
"""
from decimal import Decimal as D,localcontext

ZERO=D(0);ONE=D(1)
def dec(x):return D.from_float(float(x))
def dot(a,b):return a[0]*b[0]+a[1]*b[1]
def sub(a,b):return (a[0]-b[0],a[1]-b[1])
def cross(a,b):return a[0]*b[1]-a[1]*b[0]

def clip(lo,hi):
    lo=max(ZERO,lo);hi=min(ONE,hi)
    return (lo,hi) if lo<=hi else None

def linear_range(c,k,lo,hi):
    if not k:return (ZERO,ONE) if lo<=c<=hi else None
    a=(lo-c)/k;b=(hi-c)/k
    return clip(min(a,b),max(a,b))

def disk_interval(p,v,c,r2,A):
    w=sub(p,c);B=2*dot(v,w);C=dot(w,w)-r2
    disc=B*B-4*A*C
    if disc<0:return None
    root=disc.sqrt()
    return clip((-B-root)/(2*A),(-B+root)/(2*A))

def capsule_intervals(p,v,a,b,r,A):
    w=sub(b,a);L2=dot(w,w);ans=[]
    for c in [a,b]:
        iv=disk_interval(p,v,c,r*r,A)
        if iv:ans.append(iv)
    if L2:
        pa=sub(p,a);projection=linear_range(dot(pa,w),dot(v,w),ZERO,L2)
        if projection:
            width=r*L2.sqrt();perp=linear_range(cross(w,pa),cross(w,v),-width,width)
            if perp:
                iv=clip(max(projection[0],perp[0]),min(projection[1],perp[1]))
                if iv:ans.append(iv)
    return ans

def covered_parameter(intervals):
    if not intervals:return ZERO
    intervals.sort();lo,hi=intervals[0];covered=ZERO
    for a,b in intervals[1:]:
        if a<=hi:hi=max(hi,b)
        else:covered+=hi-lo;lo,hi=a,b
    return covered+hi-lo

def directed_coverage(source_edges,target_edges,taus,precision=60):
    with localcontext() as ctx:
        ctx.prec=precision
        src=[(tuple(map(dec,a)),tuple(map(dec,b))) for a,b in source_edges]
        dst=[(tuple(map(dec,a)),tuple(map(dec,b))) for a,b in target_edges]
        tt=[dec(t) for t in taus];matched=[ZERO for t in tt];total=ZERO
        for p,end in src:
            v=sub(end,p);A=dot(v,v)
            if not A:continue
            length=A.sqrt();total+=length
            for j,r in enumerate(tt):
                intervals=[]
                for a,b in dst:
                    # Exact Decimal AABB rejection, expanded by the tolerance.
                    if any(max(p[k],end[k])<min(a[k],b[k])-r or min(p[k],end[k])>max(a[k],b[k])+r for k in [0,1]):continue
                    intervals.extend(capsule_intervals(p,v,a,b,r,A))
                matched[j]+=length*covered_parameter(intervals)
        return total,matched

def score_planes(a,b,taus,precision=60):
    """Inputs: z -> raw float edge arrays, independently extracted by caller."""
    with localcontext() as ctx:
        ctx.prec=precision;lengths=[];coverages=[]
        for source,target in [(a,b),(b,a)]:
            total=ZERO;matched=[ZERO for t in taus]
            for z,edges in source.items():
                length,covered=directed_coverage(edges,target.get(z,[]),taus,precision)
                total+=length;matched=[v+w for v,w in zip(matched,covered)]
            lengths.append(total);coverages.append(matched)
        denom=sum(lengths)
        return [dict(tolerance_mm=float(t),apl_a_mm=float(lengths[0]-coverages[0][i]),apl_b_mm=float(lengths[1]-coverages[1][i]),contour_dice=float((coverages[0][i]+coverages[1][i])/denom) if denom else None) for i,t in enumerate(taus)]
