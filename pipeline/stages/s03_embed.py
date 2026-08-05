"""s03: Embed the corpus into a [N, dim] float32 matrix, row-aligned to node_id.

Dispatches to the configured backend. The key robustness feature is **auto-fallback**:
if the SPECTER2/S2 fetch covers fewer than ``embedding.s2_min_coverage`` of papers, we
embed the *uncovered* rows locally with SciNCL (or, if S2 is fully blocked, the whole
corpus). Vectors are L2-normalized here so all downstream cosine math is uniform.

Emits:
    data/interim/embeddings.npy   [N, dim] float32, L2-normalized
    data/interim/embed_meta.json  {backend, model, dim, coverage}
"""

from __future__ import annotations

import numpy as np
import polars as pl

from pipeline.common import log
from pipeline.common.io import write_json, write_npy
from pipeline.config import INTERIM_DIR, Config, ensure_dirs, load_config

from pipeline.config import CORPUS_ACTIVE, CORPUS_FULL

VEC_OUT = INTERIM_DIR / "embeddings.npy"
META_OUT = INTERIM_DIR / "embed_meta.json"


def _l2_normalize(v: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (v / norms).astype(np.float32)


def _local_backend(cfg: Config):
    from pipeline.embedding.scincl_local import ScinclLocalBackend
    return ScinclLocalBackend(model=cfg.embedding.scincl_model, dim=cfg.embedding.dim)


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s03_embed")

    corpus = pl.read_parquet(CORPUS_FULL)
    n = corpus.height
    log.info(f"corpus: {n} papers | backend={cfg.embedding.backend} "
             f"| on_uncovered={cfg.embedding.on_uncovered}")

    backend_name = cfg.embedding.backend
    model_used = ""
    covered = np.zeros(n, dtype=bool)
    vectors = np.zeros((n, cfg.embedding.dim), dtype=np.float32)

    if cfg.embedding.backend == "specter2_s2":
        from pipeline.embedding.specter2_s2 import Specter2S2Backend
        s2 = Specter2S2Backend(
            dim=cfg.embedding.dim,
            batch_size=cfg.embedding.s2_batch_size,
            api_key=cfg.secrets.s2_api_key,
        )
        try:
            res = s2.embed(corpus)
            vectors, covered, model_used = res.vectors, res.covered, res.model
            # Bake S2-resolved arXiv ids into the corpus (row-aligned) BEFORE any compaction,
            # so the frontend's first-figure preview can address the PDF without a runtime S2
            # call (S2 is CORS-blocked in the browser). Only fills blanks; never overwrites an
            # id the corpus already had.
            if res.arxiv_ids is not None and len(res.arxiv_ids) == n:
                existing = corpus["arxiv_id"].to_list()
                merged = [existing[i] or res.arxiv_ids[i] for i in range(n)]
                n_added = sum(1 for i in range(n) if not existing[i] and merged[i])
                corpus = corpus.with_columns(pl.Series("arxiv_id", merged))
                log.info(f"arXiv ids: +{n_added} resolved from S2 "
                         f"({sum(1 for m in merged if m)}/{n} total)")
        except Exception as e:  # noqa: BLE001
            log.warn(f"S2 fetch failed entirely ({e})")

        cov = float(covered.mean()) if n else 0.0

        if cfg.embedding.on_uncovered == "drop":
            # Keep only papers with a real SPECTER2 vector -> ONE clean embedding space,
            # no cross-model island. This drops rows, so we COMPACT the corpus and
            # re-assign dense node_ids; every downstream stage reads the compacted corpus.
            keep = np.where(covered)[0]
            log.info(f"S2 coverage {cov:.1%}; drop mode -> keeping {len(keep)}/{n} papers")
            vectors = vectors[keep]
            kept_corpus = corpus[keep.tolist()].with_columns(
                pl.arange(0, len(keep)).alias("node_id")
            )
            kept_corpus.write_parquet(CORPUS_ACTIVE)  # compacted active corpus
            covered = np.ones(len(keep), dtype=bool)
            backend_name = "specter2_s2"
        elif cov < cfg.embedding.s2_min_coverage:
            # fill_local + low coverage: SPECTER2/SciNCL are DIFFERENT spaces; don't mix at
            # scale (fake island). Re-embed the WHOLE corpus locally for one space.
            log.warn(f"S2 coverage {cov:.1%} < {cfg.embedding.s2_min_coverage:.0%} "
                     f"threshold — re-embedding entire corpus locally (consistent space)")
            local = _local_backend(cfg)
            res = local.embed(corpus)
            vectors, covered, model_used = res.vectors, res.covered, res.model
            backend_name = local.name
        else:
            # fill_local + acceptable coverage: fill the small remainder locally.
            log.info(f"S2 coverage {cov:.1%} accepted; filling remainder locally")
            missing_idx = np.where(~covered)[0]
            if len(missing_idx):
                local = _local_backend(cfg)
                sub = corpus[missing_idx.tolist()]
                sub_res = local.embed(sub)
                vectors[missing_idx] = sub_res.vectors
                covered[missing_idx] = True
                backend_name = "specter2_s2+scincl_local"
                model_used = f"{model_used or 'specter_v2'}+{local.model}"

    else:  # scincl_local
        local = _local_backend(cfg)
        res = local.embed(corpus)
        vectors, covered, model_used = res.vectors, res.covered, res.model
        backend_name = local.name

    n_uncovered = int((~covered).sum())
    if n_uncovered:
        log.warn(f"{n_uncovered} rows remain uncovered (zero vectors)")

    # In non-drop paths the active corpus == the full corpus. Write it so downstream
    # stages have a single canonical input regardless of mode.
    if not (cfg.embedding.backend == "specter2_s2"
            and cfg.embedding.on_uncovered == "drop"):
        corpus.write_parquet(CORPUS_ACTIVE)

    vectors = _l2_normalize(vectors)
    write_npy(vectors, VEC_OUT)
    meta = {
        "backend": backend_name,
        "model": model_used,
        "dim": cfg.embedding.dim,
        "coverage": float(covered.mean()) if n else 0.0,
        "n": n,
    }
    write_json(meta, META_OUT)
    log.info(f"vectors {vectors.shape} coverage={meta['coverage']:.1%} -> {VEC_OUT}")
    return str(VEC_OUT)


if __name__ == "__main__":
    run()
