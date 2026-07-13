"""s10: Build the org / author / topic index artifacts.

- orgs.json:   OpenAlex institution -> the corpus nodes affiliated with it, grouped under
               our org keys (deepmind, meta_fair, ...). Frontend org filter uses node_ids.
- authors.arrow: dense author index (local author_id, OpenAlex id, name, paper count) for
               autocomplete; plus author_id lists are already in papers.arrow.
- topics.json: OpenAlex id->name at subfield + topic levels for the legend / recolor.

Emits into data/artifacts/.
"""

from __future__ import annotations

from collections import defaultdict

import polars as pl
import pyarrow as pa

from pipeline.common import log
from pipeline.common.io import read_json, write_arrow, write_json
from pipeline.common.schema import (
    AUTHORS_SCHEMA, Institution, OrgsDoc, TopicNode, TopicsDoc,
)
from pipeline.config import ARTIFACTS_DIR, INTERIM_DIR, Config, ensure_dirs, load_config

CORPUS_IN = INTERIM_DIR / "corpus.parquet"
ORGS_RESOLVED_IN = INTERIM_DIR / "orgs_resolved.json"
ORGS_OUT = ARTIFACTS_DIR / "orgs.json"
AUTHORS_OUT = ARTIFACTS_DIR / "authors.arrow"
TOPICS_OUT = ARTIFACTS_DIR / "topics.json"


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


def _build_orgs(corpus: pl.DataFrame, resolved: dict) -> OrgsDoc:
    # institution openalex id -> set(node_id)
    inst_nodes: dict[str, list[int]] = defaultdict(list)
    for nid, insts in zip(corpus["node_id"].to_list(),
                          corpus["institution_ids"].to_list()):
        for iid in insts:
            inst_nodes[iid].append(nid)

    institutions: dict[str, Institution] = {}
    # Assign each org key its group; expose per institution so the UI can nest if desired.
    for org_key, org in resolved.items():
        # Union of node_ids across this org's institutions.
        node_set: set[int] = set()
        for inst in org["institutions"]:
            node_set.update(inst_nodes.get(inst["id"], []))
        institutions[org_key] = Institution(
            openalex_id=",".join(i["id"] for i in org["institutions"]),
            display_name=org["name"],
            ror=org["institutions"][0].get("ror"),
            type=org["institutions"][0].get("type", "education"),
            kind=org.get("kind", "university"),
            lineage=[],
            count=len(node_set),
            node_ids=sorted(node_set),
        )
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

    authors_tbl, _ = _build_authors(corpus)
    write_arrow(authors_tbl, AUTHORS_OUT)
    log.info(f"authors: {authors_tbl.num_rows} unique -> {AUTHORS_OUT}")

    orgs_doc = _build_orgs(corpus, resolved)
    write_json(orgs_doc, ORGS_OUT)
    for k, inst in orgs_doc.institutions.items():
        log.info(f"  org {k}: {inst.count} papers")

    topics_doc = _build_topics(corpus)
    write_json(topics_doc, TOPICS_OUT)
    log.info(f"topics: {len(topics_doc.nodes)} nodes -> {TOPICS_OUT}")

    return str(ORGS_OUT), str(AUTHORS_OUT), str(TOPICS_OUT)


if __name__ == "__main__":
    run()
