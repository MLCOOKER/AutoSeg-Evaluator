"""High-precision reference for continuous point-to-segment distances.

Independent of GEOS distance/STRtree and of midpoint sampling. On each source
segment, squared distance to each target segment is a piecewise quadratic:
two endpoint features and one supporting-line feature. Divide-and-conquer
merges their lower envelopes. Envelope extrema give HD, integration gives
mean, and inversion of its exact-arclength CDF gives HD95.

Decimal precision controls arithmetic, not symbolic proof. Results must be
checked under increased precision; no general certified-rounding claim is made.
"""
from decimal import Decimal as D, localcontext
from dataclasses import dataclass
import math

ZERO = D(0)
ONE = D(1)

def dec(v):return D.from_float(float(v))
def dot(a,b):return a[0]*b[0]+a[1]*b[1]
def sub(a,b):return a[0]-b[0],a[1]-b[1]
def cross(a,b):return a[0]*b[1]-a[1]*b[0]

@dataclass(frozen=True)
class Piece:
    lo: D
    hi: D
    A: D
    B: D
    C: D

    def value(self,t):return (self.A*t+self.B)*t+self.C

def roots(A,B,C):
    if not A:
        return [] if not B else [-C/B]
    disc=B*B-4*A*C
    if disc<0:return []
    s=disc.sqrt()
    if not s:return [-B/(2*A)]
    q=-(B+(s if B>=0 else -s))/2
    return sorted([q/A,C/q])

def target_function(p,v,a,b):
    w=sub(b,a);w2=dot(w,w);r=sub(p,a)
    speed2=dot(v,v)
    def endpoint(point):
        rr=sub(p,point)
        return speed2,2*dot(v,rr),dot(rr,rr)
    if not w2:
        return [Piece(ZERO,ONE,*endpoint(a))]
    u0=dot(r,w)/w2;u1=dot(v,w)/w2
    cuts=[ZERO,ONE]
    if u1:
        cuts += [t for t in [-u0/u1,(ONE-u0)/u1] if ZERO<t<ONE]
    cuts=sorted(set(cuts));out=[]
    for lo,hi in zip(cuts,cuts[1:]):
        u=u0+u1*(lo+hi)/2
        if u<0:coeff=endpoint(a)
        elif u>1:coeff=endpoint(b)
        else:
            c=cross(w,r);k=cross(w,v)
            coeff=k*k/w2,2*k*c/w2,c*c/w2
        out.append(Piece(lo,hi,*coeff))
    return out

def merge_envelopes(left,right):
    i=j=0;out=[]
    while i<len(left) and j<len(right):
        p,q=left[i],right[j]
        lo=max(p.lo,q.lo);hi=min(p.hi,q.hi)
        if lo<hi:
            cuts=[lo]+[t for t in roots(p.A-q.A,p.B-q.B,p.C-q.C) if lo<t<hi]+[hi]
            cuts=sorted(set(cuts))
            for a,b in zip(cuts,cuts[1:]):
                mid=(a+b)/2
                pick=p if p.value(mid)<=q.value(mid) else q
                item=Piece(a,b,pick.A,pick.B,pick.C)
                if out and out[-1].hi==a and (out[-1].A,out[-1].B,out[-1].C)==(item.A,item.B,item.C):
                    old=out.pop();item=Piece(old.lo,b,item.A,item.B,item.C)
                out.append(item)
        if p.hi<=q.hi:i+=1
        if q.hi<=p.hi:j+=1
    return out

def envelope(source,target):
    p,end=source;v=sub(end,p);speed=dot(v,v).sqrt()
    if not speed:return [],ZERO
    groups=[target_function(p,v,a,b) for a,b in target]
    if not groups:raise ValueError('Target boundary is empty')
    while len(groups)>1:
        groups=[merge_envelopes(groups[i],groups[i+1]) if i+1<len(groups) else groups[i]
                for i in range(0,len(groups),2)]
    return groups[0],speed

def nonnegative(x,scale=ONE):
    # Cancellation at an exact zero can leave tiny signed residuals in Decimal.
    if x<0 and abs(x)>D('1e-35')*max(ONE,abs(scale)):
        raise ArithmeticError('Negative squared distance beyond rounding allowance')
    return max(ZERO,x)

