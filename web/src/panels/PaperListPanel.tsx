// A plain list of the papers currently passing the filters.
//
// The map answers "where does this work sit?", but not "what exactly is in my selection?" —
// and for a small result (an author's 7 papers, one region) the spatial layout is the wrong
// tool entirely. This panel lists them, sorted, with the counts that matter, and selecting a
// row drives the same selection the map uses.
//
// Titles live in the deferred papers-index (D23), so rows can exist before their text does;
// usePapersReady re-renders when the strings land rather than blocking the list on a 98.8 MB
// artifact.

import { List, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";
import { useFilterMask } from "../filters/useFilterMask";
import { usePapersReady, usePointTilesEpoch } from "../data/usePapersReady";
import { useSetLabel } from "../data/useSetLabel";
import { PaperTitle } from "./PaperTitle";
import { useTitles } from "../data/useTitles";
import { PaperYear } from "./PaperYear";

// Rendering every row of a 900k-paper result would freeze the tab and help nobody; the list is
// for inspecting a selection, and anything past this is better narrowed with another facet.
const MAX_ROWS = 500;
// Rough row height, used only to work out which rows are on screen so their titles can be
// fetched. Being a few rows out costs one extra 72 KB shard, not correctness.
const ROW_HEIGHT_PX = 46;
// Rows whose titles are fetched around the scroll position. Matches TITLE_SHARD_CAP, so one
// scroll never asks for more shards than the loader will serve in a single call.
const TITLE_WINDOW = 24;
// Papers sampled to characterise the selection. Enough for stable term statistics without
// walking a 31k-paper region on every filter change.
const TOPIC_SAMPLE = 2000;

type Sort = "citations" | "newest" | "oldest";

export function PaperListPanel({ ds }: { ds: Dataset }) {
  const filters = useStore((s) => s.filters);
  const selectNode = useStore((s) => s.selectNode);
  const selectedNode = useStore((s) => s.selectedNode);
  const filter = useFilterMask(ds, filters);
  const papersReady = usePapersReady();
  const tilesEpoch = usePointTilesEpoch();
  const [open, setOpen] = useState(false);
  const [sort, setSort] = useState<Sort>("citations");
  // First row whose title we have asked for; moves with the scroll position.
  const [titleFrom, setTitleFrom] = useState(0);

  const anyFilter = !!filter?.anyOrgAuthorActive;
  const authorActive = filters.authorIds.length > 0 && selectedNode === null;


  const { rows, total, sample } = useMemo(() => {
    if (!filter || !anyFilter) return { rows: [] as number[], total: 0, sample: [] as number[] };
    const { monthIndex, citedByCount, year, count } = ds.points;
    const hits: number[] = [];
    for (let i = 0; i < count; i++) {
      if (filter.matchValue[i] !== 1) continue;
      const m = monthIndex[i];
      if (m < filters.monthMin || m > filters.monthMax) continue;
      hits.push(i);
    }
    const cmp =
      sort === "citations"
        ? (a: number, b: number) => citedByCount[b] - citedByCount[a]
        : sort === "newest"
          ? (a: number, b: number) => year[b] - year[a] || citedByCount[b] - citedByCount[a]
          : (a: number, b: number) => year[a] - year[b] || citedByCount[b] - citedByCount[a];
    hits.sort(cmp);
    // A representative sample of the WHOLE match for the topic label — `rows` is the top 500 by
    // the active sort, so labelling from it would describe the most-cited papers rather than
    // the selection. Stride-sample instead, which also avoids the era bias of a head slice.
    const stride = Math.max(1, Math.ceil(hits.length / TOPIC_SAMPLE));
    const sample: number[] = [];
    for (let k = 0; k < hits.length; k += stride) sample.push(hits[k]);
    return { rows: hits.slice(0, MAX_ROWS), total: hits.length, sample };
    // tilesEpoch: matchValue is incomplete until every point tile has arrived (D23/D25).
    // papersReady: titles arrive separately and change what the rows read.
  }, [ds, filter, anyFilter, filters.monthMin, filters.monthMax, sort, tilesEpoch, papersReady]);

  // Only the rows on screen need titles (see ROW_HEIGHT_PX).
  useTitles(rows.slice(titleFrom, titleFrom + TITLE_WINDOW));

  // Characterise the whole filtered set, not just the rows shown. Uses the same c-TF-IDF idea
  // s07 uses for regions, over titles.
  const topic = useSetLabel(ds, sample, papersReady);

  if (!anyFilter) return null;

  return (
    // With an author selected, the list moves to the right-hand column so a researcher's
    // identity and their work are in the same place. It used to sit bottom-left while the
    // author panel sat top-right — diagonally opposite corners of the screen.
    <div className={`paper-list ${open ? "open" : ""} ${authorActive ? "with-author" : ""}`}>
      <button
        type="button"
        className="paper-list-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <X size={14} aria-hidden="true" /> : <List size={14} aria-hidden="true" />}
        {open ? "Hide list" : `List ${total.toLocaleString()}`}
      </button>

      {/* A short description of what the current selection is ABOUT. Region labels cannot answer
          this — they name whichever areas the papers land in, not the papers themselves. */}
      {topic.length > 0 && (
        <div className="set-topic" title="Distinctive terms in this selection">
          <span className="set-topic-kind">mostly</span>
          {topic.join(" · ")}
        </div>
      )}

      {open && (
        <div className="paper-list-body" role="region" aria-label="Filtered papers">
          <div className="paper-list-head">
            <span className="subtle">
              {total.toLocaleString()} paper{total === 1 ? "" : "s"}
              {total > MAX_ROWS && ` · showing top ${MAX_ROWS}`}
            </span>
            <div className="seg small" role="group" aria-label="Sort papers">
              {(
                [
                  ["citations", "Cited"],
                  ["newest", "Newest"],
                  ["oldest", "Oldest"],
                ] as [Sort, string][]
              ).map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  className={sort === k ? "active" : ""}
                  aria-pressed={sort === k}
                  onClick={() => setSort(k)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <ol
            className="paper-list-rows"
            // Titles are per-node shards now. Fetching all 500 rows' titles would be 322
            // requests and 20.8 MB (measured); fetching the ones on screen is ~24 x 72 KB.
            onScroll={(e) => {
              const first = Math.floor(e.currentTarget.scrollTop / ROW_HEIGHT_PX);
              setTitleFrom(Math.max(0, first - 4));
            }}
          >
            {rows.map((node) => {
              const p = ds.papers[node];
              return (
                <li key={node}>
                  <button
                    type="button"
                    className={`paper-row ${node === selectedNode ? "active" : ""}`}
                    onClick={() => selectNode(node)}
                  >
                    <span className="paper-row-title">
                      <PaperTitle title={p?.title} node={node} />
                    </span>
                    <span className="paper-row-meta subtle">
                      <PaperYear paper={p} /> ·{" "}
                      {ds.manifest.corpus.citation_count_source && p?.citationCountAvailable
                        ? `${p.citedByCount.toLocaleString()} cites`
                        : "—"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </div>
  );
}
