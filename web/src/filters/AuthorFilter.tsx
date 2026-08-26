// Author filter: type-ahead over the author name-token index. Selecting authors dims/hides
// non-matching papers (union across selected authors, AND with org/date).
//
// The whole author list is no longer downloaded to do this (D59): a query fetches the index
// chunk holding its tokens, and the postings carry the name and paper count the dropdown
// shows, so nothing else has to be resolved to render a result.

import { useMemo, useState } from "react";
import { X } from "lucide-react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";
import { useAuthorInfo, useAuthorSearch } from "../data/useAuthorLookup";
import { peekAuthorInfo } from "../data/loadArtifacts";

export function AuthorFilter({ ds: _ds }: { ds: Dataset }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const filters = useStore((s) => s.filters);
  const setAuthors = useStore((s) => s.setAuthors);

  // No focus gate any more: the index is fetched per query (one ~127 KB chunk), not as a
  // 14.4 MB whole-list load that had to be deferred until someone clearly wanted it.
  const found = useAuthorSearch(query.trim(), query.trim().length >= 2);
  const matches = useMemo(
    // Postings are emitted most-prolific-first; ranking a name-prefix hit above a mid-name one
    // keeps "hinton" from being crowded out by someone whose forename merely starts the same.
    () => [...found]
      .sort((a, b) => {
        const q = query.trim().toLowerCase();
        const rank = (n: string) => (n.toLowerCase().startsWith(q) ? 2
          : new RegExp(`\\b${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(n.toLowerCase()) ? 1 : 0);
        return rank(b.name) - rank(a.name) || b.count - a.count;
      })
      .slice(0, 8),
    [found, query],
  );

  // Names for the chips of already-selected authors, from their own records.
  const info = useAuthorInfo(filters.authorIds);
  const selectedNames = new Map([...info].map(([id, a]) => [id, a.name]));

  const choose = (authorId: number) => {
    // Select the whole identity, not the single row that happened to be clicked. OpenAlex
    // splits one person across several author records (210,084 names occupy more than one),
    // so picking one row shows a fraction of their work — the same "only shows one paper"
    // shape the search box already avoids via addAuthorToSelection.
    const info = peekAuthorInfo(authorId);
    const ids = info?.sameNameIds ?? [authorId];
    const next = new Set(filters.authorIds);
    for (const id of ids) next.add(id);
    setAuthors([...next]);
    setQuery("");
    setActiveIndex(0);
  };

  return (
    <div className="filter-section">
      <h4>Authors</h4>
      <div className="autocomplete-field">
        <input
          className="author-input"
          aria-label="Search authors"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={matches.length > 0}
          aria-controls="author-search-results"
          aria-activedescendant={
            matches[activeIndex]
              ? `author-search-option-${matches[activeIndex].authorId}`
              : undefined
          }
          placeholder="Search author..."
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActiveIndex(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" && matches.length) {
              event.preventDefault();
              setActiveIndex((index) => (index + 1) % matches.length);
            } else if (event.key === "ArrowUp" && matches.length) {
              event.preventDefault();
              setActiveIndex((index) => (index - 1 + matches.length) % matches.length);
            } else if (event.key === "Enter" && matches[activeIndex]) {
              event.preventDefault();
              choose(matches[activeIndex].authorId);
            } else if (event.key === "Escape") {
              setQuery("");
            }
          }}
        />
        {matches.length > 0 && (
          <ul id="author-search-results" className="autocomplete" role="listbox">
            {matches.map((a, index) => (
              <li
                id={`author-search-option-${a.authorId}`}
                key={a.authorId}
                role="option"
                aria-selected={index === activeIndex}
              >
                <button
                  type="button"
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => choose(a.authorId)}
                >
                  <span>{a.name}</span>
                  <span className="count">{a.count}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {selectedNames.size > 0 && (
        <div className="chips">
          {Array.from(selectedNames).map(([id, name]) => (
            <button
              type="button"
              key={id}
              className="chip active"
              aria-label={`Remove author ${name}`}
              onClick={() => setAuthors(filters.authorIds.filter((x) => x !== id))}
            >
              {name} <X size={12} aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
