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
  organization_id: string | null; // canonical ROR or curated local: identity
  display_name: string;
  ror: string | null;
  type: string;
  kind: string;
  lineage: number[];
  count: number; // rollup count (this org/unit + all descendants), deduplicated
  // Rollup node ids. EMPTY for directory entries in the published bundle — their membership
  // is 94% of orgs.json and nothing reads it until the org is selected, so it moved to
  // org-nodes-{N}.arrow. Use useOrgNodes(), never `node_ids` directly, for a directory org.
  node_ids: number[];
  /** Shard holding this entry's node_ids when they are not inline. Null for curated entries. */
  node_shard?: number | null;
  // Directory hierarchy (evidence-backed dept/lab sub-units).
  parent: string | null; // parent org key, or null for a root org
  unit_type: string; // organization | school | department | institute | lab | ...
  children: string[]; // child unit keys
  direct_count: number; // size of this unit's own evidence set (node_ids list not shipped)
  curated: boolean; // curated seed org/unit (drives tree + color) vs. directory-only entry
  membership_methods: string[]; // empty = OpenAlex affiliation; otherwise roster provenance
  // Precomputed by s10. Per-paper author_ids left the resident index in D30, so the browser
  // can no longer count them; an org shows a dozen names, so they are counted offline instead.
  top_authors?: { author_id: number; name: string; count: number }[];
}

export interface OrgsDoc {
  institutions: Record<string, Institution>;
}

export interface TopicNode {
  id: number;
  name: string;
  level: string;
  parent: number | null;
  /** Papers in the corpus under this node (a field sums its subfields). Counted offline by
   *  s10 — the browser can only count downloaded points, and tiles are importance-ordered. */
  count?: number;
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

/** The hierarchy cell tree (regions.arrow), indexed by cell id. */
export interface RegionTree {
  parent: Int32Array;
  level: Int16Array;
}

export interface PointTileMeta {
  level: number;
  path: string;
  rows: number;
  cumulative: number;
  bytes: number;
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
    // Missing/null means the bundle has no provider-backed citation value. Do not render
    // the numeric compatibility column (zero) as a factual count in that case.
    citation_count_source?: string | null;
    citation_graph_source?: string | null;
    metadata_enrichment_source?: string | null;
    metadata_enrichment_coverage?: number | null;
  };
  embedding: { backend: string; model: string; dim: number };
  projector: Record<string, unknown>;
  levels: LevelBand[];
  files: Record<string, FileMeta>;
  /** One Arrow file per reveal level, so points can be fetched progressively (s11). */
  point_tiles?: PointTileMeta[];
  /** One Arrow file per reveal level for citation edges, loaded alongside the point tile of
   *  the same level: an edge is drawable only when both its endpoints are. */
  edge_tiles?: PointTileMeta[];
  /** Per-node adjacency shards (edges-by-node-N.arrow), keyed by `position_shard_rows`.
   *  0/absent means the bundle predates them and the whole graph is the only complete source. */
  n_edge_node_shards?: number;
  /** Alphabetical ranges of the title search index chunks (title-tokens-N.arrow). */
  search_chunks?: { chunk: number; first: string; last: string; tokens: number; bytes: number }[];
  /** s12 thinning constant; on-screen point separation is viewport_width / this. */
  tiling_base_divisor?: number;
  // Related-works neighbors are sharded for on-demand loading: shard = node_id // size.
  // 0 means the legacy whole neighbors.arrow.
  neighbor_shard_size?: number;
  n_neighbor_shards?: number;
  // Paper detail is sharded the same way; the resident index is papers-index.arrow.
  paper_shard_size?: number;
  /** Inverted author index (author-papers-N.arrow); 0/absent means the bundle predates it. */
  author_papers_shard_size?: number;
  /** Titles ship as sequential chunks so they can fill in progressively. */
  title_chunk_rows?: number;
  n_author_chunks?: number;
  has_import_index?: boolean;
  month_histogram?: number[];
  n_position_shards?: number;
  position_shard_rows?: number;
  author_chunk_rows?: number;
  n_title_chunks?: number;
  n_indexed_authors?: number;
  n_paper_shards?: number;
  // First-figure crops baked by the pipeline (s13). Present only when the figure stage ran;
  // a crop lives at `${dir}/${node_id / shard_size | 0}/${node_id}.png`. The resident papers
  // index `hasFigure` flag says which papers have one; absent ⇒ client-side pdf.js fallback.
  figures?: { dir: string; shard_size: number; count: number };
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
  // Months since the corpus start month (from the corpus publication_date), enabling
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
  /** Deepest hierarchy cell containing the paper; -1 if none. Pairs with Dataset.regions. */
  regionLeaf: Int32Array;
}

// Resident per-paper index (papers-index.arrow), accessed by node_id for every paper.
// Holds only what search, list rows, sorting, and the author filter need; heavier fields
// (author names, venue, ids) live in PaperDetail and load on demand.
export interface PaperMeta {
  title: string;
  citedByCount: number;
  // A zero count is meaningful only when the upstream citation provider matched this paper.
  citationCountAvailable: boolean;
  /** False when no provider supplied a reference list at all (vs. having none in-corpus). */
  referencesAvailable: boolean;
  authorIds: number[];
  // Publication YEAR only (from points/index). Full ISO date is in PaperDetail. Kept as a
  // string ("2017" / "") so existing `.publicationDate.slice(0,4)` call sites still work.
  publicationDate: string;
  /** Whether the date above is KNOWN. An empty string means "not downloaded yet" until the
   *  papers index lands, and "this paper genuinely has no date" afterwards; rendering both as
   *  an em dash made 47 of one author's 58 rows claim a missing year for ~2 minutes. Same
   *  distinction `citationCountAvailable` draws for a zero count. */
  dateAvailable: boolean;
  // True when the pipeline baked a first-figure crop for this paper (manifest.figures + the
  // sharded PNG). Lets the details card load the baked crop instead of parsing the PDF.
  hasFigure: boolean;
}

// Lazy per-node detail (papers-detail-<shard>.arrow), fetched only for the selected paper.
export interface PaperDetail {
  /** Local author ids, moved here from the eager index (18.2 MB saved). */
  authorIds: number[];
  paperId: string;
  publicationDate: string; // full ISO yyyy-mm-dd
  doi: string | null;
  arxivId: string | null;
  venue: string | null;
  authorNames: string[];
  /** Total works this paper cites per S2, or -1 when S2 has no reference list for it. The
   *  References tab can only draw the subset that is also in this corpus. */
  referenceCount: number;
}

export interface AuthorRow {
  authorId: number;
  name: string;
  count: number;
  /** True when this is a real OpenAlex identity rather than a name-hash fallback. The id
   *  itself lives in the author-papers shard (D32) — see loadArtifacts.peekAuthorOpenAlex. */
  verified: boolean;
}

export interface CitationEdges {
  src: Int32Array;
  dst: Int32Array;
}

export interface Dataset {
  manifest: Manifest;
  points: PointData;
  papers: PaperMeta[];
  labels: LabelsDoc;
  orgs: OrgsDoc;
  topics: TopicsDoc;
  // Loaded on first use, not at startup: authors.arrow is 34.6 MB / 829k rows and unpacking
  // it cost ~50% of total load time while nothing on first paint needs it.
  /** Cell tree for exact label-region membership; empty until regions.arrow lands. */
  regions: RegionTree;
  getAuthors: () => Promise<AuthorRow[]>;
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
