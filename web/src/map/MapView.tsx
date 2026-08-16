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
import { useFilterMask } from "../filters/useFilterMask";
import { usePointsLayer } from "./layers/usePointsLayer";
import { useLabelLayers } from "./layers/useLabelLayers";
import { useEdgeLayer } from "./layers/useEdgeLayer";
import { useRelevantLabels } from "./useRelevantLabels";
import { useRelevanceScores } from "./useRelevanceScores";
import { coordsCenter, fitZoom } from "./zoom";

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
  const selectNode = useStore((s) => s.selectNode);
  const setHover = useStore((s) => s.setHover);
  const setZoom = useStore((s) => s.setZoom);
  const relevanceThreshold = useStore((s) => s.relevanceThreshold);

  const filter = useFilterMask(ds, filters);
  const relevantLabelIds = useRelevantLabels(ds, filter, selectedNode);
  const relevance = useRelevanceScores(ds, selectedNode);

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

  const pointsLayer = usePointsLayer({
    ds,
    colorMode,
    filter: filter!,
    monthMin: filters.monthMin,
    monthMax: filters.monthMax,
    selectedNode,
    hoverNode,
    relevance: relevance?.score ?? null,
    relevanceThreshold,
    zoom,
    baseZoom: base.zoom,
    onClick: selectNode,
    onHover: onHoverNode,
  });

  const labelLayers = useLabelLayers({
    labels: ds.labels.labels,
    levels: ds.manifest.levels,
    zoom,
    base: base.zoom,
    viewport,
    relevantLabelIds,
    focusedLabelId: focusedLabel?.id ?? null,
    onHover: onHoverLabel,
  });
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
    relevance: relevance?.score ?? null,
    relevanceThreshold,
    onSelect: selectNode,
    onHover: onHoverNode,
  });

  const layers = useMemo(
    () => [
      ...edgeLayers.background,
      pointsLayer,
      ...edgeLayers.foreground,
      ...labelLayers,
    ].filter(Boolean),
    [pointsLayer, edgeLayers, labelLayers],
  );

  const bg = ds.manifest.palette.background ?? [7, 9, 13];

  // Lightweight hover card: paper metadata straight from the in-memory index (no fetch).
  const hoverPaper =
    hoverNode !== null && hoverNode !== selectedNode ? ds.papers[hoverNode] : null;
  const hoverLabel =
    hoverLabelId !== null ? ds.labels.labels.find((l) => l.id === hoverLabelId) ?? null : null;

  return (
    <>
      <DeckGL
        views={new OrthographicView({ id: "ortho" })}
        viewState={viewState}
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
      {hoverPaper && hoverPos && (
        <div
          className="node-tooltip"
          style={{
            // Flip to the left/up near the right/bottom edges so the card stays on screen.
            left: Math.min(hoverPos.x + 14, viewportSize.width - 312),
            top: Math.min(hoverPos.y + 14, viewportSize.height - 130),
          }}
        >
          <div className="node-tooltip-title">{hoverPaper.title}</div>
          {/* Author names / venue are lazy per-paper detail; the hover card uses only the
              resident index (title, year, citations). Full metadata shows on selection. */}
          <div className="node-tooltip-meta">
            {hoverPaper.publicationDate?.slice(0, 4) || "—"} ·{" "}
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
