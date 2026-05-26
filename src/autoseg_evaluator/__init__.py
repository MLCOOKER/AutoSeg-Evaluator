"""AutoSeg Evaluator — segmentation quality assessment for radiotherapy."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("autoseg-evaluator")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
