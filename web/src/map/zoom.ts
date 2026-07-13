// Maps a live deck.gl viewport zoom to the active semantic band, and decides which
// label levels are visible. Bands come from manifest.levels (emitted by s07).

import type { LevelBand } from "../data/types";

// Which single band is "current" for a given zoom (used for point sizing/emphasis).
export function bandForZoom(zoom: number, levels: LevelBand[]): number {
  for (const b of levels) {
    if (zoom >= b.zoom_min && zoom < b.zoom_max) return b.level;
  }
  // Below the coarsest / above the finest → clamp.
  if (levels.length && zoom < levels[0].zoom_min) return levels[0].level;
  return levels.length ? levels[levels.length - 1].level : 0;
}

// Labels of band L become visible once zoom reaches that band's window, AND stay visible
// as you zoom further in ONLY if they are at or coarser than the current band + 1. In
// practice: show all bands whose level <= currentBand + 1, so coarse labels persist as
// context and one finer band starts to reveal. CollisionFilter then declutters.
export function visibleLabelLevels(zoom: number, levels: LevelBand[]): Set<number> {
  const current = bandForZoom(zoom, levels);
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
