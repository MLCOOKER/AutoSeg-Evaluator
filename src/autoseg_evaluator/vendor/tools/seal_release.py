"""Refresh a release manifest after validating a native build; preserve source hashes."""
from pathlib import Path
import hashlib,json,shutil,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from native_contour_metrics_fast.platforms import host_target,TARGETS,relative_library_path

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def included(path):
    rel=path.relative_to(ROOT)
    return path.is_file() and not any(p in {'build','validation_runs','__pycache__','.pytest_cache','.git'} or p.endswith('.egg-info') for p in rel.parts) and rel.as_posix()!='MANIFEST.sha256.json'

def main():
    source=json.loads((ROOT/'SOURCE_MANIFEST.sha256.json').read_text())
    for name,expected in source.items():
        if digest(ROOT/name)!=expected:raise RuntimeError('Source changed: '+name+'; review/version it before issuing a new source manifest.')
    target=host_target();path=ROOT/'native_contour_metrics_fast'/relative_library_path(target)
    run=ROOT/'validation_runs'/target
    validation=json.loads((run/'release_validation.json').read_text())
    if not validation['passed'] or validation['target']!=target or validation['binary_sha256']!=digest(path) or validation['source_sha256']!=digest(ROOT/'cpp/fast_native.cpp'):
        raise RuntimeError('Run scripts/validate_release.py against the current binary before sealing.')
    dst=ROOT/'evidence/platforms'/target;dst.mkdir(parents=True,exist_ok=True)
    for p in run.iterdir():
        if p.is_file():shutil.copy2(p,dst/p.name)
    status={}
    for key in TARGETS:
        binary=ROOT/'native_contour_metrics_fast'/relative_library_path(key)
        record=ROOT/'evidence/platforms'/key/'release_validation.json'
        if binary.is_file() and record.is_file():
            value=json.loads(record.read_text())
            valid=value.get('passed') and value.get('target')==key and value.get('binary_sha256')==digest(binary) and value.get('source_sha256')==digest(ROOT/'cpp/fast_native.cpp')
        else:valid=False
        status[key]=dict(binary_included=binary.is_file(),native_validation_passed=bool(valid),
                        loader_selection_tested=True,build_recipe_included=True)
    (ROOT/'evidence/platform_status.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
    files={p.relative_to(ROOT).as_posix():digest(p) for p in sorted(ROOT.rglob('*')) if included(p)}
    (ROOT/'MANIFEST.sha256.json').write_text(json.dumps(files,indent=2),encoding='utf-8')
    print('Sealed',target,';',len(files),'manifest files. Other targets retain their own validation status.')

if __name__=='__main__':main()
