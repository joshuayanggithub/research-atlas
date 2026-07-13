// Directed citation arcs for the SELECTED node only (never the whole graph — that would
// be an unreadable hairball and slow). Source→target color encodes direction.

import { ArcLayer } from "@deck.gl/layers";
import type { Dataset } from "../../data/types";
import type { EdgeMode } from "../../state/store";

interface Arc {
  source: [number, number];
  target: [number, number];
  outgoing: boolean; // true = selected cites target; false = source cites selected
}

export function useEdgeLayer(
  ds: Dataset,
  selectedNode: number | null,
  edgeMode: EdgeMode,
) {
  if (selectedNode === null) return null;

  const px = ds.points.x;
  const py = ds.points.y;
  const arcs: Arc[] = [];

  if (edgeMode === "out" || edgeMode === "both") {
    for (const dst of ds.citesOut.get(selectedNode) ?? []) {
      arcs.push({
        source: [px[selectedNode], py[selectedNode]],
        target: [px[dst], py[dst]],
        outgoing: true,
      });
    }
  }
  if (edgeMode === "in" || edgeMode === "both") {
    for (const src of ds.citedBy.get(selectedNode) ?? []) {
      arcs.push({
        source: [px[src], py[src]],
        target: [px[selectedNode], py[selectedNode]],
        outgoing: false,
      });
    }
  }

  if (arcs.length === 0) return null;

  return new ArcLayer<Arc>({
    id: "citation-arcs",
    data: arcs,
    getSourcePosition: (d) => d.source,
    getTargetPosition: (d) => d.target,
    // outgoing (this paper cites X): cyan→blue. incoming (X cites this): orange→red.
    getSourceColor: (d) => (d.outgoing ? [80, 220, 240, 200] : [250, 180, 70, 200]),
    getTargetColor: (d) => (d.outgoing ? [70, 130, 240, 180] : [240, 90, 70, 180]),
    getWidth: 1.4,
    getHeight: 0.3,
    widthMinPixels: 1,
    widthMaxPixels: 3,
  });
}
