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
  count: number;
  node_ids: number[];
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
  palette: { background: number[] };
}

// Columnar point data, unpacked from points.arrow into typed arrays for deck.gl.
export interface PointData {
  count: number;
  nodeId: Int32Array;
  x: Float32Array;
  y: Float32Array;
  year: Int16Array;
  citedByCount: Int32Array;
  subfieldId: Int16Array;
  topicId: Int32Array;
  r: Uint8Array;
  g: Uint8Array;
  b: Uint8Array;
}

// Per-paper display metadata (papers.arrow), accessed by node_id.
export interface PaperMeta {
  paperId: string;
  title: string;
  publicationDate: string;
  doi: string | null;
  arxivId: string | null;
  venue: string | null;
  citedByCount: number;
  authorIds: number[];
  authorNames: string[];
}

export interface AuthorRow {
  authorId: number;
  name: string;
  count: number;
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
  neighbors: { ids: Int32Array[]; scores: Float32Array[] };
  // adjacency built at load from edges.arrow
  citesOut: Map<number, number[]>;
  citedBy: Map<number, number[]>;
}
