// Loads the static artifact bundle (Arrow IPC + JSON) into an in-memory Dataset.
// Arrow tables are read zero-copy via apache-arrow's tableFromIPC.

import { Table, tableFromIPC } from "apache-arrow";
import type {
  AuthorRow,
  ClustersDoc,
  Dataset,
  LabelsDoc,
  Manifest,
  NeighborList,
  OrgsDoc,
  PaperMeta,
  PointData,
  TopicsDoc,
} from "./types";

const BASE = "data";
const SUPPORTED_SCHEMA_VERSION = 1;
let datasetPromise: Promise<Dataset> | null = null;

async function fetchArrow(name: string): Promise<Table> {
  const res = await fetch(`${BASE}/${name}`);
  if (!res.ok) throw new Error(`failed to load ${name}: ${res.status}`);
  const buf = await res.arrayBuffer();
  // tableFromIPC has a sync overload for a materialized byte array.
  return tableFromIPC(new Uint8Array(buf)) as Table;
}

async function fetchJSON<T>(name: string): Promise<T> {
  const res = await fetch(`${BASE}/${name}`);
  if (!res.ok) throw new Error(`failed to load ${name}: ${res.status}`);
  return (await res.json()) as T;
}

function toTypedColumn(table: Table, col: string) {
  // arrow Vector -> a contiguous typed array copy (deck.gl wants plain typed arrays).
  const vec = table.getChild(col);
  if (!vec) throw new Error(`points.arrow missing column ${col}`);
  return vec.toArray();
}

function unpackPoints(table: Table): PointData {
  return {
    count: table.numRows,
    nodeId: toTypedColumn(table, "node_id") as Int32Array,
    x: toTypedColumn(table, "x") as Float32Array,
    y: toTypedColumn(table, "y") as Float32Array,
    year: toTypedColumn(table, "year") as Int16Array,
    monthIndex: new Int16Array(table.numRows), // filled from papers in loadDatasetImpl
    citedByCount: toTypedColumn(table, "cited_by_count") as Int32Array,
    subfieldId: toTypedColumn(table, "subfield_id") as Int16Array,
    topicId: toTypedColumn(table, "topic_id") as Int32Array,
    r: toTypedColumn(table, "r") as Uint8Array,
    g: toTypedColumn(table, "g") as Uint8Array,
    b: toTypedColumn(table, "b") as Uint8Array,
    revealLevel: toTypedColumn(table, "reveal_level") as Int16Array,
  };
}

/** Months elapsed from `fromYear`-01 to a `yyyy-mm-dd` date (clamped >= 0). */
function monthsSince(fromYear: number, publicationDate: string): number {
  const y = parseInt(publicationDate.slice(0, 4));
  const m = parseInt(publicationDate.slice(5, 7)) || 1;
  if (!Number.isFinite(y)) return 0;
  return Math.max(0, (y - fromYear) * 12 + (m - 1));
}

function unpackPapers(table: Table): PaperMeta[] {
  const out: PaperMeta[] = new Array(table.numRows);
  // Row-wise materialization; fine at MVP scale (~40k rows).
  for (let i = 0; i < table.numRows; i++) {
    const row = table.get(i)!;
    out[i] = {
      paperId: row.paper_id,
      title: row.title,
      publicationDate: row.publication_date,
      doi: row.doi ?? null,
      arxivId: row.arxiv_id ?? null,
      venue: row.venue ?? null,
      citedByCount: row.cited_by_count,
      authorIds: row.author_ids ? Array.from(row.author_ids) : [],
      authorNames: row.author_names ? Array.from(row.author_names) : [],
    };
  }
  return out;
}

function unpackAuthors(table: Table): AuthorRow[] {
  const out: AuthorRow[] = new Array(table.numRows);
  for (let i = 0; i < table.numRows; i++) {
    const row = table.get(i)!;
    out[i] = { authorId: row.author_id, name: row.name, count: row.count };
  }
  return out;
}

