from pipeline.stages.s02_build_corpus import _arxiv_from_ids, _clean_doi, _parse_work


def test_clean_doi_keeps_full_path():
    # The critical regression: DOIs must NOT be shortened to their last path segment.
    assert _clean_doi("https://doi.org/10.1145/3641289") == "10.1145/3641289"
    assert _clean_doi("https://doi.org/10.1609/aaai.v26i1.8301") == "10.1609/aaai.v26i1.8301"
    assert _clean_doi("http://doi.org/10.1/2") == "10.1/2"
    assert _clean_doi("10.1145/3641289") == "10.1145/3641289"  # already bare


def test_clean_doi_empty():
    assert _clean_doi(None) is None
    assert _clean_doi("") is None


def test_arxiv_extraction():
    assert _arxiv_from_ids({"doi": "https://doi.org/10.48550/arXiv.2203.15556"}) == "2203.15556"
    assert _arxiv_from_ids({"doi": "https://doi.org/10.1145/3641289"}) is None
    assert _arxiv_from_ids({}) is None


def _work(authorships):
    return {"id": "https://openalex.org/W1", "title": "T", "authorships": authorships}


def test_org_affiliation_evidence_is_scoped_per_authorship():
    # A paper with a Meta/FAIR author and an unaffiliated co-author: only the FAIR string is
    # recorded under 'meta', and the co-author's university string never leaks into it.
    inst_to_org = {"I4210114444": "meta", "I95457486": "berkeley"}
    rec = _parse_work(
        _work([
            {"author": {"id": "https://openalex.org/A1", "display_name": "A"},
             "institutions": [{"id": "https://openalex.org/I4210114444"}],
             "raw_affiliation_strings": ["Facebook AI Research (FAIR)"]},
            {"author": {"id": "https://openalex.org/A2", "display_name": "B"},
             "institutions": [{"id": "https://openalex.org/I95457486"}],
             "raw_affiliation_strings": ["UC Berkeley"]},
        ]),
        inst_to_org,
    )
    assert rec["org_affiliations"] == {
        "meta": ["Facebook AI Research (FAIR)"],
        "berkeley": ["UC Berkeley"],
    }


def test_org_affiliation_evidence_empty_without_map():
    rec = _parse_work(
        _work([
            {"author": {"id": "https://openalex.org/A1", "display_name": "A"},
             "institutions": [{"id": "https://openalex.org/I4210114444"}],
             "raw_affiliation_strings": ["Facebook AI Research"]},
        ]),
        None,
    )
    assert rec["org_affiliations"] == {}


def test_s2_addressing_routes_include_mag_fallback():
    """Regression: a DOI unknown to S2 must not write a paper off as having no vector.

    "Attention Is All You Need" is the motivating case — OpenAlex records it with only an
    unusual DOI (10.65215/2q58a426, not in S2's index) plus mag:2626778328, which S2 does
    resolve and *does* have a SPECTER2 vector for. DOI-only lookup silently dropped it.
    """
    from pipeline.embedding.specter2_s2 import Specter2S2Backend

    backend = Specter2S2Backend()

    # arXiv is preferred, then DOI, then MAG — all offered so later passes can retry.
    assert backend._paper_ids_for("10.1/x", "2101.00001", "123") == [
        "ARXIV:2101.00001",
        "DOI:10.1/x",
        "MAG:123",
    ]
    # The Transformer case: no arXiv id, S2-unknown DOI, but a usable MAG id.
    assert backend._paper_ids_for("10.65215/2q58a426", None, "2626778328") == [
        "DOI:10.65215/2q58a426",
        "MAG:2626778328",
    ]
    # Nothing addressable stays empty (caller falls back per config).
    assert backend._paper_ids_for(None, None, None) == []
