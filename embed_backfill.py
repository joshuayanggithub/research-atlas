"""Embed the 1991-2014 backfill corpus with the SAME model/adapter as the live corpus.

This must match `embed_meta.json` exactly — specter2_local, allenai/specter2_base +
allenai/specter2:proximity, 768-d — or the vectors cannot share a space with the existing
912,429 and the merge would be meaningless.

Runs on CPU by design: the RTX 3090 is present but its kernel module (580.159.03) is older than
the installed userspace driver (580.173), so CUDA init fails until a reboot. Measured CPU
throughput is ~30 papers/sec, i.e. ~1-1.5 h for 88,061 — fine unattended, and it avoids asking
for a reboot that would kill the running session. The embedder checkpoints, so it resumes.

    uv run python embed_backfill.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import polars as pl

from pipeline.config import INTERIM_DIR, load_config
from pipeline.embedding.specter2_local import Specter2LocalBackend

CORPUS_IN = INTERIM_DIR / "corpus_backfill_1991_2014.parquet"
OUT = INTERIM_DIR / "embeddings_backfill_1991_2014.npy"
META = INTERIM_DIR / "embed_meta.json"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    if OUT.exists():
        log(f"{OUT.name} already exists — nothing to do")
        return

    corpus = pl.read_parquet(CORPUS_IN)
    log(f"corpus: {corpus.height:,} papers")

    live = json.loads(META.read_text())
    log(f"live space: {live['model']} dim={live['dim']} (n={live['n']:,})")

    cfg = load_config()
    # Separate checkpoint paths so a resume here can never be confused with the live corpus's
    # partial embedding file (data/interim/embeddings.specter2.partial.npy).
    embedder = Specter2LocalBackend(
        model=cfg.embedding.specter2_model,
        adapter=cfg.embedding.specter2_adapter,
        dim=cfg.embedding.dim,
        batch_size=cfg.embedding.local_batch_size,
        device="cpu",
        precision="fp32",
        checkpoint_every=cfg.embedding.checkpoint_every,
        partial_path=INTERIM_DIR / "backfill.specter2.partial.npy",
        checkpoint_path=INTERIM_DIR / "backfill.specter2.checkpoint.json",
    )
    result = embedder.embed(corpus)
    vectors = np.asarray(result.vectors, dtype=np.float32)

    # L2-normalise, exactly as s03 does (s03_embed._l2_normalize). The backend itself returns
    # RAW vectors — norms come out ~21-22 — and calling it directly skips that step, which would
    # have put the backfill on a different scale from the live corpus and corrupted every cosine
    # distance in the merged map.
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

    np.save(OUT, vectors)
    log(f"wrote {OUT}  in {(time.time()-t0)/60:.1f} min")
    log("next: merge_backfill.py to append into corpus_active + embeddings, then rebuild s04+")


if __name__ == "__main__":
    main()
