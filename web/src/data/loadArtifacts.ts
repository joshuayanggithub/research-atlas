// Loads the static artifact bundle (Arrow IPC + JSON) into an in-memory Dataset.
// Arrow tables are read zero-copy via apache-arrow's tableFromIPC.

import { Table, tableFromIPC } from "apache-arrow";
import type {
  AuthorRow,
  Dataset,
  LabelsDoc,
  Manifest,
  NeighborList,
  OrgsDoc,
  PaperDetail,
  PaperMeta,
  PointData,
  TopicsDoc,
} from "./types";

// Where the artifact bundle lives. Relative "data" works for `vite dev`/`preview`, which
// serve web/public/data directly. In a deployed build it points at the object store instead:
// the bundle is 1,221 files / 0.79 GB and two of its members exceed GitHub's 100 MB per-file
// hard limit, so Pages hosts the app and something else hosts the data.
//
// Left as a bare relative path when unset, deliberately — a missing VITE_DATA_BASE should
// 404 visibly rather than silently produce a site that looks fine and has no papers.
const BASE = import.meta.env.VITE_DATA_BASE ?? "data";
const SUPPORTED_SCHEMA_VERSION = 3;
let datasetPromise: Promise<Dataset> | null = null;

/** Progress of the startup load, reported as it happens (see `loadDataset`). */
export interface LoadProgress {
  phase: "manifest" | "downloading" | "decoding" | "ready";
  /** Uncompressed bytes received so far, and the total expected from the manifest. */
  loadedBytes: number;
  totalBytes: number;
  /** 0..1, weighted so the bar tracks wall-clock rather than file count. */
  pct: number;
  /** What is happening right now, e.g. "papers-index.arrow" or "building citation index". */
  detail: string;
}

export type ProgressFn = (p: LoadProgress) => void;

// Downloading dominates on anything but localhost, so it owns most of the bar; decoding is the
// fixed tail (measured ~4.9s for 912k: adjacency 1.8s, papers index 2.2s).
const DOWNLOAD_SHARE = 0.88;

/**
 * `priority` is a Chrome Priority Hint. It matters a great deal on a slow link: this app streams
 * ~100 MB in the background after first paint (titles, authors, edges, the import index), so an
 * on-demand fetch the user is actively waiting for — the position shards for the papers they
 * just filtered to — otherwise queues behind all of it. Marking background streams "low" and
 * interactive fetches "high" is the difference between ~1 s and ~40 s for the same 1 MB.
 */
// Interactive fetches must not queue behind background streaming.
//
// Measured on a 1 MB/s link: after first paint this app streams ~29 MB in the background (author
// chunks, title chunks, the import index, the papers index). Those saturate all six HTTP/1.1
// sockets, and a browser Priority Hint only reorders QUEUED requests — it cannot preempt a
// multi-megabyte download already in flight. So the 0.46 MB of position shards needed to draw a
// filtered reading list arrived at t+149s despite being marked "high".
//
// The gate fixes the mechanism rather than the ordering: while an interactive fetch is
// outstanding, no NEW background fetch starts. In-flight ones finish (a second or two each) and
// their sockets go to the fetch the user is actually waiting on.
let interactiveInFlight = 0;
const backgroundWaiters: (() => void)[] = [];

function releaseBackground(): void {
  if (interactiveInFlight > 0) return;
  const waiting = backgroundWaiters.splice(0);
  for (const resume of waiting) resume();
}

async function awaitBackgroundSlot(): Promise<void> {
  while (interactiveInFlight > 0) {
    await new Promise<void>((resolve) => backgroundWaiters.push(resolve));
  }
}

async function fetchArrow(name: string, priority?: "high" | "low"): Promise<Table> {
  if (priority === "low") await awaitBackgroundSlot();
  if (priority === "high") interactiveInFlight++;
  try {
    return await fetchArrowInner(name, priority);
  } finally {
    if (priority === "high") {
      interactiveInFlight--;
      releaseBackground();
    }
  }
}

async function fetchArrowInner(name: string, priority?: "high" | "low"): Promise<Table> {
  const res = await fetch(`${BASE}/${name}`, priority ? { priority } as RequestInit : undefined);
  if (!res.ok) throw new Error(`failed to load ${name}: ${res.status}`);
  const buf = await res.arrayBuffer();
  // tableFromIPC has a sync overload for a materialized byte array.
  return tableFromIPC(new Uint8Array(buf)) as Table;
}

/** Like `fetchArrow`, but streams so bytes can be counted as they arrive.
 *
 * The reader yields DECOMPRESSED bytes (the browser inflates gzip transparently), which is why
 * these counts line up with the uncompressed sizes recorded in the manifest — and why the bar
 * measures the thing the user is actually waiting through.
 */
async function fetchArrowProgress(name: string, onBytes: (n: number) => void): Promise<Table> {
  const res = await fetch(`${BASE}/${name}`);
  if (!res.ok) throw new Error(`failed to load ${name}: ${res.status}`);
  if (!res.body) {
    const buf = await res.arrayBuffer();
    onBytes(buf.byteLength);
    return tableFromIPC(new Uint8Array(buf)) as Table;
  }
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    total += value.length;
    onBytes(value.length);
  }
  const merged = new Uint8Array(total);
  let at = 0;
  for (const c of chunks) {
    merged.set(c, at);
    at += c.length;
  }
  return tableFromIPC(merged) as Table;
}

async function fetchJSON<T>(name: string): Promise<T> {
  const res = await fetch(`${BASE}/${name}`);
  if (!res.ok) throw new Error(`failed to load ${name}: ${res.status}`);
  return (await res.json()) as T;
}



// Reveal level assigned to points whose tile has not been fetched yet. usePointsLayer culls on
// `reveal_level <= activeLevel`, so an unloaded point is simply invisible until its tile lands —
// no separate "is loaded" channel is needed.
export const UNLOADED_LEVEL = 32767;

// Levels fetched before the map is shown. Level 0 is all the home view renders; through L4 is
// 2.66 MB and covers the first several zoom steps, so panning/zooming feels instant while the
// deeper 30 MB arrives only if the user actually goes looking for it.
const EAGER_TILE_LEVEL = 4;

let loadedTileLevel = -1;
let pointsRef: PointData | null = null;
let manifestRef: Manifest | null = null;
// The resident paper rows, so lazily-loaded tiles can fill in citation counts as they land.
let papersRef: PaperMeta[] | null = null;
const tileWaiters: (() => void)[] = [];
let tilePending: Promise<void> | null = null;
// The deepest level anyone has asked for. A request arriving while a batch is in flight must
// RAISE this rather than be dropped — the previous code returned the in-flight promise and threw
// the new level away, and since the caller is a React effect whose deps do not change again,
// nothing ever asked a second time. On a fast connection the eager tiles finish before the user
// can interact, so this never fired; on a ~1 MB/s link they are still streaming when a filter is
// applied, and the "load every level" request that a filter triggers was silently lost. Symptom:
// an imported 19-paper reading list drew exactly the 7 papers living in levels 0-4 and stopped.
let wantedTileLevel = -1;

/** Ensure every point tile up to `level` is loaded; resolves when they are. Idempotent. */
export function ensurePointTiles(level: number): Promise<void> {
  if (!pointsRef || !manifestRef || level <= loadedTileLevel) return Promise.resolve();
  wantedTileLevel = Math.max(wantedTileLevel, level);
  if (tilePending) return tilePending;
  const points = pointsRef;
  const manifest = manifestRef;
  tilePending = (async () => {
    // Re-read `wantedTileLevel` and `loadedTileLevel` every pass so a deeper request that
    // arrives mid-flight extends this batch instead of needing a new one.
    for (;;) {
      const next = (manifest.point_tiles ?? [])
        .filter((t) => t.level > loadedTileLevel && t.level <= wantedTileLevel)
        .sort((a, b) => a.level - b.level)[0];
      if (!next) break;
      const table = await fetchArrow(next.path);
      fillPointTile(points, table, papersRef ?? undefined);
      loadedTileLevel = Math.max(loadedTileLevel, next.level);
      // Notify per TILE, not per batch: the filter bar's "N on the map so far" should climb as
      // levels land rather than sit frozen until the last one arrives.
      for (const fn of tileWaiters) fn();
    }
  })()
    .catch(() => { /* deeper detail simply stays hidden */ })
    .finally(() => { tilePending = null; });
  return tilePending;
}

// Shards of point rows keyed by node_id (schema.POSITION_SHARD_ROWS). Reveal-level tiles are
// ordered by IMPORTANCE, so an arbitrary selection — a reading list, one author's papers — is
// scattered across every level, and placing 19 dots meant downloading all 43 MB. Fetching the
// ~19 shards those papers actually live in costs ~1.7 MB instead.
// Deeper than any level s12 emits; means "everything". Kept below UNLOADED_LEVEL for the same
// reason usePointsLayer's MAX_REVEAL_LEVEL is: the two must never collide.
const MAX_TILE_LEVEL = 32766;

const fetchedPositionShards = new Set<number>();

// Past this many shards, the reveal-level tiles are the better deal: they are the same bytes but
// ordered by importance, so the most significant papers appear first instead of by node id.
const POSITION_SHARD_CAP = 400;

