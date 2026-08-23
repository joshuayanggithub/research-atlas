"""The artifact contract — the single seam between the Python pipeline and the web app.

Everything the frontend loads is defined here: the Arrow schemas for the large columnar
files and the pydantic models for the small JSON files. The row index of ``points.arrow``
is the ``node_id`` (dense 0..N-1); every other artifact references papers by that id.

Keeping the contract in one file means a schema change is a single, reviewable edit that
both the emitting stage (s11) and the consuming loader (web/src/data) are checked against.
"""

from __future__ import annotations

from typing import Optional

import pyarrow as pa
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# File names (referenced by manifest + s11 emit + the web loader).
# ---------------------------------------------------------------------------
POINTS = "points.arrow"
PAPERS = "papers.arrow"
NEIGHBORS = "neighbors.arrow"
EDGES = "edges.arrow"
AUTHORS = "authors.arrow"
CLUSTERS = "clusters.json"
LABELS = "labels.json"
ORGS = "orgs.json"
TOPICS = "topics.json"
MANIFEST = "manifest.json"

# Per-reveal-level point tiles (s12/s11): the frontend fetches cumulative levels 0..current
# for the viewport instead of the whole points table, so corpus size stops gating the
# initial download and no two visible points overlap at any zoom.
def points_tile(level: int) -> str:
    return f"points-L{level}.arrow"


# Neighbors (related-works kNN) are sharded by fixed node-id block so the frontend fetches
# only the shard for a selected paper on demand, instead of the whole ~9MB (→50MB at 390k)
# table up front. shard index = node_id // NEIGHBOR_SHARD_SIZE, computed with no lookup.
NEIGHBOR_SHARD_SIZE = 4096


def neighbors_shard(shard: int) -> str:
    return f"neighbors-{shard}.arrow"


# Resident papers search/list index (title + author_ids + cited_by_count + year, all N).
PAPERS_INDEX = "papers-index.arrow"

# Baked "first figure" crops (s13): one PNG per paper that has a locatable Figure 1/Table 1,
# extracted offline with PyMuPDF and served on demand. Sharded by node-id block into subdirs
# so no single directory holds the whole corpus. The resident papers index carries a
# `has_figure` flag, so the frontend fetches a crop only when one exists (no 404 probing).
FIGURE_SHARD_SIZE = 4096
FIGURES_DIR = "figures"


def figure_path(node_id: int) -> str:
    return f"{FIGURES_DIR}/{node_id // FIGURE_SHARD_SIZE}/{node_id}.png"

# Lazy per-node paper detail, sharded by fixed node-id block (same scheme as neighbors):
# shard = node_id // PAPER_SHARD_SIZE, fetched only for the selected paper.
PAPER_SHARD_SIZE = 4096


def paper_detail_shard(shard: int) -> str:
    return f"papers-detail-{shard}.arrow"


# NOTE: CLUSTERS is written to data/artifacts (pipeline-internal, source of manifest levels)
# but is intentionally NOT shipped to web/public/data — the frontend never reads it.
ALL_FILES = [
    MANIFEST, POINTS, PAPERS, PAPERS_INDEX, NEIGHBORS, EDGES, AUTHORS,
    LABELS, ORGS, TOPICS,
]

# Artifacts that stay on the build machine and are NEVER uploaded to a remote host.
#
# `papers.arrow` is the pre-D23 whole-paper table (276 MB at 1M papers). D23 split what the app
# reads into papers-index.arrow (counts/flags), papers-titles-N.arrow (titles) and
# papers-detail-N.arrow (authors/venue/ids), and nothing has fetched the original since. It is
# still emitted because it is the one place every field sits together, which makes it useful for
# offline inspection and for rebuilding the split files — but it is dead weight to a browser and
# it exceeds GitHub's 100 MB per-file limit, so it must not reach a remote repo or a CDN.
#
# Anything added here must also be absent from the manifest, or the frontend will try to fetch it.
LOCAL_ONLY_FILES = frozenset({PAPERS})

