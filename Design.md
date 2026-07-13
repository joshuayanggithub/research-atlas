# Design

This document records the important design decisions behind the Research Visualizer.
It is the "why"; `Features.md` is the "what".

## Goal

An interactive 2D **map of CS/AI research**. Each paper is a point; semantically similar
papers sit near each other; directed edges are citations; and the map supports
**Google-Maps-style semantic zoom** (broad fields when zoomed out → ML topics mid-zoom →
fine subtopics zoomed in). Users filter by **organization**, **author**, and **date**, and
inspect a paper's **citations** and **related works**.

## Architecture at a glance

```
OpenAlex (works, citations, topics)         Semantic Scholar (SPECTER2 vectors)
                \                            /
        ┌─────────────────────────────────────────┐
        │  offline Python pipeline (pipeline/)      │   s00 … s11
        │  fetch → embed → project → cluster →      │
        │  label → neighbors → edges → emit         │
        └───────────────────┬───────────────────────┘
                            │  static artifact bundle (Arrow + JSON)
                            ▼
                web/public/data/*  ──►  React + deck.gl app (web/)
```

Two halves joined by a **static artifact contract** (`pipeline/common/schema.py` ⇄
`web/src/data/types.ts`). There is **no backend** — the browser loads pre-baked files.

### Why an offline pipeline (not compute-on-demand)

The map's positions don't exist in the source data: turning "papers" into "(x, y) where
similar papers cluster" requires embedding the whole corpus and running a global 2D
projection + clustering. That work is heavy, global (a point's position depends on all
others), and changes slowly. So we compute it **once, offline**, and ship small static
artifacts. This also makes hosting trivial (static files / CDN) and the UI fast.

## Key decisions

### 1. Data spine: OpenAlex + Semantic Scholar (verified against live APIs, 2026-07)

- **OpenAlex `works`** is the spine: one record carries title, abstract (as
  `abstract_inverted_index`, reconstructed in `common/abstract.py`), `publication_date`,
  `authorships[]` (author + institution + ROR), `referenced_works[]` (**the directed
  citation edge list, free**), and a 4-level topic taxonomy (**Domain→Field→Subfield→
  Topic**). CS = `primary_topic.field.id:fields/17`. Filters by institution, author, and
  date are all supported and proven.
- **Semantic Scholar** supplies **precomputed SPECTER2 embeddings** (768-dim) via
  `/paper/batch` — so we don't run an embedding model for covered papers. Join is by
  DOI/arXiv id.
- **CSRankings** is a *reference only* for later dept/lab work (its data is CC BY-NC-ND).

### 2. Organizations: multiple institution ids per org

OpenAlex institution names are ambiguous and companies fragment into regional entities
(e.g. "Meta (United States)" vs "Meta (Israel)"; five separate "Microsoft Research"
nodes) with **no department/lab granularity**. So each org in `config.yaml` pins an
explicit **list** of verified institution ids that we OR into the filter. University→dept→
lab granularity is deferred to a later phase (needs affiliation-string parsing).

### 3. Embeddings: swappable backend, SPECTER2 first with a local fallback

`embedding/base.py` defines an `EmbeddingBackend` protocol. `specter2_s2` fetches
precomputed vectors; `scincl_local` runs `malteos/scincl` locally (MPS on Mac). `s03`
**auto-falls-back**: if S2 coverage is below a threshold (rate limits, papers not in S2,
or per-batch API errors), the uncovered rows are embedded locally so coverage reaches
100%. Vectors are L2-normalized centrally so all downstream cosine math is uniform.

### 4. Layout vs clustering: two independent reductions

- **Layout**: openTSNE 768→2D with the "PubMed-landscape" recipe (PCA init, uniform
  affinities k=10, cosine metric, exaggeration annealing). The fitted embedding is
  **frozen** (`projector.pkl`) so future incremental runs `transform()` new papers into
  the *same* space — the map stays stable (no reshuffling between builds).
