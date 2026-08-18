// Maps a live deck.gl viewport zoom to the active semantic band, and decides which
// label levels are visible. Bands come from manifest.levels (emitted by s07).

import type { LevelBand, PointData } from "../data/types";

// Mirrors loadArtifacts.UNLOADED_LEVEL: a point whose tile is not loaded yet.
const UNLOADED_LEVEL = 32767;

// The orthographic zoom at which the coordinate bounds just fill the viewport. deck.gl
// orthographic zoom is log2: 1 world unit = 2^zoom pixels. Bands from the pipeline are
// OFFSETS from this value, so the map is calibrated at any window size.
export function fitZoom(points: PointData, viewportW: number, viewportH: number): number {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < points.count; i++) {
    const x = points.x[i], y = points.y[i];
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  const spanX = Math.max(maxX - minX, 1e-3);
  const spanY = Math.max(maxY - minY, 1e-3);
  // Fit the larger span with a little margin.
  const zx = Math.log2((viewportW * 0.9) / spanX);
  const zy = Math.log2((viewportH * 0.9) / spanY);
  return Math.min(zx, zy);
}

// Center of the coordinate bounds (deck.gl target).
export function coordsCenter(points: PointData): [number, number] {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < points.count; i++) {
    const x = points.x[i], y = points.y[i];
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  return [(minX + maxX) / 2, (minY + maxY) / 2];
}

// Which single band is "current" for a given zoom. `base` is the fit zoom; pipeline bands
// are offsets from it.
export function bandForZoom(zoom: number, levels: LevelBand[], base: number): number {
  const rel = zoom - base;
  for (const b of levels) {
    if (rel >= b.zoom_min && rel < b.zoom_max) return b.level;
  }
  if (levels.length && rel < levels[0].zoom_min) return levels[0].level;
  return levels.length ? levels[levels.length - 1].level : 0;
}

// Labels of band L become visible once zoom reaches that band's window, AND stay visible
// as you zoom further in ONLY if they are at or coarser than the current band + 1. In
// practice: show all bands whose level <= currentBand + 1, so coarse labels persist as
// context and one finer band starts to reveal. The CPU screen-space pass then declutters.
export function visibleLabelLevels(
  zoom: number,
  levels: LevelBand[],
  base: number,
  // A filter or selection is active, so the map is showing a small subset of the corpus.
  restricted = false,
): Set<number> {
  const current = bandForZoom(zoom, levels, base);
  const visible = new Set<number>();
  // The band gate exists to stop a MILLION-paper map drowning in text. With a filter active
  // that pressure is gone — 6,000 papers leave most of the screen empty — while the need for
  // labels is higher, because the user is asking "where does this work sit?". Coarse bands alone
  // answer that with 5 labels for a whole organisation. Offer every finer band as a candidate
  // and let the greedy collision placement decide: it already rejects overlaps, so extra
  // candidates fill empty space and cannot clutter what is already occupied.
  const reach = restricted ? levels.length : current + 1;
  for (const b of levels) {
    if (b.level <= reach) visible.add(b.level);
  }
  // Always show band 0 as the base map layer.
  visible.add(0);
  return visible;
}

// Font size (px) for a label band relative to the current zoom band. Coarser bands are
// larger; a band at the current zoom is emphasized.
export function labelSizeForBand(band: number, currentBand: number): number {
  const delta = currentBand - band; // >0 means this band is coarser than current
  const base = 16;
  return Math.max(11, base + delta * 3);
}

/** Camera target + zoom that frames just the points passing `match` (value 1).
 *
 * Filtering the map is only half of "show me this author's work" — if their papers sit in a
 * corner, or are too sparse to see at the current zoom, the user is left staring at an
 * apparently empty map. Returns null when nothing matches or the set is a single point with no
 * extent to frame.
 */
export function fitMatching(
  points: PointData,
  match: Float32Array | Uint8Array,
  viewportW: number,
  viewportH: number,
  maxZoom: number,
  // Chrome that OVERLAYS the map: the filter sidebar on the left and the header/filter bar on
  // top. Fitting to the whole window put matched papers underneath them — for an imported
  // reading list, "see all of it at once" is the entire point, so a dot hidden behind the bar
  // is a failed fit, not a cosmetic issue.
  insetLeft = 0,
  insetTop = 0,
): { target: [number, number]; zoom: number; count: number } | null {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, n = 0;
  for (let i = 0; i < points.count; i++) {
    if (match[i] !== 1) continue;
    // Skip points whose tile has not arrived: their coordinates are still zeroed, and
    // including them drags the frame toward the origin.
    if (points.revealLevel[i] >= UNLOADED_LEVEL) continue;
    const x = points.x[i], y = points.y[i];
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
    n++;
  }
  if (n === 0) return null;
  const target: [number, number] = [(minX + maxX) / 2, (minY + maxY) / 2];
  // A lone paper (or a tight cluster) has no meaningful span; fall back to a readable zoom
  // rather than dividing by ~0 and slamming to maxZoom.
  const spanX = maxX - minX;
  const spanY = maxY - minY;
  if (spanX < 1e-3 && spanY < 1e-3) return { target, zoom: maxZoom - 2, count: n };
  // Fit into the VISIBLE rectangle, not the window.
  const usableW = Math.max(120, viewportW - insetLeft);
  const usableH = Math.max(120, viewportH - insetTop);
  const zx = Math.log2((usableW * 0.8) / Math.max(spanX, 1e-3));
  const zy = Math.log2((usableH * 0.8) / Math.max(spanY, 1e-3));
  const zoom = Math.min(zx, zy, maxZoom);
  // deck.gl centres `target` in the WINDOW, so shift it to centre the content in the visible
  // rectangle instead: half the inset, converted to world units at this zoom. Both signs are
  // negative because OrthographicView defaults to flipY, so world y grows DOWNWARD on screen —
  // adding to target.y moved the content up, the opposite of what the top inset needs.
  const scale = Math.pow(2, zoom);
  return {
    target: [target[0] - insetLeft / 2 / scale, target[1] - insetTop / 2 / scale],
    zoom,
    count: n,
  };
}
