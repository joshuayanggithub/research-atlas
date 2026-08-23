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
    AUTHORS_SCHEMA, Institution, OrgsDoc, TopAuthor, TopicNode, TopicsDoc,
)
from pipeline.directory import ORG_UNITS, extract_unit_keys
from pipeline.directory.org_names import NAME_ONLY_ORGS
from pipeline.config import ARTIFACTS_DIR, CORPUS_ACTIVE, INTERIM_DIR, Config, ensure_dirs, load_config

CORPUS_IN = CORPUS_ACTIVE
ORGS_RESOLVED_IN = INTERIM_DIR / "orgs_resolved.json"
AFFIL_IN = INTERIM_DIR / "affiliations.parquet"
INSTITUTIONS_IN = INTERIM_DIR / "institutions.json"
ROSTER_MEMBERSHIPS_IN = INTERIM_DIR / "roster_memberships.parquet"
ROSTER_ORGS_IN = INTERIM_DIR / "roster_orgs.json"
COMET_IN = INTERIM_DIR / "comet_affiliations.parquet"
ORGS_OUT = ARTIFACTS_DIR / "orgs.json"
AUTHOR_AFFILIATIONS_OUT = ARTIFACTS_DIR / "author_affiliations.arrow"
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


def _author_affiliations(
    corpus: pl.DataFrame,
    local_author_id: dict[str, int],
    node_orgs: dict[int, list[str]],
    top_n: int = 3,
) -> dict[int, list[tuple[str, int, int, int]]]:
    """Where each author publishes from: (label, papers, first year, last year).

    The org tree answers "who works at this organization" (Institution.top_authors, D36). The
    author panel needs the opposite direction and nothing carried it, so clicking a researcher
    showed a name, a paper count and three external links but not where they work — the first
    thing anyone wants.

    Ranked by RECENCY-WEIGHTED paper count, not raw count: an author who published six papers
    from a PhD lab in 2014 and twelve from a company since 2022 works at the company, and a raw
    count of a long career would keep naming the university. Half-life is four years, so work
    from a decade ago still registers but does not lead.

    The reported count and year range are RAW, so the UI never shows a weighted number as if it
    were a fact about the author.
    """
    HALF_LIFE = 4.0
    # Recency is the whole ranking signal here, so a corpus without years cannot produce an
    # honest answer — emit nothing rather than a list ordered by raw count that would read as
    # "where this author works".
    if "year" not in corpus.columns or not local_author_id:
        return {}
    latest = int(corpus["year"].max() or 0)
    # (author, label) -> [weight, count, first year, last year]
    acc: dict[tuple[int, str], list[float]] = {}
    for node, year, aids in zip(corpus["node_id"].to_list(), corpus["year"].to_list(),
                                corpus["author_ids"].to_list()):
        labels = node_orgs.get(node)
        if not labels or not aids:
            continue
        y = int(year or 0)
        weight = 0.5 ** ((latest - y) / HALF_LIFE) if y else 0.0
        for aid in aids:
            local = local_author_id.get(aid)
            if local is None:
                continue
            for label in labels:
                slot = acc.get((local, label))
                if slot is None:
                    acc[(local, label)] = [weight, 1, y or 9999, y or 0]
                else:
                    slot[0] += weight
                    slot[1] += 1
                    if y:
                        slot[2] = min(slot[2], y)
                        slot[3] = max(slot[3], y)

    by_author: dict[int, list[tuple[float, str, int, int, int]]] = defaultdict(list)
    for (local, label), (w, n, y0, y1) in acc.items():
        by_author[local].append((w, label, n, y0, y1))
    out: dict[int, list[tuple[str, int, int, int]]] = {}
    for local, rows in by_author.items():
        rows.sort(key=lambda r: (-r[0], -r[2], r[1]))
        out[local] = [(label, n, y0, y1) for _, label, n, y0, y1 in rows[:top_n]]
    return out


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
                inst_registry: dict | None = None,
                roster_orgs: dict | None = None,
                roster_memberships: pl.DataFrame | None = None,
                local_author_id: dict[str, int] | None = None,
                ) -> tuple[OrgsDoc, dict[int, list[tuple[str, int, int, int]]]]:
    # institution openalex id -> set(node_id). Sets, not lists, because the COMET merge below
    # has to ask "is this pair already known?" for ~700k papers.
    inst_sets: dict[str, set[int]] = defaultdict(set)
    for nid, insts in zip(corpus["node_id"].to_list(),
                          corpus["institution_ids"].to_list()):
        for iid in insts:
            inst_sets[iid].add(nid)

    # Model-extracted affiliations (COMET; see build_comet_affiliations.py and D35/D43). Merged
    # ON TOP of publisher-asserted authorship, never replacing it, and the pairs it introduces are
    # counted so each org can report how much of its attribution is extracted rather than asserted.
    extracted: dict[str, set[int]] = defaultdict(set)
    # node_id is POSITIONAL, so a comet_affiliations.parquet built against a different corpus
    # would attribute papers at random. Restricting to ids this corpus actually contains makes a
    # stale or foreign file harmless instead of silently wrong — and keeps unit tests, which
    # build a tiny synthetic corpus, from absorbing the real 800k-row artifact.
    valid_nodes = set(corpus["node_id"].to_list())
    if COMET_IN.exists():
        comet = pl.read_parquet(COMET_IN).filter(pl.col("node_id").is_in(valid_nodes))
        for nid, insts in zip(comet["node_id"].to_list(), comet["institution_ids"].to_list()):
            for iid in insts or []:
                if nid not in inst_sets[iid]:
                    inst_sets[iid].add(nid)
                    extracted[iid].add(nid)
        log.info(f"  COMET affiliations: {comet.height:,} papers, "
                 f"{sum(len(v) for v in extracted.values()):,} new (institution, paper) pairs "
                 f"across {len(extracted):,} institutions")
    else:
        log.warn(f"{COMET_IN.name} absent — publisher-asserted affiliations only")

    inst_nodes: dict[str, list[int]] = {k: sorted(v) for k, v in inst_sets.items()}

    # Companies and neolabs, matched from COMET's affiliation STRINGS. ROR links universities
    # well and companies not at all (Google's ROR appears zero times in COMET's 2.8M rows), so
    # without this the org tree gains 10k papers for CMU and nothing at all for Google. Patterns
    # are curated and reviewed — see pipeline/directory/org_names.py.
    comet_org_nodes: dict[str, set[int]] = defaultdict(set)
    comet_unit_evidence: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    if COMET_IN.exists() and "org_affiliations_json" in comet.columns:
        for nid, raw in zip(comet["node_id"].to_list(), comet["org_affiliations_json"].to_list()):
            if not raw or raw == "{}":
                continue
            for org_key, affs in json.loads(raw).items():
                comet_org_nodes[org_key].add(nid)
                comet_unit_evidence[org_key][nid] = affs
        log.info("  COMET org-name matches: "
                 + ", ".join(f"{k}={len(v):,}" for k, v in
                             sorted(comet_org_nodes.items(), key=lambda kv: -len(kv[1]))))

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

        # Papers attributed to this org by COMET affiliation text (see above).
        comet_here = comet_org_nodes.get(org_key, set())
        node_set |= comet_here

        # Evidence-backed child units within this org (empty for orgs with no curated units).
        org_units = ORG_UNITS.get(org_key, [])
        attributed = dict(unit_attribution.get(org_key, {}))
        # COMET strings also feed the sub-unit extractor, which is how "Microsoft Research Asia"
        # or "FAIR at Meta" become units rather than just parent-org papers.
        for nid, affs in comet_unit_evidence.get(org_key, {}).items():
            for unit_key in extract_unit_keys(org_key, affs):
                attributed.setdefault(unit_key, set()).add(nid)
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
                # Sub-unit evidence comes from OpenAlex raw affiliation text, not from COMET,
                # so a unit is never partly model-extracted.
                extracted_count=0,
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
            extracted_count=sum(
                1 for n in node_set
                if n in comet_here
                or any(n in extracted.get(i["id"], ()) for i in org["institutions"])
            ),
            direct_count=len(node_set),     # parent "direct" == its full institution set
        )

    # Curated neolabs use reviewed exact-author-id claims because they are commonly absent
    # from OpenAlex/ROR institution authorships. Keep them as roots alongside the curated
    # institution-backed organizations, with provenance visible in the artifact/UI.
    roster_nodes: dict[str, set[int]] = defaultdict(set)
    roster_methods: dict[str, set[str]] = defaultdict(set)
    if roster_memberships is not None and roster_memberships.height:
        for org_key, node_id, method in roster_memberships.select(
            ["org_key", "node_id", "provenance"]
        ).iter_rows():
            roster_nodes[org_key].add(node_id)
            roster_methods[org_key].add(method)
    for org in (roster_orgs or {}).get("organizations", []):
        key = org["key"]
        if key in institutions:
            raise ValueError(f"roster organization key collides with existing org: {key}")
        node_ids = sorted(roster_nodes.get(key, set()))
        identity = org["organization_id"]
        institutions[key] = Institution(
            openalex_id="",
            organization_id=identity,
            display_name=org["display_name"],
            ror=identity if identity.startswith("https://ror.org/") else None,
            type="company",
            kind=org.get("kind", "neolab"),
            lineage=[],
            parent=None,
            unit_type="organization",
            children=[],
            count=len(node_ids),
            node_ids=node_ids,
            direct_count=len(node_ids),
            curated=True,
            membership_methods=sorted(roster_methods.get(key, set())),
        )

    # Name-only organizations: real labs with no OpenAlex institution to resolve and no author
    # roster, whose entire evidence is the affiliation STRING (org_names.NAME_ONLY_ORGS).
    # AGENTS.md names Anthropic, DeepSeek, Kimi/Moonshot and MiniMax as first-class NeoLabs;
    # without this the browse tree listed exactly one independent lab. Provenance is recorded
    # as `affiliation_name` so the UI can say where the attribution came from, exactly as the
    # roster path above does.
    for key, meta in NAME_ONLY_ORGS.items():
        if key in institutions:
            continue  # an OpenAlex-resolved or roster org of the same key wins
        node_ids = sorted(comet_org_nodes.get(key, set()))
        if not node_ids:
            continue  # no evidence in this corpus: emit nothing rather than an empty org
        institutions[key] = Institution(
            openalex_id="",
            organization_id=f"local:{key}",
            display_name=meta["display_name"],
            ror=None,
            type="company",
            kind=meta.get("kind", "neolab"),
            lineage=[],
            parent=None,
            unit_type="organization",
            children=[],
            count=len(node_ids),
            node_ids=node_ids,
            direct_count=len(node_ids),
            curated=True,
            membership_methods=["affiliation_name"],
        )
        log.info(f"  name-only org {key}: {len(node_ids)} papers")

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
            extracted_count=len(extracted.get(iid, ())),
            direct_count=len(nids),
            curated=False,
        )
        n_directory += 1
    log.info(f"  directory: {n_directory} non-curated corpus institutions "
             f"(>={DIRECTORY_MIN_PAPERS} papers)")
    if local_author_id:
        _attach_top_authors(institutions, corpus, local_author_id)

    # Author -> where they publish from. Built here because this is the only place that holds
    # BOTH directions of the affiliation data: OpenAlex institutions (which resolve
    # universities well) and the curated name matches (which are the only thing that resolves
    # companies). Labels are display names, so the frontend needs no second lookup.
    node_orgs: dict[int, list[str]] = defaultdict(list)
    for inst_id, nodes in inst_nodes.items():
        label = (registry.get(inst_id) or {}).get("display_name")
        if not label:
            continue
        for nid in nodes:
            node_orgs[nid].append(label)
    for org_key, nodes in comet_org_nodes.items():
        inst = institutions.get(org_key)
        if inst is None:
            continue
        for nid in nodes:
            node_orgs[nid].append(inst.display_name)
    author_affiliations = _author_affiliations(corpus, local_author_id or {}, node_orgs)
    if local_author_id:
        log.info(f"  author affiliations: {len(author_affiliations):,} of "
                 f"{len(local_author_id):,} authors have at least one attributed paper "
                 f"({len(author_affiliations) / max(len(local_author_id), 1) * 100:.1f}%)")
    return OrgsDoc(institutions=institutions), author_affiliations


