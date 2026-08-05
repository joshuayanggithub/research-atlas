// A semantic-zoom label is only meaningful over papers that are actually *visible*. When
// the view is restricted — by an org/author filter, OR by selecting a single paper (which
// shows only that paper + its citation network) — this hook returns the set of label ids
// those visible papers populate, so labels over now-empty regions disappear instead of
// blanketing the whole map. Returns null when nothing is restricted (all labels relevant).
//
// Method: each visible paper votes for its nearest label centroid within each band; a label
// is kept only when its votes clear `max(MIN_VOTES, FRACTION * region size)`. The fraction
// self-scales: a broad "continent" needs many votes, a micro-cluster just a couple. For a
// single-paper selection the visible set is small, so this naturally keeps only the handful
// of labels over the selected paper and its citations.
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

    const votes = new Map<number, number>();
    const { x, y, count } = ds.points;

    for (let i = 0; i < count; i++) {
      if (!isVisible(i)) continue;
      const px = x[i];
      const py = y[i];
      // Each visible paper votes for its nearest label centroid in every band.
      for (const arr of byLevel.values()) {
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
        if (best >= 0) votes.set(best, (votes.get(best) ?? 0) + 1);
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
