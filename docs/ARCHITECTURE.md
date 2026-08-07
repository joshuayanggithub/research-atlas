# Architecture — code map

Companion to `Design.md` (rationale). This is the *where things live* reference for an
agent making changes. Data flows one way: **OpenAlex/Semantic Scholar → pipeline → static
bundle → deck.gl app**.

```
config.yaml ── single source of truth (corpus, embedding, projector, cluster, hierarchy, labels)
     │
     ▼
pipeline/  (Python 3.11, offline)                     web/  (React + TS + Vite + deck.gl)
  run_all.py         orchestrator (typer CLI)           src/data/loadArtifacts.ts  fetch + unpack
  config.py          typed loader over config.yaml+.env  src/data/types.ts          TS mirror of schema
  common/                                                src/state/store.ts         zustand app state
    schema.py        THE CONTRACT (arrow+pydantic)       src/map/MapView.tsx        <DeckGL> host
    abstract.py      inverted-index → text (tested)      src/map/zoom.ts            fit-zoom + bands
    openalex_client  cursor pagination + retry           src/map/colors.ts          color modes
    fused_similarity co-citation + biblio coupling       src/map/layers/
    io.py            arrow/json/npy (UNCOMPRESSED arrow)    usePointsLayer.ts   scatter + GPU filters
    log.py           stage logging                          useLabelLayers.ts   semantic-zoom labels (CPU declutter)
  embedding/                                                 useEdgeLayer.ts     sampled + selected directed links
    base.py          EmbeddingBackend protocol           src/filters/
    specter2_s2.py   fetch precomputed SPECTER2            OrgFilterPanel / AuthorFilter / DateRangeSlider
    scincl_local.py  local SciNCL fallback (torch)         useFilterMask.ts   filters → GPU channels + dim/hide
  stages/ s00..s11   (see below)                         src/panels/
                                                            DetailsPanel / CitationExplorer / ArxivPreview
                                                            RelatedWorksPanel / Legend / SearchBox
       │ emits
       ▼
  web/public/data/*.arrow + *.json   ← the bundle (gitignored)
```

## Pipeline stages (`pipeline/stages/`)

Run order via `run_all.py`: `s00 s01 s02 s03 s04 s05 s09 s08 s06 s07 s10 s11`.
Edges feed fused neighbors; both feed the semantic hierarchy.

| Stage | Reads | Writes (in `data/interim/` unless noted) | Notes |
|---|---|---|---|
| `s00_resolve_orgs` | config orgs | `orgs_resolved.json` | pins OpenAlex institution ids (companies fragment; ids are hard-pinned in `config.yaml`) |
| `s01_fetch_openalex` | orgs_resolved | `data/raw/works_raw.jsonl` | cursor pagination, CS field + date filter, `max_works` cap; `select`s `authorships` incl. `raw_affiliation_strings` |
| `s02_build_corpus` | works_raw, orgs_resolved | `corpus.parquet`, **`affiliations.parquet`**, **`institutions.json`** | reconstruct abstracts; `_clean_doi`, `_numeric_id`; dense `node_id`; **canonical full corpus**; per-org raw affiliation evidence (paper-id keyed) for drill-down; institution id→name/type registry for the full org directory |
| `s03_embed` | corpus.parquet | `embeddings.npy`, `embed_meta.json`, **`corpus_active.parquet`** | backend dispatch; `on_uncovered: drop` compacts corpus → active corpus; L2-normalizes |
| `s04_project` | embeddings | `coords2d.npy`, `projector.pkl` | openTSNE 768→2D; frozen reducer plus fit-time map normalization |
| `s05_cluster` | embeddings | `cluster_assign.npy` | separate UMAP→10D + HDBSCAN; emitted as `cluster_leaf`, not used for zoom regions |
| `s09_edges` | corpus_active | `edges.npz` | intra-corpus citation edge list (both endpoints in corpus) |
| `s08_neighbors` | embeddings, edges | `neighbors.npz` | union semantic/direct/coupling/co-citation candidates, then fused ranking |
| `s06_hierarchy` | coords2d, embeddings, neighbors, edges | `tiles.json` | nested Leiden communities (Louvain selectable); embedding fallback; 2D label placement only |
| `s07_label` | corpus_active, embeddings, tiles | `clusters.json`, `labels.json` (in `data/artifacts/`) | discriminative OpenAlex topics + representative c-TF-IDF phrases |
| `s10_indexes` | corpus_active, orgs_resolved, affiliations, institutions | `orgs.json`, `authors.arrow`, `topics.json` (in `data/artifacts/`) | curated org→node_ids + **evidence-backed dept/lab sub-units** (`pipeline/directory/`) as a 2-level hierarchy with rollup/direct counts, PLUS every corpus institution with ≥`DIRECTORY_MIN_PAPERS` papers as flat `curated:false` directory entries; author index; topic id→name |
| `s11_emit` | all of the above | `web/public/data/*` + `manifest.json` | builds points/papers Arrow, copies JSON, writes integrity manifest |

**Two corpus files** (important): `corpus.parquet` = full (s02 output);
`corpus_active.parquet` = what s04–s11 consume (compacted in `drop` mode, identical in
`fill_local`). `s03` derives active from full, so re-running s03 is idempotent. Paths are
`CORPUS_FULL` / `CORPUS_ACTIVE` in `pipeline/config.py`.

## Embedding backends (`pipeline/embedding/`)

`base.py` defines `EmbeddingBackend` (`embed(corpus) -> EmbeddingResult{vectors, covered}`).
- `specter2_s2.py` — POST `/paper/batch` (500 ids), `embedding.specter_v2`, on-disk cache,
  backoff, per-batch skip on non-retryable errors. Addresses papers by DOI/arXiv id.
