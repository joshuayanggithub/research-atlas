// Which papers belong to a map label's region — exactly.
//
// A label IS a hierarchy cell: `labels.json` ids match `tiles.json` cell ids (label 0 and cell 0
// share a centroid and a count of 79,068). s06 knows precisely which papers each cell contains,
// so the honest answer is a lookup, not a guess.
//
// Shipping every cell's node list would cost ~35 MB (9,116,528 node ids across 285,316 cells).
// Instead s11 emits two small things: `points.region_leaf`, the deepest cell containing each
// paper, and `regions.arrow`, the cell parent chain (285,316 rows, 2.72 MB). Membership at any
// level is then a walk up from the leaf — the hierarchy is a tree, so this is a handful of hops.
//
// An earlier version approximated membership by nearest label centroid within a per-band radius
// (the rule useRelevantLabels uses to decide which labels to DRAW). It under-selected badly:
// clicking "cs.CV: Gaussian Splatting", a region the map itself labels as 31,292 papers,
// selected 1,032 — a 30x undercount, because a centroid ball is not the region's actual shape.

import type { Dataset } from "../data/types";

/** Nodes belonging to any of `labelIds` (union), or null when membership can't be resolved. */
export function nodesInLabels(ds: Dataset, labelIds: number[]): Uint8Array | null {
  if (labelIds.length === 0) return null;
  const { parent, level } = ds.regions;
  // regions.arrow streams in after first paint; until then no region filter can be honoured.
  if (parent.length === 0) return null;

  // Target cells, grouped by the level they live at, so a leaf only has to be walked once.
  const targetsByLevel = new Map<number, Set<number>>();
  for (const id of labelIds) {
    if (id < 0 || id >= level.length) continue;
    const lv = level[id];
    if (lv < 0) continue;
    const set = targetsByLevel.get(lv) ?? new Set<number>();
    set.add(id);
    targetsByLevel.set(lv, set);
  }
  if (targetsByLevel.size === 0) return null;

  const { regionLeaf, count } = ds.points;
  const mask = new Uint8Array(count);

  // Ancestor lookups repeat heavily — sibling papers share a leaf — so memoise per leaf cell.
  const decided = new Map<number, boolean>();

  for (let i = 0; i < count; i++) {
    const leaf = regionLeaf[i];
    if (leaf < 0) continue;
    const cached = decided.get(leaf);
    if (cached !== undefined) {
      if (cached) mask[i] = 1;
      continue;
    }
    let hit = false;
    let cell = leaf;
    // Walk to the root, checking each ancestor against the targets sitting at its level.
    for (let hops = 0; cell >= 0 && cell < level.length && hops < 64; hops++) {
      const set = targetsByLevel.get(level[cell]);
      if (set && set.has(cell)) {
        hit = true;
        break;
      }
      cell = parent[cell];
    }
    decided.set(leaf, hit);
    if (hit) mask[i] = 1;
  }
  return mask;
}
