// TypeScript mirror of the pipeline artifact contract (pipeline/common/schema.py).
// Keep these in sync with that file — it is the single seam between pipeline and web.

export interface LevelBand {
  level: number;
  zoom_min: number;
  zoom_max: number;
}

export interface TopicRef {
  level: string;
  id: number;
}

export interface Cluster {
  id: number;
  level: number;
  parent: number | null;
  children: number[];
  x: number;
  y: number;
  count: number;
  bbox: [number, number, number, number];
  color: [number, number, number];
  label: string;
  topic_ref: TopicRef | null;
}

export interface ClustersDoc {
  levels: LevelBand[];
  clusters: Cluster[];
}

export interface Label {
  id: number;
  x: number;
  y: number;
  text: string;
  level: number;
  priority: number;
  count: number;
}

export interface LabelsDoc {
  labels: Label[];
}

export interface Institution {
  openalex_id: string;
  display_name: string;
  ror: string | null;
  type: string;
  kind: string;
  lineage: number[];
  count: number; // rollup count (this org/unit + all descendants), deduplicated
  node_ids: number[]; // rollup node ids
  // Directory hierarchy (evidence-backed dept/lab sub-units).
  parent: string | null; // parent org key, or null for a root org
  unit_type: string; // organization | school | department | institute | lab | ...
  children: string[]; // child unit keys
  direct_count: number; // size of this unit's own evidence set (node_ids list not shipped)
  curated: boolean; // curated seed org/unit (drives tree + color) vs. directory-only entry
}

export interface OrgsDoc {
  institutions: Record<string, Institution>;
}

export interface TopicNode {
  id: number;
  name: string;
  level: string;
  parent: number | null;
}

export interface TopicsDoc {
  nodes: TopicNode[];
}

export interface FileMeta {
  path: string;
  bytes: number;
  sha256: string;
  rows: number | null;
}

export interface Manifest {
  schema_version: number;
  built_at: string;
  corpus: {
    count: number;
    date_from: string;
    date_to: string;
    field: string;
    orgs: string[];
  };
  embedding: { backend: string; model: string; dim: number };
  projector: Record<string, unknown>;
  levels: LevelBand[];
  files: Record<string, FileMeta>;
  // Related-works neighbors are sharded for on-demand loading: shard = node_id // size.
  // 0 means the legacy whole neighbors.arrow.
  neighbor_shard_size?: number;
  n_neighbor_shards?: number;
  // Paper detail is sharded the same way; the resident index is papers-index.arrow.
  paper_shard_size?: number;
  n_paper_shards?: number;
  palette: { background: number[] };
}

export interface NeighborList {
  ids: Int32Array;
  scores: Float32Array;
}

// Columnar point data, unpacked from points.arrow into typed arrays for deck.gl.
export interface PointData {
  count: number;
  nodeId: Int32Array;
  x: Float32Array;
  y: Float32Array;
  year: Int16Array;
  // Months since the corpus start month (from papers.arrow publication_date), enabling
  // month-granularity date filtering on the GPU. Derived at load, not shipped in Arrow.
  monthIndex: Int16Array;
  citedByCount: Int32Array;
  subfieldId: Int16Array;
  topicId: Int32Array;
  r: Uint8Array;
  g: Uint8Array;
  b: Uint8Array;
  // Coarsest zoom level at which a point may render (s12 greedy thinning). The map draws a
  // point only when its reveal_level <= the active zoom level, which guarantees no two
  // visible points overlap at any zoom. A large sentinel means "not yet loaded".
  revealLevel: Int16Array;
}

// Resident per-paper index (papers-index.arrow), accessed by node_id for every paper.
// Holds only what search, list rows, sorting, and the author filter need; heavier fields
// (author names, venue, ids) live in PaperDetail and load on demand.
export interface PaperMeta {
  title: string;
  citedByCount: number;
  authorIds: number[];
  // Publication YEAR only (from points/index). Full ISO date is in PaperDetail. Kept as a
  // string ("2017" / "") so existing `.publicationDate.slice(0,4)` call sites still work.
  publicationDate: string;
}

// Lazy per-node detail (papers-detail-<shard>.arrow), fetched only for the selected paper.
export interface PaperDetail {
  paperId: string;
  publicationDate: string; // full ISO yyyy-mm-dd
  doi: string | null;
  arxivId: string | null;
  venue: string | null;
  authorNames: string[];
}

export interface AuthorRow {
  authorId: number;
  name: string;
  count: number;
}

export interface CitationEdges {
  src: Int32Array;
  dst: Int32Array;
}

export interface Dataset {
  manifest: Manifest;
  points: PointData;
  papers: PaperMeta[];
  clusters: ClustersDoc;
  labels: LabelsDoc;
  orgs: OrgsDoc;
  topics: TopicsDoc;
  authors: AuthorRow[];
  // Related-works neighbors are fetched on demand by node_id (shard = node_id // size),
  // so the ~9MB (→50MB at 390k) neighbor table is not in the initial load. Cached per shard.
  getNeighbors: (node: number) => Promise<NeighborList>;
  // Per-paper detail (author names, venue, ids, full date) is likewise fetched on demand
  // for the selected paper only; the resident `papers` index holds title/year/citations.
  getPaperDetail: (node: number) => Promise<PaperDetail | null>;
  edges: CitationEdges;
  // Adjacency built at load from the same directed edge arrays.
  citesOut: Map<number, number[]>;
  citedBy: Map<number, number[]>;
}
