"""s00: Resolve org display-names / pinned ids to OpenAlex institution metadata.

Each org in ``config.corpus.orgs`` maps to one or more OpenAlex institution ids. When
``ids`` are pinned (the reliable path — company names are ambiguous and fragment into
regional entities), we fetch each institution's metadata. Otherwise we fall back to a
relevance search on ``search``.

Writes ``data/interim/orgs_resolved.json``:
    { org_key: { name, kind, institutions: [ {id, display_name, ror, type, works_count, lineage[]} ] } }

Downstream (s01) ORs every institution id across all orgs into the works filter.
"""

from __future__ import annotations

from pipeline.common import log
from pipeline.common.io import write_json
from pipeline.common.openalex_client import OpenAlexClient, short_id
from pipeline.config import INTERIM_DIR, Config, ensure_dirs, load_config

OUT = INTERIM_DIR / "orgs_resolved.json"


def _fetch_institution(client: OpenAlexClient, inst_id: str) -> dict | None:
    try:
        data = client._get(
            f"/institutions/{inst_id}",
            {"select": "id,display_name,ror,type,country_code,works_count,lineage"},
        )
        return {
            "id": short_id(data["id"]),
            "display_name": data.get("display_name"),
            "ror": data.get("ror"),
            "type": data.get("type"),
            "country_code": data.get("country_code"),
            "works_count": data.get("works_count"),
            "lineage": [short_id(x) for x in data.get("lineage", [])],
        }
    except Exception as e:  # noqa: BLE001 - log + skip a bad id, don't fail the stage
        log.warn(f"failed to fetch institution {inst_id}: {e}")
        return None


def run(cfg: Config | None = None) -> dict:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s00_resolve_orgs")

    client = OpenAlexClient(
        cfg.secrets.openalex_mailto,
        cfg.secrets.openalex_api_key,
        api_keys=cfg.secrets.openalex_api_keys,
    )
    resolved: dict[str, dict] = {}
    try:
        for org in cfg.corpus.orgs:
            institutions: list[dict] = []
            if org.ids:
                for inst_id in org.ids:
                    inst = _fetch_institution(client, short_id(inst_id))
                    if inst:
                        institutions.append(inst)
            else:
                hit = client.resolve_institution(org.search)
                if hit:
                    institutions.append({
                        "id": short_id(hit["id"]),
                        "display_name": hit.get("display_name"),
                        "ror": hit.get("ror"),
                        "type": hit.get("type"),
                        "works_count": hit.get("works_count"),
                        "lineage": [short_id(x) for x in hit.get("lineage", [])],
                    })

            if not institutions:
                log.warn(f"{org.key}: no institutions resolved")
                continue

            resolved[org.key] = {
                "name": org.display_name,
                "kind": org.kind,
                "institutions": institutions,
            }
            names = ", ".join(f"{i['id']}({i['works_count']})" for i in institutions)
            log.info(f"{org.key} [{org.display_name}]: {names}")
    finally:
        client.close()

    write_json(resolved, OUT)
    total_ids = sum(len(v["institutions"]) for v in resolved.values())
    log.info(f"resolved {len(resolved)}/{len(cfg.corpus.orgs)} orgs, "
             f"{total_ids} institution ids -> {OUT}")
    return resolved


if __name__ == "__main__":
    run()
