// Describe a filtered set of papers in a few words.
//
// Selecting an author leaves you looking at their papers with no summary of what they work on.
// The map's region labels do not help: they describe whichever regions the papers happen to
// fall in, so a roboticist with seven papers gets whatever coarse areas contain them, not a
// characterisation of the seven.
//
// So compute one directly, the same way s07 labels a region: take the terms that are frequent
// IN the set and rare OUTSIDE it (c-TF-IDF). Titles only — abstracts are not shipped to the
// browser — which is enough because paper titles are unusually information-dense.
//
// Background document frequency comes from a STRIDE SAMPLE of the corpus, not all 912,429
// titles. IDF only needs to rank terms by rarity, and a ~60k sample settles that ordering while
// keeping the pass cheap enough to run on the main thread; a stride (rather than a head slice)
// avoids biasing the sample toward one era, since node ids correlate with publication date.

import { useMemo } from "react";
import type { Dataset } from "../data/types";

const BACKGROUND_SAMPLE = 60000;
const MAX_TERMS = 4;
const MIN_SET = 3; // below this a "topic" is noise, not a summary

// Words that carry no topical signal in a CS paper title. Deliberately short: over-pruning
// removes real signal ("learning", "network" ARE what a lot of this corpus is about), so this
// only covers function words and paper-boilerplate.
const STOP = new Set([
  "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "via", "using", "from",
  "by", "at", "as", "is", "are", "be", "can", "we", "our", "their", "its", "it", "this", "that",
  "these", "those", "towards", "toward", "into", "over", "under", "between", "through",
  "approach", "approaches", "method", "methods", "framework", "frameworks", "novel", "new",
  "study", "analysis", "based", "case", "paper", "papers", "work", "works", "results",
  "improving", "improved", "efficient", "effective", "robust", "simple", "fast", "deep",
  "learning", "model", "models", "network", "networks", "system", "systems", "data",
]);

function tokenize(title: string): string[] {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 2 && !STOP.has(w));
}

/** Unigrams plus adjacent bigrams — bigrams are what make a label read like a topic. */
function terms(title: string): string[] {
  const w = tokenize(title);
  const out: string[] = [...w];
  for (let i = 0; i + 1 < w.length; i++) out.push(`${w[i]} ${w[i + 1]}`);
  return out;
}

/** Corpus-wide document frequency, sampled. Memoised per dataset. */
function useBackground(ds: Dataset, ready: boolean) {
  return useMemo(() => {
    if (!ready) return null;
    const n = ds.papers.length;
    if (n === 0) return null;
    const stride = Math.max(1, Math.ceil(n / BACKGROUND_SAMPLE));
    const df = new Map<string, number>();
    let docs = 0;
    for (let i = 0; i < n; i += stride) {
      const title = ds.papers[i]?.title;
      if (!title) continue;
      docs++;
      for (const t of new Set(terms(title))) df.set(t, (df.get(t) ?? 0) + 1);
    }
    return docs > 0 ? { df, docs } : null;
  }, [ds, ready]);
}

/** A few distinctive terms for `nodes`, or [] when there is nothing meaningful to say. */
export function useSetLabel(ds: Dataset, nodes: number[], ready: boolean): string[] {
  const background = useBackground(ds, ready);
  return useMemo(() => {
    if (!background || nodes.length < MIN_SET) return [];
    const { df, docs } = background;

    const tf = new Map<string, number>();
    let counted = 0;
    for (const node of nodes) {
      const title = ds.papers[node]?.title;
      if (!title) continue;
      counted++;
      for (const t of new Set(terms(title))) tf.set(t, (tf.get(t) ?? 0) + 1);
    }
    if (counted < MIN_SET) return [];

    const scored: { term: string; score: number }[] = [];
    for (const [term, freq] of tf) {
      // Appearing in one paper of a set is an accident, not a theme.
      if (freq < 2 && counted > 4) continue;
      const idf = Math.log(1 + docs / (1 + (df.get(term) ?? 0)));
      scored.push({ term, score: (freq / counted) * idf });
    }
    scored.sort((a, b) => b.score - a.score);

    // Drop a term already covered by a higher-ranked bigram (or vice versa) so the label reads
    // as distinct ideas rather than "gaussian, splatting, gaussian splatting".
    const chosen: string[] = [];
    for (const { term } of scored) {
      if (chosen.some((c) => c.includes(term) || term.includes(c))) continue;
      chosen.push(term);
      if (chosen.length >= MAX_TERMS) break;
    }
    return chosen;
  }, [ds, nodes, background]);
}
