# Design decisions — tradeoff log

A **decision log**, distinct from the other three docs:

- `Features.md` — *what's possible* (capabilities).
- `Design.md` — *how it's implemented* (the current mechanism).
- **This file** — *why we chose it over the alternatives, and what it would cost to revert.*
- `TODO.md` — *what's left to build.*

Each entry records the decision, the alternatives we rejected, the reason, and a **Revert**
line: the trigger that would make us reconsider and the concrete cost of undoing it. When you
change a decision, don't delete its entry — mark it superseded and add the new one, so the
history of *why* survives.

Status legend: **ACTIVE** (in force) · **PROPOSED** (agreed, not yet built) · **SUPERSEDED**.

---

## D1. Offline pipeline + static bundle, no backend — ACTIVE

- **Decision.** Compute embeddings, layout, clustering, labels, and joins **once, offline**
  in the Python pipeline; ship static Arrow/JSON the browser loads directly.
- **Alternatives.** A compute-on-demand backend (API + DB + vector index).
- **Why.** Map positions are global (a point's (x,y) depends on all others) and change
  slowly, so per-request compute buys nothing; static files make hosting trivial and the UI
  fast.
- **Revert.** Trigger: corpus outgrows an in-browser bundle (~>100k papers over the wire) or
  we need live "embed my own text." Cost: the artifact seam (`schema.py` ⇄ `types.ts`) is
  designed to survive this — the migration is to *tiled* static serving first, and only then
  a query service for what tiles can't answer (see `ROADMAP.md` P2). Reverting the whole
  "no backend" stance means adding infra + ops that the MVP deliberately avoids.

## D2. SPECTER2-only space via `on_uncovered: drop` — SUPERSEDED by D15

- **Decision.** Keep only papers with a real SPECTER2 vector from Semantic Scholar; compact
  the corpus to fresh dense `node_id`s (`corpus_active.parquet`).
- **Alternatives.** `fill_local` (fill misses with SciNCL) — rejected at scale; mixing two
  embedding spaces creates a visible artificial "island" (papers cluster by *model*, not
  topic). See HANDOFF gotcha #6.
- **Why.** One clean, coherent space is worth dropping rows. Biases the visible corpus toward
  papers S2 indexes under a resolvable external id — an accepted MVP cost.
- **Revert.** Trigger: coverage gaps hide papers you care about. Cost: **low and planned** —
  build the `specter2_local` backend (same allenai/specter2 model, Apache-2.0) to embed the
  misses into the *same* space, giving 100% coverage with no island and no dropped rows. This
  is the intended fix, not a true reversal. (ROADMAP P0 #4.)

## D3. Semantic Scholar addressing: arXiv → DOI → MAG — ACTIVE

- **Decision.** Resolve each paper against S2's batch endpoint by trying arXiv id, then DOI,
  then MAG id; each pass retries only still-uncovered rows.
- **Alternatives.** DOI-only (what we started with).
- **Why.** OpenAlex sometimes records a landmark paper under a DOI S2 doesn't index (canonical
  case: "Attention Is All You Need" — recovered only via its MAG id). Measured: the MAG pass
  alone recovered thousands of rows with zero change to the vector space.
- **Revert.** No reason to; strictly additive. Removing a route only loses coverage.

## D4. Layout (openTSNE) separate from clustering (UMAP+HDBSCAN) — ACTIVE

- **Decision.** openTSNE 768→2D for *display*; a separate UMAP→10D + HDBSCAN for
  `cluster_leaf`. Never cluster on the 2D display coords.
- **Alternatives.** Cluster directly on the 2D projection (simpler, one reduction).
- **Why.** UMAP's own docs warn clustering the display projection creates false tears/density
  artifacts. openTSNE also uniquely supports **frozen incremental** layout (`transform()` new
  papers into the same space) — the property `projector.pkl` relies on.
- **Revert.** Trigger: a benchmark shows PaCMAP/UMAP layout is measurably better on
  *layout/cluster coherence* (not a generic leaderboard). Cost: re-fit + re-freeze projector,
  full re-projection of the corpus; the stage boundary is unchanged. Benchmark with PCA init
  before switching — init dominates the "global vs local structure" tradeoff.

## D5. Semantic-zoom regions: Leiden over a **planar substrate** — ACTIVE

- **Decision.** Recursive Leiden where *adjacency* = the 2D-layout kNN graph and *weights* =
  768-D embedding cosine.
- **Alternatives.** (a) Louvain — leaves up to 25% of communities badly connected / 16%
  disconnected, worse under recursive splitting. (b) Communities in the **fused 768-D +
  citation graph** (the prior default) — *not planar*: graph-neighbors can sit at opposite
  corners of the map, so each band-0 "continent" scattered into ~122 disconnected fragments.
- **Why.** The planar substrate makes regions contiguous *by construction* and even *improves*
  topic purity. This was the actual cause of the "dissimilar papers clustered together"
  symptom — the embeddings were fine (see `Design.md` §5 table).
- **Revert.** All prior methods stay **selectable** (`hierarchy.method: leiden | louvain |
  kmeans | quadtree`) — reverting is a config flag + `--only s06,s07,s11` rebuild, no
  re-embed. Keep them for comparison; don't delete.

## D6. Leaf labels from shared title n-gram (not c-TF-IDF) at max zoom — ACTIVE

- **Decision.** For small leaf communities (n<10), name from the longest content phrase their
  member **titles literally share**; c-TF-IDF for coarser bands.
- **Alternatives.** (a) c-TF-IDF everywhere — loses discriminative power at n<10 (`min_df=1`
  makes every rare n-gram look distinctive). (b) On-the-fly frontend labels — adapt to
  pan/zoom but recompute per viewport, less stable. (c) Cached LLM leaf-naming — highest
  quality, at the cost of an API dependency + spend + a community-hash cache.
- **Why.** Concrete, verifiable names at max zoom with no external dependency.
- **Revert.** Trigger: leaf names feel weak on human spot-check. Cost: the **LLM pass is the
  documented highest-value upgrade** — additive (key by community hash, deterministic
  fallback), not a rewrite. (ROADMAP UX follow-ups.)

## D7. Arrow written **uncompressed** — ACTIVE

- **Decision.** Emit Arrow IPC uncompressed; rely on gzip/brotli at the HTTP/CDN layer.
- **Alternatives.** zstd/lz4 record-batch compression.
- **Why.** The browser's `apache-arrow` cannot decode compressed record batches ("compression
  not implemented"). This is a hard constraint, not a preference. (HANDOFF gotcha #3.)
- **Revert.** Only if `apache-arrow` gains decompression. Until then, reverting **breaks the
  frontend load** — do not.

## D8. On-demand sharding (neighbors, paper detail) by node-id block — ACTIVE

- **Decision.** Ship a resident papers *index* (title/year/citations/author_ids) + point
  tiles; fetch per-paper detail and related-works neighbors as node-id-sharded files on
  selection.
- **Alternatives.** Ship everything resident (the original bundle).
- **Why.** Keeps the *initial* download small as the corpus grows — the real constraint of a
  no-backend design. Shard = `node_id // SIZE`, computed with no lookup.
- **Revert.** Legacy whole-table path still exists (shard size 0 in the manifest). Reverting
  is a manifest flag; costs initial-download size at large N.

## D9. Organization membership for neolabs: **curated author-roster join**, not document parsing — ACTIVE

- **Decision.** Attribute neolab papers (Redwood, Anthropic-preprints,
  DeepSeek, …) by an **author-id roster** joined against the OpenAlex author ids already on
  every paper — *not* by parsing affiliation text from PDFs/HTML/LaTeX.
- **Alternatives, with evidence gathered this session:**
  - *OpenAlex `institutions`* (ROR-matched): neolabs have **no institution entity** → empty.
  - *OpenAlex `raw_affiliation_strings`*: publisher-sourced, ~58% of works but **empty for
    bare arXiv preprints** (verified: Redwood & DeepSeek both `[]`).
  - *arXiv `/html/` or S3 `src/` document parse*: **does** recover neolabs (verified: "Redwood
    Research", 86× "DeepSeek-AI"), but O(papers), rate-limited (1 req/3s, no parallelism) or
    egress-costed, fuzzy string→org matching, and **not time-bounded**.
  - *Registry seed (ROR + ORCID + Wikidata)*: auto-builds rosters for big labs (Anthropic 57
    ORCID people) but **fails for Redwood** — ROR mis-anchors it to "Redwood Family
    Dermatology," Wikidata has no entity, ORCID yields 3.
- **Why.** Membership is O(orgs × people), keyed on an **exact author id** (no fuzzy strings),
  reuses ids we already store, catches papers that omit the affiliation, and is
  time-boundable via ORCID/Wikidata dates. Matches the CSRankings pattern.
- **Revert / fallback.** If a neolab is registry-invisible, **bootstrap the roster from its
  own known papers' author lists** (co-authorship expansion + a short human approval list).
  Document parse (`/html/` for prototype, S3 `src/` for bulk) remains the *only* path for
  arbitrary long-tail affiliation and stays on the table for that separate goal — but is
  explicitly **not** on the neolab critical path. Anchor to ROR id where it exists; mint
  `local:<org>` ids where it doesn't (and never let a wrong ROR hit contaminate a local id).
- **Implementation (2026-08).** `org_rosters.yaml` holds reviewed exact-id claims;
  `s14_rosters` joins them to `corpus_active.parquet`, applies inclusive date bounds, and
  retains a paper/member/provenance evidence row in `roster_memberships.parquet`. `s10`
  emits those organizations as curated `neolab` roots with a canonical `organization_id`
  and `membership_methods`. Redwood is the first seed. Registry and reviewed co-authorship
  expansion remain optional roster-authoring inputs; neither silently runs during a build.

## D10. Author granularity: OpenAlex author id primary, ORCID validation, S2AND last — PROPOSED

- **Decision.** Trust the OpenAlex-disambiguated author id (already on every paper) as the
  primary key; use ORCID to validate/merge; reserve an S2AND-style model only for the id-less
  residual. Add a small curated **alias/override** map for known OpenAlex errors.
- **Alternatives.** (a) Build an author-name-disambiguation model from scratch — its main
  documented risk is poor cross-dataset generalization. (b) Google Scholar / SerpApi — best
  identity data but closed, not CC-licensed (can't redistribute in the bundle), per-request
  cost, and no clean crosswalk to OpenAlex ids. (c) arXiv — gives **names, not identities**
  (verified: bare `<name>` tags, no id; five indistinguishable "Wei Zhang"s).
- **Why.** OpenAlex already solved disambiguation at high coverage, free, CC0, and join-able;
  the leverage is a cheap correction layer, not a new model. Observed OpenAlex errors this
  session: split "Ethan Perez," phantom "DeepSeek-AI" author.
- **Revert.** If OpenAlex error rate proves too high, the override map grows first; only then
  consider self-hosting S2AND (open, cuts AND error >50% vs S2 production). Scholar/SerpApi
  usable only as a **manual curation eyeball**, never a redistributable feed.
- **2026-08-15 addendum — reconsider "S2AND last."** This decision assumed S2 involvement
  meant self-hosting an S2AND disambiguation *model* for the OpenAlex-id-less residual, which
  is why it was ranked last. That's no longer the only option: Semantic Scholar's own hosted
  Graph API (`/graph/v1/paper/arXiv:<id>`) already ships resolved author ids per paper, same as
  OpenAlex — no model-building required, just another API to join against. Empirically checked
  8 papers sampled from the 26,636-paper OpenAlex-unmatched residual (arXiv 2505.18134
  "VideoGameBench" + 7 random draws): S2 found **8/8**, every author resolved to a non-null id,
  and it correctly assigned author "Alex L. Zhang" the **same** S2 id (`2324917699`) on both
  2505.18134 and arXiv 2512.24601 ("Recursive Language Models") — exactly the cross-paper
  identity case OpenAlex fails on for this pair (OpenAlex has never matched 2505.18134 as a
  work at all, and separately returns a `null` per-authorship author id for this person even
  when a work lookup succeeds). Sample is small (n=8) and S2's disambiguation error rate at
  scale is still unverified, so this isn't a decision reversal yet — but it demotes "S2AND last"
  from "build our own model" to "just also query S2's existing API for the residual," which is
  far cheaper than this decision originally priced in. See `TODO.md` for the deferred
  follow-up (blocked on the in-flight `s2-citations` job finishing, to avoid API-quota
  contention).

## D11. First-figure extraction: **offline PyMuPDF baked crops, client-side pdf.js fallback** — ACTIVE

- **Decision.** Extract Figure 1 / Table 1 **offline in the pipeline** (stage s13) with
  **PyMuPDF** — caption anchor + `find_tables` / `cluster_drawings` / `get_image_info`, plus
  a text-block fallback for borderless tables — render the crop to a PNG, shard it by node id,
  and serve it statically. The resident papers index carries a `has_figure` flag; the details
  card prefers the baked crop and **falls back to the client-side pdf.js path**
  (`figureExtract.ts`) when none was baked. This is how Semantic Scholar does it (PDFFigures
  2.0), moved to build time.
- **Alternatives, benchmarked this session:**
  - *Client-side pdf.js only* (the prior approach) — no new dep, but the ink-scan is fragile
    (grabbed headers, over-trimmed to a sliver) and *failed outright on the Transformer paper*.
    Kept as the fallback, not the primary.
  - *PDFFigures 2.0* (S2's actual tool, Apache-2.0) — the reference method, but **Scala/JVM**,
    foreign to a Python pipeline.
  - *DocLayout-YOLO / PDF-Extract-Kit / marker* — ML vision layout models; heavier (GPU
    helps), overkill for born-digital arXiv PDFs.
  - *PyMuPDF* — pure-Python, CPU-only; **clean crops on all test papers**
    (Transformer/ViT/ResNet/DeepSeek/BERT figures + GPT-3's "Figure 1.1" + GLUE's borderless
    Table 1), including the Transformer paper the client-side version failed on.
- **Why.** PyMuPDF reproduces the PDFFigures pattern natively, far more robust than the
  ink-scan, fits the Python stack, and moving it offline means the browser serves an instant
  static image instead of parsing a ~2MB PDF per selection.
- **Revert / caveats.**
  - **License:** PyMuPDF is **AGPL-3.0**. Used *offline* to emit PNGs (we ship images, not the
    library) this is defensible, but it is a copyleft dependency in a public repo — a conscious
    call flagged for the maintainer. If AGPL is unacceptable, swap s13's extractor for
    **PDFFigures 2.0** (Apache, JVM cost); the frontend seam (`has_figure` + baked PNG) is
    extractor-agnostic, and the client-side pdf.js fallback still ships regardless.
  - **Acquisition cost:** s13 downloads a PDF per arXiv paper under arXiv's 1-req/3s limit, so
    a full-corpus bake is a multi-hour polite batch — hence `figures.enabled` defaults **off**
    and `figures.max_papers` caps a sample run. Bundles built without it fall back to pdf.js.
  - Reverting to client-side-only is a one-line frontend change (ignore `manifest.figures`);
    the baked path is purely additive.

## D12. Client-side figure extraction: pdf.js operator list, BOTH Figure 1 + Table 1 — ACTIVE

- **Decision.** The on-selection client-side extractor (`figureExtract.ts`) reconstructs
  figure/table boxes from the **pdf.js operator list** (walk `fnArray` tracking the transform
  stack; bound every `constructPath`/`paintImage` op; cluster touching boxes) and takes the
  cluster directly above figure captions — the PyMuPDF/PDFFigures method ported to the browser.
  It extracts **both Figure 1 and Table 1** (each when present). Because table captions are
  conventionally above the table, tables instead collect contiguous text rows below the real
  punctuated caption, stopping before following prose; an upward fallback covers unusual
  templates. This direction-aware rule fixed RLM Table 1, whose ruling lines appeared as two
  partial clusters and whose preceding sentence began with the misleading words "Table 1
  reports…".
- **Alternatives.** The prior **ink-density scan** — grabbed page headers/body text and
  over-trimmed ("much more of the page"); failed on the Transformer paper. Replaced.
- **Why.** Accurate, runs on-selection with no backend (PyMuPDF can't run in-browser), and
  matches the offline baked path (D11) so baked and fallback crops look the same.
- **Coordinate note (gotcha).** pdf.js operator-list geometry AND text transforms are both in
  **PDF user space (y-up, no viewport flip)** at scale 1 — "above the caption" = larger y.
  Getting this wrong inverts the box selection (found + fixed during the port).
- **Revert.** The offline baked crop (D11) takes precedence when present; this is the fallback.
  Reverting to the ink-scan is undesirable (it's the bug we fixed).

## D13. Relevance slider score = coupling + co-citation, GPU-channel reuse — ACTIVE

- **Decision.** On selection, score each connected paper by `|refs(sel)∩refs(p)| +
  |citers(sel)∩citers(p)|` (bibliographic coupling + co-citation, Connected-Papers-style),
  normalized to [0,1]. The slider raises the lower bound of the **existing GPU filter
  channel 2** (previously a binary selection-membership cull) — no new channel — and gates the
  selected edges the same way.
- **Alternatives.** (a) Reuse the precomputed **fused neighbor score** (text+citation, s08) —
  cheaper, no per-select compute, but the user explicitly chose the Connected-Papers signal.
  (b) A new GPU filter channel — unnecessary; channel 2 was already selection-scoped.
- **Why.** Matches the requested Connected-Papers behavior; overloading channel 2 keeps the
  filter at 4 channels (deck.gl `DataFilterExtension` filterSize).
- **Revert.** Score lives in one hook (`useRelevanceScores`); swapping to the fused score is a
  one-function change. The slider defaults to 0 (whole network), so it's inert until dragged.

## D14. Selection/label layer keys positional accessors on the placed set — ACTIVE

- **Decision.** The semantic-zoom `TextLayer` keeps a **stable id** but its `updateTriggers`
  key `getPosition`/`getText`/`getSize` on the exact placed-label set (ids + band).
- **Alternatives.** (a) The prior code omitted `getPosition` from `updateTriggers` → deck.gl
  reused previous rows' position buffers, so on selection labels showed the *previous* view's
  names at those slots (the reported bug). (b) Keying the layer **id** on the placed set —
  rejected: the set changes every zoom tick, so a per-set id tears down/rebuilds the layer each
  frame (flicker). Stable id + triggers is the correct deck.gl idiom.
- **Why.** Fixes stale labels without per-frame layer churn.
- **Revert.** None wanted; this is a bug fix. No dedicated test yet (frontend has no JS unit
  runner — noted in TODO).

## D15. Snapshot-first arXiv corpus + full local SPECTER2 space — ACTIVE

- **Decision.** Build the comprehensive recent corpus from Cornell's weekly arXiv metadata
  snapshot, apply append-only OAI-PMH deltas by arXiv-id upsert, and embed every selected
  title+abstract locally with `specter2_base` plus the proximity adapter. Use v1 creation
  timestamps and any-position `cs.* OR stat.ML` category membership. Checkpoint local
  inference against the ordered corpus fingerprint.
- **Alternatives.** (a) arXiv Atom API per paper/query — free but capped at one request per
  three seconds and unsuitable for hundreds of thousands of records. (b) OAI for all history
  — bulk-capable but slower than the snapshot and cannot select by submission date. (c)
  Semantic Scholar-only embeddings with `drop` — biased/incomplete. (d) SciNCL fill — a
  different vector space.
- **Why.** The snapshot parsed 3.13M records in about 70 seconds and had complete required
  metadata. Ten OAI pages caught the weekly lag. The resulting 271,366-paper corpus has 100%
  abstracts. Local RTX 3090 inference completed in 9m48s with 100% coverage; overlap cosine
  against S2 `specter_v2` was 0.9994–1.0000. There is no metadata or embedding API charge.
- **Limit.** arXiv metadata has no references, disambiguated people, or institutions. Those
  must be bulk-enriched before citation and organization features are rebuilt; provisional
  name-hash ids are explicitly not person identity claims.
- **Revert.** Switch `corpus.source` back to `openalex` and/or `embedding.backend` to
  `specter2_s2`; stage and artifact seams remain unchanged. Reverting the corpus invalidates
  the embedding checkpoint fingerprint and requires re-embedding/re-projecting.

## D16. arXiv identity spine + provenance-preserving OpenAlex sidecar — ACTIVE

- **Decision.** Keep every selected paper keyed and dated by arXiv, then run `s15` as a
  resumable exact-id left join. OpenAlex supplies structured author/institution identity,
  affiliation evidence, venue/identifier gaps, topics, and secondary citation/reference
  fields. Provider conflicts remain in `openalex_*` columns; arXiv title, abstract, v1 date,
  categories, `paper_id`, and `node_id` are never overwritten.
- **Alternatives.** (a) Make OpenAlex the corpus spine — loses recent arXiv coverage and lets
  provider duplicates define nodes. (b) Fetch one API record per paper — needlessly slow.
  (c) Merge citation counts across duplicate OpenAlex works — double-counts evidence.
- **Why.** Authors submit to arXiv, while OpenAlex is a downstream probabilistic index. Exact
  DOI/landing-page filters can batch 100 values, and 12 bounded workers plus checkpointed
  routes completed the 271k join in about five minutes for $0.4371, with 90.2% exact-match
  coverage. A live audit found recent duplicate/split records, so OpenAlex values must carry
  provenance and cannot silently replace source metadata or Semantic Scholar citation truth.
- **Revert.** Disable `openalex_enrichment.enabled`; s02's arXiv corpus and existing semantic
  artifacts remain valid. Removing the sidecar only removes organization/provider metadata,
  not papers, embeddings, or coordinates.

## D17. Semantic Scholar bulk graph for canonical citations; OpenAlex as a sidecar — SUPERSEDED by D18

- **Decision.** Use the S2AG bulk `citations` dataset as the source of canonical citation
  counts and directed relationships for the arXiv spine. Resolve arXiv→S2 identity with cached,
  paced `paper/batch` requests, map hashes through the bulk `paper-ids` crosswalk, then stream
  and delete each citation shard locally. Keep only corpus-internal edges in the static browser
  graph, but count every S2 edge that targets a matched corpus paper.
- **Alternatives.** (a) Crawl per-paper S2 citation/reference endpoints — the granted key is
  cumulative 1 RPS, so this is too slow and fragile. (b) Use OpenAlex as citation truth — its
  records can be split/lag and its own documentation permits incomplete reference lists. (c)
  union providers — duplicates become un-auditable double counts.
- **Why.** This supplies both in- and out-edges from one snapshot, preserves a verifiable
  release date/license, and gives recent arXiv papers the best available identity path. It
  separates global counts from the deliberately finite rendered graph.
- **Revert.** Replacing S2 requires a source with downloadable, directed, full-graph data and
  a stable crosswalk from arXiv ids. The migration cost is rebuilding counts, edges, related
  works, hierarchy, labels, and the static bundle; preserve `s2_*` provenance columns so old
  artifacts remain auditable.

## D18. OpenAlex immediate citation materialization; Semantic Scholar retained for reconciliation — ACTIVE

- **Decision.** Materialize OpenAlex citation totals and exact-match corpus-internal outgoing
  references in `s16_apply_openalex_citations` immediately after the completed `s15` crosswalk.
  Keep every `openalex_*` and `s2_*` provider column and both provider metadata sidecars. A full
  S2AG scan remains an explicit later operation; when complete it overrides canonical values for
  S2-matched rows without adding provider counts, while OpenAlex remains the fallback.
- **Alternatives.** (a) Wait for S2's full bulk release scan before showing citations — the
  introductory key's 1 RPS identity crosswalk makes that unusably slow for the interactive build.
  (b) Delete S2 support — loses a useful independent graph audit. (c) Sum or union provider
  counts — double-counts and makes provenance irrecoverable.
- **Why.** The existing exact OpenAlex join covers 244,730/271,366 arXiv records (90.2%) and
  already contains both `cited_by_count` and `referenced_works`; local materialization therefore
  makes the app useful in minutes, not after a long S2 run. Provider disagreement is presented as
  provenance rather than hidden arithmetic.
- **Revert.** Restore D17's ordering in `run_all.py` and set S2 enabled by default. This again
  makes a full rebuild wait on the S2 release scan; keeping the two sidecars means no identity or
  citation data migration is required.

## D19. Reveal level gated by a global importance floor, over an age-discounted signal — ACTIVE

- **Decision.** `assign_reveal_levels` admits a paper at level L only if it clears *both* the
  spatial separation radius and a global importance floor (`top_fraction`, 0.2% at level 0,
  quadrupling per level until it saturates). The ordering signal is
  `cited_by_count / age**0.5` (`tiling.importance = age_adjusted_citations`), not raw citations.
- **Alternatives.** (a) Pure spatial thinning (the prior behaviour) — admits whichever paper
  tops each empty region, so with 42.7% of the merged corpus at zero citations and a median of
  1, the home view seeded itself with 5-citation papers. (b) Raise `base_divisor` instead —
  makes the map sparser everywhere without making it more *influential*; the local maximum of a
  sparse region is still admitted first. (c) Raw citations as importance — measured: 89% of the
  top 2,000 predate 2022, turning the all-years home view into a museum. (d) Pure
  citations-per-year — over-corrects, 42% of the top 2,000 is 2025 alone.
- **Why.** Separation and influence are independent properties, and only the first was being
  enforced. Gating on a global quantile lets a thinly-populated region stay *empty* at coarse
  zoom rather than promoting its best-of-a-weak-field. Measured on the 912k corpus: level 0 now
  requires >=144 citations (median 805) and leads with BERT, XGBoost, ViT, BatchNorm, while
  keeping every year represented (136 papers from 2015 ... 223 from 2025).
- **Revert.** Set `tiling.top_fraction = 1.0` to disable the gate (covered by
  `test_importance_gate_disabled_restores_pure_spatial_thinning`) and
  `tiling.importance = cited_by_count` for the raw signal. Both are config-only; no data
  migration, though `s12` must be re-run to regenerate `reveal_levels.npy`.

## D20. Citation direction lives on the node's colour, not on an overlay ring — ACTIVE

- **Decision.** With a paper selected, the connected papers are tinted by their relation to it —
  teal = a reference that influenced the selection, amber = a paper influenced by it, white =
  the selection — using the shared palette in `web/src/map/citationColors.ts`, which the edge
  layer imports for the matching link hue. Encoding rule: hue = direction, alpha = strength,
  geometry constant (size already means citation count).
- **Alternatives.** (a) The previous overlay: a `ScatterplotLayer` drew a disc filled with the
  *background* colour `[12,14,20,220]` over each connected paper, ringed 1.4px in the direction
  colour. It occluded the very node it highlighted — it read as a black dot inside a blue one —
  and the ring was too thin to carry the signal; it also stole hover from the point beneath,
  which is why it had to re-route `onHover`. Deleted. (b) Encode direction with size or arrow
  count — size is already citation count, and doubling it up made a well-connected paper an
  unreadable thicket. (c) Leave the topic hue on connected nodes — with a selection active,
  channel 2 culls everything except the network, so the whole visible set shares one topic hue
  and the colour conveys nothing.
- **Why.** Once a paper is selected the visible points *are* its citation network, so the node
  itself is the highest-bandwidth place to say which direction it sits in, and it costs no extra
  geometry. Legend wording moved from "outgoing/incoming" to influence language, which is the
  question a reader actually has.
- **Revert.** Restore the `citation-selected-endpoints` layer in `useEdgeLayer` and drop the
  `refs`/`citers` branch of `getFillColor` in `usePointsLayer`. Both are self-contained; no data
  or pipeline change is involved.

## D21. Gzip the static artifact bundle in the Vite server — ACTIVE

- **Decision.** A `compressArtifacts` plugin in `web/vite.config.ts` gzips `/data/*` on the fly
  (level 6) and caches bodies in memory keyed by path+mtime+size. Google Fonts moved from a
  render-blocking `<link rel="stylesheet">` to `rel="preload"` + `onload` promotion.
- **Alternatives.** (a) No compression (prior state) — invisible on localhost, where the whole
  eager bundle transfers in ~0.1 s, but it is 49 MB and dominates over an SSH tunnel or LAN,
  which is exactly how this is viewed. (b) Brotli — measured only **4% smaller than gzip**
  (19.1 MB vs 19.8 MB) at quality 5, not worth the extra negotiation path. (c) Pre-compress
  `.gz` files in the pipeline — adds an artifact-lifecycle step that must re-run on every s11.
- **Why.** Measured 49.0 MB -> 20.9 MB served, a 57% cut in bytes on the startup path, for one
  dependency-free plugin. The mtime-keyed cache means papers-index.arrow (31 MB) is gzipped
  once per emit rather than per reload, so it does not compete with the pipeline for CPU.
- **Revert.** Drop the plugin from the `plugins` array; the dev server falls back to raw bytes.

## D22. Citation edges capped per paper before they reach the browser — ACTIVE

- **Decision.** `emit.max_edges_per_paper` (default 4) keeps each paper's strongest N links in
  EACH direction — ranked by the citation count of the paper at the other end — before writing
  `edges.arrow`. On the 912k corpus this is 13,006,390 -> 4,174,891 edges (32.1%). 0 disables it.
- **Alternatives.** (a) Ship the full graph — measured: `edges.arrow` 99.3 MB (74.7 MB gzipped;
  it barely compresses because node ids are near-random int32), eager bundle 240 MB / 134 MB
  gzipped, time-to-first-map 7.9s -> **27.7s**, JS heap **1,020 MB**. Unusable, and far worse over
  a tunnel. (b) A GLOBAL "top N edges" cut — strips the long tail entirely, so sparsely-cited
  papers lose their whole network; the per-paper cap instead leaves the same **811,364** papers
  connected at every K tested and only thins hubs. (c) Lazy per-node adjacency shards — the
  architecturally correct answer, but `useRelevanceScores` needs 2-hop adjacency and ten files
  read `citesOut`/`citedBy`, so it is a real refactor rather than a config change.
- **Why.** The browser fetches `edges.arrow` eagerly and builds `citesOut`/`citedBy` Maps from
  it, so graph size is paid on every load. Capping per paper preserves the shape a reader
  actually looks at (a paper's most significant links) while making size proportional to the
  corpus rather than to the square of its connectivity. Filtering is unaffected: `CitationFilter`
  and `useFilterMask` read `points.citedByCount`, the stored global count, not the edge list.
- **Cost to be honest about.** `CitationExplorer` builds its in/out lists FROM the adjacency, so
  its "References N / Cited by N" now reflects the capped graph, not every in-corpus link.
- **Revert.** Set `emit.max_edges_per_paper = 0` and re-run s11 (~1 min); no upstream stage is
  invalidated.

## D23. Only point tiles L0-L4 are on the critical path; titles and edges stream in after — ACTIVE

- **Decision.** Startup fetches `points-L0..L4` (2.66 MB), `labels/orgs/topics.json`, and nothing
  else. `papers-index.arrow` (titles) and `edges.arrow` (citation graph) are fetched AFTER first
  paint and filled into pre-existing structures in place; deeper point tiles load on demand as
  the user zooms (`ensurePointTiles`). A measured progress bar reports byte progress from the
  sizes s11 records in the manifest.
- **Alternatives.** (a) Fetch everything eagerly (prior behaviour) — 173 MB, and the user's link
  measured **~1 MB/s** over an SSH tunnel (server serves at 2.7 GB/s warm; `ss` showed 2.9-3.8 MB
  stuck in Send-Q, the signature of a slow consumer). That is minutes of blank screen. (b) Cap
  the corpus instead — throws away data to work around a transport problem. (c) Make consumers
  async — `ds.papers[...]` and `ds.citesOut` are read synchronously in ten files; filling in
  place keeps every call site unchanged.
- **Why.** Wall-clock load was byte-bound, so the fix had to be bytes. Only three fields are ever
  read off `ds.papers` — `citedByCount`, `title`, `publicationDate` — and counts/years already
  ship in points, so titles were the only unique payload in a 98.8 MB artifact. `edges.arrow`
  gzips at just 1.33x (near-random int32 ids), making it the largest thing on the wire once
  titles moved. Neither is needed to paint a map.
  Measured at a throttled 1 MB/s: eager **173 -> 12 MB**, map on screen **~110s -> 10.5s**.
  Point ids stay dense because tiles scatter into arrays preallocated to the corpus size, with
  unfetched points parked at `UNLOADED_LEVEL` so the existing reveal-level cull hides them.
- **Revert.** Restore the single `EAGER` list containing `points.arrow`, `papers-index.arrow` and
  `edges.arrow`, and drop `ensurePointTiles`. No pipeline change — s11 has emitted these tiles
  all along; this decision is only about which of them the frontend chooses to fetch.

## D24. Per-point attributes are precomputed typed arrays, and selection work is bounded — ACTIVE

- **Decision.** `usePointsLayer` hands deck.gl BINARY attributes (`getPosition`, `getFillColor`,
  `getRadius`, `getFilterValue`) built in one pass over typed arrays, instead of per-point
  accessor closures with `updateTriggers`. `useRelevanceScores` returns dense `Float32Array` /
  `Uint8Array` rather than `Map`s, caps the scored candidate set at 3,000 (ranked by citations),
  and `useRelevantLabels` caches its selection-independent band geometry and stride-samples at
  most 2,500 voters.
- **Alternatives.** (a) Keep accessors — deck.gl re-invoked each of three closures 912,429 times
  per selection; `_normalizeValue`/`updateBuffer`/`toDoublePrecisionArray` were the top JS costs
  in a CPU profile. (b) Keep `Map`-based relevance — a `Map.get()` per point per accessor, on
  912k points. (c) Leave the label vote loop uncapped — it cost
  `visible x bands x labels-per-band`, about **359 million** operations for a hub selection
  (PPO has 16,322 in-corpus citers), which blocked the main thread long enough that the citation
  panel looked empty. That is the browser-side twin of the pipeline's `fused.hub_degree_limit`.
- **Why.** Measured by CPU profile across a selection: `useRelevantLabels` fell from dominating
  to 0.4%, deck.gl's attribute rebuild vanished from the profile (`bufferSubData` 0.1%), and
  total JS dropped to ~6% — the remainder is native rendering. Sampling is safe for labels
  because which regions light up is a coarse visual cue; the vote threshold is scaled by the
  stride so filtered views keep the same labels.
- **Gotcha worth keeping.** A binary colour attribute must NOT set `normalized: false`: deck.gl
  normalises unsigned bytes to 0-1 in the shader, and forcing it off rendered every point
  saturated white (caught by a pixel census: 0 coloured pixels).
- **Revert.** Restore the accessor functions with their `updateTriggers`, and return `Map`s from
  `useRelevanceScores`. Self-contained in the three files; no data or pipeline change.

## D25. Label-region membership comes from the cell tree, not from centroid distance — ACTIVE

- **Decision.** Clicking a map label (or picking one from search) adds a `labelIds` facet that
  selects exactly the papers in that region. Membership is a lookup: s11 emits
  `points.region_leaf` (the deepest hierarchy cell containing each paper) and `regions.arrow`
  (the cell parent chain, 285,316 rows / 2.72 MB), and the UI walks a paper's leaf up to see
  whether it passes through the clicked cell. The facet appears as a "Region" chip in the
  active-filter bar (D-note: ActiveFilters).
- **Alternatives.** (a) Nearest label centroid within a per-band radius — the rule
  `useRelevantLabels` uses to decide which labels to DRAW. Tried first because it needs no new
  artifact, but it under-selected 30x: "cs.CV: Gaussian Splatting", a region the map itself
  labels 31,292 papers, selected **1,032**. A centroid ball is not the region's shape. (b) Ship
  every cell's `node_idx` — exact, but 9,116,528 node ids ≈ **35 MB**, unacceptable on a 1 MB/s
  link. (c) Ship a per-point region id for every level — 11 x 912k int32.
- **Why.** Labels ARE cells: all 15,956 emitted labels map to a `tiles.json` cell with an
  identical count and centroid, so the pipeline already knows the answer and guessing was never
  necessary. The tree makes it cheap — leaf + parent chain is ~5 MB total and membership is a
  handful of hops (observed depth 8-11). Verified end to end: the filter now reports exactly
  **31,292**, matching the label.
- **Gotcha this exposed.** Anything computed per-point across the whole corpus is wrong until
  every point tile has arrived (D23), because unloaded points carry zeroed data — `region_leaf`
  reads -1. The first exact implementation still reported 2,363 for that reason. `useFilterMask`
  now depends on a tile epoch so masks converge as tiles land, rather than freezing at whatever
  was loaded when the filter was applied.
- **Revert.** Drop the `labelIds` facet and the `onClick` toggle in `MapView`/`SearchBox`;
  `region_leaf` and `regions.arrow` are additive and harmless if unused.

## D26. The filtered set describes itself with c-TF-IDF over titles — ACTIVE

- **Decision.** Whenever a filter is active, a one-line "mostly: …" label names the terms that
  are frequent in the selection and rare outside it, computed client-side over paper TITLES.
  Background document frequency comes from a ~60k stride sample of the corpus; the selection
  itself is stride-sampled to 2,000 papers.
- **Alternatives.** (a) Reuse the map's region labels — they name whichever regions the papers
  land in, not the papers. An author with seven papers gets whatever coarse areas contain them,
  which is exactly the complaint. (b) Compute over abstracts — not shipped to the browser, and
  adding them would be far larger than the 98.8 MB title index already being deferred.
  (c) Precompute per-author/per-region labels in the pipeline — cannot cover arbitrary filter
  combinations (author AND category AND date).
- **Why.** Titles are information-dense enough for this, and c-TF-IDF is the same technique s07
  already uses per region, so the vocabulary matches the map. Both samples are strides rather
  than head slices because node ids correlate with publication date, so a head slice would bias
  the label toward one era. Verified: Eliot Xing's 7 papers -> "multimodal · reinforcement";
  the 31,292-paper Gaussian Splatting region -> "video · image · human · generation".
- **Bias caught in verification.** The label was first computed from the LIST ROWS, which are
  the top 500 by the active sort — so it described the most-cited papers rather than the
  selection. It now samples the full match set.
- **Note this exposes.** For broad regions the pipeline's own label can be misleading: a level-0
  cell named "cs.CV: Gaussian Splatting" actually contains generic vision work, and the
  self-computed label says so. Worth revisiting how s07 names large regions.
- **Revert.** Delete `useSetLabel` and its one call site in `PaperListPanel`; nothing else
  depends on it.

## D27. Opacity carries relevance for the selected paper's network — ACTIVE

- **Decision.** With a paper selected, connected papers are drawn with alpha ramped from the
  Connected-Papers relevance score (70 -> 255), matching `useEdgeLayer`'s existing edge ramp so a
  node and the edge touching it read as the same strength. The three channels stay independent:
  **hue = direction** (D20), **alpha = relevance**, **size = citation count**.
- **Alternatives.** (a) The previous flat alpha 235 for every connected paper — a paper sharing
  dozens of references with the selection looked exactly as important as one with a single
  incidental link, across a network of 69,286 papers for "Attention Is All You Need". (b) Use
  size for relevance — already taken by citation count, and doubling up on one channel is what
  made the old edge rendering unreadable (D20). (c) Cull the weakly related instead of dimming —
  that is what the relevance slider already offers; dimming keeps the shape of the network
  visible while ranking it.
- **Why.** The score was already computed per node and used only to CULL via the slider, so the
  information existed and was being thrown away.
- **Honest limit.** `useRelevanceScores` scores only the top 3,000 candidates (the bound added in
  D24 to stop hub selections freezing the tab), so on a large network the remainder score 0:
  measured p10/p50/p90 = 0/0/0 over 69,286 connected papers, max 1. In practice this renders as
  "the 3,000 most-related are bright, the rest sit at the alpha floor" rather than a smooth
  gradient. That is a truthful summary — those papers ARE weakly related — but it is coarser
  than the ramp suggests, and raising the cap would reintroduce the freeze.
- **Revert.** Restore the constant `ca = 235` for DIR_REFERENCE/DIR_CITER in `usePointsLayer`'s
  colour pass.

## D28. The crop includes every caption LINE, not just the first — ACTIVE

- **Decision.** `locateCrop`'s figure path now sets the crop's lower bound from
  `captionBlockBottom(items, cap)` — the baseline of the caption's LAST line — instead of
  `cap.y`, the baseline of its first.
- **Why.** pdf.js reports a caption's position as the baseline of its opening line, and user
  space is y-up, so every wrapped line sits BELOW `cap.y`. Cropping to `cap.y` therefore clipped
  any caption that did not fit on one line, which is why a one-line figure caption rendered
  correctly while a three-line table caption on the same page was cut off mid-sentence.
- **How the block is bounded.** Walk down from the caption baseline taking lines that overlap
  its column and sit within `CAPTION_LINE_GAP` (20pt) of the previous one — continuation lines
  are far closer together than the gap to the next block — stopping after `CAPTION_MAX_LINES`
  (8) so a caption-shaped paragraph cannot swallow the body text beneath it. Runs sharing a
  baseline are skipped, since pdf.js splits one line into several items on style changes (the
  same fact that made `captionLineWidth` necessary).
- **Alternatives.** (a) A fixed extra padding below `cap.y` — either too small for a long caption
  or large enough to drag in unrelated text, and it cannot adapt to font size. (b) Use the
  caption's reported `height` — that is the height of one line, not the block.
- **Revert.** Restore `y0: cap.y` in `locateCrop`.

## D29. "No reference data" is distinguished from "no references in this map" — ACTIVE

- **Decision.** `build_reference_availability.py` computes a per-paper `references_available`
  flag from the S2AG reference table; s11 ships it in `papers-index.arrow`, and
  `CitationExplorer` says "No reference data available for this paper" instead of the generic
  "No references in this corpus" when it is false.
- **Why.** Those two states render identically as an empty list but mean opposite things, and one
  of them is a claim about the paper that is simply false. S2 supplies no reference list at all
  for **66,936 of 912,429 papers (7.3%)**, and the gap is systematic in recent work — 2019-2022
  ~99% covered, 2024 92.3%, 2025 92.4%, **2026 only 70.4%** — because S2's reference extraction
  lags publication. The case that surfaced it: "Gemini 2.5" (arXiv 2507.06261) has 2,935
  in-corpus citers and zero references, so the panel implied a landmark report cites nothing.
  This mirrors `citation_count_available`, which already separates "unavailable" from "zero".
- **Alternatives.** (a) Infer it in the browser from `citedByCount > 0 && refs.length === 0` —
  wrong for a genuinely reference-free item and unable to distinguish an unmatched paper.
  (b) Backfill from OpenAlex `referenced_works` — worth doing, but that is new data rather than
  honesty about existing data; the flag is a prerequisite either way.
- **Gotcha.** A few arXiv ids map to more than one S2 corpusid, so the crosswalk join fans out —
  the first run produced 912,479 rows for a 912,429-paper corpus and s11's schema length check
  caught it. The flag is now aggregated per `node_id` (available if ANY corpusid has refs).
- **Revert.** Delete the flag from `PAPERS_INDEX_SCHEMA` and the `!referencesAvailable` branch in
  `CitationExplorer`; the emitter tolerates a missing parquet by assuming availability.

## D30. The author filter uses an inverted index, not per-paper author lists — ACTIVE

- **Decision.** `author_ids` left `papers-index.arrow`. Per-paper lists moved into the
  `papers-detail-N.arrow` shards (which the details panel already fetches on selection), and s11
  additionally emits `author-papers-N.arrow` — `author_id -> node_ids`, 1,405,248 authors across
  176 shards of ~0.6 MB. `useFilterMask` resolves a selected author by fetching that author's
  shard instead of scanning every paper.
- **Alternatives.** (a) Keep the per-paper lists resident — 18.2 MB of the eager bundle, and each
  filter change walked all 912,429 rows testing set membership. (b) Move them to detail shards
  only — fixes the bytes but breaks the filter, which needs every paper's authors at once.
  (c) One monolithic inverted index — same total (25.6 MB) but paid entirely to answer a
  one-author question.
- **Why.** Inverting turns "which papers has this author written" from a corpus scan into a
  lookup, and simultaneously removes the reason those lists had to ship eagerly. Measured:
  `papers-index.arrow` **98.8 -> 80.6 MB raw, 42.8 -> 28.1 MB gzipped**, so titles arrive ~15s
  sooner on the user's ~1 MB/s link; an author filter now costs one ~0.6 MB fetch, cached per
  shard so a second author in the same block is free.
- **Shard size.** 8,000 authors/shard. The first attempt used 50,000, giving 5.56 MB shards —
  correct but absurd for a single-author lookup.
- **Consequence to keep in mind.** `DetailsPanel` reads author ids from `detail`, so they are
  empty for the instant before it resolves — the same window in which `authorNames` is empty, so
  the two stay in step.
- **Revert.** Restore `author_ids` to `PAPERS_INDEX_SCHEMA` and the scan in `useFilterMask`; the
  extra artifacts are additive and harmless if unused.

## D31. Citation counts are floored at the citers we can enumerate — ACTIVE

- **Decision.** s11 raises every paper's `cited_by_count` to at least its in-degree in the
  corpus citation graph, and marks a count as available whenever we can enumerate citers.
- **Why.** A global citation count cannot be lower than the citing papers we can name, yet
  **337,426 papers (37%)** reported exactly that, understating by **8,272,741 citations**. The
  case that surfaced it: "Stabilizing Reinforcement Learning in Differentiable Multiphysics
  Simulation" showed 1 citation while its Cited-by tab listed 19. Cause: the 2015-2024 half of
  the merge carries only OpenAlex counts, and OpenAlex loses citation coverage for arXiv-only
  preprints after the MAG shutdown — 99.9% of affected papers have no S2 count, and the split is
  stark by year (2021-2024: 55-60% affected; 2025-2026: 0.1%).
- **Alternatives.** (a) Leave provider counts untouched — self-contradictory in the UI, which
  displays both numbers on the same panel. (b) Sum providers — double counts. (c) Backfill real
  S2 counts for the historical half — the CORRECT repair, and still open (see the follow-up
  task); this floor is what can be justified from data already on disk.
- **Honest limit.** This is a floor, not the truth. The real count includes citers outside the
  corpus, so these papers are still understated — just no longer provably wrong. Node radius and
  the citation filter both read this column, so they improve with it.
- **Revert.** Drop `_citation_floor` from `_build_points` and `_build_papers_index`.

## D32. The author index ships as slim name-only chunks; ids ride with the papers — ACTIVE

- **Decision.** `authors.arrow` is no longer emitted. s11 writes `authors-N.arrow`
  (`author_id`, `name`, `count`, `verified`) in 120,000-row chunks, and `openalex_id` moves into
  the `author-papers-N.arrow` shards. The frontend fetches the chunks sequentially after first
  paint, each one replacing the cached array so search improves as they land; `AuthorPanel`
  reads the profile id from the shard it already fetched to apply the filter.
- **Why.** The old file was **58.6 MB / 21.2 MB gzipped**, fetched whole after first paint purely
  so search could match names — over a ~1 MB/s link that is ~21 s before the first author is
  findable. `openalex_id` was **40.3%** of those bytes and only one link in one panel reads it.
  Measured after: **12 chunks totalling 13.5 MB gzipped** (−36%), first chunk **1.18 MB** — names
  searchable in roughly a second instead of twenty.
- **Alternatives.** (a) A prefix trie built at build time — smaller still, but it answers only
  prefix queries and the box matches substrings ("xing" finds "Eliot Xing"). (b) Server-side
  search — there is no server; the app is static files. (c) Keep one file, drop only
  `openalex_id` — 12.8 MB gzipped, but still all-or-nothing, so search stays dead until it lands.
- **Honest limit.** `verified` (one byte) had to stay in the resident index because the details
  panel flags unconfirmed identities for authors whose shards are not loaded. And a name typed
  before the last chunk arrives can miss a real author; the count in the box says how far along
  the index is.
- **Trap this exposed.** Chunks first accumulated by pushing into one array. Consumers memoise
  derived maps with `[authors]`, and an array mutated in place keeps its identity forever, so
  every such memo froze at the empty map built on the first render. The failure was silent and
  partial: the filter applied and the map showed the right 7 papers, while the filter bar and the
  author panel rendered *nothing*, because both look names up in that frozen map. Each chunk now
  hands back a new array (~8.4M reference copies over the whole load — a few tens of ms).
- **Revert.** Restore the single `write_arrow(read_arrow(ARTIFACTS_DIR / S.AUTHORS), ...)` in s11
  and drop `n_author_chunks` from the manifest; `loadAuthors` already falls back to
  `authors.arrow` when the manifest declares no chunks.

## D33. Playwright budgets are sized for software GL, and the suite runs serially — ACTIVE

- **Decision.** `timeout: 180_000`, `expect.timeout: 60_000`, `fullyParallel: false`,
  `workers: 1`.
- **Why.** Headless Chromium has no GPU here, so deck.gl rasterises ~900k points on the CPU
  through SwiftShader. A CPU profile of the first 30 s of load put **94.7% of samples in
  "(program)"** — native/GL work, not app JS — with main-thread stalls of **24 s, 12 s and 14 s**
  back to back. Playwright polls actionability on that same thread, so a 30 s budget expired
  during load; every action then reads as "the app ignored my click". Raising the budget makes
  the same interactions succeed unchanged.
- **Consequence for #10.** "deck.gl click handlers never fire in the test env" is very likely
  this and not a deck.gl/mjolnir bug: a plain React `<button>` in the search dropdown showed the
  identical symptom, and both started working at 120 s budgets.
- **Alternatives.** (a) Keep 30 s and mark tests flaky — hides real regressions. (b) Force a GPU
  in CI — not available on this box. (c) Shrink the corpus for tests — then the suite stops
  guarding the thing that is actually shipped.
- **Honest limit.** The suite is now slow (minutes, not seconds). It is a regression guard run
  deliberately, not a watch-mode check.
- **Revert.** Restore the 30 s / 10 s budgets and `fullyParallel: true`.

## D34. Citation counts come from the S2 bulk snapshot, not from provider fields — ACTIVE

- **Decision.** `build_s2_citation_counts.py` derives every paper's true global citation count
  from `data/s2ag/cited_by.parquet` — one row per cited paper, and the **length of its citer list
  IS the count** — and s11 takes `max(provider, s2_global, in_corpus_floor)`. This supersedes the
  D31 floor as the primary source; the floor now only carries papers absent from the snapshot.
- **Why.** D31 fixed the self-contradiction (1 shown, 19 listed) but not the understatement.
  Measured on the merged 1,000,490-paper corpus: **744,512 papers raised, +59,079,066 citations**,
  taking the corpus total from **9,352,609 to 68,431,675** — the app was displaying **13.7%** of
  real citations. The user's example, "Stabilizing Reinforcement Learning in Differentiable
  Multiphysics Simulation", goes 1 → **40**; "Gemini 2.5" goes 3,844 → **6,428**. By year the gap
  tracks the MAG shutdown exactly: 2018-2024 raise 88-91% of papers, 2025 57%, 2026 12%.
- **Cost.** One streaming pass, **2.3 minutes** over 109,580,699 rows / 17.7 GB. Only Arrow list
  *lengths* are read (`value_lengths()`), so the citing ids are never materialised; peak memory is
  one int64 per corpus paper. 991,999 of 1,000,490 papers (99.2%) resolve to an S2 corpusid,
  775,323 of those have ≥1 citation.
- **Alternatives.** (a) The S2 batch API (`s16_enrich_s2_citations.fetch_authoritative_counts`) —
  correct but rate-limited across ~1M papers, and it re-fetches what is already on disk.
  (b) Keep the D31 floor alone — leaves 744k papers understated. (c) Sum providers — double counts.
- **Honest limit.** The count is as of the S2AG snapshot on disk (2026-08-16), so it ages; and it
  is S2's view, which disagrees with Google Scholar. Both are stated on the panel already.
- **Revert.** Delete `_s2_global_counts` from s11 (the `np.maximum` makes a missing file a no-op)
  and the D31 floor resumes alone.

## D35. Affiliations are NOT inferred from an author's other papers — REJECTED (measured)

- **Decision.** Do not carry an author's affiliation across their papers. Post-2021 org
  attribution stays low rather than becoming wrong. `eval_affiliation_carryover.py` keeps the
  experiment so nobody has to re-run the reasoning.
- **Why it was tempting.** Only **270,342 of 1,000,490 papers (27.0%)** carry `institution_ids`,
  and the collapse tracks the MAG shutdown exactly: 50-63% before 2021, 17-21% after, **6.0% in
  2026**. Meanwhile **558,353 of the 730,148 unaffiliated papers have at least one author we know
  an institution for** — the rule would take attribution from 27.0% to **82.8%** coverage.
- **Why it was rejected.** Held-out precision, scoring papers whose true affiliations we know
  with the paper itself excluded:

  | rule | precision | recall |
  |---|---|---|
  | nearest affiliated paper, ±1y, all authors | 34.2% | 55.7% |
  | first author only, ±1y | 50.1% | 27.4% |
  | agreement of ≥2 authors, ±2y | **61.6%** | 37.4% |
  | single-author papers only, ±1y | 45.5% | 40.9% |

  The best variant is wrong about **two times in five**. The existing affiliation pipeline runs
  at 98-100% precision, and a "CMU" filter that is a third wrong is worse than one that is
  honestly incomplete. Single-author papers — where per-paper truth is unambiguous, so
  multi-author dilution cannot be blamed — score only 45.5%, which says the rule itself is weak:
  authors move, hold several affiliations at once, and OpenAlex ids mix department- and
  university-level entities.
- **What is left.** Parsing affiliations out of the PDFs. GROBID is **Apache-2.0** (checked
  2026-08-17), so licensing is fine. OpenAlex offers nothing else to mine: `org_affiliations_json`
  and `institutions_json` are empty for **0.0%** — literally all — of the 730,148 unaffiliated
  papers, so there are not even raw affiliation strings to re-map.
- **No API has this, and it is structural.** Checked live against six 2023-2026 papers our
  corpus lists as unaffiliated (2026-08-17): **OpenAlex** returns neither `institutions` nor
  `raw_affiliation_strings`; **Semantic Scholar** `authors.affiliations` is empty for 0/6;
  **arXiv's own API** returns zero `<arxiv:affiliation>` elements; **DataCite**, which holds the
  `10.48550/arXiv.*` DOIs, has `affiliation` on 0 creators. The cause is upstream of all of them:
  **arXiv does not require an affiliation at submission**, so it never enters the metadata chain,
  and every aggregator either inherits publisher metadata (which a preprint has none of) or runs
  its own PDF extraction. The published-version route is also thin — only **1.7%** (9,172) of the
  532,003 post-2021 unaffiliated papers carry a non-arXiv DOI; 89% are recorded solely as
  "arXiv (Cornell University)". PDF/LaTeX extraction is not one option among several; it is the
  only remaining source. arXiv bulk PDFs are a
  **requester-pays** S3 bucket (~500 MB monthly tars, ~6.8 TB for 2021-2026), which makes a full
  post-2021 pass a real AWS bill and a multi-week GROBID run. Scoped by influence, using the true
  S2 counts (D34), it becomes tractable: **≥50 citations = 57,200 papers ≈ 86 GB**, **≥25 =
  102,690 ≈ 154 GB**. That is a cost/scope call for the project owner, not something to start
  unilaterally.
- **Revert.** Nothing to revert — nothing was applied. If the rule is ever wanted, it must ship
  behind its own provenance marker (like the existing `ROSTER` badge) and never be blended into
  evidence-backed attribution.

## D36. Org-scoped "top researchers" are precomputed by s10 — ACTIVE

- **Decision.** `Institution.top_authors` (20 per unit) is computed offline in s10 and shipped in
  `orgs.json`; the browser reads it instead of counting author ids across the unit's papers.
- **Why.** D30 moved per-paper `author_ids` out of the resident papers index into the on-demand
  detail shards. `topAuthorsInNodes` still read `ds.papers[n].authorIds`, which is now empty until
  a paper is selected — so every org's researcher list rendered **empty**, silently, with no
  error. The e2e suite caught it ("selecting a lab reveals org-scoped researchers") only once the
  D33 timeout fix let that test actually run to its assertion.
- **Alternatives.** (a) Fetch the detail shards for a unit's papers — a large org spans thousands
  of papers and therefore most shards, to display twelve names. (b) Put `author_ids` back in the
  resident index — that is the 18.2 MB D30 removed, over a 1 MB/s link. (c) Invert the
  author-papers index in the browser — it is sharded by author, so answering "who is in this org"
  means fetching all of it.
- **Honest limit.** The list is fixed at build time, so it does not respond to the date or
  citation filters — it names the unit's most prolific researchers overall. Older artifacts
  without the field keep the in-browser fallback, which stays empty; that is why the field is
  optional rather than required.
- **Revert.** Drop `_attach_top_authors` from s10; the frontend falls back automatically.

## D37. Placeholder artifacts are merged, never overwritten — ACTIVE

- **Decision.** `s02_build_arxiv_corpus` merges its empty affiliation placeholder into any
  existing `affiliations.parquet` (keeping every row that carries evidence) and no longer
  rewrites `institutions.json` when one is already present.
- **Why.** The 1991-2014 backfill ran s02 over the backfill snapshot, which overwrote
  `affiliations.parquet` with **88,061 rows and zero evidence**, destroying the OpenAlex
  authorship evidence for **25,279 papers**. s10 read that file, found no unit evidence, and
  emitted an `orgs.json` with **zero department/lab sub-units** — no BAIR, no CMU Robotics
  Institute, no FAIR, no MSR Asia — while logging nothing but a normal-looking success. The
  rebuild would have shipped it. Recovered because the evidence also lives in
  `corpus_active.org_affiliations_json`; the recovered artifact yields **30 sub-units**.
- **Alternatives.** (a) Re-fetch affiliations from OpenAlex — thousands of API calls to restore
  data already on disk. (b) Rebuild it from the corpus every time — that is the recovery path,
  but it hides the bug rather than fixing it. (c) Write the placeholder to a different filename —
  then two stages disagree about which file s10 should read.
- **Second casualty, found later.** The same s02 call also did `write_json({}, INSTITUTIONS_OUT)`,
  emptying `institutions.json` — the OpenAlex id → name registry. s10 falls back to the raw id as
  a display name, so the org directory silently listed **"I1294671590"** instead of "Centre
  National de la Recherche Scientifique" for all **9,926** non-curated institutions, and org
  search for "tsinghua" matched nothing. Nothing errored; the counts were all correct. Caught by
  the e2e test `org search finds a non-curated corpus institution`, and recovered by re-merging
  the per-paper `institutions_json` column out of `openalex_enrichment.parquet` (**10,465**
  institutions). s02 now writes that file only when it does not already exist.
- **Honest limit.** This protects the artifacts going forward; it does not add a check that the
  evidence is still there. The real guard is the e2e suite — the two tests that caught both
  casualties existed all along, and only started running once D33's budgets let them.
- **Revert.** Restore the unconditional `write_parquet` / `write_json` pair.

## D38. A reading list is imported as CSL-JSON and matched client-side — ACTIVE

- **Decision.** Users import their own library (Zotero, Mendeley, Paperpile, BibTeX) and it
  becomes a filter facet like org or author. The interchange format is **CSL-JSON inside a small
  envelope** that adds the one thing CSL has no field for — which list each item came from.
  `tools/zotero_export.py` produces it from a local Zotero database, Zotero's local HTTP API, or
  the hosted Web API with a read-only key. Matching happens **in the browser**, identifier-first:
  arXiv id, then DOI (an arXiv DOI reduces to an arXiv id), then normalised title.
- **Why this format.** CSL-JSON is what every reference manager already exports, so a user who
  clicks "Export Collection → CSL JSON" gets a file that imports without the script at all — it
  just arrives without list names. Inventing a bespoke schema would have meant nobody could
  produce it except our own tool. `custom` is CSL's sanctioned home for application data, so the
  file stays valid CSL for anything else that reads it.
- **Why client-side matching.** The alternative is a server, and there isn't one — the app is
  static files. It also keeps a personal reading history on the user's machine: the library never
  leaves the browser. The cost is one artifact, `import-index.arrow` (node_id → arXiv id for all
  1,000,490 papers, **17.9 MB**), fetched **only when someone actually imports** — verified by
  asserting zero requests for it before the file is chosen. Titles need no artifact at all; they
  are already resident from the D30 chunks.
- **Alternatives.** (a) Ship DOIs too — another ~25 MB for the **1.7%** of relevant papers that
  have a non-arXiv DOI. (b) Match on the server — no server. (c) Store the imported file rather
  than the resolved node ids — re-matching on every reload would re-fetch the 17.9 MB index.
- **Honest limits.** Title matching is the only step that can be wrong (two papers can share a
  title), so it runs last, and it needs the streamed titles — an import in the first seconds
  resolves on identifiers alone, which is why the panel re-matches once `papersReady` fires.
  Node ids are positions in the current build, so the persisted list records the corpus size it
  was matched against and is discarded when a rebuild changes it; the user re-imports. Measured
  on a real 22-paper library: **18 matched**; the four misses were GPT-1, GPT-2 and the Nature
  DQN paper (never on arXiv) plus one paper outside the old corpus's date range.
- **Revert.** Drop `_emit_import_index` from s11, the `readingLists` facet from the store and
  mask, and `ReadingListPanel` from the sidebar; `tools/zotero_export.py` is standalone.

## D39. Placeholder rows never claim their data is available — ACTIVE

- **Decision.** `placeholderPapers` sets `citationCountAvailable` from whether that paper's point
  tile has actually loaded, instead of hard-coding `true`; and `fillPointTile` updates the
  resident paper row as each tile lands, so a real count appears without waiting for the separate
  `papers-index.arrow` fetch. The filter bar likewise reports **two** numbers when they differ:
  how many papers match, and how many of those the map can draw yet.
- **Why.** Progressive loading (D23) means a paper can have a title before it has coordinates or
  a citation count. The placeholder was zero-filled but flagged available, so the details panel
  printed a confident **"0 citations · Semantic Scholar S2AG (2026-08-11)"** for a paper whose
  artifact says **40** — indistinguishable from the genuinely-wrong counts D31 and D34 existed to
  fix. The same silence hit an imported reading list: 17 papers matched, **6** were drawn, and
  nothing said the other 11 were still downloading — the user reported "I was only able to see
  like 5 papers".
- **Alternatives.** (a) Block the panel until `papers-index.arrow` lands — that artifact is
  deliberately deferred; blocking on it undoes D23. (b) Show a spinner per field — noisier than
  the em-dash the panel already uses for genuinely-unavailable data. (c) Load every tile eagerly —
  42 MB, which is exactly what D23 removed from the critical path.
- **Honest limit.** "N on the map so far" is only shown while it is true, so it cannot become
  background noise — but it does mean the count moves while tiles stream. That is the truth
  moving, not the UI flickering.
- **Revert.** Restore `citationCountAvailable: true` in `placeholderPapers`, drop the `papers`
  argument from `fillPointTile`, and drop `drawn` from `ActiveFilters`.

## D40. Point rows are also sharded by node_id, not just by reveal level — ACTIVE

- **Decision.** s11 emits `points-by-node-N.arrow` (489 shards of 2,048 rows, ~89 KB each)
  alongside the reveal-level tiles. When a filter or selection needs papers whose tiles have not
  loaded, the frontend fetches the shards holding exactly those node ids
  (`ensurePositionsFor`), falling back to the level tiles past 400 shards, where they are the
  better deal because they are ordered by importance.
- **Why.** Reveal-level tiles are ordered by **importance**, so an arbitrary selection is
  scattered across every level: a 19-paper reading list spanned levels 0-8, meaning **43 MB had
  to be downloaded to place 19 dots**. Measured after: **0.46 MB in 11 requests**, ~90x less.
- **The bug this exposed.** `ensurePointTiles` computed its work list once and then did
  `if (tilePending) return tilePending` — a request for a deeper level arriving while a fetch was
  in flight was **silently discarded**, and since the caller is a React effect whose deps do not
  change again, nothing ever asked twice. On a fast link the eager tiles finish before the user
  can interact, so it never fired; on a ~1 MB/s link they are still streaming when a filter is
  applied, so the "load every level" request that a filter triggers was lost. The user's reading
  list drew exactly the 7 papers living in levels 0-4 and stopped, permanently. It now tracks the
  deepest requested level and keeps pulling until it is reached, notifying per tile.
- **Also.** Background streaming (title chunks, author chunks, edges) is gated: while an
  interactive fetch is outstanding, no new background fetch starts. A browser Priority Hint alone
  was not enough — it reorders queued requests but cannot preempt a multi-megabyte download
  already holding one of the six HTTP/1.1 sockets.
- **Honest limit.** Wall-clock could not be verified here. Under a 1 MB/s emulation the app takes
  66 s to fetch a file that a blank page fetches in 7.5 s at the same throttle, because headless
  Chromium rasterises 1M points through SwiftShader and starves the main thread (D33). The byte
  counts above are environment-independent; the seconds are not, and were not quoted.
- **Cost.** +43 MB of artifacts on disk (the same rows as `points.arrow`, re-sliced) and 489 more
  files. Nothing extra is downloaded at startup.
- **Revert.** Drop `_emit_position_shards` from s11 and `ensurePositionsFor` from the frontend;
  `usePointsLayer` falls back to `ensurePointTiles(MAX)` on its own.

## D41. GPU attribute memos depend on the tile epoch, and the fit avoids overlay chrome — ACTIVE

- **Decision.** The three `useMemo`s in `usePointsLayer` that snapshot point arrays (`geometry`,
  `rgb`, `attributes`) take `tileTick` as a dependency. `fitMatching` accepts `insetLeft` /
  `insetTop` and frames the selection inside the visible rectangle. The camera fit now fires for
  reading-list filters, not only org/author ones.
- **Why.** `ds.points.{x,y,r,g,b,revealLevel,monthIndex}` are typed arrays that `fillPointTile`
  **mutates in place**, so `ds` keeps its identity and a memo keyed on it never rebuilt. Papers
  whose tile or shard arrived after first paint kept what `emptyPoints` zeroed: position
  **(0,0)**, colour **black**, `revealLevel` **32767**. They were therefore drawn as a single
  black dot at the world origin and GPU-culled everywhere else. An imported 19-paper reading list
  rendered **6** dots; after the fix, **17 of 17** (measured by connected-component analysis of
  the framebuffer, 11,904 lit pixels ÷ ~805 per dot, with two blobs being genuinely overlapping
  papers).
- **Third instance of one bug.** D32 (author chunks), D39 (placeholder citation counts) and this
  are all the same shape: a shared array filled progressively while memoised consumers hold a
  stale view. The rule is now explicit — **anything that fills `ds.points.*` in place must bump
  a signal that every derived memo depends on.**
- **Why the fit changed too.** Reading lists were excluded from the zoom-to-fit, so 19 papers
  loaded and rendered correctly while remaining invisible: single pixels scattered across a
  million-paper map at the home view. Range facets (citations, dates) stay excluded — they are
  dragged continuously and re-framing on each step would yank the view. Labels stay excluded
  because `focusLabel` already moves the camera.
- **flipY.** `OrthographicView` defaults to `flipY: true`, so world y grows *downward* on screen:
  the top inset subtracts from `target.y`. Adding moved content the wrong way, caught by
  measuring dot positions rather than assuming.
- **Revert.** Drop `tileTick` from the three dependency arrays (points reappear only when some
  other dep happens to change) and pass no insets to `fitMatching`.

## D42. Restriction-aware labels vote by hierarchy membership, not nearest centroid — ACTIVE

- **Decision.** `useRelevantLabels` resolves each visible paper's cell in every band by walking
  `points.regionLeaf` up the `regions.arrow` parent chain, and keeps a label when it covers
  `max(2, 5%)` of the **visible set**. The old rule — vote for the nearest label centroid within
  a per-band radius, keep when votes clear `2%` of the **region's** size — is gone.
- **Why.** Filtering to Graham Neubig's papers surfaced *"Sentiment Analysis and Opinion Mining:
  Sarcasm Detection"* and *"Spam and Phishing Detection"* while dropping *"Language Models"*. Two
  compounding causes, measured on his 328 papers:
  1. **Nearest centroid ≠ membership.** A small region whose centroid sits inside a dense
     neighbourhood collects votes from papers that belong elsewhere: **76 of his papers voted for
     a 159-paper sarcasm region** none of them are in.
  2. **The threshold punished big regions.** At 2% of region size, *Language Models* (65,048
     papers, **213 of his**, 65%) needed **1,301** votes and was rejected; the sarcasm region
     needed **3** and survived. Systematically, accurate labels lost and niche ones won.
- **Measured after.** Neubig's top labels by true membership: Language Models 65%, Machine
  Translation 36%, Multilingual NMT 23%, Translation Quality 18%, RAG 16%, Text Generation 13%,
  Hallucination Detection 11%, Dependency Parsing 10% — and the map draws Vision-Language Models,
  Multimodal LLMs, cs.AI/cs.LG Large Language Models, RAG and Code Generation. No sarcasm, no spam.
- **Small selections now work too.** The old threshold made big regions unreachable for a small
  set, so an author with ~15 papers cleared nothing anywhere and got **no labels at all**. A share
  of the visible set is scale-free: Tuan Anh Le's 16 papers now yield Reconfigurable Intelligent
  Surfaces (67%), Channel Estimation (47%), RIS (27%), Massive MIMO (20%).
- **Bonus: it is cheaper.** O(voters x depth) instead of O(voters x bands x labels-per-band), and
  the O(labels²) per-band radius pass is gone entirely. `MAX_VOTERS` could therefore rise from
  2,500 to 20,000.
- **Amended: threshold AND ranking.** A share test alone empties the map for BROAD filters — an
  org with 14,522 papers spreads them across the whole atlas, so almost no single region reaches
  5% and the view loses its labels exactly when it most needs orientation. Each band therefore
  also always keeps its **top 6 regions by share** (still never below 2 papers). Measured labels
  kept: Google 11 -> 68, CMU 11 -> 69, DeepMind 13 -> 66, OpenAI 18 -> 63; a 28-paper org is
  unaffected at 37. Thresholding answers "is this label meaningful?", ranking answers "what is
  this view mostly about?", and a filtered map needs both.
- **Amended again: a filtered map gets the finer bands too.** With ~6 candidates per band and
  only bands 0-1 offered at the home view, a 5,698-paper organisation drew **five labels for the
  whole map**. The band gate exists to stop a million-paper map drowning in text; under a filter
  that pressure is gone while the need for orientation is higher. So a restricted view offers
  EVERY band as a candidate (`visibleLabelLevels(..., restricted)`), coarse bands sorted first so
  they still claim the prime space, and `TOP_PER_BAND` rose 6 -> 14. The greedy collision placer
  already rejects overlaps, so extra candidates fill empty space and cannot clutter occupied
  space. Amazon went from 5 labels to ~16 spread across its cloud — Object Detection, Visual
  Search, Thompson Sampling, Automatic Speech Recognition, Federated Learning, Code Generation.
  A label whose box leaves the viewport is now dropped rather than drawn half off-screen.
- **Honest limit.** Membership needs `regions.arrow`, which streams in after first paint; until it
  lands the hook returns null (every label relevant), which is the pre-filter behaviour.
- **Revert.** Restore the centroid/radius implementation from git history. Note that doing so
  reintroduces both failure modes; the region-size threshold in particular is not salvageable.

## D43. Org attribution ingests COMET's arXiv affiliation extraction — ACTIVE

- **Decision.** `build_comet_affiliations.py` joins `cometadata/arxiv-author-affiliations-matched-ror-ids`
  (CC0, 2,799,088 papers, 2.4 GB) onto the corpus by arXiv id. ROR ids bridge to our OpenAlex
  institutions via the `ror` field present on **99.7%** of the registry. s10 merges the result on
  top of publisher-asserted authorship, never replacing it, and records `extracted_count` per org
  so the UI can distinguish the two confidences.
- **Result.** Corpus attribution **27.0% → 75.4%**. By year: 2021 47.7→90.0%, 2022 21.0→87.1%,
  2023 18.0→87.1%, 2024 16.8→86.9%, 2025 16.9→79.6%. **2026 is unchanged (6.0%)** — COMET's
  snapshot ends December 2025, and as of 2026-08-18 they have published no newer extraction
  (their recent releases are ROR-matcher training data, DataCite affiliations and funding
  extraction).
- **ROR links universities and NOT companies.** Measured across all 2.8M rows: Google's ROR
  (`00njsd438`) appears **zero** times and OpenAI's zero, while Carnegie Mellon's appears
  **20,126**. COMET *extracts* the strings correctly — "Google Research", "Meta AI", "FAIR at
  Meta" — its ROR linker just does not resolve them. Without a fix, the org tree gained 10,614
  papers for CMU and **nothing at all** for Google, which would have quietly skewed the entire
  industry-vs-academia picture the project exists to show.
- **So company matching is curated by name** (`pipeline/directory/org_names.py`), for companies
  and neolabs only. Universities stay ROR-only: they already resolve at high precision, and
  name-matching them adds risk ("Berkeley" is also Lawrence Berkeley National Laboratory) for no
  gain. Patterns follow `units.py` discipline — acronyms only as standalone uppercase tokens,
  explicit vetoes (`Amazon rainforest`), and `\bMeta\b(?!-)` so "Meta-Learning Lab" is not Meta.
- **Gains.** Google 4,633 → **14,522**; Microsoft Research 2,423 → **13,134**; Meta 1,594 →
  **7,107**; Amazon 1,282 → **5,698**; DeepMind 569 → **4,293**; NVIDIA 705 → **3,893**; OpenAI
  101 → **402**; AI2 251 → **1,512**. Feeding the same strings to `extract_unit_keys` finally
  populates the sub-units this project was built to show: FAIR 479 → **2,505**, MSR Redmond 88 →
  **1,997**, MSR Asia 369 → **1,972**, Meta AI 84 → **1,760**, Reality Labs 66 → **668**.
- **Precision evidence.** Random newly-attributed papers carry unambiguous strings ("Google Inc,
  Mountain View, USA", "AWS AI Labs", "NVIDIA Research", "Facebook Inc., Menlo Park"). FAIR's top
  researchers come out as Jason Weston, Lior Wolf, Yann LeCun, Dhruv Batra, Devi Parikh.
- **Honest limits.** This is model-extracted at **91% precision / 81% recall** (COMET's own
  measurement), not publisher-asserted at 98-100% — hence `extracted_count`, which runs 92-98%
  for the companies and 58-69% for the universities. **8,219 ROR ids (97,649 mentions) do not
  bridge**, because those institutions never appear in our OpenAlex registry; among them are real
  ones (HKUST Guangzhou, Université de Toulouse) and clear ROR mis-matches ("The Ark", an archive
  in Ireland, 5,631 mentions). Ingesting the ROR registry to close that tail should filter by
  type and mention count first.
- **Revert.** Delete `data/interim/comet_affiliations.parquet`; s10 logs a warning and falls back
  to publisher-asserted affiliations alone.

## D44. The date histogram groups bars adaptively, scales by log, and ignores unplaced papers — ACTIVE

- **Decision.** Months are grouped for DISPLAY into the smallest natural unit (month, quarter,
  half, year, …) that keeps ≤56 bars; bar height is `log1p(count)/log1p(peak)`; papers whose point
  tile has not arrived are excluded from the bins. The range control underneath stays
  month-granular — this changes what is drawn, not what is selectable.
- **Why, three separate faults the 1991-2026 corpus exposed at once.**
  1. **One bar per month.** 428 months in a ~260px sidebar is **0.61px per bar against a 1px
     gap** — the gaps were wider than the bars, so the histogram rendered as nothing.
  2. **sqrt scale.** arXiv CS output grows ~3 orders of magnitude across the range; sqrt puts the
     early 1990s at ~3% height, present in the DOM and invisible on screen.
  3. **Unplaced papers counted as month 0.** `emptyPoints` zeroes `monthIndex`, so at startup
     ~925,000 papers whose tiles had not loaded stacked into a single 1991 bar and flattened
     every real one. The bin label read "Jan 1991 – Dec 1991: 925,713 papers" against an artifact
     whose `month_index` never takes the value 0 at all.
- **The third is the recurring one.** D39 (citation counts), D41 (GPU attributes) and this are all
  a placeholder read as fact. The rule stands: anything derived from `ds.points.*` must either
  skip `UNLOADED_LEVEL` rows or depend on the tile epoch. Here it does both.
- **Honest limit.** The histogram therefore describes what has loaded, and fills in as tiles
  arrive — which is why D45 exists to say so out loud.
- **Revert.** Restore the per-month `bins.map` render and the `Math.sqrt` height.

## D45. Deferred loading is stated, not implied — ACTIVE

- **Decision.** A `LoadingStatus` pill (bottom-right, `role="status"`) names the streams still in
  flight and their combined percentage, and disappears when they finish. Fed by
  `deferredProgress()`, which reads the existing per-stream signals; no new plumbing.
- **Why.** Painting before the data is complete (D23) is right on a ~1 MB/s link, but it makes the
  interface quietly untruthful in three places at once: a paper with no title looks untitled, a
  search with no hits looks like it found nothing, and a histogram bar at zero looks like a year
  with no papers. All three are "not downloaded yet". Measured at 1 MB/s it reads
  "loading map detail · titles · authors · citations 13%" and climbs to 55% by t+60s.
- **Alternatives.** (a) Block until loaded — undoes D23. (b) Per-widget spinners — repeats the
  same message in five places and still cannot explain the histogram. (c) Nothing, as before —
  the state the user reported twice.
- **Placement note.** It first went bottom-left and was invisible: the filter sidebar sits above
  that corner, and the bottom-left already holds the list toggle and the set-topic label. Caught
  by looking at the screenshot rather than at the DOM.
- **Revert.** Remove `<LoadingStatus />` from `App.tsx`.

## D47. Some artifacts are build-machine only and must never be published — ACTIVE

- **Decision.** `schema.LOCAL_ONLY_FILES` names artifacts that s11 writes to `web/public/data`
  for local use but that must not reach a remote repo or CDN. It currently holds
  **`papers.arrow`**. Anything listed must also be absent from the manifest, or the frontend
  would try to fetch it.
- **Why keep it at all.** `papers.arrow` is the pre-D23 whole-paper table — one row per paper
  with every field together (title, date, doi, arxiv id, venue, citations, topic, author and
  institution lists). D23 split what the browser reads into `papers-index.arrow` (counts and
  availability flags), `papers-titles-N.arrow` (titles, streamed) and `papers-detail-N.arrow`
  (authors, venue, ids, on selection), and nothing has fetched the original since. It stays
  because it is the only place every field sits side by side, which is genuinely useful for
  offline inspection and for rebuilding the split files.
- **Why not publish it.** **276 MB** at 1,000,490 papers, which is over GitHub's **100 MB**
  per-file hard limit, and it is a quarter of the whole artifact bundle. No browser has ever
  requested it.
- **Alternatives.** (a) Delete it outright — loses the one convenient offline view and the
  ability to rebuild the split files without rerunning s11 over the corpus. (b) Move it under
  `data/artifacts/` — cleaner in principle, but it is produced alongside the split files by the
  same function and separating them invites the two drifting apart. (c) Publish it — impossible
  (file limit) and pointless (never fetched).
- **Consequence for the upload step.** Whatever eventually copies `web/public/data` to a host
  MUST filter `LOCAL_ONLY_FILES`. A naive `rsync`/`aws s3 sync` of the directory would push
  276 MB nobody reads, and on GitHub it would fail outright.
- **Revert.** Empty the set; publishing then includes everything the directory holds.

## D48. A hub selection opens the relevance filter instead of flooding the map — ACTIVE

- **Decision.** `selectNode` sets `relevanceThreshold` from `autoRelevanceThreshold`: 0 for an
  ordinary paper, and for a network above `NETWORK_SOFT_CAP` (1,500) enough to leave roughly that
  many of the most relevant papers visible. The slider still drags back to "all".
- **Why.** Selecting "Attention Is All You Need" revealed its **69,262 citers** as points, which
  covered the entire map in one colour — reported as "clicking a paper shows tons of edges in
  background". It is not edges: those are capped at 40 per direction and were behaving. It is the
  network's *points*, and the flood hid exactly the structure the selection was meant to expose.
  The slider was the intended remedy but started at "all", so the flood was the first thing seen.
- **Depends on the percentile fix.** The slider applied a RAW score cutoff while its label claimed
  "top N%". Since `score = raw / max` over small integer counts, a typical connected paper sits at
  `1/max` — often 0.02 — so the first few percent of travel culled nearly everything and the label
  was simply untrue. `map/importance.relevanceCutoff` reads the cutoff out of the sorted score
  list, which makes the percentile real and is what lets this decision express itself as
  "leave 1,500 papers visible".
- **Alternatives.** (a) Hard-cap the rendered network — the slider then cannot reach what it hides,
  and a user asking for "all" should get all. (b) Shrink dot size for large networks — makes it
  denser, not clearer. (c) Leave it and rely on the user finding the slider — that is what was
  already happening, and it read as a bug.
- **Honest limit.** 1,500 is a judgement, not a measurement: it sits above a typical network (which
  opens fully) and far below a hub's tens of thousands. Verified on a hub: the slider reads
  "top 2%" and the fan of edges and topic spread become legible.
- **Revert.** Return `selectNode` to `relevanceThreshold: 0`.

## D49. A paper's year is read from the resident index, and its absence is stated — ACTIVE

**Context.** An author's paper list rendered em dashes where years belong, which turned into
real years a minute or two later — and appeared to be "fixed" by clicking a paper, because the
detail shard carries the date. Measured on Aditi Raghunathan (58 papers) over a throttled
1 MB/s link: **47 of 58 rows showed an em-dash year**, decaying 47 → 41 → 37 → 0 across roughly
two minutes.

**Cause.** `papers-index.arrow` has always carried a `year` column for all 1,000,490 papers, and
`fillPapersIndex` never read it. Dates were therefore populated *only* by point tiles and
position shards — per-paper fetches that trickle in — even though a single 2.6 MB artifact
already on the wire knew every one of them. The same function's comment describes fixing exactly
this omission for `cited_by_count` (D39); `year` sat in the next column and was missed.

**Decision.**
1. Read `year` in `fillPapersIndex`, filling `publicationDate` when it is not already a fuller
   ISO date from a detail shard.
2. Add `PaperMeta.dateAvailable`, set by whichever source lands first, and render the pending
   state as a shimmer (`PaperYear`, mirroring `PaperTitle` / D45) instead of an em dash. An em
   dash now appears only when the index has landed and the paper genuinely has no date.

**Tradeoffs.** Costs one boolean per resident row (~1 MB of heap across 1M papers, no wire
bytes) and one more component in five render sites. The alternative — carrying `year` in
`AUTHOR_PAPERS_SCHEMA` so an author's dates arrive with their paper list — was rejected: it adds
wire bytes to fix data we already ship, and helps only the author path, not search results,
citation lists or related works.

**Revert condition.** If `papers-index.arrow` ever stops shipping `year` for all N papers, the
shimmer would never resolve for unfetched papers; `dateAvailable` would then need to fall back to
a completion signal for the index rather than a per-row flag.

**Verified.** Desktop 1440x900 and iPhone 13, 1 MB/s CDP throttle, `papers-index.arrow` held
back 45 s by a Playwright route to force the pending window: **0 em-dash years at every sample
on both viewports** (was 47), 47 shimmer placeholders during the hold, all 58 real years once
the index landed, console clean.

## D50. Directory-org membership is sharded; orgs.json ships the browse tree only — ACTIVE

**Context.** `orgs.json` is fetched before first paint (the org filter and colour-by-org both
need it) and cost **5.05 MB gzipped**. Measured, **94% of it was `node_ids`**: 1,489,472 ids
across 10,518 institutions — and 1,370,907 of those belong to the **10,475 directory entries**,
which are search-only. Nothing reads a directory org's membership until someone selects it, so
every visit paid for 1.37M ids to answer a question almost no visit asks.

**Decision.** Publish `orgs.json` slim. The **43 curated browse-tree entries** keep their ids
inline (118,565), because `buildOrgOfNode` maps every point to a root org for colour-by-org
before any selection exists. The rest move to `org-nodes-{N}.arrow`, fetched when an org is
selected.

- **Emitted in `s11_emit.py`, not `s10_indexes.py`** (the plan said s10). s11 is where artifacts
  are published to `web/public/data`, so `data/artifacts/orgs.json` keeps its ids inline and
  nothing downstream of the emit stage loses information. It also means the shards can be
  regenerated without recomputing the org build.
- **128 orgs per shard, not one file per org.** 10,475 tiny files would trade bytes for request
  count, which is what the object store actually bills; at this size a selection costs one
  ~47 KB fetch and the whole set is 82 files.
- Keys sorted, so shard assignment is stable across builds.

**Measured.** orgs.json **5.05 → 0.67 MB gzipped**; bytes before first paint **7.2 → 3.2 MB**.
Selecting Tsinghua University costs **one** shard request and yields its full 16,844 papers.

**Consequences.**
- `Institution.node_ids` is empty for directory entries in the published bundle. Anything
  needing it must go through `useOrgNodes`, never the field.
- The in-browser `topAuthorsInNodes` fallback is deleted. It had already stopped working when
  per-paper `author_ids` left the resident index (D30) and cannot work at all now; an org with
  no precomputed `top_authors` (D36) says so instead of rendering an empty list.
- A selection is briefly unresolved, which surfaced a second bug — see below.

**Revert condition.** If colour-by-org is ever extended to directory institutions, their ids
would be needed eagerly again and this would have to be reversed or the colouring reworked to
use a precomputed per-node org column instead.

## D51. A filter's match count is not reported until the selection resolves — ACTIVE

**Context.** With D50 (and D30 before it) a selection's membership arrives asynchronously. The
filter bar computed its count from the mask regardless, so selecting Tsinghua University showed

    0 of 1,000,490 papers · ORG Tsinghua University

for about a second before it became 16,844. Measured in the browser at 1 MB/s. A zero that
turns into a real number is the placeholder-read-as-fact bug this project keeps hitting
(D39, D41, D44, D49), and it is worse here than a blank: it reads as "this organization has no
papers in the map".

**Decision.** `useFilterMask` returns `pending` — true while any selected org's shard or any
selected author's paper list is still in flight — and `ActiveFilters` renders a shimmer in
place of the number until it clears. The total ("of 1,000,490 papers") stays visible because it
is always known.

**Tradeoffs.** One more boolean threaded through the mask, and the count now has three states
rather than two. The alternative, showing the last known count, would be worse: it would assert
a stale number as the current one.

**Verified.** Selecting Tsinghua at 1 MB/s: shimmer while the shard is in flight, then 16,844;
at no sample does the bar read 0. Curated orgs (ids inline) never enter the pending state —
Carnegie Mellon reads 15,464 immediately.

## D52. The app deploys to Pages; the artifacts do not — ACTIVE

**Context.** The bundle is **1,303 files / 0.79 GB**, and two members exceed GitHub's 100 MB
per-file hard limit (`papers.arrow` 263 MB, `edges.arrow` 110 MB). Pages also cannot set cache
headers and has a ~100 GB/month bandwidth allowance, which at the pre-slimming per-visit cost
was roughly 700 visits.

**Decision.** Pages serves only the app shell. The artifact origin is one environment variable,
`VITE_DATA_BASE`, defaulting to the relative `"data"` path that `vite dev`/`preview` already
serve. `vite.config.ts` sets `base: '/research-atlas/'` (overridable via `VITE_BASE`) because a
project page is served from a sub-path and every asset URL 404s without it — a failure that
appears only once deployed.

Left deliberately un-defensive: with `VITE_DATA_BASE` unset a deployed build requests a
relative `data/` path and 404s visibly, rather than falling back to something that makes the
site look fine while having no papers.

**Publishing** is `tools/publish_artifacts.sh`, dry-run by default. It **pre-gzips every file
and stamps `Content-Encoding: gzip`**, because measured against the real bucket R2 serves a
plain `.arrow` at full size even when the browser sends `Accept-Encoding: gzip` (2,346 bytes in,
2,346 on the wire; pre-compressed, 82). Without that the per-visit budget is wrong by ~2x. It
also reads its exclusion list from `schema.LOCAL_ONLY_FILES` (D47) so the list cannot drift from
the code that defines it, and writes under an immutable versioned prefix `v/<date>/` with
`max-age=31536000, immutable`, since Pages-hosted artifacts could never be cached that way.

**Tradeoffs.** Two origins instead of one, so CORS must stay correct on the bucket (it is set
and verified, and must be updated by hand for any new origin — the API token is object-scoped
and cannot call `PutBucketCors`). A versioned prefix means each publish writes a full copy;
at 0.40 GB gzipped against R2's 10 GB free tier that allows ~25 builds before pruning matters.

**Verified.** Built with `VITE_DATA_BASE` pointed at a separate origin (port 4322, CORS,
immutable cache headers) and served the app from `/research-atlas/` on another: map renders,
**15 of 18 requests go to the data origin**, zero failed requests, zero console errors, on
desktop 1440x900 and iPhone 13. Publish dry-run stages 1,303 files, 0.78 GB raw -> 0.40 GB gzipped
(ratio 0.509, 380 MiB to transfer), correctly excluding `papers.arrow`.

## D53. The citation graph arrives in two shapes: zoom tiers and per-node shards — ACTIVE

**Context.** `edges.arrow` was 110 MB raw / 87 MB gzipped, fetched **whole** after first paint
on every visit regardless of what the user did — the largest single item on the wire. It also
exceeds GitHub's 100 MB per-file hard limit, so it made the project unhostable on its own.

**Decision.** Two mechanisms, because there are two different questions.

- **Zoom tiers** (`edges-L{N}.arrow`, D-4a) answer *"what is drawable right now"*. An edge needs
  both endpoints on screen, so it belongs to the tier of its deeper endpoint and is loaded
  alongside the point tile of the same level.
- **Per-node shards** (`edges-by-node-{N}.arrow`, D-4b) answer *"everything connected to THIS
  paper"*, which ignores zoom entirely.

`completeNodes` records which nodes have their authoritative lists. Tiers are disjoint so
merging never duplicates; a node shard **replaces** rather than merges, because it is a
superset of anything the tiers contributed.

**The distinction is not an optimisation detail, it is a correctness requirement.** Everything a
panel says about a specific paper must come from its shard. From the home view a reader has 408
edges loaded; answering "how many of this paper's references are in the map?" from that would
have announced *"3 of 78"* for "Attention Is All You Need" — not merely incomplete but
confidently false. The reference note is therefore gated on **that paper's** shard, not on
`useEdgesReady` (which now means only "some tier has landed").

**Three consequences that had to be handled, not assumed:**

1. `autoRelevanceThreshold` (D48) ran at selection time, before the shard arrived, so a hub
   looked like an ordinary paper and the slider stayed at "show everything" — re-creating
   exactly the flood D48 exists to prevent. It is now recomputed when the shard lands
   (`syncAutoRelevance`), unless the user has already moved the slider (`relevanceTouched`).
2. Relevance scoring does a **second hop** (each candidate's own references, for bibliographic
   coupling). Candidates are scattered across the corpus, so fetching all of them would be
   hundreds of round trips. `SECOND_HOP_SHARDS = 12` spends the budget on the highest-cited
   candidates; the rest keep their direct-link score and are **reported** as unscored via the
   existing `scored`/`total` fields rather than silently ranked on partial lists.
3. `useEdgeLayer` and `useRelevantLabels` depend on an edges epoch, or they freeze at whatever
   fraction of the graph existed when the paper was clicked.

**Tradeoffs.** Storage roughly doubles for the graph (tiers 86 MB + node shards 127 MB raw,
against one 110 MB file) because each edge is stored in a tier and in both endpoints' adjacency
lists. That buys a ~29,000x reduction in what a home-view visit downloads for edges. Selecting
a paper costs one ~200-550 KB request, and a hub's full relevance ranking up to twelve more.

**`edges.arrow` is now `LOCAL_ONLY` (D47).** Nothing fetches it; it is still emitted as the
source the tiers and shards derive from, and as the obvious thing to inspect locally.

**Revert condition.** If a feature ever needs whole-graph statistics in the browser (global
PageRank, community detection), no amount of tiering helps and this would need a precomputed
per-node summary in the pipeline instead of the raw graph.

**Verified** at 1 MB/s on desktop 1440x900 and iPhone 13, identical on both:

| | measured |
|---|---|
| home view | 23 requests / 4.3 MB total; edge tiers 2 files / **10 KB**; `edges.arrow` fetched **0 times** |
| select "Attention Is All You Need" | References **30** · Cited by **69,262** · Both **69,292** |
| reference note | "**30 of 78** references are in this map — the other 48 cite work outside it" |
| node shards | 1 (555 KB) for the selection, **13 total** — the 12-shard second-hop cap, exactly |
| relevance slider | auto-opened to **top 2%** (1 − 1500/69,292), proving the post-shard recompute |
| console | clean |

## D54. Title search runs on a token index, not a scan of downloaded titles — ACTIVE

**Context.** Search scanned every resident title with `includes(q)`. That made results depend on
**download progress**, which is a correctness problem, not a performance one. Measured while
verifying D53: searching the exact string "Attention Is All You Need" early in a session
returned *"Element-wise Attention Is All You Need"* and *"Attention is All You Need Until You
Need Retention"* — but never the paper itself, because the title chunk holding it had not
arrived. The box looked like it was ranking badly; it was answering from a fraction of the
corpus and saying nothing about it.

**Decision.** s11 emits a token → papers index over all 1,000,490 titles, **split
alphabetically into 64 chunks** (`SEARCH_INDEX_CHUNKS`). A query fetches the chunk(s) holding
its tokens — ~115 KB, 224 KB worst case — rather than 7.1 MB for the whole index or 31 MB of
titles.

Alphabetical order is doing real work here, not just partitioning: **a prefix is a contiguous
run of tokens**, so "atten" lives in one or two known files and type-ahead works without
shipping the vocabulary. A hash-partitioned index could not do that.

- `SEARCH_TOKEN_CAP = 200` most-cited papers per token. 97.9% of tokens are under it; those over
  are stopword-like, and the box shows ten results ranked by citations anyway.
- `SEARCH_CHUNK_CAP = 4` chunks per query, spent on the longest (most selective) tokens.
- The last token is treated as a prefix, because the user is probably still typing it.

**Tradeoffs.** Infix matching is gone: "ttenti" no longer finds "Attention", because there is no
resident title to substring-scan. Prefix and whole-word matching cover ordinary type-ahead, and
the alternative — keeping 31 MB of titles resident to serve infix queries — is what this
replaced. An empty dropdown now says "searching titles…" while chunks are in flight, so
"no matches" is never claimed before it is known.

**Verified.** Exact-title query returns "Attention Is All You Need" first, followed by "Is
Space-Time Attention All You Need for Video Understanding?" — on both viewports. Prefix "atten"
returns the same paper first. Multi-token "residual learning image" returns "Deep Residual
Learning for Image Recognition". A nonsense query returns empty. **7 index chunks / 903 KB**
across a whole session of varied queries. Console clean.

## D55. Titles are sharded by node — overturning D-era reasoning that they could not be — ACTIVE

**Context.** Titles shipped as 17 sequential chunks of 60,000 rows, streamed in full after first
paint: **31.1 MB gzipped on every visit**, to display a few dozen strings. They also saturated
the sockets, so an interactive fetch a user was waiting on queued behind them.

The schema comment explicitly argued this was necessary: *"they cannot be sharded by node the
way detail is: search needs every title, and a citation panel's papers are scattered across the
corpus, so node-sharding would cost ~11 MB of fetches to render one panel."*

**Both premises were re-checked rather than trusted.** The first expired when D54 landed —
search reads no titles at all now. The second was an estimate; measured against real access
patterns it is wrong at the right shard size:

| rows/shard | shard size | citation panel (20) | references (30) | search results (10) | list (500 rows) |
|---|---|---|---|---|---|
| **2048** | 72 KB | 19 req / **1.2 MB** | 21 / **1.4 MB** | 10 / **0.6 MB** | 322 / 20.8 MB |
| 8192 | 244 KB | 17 / 4.2 MB | 10 / 2.4 MB | 10 / 2.4 MB | 123 / 30.1 MB |

**Decision.** `TITLE_CHUNK_ROWS = 2048`, the same key already used by position, detail and edge
shards — so a selection's title, position, detail and network all come from the same index.
Views fetch titles for the rows they render (`ensureTitles`, `TITLE_SHARD_CAP = 24`).

The one genuinely expensive pattern is the 500-row list panel, which is why it fetches a
**24-row window that follows the scroll position** rather than every match.

**Consequences.**
- `papersReady` no longer means "all titles are in" — that moment does not exist. It now means
  "the resident index is in". `PaperTitle` takes a `node` and asks whether **that paper's**
  shard has landed, so "(untitled)" still means the paper has no title rather than "not yet".
- The loading readout dropped its "titles" row: there is no honest "N of 489" for a stream that
  never runs to completion.
- Every view that renders a title must call `useTitles`, or it will shimmer forever.

**Also fixed here:** `regions.arrow` and `papers-index.arrow` were fetched with **no priority**,
so the interactive gate never applied and they could hold sockets while a user waited on a
search. Both are now `"low"`.

**Revert condition.** If a view ever needs titles for thousands of scattered papers at once
(a CSV export, a client-side re-ranking pass), the window approach does not cover it and those
would need a purpose-built artifact.

**Verified.** Titles fell from a 31.1 MB stream on every visit to **2–28 shards / 0.13–2.07 MB**
depending on how much of the app is used. Both viewports, console clean.

**Measurement caveat recorded for whoever comes next:** per-request wall-clock timing in this
headless environment is not trustworthy — the same fetch measured ~18 s *identically with and
without a 1 MB/s throttle*, because SwiftShader rendering 1M points dominates. Quote bytes and
request counts.
