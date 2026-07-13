"""s08: Build the related-works kNN graph on FUSED (text + citation) similarity.

1. Build an hnswlib cosine index over the L2-normalized embeddings.
2. Query top (knn_k * 3) text neighbors per node (candidate pool).
3. Re-rank candidates by fused score = alpha*cosine + (1-alpha)*citation_score, where
   citation_score = mean(bibliographic-coupling, co-citation) over the intra-corpus graph.
4. Keep top knn_k per node.

Emits:
    data/interim/neighbors.npz  (ids [N,k] int32, scores [N,k] float32)
"""

from __future__ import annotations

import numpy as np
import polars as pl

from pipeline.common import log
from pipeline.common.io import read_npy
from pipeline.common.fused_similarity import build_reference_sets, fuse_neighbors
from pipeline.config import INTERIM_DIR, Config, ensure_dirs, load_config

VEC_IN = INTERIM_DIR / "embeddings.npy"
CORPUS_IN = INTERIM_DIR / "corpus.parquet"
EDGES_IN = INTERIM_DIR / "edges.npz"  # produced by s09; optional (fused falls back to text)
OUT = INTERIM_DIR / "neighbors.npz"


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s08_neighbors")

    import hnswlib

    vectors = read_npy(VEC_IN)
    n, dim = vectors.shape
    k = cfg.fused.knn_k
    cand = min(n - 1, k * 3)
    log.info(f"hnswlib cosine index: n={n} dim={dim} k={k} candidates={cand}")

    index = hnswlib.Index(space="cosine", dim=dim)
    index.init_index(max_elements=n, ef_construction=200, M=16)
    index.add_items(vectors, np.arange(n))
    index.set_ef(max(cand + 10, 50))

    with log.timer("knn query"):
        labels, distances = index.knn_query(vectors, k=cand + 1)  # +1 for self
    # cosine similarity = 1 - cosine distance; drop self (first col is usually self).
    text_ids = np.empty((n, cand), dtype=np.int32)
    text_scores = np.empty((n, cand), dtype=np.float32)
    for i in range(n):
        row_ids = labels[i]
        row_sim = 1.0 - distances[i]
        # remove self
        mask = row_ids != i
        ids_i = row_ids[mask][:cand]
        sim_i = row_sim[mask][:cand]
        pad = cand - len(ids_i)
        if pad > 0:
            ids_i = np.concatenate([ids_i, np.full(pad, -1, dtype=np.int32)])
            sim_i = np.concatenate([sim_i, np.zeros(pad, dtype=np.float32)])
        text_ids[i] = ids_i
        text_scores[i] = sim_i

    # Fuse with citation score if the intra-corpus edge list exists.
    if EDGES_IN.exists():
        e = np.load(EDGES_IN)
        out_refs, in_citers = build_reference_sets(e["src"], e["dst"], n)
        log.info("fusing text kNN with citation coupling")
        with log.timer("fuse"):
            fused_ids, fused_scores = fuse_neighbors(
                text_ids, text_scores, out_refs, in_citers, cfg.fused.alpha, k
            )
    else:
        log.warn("no edges.npz — using pure text neighbors (run s09 first for fused)")
        fused_ids, fused_scores = text_ids[:, :k], text_scores[:, :k]

    np.savez(OUT, ids=fused_ids, scores=fused_scores)
    log.info(f"neighbors {fused_ids.shape} -> {OUT}")
    return str(OUT)


if __name__ == "__main__":
    run()
