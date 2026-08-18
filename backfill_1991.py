"""Build the pre-2015 arXiv CS/stat.ML corpus so the map can reach back to 1991.

Measured from the 2026-08-08 snapshot (3,127,799 papers total):

    era          cs+stat.ML
    1991-2004         5,835
    2005-2014        82,148
    2015-2026       908,589   <- what the live map already covers

So reaching 1991 adds ~88k papers, a 9.7% increase — cheap, unlike an all-fields expansion
(3.13M, 3.4x, and s07 would need ~155 GB at its measured 45.4 GB/912k peak).

This stage only produces the CORPUS. Embedding, merging and the downstream rebuild are separate
steps, because embedding wants the GPU and the merge must not run until vectors exist.

    uv run python backfill_1991.py
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import polars as pl

from pipeline.config import CORPUS_FULL, INTERIM_DIR, load_config
from pipeline.stages import s02_build_arxiv_corpus

BACKUP = INTERIM_DIR / "corpus_2025_2026.parquet"
OUT = INTERIM_DIR / "corpus_backfill_1991_2014.parquet"
DATE_FROM = "1991-01-01"
DATE_TO = "2014-12-31"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    if OUT.exists():
        log(f"{OUT.name} already exists — nothing to do")
        return

    # s02 always writes CORPUS_FULL, which currently holds the 2025-2026 corpus. Preserve it;
    # corpus_active.parquet (the merged 912k the site serves) is a different file and untouched.
    if CORPUS_FULL.exists() and not BACKUP.exists():
        log(f"backing up {CORPUS_FULL.name} -> {BACKUP.name}")
        shutil.copy2(CORPUS_FULL, BACKUP)

    cfg = load_config()
    cfg = cfg.model_copy(
        update={"corpus": cfg.corpus.model_copy(update={"date_from": DATE_FROM, "date_to": DATE_TO})}
    )
    log(f"scope: {DATE_FROM}..{DATE_TO}  prefixes={cfg.arxiv.category_prefixes} "
        f"categories={cfg.arxiv.categories}")

    s02_build_arxiv_corpus.run(cfg)

    df = pl.read_parquet(CORPUS_FULL)
    dates = df["publication_date"].cast(pl.Utf8)
    log(f"backfill corpus: {df.height:,} papers, {dates.min()} .. {dates.max()}")
    df.write_parquet(OUT)
    log(f"wrote {OUT}")

    # Put the original full corpus back so no other stage sees a surprise window.
    if BACKUP.exists():
        shutil.copy2(BACKUP, CORPUS_FULL)
        log(f"restored {CORPUS_FULL.name} from {BACKUP.name}")

    log(f"done in {(time.time()-t0)/60:.1f} min")
    log("next: embed OUT (s03 on GPU), then merge into corpus_active + embeddings")


if __name__ == "__main__":
    main()
