// Paper results for the search box, from the title token index.
//
// Replaces a scan over every resident title. That scan had a correctness problem, not just a
// cost one: titles stream in as chunks, so results depended on how much had downloaded.
// Measured during D53 verification — searching the exact string "Attention Is All You Need"
// early in a session returned "Element-wise Attention Is All You Need" and "Attention is All
// You Need Until You Need Retention", but never the paper itself, because the chunk holding
// its title had not arrived yet. The box looked like it was ranking badly; it was answering
// from a fraction of the corpus.
//
// The index covers ALL 1,000,490 papers from the first query, and a query costs one or two
// ~115 KB chunk fetches instead of 31 MB of titles.

import { useEffect, useState } from "react";
import { searchTitles } from "./loadArtifacts";

export interface TitleSearch {
  nodes: number[];
  /** True while the index chunks for this query are still in flight. */
  pending: boolean;
}

export function useTitleSearch(query: string, enabled: boolean): TitleSearch {
  const [state, setState] = useState<TitleSearch>({ nodes: [], pending: false });

  useEffect(() => {
    if (!enabled || query.trim().length === 0) {
      setState({ nodes: [], pending: false });
      return;
    }
    let live = true;
    setState((prev) => ({ nodes: prev.nodes, pending: true }));
    // Debounced: a chunk fetch per keystroke would be a request per character on a slow link.
    const timer = window.setTimeout(() => {
      void searchTitles(query)
        .then((nodes) => { if (live) setState({ nodes, pending: false }); })
        .catch(() => { if (live) setState({ nodes: [], pending: false }); });
    }, 160);
    return () => { live = false; window.clearTimeout(timer); };
  }, [query, enabled]);

  return state;
}
