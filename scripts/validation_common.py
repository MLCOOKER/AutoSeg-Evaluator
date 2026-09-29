"""Small helpers the validation scripts share."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


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
