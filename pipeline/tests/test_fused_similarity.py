import numpy as np
import pytest

from pipeline.common.fused_similarity import (
    build_reference_sets,
    citation_candidates,
    citation_score,
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



def test_hub_pivot_is_skipped_but_direct_citations_survive():
    """A reference cited by everyone is a bibliographic stopword.

    Papers 1..5 all cite hub 0, so via the hub each is "coupled" to the other four. That is
    both meaningless and quadratic (sum(indeg^2) — 30.9 billion steps on the real graph). With
    the cap set below the hub's degree, those spurious coupling candidates disappear, but the
    hub itself must STILL be a candidate for 1 because 1 directly cites it.
    """
    n = 6
    src = np.array([1, 2, 3, 4, 5], dtype=np.int32)
    dst = np.array([0, 0, 0, 0, 0], dtype=np.int32)
    out_refs, in_citers = build_reference_sets(src, dst, n)

    uncapped = citation_candidates(1, out_refs, in_citers, overlap_limit=10)
    assert uncapped == {0, 2, 3, 4, 5}, "without a cap the hub couples all its citers"

    capped = citation_candidates(1, out_refs, in_citers, overlap_limit=10, hub_degree_limit=3)
    assert capped == {0}, "hub pivot skipped, but the direct citation 1->0 is kept"


def test_hub_cap_leaves_ordinary_coupling_untouched():
    """Below the cap, behaviour must be identical to the uncapped version."""
    n = 5
    # 1 and 2 both cite 0 and 3 — genuine coupling, well under any sane cap.
    src = np.array([1, 1, 2, 2], dtype=np.int32)
    dst = np.array([0, 3, 0, 3], dtype=np.int32)
    out_refs, in_citers = build_reference_sets(src, dst, n)
    assert citation_candidates(1, out_refs, in_citers, 10, hub_degree_limit=1000) == (
        citation_candidates(1, out_refs, in_citers, 10, hub_degree_limit=0)
    )


def test_hub_limit_zero_disables_the_cap():
    n = 4
    src = np.array([1, 2, 3], dtype=np.int32)
    dst = np.array([0, 0, 0], dtype=np.int32)
    out_refs, in_citers = build_reference_sets(src, dst, n)
    assert citation_candidates(1, out_refs, in_citers, 10, hub_degree_limit=0) == {0, 2, 3}


def test_jaccard_union_size_is_computed_not_allocated():
    """|a ∪ b| = |a| + |b| - |a ∩ b|; the refactor must not change the value."""
    n = 4
    # 0 and 1 share reference 2; 0 also cites 3.
    src = np.array([0, 0, 1], dtype=np.int32)
    dst = np.array([2, 3, 2], dtype=np.int32)
    out_refs, in_citers = build_reference_sets(src, dst, n)
    # out_refs[0]={2,3}, out_refs[1]={2} -> inter 1, union 2 -> 0.5 coupling, no co-citation.
    assert citation_score(0, 1, out_refs, in_citers) == pytest.approx(0.5 * 0.5)
