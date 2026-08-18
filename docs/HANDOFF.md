# Handoff — Research Visualizer

You are taking over a **working MVP**. This doc gets you productive fast. Read
`Design.md` for *why* decisions were made, `Features.md` for *what* the app does,
`ARCHITECTURE.md` for the code map, `ORGANIZATION_DIRECTORY.md` for the directory
design, and `ROADMAP.md` for what to build next.

---

## TL;DR

An interactive 2D "map of CS/AI research": 271k recent papers as points, semantically similar
papers near each other, Google-Maps-style semantic zoom (fields → topics → subtopics),
plus a drill-down organization filter (org → dept/lab → researchers), author and
month-granularity date filters (with a publication histogram + presets), default-visible
directed citation edges, a selected-paper citation explorer and arXiv preview, and related
works.

- **Offline Python pipeline** (`pipeline/`, dependency-ordered stages `s00`–`s16`) turns arXiv + OpenAlex + Semantic
  Scholar data into a **static artifact bundle**.
- **React + deck.gl frontend** (`web/`) renders that bundle. **No backend.**
- **The 271,366-paper 2025+2026 arXiv bundle is generated and desktop/mobile browser-verified
  in this working copy.** OpenAlex metadata enrichment is present. `s16` now materializes its
  citation fields locally; the current generated bundle remains pre-citation until that stage
  and the downstream rebuild are run. Semantic Scholar remains available for later reconciliation.

---

## ⚠️ Read this first: the data bundle is NOT in git

`web/public/data/*`, `data/` (raw/interim/artifacts), and `pipeline/.cache/` are
**gitignored** (see `.gitignore`). A fresh clone has **no data**, so the app will show
**"Failed to load data"** until you generate the bundle.

This working copy has the ignored corpus, embeddings, and generated preview bundle. A fresh
clone has none of them and must rebuild (see "Rebuild the data").

---

## Environment

- **Python 3.11** via `uv` — venv at `.venv/`; use `uv sync --extra dev` for tests and add
  `--extra local-embed` only when local embedding is needed. This host exposes ROS pytest
  plugins through its environment, so prefix tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.
- **Node 24 is not installed on the host PATH.** Install/select Node 24 before frontend
  work. `web/node_modules/` was restored with `npm ci` on 2026-08-14.
- **GPU driver reboot pending on this host.** The loaded kernel module is 580.159.03 while
  installed user libraries are 580.173.02. The completed embedding run used an unprivileged
  exact-version library extracted under `/tmp/research-atlas-nvidia-580159`; a reboot should
  load the installed module and remove the need for that temporary `LD_LIBRARY_PATH`.
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

## Run the app (after rebuilding the missing bundle)

```bash
cd web && npm run dev        # http://localhost:5173
```
After a successful rebuild, the map should show colored points, semantic-zoom labels, and a
Filters sidebar. Until then, "Failed to load data" is expected.

**Verified working** (headless browser, screenshots): map render, semantic zoom
(fields→topics→subtopics), default directional edges, title search, node select (directed
citation graph/lists plus paper preview), fused related-works ranking, org-filter dimming,
GPU date slider.

---

## Rebuild the data (only if the bundle is missing / you changed the pipeline)

```bash
uv run python -m pipeline.run_all                 # full run; large layout stages need benchmarking
# resume from a stage:   uv run python -m pipeline.run_all --from s06
# run a subset:          uv run python -m pipeline.run_all --only s06,s07
```
Config is `config.yaml` (corpus orgs, dates, cap, embedding backend, hierarchy, labels).
Output lands in `web/public/data/`.

For the configured arXiv build, `s03_embed` runs local SPECTER2 with the proximity adapter.
It checkpoints every 2,048 rows and resumes only when the ordered corpus fingerprint
matches. The completed 271k run took 9m48s on the RTX 3090 at batch 128/fp16. The older
`specter2_s2` backend remains available for OpenAlex builds, but its shared API pool can
rate-limit hard.

