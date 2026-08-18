"""Fused similarity and citation-derived candidate generation.

Related-works ranking blends two signals (Connected Papers' insight: citations imply
relatedness, but text catches new/sparsely-cited work):

    fused = alpha * cosine(text_embeddings) + (1 - alpha) * citation_score

``citation_score`` is 1 for a direct citation; otherwise it combines bibliographic
coupling (shared references) and co-citation (shared citers), each as a Jaccard overlap.
The candidate pool is the union of text kNN, direct citations, and the strongest
shared-reference/co-citation candidates. This matters: re-ranking text candidates alone
cannot recover a citation-strong paper that is text-distant.
"""

from __future__ import annotations

from collections import Counter

import numpy as np


def build_reference_sets(
    edges_src: np.ndarray, edges_dst: np.ndarray, n_nodes: int
) -> tuple[list[set[int]], list[set[int]]]:
    """Return (out_refs, in_citers) per node from a directed edge list (src cites dst).

    - out_refs[i]  = set of works that i references (i -> j).
    - in_citers[i] = set of works that cite i (j -> i).
    """
    out_refs: list[set[int]] = [set() for _ in range(n_nodes)]
    in_citers: list[set[int]] = [set() for _ in range(n_nodes)]
    for s, d in zip(edges_src.tolist(), edges_dst.tolist()):
        out_refs[s].add(d)
        in_citers[d].add(s)
    return out_refs, in_citers


def _jaccard(a: set[int], b: set[int]) -> float:
    if not a or not b:
        return 0.0
    # `a & b` walks the SMALLER set; `a | b` would allocate a whole new set sized |a|+|b|.
    # With a 74k-citer hub in play that allocation dominated, so derive the union size
    # arithmetically instead: |a ∪ b| = |a| + |b| - |a ∩ b|.
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / (len(a) + len(b) - inter)


def citation_score(
    i: int, j: int, out_refs: list[set[int]], in_citers: list[set[int]]
) -> float:
    """Citation relatedness in [0, 1], including direct citation evidence."""
    if j in out_refs[i] or i in out_refs[j]:
        return 1.0
    bib_coupling = _jaccard(out_refs[i], out_refs[j])   # shared references
    co_citation = _jaccard(in_citers[i], in_citers[j])  # shared citers
    return 0.5 * (bib_coupling + co_citation)


def citation_candidates(
    node: int,
    out_refs: list[set[int]],
    in_citers: list[set[int]],
    overlap_limit: int,
    hub_degree_limit: int = 0,
) -> set[int]:
    """Return direct and strongest second-order citation candidates for ``node``.

    Bibliographic-coupling candidates are other papers that cite one of ``node``'s
    references. Co-citation candidates are other papers cited alongside ``node``.
    Direct citations are never capped; only second-order candidates use ``overlap_limit``.

    ``hub_degree_limit`` (0 = disabled) skips *pivots* that are too widely connected to carry
    any similarity signal. `overlap_limit` bounds the OUTPUT, not the work: the loops below
    cost ``sum(indeg^2) + sum(outdeg^2)`` over the whole graph regardless. On the 13M-edge
    all-years graph that measured **30.9 billion** inner steps — a projected 21 hours, with
    49% of it contributed by just five papers (in-degrees 74k, 69k, 49k, 34k, 32k).

    This is the bibliographic analogue of a stopword. "Both papers cite *Attention Is All You
    Need*" says nothing — every paper citing it would otherwise be coupled to every other,
    which is both meaningless and quadratic. Papers over the limit are skipped only as
    *pivots*; they remain candidates via ``direct`` below, which is never capped, so no
    citation relationship is lost. Measured at limit=1000: 14x less work (~91 min), excluding
    1,025 of 912,429 papers (0.112%) that absorb 22% of all edges.
    """
    overlap: Counter[int] = Counter()
    cap = hub_degree_limit if hub_degree_limit > 0 else None

    for reference in out_refs[node]:
        citers = in_citers[reference]
        if cap is not None and len(citers) > cap:
            continue
        for other in citers:
            if other != node:
                overlap[other] += 1

    for citer in in_citers[node]:
        refs = out_refs[citer]
        if cap is not None and len(refs) > cap:
            continue
        for other in refs:
            if other != node:
                overlap[other] += 1

    direct = out_refs[node] | in_citers[node]
    ranked_overlap = sorted(overlap, key=lambda other: (-overlap[other], other))
    return set(direct) | set(ranked_overlap[:overlap_limit])


