"""Per-paper flag: does S2 supply a reference list for this paper at all?

An empty References tab currently means two very different things and the UI cannot tell them
apart: "this paper's references are all outside the corpus" versus "nobody has extracted this
paper's references". The second is common and systematic — S2's reference extraction lags for
recent work:

    2019 99.7% | 2020 99.7% | 2021 99.2% | 2022 99.0%
    2023 97.9% | 2024 92.3% | 2025 92.4% | 2026 70.4%

Overall 60,036 of 905,556 matched papers (6.6%) have no reference list. "Gemini 2.5" is the case
the user hit: 2,935 in-corpus citers, zero references.

This mirrors how `citation_count_available` already separates "unavailable" from "zero", so the
panel can say which it is instead of rendering a blank list.

    uv run python build_reference_availability.py
"""
from __future__ import annotations

import time

import polars as pl

from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR

OUT = INTERIM_DIR / "reference_availability.parquet"
S2AG = "data/s2ag"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    corpus = pl.read_parquet(CORPUS_ACTIVE, columns=["node_id", "arxiv_id"]).with_columns(
        pl.col("arxiv_id").str.replace(r"v\d+$", "").alias("aid")
    )
    cross = pl.read_parquet(f"{S2AG}/crosswalk.parquet")
    matched = corpus.join(cross, left_on="aid", right_on="arxiv_id", how="left")
    log(f"corpus {corpus.height:,} | matched to an S2 corpusid "
        f"{matched['corpusid'].is_not_null().sum():,}")

    cids = matched.filter(pl.col("corpusid").is_not_null())["corpusid"].to_list()
    # The COUNT as well as the flag: the UI needs to say "5 of 18 references are in this map",
    # and S2's own `s2_reference_count` column disagrees with refs.parquet (9 vs 18 for
    # arXiv:2606.00321), so derive it from the edge lists we actually hold.
    have = (
        pl.scan_parquet(f"{S2AG}/refs.parquet")
        .filter(pl.col("src").is_in(cids))
        .select(["src", pl.col("neighbors").list.len().alias("n_refs")])
        .group_by("src")
        .agg(pl.col("n_refs").max())
        .collect()
    )
    with_refs = set(have["src"].to_list())
    ref_count = dict(zip(have["src"].to_list(), have["n_refs"].to_list()))
    log(f"of those, S2 has >=1 outgoing reference for {len(with_refs):,}")

    # A handful of arXiv ids map to MORE THAN ONE S2 corpusid, so the join above fans out and
    # would emit more rows than the corpus has papers (912,479 vs 912,429 when first run).
    # Collapse per paper: it has reference data if ANY of its corpusids does.
    out = (
        matched.with_columns(
            pl.col("corpusid").is_in(list(with_refs)).fill_null(False).alias("has_refs"),
            pl.col("corpusid").replace_strict(ref_count, default=0).alias("n_refs"),
        )
        .group_by("node_id")
        .agg(
            pl.col("has_refs").any().alias("references_available"),
            # Fan-out: one arXiv id can map to several corpusids, so take the fullest list.
            pl.col("n_refs").max().alias("reference_count"),
        )
        .sort("node_id")
    )

    n_true = int(out["references_available"].sum())
    log(f"references_available: {n_true:,} true / {out.height - n_true:,} false "
        f"({(out.height - n_true) / out.height * 100:.1f}% will now say so explicitly)")
    out.write_parquet(OUT)
    log(f"wrote {OUT} in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
