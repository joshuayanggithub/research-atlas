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

CORPUS_IN = INTERIM_DIR / "corpus.parquet"
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

    corpus = pl.read_parquet(CORPUS_IN)
    n = corpus.height
    log.info(f"corpus: {n} papers | backend={cfg.embedding.backend}")

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
        except Exception as e:  # noqa: BLE001
            log.warn(f"S2 fetch failed entirely ({e}); will fall back to local")

        cov = float(covered.mean()) if n else 0.0
        if cov < cfg.embedding.s2_min_coverage:
            log.warn(f"S2 coverage {cov:.1%} < {cfg.embedding.s2_min_coverage:.0%} "
                     f"threshold — embedding uncovered rows locally")
            missing_idx = np.where(~covered)[0]
            if len(missing_idx):
                local = _local_backend(cfg)
                sub = corpus[missing_idx.tolist()]
                sub_res = local.embed(sub)
                vectors[missing_idx] = sub_res.vectors
                covered[missing_idx] = True
                # Mixed backends: record both.
                backend_name = f"specter2_s2+scincl_local"
                model_used = f"{model_used or 'specter_v2'}+{local.model}"
        else:
            log.info(f"S2 coverage {cov:.1%} accepted")

    else:  # scincl_local
        local = _local_backend(cfg)
        res = local.embed(corpus)
        vectors, covered, model_used = res.vectors, res.covered, res.model
        backend_name = local.name

    # Any rows still uncovered (e.g. local also skipped) get a zero vector -> place at origin.
    n_uncovered = int((~covered).sum())
    if n_uncovered:
        log.warn(f"{n_uncovered} rows remain uncovered (zero vectors)")

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
