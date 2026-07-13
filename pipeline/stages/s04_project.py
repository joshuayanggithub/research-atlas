"""s04: Project 768-dim embeddings to 2D for the map layout.

Uses openTSNE with the "PubMed landscape" recipe (the 20M-paper precedent): PCA
initialization, uniform affinities on an approximate kNN graph (k = projector.n_neighbors),
learning rate n/12, and exaggeration annealing. The fitted embedding is **frozen**
(pickled) so future incremental runs can ``transform()`` new papers into the *same* space
without moving existing points (map stability, risk #7).

Emits:
    data/interim/coords2d.npy     [N, 2] float32
    data/interim/projector.pkl    the frozen openTSNE embedding (for transform())
"""

from __future__ import annotations

import pickle

import numpy as np

from pipeline.common import log
from pipeline.common.io import read_npy, write_npy
from pipeline.config import INTERIM_DIR, Config, ensure_dirs, load_config

VEC_IN = INTERIM_DIR / "embeddings.npy"
COORDS_OUT = INTERIM_DIR / "coords2d.npy"
PROJECTOR_OUT = INTERIM_DIR / "projector.pkl"


def _project_opentsne(vectors: np.ndarray, cfg: Config) -> tuple[np.ndarray, object]:
    from openTSNE import TSNE

    n = vectors.shape[0]
    lr = cfg.projector.learning_rate or max(200, n / 12.0)
    # openTSNE applies early-exaggeration annealing internally; PCA init + cosine metric
    # match the large-scale scientific-embedding recipe.
    tsne = TSNE(
        n_components=2,
        perplexity=max(5, cfg.projector.n_neighbors * 3),
        metric="cosine",
        initialization="pca",
        learning_rate=lr,
        n_jobs=-1,
        random_state=cfg.projector.random_state,
        verbose=True,
    )
    embedding = tsne.fit(vectors)  # TSNEEmbedding supports .transform() later
    return np.asarray(embedding, dtype=np.float32), embedding


def _project_umap(vectors: np.ndarray, cfg: Config) -> tuple[np.ndarray, object]:
    import umap

    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=cfg.projector.n_neighbors * 3,
        min_dist=0.1,
        metric="cosine",
        random_state=cfg.projector.random_state,
    )
    coords = reducer.fit_transform(vectors).astype(np.float32)
    return coords, reducer


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s04_project")

    vectors = read_npy(VEC_IN)
    log.info(f"projecting {vectors.shape} with method={cfg.projector.method}")

    with log.timer("projection"):
        if cfg.projector.method == "umap":
            coords, projector = _project_umap(vectors, cfg)
        else:
            coords, projector = _project_opentsne(vectors, cfg)

    # Normalize coords to a stable, centered range so the frontend viewport math is simple.
    coords = coords - coords.mean(axis=0)
    scale = np.percentile(np.abs(coords), 99)
    if scale > 0:
        coords = coords / scale * 100.0  # ~[-100, 100] with tails beyond
    coords = coords.astype(np.float32)

    write_npy(coords, COORDS_OUT)
    with PROJECTOR_OUT.open("wb") as f:
        pickle.dump(projector, f)

    log.info(f"coords {coords.shape} range x[{coords[:,0].min():.1f},{coords[:,0].max():.1f}] "
             f"y[{coords[:,1].min():.1f},{coords[:,1].max():.1f}] -> {COORDS_OUT}")
    return str(COORDS_OUT)


if __name__ == "__main__":
    run()
