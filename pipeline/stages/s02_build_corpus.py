"""s02: Build the clean corpus from raw works.

- Reconstruct abstracts from ``abstract_inverted_index``.
- Extract the fields downstream stages need (metadata, authorships, topic lineage, refs).
- Deduplicate by OpenAlex id, require a title, assign a dense ``node_id`` (0..N-1).
- Persist ``data/interim/corpus.parquet`` (one row per paper) — the canonical corpus that
  every later stage joins against by ``node_id``.

Also logs abstract coverage % (risk #4 monitoring).
"""

from __future__ import annotations

import json
import re

import polars as pl

from pipeline.common import log
from pipeline.common.abstract import embed_text, reconstruct_abstract
from pipeline.common.io import read_jsonl, read_json, write_json
from pipeline.common.openalex_client import short_id
from pipeline.config import INTERIM_DIR, RAW_DIR, Config, ensure_dirs, load_config

RAW_IN = RAW_DIR / "works_raw.jsonl"
ORGS_RESOLVED_IN = INTERIM_DIR / "orgs_resolved.json"
OUT = INTERIM_DIR / "corpus.parquet"
# Per-paper organization affiliation evidence (keyed by paper_id, survives the s03 drop
# compaction). Kept SEPARATE from corpus.parquet so it never affects node ordering.
AFFIL_OUT = INTERIM_DIR / "affiliations.parquet"
# OpenAlex institution id -> {display_name, type, country_code} for every institution seen
# in the corpus. s10 turns this into the full searchable org directory.
INSTITUTIONS_OUT = INTERIM_DIR / "institutions.json"


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


