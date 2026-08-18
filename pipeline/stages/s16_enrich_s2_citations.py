"""Optional S2AG reconciliation for citation data materialized from OpenAlex.

arXiv remains the paper-identity, date, title, and abstract spine. This stage resolves only
the corpus's arXiv ids to S2 paper hashes in cached 500-id batches, streams the compact S2AG
``paper-ids`` crosswalk to obtain S2 corpus ids, then downloads and scans each bulk S2AG
``citations`` shard before deleting it. It does not crawl citation endpoints.

The streaming pass has two intentionally distinct results:

* ``cited_by_count`` is the number of every S2 citation whose target is a matched corpus
  paper, including citations from papers outside Research Atlas. This is the user-facing
  count as of the recorded S2AG snapshot.
* ``referenced_works`` contains only citations whose source and target are both in the
  corpus, encoded as canonical ``paper_id`` values. That keeps the browser graph finite
  and lets s09 resolve its two endpoints to dense node ids.

The original OpenAlex citation fields remain sidecar provenance; they are never added to
S2 counts. The stage is resumable at both the id-resolution and dataset-file levels, and
does not alter the corpus until a complete citation scan has succeeded.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
import polars as pl
import requests
from tqdm import tqdm

from pipeline.common import log
from pipeline.common.io import read_json, write_json
from pipeline.config import (
    CACHE_DIR, CORPUS_ACTIVE, CORPUS_FULL, INTERIM_DIR, RAW_DIR, Config, ensure_dirs, load_config,
)

S2_RELEASE_URL = "https://api.semanticscholar.org/datasets/v1/release"
S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"

S2_ROOT = RAW_DIR / "s2ag"
MATCHES_OUT = INTERIM_DIR / "s2_citation_matches.parquet"
STATS_OUT = INTERIM_DIR / "s2_citation_stats.parquet"
META_OUT = INTERIM_DIR / "s2_citation_meta.json"


class S2RequestError(RuntimeError):
    """A Semantic Scholar request could not complete after compliant retries."""


class S2Client:
    """Small sequential S2 client that enforces the configured cumulative API limit."""

    def __init__(self, api_key: str, *, min_interval: float, max_retries: int):
        if not api_key:
            raise ValueError("S2_API_KEY is required for Semantic Scholar citation ingestion")
        self.headers = {"x-api-key": api_key}
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.last_request_at = 0.0
        self.session = requests.Session()

    def close(self) -> None:
        self.session.close()

    def _wait_turn(self) -> None:
        wait = self.min_interval - (time.monotonic() - self.last_request_at)
        if wait > 0:
            time.sleep(wait)

    def _request_json(self, method: str, url: str, **kwargs) -> dict | list:
        """Make one API request at a time and back off on S2's rate-limit responses."""
        last_error = "unknown error"
        for attempt in range(self.max_retries + 1):
            self._wait_turn()
            self.last_request_at = time.monotonic()
            try:
                response = self.session.request(
                    method, url, headers=self.headers, timeout=120, **kwargs
                )
            except requests.RequestException as exc:
                last_error = str(exc)
            else:
                if response.ok:
                    return response.json()
                last_error = f"HTTP {response.status_code}"
                # Do not retry client errors other than the explicit rate limiter.
                if response.status_code not in {429, 500, 502, 503, 504}:
                    raise S2RequestError(f"S2 request {method} {url} failed: {last_error}")
            if attempt < self.max_retries:
                delay = max(self.min_interval, min(120.0, self.min_interval * (2 ** attempt)))
                log.warn(f"S2 {last_error}; retrying in {delay:.1f}s")
                time.sleep(delay)
        raise S2RequestError(f"S2 request {method} {url} failed after retries: {last_error}")

    def latest_release(self) -> dict:
        # The public release manifest does not require a key. Avoid consuming the user's
        # constrained key quota for it; authenticated calls begin with the next request.
        response = self.session.get(f"{S2_RELEASE_URL}/latest", timeout=120)
        response.raise_for_status()
        return response.json()

    def dataset_download_urls(self, release_id: str, name: str) -> list[str]:
        data = self._request_json("GET", f"{S2_RELEASE_URL}/{release_id}/dataset/{name}")
        if not isinstance(data, dict):
            raise S2RequestError(f"unexpected S2 {name!r} dataset response")
        # The documented API calls these ``files``. Accept the alternate names used by older
        # S2AG releases so a pinned historical rebuild stays usable.
        raw_urls = data.get("files") or data.get("download_urls") or data.get("download_links")
        if not isinstance(raw_urls, list) or not raw_urls:
            raise S2RequestError(f"S2 {name!r} dataset response contained no download files")
        urls: list[str] = []
        for item in raw_urls:
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, dict):
                url = item.get("url") or item.get("download_url")
                if isinstance(url, str):
                    urls.append(url)
        if not urls:
            raise S2RequestError(f"S2 {name!r} dataset response contained no usable URLs")
        return urls

    def paper_batch(self, ids: list[str], fields: str = "paperId") -> list[dict | None]:
        result = self._request_json(
            "POST", S2_BATCH_URL,
            params={"fields": fields}, json={"ids": ids},
        )
        if not isinstance(result, list):
            raise S2RequestError("unexpected S2 paper/batch response")
        return result

