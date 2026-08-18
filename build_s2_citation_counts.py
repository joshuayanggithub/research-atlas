"""True global citation counts for every corpus paper, from the S2AG bulk snapshot.

D31 could only apply a *floor*: a paper cites-count was raised to the number of citers we can
enumerate inside Research Atlas. That fixed the self-contradiction (1 shown, 19 listed) but left
337,426 papers still understated, because most citers of a paper are outside a 1M-paper corpus.

The real number is already on disk. `data/s2ag/cited_by.parquet` is the bulk citations dataset
inverted per target: one row per cited paper, `dst` plus `neighbors`, the list of every S2 paper
that cites it — the whole graph, not just our slice. So the global count is simply the length of
that list. No API, no rate limit, no snapshot skew against the reference lists we already use.

Cost is one streaming pass over 109,580,699 rows / 17.7 GB. Only the list LENGTHS are needed, so
batches are reduced immediately and nothing accumulates except one int per corpus paper.

    uv run python build_s2_citation_counts.py

Output: data/interim/s2_citation_counts.parquet — node_id, s2_cited_by_count.
"""
from __future__ import annotations

import time

import numpy as np
import polars as pl
import pyarrow.parquet as pq

from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR

OUT = INTERIM_DIR / "s2_citation_counts.parquet"
CITED_BY = "data/s2ag/cited_by.parquet"
CROSSWALK = "data/s2ag/crosswalk.parquet"
BATCH_ROWS = 200_000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    corpus = pl.read_parquet(CORPUS_ACTIVE, columns=["node_id", "arxiv_id"]).with_columns(
        pl.col("arxiv_id").str.replace(r"v\d+$", "").alias("aid")
    )
    cross = pl.read_parquet(CROSSWALK)
    matched = corpus.join(cross, left_on="aid", right_on="arxiv_id", how="inner")
    log(f"corpus {corpus.height:,} | matched to an S2 corpusid {matched.height:,}")

    # A few arXiv ids map to more than one corpusid, so this is deliberately a multimap; the
    # per-node aggregation at the end takes the max, matching build_reference_availability.
    want = np.asarray(matched["corpusid"].to_list(), dtype=np.int64)
    order = np.argsort(want)
    want_sorted = want[order]
    node_of = np.asarray(matched["node_id"].to_list(), dtype=np.int64)[order]
    counts = np.zeros(want_sorted.shape[0], dtype=np.int64)

    pf = pq.ParquetFile(CITED_BY)
    total = pf.metadata.num_rows
    seen = 0
    hits = 0
    for batch in pf.iter_batches(batch_size=BATCH_ROWS, columns=["dst", "neighbors"]):
        dst = batch.column("dst").to_numpy(zero_copy_only=False)
        # List lengths straight off the Arrow offsets — the citing ids themselves are never
        # materialised, which is what keeps a 17.7 GB pass affordable.
        lens = np.asarray(batch.column("neighbors").value_lengths(), dtype=np.int64)

        idx = np.searchsorted(want_sorted, dst)
        idx_clipped = np.clip(idx, 0, want_sorted.shape[0] - 1)
        ok = want_sorted[idx_clipped] == dst
        if ok.any():
            # np.maximum.at, not plain assignment: one corpusid can appear in several batches
            # only if the dataset is not unique on dst, and max is the safe reduction either way.
            np.maximum.at(counts, idx_clipped[ok], lens[ok])
            hits += int(ok.sum())
        seen += len(dst)
        if seen % (BATCH_ROWS * 50) == 0:
            log(f"  {seen:,}/{total:,} rows ({seen / total * 100:.1f}%) · {hits:,} corpus papers hit")

    log(f"scan complete: {hits:,} of {want_sorted.shape[0]:,} corpusids found in cited_by")

    out = (
        pl.DataFrame({"node_id": node_of, "s2_cited_by_count": counts})
        .group_by("node_id")
        .agg(pl.col("s2_cited_by_count").max())
        .sort("node_id")
    )
    nonzero = int((out["s2_cited_by_count"] > 0).sum())
    log(f"papers with >=1 S2 citation: {nonzero:,} / {out.height:,}")
    log(f"total citations attributed: {int(out['s2_cited_by_count'].sum()):,}")
    out.write_parquet(OUT)
    log(f"wrote {OUT} in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
