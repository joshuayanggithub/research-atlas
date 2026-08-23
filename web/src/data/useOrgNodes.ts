// Node ids for the currently selected organizations.
//
// Curated entries (the 43-entry browse tree) carry their ids inline in orgs.json, because
// color-by-org has to map every point to a root org before the user selects anything. The
// 10,475 directory institutions do not: their 1,370,907 ids were 94% of a file every visit
// downloads before first paint, to answer a question most visits never ask. They now arrive
// from org-nodes-{N}.arrow when an org is actually picked.
//
// Same shape as useAuthorPapers, deliberately — a selection that is still resolving yields an
// empty list, and the mask rebuilds when it lands.

import { useEffect, useState } from "react";
import type { Dataset } from "./types";
import { loadOrgNodes } from "./loadArtifacts";

export function useOrgNodes(ds: Dataset | null, orgKeys: string[]): Map<string, number[]> {
  const [map, setMap] = useState<Map<string, number[]>>(new Map());
  const key = orgKeys.join(",");

  useEffect(() => {
    if (!ds || orgKeys.length === 0) {
      setMap(new Map());
      return;
    }
    // Only the entries whose ids are NOT inline need fetching.
    const wanted = orgKeys.filter((k) => {
      const inst = ds.orgs.institutions[k];
      return inst && !inst.node_ids.length && inst.node_shard != null;
    });
    if (wanted.length === 0) {
      setMap(new Map());
      return;
    }
    let live = true;
    const shards = [...new Set(wanted.map((k) => ds.orgs.institutions[k].node_shard as number))];
    Promise.all(shards.map((s) => loadOrgNodes(s).catch(() => null))).then((tables) => {
      if (!live) return;
      const out = new Map<string, number[]>();
      for (const t of tables) {
        if (!t) continue;
        for (const k of wanted) {
          const nodes = t.get(k);
          if (nodes) out.set(k, nodes);
        }
      }
      setMap(out);
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ds, key]);

  return map;
}
