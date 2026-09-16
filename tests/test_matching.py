"""Tests for the deterministic matching pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoseg_evaluator.core.matching import (
    MISMATCH_INDEX,
    MISMATCH_LATERALITY,
    MISMATCH_POSITION,
    MISMATCH_REASONS,
    Match,
    ReplacementRule,
    best_match,
    canonicalise,
    canonicalise_with_meta,
    similarity,
    structural_mismatch,
)
from autoseg_evaluator.data.synonyms import flatten_synonyms, load_synonyms

SYNONYMS_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "autoseg_evaluator"
    / "resources"
    / "synonyms.json"
)


@pytest.fixture(scope="module")
def synonyms_flat():
    """The dictionary the application actually ships."""
    return flatten_synonyms(load_synonyms(SYNONYMS_PATH))


# ---- canonicalise --------------------------------------------------------


def test_canonicalise_lowercases_and_normalises_separators():
    assert canonicalise("Brain_Stem") == "brain stem"
    assert canonicalise("BRAIN-STEM") == "brain stem"
    assert canonicalise("  Brain   Stem  ") == "brain stem"


def test_canonicalise_collapses_repeated_whitespace():
    assert canonicalise("a   b\tc") == "a b c"


def test_canonicalise_empty_input():
    assert canonicalise("") == ""
    assert canonicalise(None) == ""  # type: ignore[arg-type]


def test_canonicalise_applies_replacement_rules():
    rules = [ReplacementRule(find="musc_constrict", replace="pharyngeal_constrictor")]
    assert canonicalise("Musc_Constrict", rules=rules) == "pharyngeal constrictor"


def test_canonicalise_resolves_synonyms_to_canonical():
    synonyms_flat = {"oesophagus": "esophagus", "esophagus": "esophagus"}
    assert canonicalise("Oesophagus", synonyms_flat=synonyms_flat) == "esophagus"


def test_canonicalise_passes_through_when_no_synonym_match():
    synonyms_flat = {"esophagus": "esophagus"}
    assert canonicalise("Heart", synonyms_flat=synonyms_flat) == "heart"


def test_canonicalise_with_meta_reports_dictionary_resolution():
    synonyms_flat = {"oesophagus": "esophagus", "esophagus": "esophagus"}
    assert canonicalise_with_meta("Oesophagus", synonyms_flat=synonyms_flat) == ("esophagus", True)
    assert canonicalise_with_meta("Heart", synonyms_flat=synonyms_flat) == ("heart", False)


# ---- similarity ----------------------------------------------------------


def test_similarity_identical_strings():
    m = similarity("Prostate", "Prostate")
    assert m.score == 1.0
    assert m.method == "fuzzy"  # no dictionary used → fuzzy provenance


def test_similarity_case_insensitive():
    assert similarity("prostate", "PROSTATE").score == 1.0


def test_similarity_handles_separator_variations():
    # All of these collapse to "brain stem" → match
    assert similarity("Brain Stem", "Brainstem").score == 1.0
    assert similarity("Brain_Stem", "brain-stem").score == 1.0


def test_similarity_reordered_tokens_scores_high_but_not_one():
    """The hybrid Lev+Cosine score is high for permutations (cosine == 0) but
    not exactly 1 because the edit-distance component penalises the reorder."""
    s = similarity("Bowel_Bag", "Bag_Bowel").score
    assert 0.55 <= s < 1.0
    s2 = similarity("L_Parotid", "Parotid_L").score
    assert 0.55 <= s2 < 1.0


def test_similarity_low_for_unrelated_strings():
    # The Lev+Cosine hybrid scores ~0.4 for short unrelated strings (they share
    # a few common Latin characters). 0.5 is a comfortable ceiling for "no real
    # relationship" and well below the default 0.6 ⚠ threshold.
    assert similarity("Prostate", "Heart").score < 0.5


def test_similarity_uses_replacement_rules():
    rules = [ReplacementRule(find="musc_constrict", replace="pharyngeal_constrictor")]
    assert similarity("Musc_Constrict", "Pharyngeal Constrictor", rules=rules).score == 1.0


def test_similarity_uses_synonyms_and_marks_tg263():
    synonyms_flat = {"oesophagus": "Esophagus", "eso": "Esophagus", "esophagus": "Esophagus"}
    m1 = similarity("Oesophagus", "esophagus", synonyms_flat=synonyms_flat)
    assert m1.score == 1.0
    assert m1.method == "tg263"
    m2 = similarity("eso", "Oesophagus", synonyms_flat=synonyms_flat)
    assert m2.score == 1.0
    assert m2.method == "tg263"


def test_similarity_method_is_fuzzy_when_only_one_side_resolves():
    """If only one side resolves via the dictionary, the match isn't grounded."""
    synonyms_flat = {"oesophagus": "Esophagus", "esophagus": "Esophagus"}
    m = similarity("Oesophagus", "Heart", synonyms_flat=synonyms_flat)
    assert m.score < 0.5
    assert m.method == "fuzzy"


