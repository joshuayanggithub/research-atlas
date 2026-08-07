import numpy as np

from pipeline.common.fused_similarity import (
    build_reference_sets,
    citation_candidates,
    fuse_candidate_neighbors,
)


def test_reference_sets_preserve_citation_direction():
    source = np.asarray([0, 2, 2], dtype=np.int32)
    target = np.asarray([1, 1, 3], dtype=np.int32)

    references, citers = build_reference_sets(source, target, n_nodes=4)

    assert references == [{1}, set(), {1, 3}, set()]
    assert citers == [set(), {0, 2}, set(), {2}]


def test_citation_candidates_include_direct_coupling_and_co_citation():
    # 0 and 1 share reference 2. Paper 3 cites 0 and 4 together.
    source = np.asarray([0, 1, 3, 3], dtype=np.int32)
    target = np.asarray([2, 2, 0, 4], dtype=np.int32)
    references, citers = build_reference_sets(source, target, n_nodes=5)

    candidates = citation_candidates(0, references, citers, overlap_limit=10)

    assert {1, 2, 3, 4} <= candidates


def test_fused_ranking_can_introduce_a_non_text_citation_candidate():
    vectors = np.asarray([
        [1.0, 0.0, 0.0],
        [0.9, 0.1, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    text_ids = np.asarray([
        [1],
        [0],
        [1],
    ], dtype=np.int32)
    source = np.asarray([0], dtype=np.int32)
    target = np.asarray([2], dtype=np.int32)
    references, citers = build_reference_sets(source, target, n_nodes=3)

    ids, scores = fuse_candidate_neighbors(
        vectors,
        text_ids,
        references,
        citers,
        alpha=0.6,
        top_k=2,
        citation_candidate_limit=4,
    )

    assert 2 in ids[0]
    assert np.all(np.isfinite(scores))

