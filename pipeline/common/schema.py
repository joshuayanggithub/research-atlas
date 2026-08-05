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


ALL_FILES = [
    MANIFEST, POINTS, PAPERS, NEIGHBORS, EDGES, AUTHORS,
    CLUSTERS, LABELS, ORGS, TOPICS,
]

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
])

# Display metadata keyed by node_id. Abstract is NOT shipped (size); linked via doi/arxiv.
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


class Institution(BaseModel):
    openalex_id: str
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
    palette: dict