- `scincl_local.py` — `malteos/scincl` via sentence-transformers, MPS/CUDA/CPU. Lazy torch
  import so the base env loads without it.

`s03_embed` picks the backend and applies `embedding.on_uncovered` (`drop` | `fill_local`).
To add a backend (e.g. `specter2_local`, see ROADMAP), implement the protocol and wire it
in `s03_embed.run`.

## Frontend (`web/src/`)

- **Data load** (`data/loadArtifacts.ts`): fetches the bundle once, validates schema and
  row-count invariants, unpacks Arrow tables to typed arrays / row objects, and builds
  citation adjacency maps. Returns a `Dataset`.
- **State** (`state/store.ts`, zustand): `dataset`, `filters` (yearMin/Max, orgKeys,
  authorIds), `selectedNode`, `colorMode` (subfield|org|recency), `orgDisplayMode`
  (dim|hide), `edgeMode`, and `showCitationEdges`.
- **Map** (`map/MapView.tsx`): `<DeckGL>` + `OrthographicView`. Computes a runtime
  `fitZoom` + center (`map/zoom.ts`) as the base for band offsets. Composes three layer
  hooks. Selection recenters the camera; resize updates viewport-dependent label placement.
  - `usePointsLayer` — `ScatterplotLayer`; fill by color mode; `DataFilterExtension`
    (channel 0 = year on GPU, channel 1 = org/author match when hiding).
  - `useLabelLayers` — **CPU greedy screen-space declutter**: project candidates, place by
    priority, skip overlaps, dedupe texts. One `TextLayer`. (No GPU CollisionFilter — it
    fails in OrthographicView.)
  - `useEdgeLayer` — a default-visible, deterministic zoom/screen-length sample of global
    citations (`LineLayer` + `SolidPolygonLayer` arrowheads), plus high-contrast selected
    incoming/outgoing links and clickable endpoint rings.
- **Filters** (`filters/`): `useFilterMask` turns active filters into a per-point match
  array (org/author) + drives the GPU year channel. Org/author = dim (default) or hide.
- **Panels** (`panels/`): paper metadata, directed citation graph/lists, related works
  (reads `neighbors`), lazy arXiv PDF preview with Semantic Scholar ID resolution, legend,
  and search.

## Organization directory (partial implementation)

**Implemented (this iteration).** `pipeline/directory/units.py` holds a curated registry of
department/lab sub-units per seed org, matched against the raw affiliation strings `s02`
retains in `affiliations.parquet`. `s10` resolves each active paper's evidence into sub-unit
memberships and emits a two-level hierarchy in `orgs.json` (each `Institution` gains
`parent`, `children`, `unit_type`, and `direct_count`/`direct_node_ids` alongside the rollup
`count`/`node_ids`). Matching is confidence-95 exact-name only, case-sensitive for ambiguous
acronyms, most-specific-unit-wins, and org-scoped. This is Phase 1 (retain evidence) plus a
conservative slice of Phases 2/5 (curated units + hierarchical UI) from the design doc — it
does **not** yet emit the v2 artifact schema, temporal validity, or researcher identity.

**Full target (not yet implemented).** The complete replacement for the `s00`/`s10` path is
designed in [`ORGANIZATION_DIRECTORY.md`](ORGANIZATION_DIRECTORY.md):

```
directory YAML + provider snapshots
              │
              ▼
    s00 canonical directory
              │
OpenAlex field/date corpus + retained authorship evidence
              │
              ▼
    s10 researcher/work attribution
              │
              ▼
    s11 schema-v2 directory artifacts
```

The canonical model is a typed temporal DAG, not an academic-only tree. ROR/OpenAlex map
root identities; optional CSRankings records academic institutions, faculty roster claims,
and venue taxonomy; official sources and curated overrides establish departments, labs,
and corporate units. Browser artifacts contain the small organization DAG and compact
direct attribution tables. Parent rollups are derived and deduplicated.

Source boundaries (`pipeline/directory/` — `units.py` exists today; the rest are planned):

```
directory/                         curated canonical YAML and crosswalks (planned)
pipeline/directory/units.py        curated dept/lab registry + affiliation matcher (DONE)
pipeline/directory/providers/      source-specific immutable record adapters (planned)
pipeline/directory/canonicalize.py identity resolution (planned)
pipeline/directory/attribution.py  authorship-backed paper membership (planned)
pipeline/directory/validation.py   DAG, provenance, confidence, and coverage gates (planned)
```

CSRankings remains disabled in redistributable builds until its CC BY-NC-ND constraints
are resolved. The adapter uses its institution and faculty rosters as claims; it does not
interpret the generated `dept` field as an actual university department.

## Key invariants

- `points.arrow` row index **is** `node_id`; all artifacts reference it.
- `cluster_leaf` is computed **in high-D** (s05 UMAP→10D) but is separate from zoom.
  Semantic zoom uses nested fused-graph communities; it still requires benchmark
  validation before being treated as topic ground truth.
- Projector reducer **and normalization** are frozen (`projector.pkl`) so transformed
  points use the displayed map's coordinate system.
- Arrow files are **uncompressed** for browser compatibility.
- Semantic-zoom regions are **strictly nested** (a child's points ⊂ its parent's).
- The current corpus and org index cover only the configured seed institutions. The broad
  Meta IDs establish Meta membership, not FAIR membership. Supporting arbitrary
  organizations requires corpus discovery to be separated from org membership.