def test_similarity_empty_inputs_zero():
    m = similarity("", "Prostate")
    assert m.score == 0.0
    assert m.method == "none"
    assert similarity("Prostate", "").score == 0.0


def test_similarity_distinguishes_l_r_pairs():
    # The matcher should NOT treat Eye_L and Eye_R as identical.
    # token_set_ratio gives a non-trivial score but it must be < 1.
    assert similarity("Eye_L", "Eye_R").score < 1.0


def test_match_is_float_castable():
    """Match instances should be usable wherever a float is expected via cast."""
    m = similarity("Prostate", "Prostate")
    assert float(m) == 1.0


# ---- best_match ----------------------------------------------------------


def test_best_match_empty_candidates():
    chosen, match = best_match("Prostate", [], key=lambda x: x)
    assert chosen is None
    assert isinstance(match, Match)
    assert match.score == 0.0
    assert match.method == "none"


def test_best_match_returns_strongest_candidate():
    candidates = ["Bladder", "Prostate", "Rectum"]
    chosen, match = best_match("Prostate", candidates, key=lambda x: x)
    assert chosen == "Prostate"
    assert match.score == 1.0


def test_best_match_first_on_all_zero():
    """If every candidate scores 0, the first is returned so callers always get something."""
    candidates = ["xyz1", "xyz2"]
    chosen, match = best_match("", candidates, key=lambda x: x)
    assert chosen == "xyz1"
    assert match.score == 0.0
    assert match.method == "none"


def test_best_match_uses_supplied_key():
    objects = [{"name": "Bladder"}, {"name": "Prostate"}]
    chosen, match = best_match("PROSTATE", objects, key=lambda o: o["name"])
    assert chosen == {"name": "Prostate"}
    assert match.score == 1.0


# ---- synonyms loader ----------------------------------------------------


def test_load_synonyms_missing_file_returns_empty(tmp_path):
    assert load_synonyms(tmp_path / "nope.json") == {}


