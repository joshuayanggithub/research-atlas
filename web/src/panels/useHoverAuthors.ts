// Author names for the paper under the cursor, fetched from its detail shard.
//
// Authors are NOT in the resident index, deliberately: measured, "Surname et al." for every
// paper costs +3.3 MB gzipped and two full names +13.9 MB even dictionary-encoded, because
// 779,838 distinct author pairs across a million papers leave almost nothing to deduplicate.
// That is a lot to add to the first file every visitor waits on, to serve a card that shows one
// paper at a time.
//
// The shard is a better trade: `getPaperDetail` already caches per 2,048-row block and is
// already fetched the moment anyone clicks a paper, so the first hover in a region costs one
// small request and its 2,047 neighbours are then free.
//
// Debounced because hovering crosses many points on the way to the one the user means; without
// it, dragging the cursor across a dense region queues a fetch per point.

import { useEffect, useState } from "react";
import type { Dataset } from "../data/types";

const HOVER_SETTLE_MS = 120;

export function useHoverAuthors(ds: Dataset, node: number | null): string[] | null {
  const [authors, setAuthors] = useState<{ node: number; names: string[] } | null>(null);

  useEffect(() => {
    if (node === null) return;
    let live = true;
    const timer = window.setTimeout(() => {
      void ds.getPaperDetail(node).then((detail) => {
        if (live && detail) setAuthors({ node, names: detail.authorNames });
      });
    }, HOVER_SETTLE_MS);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [ds, node]);

  // Only answer for the paper currently hovered: a late resolution for the previous one would
  // otherwise caption the new card with the old paper's authors.
  return authors && authors.node === node ? authors.names : null;
}
