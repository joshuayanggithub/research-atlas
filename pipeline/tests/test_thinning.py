import numpy as np
import pytest
from scipy.spatial import cKDTree

from pipeline.common.thinning import assign_reveal_levels


def test_every_paper_gets_a_level():
    rng = np.random.default_rng(0)
    coords = rng.normal(size=(2000, 2))
    importance = rng.random(2000)
    levels = assign_reveal_levels(coords, importance, n_levels=16, base_divisor=20.0)
    assert (levels >= 0).all()
    assert len(levels) == 2000


def test_cumulative_levels_are_overlap_free():
    """The core guarantee: at every zoom level, no two *visible* points are closer than
    that level's separation radius. Visible = cumulative (levels 0..L)."""
    rng = np.random.default_rng(1)
    coords = rng.normal(size=(3000, 2)) * 50.0
    importance = rng.random(3000)
    base_divisor = 30.0
    levels = assign_reveal_levels(coords, importance, n_levels=16, base_divisor=base_divisor)

    span = max(np.ptp(coords[:, 0]), np.ptp(coords[:, 1]))
    r0 = span / base_divisor
    n_used = int(levels.max()) + 1
    # Skip the final catch-all level: it intentionally admits duplicates that cannot satisfy
    # the separation test (here there are none, but the contract allows it).
    for level in range(n_used - 1):
        radius = r0 / (2**level)
        visible = np.where(levels <= level)[0]
        if len(visible) < 2:
            continue
        pts = coords[visible]
        dist, idx = cKDTree(pts).query(pts, k=2)  # nearest OTHER point
        nn = dist[:, 1]
        # Allow a hair of float slack.
        assert nn.min() >= radius - 1e-9, (
            f"level {level}: two visible points {nn.min():.4f} apart < radius {radius:.4f}"
        )


def test_most_important_papers_reveal_first():
    """Level 0 should be dominated by the highest-importance papers."""
    rng = np.random.default_rng(2)
    coords = rng.normal(size=(2000, 2)) * 50.0
    importance = rng.random(2000)
    levels = assign_reveal_levels(coords, importance, n_levels=16, base_divisor=15.0)

    level0 = importance[levels == 0]
    rest = importance[levels > 0]
    # The coarsest level's papers are, on average, far more important than the rest.
    assert level0.mean() > rest.mean()


def test_deterministic():
    rng = np.random.default_rng(3)
    coords = rng.normal(size=(1500, 2)) * 40.0
    importance = rng.random(1500)
    a = assign_reveal_levels(coords, importance, base_divisor=25.0)
    b = assign_reveal_levels(coords, importance, base_divisor=25.0)
    assert np.array_equal(a, b)


def test_ties_broken_by_index_not_random():
    """Equal importance must resolve deterministically (stable by index), so rebuilds and
    the cached projector stay consistent."""
    coords = np.array([[0.0, 0.0], [0.001, 0.0], [10.0, 10.0]])
    importance = np.array([1.0, 1.0, 1.0])  # all tied
    levels = assign_reveal_levels(coords, importance, n_levels=8, base_divisor=2.0)
    # The two near-coincident points cannot both be at level 0; the lower index wins.
    assert levels[0] <= levels[1]


def test_empty_input():
    levels = assign_reveal_levels(np.zeros((0, 2)), np.zeros(0))
    assert levels.shape == (0,)


def test_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        assign_reveal_levels(np.zeros((5, 2)), np.zeros(4))