/**
 * Ensure `nodeIds` have coordinates, fetching only the shards that hold them.
 *
 * Returns immediately for nodes already loaded. Notifies tile waiters per shard so the
 * "N on the map so far" count climbs while this runs.
 */
export async function ensurePositionsFor(nodeIds: number[]): Promise<void> {
  const points = pointsRef;
  const manifest = manifestRef;
  if (!points || !manifest) return;
  const rows = manifest.position_shard_rows ?? 0;
  const shardCount = manifest.n_position_shards ?? 0;
  if (rows <= 0 || shardCount <= 0) {
    // Older artifact bundle without shards: fall back to the reveal-level tiles.
    await ensurePointTiles(MAX_TILE_LEVEL);
    return;
  }

  const need = new Set<number>();
  for (const node of nodeIds) {
    if (node < 0 || node >= points.count) continue;
    if (points.revealLevel[node] !== UNLOADED_LEVEL) continue; // already placed
    const shard = Math.floor(node / rows);
    if (!fetchedPositionShards.has(shard)) need.add(shard);
  }
  if (need.size === 0) return;
  if (need.size > POSITION_SHARD_CAP) {
    await ensurePointTiles(MAX_TILE_LEVEL);
    return;
  }

  // Bounded concurrency: a few hundred parallel requests over a slow tunnel is worse than a
  // steady handful, and the browser caps at ~6 per host anyway.
  const queue = [...need];
  const worker = async () => {
    for (;;) {
      const shard = queue.pop();
      if (shard === undefined) return;
      if (fetchedPositionShards.has(shard)) continue;
      fetchedPositionShards.add(shard);
      try {
        const table = await fetchArrow(`points-by-node-${shard}.arrow`, "high");
        fillPointTile(points, table, papersRef ?? undefined);
        for (const fn of tileWaiters) fn();
      } catch {
        fetchedPositionShards.delete(shard); // allow a retry
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(6, queue.length) }, worker));
}

/** Subscribe to "more point detail just landed"; returns an unsubscribe. */
export function onPointTiles(fn: () => void): () => void {
  tileWaiters.push(fn);
  return () => {
    const i = tileWaiters.indexOf(fn);
    if (i >= 0) tileWaiters.splice(i, 1);
  };
}

/** Allocate the full-corpus point arrays up front, all initially culled.
 *
 * Tiles carry an arbitrary subset of node ids, but the whole app assumes `nodeId[i] === i`
 * (validateDataset enforces it, and every layer indexes ds.points.* by node id). Preallocating
 * to the corpus size and filling by node id keeps that invariant while letting the bytes arrive
 * progressively — the point of the tiles s11 has been emitting all along.
 */
function emptyPoints(count: number): PointData {
  const reveal = new Int16Array(count).fill(UNLOADED_LEVEL);
  const nodeId = new Int32Array(count);
  for (let i = 0; i < count; i++) nodeId[i] = i;
  return {
    count,
    nodeId,
    x: new Float32Array(count),
    y: new Float32Array(count),
    year: new Int16Array(count),
    monthIndex: new Int16Array(count),
    citedByCount: new Int32Array(count),
    subfieldId: new Int16Array(count),
    topicId: new Int32Array(count),
    r: new Uint8Array(count),
    g: new Uint8Array(count),
    b: new Uint8Array(count),
    revealLevel: reveal,
    regionLeaf: new Int32Array(count).fill(-1),
  };
}

/** Scatter one tile's rows into the preallocated arrays, by node id. */
function fillPointTile(points: PointData, table: Table, papers?: PaperMeta[]): void {
  const id = table.getChild("node_id")!.toArray() as Int32Array;
  const col = (n: string) => table.getChild(n)!.toArray() as never;
  const x = col("x") as unknown as Float32Array;
  const y = col("y") as unknown as Float32Array;
  const year = col("year") as unknown as Int16Array;
  const month = col("month_index") as unknown as Int16Array;
  const cited = col("cited_by_count") as unknown as Int32Array;
  const sub = col("subfield_id") as unknown as Int16Array;
  const topic = col("topic_id") as unknown as Int32Array;
  const r = col("r") as unknown as Uint8Array;
  const g = col("g") as unknown as Uint8Array;
  const b = col("b") as unknown as Uint8Array;
  const lvl = col("reveal_level") as unknown as Int16Array;
  const region = table.getChild("region_leaf")?.toArray() as Int32Array | undefined;
  for (let i = 0; i < id.length; i++) {
    const n = id[i];
    points.x[n] = x[i];
    points.y[n] = y[i];
    points.year[n] = year[i];
    points.monthIndex[n] = month[i];
    points.citedByCount[n] = cited[i];
    // Keep the resident paper row in step so the details panel shows a real count as soon as
    // the tile arrives, rather than waiting for the separate papers-index fetch.
    if (papers) {
      const row = papers[n];
      if (row) {
        row.citedByCount = cited[i];
        row.citationCountAvailable = true;
        // The YEAR too. placeholderPapers derives publicationDate from points.year, which is 0
        // for a paper whose tile has not arrived — so its date rendered as an em dash and stayed
        // that way, because nothing refreshed it when the tile finally landed. Clicking the paper
        // appeared to "fix" it only because the detail shard carries the real date. Reported as
        // years being hyphenated in an author's paper list.
        if (!row.publicationDate && year[i]) row.publicationDate = String(year[i]);
        row.dateAvailable = true;
      }
    }
    points.subfieldId[n] = sub[i];
    points.topicId[n] = topic[i];
    points.r[n] = r[i];
    points.g[n] = g[i];
    points.b[n] = b[i];
    points.revealLevel[n] = lvl[i];
    if (region) points.regionLeaf[n] = region[i];
  }
}


/** Rows built from points.arrow alone, so the map works before titles have arrived.
 *
 * `cited_by_count` and `year` already ship in points.arrow, and those plus `title` are the only
 * fields anything reads off `ds.papers[...]`. Starting with real numbers and a blank title keeps
 * every consumer synchronous while the 98.8 MB title index is still in flight.
 */
function placeholderPapers(points: PointData): PaperMeta[] {
  const out: PaperMeta[] = new Array(points.count);
  const yearStr = new Map<number, string>();
  for (let i = 0; i < points.count; i++) {
    const y = points.year[i] ?? 0;
    let ys = yearStr.get(y);
    if (ys === undefined) {
      ys = y ? String(y) : "";
      yearStr.set(y, ys);
    }
    out[i] = {
      title: "",
      citedByCount: points.citedByCount[i],
      // NOT true. A placeholder row has whatever `emptyPoints` zeroed, and claiming the count
      // is available made the details panel print a confident "0 citations · Semantic Scholar
      // S2AG" for a paper whose tile simply had not downloaded yet — the exact class of wrong
      // number D31/D34 were about. Availability starts false and becomes true when the paper's
      // point tile lands (fillPointTile) or papers-index.arrow does (fillPapersIndex).
      citationCountAvailable: points.revealLevel[i] !== UNLOADED_LEVEL,
      referencesAvailable: true,
      authorIds: [],
      publicationDate: ys,
      // A tile-less row has year 0, which is not a date — say so rather than letting the UI
      // render the placeholder as a real absence.
      dateAvailable: points.revealLevel[i] !== UNLOADED_LEVEL,
      hasFigure: false,
    };
  }
  return out;
}

/** Fill titles/author ids/figure flags into the existing rows, IN PLACE. */
function fillPapersIndex(papers: PaperMeta[], table: Table): void {
  const n = table.numRows;
  const nodeIds = table.getChild("node_id")!.toArray() as Int32Array;
  const hasFigureCol = table.getChild("has_figure");
  const availCol = table.getChild("citation_count_available");
  const refAvailCol = table.getChild("references_available");
  // The COUNT, not just its availability flag. These used to come from different artifacts on
  // different schedules: this file (1.9 MB, early) flipped the flag to available while the
  // number was still the zero placeholder from an unloaded point tile (42 MB, much later), so
  // the panel printed "0 citations · Semantic Scholar S2AG" for a paper the artifact records
  // as having 40. One source for both, and the pair can no longer disagree.
  const citedCol = table.getChild("cited_by_count");
  // The YEAR, which this artifact has always carried for all N papers and which nothing read.
  // Dates were therefore filled ONLY by point tiles and position shards — per-paper fetches
  // that trickle in over minutes — so an author's list showed em-dash years long after the
  // 2.6 MB index that knows every one of them had already landed. Exactly the omission the
  // comment above describes for `cited_by_count`, in the column next to it.
  const yearCol = table.getChild("year");
  for (let i = 0; i < n; i++) {
    const row = papers[nodeIds[i]];
    if (!row) continue;
    if (hasFigureCol) row.hasFigure = Boolean(hasFigureCol.get(i));
    if (citedCol) row.citedByCount = Number(citedCol.get(i) ?? 0);
    if (yearCol) {
      const y = Number(yearCol.get(i) ?? 0);
      // Never overwrite a full ISO date already resolved from a detail shard with a bare year.
      if (y > 0 && row.publicationDate.length < 4) row.publicationDate = String(y);
      // True even when y is 0: the index is authoritative for all N rows, so a blank date is
      // now a real absence and the UI should stop shimmering and say so.
      row.dateAvailable = true;
    }
    if (availCol) row.citationCountAvailable = Boolean(availCol.get(i));
    if (refAvailCol) row.referencesAvailable = Boolean(refAvailCol.get(i));
  }
}

