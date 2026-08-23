"""s11: Assemble the artifact bundle into web/public/data/ + write the manifest.

Builds the two big Arrow tables that the frontend renders (points, papers) by joining the
corpus with coords (s04), cluster assignments (s05), and per-subfield colors; copies the
JSON artifacts (clusters, labels, orgs, topics); converts neighbors/edges to Arrow; and
writes manifest.json with byte sizes, row counts, and sha256 for integrity.
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import polars as pl
import pyarrow as pa

from pipeline.common import log
from pipeline.common.io import (
    read_arrow, read_json, read_npy, sha256_file, write_arrow, write_json,
)
from pipeline.common import schema as S
from pipeline.config import (
    ARTIFACTS_DIR, CORPUS_ACTIVE, INTERIM_DIR, WEB_DATA_DIR, Config, ensure_dirs, load_config,
)

CORPUS_IN = CORPUS_ACTIVE
COORDS_IN = INTERIM_DIR / "coords2d.npy"
CLUSTER_IN = INTERIM_DIR / "cluster_assign.npy"
REVEAL_IN = INTERIM_DIR / "reveal_levels.npy"
NEIGHBORS_IN = INTERIM_DIR / "neighbors.npz"
EDGES_IN = INTERIM_DIR / "edges.npz"
EMBED_META_IN = INTERIM_DIR / "embed_meta.json"
S2_CITATION_META_IN = INTERIM_DIR / "s2_citation_meta.json"
OPENALEX_CITATION_META_IN = INTERIM_DIR / "openalex_citation_meta.json"
TILES_IN = INTERIM_DIR / "tiles.json"
REF_AVAIL_IN = INTERIM_DIR / "reference_availability.parquet"
S2_COUNTS_IN = INTERIM_DIR / "s2_citation_counts.parquet"

# Same palette as s07 so points match cluster label colors.
_PALETTE = [
    [99, 179, 237], [246, 173, 85], [104, 211, 145], [237, 100, 166],
    [159, 122, 234], [246, 224, 94], [79, 209, 197], [252, 129, 129],
    [144, 205, 244], [183, 148, 244], [104, 211, 145], [237, 137, 54],
]


def _region_tables(n_nodes: int) -> tuple[np.ndarray, pa.Table]:
    """Per-node deepest hierarchy cell + the cell parent chain.

    s06 writes tiles.json with one cell per region, each listing the nodes it contains. The
    deepest level is not a full partition (only 370,789 of 912,429 nodes reach level 10), so a
    node's "leaf" is simply the deepest cell that lists it. Membership at ANY level is then a
    walk up `parent`, which is what lets the UI select exactly the papers a label covers
    instead of approximating with centroid distance.
    """
    cells = read_json(TILES_IN)["cells"]
    leaf = np.full(n_nodes, -1, dtype=np.int32)
    best_level = np.full(n_nodes, -1, dtype=np.int16)
    ids, parents, levels = [], [], []
    for c in cells:
        cid, lvl = int(c["id"]), int(c["level"])
        ids.append(cid)
        parents.append(-1 if c.get("parent") is None else int(c["parent"]))
        levels.append(lvl)
        idx = np.asarray(c["node_idx"], dtype=np.int64)
        if idx.size == 0:
            continue
        idx = idx[(idx >= 0) & (idx < n_nodes)]
        deeper = lvl > best_level[idx]
        sel = idx[deeper]
        leaf[sel] = cid
        best_level[sel] = lvl
    table = pa.table({
        "id": pa.array(ids, pa.int32()),
        "parent": pa.array(parents, pa.int32()),
        "level": pa.array(levels, pa.int16()),
    }, schema=S.REGIONS_SCHEMA)
    log.info(f"regions: {len(ids):,} cells | nodes with a leaf cell: "
             f"{int((leaf >= 0).sum()):,}/{n_nodes:,}")
    return leaf, table


def _build_points(corpus: pl.DataFrame, coords: np.ndarray,
                  clusters: np.ndarray, reveal_levels: np.ndarray,
                  month_index: np.ndarray, region_leaf: np.ndarray) -> pa.Table:
    sub_ids = corpus["subfield_id"].to_list()
    uniq = sorted(set(sub_ids))
    sub_color = {s: _PALETTE[i % len(_PALETTE)] for i, s in enumerate(uniq)}
    rgb = np.array([sub_color.get(s, [160, 160, 160]) for s in sub_ids], dtype=np.uint8)

    return pa.table({
        "node_id": pa.array(corpus["node_id"].to_list(), pa.int32()),
        "x": pa.array(coords[:, 0], pa.float32()),
        "y": pa.array(coords[:, 1], pa.float32()),
        "year": pa.array(corpus["year"].to_list(), pa.int16()),
        "cited_by_count": pa.array(_citation_floor(corpus)[0], pa.int32()),
        "cluster_leaf": pa.array(clusters.tolist(), pa.int32()),
        "domain_id": pa.array(corpus["domain_id"].to_list(), pa.int8()),
        "field_id": pa.array(corpus["field_id"].to_list(), pa.int16()),
        "subfield_id": pa.array(sub_ids, pa.int16()),
        "topic_id": pa.array(corpus["topic_id"].to_list(), pa.int32()),
        "r": pa.array(rgb[:, 0], pa.uint8()),
        "g": pa.array(rgb[:, 1], pa.uint8()),
        "b": pa.array(rgb[:, 2], pa.uint8()),
        "reveal_level": pa.array(reveal_levels.tolist(), pa.int16()),
        "month_index": pa.array(month_index.tolist(), pa.int16()),
        "region_leaf": pa.array(region_leaf.tolist(), pa.int32()),
    }, schema=S.POINTS_SCHEMA)


def _emit_point_tiles(points: pa.Table, reveal_levels: np.ndarray) -> list[S.PointTile]:
    """Split the points table into one Arrow file per reveal level.

    The frontend fetches cumulative tiles 0..current for the viewport, so a huge corpus is
    downloaded incrementally rather than all at once. Each tile is self-contained (full
    POINTS_SCHEMA rows), so loading a set of tiles is a plain concatenation.
    """
    tiles: list[S.PointTile] = []
    n = points.num_rows
    n_used = int(reveal_levels.max()) + 1 if n else 0
    cumulative = 0
    for level in range(n_used):
        mask = reveal_levels == level
        rows = int(mask.sum())
        if rows == 0:
            continue
        cumulative += rows
        tile = points.filter(pa.array(mask))
        path = WEB_DATA_DIR / S.points_tile(level)
        write_arrow(tile, path)
        tiles.append(S.PointTile(
            level=level,
            path=S.points_tile(level),
            rows=rows,
            cumulative=cumulative,
            bytes=path.stat().st_size,
        ))
    return tiles


def _build_papers(corpus: pl.DataFrame) -> pa.Table:
    def col(name):
        return corpus[name].to_list()

    def nz(name):  # nullable string
        return [v if v else None for v in corpus[name].to_list()]

    return pa.table({
        "node_id": pa.array(col("node_id"), pa.int32()),
        "paper_id": pa.array(col("paper_id"), pa.string()),
        "title": pa.array(col("title"), pa.string()),
        "publication_date": pa.array(col("publication_date"), pa.string()),
        "doi": pa.array(nz("doi"), pa.string()),
        "arxiv_id": pa.array(nz("arxiv_id"), pa.string()),
        "venue": pa.array(nz("venue"), pa.string()),
        "cited_by_count": pa.array(col("cited_by_count"), pa.int32()),
        "primary_topic_id": pa.array(col("topic_id"), pa.int32()),
        "author_ids": pa.array(_local_author_ids(corpus), pa.list_(pa.int32())),
        "author_names": pa.array(col("author_names"), pa.list_(pa.string())),
        "institution_ids": pa.array(_inst_placeholder(corpus), pa.list_(pa.int32())),
    }, schema=S.PAPERS_SCHEMA)


def _build_papers_index(
    corpus: pl.DataFrame, figure_nodes: set[int], *, default_citation_available: bool,
) -> pa.Table:
    """Resident search/list index: the fields every all-N consumer needs (search, list
    rows, sorting, the author filter). Ships whole; heavier fields go to lazy detail.

    ``has_figure`` marks the papers s13 baked a first-figure crop for, so the frontend
    fetches a crop only when one exists (no 404 probing)."""
    node_ids = corpus["node_id"].to_list()
    if "s2_citation_available" in corpus.columns:
        s2_available = corpus["s2_citation_available"].to_list()
        oa_available = (corpus["openalex_citation_available"].to_list()
                        if "openalex_citation_available" in corpus.columns else [False] * corpus.height)
        # S2 is authoritative for its matched rows; OpenAlex remains a truthful fallback for
        # an unmatched S2 minority. Canonical counts were materialized with the same precedence.
        citation_available = [bool(s2 or oa) for s2, oa in zip(s2_available, oa_available)]
    elif "openalex_citation_available" in corpus.columns:
        citation_available = corpus["openalex_citation_available"].to_list()
    else:
        citation_available = [default_citation_available] * corpus.height
    floored, enumerable, _ = _citation_floor(corpus)
    citation_available = [a or b for a, b in zip(citation_available, enumerable)]
    return pa.table({
        "node_id": pa.array(node_ids, pa.int32()),
        "cited_by_count": pa.array(floored, pa.int32()),
        "year": pa.array(corpus["year"].to_list(), pa.int16()),
        "citation_count_available": pa.array(citation_available, pa.bool_()),
        "references_available": pa.array(_reference_availability(corpus), pa.bool_()),
        "has_figure": pa.array([nid in figure_nodes for nid in node_ids], pa.bool_()),
    }, schema=S.PAPERS_INDEX_SCHEMA)


def _citation_floor(corpus: pl.DataFrame) -> tuple[list[int], list[bool], int]:
    """Citation counts raised to the number of citers we can actually enumerate.

    A global citation count cannot be lower than the citers visible inside this corpus, yet
    337,426 papers (37%) reported exactly that — understating by 8,272,741 citations in total.
    Cause: the 2015-2024 half of the merge carries only OpenAlex counts, and OpenAlex loses
    citation coverage for arXiv-only preprints after the MAG shutdown (99.9% of affected papers
    have no S2 count; 2021-2024 are 55-60% affected, 2025-2026 only 0.1%). The S2 edge graph
    knows better, so use it as a PROVABLE floor: we can name that many citing papers.

    Where a real S2 global count is available it is used instead of the floor: that repair now
    exists (build_s2_citation_counts.py, D34) and covers 775,323 papers with 68,096,874
    citations, so the floor only carries the papers S2's snapshot does not list at all.
    """
    n_nodes = corpus.height
    counts = np.zeros(n_nodes, dtype=np.int64)
    node_ids = np.asarray(corpus["node_id"].to_list(), dtype=np.int64)
    counts[node_ids] = np.asarray(
        [c if c is not None else 0 for c in corpus["cited_by_count"].to_list()], dtype=np.int64
    )
    counts = np.maximum(counts, _s2_global_counts(corpus, n_nodes))
    edges = np.load(EDGES_IN)
    in_corpus = np.bincount(edges["dst"], minlength=n_nodes)[:n_nodes]
    raised = int((in_corpus > counts).sum())
    floored = np.maximum(counts, in_corpus)
    if raised:
        log.warn(
            f"citation floor: raised {raised:,} papers to their in-corpus citer count "
            f"(+{int((floored - counts).sum()):,} citations) — provider counts were below what "
            f"the citation graph can enumerate"
        )
    # A paper we can enumerate citers for HAS a citation count, whatever the provider said.
    return floored[node_ids].tolist(), (in_corpus[node_ids] > 0).tolist(), raised


def _s2_global_counts(corpus: pl.DataFrame, n_nodes: int) -> np.ndarray:
    """True global citation counts from the S2AG bulk snapshot, by node_id.

    D31 could only apply a floor — the citers we can name inside a 1M-paper corpus — because the
    2015-2024 half of the merge carries OpenAlex counts, which lose arXiv-preprint coverage after
    the MAG shutdown. `build_s2_citation_counts.py` reads the real number straight off
    `cited_by.parquet` (one row per cited paper, the length of its citer list IS the count), so
    this is the whole graph, not our slice of it.

    Missing file => zeros, which `np.maximum` makes a no-op: an interim tree without the scan
    keeps exactly the previous behaviour.
    """
    out = np.zeros(n_nodes, dtype=np.int64)
    if not S2_COUNTS_IN.exists():
        log.warn(f"{S2_COUNTS_IN.name} absent — falling back to the in-corpus citation floor")
        return out
    counts = pl.read_parquet(S2_COUNTS_IN)
    joined = corpus.select("node_id").join(counts, on="node_id", how="left")
    vals = np.asarray(
        [c if c is not None else 0 for c in joined["s2_cited_by_count"].to_list()], dtype=np.int64
    )
    out[np.asarray(corpus["node_id"].to_list(), dtype=np.int64)] = vals
    covered = int((vals > 0).sum())
    log.info(
        f"S2 global citation counts: {covered:,}/{corpus.height:,} papers "
        f"({vals.sum():,} citations)"
    )
    return out


def _reference_availability(corpus: pl.DataFrame) -> list[bool]:
    """Whether any provider gave this paper a reference list (build_reference_availability.py).

    Missing file => assume available, so an older interim tree keeps the previous behaviour
    rather than silently claiming every paper lacks references.
    """
    if not REF_AVAIL_IN.exists():
        log.warn(f"{REF_AVAIL_IN.name} absent — assuming references available for all papers")
        return [True] * corpus.height
    flags = pl.read_parquet(REF_AVAIL_IN)
    joined = corpus.select("node_id").join(flags, on="node_id", how="left")
    out = [bool(v) if v is not None else True for v in joined["references_available"].to_list()]
    log.info(f"references_available: {sum(out):,}/{len(out):,} papers have a reference list")
    return out


def _month_histogram(month_index: np.ndarray) -> list[int]:
    """Papers per month across the whole corpus (see Manifest.month_histogram)."""
    if month_index.size == 0:
        return []
    counts = np.bincount(month_index[month_index >= 0], minlength=int(month_index.max()) + 1)
    log.info(f"month histogram: {len(counts)} months, peak {int(counts.max()):,}/mo")
    return [int(v) for v in counts]


def _emit_position_shards(points: pa.Table) -> tuple[int, int]:
    """Point rows sharded by node_id (see schema.POSITION_SHARD_ROWS).

    `points` is already dense and ordered by node_id, so each shard is a plain slice — the
    frontend reuses the same fill path it uses for reveal-level tiles.
    """
    rows = S.POSITION_SHARD_ROWS
    n = points.num_rows
    count = 0
    total_bytes = 0
    for shard, start in enumerate(range(0, n, rows)):
        piece = points.slice(start, min(rows, n - start))
        path = WEB_DATA_DIR / S.points_shard(shard)
        write_arrow(piece, path)
        total_bytes += path.stat().st_size
        count = shard + 1
    log.info(f"position shards: {count} x {rows:,} rows "
             f"({total_bytes / 1e6:.1f} MB, ~{total_bytes / max(count, 1) / 1e3:.0f} KB each)")
    return count, rows


def _emit_import_index(corpus: pl.DataFrame) -> bool:
    """External-id lookup so a reader's own library can be matched to corpus nodes.

    Written once, fetched on demand — the import flow is an explicit user action, so a few MB
    at that moment is affordable in a way the startup bundle is not.
    """
    # Strip only a trailing version suffix. Splitting on "v" would maul the old-style archive
    # ids that contain one — "solv-int/9801001v1" becomes "sol".
    ids = [
        (a or "")
        for a in corpus.select(
            pl.col("arxiv_id").fill_null("").str.replace(r"v\d+$", "")
        )["arxiv_id"].to_list()
    ]
    table = pa.table({
        "node_id": pa.array(corpus["node_id"].to_list(), pa.int32()),
        "arxiv_id": pa.array(ids, pa.string()),
    }, schema=S.IMPORT_INDEX_SCHEMA)
    path = WEB_DATA_DIR / S.IMPORT_INDEX
    write_arrow(table, path)
    known = sum(1 for x in ids if x)
    log.info(f"import index: {known:,}/{len(ids):,} papers carry an arXiv id "
             f"({path.stat().st_size / 1e6:.1f} MB)")
    return True


def _emit_title_chunks(corpus: pl.DataFrame) -> tuple[list[str], int]:
    """Titles as sequential chunks, so the frontend can fill them in progressively."""
    node_ids = corpus["node_id"].to_list()
    titles = corpus["title"].to_list()
    rows = S.TITLE_CHUNK_ROWS
    paths: list[str] = []
    for chunk, start in enumerate(range(0, len(node_ids), rows)):
        end = min(start + rows, len(node_ids))
        table = pa.table({
            "node_id": pa.array(node_ids[start:end], pa.int32()),
            "title": pa.array(titles[start:end], pa.string()),
        }, schema=S.TITLES_SCHEMA)
        name = S.titles_chunk(chunk)
        write_arrow(table, WEB_DATA_DIR / name)
        paths.append(name)
    log.info(f"title chunks: {len(paths)} x {rows:,} rows")
    return paths, rows


def _emit_author_papers(corpus: pl.DataFrame) -> tuple[list[str], int, int]:
    """Inverted author index: author_id -> the nodes they wrote, sharded by author id.

    The author filter used to scan every paper's author_ids, which meant shipping 18.2 MB in the
    eager bundle and walking 912k rows per filter change. Inverting it makes selecting an author
    a lookup of one shard.
    """
    from collections import defaultdict

    by_author: dict[int, list[int]] = defaultdict(list)
    node_ids = corpus["node_id"].to_list()
    for node, aids in zip(node_ids, _local_author_ids(corpus)):
        for a in aids:
            by_author[a].append(node)

    size = S.AUTHOR_PAPERS_SHARD_SIZE
    shards: dict[int, list[int]] = defaultdict(list)
    for a in by_author:
        shards[a // size].append(a)

    src = read_arrow(ARTIFACTS_DIR / S.AUTHORS)
    oa_by_id = dict(zip(src["author_id"].to_pylist(), src["openalex_id"].to_pylist()))

    paths: list[str] = []
    for shard in sorted(shards):
        authors = sorted(shards[shard])
        table = pa.table({
            "author_id": pa.array(authors, pa.int32()),
            "node_ids": pa.array([by_author[a] for a in authors], pa.list_(pa.int32())),
            "openalex_id": pa.array([oa_by_id.get(a, "") for a in authors], pa.string()),
        }, schema=S.AUTHOR_PAPERS_SCHEMA)
        name = S.author_papers_shard(shard)
        write_arrow(table, WEB_DATA_DIR / name)
        paths.append(name)
    log.info(f"author->papers index: {len(by_author):,} authors across {len(paths)} shards")
    return paths, size, len(by_author)


def _build_paper_detail(corpus: pl.DataFrame) -> pa.Table:
    """Per-node detail shown only when a paper is selected (author names, venue, ids)."""
    def nz(name):
        return [v if v else None for v in corpus[name].to_list()]
    return pa.table({
        "node_id": pa.array(corpus["node_id"].to_list(), pa.int32()),
        "paper_id": pa.array(corpus["paper_id"].to_list(), pa.string()),
        "publication_date": pa.array(corpus["publication_date"].to_list(), pa.string()),
        "doi": pa.array(nz("doi"), pa.string()),
        "arxiv_id": pa.array(nz("arxiv_id"), pa.string()),
        "venue": pa.array(nz("venue"), pa.string()),
        "author_names": pa.array(corpus["author_names"].to_list(), pa.list_(pa.string())),
        "author_ids": pa.array(_local_author_ids(corpus), pa.list_(pa.int32())),
        "reference_count": pa.array(_reference_counts(corpus), pa.int32()),
    }, schema=S.PAPER_DETAIL_SCHEMA)


def _reference_counts(corpus: pl.DataFrame) -> list[int]:
    """S2's TOTAL reference count per paper; -1 when S2 has no reference list at all.

    The References tab can only draw edges whose other end is also in this corpus, so a paper
    citing 18 works of which 5 are arXiv CS shows 5. Without the total that reads as a claim
    about the paper ("it has 5 references") rather than about the map.
    """
    if not REF_AVAIL_IN.exists():
        return [-1] * corpus.height
    flags = pl.read_parquet(REF_AVAIL_IN)
    if "reference_count" not in flags.columns:
        log.warn(f"{REF_AVAIL_IN.name} predates reference_count — rerun "
                 "build_reference_availability.py to show reference totals")
        return [-1] * corpus.height
    joined = corpus.select("node_id").join(flags, on="node_id", how="left")
    return [
        int(c) if c is not None and avail else -1
        for c, avail in zip(joined["reference_count"].to_list(),
                            joined["references_available"].to_list())
    ]


def _emit_paper_detail_shards(detail: pa.Table) -> tuple[list[str], int]:
    """Shard per-node paper detail by fixed node-id block (same scheme as neighbors)."""
    size = S.PAPER_SHARD_SIZE
    n = detail.num_rows
    paths: list[str] = []
    for shard, start in enumerate(range(0, n, size)):
        chunk = detail.slice(start, min(size, n - start))
        name = S.paper_detail_shard(shard)
        write_arrow(chunk, WEB_DATA_DIR / name)
        paths.append(name)
    return paths, size


def _month_index(corpus: pl.DataFrame, date_from: str) -> np.ndarray:
    """Months elapsed from the corpus start month to each paper's publication month (>=0).

    Computed here (not derived from papers at load) so the GPU date filter needs no paper
    metadata — papers detail is now fetched on demand.

    The origin comes from the CORPUS, not from ``cfg.corpus.date_from``. The manifest already
    reports the range as ``corpus["publication_date"].min()``, so taking the origin from config
    lets the two disagree — and they did: after the all-years merge the config still said
    2025-01-01, so `max(0, ...)` clamped all 650,513 pre-2025 papers into month 0. The date
    histogram showed one bar holding 71% of the corpus and the date filter could not select any
    pre-2025 window at all. Deriving both ends from the same source makes that drift impossible.
    """
    dates = [d for d in corpus["publication_date"].to_list() if d and d[:4].isdigit()]
    corpus_min = min(dates) if dates else date_from
    if corpus_min[:4] != date_from[:4]:
        log.warn(
            f"month_index origin: using corpus min {corpus_min[:7]} rather than "
            f"config date_from {date_from[:7]} (config is stale for this corpus)"
        )
    date_from = corpus_min
    from_year = int(date_from[:4])
    out = np.zeros(corpus.height, dtype=np.int16)
    for i, d in enumerate(corpus["publication_date"].to_list()):
        if not d:
            continue
        y = int(d[:4]) if d[:4].isdigit() else from_year
        m = int(d[5:7]) if len(d) >= 7 and d[5:7].isdigit() else 1
        out[i] = max(0, (y - from_year) * 12 + (m - 1))
    return out


def _local_author_ids(corpus: pl.DataFrame) -> list[list[int]]:
    """Map OpenAlex author ids in each paper to dense local author ids from authors.arrow."""
    authors = read_arrow(ARTIFACTS_DIR / S.AUTHORS)
    oa_to_local = {oa: lid for oa, lid in
                   zip(authors["openalex_id"].to_pylist(),
                       authors["author_id"].to_pylist())}
    out = []
    for aids in corpus["author_ids"].to_list():
        out.append([oa_to_local[a] for a in aids if a in oa_to_local])
    return out


def _inst_placeholder(corpus: pl.DataFrame) -> list[list[int]]:
    # MVP: institution filtering uses orgs.json node_ids, so we ship an empty list here
    # to keep papers.arrow small. (Reserved column for Phase 2 dept/lab work.)
    return [[] for _ in range(corpus.height)]


def _neighbors_table() -> pa.Table:
    d = np.load(NEIGHBORS_IN)
    ids, scores = d["ids"], d["scores"]
    n, k = ids.shape
    neighbor_ids = [ids[i][ids[i] >= 0].tolist() for i in range(n)]
    neighbor_scores = [scores[i][:len(neighbor_ids[i])].tolist() for i in range(n)]
    return pa.table({
        "node_id": pa.array(list(range(n)), pa.int32()),
        "neighbor_ids": pa.array(neighbor_ids, pa.list_(pa.int32())),
        "scores": pa.array(neighbor_scores, pa.list_(pa.float32())),
    }, schema=S.NEIGHBORS_SCHEMA)


def _emit_neighbor_shards(neighbors: pa.Table) -> tuple[list[str], int]:
    """Write neighbors as fixed node-id-block shards for on-demand loading.

    node_id is dense (0..N-1) and the table is emitted in node order, so shard ``s`` holds
    rows [s*SIZE, (s+1)*SIZE). The frontend computes shard = node_id // SIZE and fetches
    only that file when a paper is selected. Returns (shard paths, shard_size).
    """
    size = S.NEIGHBOR_SHARD_SIZE
    n = neighbors.num_rows
    paths: list[str] = []
    for shard, start in enumerate(range(0, n, size)):
        chunk = neighbors.slice(start, min(size, n - start))
        name = S.neighbors_shard(shard)
        write_arrow(chunk, WEB_DATA_DIR / name)
        paths.append(name)
    return paths, size


def _edges_table(corpus: pl.DataFrame, max_per_paper: int) -> pa.Table:
    """Citation edges for the browser, optionally capped per paper.

    edges.arrow is fetched eagerly at startup and turned into citesOut/citedBy Maps, so its
    size is paid on every load. The full 912k graph (13,006,390 edges) measured 99.3 MB /
    74.7 MB gzipped and pushed time-to-first-map to 27.7 s with a 1,020 MB JS heap.

    The cap keeps each paper's strongest `max_per_paper` links in EACH direction, ranked by how
    cited the paper at the other end is — so what survives is the part of the network a reader
    would look at first. Capping per paper rather than globally is what preserves coverage:
    a global "top N edges" cut would strip the long tail entirely, whereas this leaves the same
    811,364 papers connected and only thins hubs.
    """
    d = np.load(EDGES_IN)
    src, dst = d["src"], d["dst"]

    if max_per_paper > 0:
        n = corpus.height
        cites = np.zeros(n, dtype=np.int64)
        cites[corpus["node_id"].to_numpy()] = (
            corpus["cited_by_count"].fill_null(0).to_numpy()
        )
        # Rank each paper's edges by the OTHER endpoint's citation count, then keep the first
        # `max_per_paper` of each group. lexsort orders by group, then by -cites within it.
        keep = np.zeros(len(src), dtype=bool)
        for group, partner in ((src, dst), (dst, src)):
            order = np.lexsort((-cites[partner], group))
            g = group[order]
            # Position of each edge within its group: index minus where the group starts.
            rank = np.arange(len(g)) - np.searchsorted(g, g)
            keep[order[rank < max_per_paper]] = True
        src, dst = src[keep], dst[keep]
        log.info(
            f"edges capped at {max_per_paper}/paper/direction: "
            f"{len(d['src']):,} -> {len(src):,} "
            f"({len(src) / max(len(d['src']), 1) * 100:.1f}%)"
        )

    return pa.table({
        "src": pa.array(src.tolist(), pa.int32()),
        "dst": pa.array(dst.tolist(), pa.int32()),
    }, schema=S.EDGES_SCHEMA)


def _emit_edge_tiles(edges: pa.Table, reveal_levels: np.ndarray) -> list[S.EdgeTile]:
    """Split the citation graph by reveal level, so a visit fetches only drawable edges.

    edges.arrow was 87 MB gzipped and was fetched in full after first paint, on every visit,
    regardless of where the user looked — the single largest artifact on the wire. But an edge
    can only be drawn when BOTH its endpoints are visible, and at the home view exactly 408 of
    its 14,303,089 edges qualify.

    An edge therefore belongs to the tier of its DEEPER endpoint, and the frontend loads edge
    tier N alongside point tile N. Measured, cumulative gzipped:

        L0 3 KB | L4 1.8 MB | L7 51 MB | all 87 MB

    Nothing is dropped: the whole graph is still available, it just arrives with the points
    that make it drawable.
    """
    src = edges.column("src").to_numpy()
    dst = edges.column("dst").to_numpy()
    # Guard: an edge referencing a node outside the corpus would index out of bounds, and
    # silently clipping it would put the edge in the wrong tier.
    n = len(reveal_levels)
    valid = (src >= 0) & (src < n) & (dst >= 0) & (dst < n)
    if not valid.all():
        log.info(f"  dropping {int((~valid).sum()):,} edges with an out-of-range endpoint")
        src, dst = src[valid], dst[valid]

    tier = np.maximum(reveal_levels[src], reveal_levels[dst])
    tiles: list[S.EdgeTile] = []
    cumulative = 0
    for level in range(int(tier.max()) + 1 if len(tier) else 0):
        mask = tier == level
        rows = int(mask.sum())
        if rows == 0:
            continue
        cumulative += rows
        table = pa.table({
            "src": pa.array(src[mask], pa.int32()),
            "dst": pa.array(dst[mask], pa.int32()),
        }, schema=S.EDGES_SCHEMA)
        path = WEB_DATA_DIR / S.edges_tile(level)
        write_arrow(table, path)
        tiles.append(S.EdgeTile(level=level, path=S.edges_tile(level), rows=rows,
                                cumulative=cumulative, bytes=path.stat().st_size))
    log.info(f"edge tiles: {len(tiles)} levels, {cumulative:,} edges "
             f"({tiles[0].rows:,} drawable at the home view)" if tiles else "edge tiles: none")
    return tiles


def _emit_org_nodes(orgs: dict) -> tuple[int, int]:
    """Move DIRECTORY institutions' node_ids out of orgs.json into shards, in place.

    orgs.json is 5.05 MB gzipped and 94% of it is `node_ids`; 1,370,907 of the 1,489,472 ids
    belong to the 10,475 search-only directory entries, whose membership nothing reads until
    someone selects one. Moving them out leaves 0.64 MB on the critical path.

    The build-machine copy under data/artifacts keeps its ids inline — only the published copy
    is slimmed — so nothing downstream of this stage loses information.
    """
    insts = orgs["institutions"]
    directory = sorted(k for k, v in insts.items() if not v.get("curated"))
    n_shards = 0
    moved = 0
    for shard, start in enumerate(range(0, len(directory), S.ORG_SHARD_ORGS)):
        chunk = directory[start:start + S.ORG_SHARD_ORGS]
        table = pa.table(
            {"org_key": chunk, "node_ids": [insts[k].get("node_ids") or [] for k in chunk]},
            schema=S.ORG_NODES_SCHEMA,
        )
        write_arrow(table, WEB_DATA_DIR / S.org_nodes_shard(shard))
        for k in chunk:
            moved += len(insts[k].get("node_ids") or [])
            insts[k]["node_ids"] = []
            insts[k]["node_shard"] = shard
        n_shards = shard + 1
    kept = sum(len(v.get("node_ids") or []) for v in insts.values())
    log.info(f"org nodes: {moved:,} ids -> {n_shards} shards; {kept:,} ids stay inline "
             f"(curated tree, needed for color-by-org before any selection)")
    return n_shards, S.ORG_SHARD_ORGS


def run(cfg: Config | None = None, built_at: str | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s11_emit")

    corpus = pl.read_parquet(CORPUS_IN)
    coords = read_npy(COORDS_IN)
    clusters = read_npy(CLUSTER_IN)
    reveal_levels = read_npy(REVEAL_IN).astype(np.int64)
    month_index = _month_index(corpus, cfg.corpus.date_from)

    # Big Arrow tables. points.arrow keeps the full corpus (reference / non-tiled fallback);
    # per-level tiles are what the frontend actually fetches.
    region_leaf, regions_table = _region_tables(corpus.height)
    points = _build_points(corpus, coords, clusters, reveal_levels, month_index, region_leaf)
    write_arrow(regions_table, WEB_DATA_DIR / S.REGIONS)
    write_arrow(points, WEB_DATA_DIR / S.POINTS)
    point_tiles = _emit_point_tiles(points, reveal_levels)
    n_position_shards, position_shard_rows = _emit_position_shards(points)

    # First-figure crops baked by s13 (optional). The index tells the resident papers index
    # which papers have a crop; the crop PNGs themselves are served on demand and are NOT
    # tracked per-file in the manifest (there can be hundreds of thousands).
    figures_index = INTERIM_DIR / "figures_index.json"
    figure_nodes = set(read_json(figures_index)["node_ids"]) if figures_index.exists() else set()

    # Papers: resident search/list index (whole) + per-node detail (sharded, on demand).
    # The legacy whole papers.arrow is still written for reference / fallback.
    # LOCAL ONLY — see schema.LOCAL_ONLY_FILES. Kept for offline inspection, never published:
    # nothing fetches it, and at 276 MB it is over GitHub's per-file limit.
    write_arrow(_build_papers(corpus), WEB_DATA_DIR / S.PAPERS)
    write_arrow(
        _build_papers_index(
            corpus,
            figure_nodes,
            default_citation_available=cfg.corpus.source == "openalex",
        ),
        WEB_DATA_DIR / S.PAPERS_INDEX,
    )
    paper_shards, paper_shard_size = _emit_paper_detail_shards(_build_paper_detail(corpus))
    author_paper_paths, author_papers_shard_size, n_indexed_authors = _emit_author_papers(corpus)
    title_paths, title_chunk_rows = _emit_title_chunks(corpus)
    has_import_index = _emit_import_index(corpus)

    # Neighbors are sharded for on-demand related-works loading (not shipped whole).
    neighbor_shards, neighbor_shard_size = _emit_neighbor_shards(_neighbors_table())
    _edges = _edges_table(corpus, cfg.emit.max_edges_per_paper)
    edge_tiles = _emit_edge_tiles(_edges, reveal_levels)
    # The whole graph is still written for local inspection and as a fallback for older
    # frontends, but it is 110 MB — over GitHub's per-file limit — and is NOT published.
    write_arrow(_edges, WEB_DATA_DIR / S.EDGES)

    # Copy JSON + authors artifacts from data/artifacts. clusters.json is intentionally NOT
    # shipped to the browser: the frontend never reads its per-region array (24k+ entries,
    # ~7MB now / ~40MB at 390k — the single largest artifact), and the only thing needed
    # from it (the zoom `levels`) goes into the manifest below. Semantic-zoom labels come
    # from labels.json. Keeping it out of web/public/data is the biggest initial-load win.
    for fname in (S.LABELS, S.TOPICS):
        write_json(read_json(ARTIFACTS_DIR / fname), WEB_DATA_DIR / fname)
    # orgs.json is published SLIM: directory membership moves to org-nodes-{N}.arrow.
    _orgs = read_json(ARTIFACTS_DIR / S.ORGS)
    n_org_shards, org_shard_orgs = _emit_org_nodes(_orgs)
    write_json(_orgs, WEB_DATA_DIR / S.ORGS)
    # Slim, chunked author search index. openalex_id (40.3% of the old file) now rides in the
    # author-papers shards, which are already loaded whenever the author panel can appear.
    _src = read_arrow(ARTIFACTS_DIR / S.AUTHORS)
    _n = _src.num_rows
    author_chunk_paths: list[str] = []
    for _c, _start in enumerate(range(0, _n, S.AUTHOR_CHUNK_ROWS)):
        _end = min(_start + S.AUTHOR_CHUNK_ROWS, _n)
        _slice = _src.slice(_start, _end - _start)
        _tbl = pa.table({
            "author_id": _slice["author_id"],
            "name": _slice["name"],
            "count": _slice["count"],
            "verified": pa.array(
                [not str(v or "").startswith("arxiv-name:")
                 for v in _slice["openalex_id"].to_pylist()],
                pa.bool_(),
            ),
        }, schema=S.AUTHORS_SEARCH_SCHEMA)
        _name = S.authors_chunk(_c)
        write_arrow(_tbl, WEB_DATA_DIR / _name)
        author_chunk_paths.append(_name)
    log.info(f"author search chunks: {len(author_chunk_paths)} x {S.AUTHOR_CHUNK_ROWS:,} rows")

    # Manifest.
    embed_meta = read_json(EMBED_META_IN)
    levels = read_json(ARTIFACTS_DIR / S.CLUSTERS)["levels"]  # from artifacts, not the bundle

    # PAPERS and NEIGHBORS are sharded (or index-split), not shipped whole in the load path.
    tracked = (
        [f for f in S.ALL_FILES if f not in (S.NEIGHBORS, S.PAPERS)]
        + [t.path for t in point_tiles]
        + neighbor_shards
        + paper_shards
    )
    files: dict[str, S.FileMeta] = {}
    for fname in tracked:
        if fname == S.MANIFEST:
            continue
        p = WEB_DATA_DIR / fname
        if not p.exists():
            continue
        rows = None
        if fname.endswith(".arrow"):
            rows = read_arrow(p).num_rows
        files[fname] = S.FileMeta(path=fname, bytes=p.stat().st_size,
                                  sha256=sha256_file(p), rows=rows)

    enrichment_source = None
    enrichment_coverage = None
    if "openalex_id" in corpus.columns:
        matched = int(corpus["openalex_id"].is_not_null().sum())
        if matched:
            enrichment_source = "OpenAlex"
            enrichment_coverage = matched / corpus.height if corpus.height else 0.0

    # S2 citation data is only considered canonical when s16 committed it into this exact
    # corpus. The small metadata file records the immutable S2AG snapshot release and makes
    # the UI/provider label truthful without loading a per-paper sidecar in the browser.
    s2_citation_source = None
    if "s2_citation_available" in corpus.columns and bool(corpus["s2_citation_available"].any()):
        s2_meta = read_json(S2_CITATION_META_IN) if S2_CITATION_META_IN.exists() else {}
        release_id = s2_meta.get("release_id")
        s2_citation_source = (
            f"Semantic Scholar S2AG ({release_id})" if release_id else "Semantic Scholar S2AG"
        )

    openalex_citation_source = None
    if "openalex_citation_available" in corpus.columns and bool(corpus["openalex_citation_available"].any()):
        openalex_citation_source = "OpenAlex"

    manifest = S.Manifest(
        schema_version=cfg.schema_version,
        built_at=built_at or _dt.datetime.now(_dt.timezone.utc).isoformat(),
        corpus=S.CorpusMeta(
            count=corpus.height,
            # Report observed corpus bounds, not a configured future cutoff that creates
            # empty months in the UI. Identify the selected source truthfully as well.
            date_from=str(corpus["publication_date"].min()),
            date_to=str(corpus["publication_date"].max()),
            field=("arxiv:cs.*|stat.ML" if cfg.corpus.source == "arxiv_snapshot"
                   else cfg.corpus.field_id),
            orgs=[o.display_name for o in cfg.corpus.orgs],
            # Cornell/arXiv bulk metadata contains neither citation counts nor references.
            # Keep its numeric compatibility columns at zero for ranking math, but mark the
            # data unavailable so the browser never claims those sentinels are real counts.
            citation_count_source=(
                f"{s2_citation_source} + OpenAlex fallback" if s2_citation_source and openalex_citation_source
                else s2_citation_source or openalex_citation_source
                or ("OpenAlex" if cfg.corpus.source == "openalex" else None)
            ),
            citation_graph_source=(
                s2_citation_source or openalex_citation_source
                or ("OpenAlex" if cfg.corpus.source == "openalex" else None)
            ),
            metadata_enrichment_source=enrichment_source,
            metadata_enrichment_coverage=enrichment_coverage,
        ),
        embedding=S.EmbeddingMeta(backend=embed_meta["backend"],
                                  model=embed_meta["model"], dim=embed_meta["dim"]),
        projector={"method": cfg.projector.method,
                   "n_neighbors": cfg.projector.n_neighbors},
        levels=[S.LevelBand(**lv) for lv in levels],
        files=files,
        point_tiles=point_tiles,
        # The frontend needs this to keep dot radius under the separation the thinning
        # guarantees (screen separation = viewport_width / base_divisor). Shipping it beats
        # duplicating the constant in usePointsLayer, where it silently went stale on mobile.
        tiling_base_divisor=cfg.tiling.base_divisor,
        neighbor_shard_size=neighbor_shard_size,
        n_neighbor_shards=len(neighbor_shards),
        paper_shard_size=paper_shard_size,
        author_papers_shard_size=author_papers_shard_size,
        title_chunk_rows=title_chunk_rows,
        n_title_chunks=len(title_paths),
        n_indexed_authors=n_indexed_authors,
        n_author_chunks=len(author_chunk_paths),
        has_import_index=has_import_index,
        month_histogram=_month_histogram(month_index),
        n_position_shards=n_position_shards,
        position_shard_rows=position_shard_rows,
        n_org_shards=n_org_shards,
        org_shard_orgs=org_shard_orgs,
        edge_tiles=edge_tiles,
        author_chunk_rows=S.AUTHOR_CHUNK_ROWS,
        n_paper_shards=len(paper_shards),
        figures=(S.FiguresMeta(count=len(figure_nodes)) if figure_nodes else None),
        palette={"background": cfg.palette.background},
    )
    write_json(manifest, WEB_DATA_DIR / S.MANIFEST)

    total_bytes = sum(f.bytes for f in files.values())
    log.info(f"emitted {len(files)} files, {total_bytes/1e6:.1f} MB -> {WEB_DATA_DIR}")
    for name, f in sorted(files.items()):
        log.info(f"  {name}: {f.bytes/1e3:.0f} KB" + (f" ({f.rows} rows)" if f.rows else ""))
    return str(WEB_DATA_DIR / S.MANIFEST)


if __name__ == "__main__":
    run()
