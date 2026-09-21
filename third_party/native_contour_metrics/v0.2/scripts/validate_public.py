"""Standalone regression on the 150 public-derived polygon fixtures."""
import argparse,csv,gzip,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from shapely.geometry import Polygon
from native_contour_metrics_fast import ROI,compare
from native_contour_metrics_fast.platforms import host_target

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=ROOT/'validation_runs'/host_target());args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    expected=json.loads((ROOT/'data/golden_metrics.json').read_text())
    with gzip.open(ROOT/'data/common_plane_fixtures.json.gz','rt') as stream:fixtures=json.load(stream)
    failures=[];maximum_distance=maximum_apl=maximum_napl=0.;count=0
    for e in expected:
        f=next(r for r in fixtures if (r['resolution'],r['family'],r['number'])==(e['resolution'],e['family'],e['number']))
        p,q=Polygon(f['a']),Polygon(f['b']);n=e['joint_planes'];na=n+e['excluded_reference_planes'];nb=n+e['excluded_test_planes']
        a=ROI({z:p for z in range(na)},{},0,'CLOSED_PLANAR')
        b=ROI({**{z:q for z in range(n)},**{na+z:q for z in range(nb-n)}},{},0,'CLOSED_PLANAR')
        r=compare(a,b,[1.,2.],missing_plane_policy='exclude')
        for key,truth in e['distance'].items():
            error=abs(r[key]-truth);maximum_distance=max(maximum_distance,error);count+=1
            if error>.001000002:failures.append([e['id'],key,error])
        for result in r['apl']:
            for side in ['a','b']:
                ref=e['apl'][str(result['tolerance_mm'])][side]
                for name,field,tol in [('apl',f'apl_{side}_mm',1e-8),('napl',f'napl_{side}',1e-10)]:
                    error=abs(result[field]-ref['apl_mm' if name=='apl' else 'napl']);count+=1
                    if name=='apl':maximum_apl=max(maximum_apl,error)
                    else:maximum_napl=max(maximum_napl,error)
                    if error>tol:failures.append([e['id'],field,error])
        if (r['joint_planes'],r['excluded_a_planes'],r['excluded_b_planes'])!=(n,na-n,nb-n):failures.append([e['id'],'plane counts'])
    result=dict(passed=not failures,pairs=len(expected),numeric_comparisons=count,max_distance_error_mm=maximum_distance,max_apl_error_mm=maximum_apl,max_napl_error=maximum_napl,failures=failures)
    print(json.dumps(result,indent=2));(args.output_dir/'public_validation.json').write_text(json.dumps(result,indent=2))
    if failures:raise SystemExit(1)

if __name__=='__main__':main()
