// The deck.gl map surface: an OrthographicView scatter of papers with semantic-zoom
// labels and on-select citation arcs. Owns the deck viewState and wires interaction back
// to the store.

import DeckGL from "@deck.gl/react";
import { OrthographicView, type OrthographicViewState } from "@deck.gl/core";
import { useCallback, useMemo, useState } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";
import { useFilterMask } from "../filters/useFilterMask";
import { usePointsLayer } from "./layers/usePointsLayer";
import { useLabelLayers } from "./layers/useLabelLayers";
import { useEdgeLayer } from "./layers/useEdgeLayer";

const INITIAL_VIEW_STATE: OrthographicViewState = {
  target: [0, 0, 0],
  zoom: -3,
  minZoom: -6,
  maxZoom: 6,
};

export function MapView({ ds }: { ds: Dataset }) {
  const [viewState, setViewState] = useState<OrthographicViewState>(INITIAL_VIEW_STATE);

  const colorMode = useStore((s) => s.colorMode);
  const orgDisplayMode = useStore((s) => s.orgDisplayMode);
  const edgeMode = useStore((s) => s.edgeMode);
  const filters = useStore((s) => s.filters);
  const selectedNode = useStore((s) => s.selectedNode);
  const hoverNode = useStore((s) => s.hoverNode);
  const selectNode = useStore((s) => s.selectNode);
  const setHover = useStore((s) => s.setHover);
  const setZoom = useStore((s) => s.setZoom);

  const filter = useFilterMask(ds, filters);

  const onViewStateChange = useCallback(
    ({ viewState: vs }: { viewState: OrthographicViewState }) => {
      setViewState(vs);
      setZoom(typeof vs.zoom === "number" ? vs.zoom : (vs.zoom as number[])[0]);
    },
    [setZoom],
  );

  const zoom = typeof viewState.zoom === "number" ? viewState.zoom : -3;

  const pointsLayer = usePointsLayer({
    ds,
    colorMode,
    filter: filter!,
    orgDisplayMode,
    yearMin: filters.yearMin,
    yearMax: filters.yearMax,
    selectedNode,
    hoverNode,
    onClick: selectNode,
    onHover: setHover,
  });

  const labelLayers = useLabelLayers({ labels: ds.labels.labels, levels: ds.manifest.levels, zoom });
  const edgeLayer = useEdgeLayer(ds, selectedNode, edgeMode);

  const layers = useMemo(
    () => [pointsLayer, edgeLayer, ...labelLayers].filter(Boolean),
    [pointsLayer, edgeLayer, labelLayers],
  );

  const bg = ds.manifest.palette.background ?? [12, 14, 20];

  return (
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
  );
}
