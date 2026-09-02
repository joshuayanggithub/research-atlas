"""Local query-time semantic search over the corpus.

The map answers "what is near THIS paper" from precomputed neighbours. This answers "where is
the work on X" -- a natural-language query encoded into the same SPECTER2 space and searched
against the persisted hnswlib index.

Local only by design: the published site is static, and putting an inference endpoint behind it
would import a cost and abuse surface the static architecture exists to avoid.

    uv run uvicorn tools.query_service:app --port 8000
    curl -s localhost:8000/search -H 'content-type: application/json' \
         -d '{"q": "tactile sensor depth reconstruction", "k": 10}' | jq

Measured on this corpus (tools/eval_retrieval.py): the adhoc_query adapter reaches
recall@1 0.902 / @10 0.973 on title->paper, against 0.830 / 0.940 for the proximity adapter the
documents themselves were embedded with.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

import re

import httpx
import numpy as np
import polars as pl
import torch
from fastapi import FastAPI
from pydantic import BaseModel

from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR

VECTORS = INTERIM_DIR / "embeddings.npy"
INDEX = INTERIM_DIR / "query_index.hnsw"
# SPECTER2's query-side adapter. Validated against the proximity doc vectors rather than
# assumed -- see tools/validate_query_adapter.py.
QUERY_ADAPTER = "allenai/specter2_adhoc_query"
LLM_URL = "http://localhost:8001/v1/chat/completions"
LLM_MODEL = "Qwen/Qwen2.5-7B-Instruct-AWQ"
# Below this top-1 cosine, do not call the model at all.
#
# Calibrated, and the calibration is the interesting part (tools/calibrate_refusal.py):
# SPECTER2 vectors are anisotropic, so EVERYTHING scores high. Real research questions land at
# 0.796-0.876 and deliberate nonsense ("best recipe for risotto") still lands at 0.732-0.789 --
# a separation of 0.007. A scale-free margin signal (top-1 minus the mean of ranks 50-100) was
# tried as an alternative and separates WORSE: the two classes overlap completely.
#
# So this gate is set just below the observed in-domain floor: it never refuses a legitimate
# question, and catches most nonsense. It is deliberately NOT the only defence -- the prompt
# also instructs the model to refuse when the abstracts do not answer, and
# tools/eval_generation.py measures which of the two actually does the work.
REFUSAL_THRESHOLD = 0.78

STATE: dict = {}


def _load() -> None:
    import hnswlib
    from adapters import AutoAdapterModel
    from transformers import AutoTokenizer

    t0 = time.time()
    dim = np.load(VECTORS, mmap_mode="r").shape[1]
    n = np.load(VECTORS, mmap_mode="r").shape[0]
    index = hnswlib.Index(space="cosine", dim=dim)
    index.load_index(str(INDEX), max_elements=n)
    # ef=800, chosen from the measured sweep rather than the default.
    #
    # The query adapter's vectors sit slightly off the document manifold the HNSW graph was
    # built over, so they navigate it worse: at ef=100 only 80.3% of the exact top-10 is
    # recovered, against 96.0% for proximity-encoded queries. Raising ef closes that to 96.3%
    # and costs 0.7ms -> 4.4ms per query, which is nothing next to the ~30ms encode.
    index.set_ef(800)
    STATE["index"] = index

    tok = AutoTokenizer.from_pretrained("allenai/specter2_base")
    model = AutoAdapterModel.from_pretrained("allenai/specter2_base")
    model.load_adapter(QUERY_ADAPTER, source="hf", load_as="adhoc_query", set_active=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    STATE["tok"], STATE["model"], STATE["device"] = tok, model.eval().to(device), device

    frame = pl.read_parquet(
        CORPUS_ACTIVE, columns=["title", "abstract", "arxiv_id", "publication_date"]
    )
    STATE["titles"] = frame["title"].to_list()
    STATE["abstracts"] = frame["abstract"].fill_null("").to_list()
    # Document vectors, memory-mapped: the support check scores a generated sentence against
    # the paper it cites, and 9 GB does not need to be resident to read 10 rows.
    STATE["vectors"] = np.load(VECTORS, mmap_mode="r")
    STATE["arxiv"] = frame["arxiv_id"].to_list()
    STATE["date"] = frame["publication_date"].cast(pl.Utf8).to_list()
    print(f"ready: {n:,} documents, device={device}, {time.time() - t0:.0f}s", flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _load()
    yield
    STATE.clear()


app = FastAPI(title="Research Atlas query service", lifespan=lifespan)


class Query(BaseModel):
    q: str
    k: int = 10


@torch.no_grad()
def embed_query(text: str) -> np.ndarray:
    enc = STATE["tok"]([text], padding=True, truncation=True, return_tensors="pt",
                       max_length=512).to(STATE["device"])
    # CLS pooling then L2 normalise -- identical to the document side (specter2_local.embed
    # plus s03's normalisation). A mismatch here silently corrupts every cosine.
    v = STATE["model"](**enc).last_hidden_state[:, 0, :].float().cpu().numpy().astype(np.float32)
    norm = np.linalg.norm(v, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return v / norm


@app.post("/search")
def search(query: Query) -> dict:
    t0 = time.perf_counter()
    qv = embed_query(query.q)
    encode_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    ids, distances = STATE["index"].knn_query(qv, k=min(query.k, 200))
    search_ms = (time.perf_counter() - t1) * 1000

    results = []
    for node_id, dist in zip(ids[0].tolist(), distances[0].tolist()):
        results.append({
            "node_id": node_id,
            # hnswlib returns cosine DISTANCE; the map speaks similarity.
            "score": round(1.0 - float(dist), 4),
            "title": STATE["titles"][node_id],
            "arxiv_id": STATE["arxiv"][node_id],
            "date": STATE["date"][node_id],
        })
    return {"query": query.q, "results": results,
            "timing_ms": {"encode": round(encode_ms, 1), "search": round(search_ms, 1)}}


class Ask(BaseModel):
    q: str
    k: int = 8


PROMPT = """You answer questions about scientific literature using ONLY the numbered abstracts \
below. Cite every claim with the abstract's number in square brackets, like [2]. If the \
abstracts do not contain the answer, say exactly: "The retrieved papers do not answer this."
Do not use knowledge from outside these abstracts. Be concise: at most 6 sentences.

