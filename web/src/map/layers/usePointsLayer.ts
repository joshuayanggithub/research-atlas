// The scatterplot of papers. Positions come from points.arrow (x,y); fill color from the
// active color mode; org/author filtering HIDES non-matches (GPU-culled, not clickable);
// the date range is applied on the GPU via DataFilterExtension so slider drags don't
// recompute anything.

import { ScatterplotLayer } from "@deck.gl/layers";
import { DataFilterExtension } from "@deck.gl/extensions";
import { useEffect, useMemo, useState } from "react";
import type { Dataset } from "../../data/types";
import type { ColorMode } from "../../state/store";
import type { FilterArrays } from "../../filters/useFilterMask";
import { baseColor } from "../colors";
import { lodRamp, relevanceCutoff } from "../importance";
import {
  UNLOADED_LEVEL,
  ensurePointTiles,
  ensureEdgeTiles,
  ensurePositionsFor,
  onPointTiles,
} from "../../data/loadArtifacts";
import { CITER, REFERENCE, SELECTED } from "../citationColors";
import { DIR_CITER, DIR_REFERENCE, DIR_SELECTED } from "../useRelevanceScores";


// Upper bound for the reveal-level filter when all points should show (selection/filter).
// Larger than any level s12 emits, but STRICTLY BELOW loadArtifacts.UNLOADED_LEVEL (32767).
// They used to be equal, so "show every level" also un-culled points whose tile had not been
// fetched — those sit at their zeroed defaults and rendered as a black dot at the origin, with
// fitMatching then framing that phantom instead of the real papers.
const MAX_REVEAL_LEVEL = 32766;

interface Args {
  ds: Dataset;
  colorMode: ColorMode;
  filter: FilterArrays;
  monthMin: number;
  monthMax: number;
  selectedNode: number | null;
  hoverNode: number | null;
  // Dense per-node relevance for the current selection (null when nothing selected):
  // score[i] = -1 outside the network, else [0,1]; direction[i] = DIR_* code.
  // `sorted` powers the percentile relevance slider (see map/importance.relevanceCutoff).
  relevance: { score: Float32Array; direction: Uint8Array; sorted?: Float32Array } | null;
  // Hide connected papers scoring below this (0 = whole network .. 1 = most relevant only).
  relevanceThreshold: number;
  zoom: number;
  baseZoom: number;
  /** Viewport width in CSS px — the thinning's on-screen separation scales with it. */
  viewportWidth: number;
  onClick: (nodeId: number | null) => void;
  onHover: (nodeId: number | null, x: number, y: number) => void;
}

// Level-of-detail thresholds and the visible-count / ramp math live in ../importance.ts
// (pure + unit-tested). At the fit view 71k points overlap into a solid mass (~90% within
// 2px of a neighbor, dot diameter 2px), so we show only the most-cited fraction and reveal
// the rest as the user zooms in.
// 99th-percentile citation count for the 2025-2026 corpus (measured: p50=1, p90=8, p99=46,
// max=7,243). The radius ramp is normalised here rather than on the max so outliers do not
// consume the whole visual range.
// Relevance -> opacity for connected papers, matching useEdgeLayer's edge ramp so a node and
// its edge read as the same strength. The floor keeps a weakly-related paper visible rather
// than invisible — it is still part of the citation network.
const REL_ALPHA_MIN = 70;
const REL_ALPHA_MAX = 255;

const CITE_ANCHOR = 46;
const CITE_ANCHOR_LOG = Math.log1p(CITE_ANCHOR);

// How deep the AMBIENT citation web goes. Deliberately shallow.
//
// Edge tiers were originally loaded to whatever depth the zoom reached, which was wrong twice
// over. Cost: tiers 5-9 are 7.4, 17.3, 24.5, 19.4 and 9.8 MB gzipped, so zooming in hard
// pulled up to 83.6 MB and merged millions of edges into the adjacency maps on the main
// thread — the "zooming aggressively janks" report.
//
// And it bought nothing. Zoomed in, every paper is connected to something off-screen, so the
// ambient web degenerates into noise laid over everything; the citation view that is actually
// useful is one paper's network, which comes from its own shard (ensureNodeEdges) and is
// complete regardless of zoom. So the ambient layer keeps the backbone between important
// papers — 400,471 edges, already loaded eagerly at 2.14 MB — and stops there.
const AMBIENT_EDGE_MAX_LEVEL = 4;

