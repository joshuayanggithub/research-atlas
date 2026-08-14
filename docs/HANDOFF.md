# Handoff — Research Visualizer

You are taking over a **working MVP**. This doc gets you productive fast. Read
`Design.md` for *why* decisions were made, `Features.md` for *what* the app does,
`ARCHITECTURE.md` for the code map, `ORGANIZATION_DIRECTORY.md` for the directory
design, and `ROADMAP.md` for what to build next.

---

## TL;DR

An interactive 2D "map of CS/AI research": ~17k papers as points, semantically similar
papers near each other, Google-Maps-style semantic zoom (fields → topics → subtopics),
plus a drill-down organization filter (org → dept/lab → researchers), author and
month-granularity date filters (with a publication histogram + presets), default-visible
directed citation edges, a selected-paper citation explorer and arXiv preview, and related
works.

- **Offline Python pipeline** (`pipeline/`, 12 stages `s00`–`s11`) turns OpenAlex + Semantic
  Scholar data into a **static artifact bundle**.
- **React + deck.gl frontend** (`web/`) renders that bundle. **No backend.**
- The generated bundle is present in this working copy. Check `git status` before editing;
  local instructions and dependency lock state may be untracked.

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
You should see **16,714** colored points filling the viewport, semantic-zoom labels, and a
Filters sidebar. If you instead see "Failed to load data", the bundle is missing → rebuild.

**Verified working** (headless browser, screenshots): map render, semantic zoom
(fields→topics→subtopics), default directional edges, title search, node select (directed
citation graph/lists plus paper preview), fused related-works ranking, org-filter dimming,
GPU date slider.

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

Stage order (edges and neighbors run before the hierarchy because `s06` consumes both):
`s00 resolve orgs → s01 fetch → s02 corpus → s03 embed → s04 project → s05 cluster →
s09 edges → s08 neighbors → s06 hierarchy → s07 label → s10 indexes → s11 emit`.

---

## Current generated bundle

| | |
|---|---|
| Corpus | **16,714** retained CS papers (OpenAlex field 17), 2023–2026, scoped to the 12 curated organizations. This is the local ignored bundle built 2026-08-13; it is newer than the checked-in `config.yaml`. |
| Orgs | **12 curated roots** (Google, Google DeepMind, Amazon, OpenAI, NVIDIA, Allen Institute for AI, Meta, Microsoft Research, UC Berkeley, CMU, Stanford, MIT) + **30 evidence-backed dept/lab sub-units** + **1,812 non-curated corpus institutions** (`curated:false`) so other universities/companies are searchable and filterable. `orgs.json` is ~0.8 MB. |
| Embeddings | **SPECTER2** (768-d) from Semantic Scholar, addressed by arXiv→DOI→MAG. `on_uncovered: drop` retained **16,714 of 25,930 fetched papers (64.5%)**. The missing 35.5% is the immediate reason to add local SPECTER2 inference. |
| Layout | openTSNE 768→2D; reducer and map normalization frozen in `projector.pkl` |
| Clusters | UMAP→10D + HDBSCAN (**63** `cluster_leaf` values) |
| Semantic zoom | **Nested Leiden communities on the planar substrate** (2D-layout kNN adjacency, 768-D cosine weights): **11 bands and 5,776 labeled regions**. |
| Labels | Discriminative OpenAlex topics + representative title/abstract c-TF-IDF phrases, with ancestor/sibling deduplication and shared title n-grams for small leaf communities. |
| Bundle | 33 manifest files, ~13 MB total in `web/public/data/` (sharded points, neighbors and paper detail) |

---

## 2026-08-13 continuation checkpoint: recent arXiv at scale

The product direction discussed immediately before this handoff is to make recent arXiv
coverage nearly exhaustive, rather than sample papers across 2015–2026. No implementation
for this direction has been committed yet; the checked-in `config.yaml` still describes the
older 2015–2026 field build, while the ignored local bundle described above is a temporary
2023–2026 organization-scoped build.

Counts measured for sizing (category union means `cs.* OR stat.ML`):

- 2025: **284,162** total arXiv papers; **144,645** in the CS/ML union.
- 2026 through 2026-08-13: **210,772** total; **111,954** in the CS/ML union.
- A simple full-year projection puts 2025+2026 at roughly **326k CS/ML papers** or
  **626k papers across all arXiv categories**. Treat the 2026 full-year values as planning
  estimates, not observed counts.

The workstation available for the next build has a local **RTX 3090 (24 GB)**. The intended
embedding path is `arXiv title + abstract -> local SPECTER2 -> normalized 768-D float32`.
SPECTER2 is free to run locally; no paid embedding API or PDF download is needed. Raw vector
storage is about 1.0 GB for 326k papers and 1.9 GB for 626k. Expected 3090 inference time is
roughly 3–8 hours for 326k or 6–16 hours for 626k, but benchmark a representative batch
before scheduling the complete run.

