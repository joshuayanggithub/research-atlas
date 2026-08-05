"""Greedy spatial thinning → per-paper *reveal level* for overlap-free semantic zoom.

The map layout (s04) places every paper in 2D, but at the zoomed-out home view tens of
thousands of points collapse into a solid, unreadable mass. We assign each paper a
``reveal_level``: the coarsest zoom band at which it becomes visible. Level 0 is a sparse,
well-separated set of the most important papers; each deeper level admits ~4× more, always
maintaining a minimum on-screen separation so **no two visible points overlap at any zoom**.

The same levels double as fetch-on-demand tiles: the frontend loads cumulative levels
0..current for the viewport instead of the whole corpus, so corpus size stops gating the
initial download.

Algorithm (deterministic): walk papers in importance order (citations desc, id asc for
ties); at level L admit a paper only if no already-admitted paper (this level or any
coarser one) lies within radius ``r_L``. ``r_L`` halves each level, so the admitted set
refines geometrically. A uniform grid keyed by the level's radius makes each conflict check
O(1) neighbors, so the whole assignment is ~linear and runs in well under a second on 72k
points.
"""

from __future__ import annotations

import numpy as np


def assign_reveal_levels(
    coords: np.ndarray,
    importance: np.ndarray,
    n_levels: int = 16,
    base_divisor: float = 40.0,
) -> np.ndarray:
    """Return an int32 ``reveal_level`` per point (0 = visible at the coarsest zoom).

    Parameters
    ----------
    coords : (N, 2) float array — the 2D map layout.
    importance : (N,) array — higher = revealed earlier (e.g. citation count).
    n_levels : maximum number of levels. The last level is a catch-all: any point still
        unassigned (typically exact-duplicate coordinates t-SNE stacked) is forced visible
        there, so every paper is guaranteed a level.
    base_divisor : level-0 radius = max(span_x, span_y) / base_divisor. Larger ⇒ sparser
        home view.
    """
    coords = np.asarray(coords, dtype=np.float64)
    n = len(coords)
    if n == 0:
        return np.zeros(0, dtype=np.int32)
    if coords.shape != (n, 2):
        raise ValueError(f"expected (N, 2) coords, got {coords.shape}")
    if len(importance) != n:
        raise ValueError("importance must be row-aligned to coords")

    levels = np.full(n, -1, dtype=np.int32)
    order = np.argsort(-np.asarray(importance), kind="stable")

    x0 = float(coords[:, 0].min())
    y0 = float(coords[:, 1].min())
    span = max(
        float(coords[:, 0].max()) - x0,
        float(coords[:, 1].max()) - y0,
        1e-9,
    )
    r0 = span / base_divisor

    last = 0
    for level in range(n_levels):
        last = level
        radius = r0 / (2**level)
        radius_sq = radius * radius
        cell = radius  # grid cell == radius ⇒ conflicts live in the 3×3 neighborhood
        # Grid of admitted-point coordinates (all levels so far still block new admits).
        grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for i in np.where(levels >= 0)[0]:
            px, py = coords[i]
            key = (int((px - x0) / cell), int((py - y0) / cell))
            grid.setdefault(key, []).append((px, py))

        for i in order:
            if levels[i] >= 0:
                continue
            px, py = coords[i]
            gx = int((px - x0) / cell)
            gy = int((py - y0) / cell)
            conflict = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for qx, qy in grid.get((gx + dx, gy + dy), ()):
                        if (qx - px) ** 2 + (qy - py) ** 2 < radius_sq:
                            conflict = True
                            break
                    if conflict:
                        break
                if conflict:
                    break
            if not conflict:
                levels[i] = level
                grid.setdefault((gx, gy), []).append((px, py))

        if (levels >= 0).all():
            break

    # Any survivors are exact/near-exact coordinate duplicates that can never satisfy the
    # separation test — force them into the deepest used level so coverage is total.
    levels[levels < 0] = last
    return levels
