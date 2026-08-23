# TODO — remaining tasks (handoff)

Concrete, checkable next steps for whoever picks this up. This is the *actionable* list;
`ROADMAP.md` holds the strategic ordering and `DESIGN_DECISIONS.md` records why choices
were made and what reverting costs. When you finish an item, check it off and note the
commit; when you add one, keep it specific enough to act on without re-deriving context.

Last updated: 2026-08-23 (publishable bundle: per-visit cost ~143 MB -> 5.7 MB; edges and
titles sharded; token search index; NeoLabs curated; author affiliations. D49-D58).

---

## 🔴 Open right now

- [ ] **Two e2e tests fail and I broke one of them.** `title search selects a paper and opens
      details` has failed since search became asynchronous (D54/D55): results now come from an
      index-chunk fetch instead of a scan over resident titles, and the dropdown re-renders as
      each title shard lands, so Playwright's click never finds a stable element. Verified
      failing against the committed spec at HEAD, so it is not a local artefact. The app itself
      is fine — driving the same flow through the DOM opens the details panel with a clean
      console on both viewports — this is a test-harness problem, not a product one.
      Attempts that did NOT fix it: widening the expect timeouts to 30 s; waiting for the
      title shimmer to clear before clicking; `dispatchEvent("click")` on the row and on its
      inner button; clicking via `page.evaluate`. Next thing to try is probably running the
      suite against a preview with the artifacts pre-warmed, or giving SearchBox a stable
      `data-testid` row identity that survives re-render.
- [ ] **Add a session-transition regression test.** The only thing that caught the React #310
      hooks-order bug (fixed in e821913) was a full sweep — open a paper, close it, filter an
      org, filter an author — because every per-feature check passed without ever CROSSING
      between those states. Assert: no console errors across that sequence.

- [ ] **Decide the scope of PDF affiliation parsing** (org attribution recall, task #9). This is
      the only item waiting on a human. Everything cheaper is ruled out with measurements —
      see D35: the author carry-over rule tops out at 61.6% precision and was rejected, and
      **no metadata source has this data**. Checked live 2026-08-17 against six unaffiliated
      2023-2026 papers: OpenAlex returns neither `institutions` nor `raw_affiliation_strings`,
      Semantic Scholar's `authors.affiliations` is empty, arXiv's own API returns no
      `<arxiv:affiliation>`, and DataCite has `affiliation` on zero creators. arXiv does not
      require an affiliation at submission, so it never enters the metadata chain. Only **1.7%**
      (9,172 of 532,003) post-2021 unaffiliated papers carry a non-arXiv DOI.
      GROBID is Apache-2.0, so only cost is undecided. Scoped by the true S2 counts (D34):
      **≥50 citations = 57,200 papers ≈ 86 GB**, **≥25 = 102,690 ≈ 154 GB**,
      **≥10 = 192,669 ≈ 289 GB** of requester-pays arXiv S3 egress. Local free disk is 262 GB,
      so anything past ~150 GB must stream (download tar → parse → delete, as s16 already does).

---

## 🔴 In flight / decided this session (do these next)

- [x] **Neolab org membership via curated author-roster join** (decision D9, implemented;
      browser re-verification awaits a rebuilt ignored bundle).
  - [x] Add `org_rosters.yaml` (org key → display name, ROR id or `local:` id, seed author
        ids). Seed from known papers' OpenAlex author ids (Redwood AI-Control authors are
        `A5037548279`, `A5050408969`, `A5084231398`, `A5028042353`).
  - [x] New pipeline stage: for each roster author id, tag corpus papers whose `author_ids`
        include it; store provenance (self-asserted / registry / co-authorship) + date bounds
        where available. Reuses ids already in the corpus — no scraping, no egress.
  - [ ] Optional booster for big labs: ORCID employment + Wikidata P108, auto-accept anyone in
        ≥2 sources, crosswalk ORCID→OpenAlex author id. (Prototype exists at `/tmp/roster_proto.py`
        from this session — move into the repo if kept.)
  - [ ] Co-authorship-expansion bootstrap for registry-invisible orgs (Redwood-class), with a
        short human approval list.
  - [x] Surface neolabs in the org filter alongside OpenAlex-institution orgs; provenance flag
        so doc/roster-derived membership is distinguishable from ROR-verified.
  - Note: this is **blocked-ish** on arXiv-id / corpus coverage for the papers you want tagged
    — the more of the corpus that has resolvable author ids, the more it catches.

