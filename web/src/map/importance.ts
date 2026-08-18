// Pure visual-encoding helpers shared by the map layers and the details panel, extracted
// from usePointsLayer/useEdgeLayer/CitationExplorer so the LOD and citation-importance math
// lives in one testable place (no deck.gl/React dependency). See Features.md's coverage
// table — these are the "LOD reveal" and "citation importance ordering" features.
//
// Two concerns live here:
//   1. Level-of-detail: how many points to reveal at a given zoom (the home view is far too
//      dense to show every point — see usePointsLayer for the measurement).
//   2. Citation importance: a [0,1] weight that drives edge width, opacity, and node size so
//      a selected paper's references/citers read in rank order.

// --- Level of detail -------------------------------------------------------

export const LOD_MIN_VISIBLE = 6000; // never show fewer than this, so the map is never blank
export const LOD_BASE_FRACTION = 0.12; // fraction of the corpus shown at the fit (home) zoom
export const LOD_FULL_OFFSET = 3.5; // zoom offset from fit at which 100% of points show

/**
 * How many of the `n` most-cited points to reveal at the current zoom.
 * Ramps linearly from LOD_BASE_FRACTION at the fit zoom to the whole corpus at
 * +LOD_FULL_OFFSET, clamped to [LOD_MIN_VISIBLE, n]. `forceAll` (a selection or active
 * filter) shows everything so nothing relevant is hidden by LOD.
 */
export function lodVisibleCount(
  n: number,
  relOffset: number,
  forceAll: boolean,
): number {
  if (forceAll) return n;
  const t = Math.min(1, Math.max(0, relOffset) / LOD_FULL_OFFSET);
  const frac = LOD_BASE_FRACTION + (1 - LOD_BASE_FRACTION) * t;
  return Math.min(n, Math.max(LOD_MIN_VISIBLE, Math.round(n * frac)));
}

/** LOD ramp in [0,1]: 0 at the fit zoom, 1 by +LOD_FULL_OFFSET (or when forced). */
export function lodRamp(relOffset: number, forceAll: boolean): number {
  if (forceAll) return 1;
  return Math.min(1, Math.max(0, relOffset) / LOD_FULL_OFFSET);
}

// --- Citation importance ---------------------------------------------------

// How magnitude and rank are blended into the final importance weight. Rank dominates
// slightly so the ordering is always visible even when every linked paper is hugely cited
// (e.g. ResNet's references are all 18K-75K cites, which magnitude alone flattens to ~1.0).
const MAGNITUDE_WEIGHT = 0.45;
const RANK_WEIGHT = 0.55;

/**
 * Importance in [0,1] for the paper at `index` within a citation-sorted fan of `count`
 * papers (index 0 = most cited). `topLogCites` is log1p of the most-cited paper's citations
 * in that fan; `logCites` is log1p of this paper's citations. In/out fans should each pass
 * their own `topLogCites` so the two sides self-scale independently.
 */
export function importanceWeight(
  logCites: number,
  topLogCites: number,
  index: number,
  count: number,
): number {
  const magnitude = topLogCites > 0 ? logCites / topLogCites : 0;
  const rank = count > 1 ? 1 - index / (count - 1) : 1;
  return MAGNITUDE_WEIGHT * magnitude + RANK_WEIGHT * rank;
}


/**
 * Score cutoff for a relevance-slider position, by quantile.
 *
 * `t` is the slider in [0,1] and means "hide the least-relevant t of the network", which is what
 * the label has always claimed. Reading the cutoff out of the sorted score list makes that true
 * regardless of how skewed the underlying scores are — and they are very skewed, because a score
 * is a small integer count divided by the network's maximum.
 */
export function relevanceCutoff(sorted: Float32Array | null | undefined, t: number): number {
  if (t <= 0) return 0;               // 0 means "show everything", including score-0 members
  if (!sorted || sorted.length === 0) return t;
  const idx = Math.min(sorted.length - 1, Math.floor(t * sorted.length));
  // Nudge above the quantile value so papers exactly AT it are hidden, otherwise a tie-heavy
  // distribution (many papers sharing one raw count) ignores the slider entirely.
  return sorted[idx] + 1e-6;
}
