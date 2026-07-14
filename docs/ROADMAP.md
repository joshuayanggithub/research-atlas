# Roadmap — next work, prioritized

Ordered by value-per-effort for the next agent. Each item says *why*, *where*, and gives a
concrete starting point. The MVP is done and verified; these are improvements and scale-ups.

---

## Known rough edges (quick wins)

### 1. Band 0 is dominated by "Artificial Intelligence"
AI is ~67% of the corpus, so most top-level k-means regions are AI-majority and get the
same coarse label. **Fix options** (in `pipeline/stages/s07_label.py` / `config.yaml`):
- Bump `hierarchy.root_clusters` (8 → ~14) so band 0 has more, smaller regions.
- When a band-0 region is overwhelmingly one subfield, label it by its **majority
  band-1/topic** name instead of the subfield (more informative than "Artificial
  Intelligence" ×5).

### 2. A few fine labels are still generic ("Local Data", "Sonar Web")
c-TF-IDF top-1 phrases are occasionally weak. **Fix**: a one-time **LLM naming pass** over
each region's top keywords + a few representative titles → clean topic names. Cheap because
it runs once per region (~2k regions), not per paper. Add as an optional `s07` step gated by
a config flag; cache results keyed by region membership hash so reruns are free.

### 3. Citation arcs are hard to see in the dense cloud
`useEdgeLayer` draws them but they're lost among 28k points. **Fix**: on select, fade
non-neighbor points harder, or auto-zoom to the selected node's neighborhood.

---

## Medium: the SPECTER2 coverage fix (removes the dropped 28%)

**Problem**: `on_uncovered: drop` discards ~11k papers Semantic Scholar has no SPECTER2
vector for. The user asked "can SPECTER2 generalize?" — **yes**: SPECTER2 is an open model
(`allenai/specter2`, Apache-2.0), not just S2's lookup table.

**Fix**: add a **`specter2_local` embedding backend** that runs the SPECTER2 model locally
(via the `adapters` library: base `allenai/specter2_base` + the proximity adapter
`allenai/specter2`) on papers S2 didn't cover. Then the pipeline gets **one SPECTER2 space
at 100% coverage, no dropped rows, no island**.
- Where: new `pipeline/embedding/specter2_local.py` implementing `EmbeddingBackend`; wire
  into `s03_embed.run` so `on_uncovered: "fill_local"` uses it instead of SciNCL (or add a
  third `on_uncovered: "fill_specter2"`).
- Note: `adapters` is a different API than sentence-transformers (see `scincl_local.py` for
  the device-handling pattern to mirror).
- Then flip `config.yaml` `on_uncovered` and rerun `s03`→`s11`.

---

## Medium: get a Semantic Scholar API key
The S2 shared pool rate-limits hard (429s, minutes of backoff). A free key (set `S2_API_KEY`
in `.env`, already plumbed through `config.secrets` → `Specter2S2Backend`) raises limits and
makes `s03` fast + coverage higher. Request at semanticscholar.org/product/api (approval can
take days). No code change needed — just the env var.

---

## Larger: Phase 2 — university → department → lab granularity
Currently org = OpenAlex institution level (OpenAlex has no dept/lab nodes). The spec wants
university→dept→lab (e.g. Berkeley → BAIR). **Approach** (from `Design.md`): parse
`authorships[].raw_affiliation_strings` (already fetched in `s01`'s `SELECT`) for
"Department of …" / lab names, and/or seed from the CSRankings faculty roster (CC BY-NC-ND —
use as a *reference to regenerate*, don't ship their CSVs). Build a nested org tree artifact;
make `OrgFilterPanel` a collapsible tree. This is real work — scope it as its own phase.

---

## Larger: Phase 1 — scale toward ~1M papers
The MVP ships everything statically (~17 MB). That breaks past ~100k points. When scaling:
- **Fetch**: switch `s01` from the metered API to the free OpenAlex S3 snapshot
  (`s3://openalex`, ~330 GB, `--no-sign-request`); join SPECTER2 from S2's bulk
  `embeddings-specter_v2` dataset instead of per-paper batches.
- **Projection/clustering**: openTSNE time/RAM grows; HDBSCAN blows up past ~200k. Fit on a
  subsample + `projector.transform()` the rest; swap HDBSCAN → hierarchical k-means (already
  behind `cluster.method`).
- **Frontend**: `papers`/`neighbors`/`edges` shipped whole become hundreds of MB. Move to
  **tiled** point/label streaming (deck.gl `TileLayer` + quadtree Arrow tiles) and serve
  metadata/neighbors on demand; push filtering to GPU bitmasks or DuckDB-WASM.
The stage boundaries + artifact contract are designed to stay the same — only stage
*internals* and the frontend *data-loading layer* change.

---

## Smaller ideas
- **Recency/venue color modes** exist (`colors.ts`); add a "citation count" heat mode.
- **Deep links / saved views** — encode viewState + filters + selection in the URL.
- **Local citation subgraph panel** (sigma.js / cosmos.gl) for a Connected-Papers-style
  drill-down on the selected node (currently only the related-works list + arcs).
- **KDE "topography" contours** under the points (deck.gl `ContourLayer` or precomputed
  paths) for a map-like feel.
- **Incremental updates**: nightly OpenAlex delta → embed new papers →
  `projector.transform()` (existing points don't move) → append to clusters/labels/neighbors.

---

## Testing expectations for any change
- `uv run pytest` stays green (add tests for new pipeline logic — see
  `pipeline/tests/test_abstract.py`, `test_corpus.py` for the style).
- `cd web && npx tsc -b && npm run build` stays clean.
- After pipeline changes, rerun affected stages and **look at the map in a browser** — the
  previous agent caught 4 real bugs (DOI mangling, topic-id parsing, zstd Arrow, label
  culling) *only* by rendering it, not from tests. Don't skip the visual check.