# ---------------------------------------------------------------------------
# Arrow schemas (large columnar artifacts).
# ---------------------------------------------------------------------------

# One row per paper; ROW INDEX == node_id. Ships to the browser in full.
POINTS_SCHEMA = pa.schema([
    ("node_id", pa.int32()),
    ("x", pa.float32()),
    ("y", pa.float32()),
    ("year", pa.int16()),
    ("cited_by_count", pa.int32()),
    ("cluster_leaf", pa.int32()),
    ("domain_id", pa.int8()),
    ("field_id", pa.int16()),
    ("subfield_id", pa.int16()),
    ("topic_id", pa.int32()),
    ("r", pa.uint8()),
    ("g", pa.uint8()),
    ("b", pa.uint8()),
    # Coarsest zoom level at which this point becomes visible (s12). Papers are split into
    # per-level tile files by this column; the full points.arrow keeps it for reference.
    ("reveal_level", pa.int16()),
    # Months since corpus start (from publication_date). Lives here — not derived from
    # papers at load — so month-granularity date filtering needs no paper metadata, which
    # is now fetched on demand (Phase B). Clamped >= 0.
    ("month_index", pa.int16()),
    # Deepest hierarchy cell containing this paper (s06 tiles.json). With the cell parent chain
    # in regions.arrow this gives EXACT region membership, so clicking a map label can select
    # the papers that label actually covers. -1 when the paper is in no cell.
    ("region_leaf", pa.int32()),
])

# --- Papers: split into a resident search/list INDEX + lazy per-node DETAIL (Phase B) ----
# The index ships whole (search, list rows, sorting, author filter all need it); detail is
# sharded and fetched only for the selected paper, so the heavy title-adjacent fields
# (author_names, venue, ...) stay out of the initial download.

# Resident index, one row per paper; ROW INDEX == node_id. Ships in full.
PAPERS_INDEX_SCHEMA = pa.schema([
    ("node_id", pa.int32()),
    ("cited_by_count", pa.int32()),
    ("year", pa.int16()),
    # Whether this row has a provider-backed citation count. ``cited_by_count == 0`` is a
    # real value only when this is true; unmatched rows must render as unavailable.
    ("citation_count_available", pa.bool_()),
    # False when NO provider supplied a reference list for this paper, so the UI can say
    # "no reference data" instead of rendering an empty References tab. Distinct from "has
    # references, none of which are in this corpus". 7.3% of the 912k corpus.
    ("references_available", pa.bool_()),
    # True when s13 baked a first-figure crop for this paper (figure_path(node_id) exists).
    # Lets the frontend fetch the crop only when present; absent/false ⇒ client-side fallback.
    ("has_figure", pa.bool_()),
])

# Lazy per-node detail, sharded by node-id block like neighbors. Fetched on selection.
PAPER_DETAIL_SCHEMA = pa.schema([
    ("node_id", pa.int32()),
    ("paper_id", pa.string()),
    ("publication_date", pa.string()),
    ("doi", pa.string()),
    ("arxiv_id", pa.string()),
    ("venue", pa.string()),
    ("author_names", pa.list_(pa.string())),
    # Moved here from papers-index: only the details panel needs a paper's author ids, and it
    # already fetches this shard on selection. In the eager index they cost 18.2 MB.
    ("author_ids", pa.list_(pa.int32())),
    # How many works this paper cites IN TOTAL, per Semantic Scholar — not just the ones inside
    # this corpus. The References tab can only draw intra-corpus edges, so without the total a
    # paper citing 18 works of which 5 are arXiv CS reads as "5 references", which is wrong about
    # the paper rather than honest about the map. -1 means S2 has no reference list at all.
    ("reference_count", pa.int32()),
])

