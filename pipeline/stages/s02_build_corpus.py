"""s02: Build the clean corpus from raw works.

- Reconstruct abstracts from ``abstract_inverted_index``.
- Extract the fields downstream stages need (metadata, authorships, topic lineage, refs).
- Deduplicate by OpenAlex id, require a title, assign a dense ``node_id`` (0..N-1).
- Persist ``data/interim/corpus.parquet`` (one row per paper) — the canonical corpus that
  every later stage joins against by ``node_id``.

Also logs abstract coverage % (risk #4 monitoring).
"""

from __future__ import annotations

import polars as pl

from pipeline.common import log
from pipeline.common.abstract import embed_text, reconstruct_abstract
from pipeline.common.io import read_jsonl
from pipeline.common.openalex_client import short_id
from pipeline.config import INTERIM_DIR, RAW_DIR, Config, ensure_dirs, load_config

RAW_IN = RAW_DIR / "works_raw.jsonl"
OUT = INTERIM_DIR / "corpus.parquet"


def _numeric_id(openalex_url: str | None) -> int:
    """Extract the numeric part of an OpenAlex taxonomy id.

    OpenAlex uses TWO id styles: topics are 'T13650' (letter-prefixed) while
    domains/fields/subfields are 'domains/3', 'fields/17', 'subfields/1702'
    (slash-separated). Handle both: take the last path segment, strip leading
    non-digits, parse the rest.
    """
    if not openalex_url:
        return -1
    tail = openalex_url.rstrip("/").rsplit("/", 1)[-1]  # 'T13650' or '1702'
    digits = "".join(c for c in tail if c.isdigit())
    try:
        return int(digits) if digits else -1
    except ValueError:
        return -1


def _topic_lineage(topic: dict | None) -> dict:
    """Extract domain/field/subfield/topic numeric ids + names from a topic object."""
    def part(obj, key):
        v = (obj or {}).get(key) or {}
        return _numeric_id(v.get("id")), v.get("display_name")

    if not topic:
        return {"domain_id": -1, "field_id": -1, "subfield_id": -1, "topic_id": -1,
                "domain_name": None, "field_name": None, "subfield_name": None,
                "topic_name": None}
    d_id, d_nm = part(topic, "domain")
    f_id, f_nm = part(topic, "field")
    s_id, s_nm = part(topic, "subfield")
    t_id = _numeric_id(topic.get("id"))  # topic id is on the topic object itself
    return {"domain_id": d_id, "field_id": f_id, "subfield_id": s_id, "topic_id": t_id,
            "domain_name": d_nm, "field_name": f_nm, "subfield_name": s_nm,
            "topic_name": topic.get("display_name")}


def _parse_work(w: dict) -> dict | None:
    wid = short_id(w.get("id"))
    title = (w.get("title") or "").strip()
    if not wid or not title:
        return None

    abstract = reconstruct_abstract(w.get("abstract_inverted_index"))

    authors: list[str] = []
    author_ids: list[str] = []
    inst_ids: list[str] = []
    for a in w.get("authorships", []) or []:
        au = a.get("author") or {}
        aid = short_id(au.get("id"))
        if aid:
            author_ids.append(aid)
            authors.append(au.get("display_name") or aid)
        for inst in a.get("institutions", []) or []:
            iid = short_id(inst.get("id"))
            if iid:
                inst_ids.append(iid)

    refs = [short_id(r) for r in (w.get("referenced_works") or [])]
    ids = w.get("ids") or {}
    prim = _topic_lineage(w.get("primary_topic"))
    loc = w.get("primary_location") or {}
    source = (loc.get("source") or {}) if isinstance(loc, dict) else {}

    return {
        "paper_id": wid,
        "title": title,
        "abstract": abstract,
        "text": embed_text(title, abstract),
        "has_abstract": abstract is not None,
        "publication_date": w.get("publication_date") or "",
        "year": int(w.get("publication_year") or 0),
        "cited_by_count": int(w.get("cited_by_count") or 0),
        "doi": _clean_doi(ids.get("doi")),
        "arxiv_id": _arxiv_from_ids(ids),
        "venue": source.get("display_name"),
        "author_ids": author_ids,
        "author_names": authors,
        "institution_ids": list(dict.fromkeys(inst_ids)),  # de-dup, keep order
        "referenced_works": refs,
        **prim,
    }


def _clean_doi(doi_url: str | None) -> str | None:
    """Strip the URL prefix but KEEP the full DOI (e.g. '10.1145/3641289').

    NOTE: do NOT use short_id() on DOIs — it strips everything before the last '/',
    mangling '10.1145/3641289' into '3641289' and breaking Semantic Scholar lookups.
    """
    if not doi_url:
        return None
    d = doi_url.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/"):
        if d.lower().startswith(prefix):
            d = d[len(prefix):]
            break
    return d or None


def _arxiv_from_ids(ids: dict) -> str | None:
    # arXiv works usually carry DOI 10.48550/arXiv.<id>; extract the bare arXiv id.
    doi = ids.get("doi") or ""
    if "arxiv" in doi.lower():
        return doi.lower().split("arxiv.")[-1].strip("/")
    return None


def run(cfg: Config | None = None) -> int:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s02_build_corpus")

    rows: list[dict] = []
    seen: set[str] = set()
    n_raw = 0
    for w in read_jsonl(RAW_IN):
        n_raw += 1
        rec = _parse_work(w)
        if rec is None:
            continue
        if rec["paper_id"] in seen:
            continue
        seen.add(rec["paper_id"])
        rows.append(rec)

    # Assign dense node_id in a stable order (by paper_id) so rebuilds are deterministic.
    rows.sort(key=lambda r: r["paper_id"])
    for i, r in enumerate(rows):
        r["node_id"] = i

    df = pl.DataFrame(rows)
    df.write_parquet(OUT)

    n = len(rows)
    cov = df["has_abstract"].sum() / n if n else 0.0
    log.info(f"raw={n_raw} | corpus={n} (deduped) | abstract coverage={cov:.1%}")
    log.info(f"year range: {df['year'].min()}..{df['year'].max()}")
    log.info(f"wrote -> {OUT}")
    return n


if __name__ == "__main__":
    run()
