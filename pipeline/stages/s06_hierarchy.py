"""s06: Build the semantic-zoom hierarchy (the core feature).

We overlay a **quadtree** on the frozen 2D coordinates. A quadtree recursively splits the
plane into 4 quadrants; at depth d there are 4^d cells covering the map. Each semantic
zoom *band* samples a quadtree depth:

    band b  ->  quadtree depth (start_depth + b)

so band 0 (zoomed out) has few big cells ("continents") and each deeper band has 4x more,
finer cells ("cities" -> "streets"). Because a child cell is spatially nested inside its
parent, the bands are **guaranteed nested** — a fine label always sits inside its coarse
parent's region (no contradictory labels across zoom, unlike multi-bandwidth density maps).

For each non-empty cell we record its centroid, point count, bbox, and the member node
ids (so s07 can label it). We also link each cell to its parent cell to expose the tree.

Emits:
    data/interim/tiles.json  { levels:[{level,depth,cells}], cells:[{id,level,depth,cx,cy,
                               count,bbox,parent,node_idx:[...]}] }
"""

from __future__ import annotations

import numpy as np

from pipeline.common import log
from pipeline.common.io import read_npy, write_json
from pipeline.config import INTERIM_DIR, Config, ensure_dirs, load_config

COORDS_IN = INTERIM_DIR / "coords2d.npy"
OUT = INTERIM_DIR / "tiles.json"


def _cell_index(coords: np.ndarray, depth: int, bounds: tuple[float, float, float, float]):
    """Return per-point (ix, iy) cell coordinates at a quadtree depth over given bounds."""
    x0, y0, x1, y1 = bounds
    n_cells = 2 ** depth
    # Guard against zero-width bounds.
    w = max(x1 - x0, 1e-6)
    h = max(y1 - y0, 1e-6)
    ix = np.clip(((coords[:, 0] - x0) / w * n_cells).astype(int), 0, n_cells - 1)
    iy = np.clip(((coords[:, 1] - y0) / h * n_cells).astype(int), 0, n_cells - 1)
    return ix, iy, n_cells


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s06_hierarchy")

    coords = read_npy(COORDS_IN)
    n = coords.shape[0]
    x0, y0 = float(coords[:, 0].min()), float(coords[:, 1].min())
    x1, y1 = float(coords[:, 0].max()), float(coords[:, 1].max())
    bounds = (x0, y0, x1, y1)
    log.info(f"{n} points | bounds x[{x0:.1f},{x1:.1f}] y[{y0:.1f},{y1:.1f}]")

    cells: list[dict] = []
    levels: list[dict] = []
    cell_id = 0
    # cell key -> id map per level, to link children to parents.
    prev_key_to_id: dict[tuple[int, int], int] = {}

    for band in range(cfg.hierarchy.max_depth):
        depth = cfg.hierarchy.start_depth + band
        ix, iy, n_cells = _cell_index(coords, depth, bounds)
        key_to_id: dict[tuple[int, int], int] = {}
        band_cell_count = 0

        # Group point indices by cell.
        keys = ix.astype(np.int64) * n_cells + iy.astype(np.int64)
        order = np.argsort(keys)
        sorted_keys = keys[order]
        # Find contiguous runs of equal key.
        boundaries = np.where(np.diff(sorted_keys) != 0)[0] + 1
        groups = np.split(order, boundaries)

        for grp in groups:
            if len(grp) < cfg.hierarchy.min_tile_points:
                continue
            k = int(sorted_keys[np.searchsorted(sorted_keys, keys[grp[0]])])
            cix, ciy = k // n_cells, k % n_cells
            pts = coords[grp]
            cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
            bbox = [float(pts[:, 0].min()), float(pts[:, 1].min()),
                    float(pts[:, 0].max()), float(pts[:, 1].max())]

            # Parent = the cell at the previous (coarser) band containing this centroid.
            parent = None
            if band > 0:
                pix = min(int((cx - x0) / max(x1 - x0, 1e-6) * (2 ** (depth - 1))),
                          2 ** (depth - 1) - 1)
                piy = min(int((cy - y0) / max(y1 - y0, 1e-6) * (2 ** (depth - 1))),
                          2 ** (depth - 1) - 1)
                parent = prev_key_to_id.get((pix, piy))

            cells.append({
                "id": cell_id,
                "level": band,
                "depth": depth,
                "cx": cx, "cy": cy,
                "count": int(len(grp)),
                "bbox": bbox,
                "parent": parent,
                "node_idx": grp.astype(int).tolist(),
            })
            key_to_id[(cix, ciy)] = cell_id
            cell_id += 1
            band_cell_count += 1

        levels.append({"level": band, "depth": depth, "cells": band_cell_count})
        prev_key_to_id = key_to_id
        log.info(f"band {band} (quadtree depth {depth}): {band_cell_count} labeled cells")

    write_json({"levels": levels, "cells": cells}, OUT)
    log.info(f"total {len(cells)} cells across {cfg.hierarchy.max_depth} bands -> {OUT}")
    return str(OUT)


if __name__ == "__main__":
    run()