def _cache_path(arxiv_ids: list[str], namespace: str = "s2_citation_resolution") -> Path:
    digest = hashlib.sha256("\n".join(arxiv_ids).encode()).hexdigest()[:24]
    path = CACHE_DIR / namespace / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


COUNTS_OUT = INTERIM_DIR / "s2_authoritative_counts.parquet"
COUNT_FIELDS = "paperId,citationCount,referenceCount"


def fetch_authoritative_counts(
    corpus: pl.DataFrame, client: S2Client, batch_size: int = 500,
) -> dict[str, tuple[int, int]]:
    """Return ``arxiv_id -> (citationCount, referenceCount)`` straight from S2's paper records.

    The bulk ``citations`` dataset is the right source for the EDGE GRAPH (its pairs are
    deduplicated into ``referenced_works``), but it is the wrong source for the displayed
    totals: ``_stream_citations`` accumulates ``incoming``/``outgoing`` with ``+=`` and never
    deduplicates a repeated citing->cited pair, which measured ~1.68x higher than S2's own
    number across a 12-paper sample (e.g. 104 vs 69 for arXiv 2512.24601). This endpoint
    returns the same figure semanticscholar.org shows, for 500 papers per request, so the
    totals can be corrected in minutes instead of re-running the ~19h shard scan.
    """
    arxiv_ids = list(dict.fromkeys(str(v) for v in corpus["arxiv_id"].to_list() if v))
    counts: dict[str, tuple[int, int]] = {}
    for start in tqdm(range(0, len(arxiv_ids), batch_size), desc="s2 authoritative counts", unit="batch"):
        chunk = arxiv_ids[start:start + batch_size]
        cache_path = _cache_path(chunk, namespace="s2_authoritative_counts")
        if cache_path.exists():
            records = read_json(cache_path)
        else:
            records = client.paper_batch([f"ARXIV:{a}" for a in chunk], fields=COUNT_FIELDS)
            write_json(records, cache_path)
        if not isinstance(records, list) or len(records) != len(chunk):
            raise S2RequestError("S2 paper/batch returned an unexpected record count")
        for arxiv_id, record in zip(chunk, records):
            if not isinstance(record, dict):
                continue  # S2 has no record for this arXiv id; leave it unavailable
            cites, refs = record.get("citationCount"), record.get("referenceCount")
            if isinstance(cites, int) and isinstance(refs, int):
                counts[arxiv_id] = (cites, refs)
    return counts


