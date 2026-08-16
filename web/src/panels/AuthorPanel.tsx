// Author view: shown whenever the author filter is active and no paper is selected (clicking
// an author in DetailsPanel closes the paper panel and lands here — see
// DetailsPanel.addAuthorFilter). Surfaces external profile links per selected author.

import { ExternalLink, X } from "lucide-react";
import { useMemo } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";

function openAlexUrl(openalexId: string): string {
  return `https://openalex.org/${openalexId}`;
}
function scholarUrl(name: string): string {
  return `https://scholar.google.com/scholar?q=${encodeURIComponent(`author:"${name}"`)}`;
}
function semanticScholarUrl(name: string): string {
  return `https://www.semanticscholar.org/search?q=${encodeURIComponent(name)}&sort=relevance`;
}

export function AuthorPanel({ ds }: { ds: Dataset }) {
  const selectedNode = useStore((s) => s.selectedNode);
  const authorIds = useStore((s) => s.filters.authorIds);
  const setAuthors = useStore((s) => s.setAuthors);

  const authorById = useMemo(() => new Map(ds.authors.map((a) => [a.authorId, a])), [ds.authors]);

  if (selectedNode !== null || authorIds.length === 0) return null;
  const selected = authorIds.map((id) => authorById.get(id)).filter((a) => a !== undefined);
  if (selected.length === 0) return null;

  return (
    <div
      className="panel author-view"
      role="region"
      aria-label="Selected authors"
      onKeyDown={(e) => {
        if (e.key === "Escape") setAuthors([]);
      }}
    >
      <button
        type="button"
        className="close"
        aria-label="Clear author selection"
        title="Clear author selection"
        onClick={() => setAuthors([])}
      >
        <X size={18} aria-hidden="true" />
      </button>
      <h3>{selected.length === 1 ? "Author" : `${selected.length} authors`}</h3>
      {selected.map((a) => {
        const verified = !a.openalexId.startsWith("arxiv-name:");
        return (
          <div className="author-entry" key={a.authorId}>
            <div className="author-entry-head">
              <strong>{a.name}</strong>
              <button
                type="button"
                className="text-btn"
                aria-label={`Remove ${a.name} from author filter`}
                onClick={() => setAuthors(authorIds.filter((id) => id !== a.authorId))}
              >
                Remove
              </button>
            </div>
            <div className="meta subtle">
              {a.count.toLocaleString()} paper{a.count === 1 ? "" : "s"} in this corpus
              {!verified && " · identity not confirmed by OpenAlex, may include others sharing this name"}
            </div>
            <div className="author-links">
              {verified && (
                <a className="link" href={openAlexUrl(a.openalexId)} target="_blank" rel="noopener noreferrer">
                  OpenAlex <ExternalLink size={12} aria-hidden="true" />
                </a>
              )}
              <a className="link" href={scholarUrl(a.name)} target="_blank" rel="noopener noreferrer">
                Google Scholar <ExternalLink size={12} aria-hidden="true" />
              </a>
              <a className="link" href={semanticScholarUrl(a.name)} target="_blank" rel="noopener noreferrer">
                Semantic Scholar <ExternalLink size={12} aria-hidden="true" />
              </a>
            </div>
          </div>
        );
      })}
    </div>
  );
}
