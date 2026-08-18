// Re-render when the deferred title index lands.
//
// papers-index.arrow is 57% of the eager payload (98.8 MB), which on a ~1 MB/s link is about a
// minute of blank screen, so it is fetched AFTER first paint and its rows are filled in place
// (loadArtifacts.placeholderPapers / fillPapersIndex). Nothing becomes async as a result — but
// components that render `title` do need a nudge once the real strings exist.

import { useEffect, useState } from "react";
import {
  areEdgesReady, arePapersReady, onEdgesReady, onPapersReady, onPointTiles, onRegionsReady,
} from "./loadArtifacts";

export function usePapersReady(): boolean {
  // Fires on EVERY title chunk, not just completion, so hover cards / list rows / search results
  // fill in as titles stream rather than staying blank until the last chunk.
  const [, setTick] = useState(0);
  useEffect(() => onPapersReady(() => setTick((n) => n + 1)), []);
  return arePapersReady();
}

/** Same idea for the citation graph, which is also fetched after first paint (24 MB gzipped —
 *  the largest single artifact on the wire once titles moved off the critical path). */
export function useEdgesReady(): boolean {
  const [ready, setReady] = useState(areEdgesReady);
  useEffect(() => onEdgesReady(() => setReady(true)), []);
  return ready;
}

/** Region cell tree (regions.arrow), needed before a label-region filter can resolve. */
export function useRegionsReady(): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => onRegionsReady(() => setReady(true)), []);
  return ready;
}

/** Bumps each time more point tiles land.
 *
 * Anything computed per-point over the WHOLE corpus — filter masks, region membership — is
 * incomplete until every tile has arrived, because unloaded points carry zeroed data. Depending
 * on this epoch makes those recomputations converge instead of freezing at whatever had loaded
 * when the filter was applied.
 */
export function usePointTilesEpoch(): number {
  const [epoch, setEpoch] = useState(0);
  useEffect(() => onPointTiles(() => setEpoch((n) => n + 1)), []);
  return epoch;
}
