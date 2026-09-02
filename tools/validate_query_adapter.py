"""Does SPECTER2's adhoc_query adapter land in the same space as our proximity doc vectors?

The plan's day-one risk. Documents were embedded as `title[SEP]abstract` through the
*proximity* adapter (embed_meta.json). SPECTER2 also ships an *adhoc_query* adapter meant for
short queries against those same document vectors -- but "meant for" is not evidence, and
building a 10 GB index on an assumption is how you waste a day.

Cheap decisive test: take a paper's TITLE as the query, and see whether its own document vector
comes back. Ground truth needs no annotation. Brute-force over a sampled pool, so this runs in
minutes instead of requiring the full index.

    uv run python tools/validate_query_adapter.py [--queries 500] [--pool 200000]
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import polars as pl
import torch

from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR

VECTORS = INTERIM_DIR / "embeddings.npy"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_model(adapter_repo: str, load_as: str):
    from adapters import AutoAdapterModel
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained("allenai/specter2_base")
    model = AutoAdapterModel.from_pretrained("allenai/specter2_base")
    model.load_adapter(adapter_repo, source="hf", load_as=load_as, set_active=True)
    model.eval().to("cuda")
    return tok, model


@torch.no_grad()
def encode(tok, model, texts: list[str], batch: int = 64) -> np.ndarray:
    out = []
    for i in range(0, len(texts), batch):
        enc = tok(texts[i:i + batch], padding=True, truncation=True,
                  return_tensors="pt", max_length=512).to("cuda")
        # CLS pooling, exactly as the document side does (specter2_local.embed).
        vec = model(**enc).last_hidden_state[:, 0, :].float().cpu().numpy()
        out.append(vec)
    v = np.concatenate(out).astype(np.float32)
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=500)
    ap.add_argument("--pool", type=int, default=200_000)
    args = ap.parse_args()

    rng = np.random.default_rng(0)
    vectors = np.load(VECTORS, mmap_mode="r")
    n_total = vectors.shape[0]
    corpus = pl.read_parquet(CORPUS_ACTIVE, columns=["node_id", "title"])
    titles = corpus["title"].to_list()

    # Queries must have a real title; the pool always contains their own documents.
    q_ids = rng.choice(n_total, size=args.queries * 2, replace=False)
    q_ids = [int(i) for i in q_ids if titles[i] and len(titles[i]) > 20][: args.queries]
    pool_ids = np.unique(np.concatenate([
        rng.choice(n_total, size=args.pool, replace=False), np.asarray(q_ids)
    ]))
    log(f"{len(q_ids)} queries against a {len(pool_ids):,}-document pool")

    pool = torch.from_numpy(np.asarray(vectors[pool_ids], dtype=np.float32)).cuda()
    where = {int(nid): i for i, nid in enumerate(pool_ids)}
    gold = torch.tensor([where[i] for i in q_ids], device="cuda")
    queries = [titles[i] for i in q_ids]

    for name, repo, load_as in (
        ("adhoc_query", "allenai/specter2_adhoc_query", "adhoc_query"),
        ("proximity  ", "allenai/specter2", "proximity"),
    ):
        try:
            tok, model = load_model(repo, load_as)
        except Exception as exc:  # noqa: BLE001 - report and continue to the fallback
            log(f"{name}: could not load ({type(exc).__name__}: {str(exc)[:90]})")
            continue
        t0 = time.time()
        qv = torch.from_numpy(encode(tok, model, queries)).cuda()
        scores = qv @ pool.T
        ranks = (scores > scores.gather(1, gold[:, None])).sum(1) + 1
        r = ranks.float().cpu().numpy()
        log(f"{name}  recall@1={np.mean(r <= 1):.3f}  @10={np.mean(r <= 10):.3f}  "
            f"@100={np.mean(r <= 100):.3f}  median rank={int(np.median(r))}  "
            f"({time.time() - t0:.0f}s)")
        del model, tok, qv
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
