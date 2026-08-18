import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Network,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import type { Dataset } from "../data/types";
import { useStore, type EdgeMode } from "../state/store";
import { useEdgesReady } from "../data/usePapersReady";
import { importanceWeight } from "../map/importance";

const GRAPH_LIMIT = 5;
const LIST_LIMIT = 7;

interface GraphPaper {
  id: number;
  x: number;
  y: number;
  direction: "in" | "out";
  // Importance in [0,1] within its own direction (1 = most cited shown). Drives node
  // radius and edge width so the graph reads in order of citation importance.
  weight: number;
}

// Importance weight in [0,1] for a paper shown at position `index` within a `count`-long
// fan already sorted by citations (0 = most important). See map/importance.ts — blends
// citation magnitude with rank so the ordering is always visible even when every linked
// paper is hugely cited (e.g. ResNet's references are all 40K-75K cites).
function weighByRankAndCitations(
  ids: number[],
  ds: Dataset,
  index: number,
  count: number,
): number {
  const top = ids.length ? Math.log1p(ds.papers[ids[0]].citedByCount) : 0;
  return importanceWeight(
    Math.log1p(ds.papers[ids[index]].citedByCount),
    top,
    index,
    count,
  );
}

function ranked(ids: number[], ds: Dataset): number[] {
  return Array.from(new Set(ids))
    .filter((id) => id >= 0 && id < ds.papers.length)
    .sort((a, b) => {
      const citationDelta = ds.papers[b].citedByCount - ds.papers[a].citedByCount;
      if (citationDelta !== 0) return citationDelta;
      return ds.papers[b].publicationDate.localeCompare(ds.papers[a].publicationDate);
    });
}

function graphY(index: number, count: number): number {
  if (count <= 1) return 78;
  return 27 + (index * 102) / (count - 1);
}

function activatePaper(event: KeyboardEvent<SVGGElement>, select: () => void) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    select();
  }
}

function CitationGraph({
  ds,
  incoming,
  outgoing,
  mode,
}: {
  ds: Dataset;
  incoming: number[];
  outgoing: number[];
  mode: EdgeMode;
}) {
  const selectNode = useStore((s) => s.selectNode);
  const shownIn = mode === "out" ? [] : incoming.slice(0, GRAPH_LIMIT);
  const shownOut = mode === "in" ? [] : outgoing.slice(0, GRAPH_LIMIT);
  const nodes: GraphPaper[] = [
    ...shownIn.map((id, index) => ({
      id,
      x: 24,
      y: graphY(index, shownIn.length),
      direction: "in" as const,
      weight: weighByRankAndCitations(shownIn, ds, index, shownIn.length),
    })),
    ...shownOut.map((id, index) => ({
      id,
      x: 296,
      y: graphY(index, shownOut.length),
      direction: "out" as const,
      weight: weighByRankAndCitations(shownOut, ds, index, shownOut.length),
    })),
  ];

  return (
    <svg
      className="citation-graph"
      viewBox="0 0 320 156"
      role="img"
      aria-label="Directed citation neighborhood"
    >
      <defs>
        <marker
          id="citation-arrow-in"
          markerWidth="7"
          markerHeight="7"
          refX="6"
          refY="3.5"
          orient="auto"
        >
          <path d="M0,0 L7,3.5 L0,7 Z" className="citation-arrow-in" />
        </marker>
        <marker
          id="citation-arrow-out"
          markerWidth="7"
          markerHeight="7"
          refX="6"
          refY="3.5"
          orient="auto"
        >
          <path d="M0,0 L7,3.5 L0,7 Z" className="citation-arrow-out" />
        </marker>
      </defs>

      {shownIn.length > 0 && (
        <text x="14" y="12" className="citation-graph-label">
          CITING
        </text>
      )}
      {shownOut.length > 0 && (
        <text x="306" y="12" textAnchor="end" className="citation-graph-label">
          REFERENCES
        </text>
      )}

      {nodes.map((paper) => {
        const incomingEdge = paper.direction === "in";
        return (
          <line
            key={`edge-${paper.direction}-${paper.id}`}
            x1={incomingEdge ? paper.x + 8 : 168}
            y1={incomingEdge ? paper.y : 78}
            x2={incomingEdge ? 152 : paper.x - 8}
            y2={incomingEdge ? 78 : paper.y}
            className={`citation-graph-edge ${incomingEdge ? "incoming" : "outgoing"}`}
            // Stroke width encodes citation importance (1..3px), matching the node radius.
            strokeWidth={1 + 2 * paper.weight}
            markerEnd={`url(#citation-arrow-${paper.direction})`}
          />
        );
      })}

      <circle cx="160" cy="78" r="12" className="citation-center" />
      <circle cx="160" cy="78" r="3" className="citation-center-dot" />
      <text x="160" y="102" textAnchor="middle" className="citation-center-label">
        THIS PAPER
      </text>

      {nodes.map((paper) => (
        <g
          key={`node-${paper.direction}-${paper.id}`}
          className={`citation-graph-node ${paper.direction === "in" ? "incoming" : "outgoing"}`}
          role="button"
          tabIndex={0}
          aria-label={`Open ${ds.papers[paper.id].title}`}
          onClick={() => selectNode(paper.id)}
          onKeyDown={(event) => activatePaper(event, () => selectNode(paper.id))}
        >
          <title>{`${ds.papers[paper.id].title} · ${ds.papers[
            paper.id
          ].citedByCount.toLocaleString()} citations`}</title>
          {/* Radius encodes citation importance (4.5..8px) so the most-cited linked papers
              are the largest nodes, in rank order down each side. */}
          <circle cx={paper.x} cy={paper.y} r={4.5 + 3.5 * paper.weight} />
        </g>
      ))}

      {incoming.length > shownIn.length && mode !== "out" && (
        <text x="14" y="151" className="citation-more-label">
          +{incoming.length - shownIn.length} more
        </text>
      )}
      {outgoing.length > shownOut.length && mode !== "in" && (
        <text x="306" y="151" textAnchor="end" className="citation-more-label">
          +{outgoing.length - shownOut.length} more
        </text>
      )}
    </svg>
  );
}

