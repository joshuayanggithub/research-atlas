// Metadata and graph context for the selected paper.

import { ExternalLink, FileText, Network, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Dataset, PaperDetail } from "../data/types";
import { useStore } from "../state/store";
import { ArxivPreview } from "./ArxivPreview";
import { CitationExplorer } from "./CitationExplorer";
import { RelatedWorksPanel } from "./RelatedWorksPanel";

export function DetailsPanel({ ds }: { ds: Dataset }) {
  const selectedNode = useStore((s) => s.selectedNode);
  const selectNode = useStore((s) => s.selectNode);
  const [view, setView] = useState<"citations" | "paper">("citations");
  const [detail, setDetail] = useState<PaperDetail | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => setView("citations"), [selectedNode]);

  // Fetch the selected paper's full detail (author names, venue, ids, full date) on demand.
  useEffect(() => {
    if (selectedNode === null) return;
    let live = true;
    setDetail(null);
    ds.getPaperDetail(selectedNode).then((d) => {
      if (live) setDetail(d);
    });
    return () => {
      live = false;
    };
  }, [ds, selectedNode]);

  // Move keyboard focus into the panel when a paper is selected so screen-reader and
  // keyboard users land on the new content (the panel is last in the DOM). Escape closes.
  useEffect(() => {
    if (selectedNode !== null) headingRef.current?.focus();
  }, [selectedNode]);

  if (selectedNode === null) return null;
  const p = ds.papers[selectedNode];
  if (!p) return null;

  // Resident index has title/year/citations; author names, venue, links come from detail.
  const authorNames = detail?.authorNames ?? [];
  const dateText = detail?.publicationDate || p.publicationDate || "—";
  const link = detail?.doi
    ? `https://doi.org/${detail.doi}`
    : detail?.arxivId
      ? `https://arxiv.org/abs/${detail.arxivId}`
      : null;

  return (
    <div
      className="panel details"
      role="dialog"
      aria-label="Paper details"
      onKeyDown={(e) => {
        if (e.key === "Escape") selectNode(null);
      }}
    >
      <button
        type="button"
        className="close"
        aria-label="Close paper details"
        title="Close paper details"
        onClick={() => selectNode(null)}
      >
        <X size={18} aria-hidden="true" />
      </button>
      <h3 ref={headingRef} tabIndex={-1}>{p.title}</h3>
      <div className="meta">
        {authorNames.length > 0
          ? authorNames.slice(0, 6).join(", ") +
            (authorNames.length > 6 ? ` +${authorNames.length - 6}` : "")
          : detail === null
            ? "Loading authors…"
            : "—"}
      </div>
      <div className="meta subtle">
        {dateText} · {detail?.venue ?? "—"} · {p.citedByCount.toLocaleString()} citations
      </div>
      {link && (
        <a className="link" href={link} target="_blank" rel="noreferrer">
          Open paper <ExternalLink size={13} aria-hidden="true" />
        </a>
      )}

      <div
        className="seg details-tabs"
        role="tablist"
        aria-label="Paper detail view"
        onKeyDown={(e) => {
          // Arrow keys move between tabs (standard tabs pattern).
          if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            e.preventDefault();
            setView((v) => (v === "citations" ? "paper" : "citations"));
          }
        }}
      >
        <button
          type="button"
          role="tab"
          id="tab-citations"
          aria-controls="panel-citations"
          tabIndex={view === "citations" ? 0 : -1}
          className={view === "citations" ? "active" : ""}
          aria-selected={view === "citations"}
          onClick={() => setView("citations")}
        >
          <Network size={14} aria-hidden="true" />
          Citations
        </button>
        <button
          type="button"
          role="tab"
          id="tab-paper"
          aria-controls="panel-paper"
          tabIndex={view === "paper" ? 0 : -1}
          className={view === "paper" ? "active" : ""}
          aria-selected={view === "paper"}
          onClick={() => setView("paper")}
        >
          <FileText size={14} aria-hidden="true" />
          Paper
        </button>
      </div>

      {view === "paper" ? (
        <div role="tabpanel" id="panel-paper" aria-labelledby="tab-paper">
          <ArxivPreview arxivId={detail?.arxivId ?? null} doi={detail?.doi ?? null} title={p.title} />
        </div>
      ) : (
        <div role="tabpanel" id="panel-citations" aria-labelledby="tab-citations">
          <CitationExplorer ds={ds} node={selectedNode} />
          <RelatedWorksPanel ds={ds} node={selectedNode} />
        </div>
      )}
    </div>
  );
}
