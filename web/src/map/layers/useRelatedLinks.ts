// Semantic neighbours of the selected paper, drawn on the map.
//
// The citation layer answers "what did this paper build on, and what built on it". For a recent
// preprint there is no answer: S2 and OpenAlex have not extracted its references, so the paper
// sits on the map with nothing attached to it. Its s08 fused-kNN neighbours exist for every
// paper in the corpus, so this is the one structural link that is always available.
//
// Drawn WITHOUT arrowheads and in violet, because similarity is symmetric and carries no
// direction — the teal/amber axis is reserved for influence.

import { useEffect, useMemo, useState } from "react";
import type { Dataset, NeighborList } from "../../data/types";
import { ensurePositionsFor, UNLOADED_LEVEL } from "../../data/loadArtifacts";
import { usePointTilesEpoch } from "../../data/usePapersReady";
import { useStore } from "../../state/store";

export type RelatedLink = {
  source: [number, number];
  target: [number, number];
  node: number;
  /** Similarity score in [0,1]; drives alpha so the nearest neighbours read strongest. */
  score: number;
};

/** Links from the selected paper to its nearest neighbours, or [] when there is nothing to
 *  draw. Honours the same Hide/Show toggle as the Related works panel: hidden means the
 *  neighbour shard is never fetched. */
/** The selected paper's fused-kNN neighbours, or null.
 *
 *  Shared by the map layer and the panel's network diagram so both read ONE cached shard fetch
 *  and agree on what "similar" means.
 *
 *  Fetches only on a real change of paper. An earlier version also listed tilesEpoch and awaited
 *  inside the effect, so every tile that landed re-ran it and the cleanup killed the run still
 *  awaiting — with tiles arriving every few seconds a heavily-linked paper never produced a
 *  single link. */
export function useNeighborList(ds: Dataset, selectedNode: number | null): NeighborList | null {
  const showRelated = useStore((s) => s.showRelated);
  const [neighbors, setNeighbors] = useState<NeighborList | null>(null);
  useEffect(() => {
    // Clear FIRST, on every change of paper. Holding the previous paper's list until the new
    // shard resolved meant the diagram attributed one paper's neighbours to another: selecting
    // Attention Is All You Need drew 3D Cal's tactile-sensor papers as its similar work. A
    // wrong answer displayed confidently is worse than an empty diagram (the
    // placeholder-read-as-fact rule).
    setNeighbors(null);
    if (selectedNode === null || !showRelated) return;
    let live = true;
    void ds.getNeighbors(selectedNode).then((n) => {
      if (!live) return;
      setNeighbors(n && n.ids.length > 0 ? n : null);
      // Nudge the shards these neighbours live in; consumers pick them up as they land.
      if (n && n.ids.length > 0) void ensurePositionsFor(Array.from(n.ids));
    });
    return () => { live = false; };
  }, [ds, selectedNode, showRelated]);
  return neighbors;
}

export function useRelatedLinks(ds: Dataset, selectedNode: number | null): RelatedLink[] {
  const showRelated = useStore((s) => s.showRelated);
  const neighbors = useNeighborList(ds, selectedNode);
  // Positions arrive per tile/shard, so a neighbour may not be placed on the first pass.
  // Readiness is a render-time question, so it lives in the memo below.
  const tilesEpoch = usePointTilesEpoch();

  return useMemo(() => {
    if (!neighbors || selectedNode === null || !showRelated) return [];
    const { x, y, revealLevel } = ds.points;
    // A neighbour whose tile has not landed has no position — drawing it would put a line
    // through the origin. Skip it; it appears when its shard arrives (tilesEpoch).
    if (revealLevel[selectedNode] >= UNLOADED_LEVEL) return [];
    const source: [number, number] = [x[selectedNode], y[selectedNode]];
    const ids = Array.from(neighbors.ids);
    return ids.flatMap((node, i) => (
      revealLevel[node] >= UNLOADED_LEVEL
        ? []
        : [{ source, target: [x[node], y[node]] as [number, number], node, score: neighbors.scores[i] ?? 0 }]
    ));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [neighbors, ds, selectedNode, showRelated, tilesEpoch]);
}