# Legacy full papers table (kept for reference / any non-tiled fallback path).
PAPERS_SCHEMA = pa.schema([
    ("node_id", pa.int32()),
    ("paper_id", pa.string()),        # OpenAlex work id (short form, e.g. "W123")
    ("title", pa.string()),
    ("publication_date", pa.string()),  # ISO yyyy-mm-dd
    ("doi", pa.string()),             # nullable
    ("arxiv_id", pa.string()),        # nullable
    ("venue", pa.string()),           # nullable
    ("cited_by_count", pa.int32()),
    ("primary_topic_id", pa.int32()),
    ("author_ids", pa.list_(pa.int32())),
    ("author_names", pa.list_(pa.string())),
    ("institution_ids", pa.list_(pa.int32())),
])

# Related-works kNN on fused (text + citation) similarity.
NEIGHBORS_SCHEMA = pa.schema([
    ("node_id", pa.int32()),
    ("neighbor_ids", pa.list_(pa.int32())),
    ("scores", pa.list_(pa.float32())),
])

# Directed intra-corpus citations (both endpoints resolved to node_ids).
EDGES_SCHEMA = pa.schema([
    ("src", pa.int32()),  # citing
    ("dst", pa.int32()),  # cited
])

# Author index for autocomplete / resolution.
AUTHORS_SCHEMA = pa.schema([
    ("author_id", pa.int32()),      # dense local id
    ("openalex_id", pa.string()),
    ("name", pa.string()),
    ("count", pa.int32()),          # papers in corpus
])

# ---------------------------------------------------------------------------
# JSON models (small structured artifacts). These serialize to the *.json files.
# ---------------------------------------------------------------------------


class LevelBand(BaseModel):
    """Maps a semantic-zoom band to a viewport zoom range."""

    level: int
    zoom_min: float
    zoom_max: float


class TopicRef(BaseModel):
    level: str  # "domain" | "field" | "subfield" | "topic"
    id: int


class Cluster(BaseModel):
    id: int
    level: int
    parent: Optional[int] = None
    children: list[int] = Field(default_factory=list)
    x: float
    y: float
    count: int
    bbox: list[float]  # [x0, y0, x1, y1]
    color: list[int]   # [r, g, b]
    label: str
    topic_ref: Optional[TopicRef] = None


class ClustersDoc(BaseModel):
    levels: list[LevelBand]
    clusters: list[Cluster]


class Label(BaseModel):
    id: int
    x: float
    y: float
    text: str
    level: int      # semantic band (0 coarse .. hierarchy.max_depth-1 fine)
    priority: float  # decluttering rank; higher wins scarce screen space
    count: int


class LabelsDoc(BaseModel):
    labels: list[Label]


class TopAuthor(BaseModel):
    """A prolific researcher within one org unit, precomputed by s10.

    The frontend used to derive this in the browser from each paper's author_ids, but those
    lists left the resident papers index in D30 (they now ship per-paper on demand), which left
    the org researcher list silently empty. An org shows at most a dozen names, so the honest
    fix is to compute them once, offline, where the author lists actually live.
    """
    author_id: int
    name: str
    count: int


