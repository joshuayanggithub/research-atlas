"""Measure query-time retrieval: recall, ANN approximation loss, and latency.

Three numbers an interviewer will ask for, none of which a demo answers:

1. **Recall on a task with real ground truth.** A paper's TITLE is the query; its own document
   vector is the answer. No annotation needed, thousands of queries available.
2. **ANN vs exact.** hnswlib is approximate. Comparing its top-k against an exact GPU matmul
   over all 3.13M vectors quantifies what the approximation costs, per `ef`.
3. **Latency.** Median and p95 per query, at each `ef`.

Also ablates the query encoder: SPECTER2's adhoc_query adapter against the proximity adapter
the documents themselves were embedded with.

    uv run python tools/eval_retrieval.py --queries 500
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import polars as pl
import torch

from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR

VECTORS = INTERIM_DIR / "embeddings.npy"
INDEX = INTERIM_DIR / "query_index.hnsw"

ENCODERS = {
    "adhoc_query": ("allenai/specter2_adhoc_query", "adhoc_query"),
    "proximity": ("allenai/specter2", "proximity"),
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_encoder(repo: str, load_as: str):
    from adapters import AutoAdapterModel
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained("allenai/specter2_base")
    model = AutoAdapterModel.from_pretrained("allenai/specter2_base")
    model.load_adapter(repo, source="hf", load_as=load_as, set_active=True)
    return tok, model.eval().to("cuda")


@torch.no_grad()
def encode(tok, model, texts: list[str], batch: int = 64) -> np.ndarray:
    out = []
    for i in range(0, len(texts), batch):
        enc = tok(texts[i:i + batch], padding=True, truncation=True,
                  return_tensors="pt", max_length=512).to("cuda")
        out.append(model(**enc).last_hidden_state[:, 0, :].float().cpu().numpy())
    v = np.concatenate(out).astype(np.float32)
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


@torch.no_grad()
def exact_topk(qv: np.ndarray, vectors, k: int, block: int = 400_000) -> np.ndarray:
    """Exact top-k by brute force, streamed in blocks. The ANN ground truth."""
    q = torch.from_numpy(qv).cuda()
    best_score = torch.full((q.shape[0], k), -2.0, device="cuda")
    best_idx = torch.zeros((q.shape[0], k), dtype=torch.long, device="cuda")
    for start in range(0, vectors.shape[0], block):
        stop = min(start + block, vectors.shape[0])
        chunk = torch.from_numpy(np.asarray(vectors[start:stop], dtype=np.float32)).cuda()
        s = q @ chunk.T
        cat_s = torch.cat([best_score, s], dim=1)
        cat_i = torch.cat([best_idx, torch.arange(start, stop, device="cuda")
                           .expand(q.shape[0], -1)], dim=1)
        best_score, order = cat_s.topk(k, dim=1)
        best_idx = cat_i.gather(1, order)
        del chunk, s, cat_s, cat_i
    return best_idx.cpu().numpy()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=500)
    ap.add_argument("--ef", type=int, nargs="+", default=[100, 200, 400, 800])
    ap.add_argument("--skip-exact", action="store_true")
    args = ap.parse_args()

    import hnswlib

    vectors = np.load(VECTORS, mmap_mode="r")
    n, dim = vectors.shape
    corpus = pl.read_parquet(CORPUS_ACTIVE, columns=["title"])
    titles = corpus["title"].to_list()

    rng = np.random.default_rng(0)
    cand = rng.choice(n, size=args.queries * 3, replace=False)
    gold = [int(i) for i in cand if titles[i] and len(titles[i]) > 20][: args.queries]
    queries = [titles[i] for i in gold]
    gold_arr = np.asarray(gold)
    log(f"{len(gold)} title->paper queries over the full {n:,}-document index")

    index = hnswlib.Index(space="cosine", dim=dim)
    index.load_index(str(INDEX), max_elements=n)
    log(f"loaded {INDEX.name}")

    for enc_name, (repo, load_as) in ENCODERS.items():
        tok, model = load_encoder(repo, load_as)
        qv = encode(tok, model, queries)

        exact = None
        if not args.skip_exact:
            t0 = time.time()
            exact = exact_topk(qv, vectors, k=100)
            ehit = exact == gold_arr[:, None]
            log(f"[{enc_name}] EXACT (brute force over {n:,}, {time.time() - t0:.0f}s): "
                f"recall@1={ehit[:, :1].any(1).mean():.3f} "
                f"@10={ehit[:, :10].any(1).mean():.3f} @100={ehit.any(1).mean():.3f}")

        for ef in args.ef:
            # hnswlib silently raises ef to k, so an ef below the requested k measures nothing.
            index.set_ef(max(ef, 100))
            index.set_num_threads(1)  # per-query latency, not batch throughput
            lat = []
            got = []
            for i in range(len(queries)):
                t0 = time.perf_counter()
                ids, _ = index.knn_query(qv[i:i + 1], k=100)
                lat.append((time.perf_counter() - t0) * 1000)
                got.append(ids[0])
            got = np.asarray(got)

            hit = got == gold_arr[:, None]
            r1 = hit[:, :1].any(1).mean()
            r10 = hit[:, :10].any(1).mean()
            r100 = hit.any(1).mean()
            line = (f"[{enc_name:<11} ef={ef:>3}] recall@1={r1:.3f} @10={r10:.3f} @100={r100:.3f}"
                    f"  latency p50={np.percentile(lat, 50):.1f}ms p95={np.percentile(lat, 95):.1f}ms")
            if exact is not None:
                agree = np.mean([len(set(got[i][:10]) & set(exact[i][:10])) / 10.0
                                 for i in range(len(got))])
                line += f"  ANN/exact top-10 agreement={agree:.3f}"
            log(line)

        del model, tok
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
