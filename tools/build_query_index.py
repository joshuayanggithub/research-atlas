"""Persist an hnswlib index over the corpus embeddings for query-time retrieval.

s08_neighbors builds an equivalent index, uses it, and throws it away -- fine for an offline
stage, useless for a service that must answer in milliseconds. Same parameters (cosine, M=16,
ef_construction=200) so query-time results match the precomputed neighbours the map already
shows; this only adds persistence. s08 itself is untouched.

    uv run python tools/build_query_index.py
"""
from __future__ import annotations

import os
import time

import numpy as np

from pipeline.config import INTERIM_DIR

VECTORS = INTERIM_DIR / "embeddings.npy"
OUT = INTERIM_DIR / "query_index.hnsw"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    import hnswlib

    if OUT.exists():
        log(f"{OUT.name} exists — nothing to do")
        return
    t0 = time.time()
    vectors = np.load(VECTORS, mmap_mode="r")
    n, dim = vectors.shape
    log(f"indexing {n:,} x {dim} vectors (cosine, M=16, ef_construction=200)")

    index = hnswlib.Index(space="cosine", dim=dim)
    index.init_index(max_elements=n, ef_construction=200, M=16, random_seed=42)
    # Mirrors s08's threading note: an explicit positive count, half the logical CPUs, because
    # hnswlib 0.8 can segfault on the num_threads=0 "all cores" sentinel.
    threads = max(1, min(8, (os.cpu_count() or 1) // 2))
    index.set_num_threads(threads)

    # Add in blocks so the memmap is streamed rather than fully materialised.
    block = 250_000
    for start in range(0, n, block):
        stop = min(start + block, n)
        index.add_items(np.asarray(vectors[start:stop], dtype=np.float32),
                        np.arange(start, stop), num_threads=threads)
        log(f"  {stop:,}/{n:,} ({100 * stop / n:.0f}%)  {(time.time() - t0) / 60:.1f} min")

    index.save_index(str(OUT))
    size = OUT.stat().st_size / 2**30
    log(f"wrote {OUT.name} ({size:.1f} GB) in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