class Institution(BaseModel):
    openalex_id: str
    # Stable identity for sources beyond OpenAlex (canonical ROR or a curated local: id).
    organization_id: Optional[str] = None
    display_name: str
    ror: Optional[str] = None
    type: str = "education"  # education | company | facility | ...
    kind: str = "university"  # our grouping: industry | university | neolab
    lineage: list[int] = Field(default_factory=list)  # local institution ids (self + parents)
    count: int = 0
    node_ids: list[int] = Field(default_factory=list)
    # Directory hierarchy (evidence-backed department/lab sub-units, docs/ORGANIZATION_DIRECTORY.md).
    parent: Optional[str] = None  # parent org key, or None for a root org
    unit_type: str = "organization"  # organization | school | department | institute | lab | ...
    children: list[str] = Field(default_factory=list)  # child unit keys
    # node_ids is the ROLLUP set (this unit + all descendants), deduplicated. direct_count is
    # the size of the unit's OWN evidence set (== count for leaf units and for parents whose
    # rollup is just their institution set). The frontend never needs the direct node-id list
    # (it filters on node_ids), so we do NOT ship direct_node_ids — it was a 100% duplicate of
    # node_ids and ~31% of orgs.json.
    direct_count: int = 0
    # Curated seed org (or its reviewed sub-unit) vs. an auto-included corpus institution.
    # Curated entries drive the hierarchy tree and color-by-org; directory entries are
    # search-and-filter only. See docs/ORGANIZATION_DIRECTORY.md.
    curated: bool = True
    # Which org-nodes shard holds this entry's node_ids, for directory entries whose ids were
    # moved out of the published orgs.json. None means the ids are inline (every curated
    # entry, and every entry in the build-machine copy under data/artifacts).
    node_shard: Optional[int] = None
    # How papers were attributed to this entry. Empty means OpenAlex institution
    # authorship; roster-backed neolabs carry explicit reviewed claim provenance.
    membership_methods: list[str] = Field(default_factory=list)
    # Most prolific researchers in this unit, precomputed (see TopAuthor).
    top_authors: list[TopAuthor] = Field(default_factory=list)
    # Of `count`, how many papers are attributed ONLY by model extraction from the PDF (COMET,
    # 91% precision / 81% recall) rather than by publisher-asserted authorship (98-100%). Kept
    # separate so the UI can say which, instead of blending two very different confidences.
    extracted_count: int = 0


class OrgsDoc(BaseModel):
    # institution local-id (as string key for JSON) -> Institution
    institutions: dict[str, Institution]


class TopicNode(BaseModel):
    id: int
    name: str
    level: str  # domain | field | subfield | topic
    parent: Optional[int] = None


class TopicsDoc(BaseModel):
    # level -> {id -> name}; parent links flattened into nodes for the frontend legend.
    nodes: list[TopicNode]


class CorpusMeta(BaseModel):
    count: int
    date_from: str
    date_to: str
    field: str
    orgs: list[str]
    # Null means the bundle has no provider-backed data for this metric. This is distinct
    # from a real zero and prevents arXiv metadata's missing citation fields being presented
    # as "0 citations". Counts and the directed graph may come from different providers.
    citation_count_source: Optional[str] = None
    citation_graph_source: Optional[str] = None
    # Metadata enrichment is independent from corpus discovery and citation truth. Coverage
    # is the fraction of canonical corpus rows with an exact provider match.
    metadata_enrichment_source: Optional[str] = None
    metadata_enrichment_coverage: Optional[float] = None


class EmbeddingMeta(BaseModel):
    backend: str
    model: str
    dim: int


class FileMeta(BaseModel):
    path: str
    bytes: int
    sha256: str
    rows: Optional[int] = None


class PointTile(BaseModel):
    """One per-reveal-level point tile the frontend fetches on demand (s12/s11)."""

    level: int
    path: str
    rows: int          # papers newly revealed at this level
    cumulative: int    # papers visible once levels 0..this are loaded
    bytes: int


class EdgeTile(BaseModel):
    """One per-reveal-level slice of the citation graph.

    An edge is only drawable when BOTH endpoints are on screen, so an edge belongs to the tier
    of its deeper endpoint: ``max(reveal_level[src], reveal_level[dst])``. Loading tiers 0..N
    alongside point tiles 0..N therefore yields exactly the edges that can be drawn, and no
    more. Measured on the 1,000,490-paper corpus: the home view needs 408 edges (3 KB), the
    eager depth (L<=4) needs 400,471 (1.8 MB gzipped), and the whole graph is 14,303,089
    (87 MB gzipped) — which is what every visit used to download before it drew anything.
    """

    level: int
    path: str
    rows: int          # edges that become drawable at this level
    cumulative: int    # edges drawable once levels 0..this are loaded
    bytes: int


