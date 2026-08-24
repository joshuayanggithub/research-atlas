// Citation links on the main map.
//
// Ordinary browsing gets a zoom-adaptive sample of the global directed graph.
// Every rendered link has a screen-sized triangle pointing from the citing paper to the
// cited paper. Selecting a paper overlays its incoming/outgoing links at full contrast.
//
// Direction is carried by HUE ONLY (see ../citationColors): teal = a reference that
// influenced the selection, amber = a paper influenced by it. The connected papers
// themselves are tinted to match in usePointsLayer, so the node and its edge agree. An
// earlier version also drew a background-coloured disc over each connected paper to ring it
// in the direction colour; that occluded the very node it was highlighting (it read as a
// black dot inside a blue one) and is gone.

import { LineLayer, SolidPolygonLayer } from "@deck.gl/layers";
import { useMemo } from "react";
import type { Dataset } from "../../data/types";
import type { FilterArrays } from "../../filters/useFilterMask";
import type { EdgeMode } from "../../state/store";
import { importanceWeight, relevanceCutoff } from "../importance";
import { CITER, GLOBAL_EDGE, REFERENCE } from "../citationColors";
import { useEdgesEpoch, useNodeEdges } from "../../data/useNodeEdges";

type Position = [number, number];
type Color = [number, number, number, number];
type ArrowPolygon = [Position, Position, Position];

interface GlobalEdge {
  source: Position;
  target: Position;
  arrow: ArrowPolygon;
  color: Color;
  arrowColor: Color;
}

interface SelectedEdge {
  source: Position;
  target: Position;
  sourceNode: number;
  targetNode: number;
  outgoing: boolean;
  arrow: ArrowPolygon;
  color: Color;
  // Importance of the linked paper within its direction, in [0,1] (1 = most important).
  // Drives edge width, opacity, arrowhead size, and endpoint-ring radius so the citation
  // network reads in rank order at a glance.
  weight: number;
  rank: number;
}

interface Args {
  ds: Dataset;
  selectedNode: number | null;
  edgeMode: EdgeMode;
  show: boolean;
  zoom: number;
  baseZoom: number;
  filter: FilterArrays;
  monthMin: number;
  monthMax: number;
  // Connected-Papers relevance per node for the selection + the slider threshold, so selected
  // edges to papers hidden by the relevance slider are dropped too (matches the point cull).
  relevance: { score: Float32Array; sorted?: Float32Array } | null;
  relevanceThreshold: number;
  onSelect: (node: number) => void;
  // Hovering a connected paper's endpoint ring surfaces the same preview tooltip as
  // hovering a point (the ring sits atop the point layer and would otherwise swallow it).
  onHover: (node: number | null, x: number, y: number) => void;
  /** True while two sides are being compared; suppresses the ambient web. */
  comparing?: boolean;
}

// Edges encode exactly two things: HUE = direction (reference / citation / unrelated global
// link) and ALPHA = strength. Geometry is deliberately constant — a dense selection used to
// vary line width, arrowhead size, ring radius AND ring thickness by importance all at once,
// which turned a well-connected paper into an unreadable thicket. Thin uniform strokes keep
// the map legible and let colour carry the whole signal.

const EDGE_WIDTH_PX = 1.1;      // every selected edge, regardless of importance
const ARROW_LENGTH_PX = 7;      // constant arrowhead; direction is read from hue, not size
const ARROW_WIDTH_PX = 4.5;
// Alpha now carries importance alone, so it needs the full range to stay readable: a weak
// link fades toward the background instead of merely being thinner.
// Mirrors loadArtifacts.UNLOADED_LEVEL: a point whose tile has not arrived has no real
// coordinates, so nothing may be drawn to it.
const UNLOADED_LEVEL = 32767;

const ALPHA_MIN = 62;
const ALPHA_MAX = 255;
const alphaFor = (weight: number) =>
  Math.round(ALPHA_MIN + (ALPHA_MAX - ALPHA_MIN) * Math.max(0, Math.min(1, weight)));

function edgeHash(source: number, target: number): number {
  return ((Math.imul(source + 1, 73856093) ^ Math.imul(target + 1, 19349663)) >>> 0) % 1000;
}