let papersReady = false;
let titleChunksLoaded = 0;

/** How many title chunks have landed — bumps as they stream so views can re-render. */
/** What the deferred streams have delivered so far, for the loading readout. */
export interface DeferredProgress {
  key: string;
  label: string;
  loaded: number;
  total: number;
}

/**
 * Snapshot of everything still arriving after first paint.
 *
 * The app deliberately paints before its data is complete (D23), which is the right trade on a
 * ~1 MB/s link — but it left the user unable to tell "this paper has no title" from "this title
 * has not downloaded yet", or an empty search result from an index that is still filling. This
 * is the honest readout of that gap.
 */
export function deferredProgress(): DeferredProgress[] {
  const m = manifestRef;
  if (!m) return [];
  const out: DeferredProgress[] = [
    // Counted against ALL point tiles, this sat at "35%" forever: the home view loads 5 of 16
    // and the rest arrive only if someone zooms to the bottom, so the denominator was one the
    // reader could never reach. The honest statement is whether the eager set has landed.
    { key: "tiles", label: "map detail",
      loaded: loadedTileLevel >= EAGER_TILE_LEVEL ? 1 : 0, total: 1 },
    // Titles are no longer a stream with an end: they are fetched per view (ensureTitles), so
    // there is no honest "N of 489" to report. The loading pill covers the streams that DO
    // run to completion; a pending title shows its own shimmer where it will appear.

    // No "authors" row: nothing streams the author index any more (D59). Reporting a
    // percentage for a fetch that never happens is the same false claim as a title
    // progress bar for shards fetched on demand.
    { key: "edges", label: "citations", loaded: edgesReady ? 1 : 0, total: 1 },
  ];
  return out.filter((p) => p.total > 0);
}

export function titlesProgress(): number {
  return titleChunksLoaded;
}
const papersReadyWaiters: (() => void)[] = [];
const regionsWaiters: (() => void)[] = [];

/** Subscribe to the cell tree landing; returns an unsubscribe. */
export function onRegionsReady(fn: () => void): () => void {
  regionsWaiters.push(fn);
  return () => {
    const i = regionsWaiters.indexOf(fn);
    if (i >= 0) regionsWaiters.splice(i, 1);
  };
}

let edgesReady = false;
const edgesReadyWaiters: (() => void)[] = [];

/** True once the citation graph has streamed in. */
export function areEdgesReady(): boolean {
  return edgesReady;
}

/** Subscribe to the moment the citation graph lands; returns an unsubscribe. */
export function onEdgesReady(fn: () => void): () => void {
  if (edgesReady) {
    fn();
    return () => {};
  }
  edgesReadyWaiters.push(fn);
  return () => {
    const i = edgesReadyWaiters.indexOf(fn);
    if (i >= 0) edgesReadyWaiters.splice(i, 1);
  };
}

/** True once titles have streamed in. Search/tooltips use it to avoid showing blank rows. */
export function arePapersReady(): boolean {
  return papersReady;
}

/** Subscribe to paper data arriving; returns an unsubscribe.
 *
 * This fires for the resident index AND for every per-node title shard (D30), so it must keep
 * notifying long after `papersReady` flips. Subscribing only while the flag was false, and
 * firing once, meant a component that mounted after papers-index had landed never heard about
 * its own titles: search results rendered a permanent shimmer with 22 title shards sitting
 * downloaded in memory, and "Attention Is All You Need" lost its own search to papers whose
 * titles happened to arrive before the flag flipped. */
export function onPapersReady(fn: () => void): () => void {
  if (papersReady) fn();
  papersReadyWaiters.push(fn);
  return () => {
    const i = papersReadyWaiters.indexOf(fn);
    if (i >= 0) papersReadyWaiters.splice(i, 1);
  };
}

let authorsCache: AuthorRow[] | null = null;
let authorsPending: Promise<AuthorRow[]> | null = null;
let authorChunksLoaded = 0;
const authorsWaiters: (() => void)[] = [];

/** How many author chunks have landed. Consumers re-read `peekAuthors` when this moves. */
export function authorsProgress(): number {
  return authorChunksLoaded;
}

/** Subscribe to each author chunk landing; returns an unsubscribe. */
export function onAuthorsChunk(fn: () => void): () => void {
  authorsWaiters.push(fn);
  return () => {
    const i = authorsWaiters.indexOf(fn);
    if (i >= 0) authorsWaiters.splice(i, 1);
  };
}

/**
 * Fetch the author search index once, on first use.
 *
 * It arrives as `authors-N.arrow` chunks (D32) rather than one 55.9 MB file, and rows accumulate
 * as each lands — so a name typed early can match against the names that have arrived instead
 * of nothing at all. The promise still resolves only when every chunk is in, for callers that
 * need the complete index (org-scoped researcher lists).
 *
 * Each chunk REPLACES the cache with a new array rather than pushing into the existing one.
 * That costs ~8.4M reference copies across the whole load and buys correctness: consumers
 * memoise derived maps with `[authors]`, and an array mutated in place keeps its identity, so
 * every one of those memos froze at the empty map built on the first render. The symptom was
 * silent — the author filter applied and the map showed the 7 matching papers, but the filter
 * bar and author panel rendered nothing at all, because both look their names up in that map.
 */
// ---------------------------------------------------------------------------------------
// Author lookup.
//
// authors.arrow shipped as 13 chunks / ~14.4 MB gzipped, fetched after first paint on every
// visit, purely so the author box could substring-match 1,482,740 names — the largest stream
// left on the wire once titles moved off (D55). Same treatment as titles: a name-token index
// in alphabetical chunks, so a query costs one or two ~127 KB fetches.
//
// The postings carry name, count and verified flag, so the dropdown needs nothing else; and a
// selected author's own record (with every row sharing their name) comes from a separate
// author-info shard, which is what makes identity merging possible without the full index.
// ---------------------------------------------------------------------------------------

const authorTokenCache =
  new Map<number, Promise<Map<string, { rows: AuthorRow[]; capped: boolean }>>>();
const AUTHOR_CHUNK_CAP = 3;
const AUTHOR_PREFIX_CAP = 48;

function loadAuthorTokenChunk(
  chunk: number,
): Promise<Map<string, { rows: AuthorRow[]; capped: boolean }>> {
  let pending = authorTokenCache.get(chunk);
  if (!pending) {
    pending = fetchArrow(`author-tokens-${chunk}.arrow`, "high")
      .then((t) => {
        const tokens = t.getChild("token")!;
        const ids = t.getChild("author_ids")!;
        const names = t.getChild("names")!;
        const counts = t.getChild("counts")!;
        const verified = t.getChild("verified")!;
        const cappedCol = t.getChild("capped");
        const out = new Map<string, { rows: AuthorRow[]; capped: boolean }>();
        for (let i = 0; i < t.numRows; i++) {
          const idList = ids.get(i);
          const nameList = names.get(i);
          const countList = counts.get(i);
          const verList = verified.get(i);
          const rows: AuthorRow[] = [];
          for (let k = 0; k < (idList?.length ?? 0); k++) {
            const row: AuthorRow = {
              authorId: Number(idList.get(k)),
              name: String(nameList?.get(k) ?? ""),
              count: Number(countList?.get(k) ?? 0),
              verified: Boolean(verList?.get(k)),
            };
            rows.push(row);
            if (!authorInfoCache.has(row.authorId)) authorRowHint.set(row.authorId, row);
          }
          out.set(String(tokens.get(i) ?? ""),
            { rows, capped: Boolean(cappedCol?.get(i)) });
        }
        return out;
      })
      .catch((e) => { authorTokenCache.delete(chunk); throw e; });
    authorTokenCache.set(chunk, pending);
  }
  return pending;
}

function authorChunksFor(token: string): number[] {
  const chunks = manifestRef?.author_index_chunks ?? [];
  const hits: number[] = [];
  for (const c of chunks) {
    const beforeStart = token < c.first.slice(0, token.length);
    const afterEnd = token > c.last.slice(0, Math.max(token.length, c.last.length));
    if (!beforeStart && !afterEnd) hits.push(c.chunk);
  }
  return hits;
}

