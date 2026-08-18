"""s08: Build the fused related-works graph.

1. Build an hnswlib cosine index over the L2-normalized embeddings.
2. Query semantic candidates from text embeddings.
3. Add direct citations, bibliographic-coupling, and co-citation candidates.
4. Rank the union by fused score = alpha*cosine + (1-alpha)*citation_score.
4. Keep top knn_k per node.

Emits:
    data/interim/neighbors.npz  (ids [N,k] int32, scores [N,k] float32)
"""

from __future__ import annotations

import os

import numpy as np

from pipeline.common import log
from pipeline.common.io import read_npy
from pipeline.common.fused_similarity import (
    build_reference_sets,
    fuse_candidate_neighbors,
)
from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR, Config, ensure_dirs, load_config

VEC_IN = INTERIM_DIR / "embeddings.npy"
CORPUS_IN = CORPUS_ACTIVE
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
    cand = min(n - 1, k * cfg.fused.text_candidate_multiplier)
    log.info(f"hnswlib cosine index: n={n} dim={dim} k={k} candidates={cand}")

    index = hnswlib.Index(space="cosine", dim=dim)
    index.init_index(
        max_elements=n,
        ef_construction=200,
        M=16,
        random_seed=cfg.cluster.random_state,
    )
    # hnswlib's parallel construction is not reproducible even with a fixed seed, so we use
    # one thread for stable artifacts — but single-thread is impractically slow at scale
    # (~30+ min at 400k). Above fused.single_thread_max_n, use all cores and accept the minor
    # non-determinism (an offline stage; the approximate kNN is robust to it).
    # Pass an explicit positive count. hnswlib 0.8 can segfault in parallel add_items when
    # num_threads=0 is used as an "all cores" sentinel (observed at 271k x 768 on Linux).
    # Half the logical CPUs approximates physical cores and avoids oversubscribing SMT.
    parallel_threads = max(1, min(8, (os.cpu_count() or 1) // 2))
    threads = 1 if n <= cfg.fused.single_thread_max_n else parallel_threads
    if threads != 1:
        log.warn(f"n={n} > single_thread_max_n={cfg.fused.single_thread_max_n}: building "
                 f"HNSW with {threads} workers (faster, slightly non-deterministic)")
    index.set_num_threads(threads)
    index.add_items(vectors, np.arange(n), num_threads=threads)
    index.set_ef(max(cand + 10, 50))

    with log.timer("knn query"):
        labels, distances = index.knn_query(
            vectors,
            k=cand + 1,
            num_threads=threads,
        )  # +1 for self
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
        citation_limit = k * cfg.fused.citation_candidate_multiplier
        log.info(
            "ranking union of text, direct-citation, coupling, and co-citation candidates"
        )
        with log.timer("fuse"):
            fused_ids, fused_scores = fuse_candidate_neighbors(
                vectors,
                text_ids,
                out_refs,
                in_citers,
                cfg.fused.alpha,
                k,
                citation_limit,
                cfg.fused.hub_degree_limit,
            )
    else:
        log.warn("no edges.npz — using pure text neighbors (run s09 first for fused)")
        fused_ids, fused_scores = text_ids[:, :k], text_scores[:, :k]

    np.savez(OUT, ids=fused_ids, scores=fused_scores)
    log.info(f"neighbors {fused_ids.shape} -> {OUT}")
    return str(OUT)


if __name__ == "__main__":
    run()