# Names shown per org unit. The panel lists a dozen; a few spare keep it useful if the
# frontend ever wants to page, without making orgs.json meaningfully bigger.
TOP_AUTHORS_PER_UNIT = 20


def _attach_top_authors(
    institutions: dict[str, Institution],
    corpus: pl.DataFrame,
    local_author_id: dict[str, int],
) -> None:
    """Fill each unit's `top_authors` from the papers attributed to it."""
    authors_by_node: dict[int, list[int]] = {}
    for node, aids in zip(corpus["node_id"].to_list(), corpus["author_ids"].to_list()):
        ids = [local_author_id[a] for a in (aids or []) if a in local_author_id]
        if ids:
            authors_by_node[node] = ids
    name_of: dict[int, str] = {}
    for aids, anames in zip(corpus["author_ids"].to_list(), corpus["author_names"].to_list()):
        for aid, anm in zip(aids or [], anames or []):
            lid = local_author_id.get(aid)
            if lid is not None:
                name_of.setdefault(lid, anm)

    # Only the browse tree (13 curated roots + their evidence-backed units). Attaching this to
    # all 9,939 directory entries as well took orgs.json from 2.23 to 4.96 MB gzipped — ~2.7 s of
    # eager load on a 1 MB/s link — to populate a panel that only opens on a tree unit.
    total = 0
    for inst in institutions.values():
        if not (inst.curated or inst.parent):
            continue
        counts: dict[int, int] = {}
        for node in inst.node_ids:
            for lid in authors_by_node.get(node, ()):
                counts[lid] = counts.get(lid, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], name_of.get(kv[0], "")))
        inst.top_authors = [
            TopAuthor(author_id=lid, name=name_of.get(lid, f"#{lid}"), count=c)
            for lid, c in ranked[:TOP_AUTHORS_PER_UNIT]
        ]
        total += len(inst.top_authors)
    log.info(f"  top researchers: {total:,} rows across {len(institutions):,} org units")


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
    # Exact paper counts, so the frontend can rank facets by size instead of alphabetically.
    counts: dict[tuple[str, int], int] = {}
    for level, col in (("field", "field_id"), ("subfield", "subfield_id"), ("topic", "topic_id")):
        for row in corpus.group_by(col).len().iter_rows():
            key, n = row
            if key and key > 0:
                counts[(level, key)] = n
    for key, node in nodes.items():
        node.count = counts.get(key, 0)
    return TopicsDoc(nodes=list(nodes.values()))


