"""s12: Assign each paper a reveal level for overlap-free semantic zoom + tiling.

Runs greedy spatial thinning (``pipeline.common.thinning``) over the 2D layout: level 0 is
a sparse, well-separated set of the most-cited papers, and each deeper level admits ~4×
more while keeping a minimum on-screen separation. The frontend shows cumulative levels
0..current for the viewport, so (a) no two visible points overlap at any zoom and (b) the
corpus is fetched on demand rather than all at once.

Runs after s04 (needs coords) and reads the active corpus for the importance signal.

Emits:
    data/interim/reveal_levels.npy   [N] int32, row-aligned to node_id (0 = coarsest)
"""

from __future__ import annotations

import numpy as np
import polars as pl

from pipeline.common import log
from pipeline.common.io import read_npy, write_npy
from pipeline.common.thinning import assign_reveal_levels
from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR, Config, ensure_dirs, load_config

COORDS_IN = INTERIM_DIR / "coords2d.npy"
OUT = INTERIM_DIR / "reveal_levels.npy"


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s12_tiles")

    coords = read_npy(COORDS_IN)
    corpus = pl.read_parquet(CORPUS_ACTIVE)
    importance = np.asarray(corpus[cfg.tiling.importance].to_list(), dtype=np.float64)

    levels = assign_reveal_levels(
        coords,
        importance,
        n_levels=cfg.tiling.max_levels,
        base_divisor=cfg.tiling.base_divisor,
    )
    write_npy(levels.astype(np.int32), OUT)

    n = len(levels)
    n_used = int(levels.max()) + 1
    log.info(f"{n} papers → {n_used} reveal levels (base_divisor={cfg.tiling.base_divisor})")
    cumulative = 0
    for level in range(n_used):
        count = int((levels == level).sum())
        cumulative += count
        log.info(f"  level {level:2d}: +{count:7d}  cumulative={cumulative:8d} "
                 f"({cumulative / n * 100:5.1f}%)")
    log.info(f"wrote → {OUT}")
    return str(OUT)


if __name__ == "__main__":
    run()
