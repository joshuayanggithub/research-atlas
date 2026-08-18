"""s14: Join curated organization rosters to the active corpus.

Writes a resumable, paper-level evidence table plus normalized organization metadata.
Each row says which exact roster member caused a paper attribution and retains the claim's
provenance/date bounds. s10 consumes these files when building ``orgs.json``.
"""

from __future__ import annotations

import polars as pl

from pipeline.common import log
from pipeline.common.io import write_json
from pipeline.config import CORPUS_ACTIVE, INTERIM_DIR, Config, ensure_dirs, load_config
from pipeline.directory.rosters import RosterMember, load_rosters

MEMBERSHIPS_OUT = INTERIM_DIR / "roster_memberships.parquet"
ORGS_OUT = INTERIM_DIR / "roster_orgs.json"

MEMBERSHIP_SCHEMA = {
    "org_key": pl.String,
    "node_id": pl.Int64,
    "paper_id": pl.String,
    "author_id": pl.String,
    "provenance": pl.String,
    "valid_from": pl.String,
    "valid_to": pl.String,
}


def _membership_rows(corpus: pl.DataFrame, rosters) -> list[dict]:
    claims: dict[str, list[tuple[str, RosterMember]]] = {}
    for org in rosters.organizations:
        for member in org.members:
            claims.setdefault(member.openalex_id, []).append((org.key, member))
    rows: list[dict] = []
    for node_id, paper_id, publication_date, author_ids in corpus.select(
        ["node_id", "paper_id", "publication_date", "author_ids"]
    ).iter_rows():
        for author_id in dict.fromkeys(author_ids or []):
            author_claims = claims.get(author_id)
            if author_claims is None:
                continue
            for org_key, member in author_claims:
                paper_date = publication_date or ""
                if (member.valid_from or member.valid_to) and not paper_date:
                    # A bounded roster claim cannot establish membership for an undated
                    # paper. Exclude it rather than silently treating unknown as in-range.
                    continue
                if member.valid_from and paper_date and paper_date < member.valid_from.isoformat():
                    continue
                if member.valid_to and paper_date and paper_date > member.valid_to.isoformat():
                    continue
                rows.append({
                    "org_key": org_key,
                    "node_id": node_id,
                    "paper_id": paper_id,
                    "author_id": author_id,
                    "provenance": member.provenance,
                    "valid_from": member.valid_from.isoformat() if member.valid_from else None,
                    "valid_to": member.valid_to.isoformat() if member.valid_to else None,
                })
    return rows


def run(cfg: Config | None = None) -> tuple[str, str]:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s14_rosters")

    corpus = pl.read_parquet(CORPUS_ACTIVE)
    rosters = load_rosters()
    rows = _membership_rows(corpus, rosters)
    pl.DataFrame(rows, schema=MEMBERSHIP_SCHEMA).write_parquet(MEMBERSHIPS_OUT)
    write_json({
        "version": rosters.version,
        "organizations": [org.model_dump(mode="json") for org in rosters.organizations],
    }, ORGS_OUT)

    paper_count = len({row["node_id"] for row in rows})
    log.info(
        f"rosters: {len(rosters.organizations)} orgs, {len(rows)} evidence rows, "
        f"{paper_count} attributed papers -> {MEMBERSHIPS_OUT}"
    )
    return str(MEMBERSHIPS_OUT), str(ORGS_OUT)


if __name__ == "__main__":
    run()