def _write_author_affiliations(
    affiliations: dict[int, list[tuple[str, int, int, int]]],
) -> None:
    """Persist author -> affiliations for s11 to fold into the author-papers shards."""
    rows = sorted(affiliations.items())
    pa_table = pa.table({
        "author_id": pa.array([a for a, _ in rows], pa.int32()),
        "labels": pa.array([[x[0] for x in v] for _, v in rows], pa.list_(pa.string())),
        "counts": pa.array([[x[1] for x in v] for _, v in rows], pa.list_(pa.int32())),
        "first_year": pa.array([[x[2] for x in v] for _, v in rows], pa.list_(pa.int16())),
        "last_year": pa.array([[x[3] for x in v] for _, v in rows], pa.list_(pa.int16())),
    })
    write_arrow(pa_table, AUTHOR_AFFILIATIONS_OUT)
    log.info(f"author affiliations -> {AUTHOR_AFFILIATIONS_OUT}")


def run(cfg: Config | None = None) -> tuple[str, str, str]:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s10_indexes")

    corpus = pl.read_parquet(CORPUS_IN)
    resolved = read_json(ORGS_RESOLVED_IN)
    inst_registry = read_json(INSTITUTIONS_IN) if INSTITUTIONS_IN.exists() else {}
    roster_orgs = read_json(ROSTER_ORGS_IN) if ROSTER_ORGS_IN.exists() else {}
    roster_memberships = (
        pl.read_parquet(ROSTER_MEMBERSHIPS_IN) if ROSTER_MEMBERSHIPS_IN.exists() else None
    )

    authors_tbl, local_author_id = _build_authors(corpus)
    write_arrow(authors_tbl, AUTHORS_OUT)
    log.info(f"authors: {authors_tbl.num_rows} unique -> {AUTHORS_OUT}")

    orgs_doc, author_affiliations = _build_orgs(
        corpus, resolved, inst_registry, roster_orgs, roster_memberships, local_author_id,
    )
    _write_author_affiliations(author_affiliations)
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
