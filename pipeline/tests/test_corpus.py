from pipeline.stages.s02_build_corpus import _arxiv_from_ids, _clean_doi


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
