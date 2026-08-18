"""Tests for s10 org-index construction (`_build_orgs`): curated hierarchy + full corpus
directory. Uses a tiny synthetic corpus so we exercise the emission logic without the
pipeline's heavy inputs.
"""

import polars as pl

from pipeline.stages import s10_indexes
from pipeline.stages.s10_indexes import _build_orgs, DIRECTORY_MIN_PAPERS


def _corpus(rows):
    return pl.DataFrame(rows)


# A resolved-orgs stub: one curated university with a known institution id.
RESOLVED = {
    "cmu": {
        "name": "Carnegie Mellon University",
        "kind": "university",
        "institutions": [{"id": "I74973139", "ror": "ror:cmu", "type": "education"}],
    },
}


def _run(rows, registry, monkeypatch):
    # Avoid touching affiliations.parquet on disk.
    monkeypatch.setattr(s10_indexes, "_load_unit_attribution", lambda corpus: {})
    return _build_orgs(_corpus(rows), RESOLVED, registry).institutions


def test_curated_root_and_directory_split(monkeypatch):
    rows = [
        {"node_id": 0, "institution_ids": ["I74973139", "I999"]},  # CMU + a directory org
        {"node_id": 1, "institution_ids": ["I999"]},
        {"node_id": 2, "institution_ids": ["I999"]},
        {"node_id": 3, "institution_ids": ["I74973139"]},
        {"node_id": 4, "institution_ids": ["I555"]},  # below the directory threshold (1 paper)
    ]
    registry = {
        "I999": {"display_name": "Peking University", "type": "education"},
        "I555": {"display_name": "Tiny Lab", "type": "education"},
    }
    insts = _run(rows, registry, monkeypatch)

    # Curated root present, flagged curated, with the right rollup node_ids.
    assert "cmu" in insts and insts["cmu"].curated is True
    assert insts["cmu"].node_ids == [0, 3]

    # Directory org present under a namespaced key, flagged non-curated, correct count.
    assert "oa:I999" in insts
    peking = insts["oa:I999"]
    assert peking.curated is False
    assert peking.display_name == "Peking University"
    assert peking.count == 3 and peking.node_ids == [0, 1, 2]

    # Below-threshold institution is dropped from the directory.
    assert DIRECTORY_MIN_PAPERS == 3
    assert "oa:I555" not in insts


def test_curated_institution_not_duplicated_in_directory(monkeypatch):
    # CMU's own OpenAlex id must never also appear as a directory "oa:" entry.
    rows = [{"node_id": i, "institution_ids": ["I74973139"]} for i in range(5)]
    insts = _run(rows, {}, monkeypatch)
    assert "cmu" in insts
    assert "oa:I74973139" not in insts


def test_directory_entry_falls_back_to_id_without_registry(monkeypatch):
    rows = [{"node_id": i, "institution_ids": ["I999"]} for i in range(3)]
    insts = _run(rows, {}, monkeypatch)  # empty registry
    assert insts["oa:I999"].display_name == "I999"


def test_curated_child_units_are_subsets_of_parent(monkeypatch):
    rows = [{"node_id": i, "institution_ids": ["I74973139"]} for i in range(6)]
    # Attribute nodes 0-1 to the Robotics Institute sub-unit.
    monkeypatch.setattr(
        s10_indexes, "_load_unit_attribution", lambda corpus: {"cmu": {"cmu-ri": {0, 1}}}
    )
    insts = _build_orgs(_corpus(rows), RESOLVED, {}).institutions
    assert "cmu-ri" in insts
    assert insts["cmu-ri"].parent == "cmu"
    assert set(insts["cmu-ri"].node_ids) <= set(insts["cmu"].node_ids)
    assert "cmu-ri" in insts["cmu"].children


def test_roster_backed_neolab_is_a_curated_root_with_provenance(monkeypatch):
    rows = [
        {"node_id": 0, "institution_ids": []},
        {"node_id": 1, "institution_ids": ["I74973139"]},
    ]
    roster_orgs = {"organizations": [{
        "key": "redwood",
        "display_name": "Redwood Research",
        "organization_id": "local:redwood-research",
        "kind": "neolab",
    }]}
    memberships = pl.DataFrame([
        {"org_key": "redwood", "node_id": 0, "provenance": "publication_history"},
        # Two matching roster authors on one paper must still yield one paper membership.
        {"org_key": "redwood", "node_id": 0, "provenance": "self_asserted"},
    ])
    monkeypatch.setattr(s10_indexes, "_load_unit_attribution", lambda corpus: {})
    insts = _build_orgs(
        _corpus(rows), RESOLVED, {}, roster_orgs, memberships,
    ).institutions

    redwood = insts["redwood"]
    assert redwood.kind == "neolab" and redwood.curated is True
    assert redwood.organization_id == "local:redwood-research"
    assert redwood.node_ids == [0] and redwood.count == 1
    assert redwood.membership_methods == ["publication_history", "self_asserted"]