function CitationRows({
  ds,
  ids,
  direction,
  expanded,
  referencesAvailable,
}: {
  ds: Dataset;
  ids: number[];
  direction: "in" | "out";
  expanded: boolean;
  /** False when no provider supplied a reference list for the SELECTED paper. */
  referencesAvailable: boolean;
}) {
  const selectNode = useStore((s) => s.selectNode);
  const setHover = useStore((s) => s.setHover);
  const shown = expanded ? ids : ids.slice(0, LIST_LIMIT);

  if (ids.length === 0) {
    // "No references in this corpus" and "nobody extracted this paper's references" look
    // identical as an empty list, but mean completely different things. S2 has no reference
    // list at all for 7.3% of the corpus, rising sharply for recent work (2026: ~30% missing),
    // so an unqualified "none in this corpus" reads as a claim about the paper when it is
    // really a gap in the data. Say which it is.
    if (direction === "out" && !referencesAvailable) {
      return (
        <div className="citation-empty">
          No reference data available for this paper.
          <span className="subtle"> Its reference list was never extracted upstream — common
          for recent preprints — so this is missing data, not a paper without references.</span>
        </div>
      );
    }
    return (
      <div className="citation-empty">
        No {direction === "out" ? "references" : "citing papers"} in this corpus.
      </div>
    );
  }

  return (
    <ol className="citation-list">
      {shown.map((id) => {
        const paper = ds.papers[id];
        // Rows use the resident index only (title, year, citations); author names/venue are
        // lazy per-paper detail shown when a paper is selected, not per list row.
        const year = paper.publicationDate.slice(0, 4);
        return (
          <li key={`${direction}-${id}`}>
            <button
              type="button"
              onClick={() => selectNode(id)}
              onMouseEnter={() => setHover(id)}
              onMouseLeave={() => setHover(null)}
            >
              <span className={`citation-direction ${direction}`}>
                {direction === "out" ? (
                  <ArrowRight size={15} aria-hidden="true" />
                ) : (
                  <ArrowLeft size={15} aria-hidden="true" />
                )}
              </span>
              <span className="citation-paper">
                <span className="citation-title">{paper.title}</span>
                <span className="citation-meta">{year || "—"}</span>
              </span>
              <span className="citation-count">{paper.citedByCount.toLocaleString()}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export function CitationExplorer(
  { ds, node, referenceCount = -1 }: { ds: Dataset; node: number; referenceCount?: number },
) {
  const edgesReady = useEdgesReady();
  const edgeMode = useStore((s) => s.edgeMode);
  const setEdgeMode = useStore((s) => s.setEdgeMode);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(false);

  const incoming = useMemo(
    () => ranked(ds.citedBy.get(node) ?? [], ds),
    [ds, node],
  );
  const outgoing = useMemo(
    () => ranked(ds.citesOut.get(node) ?? [], ds),
    [ds, node],
  );

  useEffect(() => {
    setQuery("");
    setExpanded(false);
  }, [node, edgeMode]);

  if (!ds.manifest.corpus.citation_graph_source) {
    return (
      <section className="citation-explorer" aria-labelledby="citation-heading">
        <div className="panel-section-head">
          <h4 id="citation-heading">Citation network</h4>
          <span>unavailable</span>
        </div>
        <div className="citation-empty">
          This arXiv metadata build has no citation graph. Semantic related works remain
          available below.
        </div>
      </section>
    );
  }

  const matches = (id: number) => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    // Filter by title only — author names are lazy per-paper detail, not resident.
    return ds.papers[id].title.toLowerCase().includes(needle);
  };
  const filteredIn = incoming.filter(matches);
  const filteredOut = outgoing.filter(matches);
  const activeCount =
    edgeMode === "in"
      ? filteredIn.length
      : edgeMode === "out"
        ? filteredOut.length
        : filteredIn.length + filteredOut.length;
  const canExpand = activeCount > (edgeMode === "both" ? LIST_LIMIT * 2 : LIST_LIMIT);

  const modes: { id: EdgeMode; label: string; count: number }[] = [
    { id: "out", label: "References", count: outgoing.length },
    { id: "in", label: "Cited by", count: incoming.length },
    { id: "both", label: "Both", count: incoming.length + outgoing.length },
  ];

  return (
    <section className="citation-explorer" aria-labelledby="citation-heading">
      <div className="panel-section-head">
        <h4 id="citation-heading">Citation network</h4>
        <span>within this map</span>
      </div>

      <div className="seg citation-tabs" role="tablist" aria-label="Citation direction">
        {modes.map((mode) => (
          <button
            type="button"
            role="tab"
            key={mode.id}
            className={edgeMode === mode.id ? "active" : ""}
            aria-selected={edgeMode === mode.id}
            onClick={() => setEdgeMode(mode.id)}
          >
            {mode.id === "out" ? (
              <ArrowRight size={14} aria-hidden="true" />
            ) : mode.id === "in" ? (
              <ArrowLeft size={14} aria-hidden="true" />
            ) : (
              <Network size={14} aria-hidden="true" />
            )}
            <span>{mode.label}</span>
            <strong>{mode.count}</strong>
          </button>
        ))}
      </div>

      {/* "References 5" on a paper that cites 18 works reads as a fact about the PAPER when it is
          really the shape of the MAP: only edges whose other end is also in this corpus can be
          drawn. Users reported this as a bug twice before it was stated.
          Gated on edgesReady: the citation graph streams in after first paint, so before it
          lands `outgoing` is empty and this would confidently announce "0 of 18 — the other 18
          cite work outside it", which is false. Same placeholder-as-fact trap as D39/D41/D44. */}
      {edgesReady && referenceCount > outgoing.length && edgeMode !== "in" && (
        <p className="citation-note subtle">
          {outgoing.length} of {referenceCount.toLocaleString()} references are in this map — the
          other {(referenceCount - outgoing.length).toLocaleString()} cite work outside it
          (books, journals, non-arXiv venues).
        </p>
      )}

      <CitationGraph
        ds={ds}
        incoming={incoming}
        outgoing={outgoing}
        mode={edgeMode}
      />

      {incoming.length + outgoing.length > LIST_LIMIT && (
        <label className="citation-search">
          <Search size={14} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter linked papers"
            aria-label="Filter linked papers"
          />
        </label>
      )}

      {edgeMode !== "in" && (
        <div className="citation-group">
          {edgeMode === "both" && <h5>References</h5>}
          <CitationRows
            ds={ds}
            ids={filteredOut}
            direction="out"
            expanded={expanded}
            referencesAvailable={ds.papers[node]?.referencesAvailable ?? true}
          />
        </div>
      )}
      {edgeMode !== "out" && (
        <div className="citation-group">
          {edgeMode === "both" && <h5>Cited by</h5>}
          <CitationRows
            ds={ds}
            ids={filteredIn}
            direction="in"
            expanded={expanded}
            referencesAvailable={ds.papers[node]?.referencesAvailable ?? true}
          />
        </div>
      )}

      {canExpand && (
        <button
          type="button"
          className="citation-expand"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? (
            <ChevronUp size={15} aria-hidden="true" />
          ) : (
            <ChevronDown size={15} aria-hidden="true" />
          )}
          {expanded ? "Show fewer" : `Show all ${activeCount}`}
        </button>
      )}
    </section>
  );
}
