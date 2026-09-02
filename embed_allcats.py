"""Embed the all-category arXiv expansion (2,134,371 papers) into the LIVE vector space.

Must match embed_meta.json exactly — specter2_local, allenai/specter2_base +
allenai/specter2:proximity, 768-d, fp32 — or the vectors cannot share a space with the existing
1,000,490 and the merge would be meaningless. Precision stays fp32 for that reason: fp16 would
be faster but puts these vectors on a subtly different footing from every one already on the map.

Unlike embed_backfill.py this runs on the GPU. That script was written when the 3090's kernel
module was older than its userspace driver and CUDA init failed; that is fixed (torch 2.13+cu130
reports the device with 24.2 GB free), and ~30 papers/sec on CPU would be ~20 hours here.

Checkpoints every 2,048 papers to its OWN paths, so a resume can never be confused with the live
corpus's partial file, and an interrupted run continues rather than restarting.

    uv run python embed_allcats.py [--limit N]
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import polars as pl

from pipeline.config import INTERIM_DIR, load_config
from pipeline.embedding.specter2_local import Specter2LocalBackend

CORPUS_IN = INTERIM_DIR / "corpus_allcats_new.parquet"
OUT = INTERIM_DIR / "embeddings_allcats_new.npy"
META = INTERIM_DIR / "embed_meta.json"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    batch_override = None
    if "--batch" in sys.argv:
        batch_override = int(sys.argv[sys.argv.index("--batch") + 1])
    if OUT.exists() and limit is None:
        log(f"{OUT.name} already exists — nothing to do")
        return

    corpus = pl.read_parquet(CORPUS_IN)
    if limit:
        corpus = corpus.head(limit)
        log(f"SMOKE TEST on {corpus.height:,} papers (not written to {OUT.name})")
    log(f"corpus: {corpus.height:,} papers")

    live = json.loads(META.read_text())
    log(f"live space: {live['model']} dim={live['dim']} (n={live['n']:,})")

    cfg = load_config()
    suffix = ".smoke" if limit else ""
    embedder = Specter2LocalBackend(
        model=cfg.embedding.specter2_model,
        adapter=cfg.embedding.specter2_adapter,
        dim=cfg.embedding.dim,
        batch_size=batch_override or cfg.embedding.local_batch_size,
        device="cuda",
        precision="fp32",
        checkpoint_every=cfg.embedding.checkpoint_every,
        partial_path=INTERIM_DIR / f"allcats{suffix}.specter2.partial.npy",
        checkpoint_path=INTERIM_DIR / f"allcats{suffix}.specter2.checkpoint.json",
    )
    result = embedder.embed(corpus)
    vectors = np.asarray(result.vectors, dtype=np.float32)

    # L2-normalise exactly as s03 does. The backend returns RAW vectors (norms ~21-22); calling
    # it directly skips that step, which would put this half of the corpus on a different scale
    # from the live half and corrupt every cosine distance in the merged map.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = (vectors / norms).astype(np.float32)

    if vectors.shape[0] != corpus.height:
        raise SystemExit(f"row mismatch: {vectors.shape[0]} vectors vs {corpus.height} papers")
    if vectors.shape[1] != live["dim"]:
        raise SystemExit(f"dim mismatch: {vectors.shape[1]} vs live {live['dim']}")
    norms = np.linalg.norm(vectors, axis=1)
    log(f"vectors {vectors.shape}  norm {norms.min():.4f}..{norms.max():.4f} (expect ~1.0)")
    if not np.isfinite(vectors).all():
        raise SystemExit("non-finite vectors")

    rate = corpus.height / max(time.time() - t0, 1e-6)
    log(f"throughput {rate:.0f} papers/sec")
    if limit:
        log(f"full run estimate: {2_134_371 / rate / 3600:.1f} h for 2,134,371 papers")
        return
    np.save(OUT, vectors)
    log(f"wrote {OUT} in {(time.time()-t0)/60:.1f} min")
    log("next: merge into corpus_active + embeddings, then rebuild s04+")


if __name__ == "__main__":
    main()
