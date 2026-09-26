"""settings.json I/O — auto-persisted preferences next to the executable.

Settings cover app-wide preferences only (defaults, window geometry, last folder,
theme, custom source labels). Per-project work-in-progress state lives in session
files (see autoseg_evaluator.data.session).
"""

from __future__ import annotations

import json
from typing import Any

from autoseg_evaluator.utils.paths import settings_path

_DEFAULTS: dict[str, Any] = {
    "last_folder": "",
    "window": {
        "width": 1400,
        "height": 900,
        "x": 100,
        "y": 100,
        "maximized": False,
    },
    "active_tab": 0,
    "theme": "light_blue.xml",
    # Tolerances are lists: every value is computed in one run, each in its
    # own column. A single number, as older settings files hold, still reads.
    "tolerances": {
        "surface_dice_tau_mm": [3.0],
        "similarity_threshold": 0.6,
    },
    "dvh": {
        "d_at_volumes_pct": [95, 50, 5, 2],
        "v_at_doses_gy": [20, 30, 40],
        "include_dmean": True,
        "include_dmax": True,
        "include_dmin": False,
    },
    "compute_geometric": {
        "dice": True,
        "precision_recall": True,
        "hausdorff100": True,
        "hausdorff95": True,
        "mean_surface_distance": True,
        "surface_dice": True,
    },
    # The native-polygon stream. Off by default: it is a second way of measuring
    # the same structures, not a refinement of the first, and it adds its own
    # columns. An install should start producing them because someone asked, not
    # because it was upgraded.
    "compute_polygon": {
        "metrics": {
            "apl": False,
            "napl": False,
            "hd100": False,
            "hd95": False,
            "mean": False,
            "median": False,
        },
        "tolerance_mm": [3.0],
    },
    # Keep the full detail behind every number for a .audit.json beside an
    # export. Off by default: it is only useful if someone intends to audit
    # or reproduce a run, and it has to be collected while computing.
    "audit": {"sidecar": False},
    "custom_source_labels": {},  # keyed by SOP Instance UID
    # Source labels designated as manual observers for the Build Consensus GT
    # tab (v2.4). Each patient's RTSSes carrying one of these labels are
    # combined into a STAPLE consensus, with each label treated as a rater.
    "consensus_observer_labels": [],  # list[str]
    # User-defined Find→Replace rules applied to organ names before matching
    "replacement_rules": [],  # list of {"find": str, "replace": str}
    # Last-used auto-match template (organ list + GT identifier criteria)
    "last_template": {
        "organs": [],
        "gt_manufacturer": "",  # substring to look for in Manufacturer tag
        "gt_filename": "",  # substring to look for in RTSTRUCT filename
        "similarity_threshold": 0.6,
    },
}


def default_settings() -> dict[str, Any]:
    """Return a fresh copy of the built-in defaults."""
    return _deepcopy_json(_DEFAULTS)


def load_settings() -> dict[str, Any]:
    """Load settings.json, filling missing keys from defaults.

    Returns defaults on a missing or unreadable file (best-effort persistence).
    """
    path = settings_path()
    if not path.exists():
        return default_settings()
    try:
        with path.open("r", encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default_settings()
    return _merge(default_settings(), _drop_retired(user))


#: Settings that named something the application no longer has. They are
#: dropped on load, so the next save leaves them out, instead of travelling on
#: forever and suggesting to anyone reading the file that they still do
#: something. Mask APL and its tolerance were removed in v3 (spec D3).
_RETIRED: dict[str, tuple[str, ...]] = {
    "compute_geometric": ("apl_mean", "apl_total"),
    "tolerances": ("apl_tolerance_mm",),
}


def _drop_retired(user: dict[str, Any]) -> dict[str, Any]:
    for section, keys in _RETIRED.items():
        block = user.get(section)
        if isinstance(block, dict):
            for key in keys:
                block.pop(key, None)
    return user


def save_settings(settings: dict[str, Any]) -> None:
    """Persist settings.json. Silently ignores write failures."""
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except OSError:
        # Best-effort: never crash the app over a settings write
        pass


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge — overlay values win, base fills gaps."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _merge(base[key], value)
        else:
            base[key] = value
    return base


def _deepcopy_json(obj: Any) -> Any:
    """Deep-copy a JSON-compatible structure without importing copy."""
    return json.loads(json.dumps(obj))
