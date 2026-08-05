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

    def _paper_ids_for(
        self, doi: str | None, arxiv_id: str | None, mag_id: str | None
    ) -> list[str]:
        """S2 ids to try for one paper, best-addressing-route first.

        Returns several candidates because no single id resolves everything: arXiv is the
        most reliable when present, DOI covers most published work, and MAG recovers papers
        whose OpenAlex DOI is not in S2's index at all. "Attention Is All You Need" is the
        motivating case — OpenAlex gives it only an unusual DOI (unknown to S2) plus a MAG
        id that S2 resolves *with* a SPECTER2 vector, so a DOI-only lookup dropped it.
        """
        candidates = []
        if arxiv_id:
            candidates.append(f"ARXIV:{arxiv_id}")
        if doi:
            candidates.append(f"DOI:{doi}")
        if mag_id:
            candidates.append(f"MAG:{mag_id}")
        return candidates

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

    def _fetch_pass(
        self,
        client: httpx.Client,
        requests: list[tuple[int, str]],
        vectors: np.ndarray,
        covered: np.ndarray,
        arxiv_ids: list[str | None],
        desc: str,
    ) -> None:
        """Resolve one (row_index, s2_id) list, filling ``vectors``/``covered``/``arxiv_ids``
        in place. The S2 record carries ``externalIds.ArXiv`` alongside the vector, so we
        capture the arXiv id here for free (used by the frontend's figure preview)."""
        for start in tqdm(
            range(0, len(requests), self.batch_size), desc=desc, unit="batch"
        ):
            chunk = requests[start:start + self.batch_size]
            idx = [i for i, _ in chunk]
            ids = [s for _, s in chunk]

            cache_path = self._cache_path(ids)
            if cache_path.exists():
                data = json.loads(cache_path.read_text())
            else:
                try:
                    data = self._post_batch(client, ids)
                except Exception as e:  # noqa: BLE001
                    # A non-retryable error (e.g. 400 from a malformed id) must not abort
                    # the whole fetch — skip this batch; uncovered rows fall back to the
                    # next addressing pass, then to s03's configured policy.
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
                # Capture the arXiv id from the same record (present for arXiv-origin papers
                # regardless of which id we queried by), if not already known for this row.
                ext = rec.get("externalIds") or {}
                axid = ext.get("ArXiv") or ext.get("arXiv")
                if axid and not arxiv_ids[row_i]:
                    arxiv_ids[row_i] = str(axid)

    def embed(self, corpus: pl.DataFrame) -> EmbeddingResult:
        n = corpus.height
        vectors = np.zeros((n, self.dim), dtype=np.float32)
        covered = np.zeros(n, dtype=bool)
        # arXiv id per row, seeded from what the corpus already has, then enriched from S2
        # responses (which carry externalIds.ArXiv for arXiv-origin papers).
        arxiv_ids: list[str | None] = list(corpus["arxiv_id"].to_list())

        # Per row, the ordered list of S2 ids worth trying (arXiv, then DOI, then MAG).
        columns = ["node_id", "doi", "arxiv_id"]
        has_mag = "mag_id" in corpus.columns
        if has_mag:
            columns.append("mag_id")
        rows = corpus.select(columns).to_dicts()
        candidates: list[list[str]] = [
            self._paper_ids_for(
                row["doi"], row["arxiv_id"], row.get("mag_id") if has_mag else None
            )
            for row in rows
        ]

        n_addressable = sum(1 for c in candidates if c)
        max_routes = max((len(c) for c in candidates), default=0)
        log.info(f"S2 SPECTER2: {n_addressable}/{n} rows have an addressable id "
                 f"(arXiv/DOI{'/MAG' if has_mag else ''})")
        if not n_addressable:
            return EmbeddingResult(vectors, covered, self.name, self.model,
                                   arxiv_ids=arxiv_ids)

        # Successive passes: each pass tries the next-choice id, but ONLY for rows still
        # uncovered. A row with a DOI S2 does not index therefore still gets its MAG
        # attempt, instead of being written off as having no SPECTER2 vector.
        with httpx.Client() as client:
            for route in range(max_routes):
                requests = [
                    (i, candidates[i][route])
                    for i in range(n)
                    if not covered[i] and len(candidates[i]) > route
                ]
                if not requests:
                    continue
                before = int(covered.sum())
                self._fetch_pass(
                    client, requests, vectors, covered, arxiv_ids, f"  s2 pass{route + 1}"
                )
                gained = int(covered.sum()) - before
                log.info(f"  pass {route + 1}: {len(requests)} lookups -> "
                         f"+{gained} vectors (coverage {covered.mean():.1%})")

        n_arxiv = sum(1 for a in arxiv_ids if a)
        res = EmbeddingResult(vectors, covered, self.name, self.model, arxiv_ids=arxiv_ids)
        log.info(f"S2 SPECTER2 coverage: {res.coverage:.1%} | arXiv ids known: "
                 f"{n_arxiv}/{n} ({n_arxiv / n * 100:.0f}%)")
        return res
