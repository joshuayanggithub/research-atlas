// Related works for the selected paper: the fused text+citation kNN neighbors (s08).
// Clicking a related work selects and (via the map) re-centers on it.

import type { Dataset } from "../data/types";
import { useStore } from "../state/store";

export function RelatedWorksPanel({ ds, node }: { ds: Dataset; node: number }) {
  const selectNode = useStore((s) => s.selectNode);
  const setHover = useStore((s) => s.setHover);

  const ids = ds.neighbors.ids[node] ?? new Int32Array();
  const scores = ds.neighbors.scores[node] ?? new Float32Array();

  if (ids.length === 0) return <div className="related empty">No related works.</div>;

  return (
    <div className="related">
      <h4>Related works</h4>
      <ol>
        {Array.from(ids).map((nid, i) => {
          const p = ds.papers[nid];
          if (!p) return null;
          return (
            <li
              key={nid}
              onClick={() => selectNode(nid)}
              onMouseEnter={() => setHover(nid)}
              onMouseLeave={() => setHover(null)}
            >
              <span className="score">{scores[i]?.toFixed(2)}</span>
              <span className="rtitle">{p.title}</span>
              <span className="ryear">{p.publicationDate?.slice(0, 4)}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
