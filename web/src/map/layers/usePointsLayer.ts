// The scatterplot of papers. Positions come from points.arrow (x,y); fill color from the
// active color mode; org/author filtering dims or hides non-matches; the date range is
// applied on the GPU via DataFilterExtension so slider drags don't recompute anything.

import { ScatterplotLayer } from "@deck.gl/layers";
import { DataFilterExtension } from "@deck.gl/extensions";
import { useMemo } from "react";
import type { Dataset } from "../../data/types";
import type { ColorMode, OrgDisplayMode } from "../../state/store";
import type { FilterArrays } from "../../filters/useFilterMask";
import { baseColor } from "../colors";

interface Args {
  ds: Dataset;
  colorMode: ColorMode;
  filter: FilterArrays;
  orgDisplayMode: OrgDisplayMode;
  monthMin: number;
  monthMax: number;
  selectedNode: number | null;
  hoverNode: number | null;
  zoom: number;
  baseZoom: number;
  onClick: (nodeId: number | null) => void;
  onHover: (nodeId: number | null, x: number, y: number) => void;
}

// Level-of-detail: at the fully-zoomed-out "fit" view, 71k points overlap into a solid
// mass (measured: ~90% of points sit within 2px of a neighbor, and a dot is 2px wide). So
// zoomed out we show only the most-cited papers and reveal the rest as the user zooms in,
// where there is pixel room for them. `LOD_MIN_VISIBLE` points are always shown so a filter
// that matches only low-citation papers is never blank.
const LOD_MIN_VISIBLE = 6000;
// Fraction of the corpus visible at the fit zoom, ramping to 1.0 by LOD_FULL_OFFSET.
const LOD_BASE_FRACTION = 0.12;
const LOD_FULL_OFFSET = 3.5; // zoom offset (from fit) at which all points are shown

