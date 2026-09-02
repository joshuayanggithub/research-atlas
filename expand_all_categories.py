"""Build the ALL-CATEGORY arXiv corpus slice that the live map does not already have.

The live corpus is cs.* + stat.ML, 1991-2026: 1,000,490 papers. The 2026-08-08 snapshot holds
3,127,799 across every archive, so this adds ~2.13M papers (astro-ph 340k, math 594k,
cond-mat 353k, physics 215k, quant-ph 135k, q-bio 35k, econ 12k ...).

Same shape as backfill_1991.py, and the same rule: this stage produces the CORPUS ONLY.
Embedding, merging and the downstream rebuild are separate steps, because embedding wants the
GPU and the merge must not run until vectors exist. corpus_active.parquet — the 1,000,490 the
live site is built from — is never touched by this script.

Papers already in corpus_active are excluded by arXiv id, so the output is exactly the work to
embed. Their existing vectors stay valid: the merge concatenates rather than re-embeds, which
is only sound because the same model/adapter is used (see embed_meta.json).

    uv run python expand_all_categories.py
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import polars as pl

from pipeline.config import CORPUS_ACTIVE, CORPUS_FULL, INTERIM_DIR, load_config
from pipeline.stages import s02_build_arxiv_corpus

BACKUP = INTERIM_DIR / "corpus.parquet.pre-allcats.bak"
RAW_OUT = INTERIM_DIR / "corpus_allcats_full.parquet"
OUT = INTERIM_DIR / "corpus_allcats_new.parquet"
DATE_FROM = "1991-01-01"
DATE_TO = "2026-12-31"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    if OUT.exists():
        log(f"{OUT.name} already exists — nothing to do")
        return

    if CORPUS_FULL.exists() and not BACKUP.exists():
        log(f"backing up {CORPUS_FULL.name} -> {BACKUP.name}")
        shutil.copy2(CORPUS_FULL, BACKUP)

    cfg = load_config()
    # "" is a prefix of every category string, so this admits every archive by construction —
    # including any arXiv adds later. Enumerating archives by hand would silently miss those.
    cfg = cfg.model_copy(update={
        "corpus": cfg.corpus.model_copy(update={"date_from": DATE_FROM, "date_to": DATE_TO}),
        "arxiv": cfg.arxiv.model_copy(update={"category_prefixes": [""], "categories": []}),
    })
    log(f"scope: {DATE_FROM}..{DATE_TO}, ALL categories")

    s02_build_arxiv_corpus.run(cfg)

    full = pl.read_parquet(CORPUS_FULL)
    dates = full["publication_date"].cast(pl.Utf8)
    log(f"all-category corpus: {full.height:,} papers, {dates.min()} .. {dates.max()}")
    full.write_parquet(RAW_OUT)

    have = set(pl.read_parquet(CORPUS_ACTIVE, columns=["arxiv_id"])["arxiv_id"].to_list())
    log(f"already live: {len(have):,} arXiv ids")
    new = full.filter(~pl.col("arxiv_id").is_in(have))
    log(f"NEW to embed: {new.height:,} papers")
    if "primary_category" in new.columns:
        top = (new.group_by("primary_category").len()
                  .sort("len", descending=True).head(12).rows())
        log("top new categories: " + ", ".join(f"{c}={n:,}" for c, n in top))
    new.write_parquet(OUT)
    log(f"wrote {OUT}")

    if BACKUP.exists():
        shutil.copy2(BACKUP, CORPUS_FULL)
        log(f"restored {CORPUS_FULL.name} from {BACKUP.name}")
    log(f"done in {(time.time()-t0)/60:.1f} min")
    log("next: embed OUT on the GPU with the SAME model as embed_meta.json, then merge")


if __name__ == "__main__":
    main()
