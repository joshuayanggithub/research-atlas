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
ties); at level L admit a paper only if (a) no already-admitted paper (this level or any
coarser one) lies within radius ``r_L``, and (b) its importance clears that level's global
floor. ``r_L`` halves each level, so the admitted set refines geometrically. A uniform grid
keyed by the level's radius makes each conflict check O(1) neighbors, so the whole
assignment is ~linear.

The importance floor (b) is what keeps the home view *influential* rather than merely
*spread out*. Spatial separation alone admits whichever paper happens to top each empty
region, however unimportant: in the 912k all-years corpus 42.7% of papers have zero
citations and the median is 1, so sparse regions were seeding the coarsest zoom with
5-citation papers. The floor gates each level to a global top fraction of the corpus
(~4× more per level, matching the geometric admission rate), so a thinly-populated region
simply stays empty until the zoom is deep enough to warrant it. Once the fraction reaches
1.0 the gate is inert and the behaviour is purely spatial again.
"""

from __future__ import annotations

import numpy as np


def assign_reveal_levels(
    coords: np.ndarray,
    importance: np.ndarray,
    n_levels: int = 16,
    base_divisor: float = 40.0,
    top_fraction: float = 0.002,
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
    top_fraction : fraction of the corpus eligible at level 0, by importance. Quadruples
        each level until it saturates at 1.0, after which only spatial separation applies.
        Set to 1.0 to disable the gate entirely (pure spatial thinning).
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
    imp = np.asarray(importance, dtype=np.float64)
    order = np.argsort(-imp, kind="stable")

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

        # Global importance floor for this level: the top `keep` fraction of the corpus is
        # eligible, quadrupling per level to match the ~4× spatial admission rate. Once it
        # saturates the gate is inert, so deep levels behave exactly as before.
        keep = min(1.0, top_fraction * (4**level))
        floor = -np.inf if keep >= 1.0 else float(np.quantile(imp, 1.0 - keep))
        # Grid of admitted-point coordinates (all levels so far still block new admits).
        grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for i in np.where(levels >= 0)[0]:
            px, py = coords[i]
            key = (int((px - x0) / cell), int((py - y0) / cell))
            grid.setdefault(key, []).append((px, py))

        for i in order:
            if levels[i] >= 0:
                continue
            # `order` is importance-descending, so once one paper fails the floor every
            # remaining one does too — stop rather than scanning the rest of the corpus.
            if imp[i] < floor:
                break
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
