"""s15: Supplement an arXiv-authoritative corpus with exact-match OpenAlex metadata.

This stage is deliberately a LEFT JOIN. arXiv remains authoritative for corpus membership,
paper ids, title/abstract, first-version date, and categories. OpenAlex contributes structured
author and institution identities, affiliation evidence, venue/identifier gaps, its topic
taxonomy, and citation/reference provenance.

The fetch is bulk and resumable:

* up to 100 identifiers are OR-ed into each OpenAlex filter request;
* successful matches and attempted routes are appended durably after every batch;
* reruns skip fresh matches and already-attempted routes;
* a quota interruption materializes all matches obtained so far before propagating.

OpenAlex citation values are retained under ``openalex_*`` columns. The following local
``s16_apply_openalex_citations`` stage materializes exact-match values as the fast browser
default; a later optional Semantic Scholar bulk scan can reconcile its matched rows.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
from tqdm import tqdm

from pipeline.common import log
from pipeline.common.io import read_json, read_jsonl, write_json
from pipeline.common.openalex_client import OpenAlexClient, QuotaExhausted, short_id
from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR, RAW_DIR, Config, ensure_dirs, load_config
from pipeline.stages.s02_build_corpus import _clean_doi, _numeric_id, _topic_lineage

CORPUS_IN = INTERIM_DIR / "corpus.parquet"
AFFIL_OUT = INTERIM_DIR / "affiliations.parquet"
INSTITUTIONS_OUT = INTERIM_DIR / "institutions.json"
ENRICHMENT_OUT = INTERIM_DIR / "openalex_enrichment.parquet"
MATCH_LOG = RAW_DIR / "openalex_enrichment_matches.jsonl"
ATTEMPT_LOG = RAW_DIR / "openalex_enrichment_attempts.jsonl"
META_OUT = INTERIM_DIR / "openalex_enrichment_meta.json"

SELECT = ",".join([
    "id",
    "display_name",
    "publication_date",
    "publication_year",
    "cited_by_count",
    "referenced_works",
    "authorships",
    "primary_topic",
    "topics",
    "ids",
    "primary_location",
    "locations",
    "updated_date",
])

ENRICH_SCHEMA: dict[str, pl.DataType] = {
    "arxiv_id": pl.String,
    "openalex_id": pl.String,
    "openalex_match_method": pl.String,
    "openalex_fetched_at": pl.String,
    "openalex_updated_date": pl.String,
    "openalex_title": pl.String,
    "openalex_publication_date": pl.String,
    "openalex_year": pl.Int32,
    "openalex_cited_by_count": pl.Int32,
    "openalex_doi": pl.String,
    "openalex_mag_id": pl.String,
    "openalex_venue": pl.String,
    "openalex_author_ids": pl.List(pl.String),
    "openalex_author_names": pl.List(pl.String),
    "openalex_institution_ids": pl.List(pl.String),
    "openalex_referenced_works": pl.List(pl.String),
    "openalex_domain_id": pl.Int32,
    "openalex_field_id": pl.Int32,
    "openalex_subfield_id": pl.Int32,
    "openalex_topic_id": pl.Int32,
    "openalex_domain_name": pl.String,
    "openalex_field_name": pl.String,
    "openalex_subfield_name": pl.String,
    "openalex_topic_name": pl.String,
    "openalex_topic_ids": pl.List(pl.Int32),
    "openalex_topic_names": pl.List(pl.String),
    "org_affiliations_json": pl.String,
    "institutions_json": pl.String,
}

_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)
_ARXIV_URL_RE = re.compile(
    r"(?:arxiv\.org|export\.arxiv\.org)/(?:abs|pdf)/([^?#]+)", re.IGNORECASE
)
_ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.(.+)$", re.IGNORECASE)


def _canonical_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    result = value.strip().removesuffix(".pdf")
    result = _VERSION_RE.sub("", result)
    return result.casefold() or None


def _normal_text(value: str | None) -> str:
    return re.sub(r"\W+", "", (value or "").casefold())


def _fresh(iso: str | None, days: int, now: datetime) -> bool:
    if not iso:
        return False
    if days == 0:
        return True
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp >= now - timedelta(days=days)


def _work_arxiv_ids(work: dict) -> set[str]:
    found: set[str] = set()
    ids = work.get("ids") or {}
    doi = _clean_doi(ids.get("doi"))
    match = _ARXIV_DOI_RE.search(doi or "")
    if match and (arxiv_id := _canonical_arxiv_id(match.group(1))):
        found.add(arxiv_id)
    for location in work.get("locations") or []:
        for key in ("landing_page_url", "pdf_url"):
            match = _ARXIV_URL_RE.search(str(location.get(key) or ""))
            if match and (arxiv_id := _canonical_arxiv_id(match.group(1))):
                found.add(arxiv_id)
    return found


def _candidate_score(work: dict, corpus_row: dict, route: str) -> tuple:
    """Prefer the richest exact-id duplicate without treating citation count as additive."""
    ids = work.get("ids") or {}
    requested_doi = (_clean_doi(corpus_row.get("doi")) or "").casefold()
    candidate_doi = (_clean_doi(ids.get("doi")) or "").casefold()
    original_doi_match = int(bool(requested_doi and requested_doi == candidate_doi))
    exact_title = int(_normal_text(work.get("display_name")) == _normal_text(corpus_row["title"]))
    authorships = work.get("authorships") or []
    institutions = {
        short_id(inst.get("id"))
        for authorship in authorships
        for inst in (authorship.get("institutions") or [])
        if inst.get("id")
    }
    references = work.get("referenced_works") or []
    # Route order is already meaningful; this tuple only resolves duplicates within one
    # exact filter response. Prefer the publisher DOI record, then semantic agreement and
    # structured evidence. Citation count is only the final tie-breaker.
    return (
        original_doi_match,
        exact_title,
        len(institutions),
        len(authorships),
        len(references),
        int(work.get("cited_by_count") or 0),
        short_id(work.get("id")) or "",
    )


def _normalize_work(
    arxiv_id: str,
    work: dict,
    route: str,
    fetched_at: str,
    inst_to_org: dict[str, str],
) -> dict:
    authors: dict[str, str] = {}
    institution_ids: list[str] = []
    institutions: dict[str, dict] = {}
    org_affiliations: dict[str, list[str]] = defaultdict(list)

    for authorship in work.get("authorships") or []:
        author = authorship.get("author") or {}
        author_id = short_id(author.get("id"))
        author_name = str(author.get("display_name") or author_id or "").strip()
        if author_id and author_name:
            authors.setdefault(author_id, author_name)

        raw_affiliations = [
            str(value).strip()
            for value in (authorship.get("raw_affiliation_strings") or [])
            if str(value).strip()
        ]
        org_keys: set[str] = set()
        for inst in authorship.get("institutions") or []:
            inst_id = short_id(inst.get("id"))
            if not inst_id:
                continue
            institution_ids.append(inst_id)
            institutions.setdefault(inst_id, {
                "display_name": inst.get("display_name") or inst_id,
                "type": inst.get("type") or "education",
                "country_code": inst.get("country_code"),
                "ror": inst.get("ror"),
            })
            if inst_id in inst_to_org:
                org_keys.add(inst_to_org[inst_id])
        for org_key in org_keys:
            org_affiliations[org_key].extend(raw_affiliations)

    ids = work.get("ids") or {}
    primary = _topic_lineage(work.get("primary_topic"))
    topics = work.get("topics") or []
    location = work.get("primary_location") or {}
    source = location.get("source") or {} if isinstance(location, dict) else {}
    referenced = [value for value in (short_id(ref) for ref in work.get("referenced_works") or []) if value]

    return {
        "arxiv_id": arxiv_id,
        "openalex_id": short_id(work.get("id")),
        "openalex_match_method": route,
        "openalex_fetched_at": fetched_at,
        "openalex_updated_date": work.get("updated_date"),
        "openalex_title": (work.get("display_name") or "").strip() or None,
        "openalex_publication_date": work.get("publication_date"),
        "openalex_year": int(work.get("publication_year") or 0),
        "openalex_cited_by_count": int(work.get("cited_by_count") or 0),
        "openalex_doi": _clean_doi(ids.get("doi")),
        "openalex_mag_id": str(ids.get("mag")) if ids.get("mag") else None,
        "openalex_venue": source.get("display_name"),
        "openalex_author_ids": list(authors),
        "openalex_author_names": list(authors.values()),
        "openalex_institution_ids": list(dict.fromkeys(institution_ids)),
        "openalex_referenced_works": list(dict.fromkeys(referenced)),
        "openalex_domain_id": primary["domain_id"],
        "openalex_field_id": primary["field_id"],
        "openalex_subfield_id": primary["subfield_id"],
        "openalex_topic_id": primary["topic_id"],
        "openalex_domain_name": primary["domain_name"],
        "openalex_field_name": primary["field_name"],
        "openalex_subfield_name": primary["subfield_name"],
        "openalex_topic_name": primary["topic_name"],
        "openalex_topic_ids": [_numeric_id(topic.get("id")) for topic in topics],
        "openalex_topic_names": [
            str(topic.get("display_name") or "") for topic in topics
        ],
        "org_affiliations_json": json.dumps(
            {key: list(dict.fromkeys(values)) for key, values in org_affiliations.items()},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "institutions_json": json.dumps(
            institutions, ensure_ascii=False, separators=(",", ":")
        ),
    }


def _route_values(route: str, row: dict) -> str | None:
    arxiv_id = row["arxiv_id"]
    if route == "original_doi":
        doi = (_clean_doi(row.get("doi")) or "").strip()
        return doi if doi and not _ARXIV_DOI_RE.search(doi) else None
    if route == "arxiv_doi":
        return f"10.48550/arxiv.{arxiv_id}"
    if route == "https_url":
        return f"https://arxiv.org/abs/{arxiv_id}"
    if route == "http_url":
        return f"http://arxiv.org/abs/{arxiv_id}"
    raise ValueError(route)


def _route_filter(route: str, values: list[str]) -> str:
    field = "doi" if route in {"original_doi", "arxiv_doi"} else "locations.landing_page_url"
    return f"{field}:{'|'.join(values)}"


def _load_inst_to_org() -> dict[str, str]:
    path = INTERIM_DIR / "orgs_resolved.json"
    if not path.exists():
        return {}
    resolved = read_json(path)
    return {
        inst["id"]: org_key
        for org_key, org in resolved.items()
        for inst in org.get("institutions", [])
        if inst.get("id")
    }


def _read_latest_matches() -> dict[str, dict]:
    if not MATCH_LOG.exists():
        return {}
    latest: dict[str, dict] = {}
    for row in read_jsonl(MATCH_LOG):
        latest[row["arxiv_id"]] = row
    return latest


def _read_attempts() -> dict[tuple[str, str], str]:
    if not ATTEMPT_LOG.exists():
        return {}
    latest: dict[tuple[str, str], str] = {}
    for row in read_jsonl(ATTEMPT_LOG):
        latest[(row["arxiv_id"], row["route"])] = row["attempted_at"]
    return latest


def _append_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
        stream.flush()


def _merge_corpus(corpus: pl.DataFrame, enrichment: pl.DataFrame) -> pl.DataFrame:
    """Left-join provider fields while preserving canonical rows and their exact order."""
    canonical_ids = corpus.sort("node_id")["paper_id"].to_list()
    # Reruns replace provider columns, never stack suffixes. Canonical arXiv columns remain.
    provider_columns = [name for name in ENRICH_SCHEMA if name != "arxiv_id"]
    existing_provider = [name for name in provider_columns if name in corpus.columns]
    if existing_provider:
        corpus = corpus.drop(existing_provider)
    merged = corpus.join(enrichment, on="arxiv_id", how="left")
    has_match = pl.col("openalex_id").is_not_null()
    has_authors = has_match & (pl.col("openalex_author_ids").list.len().fill_null(0) > 0)
    merged = merged.with_columns([
        pl.when(has_authors).then(pl.col("openalex_author_ids"))
        .otherwise(pl.col("author_ids")).alias("author_ids"),
        pl.when(has_authors).then(pl.col("openalex_author_names"))
        .otherwise(pl.col("author_names")).alias("author_names"),
        pl.when(has_match).then(pl.col("openalex_institution_ids"))
        .otherwise(pl.col("institution_ids")).alias("institution_ids"),
        pl.coalesce([pl.col("doi"), pl.col("openalex_doi")]).alias("doi"),
        pl.coalesce([pl.col("mag_id"), pl.col("openalex_mag_id")]).alias("mag_id"),
        pl.coalesce([pl.col("venue"), pl.col("openalex_venue")]).alias("venue"),
    ]).sort("node_id")
    # Downstream labels, colors, indexes, and filters consume the canonical facet columns.
    # Use OpenAlex's richer taxonomy for exact matches, while retaining the arXiv-derived
    # category facets when a paper is unmatched or a provider facet is absent.
    taxonomy = (
        ("domain_id", "openalex_domain_id"),
        ("field_id", "openalex_field_id"),
        ("subfield_id", "openalex_subfield_id"),
        ("topic_id", "openalex_topic_id"),
        ("domain_name", "openalex_domain_name"),
        ("field_name", "openalex_field_name"),
        ("subfield_name", "openalex_subfield_name"),
        ("topic_name", "openalex_topic_name"),
    )
    taxonomy_exprs = []
    for canonical, provider in taxonomy:
        if canonical not in merged.columns or provider not in merged.columns:
            continue
        taxonomy_exprs.append(
            pl.when(has_match & pl.col(provider).is_not_null())
            .then(pl.col(provider))
            .otherwise(pl.col(canonical))
            .alias(canonical)
        )
    if taxonomy_exprs:
        merged = merged.with_columns(taxonomy_exprs)
    if merged["paper_id"].to_list() != canonical_ids:
        raise RuntimeError("OpenAlex enrichment changed canonical paper ordering")
    return merged


def _materialize(corpus: pl.DataFrame, matches: dict[str, dict], built_at: str) -> float:
    relevant = [matches[ax] for ax in corpus["arxiv_id"].to_list() if ax in matches]
    enrichment = pl.DataFrame(relevant, schema=ENRICH_SCHEMA) if relevant else pl.DataFrame(
        schema=ENRICH_SCHEMA
    )
    enrichment.write_parquet(ENRICHMENT_OUT)

    merged = _merge_corpus(corpus, enrichment)
    merged.write_parquet(CORPUS_IN)

    # Embeddings depend only on canonical id + title/abstract text, none of which changes.
    # If an already-embedded active corpus has the same ids, enrich it in place so users can
    # rebuild organization/index artifacts without spending another GPU pass.
    if CORPUS_ACTIVE.exists() and CORPUS_ACTIVE != CORPUS_IN:
        active = pl.read_parquet(CORPUS_ACTIVE)
        active_ids = set(active["paper_id"].to_list())
        full_ids = set(corpus["paper_id"].to_list())
        if active_ids <= full_ids:
            _merge_corpus(active, enrichment).write_parquet(CORPUS_ACTIVE)
            log.info(f"propagated enrichment to {CORPUS_ACTIVE} ({active.height:,} rows)")
        else:
            raise RuntimeError("active corpus contains ids absent from canonical corpus")

    # Replace the placeholder arXiv affiliation artifact with OpenAlex authorship evidence.
    affil = merged.select([
        "paper_id",
        pl.col("org_affiliations_json").fill_null("{}").alias("org_affiliations_json"),
    ])
    affil.write_parquet(AFFIL_OUT)

    registry: dict[str, dict] = {}
    if INSTITUTIONS_OUT.exists():
        registry.update(read_json(INSTITUTIONS_OUT))
    for raw in enrichment["institutions_json"].to_list():
        if raw:
            registry.update(json.loads(raw))
    write_json(registry, INSTITUTIONS_OUT)

    coverage = enrichment.height / corpus.height if corpus.height else 0.0
    write_json({
        "source": "OpenAlex",
        "built_at": built_at,
        "matched": enrichment.height,
        "corpus": corpus.height,
        "coverage": coverage,
        "unmatched": corpus.height - enrichment.height,
    }, META_OUT)
    log.info(
        f"OpenAlex enrichment: {enrichment.height:,}/{corpus.height:,} "
        f"({coverage:.1%}) -> {ENRICHMENT_OUT}"
    )
    return coverage


def _fetch_route(
    client: OpenAlexClient,
    route: str,
    corpus_rows: list[dict],
    matches: dict[str, dict],
    attempts: dict[tuple[str, str], str],
    cfg: Config,
    now: datetime,
    inst_to_org: dict[str, str],
) -> None:
    candidates = []
    for row in corpus_rows:
        arxiv_id = row["arxiv_id"]
        if arxiv_id in matches and _fresh(
            matches[arxiv_id].get("openalex_fetched_at"), cfg.openalex_enrichment.refresh_days, now
        ):
            continue
        attempted_at = attempts.get((arxiv_id, route))
        if _fresh(attempted_at, cfg.openalex_enrichment.refresh_days, now):
            continue
        value = _route_values(route, row)
        if value and "|" not in value and "," not in value:
            candidates.append((row, value))

    if not candidates:
        log.info(f"OpenAlex {route}: nothing pending")
        return

    batch_size = cfg.openalex_enrichment.batch_size
    fetched_at = now.isoformat()
    row_by_ax = {row["arxiv_id"]: row for row, _ in candidates}
    batches = [
        candidates[start:start + batch_size]
        for start in range(0, len(candidates), batch_size)
    ]

    def fetch_batch(batch: list[tuple[dict, str]]) -> list[dict]:
        values = [value for _, value in batch]
        works = client.list_works(_route_filter(route, values), SELECT, per_page=100)
        if cfg.openalex_enrichment.request_delay:
            time.sleep(cfg.openalex_enrichment.request_delay)
        return works

    # Only network calls run concurrently. Log writes and shared-state updates stay on this
    # thread, preserving valid append-only checkpoints even when a request fails.
    executor = ThreadPoolExecutor(max_workers=cfg.openalex_enrichment.workers)
    futures: dict[Future[list[dict]], list[tuple[dict, str]]] = {
        executor.submit(fetch_batch, batch): batch for batch in batches
    }
    try:
        with tqdm(total=len(candidates), desc=f"  oa {route}", unit="paper") as bar:
            for future in as_completed(futures):
                batch = futures[future]
                works = future.result()
                requested = {row["arxiv_id"] for row, _ in batch}
                original_doi_map = {
                    (_clean_doi(value) or "").casefold(): row["arxiv_id"]
                    for row, value in batch
                }

                by_arxiv: dict[str, list[dict]] = defaultdict(list)
                for work in works:
                    found = _work_arxiv_ids(work) & requested
                    if route == "original_doi":
                        work_doi = (_clean_doi((work.get("ids") or {}).get("doi")) or "").casefold()
                        if work_doi in original_doi_map:
                            found.add(original_doi_map[work_doi])
                    for arxiv_id in found:
                        by_arxiv[arxiv_id].append(work)

                normalized: list[dict] = []
                for arxiv_id, options in by_arxiv.items():
                    best = max(
                        options,
                        key=lambda work: _candidate_score(work, row_by_ax[arxiv_id], route),
                    )
                    record = _normalize_work(arxiv_id, best, route, fetched_at, inst_to_org)
                    matches[arxiv_id] = record
                    normalized.append(record)
                _append_rows(MATCH_LOG, normalized)

                attempted_rows = [
                    {"arxiv_id": row["arxiv_id"], "route": route, "attempted_at": fetched_at}
                    for row, _ in batch
                ]
                _append_rows(ATTEMPT_LOG, attempted_rows)
                attempts.update({
                    (row["arxiv_id"], route): fetched_at for row, _ in batch
                })
                bar.update(len(batch))
    except BaseException:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    gained = sum(1 for row, _ in candidates if row["arxiv_id"] in matches)
    log.info(f"OpenAlex {route}: {gained:,}/{len(candidates):,} resolved")


def run(cfg: Config | None = None) -> float:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s15_enrich_openalex")
    if cfg.corpus.source != "arxiv_snapshot" or not cfg.openalex_enrichment.enabled:
        log.info("OpenAlex enrichment skipped (not an enabled arXiv-spine build)")
        return 0.0
    if not CORPUS_IN.exists():
        raise FileNotFoundError(f"canonical arXiv corpus missing: {CORPUS_IN}; run s02 first")

    corpus = pl.read_parquet(CORPUS_IN).sort("node_id")
    if "arxiv_id" not in corpus.columns or corpus["arxiv_id"].null_count():
        raise RuntimeError("OpenAlex enrichment requires a canonical arXiv id on every row")
    if cfg.openalex_enrichment.require_api_key and not cfg.secrets.openalex_api_key:
        raise RuntimeError(
            "s15 requires OPENALEX_API_KEY for a full-corpus run. Create a free key at "
            "https://openalex.org/settings/api and add it to .env; anonymous quota is too "
            f"small for {corpus.height:,} papers."
        )

    rows = corpus.select(["arxiv_id", "doi", "title"]).to_dicts()
    if cfg.openalex_enrichment.max_papers:
        rows = rows[:cfg.openalex_enrichment.max_papers]
        log.warn(f"OpenAlex development cap: {len(rows):,}/{corpus.height:,} papers")

    now = datetime.now(timezone.utc)
    matches = _read_latest_matches()
    attempts = _read_attempts()
    inst_to_org = _load_inst_to_org()
    client = OpenAlexClient(
        cfg.secrets.openalex_mailto,
        cfg.secrets.openalex_api_key,
        page_pause=cfg.openalex_enrichment.request_delay,
        api_keys=cfg.secrets.openalex_api_keys,
    )
    quota_error: QuotaExhausted | None = None
    try:
        # A publisher DOI generally yields the richest merged record. The arXiv DOI alias
        # covers most remaining papers; URL variants are only queried for unresolved ids.
        for route in ("original_doi", "arxiv_doi", "https_url", "http_url"):
            _fetch_route(client, route, rows, matches, attempts, cfg, now, inst_to_org)
    except QuotaExhausted as exc:
        quota_error = exc
        log.warn(str(exc))
    finally:
        client.close()

    coverage = _materialize(corpus, matches, now.isoformat())
    if quota_error is not None:
        raise quota_error
    return coverage


if __name__ == "__main__":
    run()
