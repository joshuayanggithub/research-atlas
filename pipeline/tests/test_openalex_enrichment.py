import json

import httpx
import polars as pl

from pipeline.common.openalex_client import OpenAlexClient
from pipeline.stages import s15_enrich_openalex as enrich
from pipeline.common.openalex_client import OpenAlexClient


def _openalex_work():
    return {
        "id": "https://openalex.org/W123",
        "display_name": "Provider title",
        "publication_date": "2026-02-03",
        "publication_year": 2026,
        "cited_by_count": 17,
        "updated_date": "2026-08-14T00:00:00Z",
        "ids": {
            "doi": "https://doi.org/10.48550/arXiv.2601.00001",
            "mag": "987",
        },
        "primary_location": {"source": {"display_name": "A conference"}},
        "locations": [{
            "landing_page_url": "https://arxiv.org/abs/2601.00001v2",
            "pdf_url": "https://arxiv.org/pdf/2601.00001v2.pdf",
        }],
        "authorships": [{
            "author": {"id": "https://openalex.org/A1", "display_name": "Ada Lovelace"},
            "institutions": [{
                "id": "https://openalex.org/I1",
                "display_name": "Example Lab",
                "type": "company",
                "country_code": "US",
                "ror": "https://ror.org/example",
            }],
            "raw_affiliation_strings": ["Example Lab, Robotics Group"],
        }],
        "referenced_works": ["https://openalex.org/W9"],
        "primary_topic": {
            "id": "https://openalex.org/T10",
            "display_name": "Robot Learning",
            "subfield": {"id": "https://openalex.org/subfields/1702", "display_name": "AI"},
            "field": {"id": "https://openalex.org/fields/17", "display_name": "CS"},
            "domain": {"id": "https://openalex.org/domains/3", "display_name": "Physical"},
        },
        "topics": [{"id": "https://openalex.org/T10", "display_name": "Robot Learning"}],
    }


def test_extracts_arxiv_id_from_doi_and_versioned_locations():
    assert enrich._work_arxiv_ids(_openalex_work()) == {"2601.00001"}

    old = _openalex_work()
    old["ids"] = {}
    old["locations"] = [{
        "landing_page_url": "http://export.arxiv.org/abs/cs/0611005v3",
        "pdf_url": None,
    }]
    assert enrich._work_arxiv_ids(old) == {"cs/0611005"}


def test_normalize_keeps_structured_affiliation_and_secondary_citations():
    row = enrich._normalize_work(
        "2601.00001",
        _openalex_work(),
        "arxiv_doi",
        "2026-08-14T00:00:00+00:00",
        {"I1": "example"},
    )
    assert row["openalex_id"] == "W123"
    assert row["openalex_author_ids"] == ["A1"]
    assert row["openalex_institution_ids"] == ["I1"]
    assert row["openalex_referenced_works"] == ["W9"]
    assert row["openalex_cited_by_count"] == 17
    assert json.loads(row["org_affiliations_json"]) == {
        "example": ["Example Lab, Robotics Group"]
    }
    assert json.loads(row["institutions_json"])["I1"]["display_name"] == "Example Lab"