function arrowPolygon(
  source: Position,
  target: Position,
  position: number,
  screenScale: number,
  lengthPixels: number,
  widthPixels: number,
): ArrowPolygon {
  const dx = target[0] - source[0];
  const dy = target[1] - source[1];
  const distance = Math.hypot(dx, dy);
  if (distance === 0) return [source, source, source];

  const ux = dx / distance;
  const uy = dy / distance;
  const edgePixels = distance * screenScale;
  const length = Math.min(lengthPixels, edgePixels * 0.34) / screenScale;
  const halfWidth = Math.min(widthPixels / 2, edgePixels * 0.18) / screenScale;
  const centerX = source[0] + dx * position;
  const centerY = source[1] + dy * position;
  const tip: Position = [centerX + ux * length * 0.6, centerY + uy * length * 0.6];
  const baseX = centerX - ux * length * 0.4;
  const baseY = centerY - uy * length * 0.4;
  const left: Position = [baseX - uy * halfWidth, baseY + ux * halfWidth];
  const right: Position = [baseX + uy * halfWidth, baseY - ux * halfWidth];
  return [tip, left, right];
}

export function useEdgeLayer({
  ds,
  selectedNode,
  edgeMode,
  show,
  zoom,
  baseZoom,
  filter,
  monthMin,
  monthMax,
  relevance,
  relevanceThreshold,
  onSelect,
  onHover,
  comparing = false,
}: Args) {
  const relativeZoom = zoom - baseZoom;
  const sampleThreshold =
    relativeZoom < 0.75 ? 360 : relativeZoom < 1.75 ? 560 : relativeZoom < 3 ? 800 : 1000;
  const maxScreenLength =
    relativeZoom < 0.75 ? 160 : relativeZoom < 1.75 ? 230 : relativeZoom < 3 ? 340 : 520;
  const baseAlpha = selectedNode === null
    ? relativeZoom < 0.75 ? 120 : 105
    : 18;
  const screenScale = 2 ** zoom;
  // Any active org/author filter hides edges touching a non-matching paper entirely (to
  // match the points, which are GPU-culled), so a filtered view shows only intra-set links.
  const hideNonMatch = filter.anyOrgAuthorActive;

  // More of the citation graph keeps arriving: zoom tiers for the ambient web, and the
  // selected paper's own shard for its complete network.
  const edgesEpoch = useEdgesEpoch();
  useNodeEdges(ds, selectedNode);

  const selectedEdges = useMemo(() => {
    if (selectedNode === null) return [];

    const edges: SelectedEdge[] = [];
    const perDirectionLimit = edgeMode === "both" ? 40 : 80;
    const prioritize = (ids: number[]) =>
      [...ids]
        .sort((a, b) => ds.papers[b].citedByCount - ds.papers[a].citedByCount)
        .slice(0, perDirectionLimit);

    // Importance weight in [0,1] for a linked paper at rank `index` within its own
    // (citation-sorted) fan. See importance.ts: blends citation magnitude with rank so the
    // ordering is always visible even when every linked paper is hugely cited. Matches the
    // details-panel citation graph's weighting.
    const weigh = (linked: number[]) => {
      const top = linked.length ? Math.log1p(ds.papers[linked[0]].citedByCount) : 0;
      const count = linked.length;
      return (node: number, index: number) =>
        importanceWeight(Math.log1p(ds.papers[node].citedByCount), top, index, count);
    };

    const add = (
      sourceNode: number,
      targetNode: number,
      outgoing: boolean,
      weight: number,
      rank: number,
    ) => {
      const source: Position = [ds.points.x[sourceNode], ds.points.y[sourceNode]];
      const target: Position = [ds.points.x[targetNode], ds.points.y[targetNode]];
      const directionColor = outgoing ? REFERENCE : CITER;
      // Importance shows up only as opacity; the arrowhead stays a fixed small size.
      const alpha = alphaFor(weight);
      edges.push({
        source,
        target,
        sourceNode,
        targetNode,
        outgoing,
        arrow: arrowPolygon(source, target, 0.72, screenScale, ARROW_LENGTH_PX, ARROW_WIDTH_PX),
        color: [directionColor[0], directionColor[1], directionColor[2], alpha],
        weight,
        rank,
      });
    };

    // ONE visibility predicate, matching what the points layer actually draws.
    //
    // The ambient layer already tested reveal level, month range and the org/author mask, but
    // the selected-paper overlay tested only relevance — so with any filter or date range
    // active, a selected paper's arrows pointed at papers the map was hiding. An arrow into
    // empty space is worse than no arrow: it asserts a link to something invisible.
    //
    // The tile check matters since D23: a point whose tile has not downloaded still has zeroed
    // coordinates, so its edge would be drawn to the map origin.
    const { revealLevel, monthIndex } = ds.points;
    const visible = (node: number) => {
      if (revealLevel[node] >= UNLOADED_LEVEL) return false;        // tile not loaded yet
      if (monthIndex[node] < monthMin || monthIndex[node] > monthMax) return false;
      if (hideNonMatch && filter.matchValue[node] === 0) return false;
      return true;
    };

    // Relevance slider: drop links to a paper scoring below the threshold, so the edge web
    // thins together with the points the slider hides.
    const passesRelevance = (node: number) =>
      relevanceThreshold <= 0
      || (relevance ? relevance.score[node] : 0) >= relevanceCutoff(relevance?.sorted, relevanceThreshold);

    const shown = (node: number) => visible(node) && passesRelevance(node);

    if (edgeMode === "out" || edgeMode === "both") {
      const refs = prioritize(ds.citesOut.get(selectedNode) ?? []).filter(shown);
      const w = weigh(refs);
      refs.forEach((target, i) => add(selectedNode, target, true, w(target, i), i));
    }
    if (edgeMode === "in" || edgeMode === "both") {
      const citers = prioritize(ds.citedBy.get(selectedNode) ?? []).filter(shown);
      const w = weigh(citers);
      citers.forEach((source, i) => add(source, selectedNode, false, w(source, i), i));
    }
    return edges;
  }, [ds, edgeMode, screenScale, selectedNode, relevance, relevanceThreshold,
      monthMin, monthMax, hideNonMatch, filter.matchValue,
      // The selected paper's adjacency is replaced wholesale when its shard lands, and the
      // ambient arrays grow as zoom tiers arrive. Without this the arrows freeze at whatever
      // fraction of the graph existed when the paper was clicked.
      edgesEpoch]);

  // Match the points layer's reveal-level LOD (usePointsLayer): a global edge is only drawn
  // when BOTH endpoints are currently revealed, so the edge web thins out with the points at
  // the zoomed-out home view instead of drawing lines to hidden nodes.
  const activeLevel = Math.floor(Math.max(0, zoom - baseZoom));
  const revealLevel = ds.points.revealLevel;

  const globalEdges = useMemo(() => {
    if (!show) return [];

    const edges: GlobalEdge[] = [];
    for (let index = 0; index < ds.edges.src.length; index++) {
      const sourceNode = ds.edges.src[index];
      const targetNode = ds.edges.dst[index];
      if (
        sourceNode === selectedNode ||
        targetNode === selectedNode ||
        revealLevel[sourceNode] > activeLevel ||
        revealLevel[targetNode] > activeLevel ||
        ds.points.monthIndex[sourceNode] < monthMin ||
        ds.points.monthIndex[sourceNode] > monthMax ||
        ds.points.monthIndex[targetNode] < monthMin ||
        ds.points.monthIndex[targetNode] > monthMax
      ) {
        continue;
      }

      // Drop any edge touching a non-matching paper when a filter is active — hidden points
      // must not keep dangling links (matches the unconditional point cull).
      if (
        hideNonMatch &&
        (filter.matchValue[sourceNode] === 0 || filter.matchValue[targetNode] === 0)
      ) {
        continue;
      }
      if (edgeHash(sourceNode, targetNode) >= sampleThreshold) continue;

      const source: Position = [ds.points.x[sourceNode], ds.points.y[sourceNode]];
      const target: Position = [ds.points.x[targetNode], ds.points.y[targetNode]];
      const screenLength = Math.hypot(
        source[0] - target[0],
        source[1] - target[1],
      ) * screenScale;
      if (screenLength < 10 || screenLength > maxScreenLength) continue;

      const influence = Math.min(1, Math.log1p(ds.points.citedByCount[targetNode]) / 8);
      const alpha = Math.round(baseAlpha * (0.52 + 0.48 * influence));
      const arrowAlpha = Math.min(180, Math.round(alpha * 1.8));
      edges.push({
        source,
        target,
        arrow: arrowPolygon(source, target, 0.68, screenScale, 6.5, 5),
        color: [GLOBAL_EDGE[0], GLOBAL_EDGE[1], GLOBAL_EDGE[2], alpha],
        arrowColor: [150, 186, 218, arrowAlpha],
      });
    }
    return edges;
  }, [
    activeLevel,
    baseAlpha,
    ds,
    filter.anyOrgAuthorActive,
    filter.matchValue,
    hideNonMatch,
    maxScreenLength,
    revealLevel,
    sampleThreshold,
    screenScale,
    selectedNode,
    show,
    monthMax,
    monthMin,
  ]);

  if (!show || comparing) {
    // Ambient edges are suppressed while comparing. They are drawn between whatever papers are
    // revealed at this zoom, which in a comparison means links between papers that belong to
    // NEITHER side — arrows sprawling across two panes that are meant to show one researcher's
    // work each. The selection network still draws, because that is scoped to a paper.
    return { background: [], foreground: [] };
  }

  const globalLines = new LineLayer<GlobalEdge>({
    id: "citation-global-lines",
    data: globalEdges,
    getSourcePosition: (edge) => edge.source,
    getTargetPosition: (edge) => edge.target,
    getColor: (edge) => edge.color,
    getWidth: 1.4,
    widthUnits: "pixels",
    widthMinPixels: 1,
    widthMaxPixels: 2,
    pickable: false,
    parameters: { depthCompare: "always" },
  });

  const globalArrows = new SolidPolygonLayer<GlobalEdge>({
    id: "citation-global-arrows",
    data: globalEdges,
    getPolygon: (edge) => edge.arrow,
    getFillColor: (edge) => edge.arrowColor,
    filled: true,
    extruded: false,
    pickable: false,
    parameters: { depthCompare: "always" },
  });

  if (selectedEdges.length === 0) {
    return { background: [globalLines, globalArrows], foreground: [] };
  }

  const foreground = [
    new LineLayer<SelectedEdge>({
      id: "citation-selected-lines",
      data: selectedEdges,
      getSourcePosition: (edge) => edge.source,
      getTargetPosition: (edge) => edge.target,
      getColor: (edge) => edge.color,
      // Constant hairline: importance is in the alpha of getColor, not the stroke weight.
      getWidth: EDGE_WIDTH_PX,
      widthUnits: "pixels",
      widthMinPixels: 1,
      widthMaxPixels: EDGE_WIDTH_PX,
      pickable: true,
      autoHighlight: true,
      highlightColor: [255, 255, 255, 255],
      parameters: { depthCompare: "always" },
      updateTriggers: { getWidth: [selectedNode, edgeMode], getColor: [selectedNode, edgeMode] },
      onClick: (info) => {
        if (!info.object) return;
        onSelect(info.object.outgoing ? info.object.targetNode : info.object.sourceNode);
      },
      // Hovering a link previews the paper at its FAR end — the one it connects the selection
      // to — so a link is readable without having to land on its endpoint dot.
      onHover: (info) =>
        onHover(
          info.object
            ? info.object.outgoing
              ? info.object.targetNode
              : info.object.sourceNode
            : null,
          info.x,
          info.y,
        ),
    }),
    new SolidPolygonLayer<SelectedEdge>({
      id: "citation-selected-arrows",
      data: selectedEdges,
      getPolygon: (edge) => edge.arrow,
      getFillColor: (edge) => edge.color,
      filled: true,
      extruded: false,
      pickable: true,
      parameters: { depthCompare: "always" },
      onClick: (info) => {
        if (!info.object) return;
        onSelect(info.object.outgoing ? info.object.targetNode : info.object.sourceNode);
      },
    }),
  ];

  return { background: [globalLines, globalArrows], foreground };
}
