// Filter state in the address bar.
//
// Everything the map shows is derived from a handful of facets, so a view is a value, not a
// session — but it lived only in memory, which meant a filtered map could not be sent to
// anyone. This encodes the facets into the query string and reads them back on load, so the
// URL is the thing you copy.
//
// Two rules govern what goes in:
//
//   1. Only STABLE identifiers. Org keys and topic/subfield ids come from the artifacts;
//      dates are written as absolute YYYY-MM rather than the internal month index, so a link
//      keeps meaning if the corpus start month ever moves.
//   2. Nothing personal. Imported reading lists are files on the reader's own machine (they
//      never leave the browser, D-reading-list), so a shared link that named them would
//      promise papers the recipient does not have.

import type { Filters } from "./store";

export interface UrlState {
  filters: Partial<Filters>;
  selectedNode: number | null;
}

const MONTH_RE = /^(\d{4})-(\d{2})$/;

/** Origin of the month index: JANUARY of the corpus start year.
 *
 *  Not the corpus start month. The app derives its range as
 *  `(toYear - fromYear) * 12 + (toMonth - 1)` (App.tsx, DateRangeSlider), which places index 0
 *  at January regardless of when the first paper landed — the corpus opens 1991-03, so reading
 *  date_from's month here shifted every shared date range two months earlier ("2020-01" came
 *  back as "Nov 2019"). */
function corpusStart(dateFrom: string): [number, number] {
  return [Number(dateFrom.slice(0, 4)), 0];
}

/** Month index (months since corpus start) -> "YYYY-MM". */
export function monthToAbs(index: number, dateFrom: string): string {
  const [y0, m0] = corpusStart(dateFrom);
  const total = y0 * 12 + m0 + Math.max(0, Math.round(index));
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

/** "YYYY-MM" -> month index, or null when unparseable. */
export function absToMonth(value: string, dateFrom: string): number | null {
  const m = MONTH_RE.exec(value);
  if (!m) return null;
  const [y0, m0] = corpusStart(dateFrom);
  const index = Number(m[1]) * 12 + (Number(m[2]) - 1) - (y0 * 12 + m0);
  return Number.isFinite(index) ? index : null;
}

/** Parse "1,2,3" into ids, dropping anything that is not a non-negative integer.
 *
 *  `filter(Boolean)` before the map is load-bearing: "".split(",") is [""], and Number("") is
 *  0, which is a perfectly good id. Without it an ABSENT parameter decoded as [0] — so every
 *  visit to the bare URL silently applied author 0, category 0, topic 0 and region 0, and then
 *  wrote them back into the address bar as if the reader had chosen them. */
const numbers = (raw: string | null): number[] =>
  (raw ?? "")
    .split(",")
    .filter((v) => v.trim() !== "")
    .map((v) => Number(v))
    .filter((v) => Number.isInteger(v) && v >= 0);

/** Build the query string for the current view. Empty when nothing is set, so a default map
 *  keeps a clean URL rather than a paragraph of defaults. */
export function encodeUrlState(
  filters: Filters,
  selectedNode: number | null,
  dateFrom: string,
  fullMaxMonth: number,
): string {
  const p = new URLSearchParams();
  if (filters.orgKeys.length) p.set("org", filters.orgKeys.join(","));
  if (filters.authorIds.length) p.set("author", filters.authorIds.join(","));
  if (filters.subfieldIds.length) p.set("field", filters.subfieldIds.join(","));
  if (filters.topicIds.length) p.set("topic", filters.topicIds.join(","));
  if (filters.labelIds.length) p.set("region", filters.labelIds.join(","));
  if (filters.citeMin > 0) p.set("cmin", String(filters.citeMin));
  if (filters.citeMax !== null) p.set("cmax", String(filters.citeMax));
  if (filters.monthMin > 0) p.set("from", monthToAbs(filters.monthMin, dateFrom));
  if (filters.monthMax < fullMaxMonth) p.set("to", monthToAbs(filters.monthMax, dateFrom));
  if (selectedNode !== null) p.set("paper", String(selectedNode));
  const q = p.toString();
  return q ? `?${q}` : "";
}

/** Read a view out of a query string. Unknown or malformed values are dropped rather than
 *  applied as zeroes — a bad link should open the default map, not a silently wrong one. */
export function decodeUrlState(
  search: string,
  dateFrom: string,
  nodeCount: number,
): UrlState {
  const p = new URLSearchParams(search);
  const filters: Partial<Filters> = {};
  const orgs = (p.get("org") ?? "").split(",").filter(Boolean);
  if (orgs.length) filters.orgKeys = orgs;
  const authors = numbers(p.get("author"));
  if (authors.length) filters.authorIds = authors;
  const fields = numbers(p.get("field"));
  if (fields.length) filters.subfieldIds = fields;
  const topics = numbers(p.get("topic"));
  if (topics.length) filters.topicIds = topics;
  const regions = numbers(p.get("region"));
  if (regions.length) filters.labelIds = regions;

  const cmin = Number(p.get("cmin"));
  if (p.has("cmin") && Number.isFinite(cmin) && cmin > 0) filters.citeMin = cmin;
  const cmax = Number(p.get("cmax"));
  if (p.has("cmax") && Number.isFinite(cmax) && cmax >= 0) filters.citeMax = cmax;

  const from = p.get("from") ? absToMonth(p.get("from")!, dateFrom) : null;
  if (from !== null && from >= 0) filters.monthMin = from;
  const to = p.get("to") ? absToMonth(p.get("to")!, dateFrom) : null;
  if (to !== null && to >= 0) filters.monthMax = to;

  // A node id is an index into THIS build's corpus. Range-check it so a link from an older
  // build opens the map rather than selecting an unrelated paper or crashing on undefined.
  const paper = Number(p.get("paper"));
  const selectedNode =
    p.has("paper") && Number.isInteger(paper) && paper >= 0 && paper < nodeCount ? paper : null;

  return { filters, selectedNode };
}
