import polars as pl

from pipeline.stages import s16_apply_openalex_citations as citations


def _corpus() -> pl.DataFrame:
    return pl.DataFrame([
        {
            "node_id": 0, "paper_id": "arxiv:2501.00001", "cited_by_count": 0,
            "referenced_works": [], "openalex_id": "W1", "openalex_cited_by_count": 9,
            "openalex_referenced_works": [],
        },
        {
            "node_id": 1, "paper_id": "arxiv:2501.00002", "cited_by_count": 0,
            "referenced_works": [], "openalex_id": "W2", "openalex_cited_by_count": 0,
            "openalex_referenced_works": ["W1", "W999", "W1"],
        },
        {
            "node_id": 2, "paper_id": "arxiv:2501.00003", "cited_by_count": 0,
            "referenced_works": [], "openalex_id": "W3", "openalex_cited_by_count": 3,
            "openalex_referenced_works": ["W1", "W2"],
        },
        {
            "node_id": 3, "paper_id": "arxiv:2501.00004", "cited_by_count": 0,
            "referenced_works": [], "openalex_id": None, "openalex_cited_by_count": None,
            "openalex_referenced_works": None,
        },
    ])


def test_materialize_uses_openalex_totals_and_only_exact_internal_references():
    result, meta = citations._materialize(_corpus())
    rows = result.sort("node_id").to_dicts()

    assert [row["cited_by_count"] for row in rows] == [9, 0, 3, 0]
    assert [row["openalex_citation_available"] for row in rows] == [True, True, True, False]
    assert rows[1]["referenced_works"] == ["arxiv:2501.00001"]
    assert rows[2]["referenced_works"] == ["arxiv:2501.00001", "arxiv:2501.00002"]
    assert rows[3]["referenced_works"] == []
    assert meta["openalex_match_count"] == 3
    assert meta["internal_edge_count"] == 3


def test_materialize_never_overwrites_completed_s2_canonical_values():
    corpus = _corpus().with_columns([
        pl.Series("s2_citation_available", [True, False, False, False]),
        pl.Series("cited_by_count", [42, 0, 3, 0]),
        pl.Series("referenced_works", [["arxiv:2501.00003"], [], [], []]),
    ])
    result, meta = citations._materialize(corpus)
    first = result.sort("node_id").row(0, named=True)

    assert first["cited_by_count"] == 42
    assert first["referenced_works"] == ["arxiv:2501.00003"]
    assert first["openalex_citation_available"] is True
    assert meta["canonical_values_applied"] is False
