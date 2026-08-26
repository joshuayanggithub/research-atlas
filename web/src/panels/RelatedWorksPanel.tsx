// Related works for the selected paper: the fused text+citation kNN neighbors (s08).
// Neighbors load ON DEMAND (ds.getNeighbors) — sharded by node id — so the whole neighbor
// table is not in the initial download. Clicking a related work selects and re-centers on it.
//
// This is the ONLY structural signal for a recent preprint. S2/OpenAlex have not indexed the
// references of a 2026 paper (Meshy T2, node 263422, is `references_available: false` with zero
// edges in either direction), so its citation panel is honestly but entirely empty. Semantic
// neighbours still exist for every paper in the corpus, which is why this section is worth
// keeping visible — and why it can be switched off for papers where the citation graph already
// answers the question, since its titles cost ~0.5 MB of shards per selection.

import { useEffect, useState } from "react";
import type { Dataset, NeighborList } from "../data/types";
import { useStore } from "../state/store";
import { PaperTitle } from "./PaperTitle";
import { useTitles } from "../data/useTitles";
import { PaperYear } from "./PaperYear";

/** Rows shown before "show all". Each row's title lives in its own 60 KB shard, so this is a
 *  byte budget as much as a layout choice. */
const RELATED_LIMIT = 8;

export function RelatedWorksPanel({ ds, node }: { ds: Dataset; node: number }) {
  const selectNode = useStore((s) => s.selectNode);
  const setHover = useStore((s) => s.setHover);
  const showRelated = useStore((s) => s.showRelated);
  const setShowRelated = useStore((s) => s.setShowRelated);
  const [neighbors, setNeighbors] = useState<NeighborList | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setExpanded(false);
  }, [node]);

  useEffect(() => {
    // Hidden means hidden all the way down: no neighbour fetch, no title shards.
    if (!showRelated) {
      setNeighbors(null);
      return;
    }
    let live = true;
    setNeighbors(null);
    ds.getNeighbors(node).then((n) => {
      if (live) setNeighbors(n);
    });
    return () => {
      live = false;
    };
  }, [ds, node, showRelated]);

  const ids = neighbors ? Array.from(neighbors.ids) : [];
  const shown = ids.slice(0, expanded ? ids.length : RELATED_LIMIT);
  // Titles are per-node; fetch exactly the rows drawn, so no row is rendered that can never
  // resolve. MUST be above the early returns below — this component returns before rendering
  // while neighbours load, and a hook called on only some renders is a hooks-order violation
  // (React #310).
  useTitles(showRelated ? shown : []);

  const header = (
    <div className="related-head">
      <h4>Related works</h4>
      <button
        type="button"
        className="related-toggle"
        onClick={() => setShowRelated(!showRelated)}
        aria-pressed={showRelated}
        title={
          showRelated
            ? "Hide related works (skips downloading them)"
            : "Show semantically nearest papers"
        }
      >
        {showRelated ? "Hide" : "Show"}
      </button>
    </div>
  );

  if (!showRelated) {
    return (
      <div className="related">
        {header}
        <div className="related empty subtle">
          Hidden. Nearest papers by embedding — useful when a paper has no citation data yet.
        </div>
      </div>
    );
  }

  if (neighbors === null) {
    return (
      <div className="related">
        {header}
        <div className="related loading">Loading related works…</div>
      </div>
    );
  }
  if (ids.length === 0) {
    return (
      <div className="related">
        {header}
        <div className="related empty">No related works.</div>
      </div>
    );
  }

  return (
    <div className="related">
      {header}
      <ol>
        {shown.map((nid, i) => {
          const p = ds.papers[nid];
          if (!p) return null;
          return (
            <li key={nid}>
              <button
                type="button"
                onClick={() => selectNode(nid)}
                onMouseEnter={() => setHover(nid)}
                onMouseLeave={() => setHover(null)}
              >
                <span className="score">{neighbors.scores[i]?.toFixed(2)}</span>
                <PaperTitle className="rtitle" title={p.title} node={nid} />
                <span className="ryear"><PaperYear paper={p} /></span>
              </button>
            </li>
          );
        })}
      </ol>
      {ids.length > RELATED_LIMIT && (
        <button
          type="button"
          className="citation-expand"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Show fewer" : `Show all ${ids.length}`}
        </button>
      )}
    </div>
  );
}
