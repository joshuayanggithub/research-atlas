"""Golden tests for evidence-backed org sub-unit attribution.

These lock down the guarantees in docs/ORGANIZATION_DIRECTORY.md:
- FAIR / Meta AI / Reality Labs are separated and never all collapsed into "Meta".
- A parent match never implies a child; ambiguous prose never creates an attribution.
- Cross-org leakage is impossible (a CMU string cannot resolve to a Stanford unit).
"""

from pipeline.directory import attribute_units, extract_unit_keys
from pipeline.directory.units import ORG_UNITS


def test_fair_is_separated_from_generic_meta():
    assert extract_unit_keys("meta", ["Facebook AI Research (FAIR)"]) == ["meta-fair"]
    assert extract_unit_keys("meta", ["FAIR, Menlo Park"]) == ["meta-fair"]
    assert extract_unit_keys("meta", ["Fundamental AI Research, Meta"]) == ["meta-fair"]
    # Reality Labs and Meta AI are their own units, not FAIR.
    assert extract_unit_keys("meta", ["Facebook Reality Labs"]) == ["meta-reality-labs"]
    assert extract_unit_keys("meta", ["Meta AI"]) == ["meta-ai"]


def test_generic_meta_affiliation_has_no_subunit():
    # A parent-only affiliation must NOT be attributed to any child unit.
    assert extract_unit_keys("meta", ["Meta Platforms, Inc., Menlo Park, CA, USA"]) == []
    assert extract_unit_keys("meta", ["Facebook"]) == []


def test_facebook_ai_without_research_is_meta_ai_not_fair():
    # "Facebook AI" (no "Research") is Meta AI; only explicit FAIR maps to FAIR.
    assert extract_unit_keys("meta", ["Facebook AI, USA"]) == ["meta-ai"]


def test_ambiguous_lowercase_acronym_never_matches():
    # "fair" as ordinary prose must not create a FAIR attribution (case-sensitive acronym).
    assert extract_unit_keys("meta", ["a fair evaluation protocol"]) == []
    # "sail" / "email" style false positives for Stanford's SAIL.
    assert extract_unit_keys("stanford", ["available compute at Stanford"]) == []


def test_uppercase_acronym_matches():
    assert extract_unit_keys("stanford", ["SAIL, Stanford University"]) == ["stanford-sail"]
    assert extract_unit_keys("mit", ["CSAIL, MIT"]) == ["mit-csail"]
    assert extract_unit_keys("berkeley", ["BAIR, UC Berkeley"]) == ["berkeley-bair"]


def test_cmu_specific_unit_wins_over_school():
    # The Robotics Institute is more specific than "School of Computer Science".
    keys = extract_unit_keys(
        "cmu", ["Robotics Institute, School of Computer Science, Carnegie Mellon University"]
    )
    assert keys == ["cmu-ri"]


def test_no_cross_org_leakage():
    # A Stanford affiliation string offered under the CMU org resolves to no CMU unit.
    assert extract_unit_keys("cmu", ["Computer Science Department, Stanford University"]) != []
    assert extract_unit_keys("stanford", ["Robotics Institute, Carnegie Mellon"]) == []


def test_unknown_org_returns_nothing():
    assert extract_unit_keys("nonexistent-org", ["FAIR"]) == []


def test_attribute_units_is_org_scoped():
    evidence = {
        "meta": ["Facebook AI Research (FAIR)"],
        "cmu": ["Robotics Institute, CMU"],
    }
    result = attribute_units(evidence)
    assert result == {"meta": ["meta-fair"], "cmu": ["cmu-ri"]}


def test_every_unit_key_is_unique():
    seen = set()
    for units in ORG_UNITS.values():
        for u in units:
            assert u.key not in seen, f"duplicate unit key {u.key}"
            seen.add(u.key)