Recommended next implementation:

1. Add a true `specter2_local` backend using the same model and proximity adapter as the
   Semantic Scholar `specter_v2` vectors; expose batch size/device/precision in config.
2. Run an overlap compatibility test against Semantic Scholar vectors. If they are not
   numerically compatible, embed the entire selected corpus locally rather than mixing
   spaces.
3. Add an arXiv-first metadata ingestion path with resumable checkpoints and polite bulk/API
   access. Keep title, abstract, authors, categories, versions and dates; PDFs are optional
   and should not be fetched for embedding.
4. Decide the first production scope. **2025+2026 CS/ML** is the recommended first complete
   build; all-category arXiv can follow after measuring layout, indexing and browser limits.
5. Do not overwrite the currently served ignored bundle until the new pipeline completes and
   its artifact contract, desktop/mobile browser behavior and console are verified.

---

## The seam you must respect

The **only** contract between pipeline and frontend is the artifact bundle, defined in
**`pipeline/common/schema.py`** (pyarrow schemas + pydantic JSON models) and mirrored in
**`web/src/data/types.ts`**. If you change one, change the other and bump
`schema_version` in `config.yaml`. `points.arrow`'s **row index == `node_id`**; every other
artifact references papers by `node_id`.

Artifacts: `points.arrow` (x,y,year,cites,cluster,topic ids,color) · `papers.arrow`
(title/authors/doi/…) · `neighbors.arrow` (fused kNN) · `edges.arrow` (citations) ·
`authors.arrow` · `clusters.json` · `labels.json` · `orgs.json` (hierarchical) · `topics.json` ·
`manifest.json`. The frontend derives a per-point `monthIndex` at load from
`papers.publication_date` for month-granularity date filtering (not shipped in Arrow).

Org sub-units come from a *separate* offline artifact: `s02` writes
`data/interim/affiliations.parquet` (paper-id keyed raw-affiliation evidence, so it never
touches the frozen `node_id` order), and `pipeline/directory/units.py` matches it into the
`orgs.json` hierarchy in `s10`. Regenerating org data is cheap: `uv run python -m
pipeline.run_all --only s10,s11` — no re-embed/re-project.

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
7. **The zoom hierarchy is graph-derived but not validated ground truth.** `s06` recursively
   partitions the fused semantic/citation graph and uses 2D only for placement. Do not claim
   topic correctness until the human-reviewed benchmark in the ROADMAP exists.
8. **Organization config defines the corpus.** The seven *root* org filters still double as
   corpus-fetch predicates, so they are not yet an arbitrary organization catalog —
   supporting "any organization" requires decoupling broad corpus ingestion from the
   membership index. Dept/lab sub-units, however, are now real: they come from
   `pipeline/directory/units.py` matching retained raw affiliation strings, and FAIR is a
   narrow affiliation-evidenced child (425 papers) beneath the broad `Meta` parent — not the
   whole parent. Sub-unit matching is confidence-95 exact-name only; a parent match never
   implies a child. Follow `ORGANIZATION_DIRECTORY.md` for the full target model.
9. **Org sub-units regenerate without a full rebuild.** Because affiliation evidence is a
   separate paper-id-keyed artifact, editing `pipeline/directory/units.py` only needs
   `--only s02,s10,s11` (or just `s10,s11` if `affiliations.parquet` already exists). The
   frozen embedding/projection is untouched — verify `points.arrow` row count is unchanged.

---

## Verifying changes

- Pipeline unit tests: `uv run pytest` (30 tests: abstract, DOI, projector-coordinate,
  fused-similarity, hierarchy, label, and **org sub-unit attribution** invariants).
- Frontend typecheck / build: `cd web && npx tsc -b && npm run build`.
- Automated e2e: `cd web && npm run test:e2e` (Playwright, desktop + mobile — load, search,
  org drill-down, org-scoped researchers, date presets, route-mocked load failure). On a
  fresh machine run `npx playwright install chromium` once.
- Visual (still required for UI changes per `AGENTS.md`): `cd web && npm run dev`, then
  drive it via the Playwright MCP — navigate, screenshot at a desktop and a mobile
  viewport, exercise the changed workflow, and check the browser console for errors (a
  favicon 404 is the only expected one). The project-scoped MCP server is configured in
  `.codex/config.toml`; trust the project and restart Codex after cloning so it is loaded.
- After a pipeline change, re-run the affected stages and eyeball
  `web/public/data/labels.json` / `orgs.json` / `manifest.json`.

See `ROADMAP.md` for prioritized next work and known rough edges.
