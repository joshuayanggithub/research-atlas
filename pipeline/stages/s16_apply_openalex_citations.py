"""s16: Materialize fast OpenAlex citation data for an arXiv-spine corpus.

``s15_enrich_openalex`` stores exact-match OpenAlex citation fields as provenance.  This
stage makes those fields useful to the static application without another network request:

* a matched row's ``cited_by_count`` becomes OpenAlex's current total;
* an outgoing reference becomes a browser edge only when its OpenAlex target is another
  exact-match row in this corpus; and
* the original ``openalex_*`` fields remain intact for audit and later provider comparison.

Semantic Scholar's bulk stage is intentionally separate.  When it is run later it preserves
these columns, can replace counts for its matched rows, and leaves OpenAlex as the fallback
for the remaining rows.  No provider counts are ever summed.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from pipeline.common import log
from pipeline.common.io import write_json
from pipeline.config import CORPUS_ACTIVE, CORPUS_FULL, INTERIM_DIR, Config, ensure_dirs, load_config

CORPUS_IN = CORPUS_FULL
STATS_OUT = INTERIM_DIR / "openalex_citation_stats.parquet"
META_OUT = INTERIM_DIR / "openalex_citation_meta.json"


def _materialize(corpus: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    required = {"node_id", "paper_id", "openalex_id", "openalex_cited_by_count", "openalex_referenced_works"}
    missing = required - set(corpus.columns)
    if missing:
        raise ValueError(
            "OpenAlex citation materialization requires s15 enrichment columns; missing "
            + ", ".join(sorted(missing))
        )

    # A provider work id is one-to-one in a clean crosswalk.  If a historical duplicate is
    # present, keep the first deterministic node and record the collision in metadata rather
    # than emitting ambiguous arrows.
    openalex_to_paper: dict[str, str] = {}
    duplicate_ids: set[str] = set()
    for openalex_id, paper_id in zip(corpus["openalex_id"].to_list(), corpus["paper_id"].to_list()):
        if not openalex_id:
            continue
        value = str(openalex_id)
        if value in openalex_to_paper:
            duplicate_ids.add(value)
            continue
        openalex_to_paper[value] = str(paper_id)

    # A completed S2 bulk scan has already made its own provider-precedence decision.  Do not
    # let a later fast refresh silently replace it; OpenAlex still refreshes its sidecar and
    # continues to cover rows S2 could not crosswalk.
    s2_is_primary = (
        "s2_citation_available" in corpus.columns
        and bool(corpus["s2_citation_available"].any())
    )
    available: list[bool] = []
    canonical_counts: list[int] = []
    canonical_refs: list[list[str]] = []
    reference_counts: list[int] = []
    prior_counts = corpus["cited_by_count"].to_list()
    prior_references = corpus["referenced_works"].to_list()
    for openalex_id, count, refs, prior, prior_refs in zip(
        corpus["openalex_id"].to_list(),
        corpus["openalex_cited_by_count"].to_list(),
        corpus["openalex_referenced_works"].to_list(),
        prior_counts,
        prior_references,
    ):
        matched = openalex_id is not None
        available.append(matched)
        canonical_counts.append(int(prior or 0) if s2_is_primary else (
            int(count or 0) if matched else int(prior or 0)
        ))
        # ``dict`` preserves citation ordering while removing duplicate provider edges.
        internal = list(dict.fromkeys(
            target for target in (openalex_to_paper.get(str(ref)) for ref in (refs or []))
            if target is not None
        )) if matched else []
        canonical_refs.append((prior_refs or []) if s2_is_primary else internal)
        reference_counts.append(len(internal))

    enriched = corpus.with_columns([
        pl.Series("openalex_citation_available", available, dtype=pl.Boolean),
        pl.Series("openalex_reference_count", reference_counts, dtype=pl.Int32),
        pl.Series("cited_by_count", canonical_counts, dtype=pl.Int32),
        pl.Series("referenced_works", canonical_refs, dtype=pl.List(pl.String)),
    ])
    meta = {
        "provider": "OpenAlex",
        "corpus_rows": corpus.height,
        "openalex_match_count": sum(available),
        "openalex_match_coverage": sum(available) / corpus.height if corpus.height else 0.0,
        "internal_edge_count": sum(reference_counts),
        "canonical_values_applied": not s2_is_primary,
        "duplicate_openalex_id_count": len(duplicate_ids),
        "citation_count_contract": "OpenAlex cited_by_count for exact OpenAlex matches; missing rows remain unavailable",
        "graph_contract": "OpenAlex referenced_works with both endpoints exact-matched in the active corpus",
        "materialized_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    return enriched, meta


def _update_active(enriched_full: pl.DataFrame) -> None:
    """Propagate only citation columns when embeddings already made an active subset."""
    if not CORPUS_ACTIVE.exists() or CORPUS_ACTIVE == CORPUS_IN:
        return
    active = pl.read_parquet(CORPUS_ACTIVE)
    active_ids = active["paper_id"].to_list()
    full_ids = set(enriched_full["paper_id"].to_list())
    if not set(active_ids) <= full_ids:
        raise RuntimeError("active corpus is not a subset of the enriched full corpus")
    citation_columns = [
        "paper_id", "cited_by_count", "referenced_works",
        "openalex_citation_available", "openalex_reference_count",
    ]
    replacements = enriched_full.select(citation_columns)
    active = active.drop([c for c in citation_columns[1:] if c in active.columns])
    active.join(replacements, on="paper_id", how="left").sort("node_id").write_parquet(CORPUS_ACTIVE)


def run(cfg: Config | None = None) -> str:
    _ = cfg or load_config()
    ensure_dirs()
    log.stage("s16_apply_openalex_citations")
    if not CORPUS_IN.exists():
        raise FileNotFoundError(f"corpus missing: {CORPUS_IN}; run s02 and s15 first")

    corpus = pl.read_parquet(CORPUS_IN)
    enriched, meta = _materialize(corpus)
    # The stage is local and atomic at the corpus level: only fully mapped counts/edges commit.
    enriched.write_parquet(CORPUS_IN)
    _update_active(enriched)
    enriched.select([
        "node_id", "openalex_id", "openalex_cited_by_count",
        "openalex_citation_available", "openalex_reference_count",
    ]).write_parquet(STATS_OUT)
    write_json(meta, META_OUT)
    log.info(
        f"OpenAlex citations: {meta['openalex_match_count']:,}/{corpus.height:,} matched | "
        f"{meta['internal_edge_count']:,} internal edges"
    )
    log.info(f"wrote -> {META_OUT}")
    return str(META_OUT)


if __name__ == "__main__":
    run()
