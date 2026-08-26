// Semantic neighbours of the selected paper, drawn on the map.
//
// The citation layer answers "what did this paper build on, and what built on it". For a recent
// preprint there is no answer: S2 and OpenAlex have not extracted its references, so the paper
// sits on the map with nothing attached to it. Its s08 fused-kNN neighbours exist for every
// paper in the corpus, so this is the one structural link that is always available.
//
// Drawn WITHOUT arrowheads and in violet, because similarity is symmetric and carries no
// direction — the teal/amber axis is reserved for influence.

import { useEffect, useState } from "react";
import type { Dataset } from "../../data/types";
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
export function useRelatedLinks(ds: Dataset, selectedNode: number | null): RelatedLink[] {
  const showRelated = useStore((s) => s.showRelated);
  const [links, setLinks] = useState<RelatedLink[]>([]);
  // Positions arrive per tile/shard, so a neighbour may not be placed yet on the first pass.
  const tilesEpoch = usePointTilesEpoch();

  useEffect(() => {
    if (selectedNode === null || !showRelated) {
      setLinks([]);
      return;
    }
    let live = true;
    void ds.getNeighbors(selectedNode).then(async (n) => {
      if (!live || !n || n.ids.length === 0) return;
      const ids = Array.from(n.ids);
      // A neighbour whose tile has not landed has no position — drawing it would put a line
      // through the origin. Fetch the shards it lives in, then draw whatever is placed.
      await ensurePositionsFor(ids);
      if (!live) return;
      const { x, y, revealLevel } = ds.points;
      if (revealLevel[selectedNode] === UNLOADED_LEVEL) return;
      const source: [number, number] = [x[selectedNode], y[selectedNode]];
      setLinks(ids.flatMap((node, i) => (
        revealLevel[node] === UNLOADED_LEVEL
          ? []
          : [{ source, target: [x[node], y[node]] as [number, number], node, score: n.scores[i] ?? 0 }]
      )));
    });
    return () => { live = false; };
  }, [ds, selectedNode, showRelated, tilesEpoch]);

  return links;
}
