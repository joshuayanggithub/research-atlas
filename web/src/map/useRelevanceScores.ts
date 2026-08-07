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
// Memoized on (dataset, selectedNode) — recomputed only when the selection changes.

import { useMemo } from "react";
import type { Dataset } from "../data/types";

export interface RelevanceScores {
  // node id -> normalized relevance in [0,1] (1 = most relevant). Only the selected paper and
  // its citation-network neighbors appear; everything else is implicitly 0 (culled already).
  score: Map<number, number>;
  // The selected node is always 1.0 and always kept.
  selected: number;
}

export function useRelevanceScores(
  ds: Dataset | null,
  selectedNode: number | null,
): RelevanceScores | null {
  return useMemo(() => {
    if (!ds || selectedNode === null || selectedNode < 0) return null;

    const refsOf = (n: number): number[] => ds.citesOut.get(n) ?? [];
    const citersOf = (n: number): number[] => ds.citedBy.get(n) ?? [];

    const selRefs = new Set(refsOf(selectedNode));
    const selCiters = new Set(citersOf(selectedNode));

    // Candidate set = the selected paper's direct neighbors (its citation network), the same
    // set the map culls to. We score each candidate by shared references + shared citers.
    const candidates = new Set<number>([...selRefs, ...selCiters]);

    const raw = new Map<number, number>();
    let max = 0;
    for (const p of candidates) {
      if (p === selectedNode) continue;
      let coupling = 0;
      for (const r of refsOf(p)) if (selRefs.has(r)) coupling++;
      let cocitation = 0;
      for (const c of citersOf(p)) if (selCiters.has(c)) cocitation++;
      // A direct link (p is a ref or citer of sel) gets a +1 floor so it never scores 0 just
      // because it happens to share no other references/citers with the selected paper.
      const direct = selRefs.has(p) || selCiters.has(p) ? 1 : 0;
      const s = coupling + cocitation + direct;
      raw.set(p, s);
      if (s > max) max = s;
    }

    const score = new Map<number, number>();
    score.set(selectedNode, 1);
    const norm = max > 0 ? 1 / max : 1;
    for (const [p, s] of raw) score.set(p, s * norm);

    return { score, selected: selectedNode };
  }, [ds, selectedNode]);
}
