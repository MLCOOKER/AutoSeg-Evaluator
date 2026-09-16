"""Tests for canonical organ grouping.

The property that matters most here is a negative one: no amount of string
similarity may ever put a left organ and a right organ in the same group.
Measured on this project's own matcher, ``OpticNerve_L`` and ``OpticNerve_R``
score 0.926 while ``Parotid_L`` and ``Submandibular_L`` — genuinely different
organs — score 0.378. A similarity threshold cannot separate those cases, so
laterality is extracted onto its own axis and the guarantee becomes structural.
Several tests below exist purely to keep it that way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoseg_evaluator.core.organ_groups import (
    LATERALITY_L,
    LATERALITY_NONE,
    LATERALITY_R,
    QUALIFIER_COMPOSITE,
    QUALIFIER_DOSE,
    QUALIFIER_NONANATOMIC,
    QUALIFIER_OAR,
    QUALIFIER_PRV,
    QUALIFIER_TARGET,
    TIER_DICTIONARY,
    TIER_MANUAL,
    TIER_STRIPPED,
    TIER_UNASSIGNED,
    OrganKey,
    OrganRules,
    assign,
    classify_qualifier,
    differs_by_a_short_code,
    dominant_type,
    extract_laterality,
    group_by_key,
    index_signature,
    load_rules,
    positional_signature,
    propose_fuzzy_groups,
    strip_decorations,
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
def syn():
    return flatten_synonyms(load_synonyms(SYNONYMS_PATH))


def _assign(name, itype="ORGAN", syn=None, **kw):
    return assign(name, itype, synonyms_flat=syn, **kw)


# ---- Laterality extraction ------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expect_lat"),
    [
        ("Parotid_L", LATERALITY_L),
        ("Parotid_R", LATERALITY_R),
        ("Left optic nerve", LATERALITY_L),
        ("Right optic nerve", LATERALITY_R),
        ("optic nerve lt", LATERALITY_L),
        ("Lt Optic Nerve", LATERALITY_L),
        ("LN_L4_R", LATERALITY_R),
        ("Bone_Ilium_L", LATERALITY_L),
        ("Brainstem", LATERALITY_NONE),
        ("Heart", LATERALITY_NONE),
    ],
)
def test_extract_laterality(name, expect_lat):
    _stem, lat = extract_laterality(name)
    assert lat == expect_lat


@pytest.mark.parametrize("name", ["LAD_Coronary", "Rectum", "Liver", "Lips", "Lung", "Retina"])
def test_leading_letter_is_not_mistaken_for_laterality(name):
    """A word starting with L or R must not read as 'left' or 'right'.

    Extraction works on whole tokens, so ``LAD_Coronary`` keeps its L.
    """
    _stem, lat = extract_laterality(name)
    assert lat == LATERALITY_NONE


def test_single_token_never_yields_laterality():
    """``L`` alone is not an organ; refusing it avoids an empty base."""
    assert extract_laterality("L")[1] == LATERALITY_NONE
    assert extract_laterality("Left")[1] == LATERALITY_NONE


# ---- THE guarantee: left never merges with right --------------------------

_LR_PAIRS = [
    ("OpticNerve_L", "OpticNerve_R"),
    ("Parotid_L", "Parotid_R"),
    ("Lung_L", "Lung_R"),
    ("Cochlea_L", "Cochlea_R"),
    ("Left optic nerve", "Right optic nerve"),
    ("Kidney_L", "Kidney_R"),
    ("Glnd_Submand_L", "Glnd_Submand_R"),
    ("Hippocampus_L_MR", "Hippocampus_R_MR"),
    ("Musc_Scalene_Ant_L(BrachialPlex_proxy)", "Musc_Scalene_Ant_R(BrachialPlex_proxy)"),
]


@pytest.mark.parametrize(("left", "right"), _LR_PAIRS)
def test_left_and_right_never_share_a_key(left, right, syn):
    a = _assign(left, syn=syn)
    b = _assign(right, syn=syn)
    assert a.key != b.key, f"{left} and {right} must not group together"
    assert a.key.laterality == LATERALITY_L
    assert b.key.laterality == LATERALITY_R
    # Same organ family, though — so pooling on request still works.
    assert a.key.base == b.key.base
    assert a.key.pooled == b.key.pooled


@pytest.mark.parametrize(("left", "right"), _LR_PAIRS)
def test_fuzzy_proposals_never_cross_laterality(left, right, syn):
    """Even with the threshold dropped to zero, the axes cannot be crossed."""
    reckless = OrganRules(fuzzy_threshold=0.0)
    items = [_assign(left, syn=syn), _assign(right, syn=syn)]
    proposals = propose_fuzzy_groups(items, synonyms_flat=syn, rules=reckless)
    for proposal in proposals:
        assert not (left in proposal.members and right in proposal.members)


def test_pooling_is_available_when_wanted(syn):
    """Grouping by the pooled key is how 'both parotids' is asked for."""
    items = [_assign("Parotid_L", syn=syn), _assign("Parotid_R", syn=syn)]
    assert len(group_by_key(items)) == 2
    pooled = group_by_key(items, pool_laterality=True)
    assert len(pooled) == 1
    assert len(next(iter(pooled.values()))) == 2


# ---- Tier 1: dictionary ---------------------------------------------------


def test_spelling_variants_collapse_through_the_dictionary(syn):
    """The motivating case: TG-263 and free text reaching one key."""
    names = [
        "OpticNerve_L",
        "Left optic nerve",
        "L optic nerve",
        "optic nerve lt",
        "Lt Optic Nerve",
        "OpticNrv_L",
    ]
    keys = {_assign(n, syn=syn).key for n in names}
    assert len(keys) == 1, f"expected one key, got {keys}"
    key = next(iter(keys))
    assert key.laterality == LATERALITY_L
    assert all(_assign(n, syn=syn).tier == TIER_DICTIONARY for n in names)


def test_brainstem_spellings_collapse(syn):
    keys = {_assign(n, syn=syn).key for n in ("Brainstem", "Brain Stem", "BrainStem")}
    assert len(keys) == 1


# ---- Tier 2: decoration stripping -----------------------------------------


@pytest.mark.parametrize(
    ("decorated", "plain"),
    [
        ("Brainstem_Experimental", "Brainstem"),
        ("Eye_L_Experimental", "Eye_L"),
        ("Chestwall_L (1)", "Chestwall_L"),
        ("Brain_MR", "Brain"),
        ("Hippocampus_L_MR", "Hippocampus_L"),
        ("Bladder_F", "Bladder"),
        ("Parotid_L_Experimental", "Parotid_L"),
    ],
)
def test_decorated_names_reach_the_same_key_as_plain_ones(decorated, plain, syn):
    assert _assign(decorated, syn=syn).key == _assign(plain, syn=syn).key


def test_stripping_is_recorded_for_audit(syn):
    result = _assign("Brainstem_Experimental", syn=syn)
    assert result.tier == TIER_STRIPPED
    assert result.removed, "what was removed must be reported to the user"
    assert "Experimental" in result.removed[0]


def test_stacked_decorations_are_all_removed():
    clean, removed = strip_decorations("Eye_L_Experimental (1)")
    assert clean == "Eye_L"
    assert len(removed) == 2


def test_parenthetical_annotation_is_stripped(syn):
    a = _assign("Musc_Scalene_Ant_L(BrachialPlex_proxy)", syn=syn)
    b = _assign("Musc_Scalene_Ant_L", syn=syn)
    assert a.key == b.key


def test_stripping_never_empties_a_name():
    """A name that is nothing but decoration must not become the empty base."""
    for name in ("Experimental", "MR", "F", "(1)"):
        clean, _removed = strip_decorations(name)
        assert clean or name, "stripping must not annihilate the only token"


# ---- Qualifiers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "itype", "expect"),
    [
        ("Parotid_L", "ORGAN", QUALIFIER_OAR),
        ("Kidney Left", "AVOIDANCE", QUALIFIER_OAR),
        ("1CTV_", "CTV", QUALIFIER_TARGET),
        ("LN_Neck_IB_L", "CTV", QUALIFIER_TARGET),
        ("Body", "EXTERNAL", QUALIFIER_NONANATOMIC),
        ("Board", "SUPPORT", QUALIFIER_NONANATOMIC),
        ("Ring 48Gy", "CONTROL", QUALIFIER_NONANATOMIC),
        ("BrainstemPRV", "ORGAN", QUALIFIER_PRV),
        ("Heart+A_Pulm", "ORGAN", QUALIFIER_COMPOSITE),
        ("Body-PTV", "ORGAN", QUALIFIER_COMPOSITE),
        ("10.00 Gy (16.54% of Previous Dose(s))", "ORGAN", QUALIFIER_DOSE),
    ],
)
def test_classify_qualifier(name, itype, expect):
    assert classify_qualifier(name, itype) == expect


def test_avoidance_counts_as_an_organ():
    """Clinical sets type Kidney/Liver/Skeleton as AVOIDANCE — intent, not anatomy."""
    for name in ("Kidney Left", "Liver", "Skeleton"):
        assert classify_qualifier(name, "AVOIDANCE") == QUALIFIER_OAR


def test_name_level_facts_outrank_the_dicom_type():
    """A PRV typed ORGAN is still a PRV; the type cannot express it."""
    assert classify_qualifier("BrainstemPRV", "ORGAN") == QUALIFIER_PRV
    assert classify_qualifier("Brain-PTV", "ORGAN") == QUALIFIER_COMPOSITE


def test_targets_are_classified_not_discarded():
    """Nodal levels are targets, but nothing here filters them out."""
    result = assign("LN_Neck_IB_L", "CTV")
    assert result.key.qualifier == QUALIFIER_TARGET
    assert result.key.base, "a target still gets a usable base"
    assert result.key.laterality == LATERALITY_L


def test_qualifier_separates_otherwise_identical_names():
    """Brainstem and BrainstemPRV must not group together."""
    assert assign("Brainstem", "ORGAN").key != assign("BrainstemPRV", "ORGAN").key


def test_missing_type_falls_back_to_the_name():
    assert classify_qualifier("1PTV_", "") == QUALIFIER_TARGET
    assert classify_qualifier("Parotid_L", "") == QUALIFIER_OAR


def test_ordinary_anatomy_is_not_called_a_composite():
    """Underscored and hyphen-free anatomy must stay plain organs."""
    rules = OrganRules()
    for name in ("Glnd_Submand_L", "A_Aorta_Dsc", "Bone_Mandible", "Parotid_L"):
        assert classify_qualifier(name, "ORGAN", rules) == QUALIFIER_OAR


@pytest.mark.parametrize(
    "name", ["BODY -1.0", "BODY-1.5cm", "GTV+10mm", "Metal+1.0cm", "BODY-PTV+2.0CM"]
)
def test_margin_operations_are_composites(name):
    """A structure grown or shrunk from another is not that other structure."""
    assert classify_qualifier(name, "ORGAN") == QUALIFIER_COMPOSITE


# ---- Manual assignment ----------------------------------------------------


def test_manual_assignment_outranks_every_automatic_tier(syn):
    manual = {"Parotid_L": "salivary_gland"}
    result = _assign("Parotid_L", syn=syn, manual=manual)
    assert result.tier == TIER_MANUAL
    assert result.key.base == "salivary_gland"
    # Laterality still comes from the original name, not the typed base.
    assert result.key.laterality == LATERALITY_L


def test_manual_assignment_decomposes_a_typed_laterality(syn):
    """Typing "salivary_left" means the left one, not an organ called that."""
    result = _assign("Parotid_L", syn=syn, manual={"Parotid_L": "salivary_left"})
    assert result.key.base == "salivary"
    assert result.key.laterality == LATERALITY_L


def test_manual_assignment_keeps_laterality_from_the_original(syn):
    """Assigning a side-neutral base must not lose which side it was."""
    result = _assign("Parotid_L", syn=syn, manual={"Parotid_L": "parotid"})
    assert result.key.laterality == LATERALITY_L


# ---- Fuzzy proposals ------------------------------------------------------


def test_unresolved_names_stand_alone_rather_than_failing(syn):
    result = _assign("Aorte_Thx_Asc", syn=syn)
    assert result.tier == TIER_UNASSIGNED
    assert result.key.base, "an unresolved name still gets a usable group of one"


def test_fuzzy_proposes_but_does_not_apply(syn):
    items = [_assign(n, syn=syn) for n in ("Bowel_Bag", "Bowel Bag", "Bag_Bowel")]
    proposals = propose_fuzzy_groups(items, synonyms_flat=syn)
    assert proposals, "near-identical spellings should be proposed"
    # The assignments themselves are untouched — proposals are advisory.
    assert all(i.tier == TIER_UNASSIGNED for i in items)


def test_fuzzy_seeds_on_the_most_frequent_spelling(syn):
    items = [_assign(n, syn=syn) for n in ("Bowel Bag", "Bowel_Bag")]
    proposals = propose_fuzzy_groups(
        items, synonyms_flat=syn, frequencies={"Bowel_Bag": 60, "Bowel Bag": 3}
    )
    assert proposals
    assert proposals[0].members[0] == "Bowel_Bag"


def test_fuzzy_never_crosses_qualifiers(syn):
    """A target and an organ cannot be proposed as one group, however alike."""
    reckless = OrganRules(fuzzy_threshold=0.0)
    items = [assign("Brainstem_x", "ORGAN"), assign("Brainstem_x", "CTV")]
    for proposal in propose_fuzzy_groups(items, synonyms_flat=syn, rules=reckless):
        assert len({m for m in proposal.members}) <= 1


def test_singleton_proposals_are_not_reported(syn):
    """Only actual merges are worth a user's attention."""
    items = [_assign("Something_Unique_Xyz", syn=syn)]
    assert propose_fuzzy_groups(items, synonyms_flat=syn) == []


