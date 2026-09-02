"""s06: Build the nested semantic-zoom hierarchy.

The default method is ``"planar"``: recursive **Leiden** over a graph whose *adjacency* is
the 2D layout's kNN and whose *edge weights* are 768-D embedding cosine. Every child is an
exact subset of its parent, so zoom levels cannot contradict one another.

Why the adjacency is planar
---------------------------
A region is only meaningful to a user if it reads as one contiguous area of the map. The
previous default detected communities in the *fused 768-D + citation* graph, which is not
planar: two papers can be graph-neighbors while sitting at opposite corners of the t-SNE
layout. Measured on the 28k-paper corpus, each band-0 "continent" was scattered across
~122 disconnected on-screen fragments, and only 32% of a node's 10 nearest on-screen
neighbors shared its region at band 6. Visually that is indistinguishable from bad
embeddings — unrelated papers appear grouped, and every region overlaps every other.

Restricting adjacency to the 2D kNN graph makes regions contiguous *by construction*
(~1-2 fragments/region) and, because t-SNE preserves this corpus's neighborhoods well
(kNN purity 0.69 in 768-D vs 0.68 in 2D), it costs no semantic accuracy. It in fact
*improves* it — topic purity rises (band 0: 0.19 -> 0.31; band 3: 0.43 -> 0.54) because
communities are no longer stitched together across unrelated parts of the map. Weights
stay in 768-D so that *which* papers group within a neighborhood remains semantic; the
layout only decides what is adjacent.

Resolution is found by bisection to hit the requested child count, replacing the previous
over-segment-then-agglomerate heuristic. That heuristic was doing the real partitioning
work: the configured resolution sweep returned 2 communities at band 0 where 8 were
requested, so the shape of the map came from post-hoc centroid agglomeration rather than
from community detection.

Leiden (not Louvain) because Louvain can leave communities badly connected or internally
disconnected, which worsens under recursive per-parent splitting ("From Louvain to
Leiden", Traag et al., Nature Sci Rep 2019). ``"leiden"``/``"louvain"`` (fused 768-D +
citation graph) and the legacy 2D ``"kmeans"``/``"quadtree"`` methods remain selectable
for controlled comparison.

Emits:
    data/interim/tiles.json  {levels:[...], cells:[{id,level,cx,cy,count,bbox,
                               parent,node_idx:[...]}]}
"""

from __future__ import annotations

import networkx as nx
import numpy as np
from sklearn.cluster import AgglomerativeClustering, KMeans

from pipeline.common import log
from pipeline.common.io import read_npy, write_json
from pipeline.config import INTERIM_DIR, Config, ensure_dirs, load_config

# Graph community-detection methods that run over the fused semantic+citation graph
# (as opposed to the legacy 2D "kmeans"/"quadtree" spatial methods).
_GRAPH_METHODS = ("leiden", "louvain")

# The default: 2D-layout adjacency, 768-D cosine weights (see module docstring).
_PLANAR_METHOD = "planar"

COORDS_IN = INTERIM_DIR / "coords2d.npy"
VECTORS_IN = INTERIM_DIR / "embeddings.npy"
NEIGHBORS_IN = INTERIM_DIR / "neighbors.npz"
EDGES_IN = INTERIM_DIR / "edges.npz"
OUT = INTERIM_DIR / "tiles.json"

_RESOLUTIONS = (0.08, 0.12, 0.18, 0.26, 0.38, 0.55, 0.8, 1.15, 1.65)


def _bbox(pts: np.ndarray) -> list[float]:
    return [
        float(pts[:, 0].min()),
        float(pts[:, 1].min()),
        float(pts[:, 0].max()),
        float(pts[:, 1].max()),
    ]


