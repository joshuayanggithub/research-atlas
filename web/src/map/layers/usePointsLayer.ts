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
  yearMin: number;
  yearMax: number;
  selectedNode: number | null;
  hoverNode: number | null;
  onClick: (nodeId: number | null) => void;
  onHover: (nodeId: number | null) => void;
}

export function usePointsLayer({
  ds,
  colorMode,
  filter,
  orgDisplayMode,
  yearMin,
  yearMax,
  selectedNode,
  hoverNode,
  onClick,
  onHover,
}: Args) {
  const n = ds.points.count;

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

  return new ScatterplotLayer({
    id: "points",
    data: { length: n },
    getPosition: (_: unknown, { index }: { index: number }) =>
      [ds.points.x[index], ds.points.y[index]] as [number, number],
    getFillColor: (_: unknown, { index }: { index: number }) => {
      const dim = filter.anyOrgAuthorActive && filter.matchValue[index] === 0;
      const a = dim ? 26 : 200;
      return [rgb[index * 3], rgb[index * 3 + 1], rgb[index * 3 + 2], a] as [
        number, number, number, number,
      ];
    },
    // Radius scales gently with citation count; selected/hover points get a floor bump.
    getRadius: (_: unknown, { index }: { index: number }) => {
      const c = ds.points.citedByCount[index];
      const base = 1 + Math.log10(1 + c) * 0.6;
      if (index === selectedNode) return base * 3;
      if (index === hoverNode) return base * 2;
      return base;
    },
    radiusUnits: "common",
    radiusMinPixels: 1.5,
    radiusMaxPixels: 14,
    pickable: true,
    autoHighlight: true,
    highlightColor: [255, 255, 255, 120],
    onClick: (info) => onClick(info.index >= 0 ? info.index : null),
    onHover: (info) => onHover(info.index >= 0 ? info.index : null),

    // GPU date filter + optional org/author hide, both via DataFilterExtension.
    // channel 0 = year, channel 1 = org/author match (only enforced in "hide" mode).
    extensions: [new DataFilterExtension({ filterSize: 2 })],
    getFilterValue: (_: unknown, { index }: { index: number }) =>
      [ds.points.year[index], hideNonMatch ? filter.matchValue[index] : 1] as [
        number, number,
      ],
    filterRange: [
      [yearMin, yearMax],
      [1, 1],
    ],
    updateTriggers: {
      getFillColor: [colorMode, filter.matchValue, filter.anyOrgAuthorActive, rgb],
      getRadius: [selectedNode, hoverNode],
      getFilterValue: [hideNonMatch, filter.matchValue],
    },
  });
}
