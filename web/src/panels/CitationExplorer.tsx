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
// fan already sorted by citations (0 = most important). We blend two signals:
//   - magnitude: log citations normalized against the strongest paper in the same fan;
//   - rank: position within the shown fan.
// Rank guarantees visible, monotonic steps even when every linked paper is hugely cited
// (e.g. ResNet's references are all 40K-75K cites, which magnitude alone compresses to a
// flat ~1.0). The two sides self-scale independently.
function weighByRankAndCitations(
  ids: number[],
  ds: Dataset,
  index: number,
  count: number,
): number {
  const top = ids.length ? Math.log1p(ds.papers[ids[0]].citedByCount) : 0;
  const magnitude = top > 0 ? Math.log1p(ds.papers[ids[index]].citedByCount) / top : 0;
  const rank = count > 1 ? 1 - index / (count - 1) : 1;
  return 0.45 * magnitude + 0.55 * rank;
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
}: {
  ds: Dataset;
  ids: number[];
  direction: "in" | "out";
  expanded: boolean;
}) {
  const selectNode = useStore((s) => s.selectNode);
  const setHover = useStore((s) => s.setHover);
  const shown = expanded ? ids : ids.slice(0, LIST_LIMIT);

  if (ids.length === 0) {
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
        const authors = paper.authorNames.slice(0, 2).join(", ");
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
                <span className="citation-meta">
                  {authors || "Unknown authors"} · {year}
                  {paper.venue ? ` · ${paper.venue}` : ""}
                </span>
              </span>
              <span className="citation-count">{paper.citedByCount.toLocaleString()}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export function CitationExplorer({ ds, node }: { ds: Dataset; node: number }) {
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

  const matches = (id: number) => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    const paper = ds.papers[id];
    return (
      paper.title.toLowerCase().includes(needle) ||
      paper.authorNames.some((name) => name.toLowerCase().includes(needle))
    );
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
          <CitationRows ds={ds} ids={filteredOut} direction="out" expanded={expanded} />
        </div>
      )}
      {edgeMode !== "out" && (
        <div className="citation-group">
          {edgeMode === "both" && <h5>Cited by</h5>}
          <CitationRows ds={ds} ids={filteredIn} direction="in" expanded={expanded} />
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
