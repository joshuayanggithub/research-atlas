# Design

This document records the important design decisions behind the Research Visualizer.
It is the "why"; `Features.md` is the "what".

## Goal

An interactive 2D **map of CS/AI research**. Each paper is a point; semantically similar
papers sit near each other; directed edges are citations; and the map supports
**Google-Maps-style semantic zoom** (broad fields when zoomed out → ML topics mid-zoom →
fine subtopics zoomed in). Users filter by **organization**, **author**, and **date**, and
inspect a paper's **citations** and **related works**.

## Architecture re-evaluation (2026-07)

The offline-pipeline/static-client split is the right MVP architecture. Projection,
clustering, citation joins, and label generation are global batch computations, while the
current 28k-paper bundle is small enough to load once and explore entirely in the browser.
Adding an application backend now would increase operating complexity without fixing the
important product gaps.

The current implementation is nevertheless a **seed-corpus demonstrator**, not yet the
architecture described by the product goal:

- The corpus is fetched only for seven configured institutions. It cannot discover or
  filter "any organization" without editing config and rebuilding the entire map.
- OpenAlex institutions do not provide department/lab granularity. The current org index
  is a flat aggregate, not a university -> department -> lab hierarchy.
- Semantic-zoom regions come from Leiden over a **planar substrate** (2D-layout kNN
  adjacency, 768-D cosine weights), so a region is one contiguous area of the map rather
  than being scattered across it. See §5 for the measured before/after. The hierarchy and
  names still need evaluation against a human-reviewed topic benchmark.
- Semantic Scholar is queried by arXiv → DOI → MAG id (see §3); drop mode still removes the
  remainder to keep one coherent SPECTER2 space, which biases the visible corpus toward
  papers S2 indexes under a resolvable external id.
- The frozen projector previously omitted the display normalization. `projector.pkl` now
  stores the reducer together with its fit-time center and scale so transformed points land
  in the same map coordinate system.

The next architecture work should establish truthful organization attribution, improve
corpus coverage, and validate graph/topic quality before adding more presentation features.
See **Re-evaluated target architecture** below.

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
- **CSRankings** is a useful, optional academic-directory and venue-taxonomy provider. Its
  data is CC BY-NC-ND (NonCommercial + NoDerivatives; the underlying DBLP layer is separately
  ODC-BY), so transformed data remains disabled in redistributable builds unless permission
  or compatible legal guidance is obtained. Its faculty→institution map is manually curated,
  not an automated affiliation matcher we could reuse. (Verified against the CSRankings
  README; see `docs/RESEARCH_PRIOR_WORK.md`.)

### 2. Organizations: two-level drill-down from affiliation evidence

OpenAlex institution names are ambiguous and companies fragment into regional entities
(e.g. "Meta (United States)" vs "Meta (Israel)"; five separate "Microsoft Research"
nodes). So each org in `config.yaml` pins an explicit **list** of verified institution ids
that we OR into the corpus filter.

**Department/lab drill-down (implemented).** OpenAlex institution ids alone have no
sub-unit granularity, but the *raw affiliation strings* on each authorship do
("Robotics Institute, Carnegie Mellon University", "Facebook AI Research (FAIR)"). `s02`
now retains these strings, scoped per authorship to the org institution OpenAlex already
resolved, into a separate `affiliations.parquet` (paper-id keyed, so it never perturbs the
frozen `node_id` ordering). A curated, reviewed unit registry (`pipeline/directory/`)
matches those strings into evidence-backed sub-units, which `s10` emits as a two-level
hierarchy in `orgs.json` (root org → department/lab, each with rollup and direct counts).

The matcher is deliberately conservative, matching the confidence-95 "exact unit name in
the paper's raw affiliation" tier of `docs/ORGANIZATION_DIRECTORY.md`:

- Descriptive names ("Robotics Institute") match case-insensitively; short ambiguous
  acronyms ("FAIR", "SAIL", "EECS") match **only** as standalone uppercase tokens, so
  lowercase prose ("a fair comparison") never creates a false attribution.
