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

## D2. SPECTER2-only space via `on_uncovered: drop` — ACTIVE

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

## D9. Organization membership for neolabs: **curated author-roster join**, not document parsing — PROPOSED

- **Decision (agreed, not yet built).** Attribute neolab papers (Redwood, Anthropic-preprints,
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
  cluster directly above each caption — the PyMuPDF/PDFFigures method ported to the browser.
  It extracts **both Figure 1 and Table 1** (each when present), with a text-block fallback for
  borderless tables.
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
