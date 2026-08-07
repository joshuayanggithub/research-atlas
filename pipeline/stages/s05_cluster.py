"""s05: Cluster papers for topic assignment.

Clustering runs in a SEPARATE ~10-D UMAP space (not the 2D layout coords) to avoid the
projection artifacts UMAP's own docs warn about (false tears / density distortion). We
then run HDBSCAN there. The result is a per-paper leaf cluster id (noise = -1).

Note: the 2D map regions come from adaptive spatial partitioning in s06. These high-D
clusters currently feed only the ``cluster_leaf`` column; they do not define semantic-zoom
regions. Clustering in high-D preserves a future path to topic communities that are not
artifacts of the flattened picture.

Emits:
    data/interim/cluster_assign.npy   [N] int32 leaf cluster id (-1 = noise)
"""

from __future__ import annotations

import numpy as np

from pipeline.common import log
from pipeline.common.io import read_npy, write_npy
from pipeline.config import INTERIM_DIR, Config, ensure_dirs, load_config

VEC_IN = INTERIM_DIR / "embeddings.npy"
OUT = INTERIM_DIR / "cluster_assign.npy"


def _cluster_hdbscan(reduced: np.ndarray, cfg: Config) -> np.ndarray:
    import hdbscan

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=cfg.cluster.hdbscan_min_cluster_size,
        min_samples=cfg.cluster.hdbscan_min_samples,
        metric="euclidean",
        core_dist_n_jobs=-1,
    )
    labels = clusterer.fit_predict(reduced)
    return labels.astype(np.int32)


def _cluster_hkmeans(reduced: np.ndarray, cfg: Config) -> np.ndarray:
    # Scale fallback: flat k-means with k ~ sqrt(N/2). Behind the same interface.
    from sklearn.cluster import MiniBatchKMeans

    n = reduced.shape[0]
    k = max(8, int(np.sqrt(n / 2)))
    km = MiniBatchKMeans(n_clusters=k, random_state=cfg.cluster.random_state, n_init=3)
    return km.fit_predict(reduced).astype(np.int32)


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s05_cluster")

    vectors = read_npy(VEC_IN)
    log.info(f"reducing {vectors.shape} -> {cfg.cluster.umap_components}D for clustering")

    with log.timer("cluster-umap"):
        import umap
        reducer = umap.UMAP(
            n_components=cfg.cluster.umap_components,
            n_neighbors=cfg.cluster.umap_n_neighbors,
            min_dist=cfg.cluster.umap_min_dist,
            metric="cosine",
            random_state=cfg.cluster.random_state,
        )
        reduced = reducer.fit_transform(vectors).astype(np.float32)

    with log.timer("cluster"):
        if cfg.cluster.method == "hkmeans":
            labels = _cluster_hkmeans(reduced, cfg)
        else:
            labels = _cluster_hdbscan(reduced, cfg)

    n_clusters = len(set(labels.tolist()) - {-1})
    noise = float((labels == -1).mean())
    log.info(f"clusters={n_clusters} | noise={noise:.1%}")
    write_npy(labels, OUT)
    log.info(f"wrote -> {OUT}")
    return str(OUT)


if __name__ == "__main__":
    run()