def apply_authoritative_counts(counts: dict[str, tuple[int, int]]) -> int:
    """Replace the inflated bulk totals in both corpora. The edge graph is left untouched."""
    updated = 0
    for path in (CORPUS_FULL, CORPUS_ACTIVE):
        if not path.exists():
            continue
        df = pl.read_parquet(path)
        cites, refs, available = [], [], []
        for arxiv_id, prior_available in zip(
            df["arxiv_id"].to_list(),
            df["s2_citation_available"].to_list() if "s2_citation_available" in df.columns
            else [False] * df.height,
        ):
            hit = counts.get(str(arxiv_id)) if arxiv_id else None
            # No S2 record: keep the row unavailable rather than asserting a zero.
            cites.append(hit[0] if hit else None)
            refs.append(hit[1] if hit else None)
            available.append(bool(hit) or bool(prior_available))
        df = df.with_columns([
            pl.Series("s2_citation_count", cites, dtype=pl.Int32),
            pl.Series("s2_reference_count", refs, dtype=pl.Int32),
            pl.Series("s2_citation_available", available, dtype=pl.Boolean),
            # cited_by_count is what the browser renders; fall back to the existing value
            # (OpenAlex) for rows S2 could not resolve.
            pl.when(pl.Series(cites).is_not_null())
              .then(pl.Series(cites))
              .otherwise(pl.col("cited_by_count"))
              .cast(pl.Int32).alias("cited_by_count"),
        ])
        df.write_parquet(path)
        updated += 1
        log.info(f"applied authoritative S2 counts -> {path}")
    return updated


def _resolve_s2_ids(corpus: pl.DataFrame, client: S2Client, batch_size: int) -> dict[str, str]:
    """Return ``arxiv_id -> S2 paper hash`` using durable, paced batch-response caching."""
    arxiv_ids = list(dict.fromkeys(str(value) for value in corpus["arxiv_id"].to_list() if value))
    resolved: dict[str, str] = {}
    for start in tqdm(range(0, len(arxiv_ids), batch_size), desc="s2 id resolution", unit="batch"):
        chunk = arxiv_ids[start:start + batch_size]
        cache_path = _cache_path(chunk)
        if cache_path.exists():
            records = read_json(cache_path)
        else:
            records = client.paper_batch([f"ARXIV:{arxiv_id}" for arxiv_id in chunk])
            write_json(records, cache_path)
        if not isinstance(records, list) or len(records) != len(chunk):
            raise S2RequestError("S2 paper/batch returned an unexpected record count")
        for arxiv_id, record in zip(chunk, records):
            paper_id = record.get("paperId") if isinstance(record, dict) else None
            if isinstance(paper_id, str) and paper_id:
                resolved[arxiv_id] = paper_id
    return resolved


def _download_shard(
    url: str, destination: Path, index: int, *, timeout: float, label: str,
) -> Path:
    """Download one presigned S2 shard atomically; caller deletes it after streaming."""
    destination.mkdir(parents=True, exist_ok=True)
    suffix = ".jsonl.gz" if ".gz" in url.lower() else ".jsonl"
    out = destination / f"{label}-{index:03d}{suffix}"
    part = out.with_suffix(out.suffix + ".part")
    if part.exists():
        part.unlink()
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with part.open("wb") as handle:
            for block in response.iter_content(chunk_size=1 << 20):
                if block:
                    handle.write(block)
    part.replace(out)
    return out


def _citation_rows(paths: Iterable[Path]) -> Iterator[dict]:
    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON in {path} line {line_no}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"non-object citation record in {path} line {line_no}")
                yield row


def _citation_endpoints(row: dict) -> tuple[str | None, str | None]:
    """Read current S2AG corpus-id names plus historical paper-id names.

    The live dataset uses compact ``citingcorpusid``/``citedcorpusid`` fields. Older pinned
    S2AG releases used paper-id spellings, so accepting those preserves reproducibility.
    """
    source = (
        row.get("citingcorpusid") or row.get("citingCorpusId") or
        row.get("citingPaperId") or row.get("citing_paper_id")
    )
    target = (
        row.get("citedcorpusid") or row.get("citedCorpusId") or
        row.get("citedPaperId") or row.get("cited_paper_id")
    )
    return (
        str(source) if source is not None else None,
        str(target) if target is not None else None,
    )


