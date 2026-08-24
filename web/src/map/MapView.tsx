// The deck.gl map surface: an OrthographicView scatter of papers with semantic-zoom
// labels, sampled global citations, and selected-paper citation links. Owns the deck
// viewState and wires interaction back to the store.

import DeckGL from "@deck.gl/react";
import {
  OrthographicView,
  OrthographicViewport,
  type OrthographicViewState,
} from "@deck.gl/core";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";
import { PaperTitle } from "../panels/PaperTitle";
import { useHoverAuthors } from "../panels/useHoverAuthors";
import { useCompareMask } from "../filters/useCompareMask";
import { PaperYear } from "../panels/PaperYear";
import { useTitles } from "../data/useTitles";
import { useEdgesReady, usePapersReady } from "../data/usePapersReady";
import { onPointTiles } from "../data/loadArtifacts";
import { useFilterMask } from "../filters/useFilterMask";
import { usePointsLayer } from "./layers/usePointsLayer";
import { useLabelLayers } from "./layers/useLabelLayers";
import { useEdgeLayer } from "./layers/useEdgeLayer";
import { useRelevantLabels } from "./useRelevantLabels";
import { useRelevanceScores } from "./useRelevanceScores";
import { coordsCenter, fitMatching, fitZoom } from "./zoom";

// Left edge of the split on desktop: the sidebar is 296px at left:16, and a pane drawn
// underneath it is a pane you cannot read.
const SIDEBAR_INSET = 328;

