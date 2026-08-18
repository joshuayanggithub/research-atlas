"""Typed configuration loader.

Reads ``config.yaml`` (the single source of truth) plus ``.env`` for secrets, and
exposes a validated :class:`Config` object that every pipeline stage consumes.

Usage::

    from pipeline.config import load_config
    cfg = load_config()            # reads ./config.yaml + ./.env
    cfg = load_config("other.yaml")
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Repo root = parent of the ``pipeline`` package directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

# Canonical on-disk locations (kept here so stages never hard-code paths).
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
ARTIFACTS_DIR = DATA_DIR / "artifacts"
CACHE_DIR = REPO_ROOT / "pipeline" / ".cache"
WEB_DATA_DIR = REPO_ROOT / "web" / "public" / "data"

# The full corpus (s02 output) vs the ACTIVE corpus that s04..s11 consume. s03 derives
# the active corpus from the full one (compacting it in "drop" mode), so re-running s03 is
# idempotent — it always starts from the full corpus.
CORPUS_FULL = INTERIM_DIR / "corpus.parquet"
CORPUS_ACTIVE = INTERIM_DIR / "corpus_active.parquet"


class OrgSpec(BaseModel):
    key: str
    search: str
    name: Optional[str] = None  # human label for the UI; defaults to search
    ids: list[str] = Field(default_factory=list)  # pinned OpenAlex institution ids
    kind: Literal["industry", "university", "neolab"] = "university"

    @property
    def display_name(self) -> str:
        return self.name or self.search


class CorpusCfg(BaseModel):
    source: Literal["openalex", "arxiv_snapshot"] = "openalex"
    field_id: str = "fields/17"
    date_from: str = "2020-01-01"
    date_to: str = "2026-12-31"
    max_works: int = 40000
    per_page: int = 200
    orgs: list[OrgSpec] = Field(default_factory=list)
    # Corpus scope (s01 fetch predicate):
    #   "orgs"  — only works authored at one of `orgs` (affiliation-gated; the MVP default).
    #   "field" — the WHOLE CS field, no org gate, with a citation floor so the corpus stays
    #             a feasible size. Orgs then act only as UI filters, not as a fetch gate, so
    #             any paper (e.g. arXiv landmarks whose authors aren't at a listed org) can
    #             appear. This is what makes "show all papers regardless of org" real.
    scope: Literal["orgs", "field"] = "orgs"
    # field scope only: keep works with cited_by_count > min_citations, OR published on/after
    # recent_since with cited_by_count > recent_min_citations (a lighter floor so recent work
    # that hasn't accrued citations yet still appears, without flooding the corpus with the
    # ~1.5M uncited papers from the last two years).
    min_citations: int = 25
    recent_since: str = "2025-01-01"
    recent_min_citations: int = 2


class ArxivCfg(BaseModel):
    """Bulk arXiv snapshot plus resumable OAI-PMH deltas."""

    snapshot_path: str = "data/raw/arxiv-metadata-oai-snapshot.json"
    # The Cornell/Kaggle snapshot's most recent OAI datestamp. OAI harvesting is
    # inclusive, and arXiv-id upserts make the overlap harmless.
    snapshot_updated_through: str = "2026-08-08"
    category_prefixes: list[str] = Field(default_factory=lambda: ["cs."])
    categories: list[str] = Field(default_factory=lambda: ["stat.ML"])
    oai_enabled: bool = True
    oai_base_url: str = "https://export.arxiv.org/oai2"
    oai_request_delay: float = 3.0
    oai_timeout: float = 120.0


class OpenAlexEnrichmentCfg(BaseModel):
    """Exact-id OpenAlex enrichment layered onto an arXiv-authoritative corpus."""

    enabled: bool = True
    # OpenAlex accepts at most 100 OR values in one filter. Keeping this at the maximum
    # makes the 271k-paper crosswalk a few thousand requests instead of one per paper.
    batch_size: int = Field(default=100, ge=1, le=100)
    # Requests are I/O-bound. Keep concurrency bounded so bulk enrichment is fast while
    # remaining comfortably below OpenAlex's 100 request/second API ceiling.
    workers: int = Field(default=12, ge=1, le=32)
    # A dedicated free key has enough daily filter credits for this corpus and avoids the
    # heavily shared anonymous pool. Requiring it prevents a full run from predictably
    # spending the small anonymous quota and stopping part-way through.
    require_api_key: bool = True
    request_delay: float = Field(default=0.02, ge=0.0)
    # A completed match remains a valid identity crosswalk, but affiliations/topics evolve.
    # Re-fetch it after this many days (0 disables age-based refresh).
    refresh_days: int = Field(default=30, ge=0)
    # Development/sample cap; 0 means enrich every corpus row.
    max_papers: int = Field(default=0, ge=0)


class SemanticScholarCitationsCfg(BaseModel):
    """Bulk Semantic Scholar citation enrichment for the arXiv spine.

    The S2AG citation dataset is an optional reconciliation source for citation counts and
    directed edges. The Graph API resolves only our arXiv ids to S2 paper hashes; the
    downloadable ``paper-ids`` and ``citations`` datasets supply the large graph locally.
    """

    enabled: bool = False
    # ``latest`` obtains the newest available monthly S2AG release. Pin a concrete release
    # when an exactly reproducible rebuild is required.
    release: str = "latest"
    # S2's Graph API accepts up to 500 paper ids per batch request. This is only an identity
    # crosswalk for our corpus, not a citation crawl.
    resolve_batch_size: int = Field(default=500, ge=1, le=500)
    # The user's introductory key is cumulative 1 RPS across all S2 endpoints. Keep a small
    # cushion and retry server-directed overloads instead of using concurrent requests.
    min_request_interval: float = Field(default=1.1, ge=1.0)
    max_retries: int = Field(default=8, ge=0)
    download_timeout: float = Field(default=120.0, ge=1.0)
    # Development/test cap only. 0 means download and process every citation shard, which is
    # required for globally correct incoming counts.
    max_citation_shards: int = Field(default=0, ge=0)


class EmbeddingCfg(BaseModel):
    backend: Literal["specter2_s2", "specter2_local", "scincl_local"] = "specter2_s2"
    dim: int = 768
    specter2_model: str = "allenai/specter2_base"
    specter2_adapter: str = "allenai/specter2"
    local_batch_size: int = 64
    local_device: Literal["auto", "cuda", "cpu", "mps"] = "auto"
    local_precision: Literal["auto", "fp32", "fp16", "bf16"] = "auto"
    checkpoint_every: int = 2048
    scincl_model: str = "malteos/scincl"
    s2_batch_size: int = 500
    on_uncovered: Literal["drop", "fill_local"] = "drop"
    s2_min_coverage: float = 0.5


class ProjectorCfg(BaseModel):
    method: Literal["opentsne", "umap"] = "opentsne"
    n_neighbors: int = 10
    learning_rate: Optional[float] = None
    random_state: int = 42
    # openTSNE exaggeration held through the whole optimization (not just the early phase).
    # >1 pulls topic clusters apart into visibly separated islands with whitespace between
    # them, which reads far less crowded at the zoomed-out home view. 1.0 = standard t-SNE.
    exaggeration: float = 1.5


class ClusterCfg(BaseModel):
    method: Literal["hdbscan", "hkmeans"] = "hdbscan"
    umap_components: int = 10
    umap_n_neighbors: int = 30
    umap_min_dist: float = 0.0
    hdbscan_min_cluster_size: int = 40
    hdbscan_min_samples: int = 10
    random_state: int = 42


class HierarchyCfg(BaseModel):
    method: Literal["planar", "leiden", "louvain", "kmeans", "quadtree"] = "planar"
    max_depth: int = 11
    root_clusters: int = 8
    branching: int = 3
    min_cluster_size: int = 8
    min_tile_points: int = 3
    max_labels_per_level: int = 320
    direct_citation_weight: float = 0.45
    max_child_fraction: float = 0.68
    # Spatial degree of the planar substrate. Higher = smoother, larger regions; lower =
    # more fragmented. 15 matches the projector's n_neighbors scale.
    planar_k: int = 15
    # legacy quadtree knob (only used when method == "quadtree")
    start_depth: int = 2


class FusedCfg(BaseModel):
    alpha: float = 0.6
    knn_k: int = 15
    text_candidate_multiplier: int = 4
    citation_candidate_multiplier: int = 4
    # s08 builds/queries the HNSW index single-threaded for reproducible artifacts. Parallel
    # hnswlib is not deterministic even with a fixed seed, but single-thread is impractically
    # slow at scale (~30+ min at 400k). Above this N, s08 uses all cores and accepts the
    # minor non-determinism. Set to 0 to always parallelize, or a huge number to never.
    single_thread_max_n: int = 150000
    # Skip second-order citation pivots whose degree exceeds this (0 = no cap). `knn_k` and
    # the multipliers bound the candidate OUTPUT but not the work, which is
    # sum(indeg^2)+sum(outdeg^2) over the graph: on the 13M-edge all-years graph that is 30.9
    # billion inner steps (~21h), 49% of it from five hub papers. A pivot cited by >1000
    # papers is the bibliographic equivalent of a stopword and carries no coupling signal;
    # skipping those is 14x less work and loses nothing, since direct citations are never
    # capped. See fused_similarity.citation_candidates.
    hub_degree_limit: int = 1000


class LabelsCfg(BaseModel):
    ctfidf_candidates: int = 12
    ngram_max: int = 4
    ctfidf_min_gram: int = 2
    use_abstract: bool = True


class PaletteCfg(BaseModel):
    background: list[int] = Field(default_factory=lambda: [12, 14, 20])


class TilingCfg(BaseModel):
    """Reveal-level thinning (s12) → overlap-free semantic zoom + fetch-on-demand tiles."""

    # Level-0 separation radius = layout span / base_divisor. Larger ⇒ sparser home view.
    base_divisor: float = 40.0
    # Max reveal levels; the last is a catch-all for coordinate duplicates.
    max_levels: int = 16
    # Importance signal that orders reveal. "cited_by_count" is raw citations; on the merged
    # 2015-2026 corpus that makes the home view a museum (89% of the top 2,000 predate 2022),
    # so the default divides by age**age_alpha to keep recent work competitive.
    importance: Literal["cited_by_count", "age_adjusted_citations"] = "age_adjusted_citations"
    # Age discount exponent. 0 = raw citations (89% of the top 2k pre-2022), 1 = pure
    # citations-per-year (over-corrects: 42% of the top 2k is 2025 alone). 0.5 measured as
    # the balance point — top-2k median is 747 citations, spread across every year.
    age_alpha: float = 0.5
    # Fraction of the corpus eligible at reveal level 0, by importance; quadruples per level.
    # Without this gate, spatial thinning seeds the home view with whatever tops each empty
    # region — 5-citation papers, since 42.7% of the corpus has zero citations.
    top_fraction: float = 0.002


class EmitCfg(BaseModel):
    """Static bundle emission (s11) — what actually ships to the browser."""

    # Cap the citation edges written to edges.arrow, keeping each paper's top-N strongest
    # links in EACH direction (ranked by the other endpoint's citation count). 0 = uncapped.
    #
    # The browser fetches edges.arrow EAGERLY and builds citesOut/citedBy Maps from it, so the
    # full graph is not free. Measured on the 912k corpus with all 13,006,390 edges:
    # edges.arrow was 99.3 MB (74.7 MB gzipped — it barely compresses, node ids are near-random
    # int32), the eager bundle hit 240 MB / 134 MB gzipped, time-to-first-map went 7.9s -> 27.7s
    # and JS heap reached 1,020 MB. That is unusable, and far worse over a tunnel.
    #
    # Capping per PAPER rather than globally keeps coverage identical — 811,364 papers retain
    # at least one edge at every K tested — and only reduces density for hub papers.
    #   K=2 -> 2.41M edges (14.5 MB gz) | K=4 -> 4.17M (25 MB gz) | K=8 -> 6.75M (40 MB gz)
    #
    # DEFAULT IS NOW 0 (uncapped). Capping at 4 silently truncated reference lists: RIO
    # (arXiv 2605.11564) has 92 references per S2, 43 of them inside the corpus, and the cap
    # shipped 7. Only 38.1% of papers kept a COMPLETE in-corpus reference list; at K=64 that is
    # 98.4% for 75.7 MB gzipped vs 78.0 MB uncapped — so the cap bought almost nothing once
    # edges moved off the critical path (D23) and stopped blocking first paint. A citation map
    # that quietly under-reports references is worse than one that takes longer to fill in.
    max_edges_per_paper: int = 0


class FiguresCfg(BaseModel):
    """First-figure extraction (s13): bake Figure 1 / Table 1 crops from arXiv PDFs offline.

    Disabled by default because it downloads a PDF per arXiv paper, and arXiv's Terms of Use
    cap requests at 1 / 3s with no parallelism — so a full-corpus pass is a multi-hour polite
    batch. Enable for a run (or point at a subset) when you want baked crops; the frontend
    falls back to client-side pdf.js for any paper without one.
    """

    enabled: bool = False
    # Seconds between arXiv PDF requests (arXiv API TOU: 1 req / 3s, single connection).
    request_delay: float = 3.0
    # First N pages to scan for the caption (Figure 1/Table 1 is always early).
    max_pages: int = 8
    # Render scale for the crop PNG (2× ≈ crisp on hi-dpi panels).
    scale: float = 2.0
    # Cap papers processed in one run (0 = no cap). Lets you bake a sample without the full
    # multi-hour batch; the rest keep the client-side fallback.
    max_papers: int = 0


class Secrets(BaseModel):
    """Loaded from environment (.env), never from config.yaml."""

    openalex_mailto: Optional[str] = None
    openalex_api_key: Optional[str] = None
    openalex_api_key_2: Optional[str] = None
    s2_api_key: Optional[str] = None

    @property
    def openalex_api_keys(self) -> list[str]:
        return [key for key in (self.openalex_api_key, self.openalex_api_key_2) if key]


class Config(BaseModel):
    schema_version: int = 3
    corpus: CorpusCfg = Field(default_factory=CorpusCfg)
    arxiv: ArxivCfg = Field(default_factory=ArxivCfg)
    openalex_enrichment: OpenAlexEnrichmentCfg = Field(default_factory=OpenAlexEnrichmentCfg)
    semantic_scholar_citations: SemanticScholarCitationsCfg = Field(
        default_factory=SemanticScholarCitationsCfg
    )
    embedding: EmbeddingCfg = Field(default_factory=EmbeddingCfg)
    projector: ProjectorCfg = Field(default_factory=ProjectorCfg)
    cluster: ClusterCfg = Field(default_factory=ClusterCfg)
    hierarchy: HierarchyCfg = Field(default_factory=HierarchyCfg)
    fused: FusedCfg = Field(default_factory=FusedCfg)
    labels: LabelsCfg = Field(default_factory=LabelsCfg)
    tiling: TilingCfg = Field(default_factory=TilingCfg)
    emit: EmitCfg = Field(default_factory=EmitCfg)
    figures: FiguresCfg = Field(default_factory=FiguresCfg)
    palette: PaletteCfg = Field(default_factory=PaletteCfg)
    secrets: Secrets = Field(default_factory=Secrets)


def _load_secrets() -> Secrets:
    load_dotenv(REPO_ROOT / ".env")
    return Secrets(
        openalex_mailto=os.getenv("OPENALEX_MAILTO") or None,
        openalex_api_key=os.getenv("OPENALEX_API_KEY") or None,
        openalex_api_key_2=os.getenv("OPENALEX_API_KEY_2") or None,
        s2_api_key=os.getenv("S2_API_KEY") or None,
    )


@lru_cache(maxsize=None)
def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load and validate config from YAML + environment. Cached per path."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    cfg = Config.model_validate(raw or {})
    cfg.secrets = _load_secrets()
    return cfg


def ensure_dirs() -> None:
    """Create all canonical data directories if missing."""
    for d in (RAW_DIR, INTERIM_DIR, ARTIFACTS_DIR, CACHE_DIR, WEB_DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)