def _scan_paper_ids(s2_paper_ids: set[str], paths: Iterable[Path]) -> dict[str, str]:
    """Select only our paper-hash→S2-corpus-id mappings from the compact bulk crosswalk."""
    found: dict[str, str] = {}
    for row in _citation_rows(paths):
        paper_id = row.get("paperid") or row.get("paperId") or row.get("sha")
        corpus_id = row.get("corpusid") or row.get("corpusId")
        if paper_id is None or corpus_id is None:
            continue
        paper_id_text = str(paper_id)
        if paper_id_text in s2_paper_ids:
            found[paper_id_text] = str(corpus_id)
            if len(found) == len(s2_paper_ids):
                break
    return found


def _stream_paper_id_crosswalk(
    urls: list[str], s2_paper_ids: set[str], destination: Path, *, timeout: float,
) -> dict[str, str]:
    """Download/scan/delete paper-id shards, retaining only our small crosswalk."""
    found: dict[str, str] = {}
    for index, url in enumerate(tqdm(urls, desc="stream S2 paper-id crosswalk", unit="file")):
        path = _download_shard(url, destination, index, timeout=timeout, label="paper-ids")
        try:
            found.update(_scan_paper_ids(s2_paper_ids - set(found), [path]))
        finally:
            path.unlink(missing_ok=True)
        if len(found) == len(s2_paper_ids):
            break
    return found


def _scan_citations(
    corpus: pl.DataFrame, arxiv_to_corpus_id: dict[str, str], paths: Iterable[Path],
) -> tuple[np.ndarray, np.ndarray, list[list[str]], np.ndarray, np.ndarray, int]:
    """Stream S2AG edges and return global counts plus internal graph endpoints.

    No external edges are retained. Counts, however, are incremented before that graph-only
    restriction, so a corpus paper cited by an external work still receives its real S2
    snapshot count.
    """
    node_ids = corpus["node_id"].to_list()
    paper_ids = corpus["paper_id"].to_list()
    arxiv_ids = corpus["arxiv_id"].to_list()
    corpus_id_to_index = {
        arxiv_to_corpus_id[arxiv_id]: index
        for index, arxiv_id in enumerate(arxiv_ids)
        if arxiv_id and arxiv_id in arxiv_to_corpus_id
    }
    incoming = np.zeros(corpus.height, dtype=np.int32)
    outgoing = np.zeros(corpus.height, dtype=np.int32)
    refs: list[list[str]] = [[] for _ in range(corpus.height)]
    src_nodes: list[int] = []
    dst_nodes: list[int] = []
    # S2AG citations are expected to be unique. Deduplicating only internal pairs is cheap
    # at this corpus scale and guarantees the browser never sees duplicate arrows.
    seen_internal: set[int] = set()
    scanned = 0

    for row in _citation_rows(paths):
        scanned += 1
        source_corpus_id, target_corpus_id = _citation_endpoints(row)
        source_i = corpus_id_to_index.get(source_corpus_id) if source_corpus_id else None
        target_i = corpus_id_to_index.get(target_corpus_id) if target_corpus_id else None
        if source_i is not None:
            outgoing[source_i] += 1
        if target_i is not None:
            incoming[target_i] += 1
        if source_i is None or target_i is None or source_i == target_i:
            continue
        packed = (int(node_ids[source_i]) << 32) | int(node_ids[target_i])
        if packed in seen_internal:
            continue
        seen_internal.add(packed)
        src_nodes.append(int(node_ids[source_i]))
        dst_nodes.append(int(node_ids[target_i]))
        refs[source_i].append(str(paper_ids[target_i]))

    return (
        np.asarray(src_nodes, dtype=np.int32),
        np.asarray(dst_nodes, dtype=np.int32), refs, incoming, outgoing, scanned,
    )


