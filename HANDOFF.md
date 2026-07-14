# Handoff — Research Visualizer

You are taking over a **working MVP**. This doc gets you productive fast. Read
`Design.md` for *why* decisions were made, `Features.md` for *what* the app does,
`docs/ARCHITECTURE.md` for the code map, and `docs/ROADMAP.md` for what to build next.

---

## TL;DR

An interactive 2D "map of CS/AI research": ~28k papers as points, semantically similar
papers near each other, Google-Maps-style semantic zoom (fields → topics → subtopics),
plus organization / author / date filters, on-select citation arcs, and related works.

- **Offline Python pipeline** (`pipeline/`, 12 stages `s00`–`s11`) turns OpenAlex + Semantic
  Scholar data into a **static artifact bundle**.
- **React + deck.gl frontend** (`web/`) renders that bundle. **No backend.**
- Everything below is **committed and verified end-to-end in a browser**. Working tree clean.

---

## ⚠️ Read this first: the data bundle is NOT in git

`web/public/data/*`, `data/` (raw/interim/artifacts), and `pipeline/.cache/` are
**gitignored** (see `.gitignore`). A fresh clone has **no data**, so the app will show
**"Failed to load data"** until you generate the bundle.

Two situations:
- **This working copy** already has the bundle + all intermediates on disk — the app runs
  now (see "Run the app"). Nothing to regenerate.
- **A fresh clone** (or after `git clean`): you must run the pipeline once to rebuild
  `web/public/data/` (see "Rebuild the data").

---

## Environment (already set up in this working copy)

- **Python 3.11** via `uv` — venv at `.venv/`, deps installed (incl. the heavy
  `torch`+`sentence-transformers` extra for the local-embed fallback).
- **Node 24** — `web/node_modules/` installed.
- Verify: `uv run python -c "import openTSNE, umap, hdbscan, hnswlib; print('ok')"` and
  `cd web && npx tsc -b`.

On a fresh clone, recreate the Python env:
```bash
uv venv --python 3.11
uv pip install -r pipeline/requirements.txt
uv pip install "sentence-transformers>=3.0" "torch>=2.2"   # optional local-embed fallback
cp .env.example .env        # set OPENALEX_MAILTO; optional S2_API_KEY
cd web && npm install
```

---

## Run the app (data bundle already present here)

```bash
cd web && npm run dev        # http://localhost:5173
```
You should see ~28,043 colored points filling the viewport, semantic-zoom labels, and a
Filters sidebar. If you instead see "Failed to load data", the bundle is missing → rebuild.

**Verified working** (headless browser, screenshots): map render, semantic zoom
(fields→topics→subtopics), title search, node select (details + citation toggle),
fused related-works ranking, org-filter dimming, GPU date slider.

---

## Rebuild the data (only if the bundle is missing / you changed the pipeline)

```bash
uv run python -m pipeline.run_all                 # full run, ~10-15 min (S2 fetch is the slow part)
# resume from a stage:   uv run python -m pipeline.run_all --from s06
# run a subset:          uv run python -m pipeline.run_all --only s06,s07
```
Config is `config.yaml` (corpus orgs, dates, cap, embedding backend, hierarchy, labels).
Output lands in `web/public/data/`.

**The slow/fragile stage is `s03_embed`** (Semantic Scholar SPECTER2 fetch): the shared
pool rate-limits hard (HTTP 429 with long backoffs). On this machine the fetch is **cached**
(`pipeline/.cache/s2_specter2/`, 74 batches) so a rebuild is fast; on a fresh clone the
first `s03` run takes several minutes. A `429`/`400` on a batch is skipped, not fatal.

Stage order (note **s09 runs before s08** — neighbors need the edge list):
`s00 resolve orgs → s01 fetch → s02 corpus → s03 embed → s04 project → s05 cluster →
s06 hierarchy → s07 label → s09 edges → s08 neighbors → s10 indexes → s11 emit`.

---