/** Authors whose name matches `query`. The last token is treated as a prefix (type-ahead). */
export async function searchAuthors(query: string): Promise<AuthorRow[]> {
  const words = query.toLowerCase().match(/[a-z0-9]+/g)?.filter((w) => w.length >= 2) ?? [];
  if (words.length === 0) return [];
  const ordered = [...new Set(words)].sort((a, b) => b.length - a.length);
  const last = words[words.length - 1];

  // Same rule as the title index: a CAPPED list holds only the 25 most prolific authors for
  // that token, so it can confirm a name contains the token but never prove one does not.
  // "Kaival Shah" has one paper and is absent from `shah`'s top 25, so intersecting
  // `kaival` with `shah` erased him — his own full name could not find him.
  let result: Map<number, AuthorRow> | null = null;
  const sampled: Map<number, AuthorRow>[] = [];
  let fetched = 0;
  for (const word of ordered) {
    const wanted = authorChunksFor(word);
    if (wanted.length === 0) continue;
    if (fetched >= AUTHOR_CHUNK_CAP) break;
    fetched += wanted.length;
    const tables = await Promise.all(wanted.map((c) => loadAuthorTokenChunk(c).catch(() => null)));
    const hits = new Map<number, AuthorRow>();
    // Complete only if every contributing list is (see the title index for the reasoning).
    let complete = true;
    for (const table of tables) {
      if (!table) continue;
      const exact = table.get(word);
      if (exact) {
        for (const row of exact.rows) hits.set(row.authorId, row);
        if (exact.capped) complete = false;
      }
      if (word === last) {
        let expanded = 0;
        for (const [tok, entry] of table) {
          if (tok.length > word.length && tok.startsWith(word)) {
            for (const row of entry.rows) hits.set(row.authorId, row);
            if (entry.capped) complete = false;
            if (++expanded >= AUTHOR_PREFIX_CAP) { complete = false; break; }
          }
        }
      }
    }
    if (hits.size === 0) {
      if (complete) return [];
      continue;
    }
    if (complete) {
      if (result === null) result = hits;
      else for (const id of [...result.keys()]) if (!hits.has(id)) result.delete(id);
      if (result.size === 0) return [];
    } else {
      sampled.push(hits);
    }
  }

  if (result !== null) {
    // Names confirmed by every complete token; those also in the capped samples matched more
    // of the query, so rank them first.
    const out = [...result.values()];
    if (sampled.length) {
      out.sort((a, b) => sampled.filter((s) => s.has(b.authorId)).length
        - sampled.filter((s) => s.has(a.authorId)).length);
    }
    return out;
  }
  if (sampled.length === 0) return [];
  let acc = sampled[0];
  for (const s of sampled.slice(1)) {
    const next = new Map<number, AuthorRow>();
    for (const [id, row] of acc) if (s.has(id)) next.set(id, row);
    acc = next;
  }
  return acc.size > 0 ? [...acc.values()] : [...sampled[0].values()];
}

const authorInfoCache =
  new Map<number, AuthorRow & { sameNameIds: number[]; sameNamePapers: number }>();
/** Name/count seen in a token posting, used until the author's own record lands. */
const authorRowHint = new Map<number, AuthorRow>();
const authorInfoShards = new Map<number, Promise<void>>();
const authorInfoWaiters: (() => void)[] = [];

export function onAuthorInfo(fn: () => void): () => void {
  authorInfoWaiters.push(fn);
  return () => {
    const i = authorInfoWaiters.indexOf(fn);
    if (i >= 0) authorInfoWaiters.splice(i, 1);
  };
}

/** An author's record, or the lighter hint from a search posting, or null. */
export function peekAuthorInfo(
  id: number,
): (AuthorRow & { sameNameIds?: number[]; sameNamePapers?: number }) | null {
  return authorInfoCache.get(id) ?? authorRowHint.get(id) ?? null;
}

/** Fetch records for specific authors — the ones a session actually selects or displays. */
export async function ensureAuthorInfo(ids: number[]): Promise<void> {
  const manifest = manifestRef;
  if (!manifest || (manifest.n_author_info_shards ?? 0) === 0) return;
  const rows = 2048; // AUTHOR_INFO_SHARD_ROWS
  const want = new Set<number>();
  for (const id of ids) {
    if (id < 0 || authorInfoCache.has(id)) continue;
    const shard = Math.floor(id / rows);
    if (!authorInfoShards.has(shard)) want.add(shard);
  }
  if (want.size === 0) return;
  await Promise.all([...want].slice(0, 8).map((shard) => {
    const pending = fetchArrow(`author-info-${shard}.arrow`, "high")
      .then((t) => {
        const ids2 = t.getChild("author_id")!.toArray() as Int32Array;
        const name = t.getChild("name")!;
        const count = t.getChild("count")!;
        const ver = t.getChild("verified")!;
        const same = t.getChild("same_name_ids")!;
        const groupPapers = t.getChild("same_name_papers");
        for (let i = 0; i < ids2.length; i++) {
          const sib = same.get(i);
          authorInfoCache.set(ids2[i], {
            authorId: ids2[i],
            name: String(name.get(i) ?? ""),
            count: Number(count.get(i) ?? 0),
            verified: Boolean(ver.get(i)),
            sameNameIds: sib ? (Array.from(sib) as number[]) : [ids2[i]],
            sameNamePapers: Number(groupPapers?.get(i) ?? 0),
          });
        }
        for (const fn of authorInfoWaiters) fn();
      })
      .catch(() => { authorInfoShards.delete(shard); });
    authorInfoShards.set(shard, pending);
    return pending;
  }));
}

export function loadAuthors(): Promise<AuthorRow[]> {
  if (authorsCache && authorsPending === null) return Promise.resolve(authorsCache);
  if (!authorsPending) {
    const nChunks = manifestRef?.n_author_chunks ?? 0;
    authorsCache = [];
    authorsPending = (async () => {
      // Legacy artifacts (or a manifest written before D32) still ship one authors.arrow.
      const paths = nChunks > 0
        ? Array.from({ length: nChunks }, (_, c) => `authors-${c}.arrow`)
        : ["authors.arrow"];
      for (const path of paths) {
        const table = await fetchArrow(path, "low");
        const part: AuthorRow[] = [];
        appendAuthors(part, table);
        authorsCache = (authorsCache ?? []).concat(part);
        authorChunksLoaded++;
        for (const fn of authorsWaiters) fn();
        // Yield between chunks: unpacking 829k rows is real main-thread work and must not
        // block panning while it happens behind the map.
        await new Promise((r) => setTimeout(r, 0));
      }
      authorsPending = null;
      return authorsCache ?? [];
    })().catch((e) => {
      authorsPending = null; // allow retry
      throw e;
    });
  }
  return authorsPending;
}

// Inverted author index. Selecting an author used to mean scanning all 912,429 papers' author
// id lists (18.2 MB shipped eagerly); now it is a lookup of the one ~0.6 MB shard holding that
// author's row. Shards are cached, so a second author in the same block costs nothing.
const authorPapersCache = new Map<number, Promise<Map<number, number[]>>>();

// OpenAlex ids ride in the author-papers shards instead of the resident index (D32): they cost
// 22.5 MB there and only the author panel's profile link reads them — and that panel only shows
// while an author filter is active, which means the author's shard has already been fetched.
const authorOpenAlexCache = new Map<number, string>();

/** OpenAlex id for an author whose shard has loaded, else null. */
export function peekAuthorOpenAlex(authorId: number): string | null {
  return authorOpenAlexCache.get(authorId) ?? null;
}

/** Where an author publishes from, recency-ranked (s10). Filled by loadAuthorPapers. */
export interface AuthorAffiliation {
  label: string;
  count: number;
  from: number;
  to: number;
}

const authorAffiliationCache = new Map<number, AuthorAffiliation[]>();

/** Null means "not loaded"; an empty array means "loaded, and this author has none". The two
 *  render differently — an author with no attributed papers must show nothing at all, not a
 *  blank affiliation line. */
export function peekAuthorAffiliations(authorId: number): AuthorAffiliation[] | null {
  return authorAffiliationCache.get(authorId) ?? null;
}

const authorAffiliationShards = new Map<number, Promise<void>>();
const affiliationWaiters: (() => void)[] = [];

export function onAuthorAffiliations(fn: () => void): () => void {
  affiliationWaiters.push(fn);
  return () => {
    const i = affiliationWaiters.indexOf(fn);
    if (i >= 0) affiliationWaiters.splice(i, 1);
  };
}

/**
 * Affiliations for specific authors, from their own small shards.
 *
 * NOT carried in the author-papers shard even though that one is already fetched when the
 * panel appears: author ids are ordered by descending paper count, so shard 0 holds the most
 * prolific authors at ~1.8 MB while the average is 269 KB — and a searched-for researcher is
 * prolific by definition. Folding affiliations in doubled exactly the fetch that mattered.
 * These shards are ~53 KB.
 */
export async function ensureAuthorAffiliations(authorIds: number[]): Promise<void> {
  const manifest = manifestRef;
  const rows = 2048; // AUTHOR_AFFILIATION_SHARD_ROWS
  if (!manifest || (manifest.n_author_affiliation_shards ?? 0) === 0) return;
  const want = new Set<number>();
  for (const id of authorIds) {
    if (id < 0 || authorAffiliationCache.has(id)) continue;
    const shard = Math.floor(id / rows);
    if (!authorAffiliationShards.has(shard)) want.add(shard);
  }
  if (want.size === 0) return;
  await Promise.all([...want].slice(0, 8).map((shard) => {
    const pending = fetchArrow(`author-affiliations-${shard}.arrow`, "high")
      .then((t) => {
        const ids = t.getChild("author_id")!.toArray() as Int32Array;
        const labels = t.getChild("labels")!;
        const counts = t.getChild("counts")!;
        const first = t.getChild("first_year")!;
        const last = t.getChild("last_year")!;
        for (let i = 0; i < ids.length; i++) {
          const l = labels.get(i);
          const c = counts.get(i);
          const f = first.get(i);
          const to = last.get(i);
          const out: AuthorAffiliation[] = [];
          for (let k = 0; k < (l?.length ?? 0); k++) {
            out.push({
              label: String(l.get(k) ?? ""),
              count: Number(c?.get(k) ?? 0),
              from: Number(f?.get(k) ?? 0),
              to: Number(to?.get(k) ?? 0),
            });
          }
          authorAffiliationCache.set(ids[i], out);
        }
        for (const fn of affiliationWaiters) fn();
      })
      .catch(() => { authorAffiliationShards.delete(shard); });
    authorAffiliationShards.set(shard, pending);
    return pending;
  }));
}

