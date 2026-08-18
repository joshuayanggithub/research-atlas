import numpy as np
import polars as pl

from pipeline.embedding.specter2_local import Specter2LocalBackend


def test_checkpoint_resumes_only_matching_corpus(tmp_path):
    partial = tmp_path / "partial.npy"
    checkpoint = tmp_path / "checkpoint.json"
    backend = Specter2LocalBackend(
        dim=3, batch_size=2, partial_path=partial, checkpoint_path=checkpoint,
    )
    corpus = pl.DataFrame({"paper_id": ["a", "b"], "title": ["A", "B"],
                           "abstract": ["", ""]})
    matrix, start, fingerprint = backend._open_checkpoint(corpus)
    assert start == 0
    matrix[0] = [1, 2, 3]
    matrix.flush()
    backend._save_checkpoint(fingerprint, 2, 1)

    resumed, start, _ = backend._open_checkpoint(corpus)
    assert start == 1
    np.testing.assert_array_equal(resumed[0], [1, 2, 3])

    changed = corpus.with_columns(pl.Series("paper_id", ["a", "c"]))
    reset, start, _ = backend._open_checkpoint(changed)
    assert start == 0
    np.testing.assert_array_equal(reset, np.zeros((2, 3), dtype=np.float32))