export function usePointsLayer({
  ds,
  colorMode,
  filter,
  orgDisplayMode,
  monthMin,
  monthMax,
  selectedNode,
  hoverNode,
  zoom,
  baseZoom,
  onClick,
  onHover,
}: Args) {
  const n = ds.points.count;

  // Descending citation rank per point (0 = most cited). A point is "important" if its rank
  // is within the currently-visible budget. Computed once per dataset; the actual threshold
  // is applied cheaply on the GPU so panning/zooming never recomputes this.
  const rank = useMemo(() => {
    const order = Array.from({ length: n }, (_, i) => i);
    order.sort((a, b) => ds.points.citedByCount[b] - ds.points.citedByCount[a]);
    const r = new Int32Array(n);
    for (let pos = 0; pos < n; pos++) r[order[pos]] = pos;
    return r;
  }, [ds, n]);

  // How many points to reveal at the current zoom. A selection or active filter forces the
  // full set (LOD would otherwise hide connected/matching papers that happen to be low-cited).
  const relOffset = Math.max(0, zoom - baseZoom);
  const forceAll = selectedNode !== null || filter.anyOrgAuthorActive;
  const visibleCount = useMemo(() => {
    if (forceAll) return n;
    const t = Math.min(1, relOffset / LOD_FULL_OFFSET);
    const frac = LOD_BASE_FRACTION + (1 - LOD_BASE_FRACTION) * t;
    return Math.min(n, Math.max(LOD_MIN_VISIBLE, Math.round(n * frac)));
  }, [forceAll, relOffset, n]);

  // Fade + shrink dots at the fit view so the home map reads as airy topic fields rather
  // than a wall of ink; both ramp to full by LOD_FULL_OFFSET (matching the LOD reveal).
  const lodT = forceAll ? 1 : Math.min(1, relOffset / LOD_FULL_OFFSET);
  const layerOpacity = 0.55 + 0.45 * lodT;
  const radiusScale = 0.72 + 0.28 * lodT;

  // Precompute base RGB per point for the active color mode (cheap; memo on mode).
  const rgb = useMemo(() => {
    const arr = new Uint8Array(n * 3);
    for (let i = 0; i < n; i++) {
      const c = baseColor(ds, i, colorMode, filter.orgOfNode);
      arr[i * 3] = c[0];
      arr[i * 3 + 1] = c[1];
      arr[i * 3 + 2] = c[2];
    }
    return arr;
  }, [ds, colorMode, filter.orgOfNode, n]);

  // Whether org/author filtering hides (vs dims) non-matches.
  const hideNonMatch = filter.anyOrgAuthorActive && orgDisplayMode === "hide";
  const connected = useMemo(() => {
    if (selectedNode === null) return null;
    return new Set([
      selectedNode,
      ...(ds.citesOut.get(selectedNode) ?? []),
      ...(ds.citedBy.get(selectedNode) ?? []),
    ]);
  }, [ds, selectedNode]);

  return new ScatterplotLayer({
    id: "points",
    data: { length: n },
    opacity: layerOpacity,
    radiusScale,
    getPosition: (_: unknown, { index }: { index: number }) =>
      [ds.points.x[index], ds.points.y[index]] as [number, number],
    getFillColor: (_: unknown, { index }: { index: number }) => {
      // When an org/author filter is active, non-matching papers are effectively hidden
      // (near-transparent) rather than merely dimmed — the filtered set should read as the
      // whole map, so its topic labels/colors dominate. In "hide" mode the GPU filter culls
      // them entirely; here we handle the default "dim" mode.
      // Papers outside a selection's citation context are culled on the GPU (channel 2
      // below), so they never reach this branch — the selected paper + its cited/citing
      // set read as the only papers on the map.
      const filteredOut = filter.anyOrgAuthorActive && filter.matchValue[index] === 0;
      const a = filteredOut ? 8 : 210;
      return [rgb[index * 3], rgb[index * 3 + 1], rgb[index * 3 + 2], a] as [
        number, number, number, number,
      ];
    },
    // Radius encodes citation count as an importance signal, on a log scale so the
    // 0 → ~10K span is a *noticeable but restrained* ~2x radius (not a 4x blob): a
    // 0-cite paper is 1.0, a 10K-cite paper ≈ 2.1. Small overall so dense topic regions
    // read as distinct color fields rather than merging; citation edges carry visual weight.
    // selected/hover/connected get a floor bump; filtered-out papers shrink.
    getRadius: (_: unknown, { index }: { index: number }) => {
      const c = ds.points.citedByCount[index];
      const base = 1.0 + Math.log10(1 + c) * 0.28; // c=0→1.0, 10→1.3, 100→1.56, 1K→1.84, 10K→2.1
      if (index === selectedNode) return base * 2.8;
      if (index === hoverNode) return base * 1.9;
      if (connected?.has(index)) return base * 1.35;
      if (filter.anyOrgAuthorActive && filter.matchValue[index] === 0) return base * 0.55;
      return base;
    },
    radiusUnits: "common",
    radiusMinPixels: 1,
    radiusMaxPixels: 13,
    pickable: true,
    autoHighlight: true,
    highlightColor: [255, 255, 255, 120],
    onClick: (info) => onClick(info.index >= 0 ? info.index : null),
    onHover: (info) => onHover(info.index >= 0 ? info.index : null, info.x, info.y),

    // GPU date filter + org/author hide + selection cull + zoom LOD, all via
    // DataFilterExtension. channel 0 = month index (date filter); channel 1 = org/author
    // match (only in "hide" mode); channel 2 = selection membership (only the selected node
    // + its cited/citing set pass when a paper is selected); channel 3 = citation-rank LOD
    // (points ranked beyond the current zoom's budget are culled when zoomed out). All four
    // are applied on the GPU, so panning/zooming never re-evaluates JS per point.
    extensions: [new DataFilterExtension({ filterSize: 4 })],
    getFilterValue: (_: unknown, { index }: { index: number }) =>
      [
        ds.points.monthIndex[index],
        hideNonMatch ? filter.matchValue[index] : 1,
        connected === null || connected.has(index) ? 1 : 0,
        rank[index],
      ] as [number, number, number, number],
    filterRange: [
      [monthMin, monthMax],
      [1, 1],
      [1, 1],
      [0, visibleCount - 1],
    ],
    updateTriggers: {
      getFillColor: [
        colorMode,
        filter.matchValue,
        filter.anyOrgAuthorActive,
        rgb,
        selectedNode,
      ],
      getRadius: [selectedNode, hoverNode, connected, filter.matchValue, filter.anyOrgAuthorActive],
      getFilterValue: [hideNonMatch, filter.matchValue, connected, rank],
    },
  });
}
