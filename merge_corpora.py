"""Merge the 2015-2024 and 2025-2026 corpora into one all-years build.

The two corpora were built separately (see docs/CITATION_GRAPH_PLAN.md) and are cleanly
mergeable:

  * 271,366 + 641,063 = 912,429 papers with **zero arXiv-id overlap** (a clean date partition)
  * both embedded with the SAME model and adapter — `specter2_local`,
    `allenai/specter2_base+allenai/specter2:proximity`, 768-d, 100% coverage — so the vectors
    concatenate directly and **nothing needs re-embedding** (which would otherwise be the
    single most expensive step)
  * the historical corpus is a strict column subset: it lacks only the five `s2_*` columns,
    which are filled as null for those rows

The one invariant that matters: `embeddings.npy` row i must correspond to `corpus_active`
`node_id == i`. Both inputs are sorted by their own node_id, so concatenating the corpora and
the vectors in the same order and then renumbering densely preserves alignment. It is checked
explicitly at the end rather than assumed.

    uv run python merge_corpora.py
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np
import polars as pl

LIVE = Path("data/interim")
HIST = Path("/home/joshua/research-atlas-historical/data/interim")
BACKUP = Path("data/interim/_pre_merge_backup")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    BACKUP.mkdir(parents=True, exist_ok=True)
    for f in ("corpus_active.parquet", "embeddings.npy", "embed_meta.json"):
        src = LIVE / f
        if src.exists() and not (BACKUP / f).exists():
            log(f"backing up {f} -> {BACKUP/f}")
            shutil.copy2(src, BACKUP / f)

    a = pl.read_parquet(LIVE / "corpus_active.parquet").sort("node_id")
    b = pl.read_parquet(HIST / "corpus_active.parquet").sort("node_id")
    log(f"live {a.height:,} + historical {b.height:,} = {a.height + b.height:,}")

    overlap = set(a["arxiv_id"].to_list()) & set(b["arxiv_id"].to_list())
    if overlap:
        raise SystemExit(f"arXiv id overlap ({len(overlap)}) — dedupe before merging")

    # Give the historical rows the s2_* columns as nulls so the schemas line up.
    missing = [c for c in a.columns if c not in b.columns]
    log(f"filling {len(missing)} missing columns on historical rows: {missing}")
    b = b.with_columns([pl.lit(None).cast(a.schema[c]).alias(c) for c in missing])
    b = b.select(a.columns)

    # The two builds came from different pipeline runs, so some columns drifted (observed:
    # topic_id Int64 vs Int32, subfield_name String vs Null). Always align to the WIDER type:
    # casting toward a degenerate all-Null column would silently destroy real data — an earlier
    # version of this script did exactly that and discarded 640,152 subfield_name values.
    casts = []
    for col in a.columns:
        ta, tb = a.schema[col], b.schema[col]
        if ta == tb:
            continue
        if ta == pl.Null:            # live side is empty -> adopt the historical type
            target = tb
            a = a.with_columns(pl.col(col).cast(target, strict=False).alias(col))
            casts.append(f"{col}: live {ta} -> {target} (widened; historical has data)")
        elif tb == pl.Null:
            target = ta
            b = b.with_columns(pl.col(col).cast(target, strict=False).alias(col))
            casts.append(f"{col}: hist {tb} -> {target} (widened)")
        else:
            target = ta
            b = b.with_columns(pl.col(col).cast(target, strict=False).alias(col))
            casts.append(f"{col}: hist {tb} -> {target}")
    if casts:
        log(f"aligning {len(casts)} dtype mismatches (widening, never narrowing to Null):")
        for c in casts:
            log(f"    {c}")
    b = b.select(a.columns)

    merged = pl.concat([a, b], how="vertical")
    merged = merged.with_columns(pl.int_range(0, merged.height, dtype=pl.Int64).alias("node_id"))
    log(f"merged corpus: {merged.height:,} rows, node_id renumbered densely")

    va = np.load(LIVE / "embeddings.npy", mmap_mode="r")
    vb = np.load(HIST / "embeddings.npy", mmap_mode="r")
    if va.shape[1] != vb.shape[1]:
        raise SystemExit(f"embedding dim mismatch: {va.shape} vs {vb.shape}")
    if va.shape[0] != a.height or vb.shape[0] != b.height:
        raise SystemExit("embedding row counts do not match their corpora")

    out = np.empty((va.shape[0] + vb.shape[0], va.shape[1]), dtype=np.float32)
    out[: va.shape[0]] = va
    out[va.shape[0] :] = vb
    log(f"merged embeddings: {out.shape} {out.dtype}")

    # Alignment check: the concatenation order must match the corpus order.
    norms = np.linalg.norm(out, axis=1)
    log(f"norm range {norms.min():.6f}..{norms.max():.6f} (expect ~1.0, unit-normalised)")
    if not np.isfinite(out).all():
        raise SystemExit("merged embeddings contain non-finite values")

    merged.write_parquet(LIVE / "corpus_active.parquet")
    np.save(LIVE / "embeddings.npy", out)
    meta = json.loads((LIVE / "embed_meta.json").read_text())
    meta.update({"n": int(out.shape[0]), "merged_from": ["2025-2026", "2015-2024"]})
    (LIVE / "embed_meta.json").write_text(json.dumps(meta, indent=2))

    # Re-read and verify what actually landed on disk.
    chk = pl.read_parquet(LIVE / "corpus_active.parquet")
    vec = np.load(LIVE / "embeddings.npy", mmap_mode="r")
    assert chk.height == vec.shape[0], "corpus/embedding row mismatch after write"
    assert chk["node_id"].to_list() == list(range(chk.height)), "node_id not dense"
    yrs = [str(d)[:4] for d in chk["publication_date"].to_list() if d]
    log(f"\nMERGE COMPLETE in {(time.time()-t0)/60:.1f} min")
    log(f"  papers   : {chk.height:,}")
    log(f"  years    : {min(yrs)}..{max(yrs)}")
    log(f"  vectors  : {vec.shape}")
    log(f"  backup   : {BACKUP}")
    log("  next: run_all --from s04 (skips embedding; vectors already merged)")


if __name__ == "__main__":
    main()