const orgNodesCache = new Map<number, Promise<Map<string, number[]>>>();

/**
 * Membership for a DIRECTORY organization, from its shard.
 *
 * The curated browse tree (43 entries, 118,565 ids) still ships inline in orgs.json because
 * color-by-org needs it before any selection exists. The other 10,475 institutions are
 * search-only; their 1,370,907 ids were 94% of the file and nothing read them until someone
 * picked one, so they load on demand here — one ~47 KB fetch per 128 orgs.
 */
export function loadOrgNodes(shard: number): Promise<Map<string, number[]>> {
  let pending = orgNodesCache.get(shard);
  if (!pending) {
    pending = fetchArrow(`org-nodes-${shard}.arrow`, "high")
      .then((t) => {
        const keys = t.getChild("org_key")!;
        const lists = t.getChild("node_ids")!;
        const out = new Map<string, number[]>();
        for (let i = 0; i < t.numRows; i++) {
          const v = lists.get(i);
          out.set(String(keys.get(i) ?? ""), v ? (Array.from(v) as number[]) : []);
        }
        return out;
      })
      .catch((e) => {
        orgNodesCache.delete(shard);
        throw e;
      });
    orgNodesCache.set(shard, pending);
  }
  return pending;
}

export function loadAuthorPapers(
  shardSize: number,
  authorId: number,
): Promise<Map<number, number[]>> {
  const shard = Math.floor(authorId / shardSize);
  let pending = authorPapersCache.get(shard);
  if (!pending) {
    pending = fetchArrow(`author-papers-${shard}.arrow`)
      .then((t) => {
        const ids = t.getChild("author_id")!.toArray() as Int32Array;
        const lists = t.getChild("node_ids")!;
        const oa = t.getChild("openalex_id");
        const out = new Map<number, number[]>();
        for (let i = 0; i < ids.length; i++) {
          const v = lists.get(i);
          out.set(ids[i], v ? (Array.from(v) as number[]) : []);
          if (oa) authorOpenAlexCache.set(ids[i], String(oa.get(i) ?? ""));
        }
        return out;
      })
      .catch((e) => {
        authorPapersCache.delete(shard);
        throw e;
      });
    authorPapersCache.set(shard, pending);
  }
  return pending;
}

// arXiv id -> node_id, for matching an imported reading list. Fetched only when the user
// actually imports (~14 MB raw / a few MB gzipped), never at startup.
let importIndexPending: Promise<Map<string, number>> | null = null;

export function loadImportIndex(): Promise<Map<string, number>> {
  if (!importIndexPending) {
    // The user is blocked on this one — they just chose a file and are waiting for a count.
    importIndexPending = fetchArrow("import-index.arrow", "high")
      .then((t) => {
        const nodes = t.getChild("node_id")!.toArray() as Int32Array;
        const ids = t.getChild("arxiv_id")!;
        const out = new Map<string, number>();
        for (let i = 0; i < nodes.length; i++) {
          const aid = ids.get(i);
          if (aid) out.set(aid, nodes[i]);
        }
        return out;
      })
      .catch((e) => {
        importIndexPending = null; // allow retry
        throw e;
      });
  }
  return importIndexPending;
}

/** Already-loaded authors, or [] — for non-React callers that must stay synchronous. */
export function peekAuthors(): AuthorRow[] {
  return authorsCache ?? [];
}

function appendAuthors(out: AuthorRow[], table: Table): void {
  const ids = table.getChild("author_id")!.toArray() as Int32Array;
  const names = table.getChild("name")!;
  const counts = table.getChild("count")!.toArray() as Int32Array;
  // Pre-D32 artifacts carry openalex_id and no `verified` column; derive the flag from it.
  const verified = table.getChild("verified");
  const legacyOa = table.getChild("openalex_id");
  for (let i = 0; i < ids.length; i++) {
    out.push({
      authorId: ids[i],
      name: names.get(i),
      count: counts[i],
      verified: verified
        ? !!verified.get(i)
        : !String(legacyOa?.get(i) ?? "").startsWith("arxiv-name:"),
    });
  }
}

// Generic lazy, cached, node-id-block-sharded record loader. Both related-works neighbors
// and per-paper detail are sharded the same way (shard = node_id // size, s11), so a
// selection fetches only its shard instead of the whole table. Returns a fetcher that
// resolves one node's record (or `fallback` when the bundle isn't sharded / row is absent).
function makeShardLoader<T>(
  size: number,
  filename: (shard: number) => string,
  unpackRow: (row: Record<string, unknown>) => T,
  fallback: T,
): (node: number) => Promise<T> {
  const shardCache = new Map<number, Promise<Map<number, T>>>();

  async function loadShard(shard: number): Promise<Map<number, T>> {
    const table = await fetchArrow(filename(shard));
    const byNode = new Map<number, T>();
    for (let i = 0; i < table.numRows; i++) {
      const row = table.get(i)! as unknown as Record<string, unknown>;
      byNode.set(row.node_id as number, unpackRow(row));
    }
    return byNode;
  }

  return async function get(node: number): Promise<T> {
    if (size <= 0) return fallback;
    const shard = Math.floor(node / size);
    let pending = shardCache.get(shard);
    if (!pending) {
      pending = loadShard(shard).catch((error) => {
        shardCache.delete(shard); // allow retry on transient failure
        throw error;
      });
      shardCache.set(shard, pending);
    }
    return (await pending).get(node) ?? fallback;
  };
}

// ---------------------------------------------------------------------------------------
// Citation graph, in two pieces.
//
// edges.arrow is 110 MB (87 MB gzipped) and used to be fetched WHOLE after first paint, on
// every visit, regardless of where the user looked. It is also over GitHub's 100 MB per-file
// limit, so it cannot be hosted at all. Two mechanisms replace it, because there are two
// different questions:
//
//   TIERS (edges-L{N}.arrow) — "what is drawable right now". An edge needs BOTH endpoints on
//   screen, so it belongs to the tier of its deeper endpoint. The home view needs 408 of
//   14,303,089 edges (3 KB); the eager depth needs 400,471 (2.1 MB).
//
//   NODE SHARDS (edges-by-node-{N}.arrow) — "everything connected to THIS paper", which
//   ignores zoom entirely: "Attention Is All You Need" has 69,262 citers, overwhelmingly in
//   tiers the reader has not loaded.
//
// Tiers are disjoint (an edge belongs to exactly one), so merging them never duplicates. A
// node shard is AUTHORITATIVE for its nodes and replaces whatever the tiers contributed, which
// is what `completeNodes` records — the difference between "these are its references" and
// "these are the references we happen to have", which the UI must not confuse.
// ---------------------------------------------------------------------------------------

/** Nodes whose adjacency lists are known to be COMPLETE (their node shard has landed). */
const completeNodes = new Set<number>();

export function hasCompleteEdges(node: number): boolean {
  return completeNodes.has(node);
}

/** Merge one tier into the adjacency maps and the flat arrays the ambient layer scans. */
function mergeEdgeTier(
  table: Table,
  edges: { src: Int32Array; dst: Int32Array },
  citesOut: Map<number, number[]>,
  citedBy: Map<number, number[]>,
): void {
  const src = table.getChild("src")!.toArray() as Int32Array;
  const dst = table.getChild("dst")!.toArray() as Int32Array;
  const grown = new Int32Array(edges.src.length + src.length);
  grown.set(edges.src);
  grown.set(src, edges.src.length);
  const grownDst = new Int32Array(edges.dst.length + dst.length);
  grownDst.set(edges.dst);
  grownDst.set(dst, edges.dst.length);
  edges.src = grown;
  edges.dst = grownDst;
  for (let i = 0; i < src.length; i++) {
    const s = src[i];
    const d = dst[i];
    // A node whose shard already landed holds the authoritative list; adding tier edges to it
    // would duplicate entries it already has.
    if (!completeNodes.has(s)) (citesOut.get(s) ?? citesOut.set(s, []).get(s)!).push(d);
    if (!completeNodes.has(d)) (citedBy.get(d) ?? citedBy.set(d, []).get(d)!).push(s);
  }
}

// Refs the tier/shard loaders need. Set once the dataset is built; before that there is
// nothing to merge into.
let edgesRef: { src: Int32Array; dst: Int32Array } | null = null;
let citesOutRef: Map<number, number[]> | null = null;
let citedByRef: Map<number, number[]> | null = null;