def test_load_synonyms_round_trip(tmp_path):
    path = tmp_path / "syn.json"
    payload = {"_comment": "ignored", "esophagus": ["oesophagus", "eso"]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_synonyms(path)
    assert loaded == {"esophagus": ["oesophagus", "eso"]}


def test_load_synonyms_ignores_invalid_entries(tmp_path):
    path = tmp_path / "syn.json"
    payload = {
        "esophagus": ["oesophagus", "eso"],
        "junk": "not_a_list",  # should be skipped
        "_ignored": ["whatever"],  # underscore prefix → comment
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_synonyms(path)
    assert loaded == {"esophagus": ["oesophagus", "eso"]}


def test_load_synonyms_malformed_file_returns_empty(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_synonyms(path) == {}


def test_flatten_synonyms():
    raw = {"esophagus": ["oesophagus", "eso"], "bowel_bag": ["bag_bowel"]}
    flat = flatten_synonyms(raw)
    # Variant lookups
    assert flat["oesophagus"] == "esophagus"
    assert flat["eso"] == "esophagus"
    assert flat["bagbowel"] == "bowel_bag"
    # Canonical key itself is also a variant of itself
    assert flat["esophagus"] == "esophagus"
    assert flat["bowelbag"] == "bowel_bag"


def test_flatten_synonyms_normalises_keys_and_values():
    raw = {"L_Parotid": ["L Parotid", "left parotid"]}
    flat = flatten_synonyms(raw)
    assert flat["lparotid"] == "L_Parotid"
    assert flat["leftparotid"] == "L_Parotid"


# ---- Integration: replacement_rules + synonyms together ------------------


def test_integration_rules_then_synonyms():
    """Rules apply first, then synonyms canonicalise."""
    rules = [ReplacementRule(find="constrict", replace="esophagus")]
    synonyms_flat = {"esophagus": "esophagus", "oesophagus": "esophagus"}
    # "Musc_Constrict" → rule → "musc_esophagus" → " " sub → "musc esophagus"
    # spaceless → "muscesophagus" — not a synonym → stays "musc esophagus"
    # then compared to "Oesophagus" → synonym → "esophagus"
    # similarity("musc esophagus", "esophagus") — high but not 1.0
    s = similarity("Musc_Constrict", "Oesophagus", rules=rules, synonyms_flat=synonyms_flat).score
    assert s > 0.5


# ---- Structural mismatch --------------------------------------------------
#
# Reported from real use: a ground truth of Lens_R was matched to a vendor's
# Lens_L — the only lens that vendor produced — and carried into metric
# computation with nothing to show anything was wrong. Similarity cannot catch
# this. Lens_R against Lens_L scores 0.85, well above the default 0.6 matching
# threshold, while Parotid_L against Submandibular_L, which really are
# different organs, scores 0.38.


@pytest.mark.parametrize(
    ("gt", "candidate"),
    [
        ("Lens_R", "Lens_L"),
        ("Kidney_R", "Kidney_L"),
        ("Parotid_L", "Parotid_R"),
        ("Left optic nerve", "Right optic nerve"),
        ("OpticNrv_L", "OpticNrv_R"),
    ],
)
def test_opposite_sides_never_match(gt, candidate, synonyms_flat):
    assert structural_mismatch(gt, candidate) == MISMATCH_LATERALITY
    match = similarity(gt, candidate, synonyms_flat=synonyms_flat)
    assert match.score == 0.0
    assert match.method == MISMATCH_LATERALITY


def test_a_side_against_no_side_is_still_allowed(synonyms_flat):
    """An omission is not a contradiction — a vendor may not state the side."""
    assert structural_mismatch("Parotid_L", "Parotid") == ""
    assert similarity("Parotid_L", "Parotid", synonyms_flat=synonyms_flat).score > 0.6


@pytest.mark.parametrize(
    ("gt", "candidate", "reason"),
    [
        ("Rib 1", "Rib 2", MISMATCH_INDEX),
        ("LN_Neck_IVA_L", "LN_Neck_IVB_L", MISMATCH_INDEX),
        ("Lung Lobe Upper", "Lung Lobe Lower", MISMATCH_POSITION),
        ("A_Aorta_Asc", "A_Aorta_Desc", MISMATCH_POSITION),
    ],
)
def test_other_structural_conflicts_are_blocked(gt, candidate, reason, synonyms_flat):
    assert structural_mismatch(gt, candidate) == reason
    assert similarity(gt, candidate, synonyms_flat=synonyms_flat).score == 0.0


@pytest.mark.parametrize(
    ("gt", "candidate"),
    [
        ("Lens_R", "Lens_R"),
        ("Optic Nerve Left", "OpticNrv_L"),
        ("Brainstem", "Brain Stem"),
        ("Parotid_L", "Left Parotid"),
        ("Bowel_Bag", "Bowel Bag"),
    ],
)
def test_genuine_matches_are_untouched(gt, candidate, synonyms_flat):
    """The gate must not cost anything on pairs that were already correct."""
    assert structural_mismatch(gt, candidate) == ""
    assert similarity(gt, candidate, synonyms_flat=synonyms_flat).score >= 0.6


def test_best_match_prefers_the_right_side_over_a_closer_wrong_one(synonyms_flat):
    """Given both sides, the correct one must win rather than the first seen."""
    chosen, match = best_match(
        "Lens_R",
        ["Lens_L", "Lens_R"],
        key=lambda x: x,
        synonyms_flat=synonyms_flat,
    )
    assert chosen == "Lens_R"
    assert match.score == 1.0


def test_the_only_candidate_being_the_wrong_side_scores_zero(synonyms_flat):
    """The reported failure: the wrong side is all a vendor produced.

    It is still returned — the app surfaces a poor match rather than dropping
    a contour silently — but with score 0 and a reason, so it is flagged
    instead of feeding metric computation unnoticed.
    """
    chosen, match = best_match("Lens_R", ["Lens_L"], key=lambda x: x, synonyms_flat=synonyms_flat)
    assert chosen == "Lens_L"
    assert match.score == 0.0
    assert match.method in MISMATCH_REASONS


def test_every_mismatch_reason_is_explainable():
    """The UI shows the reason, so each value needs wording."""
    for value in (MISMATCH_LATERALITY, MISMATCH_INDEX, MISMATCH_POSITION):
        assert MISMATCH_REASONS[value]