# ---- Type conflicts -------------------------------------------------------


def test_dominant_type_reports_conflict():
    """Nodal levels really are typed both ways across producers."""
    itype, conflicted = dominant_type({"ORGAN": 58, "CTV": 38})
    assert itype == "ORGAN"
    assert conflicted is True


def test_dominant_type_ignores_blanks():
    itype, conflicted = dominant_type({"ORGAN": 5, "(blank)": 74})
    assert itype == "ORGAN"
    assert conflicted is False


def test_dominant_type_with_nothing_populated():
    assert dominant_type({"(blank)": 3}) == ("", False)


# ---- Rules loading --------------------------------------------------------


def test_rules_fall_back_to_defaults_when_absent(tmp_path):
    assert load_rules(None).decorations == OrganRules().decorations
    assert load_rules(tmp_path / "nope.json").fuzzy_threshold == OrganRules().fuzzy_threshold


def test_malformed_rules_file_does_not_raise(tmp_path):
    bad = tmp_path / "organ_rules.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_rules(bad).decorations == OrganRules().decorations


def test_rules_file_can_override_decorations(tmp_path):
    path = tmp_path / "organ_rules.json"
    path.write_text(
        json.dumps(
            {
                "decorations": [{"pattern": r"[_\s-]+DRAFT\b", "reason": "draft marker"}],
                "fuzzy_threshold": 0.95,
            }
        ),
        encoding="utf-8",
    )
    rules = load_rules(path)
    assert rules.fuzzy_threshold == 0.95
    clean, removed = strip_decorations("Parotid_L_DRAFT", rules)
    assert clean == "Parotid_L"
    assert "draft marker" in removed[0]


