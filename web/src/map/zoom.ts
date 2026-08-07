// Maps a live deck.gl viewport zoom to the active semantic band, and decides which
// label levels are visible. Bands come from manifest.levels (emitted by s07).

import type { LevelBand, PointData } from "../data/types";

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
export function visibleLabelLevels(zoom: number, levels: LevelBand[], base: number): Set<number> {
  const current = bandForZoom(zoom, levels, base);
  const visible = new Set<number>();
  for (const b of levels) {
    if (b.level <= current + 1) visible.add(b.level);
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
