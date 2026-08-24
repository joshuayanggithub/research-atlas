// Membership mask for a two-sided comparison.
//
// Encodes the side directly into the value so the GPU can split the panes with the filter
// channel it already has (DataFilterExtension is capped at four channels and all four are
// spoken for). The codes are ordered so each pane is a CONTIGUOUS range:
//
//   0 = neither    1 = A only    2 = both    3 = B only
//
//   pane A -> filterRange [1, 2]      pane B -> filterRange [2, 3]
//
// "Both" sits in the middle precisely so it falls inside both ranges and is drawn in each pane.

import { useMemo } from "react";
import type { CompareSide } from "../state/store";
import type { Dataset } from "../data/types";
import { useAuthorPapers } from "../data/useAuthorPapers";
import { useOrgNodes } from "../data/useOrgNodes";

export const CMP_NONE = 0;
export const CMP_A = 1;
export const CMP_BOTH = 2;
export const CMP_B = 3;

export interface CompareMask {
  /** Per-node side code (see above). */
  value: Uint8Array;
  counts: { a: number; b: number; both: number };
  /** Node ids in both sides, most-cited first. */
  shared: number[];
  /** True while either side's membership is still being fetched — counts are NOT yet real. */
  pending: boolean;
}

const EMPTY: number[] = [];

function sideAuthorIds(side: CompareSide | null): number[] {
  return side?.kind === "author" ? side.ids : EMPTY;
}
function sideOrgKeys(side: CompareSide | null): string[] {
  return side?.kind === "org" ? side.keys : [];
}

export function useCompareMask(
  ds: Dataset | null,
  a: CompareSide | null,
  b: CompareSide | null,
): CompareMask | null {
  // Both sides' sources are the same ones the ordinary filters use, so a comparison needs no
  // new artifact: author-papers shards (D30) and org-node shards (D50).
  const authorIds = useMemo(
    () => [...sideAuthorIds(a), ...sideAuthorIds(b)],
    [a, b],
  );
  const orgKeys = useMemo(() => [...sideOrgKeys(a), ...sideOrgKeys(b)], [a, b]);
  const authorPapers = useAuthorPapers(ds, authorIds);
  const orgNodes = useOrgNodes(ds, orgKeys);

  return useMemo(() => {
    if (!ds || !a || !b) return null;
    const n = ds.points.count;
    const value = new Uint8Array(n);

    let pending = false;
    const paint = (side: CompareSide, bit: 1 | 2) => {
      if (side.kind === "author") {
        for (const id of side.ids) {
          const nodes = authorPapers.get(id);
          if (!nodes) { pending = true; continue; }
          for (const node of nodes) if (node >= 0 && node < n) value[node] |= bit;
        }
      } else {
        for (const key of side.keys) {
          const inst = ds.orgs.institutions[key];
          if (!inst) continue;
          // Inline for curated entries; from the shard for a directory institution, which is
          // empty until it lands — that is a pending state, not an empty organization.
          const nodes = inst.node_ids.length ? inst.node_ids : orgNodes.get(key);
          if (!nodes) { pending = true; continue; }
          for (const node of nodes) if (node >= 0 && node < n) value[node] |= bit;
        }
      }
    };
    // Paint into bitflags first (1 = A, 2 = B), then translate to the ordered codes.
    paint(a, 1);
    paint(b, 2);

    let ca = 0, cb = 0, cboth = 0;
    const shared: number[] = [];
    for (let i = 0; i < n; i++) {
      const bits = value[i];
      if (bits === 3) { value[i] = CMP_BOTH; cboth++; ca++; cb++; shared.push(i); }
      else if (bits === 1) { value[i] = CMP_A; ca++; }
      else if (bits === 2) { value[i] = CMP_B; cb++; }
    }
    const cited = ds.points.citedByCount;
    shared.sort((x, y) => cited[y] - cited[x]);

    return { value, counts: { a: ca, b: cb, both: cboth }, shared, pending };
  }, [ds, a, b, authorPapers, orgNodes]);
}