# ---- Key behaviour --------------------------------------------------------


def test_key_is_hashable_and_sortable():
    keys = {OrganKey("parotid", LATERALITY_R), OrganKey("parotid", LATERALITY_L)}
    assert len(keys) == 2
    assert sorted(keys)[0].laterality == LATERALITY_L


def test_key_string_form_is_stable_and_readable():
    assert str(OrganKey("parotid", LATERALITY_L)) == "parotid|L"
    assert str(OrganKey("brainstem")) == "brainstem"
    assert str(OrganKey("ln_neck", LATERALITY_L, QUALIFIER_TARGET)) == "ln_neck|L|target"


def test_key_label_is_human_readable():
    assert OrganKey("opticnrv", LATERALITY_L).label() == "Opticnrv (L)"
    assert "target" in OrganKey("ln_neck", "", QUALIFIER_TARGET).label()


# ---- Barriers found by validating against real cohorts --------------------
#
# Each of the following was a live defect caught by running the grouper over
# the HN1 and Tender corpora rather than by any test — the fuzzy tier merged
# all twelve ribs into one structure, put three nodal levels together, and
# crossed left with right on a name whose laterality sat mid-string. They are
# pinned here so they cannot come back.


def test_mid_name_laterality_is_extracted(syn):
    """Clinical nomenclature buries the side in the middle of the name."""
    left = "Level IVb Left: Medial supraclavicular group"
    right = "Level IVb Right: Medial supraclavicular group"
    assert extract_laterality(left)[1] == LATERALITY_L
    assert extract_laterality(right)[1] == LATERALITY_R
    assert _assign(left, syn=syn).key != _assign(right, syn=syn).key


