"""Append the all-category expansion to the live corpus: 1,000,490 -> 3,134,861 papers.

Same shape as merge_corpora.py, and the same hard-won rule: when the two frames disagree on a
column type, always widen — casting toward a degenerate all-Null column silently destroys data
(an earlier version of that script discarded 640,152 subfield_name values that way).

The invariant that matters: embeddings.npy row i must correspond to corpus_active node_id == i.
Both sides are sorted by their own node_id, so concatenating corpus and vectors in the same
order and renumbering densely preserves it. It is asserted at the end rather than assumed.

    uv run python merge_allcats.py
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np
import polars as pl

LIVE = Path("data/interim")
BACKUP = LIVE / "_pre_allcats_backup"
CORPUS_IN = LIVE / "corpus_allcats_new.parquet"
VECTORS_IN = LIVE / "embeddings_allcats_new.npy"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    BACKUP.mkdir(parents=True, exist_ok=True)
    for f in ("corpus_active.parquet", "embeddings.npy", "embed_meta.json"):
        src = LIVE / f
        if src.exists() and not (BACKUP / f).exists():
            log(f"backing up {f}")
            shutil.copy2(src, BACKUP / f)

    live = pl.read_parquet(LIVE / "corpus_active.parquet").sort("node_id")
    add = pl.read_parquet(CORPUS_IN)
    log(f"live {live.height:,} + expansion {add.height:,} = {live.height + add.height:,}")

    overlap = set(live["arxiv_id"].to_list()) & set(add["arxiv_id"].to_list())
    if overlap:
        raise SystemExit(f"arXiv id overlap ({len(overlap)}) — dedupe before merging")

    missing = [c for c in live.columns if c not in add.columns]
    log(f"filling {len(missing)} columns absent from the expansion: {missing[:8]}"
        f"{'…' if len(missing) > 8 else ''}")
    add = add.with_columns([pl.lit(None).cast(live.schema[c]).alias(c) for c in missing])

    casts = []
    for col in live.columns:
        ta, tb = live.schema[col], add.schema[col]
        if ta == tb:
            continue
        if ta == pl.Null:  # live side empty -> adopt the backfill's real type
            live = live.with_columns(pl.col(col).cast(tb, strict=False).alias(col))
            casts.append(f"{col}: live Null -> {tb} (widened)")
        else:
            add = add.with_columns(pl.col(col).cast(ta, strict=False).alias(col))
            casts.append(f"{col}: expansion {tb} -> {ta}")
    for c in casts:
        log(f"    {c}")
    add = add.select(live.columns)

    merged = pl.concat([live, add], how="vertical")
    merged = merged.with_columns(pl.int_range(0, merged.height, dtype=pl.Int64).alias("node_id"))

    va = np.load(LIVE / "embeddings.npy", mmap_mode="r")
    vb = np.load(VECTORS_IN, mmap_mode="r")
    if va.shape[1] != vb.shape[1]:
        raise SystemExit(f"embedding dim mismatch: {va.shape} vs {vb.shape}")
    if va.shape[0] != live.height or vb.shape[0] != add.height:
        raise SystemExit("embedding row counts do not match their corpora")

    out = np.empty((va.shape[0] + vb.shape[0], va.shape[1]), dtype=np.float32)
    out[: va.shape[0]] = va
    out[va.shape[0] :] = vb
    norms = np.linalg.norm(out, axis=1)
    log(f"merged vectors {out.shape}, norms {norms.min():.6f}..{norms.max():.6f}")
    if norms.min() < 0.99 or norms.max() > 1.01:
        raise SystemExit("vectors are not unit-normalised — the two halves are on different scales")
    if not np.isfinite(out).all():
        raise SystemExit("merged embeddings contain non-finite values")

    merged.write_parquet(LIVE / "corpus_active.parquet")
    np.save(LIVE / "embeddings.npy", out)
    meta = json.loads((LIVE / "embed_meta.json").read_text())
    meta.update({"n": int(out.shape[0]), "merged_from": ["2025-2026", "2015-2024", "1991-2014", "all-categories-2026-08"]})
    (LIVE / "embed_meta.json").write_text(json.dumps(meta, indent=2))

    chk = pl.read_parquet(LIVE / "corpus_active.parquet")
    vec = np.load(LIVE / "embeddings.npy", mmap_mode="r")
    assert chk.height == vec.shape[0], "corpus/embedding row mismatch after write"
    assert chk["node_id"].to_list() == list(range(chk.height)), "node_id not dense"
    yrs = [str(d)[:4] for d in chk["publication_date"].to_list() if d]
    log(f"\nMERGE COMPLETE in {(time.time()-t0)/60:.1f} min")
    log(f"  papers : {chk.height:,}")
    log(f"  years  : {min(yrs)}..{max(yrs)}")
    log(f"  backup : {BACKUP}")


if __name__ == "__main__":
    main()