def fuse_candidate_neighbors(
    vectors: np.ndarray,
    text_neighbor_ids: np.ndarray,
    out_refs: list[set[int]],
    in_citers: list[set[int]],
    alpha: float,
    top_k: int,
    citation_candidate_limit: int,
    hub_degree_limit: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Rank the union of semantic and citation candidates for every paper."""
    n = text_neighbor_ids.shape[0]
    out_ids = np.full((n, top_k), -1, dtype=np.int32)
    out_scores = np.zeros((n, top_k), dtype=np.float32)

    for i in range(n):
        candidates = {int(j) for j in text_neighbor_ids[i] if j >= 0 and j != i}
        candidates.update(
            citation_candidates(
                i, out_refs, in_citers, citation_candidate_limit, hub_degree_limit
            )
        )
        candidates.discard(i)
        if not candidates:
            continue

        candidate_ids = np.asarray(sorted(candidates), dtype=np.int32)
        # Embeddings are L2-normalized in s03, so the dot product is cosine similarity.
        # Accumulate in float64. Some accelerated BLAS builds emit spurious overflow
        # warnings for thousands of small float32 matrix-vector products.
        text_similarity = np.clip(
            np.einsum(
                "ij,j->i",
                vectors[candidate_ids],
                vectors[i],
                dtype=np.float64,
                optimize=True,
            ),
            0.0,
            1.0,
        )
        citation_similarity = np.fromiter(
            (citation_score(i, int(j), out_refs, in_citers) for j in candidate_ids),
            dtype=np.float32,
            count=len(candidate_ids),
        )
        fused = alpha * text_similarity + (1.0 - alpha) * citation_similarity

        # Stable tie break by node id.
        order = np.lexsort((candidate_ids, -fused))[:top_k]
        count = len(order)
        out_ids[i, :count] = candidate_ids[order]
        out_scores[i, :count] = fused[order].astype(np.float32)

    return out_ids, out_scores


def fuse_neighbors(
    text_neighbor_ids: np.ndarray,   # [N, k] candidate ids from text kNN
    text_scores: np.ndarray,         # [N, k] cosine similarities in [0, 1]
    out_refs: list[set[int]],
    in_citers: list[set[int]],
    alpha: float,
    top_k: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Legacy text-candidate-only re-ranker.

    Kept for callers that already have candidate scores. New pipeline code should use
    :func:`fuse_candidate_neighbors`, which can introduce citation-derived candidates.
    """
    n, k = text_neighbor_ids.shape
    out_ids = np.full((n, top_k), -1, dtype=np.int32)
    out_scores = np.zeros((n, top_k), dtype=np.float32)

    for i in range(n):
        cand_ids = text_neighbor_ids[i]
        cand_txt = text_scores[i]
        fused = np.empty(k, dtype=np.float32)
        for c in range(k):
            j = int(cand_ids[c])
            if j < 0 or j == i:
                fused[c] = -1.0
                continue
            cs = citation_score(i, j, out_refs, in_citers)
            fused[c] = alpha * float(cand_txt[c]) + (1.0 - alpha) * cs
        order = np.argsort(-fused)[:top_k]
        m = len(order)
        out_ids[i, :m] = cand_ids[order]
        out_scores[i, :m] = fused[order]
    return out_ids, out_scores