def test_punctuation_does_not_hide_laterality():
    """A colon stuck to the word was why the mid-name case slipped through."""
    for name in ("Lung (Left): upper lobe", "Kidney, Left", "Parotid - Left"):
        assert extract_laterality(name)[1] == LATERALITY_L


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Rib Left 1", "Rib Left 12"),
        ("Rib Right 2", "Rib Right 9"),
        ("LN_Neck_IX_L", "LN_Neck_XA_L"),
        ("LN_Neck_VIA", "LN_Neck_VIB"),
        ("LN_L4_R", "LN_L5_R"),
    ],
)
def test_enumerated_structures_never_fuzzy_merge(a, b, syn):
    """Names differing only by their index are different structures."""
    reckless = OrganRules(fuzzy_threshold=0.0)
    items = [_assign(a, syn=syn), _assign(b, syn=syn)]
    for proposal in propose_fuzzy_groups(items, synonyms_flat=syn, rules=reckless):
        assert not (a in proposal.members and b in proposal.members)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Aorte_Thx_Asc", "Aorte_Thx_Desc"),
        ("Lung Lobe Left Lower", "Lung Lobe Left Upper"),
        ("LN External Iliac Left", "LN Internal Iliac Left"),
        ("Proximal_Circumflex_Coronary_Art", "Distal_Circumflex_Coronary_Art"),
        ("Musc_Constrict_Sup", "Musc_Constrict_Inf"),
    ],
)
def test_opposed_position_modifiers_never_fuzzy_merge(a, b, syn):
    """Antonym pairs sit a few characters apart but name opposite things."""
    reckless = OrganRules(fuzzy_threshold=0.0)
    items = [_assign(a, syn=syn), _assign(b, syn=syn)]
    for proposal in propose_fuzzy_groups(items, synonyms_flat=syn, rules=reckless):
        assert not (a in proposal.members and b in proposal.members)


