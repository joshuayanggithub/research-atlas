"""s10: Build the org / author / topic index artifacts.

- orgs.json:   OpenAlex institution -> the corpus nodes affiliated with it, grouped under
               our org keys (deepmind, meta, ...). Frontend org filter uses node_ids.
- authors.arrow: dense author index (local author_id, OpenAlex id, name, paper count) for
               autocomplete; plus author_id lists are already in papers.arrow.
- topics.json: OpenAlex id->name at subfield + topic levels for the legend / recolor.

Emits into data/artifacts/.
"""

from __future__ import annotations

import json
from collections import defaultdict

import polars as pl
import pyarrow as pa

from pipeline.common import log
from pipeline.common.io import read_json, write_arrow, write_json
from pipeline.common.schema import (
    AUTHORS_SCHEMA, Institution, OrgsDoc, TopicNode, TopicsDoc,
)
from pipeline.directory import ORG_UNITS, extract_unit_keys
from pipeline.config import ARTIFACTS_DIR, CORPUS_ACTIVE, INTERIM_DIR, Config, ensure_dirs, load_config

CORPUS_IN = CORPUS_ACTIVE
ORGS_RESOLVED_IN = INTERIM_DIR / "orgs_resolved.json"
AFFIL_IN = INTERIM_DIR / "affiliations.parquet"
INSTITUTIONS_IN = INTERIM_DIR / "institutions.json"
ORGS_OUT = ARTIFACTS_DIR / "orgs.json"
AUTHORS_OUT = ARTIFACTS_DIR / "authors.arrow"
TOPICS_OUT = ARTIFACTS_DIR / "topics.json"

# Minimum corpus papers for a non-curated institution to appear in the searchable directory.
# Keeps the index to institutions with a real footprint (~2.2k) and drops OpenAlex noise.
DIRECTORY_MIN_PAPERS = 3


def _build_authors(corpus: pl.DataFrame) -> tuple[pa.Table, dict]:
    """Return (authors arrow table, {openalex_author_id: local_author_id})."""
    count: dict[str, int] = defaultdict(int)
    name: dict[str, str] = {}
    for aids, anames in zip(corpus["author_ids"].to_list(),
                            corpus["author_names"].to_list()):
        for aid, anm in zip(aids, anames):
            count[aid] += 1
            name.setdefault(aid, anm)

    # Sort by descending count for a stable, useful autocomplete order.
    ordered = sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))
    local_id = {aid: i for i, (aid, _) in enumerate(ordered)}
    table = pa.table({
        "author_id": pa.array([local_id[a] for a, _ in ordered], pa.int32()),
        "openalex_id": pa.array([a for a, _ in ordered], pa.string()),
        "name": pa.array([name[a] for a, _ in ordered], pa.string()),
        "count": pa.array([c for _, c in ordered], pa.int32()),
    }, schema=AUTHORS_SCHEMA)
    return table, local_id


def _load_unit_attribution(corpus: pl.DataFrame) -> dict[str, dict[str, set[int]]]:
    """Resolve each active paper's org affiliation evidence into sub-unit memberships.

    Returns ``{org_key: {unit_key: {node_id, ...}}}`` — the DIRECT membership of each
    curated department/lab unit, restricted to the active (shipped) corpus. Papers with no
    matching unit name contribute nothing here and remain parent-only (an expected state).
    """
    unit_nodes: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    if not AFFIL_IN.exists():
        log.warn(f"no affiliation evidence at {AFFIL_IN}; org sub-units skipped")
        return unit_nodes

    affil = pl.read_parquet(AFFIL_IN)
    pid_to_evidence = dict(zip(affil["paper_id"].to_list(),
                               affil["org_affiliations_json"].to_list()))
    for nid, pid in zip(corpus["node_id"].to_list(), corpus["paper_id"].to_list()):
        raw = pid_to_evidence.get(pid)
        if not raw or raw == "{}":
            continue
        evidence: dict[str, list[str]] = json.loads(raw)
        for org_key, affs in evidence.items():
            for unit_key in extract_unit_keys(org_key, affs):
                unit_nodes[org_key][unit_key].add(nid)
    return unit_nodes


