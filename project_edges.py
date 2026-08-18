"""Phase 4: project the global citation graph onto the active corpus -> data/interim/edges.npz.

This replaces `s09_edges` for arXiv-spine builds. s09 could only resolve citations that were
already inside the corpus rows (`referenced_works`), which for the 2025-2026 corpus meant
OpenAlex data — and OpenAlex has *zero* references for arXiv-only preprints after the MAG
shutdown (see docs/CITATION_GRAPH_PLAN.md §1). This reads the 5.09-billion-edge S2 graph
instead, so an edge exists whenever S2 saw the citation, regardless of provider coverage.

The projection is a filtered join, not a scan-per-paper:
    corpus arxiv_id --crosswalk--> corpusid --filter refs.parquet--> explode --> node_id pairs

Deduplication is required: the bulk S2AG data contains repeated (citing, cited) rows — a
sampled paper had 69 edge rows resolving to only 35 distinct targets — so without it the
browser would draw duplicate arrows.

    uv run python project_edges.py
"""
from __future__ import annotations

import time

import numpy as np
import polars as pl

from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR

S2AG = "data/s2ag"
OUT = INTERIM_DIR / "edges.npz"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    corpus = pl.read_parquet(CORPUS_ACTIVE, columns=["node_id", "arxiv_id"])
    log(f"corpus: {corpus.height:,} papers")

    cross = pl.read_parquet(f"{S2AG}/crosswalk.parquet")
    # arxiv ids are bare (no version suffix) on both sides; guard anyway.
    corpus = corpus.with_columns(
        pl.col("arxiv_id").str.replace(r"v\d+$", "").alias("arxiv_id")
    )
    m = corpus.join(cross, on="arxiv_id", how="inner").select(["node_id", "corpusid"]).unique()
    log(f"resolved to corpusid: {m.height:,} / {corpus.height:,} "
        f"({m.height/corpus.height*100:.1f}%)")

    cids = m["corpusid"].to_list()
    cid_set = set(cids)
    log(f"projecting refs.parquet against {len(cid_set):,} corpus corpusids…")

    # Filter by src FIRST so only corpus rows are exploded (97M keys -> ~270k), then keep
    # only neighbours that are also in the corpus.
    edges = (
        pl.scan_parquet(f"{S2AG}/refs.parquet")
        .filter(pl.col("src").is_in(cids))
        .select(["src", "neighbors"])
        .explode("neighbors")
        .filter(pl.col("neighbors").is_in(cids))
        .rename({"neighbors": "dst_cid", "src": "src_cid"})
        .unique()                                  # dedupe repeated (citing, cited) rows
        .collect()
    )
    log(f"in-corpus edges after dedupe: {edges.height:,}")

    # corpusid -> node_id
    lut = m.rename({"corpusid": "src_cid", "node_id": "src"})
    edges = edges.join(lut, on="src_cid", how="inner")
    lut2 = m.rename({"corpusid": "dst_cid", "node_id": "dst"})
    edges = edges.join(lut2, on="dst_cid", how="inner")
    edges = edges.filter(pl.col("src") != pl.col("dst")).select(["src", "dst"]).unique()

    src = edges["src"].to_numpy().astype(np.int32)
    dst = edges["dst"].to_numpy().astype(np.int32)
    np.savez(OUT, src=src, dst=dst)

    n_out = len(np.unique(src))
    n_in = len(np.unique(dst))
    log(f"\nPHASE 4 COMPLETE in {(time.time()-t0)/60:.1f} min")
    log(f"  edges written : {len(src):,}  -> {OUT}")
    log(f"  papers citing within corpus : {n_out:,}")
    log(f"  papers cited within corpus  : {n_in:,}")
    log(f"  (s09/OpenAlex+S2 previously produced 597,120)")


if __name__ == "__main__":
    main()