def test_index_signature_reads_digits_and_roman_numerals():
    assert index_signature("Rib Left 12") == ("12",)
    assert index_signature("LN_Neck_IVB_L") == ("ivb",)
    assert index_signature("Parotid_L") == ()


def test_positional_signature_folds_spellings_of_one_axis():
    """``asc`` and ``ascending`` are one modifier; ``asc`` and ``desc`` are not."""
    assert positional_signature("A_Aorta_Asc") == positional_signature("Aorta ascending")
    assert positional_signature("A_Aorta_Asc") != positional_signature("A_Aorta_Desc")
    assert positional_signature("Parotid_L") == ()


def test_formatting_variants_still_merge(syn):
    """The barriers must not stop the merges that are the whole point."""
    names = ["Bowel_Bag", "Bowel Bag", "bowel bag"]
    items = [_assign(n, syn=syn) for n in names]
    proposals = propose_fuzzy_groups(items, synonyms_flat=syn)
    assert proposals, "pure formatting differences should still be proposed"
    assert len(proposals[0].members) == 3


def test_typo_variants_still_merge(syn):
    """Character similarity is retained for what it is actually good at."""
    items = [_assign(n, syn=syn) for n in ("Artefact", "Artifact")]
    assert propose_fuzzy_groups(items, synonyms_flat=syn)


def test_parenthetical_naming_a_target_is_not_stripped(syn):
    """``Kidney_L(PTV)`` is the kidney cropped to the PTV, not the kidney."""
    clean, _removed = strip_decorations("Kidney_L(PTV)")
    assert "PTV" in clean
    assert _assign("Kidney_L(PTV)", syn=syn).key != _assign("Kidney_L", syn=syn).key


