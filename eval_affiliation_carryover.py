"""Can an author's OTHER papers supply the affiliation their recent papers are missing?

Org attribution collapses after the MAG shutdown: papers carrying institution_ids run 50-63%
before 2021 and 17-21% after (6% in 2026), because OpenAlex stopped getting affiliations for
arXiv-only preprints. The obvious repair, parsing affiliations out of PDFs, needs ~730k PDFs
over a ~1 MB/s link — not viable here.

But the corpus already knows where most of these authors work: 270,342 papers DO carry
institutions, and authors persist across papers. So attribute a paper from its authors' nearest
IN TIME affiliated paper.

This script does not apply anything. It measures whether the rule is good enough to apply, on
papers whose true affiliations we know, with the paper itself held out:

    uv run python eval_affiliation_carryover.py

Reports precision (of predicted institutions, how many are correct), recall (of true
institutions, how many we predict), and the extra coverage the rule would buy — sliced by the
time gap allowed, because an affiliation from eight years ago is a different claim from one made
last month.
"""
from __future__ import annotations

import random
import time
from collections import defaultdict

import polars as pl

from pipeline.config import CORPUS_ACTIVE

SAMPLE = 20_000          # held-out affiliated papers to score
GAPS = (365, 730, 1825, 100_000)  # days of separation allowed, last one = unlimited
SEED = 17


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    c = pl.read_parquet(
        CORPUS_ACTIVE, columns=["node_id", "publication_date", "author_ids", "institution_ids"]
    )
    c = c.with_columns(
        pl.col("publication_date").cast(pl.Date).alias("d"),
        pl.col("institution_ids").list.len().alias("n_inst"),
    )
    affiliated = c.filter((pl.col("n_inst") > 0) & pl.col("d").is_not_null())
    log(f"corpus {c.height:,} | affiliated {affiliated.height:,} ({affiliated.height/c.height*100:.1f}%)")

    # author -> [(ordinal date, frozenset(institutions), node_id)], from affiliated papers only.
    by_author: dict[str, list[tuple[int, frozenset[str], int]]] = defaultdict(list)
    for node, day, authors, insts in zip(
        affiliated["node_id"].to_list(),
        affiliated["d"].to_list(),
        affiliated["author_ids"].to_list(),
        affiliated["institution_ids"].to_list(),
    ):
        if not authors:
            continue
        rec = (day.toordinal(), frozenset(i for i in (insts or []) if i), node)
        for a in authors:
            if a:
                by_author[a].append(rec)
    log(f"authors with at least one affiliated paper: {len(by_author):,}")

    rng = random.Random(SEED)
    idx = list(range(affiliated.height))
    rng.shuffle(idx)
    held = idx[:SAMPLE]
    nodes = affiliated["node_id"].to_list()
    days = affiliated["d"].to_list()
    auths = affiliated["author_ids"].to_list()
    truths = affiliated["institution_ids"].to_list()

    def score(name, gap, mode, min_papers=1):
        tp = fp = fn = 0
        predicted_any = 0
        scored = 0
        for i in held:
            node, day = nodes[i], days[i].toordinal()
            truth = {x for x in (truths[i] or []) if x}
            if not truth:
                continue
            scored += 1
            votes: dict[str, int] = {}
            paper_authors = auths[i] or []
            considered = paper_authors[:1] if mode == "first" else paper_authors
            for a in considered:
                history = by_author.get(a, ())
                if len(history) < min_papers:
                    continue
                best = None
                for other_day, insts, other_node in history:
                    if other_node == node:
                        continue
                    delta = abs(other_day - day)
                    if delta > gap:
                        continue
                    if best is None or delta < best[0]:
                        best = (delta, insts)
                if best:
                    for inst in best[1]:
                        votes[inst] = votes.get(inst, 0) + 1
            need = 2 if mode == "agree2" else 1
            pred = {k for k, v in votes.items() if v >= need}
            if pred:
                predicted_any += 1
            tp += len(pred & truth)
            fp += len(pred - truth)
            fn += len(truth - pred)
        prec = tp / (tp + fp) * 100 if tp + fp else 0.0
        rec = tp / (tp + fn) * 100 if tp + fn else 0.0
        log(f"{name:34s}: precision {prec:5.1f}% | recall {rec:5.1f}% | "
            f"predicts for {predicted_any/scored*100:5.1f}%")

    # The measured precision above is a LOWER BOUND: institution_ids is a flat per-PAPER list,
    # so on a 10-author paper listing 2 institutions, a correct prediction for the other 8
    # authors is counted as a false positive. Single-author papers have no such ambiguity —
    # truth there is exactly that one author's affiliations — so score them separately.
    solo = [i for i in held if len(auths[i] or []) == 1]
    log(f"single-author held-out papers: {len(solo):,}")
    _held = held

    score("first author only, +-1y", 365, "first")
    score("first author only, +-2y", 730, "first")
    score("first author, >=3 affil papers", 730, "first", 3)
    score("agreement of >=2 authors, +-2y", 730, "agree2")
    score("agreement of >=2 authors, +-5y", 1825, "agree2")
    held = solo
    score("SOLO papers, nearest +-1y", 365, "all")
    score("SOLO papers, nearest +-2y", 730, "all")
    score("SOLO papers, unlimited", 100_000, "all")
    held = _held

    for gap in GAPS:
        tp = fp = fn = 0
        predicted_any = 0
        scored = 0
        for i in held:
            node, day = nodes[i], days[i].toordinal()
            truth = {x for x in (truths[i] or []) if x}
            if not truth:
                continue
            scored += 1
            pred: set[str] = set()
            for a in auths[i] or []:
                best: tuple[int, frozenset[str]] | None = None
                for other_day, insts, other_node in by_author.get(a, ()):  # held out below
                    if other_node == node:
                        continue          # the paper itself must not answer its own question
                    delta = abs(other_day - day)
                    if delta > gap:
                        continue
                    if best is None or delta < best[0]:
                        best = (delta, insts)
                if best:
                    pred |= best[1]
            if pred:
                predicted_any += 1
            tp += len(pred & truth)
            fp += len(pred - truth)
            fn += len(truth - pred)
        prec = tp / (tp + fp) * 100 if tp + fp else 0.0
        rec = tp / (tp + fn) * 100 if tp + fn else 0.0
        label = "unlimited" if gap > 50_000 else f"±{gap/365:.0f}y"
        log(
            f"gap {label:>9}: precision {prec:5.1f}% | recall {rec:5.1f}% | "
            f"a prediction at all for {predicted_any/scored*100:5.1f}% of {scored:,} papers"
        )

    # What the rule would actually buy: unaffiliated papers whose authors are known elsewhere.
    unaffiliated = c.filter(pl.col("n_inst") == 0)
    reachable = 0
    for authors in unaffiliated["author_ids"].to_list():
        if any(a in by_author for a in (authors or [])):
            reachable += 1
    log(
        f"coverage: {reachable:,} of {unaffiliated.height:,} unaffiliated papers have at least "
        f"one author we know an institution for "
        f"(corpus attribution {affiliated.height/c.height*100:.1f}% -> "
        f"{(affiliated.height + reachable)/c.height*100:.1f}%)"
    )
    log(f"done in {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    main()
