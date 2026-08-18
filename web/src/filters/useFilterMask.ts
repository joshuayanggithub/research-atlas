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
import { useStore } from "../state/store";
import { usePapersReady, usePointTilesEpoch, useRegionsReady } from "../data/usePapersReady";
import { useAuthorPapers } from "../data/useAuthorPapers";
import { nodesInLabels } from "./labelMembership";
import type { Dataset } from "../data/types";
import type { Filters } from "../state/store";

export interface FilterArrays {
  orgOfNode: Int32Array; // node -> org index (order of orgKeysAll), or -1
  orgKeysAll: string[]; // stable org order for coloring
  matchValue: Float32Array; // 1 = passes org+author filter, 0 = filtered out
  anyOrgAuthorActive: boolean;
}

// Precompute node -> ROOT-org index once per dataset (independent of selection). Only root
// orgs get a color hue; sub-units inherit their parent's color, so color-by-org stays
// readable regardless of how deep the user drills.
function buildOrgOfNode(ds: Dataset): { orgOfNode: Int32Array; orgKeysAll: string[] } {
  // Color-by-org uses only the curated seed roots — the 2k+ directory institutions are
  // filter-only and would exhaust the hue palette / overlap heavily.
  const orgKeysAll = Object.keys(ds.orgs.institutions).filter(
    (k) => ds.orgs.institutions[k].parent === null && ds.orgs.institutions[k].curated,
  );
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
  // author_ids live in the DEFERRED papers-index (D23), so the author mask must be rebuilt
  // when they land — otherwise selecting an author matches nothing until then.
  const papersReady = usePapersReady();
  // regions.arrow streams in after first paint; rebuild once it lands so a label filter resolves.
  const regionsReady = useRegionsReady();
  // Region membership reads points.regionLeaf, which is -1 until a point's tile arrives.
  const tilesEpoch = usePointTilesEpoch();
  // author_id -> node ids for the SELECTED authors, from the inverted index.
  const authorPapers = useAuthorPapers(ds, filters.authorIds);
  // The imported library lives outside `filters` (it is data, not a selection), so the mask
  // has to subscribe to it directly.
  const readingList = useStore((s) => s.readingList);

  return useMemo(() => {
    if (!ds || !orgIndex) return null;
    const n = ds.points.count;
    const { orgOfNode, orgKeysAll } = orgIndex;

    const orgActive = filters.orgKeys.length > 0;
    const authorActive = filters.authorIds.length > 0;
    const subfieldActive = filters.subfieldIds.length > 0;
    const topicActive = filters.topicIds.length > 0;
    const citeActive = filters.citeMin > 0 || filters.citeMax !== null;
    const labelActive = filters.labelIds.length > 0;
    const readingActive = filters.readingLists.length > 0;
    const matchValue = new Float32Array(n).fill(1);

    if (orgActive || authorActive || subfieldActive || topicActive || citeActive
        || labelActive || readingActive) {
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
        // Direct lookup via the inverted index — no 912k scan, and no author_ids in the eager
        // bundle. Empty until the author's shard lands, which useAuthorPapers signals.
        for (const a of filters.authorIds) {
          for (const node of authorPapers.get(a) ?? []) {
            if (node >= 0 && node < n) authorMask[node] = 1;
          }
        }
      }

      // CS-topic facet: a node passes if its subfield ∈ selected subfields (when any) AND its
      // topic ∈ selected topics (when any). Read straight off the point columns — no scan.
      const wantSub = subfieldActive ? new Set(filters.subfieldIds) : null;
      const wantTopic = topicActive ? new Set(filters.topicIds) : null;
      const { subfieldId, topicId, citedByCount } = ds.points;
      // Citation facet: read straight off the resident points column, no scan. An unbounded
      // upper end is Infinity so the comparison stays a plain numeric test.
      const citeMin = filters.citeMin;
      const citeMax = filters.citeMax ?? Infinity;
      // Region membership from the label centroids — same rule the map uses to decide which
      // labels are relevant, so a clicked label selects exactly what it visually claims.
      const labelMask = labelActive ? nodesInLabels(ds, filters.labelIds) : null;

      // Reading list: the node ids were resolved at import time (data/readingList), so this
      // facet is a plain set membership test with nothing to look up.
      let readingMask: Uint8Array | null = null;
      if (readingActive && readingList) {
        readingMask = new Uint8Array(n);
        for (const name of filters.readingLists) {
          for (const node of readingList.nodesByList[name] ?? []) {
            if (node >= 0 && node < n) readingMask[node] = 1;
          }
        }
      }

      for (let i = 0; i < n; i++) {
        const passOrg = !orgMask || orgMask[i] === 1;
        const passAuthor = !authorMask || authorMask[i] === 1;
        const passSub = !wantSub || wantSub.has(subfieldId[i]);
        const passTopic = !wantTopic || wantTopic.has(topicId[i]);
        const passCite = !citeActive || (citedByCount[i] >= citeMin && citedByCount[i] <= citeMax);
        const passLabel = !labelMask || labelMask[i] === 1;
        const passReading = !readingMask || readingMask[i] === 1;
        matchValue[i] =
          passOrg && passAuthor && passSub && passTopic && passCite && passLabel && passReading
            ? 1
            : 0;
      }
    }

    return {
      orgOfNode,
      orgKeysAll,
      matchValue,
      anyOrgAuthorActive:
        orgActive || authorActive || subfieldActive || topicActive || citeActive || labelActive
        || readingActive,
    };
  }, [ds, orgIndex, filters.orgKeys, filters.authorIds, filters.subfieldIds, filters.topicIds,
      filters.citeMin, filters.citeMax, filters.labelIds, filters.readingLists, readingList,
      regionsReady, tilesEpoch, authorPapers,
      // author_ids live in the DEFERRED papers-index (D23). Without this the author mask is
      // computed against empty arrays and silently matches nothing until titles land.
      papersReady]);
}
