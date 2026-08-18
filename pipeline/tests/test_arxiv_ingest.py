from datetime import date

from pipeline.config import Config
from pipeline.stages.s01_fetch_arxiv import _parse_page
from pipeline.stages.s02_build_arxiv_corpus import _normalize, _v1_date


def test_uses_first_version_date_not_identifier_month():
    record = {
        "id": "2502.00001",
        "versions": [{"version": "v1", "created": "Fri, 31 Jan 2025 23:59:00 GMT"}],
    }
    assert _v1_date(record) == date(2025, 1, 31)


def test_normalize_includes_cross_listed_cs_category():
    cfg = Config()
    cfg.corpus.date_from = "2025-01-01"
    cfg.corpus.date_to = "2026-12-31"
    record = {
        "id": "2501.00001",
        "title": "  A   useful paper  ",
        "abstract": " An abstract. ",
        "authors_parsed": [["Lovelace", "Ada", ""]],
        "categories": "math.OC cs.LG",
        "versions": [{"version": "v1", "created": "Wed, 1 Jan 2025 00:00:00 GMT"}],
    }
    row = _normalize(record, cfg)
    assert row is not None
    assert row["publication_date"] == "2025-01-01"
    assert row["author_names"] == ["Ada Lovelace"]
    assert row["arxiv_categories"] == ["math.OC", "cs.LG"]
    assert row["topic_name"] == "cs.LG"
    assert row["field_name"] == "Computer Science"


def test_oai_page_parses_upsert_and_resumption_token():
    xml = b"""<?xml version="1.0"?>
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <responseDate>2026-08-14T00:00:00Z</responseDate>
      <ListRecords><record><header><identifier>oai:arXiv.org:2608.00001</identifier>
      <datestamp>2026-08-13</datestamp></header><metadata>
      <arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/">
        <id>2608.00001</id><submitter>Ada</submitter>
        <version version="v1"><date>Wed, 12 Aug 2026 00:00:00 GMT</date></version>
        <title>Paper</title><authors>Lovelace, Ada</authors><categories>cs.AI</categories>
        <abstract>Abstract</abstract>
      </arXivRaw></metadata></record>
      <resumptionToken>next-page</resumptionToken></ListRecords>
    </OAI-PMH>"""
    rows, token, response_date = _parse_page(xml)
    assert token == "next-page"
    assert response_date == "2026-08-14T00:00:00Z"
    assert rows[0]["id"] == "2608.00001"
    assert rows[0]["versions"][0]["created"].startswith("Wed, 12 Aug")
