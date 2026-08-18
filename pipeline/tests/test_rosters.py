from pathlib import Path

import polars as pl
import pytest

from pipeline.directory.rosters import load_rosters
from pipeline.stages.s14_rosters import _membership_rows


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_roster_exact_join_preserves_evidence_and_date_bounds(tmp_path):
    rosters = load_rosters(_write(tmp_path / "rosters.yaml", """
version: 1
organizations:
  - key: redwood
    display_name: Redwood Research
    organization_id: local:redwood-research
    members:
      - openalex_id: A123
        provenance: self_asserted
        valid_from: 2024-01-01
        valid_to: 2024-12-31
"""))
    corpus = pl.DataFrame([
        {"node_id": 0, "paper_id": "W0", "publication_date": "2023-12-01", "author_ids": ["A123"]},
        {"node_id": 1, "paper_id": "W1", "publication_date": "2024-06-01", "author_ids": ["A999", "A123"]},
        {"node_id": 2, "paper_id": "W2", "publication_date": "2025-01-01", "author_ids": ["A123"]},
        {"node_id": 3, "paper_id": "W3", "publication_date": "", "author_ids": ["A123"]},
    ])

    rows = _membership_rows(corpus, rosters)
    assert rows == [{
        "org_key": "redwood",
        "node_id": 1,
        "paper_id": "W1",
        "author_id": "A123",
        "provenance": "self_asserted",
        "valid_from": "2024-01-01",
        "valid_to": "2024-12-31",
    }]


def test_roster_rejects_non_openalex_author_id(tmp_path):
    path = _write(tmp_path / "rosters.yaml", """
version: 1
organizations:
  - key: bad
    display_name: Bad
    organization_id: local:bad
    members:
      - openalex_id: somebody
        provenance: registry
""")
    with pytest.raises(ValueError, match="OpenAlex author id"):
        load_rosters(path)


def test_one_author_may_have_reviewed_claims_at_multiple_orgs(tmp_path):
    path = _write(tmp_path / "rosters.yaml", """
version: 1
organizations:
  - key: one
    display_name: One
    organization_id: local:one
    members: [{openalex_id: A123, provenance: registry}]
  - key: two
    display_name: Two
    organization_id: local:two
    members: [{openalex_id: A123, provenance: self_asserted}]
""")
    corpus = pl.DataFrame([{
        "node_id": 0, "paper_id": "W0", "publication_date": "2024-01-01",
        "author_ids": ["A123"],
    }])
    rows = _membership_rows(corpus, load_rosters(path))
    assert {(row["org_key"], row["provenance"]) for row in rows} == {
        ("one", "registry"), ("two", "self_asserted"),
    }
