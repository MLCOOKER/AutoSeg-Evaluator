"""Vendored from native_contour_metrics_fast 0.2.0.dev2, unmodified except for imports.

The kernel under ``autoseg_evaluator.vendor`` is byte-identical to the supplied
package and is pinned by ``tests/test_vendor_integrity.py``. This file is not:
its import lines were rewritten to the vendored package path, and the path it loads the build script from. Nothing
else — no assertion, tolerance or fixture — was touched.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import ctypes
import pytest
from autoseg_evaluator.vendor.native_contour_metrics_fast import loader
from autoseg_evaluator.vendor.native_contour_metrics_fast.platforms import target_for,relative_library_path,NativeLibraryError

ROOT = Path(__file__).resolve().parents[2] / 'src/autoseg_evaluator/vendor'
spec = importlib.util.spec_from_file_location('build_native_test',ROOT/'tools/build_native_library.py')
build_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_module)

@pytest.mark.parametrize('system,machine,target',[
    ('win32','AMD64','windows-x86_64'),('linux','x86_64','linux-x86_64'),
    ('darwin','arm64','macos-arm64'),('darwin','x86_64','macos-x86_64'),
    ('darwin','aarch64','macos-arm64')])
def test_process_target(system,machine,target):
    assert target_for(system,machine,64)==target

@pytest.mark.parametrize('system,machine,bits',[
    ('linux','aarch64',64),('win32','ARM64',64),('freebsd','x86_64',64),
    ('win32','AMD64',32),('darwin','i386',32),('linux','mips',64)])
def test_unsupported_process_refused(system,machine,bits):
    with pytest.raises(NativeLibraryError,match='Unsupported native-metrics process'):
        target_for(system,machine,bits)

@pytest.fixture
def isolated_loader(monkeypatch,tmp_path):
    monkeypatch.setattr(loader,'PACKAGE_ROOT',tmp_path)
    monkeypatch.setattr(loader,'_lib',None)
    return tmp_path

@pytest.mark.parametrize('target,filename',[
    ('windows-x86_64','fast_native.dll'),('linux-x86_64','libfast_native.so'),
    ('macos-arm64','libfast_native.dylib'),('macos-x86_64','libfast_native.dylib')])
def test_loads_only_correct_packaged_file(isolated_loader,monkeypatch,target,filename):
    monkeypatch.setattr(loader,'host_target',lambda:target)
    path=isolated_loader/'bin'/target/filename;path.parent.mkdir(parents=True);path.write_bytes(b'mock')
    calls=[];function=SimpleNamespace();fake=SimpleNamespace(ncm_direction=function)
    def load(p):calls.append(p);return fake
    monkeypatch.setattr(loader.ctypes,'CDLL',load)
    assert loader.library() is fake
    assert calls==[str(path)]
    assert len(function.argtypes)==11 and function.restype is ctypes.c_int

def test_missing_library_does_not_fall_back_to_windows_or_system(isolated_loader,monkeypatch):
    monkeypatch.setattr(loader,'host_target',lambda:'linux-x86_64')
    legacy=isolated_loader/'bin/fast_native.dll';legacy.parent.mkdir();legacy.write_bytes(b'mock')
    monkeypatch.setattr(loader.ctypes,'CDLL',lambda p:pytest.fail('Must not try another binary'))
    with pytest.raises(NativeLibraryError,match='linux-x86_64'):
        loader.library()

@pytest.mark.parametrize('failure',['loader_error','missing_symbol'])
def test_failed_initialization_is_not_cached(isolated_loader,monkeypatch,failure):
    monkeypatch.setattr(loader,'host_target',lambda:'windows-x86_64')
    path=isolated_loader/relative_library_path('windows-x86_64');path.parent.mkdir(parents=True);path.write_bytes(b'mock')
    def broken(path):
        if failure=='loader_error':raise OSError('wrong architecture')
        return SimpleNamespace()
    monkeypatch.setattr(loader.ctypes,'CDLL',broken)
    with pytest.raises(NativeLibraryError):loader.library()
    assert loader._lib is None
    fake=SimpleNamespace(ncm_direction=SimpleNamespace())
    monkeypatch.setattr(loader.ctypes,'CDLL',lambda p:fake)
    assert loader.library() is fake

def test_concurrent_initialization_binds_once(isolated_loader,monkeypatch):
    monkeypatch.setattr(loader,'host_target',lambda:'windows-x86_64')
    path=isolated_loader/relative_library_path('windows-x86_64');path.parent.mkdir(parents=True);path.write_bytes(b'mock')
    calls=[];fake=SimpleNamespace(ncm_direction=SimpleNamespace())
    def load(p):calls.append(p);return fake
    monkeypatch.setattr(loader.ctypes,'CDLL',load)
    with ThreadPoolExecutor(8) as executor:
        results=list(executor.map(lambda _:loader.library(),range(32)))
    assert len(calls)==1 and all(x is fake for x in results)

@pytest.mark.parametrize('target',['linux-x86_64','macos-arm64','macos-x86_64'])
def test_unix_build_disables_implicit_contraction_and_fast_math(target):
    args=build_module.compiler_arguments(target,Path('source with spaces.cpp'),Path('output with spaces'))
    assert args.index('-fno-fast-math')<args.index('-ffp-contract=off')
    assert '-ffast-math' not in args and '-Ofast' not in args and '-march=native' not in args
    assert ('-shared' if target.startswith('linux') else '-dynamiclib') in args
    assert str(Path('source with spaces.cpp')) in args

def test_windows_build_retains_strict_flags():
    args=build_module.compiler_arguments('windows-x86_64',Path('a.cpp'),Path('a.dll'))
    assert '/fp:strict' in args and '/fp:fast' not in args and '/MT' in args

def test_build_refuses_cross_host_before_starting_compiler(monkeypatch,tmp_path):
    monkeypatch.setattr(build_module,'load_platforms',lambda root:SimpleNamespace(host_target=lambda:'windows-x86_64'))
    with pytest.raises(RuntimeError,match='requires native execution'):
        build_module.build(tmp_path,target='macos-arm64')