let loadedEdgeTier = -1;
let wantedEdgeTier = -1;
let edgeTierPending: Promise<void> | null = null;

/**
 * Load edge tiers 0..level. Mirrors ensurePointTiles, including its hard-won detail: a deeper
 * request arriving mid-flight must not be dropped, or the graph silently stops at whatever
 * depth happened to be in flight when the user zoomed.
 */
export function ensureEdgeTiles(level: number): Promise<void> {
  const manifest = manifestRef;
  if (!manifest || !edgesRef || !citesOutRef || !citedByRef) return Promise.resolve();
  const tiles = manifest.edge_tiles ?? [];
  if (tiles.length === 0) return Promise.resolve();
  const want = Math.min(level, tiles[tiles.length - 1].level);
  if (want <= loadedEdgeTier) return Promise.resolve();
  wantedEdgeTier = Math.max(wantedEdgeTier, want);
  if (edgeTierPending) return edgeTierPending;

  edgeTierPending = (async () => {
    while (loadedEdgeTier < wantedEdgeTier) {
      const next = loadedEdgeTier + 1;
      const tile = tiles.find((t) => t.level === next);
      if (tile) {
        try {
          const table = await fetchArrow(tile.path, next <= 1 ? "high" : "low");
          mergeEdgeTier(table, edgesRef!, citesOutRef!, citedByRef!);
        } catch {
          break; // leave loadedEdgeTier where it is so a later call can retry
        }
      }
      loadedEdgeTier = next;
      edgesReady = true;
      for (const fn of edgesReadyWaiters) fn();
      for (const fn of edgeWaiters) fn();
    }
    edgeTierPending = null;
  })();
  return edgeTierPending;
}

const edgeWaiters: (() => void)[] = [];

/** Bumps whenever more of the graph lands — tier or node shard. */
export function onEdgesChanged(fn: () => void): () => void {
  edgeWaiters.push(fn);
  return () => {
    const i = edgeWaiters.indexOf(fn);
    if (i >= 0) edgeWaiters.splice(i, 1);
  };
}

const edgeNodeShards = new Map<number, Promise<void>>();

/**
 * The COMPLETE network of specific papers, from their node shards.
 *
 * Everything the UI says about a selected paper — "30 of 78 references are in this map", the
 * citer list, the arrows, the relevance ranking — has to be answered from complete lists. The
 * tiers cannot do that: they hold only what is drawable at the current zoom.
 *
 * Capped like ensurePositionsFor: a request for more than this many shards means the caller is
 * asking about a large slice of the corpus, and a few hundred round trips would cost far more
 * than the bytes saved.
 */
export const EDGE_SHARD_CAP = 24;

export async function ensureNodeEdges(nodes: number[]): Promise<void> {
  const manifest = manifestRef;
  if (!manifest || !citesOutRef || !citedByRef) return;
  const rows = manifest.position_shard_rows ?? 0;
  const count = manifest.n_edge_node_shards ?? 0;
  if (rows <= 0 || count <= 0) {
    // Bundle without node shards: the whole graph is the only complete source.
    await ensureEdgeTiles(Number.MAX_SAFE_INTEGER);
    return;
  }
  const want = new Set<number>();
  for (const node of nodes) {
    if (node < 0 || completeNodes.has(node)) continue;
    const shard = Math.floor(node / rows);
    if (shard < count && !edgeNodeShards.has(shard)) want.add(shard);
  }
  if (want.size === 0) {
    // Still wait on anything already in flight for these nodes.
    const inflight = nodes
      .map((n) => edgeNodeShards.get(Math.floor(n / rows)))
      .filter((p): p is Promise<void> => !!p);
    await Promise.all(inflight);
    return;
  }
  if (want.size > EDGE_SHARD_CAP) {
    for (const shard of [...want].slice(EDGE_SHARD_CAP)) want.delete(shard);
  }
  await Promise.all([...want].map((shard) => {
    const pending = fetchArrow(`edges-by-node-${shard}.arrow`, "high")
      .then((table) => {
        const ids = table.getChild("node_id")!.toArray() as Int32Array;
        const out = table.getChild("cites_out")!;
        const inn = table.getChild("cited_by")!;
        for (let i = 0; i < ids.length; i++) {
          const node = ids[i];
          const o = out.get(i);
          const c = inn.get(i);
          // REPLACE, not merge: this list is authoritative, and whatever the tiers contributed
          // is a subset of it.
          citesOutRef!.set(node, o ? (Array.from(o) as number[]) : []);
          citedByRef!.set(node, c ? (Array.from(c) as number[]) : []);
          completeNodes.add(node);
        }
        for (const fn of edgeWaiters) fn();
      })
      .catch(() => { edgeNodeShards.delete(shard); });
    edgeNodeShards.set(shard, pending);
    return pending;
  }));
}

// ---------------------------------------------------------------------------------------
// Title search.
//
// Search used to scan every resident title with `includes(q)`, which made results depend on
// DOWNLOAD PROGRESS: measured during D53 verification, searching "Attention Is All You Need"
// early in a session returned "Element-wise Attention Is All You Need" and "...Until You Need
// Retention" but never the real paper, because the title chunk holding it had not arrived.
// A search box that silently answers from a fraction of the corpus is the placeholder-as-fact
// bug wearing a different hat.
//
// s11 emits a token -> papers index, split ALPHABETICALLY into 64 chunks (~115 KB gzipped,
// 224 KB worst case). Alphabetical order is what makes prefix search possible without shipping
// the vocabulary: "atten" is a contiguous run of tokens, so it lives in one or two known files.
// ---------------------------------------------------------------------------------------

const searchChunkCache =
  new Map<number, Promise<Map<string, { nodes: number[]; capped: boolean }>>>();

/** How many index chunks one query may fetch. A query with more distinct tokens than this is
 *  answered from the ones that matter most (longest tokens are the most selective). */
const SEARCH_CHUNK_CAP = 4;
/** How many distinct tokens a prefix may expand to inside its chunk. */
const PREFIX_EXPANSION_CAP = 64;

function loadSearchChunk(
  chunk: number,
): Promise<Map<string, { nodes: number[]; capped: boolean }>> {
  let pending = searchChunkCache.get(chunk);
  if (!pending) {
    pending = fetchArrow(`title-tokens-${chunk}.arrow`, "high")
      .then((t) => {
        const tokens = t.getChild("token")!;
        const lists = t.getChild("node_ids")!;
        const cappedCol = t.getChild("capped");
        const out = new Map<string, { nodes: number[]; capped: boolean }>();
        for (let i = 0; i < t.numRows; i++) {
          const v = lists.get(i);
          out.set(String(tokens.get(i) ?? ""), {
            nodes: v ? (Array.from(v) as number[]) : [],
            capped: Boolean(cappedCol?.get(i)),
          });
        }
        return out;
      })
      .catch((e) => {
        searchChunkCache.delete(chunk);
        throw e;
      });
    searchChunkCache.set(chunk, pending);
  }
  return pending;
}

/** Chunks whose alphabetical range could contain `token` (or tokens starting with it). */
function chunksFor(token: string): number[] {
  const chunks = manifestRef?.search_chunks ?? [];
  const hits: number[] = [];
  for (const c of chunks) {
    // A prefix matches a chunk when it is not entirely after `last` nor before `first`.
    // Compare against `last` on the prefix length so "atten" still matches a chunk whose
    // last token is "attention…".
    const beforeStart = token < c.first.slice(0, token.length);
    const afterEnd = token > c.last.slice(0, Math.max(token.length, c.last.length));
    if (!beforeStart && !afterEnd) hits.push(c.chunk);
  }
  return hits;
}

export interface TitleSearchResult {
  nodes: number[];
  /** True while an index chunk this query needs is still being fetched. */
  pending: boolean;
}

/**
 * Node ids whose titles contain every token of `query`.
 *
 * The LAST token is treated as a prefix, because the user is probably still typing it.
 */
