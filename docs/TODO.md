# TODO — remaining tasks (handoff)

Concrete, checkable next steps for whoever picks this up. This is the *actionable* list;
`ROADMAP.md` holds the strategic ordering and `DESIGN_DECISIONS.md` records why choices
were made and what reverting costs. When you finish an item, check it off and note the
commit; when you add one, keep it specific enough to act on without re-deriving context.

Last updated: 2026-08 (this session focused on org/author attribution research + the
figure-extraction benchmark).

---

## 🔴 In flight / decided this session (do these next)

- [ ] **Neolab org membership via curated author-roster join** (decision D9, agreed).
  - [ ] Add `org_rosters.yaml` (org key → display name, ROR id or `local:` id, seed author
        ids). Seed from known papers' OpenAlex author ids (Redwood AI-Control authors are
        `A5037548279`, `A5050408969`, `A5084231398`, `A5028042353`).
  - [ ] New pipeline stage: for each roster author id, tag corpus papers whose `author_ids`
        include it; store provenance (self-asserted / registry / co-authorship) + date bounds
        where available. Reuses ids already in the corpus — no scraping, no egress.
  - [ ] Optional booster for big labs: ORCID employment + Wikidata P108, auto-accept anyone in
        ≥2 sources, crosswalk ORCID→OpenAlex author id. (Prototype exists at `/tmp/roster_proto.py`
        from this session — move into the repo if kept.)
  - [ ] Co-authorship-expansion bootstrap for registry-invisible orgs (Redwood-class), with a
        short human approval list.
  - [ ] Surface neolabs in the org filter alongside OpenAlex-institution orgs; provenance flag
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

---

## 🟠 Data / rebuild state (know this before rebuilding)

- [ ] **Served bundle is the 71,831-paper build.** A prior 450k rebuild **died during
      embedding** (S2 429s) and left interim artifacts (`corpus.parquet` 449,840;
      `corpus_active.parquet` 394,269 with only ~15,672 arXiv ids) but **never emitted a
      bundle**. Decide: discard vs. resume. It ran on pre-arXiv-id-fix code, so a clean re-run
      is likely better than resuming.
- [ ] **arXiv-id backfill** on the current bundle (queued earlier): `s03` now merges S2's
      returned arXiv ids into the corpus, but the *served* bundle predates that — only ~14% of
      papers have an arXiv id, which caps the figure feature and the roster join.
- [ ] Task #4 in the tracker ("re-fetch 390k+ once metadata is lazy") is still open and tied to
      the dead rebuild above.

---

## 🟡 From ROADMAP (P0 — correctness; see ROADMAP.md for full detail)

- [ ] **Decouple corpus discovery from org selection** (ROADMAP P0 #3). Today the configured
      institutions are *both* fetch predicates and UI filters — the root blocker for "any
      organization." Ingest broad CS/AI by field+date; make orgs a membership index. (HANDOFF
      gotcha #8.)
- [ ] **Complete one-space embeddings** — build `specter2_local` backend to embed the
      `on_uncovered: drop` misses into the same SPECTER2 space (ROADMAP P0 #4 / decision D2).
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