class Manifest(BaseModel):
    schema_version: int
    built_at: str
    corpus: CorpusMeta
    embedding: EmbeddingMeta
    projector: dict
    levels: list[LevelBand]
    files: dict[str, FileMeta]
    # Present when the corpus is shipped as fetch-on-demand reveal-level tiles. Ordered by
    # level (0 = coarsest). Empty/omitted for a legacy single-points.arrow bundle.
    point_tiles: list[PointTile] = []
    edge_tiles: list[EdgeTile] = []
    # s12's thinning constant. The frontend derives its max dot radius from it, because the
    # on-screen separation the thinning guarantees is viewport_width / base_divisor.
    tiling_base_divisor: float = 40.0
    # Neighbors (related-works) shard block size; 0 means neighbors.arrow is shipped whole
    # (legacy). When >0 the frontend loads shard (node_id // size) on demand.
    neighbor_shard_size: int = 0
    n_neighbor_shards: int = 0
    # Paper-detail shard block size; 0 means papers.arrow is shipped whole (legacy). When >0
    # the frontend loads the resident papers-index.arrow whole and fetches per-node detail
    # from shard (node_id // size) on demand.
    paper_shard_size: int = 0
    # Inverted author index (author-papers-N.arrow); 0 means the bundle predates it.
    author_papers_shard_size: int = 0
    title_chunk_rows: int = 0
    n_title_chunks: int = 0
    n_author_chunks: int = 0
    has_import_index: bool = False
    # Papers per month over the WHOLE corpus, indexed from the corpus's first month. 428 ints,
    # so the date histogram can show the true distribution without waiting for point tiles —
    # which are ordered by importance, and therefore hide the sparse early years longest.
    month_histogram: list[int] = Field(default_factory=list)
    n_position_shards: int = 0
    position_shard_rows: int = 0
    # Directory-org membership lives in org-nodes-{N}.arrow, not orgs.json (see ORG_SHARD_ORGS).
    n_org_shards: int = 0
    org_shard_orgs: int = 0
    author_chunk_rows: int = 0
    n_indexed_authors: int = 0
    n_paper_shards: int = 0
    # First-figure crops (s13). Present only when the figure stage ran. `count` papers have a
    # baked crop under `dir`/<node_id // shard_size>/<node_id>.png; the resident papers index
    # `has_figure` flag says which. Absent/empty ⇒ frontend uses the client-side pdf.js path.
    figures: Optional["FiguresMeta"] = None
    palette: dict


class FiguresMeta(BaseModel):
    dir: str = FIGURES_DIR
    shard_size: int = FIGURE_SHARD_SIZE
    count: int = 0  # number of papers with a baked crop


# Cell tree for exact region membership: one row per hierarchy cell (285,316 at 912k), ~1.4 MB.
# Shipping this plus points.region_leaf costs ~5 MB, against ~35 MB to ship every cell's
# node_idx list outright.
REGIONS = "regions.arrow"
REGIONS_SCHEMA = pa.schema([
    ("id", pa.int32()),
    ("parent", pa.int32()),   # -1 at the root
    ("level", pa.int16()),
])


# Inverted author index: one row per author with the nodes they wrote.
#
# The author FILTER previously scanned every paper's author_ids — 18.2 MB shipped eagerly plus a
# 912k-row scan per filter change. Inverting it makes selecting an author a direct lookup of the
# few rows that matter, and the per-paper lists move to the detail shards where they are already
# fetched lazily. Sharded by author_id // size so one author costs one small fetch.
AUTHOR_PAPERS_SHARD_SIZE = 8000  # ~0.9 MB/shard: one author filter should not pull 5.5 MB

def author_papers_shard(shard: int) -> str:
    return f"author-papers-{shard}.arrow"

AUTHOR_PAPERS_SCHEMA = pa.schema([
    ("author_id", pa.int32()),
    ("node_ids", pa.list_(pa.int32())),
    # Carried here rather than in the search index: it is 22.5 MB (40.3%) of authors.arrow but
    # is only ever needed to build one author's OpenAlex link, and the author panel only renders
    # while an author filter is active — which means this shard is already loaded.
    ("openalex_id", pa.string()),
])


