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
  embedding/                                                 useEdgeLayer.ts     on-select citation arcs
    base.py          EmbeddingBackend protocol           src/filters/
    specter2_s2.py   fetch precomputed SPECTER2            OrgFilterPanel / AuthorFilter / DateRangeSlider
    scincl_local.py  local SciNCL fallback (torch)         useFilterMask.ts   filters → GPU channels + dim/hide
  stages/ s00..s11   (see below)                         src/panels/
                                                            DetailsPanel / RelatedWorksPanel / Legend / SearchBox
       │ emits
       ▼
  web/public/data/*.arrow + *.json   ← the bundle (gitignored)
```

## Pipeline stages (`pipeline/stages/`)

Run order via `run_all.py`: `s00 s01 s02 s03 s04 s05 s06 s07 s09 s08 s10 s11`
(**s09 before s08** — the fused kNN in s08 consumes s09's edge list).

| Stage | Reads | Writes (in `data/interim/` unless noted) | Notes |
|---|---|---|---|
| `s00_resolve_orgs` | config orgs | `orgs_resolved.json` | pins OpenAlex institution ids (companies fragment; ids are hard-pinned in `config.yaml`) |
| `s01_fetch_openalex` | orgs_resolved | `data/raw/works_raw.jsonl` | cursor pagination, CS field + date filter, `max_works` cap |
| `s02_build_corpus` | works_raw | `corpus.parquet` | reconstruct abstracts; `_clean_doi`, `_numeric_id`; dense `node_id`; **canonical full corpus** |
| `s03_embed` | corpus.parquet | `embeddings.npy`, `embed_meta.json`, **`corpus_active.parquet`** | backend dispatch; `on_uncovered: drop` compacts corpus → active corpus; L2-normalizes |
| `s04_project` | embeddings | `coords2d.npy`, `projector.pkl` | openTSNE 768→2D, frozen projector, coords normalized to ~±100 |
| `s05_cluster` | embeddings | `cluster_assign.npy` | separate UMAP→10D + HDBSCAN (NOT on 2D coords) |
| `s06_hierarchy` | coords2d | `tiles.json` | **adaptive recursive k-means** regions (or legacy quadtree) |
| `s07_label` | corpus_active, tiles | `clusters.json`, `labels.json` (in `data/artifacts/`) | OpenAlex names (coarse) + c-TF-IDF phrases (fine, ancestor-excluded) |
| `s09_edges` | corpus_active | `edges.npz` | intra-corpus citation edge list (both endpoints in corpus) |
| `s08_neighbors` | embeddings, edges | `neighbors.npz` | hnswlib text kNN → fused with citation score |
| `s10_indexes` | corpus_active, orgs_resolved | `orgs.json`, `authors.arrow`, `topics.json` (in `data/artifacts/`) | org→node_ids, author index, topic id→name |
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

- **Data load** (`data/loadArtifacts.ts`): fetches the bundle, unpacks Arrow tables to
  typed arrays / row objects, builds citation adjacency maps. Returns a `Dataset`.
- **State** (`state/store.ts`, zustand): `dataset`, `filters` (yearMin/Max, orgKeys,
  authorIds), `selectedNode`, `colorMode` (subfield|org|recency), `orgDisplayMode`
  (dim|hide), `edgeMode`.
- **Map** (`map/MapView.tsx`): `<DeckGL>` + `OrthographicView`. Computes a runtime
  `fitZoom` + center (`map/zoom.ts`) as the base for band offsets. Composes three layer
  hooks.
  - `usePointsLayer` — `ScatterplotLayer`; fill by color mode; `DataFilterExtension`
    (channel 0 = year on GPU, channel 1 = org/author match when hiding).
  - `useLabelLayers` — **CPU greedy screen-space declutter**: project candidates, place by
    priority, skip overlaps, dedupe texts. One `TextLayer`. (No GPU CollisionFilter — it
    fails in OrthographicView.)
  - `useEdgeLayer` — `ArcLayer` for the selected node's citations only.
- **Filters** (`filters/`): `useFilterMask` turns active filters into a per-point match
  array (org/author) + drives the GPU year channel. Org/author = dim (default) or hide.
- **Panels** (`panels/`): details card, related works (reads `neighbors`), legend, search.

## Key invariants

- `points.arrow` row index **is** `node_id`; all artifacts reference it.
- Cluster **in high-D** (s05 UMAP→10D), **place in 2D** (s04) — never cluster the 2D coords.
- Projector is **frozen** (`projector.pkl`) for map stability across rebuilds.
- Arrow files are **uncompressed** for browser compatibility.
- Semantic-zoom regions are **strictly nested** (a child's points ⊂ its parent's).
