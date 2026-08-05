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
  PaperDetail,
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
    // month_index ships in points now (pipeline-computed) so date filtering needs no papers.
    monthIndex: toTypedColumn(table, "month_index") as Int16Array,
    citedByCount: toTypedColumn(table, "cited_by_count") as Int32Array,
    subfieldId: toTypedColumn(table, "subfield_id") as Int16Array,
    topicId: toTypedColumn(table, "topic_id") as Int32Array,
    r: toTypedColumn(table, "r") as Uint8Array,
    g: toTypedColumn(table, "g") as Uint8Array,
    b: toTypedColumn(table, "b") as Uint8Array,
    revealLevel: toTypedColumn(table, "reveal_level") as Int16Array,
  };
}

function unpackPapersIndex(table: Table): PaperMeta[] {
  const out: PaperMeta[] = new Array(table.numRows);
  for (let i = 0; i < table.numRows; i++) {
    const row = table.get(i)!;
    const year = row.year ?? 0;
    // Index rows are node-ordered, but key by node_id defensively.
    out[row.node_id] = {
      title: row.title,
      citedByCount: row.cited_by_count,
      authorIds: row.author_ids ? Array.from(row.author_ids) : [],
      publicationDate: year ? String(year) : "",
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

  // Neighbors and per-paper detail are NOT fetched here — they load on demand per selection.
  // The resident papers-index holds title/year/citations/author_ids for all papers.
  const [pointsT, papersIndexT, authorsT, edgesT] = await Promise.all([
    fetchArrow("points.arrow"),
    fetchArrow("papers-index.arrow"),
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
  const papers = unpackPapersIndex(papersIndexT);

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
    }),
    null,
  );

  const dataset: Dataset = {
    manifest,
    points,
    papers,
    clusters,
    labels,
    orgs,
    topics,
    authors: unpackAuthors(authorsT),
    getNeighbors,
    getPaperDetail,
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
