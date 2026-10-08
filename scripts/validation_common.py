"""Small helpers the validation scripts share."""

from __future__ import annotations

import dataclasses
import gzip
import json
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
#: Where the supplementary validation reports, and their full results, live.
VALIDATION_DOCS = REPO / "docs" / "validation"


def long_path(path: Path) -> Path:
    """``path``, readable past Windows' 260-character limit.

    The external evidence packages nest deep enough that their files exceed it
    from an ordinary working folder; the ``\\\\?\\`` prefix lifts the limit for
    Python's own file access. Elsewhere the path is returned resolved.
    """
    resolved = path.resolve()
    if sys.platform == "win32" and not str(resolved).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(resolved))
    return resolved


def git_revision() -> str:
    """The commit a report's results came from, and whether the source was clean.

    The package version is not bumped between releases, so it cannot say which
    code produced a result; the commit can.
    """
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "src"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return "unknown commit"
    if not commit:
        return "unknown commit"
    return f"commit {commit}" + (" with uncommitted source changes" if dirty else "")


def autoseg_version() -> str:
    """The version in ``pyproject.toml``: the source's, not a stale installed copy's."""
    try:
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return "unknown version"
    found = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return found.group(1) if found else "unknown version"


def run_info() -> dict[str, str]:
    """The code and date a computation ran on, to keep beside its results."""
    return {
        "version": autoseg_version(),
        "revision": git_revision(),
        "date": f"{datetime.now(timezone.utc):%Y-%m-%d}",
    }


def stamp(script: str, info: dict[str, str] | None = None) -> str:
    """One line naming the code, the date and the script a report came from.

    ``info`` is :func:`run_info` as kept with cached results, so a report
    rewritten from them names the code that computed them, not today's.
    """
    info = info or run_info()
    return (
        f"AutoSeg Evaluator {info['version']}, {info['revision']} · computed "
        f"{info['date']} by `scripts/{script}`"
    )


# ---- Cached results -------------------------------------------------------------
# A slow run keeps what it computed, so its reports can be rewritten without
# computing it again. The cache is not committed: running the script remakes it.

#: Where runs keep their results.
RESULTS_CACHE = REPO / ".validation_cache"


def _encode(value: Any) -> Any:
    """``value`` as JSON can hold it; :func:`_decode` gives it back."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # Callables (a DVH's lookup function) are dropped: they are used while a
        # run computes, never by a report.
        fields = {
            f.name: _encode(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if not callable(getattr(value, f.name))
        }
        return {"__type__": type(value).__name__, "fields": fields}
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value):
            return {k: _encode(v) for k, v in value.items()}
        return {"__items__": [[_encode(k), _encode(v)] for k, v in value.items()]}
    if isinstance(value, tuple):
        return {"__tuple__": [_encode(v) for v in value]}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, np.ndarray):
        return {"__ndarray__": _encode(value.tolist()), "dtype": str(value.dtype)}
    if type(value).__name__ == "DataFrame" and hasattr(value, "to_dict"):
        return {"__dataframe__": _encode(value.to_dict(orient="split"))}
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _decode(value: Any, types: dict[str, Callable[..., Any]]) -> Any:
    if isinstance(value, list):
        return [_decode(v, types) for v in value]
    if not isinstance(value, dict):
        return value
    if "__type__" in value:
        return types[value["__type__"]](**_decode(value["fields"], types))
    if "__items__" in value:
        return {_decode(k, types): _decode(v, types) for k, v in value["__items__"]}
    if "__tuple__" in value:
        return tuple(_decode(v, types) for v in value["__tuple__"])
    if "__ndarray__" in value:
        return np.asarray(_decode(value["__ndarray__"], types), dtype=value["dtype"])
    if "__dataframe__" in value:
        import pandas as pd

        split = _decode(value["__dataframe__"], types)
        return pd.DataFrame(split["data"], index=split["index"], columns=split["columns"])
    return {k: _decode(v, types) for k, v in value.items()}


def save_results(name: str, data: dict) -> Path:
    """Keep a run's results under ``name``; return where."""
    RESULTS_CACHE.mkdir(exist_ok=True)
    path = RESULTS_CACHE / f"{name}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(_encode(data), handle)
    return path


def load_results(name: str, types: dict[str, Callable[..., Any]] | None = None) -> dict:
    """A run's kept results. ``types`` rebuilds its dataclasses, by class name.

    Raises :class:`FileNotFoundError`, naming the script to run, if there are none.
    """
    path = RESULTS_CACHE / f"{name}.json.gz"
    if not path.exists():
        raise FileNotFoundError(f"no cached results at {path}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return _decode(json.load(handle), types or {})


def demote(lines: list[str]) -> list[str]:
    """Every heading one level down, to sit a document inside another."""
    out, in_code = [], False
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
        out.append("#" + line if line.startswith("#") and not in_code else line)
    return out


_LIST_ITEM = re.compile(r"\s*(-|\d+\.) ")


def unwrap(lines: list[str]) -> str:
    """Markdown with every paragraph and list item on one line.

    Report text is written wrapped in the source, then numbers are spliced in, so
    the wrapped lines come out ragged; joined, the file reads cleanly raw and
    renders the same. Headings, tables, code blocks and blank lines are left as
    they are, and a list item always starts a line of its own.
    """
    out: list[str] = []
    in_code = joinable = False
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
        if in_code or line.startswith(("```", "#", "|")) or not line.strip():
            out.append(line)
            joinable = False
        elif joinable and not _LIST_ITEM.match(line):
            out[-1] += " " + line.strip()
        else:
            out.append(line)
            joinable = True
    return "\n".join(out)


def sci(value: float) -> str:
    """A small number as a reader writes it: 4.9 × 10⁻¹⁰ rather than 4.9e-10.

    Zero is "0"; a number of 0.001 or more is left in ordinary notation.
    """
    if value == 0:
        return "0"
    if abs(value) >= 1e-3:
        return f"{value:.4g}"
    mantissa, exponent = f"{value:.1e}".split("e")
    superscript = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")
    return f"{mantissa} × 10{str(int(exponent)).translate(superscript)}"


def md_table(headers: list[str], rows: list[list[str]], align: str) -> list[str]:
    """A markdown table, one ``l`` or ``r`` per column in ``align``, then a blank line."""
    marks = {"l": "---", "r": "---:"}
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(marks[a] for a in align) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
        "",
    ]
