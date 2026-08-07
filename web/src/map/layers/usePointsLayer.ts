// The scatterplot of papers. Positions come from points.arrow (x,y); fill color from the
// active color mode; org/author filtering HIDES non-matches (GPU-culled, not clickable);
// the date range is applied on the GPU via DataFilterExtension so slider drags don't
// recompute anything.

import { ScatterplotLayer } from "@deck.gl/layers";
import { DataFilterExtension } from "@deck.gl/extensions";
import { useMemo } from "react";
import type { Dataset } from "../../data/types";
import type { ColorMode } from "../../state/store";
import type { FilterArrays } from "../../filters/useFilterMask";
import { baseColor } from "../colors";
import { lodRamp } from "../importance";

// Upper bound for the reveal-level filter when all points should show (selection/filter).
// Larger than any level s12 emits.
const MAX_REVEAL_LEVEL = 32767;

interface Args {
  ds: Dataset;
  colorMode: ColorMode;
  filter: FilterArrays;
  monthMin: number;
  monthMax: number;
  selectedNode: number | null;
  hoverNode: number | null;
  // Connected-Papers relevance per node for the current selection (null when nothing selected).
  relevance: Map<number, number> | null;
  // Hide connected papers scoring below this (0 = whole network .. 1 = most relevant only).
  relevanceThreshold: number;
  zoom: number;
  baseZoom: number;
  onClick: (nodeId: number | null) => void;
  onHover: (nodeId: number | null, x: number, y: number) => void;
}

// Level-of-detail thresholds and the visible-count / ramp math live in ../importance.ts
// (pure + unit-tested). At the fit view 71k points overlap into a solid mass (~90% within
// 2px of a neighbor, dot diameter 2px), so we show only the most-cited fraction and reveal
// the rest as the user zooms in.
export function usePointsLayer({
  ds,
  colorMode,
  filter,
  monthMin,
  monthMax,
  selectedNode,
  hoverNode,
  relevance,
  relevanceThreshold,
  zoom,
  baseZoom,
  onClick,
  onHover,
}: Args) {
  const n = ds.points.count;

  // Level-of-detail via reveal_level (s12 greedy thinning): a point renders only when its
  // reveal_level <= the active level, which GUARANTEES no two visible points overlap at any
  // zoom (each level maintains a minimum on-screen separation). This replaces the old
  // citation-rank budget, which merely limited the count and still let points collide.
  //
  // Level 0 fills at the fit zoom (base_divisor tuned so ~a few hundred points separate by
  // ~1/40 of the span). Each reveal level corresponds to one 2x zoom step, matching how the
  // thinning radius halves per level; +1 headroom keeps the next level's points appearing
  // just before you need them. A selection or active filter forces all levels so nothing
  // connected/matching is hidden.
  const relOffset = Math.max(0, zoom - baseZoom);
  const forceAll = selectedNode !== null || filter.anyOrgAuthorActive;
  // At the fit zoom (relOffset 0) show only level 0 — the sparsest, guaranteed-separated
  // set; each ~1 zoom step in reveals the next level. floor (not +1) so the home view is
  // the calibrated sparse set rather than already two levels deep.
  const activeLevel = forceAll ? MAX_REVEAL_LEVEL : Math.floor(relOffset);

  // Fade + shrink dots at the fit view so the home map reads as airy topic fields rather
  // than a wall of ink; both ramp to full over the first few zoom steps.
  const lodT = lodRamp(relOffset, forceAll);
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

  // Any active org/author filter HIDES non-matching papers entirely (GPU-culled below — not
  // drawn, not pickable), so a filtered view shows only the matching set, never a dimmed
  // backdrop. (Previously this was an optional "hide" mode; now it is unconditional.)
  const hideNonMatch = filter.anyOrgAuthorActive;
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
      // Non-matching (org/author filter) and out-of-selection-context papers are culled on
      // the GPU (channels 2 & 1 below), so they never reach this branch — only visible
      // papers are colored, all at full opacity.
      return [rgb[index * 3], rgb[index * 3 + 1], rgb[index * 3 + 2], 210] as [
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
    // + its cited/citing set pass when a paper is selected); channel 3 = reveal_level LOD
    // (a point shows only when its reveal_level <= the active level, guaranteeing no overlap
    // at any zoom). All four are applied on the GPU, so pan/zoom never re-evaluate per point.
    // Channel 2 = selection membership + relevance. With no selection every point passes
    // (1000). With a selection, a connected paper carries its Connected-Papers relevance
    // (score×1000, so the slider's [0,1] threshold maps to [0,1000]); the selected node is
    // pinned to 1000 (always shown); non-connected papers get -1 (always culled). The slider
    // raises the filterRange floor to progressively hide the least-relevant connected papers.
    extensions: [new DataFilterExtension({ filterSize: 4 })],
    getFilterValue: (_: unknown, { index }: { index: number }) =>
      [
        ds.points.monthIndex[index],
        hideNonMatch ? filter.matchValue[index] : 1,
        connected === null
          ? 1000
          : index === selectedNode
            ? 1000
            : connected.has(index)
              ? Math.round((relevance?.get(index) ?? 0) * 1000)
              : -1,
        ds.points.revealLevel[index],
      ] as [number, number, number, number],
    filterRange: [
      [monthMin, monthMax],
      [1, 1],
      // Selected node (1000) always passes; connected papers pass when score ≥ threshold.
      [connected === null ? 1 : Math.round(relevanceThreshold * 1000), 1000],
      [0, activeLevel],
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
      getFilterValue: [hideNonMatch, filter.matchValue, connected, relevance, ds.points.revealLevel],
    },
  });
}