def _make_cell(
    cell_id: int,
    level: int,
    idx: np.ndarray,
    coords: np.ndarray,
    parent: int | None,
) -> dict:
    pts = coords[idx]
    return {
        "id": cell_id,
        "level": level,
        "cx": float(pts[:, 0].mean()),
        "cy": float(pts[:, 1].mean()),
        "count": int(len(idx)),
        "bbox": _bbox(pts),
        "parent": parent,
        "node_idx": idx.astype(int).tolist(),
    }


def _centroid(idx: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    center = vectors[idx].astype(np.float64).mean(axis=0)
    norm = np.linalg.norm(center)
    return center / norm if norm > 0 else center


def _sort_groups(groups: list[np.ndarray]) -> list[np.ndarray]:
    return sorted(groups, key=lambda group: (-len(group), int(group.min())))


def _coarsen_groups(
    groups: list[np.ndarray],
    target: int,
    vectors: np.ndarray,
) -> list[np.ndarray]:
    """Merge an over-segmented graph partition by semantic centroid similarity."""
    if len(groups) <= target:
        return groups
    centers = np.vstack([_centroid(group, vectors) for group in groups])
    labels = AgglomerativeClustering(
        n_clusters=target,
        metric="cosine",
        linkage="average",
    ).fit_predict(centers)
    merged = [
        np.concatenate([groups[i] for i in range(len(groups)) if labels[i] == label])
        for label in range(target)
    ]
    return _sort_groups(merged)


def _merge_small_groups(
    groups: list[np.ndarray],
    min_points: int,
    vectors: np.ndarray,
) -> list[np.ndarray]:
    """Attach tiny graph fragments to the nearest viable semantic community."""
    large = [group for group in groups if len(group) >= min_points]
    small = [group for group in groups if len(group) < min_points]
    if not small:
        return _sort_groups(large)
    if not large:
        return []

    centers = np.vstack([_centroid(group, vectors) for group in large])
    additions: list[list[np.ndarray]] = [[] for _ in large]
    for group in small:
        target = int(np.argmax(centers @ _centroid(group, vectors)))
        additions[target].append(group)

    merged = [
        np.concatenate([group, *additions[i]]) if additions[i] else group
        for i, group in enumerate(large)
    ]
    return _sort_groups(merged)


def _embedding_split(
    idx: np.ndarray,
    vectors: np.ndarray,
    target: int,
    min_points: int,
    seed: int,
) -> list[np.ndarray]:
    """High-dimensional fallback for a graph region with too few internal edges."""
    k = min(target, len(idx) // min_points)
    if k < 2:
        return [idx]
    points = vectors[idx].astype(np.float64)

    # Deterministic spherical k-means. This small fallback avoids a macOS Accelerate bug
    # that can emit invalid float warnings for repeated tiny sklearn KMeans fits.
    mean = points.mean(axis=0)
    mean_norm = np.linalg.norm(mean)
    if mean_norm > 0:
        mean /= mean_norm
    first = int(np.argmax(np.einsum("ij,j->i", points, mean, optimize=True)))
    center_rows = [first]
    best_similarity = np.einsum(
        "ij,j->i",
        points,
        points[first],
        optimize=True,
    )
    for _ in range(1, k):
        next_row = int(np.argmin(best_similarity))
        center_rows.append(next_row)
        similarity = np.einsum(
            "ij,j->i",
            points,
            points[next_row],
            optimize=True,
        )
        best_similarity = np.maximum(best_similarity, similarity)

    centers = points[center_rows].copy()
    labels = np.zeros(len(points), dtype=np.int32)
    for _ in range(12):
        similarity = np.einsum("ij,kj->ik", points, centers, optimize=True)
        next_labels = np.argmax(similarity, axis=1).astype(np.int32)
        if np.array_equal(next_labels, labels):
            break
        labels = next_labels
        for label in range(k):
            members = points[labels == label]
            if len(members) == 0:
                continue
            center = members.mean(axis=0)
            norm = np.linalg.norm(center)
            centers[label] = center / norm if norm > 0 else center

    groups = [idx[labels == label] for label in range(k) if np.any(labels == label)]
    groups = _merge_small_groups(groups, min_points, vectors)
    return groups if len(groups) >= 2 else [idx]


def _fill_to_target(
    groups: list[np.ndarray],
    target: int,
    vectors: np.ndarray,
    min_points: int,
    seed: int,
) -> list[np.ndarray]:
    """Semantically bisect the largest viable groups until the branch target is met."""
    output = list(groups)
    attempt = 0
    while len(output) < target:
        candidates = [
            (len(group), index)
            for index, group in enumerate(output)
            if len(group) >= 2 * min_points
        ]
        if not candidates:
            break
        _, index = max(candidates)
        split = _embedding_split(
            output[index],
            vectors,
            target=2,
            min_points=min_points,
            seed=seed + attempt,
        )
        attempt += 1
        if len(split) < 2:
            break
        output[index:index + 1] = split
    return _sort_groups(output)


def _louvain_communities(
    subgraph: nx.Graph,
    resolution: float,
    seed: int,
) -> list[list[int]]:
    """NetworkX Louvain (retained as a selectable comparison baseline)."""
    return [
        list(group)
        for group in nx.community.louvain_communities(
            subgraph,
            weight="weight",
            resolution=resolution,
            seed=seed,
        )
    ]


def _leiden_communities(
    subgraph: nx.Graph,
    resolution: float,
    seed: int,
) -> list[list[int]]:
    """Leiden via the reference ``leidenalg``/``igraph`` implementation.

    Uses ``RBConfigurationVertexPartition`` — the same gamma-resolution modularity model
    as NetworkX Louvain's ``resolution`` — so the ``_RESOLUTIONS`` sweep behaves the same,
    while Leiden additionally guarantees internally connected communities. Imported lazily
    so the base environment still loads if the optional dependency is absent.
    """
    import igraph as ig
    import leidenalg

    nodes = list(subgraph.nodes())
    index = {node: position for position, node in enumerate(nodes)}
    edges = [(index[u], index[v]) for u, v in subgraph.edges()]
    weights = [float(data.get("weight", 1.0)) for _, _, data in subgraph.edges(data=True)]

    ig_graph = ig.Graph(n=len(nodes), edges=edges, directed=False)
    partition = leidenalg.find_partition(
        ig_graph,
        leidenalg.RBConfigurationVertexPartition,
        weights=weights,
        resolution_parameter=resolution,
        seed=seed,
    )
    return [[nodes[position] for position in community] for community in partition]


def _detect_communities(
    method: str,
    subgraph: nx.Graph,
    resolution: float,
    seed: int,
) -> list[list[int]]:
    if method == "louvain":
        return _louvain_communities(subgraph, resolution, seed)
    return _leiden_communities(subgraph, resolution, seed)


def _graph_split(
    idx: np.ndarray,
    graph: nx.Graph,
    vectors: np.ndarray,
    target: int,
    min_points: int,
    max_child_fraction: float,
    seed: int,
    method: str,
) -> list[np.ndarray]:
    """Split one parent into approximately ``target`` graph communities."""
    if len(idx) < 2 * min_points:
        return [idx]

    subgraph = graph.subgraph(idx)
    if subgraph.number_of_edges() < len(idx) // 2:
        return _embedding_split(idx, vectors, target, min_points, seed)

    groups: list[np.ndarray] | None = None
    for resolution in _RESOLUTIONS:
        communities = _detect_communities(method, subgraph, resolution, seed)
        groups = [
            np.asarray(sorted(group), dtype=np.int32)
            for group in communities
        ]
        # Prefer the first partition at or above the requested granularity. If it
        # overshoots, semantic centroid agglomeration below coarsens it exactly.
        if len(groups) >= target:
            break

    if not groups or len(groups) < 2:
        return _embedding_split(idx, vectors, target, min_points, seed)

    groups = _coarsen_groups(groups, target, vectors)
    groups = _merge_small_groups(groups, min_points, vectors)
    groups = _fill_to_target(groups, target, vectors, min_points, seed)
    if (
        groups
        and max(len(group) for group in groups) / len(idx)
        > max_child_fraction
    ):
        semantic_groups = _embedding_split(
            idx,
            vectors,
            target,
            min_points,
            seed,
        )
        semantic_groups = _fill_to_target(
            semantic_groups,
            target,
            vectors,
            min_points,
            seed,
        )
        if len(semantic_groups) >= 2:
            groups = semantic_groups
    if len(groups) < 2 or sum(len(group) for group in groups) != len(idx):
        return _embedding_split(idx, vectors, target, min_points, seed)
    return groups


def _build_fused_graph(
    neighbor_ids: np.ndarray,
    neighbor_scores: np.ndarray,
    edge_src: np.ndarray,
    edge_dst: np.ndarray,
    direct_citation_weight: float,
) -> nx.Graph:
    n = neighbor_ids.shape[0]
    graph = nx.Graph()
    graph.add_nodes_from(range(n))

    for source in range(n):
        for target, score in zip(neighbor_ids[source], neighbor_scores[source]):
            target = int(target)
            weight = float(score)
            if target < 0 or target == source or weight <= 0:
                continue
            old = graph.get_edge_data(source, target, {}).get("weight", 0.0)
            if weight > old:
                graph.add_edge(source, target, weight=weight)

    for source, target in zip(edge_src.tolist(), edge_dst.tolist()):
        if source == target:
            continue
        old = graph.get_edge_data(source, target, {}).get("weight", 0.0)
        if direct_citation_weight > old:
            graph.add_edge(source, target, weight=direct_citation_weight)
    return graph


def _build_graph_hierarchy(
    coords: np.ndarray,
    vectors: np.ndarray,
    graph: nx.Graph,
    cfg: Config,
    method: str,
) -> tuple[list[dict], list[dict]]:
    cells: list[dict] = []
    levels: list[dict] = []
    next_id = 0
    seed = cfg.cluster.random_state
    min_points = cfg.hierarchy.min_tile_points

    root_idx = np.arange(len(coords), dtype=np.int32)
    root_groups = _graph_split(
        root_idx,
        graph,
        vectors,
        cfg.hierarchy.root_clusters,
        min_points,
        cfg.hierarchy.max_child_fraction,
        seed,
        method,
    )
    band_regions: list[tuple[int, np.ndarray]] = []
    for group in root_groups:
        cell = _make_cell(next_id, 0, group, coords, None)
        cells.append(cell)
        band_regions.append((next_id, group))
        next_id += 1
    levels.append({"level": 0, "count": len(band_regions)})
    log.info(f"band 0: {len(band_regions)} fused-graph communities")

    for band in range(1, cfg.hierarchy.max_depth):
        next_band: list[tuple[int, np.ndarray]] = []
        for parent_id, parent_idx in band_regions:
            if len(parent_idx) < max(
                cfg.hierarchy.min_cluster_size,
                2 * cfg.hierarchy.min_tile_points,
            ):
                continue
            groups = _graph_split(
                parent_idx,
                graph,
                vectors,
                cfg.hierarchy.branching,
                min_points,
                cfg.hierarchy.max_child_fraction,
                seed + band * 100_000 + parent_id,
                method,
            )
            if len(groups) < 2:
                continue
            for group in groups:
                cell = _make_cell(next_id, band, group, coords, parent_id)
                cells.append(cell)
                next_band.append((next_id, group))
                next_id += 1
        levels.append({"level": band, "count": len(next_band)})
        log.info(f"band {band}: {len(next_band)} nested fused-graph communities")
        band_regions = next_band
        if not band_regions:
            break

    return cells, levels


def _build_planar_graph(
    coords: np.ndarray,
    vectors: np.ndarray,
    k_spatial: int,
):
    """Build the planar substrate: 2D-kNN adjacency, 768-D cosine weights.

    Returns an ``igraph.Graph`` because the recursive splitter takes many subgraphs, and
    igraph's ``subgraph`` + Leiden path is markedly faster than rebuilding NetworkX views.
    """
    import igraph as ig
    from scipy.spatial import cKDTree

    n = len(coords)
    # k+1 because the first hit of a point's own kNN query is itself.
    _, neighbor_rows = cKDTree(coords).query(coords, k=min(k_spatial + 1, n), workers=-1)
    neighbor_rows = np.asarray(neighbor_rows, dtype=np.int64)

    # Vectorised undirected dedup. The previous Python loop built a `set` of ~16M tuples plus
    # two int lists, which is several GB of object overhead for what is one integer key per
    # edge. Encoding the ordered pair as lo*n + hi is exact here: n < 2^31, so the key fits in
    # int64 with room to spare.
    rows = np.repeat(np.arange(n, dtype=np.int64), neighbor_rows.shape[1] - 1)
    cols = neighbor_rows[:, 1:].ravel()
    keep = cols != rows
    rows, cols = rows[keep], cols[keep]
    key = np.unique(np.minimum(rows, cols) * n + np.maximum(rows, cols))
    del rows, cols, keep, neighbor_rows
    src = key // n
    dst = key % n
    del key
    log.info(f"  planar substrate: {len(src):,} undirected spatial edges")

    # Semantic weight per spatial edge, computed in CHUNKS.
    #
    # `vectors[src]` is fancy indexing, so it COPIES: at 3.13M papers that is a
    # [15.7M, 768] float32 array — 44.8 GB — and the einsum needs two of them at once. That
    # allocation OOM-killed this stage (anon-rss 76.3 GB of 78 GB) and is the real source of
    # the "45.4 GB peak" this pipeline has long attributed to s07: the same expression costs
    # 13.1 GB per side at 912k papers. Chunking bounds it to ~1.5 GB per side regardless of
    # corpus size, at no cost to the result.
    #
    # Clipped at 0: Leiden's RB-configuration model treats weights as edge mass, so a negative
    # cosine must not subtract from it.
    weights = np.empty(len(src), dtype=np.float32)
    chunk = 500_000
    for start in range(0, len(src), chunk):
        stop = start + chunk
        weights[start:stop] = np.einsum(
            "ij,ij->i", vectors[src[start:stop]], vectors[dst[start:stop]], optimize=True
        )
    np.maximum(weights, 0.0, out=weights)

    graph = ig.Graph(n=n, edges=list(zip(src.tolist(), dst.tolist())), directed=False)
    graph.es["weight"] = weights.tolist()
    return graph


def _leiden_at_target(
    graph,
    target: int,
    seed: int,
    iterations: int = 18,
) -> list[np.ndarray]:
    """Bisect Leiden's resolution to land as close as possible to ``target`` communities.

    Community count rises monotonically with the RB-configuration resolution parameter, so
    a geometric bisection converges quickly and needs no hand-tuned resolution ladder. We
    keep the closest partition seen rather than requiring an exact hit, since a graph's
    achievable community counts are discrete.
    """
    import leidenalg

    low, high = 0.005, 400.0
    best: tuple[list[np.ndarray], int] | None = None
    for _ in range(iterations):
        resolution = (low * high) ** 0.5
        partition = leidenalg.find_partition(
            graph,
            leidenalg.RBConfigurationVertexPartition,
            weights=graph.es["weight"],
            resolution_parameter=resolution,
            seed=seed,
        )
        groups = [np.asarray(sorted(c), dtype=np.int32) for c in partition if len(c)]
        count = len(groups)
        if best is None or abs(count - target) < abs(best[1] - target):
            best = (groups, count)
        if count == target:
            break
        if count < target:
            low = resolution
        else:
            high = resolution
    return best[0] if best else []


def _planar_split(
    idx: np.ndarray,
    graph,
    vectors: np.ndarray,
    target: int,
    min_points: int,
    seed: int,
) -> list[np.ndarray]:
    """Split one parent region into ~``target`` contiguous communities."""
    if len(idx) < 2 * min_points:
        return [idx]

    subgraph = graph.subgraph(idx.tolist())
    if subgraph.ecount() < 2:
        # A parent with no internal spatial edges cannot be split contiguously; fall back
        # to the high-dimensional splitter so deep bands still subdivide.
        return _embedding_split(idx, vectors, target, min_points, seed)

    local_groups = _leiden_at_target(subgraph, target, seed)
    # Leiden returns positions within the subgraph; map back to global node indices.
    groups = [idx[group] for group in local_groups if len(group)]
    groups = _merge_small_groups(groups, min_points, vectors)
    if len(groups) < 2 or sum(len(g) for g in groups) != len(idx):
        return _embedding_split(idx, vectors, target, min_points, seed)
    return _sort_groups(groups)


def _build_planar_hierarchy(
    coords: np.ndarray,
    vectors: np.ndarray,
    graph,
    cfg: Config,
) -> tuple[list[dict], list[dict]]:
    cells: list[dict] = []
    levels: list[dict] = []
    next_id = 0
    seed = cfg.cluster.random_state
    min_points = cfg.hierarchy.min_tile_points

    root_groups = _planar_split(
        np.arange(len(coords), dtype=np.int32),
        graph,
        vectors,
        cfg.hierarchy.root_clusters,
        min_points,
        seed,
    )
    band_regions: list[tuple[int, np.ndarray]] = []
    for group in root_groups:
        cells.append(_make_cell(next_id, 0, group, coords, None))
        band_regions.append((next_id, group))
        next_id += 1
    levels.append({"level": 0, "count": len(band_regions)})
    log.info(f"band 0: {len(band_regions)} planar communities")

    for band in range(1, cfg.hierarchy.max_depth):
        next_band: list[tuple[int, np.ndarray]] = []
        for parent_id, parent_idx in band_regions:
            if len(parent_idx) < max(
                cfg.hierarchy.min_cluster_size,
                2 * cfg.hierarchy.min_tile_points,
            ):
                continue
            groups = _planar_split(
                parent_idx,
                graph,
                vectors,
                cfg.hierarchy.branching,
                min_points,
                seed + band * 100_000 + parent_id,
            )
            if len(groups) < 2:
                continue
            for group in groups:
                cells.append(_make_cell(next_id, band, group, coords, parent_id))
                next_band.append((next_id, group))
                next_id += 1
        levels.append({"level": band, "count": len(next_band)})
        log.info(f"band {band}: {len(next_band)} nested planar communities")
        band_regions = next_band
        if not band_regions:
            break

    return cells, levels


def _kmeans_split(
    idx: np.ndarray,
    coords: np.ndarray,
    k: int,
    seed: int,
) -> list[np.ndarray]:
    if len(idx) <= k:
        return [idx[i:i + 1] for i in range(len(idx))]
    labels = KMeans(n_clusters=k, n_init=4, random_state=seed).fit_predict(coords[idx])
    return [idx[labels == label] for label in range(k) if np.any(labels == label)]


def _build_kmeans(coords: np.ndarray, cfg: Config) -> tuple[list[dict], list[dict]]:
    """Legacy recursive clustering of 2D display coordinates."""
    cells: list[dict] = []
    levels: list[dict] = []
    next_id = 0
    seed = cfg.cluster.random_state
    all_idx = np.arange(len(coords))
    band_regions: list[tuple[int, np.ndarray]] = []
    root_groups = _kmeans_split(all_idx, coords, cfg.hierarchy.root_clusters, seed)
    for group in root_groups:
        if len(group) < cfg.hierarchy.min_tile_points:
            continue
        cells.append(_make_cell(next_id, 0, group, coords, None))
        band_regions.append((next_id, group))
        next_id += 1
    levels.append({"level": 0, "count": len(band_regions)})

    for band in range(1, cfg.hierarchy.max_depth):
        next_band: list[tuple[int, np.ndarray]] = []
        for parent_id, parent_idx in band_regions:
            if len(parent_idx) < cfg.hierarchy.min_cluster_size:
                continue
            for group in _kmeans_split(
                parent_idx,
                coords,
                cfg.hierarchy.branching,
                seed + band,
            ):
                if len(group) < cfg.hierarchy.min_tile_points:
                    continue
                cells.append(_make_cell(next_id, band, group, coords, parent_id))
                next_band.append((next_id, group))
                next_id += 1
        levels.append({"level": band, "count": len(next_band)})
        band_regions = next_band
        if not band_regions:
            break
    return cells, levels


def _build_quadtree(coords: np.ndarray, cfg: Config) -> tuple[list[dict], list[dict]]:
    """Legacy fixed-grid quadtree."""
    x0, y0 = float(coords[:, 0].min()), float(coords[:, 1].min())
    x1, y1 = float(coords[:, 0].max()), float(coords[:, 1].max())
    cells: list[dict] = []
    levels: list[dict] = []
    cell_id = 0
    previous: dict[tuple[int, int], int] = {}
    for band in range(cfg.hierarchy.max_depth):
        depth = cfg.hierarchy.start_depth + band
        n_cells = 2 ** depth
        width, height = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        ix = np.clip(((coords[:, 0] - x0) / width * n_cells).astype(int), 0, n_cells - 1)
        iy = np.clip(((coords[:, 1] - y0) / height * n_cells).astype(int), 0, n_cells - 1)
        keys = ix.astype(np.int64) * n_cells + iy.astype(np.int64)
        current: dict[tuple[int, int], int] = {}
        count = 0
        for key in np.unique(keys):
            group = np.where(keys == key)[0]
            if len(group) < cfg.hierarchy.min_tile_points:
                continue
            cell_x, cell_y = int(key) // n_cells, int(key) % n_cells
            parent = previous.get((cell_x // 2, cell_y // 2)) if band > 0 else None
            cells.append(_make_cell(cell_id, band, group, coords, parent))
            current[(cell_x, cell_y)] = cell_id
            cell_id += 1
            count += 1
        levels.append({"level": band, "count": count})
        previous = current
    return cells, levels


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s06_hierarchy")

    coords = read_npy(COORDS_IN)
    log.info(f"{len(coords)} points | method={cfg.hierarchy.method}")

    if cfg.hierarchy.method == _PLANAR_METHOD:
        vectors = read_npy(VECTORS_IN)
        graph = _build_planar_graph(coords, vectors, cfg.hierarchy.planar_k)
        log.info(
            f"planar graph: {graph.vcount()} nodes | {graph.ecount()} undirected edges "
            f"(2D-kNN k={cfg.hierarchy.planar_k}, 768-D cosine weights)"
        )
        cells, levels = _build_planar_hierarchy(coords, vectors, graph, cfg)
    elif cfg.hierarchy.method in _GRAPH_METHODS:
        vectors = read_npy(VECTORS_IN)
        neighbors = np.load(NEIGHBORS_IN)
        edges = np.load(EDGES_IN)
        graph = _build_fused_graph(
            neighbors["ids"],
            neighbors["scores"],
            edges["src"],
            edges["dst"],
            cfg.hierarchy.direct_citation_weight,
        )
        log.info(
            f"fused graph: {graph.number_of_nodes()} nodes | "
            f"{graph.number_of_edges()} undirected edges"
        )
        cells, levels = _build_graph_hierarchy(
            coords, vectors, graph, cfg, cfg.hierarchy.method
        )
    elif cfg.hierarchy.method == "quadtree":
        cells, levels = _build_quadtree(coords, cfg)
    else:
        cells, levels = _build_kmeans(coords, cfg)

    write_json({"levels": levels, "cells": cells}, OUT)
    log.info(f"total {len(cells)} regions across {len(levels)} bands -> {OUT}")
    return str(OUT)


if __name__ == "__main__":
    run()