export async function searchTitles(query: string): Promise<number[]> {
  const words = query.toLowerCase().match(/[a-z0-9]+/g)?.filter((w) => w.length >= 2) ?? [];
  if (words.length === 0) return [];
  // Longest first: the most selective tokens are the ones worth spending the chunk budget on.
  const ordered = [...new Set(words)].sort((a, b) => b.length - a.length);
  const isLastWord = (w: string) => w === words[words.length - 1];

  // A CAPPED posting list holds only the 200 most-cited papers for that token, so it can
  // confirm that a paper contains the word but can never prove one does not. Intersecting with
  // a sample silently erases papers: "3D Cal: An Open-Source Software Library…" has 1 citation
  // and is in neither `3d` nor `calibration`, so typing its exact title found nothing.
  //
  // So complete lists FILTER, capped lists only RANK.
  let result: Set<number> | null = null;
  const sampled: Set<number>[] = [];
  // How many query words a paper matched as a WHOLE TOKEN rather than through prefix
  // expansion. Typing "3D Cal" must put the paper titled "3D Cal:" above one titled
  // "…rigid 3D calibration": both contain a token starting with "cal", but only one contains
  // the word. Without this the expansion (1,307 papers for `cal`) drowned the exact match,
  // and no amount of citation ranking could rescue a 1-citation paper from that crowd.
  const exactMatches = new Map<number, number>();
  let fetched = 0;
  for (const word of ordered) {
    const wanted = chunksFor(word);
    if (wanted.length === 0) continue;
    if (fetched >= SEARCH_CHUNK_CAP) break;
    fetched += wanted.length;
    const tables = await Promise.all(wanted.map((c) => loadSearchChunk(c).catch(() => null)));
    const hits = new Set<number>();
    const exactHits = new Set<number>();
    // A word's hit set is the union of its exact list and (for the word being typed) every
    // token extending it. That union is complete only if EVERY contributor is — one capped
    // list anywhere makes the whole set a sample. Marking it complete because some OTHER
    // token happened to be complete is how "Kaival Shah" stayed unfindable: `shah` itself is
    // capped, but its prefix expansions are not.
    let complete = true;
    for (const table of tables) {
      if (!table) continue;
      const exact = table.get(word);
      if (exact) {
        for (const n of exact.nodes) { hits.add(n); exactHits.add(n); }
        if (exact.capped) complete = false;
      }
      if (isLastWord(word)) {
        // Prefix expansion: the user is mid-word, so "atten" should also find "attention".
        let expanded = 0;
        for (const [tok, entry] of table) {
          if (tok.length > word.length && tok.startsWith(word)) {
            for (const n of entry.nodes) hits.add(n);
            if (entry.capped) complete = false;
            if (++expanded >= PREFIX_EXPANSION_CAP) { complete = false; break; }
          }
        }
      }
    }
    if (hits.size === 0) {
      // Nothing at all for this word — including no capped sample — so it really is absent.
      if (complete) return [];
      continue;
    }
    for (const n of exactHits) exactMatches.set(n, (exactMatches.get(n) ?? 0) + 1);
    if (complete) {
      result = result === null
        ? hits
        : new Set<number>([...result].filter((n: number) => hits.has(n)));
      if (result.size === 0) return [];
    } else {
      sampled.push(hits);
    }
  }

  const cited = (n: number) => papersRef?.[n]?.citedByCount ?? 0;
  const rank = (a: number, b: number) =>
    (exactMatches.get(b) ?? 0) - (exactMatches.get(a) ?? 0)
    || sampled.filter((s) => s.has(b)).length - sampled.filter((s) => s.has(a)).length
    || cited(b) - cited(a);

  if (result !== null) {
    return [...result].sort(rank);
  }
  // Every token was a common word: fall back to the papers all the samples agree on, which is
  // the "most-cited papers containing these words" answer the cap was designed to give.
  if (sampled.length === 0) return [];
  let acc = sampled[0];
  for (const s of sampled.slice(1)) acc = new Set<number>([...acc].filter((n) => s.has(n)));
  return (acc.size > 0 ? [...acc] : [...sampled[0]]).sort(rank);
}

// Titles, fetched for the papers a view actually shows.
//
// Measured against real access patterns at 2048 rows/shard: a citation panel costs 19 requests
// / 1.2 MB, a reference list 21 / 1.4 MB, ten search results 10 / 0.6 MB — against 31.1 MB
// streamed on every visit before. The one expensive pattern is a 500-row list (322 shards), so
// callers pass the rows on SCREEN rather than every row they could show.

const titleShards = new Map<number, Promise<void>>();

/** Shards whose titles have landed, so "" can be read as "no title" rather than "not yet". */
const loadedTitleShards = new Set<number>();

export function titleLoaded(node: number): boolean {
  const rows = manifestRef?.title_chunk_rows ?? 0;
  if (rows <= 0) return arePapersReady();
  return loadedTitleShards.has(Math.floor(node / rows));
}

/** Ceiling per call. A caller asking for more than this is asking about a slice of the corpus,
 *  not a screenful, and should be passing visible rows instead. */
export const TITLE_SHARD_CAP = 24;

export async function ensureTitles(nodes: number[]): Promise<void> {
  const manifest = manifestRef;
  const papers = papersRef;
  if (!manifest || !papers) return;
  const rows = manifest.title_chunk_rows ?? 0;
  const count = manifest.n_title_chunks ?? 0;
  if (rows <= 0 || count <= 0) return;

  const want: number[] = [];
  for (const node of nodes) {
    if (node < 0) continue;
    const shard = Math.floor(node / rows);
    if (shard < count && !titleShards.has(shard) && !want.includes(shard)) want.push(shard);
    if (want.length >= TITLE_SHARD_CAP) break;
  }
  if (want.length === 0) {
    await Promise.all(nodes
      .map((n) => titleShards.get(Math.floor(n / rows)))
      .filter((p): p is Promise<void> => !!p));
    return;
  }
  await Promise.all(want.map((shard) => {
    const pending = fetchArrow(`papers-titles-${shard}.arrow`, "high")
      .then((table) => {
        const ids = table.getChild("node_id")!.toArray() as Int32Array;
        const titles = table.getChild("title")!;
        for (let i = 0; i < ids.length; i++) {
          const row = papers[ids[i]];
          if (row) row.title = titles.get(i);
        }
        loadedTitleShards.add(shard);
        titleChunksLoaded = loadedTitleShards.size;
        for (const fn of papersReadyWaiters) fn();
      })
      .catch(() => { titleShards.delete(shard); });
    titleShards.set(shard, pending);
    return pending;
  }));
}

function validateDataset(dataset: Dataset): void {
  const { manifest, points, papers, edges } = dataset;
  if (manifest.schema_version !== SUPPORTED_SCHEMA_VERSION) {
    throw new Error(
      `unsupported data schema ${manifest.schema_version}; expected ${SUPPORTED_SCHEMA_VERSION}`,
    );
  }
  if (points.count !== manifest.corpus.count) {
    throw new Error(
      `points row count ${points.count} does not match manifest count ${manifest.corpus.count}`,
    );
  }
  if (papers.length !== points.count) {
    throw new Error("paper and point artifacts have different row counts");
  }
  for (let i = 0; i < points.count; i++) {
    if (points.nodeId[i] !== i) {
      throw new Error(`points.arrow row ${i} has non-dense node_id ${points.nodeId[i]}`);
    }
  }
  if (edges.src.length !== edges.dst.length) {
    throw new Error("citation edge source and target columns have different lengths");
  }
  for (let i = 0; i < edges.src.length; i++) {
    if (
      edges.src[i] < 0 ||
      edges.src[i] >= points.count ||
      edges.dst[i] < 0 ||
      edges.dst[i] >= points.count
    ) {
      throw new Error(`citation edge ${i} references a node outside the corpus`);
    }
  }
}

// Phase timer for the startup path. Load time is dominated by work that happens AFTER the
// bytes arrive (Arrow parse + unpack + adjacency), so a single end-to-end number hides where
// the time actually goes — measure per phase or you optimise the wrong thing.
function phaseTimer() {
  const marks: [string, number][] = [];
  let last = performance.now();
  const start = last;
  return {
    mark(name: string) {
      const now = performance.now();
      marks.push([name, Math.round(now - last)]);
      last = now;
    },
    report() {
      if (!import.meta.env.DEV) return;
      const total = Math.round(performance.now() - start);
      console.log(
        `[load] total ${total}ms — ` + marks.map(([n, ms]) => `${n} ${ms}ms`).join(" | "),
      );
    },
  };
}

