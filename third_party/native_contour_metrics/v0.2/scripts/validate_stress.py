"""Portable 44-case geometric stress audit; references use Decimal."""
from pathlib import Path
import argparse,csv,json,sys
import numpy as np
from shapely.geometry import Polygon,box
from shapely.affinity import rotate,translate
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from native_contour_metrics_fast import ROI,compare,AmbiguousQuantileError
from native_contour_metrics_fast.metrics import segments
from native_contour_metrics_fast.capsule_oracle import directed_coverage
from native_contour_metrics_fast.platforms import host_target
from validation.segment_distance_oracle import summarize,build_distribution,quantile
OUT=ROOT/'validation_runs'/host_target()
def roi(p,n=1):return ROI({z:p for z in range(n)},{},0,'CLOSED_PLANAR')
def dump(name,value):(OUT/name).write_text(json.dumps(value,indent=2))
def writecsv(path,rows):
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(rows)

def stress():
    from decimal import Decimal as D
    fixtures=json.loads((ROOT/'data/stress_fixture_edges.json').read_text())
    rows=[];fail=[]
    shapes=[('identity',box(0,0,10,10),box(0,0,10,10)),('asymmetric',box(0,0,10,10),box(1,1,3,3)),
        ('holed',box(0,0,10,10).difference(box(2,2,4,4)),box(1,0,11,10)),
        ('disconnected',box(0,0,1,1).union(box(100,0,101.1,1.1)),box(0,0,1,1))]
    for angle in [0,10,13,17,30,37,45,60,90]:
        shapes.append((f'threshold_{angle}',rotate(box(2,2,8,8),angle,origin=(0,0)),rotate(box(0,0,10,10),angle,origin=(0,0))))
    for shift in [1e3,1e9,1e12]:shapes.append((f'translation_{shift}',translate(box(0,0,10,10),shift,shift),translate(box(1,1,3,3),shift,shift)))
    for f in fixtures:
        if f['label'].startswith('random_star') or f['label'] in ['tiny','thin']:
            shapes.append((f['label'],Polygon(np.array(f['a_edges'])[:,0]),Polygon(np.array(f['b_edges'])[:,0])))
    for label,p,q in shapes:
        r=compare(roi(p),roi(q),[.5,1.,2.,3.]);edges=(segments(p)[0],segments(q)[0])
        truth=summarize([(*edges,1)],80)
        for side,a,b in [('a',edges[0],edges[1]),('b',edges[1],edges[0])]:
            d=build_distribution([(a,b,1)],80);lo,hi=quantile(d,D('.5'));truth[side+'_median_mm']=float((lo+hi)/2)
        truth['median_mm']=max(truth['a_median_mm'],truth['b_median_mm'])
        maximum=max(abs(r[k]-truth[k]) for k in truth if k in r and k.endswith('_mm') and 'length' not in k)
        aplerror=0
        for side,a,b in [('a',edges[0],edges[1]),('b',edges[1],edges[0])]:
            length,matched=directed_coverage(a,b,[.5,1.,2.,3.],100)
            aplerror=max(aplerror,max(abs(x[f'apl_{side}_mm']-float(length-m)) for x,m in zip(r['apl'],matched)))
        row=dict(label=label,max_distance_error_mm=maximum,max_apl_error_mm=aplerror,fallbacks=r['a_decimal_fallbacks']+r['b_decimal_fallbacks'])
        if maximum>.001000002 or aplerror>1e-8:fail.append(row)
        rows.append(row)
    for probability,side in [(.5,1.),(.95,.5)]:
        big=box(0,0,1,1) if probability==.5 else box(0,0,9.5,9.5)
        source=big.union(box(100,0,100+side,side))
        try:compare(roi(source),roi(big));fail.append(dict(label=f'ambiguous_{probability}',error='not rejected'))
        except AmbiguousQuantileError:rows.append(dict(label=f'ambiguous_{probability}',status='rejected'))
    writecsv(OUT/'stress.csv',rows);dump('stress_failures.json',fail);print('Stress',len(rows),'failures',len(fail),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=OUT);args=parser.parse_args()
    OUT=args.output_dir;OUT.mkdir(parents=True,exist_ok=True);stress()
    if json.loads((OUT/'stress_failures.json').read_text()):raise SystemExit(1)
