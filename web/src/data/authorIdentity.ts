// Resolving "this author" to every row that is actually them.
//
// OpenAlex issues more than one author record for the same person, and the arXiv-only fallback
// hashes a name per paper, so one human is routinely split across several rows in
// authors.arrow. Measured on the 912k corpus: 190,038 names occupy multiple rows and 576,125 of
// 1,405,248 rows (41.0%) are redundant. "Eliot Xing" is two rows — 6 papers and 1 — so clicking
// his name on a paper that happens to carry the second id shows a single dot and looks broken.
//
// Selecting an author therefore selects every row sharing that exact name. That is the right
// call for a distinctive name and the wrong one for a common one — "Yang Liu" is 464 rows and
// 2,120 papers, which is certainly many different people — so the UI reports how many profiles
// were merged instead of pretending the union is one person. Honest and useful beats silently
// wrong in either direction.

import type { AuthorRow } from "./types";

export interface AuthorIdentity {
  /** Every author row id sharing the selected row's name. */
  ids: number[];
  /** Display name. */
  name: string;
  /** How many distinct rows were merged (1 = unambiguous). */
  profiles: number;
  /** Total papers across the merged rows. */
  papers: number;
}

/** All rows sharing `id`'s name, plus the counts needed to describe the merge honestly. */
export function resolveAuthorIdentity(
  id: number,
  authors: AuthorRow[],
): AuthorIdentity | null {
  const self = authors.find((a) => a.authorId === id);
  if (!self) return null;
  const same = authors.filter((a) => a.name === self.name);
  return {
    ids: same.map((a) => a.authorId),
    name: self.name,
    profiles: same.length,
    papers: same.reduce((sum, a) => sum + a.count, 0),
  };
}

/** Merge `id`'s whole identity into an existing selection, without duplicates. */
export function addAuthorToSelection(
  id: number,
  authors: AuthorRow[],
  current: number[],
): number[] {
  const identity = resolveAuthorIdentity(id, authors);
  const next = new Set(current);
  if (identity) for (const x of identity.ids) next.add(x);
  else next.add(id);
  return Array.from(next);
}
