// Citation links on the main map.
//
// Ordinary browsing gets a zoom-adaptive sample of the global directed graph.
// Every rendered link has a screen-sized triangle pointing from the citing paper to the
// cited paper. Selecting a paper overlays its incoming/outgoing links at full contrast.

import { LineLayer, ScatterplotLayer, SolidPolygonLayer } from "@deck.gl/layers";
import { useMemo } from "react";
import type { Dataset } from "../../data/types";
import type { FilterArrays } from "../../filters/useFilterMask";
import type { EdgeMode, OrgDisplayMode } from "../../state/store";
import { importanceWeight } from "../importance";

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

interface EdgeEndpoint {
  node: number;
  position: Position;
  outgoing: boolean;
  weight: number;
}

interface Args {
  ds: Dataset;
  selectedNode: number | null;
  edgeMode: EdgeMode;
  show: boolean;
  zoom: number;
  baseZoom: number;
  filter: FilterArrays;
  orgDisplayMode: OrgDisplayMode;
  monthMin: number;
  monthMax: number;
  onSelect: (node: number) => void;
}

const OUTGOING = [55, 214, 199] as const;
const INCOMING = [244, 162, 97] as const;
const GLOBAL = [116, 151, 184] as const;

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
  orgDisplayMode,
  monthMin,
  monthMax,
  onSelect,
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
  const hideNonMatch = filter.anyOrgAuthorActive && orgDisplayMode === "hide";

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
      const directionColor = outgoing ? OUTGOING : INCOMING;
      // Higher-importance links are more opaque; the arrowhead also grows with weight.
      const alpha = Math.round(150 + 105 * weight);
      edges.push({
        source,
        target,
        sourceNode,
        targetNode,
        outgoing,
        arrow: arrowPolygon(source, target, 0.72, screenScale, 9 + 7 * weight, 6 + 5 * weight),
        color: [directionColor[0], directionColor[1], directionColor[2], alpha],
        weight,
        rank,
      });
    };

    if (edgeMode === "out" || edgeMode === "both") {
      const refs = prioritize(ds.citesOut.get(selectedNode) ?? []);
      const w = weigh(refs);
      refs.forEach((target, i) => add(selectedNode, target, true, w(target, i), i));
    }
    if (edgeMode === "in" || edgeMode === "both") {
      const citers = prioritize(ds.citedBy.get(selectedNode) ?? []);
      const w = weigh(citers);
      citers.forEach((source, i) => add(source, selectedNode, false, w(source, i), i));
    }
    return edges;
  }, [ds, edgeMode, screenScale, selectedNode]);

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

      const bothMatch =
        !filter.anyOrgAuthorActive ||
        (filter.matchValue[sourceNode] === 1 && filter.matchValue[targetNode] === 1);
      if (hideNonMatch && !bothMatch) continue;
      if (edgeHash(sourceNode, targetNode) >= sampleThreshold) continue;

      const source: Position = [ds.points.x[sourceNode], ds.points.y[sourceNode]];
      const target: Position = [ds.points.x[targetNode], ds.points.y[targetNode]];
      const screenLength = Math.hypot(
        source[0] - target[0],
        source[1] - target[1],
      ) * screenScale;
      if (screenLength < 10 || screenLength > maxScreenLength) continue;

      const influence = Math.min(1, Math.log1p(ds.points.citedByCount[targetNode]) / 8);
      const filterFactor = bothMatch ? 1 : 0.22;
      const alpha = Math.round(baseAlpha * (0.52 + 0.48 * influence) * filterFactor);
      const arrowAlpha = Math.min(180, Math.round(alpha * 1.8));
      edges.push({
        source,
        target,
        arrow: arrowPolygon(source, target, 0.68, screenScale, 6.5, 5),
        color: [GLOBAL[0], GLOBAL[1], GLOBAL[2], alpha],
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

  if (!show) {
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

  const endpoints: EdgeEndpoint[] = selectedEdges.map((edge) => ({
    node: edge.outgoing ? edge.targetNode : edge.sourceNode,
    position: edge.outgoing ? edge.target : edge.source,
    outgoing: edge.outgoing,
    weight: edge.weight,
  }));

  const foreground = [
    new LineLayer<SelectedEdge>({
      id: "citation-selected-lines",
      data: selectedEdges,
      getSourcePosition: (edge) => edge.source,
      getTargetPosition: (edge) => edge.target,
      getColor: (edge) => edge.color,
      // Width encodes the linked paper's importance (1.4px .. 4.4px) so the strongest
      // citations read as the boldest lines, in rank order.
      getWidth: (edge) => 1.4 + 3 * edge.weight,
      widthUnits: "pixels",
      widthMinPixels: 1,
      widthMaxPixels: 5,
      pickable: true,
      autoHighlight: true,
      highlightColor: [255, 255, 255, 255],
      parameters: { depthCompare: "always" },
      updateTriggers: { getWidth: [selectedNode, edgeMode], getColor: [selectedNode, edgeMode] },
      onClick: (info) => {
        if (!info.object) return;
        onSelect(info.object.outgoing ? info.object.targetNode : info.object.sourceNode);
      },
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
    new ScatterplotLayer<EdgeEndpoint>({
      id: "citation-selected-endpoints",
      data: endpoints,
      getPosition: (edge) => edge.position,
      filled: true,
      stroked: true,
      getFillColor: [12, 14, 20, 220],
      getLineColor: (edge) =>
        [...(edge.outgoing ? OUTGOING : INCOMING), 255] as [
          number,
          number,
          number,
          number,
        ],
      // Ring radius encodes importance too, so the most-cited linked papers read as the
      // largest rings — reinforcing the width/opacity ranking on the lines.
      getRadius: (endpoint) => 2.0 + 2.4 * endpoint.weight,
      radiusUnits: "common",
      radiusMinPixels: 3,
      radiusMaxPixels: 11,
      lineWidthMinPixels: 2,
      pickable: true,
      parameters: { depthCompare: "always" },
      updateTriggers: { getRadius: [selectedNode, edgeMode] },
      onClick: (info) => {
        if (info.object) onSelect(info.object.node);
      },
    }),
  ];

  return { background: [globalLines, globalArrows], foreground };
}