export function MapView({ ds }: { ds: Dataset }) {
  const [viewportSize, setViewportSize] = useState(() => ({
    width: window.innerWidth || 1200,
    height: window.innerHeight || 700,
  }));

  useEffect(() => {
    const onResize = () =>
      setViewportSize({
        width: window.innerWidth || 1200,
        height: window.innerHeight || 700,
      });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Calibrate the initial view to the data + viewport once (the "fit" zoom is the base
  // that pipeline band offsets are measured from).
  const base = useMemo(() => {
    return {
      zoom: fitZoom(ds.points, viewportSize.width, viewportSize.height),
      center: coordsCenter(ds.points),
    };
  }, [ds, viewportSize]);

  // Let users zoom in far enough to reach the deepest label band (micro-clusters). Derive
  // the max zoom offset from the emitted bands so it always covers them, plus headroom.
  const maxZoomOffset = useMemo(() => {
    const deepest = ds.manifest.levels.reduce((m, b) => Math.max(m, b.zoom_max), 0);
    return Math.max(8, deepest + 1.5);
  }, [ds.manifest.levels]);

  const [viewState, setViewState] = useState<OrthographicViewState>(() => ({
    target: [base.center[0], base.center[1], 0],
    zoom: base.zoom,
    minZoom: base.zoom - 2,
    maxZoom: base.zoom + maxZoomOffset,
  }));

  const colorMode = useStore((s) => s.colorMode);
  const edgeMode = useStore((s) => s.edgeMode);
  const showCitationEdges = useStore((s) => s.showCitationEdges);
  const filters = useStore((s) => s.filters);
  const selectedNode = useStore((s) => s.selectedNode);
  const focusedLabel = useStore((s) => s.focusedLabel);
  const hoverNode = useStore((s) => s.hoverNode);
  const compare = useStore((s) => s.compare);
  const selectNode = useStore((s) => s.selectNode);
  const focusLabel = useStore((s) => s.focusLabel);
  const toggleLabel = useStore((s) => s.toggleLabel);
  const setHover = useStore((s) => s.setHover);
  const setZoom = useStore((s) => s.setZoom);
  const relevanceThreshold = useStore((s) => s.relevanceThreshold);

  const filter = useFilterMask(ds, filters);
  const relevantLabelIds = useRelevantLabels(ds, filter, selectedNode);
  const relevance = useRelevanceScores(ds, selectedNode);
  // Titles stream in after first paint; re-render the hover card when they arrive.
  usePapersReady();
  // Citation links also arrive after first paint; rebuild the layers when they do.
  useEdgesReady();

  useEffect(() => {
    if (selectedNode === null || selectedNode < 0 || selectedNode >= ds.points.count) return;
    setViewState((current) => {
      const currentZoom =
        typeof current.zoom === "number" ? current.zoom : (current.zoom as number[])[0];
      const zoom = Math.max(currentZoom, base.zoom + 1.5);
      // On small screens the details sheet occupies the lower half; place the selected
      // paper in the visible map area above it.
      const mobileYOffset =
        viewportSize.width <= 720 ? (viewportSize.height * 0.22) / 2 ** zoom : 0;
      return {
        ...current,
        target: [
          ds.points.x[selectedNode],
          ds.points.y[selectedNode] - mobileYOffset,
          0,
        ],
        zoom,
        minZoom: base.zoom - 2,
        maxZoom: base.zoom + maxZoomOffset,
      };
    });
  }, [base.zoom, maxZoomOffset, ds, selectedNode, viewportSize]);

  // Label search navigates to the label centroid at the zoom band where that semantic
  // region is meant to be read. The request id deliberately retriggers repeated choices.
  useEffect(() => {
    if (!focusedLabel) return;
    const band = ds.manifest.levels.find((candidate) => candidate.level === focusedLabel.level);
    const relativeZoom = band ? band.zoom_min + 0.35 : focusedLabel.level * 1.2;
    const zoom = Math.min(base.zoom + maxZoomOffset, base.zoom + relativeZoom);
    setViewState((current) => ({
      ...current,
      target: [focusedLabel.x, focusedLabel.y, 0],
      zoom,
      minZoom: base.zoom - 2,
      maxZoom: base.zoom + maxZoomOffset,
    }));
    setZoom(zoom);
  }, [base.zoom, ds.manifest.levels, focusedLabel, maxZoomOffset, setZoom]);

  // Frame the matching papers whenever an org/author filter turns on or changes.
  //
  // Filtering alone left the camera wherever it was, so picking an author often showed an
  // apparently empty map — their papers were off-screen, or too sparse to notice at the
  // current zoom. Keyed on the mask identity so it fires once per filter change, not on pan.
  // Every facet you PICK, not every facet. Orgs, authors and imported reading lists are
  // selections whose members can sit anywhere on the map, so the camera has to go find them —
  // an imported 19-paper list otherwise loads correctly and renders correctly while staying
  // invisible, because 19 dots scattered across a million-paper map at the home view are single
  // pixels you would have to hunt for. Citation and date ranges are deliberately excluded: they
  // are dragged continuously, and re-framing on every step would yank the view around. Map
  // labels are excluded too — focusLabel already moves the camera for those.
  const framedKey = [
    filters.orgKeys.join(","),
    filters.authorIds.join(","),
    filters.readingLists.join(","),
  ].join("|");
  // Matching papers can live in point tiles that have not arrived yet, so a filter frames only
  // what is loaded and then looks wrong ("I selected an author and see one dot"). Re-fit as
  // deeper tiles land until the framed set stops growing.
  const [tileEpoch, setTileEpoch] = useState(0);
  useEffect(() => onPointTiles(() => setTileEpoch((n) => n + 1)), []);
  useEffect(() => {
    if (!filter?.anyOrgAuthorActive) return;
    if (
      filters.orgKeys.length === 0 &&
      filters.authorIds.length === 0 &&
      filters.readingLists.length === 0
    ) {
      return;
    }
    // The sidebar is an overlay on desktop only (on mobile it lives behind a toggle); the
    // header + filter bar overlay at every width.
    const sidebarInset = viewportSize.width >= 900 ? 320 : 0;
    const fit = fitMatching(
      ds.points, filter.matchValue, viewportSize.width, viewportSize.height,
      base.zoom + maxZoomOffset, sidebarInset, 110,
    );
    if (!fit) return;
    setViewState((current) => ({
      ...current,
      target: [fit.target[0], fit.target[1], 0],
      zoom: fit.zoom,
      minZoom: base.zoom - 2,
      maxZoom: base.zoom + maxZoomOffset,
    }));
    setZoom(fit.zoom);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [framedKey, filter?.matchValue, tileEpoch]);

  const onViewStateChange = useCallback(
    ({ viewState: vs }: { viewState: OrthographicViewState }) => {
      setViewState(vs);
      setZoom(typeof vs.zoom === "number" ? vs.zoom : (vs.zoom as number[])[0]);
    },
    [setZoom],
  );

  const zoom = typeof viewState.zoom === "number" ? viewState.zoom : base.zoom;

  // Build a viewport from the live view state so labels can declutter in screen space.
  const viewport = useMemo(() => {
    return new OrthographicViewport({
      width: viewportSize.width,
      height: viewportSize.height,
      target: viewState.target as [number, number, number],
      zoom: viewState.zoom as number,
    });
  }, [viewState.target, viewState.zoom, viewportSize]);

  const [hoverPos, setHoverPos] = useState<{ x: number; y: number } | null>(null);
  const onHoverNode = useCallback(
    (id: number | null, x: number, y: number) => {
      setHover(id);
      setHoverPos(id !== null ? { x, y } : null);
    },
    [setHover],
  );

  const [hoverLabelId, setHoverLabelId] = useState<number | null>(null);
  const [hoverLabelPos, setHoverLabelPos] = useState<{ x: number; y: number } | null>(null);
  const onHoverLabel = useCallback((id: number | null, x: number, y: number) => {
    setHoverLabelId(id);
    setHoverLabelPos(id !== null ? { x, y } : null);
  }, []);

  // Declared before the layers that read them.
  const compareMask = useCompareMask(ds, compare.a, compare.b);
  const compareActive = compare.a !== null && compare.b !== null;

  const pointsLayers = usePointsLayer({
    ds,
    colorMode,
    filter: filter!,
    monthMin: filters.monthMin,
    monthMax: filters.monthMax,
    selectedNode,
    hoverNode,
    relevance,
    relevanceThreshold,
    zoom,
    baseZoom: base.zoom,
    viewportWidth: viewportSize.width,
    onClick: selectNode,
    onHover: onHoverNode,
    compareMask: compareMask?.value ?? null,
  });

  // Labels per pane. A pane showing one researcher's work must be named for THAT work —
  // otherwise the split shows two identical labellings of the whole corpus, most of them
  // hovering over regions the pane has no papers in.
  const labelIdsA = useRelevantLabels(ds, filter, null, compareMask?.value ?? null, "a");
  const labelIdsB = useRelevantLabels(ds, filter, null, compareMask?.value ?? null, "b");

  const labelArgs = {
    labels: ds.labels.labels,
    levels: ds.manifest.levels,
    zoom,
    base: base.zoom,
    viewport,
    relevantLabelIds,
    focusedLabelId: focusedLabel?.id ?? null,
    onHover: onHoverLabel,
    // Clicking a topic label navigates to that region through the same store action the
    // search box uses, so a label reached by clicking and one reached by searching end up
    // in an identical state (centred, zoomed to its band, highlighted, kept through the
    // label declutter pass).
    // Clicking a label both navigates to it AND selects the papers under it, so "show me
    // World Models" is one click. Clicking it again removes the facet (toggle), and the chip
    // in the ActiveFilters bar is the other way out.
    onClick: (label: (typeof ds.labels.labels)[number]) => {
      focusLabel(label);
      toggleLabel(label.id);
    },
  };
  const labelLayers = useLabelLayers(labelArgs);
  const labelLayersA = useLabelLayers({ ...labelArgs, relevantLabelIds: labelIdsA, idSuffix: "-a" });
  const labelLayersB = useLabelLayers({ ...labelArgs, relevantLabelIds: labelIdsB, idSuffix: "-b" });
  const edgeLayers = useEdgeLayer({
    ds,
    selectedNode,
    edgeMode,
    show: showCitationEdges,
    zoom,
    baseZoom: base.zoom,
    filter: filter!,
    monthMin: filters.monthMin,
    monthMax: filters.monthMax,
    relevance,
    relevanceThreshold,
    onSelect: selectNode,
    onHover: onHoverNode,
    comparing: compareActive,
  });

  const layers = useMemo(
    () => [
      ...edgeLayers.background,
      ...pointsLayers,
      ...edgeLayers.foreground,
      ...(compareActive ? [...labelLayersA, ...labelLayersB] : labelLayers),
    ].filter(Boolean),
    [pointsLayers, edgeLayers, labelLayers, labelLayersA, labelLayersB, compareActive],
  );

  const bg = ds.manifest.palette.background ?? [7, 9, 13];

  // Hover card: title/year/citations come straight from the resident index (no fetch); author
  // names come from the paper's detail shard, which is cached per block (see useHoverAuthors).
  // Two-sided comparison: two panes on ONE canvas, sharing one viewState so the same region
  // of semantic space sits at the same screen position in both. Unlinked cameras would make
  // the panes incomparable, which is the whole point of the split.
  const compareViews = useMemo(() => {
    if (!compareActive) return new OrthographicView({ id: "ortho" });
    // Stack vertically on a narrow screen; side by side otherwise. Either way it is small
    // multiples — the panes must be the same size and share a camera.
    const stacked = viewportSize.width < 820;
    if (stacked) {
      return [
        new OrthographicView({ id: "a", y: 0, height: "50%" }),
        new OrthographicView({ id: "b", y: "50%", height: "50%" }),
      ];
    }
    // Start the split clear of the sidebar. Pane A was drawing underneath it, so half the
    // comparison sat behind a filter panel. The panes stay EQUAL width — unequal panes stop
    // being small multiples, which is the entire reason for the split.
    const inset = SIDEBAR_INSET;
    const paneWidth = Math.max(160, (viewportSize.width - inset) / 2);
    return [
      new OrthographicView({ id: "a", x: inset, width: paneWidth }),
      new OrthographicView({ id: "b", x: inset + paneWidth, width: paneWidth }),
    ];
  }, [compareActive, viewportSize.width]);

  const hoverPaper =
    hoverNode !== null && hoverNode !== selectedNode ? ds.papers[hoverNode] : null;
  const hoverAuthors = useHoverAuthors(ds, hoverPaper ? hoverNode : null);
  // Titles are per-node shards; the hover card needs this one. The shard is 72 KB and is the
  // same block the next hovered paper will almost certainly fall in.
  useTitles(hoverNode !== null && hoverNode >= 0 ? [hoverNode] : []);
  const hoverLabel =
    hoverLabelId !== null ? ds.labels.labels.find((l) => l.id === hoverLabelId) ?? null : null;

  return (
    <>
      <DeckGL
        views={compareViews as never}
        // Each pane draws only its own points layer; labels and edges are shared context and
        // render in both.
        layerFilter={({ layer, viewport }) =>
          layer.id.endsWith("-a") ? viewport.id === "a"
          : layer.id.endsWith("-b") ? viewport.id === "b"
          : true}
        viewState={viewState as never}
        onViewStateChange={onViewStateChange as never}
        controller={{ doubleClickZoom: false }}
        layers={layers as never}
        style={{ background: `rgb(${bg[0]},${bg[1]},${bg[2]})` }}
        getCursor={({ isHovering }) => (isHovering ? "pointer" : "grab")}
        onClick={(info) => {
          // Click on empty space clears selection.
          if (!info.layer) selectNode(null);
        }}
      />
      {/* Pane labels. Permanent, not on hover: a split screenshot with unlabelled halves is
          unattributable, and the per-pane count is what keeps a shared camera honest — if one
          side's work sits outside the current view, the count still says it exists.
          Centred in each pane rather than pinned left, which put pane A behind the sidebar. */}
      {compareActive && compare.a && compare.b && (
        <div className="compare-pane-labels" aria-hidden="true">
          <span className="compare-divider" style={
            viewportSize.width < 820
              ? { left: 0, right: 0, top: "50%", height: 1 }
              : { top: 0, bottom: 0,
                  left: SIDEBAR_INSET + (viewportSize.width - SIDEBAR_INSET) / 2, width: 1 }
          } />
          {([["a", compare.a, compareMask?.counts.a], ["b", compare.b, compareMask?.counts.b]] as const)
            .map(([slot, side, count], i) => (
              <span
                key={slot}
                className="compare-pane-label"
                // Left-aligned inside each pane, not centred: centring put pane A behind the
                // sidebar and pane B behind the comparison panel. Pane A clears the sidebar,
                // pane B starts just past the seam, which is well clear of the panel.
                // Mobile pane A sits BELOW the search box (which owns the top strip), not at
                // the same y as on desktop where the sidebar is a column instead of a banner.
                style={viewportSize.width < 820
                  ? { left: 12, top: i === 0 ? 124 : "calc(50% + 10px)" }
                  : {
                      left: SIDEBAR_INSET + 12
                        + (i === 0 ? 0 : (viewportSize.width - SIDEBAR_INSET) / 2),
                      top: 74,
                    }}
              >
                <span className={`compare-tag compare-tag-${slot}`}>{slot.toUpperCase()}</span>
                {side.label}
                <span className="subtle">
                  {compareMask?.pending ? "…" : count?.toLocaleString()}
                </span>
              </span>
            ))}
        </div>
      )}
      {hoverPaper && hoverPos && (
        <div
          className="node-tooltip"
          style={{
            // Flip to the left/up near the right/bottom edges so the card stays on screen.
            left: Math.min(hoverPos.x + 14, viewportSize.width - 312),
            top: Math.min(hoverPos.y + 14, viewportSize.height - 130),
          }}
        >
          <div className="node-tooltip-title"><PaperTitle title={hoverPaper.title} node={hoverNode ?? undefined} /></div>
          {/* Authors arrive a beat after the title (detail shard). Rendered only once present —
              never as an em dash or an empty line, which would read as "this paper has no
              authors" rather than "not fetched yet". */}
          {hoverAuthors && hoverAuthors.length > 0 && (
            <div className="node-tooltip-authors">
              {hoverAuthors.slice(0, 3).join(", ")}
              {hoverAuthors.length > 3 && ` +${hoverAuthors.length - 3}`}
            </div>
          )}
          <div className="node-tooltip-meta">
            <PaperYear paper={hoverPaper} /> ·{" "}
            {ds.manifest.corpus.citation_count_source && hoverPaper.citationCountAvailable
              ? `${hoverPaper.citedByCount.toLocaleString()} cites`
              : "citation count unavailable"}
          </div>
        </div>
      )}
      {hoverLabel && hoverLabelPos && (
        <div
          className="node-tooltip"
          style={{
            left: Math.min(hoverLabelPos.x + 14, viewportSize.width - 312),
            top: Math.min(hoverLabelPos.y + 14, viewportSize.height - 130),
          }}
        >
          <div className="node-tooltip-title">{hoverLabel.text}</div>
          <div className="node-tooltip-meta">
            {hoverLabel.count.toLocaleString()} paper{hoverLabel.count === 1 ? "" : "s"}
          </div>
        </div>
      )}
    </>
  );
}