- The most-specific unit per string wins (a lab beats its parent school).
- A parent-org match **never** implies a child unit. Papers with no matching unit name stay
  parent-only — an expected, necessary "unresolved" state, not an error.
- Evidence is org-scoped, so a co-author's Stanford string can never be attributed to a CMU
  unit. Golden tests (`test_directory.py`) lock down Meta/FAIR separation, the ambiguous
  acronym guard, and cross-org isolation. FAIR resolves to exactly 425 papers — the
  migration acceptance baseline in the directory design doc.

**Still a seed-corpus shortcut.** Organization selection still determines which papers
exist in the map as well as which filter options the UI exposes, and the hierarchy is only
two levels (no schools-of-schools, no temporal validity, no cross-institution researcher
identity). Verified prior work supports the design: **ROR/OpenAlex deliberately stop above
departments** (only ~0.06% of ROR records are university-child departments), so the curated
`units.py` matcher is state-of-practice for sub-institution granularity, not a stopgap —
while institution-level attribution *is* fully automatable (OpenAlex/ROR/AffRo). See
`docs/RESEARCH_PRIOR_WORK.md` §2. Supporting *arbitrary* organizations still requires decoupling corpus discovery
(field/date based) from an independently versioned, many-to-many membership index. The
previous `Meta AI (FAIR)` seed was specifically incorrect: its two OpenAlex IDs are broad
Meta entities, so the config now names that aggregate `Meta`, and FAIR is a narrower
child attribution beneath it.

The full target — a provider-neutral directory with stable local identities, a typed
temporal organization DAG, aliases, researchers, dated affiliations, direct attributions,
and derived rollups — plus the CSRankings adapter, evidence policy, artifact-v2 contract,
and phased migration, is specified in
[`docs/ORGANIZATION_DIRECTORY.md`](docs/ORGANIZATION_DIRECTORY.md). This work implements
its Phase 1 (retain evidence) and a conservative slice of Phase 2/5 (curated child units +
a hierarchical filter UI) without yet emitting the v2 artifact schema.

### 3. Embeddings: swappable backend + one consistent space

`embedding/base.py` defines an `EmbeddingBackend` protocol. `specter2_s2` fetches
precomputed vectors from Semantic Scholar (batches of 500, DOI/arXiv-addressed, on-disk
cached, backoff + per-batch skip on non-retryable errors); `scincl_local` runs
`malteos/scincl` locally (MPS on Mac). Vectors are L2-normalized centrally so all
downstream cosine math is uniform.

**Addressing papers in Semantic Scholar (three routes).** S2's batch endpoint is keyed by
external id, and no single id resolves everything: `s03` tries **arXiv → DOI → MAG** per
paper, and each pass retries only the rows still uncovered. This matters because OpenAlex
sometimes records a landmark paper under a DOI S2 does not index. "Attention Is All You
Need" is the canonical case — OpenAlex gives it only `doi:10.65215/2q58a426` (unknown to
S2) plus `mag:2626778328`, which S2 *does* resolve and has a SPECTER2 vector for. Measured
on the previous build's dropped rows, 23.5% carried a MAG id and 57% of those came back
with a real vector, so the MAG pass alone recovers thousands of papers with zero change to
the vector space.

**Handling still-uncovered papers (`embedding.on_uncovered`).** SPECTER2 (fetched) and
SciNCL (local) occupy *different* embedding spaces, so mixing them at scale creates a
visible artificial "island" on the map (papers cluster by *model*, not topic). Two policies:
- `drop` (current default): keep only papers with a real SPECTER2 vector → one clean
  space. This drops rows, so `s03` **compacts the corpus** (`corpus_active.parquet`) with
  fresh dense `node_id`s; s04–s11 read the active corpus.