def integral(piece):
    A,B,C=piece.A,piece.B,piece.C
    lo,hi=piece.lo,piece.hi
    if not A:
        if not B:return (hi-lo)*nonnegative(C).sqrt()
        return 2*(nonnegative(B*hi+C)**D('1.5')-nonnegative(B*lo+C)**D('1.5'))/(3*B)
    s=A.sqrt();h2=nonnegative(C-B*B/(4*A),C)
    def primitive(t):
        u=s*t+B/(2*s)
        if not h2:return u*abs(u)/2
        h=h2.sqrt();z=abs(u)/h
        asinh=(z+(z*z+1).sqrt()).ln()
        if u<0:asinh=-asinh
        return (u*(u*u+h2).sqrt()+h2*asinh)/2
    return (primitive(hi)-primitive(lo))/s

def coverage(piece,tau):
    A,B,C=piece.A,piece.B,piece.C-tau*tau
    cuts=[piece.lo]+[t for t in roots(A,B,C) if piece.lo<t<piece.hi]+[piece.hi]
    cuts=sorted(set(cuts));total=ZERO
    for lo,hi in zip(cuts,cuts[1:]):
        mid=(lo+hi)/2
        if (A*mid+B)*mid+C<=0:total+=hi-lo
    return total

def build_distribution(groups,precision=60):
    """groups is [(source_edges, target_edges, repeated_plane_count), ...]."""
    with localcontext() as ctx:
        ctx.prec=precision
        records=[];length=area=maximum=ZERO
        for source,target,count in groups:
            src=[(tuple(map(dec,a)),tuple(map(dec,b))) for a,b in source]
            dst=[(tuple(map(dec,a)),tuple(map(dec,b))) for a,b in target]
            for edge in src:
                pieces,speed=envelope(edge,dst)
                if not speed:continue
                scale=speed*count
                length+=scale
                for piece in pieces:
                    # Every distance-to-feature quadratic is convex: a piece's
                    # maximum is attained at one of its endpoints.
                    maximum=max(maximum,piece.value(piece.lo),piece.value(piece.hi))
                    area+=scale*integral(piece)
                    records.append((piece,scale))
        if not length:raise ValueError('Source boundary has zero arclength')
        hd=nonnegative(maximum).sqrt()
        return dict(records=records,length=length,mean=area/length,hd=hd,precision=precision)

def quantile(distribution,probability=D('.95'),error_mm=D('1e-9')):
    if not ZERO<probability<=ONE:raise ValueError('Probability outside (0,1]')
    with localcontext() as ctx:
        ctx.prec=distribution['precision']
        target=distribution['length']*probability
        def covered(t):return sum((scale*coverage(p,t) for p,scale in distribution['records']),ZERO)
        if covered(ZERO)>=target:return ZERO,ZERO
        lo=ZERO;hi=distribution['hd']
        for _ in range(200):
            if hi-lo<=error_mm:return lo,hi
            mid=(lo+hi)/2
            if covered(mid)>=target:hi=mid
            else:lo=mid
        raise RuntimeError('Reference quantile failed to converge')

def summarize(groups,precision=60):
    out={}
    for label,swap in [('a',False),('b',True)]:
        gg=[(q,p,n) if swap else (p,q,n) for p,q,n in groups]
        d=build_distribution(gg,precision)
        lo,hi=quantile(d)
        out.update({f'{label}_hd_mm':float(d['hd']),f'{label}_mean_mm':float(d['mean']),
                    f'{label}_length_mm':float(d['length']),f'{label}_hd95_lower_mm':float(lo),
                    f'{label}_hd95_upper_mm':float(hi),f'{label}_hd95_mm':float((lo+hi)/2),
                    f'{label}_envelope_pieces':len(d['records'])})
    out.update(hd_mm=max(out['a_hd_mm'],out['b_hd_mm']),
               hd95_mm=max(out['a_hd95_mm'],out['b_hd95_mm']),
               mean_mm=(out['a_mean_mm']+out['b_mean_mm'])/2,
               mean_pooled_mm=(out['a_mean_mm']*out['a_length_mm']+out['b_mean_mm']*out['b_length_mm'])/(out['a_length_mm']+out['b_length_mm']))
    return out