async function loadDatasetImpl(onProgress?: ProgressFn): Promise<Dataset> {
  const t = phaseTimer();
  const report = (p: LoadProgress) => { try { onProgress?.(p); } catch { /* never break the load */ } };
  report({ phase: "manifest", loadedBytes: 0, totalBytes: 0, pct: 0, detail: "reading manifest" });

  const manifest = await fetchJSON<Manifest>("manifest.json");
  t.mark("manifest");

  // Exact uncompressed sizes are recorded per file by s11, so the bar is measured rather than
  // guessed — no synthetic easing, and it cannot finish early or stall at 99%.
  // papers-index.arrow is deliberately absent: at 98.8 MB it is 57% of the old eager payload
  // and, on a ~1 MB/s link, a minute of staring at nothing. Only titles are unique to it —
  // cited_by_count and year already ship in points.arrow — so the map is built without it and
  // titles stream in afterwards (see the background fill below).
  // The citation graph is no longer fetched whole: it is the worst byte-per-value artifact in
  // the bundle (node ids are near-random int32, so gzip reaches only 1.33x) and at 110 MB it
  // exceeds GitHub's per-file limit. Zoom tiers + per-node shards replaced it; see the block
  // above mergeEdgeTier.
  // Only the first few point tiles are on the critical path. s11 emits points-L0..L15 and the
  // home view renders level 0 alone; L0-L4 is 2.66 MB against 33.1 MB for the monolithic
  // points.arrow, and deeper levels stream in as the user zooms (ensurePointTiles).
  const tiles = manifest.point_tiles ?? [];
  const eagerTiles = tiles.filter((t) => t.level <= EAGER_TILE_LEVEL).map((t) => t.path);
  const EAGER = [...eagerTiles, "labels.json", "orgs.json", "topics.json"];
  const sizeOf = (name: string) => manifest.files?.[name]?.bytes ?? 0;
  const totalBytes = EAGER.reduce((sum, n) => sum + sizeOf(n), 0);
  let loadedBytes = 0;
  const bump = (detail: string) => (n: number) => {
    loadedBytes += n;
    report({
      phase: "downloading",
      loadedBytes,
      totalBytes,
      pct: totalBytes ? Math.min(1, loadedBytes / totalBytes) * DOWNLOAD_SHARE : 0,
      detail,
    });
  };
  // Decoding is synchronous main-thread work, so React cannot repaint mid-phase. Yielding a
  // macrotask before each step lets the browser paint the new label/percentage first —
  // otherwise the bar visibly freezes at the download share for the whole decode.
  const decoding = async (pct: number, detail: string) => {
    report({ phase: "decoding", loadedBytes, totalBytes, pct, detail });
    await new Promise((resolve) => setTimeout(resolve, 0));
  };

  // Neighbors and per-paper detail are NOT fetched here — they load on demand per selection.
  // The resident papers-index holds title/year/citations/author_ids for all papers.
  // authors.arrow is deliberately NOT fetched here. It is 34.6 MB / 829k rows and unpacking
  // it measured 2,875 ms — ~50% of load time — yet nothing on first paint reads it: only the
  // author autocomplete, the author panel, and org-scoped researcher lists do. It loads on
  // first use via loadAuthors() instead.
  const points = emptyPoints(manifest.corpus.count);
  for (const path of eagerTiles) {
    // No `papers` here on purpose: it is built from these points a few lines below, so the
    // eager tiles are already reflected in the placeholder rows.
    const tile = await fetchArrowProgress(path, bump("map positions"));
    fillPointTile(points, tile);
  }
  loadedTileLevel = EAGER_TILE_LEVEL;
  pointsRef = points;
  manifestRef = manifest;
  t.mark("fetch+parse tiles");
  // clusters.json is NOT fetched — nothing reads its per-region array, and the zoom levels
  // it used to carry are in the manifest. Semantic-zoom labels come from labels.json.
  const [labels, orgs, topics] = await Promise.all([
    fetchJSON<LabelsDoc>("labels.json"),
    fetchJSON<OrgsDoc>("orgs.json"),
    fetchJSON<TopicsDoc>("topics.json"),
  ]);
  t.mark("fetch json");

  // Decoding weights come from the measured split at 912k (adjacency 1.8s, index 2.2s, points
  // 15ms), so the tail of the bar advances at roughly the rate the work actually takes.
  // Empty until the first edge tier lands; grown in place as tiers and node shards arrive.
  const regions: { parent: Int32Array; level: Int16Array } = {
    parent: new Int32Array(0),
    level: new Int16Array(0),
  };
  const edges: { src: Int32Array; dst: Int32Array } = {
    src: new Int32Array(0),
    dst: new Int32Array(0),
  };
  const citesOut = new Map<number, number[]>();
  const citedBy = new Map<number, number[]>();
  await decoding(DOWNLOAD_SHARE + 0.05, "placing papers on the map");
  t.mark("points ready");
  // Placeholder rows built straight from points.arrow so every consumer of ds.papers[...] keeps
  // working synchronously and the map can paint. Titles arrive shortly after.
  const papers = placeholderPapers(points);
  papersRef = papers;
  t.mark("placeholderPapers");

  const getNeighbors = makeShardLoader<NeighborList>(
    manifest.neighbor_shard_size ?? 0,
    (s) => `neighbors-${s}.arrow`,
    (row) => ({
      ids: row.neighbor_ids ? Int32Array.from(row.neighbor_ids as ArrayLike<number>) : new Int32Array(),
      scores: row.scores ? Float32Array.from(row.scores as ArrayLike<number>) : new Float32Array(),
    }),
    { ids: new Int32Array(), scores: new Float32Array() },
  );

  const getPaperDetail = makeShardLoader<PaperDetail | null>(
    manifest.paper_shard_size ?? 0,
    (s) => `papers-detail-${s}.arrow`,
    (row) => ({
      paperId: row.paper_id as string,
      publicationDate: (row.publication_date as string) ?? "",
      doi: (row.doi as string) ?? null,
      arxivId: (row.arxiv_id as string) ?? null,
      venue: (row.venue as string) ?? null,
      authorNames: row.author_names ? Array.from(row.author_names as ArrayLike<string>) : [],
      authorIds: row.author_ids ? Array.from(row.author_ids as ArrayLike<number>) : [],
      referenceCount:
        typeof row.reference_count === "number" ? (row.reference_count as number) : -1,
    }),
    null,
  );

  const dataset: Dataset = {
    manifest,
    points,
    papers,
    labels,
    orgs,
    topics,
    regions,
    getAuthors: loadAuthors,
    getNeighbors,
    getPaperDetail,
    edges,
    citesOut,
    citedBy,
  };
  // Stream titles in after first paint. Consumers read ds.papers[i].title, so the rows are
  // filled IN PLACE and `onPapersReady` lets React re-render; nothing has to become async.
  // Citation graph, after first paint. Maps are mutated in place and `edges` keeps its object
  // identity, so `ds.citesOut` / `ds.edges.src` stay valid references for every consumer.
  // Cell tree for exact label-region membership (2.72 MB). Needed the moment someone clicks a
  // label, so it is fetched right after paint rather than on demand.
  // "low" so the interactive gate can hold it back: measured, an ungated 2.9 MB fetch made
  // the first search-index chunk take 18.5s on a 1 MB/s link.
  void fetchArrow("regions.arrow", "low")
    .then((table) => {
      const id = table.getChild("id")!.toArray() as Int32Array;
      const par = table.getChild("parent")!.toArray() as Int32Array;
      const lvl = table.getChild("level")!.toArray() as Int16Array;
      let max = 0;
      for (let i = 0; i < id.length; i++) if (id[i] > max) max = id[i];
      const parent = new Int32Array(max + 1).fill(-1);
      const level = new Int16Array(max + 1).fill(-1);
      for (let i = 0; i < id.length; i++) {
        parent[id[i]] = par[i];
        level[id[i]] = lvl[i];
      }
      regions.parent = parent;
      regions.level = level;
      for (const fn of regionsWaiters) fn();
      regionsWaiters.length = 0;
    })
    .catch(() => { /* label-region filtering degrades to unavailable */ });

  // Citation graph: only the tiers that are drawable at the eager depth. The rest arrives as
  // the user zooms (ensureEdgeTiles) or selects a paper (ensureNodeEdges). This replaced a
  // single 87 MB gzipped fetch that every visit paid before drawing a single arrow.
  edgesRef = edges;
  citesOutRef = citesOut;
  citedByRef = citedBy;
  void ensureEdgeTiles(EAGER_TILE_LEVEL).catch(() => {
    /* map + filters still work without the citation graph */
  });

  // Authors are NOT streamed any more. The whole index was fetched here after first paint —
  // 13 chunks / ~14.4 MB gzipped on every visit — so the author box could substring-match
  // 1,482,740 names, which made it the largest remaining stream once titles moved off (D55).
  // A query now reads the name-token index instead (D59): one or two ~127 KB chunks, and the
  // postings carry the name and count the dropdown renders.

  // Titles are NOT streamed any more. They were 17 chunks / 31.1 MB gzipped fetched in full
  // on every visit, to display a few dozen strings — and on a slow link they saturated the
  // sockets, so the search index chunks a user was actively waiting on queued behind them.
  // They are now sharded by node (2048 rows, ~72 KB gzipped) and fetched for the papers a view
  // actually renders; see ensureTitles.
  void fetchArrow("papers-index.arrow", "low")
    .then((table) => {
      fillPapersIndex(papers, table);
      // NOT papersReady. This file carries citation counts and availability flags; titles left
      // it in D30 and now stream as separate chunks. Setting the flag here made the app announce
      // "titles are in" while 31 MB of them were still downloading — so a paper with no title yet
      // rendered as "(untitled)", a claim about the paper rather than about the download. Only
      // the chunk loop below may declare titles ready. Legacy bundles with no chunks are handled
      // where nTitleChunks is read.
      // papers-index carries counts and availability flags for all N rows. Titles are no
      // longer part of any "everything has arrived" moment — they are per-node now — so this
      // flag means only "the resident index is in", which is what its consumers need.
      papersReady = true;
      // Do NOT clear the list: title shards keep arriving and keep needing to notify, and
      // dropping subscribers here also silently broke the unsubscribe returned above.
      for (const fn of papersReadyWaiters) fn();
    })
    .catch(() => { /* titles stay blank; the map, filters and citations still work */ });

  validateDataset(dataset);
  t.mark("validate");
  t.report();
  report({ phase: "ready", loadedBytes, totalBytes, pct: 1, detail: "ready" });
  return dataset;
}

export function loadDataset(onProgress?: ProgressFn): Promise<Dataset> {
  // React StrictMode mounts effects twice in development. Reuse the in-flight/materialized
  // load so the bundle is fetched and parsed once.
  if (!datasetPromise) {
    datasetPromise = loadDatasetImpl(onProgress).catch((error) => {
      datasetPromise = null;
      throw error;
    });
  }
  return datasetPromise;
}
