// Author filter: type-ahead over the author index (authors.arrow). Selecting authors
// dims/hides non-matching papers (union across selected authors, AND with org/date).

import { useMemo, useState } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";

export function AuthorFilter({ ds }: { ds: Dataset }) {
  const [query, setQuery] = useState("");
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

  return (
    <div className="filter-section">
      <h4>Authors</h4>
      <input
        className="author-input"
        placeholder="Search author…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {matches.length > 0 && (
        <ul className="autocomplete">
          {matches.map((a) => (
            <li
              key={a.authorId}
              onClick={() => {
                if (!filters.authorIds.includes(a.authorId)) {
                  setAuthors([...filters.authorIds, a.authorId]);
                }
                setQuery("");
              }}
            >
              {a.name} <span className="count">{a.count}</span>
            </li>
          ))}
        </ul>
      )}
      {selectedNames.size > 0 && (
        <div className="chips">
          {Array.from(selectedNames).map(([id, name]) => (
            <button
              key={id}
              className="chip active"
              onClick={() => setAuthors(filters.authorIds.filter((x) => x !== id))}
            >
              {name} ×
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
