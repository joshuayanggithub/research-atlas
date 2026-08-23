// Fetch the titles a view is about to render.
//
// Titles used to arrive as one 31.1 MB stream on every visit; they are now sharded by node and
// fetched for the papers actually on screen. Every view that renders a title must ask for it,
// or it will shimmer forever — which is why this hook exists rather than a call buried in each
// component.

import { useEffect } from "react";
import { ensureTitles } from "./loadArtifacts";
import { usePapersReady } from "./usePapersReady";

/** Ensures titles for `nodes` are fetched. Pass the rows ON SCREEN, not every candidate. */
export function useTitles(nodes: number[]): void {
  // usePapersReady re-renders the caller when a shard lands, so the strings appear.
  usePapersReady();
  const key = nodes.length ? `${nodes.length}:${nodes[0]}:${nodes[nodes.length - 1]}` : "";
  useEffect(() => {
    if (nodes.length) void ensureTitles(nodes);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}
