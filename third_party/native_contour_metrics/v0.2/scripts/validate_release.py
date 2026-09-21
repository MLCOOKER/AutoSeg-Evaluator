"""Run native acceptance; retain host, hashes and unmodified test outputs."""
from pathlib import Path
import datetime,hashlib,json,platform,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from native_contour_metrics_fast.platforms import host_target
from native_contour_metrics_fast.loader import library,library_path
def main():
    target=host_target();library()
    out=ROOT/'validation_runs'/target;out.mkdir(parents=True,exist_ok=True)
    commands=[['-m','pytest','-q','-p','no:cacheprovider',str(ROOT/'tests')],
              [str(ROOT/'scripts/validate_public.py'),'--output-dir',str(out)],
              [str(ROOT/'scripts/validate_stress.py'),'--output-dir',str(out)]]
    steps=[]
    for name,args in zip(['unit','public','stress'],commands):
        p=subprocess.run([sys.executable,*args],cwd=ROOT,capture_output=True,text=True)
        (out/(name+'.log')).write_text(p.stdout+p.stderr,encoding='utf-8')
        print(p.stdout+p.stderr,flush=True);steps.append(dict(name=name,returncode=p.returncode))
        if p.returncode:break
    record=dict(target=target,passed=len(steps)==3 and all(x['returncode']==0 for x in steps),
                steps=steps,python=sys.version,platform=platform.platform(),date_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                source_sha256=hashlib.sha256((ROOT/'cpp/fast_native.cpp').read_bytes()).hexdigest(),
                binary_sha256=hashlib.sha256(library_path().read_bytes()).hexdigest(),version='0.2.0.dev2')
    (out/'release_validation.json').write_text(json.dumps(record,indent=2))
    if not record['passed']:raise SystemExit(1)
if __name__=='__main__':main()
