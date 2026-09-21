"""Explicit package targets; detect the running interpreter's architecture."""
from pathlib import Path
import platform
import struct
import sys

TARGETS = {
    'windows-x86_64': 'fast_native.dll',
    'linux-x86_64': 'libfast_native.so',
    'macos-arm64': 'libfast_native.dylib',
    'macos-x86_64': 'libfast_native.dylib',
}

class NativeLibraryError(RuntimeError):
    """The current process has no usable packaged native library."""

def target_for(system, machine, pointer_bits=64):
    os_name = {'win32': 'windows', 'linux': 'linux', 'darwin': 'macos'}.get(system)
    arch = {'amd64': 'x86_64', 'x86_64': 'x86_64', 'arm64': 'arm64', 'aarch64': 'arm64'}.get(machine.lower())
    target = f'{os_name}-{arch}'
    if pointer_bits != 64 or target not in TARGETS:
        raise NativeLibraryError(
            f'Unsupported native-metrics process: {system}/{machine}/{pointer_bits}-bit. '
            'Supported targets: ' + ', '.join(TARGETS))
    return target

def host_target():
    # platform.machine() follows the running interpreter on macOS, including
    # an x86_64 interpreter running under Rosetta. No physical-CPU sysctl probe.
    return target_for(sys.platform, platform.machine(), struct.calcsize('P') * 8)

def relative_library_path(target):
    if target not in TARGETS:
        raise NativeLibraryError(f'Unsupported target: {target}')
    return Path('bin') / target / TARGETS[target]
