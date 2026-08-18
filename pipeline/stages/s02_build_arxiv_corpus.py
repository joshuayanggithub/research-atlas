"""s02 arXiv mode: stream the bulk snapshot and upsert OAI deltas by arXiv id."""

from __future__ import annotations

import hashlib
import json
import re
import zlib
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

import polars as pl
from tqdm import tqdm

from pipeline.common import log
from pipeline.common.abstract import embed_text
from pipeline.common.io import read_jsonl, write_json
from pipeline.config import INTERIM_DIR, RAW_DIR, Config, ensure_dirs, load_config

OUT = INTERIM_DIR / "corpus.parquet"
AFFIL_OUT = INTERIM_DIR / "affiliations.parquet"
INSTITUTIONS_OUT = INTERIM_DIR / "institutions.json"
OAI_IN = RAW_DIR / "arxiv_oai_updates.jsonl"


def _v1_date(record: dict) -> date | None:
    versions = record.get("versions") or []
    raw = (versions[0].get("created") if versions else None) or record.get("update_date")
    if not raw:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return date.fromisoformat(raw)
        return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError, OverflowError):
        return None


def _categories(record: dict) -> list[str]:
    raw = record.get("categories") or ""
    return [part for part in raw.split() if part]


def _in_scope(record: dict, cfg: Config) -> bool:
    cats = _categories(record)
    return any(c in cfg.arxiv.categories or
               any(c.startswith(prefix) for prefix in cfg.arxiv.category_prefixes)
               for c in cats)


def _author_names(record: dict) -> list[str]:
    parsed = record.get("authors_parsed") or []
    names = []
    for parts in parsed:
        if not parts:
            continue
        last = str(parts[0] or "").strip() if len(parts) > 0 else ""
        first = str(parts[1] or "").strip() if len(parts) > 1 else ""
        suffix = str(parts[2] or "").strip() if len(parts) > 2 else ""
        name = " ".join(p for p in (first, last, suffix) if p)
        if name:
            names.append(name)
    if names:
        return names
    # arXivRaw's author field is deliberately free-form. Preserve it as one searchable
    # display string rather than pretending a lossy comma split is person disambiguation.
    raw = re.sub(r"\s+", " ", str(record.get("authors") or "")).strip()
    return [raw] if raw else []


def _author_id(name: str) -> str:
    canonical = re.sub(r"\W+", "", name.casefold())
    digest = hashlib.sha1(canonical.encode()).hexdigest()[:16]  # noqa: S324 (identifier only)
    return f"arxiv-name:{digest}"


def _normalize(record: dict, cfg: Config) -> dict | None:
    arxiv_id = str(record.get("id") or "").strip()
    title = re.sub(r"\s+", " ", str(record.get("title") or "")).strip()
    created = _v1_date(record)
    if not arxiv_id or not title or created is None:
        return None
    if not (date.fromisoformat(cfg.corpus.date_from) <= created <=
            date.fromisoformat(cfg.corpus.date_to)) or not _in_scope(record, cfg):
        return None
    abstract = re.sub(r"\s+", " ", str(record.get("abstract") or "")).strip() or None
    authors = _author_names(record)
    cats = _categories(record)
    qualifying = [
        category for category in cats
        if category in cfg.arxiv.categories
        or any(category.startswith(prefix) for prefix in cfg.arxiv.category_prefixes)
    ]
    # arXiv's first category is not necessarily the category that admitted the record. Keep
    # the canonical topic in the configured CS/statistics scope for cross-listed records such
    # as ``math.OC cs.LG``.
    primary = qualifying[0] if qualifying else (cats[0] if cats else None)
    doi = str(record.get("doi") or "").strip() or None
    withdrawn_text = f"{record.get('comments') or ''} {abstract or ''}".casefold()
    return {
        "paper_id": f"arxiv:{arxiv_id.lower()}",
        "title": title,
        "abstract": abstract,
        "text": embed_text(title, abstract),
        "has_abstract": abstract is not None,
        "publication_date": created.isoformat(),
        "year": created.year,
        "cited_by_count": 0,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "mag_id": None,
        "venue": "arXiv",
        "author_ids": [_author_id(name) for name in authors],
        "author_names": authors,
        "institution_ids": [],
        "referenced_works": [],
        "domain_id": 1,
        "field_id": 17 if primary and primary.startswith("cs.") else 18,
        "subfield_id": -1,
        # Arrow's browser contract stores topic_id as signed int32. CRC32 is unsigned, so
        # constrain it to the positive signed range while retaining deterministic ids.
        "topic_id": int(zlib.crc32((primary or "unknown").encode()) & 0x7FFFFFFF),
        "domain_name": "Computer Science and Statistics",
        "field_name": "Computer Science" if primary and primary.startswith("cs.")
                      else "Statistics",
        "subfield_name": None,
        "topic_name": primary,
        "arxiv_categories": cats,
        "is_withdrawn": "withdrawn" in withdrawn_text,
    }


