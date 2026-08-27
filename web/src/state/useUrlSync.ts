// Keeps the address bar equal to the current view.
//
// Reads a shared link once on load, then rewrites the query string (replaceState, so the back
// button is not filled with every slider nudge) whenever the facets or the selection change.

import { useEffect, useRef } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "./store";
import { decodeUrlState, encodeUrlState } from "./urlState";

/** @param ds null while the dataset loads. The hook must still be CALLED on every render —
 *  placing it after App's `if (loading || !dataset) return` made it conditional and took the
 *  whole app down with a hooks-order violation (React #310), the same trap as D-hover-card. */
export function useUrlSync(ds: Dataset | null): void {
  const filters = useStore((s) => s.filters);
  const selectedNode = useStore((s) => s.selectedNode);
  const applyFilterPatch = useStore((s) => s.applyFilterPatch);
  const selectNode = useStore((s) => s.selectNode);
  const applied = useRef(false);

  // Corpus span, matching App's own derivation so an encoded "to" month means the same thing
  // in both places.
  const corpus = ds?.manifest.corpus;
  const fullMaxMonth = corpus
    ? (Number(corpus.date_to.slice(0, 4)) - Number(corpus.date_from.slice(0, 4))) * 12
      + ((Number(corpus.date_to.slice(5, 7)) || 12) - 1)
    : 0;

  useEffect(() => {
    if (!ds || applied.current) return;
    applied.current = true;
    const state = decodeUrlState(
      window.location.search,
      ds.manifest.corpus.date_from,
      ds.points.count,
    );
    // Selection FIRST, then facets. selectNode deliberately clears the org/author facets
    // (D63), so applying the link's filters before its paper threw them away: a link carrying
    // both ?org=anthropic and ?paper= opened with the paper selected and no org filter.
    if (state.selectedNode !== null) selectNode(state.selectedNode);
    if (Object.keys(state.filters).length > 0) applyFilterPatch(state.filters);
  }, [ds, applyFilterPatch, selectNode]);

  useEffect(() => {
    // Only after the incoming link has been read, or the first render would overwrite it with
    // the defaults it is about to replace.
    if (!ds || !applied.current) return;
    const query = encodeUrlState(filters, selectedNode, ds.manifest.corpus.date_from, fullMaxMonth);
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${query}${window.location.hash}`,
    );
  }, [filters, selectedNode, ds, fullMaxMonth]);
}
