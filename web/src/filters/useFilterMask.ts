// Turns the active filters (year range, orgs, authors) into per-point data used by the
// GPU. We expose:
//   - orgOfNode:  Int32Array node -> first matching org index (for color-by-org), or -1
//   - filterValue: Float32Array node -> 1 if the point passes org+author filters, else 0
// Year filtering is done directly on the GPU via DataFilterExtension's year channel, so
// it is NOT baked into filterValue (keeps year-slider dragging free of CPU recompute).
//
// The mask is memoized on the filter inputs so dragging the date slider never recomputes
// the (heavier) org/author mask.

import { useMemo } from "react";
import type { Dataset } from "../data/types";
import type { Filters } from "../state/store";

export interface FilterArrays {
  orgOfNode: Int32Array; // node -> org index (order of orgKeysAll), or -1
  orgKeysAll: string[]; // stable org order for coloring
  matchValue: Float32Array; // 1 = passes org+author filter, 0 = filtered out
  anyOrgAuthorActive: boolean;
}

// Precompute node -> org index once per dataset (independent of selection).
function buildOrgOfNode(ds: Dataset): { orgOfNode: Int32Array; orgKeysAll: string[] } {
  const orgKeysAll = Object.keys(ds.orgs.institutions);
  const orgOfNode = new Int32Array(ds.points.count).fill(-1);
  orgKeysAll.forEach((key, idx) => {
    for (const nid of ds.orgs.institutions[key].node_ids) {
      if (orgOfNode[nid] === -1) orgOfNode[nid] = idx; // first org wins for coloring
    }
  });
  return { orgOfNode, orgKeysAll };
}

export function useOrgIndex(ds: Dataset | null) {
  return useMemo(() => (ds ? buildOrgOfNode(ds) : null), [ds]);
}

export function useFilterMask(
  ds: Dataset | null,
  filters: Filters,
): FilterArrays | null {
  const orgIndex = useOrgIndex(ds);

  return useMemo(() => {
    if (!ds || !orgIndex) return null;
    const n = ds.points.count;
    const { orgOfNode, orgKeysAll } = orgIndex;

    const orgActive = filters.orgKeys.length > 0;
    const authorActive = filters.authorIds.length > 0;
    const matchValue = new Float32Array(n).fill(1);

    if (orgActive || authorActive) {
      // Build the set of nodes that match orgs (union) and authors (union); a node
      // passes if it matches every ACTIVE facet (org AND author), matching the store's
      // AND-across-facets semantics.
      let orgMask: Uint8Array | null = null;
      if (orgActive) {
        orgMask = new Uint8Array(n);
        for (const key of filters.orgKeys) {
          const inst = ds.orgs.institutions[key];
          if (!inst) continue;
          for (const nid of inst.node_ids) orgMask[nid] = 1;
        }
      }
      let authorMask: Uint8Array | null = null;
      if (authorActive) {
        authorMask = new Uint8Array(n);
        const wanted = new Set(filters.authorIds);
        // Resolve authors -> nodes by scanning papers (fast at MVP scale).
        for (let i = 0; i < n; i++) {
          const authors = ds.papers[i]?.authorIds;
          if (!authors) continue;
          for (const a of authors) {
            if (wanted.has(a)) {
              authorMask[i] = 1;
              break;
            }
          }
        }
      }

      for (let i = 0; i < n; i++) {
        const passOrg = !orgMask || orgMask[i] === 1;
        const passAuthor = !authorMask || authorMask[i] === 1;
        matchValue[i] = passOrg && passAuthor ? 1 : 0;
      }
    }

    return {
      orgOfNode,
      orgKeysAll,
      matchValue,
      anyOrgAuthorActive: orgActive || authorActive,
    };
  }, [ds, orgIndex, filters.orgKeys, filters.authorIds]);
}
