// Papers written by the currently selected authors, from the inverted index.
//
// The author filter used to scan all 912,429 papers' author_ids, which required shipping those
// lists eagerly (18.2 MB of papers-index). s11 now emits author-papers-N.arrow — author_id ->
// node ids, ~0.6 MB per shard — so a filter fetches only the shard holding those authors.

import { useEffect, useState } from "react";
import type { Dataset } from "./types";
import { loadAuthorPapers } from "./loadArtifacts";

export function useAuthorPapers(ds: Dataset | null, authorIds: number[]): Map<number, number[]> {
  const [map, setMap] = useState<Map<number, number[]>>(new Map());
  const key = authorIds.join(",");

  useEffect(() => {
    const size = ds?.manifest.author_papers_shard_size ?? 0;
    if (!size || authorIds.length === 0) {
      setMap(new Map());
      return;
    }
    let live = true;
    Promise.all(authorIds.map((id) => loadAuthorPapers(size, id).catch(() => null)))
      .then((shards) => {
        if (!live) return;
        const out = new Map<number, number[]>();
        authorIds.forEach((id, i) => {
          const nodes = shards[i]?.get(id);
          if (nodes) out.set(id, nodes);
        });
        setMap(out);
      });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ds, key]);

  return map;
}
