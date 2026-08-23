// Whether a paper's citation network is COMPLETE, and fetching it if not.
//
// The tiers (edges-L{N}.arrow) hold only what is drawable at the current zoom, which is the
// wrong answer to every question the UI asks about a selected paper: "Attention Is All You
// Need" has 69,262 citers spread across every depth, and a reader looking at it from the home
// view has loaded 408 edges in total. Reporting the tier view as the paper's network would
// state a number that is not just incomplete but wildly wrong — the placeholder-read-as-fact
// bug at its most misleading.
//
// So a selection fetches the paper's own shard, which is authoritative, and every consumer
// waits for `ready` before claiming anything about counts.

import { useEffect, useState } from "react";
import type { Dataset } from "./types";
import { useStore } from "../state/store";
import { ensureNodeEdges, hasCompleteEdges, onEdgesChanged } from "./loadArtifacts";

export function useNodeEdges(ds: Dataset | null, node: number | null): boolean {
  const [, setTick] = useState(0);
  useEffect(() => onEdgesChanged(() => setTick((n) => n + 1)), []);

  const syncAutoRelevance = useStore((st) => st.syncAutoRelevance);
  useEffect(() => {
    if (!ds || node === null || node < 0) return;
    let live = true;
    void ensureNodeEdges([node]).then(() => {
      // The auto relevance threshold was computed from the zoom tiers, which for a hub hold a
      // small fraction of its network — "Attention Is All You Need" looks like an ordinary
      // paper until its shard arrives. Recompute now that the real size is known.
      if (live) syncAutoRelevance(node);
    });
    return () => { live = false; };
  }, [ds, node, syncAutoRelevance]);

  return node === null || node < 0 ? false : hasCompleteEdges(node);
}

/** Bumps whenever more of the graph lands, for consumers that read the maps directly. */
export function useEdgesEpoch(): number {
  const [epoch, setEpoch] = useState(0);
  useEffect(() => onEdgesChanged(() => setEpoch((n) => n + 1)), []);
  return epoch;
}
