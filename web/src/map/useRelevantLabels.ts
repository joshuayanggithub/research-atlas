// Which semantic-zoom labels describe what is currently visible.
//
// When the view is restricted — by an org/author/reading-list filter, or by selecting a paper
// (which shows only that paper plus its citation network) — this returns the label ids those
// papers actually populate, so labels over now-empty regions disappear instead of blanketing
// the map. Returns null when nothing is restricted (every label is relevant).
//
// METHOD: exact hierarchy membership. A label IS a hierarchy cell (labels.json ids match the
// cell ids in regions.arrow), and `points.regionLeaf` records the deepest cell each paper
// belongs to, so walking the parent chain gives the exact cell that paper occupies in every
// band. Each visible paper votes for those cells, and a label survives when it covers a
// meaningful share of the visible set.
//
// This replaced nearest-centroid-within-a-radius voting, which was wrong in both directions and
// produced the complaint that started this: filtering to Graham Neubig's 328 papers surfaced
// "Sentiment Analysis and Opinion Mining: Sarcasm Detection" and dropped "Language Models".
// Two compounding causes, both fixed by the change:
//
//   1. A paper voted for the nearest centroid, not the region it is IN. Small regions whose
//      centroid happens to sit in the middle of a dense neighbourhood collected votes from
//      thousands of papers that belong to entirely different regions — 76 of Neubig's papers
//      voted for a 159-paper sarcasm region they are not members of.
//   2. The keep-threshold was a fraction of the REGION's size, so the bigger and more
//      representative a region was, the harder it was to keep: "Language Models" (65,048 papers,
//      213 of Neubig's) needed 1,301 votes and was rejected, while the sarcasm region needed 3.
//      Systematically, the accurate labels lost and the niche ones won.
//
// The threshold is now a share of the VISIBLE set, which also fixes small selections: an author
// with 15 papers previously cleared no threshold anywhere and got no labels at all.

import { useMemo } from "react";
import type { Dataset } from "../data/types";
import type { FilterArrays } from "../filters/useFilterMask";
import { useRegionsReady } from "../data/usePapersReady";
import { useEdgesEpoch } from "../data/useNodeEdges";

// A label must cover at least this share of what you are looking at...
const SHARE = 0.05;
// ...and at least this many papers, so a handful of papers cannot name a region on their own.
const MIN_VOTES = 2;
// ...but a share test alone empties the map for BROAD filters. An organisation with 14,522
// papers spreads them over the whole atlas, so almost no single region reaches 5% and the view
// loses its labels exactly when it most needs orientation. So each band also always keeps its
// top few regions by share: thresholding answers "is this label meaningful?", ranking answers
// "what is this view mostly about?", and a filtered map needs both.
// Raised from 6 after a 6,000-paper organisation showed five labels on the whole map. The
// greedy placement culls overlaps anyway, so this is a candidate budget, not a draw count.
const TOP_PER_BAND = 14;

// Cap on how many visible papers vote. The walk is O(voters x depth) rather than the old
// O(voters x bands x labels-per-band), so this is generous; it only bounds pathological cases
// like a filter matching most of the corpus. A stride keeps the sample spread across the whole
// set rather than favouring low node ids, which correlate with publication date.
const MAX_VOTERS = 20000;

export function useRelevantLabels(
  ds: Dataset | null,
  filter: FilterArrays | null,
  selectedNode: number | null = null,
): Set<number> | null {
  // regions.arrow streams in after first paint; until it lands membership cannot be resolved.
  const edgesEpoch = useEdgesEpoch();
  const regionsReady = useRegionsReady();
  return useMemo(() => {
    if (!ds) return null;
    const filterActive = !!filter?.anyOrgAuthorActive;
    const hasSelection = selectedNode !== null && selectedNode >= 0;
    if (!filterActive && !hasSelection) return null;

    const { parent, level } = ds.regions;
    if (parent.length === 0) return null; // not loaded yet: treat every label as relevant

    // The visible set: a selection shows the paper plus its citation network; a filter shows
    // everything matching. A selection wins, being the tighter restriction and what the map draws.
    const { regionLeaf, count } = ds.points;
    const visibleIdx: number[] = [];
    if (hasSelection) {
      const seen = new Set<number>([
        selectedNode,
        ...(ds.citesOut.get(selectedNode) ?? []),
        ...(ds.citedBy.get(selectedNode) ?? []),
      ]);
      for (const i of seen) if (i >= 0 && i < count) visibleIdx.push(i);
    } else {
      const { matchValue } = filter!;
      for (let i = 0; i < count; i++) if (matchValue[i] === 1) visibleIdx.push(i);
    }
    if (visibleIdx.length === 0) return new Set<number>();

    const stride = Math.max(1, Math.ceil(visibleIdx.length / MAX_VOTERS));
    const votes = new Map<number, number>();
    let voters = 0;
    // Ancestor chains repeat heavily — sibling papers share a leaf — so memoise per leaf cell.
    const chainCache = new Map<number, number[]>();

    for (let v = 0; v < visibleIdx.length; v += stride) {
      const leaf = regionLeaf[visibleIdx[v]];
      if (leaf < 0) continue; // tile not loaded yet; it will vote once it arrives
      voters++;
      let chain = chainCache.get(leaf);
      if (chain === undefined) {
        chain = [];
        let cell = leaf;
        for (let hops = 0; cell >= 0 && cell < level.length && hops < 64; hops++) {
          chain.push(cell);
          cell = parent[cell];
        }
        chainCache.set(leaf, chain);
      }
      for (const cell of chain) votes.set(cell, (votes.get(cell) ?? 0) + 1);
    }
    if (voters === 0) return null; // nothing placed yet — do not blank the map

    const threshold = Math.max(MIN_VOTES, SHARE * voters);
    const relevant = new Set<number>();

    // Group by band so ranking is per zoom level: the coarse bands keep their top regions and so
    // do the fine ones, rather than one band's big numbers crowding out every other.
    const byBand = new Map<number, { cell: number; v: number }[]>();
    for (const [cell, v] of votes) {
      if (v < MIN_VOTES) continue; // never name a region from a single paper
      const band = level[cell];
      const arr = byBand.get(band) ?? [];
      arr.push({ cell, v });
      byBand.set(band, arr);
    }
    for (const arr of byBand.values()) {
      arr.sort((a, b) => b.v - a.v);
      arr.forEach(({ cell, v }, rank) => {
        if (v >= threshold || rank < TOP_PER_BAND) relevant.add(cell);
      });
    }
    return relevant;
    // The selection's network grows as its shard and deeper tiers land; labels chosen
    // from a partial network describe a fraction of what the map is showing.
  }, [ds, filter, selectedNode, regionsReady, edgesEpoch]);
}
