import numpy as np

from pipeline.stages.s04_project import FrozenProjector


class _FakeProjector:
    def transform(self, vectors):
        return np.asarray(vectors, dtype=np.float32)[:, :2]


def test_frozen_projector_reuses_fit_normalization_for_new_points():
    raw_fit = np.array(
        [
            [-3.0, -1.0],
            [1.0, 1.0],
            [5.0, 3.0],
        ],
        dtype=np.float32,
    )

    normalized, frozen = FrozenProjector.from_fit(_FakeProjector(), raw_fit)

    np.testing.assert_allclose(normalized, frozen.normalize(raw_fit))
    np.testing.assert_allclose(
        frozen.transform(np.array([[1.0, 1.0, 99.0]], dtype=np.float32)),
        frozen.normalize(np.array([[1.0, 1.0]], dtype=np.float32)),
    )
    np.testing.assert_allclose(normalized.mean(axis=0), [0.0, 0.0], atol=1e-5)


def test_frozen_projector_rejects_non_2d_fit_coordinates():
    with np.testing.assert_raises_regex(ValueError, r"\[N, 2\]"):
        FrozenProjector.from_fit(_FakeProjector(), np.zeros((3, 3), dtype=np.float32))
