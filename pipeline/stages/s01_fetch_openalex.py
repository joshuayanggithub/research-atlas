"""s01: Fetch OpenAlex works for the resolved orgs into ``data/raw/works_raw.jsonl``.

Filter = (any target institution) AND (CS field) AND (date range). We OR every
institution id across all orgs, restrict to ``primary_topic.field.id`` = CS, and cap at
``corpus.max_works``. Only the fields we need are ``select``-ed to keep payloads small.

Writes newline-delimited JSON (one raw work per line). Resumable-ish: re-running
overwrites the file; the cap keeps the pull bounded and within API metering.
"""

from __future__ import annotations

import json

from tqdm import tqdm

from pipeline.common import log
from pipeline.common.io import read_json
from pipeline.common.openalex_client import OpenAlexClient
from pipeline.config import INTERIM_DIR, RAW_DIR, Config, ensure_dirs, load_config

ORGS_IN = INTERIM_DIR / "orgs_resolved.json"
OUT = RAW_DIR / "works_raw.jsonl"

# Only the fields the pipeline consumes (title, abstract, dates, authorships, topics,
# citations, ids). Trimming the payload is the single biggest fetch speedup.
SELECT = ",".join([
    "id",
    "title",
    "publication_date",
    "publication_year",
    "cited_by_count",
    "abstract_inverted_index",
    "referenced_works",
    "authorships",
    "primary_topic",
    "topics",
    "ids",
    "primary_location",
])


def _all_institution_ids(resolved: dict) -> list[str]:
    ids: list[str] = []
    for org in resolved.values():
        for inst in org["institutions"]:
            ids.append(inst["id"])
    # de-dup, preserve order
    seen: set[str] = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def build_filter(cfg: Config, inst_ids: list[str]) -> str:
    inst_or = "|".join(inst_ids)
    return (
        f"authorships.institutions.id:{inst_or},"
        f"primary_topic.field.id:{cfg.corpus.field_id},"
        f"from_publication_date:{cfg.corpus.date_from},"
        f"to_publication_date:{cfg.corpus.date_to}"
    )


def run(cfg: Config | None = None) -> int:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s01_fetch_openalex")

    resolved = read_json(ORGS_IN)
    inst_ids = _all_institution_ids(resolved)
    filt = build_filter(cfg, inst_ids)
    log.info(f"{len(inst_ids)} institution ids | field={cfg.corpus.field_id} "
             f"| {cfg.corpus.date_from}..{cfg.corpus.date_to}")

    client = OpenAlexClient(cfg.secrets.openalex_mailto, cfg.secrets.openalex_api_key)
    try:
        total = client.count_works(filt)
        target = min(total, cfg.corpus.max_works)
        log.info(f"matching works: {total} | fetching up to {target}")

        n = 0
        with OUT.open("w", encoding="utf-8") as f, \
                tqdm(total=target, desc="  fetch", unit="w") as bar:
            for w in client.iter_works(
                filt, SELECT, per_page=cfg.corpus.per_page, max_records=target
            ):
                f.write(json.dumps(w, ensure_ascii=False))
                f.write("\n")
                n += 1
                bar.update(1)
    finally:
        client.close()

    log.info(f"wrote {n} works -> {OUT}")
    return n


if __name__ == "__main__":
    run()
