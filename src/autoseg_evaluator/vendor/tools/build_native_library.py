"""Native build for Windows x64, Linux x86-64, macOS arm64 or x86-64.

This file is delivered in the package's tools/ directory. Run on each target
with a matching 64-bit Python interpreter. No cross-compilation is implied.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def load_platforms(root):
    spec = importlib.util.spec_from_file_location('native_build_platforms', root/'native_contour_metrics_fast/platforms.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def compiler_arguments(target, source, output, compiler=None):
    if target == 'windows-x86_64':
        return [compiler or 'cl', '/nologo', '/O2', '/MT', '/LD', '/std:c++17', '/fp:strict', '/EHsc',
                str(source), '/link', '/OUT:' + str(output)]
    if target not in ['linux-x86_64', 'macos-arm64', 'macos-x86_64']:
        raise ValueError(f'Unsupported build target: {target}')
    args = [compiler or ('c++' if target.startswith('linux') else 'clang++'), '-std=c++17', '-O2',
            '-fPIC', '-fno-fast-math', '-ffp-contract=off']
    if target.startswith('macos'):
        args += ['-dynamiclib', '-arch', target.removeprefix('macos-')]
    else:
        args += ['-shared', '-march=x86-64', '-mtune=generic']
    return args + [str(source), '-o', str(output)]

def msvc_environment():
    if shutil.which('cl') and os.environ.get('VSCMD_ARG_TGT_ARCH', '').lower() in ['x64','amd64']:
        return dict(os.environ)
    vswhere = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
    if not vswhere.exists():
        raise RuntimeError('Use an x64 Native Tools Command Prompt or install MSVC C++ Build Tools.')
    found = subprocess.run([str(vswhere), '-latest', '-products', '*', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
                           check=True, capture_output=True, text=True).stdout.strip()
    script = Path(found)/'VC/Auxiliary/Build/vcvars64.bat'
    if not found or not script.is_file():
        raise RuntimeError('MSVC x64 toolchain was not found.')
    # Only this verified local toolchain path enters cmd syntax. Compiler,
    # source and output arguments below are always passed as an argument list.
    if any(c in str(script) for c in ['"', '%', '\r', '\n']):
        raise RuntimeError('Unsupported toolchain path characters.')
    command = f'call "{script}" >nul && set'
    p = subprocess.run('cmd.exe /d /s /c "' + command + '"', check=True, capture_output=True,
                       text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    env = {key.upper(): value for key, value in os.environ.items()}
    for line in p.stdout.splitlines():
        if '=' in line and not line.startswith('='):
            key, value = line.split('=', 1)
            env[key.upper()] = value
    # Resolve the selected x64 toolchain explicitly. Some inherited Windows
    # environments contain case-variant PATH entries; avoid selecting another
    # compiler from the caller's original search path.
    toolbin=Path(env['VCTOOLSINSTALLDIR'])/'bin/Hostx64/x64'
    if not (toolbin/'cl.exe').is_file():
        raise RuntimeError('Selected MSVC installation has no x64 host/target compiler.')
    env['PATH']=str(toolbin)+os.pathsep+env.get('PATH','')
    return env

def build(root=ROOT, compiler=None, target=None):
    config = load_platforms(root)
    host = config.host_target()
    target = target or host
    if target != host:
        raise RuntimeError(f'This script requires native execution: current process is {host}, requested {target}.')
    source = root/'cpp/fast_native.cpp'
    output = root/'native_contour_metrics_fast'/config.relative_library_path(target)
    output.parent.mkdir(parents=True, exist_ok=True)
    work = root/'build'/target
    work.mkdir(parents=True, exist_ok=True)
    args = compiler_arguments(target, source, output, compiler)
    env = msvc_environment() if target.startswith('windows') else dict(os.environ)
    # Prevent inherited MSVC flags from overriding /fp:strict. No CXXFLAGS or
    # LDFLAGS are interpolated into GCC/Clang command lines either.
    for key in list(env):
        if key.upper() in ['CL', '_CL_']:
            del env[key]
    executable = shutil.which(args[0], path=env.get('PATH', env.get('Path')))
    if not executable:
        raise RuntimeError(f'Compiler not found: {args[0]}')
    args[0] = executable
    p = subprocess.run(args, cwd=work, env=env, capture_output=True, text=True,
                       **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    (work/'build.log').write_text(p.stdout+p.stderr, encoding='utf-8')
    print(p.stdout+p.stderr)
    p.check_returncode()
    version = subprocess.run([executable] if target.startswith('windows') else [executable, '--version'],
                             env=env, capture_output=True, text=True)
    record = dict(target=target,host_platform=platform.platform(),python=sys.version,command=args,
                  compiler_version=(version.stdout+version.stderr).strip(),
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  binary_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                  status='built; numerical validation still required')
    evidence = root/'evidence/builds'
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence/(target+'.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(output)
    return output

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=['windows-x86_64','linux-x86_64','macos-arm64','macos-x86_64'])
    parser.add_argument('--compiler', help='Compiler executable path only; additional flags are not accepted.')
    args = parser.parse_args()
    build(compiler=args.compiler,target=args.target)