- `fill_local`: fill uncovered rows with SciNCL, but if S2 coverage is below
  `s2_min_coverage`, re-embed the *whole* corpus locally (never mix at scale).

**Future fix** (the user's question — "can SPECTER2 generalize to other papers?"): yes —
SPECTER2 is an open *model* (allenai/specter2, Apache-2.0), not just S2's lookup table. A
planned `specter2_local` backend runs the model on whatever the arXiv/DOI/MAG passes still
miss, giving one SPECTER2 space at 100% coverage with no island and no dropped rows.

### 4. Layout vs clustering: two independent reductions

- **Layout**: openTSNE 768→2D with the "PubMed-landscape" recipe (PCA init, uniform
  affinities k=10, cosine metric, exaggeration annealing). The fitted embedding is
  **frozen** (`projector.pkl`) together with the fit-time center and scale, so future
  incremental runs `transform()` new papers into the same displayed coordinate system.
- **Clustering**: a *separate* UMAP 768→~10D + HDBSCAN. We deliberately **do not cluster
  on the 2D coords** — UMAP's own docs warn that clustering the display projection creates
  false tears / density artifacts. The resulting `cluster_leaf` is emitted per paper, but
  the current UI does not use it to construct semantic-zoom regions.

### 5. Semantic zoom: nested communities on the planar substrate

`s06_hierarchy` recursively runs **Leiden** (reference `leidenalg`/`igraph`,
`RBConfigurationVertexPartition`) over the **planar substrate**: the *adjacency* is the 2D
layout's kNN graph (`hierarchy.planar_k`), and the *edge weights* are 768-D embedding
cosine. Band 0 targets `root_clusters`; each eligible parent targets `branching` children.
Resolution is found by geometric bisection on the requested child count. A parent with no
internal spatial edges falls back to deterministic spherical clustering in the original
embedding space.

**Why the adjacency is planar.** A region is only meaningful if it reads as one contiguous
area of the map. The previous default detected communities in the fused 768-D + citation
graph, which is *not* planar: two papers can be graph-neighbors while sitting at opposite
corners of the layout. Measured on the 28k corpus, each band-0 "continent" was scattered
across **~122 disconnected on-screen fragments**, and at band 6 only **32%** of a node's 10
nearest on-screen neighbors shared its region. Visually that is indistinguishable from bad
embeddings — unrelated papers appear grouped and every region overlaps every other. This
was the actual cause of the "dissimilar papers clustered together" symptom; the embeddings
themselves were fine (kNN-15 subfield purity 0.686 in 768-D, and t-SNE retains ~100% of it).

Restricting adjacency to the 2D kNN graph makes regions contiguous *by construction* and
costs no accuracy — it *improves* topic purity, because communities are no longer stitched
together across unrelated parts of the map:

| band | fragments/region | on-screen coherence | topic purity |
|---|---|---|---|
| 0 | 122.2 → **1.9** | 0.903 → **0.998** | 0.239 → **0.317** |
| 3 | 19.3 → **1.0** | 0.647 → **0.974** | 0.533 → 0.531 |
| 6 | 4.9 → **1.0** | 0.382 → **0.727** | 0.624 → **0.630** |

Weights stay in 768-D so *which* papers group within a neighborhood remains semantic; the
layout only decides what is adjacent.

Bisection also replaced an over-segment-then-agglomerate heuristic that was silently doing
the real partitioning: the hand-tuned resolution ladder returned **2** communities at band 0
where 8 were requested, so the map's shape came from post-hoc centroid agglomeration rather
than from community detection at all.

Leiden (not NetworkX Louvain) because Louvain can leave communities badly connected or even
internally disconnected — up to 25% badly connected and 16% disconnected in the "From
Louvain to Leiden" analysis (Traag, Waltman & van Eck, 2019) — and that worsens under
recursive per-parent splitting. The prior fused-graph methods remain selectable for
comparison (`hierarchy.method: "leiden" | "louvain" | "kmeans" | "quadtree"`). See
`docs/RESEARCH_PRIOR_WORK.md` §1.4 for the evidence.

Child memberships are strict subsets of their parents, and a split's children exactly
partition that parent. The current build has **11 bands and 24,215 regions** (see §5's
micro-cluster note for why the hierarchy was deepened). Small terminal communities stop
splitting, so coarse labels persist where no defensible finer partition exists. The frozen
2D coordinates only determine each community label's centroid and bounding box.

`s07_label` combines two signals at every band:
- **discriminative OpenAlex topics**, scored by prevalence inside the community and rarity
  across the corpus;
- **c-TF-IDF phrases** from embedding-representative papers, with titles weighted above
  abstract boilerplate and MathML/XML vocabulary removed.

MathML removal is **structural** (strip attribute pairs, then tags, then namespace
leftovers) and applied to titles *and* abstracts before they enter the label vocabulary. A
stopword list is not sufficient: OpenAlex abstracts embed raw MathML for formulas, whose
attribute names are ordinary words to a tokenizer and win c-TF-IDF by looking rare. That is
how a 7,408-paper region came to be named "Quantum Computing Algorithms and Architecture:
**Stretchy False**" (from `<mml:mo stretchy="false">`); each new attribute name would
otherwise reintroduce the bug.

Labels are assigned top-down with ancestor and sibling deduplication. A topic and a
community-specific phrase can be composed into a detailed name such as
"World Models: Action-Conditioned Dynamics."

Each label carries `{x, y, text, level, priority}`. Zoom bands are emitted as **offsets
from a runtime "fit" zoom** the frontend computes from the coordinate bounds + viewport,
so the map is calibrated at any window size. The frontend declutters labels with a **CPU
greedy screen-space algorithm** (`useLabelLayers`): project candidates to pixels, place
highest-priority first, skip any overlapping an already-placed box, dedupe repeated texts.
Coarse/high-count labels win when zoomed out; finer labels' screen positions spread apart
and appear as you zoom in. (We do *not* use deck.gl's `CollisionFilterExtension` — it culls
all instances in a non-geospatial `OrthographicView`; the CPU approach is deterministic and
cheap at ~200 labels.)

**Filter-aware labels + emphasis.** When an org/author filter is active, a topic name over a
now-empty region is misleading. `useRelevantLabels` assigns every *matching* paper to its
nearest label per band and keeps only labels that win at least one paper, so the visible
labels and point colors describe the filtered subset rather than the whole map. Filtered-out
points drop to near-transparent (and shrink) instead of merely dimming, so the matching set
reads as the map. Nodes are kept small and citation edges carry more visual weight, so dense
topic regions read as distinct color fields when zoomed out rather than blobbing together.

**Ultra-fine "micro-cluster" labels (implemented — option 1).** The hierarchy now recurses to
**11 bands** (`max_depth: 11`, `min_cluster_size: 8`, `min_tile_points: 3`); the deepest band
resolves ~3-paper micro-clusters. Because c-TF-IDF loses discriminative power at n<10 (with
`min_df=1` every rare n-gram looks distinctive), `s07` names small leaf communities from the
phrase their member **titles literally share** (`_shared_title_phrase`: the longest 2–6-word
content phrase in ≥2 titles, ties broken by summed citations; a singleton falls back to its
own most specific title phrase). This yields concrete, verifiable names at max zoom
("Differential Privacy Sum-Of-Squares Exponential", "Architecture Microsecond-Scale Datacenter
Systems") instead of noisy c-TF-IDF. The seam is unchanged — `labels.json` just gained deeper
bands (835 → ~2,500 labels, bundle 17 → 19 MB) — and the rebuild was `--only s06,s07,s11`
(no re-embed/re-project). The frontend derives `maxZoom` from the emitted bands so the deep
bands are reachable. Leaf labels are still embedding-proximity groups, not validated topics
(HANDOFF gotcha #7 still applies).

Two heavier alternatives were considered and deferred: **on-the-fly frontend labels** at max
zoom (adapts to pan/zoom but recomputes per viewport and is less stable) and a **cached LLM
leaf-naming pass** (highest quality — "Sim-to-Real RL for Legged Locomotion" — at the cost of
an API dependency, spend, and a cache keyed by community hash with deterministic fallback).
The LLM pass remains the ROADMAP's highest-value upgrade for the leaf bands if crisper names
are wanted later.

### 6. Related works: fused text + citation similarity (Connected-Papers idea)

`s08_neighbors` builds semantic candidates with hnswlib, then unions them with direct
citations and the strongest **bibliographic-coupling** (shared references) and
**co-citation** (shared citers) candidates. It ranks the union by
`fused = α·cosine + (1−α)·citation_score` (α≈0.6); a direct citation contributes the
maximum citation score. In the current bundle, 97.7% of direct references appear in the
citing paper's final top-15 neighborhood. Citation-strong/text-distant papers are now
eligible rather than being excluded before scoring.

### 7. Rendering: deck.gl with directed edges in the normal view

deck.gl (`OrthographicView`) draws the scatter (`ScatterplotLayer`), zoom labels
(`TextLayer`, CPU-decluttered), and citation links (`LineLayer` plus
`SolidPolygonLayer` arrowheads). The normal view shows a deterministic, zoom-adaptive
sample of the global citation graph by default. Sampling favors locally legible screen
lengths and increases as the user zooms; filters remove or dim links consistently with
their endpoints. Every arrow points from the citing paper to the cited paper, and the
legend can hide all citation links.

Selecting a paper overlays its capped incoming/outgoing links at full contrast with
direction colors, larger arrowheads, clickable endpoint rings, and unrelated points
dimmed. The citation explorer retains every intra-corpus link in searchable **References**
and **Cited by** lists even when the map overlay is capped. A separate **Paper** tab embeds
the first page from arXiv lazily; when the corpus lacks an arXiv ID, it tries an exact
Semantic Scholar DOI/title resolution and provides explicit fallback links.

Date filtering runs on the GPU via `DataFilterExtension`; org/author filtering dims or
hides non-matches.

Arrow files are written **uncompressed** — the browser's `apache-arrow` cannot decode
compressed record batches ("compression not implemented"); gzip/brotli at the HTTP/CDN
layer recovers the wire size.

### 8. Visual identity: a cartographic instrument, not a dashboard

The product *is* a map, so the UI is styled as a **stellar-cartography instrument** rather
than a generic dark dashboard. Deliberate, subject-specific choices (2026-07 redesign):

- **Palette.** Deep-night `--void #07090d` (the map canvas shares this exact color, so chart
  and chrome are one continuous night) and `--chart #0e1219` panels, warm chart-ink ivory
  `--ink #ece3d2` instead of cold white, and a single signal color — brass/amber
  `--brass #e3a63c` = "the marked location you're navigating toward." Brass is **chrome-only**
  (active states, ticks, wordmark star); it is never a data hue, so it never collides with
  the categorical point palette or the teal/amber citation-direction colors.
- **Type.** *Space Grotesk* for the wordmark, panel/paper headings; *IBM Plex Mono* for every
  **measurement readout** — counts, years, citation numbers, coordinates, and structural
  eyebrows — because on an instrument a number is a reading. Body prose stays system-ui
  (two webfonts, not three).
- **Structure & signature.** Panels are near-square (3px radius) with warm hairline rules and
  mono uppercase section eyebrows. The signature is **corner registration ticks** (brass crop
  marks) on each "chart sheet" panel, plus a **north-star wordmark** (`✧ RESEARCH ATLAS`).
- **Restraint.** Boldness is spent only on the signature + the single brass accent; motion is
  minimal and `prefers-reduced-motion` is honored. Dark is dictated by the subject (luminous
  points read best on night), and the accent deliberately avoids the three common AI-default
  looks (cream+serif, acid-green/vermilion-on-black, broadsheet hairlines).

## Artifact contract (the seam)

Large columnar data → **Arrow IPC** (zero-copy in the browser); small structured data →
JSON. `points.arrow`'s row index **is** the `node_id`; everything else references it.
Files: `points`, `papers`, `neighbors`, `edges`, `authors` (Arrow); `clusters`, `labels`,
`orgs`, `topics`, `manifest` (JSON). See `pipeline/common/schema.py`.

## Risks & mitigations (as built)

- **S2 rate limits / bad batches** → on-disk cache, exponential backoff, and per-batch
  skip. Failed rows become uncovered and follow the configured drop/local policy.
- **Abstract reconstruction** → dedicated, unit-tested module; ~85% coverage logged;
  title-only fallback for the rest.
- **Layout instability** → frozen projector; deterministic node ordering by paper id.
- **Label clutter** → CPU screen-space collision checks + per-band gating + priority
  ranking.
- **Community/label quality** → now measured against OpenAlex subfield/topic labels as
  weak ground truth: kNN-15 purity (768-D vs 2D vs per-band region purity), plus per-band
  **on-screen contiguity** (spatial fragments per region), which is what caught the planar
  bug in §5. Reproduce with `pipeline/tests/test_hierarchy.py` for the invariants; the
  purity/contiguity sweep is a diagnostic, not a fixed gate. A human-reviewed
  neighborhood/topic benchmark remains P0 — OpenAlex topics are noisy and only 267 of them
  cover the corpus, so region purity of ~0.65 is a floor, not a ceiling.
- **Filter jank** → date on GPU; org/author masks memoized on selection, not on zoom.

## Re-evaluated target architecture

The migration path should preserve the existing stage boundary and static MVP while
changing the semantics behind it:

1. **Corpus discovery independent of organizations.** Ingest a broad CS/AI corpus by
   field and date from the OpenAlex snapshot. Treat organizations as filter indexes, not
   fetch predicates. Publish a small, reproducible sample bundle for local development.
2. **Canonical organization graph.** Represent organizations as stable entities with
   typed parent/child relations, aliases, source identifiers, and temporal validity. Store
   authorship-backed paper-to-org memberships separately with provenance and confidence.
   Resolve departments/labs from raw affiliation strings, official rosters, and curated
   overrides. See [`docs/ORGANIZATION_DIRECTORY.md`](docs/ORGANIZATION_DIRECTORY.md).
3. **One embedding space at complete coverage.** Run the same local SPECTER2 model for
   papers missing precomputed vectors, or embed the entire corpus locally. Never blend
   unrelated embedding spaces in one map.
4. **Fused high-dimensional graph (implemented at MVP scale).** Semantic kNN, direct
   citation, bibliographic-coupling, and co-citation candidates now share one ranking
   pipeline and drive both related works and hierarchy construction.
5. **Multiresolution semantic communities (implementation complete; validation pending).**
   Nested Leiden communities now define zoom membership, with 2D used only for placement.
   Evaluate neighborhood precision, hierarchy stability, and label quality on a small
   human-reviewed benchmark before treating the hierarchy as ground truth.
6. **Versioned artifacts and tiled serving.** Keep static, content-addressed bundles at
   MVP scale. Beyond roughly 100k papers, emit spatial tiles and lazy metadata/edge
   partitions behind a CDN or object store; add an API only for queries that cannot be
   answered from those artifacts.

## Scaling path (future phases)

Stage boundaries and the artifact contract are identical across scales — only each stage's
*implementation* and the frontend's *data-loading* (static → tiled) change. Phase 1: bulk
OpenAlex S3 snapshot + SPECTER2 bulk datasets, tiled point/label streaming (deck.gl
`TileLayer`), GPU/DuckDB-WASM filtering past ~200k points. Phase 2: department/lab
granularity via affiliation parsing. Phase 3: a live vector DB for "embed my own text".
