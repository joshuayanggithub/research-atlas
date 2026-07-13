// Metadata card for the selected paper + citation-edge controls.

import type { Dataset } from "../data/types";
import { useStore } from "../state/store";
import { RelatedWorksPanel } from "./RelatedWorksPanel";

export function DetailsPanel({ ds }: { ds: Dataset }) {
  const selectedNode = useStore((s) => s.selectedNode);
  const selectNode = useStore((s) => s.selectNode);
  const edgeMode = useStore((s) => s.edgeMode);
  const setEdgeMode = useStore((s) => s.setEdgeMode);

  if (selectedNode === null) return null;
  const p = ds.papers[selectedNode];
  if (!p) return null;

  const link = p.doi
    ? `https://doi.org/${p.doi}`
    : p.arxivId
      ? `https://arxiv.org/abs/${p.arxivId}`
      : null;

  const nOut = ds.citesOut.get(selectedNode)?.length ?? 0;
  const nIn = ds.citedBy.get(selectedNode)?.length ?? 0;

  return (
    <div className="panel details">
      <button className="close" onClick={() => selectNode(null)}>
        ×
      </button>
      <h3>{p.title}</h3>
      <div className="meta">
        {p.authorNames.slice(0, 6).join(", ")}
        {p.authorNames.length > 6 ? ` +${p.authorNames.length - 6}` : ""}
      </div>
      <div className="meta subtle">
        {p.publicationDate} · {p.venue ?? "—"} · {p.citedByCount.toLocaleString()} citations
      </div>
      {link && (
        <a className="link" href={link} target="_blank" rel="noreferrer">
          Open paper ↗
        </a>
      )}

      <div className="edge-controls">
        <span className="subtle">Citations in corpus:</span>
        <div className="seg">
          {(["out", "in", "both"] as const).map((m) => (
            <button
              key={m}
              className={edgeMode === m ? "active" : ""}
              onClick={() => setEdgeMode(m)}
            >
              {m === "out" ? `cites (${nOut})` : m === "in" ? `cited by (${nIn})` : "both"}
            </button>
          ))}
        </div>
      </div>

      <RelatedWorksPanel ds={ds} node={selectedNode} />
    </div>
  );
}