## Current build (as of the last commit `fad8e7d`)

| | |
|---|---|
| Corpus | **28,043** CS papers (OpenAlex field 17), 2020–2026 |
| Orgs | DeepMind, Meta/FAIR, Microsoft Research, Berkeley, CMU, Stanford, MIT |
| Embeddings | **SPECTER2** (768-d) fetched from Semantic Scholar; `on_uncovered: drop` keeps only papers with a real SPECTER2 vector (39,231 fetched → 28,043 kept) for one clean space |
| Layout | openTSNE 768→2D, frozen `projector.pkl` |
| Clusters | UMAP→10D + HDBSCAN (103 clusters) — used for `cluster_leaf` |
| Semantic zoom | **adaptive recursive k-means** on 2D coords: 6 bands, ~1,976 regions (8→24→72→216→624→1032) |
| Labels | OpenAlex subfield/topic names at coarse bands; c-TF-IDF phrases (title+abstract, ancestor-excluded) at fine bands |
| Bundle | 9 files, ~17 MB uncompressed Arrow/JSON in `web/public/data/` |

---

## The seam you must respect

The **only** contract between pipeline and frontend is the artifact bundle, defined in
**`pipeline/common/schema.py`** (pyarrow schemas + pydantic JSON models) and mirrored in
**`web/src/data/types.ts`**. If you change one, change the other and bump
`schema_version` in `config.yaml`. `points.arrow`'s **row index == `node_id`**; every other
artifact references papers by `node_id`.

Artifacts: `points.arrow` (x,y,year,cites,cluster,topic ids,color) · `papers.arrow`
(title/authors/doi/…) · `neighbors.arrow` (fused kNN) · `edges.arrow` (citations) ·
`authors.arrow` · `clusters.json` · `labels.json` · `orgs.json` · `topics.json` ·
`manifest.json`.

---

## Gotchas the previous agent hit (don't re-learn these)

1. **DOIs are NOT OpenAlex short-ids.** Keep the full `10.xxxx/yyyy` (`_clean_doi` in
   `s02`). `short_id()` mangles them and breaks Semantic Scholar lookups. Regression test
   in `pipeline/tests/test_corpus.py`.
2. **OpenAlex topic ids are `T13650`** (letter-prefixed), while subfield/field/domain are
   `subfields/1702` (slash). `_numeric_id` in `s02` handles both.
3. **Arrow must be written UNCOMPRESSED** (`io.write_arrow`). Browser `apache-arrow` can't
   decode compressed record batches ("compression not implemented").
4. **deck.gl `CollisionFilterExtension` culls ALL labels in an `OrthographicView`** (it's
   built for geospatial `MapView`). Labels use a **CPU greedy screen-space declutter** in
   `web/src/map/layers/useLabelLayers.ts` instead. Don't reintroduce the GPU extension.
5. **Zoom bands are OFFSETS from a runtime fit-zoom** the frontend computes
   (`web/src/map/zoom.ts` `fitZoom`), so the map is calibrated at any window size. Don't
   hardcode absolute zoom values.
6. **SPECTER2 (fetched) and SciNCL (local) are different embedding spaces** — mixing them
   at scale creates a visible "island". Hence `on_uncovered: drop`. See ROADMAP for the
   `specter2_local` fix.

---

## Verifying changes

- Pipeline unit tests: `uv run pytest` (9 tests: abstract reconstruction, DOI handling).
- Frontend typecheck / build: `cd web && npx tsc -b && npm run build`.
- Visual: `cd web && npm run dev`, then drive it. The previous agent used the
  Playwright MCP (`playwright-proxy-mcp`) — navigate to `localhost:5173`, screenshot,
  dispatch wheel events on the canvas to test zoom. Check the browser console for errors
  (a favicon 404 is the only expected one).
- After a pipeline change, re-run the affected stages and eyeball
  `web/public/data/labels.json` / `manifest.json`.

See `docs/ROADMAP.md` for prioritized next work and known rough edges.