Stage order (edges and neighbors run before the hierarchy because `s06` consumes both):
`s00 resolve orgs → s01 fetch → s02 corpus → s15 OpenAlex enrich → s16 OpenAlex citations → s03 embed → s04 project → s05 cluster →
s09 edges → s08 neighbors → s06 hierarchy → s07 label → s10 indexes → s11 emit`.

---

## Current generated preview bundle (ignored, present in this checkout)

| | |
|---|---|
| Corpus | **271,366** unique `cs.* OR stat.ML` arXiv papers, 2025 through 2026-08-13; 100% abstracts. |
| Orgs/citations | OpenAlex exact-match enrichment covers **244,730 / 271,366 (90.2%)** papers; 32,907 have institution evidence, producing 3,688 searchable institutions and populated curated org roots. The bundle now exposes OpenAlex-backed counts for those matches and **2,816** internal citation arrows. Its existing hierarchy labels were retained; a full `s06,s07` rerun is optional (see TODO). |
| Embeddings | **Local SPECTER2 proximity adapter**, 768-d, 271,366/271,366 coverage. |
| Layout | openTSNE 768→2D; reducer and map normalization frozen in `projector.pkl` |
| Clusters | UMAP→10D + HDBSCAN (**592** cluster values, 47.9% noise). |
| Semantic zoom | **Nested Leiden communities on the planar substrate:** 11 bands, 89,083 regions, 14,328 emitted labels. |
| Labels | Discriminative arXiv category signals + representative title/abstract c-TF-IDF phrases, with ancestor/sibling deduplication and shared title n-grams for small leaf communities. |
| Bundle | 157 files, **173.8 MB** in `web/public/data/` (sharded points, neighbors and paper detail). |

---

## 2026-08-14 continuation checkpoint: recent arXiv at scale

The bulk ingest and full local embedding pass are implemented and complete in this working
copy. `config.yaml` now selects `arxiv_snapshot`, 2025-01-01 through 2026-12-31, and any
listed category matching `cs.* OR stat.ML`.

Audited Cornell/Kaggle snapshot baseline (v1 submission dates, category in any position):

- 2025: **284,162** total arXiv papers; **152,880** in the union.
- 2026 snapshot through v1 2026-08-06: **204,831** total; **114,565** in the union.
- Snapshot JSON: 3,127,799 records / 5.45 GB, zero JSON/date errors and 100% required
  title/abstract/author/category/version coverage for the selected years.

The incremental OAI-PMH harvest from the snapshot watermark (`2026-08-08`) added 11,998
changed records. After arXiv-id upserts and scope filtering, the built corpus is **271,366
unique papers**: **152,884 from 2025** and **118,482 from 2026 through v1 2026-08-13**.
Every row has title + abstract. `versions[0].created` is the publication date; do not infer
it from the arXiv id month (18k+ audited records cross a moderation/month boundary).

The completed path is `arXiv title + abstract -> allenai/specter2_base + proximity adapter
-> normalized 768-D float32`. It is free/local and fetched no PDFs. The matrix is `(271366,
768)`, float32, 833.6 MB, finite, nonzero, and unit-normalized (norm range
0.99999976–1.00000024). It has 100% coverage and exact row alignment with
`corpus_active.parquet`. Against Semantic Scholar `specter_v2`, three sample cosine matches
were 1.0000 / 0.9994 / 1.0000.

Completed here:

1. Cornell snapshot streaming plus append-only, daily resumable OAI-PMH deltas.
2. arXiv-id upsert/delete handling and deterministic dense corpus rows.
3. Checkpointed local SPECTER2 proximity-adapter inference and S2 compatibility check.

