"""AutoSeg Evaluator — segmentation quality assessment for radiotherapy."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("autoseg-evaluator")
except PackageNotFoundError:
    # The portable Windows bundle copies the package *source* into place
    # (it is not pip-installed), so there is no dist-info metadata for
    # ``importlib.metadata`` to read. ``scripts/build_portable.py`` writes a
    # ``_version.py`` next to this file at build time — fall back to it so the
    # window title shows the real version instead of "0.0.0+unknown".
    try:
        from ._version import __version__
    except ImportError:
        __version__ = "0.0.0+unknown"
