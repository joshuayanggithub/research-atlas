// Title search: type-ahead over paper titles; selecting a result selects that node.

import { useMemo, useState } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";

export function SearchBox({ ds }: { ds: Dataset }) {
  const [query, setQuery] = useState("");
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

  return (
    <div className="search-box">
      <input
        placeholder="Search papers by title…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {matches.length > 0 && (
        <ul className="autocomplete">
          {matches.map((m) => (
            <li
              key={m.nodeId}
              onClick={() => {
                selectNode(m.nodeId);
                setQuery("");
              }}
            >
              {m.title}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
