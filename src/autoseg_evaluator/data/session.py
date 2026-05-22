"""Session file format — snapshot of the user's matching state.

A session captures everything the user has set up against a particular
DICOM folder so the work can be resumed later (e.g. add a new vendor in
two months, recompute on top of the existing matches). It contains:

* the source folder path,
* the active replacement rules and template,
* every organ drawer with its full per-patient GT and test rows.

JSON load/save is intentionally separated from the tab/UI layer; the
restore step itself lives on :class:`MatchContoursTab` as
``apply_session_state``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_SUFFIX = ".session.json"


def build_session_dict(
    *,
    folder: str,
    drawers_state: list[dict[str, Any]],
    replacement_rules: list[dict[str, str]],
    template: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the on-disk JSON dictionary."""
    return {
        "schema_version": SCHEMA_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "folder": folder or "",
        "replacement_rules": list(replacement_rules or []),
        "last_template": dict(template or {}),
        "drawers": list(drawers_state or []),
    }


def save_session(path: Path | str, session_data: dict[str, Any]) -> None:
    """Persist a session dict to ``path`` (creates parent directories as needed)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2, ensure_ascii=False)


def load_session_file(path: Path | str) -> dict[str, Any]:
    """Read a session file and return its dict. Raises on missing/malformed input."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Session file {p} did not contain a JSON object.")
    version = data.get("schema_version")
    if version is not None and int(version) > SCHEMA_VERSION:
        raise ValueError(
            f"Session file {p} is schema version {version}; this build understands "
            f"up to {SCHEMA_VERSION}. Update the app and try again."
        )
    return data
