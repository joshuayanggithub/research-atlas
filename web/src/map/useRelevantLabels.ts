// A semantic-zoom label is only meaningful over papers that are actually *visible*. When
// the view is restricted — by an org/author filter, OR by selecting a single paper (which
// shows only that paper + its citation network) — this hook returns the set of label ids
// those visible papers populate, so labels over now-empty regions disappear instead of
// blanketing the whole map. Returns null when nothing is restricted (all labels relevant).
//
// Method: each visible paper votes for its nearest label centroid within each band — but
// ONLY when that centroid is actually near the paper (within a per-band radius). Without the
// distance gate, a selected paper's citation network (which is deliberately spread across the
// whole map — a landmark paper cites ~2,000 papers spanning ~90% of the map) would have each
// neighbor claim its nearest coarse-band label regardless of distance, lighting up nearly
// every label. The gate makes a paper vote only for labels it genuinely sits under, so labels
// over regions the visible set doesn't populate disappear. A label is then kept when its votes
// clear `max(MIN_VOTES, FRACTION * region size)` (a flat 1 for selections, since the visible
// set is small).
//
// Memoized on (dataset, filter, selectedNode) so it recomputes only when the restriction
// changes, never on pan/zoom.

import { useMemo } from "react";
import type { Dataset } from "../data/types";
import type { FilterArrays } from "../filters/useFilterMask";

const MIN_VOTES = 2; // a region needs at least this many visible papers to keep its label
const FRACTION = 0.02; // ...and at least this share of the region's own size

export function useRelevantLabels(
  ds: Dataset | null,
  filter: FilterArrays | null,
  selectedNode: number | null = null,
): Set<number> | null {
  return useMemo(() => {
    if (!ds) return null;
    const filterActive = !!filter?.anyOrgAuthorActive;
    const hasSelection = selectedNode !== null && selectedNode >= 0;
    if (!filterActive && !hasSelection) return null;

    // The visible paper set: a selection shows only the selected node + its cited/citing
    // neighbors; an org/author filter shows all matching papers. A selection takes
    // precedence (it's the tighter restriction, matching what the map actually draws).
    let isVisible: (i: number) => boolean;
    if (hasSelection) {
      const visible = new Set<number>([
        selectedNode,
        ...(ds.citesOut.get(selectedNode) ?? []),
        ...(ds.citedBy.get(selectedNode) ?? []),
      ]);
      isVisible = (i) => visible.has(i);
    } else {
      const { matchValue } = filter!;
      isVisible = (i) => matchValue[i] === 1;
    }

    // Group labels by band; carry each label's region size so the threshold can scale.
    const byLevel = new Map<number, { id: number; x: number; y: number; count: number }[]>();
    for (const l of ds.labels.labels) {
      const arr = byLevel.get(l.level) ?? [];
      arr.push({ id: l.id, x: l.x, y: l.y, count: l.count });
      byLevel.set(l.level, arr);
    }

    // Per-band vote radius²: a paper only votes for a label centroid it genuinely sits under.
    // Derive it from each band's own centroid spacing (median nearest-neighbor distance among
    // the band's labels), so coarse bands (few, far-apart regions) get a large radius and fine
    // bands a small one — self-scaling with no magic constant. RADIUS_FACTOR widens it a bit
    // so a paper near a region edge still counts. Bands with <2 labels have no gate (Infinity).
    const RADIUS_FACTOR = 1.3;
    const radius2 = new Map<number, number>();
    for (const [level, arr] of byLevel) {
      if (arr.length < 2) {
        radius2.set(level, Infinity);
        continue;
      }
      const nn: number[] = [];
      for (let a = 0; a < arr.length; a++) {
        let bestD = Infinity;
        for (let b = 0; b < arr.length; b++) {
          if (a === b) continue;
          const dx = arr[a].x - arr[b].x;
          const dy = arr[a].y - arr[b].y;
          const d = dx * dx + dy * dy;
          if (d < bestD) bestD = d;
        }
        if (bestD < Infinity) nn.push(bestD);
      }
      nn.sort((p, q) => p - q);
      const median = nn[Math.floor(nn.length / 2)] ?? Infinity;
      radius2.set(level, median * RADIUS_FACTOR * RADIUS_FACTOR);
    }

    const votes = new Map<number, number>();
    const { x, y, count } = ds.points;

    for (let i = 0; i < count; i++) {
      if (!isVisible(i)) continue;
      const px = x[i];
      const py = y[i];
      // Each visible paper votes for its nearest label centroid in every band — but only if
      // that centroid is within the band's vote radius (so scattered citation neighbors don't
      // claim far-away labels).
      for (const [level, arr] of byLevel) {
        let best = -1;
        let bestD = Infinity;
        for (let j = 0; j < arr.length; j++) {
          const dx = arr[j].x - px;
          const dy = arr[j].y - py;
          const d = dx * dx + dy * dy;
          if (d < bestD) {
            bestD = d;
            best = arr[j].id;
          }
        }
        if (best >= 0 && bestD <= (radius2.get(level) ?? Infinity)) {
          votes.set(best, (votes.get(best) ?? 0) + 1);
        }
      }
    }

    // A single-paper selection is a tiny visible set, so the FRACTION*region-size floor
    // would reject every coarse-band label. Use a flat threshold of 1 vote for selections
    // (keep any region a visible paper falls in); keep the scaled threshold for filters.
    const relevant = new Set<number>();
    for (const arr of byLevel.values()) {
      for (const l of arr) {
        const v = votes.get(l.id) ?? 0;
        const threshold = hasSelection ? 1 : Math.max(MIN_VOTES, FRACTION * l.count);
        if (v >= threshold) relevant.add(l.id);
      }
    }
    return relevant;
  }, [ds, filter, selectedNode]);
}
