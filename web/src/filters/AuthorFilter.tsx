// Author filter: type-ahead over the author index (authors.arrow). Selecting authors
// dims/hides non-matching papers (union across selected authors, AND with org/date).

import { useMemo, useState } from "react";
import { X } from "lucide-react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";

export function AuthorFilter({ ds }: { ds: Dataset }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const filters = useStore((s) => s.filters);
  const setAuthors = useStore((s) => s.setAuthors);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < 2) return [];
    const out = [];
    for (const a of ds.authors) {
      if (a.name.toLowerCase().includes(q)) {
        out.push(a);
        if (out.length >= 8) break; // authors are pre-sorted by paper count
      }
    }
    return out;
  }, [query, ds.authors]);

  const selectedNames = new Map(
    ds.authors.filter((a) => filters.authorIds.includes(a.authorId)).map((a) => [a.authorId, a.name]),
  );

  const choose = (authorId: number) => {
    if (!filters.authorIds.includes(authorId)) {
      setAuthors([...filters.authorIds, authorId]);
    }
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
