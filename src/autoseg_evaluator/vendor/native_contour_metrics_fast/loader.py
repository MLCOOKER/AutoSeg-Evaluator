"""Load only the library packaged for this process; never search system paths."""
import ctypes
from pathlib import Path
from threading import Lock
import numpy as np
from .platforms import NativeLibraryError, host_target, relative_library_path

PACKAGE_ROOT = Path(__file__).resolve().parent
_lib = None
_lock = Lock()

def library_path():
    return PACKAGE_ROOT / relative_library_path(host_target())

def library():
    global _lib
    if _lib is not None:
        return _lib
    with _lock:
        if _lib is not None:
            return _lib
        target = host_target()
        path = PACKAGE_ROOT / relative_library_path(target)
        if not path.is_file():
            raise NativeLibraryError(
                f'No native-metrics binary for {target}: {path}. '
                'Install the matching validated binary, or build on that target '
                'with python tools/build_native_library.py and run scripts/validate_release.py.')
        try:
            candidate = ctypes.CDLL(str(path))
        except OSError as exc:
            raise NativeLibraryError(
                f'Cannot load native-metrics binary {path}. Check process architecture, '
                f'OS compatibility and required system libraries. Loader error: {exc}') from exc
        try:
            f = candidate.ncm_direction
        except AttributeError as exc:
            raise NativeLibraryError(f'{path} is missing the required C ABI symbol ncm_direction') from exc
        f.argtypes = [
            np.ctypeslib.ndpointer(np.float64, flags='C_CONTIGUOUS'), ctypes.c_int64,
            np.ctypeslib.ndpointer(np.float64, flags='C_CONTIGUOUS'), ctypes.c_int64,
            np.ctypeslib.ndpointer(np.int64, flags='C_CONTIGUOUS'), ctypes.c_int64,
            np.ctypeslib.ndpointer(np.float64, flags='C_CONTIGUOUS'), ctypes.c_int64, ctypes.c_double,
            np.ctypeslib.ndpointer(np.float64, flags='C_CONTIGUOUS'),
            np.ctypeslib.ndpointer(np.uint8, flags='C_CONTIGUOUS'),
        ]
        f.restype = ctypes.c_int
        # Publish only after every binding succeeded; another thread can never
        # observe a partially initialized ctypes signature.
        _lib = candidate
        return _lib
