// Helpers over the organization directory (orgs.json). The bundle is a flat map of
// institution entries; roots have `parent === null` and list their child unit keys in
// `children`. A unit's `node_ids` is always the ROLLUP set (unit + descendants), so any
// key — root or child — can be unioned directly into the filter mask.

import type { AuthorRow, Dataset, Institution } from "../data/types";

export interface OrgNode {
  key: string;
  inst: Institution;
  children: OrgNode[];
}

/** Curated root org keys (seed orgs, parent === null), preserving orgs.json order. The
 *  directory entries (curated === false) are searchable but not part of the browse tree. */
export function rootOrgKeys(ds: Dataset): string[] {
  return Object.keys(ds.orgs.institutions).filter(
    (k) => ds.orgs.institutions[k].parent === null && ds.orgs.institutions[k].curated,
  );
}

const UNIT_ORDER: Record<string, number> = {
  school: 0,
  department: 1,
  institute: 2,
  research_division: 3,
  lab: 4,
  team: 5,
  site: 6,
  organization: 7,
};

function compareNodes(a: OrgNode, b: OrgNode): number {
  return (UNIT_ORDER[a.inst.unit_type] ?? 99) - (UNIT_ORDER[b.inst.unit_type] ?? 99)
    || a.inst.display_name.localeCompare(b.inst.display_name);
}

/** Build the curated organization tree recursively. A display tree is always acyclic even if
 * a malformed directory artifact accidentally contains a loop. */
export function buildOrgTree(ds: Dataset): OrgNode[] {
  const insts = ds.orgs.institutions;
  const build = (key: string, ancestors: Set<string>): OrgNode | null => {
    const inst = insts[key];
    if (!inst || ancestors.has(key)) return null;
    const nextAncestors = new Set(ancestors).add(key);
    const children = inst.children
      .map((child) => build(child, nextAncestors))
      .filter((child): child is OrgNode => child !== null)
      .sort(compareNodes);
    return { key, inst, children };
  };
  return rootOrgKeys(ds)
    .map((key) => build(key, new Set()))
    .filter((node): node is OrgNode => node !== null)
    .sort((a, b) => a.inst.display_name.localeCompare(b.inst.display_name));
}

/** Search the full institution directory (curated + every corpus institution) by name.
 *  Returns non-curated matches ranked by paper count, excluding curated ones already shown
 *  in the browse tree. Used to surface "any university/company" from the search box. */
export function searchDirectory(ds: Dataset, query: string, limit = 40): OrgNode[] {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  const insts = ds.orgs.institutions;
  const out: OrgNode[] = [];
  for (const key of Object.keys(insts)) {
    const inst = insts[key];
    if (inst.curated) continue; // curated entries appear in the tree groups already
    if (inst.display_name.toLowerCase().includes(q)) {
      out.push({ key, inst, children: [] });
    }
  }
  out.sort((a, b) => b.inst.count - a.inst.count);
  return out.slice(0, limit);
}

/** Stable index of a root org among roots (used for color-by-org hue assignment). */
export function rootOrgIndex(ds: Dataset): Map<string, number> {
  const index = new Map<string, number>();
  rootOrgKeys(ds).forEach((k, i) => index.set(k, i));
  return index;
}

/**
 * Top authors within a set of node ids, ranked by paper count inside the set.
 * Used for organization-scoped researcher browsing.
 */
export function topAuthorsInNodes(
  ds: Dataset,
  nodeIds: number[],
  authors: AuthorRow[],
  limit = 12,
): { authorId: number; name: string; count: number }[] {
  const counts = new Map<number, number>();
  for (const nid of nodeIds) {
    const paper = ds.papers[nid];
    if (!paper) continue;
    for (const aid of paper.authorIds) counts.set(aid, (counts.get(aid) ?? 0) + 1);
  }
  const nameOf = new Map<number, string>();
  for (const a of authors) nameOf.set(a.authorId, a.name);
  return Array.from(counts.entries())
    .map(([authorId, count]) => ({ authorId, count, name: nameOf.get(authorId) ?? `#${authorId}` }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
    .slice(0, limit);
}