def test_materialize_preserves_arxiv_truth_and_node_order(tmp_path, monkeypatch):
    corpus_path = tmp_path / "corpus.parquet"
    enrichment_path = tmp_path / "enrichment.parquet"
    affil_path = tmp_path / "affiliations.parquet"
    institutions_path = tmp_path / "institutions.json"
    meta_path = tmp_path / "meta.json"
    active_path = tmp_path / "corpus_active.parquet"
    monkeypatch.setattr(enrich, "CORPUS_IN", corpus_path)
    monkeypatch.setattr(enrich, "ENRICHMENT_OUT", enrichment_path)
    monkeypatch.setattr(enrich, "AFFIL_OUT", affil_path)
    monkeypatch.setattr(enrich, "INSTITUTIONS_OUT", institutions_path)
    monkeypatch.setattr(enrich, "META_OUT", meta_path)
    monkeypatch.setattr(enrich, "CORPUS_ACTIVE", active_path)

    corpus = pl.DataFrame([{ 
        "paper_id": "arxiv:2601.00001",
        "arxiv_id": "2601.00001",
        "node_id": 0,
        "title": "Canonical arXiv title",
        "abstract": "Canonical abstract",
        "publication_date": "2026-01-01",
        "year": 2026,
        "arxiv_categories": ["cs.RO", "cs.CV"],
        "domain_id": 1,
        "field_id": 17,
        "subfield_id": -1,
        "topic_id": 123,
        "domain_name": "Computer Science and Statistics",
        "field_name": "Computer Science",
        "subfield_name": None,
        "topic_name": "cs.RO",
        "author_ids": ["arxiv-name:1"],
        "author_names": ["A. Lovelace"],
        "institution_ids": [],
        "doi": None,
        "mag_id": None,
        "venue": "arXiv",
        "cited_by_count": 0,
        "referenced_works": [],
    }])
    corpus.write_parquet(corpus_path)
    normalized = enrich._normalize_work(
        "2601.00001",
        _openalex_work(),
        "arxiv_doi",
        "2026-08-14T00:00:00+00:00",
        {"I1": "example"},
    )

    coverage = enrich._materialize(corpus, {"2601.00001": normalized}, "now")
    result = pl.read_parquet(corpus_path).row(0, named=True)
    assert coverage == 1.0
    assert result["paper_id"] == "arxiv:2601.00001"
    assert result["title"] == "Canonical arXiv title"
    assert result["publication_date"] == "2026-01-01"
    assert result["arxiv_categories"] == ["cs.RO", "cs.CV"]
    assert result["author_ids"] == ["A1"]
    assert result["institution_ids"] == ["I1"]
    assert result["cited_by_count"] == 0
    assert result["referenced_works"] == []
    assert result["domain_id"] == 3
    assert result["field_id"] == 17
    assert result["subfield_id"] == 1702
    assert result["topic_id"] == 10
    assert result["domain_name"] == "Physical"
    assert result["field_name"] == "CS"
    assert result["subfield_name"] == "AI"
    assert result["topic_name"] == "Robot Learning"
    assert result["openalex_cited_by_count"] == 17
    assert pl.read_parquet(affil_path).row(0, named=True)["paper_id"] == "arxiv:2601.00001"


def test_list_works_cursors_when_duplicate_rows_exceed_one_page(monkeypatch):
    client = OpenAlexClient()
    pages = iter([
        {"results": [{"id": "W1"}], "meta": {"next_cursor": "next"}},
        {"results": [{"id": "W2"}], "meta": {"next_cursor": None}},
    ])
    monkeypatch.setattr(client, "_get", lambda *_args, **_kwargs: next(pages))
    try:
        assert [row["id"] for row in client.list_works("doi:a|b", "id")] == ["W1", "W2"]
    finally:
        client.close()


def test_openalex_client_rotates_to_fallback_key_on_daily_quota(monkeypatch):
    client = OpenAlexClient(api_keys=["primary", "fallback"])
    seen_keys = []
    responses = iter([
        httpx.Response(
            429,
            headers={"retry-after": "3600"},
            request=httpx.Request("GET", "https://api.openalex.org/works"),
        ),
        httpx.Response(
            200,
            json={"results": []},
            request=httpx.Request("GET", "https://api.openalex.org/works"),
        ),
    ])

    def fake_get(_url, *, params):
        seen_keys.append(params.get("api_key"))
        return next(responses)

    monkeypatch.setattr(client._client, "get", fake_get)
    try:
        assert client._get("/works", {"select": "id"}) == {"results": []}
        assert seen_keys == ["primary", "fallback"]
        assert client.api_key == "fallback"
    finally:
        client.close()
