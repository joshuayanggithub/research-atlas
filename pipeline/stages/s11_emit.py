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

# Same palette as s07 so points match cluster label colors.
_PALETTE = [
    [99, 179, 237], [246, 173, 85], [104, 211, 145], [237, 100, 166],
    [159, 122, 234], [246, 224, 94], [79, 209, 197], [252, 129, 129],
    [144, 205, 244], [183, 148, 244], [104, 211, 145], [237, 137, 54],
]


def _build_points(corpus: pl.DataFrame, coords: np.ndarray,
                  clusters: np.ndarray, reveal_levels: np.ndarray,
                  month_index: np.ndarray) -> pa.Table:
    n = corpus.height
    sub_ids = corpus["subfield_id"].to_list()
    uniq = sorted(set(sub_ids))
    sub_color = {s: _PALETTE[i % len(_PALETTE)] for i, s in enumerate(uniq)}
    rgb = np.array([sub_color.get(s, [160, 160, 160]) for s in sub_ids], dtype=np.uint8)

    return pa.table({
        "node_id": pa.array(corpus["node_id"].to_list(), pa.int32()),
        "x": pa.array(coords[:, 0], pa.float32()),
        "y": pa.array(coords[:, 1], pa.float32()),
        "year": pa.array(corpus["year"].to_list(), pa.int16()),
        "cited_by_count": pa.array(corpus["cited_by_count"].to_list(), pa.int32()),
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


def _build_papers_index(corpus: pl.DataFrame) -> pa.Table:
    """Resident search/list index: the fields every all-N consumer needs (search, list
    rows, sorting, the author filter). Ships whole; heavier fields go to lazy detail."""
    return pa.table({
        "node_id": pa.array(corpus["node_id"].to_list(), pa.int32()),
        "title": pa.array(corpus["title"].to_list(), pa.string()),
        "author_ids": pa.array(_local_author_ids(corpus), pa.list_(pa.int32())),
        "cited_by_count": pa.array(corpus["cited_by_count"].to_list(), pa.int32()),
        "year": pa.array(corpus["year"].to_list(), pa.int16()),
    }, schema=S.PAPERS_INDEX_SCHEMA)


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
    }, schema=S.PAPER_DETAIL_SCHEMA)


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
    """
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


def _edges_table() -> pa.Table:
    d = np.load(EDGES_IN)
    return pa.table({
        "src": pa.array(d["src"].tolist(), pa.int32()),
        "dst": pa.array(d["dst"].tolist(), pa.int32()),
    }, schema=S.EDGES_SCHEMA)


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
    points = _build_points(corpus, coords, clusters, reveal_levels, month_index)
    write_arrow(points, WEB_DATA_DIR / S.POINTS)
    point_tiles = _emit_point_tiles(points, reveal_levels)

    # Papers: resident search/list index (whole) + per-node detail (sharded, on demand).
    # The legacy whole papers.arrow is still written for reference / fallback.
    write_arrow(_build_papers(corpus), WEB_DATA_DIR / S.PAPERS)
    write_arrow(_build_papers_index(corpus), WEB_DATA_DIR / S.PAPERS_INDEX)
    paper_shards, paper_shard_size = _emit_paper_detail_shards(_build_paper_detail(corpus))

    # Neighbors are sharded for on-demand related-works loading (not shipped whole).
    neighbor_shards, neighbor_shard_size = _emit_neighbor_shards(_neighbors_table())
    write_arrow(_edges_table(), WEB_DATA_DIR / S.EDGES)

    # Copy JSON + authors artifacts from data/artifacts. clusters.json is intentionally NOT
    # shipped to the browser: the frontend never reads its per-region array (24k+ entries,
    # ~7MB now / ~40MB at 390k — the single largest artifact), and the only thing needed
    # from it (the zoom `levels`) goes into the manifest below. Semantic-zoom labels come
    # from labels.json. Keeping it out of web/public/data is the biggest initial-load win.
    for fname in (S.LABELS, S.ORGS, S.TOPICS):
        write_json(read_json(ARTIFACTS_DIR / fname), WEB_DATA_DIR / fname)
    write_arrow(read_arrow(ARTIFACTS_DIR / S.AUTHORS), WEB_DATA_DIR / S.AUTHORS)

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

    manifest = S.Manifest(
        schema_version=cfg.schema_version,
        built_at=built_at or _dt.datetime.now(_dt.timezone.utc).isoformat(),
        corpus=S.CorpusMeta(
            count=corpus.height,
            date_from=cfg.corpus.date_from, date_to=cfg.corpus.date_to,
            field=cfg.corpus.field_id,
            orgs=[o.display_name for o in cfg.corpus.orgs],
        ),
        embedding=S.EmbeddingMeta(backend=embed_meta["backend"],
                                  model=embed_meta["model"], dim=embed_meta["dim"]),
        projector={"method": cfg.projector.method,
                   "n_neighbors": cfg.projector.n_neighbors},
        levels=[S.LevelBand(**lv) for lv in levels],
        files=files,
        point_tiles=point_tiles,
        neighbor_shard_size=neighbor_shard_size,
        n_neighbor_shards=len(neighbor_shards),
        paper_shard_size=paper_shard_size,
        n_paper_shards=len(paper_shards),
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