export function usePointsLayer({
  ds,
  colorMode,
  filter,
  monthMin,
  monthMax,
  selectedNode,
  hoverNode,
  relevance,
  relevanceThreshold,
  zoom,
  baseZoom,
  viewportWidth,
  onClick,
  onHover,
}: Args) {
  const n = ds.points.count;

  // Level-of-detail via reveal_level (s12 greedy thinning): a point renders only when its
  // reveal_level <= the active level, which GUARANTEES no two visible points overlap at any
  // zoom (each level maintains a minimum on-screen separation). This replaces the old
  // citation-rank budget, which merely limited the count and still let points collide.
  //
  // Level 0 fills at the fit zoom (base_divisor tuned so ~a few hundred points separate by
  // ~1/40 of the span). Each reveal level corresponds to one 2x zoom step, matching how the
  // thinning radius halves per level; +1 headroom keeps the next level's points appearing
  // just before you need them. A selection or active filter forces all levels so nothing
  // connected/matching is hidden.
  // Screen separation guaranteed by the thinning = viewport_width / base_divisor (see
  // radiusMaxPixels below). Keep the dot diameter under ~55% of it so neighbouring points stay
  // visibly distinct, and never exceed the original desktop cap.
  const baseDivisor = ds.manifest.tiling_base_divisor ?? 40;
  const separationPx = viewportWidth / baseDivisor;
  const maxRadiusPixels = Math.max(1.5, Math.min(13, (separationPx * 0.55) / 2));

  const relOffset = Math.max(0, zoom - baseZoom);
  const forceAll = selectedNode !== null || filter.anyOrgAuthorActive;
  // At the fit zoom (relOffset 0) show only level 0 — the sparsest, guaranteed-separated
  // set; each ~1 zoom step in reveals the next level. floor (not +1) so the home view is
  // the calibrated sparse set rather than already two levels deep.
  const activeLevel = forceAll ? MAX_REVEAL_LEVEL : Math.floor(relOffset);

  // Points arrive per reveal level (loadArtifacts.ensurePointTiles): only L0-L4 is on the
  // critical path, so ask for whatever this zoom needs and re-render when it lands.
  const [tileTick, setTileTick] = useState(0);
  useEffect(() => onPointTiles(() => setTileTick((n) => n + 1)), []);
  useEffect(() => {
    if (!forceAll) {
      void ensurePointTiles(activeLevel + 1);
      // Ambient edges stop at AMBIENT_EDGE_MAX_LEVEL — they are NOT followed all the way in.
      void ensureEdgeTiles(Math.min(activeLevel + 1, AMBIENT_EDGE_MAX_LEVEL));
    }
  }, [activeLevel, forceAll]);

  // A filter or selection can reveal papers at ANY depth, and reveal-level tiles are ordered by
  // importance — so an arbitrary selection is scattered across every level and "show my 19
  // papers" used to mean downloading all 43 MB. Fetch the id-keyed shards holding exactly the
  // matched papers instead (~19 x 89 KB); ensurePositionsFor falls back to the full tiles when
  // the selection is broad enough that the shards stop being the cheaper option.
  const matchKey = filter.anyOrgAuthorActive
    ? `${selectedNode ?? -1}:${filter.matchValue.length}`
    : "";
  useEffect(() => {
    if (!forceAll) return;
    const needed: number[] = [];
    const { revealLevel } = ds.points;
    for (let i = 0; i < revealLevel.length; i++) {
      if (revealLevel[i] === UNLOADED_LEVEL && (selectedNode === i || filter.matchValue[i] === 1)) {
        needed.push(i);
      }
    }
    if (needed.length > 0) void ensurePositionsFor(needed);
    // Deliberately NOT keyed on the tile tick: useFilterMask already rebuilds matchValue when
    // tiles land, so its identity is the signal that the matched set moved. Keying on both made
    // every landing shard re-scan a million nodes.
  }, [forceAll, matchKey, filter.matchValue, selectedNode, ds]);

  // Fade + shrink dots at the fit view so the home map reads as airy topic fields rather
  // than a wall of ink; both ramp to full over the first few zoom steps.
  const lodT = lodRamp(relOffset, forceAll);
  const layerOpacity = 0.55 + 0.45 * lodT;
  const radiusScale = 0.72 + 0.28 * lodT;
  // radiusMaxPixels caps the emphasis, so lift the cap too when few papers are shown.

  // Positions and the citation-derived base radius never change, so they are built once per
  // dataset as flat typed arrays and handed to deck.gl as BINARY attributes. Previously they
  // were per-point accessor closures, so deck.gl re-invoked them 912,429 times on every
  // selection/filter change — that attribute rebuild (_normalizeValue/updateBuffer) was the
  // largest remaining JS cost when selecting a paper.
  const geometry = useMemo(() => {
    const positions = new Float32Array(n * 2);
    const baseRadius = new Float32Array(n);
    const { x, y, citedByCount } = ds.points;
    for (let i = 0; i < n; i++) {
      positions[i * 2] = x[i];
      positions[i * 2 + 1] = y[i];
      const c = citedByCount[i];
      const t = Math.log1p(c) / CITE_ANCHOR_LOG;
      baseRadius[i] = t <= 1
        ? 1.0 + 0.6 * t
        : 1.6 + 0.22 * Math.log10(Math.max(c, 1) / CITE_ANCHOR);
    }
    return { positions, baseRadius };
  // tileTick is a DEPENDENCY, not decoration: ds.points.{x,y,r,g,b,revealLevel,monthIndex} are
  // typed arrays that fillPointTile MUTATES IN PLACE as tiles and shards land, so `ds` keeps its
  // identity and a memo keyed on it alone never rebuilds. Papers that arrived late then kept the
  // values emptyPoints zeroed: position (0,0), colour black, revealLevel 32767 — drawn as one
  // black dot at the world origin, and GPU-culled everywhere else. An imported 19-paper reading
  // list rendered 6 dots because of this.
  }, [ds, n, tileTick]);

  // Precompute base RGB per point for the active color mode (cheap; memo on mode).
  const rgb = useMemo(() => {
    const arr = new Uint8Array(n * 3);
    for (let i = 0; i < n; i++) {
      const c = baseColor(ds, i, colorMode, filter.orgOfNode);
      arr[i * 3] = c[0];
      arr[i * 3 + 1] = c[1];
      arr[i * 3 + 2] = c[2];
    }
    return arr;
  }, [ds, colorMode, filter.orgOfNode, n, tileTick]);

  // Any active org/author filter HIDES non-matching papers entirely (GPU-culled below — not
  // drawn, not pickable), so a filtered view shows only the matching set, never a dimmed
  // backdrop. (Previously this was an optional "hide" mode; now it is unconditional.)
  const hideNonMatch = filter.anyOrgAuthorActive;

  // Emphasis for a filtered view. Selecting an author leaves as few as a handful of papers on
  // a full-screen canvas; at their normal ~3px they are technically drawn but effectively
  // invisible, which reads as "the filter did nothing". Scale the dots up as the matching set
  // shrinks — a 7-paper result should look like seven papers, not dust.
  const matchCount = useMemo(() => {
    if (!filter.anyOrgAuthorActive) return -1;
    let k = 0;
    for (let i = 0; i < n; i++) if (filter.matchValue[i] === 1) k++;
    return k;
  }, [filter.matchValue, filter.anyOrgAuthorActive, n]);
  const emphasis =
    matchCount < 0 ? 1
      : matchCount <= 25 ? 4.0
      : matchCount <= 250 ? 3.0
      : matchCount <= 2500 ? 2.0
      : 1.3;
  // Split the citation network by DIRECTION, not just membership. When a paper is selected,
  // channel 2 below culls everything except this network, so the papers left on screen are
  // exactly its references and citers — which makes the node's own colour the clearest place
  // to say which it is. (The edge layer used to answer this with a thin ring drawn over a
  // background-coloured disc, which hid the paper underneath; see ../citationColors.)
  // useRelevanceScores already walked the network and produced dense arrays, so there is no
  // second pass here and no per-point hash lookup in the accessors below.
  const relScore = relevance?.score ?? null;
  const relDir = relevance?.direction ?? null;

  // One pass builds every per-point attribute that varies with selection/filter/hover. This
  // replaces three separate 912k accessor sweeps (fill, radius, filter) with a single loop over
  // typed arrays, and lets deck.gl upload the buffers directly.
  const attributes = useMemo(() => {
    const colors = new Uint8Array(n * 4);
    const radii = new Float32Array(n);
    const filterValues = new Float32Array(n * 4);
    const { monthIndex, revealLevel } = ds.points;
    const { positions, baseRadius } = geometry;
    void positions;
    for (let i = 0; i < n; i++) {
      // --- colour: direction when a paper is selected, else the topic/org/recency hue ---
      let cr = rgb[i * 3];
      let cg = rgb[i * 3 + 1];
      let cb = rgb[i * 3 + 2];
      let ca = 210;
      if (relDir !== null) {
        const d = relDir[i];
        // ALPHA CARRIES RELEVANCE. Every connected paper used to be drawn at a flat 235, so a
        // paper sharing dozens of references with the selection looked exactly as important as
        // one with a single incidental link. The Connected-Papers score already exists per node
        // (useRelevanceScores) and was only being used to CULL via the slider; using it for
        // opacity makes the strongly-related work read first. Same ramp as the edge layer, so a
        // node and the edge touching it agree.
        const rel = relScore !== null && relScore[i] >= 0 ? relScore[i] : 0;
        const relAlpha = REL_ALPHA_MIN + (REL_ALPHA_MAX - REL_ALPHA_MIN) * rel;
        if (d === DIR_SELECTED) { cr = SELECTED[0]; cg = SELECTED[1]; cb = SELECTED[2]; ca = 255; }
        else if (d === DIR_REFERENCE) { cr = REFERENCE[0]; cg = REFERENCE[1]; cb = REFERENCE[2]; ca = relAlpha; }
        else if (d === DIR_CITER) { cr = CITER[0]; cg = CITER[1]; cb = CITER[2]; ca = relAlpha; }
      }
      colors[i * 4] = cr; colors[i * 4 + 1] = cg; colors[i * 4 + 2] = cb;
      colors[i * 4 + 3] = Math.round(ca);

      // --- radius: base ramp with selected/hover/connected bumps ---
      const base = baseRadius[i];
      radii[i] = i === selectedNode ? base * 2.8
        : i === hoverNode ? base * 1.9
        : (relScore !== null && relScore[i] >= 0) ? base * 1.35
        : base * emphasis;

      // --- filter channels: 0 month, 1 org/author match, 2 selection+relevance, 3 LOD ---
      filterValues[i * 4] = monthIndex[i];
      filterValues[i * 4 + 1] = hideNonMatch ? filter.matchValue[i] : 1;
      filterValues[i * 4 + 2] = relScore === null
        ? 1000
        : i === selectedNode
          ? 1000
          : relScore[i] >= 0
            ? Math.round(relScore[i] * 1000)
            : -1;
      filterValues[i * 4 + 3] = revealLevel[i];
    }
    return { colors, radii, filterValues };
  }, [ds, n, rgb, geometry, relDir, relScore, selectedNode, hoverNode, hideNonMatch,
      filter.matchValue, emphasis, tileTick]);

  return new ScatterplotLayer({
    id: "points",
    data: {
      length: n,
      attributes: {
        getPosition: { value: geometry.positions, size: 2 },
        // Colours are unsigned bytes; deck.gl normalises them to 0-1 in the shader. Forcing
        // normalized:false made every point read as saturated white.
        getFillColor: { value: attributes.colors, size: 4 },
        getRadius: { value: attributes.radii, size: 1 },
        getFilterValue: { value: attributes.filterValues, size: 4 },
      },
    },
    opacity: layerOpacity,
    radiusScale,
    // Radius encodes citation count, anchored to where papers ACTUALLY sit rather than to
    // the maximum. Citations are extremely skewed here — 45.6% of papers have 0, the median
    // has 1, p99 is 46, the max is 7,243 — so a ramp normalised on the max spends almost all
    // of its range on the top 1% and leaves half the corpus visually identical.
    //
    // Anchoring at p99 keeps the same overall 2.1x extreme (deliberately restrained, so dense
    // topic regions still read as colour fields and the citation edges carry the visual
    // weight) while giving the middle 90% ~29% more separation:
    //     cites   0     1     3     8    15    46   198   1K    7K
    //     radius  1.00  1.11  1.22  1.34  1.43  1.60  1.74  1.89  2.08
    // selected/hover/connected get a floor bump; filtered-out papers shrink.
    radiusUnits: "common",
    radiusMinPixels: 1,
    // Scaled to the viewport, not a fixed 13.
    //
    // The thinning guarantees a world-space separation of span/base_divisor/2^L at level L, and
    // level L is active at zoom baseZoom+L where span*2^baseZoom ~= viewport width. Those cancel:
    // on-screen separation is a CONSTANT viewport_width / base_divisor, independent of level —
    // but proportional to viewport width. At base_divisor=40 that is 36px on a 1440px desktop
    // (fine against a 26px dot) and only 9.75px on a 390px phone, so the no-overlap contract
    // silently broke on narrow screens and points merged into a mass. Capping the radius at a
    // fraction of that separation restores it on every screen size.
    // With a small filtered set the dot radius already saturates this cap (at zoom 4.4 one
    // world unit is ~21px), so the CAP — not the radius multiplier — is what actually controls
    // how big a filtered paper looks. Raise it when few papers are shown; the overlap contract
    // that motivated the viewport-scaled cap only binds when the map is dense.
    radiusMaxPixels:
      matchCount >= 0 && matchCount <= 250 ? Math.max(maxRadiusPixels, 16)
      : matchCount >= 0 && matchCount <= 2500 ? Math.max(maxRadiusPixels, 12)
      : maxRadiusPixels,
    pickable: true,
    autoHighlight: true,
    highlightColor: [255, 255, 255, 120],
    onClick: (info) => onClick(info.index >= 0 ? info.index : null),
    onHover: (info) => onHover(info.index >= 0 ? info.index : null, info.x, info.y),

    // GPU date filter + org/author hide + selection cull + zoom LOD, all via
    // DataFilterExtension. channel 0 = month index (date filter); channel 1 = org/author
    // match (only in "hide" mode); channel 2 = selection membership (only the selected node
    // + its cited/citing set pass when a paper is selected); channel 3 = reveal_level LOD
    // (a point shows only when its reveal_level <= the active level, guaranteeing no overlap
    // at any zoom). All four are applied on the GPU, so pan/zoom never re-evaluate per point.
    // Channel 2 = selection membership + relevance. With no selection every point passes
    // (1000). With a selection, a connected paper carries its Connected-Papers relevance
    // (score×1000, so the slider's [0,1] threshold maps to [0,1000]); the selected node is
    // pinned to 1000 (always shown); non-connected papers get -1 (always culled). The slider
    // raises the filterRange floor to progressively hide the least-relevant connected papers.
    extensions: [new DataFilterExtension({ filterSize: 4 })],
    filterRange: [
      [monthMin, monthMax],
      [1, 1],
      // Selected node (1000) always passes; connected papers pass when score ≥ threshold.
      // Percentile, not raw score — see relevanceCutoff. A raw cutoff made the slider act like
      // an on/off switch in its first few percent.
      [relScore === null ? 1 : Math.round(relevanceCutoff(relevance?.sorted, relevanceThreshold) * 1000), 1000],
      [0, activeLevel],
    ],
    // No updateTriggers: every varying attribute is now a typed array whose identity changes
    // when it is rebuilt, which is exactly the signal deck.gl needs.
  });
}
