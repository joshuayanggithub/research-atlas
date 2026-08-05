"""s04: Project 768-dim embeddings to 2D for the map layout.

Uses openTSNE with the "PubMed landscape" recipe (the 20M-paper precedent): PCA
initialization, uniform affinities on an approximate kNN graph (k = projector.n_neighbors),
learning rate n/12, and exaggeration annealing. The fitted embedding is **frozen**
(pickled) so future incremental runs can ``transform()`` new papers into the *same* space
without moving existing points (map stability, risk #7).

Emits:
    data/interim/coords2d.npy     [N, 2] float32
    data/interim/projector.pkl    reducer + map normalization (for transform())
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass

import numpy as np

from pipeline.common import log
from pipeline.common.io import read_npy, write_npy
from pipeline.config import INTERIM_DIR, Config, ensure_dirs, load_config

VEC_IN = INTERIM_DIR / "embeddings.npy"
COORDS_OUT = INTERIM_DIR / "coords2d.npy"
PROJECTOR_OUT = INTERIM_DIR / "projector.pkl"


@dataclass
class FrozenProjector:
    """Fitted reducer plus the normalization that defines the displayed map space."""

    projector: object
    center: np.ndarray
    scale: float
    map_extent: float = 100.0

    @classmethod
    def from_fit(
        cls,
        projector: object,
        coords: np.ndarray,
        map_extent: float = 100.0,
    ) -> tuple[np.ndarray, "FrozenProjector"]:
        raw = np.asarray(coords, dtype=np.float32)
        if raw.ndim != 2 or raw.shape[1] != 2:
            raise ValueError(f"expected [N, 2] projector coordinates, got {raw.shape}")
        if not np.isfinite(raw).all():
            raise ValueError("projector coordinates contain non-finite values")

        center = raw.mean(axis=0, dtype=np.float64).astype(np.float32)
        centered = raw - center
        scale = float(np.percentile(np.abs(centered), 99))
        if not np.isfinite(scale) or scale <= 0:
            scale = 1.0

        frozen = cls(
            projector=projector,
            center=center,
            scale=scale,
            map_extent=map_extent,
        )
        return frozen.normalize(raw), frozen

    def normalize(self, coords: np.ndarray) -> np.ndarray:
        """Map raw reducer coordinates into the same centered space as the web bundle."""
        raw = np.asarray(coords, dtype=np.float32)
        return ((raw - self.center) / self.scale * self.map_extent).astype(np.float32)

    def transform(self, vectors: np.ndarray) -> np.ndarray:
        """Project new embeddings and apply the original map normalization."""
        transform = getattr(self.projector, "transform", None)
        if transform is None:
            raise TypeError("the fitted projector does not support transform()")
        return self.normalize(np.asarray(transform(vectors), dtype=np.float32))


def _project_opentsne(vectors: np.ndarray, cfg: Config) -> tuple[np.ndarray, object]:
    from openTSNE import TSNE

    n = vectors.shape[0]
    lr = cfg.projector.learning_rate or max(200, n / 12.0)
    # openTSNE applies early-exaggeration annealing internally; PCA init + cosine metric
    # match the large-scale scientific-embedding recipe. A sustained `exaggeration` > 1
    # then separates the topic clusters into distinct islands with whitespace between them,
    # so the zoomed-out home view reads as airy continents rather than one dense mass.
    tsne = TSNE(
        n_components=2,
        perplexity=max(5, cfg.projector.n_neighbors * 3),
        metric="cosine",
        initialization="pca",
        learning_rate=lr,
        exaggeration=cfg.projector.exaggeration,
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

    # Freeze both the fitted reducer and the normalization. Persisting only the raw reducer
    # would make transform() return coordinates in a different space from the displayed map.
    coords, frozen_projector = FrozenProjector.from_fit(projector, coords)

    write_npy(coords, COORDS_OUT)
    with PROJECTOR_OUT.open("wb") as f:
        pickle.dump(frozen_projector, f)

    log.info(f"coords {coords.shape} range x[{coords[:,0].min():.1f},{coords[:,0].max():.1f}] "
             f"y[{coords[:,1].min():.1f},{coords[:,1].max():.1f}] -> {COORDS_OUT}")
    return str(COORDS_OUT)


if __name__ == "__main__":
    run()
