// Combined type-ahead over paper titles and semantic map labels. Paper choices open the
// details sheet; label choices pan/zoom to the named map region.

import { useMemo, useState } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";

type SearchMatch =
  | { kind: "label"; key: string; labelId: number; text: string; count: number }
  | { kind: "paper"; key: string; nodeId: number; text: string };

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// A short acronym like "rio" is both a substring of unrelated words ("priors", "scenario")
// AND a word-boundary PREFIX of other, longer acronyms that share the same title-opening
// letters ("RIOT", "RiOSWorld"). Rank an exact whole-word match ("RIO:", boundary on both
// sides) above a same-prefix-longer-word match, above a buried-in-a-word substring, so the
// literal acronym isn't crowded out by near-miss titles that merely start the same way.
function titleMatchRank(lowerTitle: string, q: string): { rank: number; index: number } {
  const whole = new RegExp(`\\b${escapeRegExp(q)}\\b`).exec(lowerTitle);
  if (whole) return { rank: 2, index: whole.index };
  const prefixed = new RegExp(`\\b${escapeRegExp(q)}`).exec(lowerTitle);
  if (prefixed) return { rank: 1, index: prefixed.index };
  return { rank: 0, index: lowerTitle.indexOf(q) };
}

export function SearchBox({ ds }: { ds: Dataset }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const selectNode = useStore((s) => s.selectNode);
  const focusLabel = useStore((s) => s.focusLabel);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < 3) return [];
    const out: SearchMatch[] = [];

    // Repeated text is common across hierarchy levels. Keep its highest-priority instance
    // so one useful destination appears instead of a list of identical label names.
    //
    // Rank by priority FIRST, startsWith only as a tie-breaker. Almost every broad, useful
    // label in this hierarchy is prefixed ("cs.CV: World Models"), so a query like "world
    // model" never satisfies startsWith on it — ranking startsWith first meant a tiny,
    // deeply-nested 12-paper micro-cluster whose raw text happened to start with the query
    // (no category prefix to get in the way) always beat a 298-paper top-level label that
    // was actually the useful destination, regardless of the priority gap between them.
    const seenLabels = new Set<string>();
    const labelMatches = ds.labels.labels
      .filter((label) => label.text.toLowerCase().includes(q))
      .sort((a, b) => {
        const aStarts = a.text.toLowerCase().startsWith(q) ? 1 : 0;
        const bStarts = b.text.toLowerCase().startsWith(q) ? 1 : 0;
        return b.priority - a.priority || bStarts - aStarts;
      });
    for (const label of labelMatches) {
      const key = label.text.toLowerCase();
      if (seenLabels.has(key)) continue;
      seenLabels.add(key);
      out.push({
        kind: "label",
        key: `label-${label.id}`,
        labelId: label.id,
        text: label.text,
        count: label.count,
      });
      if (out.length >= 5) break;
    }

    const paperMatches: { i: number; title: string; rank: number; index: number; citedByCount: number }[] = [];
    for (let i = 0; i < ds.papers.length; i++) {
      const title = ds.papers[i].title;
      const lower = title.toLowerCase();
      if (!lower.includes(q)) continue;
      const { rank, index } = titleMatchRank(lower, q);
      paperMatches.push({ i, title, rank, index, citedByCount: ds.papers[i].citedByCount });
    }
    paperMatches.sort((a, b) => b.rank - a.rank || a.index - b.index || b.citedByCount - a.citedByCount);
    const paperSlots = Math.max(0, 10 - out.length);
    for (const m of paperMatches.slice(0, paperSlots)) {
      out.push({ kind: "paper", key: `paper-${m.i}`, nodeId: m.i, text: m.title });
    }
    return out;
  }, [query, ds.labels.labels, ds.papers]);

  const choose = (match: SearchMatch) => {
    if (match.kind === "paper") {
      selectNode(match.nodeId);
    } else {
      const label = ds.labels.labels.find((candidate) => candidate.id === match.labelId);
      if (label) focusLabel(label);
    }
    setQuery("");
    setActiveIndex(0);
  };

  return (
    <div className="search-box">
      <input
        aria-label="Search papers or map labels"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={matches.length > 0}
        aria-controls="paper-search-results"
        aria-activedescendant={
          matches[activeIndex] ? `paper-search-option-${matches[activeIndex].key}` : undefined
        }
        placeholder="Search papers or map labels..."
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
            choose(matches[activeIndex]);
          } else if (event.key === "Escape") {
            setQuery("");
          }
        }}
      />
      {matches.length > 0 && (
        <ul id="paper-search-results" className="autocomplete" role="listbox">
          {matches.map((m, index) => (
            <li
              id={`paper-search-option-${m.key}`}
              key={m.key}
              role="option"
              aria-selected={index === activeIndex}
              data-kind={m.kind}
            >
              <button
                type="button"
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => choose(m)}
              >
                <span>{m.text}</span>
                <span className="count">
                  {m.kind === "label" ? `Map label · ${m.count.toLocaleString()}` : "Paper"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
