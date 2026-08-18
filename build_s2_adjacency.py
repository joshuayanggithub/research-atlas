"""Phase 3: fold the per-shard edge parquets into both-direction adjacency (decision D3).

    refs.parquet      corpusid -> [cited corpusids]     ("what does this paper cite")
    cited_by.parquet  corpusid -> [citing corpusids]    ("who cites this paper")

MEMORY DESIGN — why this is not a one-line group_by.
A naive `scan_parquet(...).group_by(key).agg(...).sort(key).sink_parquet(...)` was measured on
8 of the 393 shards: 46.4M groups, **9.6 GB peak RSS** for a 473 MB output — about 20x
overhead. Extrapolated to ~4.9B edges that projects into the hundreds of GB and would be
OOM-killed on this 78 GB box (exactly how the phase 1 papers table died).

So we do a classic external (bucketed) group-by instead:

  pass 1  partition every edge into BUCKETS by hash(key) — one shard in memory at a time
  pass 2  group each bucket independently — a bucket is ~1/BUCKETS of the data, so it fits
  pass 3  concatenate the per-bucket outputs (disjoint by construction, since a key hashes
          to exactly one bucket)

Peak memory is therefore bounded by bucket size, not by total edges.

    uv run python build_s2_adjacency.py
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import polars as pl

EDGES = Path("data/s2ag/edges")
OUT = Path("data/s2ag")
BUCKETS = 64          # ~4.9B edges / 64 ≈ 77M edges per bucket ≈ a few GB in polars


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def partition(key: str, val: str, work: Path) -> None:
    """Pass 1: scatter edges into hash buckets, one source shard at a time."""
    work.mkdir(parents=True, exist_ok=True)
    shards = sorted(EDGES.glob("edges_*.parquet"))
    for i, shard in enumerate(shards, 1):
        df = pl.read_parquet(shard, columns=[key, val, "isinfluential"])
        df = df.with_columns((pl.col(key).hash() % BUCKETS).alias("_b"))
        for b, part in df.partition_by("_b", as_dict=True).items():
            bucket = b[0] if isinstance(b, tuple) else b
            path = work / f"b{bucket:03d}"
            path.mkdir(exist_ok=True)
            part.drop("_b").write_parquet(path / f"{i:04d}.parquet", compression="zstd")
        del df
        if i % 50 == 0 or i == len(shards):
            log(f"    partitioned {i}/{len(shards)} shards")


def group_buckets(key: str, val: str, work: Path, out_name: str) -> int:
    """Pass 2+3: group each bucket independently, then concatenate."""
    parts_dir = work / "_grouped"
    parts_dir.mkdir(exist_ok=True)
    total = 0
    for b in range(BUCKETS):
        bucket = work / f"b{b:03d}"
        if not bucket.exists():
            continue
        df = (
            pl.read_parquet(list(bucket.glob("*.parquet")))
            .group_by(key)
            .agg([pl.col(val).alias("neighbors"), pl.col("isinfluential")])
        )
        total += df.height
        df.write_parquet(parts_dir / f"{b:03d}.parquet", compression="zstd")
        del df
        shutil.rmtree(bucket, ignore_errors=True)   # reclaim space as we go
        if (b + 1) % 16 == 0:
            log(f"    grouped bucket {b+1}/{BUCKETS}  ({total:,} keys so far)")
    # Buckets are disjoint by hash, so a plain concat is the whole result.
    pl.scan_parquet(str(parts_dir / "*.parquet")).sink_parquet(
        OUT / out_name, compression="zstd"
    )
    shutil.rmtree(parts_dir, ignore_errors=True)
    return total


def build(direction: str, key: str, val: str, out_name: str) -> None:
    t0 = time.time()
    log(f"building {out_name}  ({direction}: {key} -> [{val}])")
    work = OUT / f"_work_{direction}"
    shutil.rmtree(work, ignore_errors=True)
    partition(key, val, work)
    n = group_buckets(key, val, work, out_name)
    shutil.rmtree(work, ignore_errors=True)
    size = (OUT / out_name).stat().st_size / 1e9
    log(f"  wrote {out_name}: {n:,} keys, {size:.1f} GB, {(time.time()-t0)/60:.1f} min")


def main() -> None:
    shards = sorted(EDGES.glob("edges_*.parquet"))
    if not shards:
        raise SystemExit("no edge shards found — run build_s2_edges.py first")
    log(f"{len(shards)} edge shards on disk")
    total = pl.scan_parquet(str(EDGES / "edges_*.parquet")).select(pl.len()).collect().item()
    log(f"total edges: {total:,}  (bucketed into {BUCKETS} partitions per direction)")

    build("references", "src", "dst", "refs.parquet")
    build("cited_by", "dst", "src", "cited_by.parquet")

    log("\nPHASE 3 COMPLETE")
    for name in ("refs.parquet", "cited_by.parquet"):
        p = OUT / name
        if p.exists():
            log(f"  {name:20s} {p.stat().st_size/1e9:6.1f} GB")


if __name__ == "__main__":
    main()