def _parse_work(
    w: dict,
    inst_to_org: dict[str, str] | None = None,
    inst_registry: dict[str, dict] | None = None,
) -> dict | None:
    wid = short_id(w.get("id"))
    title = (w.get("title") or "").strip()
    if not wid or not title:
        return None

    abstract = reconstruct_abstract(w.get("abstract_inverted_index"))

    authors: list[str] = []
    author_ids: list[str] = []
    inst_ids: list[str] = []
    # org_key -> raw affiliation strings on authorships resolved to that org's institution.
    # This is the evidence the directory layer mines for department/lab sub-units. We only
    # keep strings for authorships OpenAlex already tied to a configured org institution,
    # so a co-author's unrelated affiliation can never leak into an org's evidence.
    org_affils: dict[str, list[str]] = {}
    for a in w.get("authorships", []) or []:
        au = a.get("author") or {}
        aid = short_id(au.get("id"))
        if aid:
            author_ids.append(aid)
            authors.append(au.get("display_name") or aid)
        raws = [r for r in (a.get("raw_affiliation_strings") or []) if r]
        author_org_keys: set[str] = set()
        for inst in a.get("institutions", []) or []:
            iid = short_id(inst.get("id"))
            if iid:
                inst_ids.append(iid)
                # Record institution display metadata into the shared registry (first-seen
                # name/type wins; OpenAlex is consistent per id).
                if inst_registry is not None and iid not in inst_registry:
                    inst_registry[iid] = {
                        "display_name": inst.get("display_name") or iid,
                        "type": inst.get("type") or "education",
                        "country_code": inst.get("country_code"),
                    }
                if inst_to_org and iid in inst_to_org:
                    author_org_keys.add(inst_to_org[iid])
        for org_key in author_org_keys:
            org_affils.setdefault(org_key, []).extend(raws)

    refs = [short_id(r) for r in (w.get("referenced_works") or [])]
    ids = w.get("ids") or {}
    prim = _topic_lineage(w.get("primary_topic"))
    loc = w.get("primary_location") or {}
    source = (loc.get("source") or {}) if isinstance(loc, dict) else {}

    # arXiv is the source of truth for a preprint's date; OpenAlex sometimes carries a wrong
    # date for a re-registered/oddly-DOI'd work (e.g. "Attention Is All You Need" comes back
    # as 2025). When the paper has an arXiv id, prefer the date its id encodes.
    arxiv_id = _arxiv_from_ids(ids)
    oa_date = w.get("publication_date") or ""
    oa_year = int(w.get("publication_year") or 0)
    pub_date, year = _prefer_arxiv_date(arxiv_id, oa_date, oa_year)

    return {
        "paper_id": wid,
        "title": title,
        "abstract": abstract,
        "text": embed_text(title, abstract),
        "has_abstract": abstract is not None,
        "publication_date": pub_date,
        "year": year,
        "cited_by_count": int(w.get("cited_by_count") or 0),
        "doi": _clean_doi(ids.get("doi")),
        "arxiv_id": arxiv_id,
        # Third addressing route for Semantic Scholar. Some works — including landmark
        # papers — carry a DOI that S2 does not index, but S2 does resolve their MAG id.
        # "Attention Is All You Need" is exactly this case: OpenAlex gives it only
        # doi:10.65215/2q58a426 (unknown to S2) plus mag:2626778328 (which S2 resolves,
        # with a SPECTER2 vector). Measured on the dropped rows of the previous build,
        # 23.5% had a MAG id and 57% of those came back with a real vector.
        "mag_id": str(ids["mag"]) if ids.get("mag") else None,
        "venue": source.get("display_name"),
        "author_ids": author_ids,
        "author_names": authors,
        "institution_ids": list(dict.fromkeys(inst_ids)),  # de-dup, keep order
        "referenced_works": refs,
        # {org_key: [raw affiliation strings]} — dedup per org, preserve order.
        "org_affiliations": {k: list(dict.fromkeys(v)) for k, v in org_affils.items()},
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


def _arxiv_yyyymm(arxiv_id: str | None) -> tuple[int, int] | None:
    """(year, month) encoded by an arXiv id, or None if it can't be parsed.

    Modern ids are ``YYMM.NNNNN`` (April 2007-on); older ones ``archive/YYMMNNN``. In both the
    first four digits after any ``/`` are YYMM. YY < 91 → 20YY, else 19YY (arXiv began 1991-08).
    """
    if not arxiv_id:
        return None
    tail = arxiv_id.split("/")[-1]  # drop an old-style "archive/" prefix
    m = re.match(r"(\d{2})(\d{2})", tail)
    if not m:
        return None
    yy, mm = int(m.group(1)), int(m.group(2))
    if mm < 1 or mm > 12:
        return None
    year = 1900 + yy if yy >= 91 else 2000 + yy
    return year, mm


def _prefer_arxiv_date(arxiv_id: str | None, oa_date: str, oa_year: int) -> tuple[str, int]:
    """Prefer the arXiv-encoded date over OpenAlex when the paper is on arXiv.

    arXiv's id encodes the true submission month, which is authoritative for a preprint;
    OpenAlex occasionally reports a wildly wrong date for re-registered DOIs. Rule:
      - No arXiv id, or unparseable → keep OpenAlex verbatim.
      - arXiv id present → use arXiv's year/month. Keep OpenAlex's day only when its year+month
        already agree with arXiv (so we don't invent a day); otherwise use the 1st.
    """
    ym = _arxiv_yyyymm(arxiv_id)
    if ym is None:
        return oa_date, oa_year
    year, month = ym
    oa_ym = None
    m = re.match(r"(\d{4})-(\d{2})", oa_date or "")
    if m:
        oa_ym = (int(m.group(1)), int(m.group(2)))
    if oa_ym == (year, month):
        return oa_date, year  # OpenAlex agrees; keep its precise day
    return f"{year:04d}-{month:02d}-01", year


def _load_inst_to_org() -> dict[str, str]:
    """Build {openalex_institution_id: org_key} from the resolved orgs (s00 output).

    Empty if s00 has not run yet; s02 then simply records no org affiliation evidence and
    the directory layer degrades to parent-only attribution.
    """
    if not ORGS_RESOLVED_IN.exists():
        return {}
    resolved = read_json(ORGS_RESOLVED_IN)
    inst_to_org: dict[str, str] = {}
    for org_key, org in resolved.items():
        for inst in org.get("institutions", []):
            iid = inst.get("id")
            if iid:
                inst_to_org[iid] = org_key
    return inst_to_org


def run(cfg: Config | None = None) -> int:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s02_build_corpus")

    inst_to_org = _load_inst_to_org()
    inst_registry: dict[str, dict] = {}
    rows: list[dict] = []
    seen: set[str] = set()
    n_raw = 0
    for w in read_jsonl(RAW_IN):
        n_raw += 1
        rec = _parse_work(w, inst_to_org, inst_registry)
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

    # Split the org-affiliation evidence into its own paper_id-keyed artifact. Keeping it
    # out of corpus.parquet means s03's drop/compaction never has to carry a nested map,
    # and s10 can rebuild org sub-units without touching the frozen node ordering. The
    # evidence is a {org_key: [strings]} map with varying keys, so we serialize it to a
    # JSON string column (only the Python directory layer in s10 reads it).
    affil_rows = [
        {"paper_id": r["paper_id"],
         "org_affiliations_json": json.dumps(r.pop("org_affiliations"), ensure_ascii=False)}
        for r in rows
    ]
    pl.DataFrame(affil_rows).write_parquet(AFFIL_OUT)

    df = pl.DataFrame(rows)
    df.write_parquet(OUT)

    # Institution registry (every institution seen in the corpus), for the full org directory.
    write_json(inst_registry, INSTITUTIONS_OUT)

    n = len(rows)
    cov = df["has_abstract"].sum() / n if n else 0.0
    n_with_org = sum(1 for r in affil_rows if r["org_affiliations_json"] != "{}")
    log.info(f"raw={n_raw} | corpus={n} (deduped) | abstract coverage={cov:.1%}")
    log.info(f"year range: {df['year'].min()}..{df['year'].max()}")
    log.info(f"org-affiliation evidence: {n_with_org}/{n} papers -> {AFFIL_OUT}")
    log.info(f"institutions seen: {len(inst_registry)} -> {INSTITUTIONS_OUT}")
    log.info(f"wrote -> {OUT}")
    return n


if __name__ == "__main__":
    run()
