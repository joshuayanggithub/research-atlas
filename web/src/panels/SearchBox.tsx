// Combined type-ahead over paper titles, semantic map labels, and authors. Paper choices open
// the details sheet; label choices pan/zoom to the named map region; author choices filter the
// map to that author's papers.
//
// Authors are matched through the name-token index (D59), not by scanning a downloaded list:
// a query fetches the index chunk holding its tokens and the postings carry the name and paper
// count shown, so the 14.4 MB author index never has to be on the wire.

import { useMemo, useState } from "react";
import type { AuthorRow, Dataset } from "../data/types";
import { useAuthorSearchState } from "../data/useAuthorLookup";
import { addAuthorToSelection } from "../data/authorIdentity";
import { usePapersReady, usePapersEpoch } from "../data/usePapersReady";
import { useTitleSearch } from "../data/useTitleSearch";
import { useTitles } from "../data/useTitles";
import { TITLE_SHARD_CAP } from "../data/loadArtifacts";
import { PaperTitle } from "./PaperTitle";
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
  const authorSearch = useAuthorSearchState(query.trim(), query.trim().length >= MIN_QUERY);
  const authors = authorSearch.rows;
  // Papers, from the token index rather than a scan over whatever titles have downloaded.
  const titleSearch = useTitleSearch(query.trim(), query.trim().length >= MIN_QUERY);
  const titleHits = titleSearch.nodes;
  // The index says WHICH papers match; their titles still have to be fetched to show
  // and to rank. Only the handful that can be displayed.
  // Rank BEFORE slicing. Titles are needed to score a match, but they are fetched per node —
  // so taking the first 40 hits in set order meant scoring an arbitrary sample and the right
  // paper could never surface. Citations are known for every hit without any fetch, so they
  // order the pool that gets titles.
  // searchTitles already returns its hits in relevance order (whole-token matches first, then
  // citations from the RESIDENT index), so take the head of that. Re-sorting here by
  // ds.points.citedByCount was wrong twice over: it discarded that order, and points carry a
  // count of 0 for any paper whose tile has not downloaded — which is most of them — so
  // "Attention Is All You Need" sorted to the bottom of its own search.
  // Keep the pool inside TITLE_SHARD_CAP: a wider pool spans more shards than useTitles will
  // fetch, so the overflow titles can never arrive and would be ranked as if they did not match.
  const titlePool = useMemo(() => titleHits.slice(0, TITLE_SHARD_CAP), [titleHits]);
  useTitles(titlePool);
  // Paper titles arrive after first paint; recompute matches once they do.
  const papersReady = usePapersReady();
  const papersEpoch = usePapersEpoch();
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

    // Papers come from the token index (useTitleSearch), which covers the whole corpus from
    // the first query. Ranking still uses the title when it is resident — the index answers
    // WHICH papers match; how well they match is a property of the string.
    const paperMatches: { i: number; title: string; rank: number; index: number; citedByCount: number }[] = [];
    for (const i of titlePool) {
      const title = ds.papers[i]?.title ?? "";
      const lower = title.toLowerCase();
      const { rank, index } = lower ? titleMatchRank(lower, q) : { rank: 0, index: 0 };
      paperMatches.push({ i, title, rank, index, citedByCount: ds.papers[i]?.citedByCount ?? 0 });
    }
    // titleMatchRank scores the query against the title STRING, so it is only meaningful once
    // the title is resident. Sorting a mixed set ranked an unloaded title as a non-match and
    // buried it under weaker loaded ones -- "Attention Is All You Need" lost its own search to
    // "Cross-Attention is all you need" purely because its shard had not landed. Until every
    // pooled title is here, keep the index order, which already ranks whole-token matches
    // ahead of prefix expansions and breaks ties on citations.
    if (paperMatches.every((m) => m.title !== "")) {
      paperMatches.sort((a, b) => b.rank - a.rank || a.index - b.index || b.citedByCount - a.citedByCount);
    }
    const paperSlots = Math.max(0, 10 - out.length);
    for (const m of paperMatches.slice(0, paperSlots)) {
      out.push({ kind: "paper", key: `paper-${m.i}`, nodeId: m.i, text: m.title });
    }
    return out;
  }, [query, ds.labels.labels, ds.papers, ds.points.citedByCount, authors, lowerNames,
      papersReady, papersEpoch, titlePool]);

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
      {/* Same distinction for papers: an empty list means "no match" only once the index chunk
          for this query has landed. Before that it means nothing at all. */}
      {query.trim().length >= MIN_QUERY && titleSearch.pending && matches.length === 0 && (
        <ul className="autocomplete" role="listbox" aria-live="polite">
          <li role="option" aria-selected={false}>
            <button type="button" disabled>
              <span className="subtle">searching titles…</span>
            </button>
          </li>
        </ul>
      )}
      {/* This used to read "loading author index…" whenever no author matched, which was left
          over from when the whole index streamed in (D59). Nothing streams now, so for a query
          with no author it announced a load that would never finish — the "useless loading
          text that never loads". It appears only while a lookup is genuinely in flight. */}
      {query.trim().length >= MIN_QUERY && authorSearch.pending && matches.length === 0 && (
        <ul className="autocomplete" role="listbox" aria-live="polite">
          <li role="option" aria-selected={false}>
            <button type="button" disabled>
              <span className="subtle">searching…</span>
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
                {/* A paper the index matched but whose title has not downloaded yet would
                    render as an empty row labelled "Paper" — a result you cannot read and
                    cannot tell from a bug. PaperTitle shows the same shimmer used everywhere
                    else instead. */}
                <span>{m.kind === "paper" ? <PaperTitle title={m.text} node={m.nodeId} /> : m.text}</span>
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
