"""The helpers every validation report is written with (``scripts/validation_common.py``)."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import validation_common  # noqa: E402
from validation_common import demote, load_results, save_results, unwrap  # noqa: E402


@dataclass
class _Row:
    name: str
    truth: dict


@dataclass
class _Result:
    metrics: dict
    lookup: object  # a callable while computing; never needed by a report
    seconds: float = 0.0


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(validation_common, "RESULTS_CACHE", tmp_path)
    return tmp_path


def test_kept_results_come_back_as_they_were(cache):
    data = {
        "row": _Row("Sphere_10_0", {"dmean": 16.0}),
        "result": _Result({"d99": np.float64(15.2), "d95": math.nan}, lambda d: d, seconds=0.5),
        "phase": (0.25, 0.5),
        "by_key": {("a", 1): np.array([1, 2], dtype=np.int64)},
        "table": pd.DataFrame({"method": ["x", "y"], "error": [0.1, math.inf]}),
    }
    save_results("trial", data)
    back = load_results(
        "trial", {"_Row": _Row, "_Result": lambda **fields: _Result(lookup=None, **fields)}
    )
    assert back["row"] == data["row"]
    assert back["result"].metrics["d99"] == 15.2 and math.isnan(back["result"].metrics["d95"])
    assert back["result"].lookup is None and back["result"].seconds == 0.5
    assert back["phase"] == (0.25, 0.5)
    assert np.array_equal(back["by_key"][("a", 1)], [1, 2])
    pd.testing.assert_frame_equal(back["table"], data["table"])


def test_missing_results_say_so(cache):
    with pytest.raises(FileNotFoundError):
        load_results("never_run")


def test_paragraphs_and_list_items_are_joined_and_nothing_else():
    lines = [
        "# Title",
        "",
        "A paragraph written",
        "over three",
        "lines.",
        "",
        "- an item",
        "  continued",
        "- another",
        "1. numbered",
        "   continued",
        "| a | b |",
        "| --- | --- |",
        "```",
        "kept",
        "as is",
        "```",
    ]
    assert unwrap(lines).splitlines() == [
        "# Title",
        "",
        "A paragraph written over three lines.",
        "",
        "- an item continued",
        "- another",
        "1. numbered continued",
        "| a | b |",
        "| --- | --- |",
        "```",
        "kept",
        "as is",
        "```",
    ]


def test_a_decimal_number_opening_a_line_does_not_start_a_list():
    assert unwrap(["took", "  1.2 s in all."]) == "took 1.2 s in all."


def test_demote_moves_headings_down_but_not_code():
    assert demote(["## Part", "text", "```", "# comment", "```", "### Sub"]) == [
        "### Part",
        "text",
        "```",
        "# comment",
        "```",
        "#### Sub",
    ]
