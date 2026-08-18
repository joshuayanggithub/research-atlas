// Combined type-ahead over paper titles, semantic map labels, and authors. Paper choices open
// the details sheet; label choices pan/zoom to the named map region; author choices filter the
// map to that author's papers.
//
// Authors are NOT in the startup bundle (authors.arrow is 33 MB and unpacking it was ~50% of
// load time), so they are pulled in through useAuthors only once the user has actually typed a
// query. First paint stays fast; the first author search pays the unpack once, then it's cached.

import { useMemo, useState } from "react";
import type { AuthorRow, Dataset } from "../data/types";
import { useAuthors } from "../data/useAuthors";
import { addAuthorToSelection } from "../data/authorIdentity";
import { usePapersReady } from "../data/usePapersReady";
import { useStore } from "../state/store";

type SearchMatch =
  | { kind: "label"; key: string; labelId: number; text: string; count: number }
  | { kind: "author"; key: string; authorId: number; text: string; count: number }
  | { kind: "paper"; key: string; nodeId: number; text: string };

const MIN_QUERY = 3;

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

// Rank an author by how the query hits their name: a surname/forename starting with the query
// beats a mid-word substring, so typing "hinton" surfaces Geoffrey Hinton rather than someone
// named "Worthington". Ties break on how many papers they have in the corpus.
function authorMatchRank(lowerName: string, q: string): number {
  if (lowerName.startsWith(q)) return 3;
  if (new RegExp(`\\b${escapeRegExp(q)}`).test(lowerName)) return 2;
  return 1;
}

export function SearchBox({ ds }: { ds: Dataset }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const selectNode = useStore((s) => s.selectNode);
  const focusLabel = useStore((s) => s.focusLabel);
  const toggleLabel = useStore((s) => s.toggleLabel);
  const selectedAuthorIds = useStore((s) => s.filters.authorIds);
  const selectedLabelIds = useStore((s) => s.filters.labelIds);
  const setAuthors = useStore((s) => s.setAuthors);

  // Triggers the lazy authors.arrow fetch the moment the query is long enough to search.
  const authors = useAuthors(query.trim().length >= MIN_QUERY);
  // Paper titles arrive after first paint; recompute matches once they do.
  const papersReady = usePapersReady();
  // 829k names: lowercase once per load, not once per keystroke. Author chunks arrive
  // progressively (D32) and each one hands back a NEW array, so this recomputes as they land.
  const lowerNames = useMemo(
    () => authors.map((a: AuthorRow) => a.name.toLowerCase()),
    [authors],
  );

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < MIN_QUERY) return [];
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
      if (out.length >= 4) break;
    }

    // Authors, capped so they never crowd out titles. Empty until authors.arrow lands, so the
    // first keystrokes show labels/papers and authors appear a moment later.
    const authorMatches: { a: AuthorRow; rank: number }[] = [];
    for (let i = 0; i < lowerNames.length; i++) {
      if (!lowerNames[i].includes(q)) continue;
      authorMatches.push({ a: authors[i], rank: authorMatchRank(lowerNames[i], q) });
    }
    authorMatches.sort((x, y) => y.rank - x.rank || y.a.count - x.a.count);
    for (const { a } of authorMatches.slice(0, 3)) {
      out.push({
        kind: "author",
        key: `author-${a.authorId}`,
        authorId: a.authorId,
        text: a.name,
        count: a.count,
      });
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
  }, [query, ds.labels.labels, ds.papers, authors, lowerNames, papersReady]);

  const choose = (match: SearchMatch) => {
    if (match.kind === "paper") {
      selectNode(match.nodeId);
    } else if (match.kind === "author") {
      // Same destination as clicking an author in the details panel: filter the map to their
      // papers and close any open paper so the filtered map (and AuthorPanel) is revealed.
      // Merge every row sharing this name — OpenAlex splits one person across several.
      setAuthors(addAuthorToSelection(match.authorId, authors, selectedAuthorIds));
      selectNode(null);
    } else {
      const label = ds.labels.labels.find((candidate) => candidate.id === match.labelId);
      if (label) {
        // Same action as clicking the label on the map: go there AND select its papers, so
        // reaching a region by search and by click leave you in the same state.
        focusLabel(label);
        if (!selectedLabelIds.includes(label.id)) toggleLabel(label.id);
      }
    }
    setQuery("");
    setActiveIndex(0);
  };

  return (
    <div className="search-box">
      <input
        aria-label="Search papers, authors, or map labels"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={matches.length > 0}
        aria-controls="paper-search-results"
        aria-activedescendant={
          matches[activeIndex] ? `paper-search-option-${matches[activeIndex].key}` : undefined
        }
        placeholder="Search papers, authors, or labels..."
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
      {/* An empty author section is indistinguishable from "no such author", so say which it
          is while the 55.9 MB author index is still streaming in. */}
      {query.trim().length >= MIN_QUERY && authors.length === 0 && (
        <ul className="autocomplete" role="listbox" aria-live="polite">
          <li role="option" aria-selected={false}>
            <button type="button" disabled>
              <span className="subtle">loading author index…</span>
            </button>
          </li>
        </ul>
      )}
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
                  {m.kind === "label"
                    ? `Map label · ${m.count.toLocaleString()}`
                    : m.kind === "author"
                      ? `Author · ${m.count.toLocaleString()} paper${m.count === 1 ? "" : "s"}`
                      : "Paper"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
