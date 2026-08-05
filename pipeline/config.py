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
    field_id: str = "fields/17"
    date_from: str = "2020-01-01"
    date_to: str = "2026-12-31"
    max_works: int = 40000
    per_page: int = 200
    orgs: list[OrgSpec] = Field(default_factory=list)


class EmbeddingCfg(BaseModel):
    backend: Literal["specter2_s2", "scincl_local"] = "specter2_s2"
    dim: int = 768
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


class LabelsCfg(BaseModel):
    ctfidf_candidates: int = 12
    ngram_max: int = 4
    ctfidf_min_gram: int = 2
    use_abstract: bool = True


class PaletteCfg(BaseModel):
    background: list[int] = Field(default_factory=lambda: [12, 14, 20])


class Secrets(BaseModel):
    """Loaded from environment (.env), never from config.yaml."""

    openalex_mailto: Optional[str] = None
    openalex_api_key: Optional[str] = None
    s2_api_key: Optional[str] = None


class Config(BaseModel):
    schema_version: int = 1
    corpus: CorpusCfg = Field(default_factory=CorpusCfg)
    embedding: EmbeddingCfg = Field(default_factory=EmbeddingCfg)
    projector: ProjectorCfg = Field(default_factory=ProjectorCfg)
    cluster: ClusterCfg = Field(default_factory=ClusterCfg)
    hierarchy: HierarchyCfg = Field(default_factory=HierarchyCfg)
    fused: FusedCfg = Field(default_factory=FusedCfg)
    labels: LabelsCfg = Field(default_factory=LabelsCfg)
    palette: PaletteCfg = Field(default_factory=PaletteCfg)
    secrets: Secrets = Field(default_factory=Secrets)


def _load_secrets() -> Secrets:
    load_dotenv(REPO_ROOT / ".env")
    return Secrets(
        openalex_mailto=os.getenv("OPENALEX_MAILTO") or None,
        openalex_api_key=os.getenv("OPENALEX_API_KEY") or None,
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
