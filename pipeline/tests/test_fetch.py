from pipeline.config import Config
from pipeline.stages.s01_fetch_openalex import build_field_filters, build_filter


def test_orgs_scope_filter_gates_on_institutions():
    cfg = Config()
    cfg.corpus.field_id = "fields/17"
    cfg.corpus.date_from = "2015-01-01"
    cfg.corpus.date_to = "2026-12-31"
    f = build_filter(cfg, ["I1", "I2"])
    assert "authorships.institutions.id:I1|I2" in f
    assert "primary_topic.field.id:fields/17" in f
    assert "from_publication_date:2015-01-01" in f


def test_field_scope_has_no_org_gate_and_two_streams():
    """Field scope must NOT gate on institutions, and must union a high-citation stream with
    a recency stream so recent work appears without the whole uncited tail."""
    cfg = Config()
    cfg.corpus.field_id = "fields/17"
    cfg.corpus.date_from = "2015-01-01"
    cfg.corpus.date_to = "2026-12-31"
    cfg.corpus.min_citations = 25
    cfg.corpus.recent_since = "2025-01-01"
    cfg.corpus.recent_min_citations = 2

    filters = build_field_filters(cfg)
    assert len(filters) == 2
    high, recent = filters
    # No affiliation predicate anywhere — any paper can appear.
    assert all("authorships.institutions.id" not in f for f in filters)
    # High-citation stream: full date range, main floor.
    assert "cited_by_count:>25" in high
    assert "from_publication_date:2015-01-01" in high
    # Recency stream: recent window, lighter floor.
    assert "from_publication_date:2025-01-01" in recent
    assert "cited_by_count:>2" in recent
    assert all("primary_topic.field.id:fields/17" in f for f in filters)
