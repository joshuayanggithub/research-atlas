// Import a reader's own library and match it against the corpus.
//
// The map answers "what exists"; a reading list answers "what have I actually read", and the
// interesting question is where those two overlap — which regions you have covered, and which
// you have never touched. So the import is a filter facet like any other, not a separate view.
//
// FORMAT. The canonical input is CSL-JSON, the interchange format Zotero, Mendeley, Paperpile
// and Pandoc all speak, optionally inside an envelope that adds list membership (see
// tools/zotero_export.py). Three shapes are accepted, because a user who exports straight from
// Zotero should not have to care which one they picked:
//   1. {format: "research-atlas/reading-list", items: [...csl]}  — list names preserved
//   2. [...csl]                                                  — a bare CSL-JSON array
//   3. BibTeX                                                    — parsed for the few fields
//      that identify a paper (title, doi, eprint/archivePrefix), not as a general BibTeX parser
//
// MATCHING is tried strongest-first: arXiv id, then DOI (an arXiv DOI reduces to an arXiv id),
// then normalised title. Title matching is last because it is the only one that can be wrong —
// two papers can share a title — but it is what rescues the ~40% of a typical library that
// carries no identifier at all.

import type { Dataset } from "./types";

export interface ReadingItem {
  key: string;
  title: string;
  list: string;
  arxivId: string;
  doi: string;
  year: number | null;
}

export interface ReadingList {
  name: string;
  items: ReadingItem[];
  /** item key -> node id, for the items that matched a paper in this corpus. */
  matches: Map<string, number>;
  lists: string[];
}

const ARXIV_IN_TEXT = [
  /arxiv[:\s/]*(\d{4}\.\d{4,5})/i,
  /arxiv\.org\/(?:abs|pdf)\/(\d{4}\.\d{4,5})/i,
  /10\.48550\/arxiv\.(\d{4}\.\d{4,5})/i,
  /arxiv[:\s/]*([a-z-]+(?:\.[A-Z]{2})?\/\d{7})/i,
  /arxiv\.org\/(?:abs|pdf)\/([a-z-]+(?:\.[A-Z]{2})?\/\d{7})/i,
];

export function extractArxivId(...texts: (string | undefined | null)[]): string {
  for (const text of texts) {
    if (!text) continue;
    for (const re of ARXIV_IN_TEXT) {
      const m = text.match(re);
      if (m) return m[1].replace(/v\d+$/, "");
    }
  }
  return "";
}

/** Titles differ by punctuation, case, LaTeX and line wrapping far more often than by words. */
export function normalizeTitle(title: string): string {
  return title
    .toLowerCase()
    .replace(/[{}$\\]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function cslToItem(raw: Record<string, unknown>, fallbackList: string): ReadingItem | null {
  const title = String(raw.title ?? "").trim();
  if (!title) return null;
  const custom = (raw.custom ?? {}) as Record<string, unknown>;
  const doi = String(raw.DOI ?? "").trim().toLowerCase();
  const issued = raw.issued as { "date-parts"?: number[][] } | undefined;
  const year = issued?.["date-parts"]?.[0]?.[0] ?? null;
  return {
    key: String(raw.id ?? title),
    title,
    list: String(custom.list ?? fallbackList),
    arxivId:
      String(custom.arxiv_id ?? "") ||
      extractArxivId(doi, String(raw.URL ?? ""), String(raw.note ?? "")),
    doi,
    year: typeof year === "number" ? year : null,
  };
}

/** Minimal BibTeX reading — enough to identify papers, not a general parser. */
function parseBibtex(text: string): ReadingItem[] {
  const out: ReadingItem[] = [];
  // Entries start at "@type{key," — split on that rather than trying to balance braces.
  const entries = text.split(/@\w+\s*\{/).slice(1);
  for (const entry of entries) {
    const key = entry.slice(0, entry.indexOf(",")).trim();
    const field = (name: string): string => {
      const m = entry.match(new RegExp(`${name}\\s*=\\s*[{"]([\\s\\S]*?)["}]\\s*,?\\s*\\n`, "i"));
      return m ? m[1].replace(/\s+/g, " ").trim() : "";
    };
    const title = field("title");
    if (!title) continue;
    const eprint = field("eprint");
    const doi = field("doi").toLowerCase();
    const yearRaw = parseInt(field("year"), 10);
    out.push({
      key: key || title,
      title,
      list: "Imported",
      arxivId: /^\d{4}\.\d{4,5}$/.test(eprint) ? eprint : extractArxivId(eprint, doi, field("url")),
      doi,
      year: Number.isFinite(yearRaw) ? yearRaw : null,
    });
  }
  return out;
}

/** Parse a reading list file. Throws with a readable message when the shape is unrecognised. */
export function parseReadingList(text: string, filename: string): ReadingItem[] {
  const trimmed = text.trimStart();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) {
    const items = parseBibtex(text);
    if (items.length === 0) {
      throw new Error(`${filename}: no entries found — expected CSL-JSON or BibTeX`);
    }
    return items;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error(`${filename}: not valid JSON`);
  }
  const fallback = filename.replace(/\.[^.]+$/, "");
  const rawItems: unknown[] = Array.isArray(parsed)
    ? parsed
    : ((parsed as { items?: unknown[] }).items ?? []);
  if (!Array.isArray(rawItems) || rawItems.length === 0) {
    throw new Error(`${filename}: no items — expected a CSL-JSON array or an {items: [...]} file`);
  }
  const items = rawItems
    .map((raw) => cslToItem(raw as Record<string, unknown>, fallback))
    .filter((x): x is ReadingItem => x !== null);
  if (items.length === 0) throw new Error(`${filename}: entries have no titles`);
  return items;
}

/**
 * Match items to corpus nodes.
 *
 * `arxivIndex` comes from the on-demand import-index artifact; `ds.papers` supplies titles,
 * which stream in progressively — so a match run before the titles land will resolve fewer
 * items, and callers re-run it when `papersReady` flips.
 */
export function matchReadingList(
  ds: Dataset,
  items: ReadingItem[],
  arxivIndex: Map<string, number> | null,
): Map<string, number> {
  const byTitle = new Map<string, number>();
  for (let i = 0; i < ds.papers.length; i++) {
    const t = ds.papers[i]?.title;
    if (!t) continue;
    const key = normalizeTitle(t);
    // First wins: node ids run oldest-first, so a duplicated title resolves to the original
    // rather than to whichever copy happens to be last in the file.
    if (key && !byTitle.has(key)) byTitle.set(key, i);
  }

  const matches = new Map<string, number>();
  for (const item of items) {
    let node: number | undefined;
    if (arxivIndex && item.arxivId) node = arxivIndex.get(item.arxivId);
    if (node === undefined && arxivIndex && item.doi) {
      const fromDoi = extractArxivId(item.doi);
      if (fromDoi) node = arxivIndex.get(fromDoi);
    }
    if (node === undefined) node = byTitle.get(normalizeTitle(item.title));
    if (node !== undefined) matches.set(item.key, node);
  }
  return matches;
}