# Titles, split into sequential chunks so the browser can fill them in progressively.
#
# Titles are 71.6 MB of papers-index (28.1 MB gzipped, ~28s on a 1 MB/s link) and they cannot be
# sharded by node the way detail is: search needs every title, and a citation panel's papers are
# scattered across the corpus, so node-sharding would cost ~11 MB of fetches to render one panel.
# Chunking instead keeps the total identical but lets titles appear as each chunk lands rather
# than all at once at the end.
TITLE_CHUNK_ROWS = 60000

def titles_chunk(chunk: int) -> str:
    return f"papers-titles-{chunk}.arrow"

TITLES_SCHEMA = pa.schema([
    ("node_id", pa.int32()),
    ("title", pa.string()),
])


# Author search index, split into chunks so name matching becomes usable progressively.
# Dropping openalex_id (see AUTHOR_PAPERS_SCHEMA) takes the whole index from 20.1 to 12.8 MB
# gzipped; chunking means the first names are searchable in ~1s instead of ~13s.
AUTHOR_CHUNK_ROWS = 120000

def authors_chunk(chunk: int) -> str:
    return f"authors-{chunk}.arrow"


# Directory institutions' node_ids, moved out of orgs.json.
#
# 94% of orgs.json is `node_ids` — 1,489,472 ids across 10,518 institutions — and 1,370,907 of
# those belong to the 10,475 DIRECTORY entries, which are search-only: nothing reads their
# membership until someone selects one. Shipping them eagerly cost 5.05 MB gzipped on the
# critical path to answer a question almost no visit asks. Slimmed, orgs.json is 0.64 MB.
#
# Sharded by a dense index over directory keys (sorted, so it is stable across builds) rather
# than one file per org: 10,475 tiny files would trade the bytes for request count, which is
# what the object store actually bills. At 128 orgs per shard a selection costs one ~30 KB
# fetch and the whole set is 82 files.
ORG_SHARD_ORGS = 128

ORG_NODES_SCHEMA = pa.schema([
    ("org_key", pa.string()),
    ("node_ids", pa.list_(pa.int32())),
])


def org_nodes_shard(shard: int) -> str:
    return f"org-nodes-{shard}.arrow"


def edges_tile(level: int) -> str:
    return f"edges-L{level}.arrow"

# `verified` replaces openalex_id for the one thing the resident index still needs it for:
# telling a real OpenAlex identity from a name-hash fallback. One byte instead of ~27.
# Lookup for importing a personal reading list (Zotero et al.): external identifier -> node_id.
# Fetched only when the user actually imports, never at startup, because it is ~14 MB raw.
# arXiv id is the only external id shipped: every paper has one (the corpus is arXiv-spined;
# 10,061 are old-style archive/YYMMNNN ids, which the matcher handles), the arXiv DOI
# is derivable from it (10.48550/arXiv.<id>), and titles are already in the browser (D30 title
# chunks) so title matching needs no artifact at all. Only 1.7% of the papers this feature is
# likely to miss carry a non-arXiv DOI, which is not worth another 25 MB.
# Point rows sharded by node_id, so a FILTER can fetch positions for the specific papers it
# matched instead of downloading every reveal level. The reveal-level tiles are ordered by
# importance, so an arbitrary selection (a reading list, one author) is scattered across all of
# them — 42 MB to place 19 dots. Sharded by id, the same 19 dots cost ~19 small files.
# 2048 rows keeps a shard around 86 KB: small enough that fetching one to place a single paper
# is not wasteful, large enough that a few-hundred-paper filter stays a sane number of requests.
POSITION_SHARD_ROWS = 2048


def points_shard(shard: int) -> str:
    return f"points-by-node-{shard}.arrow"


IMPORT_INDEX = "import-index.arrow"

IMPORT_INDEX_SCHEMA = pa.schema([
    ("node_id", pa.int32()),
    ("arxiv_id", pa.string()),   # version suffix stripped; "" when unknown
])


AUTHORS_SEARCH_SCHEMA = pa.schema([
    ("author_id", pa.int32()),
    ("name", pa.string()),
    ("count", pa.int32()),
    ("verified", pa.bool_()),
])
