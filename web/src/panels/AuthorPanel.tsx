// Author view: shown whenever the author filter is active and no paper is selected (clicking
// an author in DetailsPanel closes the paper panel and lands here — see
// DetailsPanel.addAuthorFilter). Surfaces external profile links per selected author.

import { ExternalLink, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";
import { useAuthorInfo } from "../data/useAuthorLookup";
import {
  ensureAuthorAffiliations, onAuthorAffiliations, peekAuthorAffiliations,
  peekAuthorOpenAlex,
} from "../data/loadArtifacts";
import { useAuthorPapers } from "../data/useAuthorPapers";
import { resolveAuthorIdentity } from "../data/authorIdentity";

function AuthorAffiliations({ authorId }: { authorId: number }) {
  const [, setTick] = useState(0);
  useEffect(() => onAuthorAffiliations(() => setTick((n) => n + 1)), []);
  useEffect(() => { void ensureAuthorAffiliations([authorId]); }, [authorId]);
  const rows = peekAuthorAffiliations(authorId);
  // null = not fetched yet, [] = fetched and this author has none attributed. Both render
  // nothing: an empty line would read as "no affiliation on record" when it means the papers
  // we hold could not be attributed — common for very recent work, since the upstream
  // extraction stops at Dec 2025 and 2026 attribution is ~6%.
  if (!rows || rows.length === 0) return null;
  return (
    <ul className="author-affiliations">
      {rows.map((r) => (
        <li key={r.label}>
          <span className="aff-name">{r.label}</span>
          <span className="aff-meta subtle">
            {r.count.toLocaleString()} paper{r.count === 1 ? "" : "s"}
            {r.from > 0 && ` · ${r.from}${r.to > r.from ? `–${r.to}` : ""}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

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
  // Records for the selected authors only (name, count, and the rows sharing the name).
  const authorInfo = useAuthorInfo(authorIds);
  const authors = [...authorInfo.values()];
  const setAuthors = useStore((s) => s.setAuthors);
  // Not for the paper lists — the filter already computes those — but because OpenAlex ids
  // live in these shards (D32). Awaiting them here re-renders once they land, which is what
  // makes the profile link appear instead of depending on some other render happening later.
  useAuthorPapers(ds, authorIds);

  const authorById = useMemo(() => new Map(authors.map((a) => [a.authorId, a])), [authors]);

  if (selectedNode !== null || authorIds.length === 0) return null;
  // Selecting an author selects every row sharing the name (see data/authorIdentity), so
  // collapse them back to one entry per person for display.
  const seenName = new Set<string>();
  const selected = authorIds
    .map((id) => authorById.get(id))
    .filter((a): a is NonNullable<typeof a> => a !== undefined)
    .filter((a) => (seenName.has(a.name) ? false : (seenName.add(a.name), true)));
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
        const verified = a.verified;
        // The id itself comes from the author-papers shard, which is already loaded whenever
        // this panel can be on screen (the filter that opened it fetched it).
        const openalexId = peekAuthorOpenAlex(a.authorId);
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
              {(() => {
                const idn = resolveAuthorIdentity(a.authorId, authors);
                const papers = idn?.papers ?? a.count;
                const profiles = idn?.profiles ?? 1;
                return (
                  <>
                    {papers.toLocaleString()} paper{papers === 1 ? "" : "s"} in this corpus
                    {/* Say plainly when several records were merged: for a distinctive name
                        that is one person split by OpenAlex, but a common name can merge
                        genuinely different people, and the reader should be able to tell. */}
                    {profiles > 1 && ` · merged from ${profiles} author records with this name`}
                  </>
                );
              })()}
              {!verified && " · identity not confirmed by OpenAlex, may include others sharing this name"}
            </div>
            {/* Where they publish from. Absent entirely when nothing is attributed: a blank
                line would read as "no affiliation on record" when it means the papers we
                have could not be attributed. Attribution is ~6% for 2026 work (the upstream
                extraction stops at Dec 2025), so this is common for very recent authors. */}
            <AuthorAffiliations authorId={a.authorId} />
            <div className="author-links">
              {verified && openalexId && (
                <a className="link" href={openAlexUrl(openalexId)} target="_blank" rel="noopener noreferrer">
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