def _stream_citations(
    corpus: pl.DataFrame,
    arxiv_to_corpus_id: dict[str, str],
    urls: list[str],
    destination: Path,
    *,
    timeout: float,
    client: S2Client | None = None,
    release_id: str | None = None,
) -> tuple[np.ndarray, np.ndarray, list[list[str]], np.ndarray, np.ndarray, int]:
    """Download/scan/delete each large citation shard, retaining only Atlas-derived data."""
    incoming = np.zeros(corpus.height, dtype=np.int32)
    outgoing = np.zeros(corpus.height, dtype=np.int32)
    src_parts: list[np.ndarray] = []
    dst_parts: list[np.ndarray] = []
    scanned = 0
    for index, url in enumerate(tqdm(urls, desc="stream S2 citations", unit="file")):
        # Dataset API links are presigned and can occasionally be invalidated while a long
        # scan is running. Refresh the manifest and retry the one failed ordinal; completed
        # shards remain discarded, so the pass is still bounded in disk use.
        for refresh_attempt in range(3):
            try:
                path = _download_shard(
                    url, destination, index, timeout=timeout, label="citations"
                )
                break
            except requests.HTTPError as exc:
                if client is None or release_id is None or refresh_attempt == 2:
                    raise
                refreshed_urls = client.dataset_download_urls(release_id, "citations")
                if len(refreshed_urls) != len(urls):
                    raise S2RequestError(
                        "S2 citations manifest changed shard count during a running scan"
                    ) from exc
                url = refreshed_urls[index]
                log.warn(
                    f"citation shard {index + 1}/{len(urls)} download failed; "
                    "refreshed S2 dataset URL and retrying"
                )
        try:
            src, dst, _refs, shard_incoming, shard_outgoing, shard_scanned = _scan_citations(
                corpus, arxiv_to_corpus_id, [path]
            )
        finally:
            path.unlink(missing_ok=True)
        incoming += shard_incoming
        outgoing += shard_outgoing
        src_parts.append(src)
        dst_parts.append(dst)
        scanned += shard_scanned

    src_all = np.concatenate(src_parts) if src_parts else np.empty(0, dtype=np.int32)
    dst_all = np.concatenate(dst_parts) if dst_parts else np.empty(0, dtype=np.int32)
    # The bulk graph is pair-unique, but protect the emitted browser graph from an accidental
    # duplicate across release shards. Reconstruct refs from these final endpoints so they
    # exactly agree with s09's graph input.
    row_by_node = {int(node_id): row for row, node_id in enumerate(corpus["node_id"].to_list())}
    paper_ids = corpus["paper_id"].to_list()
    final_src: list[int] = []
    final_dst: list[int] = []
    refs: list[list[str]] = [[] for _ in range(corpus.height)]
    seen: set[int] = set()
    for source, target in zip(src_all.tolist(), dst_all.tolist()):
        packed = (int(source) << 32) | int(target)
        if packed in seen:
            continue
        seen.add(packed)
        final_src.append(source)
        final_dst.append(target)
        refs[row_by_node[source]].append(str(paper_ids[row_by_node[target]]))
    return (
        np.asarray(final_src, dtype=np.int32),
        np.asarray(final_dst, dtype=np.int32), refs, incoming, outgoing, scanned,
    )


