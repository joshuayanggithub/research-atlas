"""Set the refusal threshold from measured data instead of guessing it.

A refusal gate with a hand-picked constant is theatre. This measures the top-1 cosine an
in-domain question gets against what an out-of-domain one gets, and reports where (or whether)
the two separate. If they overlap, the honest conclusion is that similarity alone cannot gate
refusal -- which is a finding, not a failure.

    uv run python tools/calibrate_refusal.py
"""
from __future__ import annotations

import numpy as np
import polars as pl
import torch

from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR

INDEX = INTERIM_DIR / "query_index.hnsw"
VECTORS = INTERIM_DIR / "embeddings.npy"

IN_DOMAIN = [
    "What approaches are used for depth reconstruction on vision-based tactile sensors?",
    "How do transformers handle long-range dependencies in sequence modelling?",
    "methods for dark matter detection in underground experiments",
    "quantum error correction with surface codes",
    "self-supervised pretraining for speech recognition",
    "graph neural networks for molecular property prediction",
    "gravitational wave detection from binary black hole mergers",
    "diffusion models for image generation",
    "federated learning under non-IID data",
    "topological insulators and edge states",
    "protein structure prediction from sequence",
    "reinforcement learning for robotic manipulation",
    "causal inference with instrumental variables",
    "superconductivity in twisted bilayer graphene",
    "retrieval augmented generation for question answering",
]
OUT_OF_DOMAIN = [
    "what is the best recipe for risotto",
    "how do I fix a leaking kitchen tap",
    "who won the 2018 world cup final",
    "best hiking trails near Seattle in autumn",
    "how to train a puppy not to bite",
    "cheap flights from London to Tokyo",
    "what time does the supermarket close on Sunday",
    "lyrics to a Taylor Swift song about summer",
    "how to change a car tyre in the rain",
    "tax deadline for self-employed people",
    "why is my houseplant turning yellow",
    "how to knit a scarf for beginners",
]


def main() -> None:
    import hnswlib
    from adapters import AutoAdapterModel
    from transformers import AutoTokenizer

    vectors = np.load(VECTORS, mmap_mode="r")
    n, dim = vectors.shape
    index = hnswlib.Index(space="cosine", dim=dim)
    index.load_index(str(INDEX), max_elements=n)
    index.set_ef(800)

    tok = AutoTokenizer.from_pretrained("allenai/specter2_base")
    model = AutoAdapterModel.from_pretrained("allenai/specter2_base")
    model.load_adapter("allenai/specter2_adhoc_query", source="hf",
                       load_as="adhoc_query", set_active=True)
    model.eval().cuda()

    @torch.no_grad()
    def scores(texts: list[str]) -> tuple[np.ndarray, np.ndarray]:
        """Absolute top-1 cosine, and the MARGIN between top-1 and the tail of the top-100.

        SPECTER2 vectors are anisotropic -- everything lives in a narrow cone, so absolute
        cosine barely moves between a real question and nonsense. The margin asks a different
        question: does this query single anything out, or is the whole corpus equally close?
        """
        enc = tok(texts, padding=True, truncation=True, return_tensors="pt",
                  max_length=512).to("cuda")
        v = model(**enc).last_hidden_state[:, 0, :].float().cpu().numpy().astype(np.float32)
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
        _, dist = index.knn_query(v, k=100)
        sim = 1.0 - dist
        return sim[:, 0], sim[:, 0] - sim[:, 50:].mean(axis=1)

    def top1(texts: list[str]) -> np.ndarray:
        return scores(texts)[0]

    # Real paper titles are the strongest possible in-domain signal; questions are the realistic
    # case. Report both, because a threshold tuned on titles would be far too high for questions.
    titles = pl.read_parquet(CORPUS_ACTIVE, columns=["title"])["title"].to_list()
    rng = np.random.default_rng(0)
    sample = [titles[i] for i in rng.choice(len(titles), 400, replace=False)
              if titles[i] and len(titles[i]) > 20][:200]

    groups = {
        "paper titles (n=%d)" % len(sample): top1(sample),
        "research questions (n=%d)" % len(IN_DOMAIN): top1(IN_DOMAIN),
        "out-of-domain (n=%d)" % len(OUT_OF_DOMAIN): top1(OUT_OF_DOMAIN),
    }
    print(f"{'group':<28} {'min':>6} {'p05':>6} {'median':>7} {'p95':>6} {'max':>6}")
    print("-" * 64)
    for name, v in groups.items():
        print(f"{name:<28} {v.min():6.3f} {np.percentile(v, 5):6.3f} "
              f"{np.median(v):7.3f} {np.percentile(v, 95):6.3f} {v.max():6.3f}")

    print()
    print("MARGIN (top-1 minus mean of ranks 50-100) -- scale-free alternative")
    print(f"{'group':<28} {'min':>6} {'p05':>6} {'median':>7} {'p95':>6} {'max':>6}")
    print("-" * 64)
    margins = {}
    for name, texts in (("paper titles", sample), ("research questions", IN_DOMAIN),
                        ("out-of-domain", OUT_OF_DOMAIN)):
        m = scores(texts)[1]
        margins[name] = m
        print(f"{name:<28} {m.min():6.3f} {np.percentile(m, 5):6.3f} "
              f"{np.median(m):7.3f} {np.percentile(m, 95):6.3f} {m.max():6.3f}")
    mq, mo = margins["research questions"], margins["out-of-domain"]
    print(f"  margin separation: in-domain min {mq.min():.3f} vs OOD max {mo.max():.3f} "
          f"-> {'SEPARABLE, gap %.3f' % (mq.min() - mo.max()) if mo.max() < mq.min() else 'OVERLAP'}")

    q = groups["research questions (n=%d)" % len(IN_DOMAIN)]
    o = groups["out-of-domain (n=%d)" % len(OUT_OF_DOMAIN)]
    print()
    if o.max() < q.min():
        lo, hi = o.max(), q.min()
        print(f"SEPARABLE: out-of-domain max {lo:.3f} < in-domain min {hi:.3f}")
        print(f"  -> threshold {(lo + hi) / 2:.3f} (midpoint) rejects all OOD, keeps all in-domain")
    else:
        print(f"OVERLAP: out-of-domain max {o.max():.3f} >= in-domain min {q.min():.3f}")
        best, best_t = -1.0, None
        for t in np.arange(0.30, 0.90, 0.005):
            acc = ((q >= t).sum() + (o < t).sum()) / (len(q) + len(o))
            if acc > best:
                best, best_t = acc, t
            # Report the strictest threshold that still admits every in-domain question.
        safe = [t for t in np.arange(0.30, 0.90, 0.005) if (q >= t).all()]
        print(f"  -> best separating threshold {best_t:.3f} (accuracy {best:.3f})")
        if safe:
            t = max(safe)
            print(f"  -> strictest threshold admitting ALL in-domain: {t:.3f} "
                  f"(rejects {(o < t).sum()}/{len(o)} out-of-domain)")


if __name__ == "__main__":
    main()
