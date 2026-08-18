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

from datetime import date

import numpy as np
import polars as pl

from pipeline.common import log
from pipeline.common.io import read_npy, write_npy
from pipeline.common.thinning import assign_reveal_levels
from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR, Config, ensure_dirs, load_config

COORDS_IN = INTERIM_DIR / "coords2d.npy"
OUT = INTERIM_DIR / "reveal_levels.npy"


def _importance(corpus: pl.DataFrame, cfg: Config) -> np.ndarray:
    """Reveal-ordering signal, highest revealed first.

    Raw citations are strongly age-biased — a 2017 paper has had nine years to accumulate
    what a 2026 one has had months for — so on the all-years corpus they would fill the
    coarsest zoom with a decade of old work. Dividing by ``age ** age_alpha`` discounts that
    head start without flipping to citations-per-year, which over-rewards brand-new papers.
    """
    cites = np.asarray(corpus["cited_by_count"].fill_null(0).to_list(), dtype=np.float64)
    if cfg.tiling.importance == "cited_by_count":
        return cites

    years = np.array(
        [int(str(v)[:4]) if v else 0 for v in corpus["publication_date"].to_list()],
        dtype=np.float64,
    )
    # Papers with no usable date fall back to the corpus median year rather than year 0,
    # which would otherwise hand them an enormous age and bury them permanently.
    valid = years > 1900
    years = np.where(valid, years, np.median(years[valid]) if valid.any() else 2020.0)
    now = date.today().year + date.today().month / 12.0
    # +0.5 puts a paper mid-year; the 0.5y floor stops this-month papers dividing by ~0.
    age = np.maximum(now - (years + 0.5), 0.5)
    return cites / age**cfg.tiling.age_alpha


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s12_tiles")

    coords = read_npy(COORDS_IN)
    corpus = pl.read_parquet(CORPUS_ACTIVE)
    importance = _importance(corpus, cfg)

    levels = assign_reveal_levels(
        coords,
        importance,
        n_levels=cfg.tiling.max_levels,
        base_divisor=cfg.tiling.base_divisor,
        top_fraction=cfg.tiling.top_fraction,
    )
    write_npy(levels.astype(np.int32), OUT)

    n = len(levels)
    n_used = int(levels.max()) + 1
    log.info(f"{n} papers → {n_used} reveal levels (base_divisor={cfg.tiling.base_divisor})")
    # Report the citation floor per level: the whole point of the gate is that the coarsest
    # levels are genuinely influential, and that is only checkable by looking at the numbers.
    cites = np.asarray(corpus["cited_by_count"].fill_null(0).to_list(), dtype=np.float64)
    cumulative = 0
    for level in range(n_used):
        mask = levels == level
        count = int(mask.sum())
        cumulative += count
        cl = cites[mask]
        stats = f"cites min={cl.min():.0f} median={np.median(cl):.0f}" if count else ""
        log.info(f"  level {level:2d}: +{count:7d}  cumulative={cumulative:8d} "
                 f"({cumulative / n * 100:5.1f}%)  {stats}")
    log.info(f"wrote → {OUT}")
    return str(OUT)


if __name__ == "__main__":
    run()
