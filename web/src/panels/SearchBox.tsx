// Title search: type-ahead over paper titles; selecting a result selects that node.

import { useMemo, useState } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";

export function SearchBox({ ds }: { ds: Dataset }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const selectNode = useStore((s) => s.selectNode);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < 3) return [];
    const out: { nodeId: number; title: string }[] = [];
    for (let i = 0; i < ds.papers.length; i++) {
      if (ds.papers[i].title.toLowerCase().includes(q)) {
        out.push({ nodeId: i, title: ds.papers[i].title });
        if (out.length >= 10) break;
      }
    }
    return out;
  }, [query, ds.papers]);

  const choose = (nodeId: number) => {
    selectNode(nodeId);
    setQuery("");
    setActiveIndex(0);
  };

  return (
    <div className="search-box">
      <input
        aria-label="Search papers by title"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={matches.length > 0}
        aria-controls="paper-search-results"
        aria-activedescendant={
          matches[activeIndex] ? `paper-search-option-${matches[activeIndex].nodeId}` : undefined
        }
        placeholder="Search papers by title..."
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
            choose(matches[activeIndex].nodeId);
          } else if (event.key === "Escape") {
            setQuery("");
          }
        }}
      />
      {matches.length > 0 && (
        <ul id="paper-search-results" className="autocomplete" role="listbox">
          {matches.map((m, index) => (
            <li
              id={`paper-search-option-${m.nodeId}`}
              key={m.nodeId}
              role="option"
              aria-selected={index === activeIndex}
            >
              <button
                type="button"
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => choose(m.nodeId)}
              >
                {m.title}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
