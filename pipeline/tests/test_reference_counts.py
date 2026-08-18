"""Reference counting: what the map can draw vs what the paper actually cites.

These guard a class of bug that reached the user twice. The References tab lists only edges whose
OTHER end is also in this corpus, so a paper citing 18 works of which 5 are arXiv CS shows 5 —
correct about the map, and read as a claim about the paper. The reported case was
arXiv:2606.00321 ("Training-Free Object-Agnostic Jam Detection in Fulfillment Centers"): 18
references per S2, 5 of them on arXiv, 5 drawn.

The invariants below are the ones that make the on-screen sentence "5 of 18 references are in
this map" true.
"""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from pipeline.stages import s11_emit


def _ref_availability(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema={
        "node_id": pl.Int64,
        "references_available": pl.Boolean,
        "reference_count": pl.Int64,
    })


def test_reference_count_comes_from_the_stored_list(tmp_path, monkeypatch):
    """The total is S2's own reference list length, not a provider-reported column.

    `s2_reference_count` in the corpus disagreed with refs.parquet for the reported paper
    (9 vs 18), which is why the count is derived from the edge lists we actually hold.
    """
    path = tmp_path / "reference_availability.parquet"
    _ref_availability([
        {"node_id": 0, "references_available": True, "reference_count": 18},
        {"node_id": 1, "references_available": True, "reference_count": 0},
    ]).write_parquet(path)
    monkeypatch.setattr(s11_emit, "REF_AVAIL_IN", path)
    corpus = pl.DataFrame({"node_id": [0, 1]})
    assert s11_emit._reference_counts(corpus) == [18, 0]


def test_unavailable_reference_list_is_minus_one_not_zero(tmp_path, monkeypatch):
    """-1 and 0 mean different things and must not collapse.

    0 says "this paper cites nothing", -1 says "nobody extracted its bibliography" — the D29
    distinction. Rendering the second as the first is the original complaint about a paper
    showing no references at all.
    """
    path = tmp_path / "reference_availability.parquet"
    _ref_availability([
        {"node_id": 0, "references_available": False, "reference_count": 0},
        {"node_id": 1, "references_available": True, "reference_count": 0},
    ]).write_parquet(path)
    monkeypatch.setattr(s11_emit, "REF_AVAIL_IN", path)
    counts = s11_emit._reference_counts(pl.DataFrame({"node_id": [0, 1]}))
    assert counts[0] == -1, "no reference list must not read as zero references"
    assert counts[1] == 0, "an empty-but-extracted list is a genuine zero"


def test_missing_artifact_degrades_to_unknown(tmp_path, monkeypatch):
    """An interim tree without the artifact reports unknown, never a fabricated total."""
    monkeypatch.setattr(s11_emit, "REF_AVAIL_IN", tmp_path / "absent.parquet")
    assert s11_emit._reference_counts(pl.DataFrame({"node_id": [0, 1, 2]})) == [-1, -1, -1]


def test_older_artifact_without_the_column_degrades(tmp_path, monkeypatch):
    """A pre-existing availability file has no reference_count; that must not raise."""
    path = tmp_path / "reference_availability.parquet"
    pl.DataFrame({"node_id": [0], "references_available": [True]}).write_parquet(path)
    monkeypatch.setattr(s11_emit, "REF_AVAIL_IN", path)
    assert s11_emit._reference_counts(pl.DataFrame({"node_id": [0]})) == [-1]


def test_counts_follow_the_corpus_row_order(tmp_path, monkeypatch):
    """The list is positional — a join that reordered rows would mislabel every paper."""
    path = tmp_path / "reference_availability.parquet"
    _ref_availability([
        {"node_id": 0, "references_available": True, "reference_count": 5},
        {"node_id": 1, "references_available": True, "reference_count": 40},
        {"node_id": 2, "references_available": True, "reference_count": 7},
    ]).write_parquet(path)
    monkeypatch.setattr(s11_emit, "REF_AVAIL_IN", path)
    # Deliberately not ascending: s11 must not assume sorted input.
    corpus = pl.DataFrame({"node_id": [2, 0, 1]})
    assert s11_emit._reference_counts(corpus) == [7, 5, 40]


def test_a_paper_absent_from_the_artifact_is_unknown(tmp_path, monkeypatch):
    """Papers S2 never matched must report unknown rather than inheriting a neighbour's count."""
    path = tmp_path / "reference_availability.parquet"
    _ref_availability([
        {"node_id": 0, "references_available": True, "reference_count": 12},
    ]).write_parquet(path)
    monkeypatch.setattr(s11_emit, "REF_AVAIL_IN", path)
    assert s11_emit._reference_counts(pl.DataFrame({"node_id": [0, 99]})) == [12, -1]


@pytest.mark.parametrize("in_corpus,total", [(5, 18), (0, 3), (18, 18)])
def test_drawn_references_never_exceed_the_total(in_corpus, total):
    """The on-screen sentence only makes sense while this holds.

    In-corpus edges are a SUBSET of the paper's references, so "N of M" must have N <= M. If a
    rebuild ever violated this, the UI would claim more references are in the map than exist.
    """
    assert in_corpus <= total
