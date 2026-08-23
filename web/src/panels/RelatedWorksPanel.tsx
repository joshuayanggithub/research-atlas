// Related works for the selected paper: the fused text+citation kNN neighbors (s08).
// Neighbors load ON DEMAND (ds.getNeighbors) — sharded by node id — so the whole neighbor
// table is not in the initial download. Clicking a related work selects and re-centers on it.

import { useEffect, useState } from "react";
import type { Dataset, NeighborList } from "../data/types";
import { useStore } from "../state/store";
import { PaperTitle } from "./PaperTitle";
import { PaperYear } from "./PaperYear";

export function RelatedWorksPanel({ ds, node }: { ds: Dataset; node: number }) {
  const selectNode = useStore((s) => s.selectNode);
  const setHover = useStore((s) => s.setHover);
  const [neighbors, setNeighbors] = useState<NeighborList | null>(null);

  useEffect(() => {
    let live = true;
    setNeighbors(null);
    ds.getNeighbors(node).then((n) => {
      if (live) setNeighbors(n);
    });
    return () => {
      live = false;
    };
  }, [ds, node]);

  if (neighbors === null) {
    return <div className="related loading">Loading related works…</div>;
  }
  const { ids, scores } = neighbors;
  if (ids.length === 0) return <div className="related empty">No related works.</div>;

  return (
    <div className="related">
      <h4>Related works</h4>
      <ol>
        {Array.from(ids).map((nid, i) => {
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
                <span className="score">{scores[i]?.toFixed(2)}</span>
                <PaperTitle className="rtitle" title={p.title} />
                <span className="ryear"><PaperYear paper={p} /></span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
