// Loads the static artifact bundle (Arrow IPC + JSON) into an in-memory Dataset.
// Arrow tables are read zero-copy via apache-arrow's tableFromIPC.

import { Table, tableFromIPC } from "apache-arrow";
import type {
  AuthorRow,
  ClustersDoc,
  Dataset,
  LabelsDoc,
  Manifest,
  OrgsDoc,
  PaperMeta,
  PointData,
  TopicsDoc,
} from "./types";

const BASE = "data";

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
    citedByCount: toTypedColumn(table, "cited_by_count") as Int32Array,
    subfieldId: toTypedColumn(table, "subfield_id") as Int16Array,
    topicId: toTypedColumn(table, "topic_id") as Int32Array,
    r: toTypedColumn(table, "r") as Uint8Array,
    g: toTypedColumn(table, "g") as Uint8Array,
    b: toTypedColumn(table, "b") as Uint8Array,
  };
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

function unpackNeighbors(table: Table) {
  const n = table.numRows;
  const ids: Int32Array[] = new Array(n);
  const scores: Float32Array[] = new Array(n);
  for (let i = 0; i < n; i++) {
    const row = table.get(i)!;
    ids[row.node_id] = row.neighbor_ids ? Int32Array.from(row.neighbor_ids) : new Int32Array();
    scores[row.node_id] = row.scores ? Float32Array.from(row.scores) : new Float32Array();
  }
  return { ids, scores };
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
  return { citesOut, citedBy };
}

export async function loadDataset(): Promise<Dataset> {
  const manifest = await fetchJSON<Manifest>("manifest.json");

  const [pointsT, papersT, authorsT, neighborsT, edgesT] = await Promise.all([
    fetchArrow("points.arrow"),
    fetchArrow("papers.arrow"),
    fetchArrow("authors.arrow"),
    fetchArrow("neighbors.arrow"),
    fetchArrow("edges.arrow"),
  ]);
  const [clusters, labels, orgs, topics] = await Promise.all([
    fetchJSON<ClustersDoc>("clusters.json"),
    fetchJSON<LabelsDoc>("labels.json"),
    fetchJSON<OrgsDoc>("orgs.json"),
    fetchJSON<TopicsDoc>("topics.json"),
  ]);

  const { citesOut, citedBy } = buildAdjacency(edgesT);

  return {
    manifest,
    points: unpackPoints(pointsT),
    papers: unpackPapers(papersT),
    clusters,
    labels,
    orgs,
    topics,
    authors: unpackAuthors(authorsT),
    neighbors: unpackNeighbors(neighborsT),
    citesOut,
    citedBy,
  };
}
