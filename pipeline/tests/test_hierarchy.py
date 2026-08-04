import networkx as nx
import numpy as np
import pytest

from pipeline.config import Config
from pipeline.stages.s06_hierarchy import _build_fused_graph, _build_graph_hierarchy


def test_fused_graph_keeps_direct_citations_and_strongest_neighbor_weight():
    neighbor_ids = np.asarray([[1], [0], [-1]], dtype=np.int32)
    neighbor_scores = np.asarray([[0.7], [0.6], [0.0]], dtype=np.float32)
    source = np.asarray([1], dtype=np.int32)
    target = np.asarray([2], dtype=np.int32)

    graph = _build_fused_graph(
        neighbor_ids,
        neighbor_scores,
        source,
        target,
        direct_citation_weight=0.45,
    )

    assert graph[0][1]["weight"] == np.float32(0.7)
    assert graph[1][2]["weight"] == 0.45


@pytest.mark.parametrize("method", ["leiden", "louvain"])
def test_graph_hierarchy_children_partition_their_parent(method):
    rng = np.random.default_rng(7)
    n = 36
    vectors = np.vstack([
        rng.normal(loc=(1.0, 0.0, 0.0), scale=0.08, size=(18, 3)),
        rng.normal(loc=(0.0, 1.0, 0.0), scale=0.08, size=(18, 3)),
    ]).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    coords = vectors[:, :2].copy()

    graph = nx.Graph()
    graph.add_nodes_from(range(n))
    for start in (0, 18):
        for node in range(start, start + 18):
            for other in range(node + 1, start + 18):
                graph.add_edge(node, other, weight=0.9)
    graph.add_edge(8, 26, weight=0.05)

    cfg = Config()
    cfg.hierarchy.max_depth = 3
    cfg.hierarchy.root_clusters = 2
    cfg.hierarchy.branching = 2
    cfg.hierarchy.min_cluster_size = 6
    cfg.hierarchy.min_tile_points = 3
    cfg.hierarchy.max_child_fraction = 0.75

    cells, levels = _build_graph_hierarchy(coords, vectors, graph, cfg, method)
    by_id = {cell["id"]: cell for cell in cells}
    roots = [cell for cell in cells if cell["parent"] is None]

    assert levels[0]["count"] == 2
    assert set().union(*(set(root["node_idx"]) for root in roots)) == set(range(n))
    assert sum(root["count"] for root in roots) == n

    for parent in cells:
        children = [cell for cell in cells if cell["parent"] == parent["id"]]
        if not children:
            continue
        flattened = [node for child in children for node in child["node_idx"]]
        assert len(flattened) == len(set(flattened))
        assert set(flattened) == set(parent["node_idx"])
        for child in children:
            assert child["level"] == parent["level"] + 1
            assert set(child["node_idx"]) <= set(by_id[parent["id"]]["node_idx"])


def test_leiden_communities_are_internally_connected():
    """The core reason for the swap: Leiden never returns a disconnected community.

    Two cliques joined by a single weak bridge, plus a resolution that (under Louvain)
    can absorb a bridge node into the far community, leaving it internally disconnected.
    Leiden guarantees each returned community is a connected subgraph.
    """
    from pipeline.stages.s06_hierarchy import _leiden_communities

    graph = nx.Graph()
    graph.add_nodes_from(range(20))
    for start in (0, 10):
        for node in range(start, start + 10):
            for other in range(node + 1, start + 10):
                graph.add_edge(node, other, weight=1.0)
    graph.add_edge(0, 10, weight=0.01)

    communities = _leiden_communities(graph, resolution=1.0, seed=42)
    assert len(communities) >= 2
    for community in communities:
        assert nx.is_connected(graph.subgraph(community)), (
            f"Leiden returned an internally disconnected community: {sorted(community)}"
        )



def _two_islands(n_per: int = 40, seed: int = 11):
    """Two well-separated groups in BOTH 2D layout space and embedding space."""
    rng = np.random.default_rng(seed)
    coords = np.vstack([
        rng.normal(loc=(-10.0, 0.0), scale=0.6, size=(n_per, 2)),
        rng.normal(loc=(10.0, 0.0), scale=0.6, size=(n_per, 2)),
    ]).astype(np.float32)
    vectors = np.vstack([
        rng.normal(loc=(1.0, 0.0, 0.0), scale=0.05, size=(n_per, 3)),
        rng.normal(loc=(0.0, 1.0, 0.0), scale=0.05, size=(n_per, 3)),
    ]).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return coords, vectors


def test_planar_graph_only_connects_spatial_neighbors_with_semantic_weights():
    """Adjacency must come from the 2D layout; weights must come from the embeddings."""
    from pipeline.stages.s06_hierarchy import _build_planar_graph

    coords, vectors = _two_islands(n_per=40)
    graph = _build_planar_graph(coords, vectors, k_spatial=5)

    weights = np.asarray(graph.es["weight"])
    assert (weights >= 0.0).all(), "negative cosine must be clipped to 0 for Leiden"

    # With k=5 and two islands 20 units apart, no edge should bridge the islands.
    crossings = sum(
        1 for e in graph.get_edgelist() if (e[0] < 40) != (e[1] < 40)
    )
    assert crossings == 0, f"{crossings} edges bridged spatially distant islands"
    # Same-island pairs are semantically near-identical here, so weights must be high.
    assert weights.mean() > 0.9


def test_planar_regions_are_spatially_contiguous():
    """The property the planar substrate exists to guarantee.

    A region must form ONE connected component in the 2D kNN graph. Detecting communities
    in a non-planar (768-D + citation) graph does not give this, which is what made regions
    look like unrelated papers grouped together.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    from pipeline.stages.s06_hierarchy import _build_planar_graph, _build_planar_hierarchy

    coords, vectors = _two_islands(n_per=60)
    cfg = Config()
    cfg.hierarchy.method = "planar"
    cfg.hierarchy.max_depth = 2
    cfg.hierarchy.root_clusters = 2
    cfg.hierarchy.branching = 2
    cfg.hierarchy.min_cluster_size = 8
    cfg.hierarchy.min_tile_points = 3

    graph = _build_planar_graph(coords, vectors, cfg.hierarchy.planar_k)
    cells, levels = _build_planar_hierarchy(coords, vectors, graph, cfg)

    assert levels[0]["count"] == 2
    # Children still exactly partition their parent (the hierarchy invariant).
    for parent in cells:
        children = [c for c in cells if c["parent"] == parent["id"]]
        if children:
            flat = [n for c in children for n in c["node_idx"]]
            assert sorted(flat) == sorted(parent["node_idx"])

    # Contiguity: build the 2D kNN adjacency and check each region is one component.
    _, knn = cKDTree(coords).query(coords, k=11)
    for cell in cells:
        idx = np.asarray(cell["node_idx"])
        if len(idx) < 4:
            continue
        pos = {v: i for i, v in enumerate(idx)}
        members = set(idx.tolist())
        rows, cols = [], []
        for v in idx:
            for w in knn[v][1:]:
                if int(w) in members:
                    rows.append(pos[v])
                    cols.append(pos[int(w)])
        adj = coo_matrix(
            (np.ones(len(rows)), (rows, cols)), shape=(len(idx), len(idx))
        )
        n_parts, _ = connected_components(adj, directed=False)
        assert n_parts == 1, (
            f"region {cell['id']} (band {cell['level']}, {len(idx)} papers) is scattered "
            f"across {n_parts} disconnected areas of the map"
        )