def _materialize(
    corpus: pl.DataFrame,
    arxiv_to_corpus_id: dict[str, str],
    incoming: np.ndarray,
    outgoing: np.ndarray,
    refs: list[list[str]],
    *, release_id: str,
) -> tuple[pl.DataFrame, float]:
    arxiv_ids = corpus["arxiv_id"].to_list()
    corpus_ids = [arxiv_to_corpus_id.get(arxiv_id) if arxiv_id else None for arxiv_id in arxiv_ids]
    available = [corpus_id is not None for corpus_id in corpus_ids]
    coverage = sum(available) / corpus.height if corpus.height else 0.0
    # Preserve an existing provider value for the unresolved minority; it is marked
    # unavailable separately and never presented as an S2 count by the frontend.
    prior_counts = corpus["cited_by_count"].to_list()
    canonical_counts = [int(incoming[i]) if available[i] else int(prior_counts[i] or 0)
                        for i in range(corpus.height)]
    return corpus.with_columns([
        pl.Series("s2_corpus_id", corpus_ids, dtype=pl.String),
        pl.Series("s2_citation_count", incoming.tolist(), dtype=pl.Int32),
        pl.Series("s2_reference_count", outgoing.tolist(), dtype=pl.Int32),
        pl.Series("s2_citation_available", available, dtype=pl.Boolean),
        pl.Series("s2_citation_snapshot", [release_id] * corpus.height, dtype=pl.String),
        pl.Series("cited_by_count", canonical_counts, dtype=pl.Int32),
        pl.Series("referenced_works", refs, dtype=pl.List(pl.String)),
    ]), coverage


# Columns this stage owns. ``cited_by_count``/``referenced_works`` are the canonical values the
# frontend reads; the ``s2_*`` columns are provenance kept alongside OpenAlex's.
S2_CITATION_COLUMNS = [
    "cited_by_count", "referenced_works",
    "s2_corpus_id", "s2_citation_count", "s2_reference_count",
    "s2_citation_available", "s2_citation_snapshot",
]


