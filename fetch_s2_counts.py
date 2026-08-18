"""Fetch S2's authoritative citation/reference totals for the active corpus.

The bulk citations scan produced a correct EDGE GRAPH but inflated TOTALS (its per-shard
counters never deduplicate a repeated citing->cited pair; measured ~1.68x over S2's own
figure). This refetches the totals from the paper/batch endpoint, 500 papers per request,
which takes minutes rather than re-running the ~19h shard scan.

    uv run python fetch_s2_counts.py            # fetch only (safe while other stages run)
    uv run python fetch_s2_counts.py --apply    # fetch, then rewrite both corpora
"""
from __future__ import annotations

import sys

import polars as pl

from pipeline.common import log
from pipeline.config import CORPUS_ACTIVE, load_config
from pipeline.stages.s16_enrich_s2_citations import (
    COUNTS_OUT, S2Client, apply_authoritative_counts, fetch_authoritative_counts,
)


def main() -> None:
    cfg = load_config()
    settings = cfg.semantic_scholar_citations
    corpus = pl.read_parquet(CORPUS_ACTIVE)
    client = S2Client(
        cfg.secrets.s2_api_key or "",
        min_interval=settings.min_request_interval,
        max_retries=settings.max_retries,
    )
    try:
        counts = fetch_authoritative_counts(
            corpus, client, batch_size=settings.resolve_batch_size,
        )
    finally:
        client.close()

    log.info(f"resolved authoritative counts for {len(counts):,}/{corpus.height:,} papers")
    pl.DataFrame({
        "arxiv_id": list(counts),
        "s2_citation_count": [v[0] for v in counts.values()],
        "s2_reference_count": [v[1] for v in counts.values()],
    }).write_parquet(COUNTS_OUT)
    log.info(f"wrote -> {COUNTS_OUT}")

    if "--apply" in sys.argv:
        apply_authoritative_counts(counts)


if __name__ == "__main__":
    main()
