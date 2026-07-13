"""s06: Build the semantic-zoom hierarchy (the core feature).

We build an ADAPTIVE region hierarchy by recursive k-means on the frozen 2D coordinates,
so regions follow the actual data (dense subtopics become their own regions) rather than
an arbitrary fixed grid. This gives much finer, more meaningful topics as you zoom in.

    band 0: k-means with `root_clusters` centers over all points  -> "continents"
    band b: each parent region with >= min_cluster_size points is split into `branching`
            sub-regions -> progressively finer "cities" -> "streets"

Each region records its centroid, point count, bbox, member indices, and parent, so s07
can label it and the frontend can nest bands. Because a child region is a k-means split of
its parent's points, bands are strictly nested (a fine region's points are a subset of its
parent's) — no contradictory labels across zoom.

A "quadtree" method is retained for comparison (legacy fixed grid).

Emits:
    data/interim/tiles.json  { levels:[{level,count}], cells:[{id,level,cx,cy,count,bbox,
                               parent,node_idx:[...]}] }
"""

from __future__ import annotations

import numpy as np
from sklearn.cluster import KMeans

from pipeline.common import log
from pipeline.common.io import read_npy, write_json
from pipeline.config import INTERIM_DIR, Config, ensure_dirs, load_config

COORDS_IN = INTERIM_DIR / "coords2d.npy"
OUT = INTERIM_DIR / "tiles.json"


def _bbox(pts: np.ndarray) -> list[float]:
    return [float(pts[:, 0].min()), float(pts[:, 1].min()),
            float(pts[:, 0].max()), float(pts[:, 1].max())]


def _make_cell(cell_id: int, level: int, idx: np.ndarray, coords: np.ndarray,
               parent: int | None) -> dict:
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


def _kmeans_split(idx: np.ndarray, coords: np.ndarray, k: int, seed: int) -> list[np.ndarray]:
    """Split the point indices `idx` into up to k groups by k-means on their 2D coords."""
    if len(idx) <= k:
        return [idx[i:i + 1] for i in range(len(idx))]
    km = KMeans(n_clusters=k, n_init=4, random_state=seed)
    labels = km.fit_predict(coords[idx])
    return [idx[labels == c] for c in range(k) if (labels == c).any()]


def _build_kmeans(coords: np.ndarray, cfg: Config) -> tuple[list[dict], list[dict]]:
    cells: list[dict] = []
    levels: list[dict] = []
    next_id = 0
    seed = cfg.cluster.random_state

    # Band 0: root_clusters regions over everything.
    all_idx = np.arange(len(coords))
    band_regions: list[tuple[int, np.ndarray]] = []  # (cell_id, member idx) for this band
    root_groups = _kmeans_split(all_idx, coords, cfg.hierarchy.root_clusters, seed)
    band0_ids = []
    for grp in root_groups:
        if len(grp) < cfg.hierarchy.min_tile_points:
            continue
        cell = _make_cell(next_id, 0, grp, coords, None)
        cells.append(cell)
        band_regions.append((next_id, grp))
        band0_ids.append(next_id)
        next_id += 1
    levels.append({"level": 0, "count": len(band0_ids)})
    log.info(f"band 0: {len(band0_ids)} regions (k-means k={cfg.hierarchy.root_clusters})")

    # Bands 1..max_depth-1: split each region from the previous band.
    for band in range(1, cfg.hierarchy.max_depth):
        next_band: list[tuple[int, np.ndarray]] = []
        band_count = 0
        for parent_id, parent_idx in band_regions:
            # Only subdivide regions big enough to yield meaningful children.
            if len(parent_idx) < cfg.hierarchy.min_cluster_size:
                continue
            groups = _kmeans_split(parent_idx, coords, cfg.hierarchy.branching, seed + band)
            for grp in groups:
                if len(grp) < cfg.hierarchy.min_tile_points:
                    continue
                cell = _make_cell(next_id, band, grp, coords, parent_id)
                cells.append(cell)
                next_band.append((next_id, grp))
                next_id += 1
                band_count += 1
        levels.append({"level": band, "count": band_count})
        log.info(f"band {band}: {band_count} regions")
        band_regions = next_band
        if not band_regions:
            break

    return cells, levels


def _build_quadtree(coords: np.ndarray, cfg: Config) -> tuple[list[dict], list[dict]]:
    """Legacy fixed-grid quadtree (kept for comparison)."""
    n = len(coords)
    x0, y0 = float(coords[:, 0].min()), float(coords[:, 1].min())
    x1, y1 = float(coords[:, 0].max()), float(coords[:, 1].max())
    cells: list[dict] = []
    levels: list[dict] = []
    cell_id = 0
    prev_key_to_id: dict[tuple[int, int], int] = {}
    for band in range(cfg.hierarchy.max_depth):
        depth = cfg.hierarchy.start_depth + band
        n_cells = 2 ** depth
        w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        ix = np.clip(((coords[:, 0] - x0) / w * n_cells).astype(int), 0, n_cells - 1)
        iy = np.clip(((coords[:, 1] - y0) / h * n_cells).astype(int), 0, n_cells - 1)
        keys = ix.astype(np.int64) * n_cells + iy.astype(np.int64)
        key_to_id: dict[tuple[int, int], int] = {}
        band_count = 0
        for k in np.unique(keys):
            grp = np.where(keys == k)[0]
            if len(grp) < cfg.hierarchy.min_tile_points:
                continue
            cix, ciy = int(k) // n_cells, int(k) % n_cells
            parent = None
            if band > 0:
                pcx, pcy = cix // 2, ciy // 2
                parent = prev_key_to_id.get((pcx, pcy))
            cell = _make_cell(cell_id, band, grp, coords, parent)
            cells.append(cell)
            key_to_id[(cix, ciy)] = cell_id
            cell_id += 1
            band_count += 1
        levels.append({"level": band, "count": band_count})
        prev_key_to_id = key_to_id
        log.info(f"band {band} (quadtree depth {depth}): {band_count} cells")
    return cells, levels


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s06_hierarchy")

    coords = read_npy(COORDS_IN)
    log.info(f"{len(coords)} points | method={cfg.hierarchy.method}")

    if cfg.hierarchy.method == "quadtree":
        cells, levels = _build_quadtree(coords, cfg)
    else:
        cells, levels = _build_kmeans(coords, cfg)

    write_json({"levels": levels, "cells": cells}, OUT)
    log.info(f"total {len(cells)} regions across {len(levels)} bands -> {OUT}")
    return str(OUT)


if __name__ == "__main__":
    run()
