// Connected-Papers-style relevance of every paper in a selected paper's citation network.
//
// Connected Papers ranks related work by a similarity built from CO-CITATION and
// BIBLIOGRAPHIC COUPLING — two papers are related when they share references (coupling) and
// when later papers cite them together (co-citation), NOT merely when one cites the other.
// We compute the same signal over the corpus citation graph (edge adjacency), on selection:
//
//   coupling(sel, p)   = |refs(sel) ∩ refs(p)|      (shared outgoing citations)
//   cocitation(sel, p) = |citers(sel) ∩ citers(p)|  (shared incoming citations)
//   score(sel, p)      = coupling + cocitation
//
// The selected paper's direct references/citers are included with a floor so a direct link is
// always at least as relevant as a purely coupling/co-citation neighbor. Scores are
// normalized to [0,1] (max = 1) so a single slider threshold works regardless of magnitude.
//
// TWO THINGS KEEP THIS CHEAP AT 912k, both learned the hard way:
//
//   1. The candidate set is CAPPED. Selecting a hub stalled the tab — "Proximal Policy
//      Optimization Algorithms" has 16,322 in-corpus citers, and scoring each against a
//      16k-element set is millions of operations on the main thread, so the citation panel
//      came up empty. Ranking candidates by citation count and keeping the strongest
//      MAX_CANDIDATES bounds the work while keeping the part anyone actually reads. This is
//      the browser-side twin of the pipeline's fused.hub_degree_limit.
//
//   2. Results are DENSE TYPED ARRAYS, not Maps. deck.gl re-evaluates its accessors across all
//      912,429 points whenever the selection changes; a Map.get() per point per accessor was a
//      large part of the click latency. Array indexing makes those lookups free.

import { useMemo } from "react";
import type { Dataset } from "../data/types";

/** Direction codes stored in `direction` (dense, one byte per paper). */
export const DIR_NONE = 0;
export const DIR_REFERENCE = 1; // selected cites it — it influenced the selection
export const DIR_CITER = 2; // it cites selected — influenced by the selection
export const DIR_SELECTED = 3;

// Enough that the network reads as rich, small enough that scoring stays instant. The map only
// ever draws a few hundred edges (useEdgeLayer's per-direction limit) and the panel lists far
// fewer, so beyond this the extra candidates are invisible anyway.
const MAX_CANDIDATES = 3000;

export interface RelevanceScores {
  /** Dense per-node: -1 = outside the network, otherwise normalized relevance in [0,1]. */
  score: Float32Array;
  /** Dense per-node direction code (DIR_*). */
  direction: Uint8Array;
  /** The selected node is always 1.0 and always kept. */
  selected: number;
  /** In-network scores, ascending. The relevance slider is a PERCENTILE control, and raw
   *  scores are far too skewed to use directly: `score = raw / max` where raw is a small count,
   *  so a typical connected paper (raw = 1) sits at 1/max — often 0.02. A raw cutoff therefore
   *  culls almost the whole network within the first few percent of the slider's travel, while
   *  the label claims "top 90%". Sorting once per selection turns that into a real quantile. */
  sorted: Float32Array;
  /** Candidates actually scored, and how many the network holds — for honest UI. */
  scored: number;
  total: number;
}

export function useRelevanceScores(
  ds: Dataset | null,
  selectedNode: number | null,
): RelevanceScores | null {
  return useMemo(() => {
    if (!ds || selectedNode === null || selectedNode < 0) return null;

    const n = ds.points.count;
    const refsOf = (m: number): number[] => ds.citesOut.get(m) ?? [];
    const citersOf = (m: number): number[] => ds.citedBy.get(m) ?? [];

    const selRefsArr = refsOf(selectedNode);
    const selCitersArr = citersOf(selectedNode);
    const selRefs = new Set(selRefsArr);
    const selCiters = new Set(selCitersArr);

    const score = new Float32Array(n).fill(-1);
    const direction = new Uint8Array(n);

    // Direction is recorded for the WHOLE network even though only the strongest candidates get
    // scored, so colouring stays complete and honest.
    for (const p of selRefsArr) if (p !== selectedNode) direction[p] = DIR_REFERENCE;
    for (const p of selCitersArr) if (p !== selectedNode && !direction[p]) direction[p] = DIR_CITER;
    direction[selectedNode] = DIR_SELECTED;

    const all = new Set<number>([...selRefsArr, ...selCitersArr]);
    all.delete(selectedNode);
    const total = all.size;

    // Rank by citation count before capping: if we must drop candidates, drop the obscure ones.
    let candidates = Array.from(all);
    if (candidates.length > MAX_CANDIDATES) {
      const cited = ds.points.citedByCount;
      candidates.sort((a, b) => cited[b] - cited[a]);
      candidates = candidates.slice(0, MAX_CANDIDATES);
    }

    let max = 0;
    const raw = new Float32Array(candidates.length);
    for (let i = 0; i < candidates.length; i++) {
      const p = candidates[i];
      let coupling = 0;
      for (const r of refsOf(p)) if (selRefs.has(r)) coupling++;
      let cocitation = 0;
      for (const c of citersOf(p)) if (selCiters.has(c)) cocitation++;
      // A direct link gets a +1 floor so it never scores 0 just because it happens to share no
      // other references/citers with the selected paper.
      const direct = selRefs.has(p) || selCiters.has(p) ? 1 : 0;
      const s = coupling + cocitation + direct;
      raw[i] = s;
      if (s > max) max = s;
    }

    const norm = max > 0 ? 1 / max : 1;
    for (let i = 0; i < candidates.length; i++) score[candidates[i]] = raw[i] * norm;
    // Anything in the network but not scored still belongs to it; give it the floor rather than
    // -1, or the relevance slider would cull it as "outside the network".
    for (const p of all) if (score[p] < 0) score[p] = 0;
    score[selectedNode] = 1;

    // Sorted in-network scores for percentile lookups (see RelevanceScores.sorted).
    const inNetwork: number[] = [];
    for (const p of all) if (p !== selectedNode) inNetwork.push(score[p]);
    inNetwork.sort((a, b) => a - b);
    const sorted = Float32Array.from(inNetwork);

    return { score, direction, sorted, selected: selectedNode, scored: candidates.length, total };
  }, [ds, selectedNode]);
}
