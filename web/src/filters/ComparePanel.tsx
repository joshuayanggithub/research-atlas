// Pick two sides to compare: an author or an organization on each.
//
// Reuses the existing search paths rather than inventing a third: authors come from the
// name-token index (D59) and organizations from the org directory, so nothing new is fetched
// to fill a slot.

import { useMemo, useState } from "react";
import { X } from "lucide-react";
import type { Dataset } from "../data/types";
import { useStore, type CompareSide } from "../state/store";
import { useAuthorSearch } from "../data/useAuthorLookup";
import { peekAuthorInfo } from "../data/loadArtifacts";
import { searchDirectory, buildOrgTree } from "./orgHierarchy";

function SlotPicker({ ds, slot }: { ds: Dataset; slot: "a" | "b" }) {
  const [query, setQuery] = useState("");
  const setCompareSide = useStore((s) => s.setCompareSide);
  const q = query.trim();
  const authors = useAuthorSearch(q, q.length >= 2);

  const orgs = useMemo(() => {
    if (q.length < 2) return [];
    const lower = q.toLowerCase();
    const curated = buildOrgTree(ds)
      .filter((n) => n.inst.display_name.toLowerCase().includes(lower))
      .map((n) => n);
    return [...curated, ...searchDirectory(ds, q, 6)].slice(0, 6);
  }, [ds, q]);

  const choose = (side: CompareSide) => { setCompareSide(slot, side); setQuery(""); };

  return (
    <div className="compare-slot">
      <input
        className="author-input"
        aria-label={`Search for comparison side ${slot.toUpperCase()}`}
        placeholder="Author or organization…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {q.length >= 2 && (authors.length > 0 || orgs.length > 0) && (
        <ul className="autocomplete" role="listbox">
          {orgs.slice(0, 3).map((o) => (
            <li key={`org-${o.key}`} role="option" aria-selected={false}>
              <button
                type="button"
                onClick={() => choose({ kind: "org", keys: [o.key], label: o.inst.display_name })}
              >
                <span>{o.inst.display_name}</span>
                <span className="count">Org · {o.inst.count.toLocaleString()}</span>
              </button>
            </li>
          ))}
          {authors.slice(0, 3).map((a) => (
            <li key={`author-${a.authorId}`} role="option" aria-selected={false}>
              <button
                type="button"
                onClick={() => {
                  // Select the whole identity group: one person is routinely several rows
                  // (D59), and comparing one row would undercount them.
                  const info = peekAuthorInfo(a.authorId);
                  choose({
                    kind: "author",
                    ids: info?.sameNameIds ?? [a.authorId],
                    label: a.name,
                  });
                }}
              >
                <span>{a.name}</span>
                <span className="count">Author · {a.count.toLocaleString()}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function CompareSetup({ ds }: { ds: Dataset }) {
  const compare = useStore((s) => s.compare);
  const setCompareSide = useStore((s) => s.setCompareSide);
  const clearCompare = useStore((s) => s.clearCompare);
  const active = compare.a !== null && compare.b !== null;

  return (
    <div className="filter-section">
      <div className="section-head">
        <h4>Compare</h4>
        {(compare.a || compare.b) && (
          <button type="button" className="text-btn" onClick={clearCompare}>Clear</button>
        )}
      </div>
      <p className="org-hint subtle">
        Two authors or organizations, side by side on the same map. Papers belonging to both
        appear in each pane.
      </p>
      {(["a", "b"] as const).map((slot) => {
        const side = compare[slot];
        return (
          <div className="compare-row" key={slot}>
            <span className={`compare-tag compare-tag-${slot}`}>{slot.toUpperCase()}</span>
            {side ? (
              <button
                type="button"
                className="chip active compare-chip"
                aria-label={`Remove ${side.label}`}
                onClick={() => setCompareSide(slot, null)}
              >
                <span className="org-name">{side.label}</span>
                <X size={11} aria-hidden="true" />
              </button>
            ) : (
              <SlotPicker ds={ds} slot={slot} />
            )}
          </div>
        );
      })}
      {!active && (compare.a || compare.b) && (
        <p className="org-hint subtle" style={{ margin: "6px 0 0" }}>
          Pick a second side to split the map.
        </p>
      )}
    </div>
  );
}