def update_active(enriched_full: pl.DataFrame | None = None) -> bool:
    """Propagate citation columns from the full corpus into the embedded active subset.

    ``s03_embed`` compacts the full corpus into ``CORPUS_ACTIVE``, and every downstream stage
    (edges, neighbors, indexes, emit) reads the ACTIVE corpus — so writing only ``CORPUS_FULL``
    leaves the shipped bundle on the previous provider's counts. This mirrors
    ``s16_apply_openalex_citations._update_active`` so an S2 scan reaches the browser without
    re-running the embedding stage. Returns True when the active corpus was rewritten.
    """
    if not CORPUS_ACTIVE.exists() or CORPUS_ACTIVE == CORPUS_FULL:
        return False
    full = enriched_full if enriched_full is not None else pl.read_parquet(CORPUS_FULL)
    missing = [c for c in S2_CITATION_COLUMNS if c not in full.columns]
    if missing:
        raise RuntimeError(f"full corpus is missing S2 citation columns: {', '.join(missing)}")
    active = pl.read_parquet(CORPUS_ACTIVE)
    if not set(active["paper_id"].to_list()) <= set(full["paper_id"].to_list()):
        raise RuntimeError("active corpus is not a subset of the enriched full corpus")
    replacements = full.select(["paper_id", *S2_CITATION_COLUMNS])
    active = active.drop([c for c in S2_CITATION_COLUMNS if c in active.columns])
    active.join(replacements, on="paper_id", how="left").sort("node_id").write_parquet(CORPUS_ACTIVE)
    log.info(f"propagated S2 citation columns -> {CORPUS_ACTIVE}")
    return True


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s16_enrich_s2_citations")
    settings = cfg.semantic_scholar_citations
    if not settings.enabled:
        log.info("disabled by config; preserving existing citation fields")
        return str(META_OUT)
    if not CORPUS_FULL.exists():
        raise FileNotFoundError(f"corpus missing: {CORPUS_FULL}; run s02 first")

    corpus = pl.read_parquet(CORPUS_FULL)
    if "arxiv_id" not in corpus.columns:
        raise ValueError("S2 citation enrichment requires an arXiv-spine corpus")

    client = S2Client(
        cfg.secrets.s2_api_key or "",
        min_interval=settings.min_request_interval,
        max_retries=settings.max_retries,
    )
    try:
        release = client.latest_release() if settings.release == "latest" else None
        release_id = str(release["release_id"]) if release else settings.release
        if not release_id:
            raise S2RequestError("S2 release did not contain release_id")
        log.info(f"S2AG release: {release_id}")
        arxiv_to_s2 = _resolve_s2_ids(corpus, client, settings.resolve_batch_size)
        log.info(f"S2 paper-id coverage: {len(arxiv_to_s2)}/{corpus.height}")

        paper_id_urls = client.dataset_download_urls(release_id, "paper-ids")
        s2_to_corpus_id = _stream_paper_id_crosswalk(
            paper_id_urls,
            set(arxiv_to_s2.values()),
            S2_ROOT / release_id / "_stream",
            timeout=settings.download_timeout,
        )
        arxiv_to_corpus_id = {
            arxiv_id: s2_to_corpus_id[paper_id]
            for arxiv_id, paper_id in arxiv_to_s2.items()
            if paper_id in s2_to_corpus_id
        }
        log.info(
            f"S2 corpus-id coverage: {len(arxiv_to_corpus_id)}/{corpus.height} "
            f"({len(s2_to_corpus_id)}/{len(arxiv_to_s2)} resolved hashes)"
        )

        citation_urls = client.dataset_download_urls(release_id, "citations")
        if settings.max_citation_shards:
            citation_urls = citation_urls[:settings.max_citation_shards]
            log.warn(
                f"development cap: scanning {len(citation_urls)} citation shards; "
                "counts are incomplete and will not be materialized as canonical"
            )
        src, dst, refs, incoming, outgoing, scanned = _stream_citations(
            corpus,
            arxiv_to_corpus_id,
        citation_urls,
        S2_ROOT / release_id / "_stream",
        timeout=settings.download_timeout,
        client=client,
        release_id=release_id,
    )
    finally:
        client.close()

    if settings.max_citation_shards:
        raise RuntimeError(
            "max_citation_shards is a test-only partial scan; set it to 0 before canonical "
            "citation materialization"
        )

    enriched, coverage = _materialize(
        corpus, arxiv_to_corpus_id, incoming, outgoing, refs, release_id=release_id
    )
    # Commit all dependent artifacts only after the complete scan. A cancelled/failed run
    # leaves the previous corpus and graph intact and can reuse downloaded shards next time.
    enriched.write_parquet(CORPUS_FULL)
    update_active(enriched)
    pl.DataFrame({
        "node_id": corpus["node_id"],
        "arxiv_id": corpus["arxiv_id"],
        "s2_paper_id": [arxiv_to_s2.get(value) if value else None
                        for value in corpus["arxiv_id"].to_list()],
        "s2_corpus_id": [arxiv_to_corpus_id.get(value) if value else None
                         for value in corpus["arxiv_id"].to_list()],
        "s2_citation_available": [
            bool(value and value in arxiv_to_corpus_id) for value in corpus["arxiv_id"].to_list()
        ],
    }).write_parquet(MATCHES_OUT)
    pl.DataFrame({
        "node_id": corpus["node_id"],
        "s2_cited_by_count": incoming,
        "s2_reference_count": outgoing,
    }).write_parquet(STATS_OUT)
    meta = {
        "provider": "Semantic Scholar Academic Graph (S2AG)",
        "dataset": "citations",
        "release_id": release_id,
        "license": "ODC-BY",
        "corpus_rows": corpus.height,
        "s2_paper_id_match_count": len(arxiv_to_s2),
        "s2_match_count": len(arxiv_to_corpus_id),
        "s2_match_coverage": coverage,
        "citation_records_scanned": scanned,
        "internal_edge_count": int(len(src)),
        "citation_count_contract": "all S2 citations targeting matched corpus papers",
        "graph_contract": "S2 citations with both endpoints in the active corpus",
    }
    write_json(meta, META_OUT)
    log.info(
        f"S2 citations: {scanned:,} rows scanned | {len(src):,} internal edges | "
        f"{coverage:.1%} paper-id coverage"
    )
    log.info(f"wrote -> {META_OUT}")
    return str(META_OUT)


if __name__ == "__main__":
    run()