- [x] **Offline figure extraction via PyMuPDF** — DONE (decision D11 now ACTIVE). Stage
      `s13_figures` + `common/figure_extract.py`; `has_figure` on the papers index + a
      `figures` manifest block; `FirstFigure.tsx` prefers the baked crop, falls back to
      pdf.js; 6 hermetic tests in `test_figure_extract.py`. Table-1 branch validated (borderless
      GLUE table) + GPT-3 "Figure 1.1". Browser-verified on ViT. **Remaining:**
  - [ ] **Run the full-corpus bake** once arXiv-id coverage is fixed (below). `figures.enabled`
        is off by default; a full pass is a multi-hour 1-req/3s batch. Run with
        `figures.enabled: true` (optionally `figures.max_papers` for a sample). Crops land in
        `web/public/data/figures/` (gitignored).
  - [ ] **Confirm the AGPL-3.0 call** for the public repo (PyMuPDF, offline PNG emit only). If
        unacceptable, swap s13's extractor for PDFFigures 2.0 (Apache/JVM); the frontend seam is
        extractor-agnostic. See D11.
  - [ ] Consider retiring the client-side `figureExtract.ts` once baked coverage is high (it's
        the fallback today).

- [ ] **Author alias/override layer** (decision D10). Curated `author_overrides.yaml`: merge
      known-split OpenAlex ids (e.g. Ethan Perez), drop phantom org-as-author entries (e.g.
      `DeepSeek-AI` as author, `:` as author). Apply in the author-index build.
- [ ] **S2 Graph API as a secondary author-identity source for OpenAlex-unmatched papers**
      (2026-08-15 finding, see D10 addendum). Empirically checked 8 papers sampled from the
      26,636 OpenAlex-unmatched population (incl. arXiv 2505.18134, "VideoGameBench," whose
      author "Alex L. Zhang" is the same person as "Alex Zhang" on arXiv 2512.24601,
      "Recursive Language Models" — OpenAlex has never matched the first paper and returns a
      `null` per-authorship id for that author even on a successful work lookup). Semantic
      Scholar's `/graph/v1/paper/arXiv:<id>` found **8/8** with every author resolved to a
      real, non-null S2 author id, and assigned the **same** S2 author id (`2324917699`) to
      "Alex L. Zhang" on both papers — i.e. it already solves this exact cross-paper identity
      case where OpenAlex currently cannot. Small sample (n=8); S2's own disambiguation error
      rate on a larger sample is unverified. Explicitly deferred per user request
      (2026-08-15) until the in-flight `s2-citations` bulk citation reconciliation job
      finishes, so this doesn't compete with it for S2 API quota. Natural home: extend
      `s16_enrich_s2_citations.py` (it already resolves arXiv→S2 paperId for the whole
      corpus) rather than a fresh standalone pass.

---

## 🟠 Data / rebuild state (know this before rebuilding)

- [x] **Build the comprehensive 2025+2026 `cs.* OR stat.ML` corpus.** Cornell snapshot plus
      OAI deltas produced 271,366 unique rows through 2026-08-13 with 100% abstracts.
- [x] **Embed the entire corpus in one SPECTER2 space.** 271,366/271,366 local proximity-
      adapter vectors, 768-d float32, normalized and row-aligned; checkpointed GPU run.
- [x] **Execute OpenAlex metadata enrichment before the downstream graph build.** The resumable
      `s15` exact-id crosswalk completed with 244,730/271,366 (90.2%) matches using concurrent
      100-id batches, match/negative checkpoints, canonical arXiv preservation, and active-
      corpus propagation. It used $0.4371 of the free daily allowance. Add/run the separate Semantic
      Scholar count/reference pass; OpenAlex's exact-match fields are now materialized locally
      by `s16_apply_openalex_citations` as the immediate citation source.
- [ ] **Optionally run the implemented S2AG bulk reconciliation and compare providers.** The
      manual `s16_enrich_s2_citations` job resolves arXiv ids, downloads/streams every S2AG
      citation shard, and replaces canonical values only for its matched rows while retaining
      OpenAlex fallback/provenance. It needs the configured `S2_API_KEY` and a long, high-volume
      source download; raw graph shards are streamed and deleted so free disk need only cover
      the largest individual shard plus derived artifacts. Keep S2AG's ODC-BY attribution in
      any public data release.
- [ ] **Optionally refresh hierarchy labels after the citation-edge update.** `s12`, `s09`,
      `s08`, and `s11` have rebuilt the live bundle with OpenAlex counts/edges; the expensive
      `s06,s07` semantic hierarchy/label pass was intentionally deferred because 2,816 new
      edges are tiny relative to the 271k-paper text graph. Run `uv run python -m
      pipeline.run_all --only s06,s07,s11` when a full citation-influenced relabel is wanted.
- [x] Re-validate the enriched 271k bundle in desktop + mobile Chromium after the s14/s10/s11
      rebuild (157 files / 173.8 MB): map/canvas, paper/label search, Table 1, roster and
      institution drill-down, researcher scoping, date controls, screenshots, and console/
      page-error checks pass. Citation-specific UI remains unavailable until the
      Semantic Scholar pass above.

---

## 🟡 From ROADMAP (P0 — correctness; see ROADMAP.md for full detail)

