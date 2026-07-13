"""Fused similarity for the related-works graph.

Related-works ranking blends two signals (Connected Papers' insight: citations imply
relatedness, but text catches new/sparsely-cited work):

    fused = alpha * cosine(text_embeddings) + (1 - alpha) * citation_score

where ``citation_score`` combines **bibliographic coupling** (papers A and B share
references) and **co-citation** (later papers cite A and B together), each as a Jaccard
overlap in [0, 1]. Because we only have the intra-corpus citation graph, these are
computed over corpus references/citers.

Strategy: text kNN via hnswlib gives a candidate set per node; we then re-score those
candidates by adding the citation term. This avoids an O(N^2) all-pairs citation pass.
"""

from __future__ import annotations

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
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def citation_score(
    i: int, j: int, out_refs: list[set[int]], in_citers: list[set[int]]
) -> float:
    """Mean of bibliographic-coupling and co-citation Jaccard for a pair."""
    bib_coupling = _jaccard(out_refs[i], out_refs[j])   # shared references
    co_citation = _jaccard(in_citers[i], in_citers[j])  # shared citers
    return 0.5 * (bib_coupling + co_citation)


def fuse_neighbors(
    text_neighbor_ids: np.ndarray,   # [N, k] candidate ids from text kNN
    text_scores: np.ndarray,         # [N, k] cosine similarities in [0, 1]
    out_refs: list[set[int]],
    in_citers: list[set[int]],
    alpha: float,
    top_k: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Re-rank text kNN candidates by fused score; return top_k ids + scores per node.

    Candidates come from text (so brand-new papers still get neighbors); the citation
    term only reorders and boosts within that candidate pool.
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
