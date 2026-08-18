import gzip
import json

import numpy as np
import polars as pl
import pytest

from pipeline.stages import s16_enrich_s2_citations as citations


def _corpus() -> pl.DataFrame:
    return pl.DataFrame([
        {
            "node_id": 0,
            "paper_id": "arxiv:2501.00001",
            "arxiv_id": "2501.00001",
            "cited_by_count": 0,
            "referenced_works": [],
        },
        {
            "node_id": 1,
            "paper_id": "arxiv:2501.00002",
            "arxiv_id": "2501.00002",
            "cited_by_count": 0,
            "referenced_works": [],
        },
        {
            "node_id": 2,
            "paper_id": "arxiv:2501.00003",
            "arxiv_id": "2501.00003",
            "cited_by_count": 0,
            "referenced_works": [],
        },
    ])


def test_stream_keeps_external_counts_but_only_internal_graph_edges(tmp_path):
    path = tmp_path / "citations.jsonl.gz"
    rows = [
        # External citer -> corpus target: contributes to paper 0's total, not graph edge.
        {"citingcorpusid": 999, "citedcorpusid": 101},
        # Corpus 1 -> corpus 0: contributes counts and an internal directed edge.
        {"citingcorpusid": 102, "citedcorpusid": 101},
        # Corpus 1 -> external: contributes to paper 1's outgoing total, not graph edge.
        {"citingcorpusid": 102, "citedcorpusid": 998},
        # Duplicate internal edge must not create a duplicate browser arrow/reference.
        {"citingcorpusid": 102, "citedcorpusid": 101},
    ]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    src, dst, refs, incoming, outgoing, scanned = citations._scan_citations(
        _corpus(),
        {"2501.00001": "101", "2501.00002": "102"},
        [path],
    )

    assert scanned == 4
    assert incoming.tolist() == [3, 0, 0]
    assert outgoing.tolist() == [0, 3, 0]
    assert src.tolist() == [1]
    assert dst.tolist() == [0]
    assert refs == [[], ["arxiv:2501.00001"], []]


def test_materialize_marks_unmatched_rows_unavailable_without_calling_them_zero():
    result, coverage = citations._materialize(
        _corpus(),
        {"2501.00001": "101", "2501.00002": "102"},
        incoming=np.asarray([9, 0, 0], dtype=np.int32),
        outgoing=np.asarray([1, 2, 0], dtype=np.int32),
        refs=[[], ["arxiv:2501.00001"], []],
        release_id="2026-08-11",
    )

    assert coverage == 2 / 3
    rows = result.sort("node_id").to_dicts()
    assert rows[0]["cited_by_count"] == 9
    assert rows[1]["cited_by_count"] == 0
    assert rows[1]["s2_citation_available"] is True
    assert rows[2]["s2_citation_available"] is False
    assert rows[2]["s2_citation_snapshot"] == "2026-08-11"


def test_paper_id_crosswalk_selects_only_requested_s2_hashes(tmp_path):
    path = tmp_path / "paper-ids.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in [
            {"sha": "unrelated", "corpusid": 1, "primary": True},
            {"sha": "wanted-a", "corpusid": 101, "primary": True},
            {"sha": "wanted-b", "corpusid": 102, "primary": False},
        ]:
            handle.write(json.dumps(row) + "\n")

    assert citations._scan_paper_ids({"wanted-a", "wanted-b"}, [path]) == {
        "wanted-a": "101",
        "wanted-b": "102",
    }


def test_stream_refreshes_a_failed_presigned_citation_url(tmp_path, monkeypatch):
    attempts = []

    def fake_download(url, destination, index, *, timeout, label):
        attempts.append(url)
        if len(attempts) == 1:
            response = citations.requests.Response()
            response.status_code = 400
            raise citations.requests.HTTPError(response=response)
        path = tmp_path / "refreshed.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write('{"citingcorpusid":102,"citedcorpusid":101}\n')
        return path

    class FakeClient:
        def dataset_download_urls(self, release_id, name):
            assert (release_id, name) == ("2026-08-11", "citations")
            return ["fresh-url"]

    monkeypatch.setattr(citations, "_download_shard", fake_download)
    src, dst, _refs, incoming, _outgoing, scanned = citations._stream_citations(
        _corpus(),
        {"2501.00001": "101", "2501.00002": "102"},
        ["stale-url"],
        tmp_path,
        timeout=1,
        client=FakeClient(),
        release_id="2026-08-11",
    )

    assert attempts == ["stale-url", "fresh-url"]
    assert (src.tolist(), dst.tolist(), incoming.tolist(), scanned) == ([1], [0], [1, 0, 0], 1)


def test_update_active_propagates_s2_columns_to_the_embedded_subset(tmp_path, monkeypatch):
    """The bundle is emitted from CORPUS_ACTIVE, so a scan that only writes CORPUS_FULL
    silently ships the previous provider's counts (observed: OpenAlex's stale 1 citation
    for a paper S2 scored at 104)."""
    full_path = tmp_path / "corpus.parquet"
    active_path = tmp_path / "corpus_active.parquet"
    monkeypatch.setattr(citations, "CORPUS_FULL", full_path)
    monkeypatch.setattr(citations, "CORPUS_ACTIVE", active_path)

    full = pl.DataFrame({
        "paper_id": ["p1", "p2", "p3"],
        "node_id": [0, 1, 2],
        "cited_by_count": [104, 7, 0],
        "referenced_works": [["p2"], [], []],
        "s2_corpus_id": ["11", "12", None],
        "s2_citation_count": [104, 7, 0],
        "s2_reference_count": [1, 0, 0],
        "s2_citation_available": [True, True, False],
        "s2_citation_snapshot": ["2026-08-11"] * 3,
    })
    full.write_parquet(full_path)
    # The active subset is what s03_embed compacted: fewer rows, stale OpenAlex counts.
    pl.DataFrame({
        "paper_id": ["p3", "p1"],
        "node_id": [1, 0],
        "cited_by_count": [0, 1],
        "referenced_works": [[], []],
    }).write_parquet(active_path)

    assert citations.update_active() is True

    active = pl.read_parquet(active_path)
    assert active["node_id"].to_list() == [0, 1]          # re-sorted by node_id
    rows = {r["paper_id"]: r for r in active.to_dicts()}
    assert rows["p1"]["cited_by_count"] == 104            # stale 1 replaced
    assert rows["p1"]["referenced_works"] == ["p2"]
    assert rows["p1"]["s2_citation_available"] is True
    assert rows["p3"]["s2_citation_available"] is False   # unmatched stays unavailable
    assert "s2_citation_snapshot" in active.columns       # provenance carried over


def test_update_active_rejects_an_active_corpus_that_is_not_a_subset(tmp_path, monkeypatch):
    full_path = tmp_path / "corpus.parquet"
    active_path = tmp_path / "corpus_active.parquet"
    monkeypatch.setattr(citations, "CORPUS_FULL", full_path)
    monkeypatch.setattr(citations, "CORPUS_ACTIVE", active_path)

    pl.DataFrame({
        "paper_id": ["p1"], "node_id": [0], "cited_by_count": [3],
        "referenced_works": [[]], "s2_corpus_id": ["11"], "s2_citation_count": [3],
        "s2_reference_count": [0], "s2_citation_available": [True],
        "s2_citation_snapshot": ["2026-08-11"],
    }).write_parquet(full_path)
    pl.DataFrame({
        "paper_id": ["ghost"], "node_id": [0], "cited_by_count": [0], "referenced_works": [[]],
    }).write_parquet(active_path)

    with pytest.raises(RuntimeError, match="not a subset"):
        citations.update_active()