- [ ] **Decouple corpus discovery from org selection** (ROADMAP P0 #3). Today the configured
      institutions are *both* fetch predicates and UI filters — the root blocker for "any
      organization." Ingest broad CS/AI by field+date; make orgs a membership index. (HANDOFF
      gotcha #8.)
- [x] **Complete one-space embeddings** — `specter2_local` embeds the full selected corpus
      with the official proximity adapter and durable row checkpoints (decision D15).
- [ ] **Validate the planar hierarchy** against a human-reviewed benchmark — neighborhood
      precision, per-band coherence, hierarchy stability, label quality (ROADMAP P0 #5). The
      hierarchy is *not* validated topic ground truth yet (HANDOFF gotcha #7).
- [ ] Drive the residual **10.5% internally-disconnected zoom cells → ~0** via a
      connectivity-aware post-processing pass in `s06` (trades against branch-target/nesting
      invariants — needs its own design).
- [ ] Retain **author/ORCID/institution ids** as structured columns in `affiliations.parquet`
      (only affiliation strings kept today) — prerequisite for D9/D10 at scale.
- [ ] Benchmark affiliation→institution matching against **AffRo** (CC0 AffRoDB).

## 🟢 P1 — reproducibility & quality gates

- [ ] Single dependency declaration — stop hand-maintaining both `pyproject.toml` and
      `pipeline/requirements.txt`; commit the lockfile intentionally (`uv.lock` now present).
- [ ] Redistributable **sample bundle** so a fresh clone renders without API access + full run.
- [ ] Artifact-contract tests: dense IDs, row alignment, edge bounds, hierarchy
      parentage/coverage, finite normalized vectors, manifest metadata.
- [ ] Add **CI**: Python tests + frontend build/typecheck + Playwright e2e.
- [ ] Add an **open-source LICENSE** before presenting the repo as distributable.
- [ ] More e2e: selection-focus assertions, citation-mode toggles, keyboard-only nav.

## 🔵 UX follow-ups

- [ ] Human spot-check of leaf-label quality; tune `_LEAF_MAX_GROUP` / band step.
- [ ] Optional **cached LLM leaf-naming** pass (highest-value leaf upgrade; deterministic
      fallback, keyed by community hash) — decision D6.
- [ ] Deep links for view state / filters / selection.
- [ ] Remaining 2026-07 audit items: tab keyboard semantics, arXiv iframe fallback,
      zero-results overlay, bundle-load spinner, retryable error screen.

---

## Frontend JS unit tests (coverage gap noted in Features.md)

- [ ] Add vitest + a test for `web/src/map/importance.ts` (`lodRamp`, `importanceWeight`,
      zoom→level mapping) — currently verified only visually.
- [ ] e2e assert that selection **culls** non-connected points (not just dims).

## Always, for any change (from ROADMAP + AGENTS.md)

- [ ] `uv run pytest` green; add tests for new pipeline logic.
- [ ] `cd web && npx tsc -b && npm run build` clean.
- [ ] After pipeline changes, **render the map in a browser** — the previous agent caught 4
      real bugs only by looking, not from tests.
- [ ] Update `Features.md` (+ its test-coverage table), `Design.md`, and `DESIGN_DECISIONS.md`
      when a change affects capability / mechanism / a tradeoff.


---

## Where the per-visit budget stands (measured 2026-08-23, 1 MB/s, both viewports)

| | requests | bytes |
|---|---|---|
| home view | 22 | **5.7 MB** |
| + select a hub paper (Attention, 69,262 citers) | 38 | 15.1 MB |
| + filter an organization (Tsinghua) | 40 | 17.8–20.8 MB |
| + filter an author | 43–45 | 23.8–24.0 MB |

Against the ~143 MB *before touching anything* that the work started from. No artifact exceeds
GitHub's 100 MB per-file limit, so the bundle is hostable. Publish dry-run: **3,067 files,
1.03 GB raw -> 0.52 GB gzipped**, excluding `papers.arrow` and `edges.arrow` (D47).

## Biggest remaining wins, in order

1. **`authors.arrow` is now the largest stream on the wire** — ~14.4 MB fetched eagerly in 13
   chunks so the author box can substring-match names. It is the single biggest remaining item
   and the same shape as the title index that replaced 31 MB (D54): a name-token -> author-id
   index measured at **5.5 MB capped at 25 authors/token**, and it could be chunked
   alphabetically like the title index so a query costs ~100 KB instead of 14 MB.
2. **The real R2 upload has never run.** `tools/publish_artifacts.sh --confirm` is written,
   dry-run-verified and waiting on an explicit go-ahead. Nothing has been published.
3. **A custom domain in front of R2** is the highest-value cost/abuse mitigation left: a cached
   response never reaches the bucket and costs no Class B operation, and it moves off
   `pub-*.r2.dev`, which Cloudflare rate-limits and does not intend for production.
4. **The 2026 affiliation gap.** Upstream extraction stops at December 2025, so 2026 sits at
   ~6% attribution against 87-90% for 2021-2024. The GPU works now; the route is COMET's own
   open-weight student model over page-1 text (no OCR needed for recent CS papers).
