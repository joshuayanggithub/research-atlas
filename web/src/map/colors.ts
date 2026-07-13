// Color helpers: point fill by color mode, plus dim/emphasis for filter masks.

import type { Dataset } from "../data/types";
import type { ColorMode } from "../state/store";

export type RGBA = [number, number, number, number];

const RECENCY_COLD: [number, number, number] = [70, 90, 160];
const RECENCY_HOT: [number, number, number] = [250, 180, 60];

// Distinct hues for org coloring (color-by-org mode), keyed by org index.
export const ORG_COLORS: [number, number, number][] = [
  [99, 179, 237], [246, 173, 85], [104, 211, 145], [237, 100, 166],
  [159, 122, 234], [246, 224, 94], [79, 209, 197], [252, 129, 129],
];

export function lerp(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}

// Base color for a point given the color mode (before dim/emphasis is applied).
export function baseColor(
  ds: Dataset,
  i: number,
  mode: ColorMode,
  orgOfNode?: Int32Array,
): [number, number, number] {
  if (mode === "recency") {
    const { year } = ds.points;
    const yMin = parseInt(ds.manifest.corpus.date_from.slice(0, 4));
    const yMax = parseInt(ds.manifest.corpus.date_to.slice(0, 4));
    const t = yMax > yMin ? (year[i] - yMin) / (yMax - yMin) : 0.5;
    return [
      lerp(RECENCY_COLD[0], RECENCY_HOT[0], t),
      lerp(RECENCY_COLD[1], RECENCY_HOT[1], t),
      lerp(RECENCY_COLD[2], RECENCY_HOT[2], t),
    ];
  }
  if (mode === "org" && orgOfNode) {
    const o = orgOfNode[i];
    if (o < 0) return [90, 96, 110];
    return ORG_COLORS[o % ORG_COLORS.length];
  }
  // subfield (default): precomputed r/g/b in points.arrow
  return [ds.points.r[i], ds.points.g[i], ds.points.b[i]];
}
