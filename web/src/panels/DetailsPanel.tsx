// Metadata and graph context for the selected paper.

import { ExternalLink, FileText, Network, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type DetailView = "citations" | "paper";
import type { Dataset, PaperDetail } from "../data/types";
import { useStore } from "../state/store";
import { PaperTitle } from "./PaperTitle";
import { useTitles } from "../data/useTitles";
import { useAuthorInfo } from "../data/useAuthorLookup";
import { addAuthorToSelection } from "../data/authorIdentity";
import { ArxivPreview } from "./ArxivPreview";
import { CitationExplorer } from "./CitationExplorer";
import { FirstFigure } from "./FirstFigure";
import { RelatedWorksPanel } from "./RelatedWorksPanel";

// URL of the pipeline-baked first-figure crop for a node, or null when none was baked (so
// FirstFigure uses its pdf.js fallback). Mirrors schema.figure_path: figures/<node/size>/<node>.png.
function bakedFigureUrl(ds: Dataset, node: number): string | null {
  const fig = ds.manifest.figures;
  if (!fig || !ds.papers[node]?.hasFigure) return null;
  return `data/${fig.dir}/${Math.floor(node / fig.shard_size)}/${node}.png`;
}

// Authors shown before the list collapses behind a "+N more" toggle.
const AUTHOR_PREVIEW = 6;

// Draggable panel width (desktop only), persisted so it survives re-selects and reloads.
const WIDTH_KEY = "detailsPanelWidth";
const MIN_WIDTH = 360;
function clampWidth(px: number): number {
  // Cap at 92% of the viewport so the panel never fully covers the map; floor keeps the
  // citation graph / tabs usable.
  const max = Math.max(MIN_WIDTH, Math.round(window.innerWidth * 0.92));
  return Math.min(max, Math.max(MIN_WIDTH, px));
}

export function DetailsPanel({ ds }: { ds: Dataset }) {
  const selectedNode = useStore((s) => s.selectedNode);
  const selectNode = useStore((s) => s.selectNode);
  const selectedAuthorIds = useStore((s) => s.filters.authorIds);
  const setAuthors = useStore((s) => s.setAuthors);
  const [view, setView] = useState<DetailView>("citations");
  const [showAllAuthors, setShowAllAuthors] = useState(false);
  const [detail, setDetail] = useState<PaperDetail | null>(null);
  // Author ids come from the paper's detail shard; fetch just those records so clicking one
  // can merge the person's split profiles (D59). Declared after `detail` because it reads it.
  const authorInfo = useAuthorInfo(detail?.authorIds ?? []);
  const authors = [...authorInfo.values()];
  const authorById = authorInfo;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Panel width. Read the saved value lazily; 0 means "use the CSS default" until the user
  // drags. Only applied on wide viewports (the mobile bottom-sheet ignores it via CSS).
  const [width, setWidth] = useState<number>(() => {
    const saved = Number(localStorage.getItem(WIDTH_KEY));
    return saved >= MIN_WIDTH ? saved : 0;
  });

  // Left-edge drag to resize. The panel is anchored to the right, so widening means the left
  // edge moves left → width grows as the pointer x decreases. Uses a pointer capture so the
  // drag keeps tracking even over the map/canvas.
  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = panelRef.current?.offsetWidth ?? (width || MIN_WIDTH);
    const onMove = (ev: PointerEvent) => setWidth(clampWidth(startW + (startX - ev.clientX)));
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.userSelect = "";
      setWidth((w) => {
        if (w >= MIN_WIDTH) localStorage.setItem(WIDTH_KEY, String(w));
        return w;
      });
    };
    document.body.style.userSelect = "none"; // don't select text while dragging
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  useEffect(() => {
    setView("citations");
    setShowAllAuthors(false); // a new paper starts collapsed
  }, [ds, selectedNode]);

  // Fetch the selected paper's full detail (author names, venue, ids, full date) on demand.
  useEffect(() => {
    if (selectedNode === null) return;
    let live = true;
    setDetail(null);
    ds.getPaperDetail(selectedNode).then((d) => {
      if (live) setDetail(d);
    });
    return () => {
      live = false;
    };
  }, [ds, selectedNode]);

  // Move keyboard focus into the panel when a paper is selected so screen-reader and
  // keyboard users land on the new content (the panel is last in the DOM). Escape closes.
  useEffect(() => {
    if (selectedNode !== null) headingRef.current?.focus();
  }, [selectedNode]);

  // The selected paper's own title shard. MUST be above the early returns below: React
  // requires the same hooks on every render, and this component returns null when nothing is
  // selected. An empty array is the no-op case.
  useTitles(selectedNode !== null ? [selectedNode] : []);

  if (selectedNode === null) return null;
  const p = ds.papers[selectedNode];
  if (!p) return null;

  // Resident index has title/year/citations; author names, venue, links come from detail.
  const authorNames = detail?.authorNames ?? [];
  // Resident papers-index carries author_ids; author_names load async in PaperDetail. Both
  // derive from the same corpus row in the same order (s11_emit), so index i pairs across them.
  // Author ids moved into the per-paper detail shard (which this panel already fetches on
  // selection), so they are empty for the instant before `detail` resolves — the same window in
  // which authorNames is empty, so the two stay in step.
  const authorIds = detail?.authorIds ?? [];
  // OpenAlex-matched authors get a real, cross-paper-stable id ("A...", deduplicated from
  // same-named authors); unmatched papers fall back to a hash of the raw name string
  // (see s02_build_arxiv_corpus._author_id), which can collide across distinct people.
  const authorVerified = (id: number) => authorById.get(id)?.verified !== false;
  // Applying the filter and staying on this one paper would leave the (now-filtered) map
  // hidden behind the still-open panel. Close it so the user lands on the filtered map —
  // i.e. moves from "this paper" to "this author's papers" — matching how selecting an
  // org or topic filter also drops back to the map.
  const addAuthorFilter = (id: number) => {
    // Select the whole identity, not just the row this paper happens to reference: OpenAlex
    // splits one person across multiple author rows, so a single id often yields one dot.
    setAuthors(addAuthorToSelection(id, authors, selectedAuthorIds));
    selectNode(null);
  };
  // "—" only when the date is KNOWN to be absent; while the index is still in flight the
  // panel says so rather than asserting a paper has no publication date.
  const dateText = detail?.publicationDate || p.publicationDate
    || (p.dateAvailable ? "—" : "Loading…");
  const link = detail?.doi
    ? `https://doi.org/${detail.doi}`
    : detail?.arxivId
      ? `https://arxiv.org/abs/${detail.arxivId}`
      : null;
  const citationSource = ds.manifest.corpus.citation_count_source;
  const citationText = citationSource && p.citationCountAvailable
    ? `${p.citedByCount.toLocaleString()} citations · ${citationSource}`
    : "citation count unavailable";

  return (
    <div
      ref={panelRef}
      className="panel details"
      role="dialog"
      aria-label="Paper details"
      style={width >= MIN_WIDTH ? { width } : undefined}
      onKeyDown={(e) => {
        if (e.key === "Escape") selectNode(null);
      }}
    >
      {/* Drag the left edge to widen the panel (e.g. to see a figure at full width). */}
      <div
        className="details-resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize paper panel"
        title="Drag to resize"
        onPointerDown={startResize}
        onDoubleClick={() => {
          localStorage.removeItem(WIDTH_KEY);
          setWidth(0);
        }}
      />
      <button
        type="button"
        className="close"
        aria-label="Close paper details"
        title="Close paper details"
        onClick={() => selectNode(null)}
      >
        <X size={18} aria-hidden="true" />
      </button>
      <h3 ref={headingRef} tabIndex={-1}><PaperTitle title={p.title} node={selectedNode ?? undefined} /></h3>
      <div className="meta authors">
        {authorNames.length > 0
          ? (showAllAuthors ? authorNames : authorNames.slice(0, AUTHOR_PREVIEW)).map((name, i) => {
              const id = authorIds[i];
              if (id === undefined) return <span key={i}>{name}</span>;
              const verified = authorVerified(id);
              return (
                <button
                  key={id}
                  type="button"
                  className={
                    "author-link" +
                    (selectedAuthorIds.includes(id) ? " active" : "") +
                    (verified ? "" : " unverified")
                  }
                  title={
                    verified
                      ? `Show other papers by ${name}`
                      : `Show other papers by ${name} — identity not confirmed by OpenAlex, may include other authors sharing this name`
                  }
                  onClick={() => addAuthorFilter(id)}
                >
                  {name}
                </button>
              );
            }).reduce<React.ReactNode[]>((acc, el, i) => {
              if (i > 0) acc.push(", ");
              acc.push(el);
              return acc;
            }, [])
          : detail === null
            ? "Loading authors…"
            : "—"}
        {/* Large collaborations run to 100+ authors (Kimi K3 lists 99). A bare "+94" hid them
            with no way to look, so the overflow is a control that reveals the rest in place. */}
        {authorNames.length > AUTHOR_PREVIEW && (
          <>
            {showAllAuthors ? " " : " "}
            <button
              type="button"
              className="author-more"
              aria-expanded={showAllAuthors}
              onClick={() => setShowAllAuthors((open) => !open)}
            >
              {showAllAuthors
                ? "show fewer"
                : `+${authorNames.length - AUTHOR_PREVIEW} more`}
            </button>
          </>
        )}
      </div>
      <div className="meta subtle">
        {dateText} · {detail?.venue ?? "—"} · {citationText}
      </div>
      {link && (
        <a className="link" href={link} target="_blank" rel="noreferrer">
          Open paper <ExternalLink size={13} aria-hidden="true" />
        </a>
      )}

      {/* The paper's Figure 1 (or Table 1) — an at-a-glance gist shown immediately on select.
          Prefers a pipeline-baked crop (static PNG); falls back to client-side pdf.js when
          none was baked. Keyed by node so it re-fetches per paper; renders nothing when
          neither source yields a figure. */}
      <FirstFigure
        key={selectedNode}
        arxivId={detail?.arxivId ?? null}
        doi={detail?.doi ?? null}
        bakedUrl={bakedFigureUrl(ds, selectedNode)}
      />

      <div
        className="seg details-tabs"
        role="tablist"
        aria-label="Paper detail view"
        onKeyDown={(e) => {
          // Arrow keys move between tabs (standard tabs pattern).
          if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            e.preventDefault();
            setView((v) => (v === "citations" ? "paper" : "citations"));
          }
        }}
      >
        <button
          type="button"
          role="tab"
          id="tab-citations"
          aria-controls="panel-citations"
          tabIndex={view === "citations" ? 0 : -1}
          className={view === "citations" ? "active" : ""}
          aria-selected={view === "citations"}
          onClick={() => setView("citations")}
        >
          <Network size={14} aria-hidden="true" />
          Citations
        </button>
        <button
          type="button"
          role="tab"
          id="tab-paper"
          aria-controls="panel-paper"
          tabIndex={view === "paper" ? 0 : -1}
          className={view === "paper" ? "active" : ""}
          aria-selected={view === "paper"}
          onClick={() => setView("paper")}
        >
          <FileText size={14} aria-hidden="true" />
          Paper
        </button>
      </div>

      {view === "paper" && (
        <div role="tabpanel" id="panel-paper" aria-labelledby="tab-paper">
          <ArxivPreview arxivId={detail?.arxivId ?? null} doi={detail?.doi ?? null} title={p.title} />
        </div>
      )}
      {view === "citations" && (
        <div role="tabpanel" id="panel-citations" aria-labelledby="tab-citations">
          {ds.manifest.corpus.citation_graph_source && <RelevanceSlider />}
          {/* ABOVE the citation network, not below the references list, and not behind a tab.
              Selecting a paper must SHOW related works, because for a recent preprint they are
              the only structural signal there is and for everything else they are the answer
              the citation graph cannot give: papers on the same subject that never cited each
              other. Buried at the bottom of a 1,700 px panel it was never seen; moved to its
              own tab it stopped appearing on selection at all. */}
          <RelatedWorksPanel ds={ds} node={selectedNode} />
          <CitationExplorer ds={ds} node={selectedNode} referenceCount={detail?.referenceCount ?? -1} />
        </div>
      )}
    </div>
  );
}

// Gradual relevance filter for the selected paper's citation network. 0 shows the whole
// network; dragging up hides the least-relevant papers (Connected-Papers coupling +
// co-citation score, computed in useRelevanceScores). Resets to 0 on each new selection.
function RelevanceSlider() {
  const threshold = useStore((s) => s.relevanceThreshold);
  const setThreshold = useStore((s) => s.setRelevanceThreshold);
  const pct = Math.round(threshold * 100);
  return (
    <div className="relevance-slider">
      <label htmlFor="relevance-range" className="relevance-slider-label">
        Relevance filter
        <span className="relevance-slider-value">
          {threshold === 0 ? "all" : `top ${100 - pct}%`}
        </span>
      </label>
      <input
        id="relevance-range"
        type="range"
        min={0}
        max={100}
        step={1}
        value={pct}
        onChange={(e) => setThreshold(Number(e.target.value) / 100)}
        aria-label="Filter citation network by relevance"
      />
      <p className="relevance-slider-hint">
        Hide less-related papers (shared references &amp; co-citations).
      </p>
    </div>
  );
}