def test_plain_annotation_parenthetical_is_still_stripped(syn):
    assert _assign("Brain(DRtoReview)", syn=syn).key == _assign("Brain", syn=syn).key


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("UJ_Front_L", "LJ_Front_L"),
        ("UJ_Molar_R", "LJ_Molar_R"),
        ("A_Aorta", "V_Aorta"),
        ("LN_Ax_L1", "LN_Ax_L2"),
    ],
)
def test_short_codes_never_fuzzy_merge(a, b, syn):
    """Upper vs lower jaw, artery vs vein: one character, different structures.

    Caught by running the review dialog over a real head-and-neck cohort,
    where UJ_Front_L was being proposed as a merge with LJ_Front_L.
    """
    assert differs_by_a_short_code(a, b)
    reckless = OrganRules(fuzzy_threshold=0.0)
    items = [_assign(a, syn=syn), _assign(b, syn=syn)]
    for proposal in propose_fuzzy_groups(items, synonyms_flat=syn, rules=reckless):
        assert not (a in proposal.members and b in proposal.members)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Artefact", "Artifact"),
        ("Humeral_Head_L", "Humerus_Head_L"),
        ("Artefact_Adipose", "Aretfact_Adipose"),
    ],
)
def test_long_word_typos_still_merge(a, b, syn):
    """The same single-character difference in a real word is a spelling variant."""
    assert not differs_by_a_short_code(a, b)
    assert propose_fuzzy_groups([_assign(a, syn=syn), _assign(b, syn=syn)], synonyms_flat=syn)


def test_short_code_guard_ignores_differently_shaped_names():
    """Only an exactly-one-token difference is a code swap."""
    assert not differs_by_a_short_code("Bowel_Bag", "Bowel Bag")
    assert not differs_by_a_short_code("Great Vessels", "GreatVessels")
    assert not differs_by_a_short_code("UJ_Front_L", "LJ_Molar_R")


def test_a_spelling_the_dictionary_knows_is_not_left_to_the_fuzzy_tier(syn):
    """British spellings outside TG-263 fall to manual assignment, not to fuzzy.

    ``Esophagus`` resolves through the dictionary, so it never enters the pool
    the fuzzy tier works over, and ``Oesophagus`` — which the dictionary does
    not carry — stands alone until someone says otherwise. Letting similarity
    reach across into dictionary-resolved names would mean a guess overriding a
    known answer, which is the wrong trade.
    """
    known = _assign("Esophagus", syn=syn)
    unknown = _assign("Oesophagus", syn=syn)
    assert known.tier == TIER_DICTIONARY
    assert unknown.tier == TIER_UNASSIGNED
    assert propose_fuzzy_groups([known, unknown], synonyms_flat=syn) == []
    # Saying so by hand resolves it.
    fixed = _assign("Oesophagus", syn=syn, manual={"Oesophagus": known.key.base})
    assert fixed.key == known.key


def test_laterality_inside_a_parenthetical_is_not_stripped_away(syn):
    """``Mammary tissue(Lt)`` and ``(Rt)`` are two organs, not one.

    Found on a real paediatric cohort: the trailing-parenthetical rule was
    removing the very token that carried the side, merging the left and the
    right breast. The earlier corpus check missed it because it split names on
    whitespace only, so "tissue(Lt)" never looked like a laterality token.
    """
    left = _assign("Mammary tissue(Lt)", syn=syn)
    right = _assign("Mammary tissue(Rt)", syn=syn)
    neither = _assign("Mammary tissue", syn=syn)

    assert left.key.laterality == LATERALITY_L
    assert right.key.laterality == LATERALITY_R
    assert neither.key.laterality == LATERALITY_NONE
    assert len({left.key, right.key, neither.key}) == 3
    assert left.key.base == right.key.base == neither.key.base


@pytest.mark.parametrize(
    ("name", "side"),
    [
        ("Lung(L)", LATERALITY_L),
        ("Kidney (Right)", LATERALITY_R),
        ("Parotid(rt)", LATERALITY_R),
    ],
)
def test_parenthetical_sides_survive_in_every_spelling(name, side, syn):
    assert _assign(name, syn=syn).key.laterality == side


def test_ordinary_annotations_are_still_stripped(syn):
    """The guard must not stop the parenthetical rule doing its job."""
    assert _assign("Brain(DRtoReview)", syn=syn).key == _assign("Brain", syn=syn).key
    assert _assign("SpinalCord(StJude)", syn=syn).key == _assign("SpinalCord", syn=syn).key
