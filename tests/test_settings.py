"""settings.json: defaults fill gaps, and settings for removed features are dropped."""

from __future__ import annotations

import json

from autoseg_evaluator.utils import settings as settings_module


def _write(tmp_path, monkeypatch, data):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(settings_module, "settings_path", lambda: path)
    return path


def test_mask_apl_settings_from_before_v3_are_dropped_on_load(tmp_path, monkeypatch):
    """Mask APL was removed in v3; its switches must not travel on in the file.

    Left in, they would suggest to anyone reading settings.json that they still
    do something. Everything else the user set is kept.
    """
    _write(
        tmp_path,
        monkeypatch,
        {
            "compute_geometric": {"dice": False, "apl_mean": True, "apl_total": True},
            "tolerances": {"surface_dice_tau_mm": 2.0, "apl_tolerance_mm": 2.5},
        },
    )

    loaded = settings_module.load_settings()

    assert "apl_mean" not in loaded["compute_geometric"]
    assert "apl_total" not in loaded["compute_geometric"]
    assert "apl_tolerance_mm" not in loaded["tolerances"]
    assert loaded["compute_geometric"]["dice"] is False
    assert loaded["tolerances"]["surface_dice_tau_mm"] == 2.0


def test_the_next_save_leaves_the_retired_settings_out(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch, {"tolerances": {"apl_tolerance_mm": 2.5}})

    settings_module.save_settings(settings_module.load_settings())

    assert "apl_tolerance_mm" not in json.loads(path.read_text(encoding="utf-8"))["tolerances"]


def test_defaults_fill_what_the_file_does_not_say(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"theme": "dark_teal.xml"})

    loaded = settings_module.load_settings()

    assert loaded["theme"] == "dark_teal.xml"
    # A list: every tolerance given is computed in one run.
    assert loaded["tolerances"]["surface_dice_tau_mm"] == [3.0]
    assert "apl_tolerance_mm" not in loaded["tolerances"]