{context}

Question: {question}
Answer:"""


@torch.no_grad()
def embed_texts(texts: list[str]) -> np.ndarray:
    enc = STATE["tok"](texts, padding=True, truncation=True, return_tensors="pt",
                       max_length=512).to(STATE["device"])
    v = STATE["model"](**enc).last_hidden_state[:, 0, :].float().cpu().numpy().astype(np.float32)
    norm = np.linalg.norm(v, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return v / norm


@app.post("/ask")
def ask(req: Ask) -> dict:
    """Retrieval-augmented answer with a refusal gate, citation verification and support scores.

    The three checks exist because "how do you know it isn't hallucinating?" has to be answered
    with numbers. They run on every request, not just in the eval.
    """
    t0 = time.perf_counter()
    qv = embed_query(req.q)
    ids, distances = STATE["index"].knn_query(qv, k=req.k)
    node_ids = ids[0].tolist()
    sims = [1.0 - float(d) for d in distances[0].tolist()]
    retrieve_ms = (time.perf_counter() - t0) * 1000

    papers = [{"n": i + 1, "node_id": nid, "score": round(sims[i], 4),
               "title": STATE["titles"][nid], "arxiv_id": STATE["arxiv"][nid]}
              for i, nid in enumerate(node_ids)]

    # RULE 1: refuse BEFORE generating. Weak retrieval cannot be rescued by prompting, and an
    # answer built from irrelevant abstracts is worse than no answer.
    if not sims or max(sims) < REFUSAL_THRESHOLD:
        return {"query": req.q, "refused": True,
                "reason": f"top-1 similarity {max(sims) if sims else 0:.3f} "
                          f"< threshold {REFUSAL_THRESHOLD}",
                "papers": papers, "timing_ms": {"retrieve": round(retrieve_ms, 1)}}

    context = "\n\n".join(
        f"[{i + 1}] {STATE['titles'][nid]}\n{STATE['abstracts'][nid][:1200]}"
        for i, nid in enumerate(node_ids)
    )
    t1 = time.perf_counter()
    reply = httpx.post(LLM_URL, timeout=180.0, json={
        "model": LLM_MODEL, "temperature": 0.0, "max_tokens": 400,
        "messages": [{"role": "user",
                      "content": PROMPT.format(context=context, question=req.q)}],
    })
    reply.raise_for_status()
    answer = reply.json()["choices"][0]["message"]["content"].strip()
    generate_ms = (time.perf_counter() - t1) * 1000

    # RULE 2: every [n] must refer to a retrieved abstract. A citation to [12] when 8 were
    # supplied is a fabricated reference, and it is mechanically detectable.
    cited = sorted({int(m) for m in re.findall(r"\[(\d+)\]", answer)})
    valid = [c for c in cited if 1 <= c <= len(node_ids)]
    invalid = [c for c in cited if c not in valid]

    # RULE 3: does the cited paper actually support the sentence? Cosine between the generated
    # sentence and the cited paper's document vector. Not entailment -- a cheap proxy that
    # catches a citation pointing at an unrelated paper.
    support = []
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if "[" in s]
    if sentences:
        sent_vecs = embed_texts(sentences)
        for sent, vec in zip(sentences, sent_vecs):
            for c in {int(m) for m in re.findall(r"\[(\d+)\]", sent)}:
                if 1 <= c <= len(node_ids):
                    doc = np.asarray(STATE["vectors"][node_ids[c - 1]], dtype=np.float32)
                    support.append({"n": c, "cosine": round(float(vec @ doc), 3),
                                    "sentence": sent[:90]})

    scores = [s["cosine"] for s in support]
    return {
        "query": req.q, "refused": False, "answer": answer, "papers": papers,
        "citations": {"cited": cited, "invalid": invalid,
                      "validity_rate": round(len(valid) / len(cited), 3) if cited else None,
                      "uncited_sentences": sum(
                          1 for s in re.split(r"(?<=[.!?])\s+", answer) if s.strip() and "[" not in s)},
        "support": {"mean_cosine": round(float(np.mean(scores)), 3) if scores else None,
                    "min_cosine": round(float(np.min(scores)), 3) if scores else None,
                    "per_citation": support},
        "timing_ms": {"retrieve": round(retrieve_ms, 1), "generate": round(generate_ms, 1)},
    }


@app.get("/health")
def health() -> dict:
    return {"documents": len(STATE.get("titles", [])), "device": STATE.get("device")}