`s15` bulk OpenAlex enrichment completed on 2026-08-14 using 12 concurrent 100-id batches.
The normal next stage is now `s16_apply_openalex_citations`: it locally materializes the
crosswalk's OpenAlex counts and exact-match corpus-internal references, preserving every provider
field and making citation data immediately available. `s16_enrich_s2_citations` remains an
explicit, resumable reconciliation job: it resolves the arXiv spine to S2 hashes in cached/paced
batches, streams the S2 hash→corpus-id crosswalk, then scans the S2AG citation release one shard
at a time (deleting each raw shard after it is processed). Its source/release/license/stats are
recorded in `s2_citation_meta.json`; it does not add its counts to OpenAlex.
It consumed $0.4371 of the free $1 daily API allowance and produced 244,730 exact matches:
209,008 by arXiv DOI, 21,419 by author-provided DOI, 2,251 by HTTPS landing URL, and 12,052
by HTTP landing URL. Coverage is 126,897/152,884 (83.0%) for 2025 and 117,833/118,482
(99.5%) for 2026. The current active corpus and organization/search artifacts include the
result without re-embedding. Next: run `s16`, then rebuild citation-dependent artifacts from
`s12,s09,s08,s06,s07,s10,s11` for provider-backed citation counts and directed edges.

## 2026-08-15 agent handoff: S2 scan and review follow-up

The Semantic Scholar bulk reconciliation is running in detached tmux session
`s2-citations`. The worker uses an in-memory `enabled=True` override, so the default config
remains unchanged. Its ignored log is `data/interim/s2_citation_run.log`. At this handoff,
identity resolution is **270,321 / 271,366** (99.6%), the paper-ID crosswalk is complete (30/30),
and the citation pass is at **4 / 393** shards. No S2 data is committed to the active corpus or
frontend bundle until every shard finishes. The `s2-citations-notify` tmux watcher sends a
desktop notification on completion or failure.

OpenAlex credentials support `OPENALEX_API_KEY` plus optional `OPENALEX_API_KEY_2`. The client
rotates atomically to the next configured key when the current key reports daily quota
exhaustion, while retaining exponential backoff for transient 429s. Keys are never logged.

The first run failed on an invalid temporary S2 URL after six citation shards. The restarted
worker includes retry logic in `s16_enrich_s2_citations.py`: on an HTTP error from a presigned
citation URL it refreshes the S2 dataset manifest and retries that ordinal. Focused S2 tests
pass (`4 passed`). After completion, inspect `s2_citation_meta.json`, then rebuild
`s12,s09,s08,s06,s07,s10,s11` and verify that S2 values reach `CORPUS_ACTIVE` and the emitted
bundle; the stage commits its full-corpus artifacts only after a complete scan.

Playwright is installed and healthy (Chromium present; Playwright 1.61.1; Node 24 at
`/tmp/research-atlas-node-v24/bin`). The focused desktop E2E run passes. The earlier startup
failure was `listen EPERM` when the test web server tried to bind localhost inside a restricted
sandbox; it is not a missing-browser or application failure. Permit localhost binding when
running browser verification in that environment.

The latest review identified three fixes; all three are now applied and covered by the pipeline
test suite (`81 passed`):

1. `pipeline/run_all.py` now skips OpenAlex citation materialization for `corpus.source:
   openalex`, preserving that supported source path.
2. `s15_enrich_openalex.py` now copies matched OpenAlex field/subfield/topic IDs and names into
   canonical facet columns, retaining arXiv taxonomy as fallback.
3. `s02_build_arxiv_corpus.py` now chooses a qualifying `cs.*` or `stat.ML` category for
   cross-listed records (for example, `math.OC cs.LG` is categorized under `cs.LG`).

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
8. **The arXiv corpus is decoupled from organization selection, and attribution is partial.**
   The OpenAlex crosswalk resolves 90.2% of papers, but only 32,907 currently carry institution
   evidence; do not interpret an unassigned paper as evidence of no affiliation.
   OpenAlex-source builds still use the configured scope. Dept/lab sub-units come from
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

- Pipeline unit tests: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest` (69 tests, including
  arXiv normalization/OAI parsing, SPECTER2 checkpointing, and org attribution invariants).
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