def _snapshot_path(cfg: Config) -> Path:
    path = Path(cfg.arxiv.snapshot_path)
    return path if path.is_absolute() else Path(__file__).resolve().parents[2] / path


def run(cfg: Config | None = None) -> int:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s02_build_arxiv_corpus")
    snapshot = _snapshot_path(cfg)
    if not snapshot.exists():
        raise FileNotFoundError(snapshot)

    rows_by_id: dict[str, dict] = {}
    n_raw = n_bad = 0
    total_bytes = snapshot.stat().st_size
    with snapshot.open("r", encoding="utf-8") as stream, tqdm(
        total=total_bytes, desc="  snapshot", unit="B", unit_scale=True,
    ) as bar:
        while line := stream.readline():
            n_raw += 1
            bar.update(len(line.encode("utf-8")))
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                n_bad += 1
                continue
            rec = _normalize(raw, cfg)
            if rec is not None:
                rows_by_id[rec["arxiv_id"]] = rec

    delta_seen = delta_upserts = delta_deletes = 0
    if OAI_IN.exists():
        for raw in read_jsonl(OAI_IN):
            delta_seen += 1
            arxiv_id = str(raw.get("id") or "")
            if raw.get("deleted"):
                delta_deletes += int(rows_by_id.pop(arxiv_id, None) is not None)
                continue
            rec = _normalize(raw, cfg)
            if rec is None:
                rows_by_id.pop(arxiv_id, None)
            else:
                rows_by_id[arxiv_id] = rec
                delta_upserts += 1

    rows = sorted(rows_by_id.values(), key=lambda row: row["paper_id"])
    for node_id, row in enumerate(rows):
        row["node_id"] = node_id
    df = pl.DataFrame(rows)
    df.write_parquet(OUT)

    # Placeholder affiliation evidence for the papers THIS build introduces — merged into any
    # existing artifact rather than replacing it. Overwriting is how the 1991-2014 backfill
    # silently destroyed the OpenAlex authorship evidence for 25,279 papers: s10 then found no
    # unit evidence and emitted an orgs.json with zero department/lab sub-units (no BAIR, no
    # Robotics Institute, no FAIR), with nothing in the logs to say so.
    placeholder = pl.DataFrame(
        {"paper_id": df["paper_id"], "org_affiliations_json": ["{}"] * len(rows)}
    )
    if AFFIL_OUT.exists():
        existing = pl.read_parquet(AFFIL_OUT)
        keep = existing.filter(pl.col("org_affiliations_json") != "{}")
        placeholder = pl.concat(
            [keep, placeholder.filter(~pl.col("paper_id").is_in(keep["paper_id"]))],
            how="vertical",
        ).sort("paper_id")
        log.info(f"affiliations: kept {keep.height:,} existing evidence rows")
    placeholder.write_parquet(AFFIL_OUT)
    if not INSTITUTIONS_OUT.exists():
        write_json({}, INSTITUTIONS_OUT)

    coverage = float(df["has_abstract"].mean()) if rows else 0.0
    withdrawn = int(df["is_withdrawn"].sum()) if rows else 0
    log.info(f"snapshot rows={n_raw:,} bad_json={n_bad} | corpus={len(rows):,} "
             f"abstract coverage={coverage:.2%} withdrawn-marked={withdrawn}")
    log.info(f"OAI records={delta_seen:,} in-scope upserts={delta_upserts:,} "
             f"deletes={delta_deletes:,}")
    log.info(f"wrote -> {OUT}")
    return len(rows)


if __name__ == "__main__":
    run()
