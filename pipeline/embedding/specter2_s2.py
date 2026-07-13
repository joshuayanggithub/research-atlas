"""SPECTER2 embeddings fetched (precomputed) from Semantic Scholar.

Uses the ``/paper/batch`` endpoint (up to 500 ids/POST) requesting
``embedding.specter_v2`` (verified 768-dim). Papers are addressed by DOI (``DOI:...``)
or arXiv id (``ARXIV:...``); papers with neither, or not in S2's corpus, come back
uncovered so the dispatcher can fall back for them.

Rate limits: without a key the shared pool is ~1 req/s and returns 429s under load, so
we back off aggressively and cache raw responses on disk to make re-runs free.
"""

from __future__ import annotations

import hashlib
import json
import time

import httpx
import numpy as np
import polars as pl
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from tqdm import tqdm

from pipeline.common import log
from pipeline.config import CACHE_DIR
from pipeline.embedding.base import EmbeddingResult

BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"


class Specter2S2Backend:
    name = "specter2_s2"
    model = "specter_v2"

    def __init__(self, dim: int = 768, batch_size: int = 500, api_key: str | None = None):
        self.dim = dim
        self.batch_size = batch_size
        self.api_key = api_key
        self._headers = {"x-api-key": api_key} if api_key else {}
        self._cache = CACHE_DIR / "s2_specter2"
        self._cache.mkdir(parents=True, exist_ok=True)
        self._n_failed_batches = 0

    def _paper_id_for(self, doi: str | None, arxiv_id: str | None) -> str | None:
        if arxiv_id:
            return f"ARXIV:{arxiv_id}"
        if doi:
            return f"DOI:{doi}"
        return None

    def _cache_path(self, ids: list[str]):
        key = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:24]
        return self._cache / f"{key}.json"

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        wait=wait_exponential(multiplier=2, min=3, max=90),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    def _post_batch(self, client: httpx.Client, ids: list[str]) -> list:
        r = client.post(
            BATCH_URL,
            params={"fields": "paperId,externalIds,embedding.specter_v2"},
            json={"ids": ids},
            headers=self._headers,
            timeout=90,
        )
        if r.status_code in (429, 500, 502, 503, 504):
            r.raise_for_status()
        r.raise_for_status()
        return r.json()

    def embed(self, corpus: pl.DataFrame) -> EmbeddingResult:
        n = corpus.height
        vectors = np.zeros((n, self.dim), dtype=np.float32)
        covered = np.zeros(n, dtype=bool)

        # Build (row_index -> s2 id) for rows that have an addressable id.
        rows = corpus.select(["node_id", "doi", "arxiv_id"]).to_dicts()
        addressable: list[tuple[int, str]] = []
        for i, row in enumerate(rows):
            sid = self._paper_id_for(row["doi"], row["arxiv_id"])
            if sid:
                addressable.append((i, sid))

        log.info(f"S2 SPECTER2: {len(addressable)}/{n} rows have DOI/arXiv id")
        if not addressable:
            return EmbeddingResult(vectors, covered, self.name, self.model)

        with httpx.Client() as client:
            for start in tqdm(range(0, len(addressable), self.batch_size),
                              desc="  s2 batch", unit="batch"):
                chunk = addressable[start:start + self.batch_size]
                idx = [i for i, _ in chunk]
                ids = [s for _, s in chunk]

                cache_path = self._cache_path(ids)
                if cache_path.exists():
                    data = json.loads(cache_path.read_text())
                else:
                    try:
                        data = self._post_batch(client, ids)
                    except Exception as e:  # noqa: BLE001
                        # A non-retryable error (e.g. 400 from a malformed id) must not
                        # abort the whole fetch — skip this batch; uncovered rows fall
                        # back to local embedding in s03.
                        self._n_failed_batches += 1
                        log.warn(f"S2 batch {start // self.batch_size} failed ({e}); "
                                 f"skipping {len(ids)} rows")
                        continue
                    cache_path.write_text(json.dumps(data))
                    time.sleep(1.0)  # be polite to the shared pool

                for row_i, rec in zip(idx, data):
                    if not rec:
                        continue
                    emb = rec.get("embedding") or {}
                    vec = emb.get("vector")
                    if vec and len(vec) == self.dim:
                        vectors[row_i] = np.asarray(vec, dtype=np.float32)
                        covered[row_i] = True

        res = EmbeddingResult(vectors, covered, self.name, self.model)
        log.info(f"S2 SPECTER2 coverage: {res.coverage:.1%}")
        return res