- **Clustering**: a *separate* UMAP 768→~10D + HDBSCAN. We deliberately **do not cluster
  on the 2D coords** — UMAP's own docs warn that clustering the display projection creates
  false tears / density artifacts.

### 5. Semantic zoom: a quadtree over the frozen 2D coords

`s06_hierarchy` overlays a **quadtree**: band *b* samples quadtree depth `start_depth+b`,
so band 0 has few big cells ("continents") and each deeper band has 4× more, finer cells
("cities" → "streets"). Because a child cell is spatially nested in its parent, the bands
are **guaranteed nested** — a fine label always sits inside its coarse parent's region.
(We chose this over Apple's multi-bandwidth density clustering precisely because that
approach is *not* strictly nested.)

`s07_label` labels each tile two ways and picks per band:
- **majority OpenAlex topic name** at the taxonomy level matching the band (coarse band →
  subfield e.g. "Artificial Intelligence"; finer → topic e.g. "Self-Supervised Learning");
- **c-TF-IDF n-gram** — treat a tile's texts as one class-document, score n-grams vs all
  tiles at that band; the top phrase is the tile's differentiating vocabulary (e.g.
  "world models"). Used for the finest band and to refine mid bands.

Each label carries `{x, y, text, level, priority}`. The frontend renders one `TextLayer`
per band sharing a single **`CollisionFilterExtension`** group; `getCollisionPriority`
(coarse + high-count wins) declutters on the GPU every frame, so coarse labels win when
zoomed out and finer labels appear as you zoom in.

### 6. Related works: fused text + citation similarity (Connected-Papers idea)

`s08_neighbors` builds an hnswlib cosine kNN over embeddings (text candidates), then
re-ranks by `fused = α·cosine + (1−α)·citation_score`, where `citation_score` = mean of
**bibliographic coupling** (shared references) and **co-citation** (shared citers) over
the intra-corpus graph (α≈0.6). Text candidates ensure new/sparsely-cited papers still get
neighbors; the citation term boosts community relatedness. This realizes the spec's
"citations intrinsically mean relatedness" without making the map a raw citation tree.

### 7. Rendering: deck.gl, edges on-select only

deck.gl (`OrthographicView`) draws the scatter (`ScatterplotLayer`), zoom labels
(`TextLayer` + collision), and citation arcs (`ArcLayer`). The **global citation graph is
never drawn** (hairball + slow) — arcs appear only for the selected node. Date filtering
runs on the GPU via `DataFilterExtension` (smooth slider drags); org/author filtering
dims (default) or hides non-matches, preserving spatial context.

## Artifact contract (the seam)

Large columnar data → **Arrow IPC** (zero-copy in the browser); small structured data →
JSON. `points.arrow`'s row index **is** the `node_id`; everything else references it.
Files: `points`, `papers`, `neighbors`, `edges`, `authors` (Arrow); `clusters`, `labels`,
`orgs`, `topics`, `manifest` (JSON). See `pipeline/common/schema.py`.

## Risks & mitigations (as built)

- **S2 rate limits / bad batches** → on-disk cache, exponential backoff, per-batch skip,
  auto-fallback to local SciNCL. (A malformed-id 400 in one batch is skipped, not fatal.)
- **Abstract reconstruction** → dedicated, unit-tested module; ~85% coverage logged;
  title-only fallback for the rest.
- **Layout instability** → frozen projector; deterministic node ordering by paper id.
- **Label clutter** → GPU collision filter + per-band gating + priority ranking.
- **Filter jank** → date on GPU; org/author masks memoized on selection, not on zoom.

## Scaling path (future phases)

Stage boundaries and the artifact contract are identical across scales — only each stage's
*implementation* and the frontend's *data-loading* (static → tiled) change. Phase 1: bulk
OpenAlex S3 snapshot + SPECTER2 bulk datasets, tiled point/label streaming (deck.gl
`TileLayer`), GPU/DuckDB-WASM filtering past ~200k points. Phase 2: department/lab
granularity via affiliation parsing. Phase 3: a live vector DB for "embed my own text".