def _build_orgs(corpus: pl.DataFrame, resolved: dict,
                inst_registry: dict | None = None) -> OrgsDoc:
    # institution openalex id -> set(node_id)
    inst_nodes: dict[str, list[int]] = defaultdict(list)
    for nid, insts in zip(corpus["node_id"].to_list(),
                          corpus["institution_ids"].to_list()):
        for iid in insts:
            inst_nodes[iid].append(nid)

    unit_attribution = _load_unit_attribution(corpus)

    # OpenAlex institution ids already owned by a curated seed root (so we don't emit a
    # duplicate directory entry for e.g. CMU or the Microsoft Research regional nodes).
    curated_inst_ids: set[str] = set()
    for org in resolved.values():
        for inst in org["institutions"]:
            curated_inst_ids.add(inst["id"])

    institutions: dict[str, Institution] = {}
    for org_key, org in resolved.items():
        # Rollup membership: union of node_ids across this org's OpenAlex institutions.
        node_set: set[int] = set()
        for inst in org["institutions"]:
            node_set.update(inst_nodes.get(inst["id"], []))

        # Evidence-backed child units within this org (empty for orgs with no curated units).
        org_units = ORG_UNITS.get(org_key, [])
        attributed = unit_attribution.get(org_key, {})
        child_keys: list[str] = []
        for unit in org_units:
            direct = attributed.get(unit.key, set())
            # Only keep the parent's own papers; never attribute a unit paper outside the
            # org's rollup set (defensive — evidence is already org-scoped in s02).
            direct &= node_set
            if not direct:
                continue  # no evidence for this unit in the active corpus -> omit it
            child_keys.append(unit.key)
            institutions[unit.key] = Institution(
                openalex_id="",
                display_name=unit.name,
                ror=None,
                type=org["institutions"][0].get("type", "education"),
                kind=org.get("kind", "university"),
                lineage=[],
                parent=org_key,
                unit_type=unit.unit_type,
                children=[],
                count=len(direct),          # leaf: rollup == direct
                node_ids=sorted(direct),
                direct_count=len(direct),
            )

        institutions[org_key] = Institution(
            openalex_id=",".join(i["id"] for i in org["institutions"]),
            display_name=org["name"],
            ror=org["institutions"][0].get("ror"),
            type=org["institutions"][0].get("type", "education"),
            kind=org.get("kind", "university"),
            lineage=[],
            parent=None,
            unit_type="organization",
            children=child_keys,
            count=len(node_set),            # rollup: org + all descendants
            node_ids=sorted(node_set),
            direct_count=len(node_set),     # parent "direct" == its full institution set
        )

    # Full corpus directory: every OTHER institution appearing in the corpus, so any
    # university/company/lab is searchable + filterable — not just the seven curated seeds.
    # These are flat, non-curated entries (no sub-units, excluded from color-by-org). A
    # minimum paper count keeps one-off affiliations and OpenAlex noise out of the index.
    registry = inst_registry or {}
    kind_by_type = {
        "education": "university", "company": "industry", "facility": "facility",
        "nonprofit": "nonprofit", "government": "government", "healthcare": "healthcare",
        "archive": "facility", "other": "other",
    }
    n_directory = 0
    for iid, nids in inst_nodes.items():
        if iid in curated_inst_ids or len(nids) < DIRECTORY_MIN_PAPERS:
            continue
        meta = registry.get(iid, {})
        key = f"oa:{iid}"  # namespaced so it never collides with a curated org key
        institutions[key] = Institution(
            openalex_id=iid,
            display_name=meta.get("display_name") or iid,
            ror=None,
            type=meta.get("type", "education"),
            kind=kind_by_type.get(meta.get("type", "education"), "other"),
            lineage=[],
            parent=None,
            unit_type="organization",
            children=[],
            count=len(nids),
            node_ids=sorted(nids),
            direct_count=len(nids),
            curated=False,
        )
        n_directory += 1
    log.info(f"  directory: {n_directory} non-curated corpus institutions "
             f"(>={DIRECTORY_MIN_PAPERS} papers)")
    return OrgsDoc(institutions=institutions)


def _build_topics(corpus: pl.DataFrame) -> TopicsDoc:
    nodes: dict[tuple[str, int], TopicNode] = {}
    for row in corpus.select(
        ["subfield_id", "subfield_name", "topic_id", "topic_name", "field_id", "field_name"]
    ).iter_rows(named=True):
        if row["field_id"] and row["field_id"] > 0:
            nodes[("field", row["field_id"])] = TopicNode(
                id=row["field_id"], name=row["field_name"] or "", level="field")
        if row["subfield_id"] and row["subfield_id"] > 0:
            nodes[("subfield", row["subfield_id"])] = TopicNode(
                id=row["subfield_id"], name=row["subfield_name"] or "",
                level="subfield", parent=row["field_id"])
        if row["topic_id"] and row["topic_id"] > 0:
            nodes[("topic", row["topic_id"])] = TopicNode(
                id=row["topic_id"], name=row["topic_name"] or "",
                level="topic", parent=row["subfield_id"])
    return TopicsDoc(nodes=list(nodes.values()))


def run(cfg: Config | None = None) -> tuple[str, str, str]:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s10_indexes")

    corpus = pl.read_parquet(CORPUS_IN)
    resolved = read_json(ORGS_RESOLVED_IN)
    inst_registry = read_json(INSTITUTIONS_IN) if INSTITUTIONS_IN.exists() else {}

    authors_tbl, _ = _build_authors(corpus)
    write_arrow(authors_tbl, AUTHORS_OUT)
    log.info(f"authors: {authors_tbl.num_rows} unique -> {AUTHORS_OUT}")

    orgs_doc = _build_orgs(corpus, resolved, inst_registry)
    write_json(orgs_doc, ORGS_OUT)
    # Only log the curated seed roots + units (the directory adds thousands).
    for k, inst in orgs_doc.institutions.items():
        if inst.curated:
            log.info(f"  org {k}: {inst.count} papers")

    topics_doc = _build_topics(corpus)
    write_json(topics_doc, TOPICS_OUT)
    log.info(f"topics: {len(topics_doc.nodes)} nodes -> {TOPICS_OUT}")

    return str(ORGS_OUT), str(AUTHORS_OUT), str(TOPICS_OUT)


if __name__ == "__main__":
    run()