// Lazy, cached related-works loader. Neighbors are sharded by node-id block (s11), so a
// selection fetches only its shard instead of the whole ~9MB (→50MB at 390k) table.
function makeNeighborLoader(manifest: Manifest) {
  const size = manifest.neighbor_shard_size ?? 0;
  const shardCache = new Map<number, Promise<Map<number, NeighborList>>>();
  const EMPTY: NeighborList = { ids: new Int32Array(), scores: new Float32Array() };

  async function loadShard(shard: number): Promise<Map<number, NeighborList>> {
    const table = await fetchArrow(`neighbors-${shard}.arrow`);
    const byNode = new Map<number, NeighborList>();
    for (let i = 0; i < table.numRows; i++) {
      const row = table.get(i)!;
      byNode.set(row.node_id, {
        ids: row.neighbor_ids ? Int32Array.from(row.neighbor_ids) : new Int32Array(),
        scores: row.scores ? Float32Array.from(row.scores) : new Float32Array(),
      });
    }
    return byNode;
  }

  return async function getNeighbors(node: number): Promise<NeighborList> {
    if (size <= 0) return EMPTY; // no sharded neighbors in this bundle
    const shard = Math.floor(node / size);
    let pending = shardCache.get(shard);
    if (!pending) {
      pending = loadShard(shard).catch((error) => {
        shardCache.delete(shard); // allow retry on transient failure
        throw error;
      });
      shardCache.set(shard, pending);
    }
    return (await pending).get(node) ?? EMPTY;
  };
}

function buildAdjacency(table: Table) {
  const citesOut = new Map<number, number[]>();
  const citedBy = new Map<number, number[]>();
  const src = table.getChild("src")!.toArray() as Int32Array;
  const dst = table.getChild("dst")!.toArray() as Int32Array;
  for (let i = 0; i < src.length; i++) {
    const s = src[i];
    const d = dst[i];
    (citesOut.get(s) ?? citesOut.set(s, []).get(s)!).push(d);
    (citedBy.get(d) ?? citedBy.set(d, []).get(d)!).push(s);
  }
  return { edges: { src, dst }, citesOut, citedBy };
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

async function loadDatasetImpl(): Promise<Dataset> {
  const manifest = await fetchJSON<Manifest>("manifest.json");

  // Neighbors are NOT fetched here — they load on demand per selection (makeNeighborLoader).
  const [pointsT, papersT, authorsT, edgesT] = await Promise.all([
    fetchArrow("points.arrow"),
    fetchArrow("papers.arrow"),
    fetchArrow("authors.arrow"),
    fetchArrow("edges.arrow"),
  ]);
  const [clusters, labels, orgs, topics] = await Promise.all([
    fetchJSON<ClustersDoc>("clusters.json"),
    fetchJSON<LabelsDoc>("labels.json"),
    fetchJSON<OrgsDoc>("orgs.json"),
    fetchJSON<TopicsDoc>("topics.json"),
  ]);

  const { edges, citesOut, citedBy } = buildAdjacency(edgesT);

  const points = unpackPoints(pointsT);
  const papers = unpackPapers(papersT);
  // Derive month-since-corpus-start per point for month-granularity date filtering.
  const fromYear = parseInt(manifest.corpus.date_from.slice(0, 4));
  for (let i = 0; i < points.count; i++) {
    points.monthIndex[i] = monthsSince(fromYear, papers[i]?.publicationDate ?? "");
  }

  const dataset: Dataset = {
    manifest,
    points,
    papers,
    clusters,
    labels,
    orgs,
    topics,
    authors: unpackAuthors(authorsT),
    getNeighbors: makeNeighborLoader(manifest),
    edges,
    citesOut,
    citedBy,
  };
  validateDataset(dataset);
  return dataset;
}

export function loadDataset(): Promise<Dataset> {
  // React StrictMode mounts effects twice in development. Reuse the in-flight/materialized
  // load so the 17 MB bundle is fetched and parsed once.
  if (!datasetPromise) {
    datasetPromise = loadDatasetImpl().catch((error) => {
      datasetPromise = null;
      throw error;
    });
  }
  return datasetPromise;
}
