# Roadmap — next work, prioritized

Ordered by architectural value after the 2026-07 re-evaluation in `Design.md`. The current
MVP is useful, but presentation polish should not outrank corpus and topic correctness.

Prior-work survey backing several items below (embedding/layout/semantic-zoom and
org/lab/author attribution, with primary sources and adopt-now recommendations):
[`RESEARCH_PRIOR_WORK.md`](RESEARCH_PRIOR_WORK.md).

## P0: organization, corpus, and semantic correctness

### 1. Preserve organization evidence — DONE (partial)

`s02` now retains the raw affiliation strings per authorship (scoped to the resolved org
institution) in `affiliations.parquet`, and `pipeline/directory/units.py` matches them into
evidence-backed dept/lab sub-units emitted as a two-level hierarchy in `orgs.json`. The
broad Meta seed is named `Meta`; FAIR is a narrow child (425 papers). **Still to do:**
retain author/ORCID IDs and institution IDs as structured columns (only affiliation strings
are kept today), and add normalization fixtures for messier affiliation variants.

Prior-work confirms this manual-curation approach is *correct*, not a shortcut: **ROR (and
therefore OpenAlex) deliberately excludes departments/labs** — only ~0.06% of ROR records are
university-child departments, so dept/lab granularity is unsourceable from registries and
must be built. Two verified upgrades to consider (see `RESEARCH_PRIOR_WORK.md` §2):
- **Benchmark affiliation→institution matching against AffRo** (OpenAIRE, arXiv:2505.07577):
  it beats OpenAlex's built-in matcher (F1 ~0.937 vs 0.921) on the CC0 **AffRoDB** set and is
  production-proven — and it catches the documented "OpenAlex parser not retrained since April
  2023" gap for newer institutions.
- **Auto-propose sub-units via co-authorship community detection** (communities cluster around
  PIs ≈ research groups) for human confirmation, to scale curation past hand-listing.

### 2. Build the canonical organization directory

Implement the provider-neutral model in
[`ORGANIZATION_DIRECTORY.md`](ORGANIZATION_DIRECTORY.md): stable local identities, typed
and date-valid `part_of` relationships, aliases, researcher affiliations, evidence, and
authorship-backed direct paper attributions.

- Add `pipeline/directory/` provider, canonicalization, attribution, and validation
  boundaries.
- Seed the current curated org roots (Google, Google DeepMind, Amazon, OpenAI, NVIDIA, Allen
  Institute for AI, Meta, Microsoft Research, UC Berkeley, CMU, Stanford, MIT — 12 as of
  `config.yaml`, up from the seven this section originally referenced) and evidence-backed
  Meta/academic child units, plus the growing curated-roster neolabs (`org_rosters.yaml`,
  currently just Redwood Research as a pilot).
- Emit artifact schema v2 (`organizations.json`, `paper_organizations.arrow`,
  `researchers.arrow`, and `researcher_organizations.arrow`) alongside legacy artifacts.
- Add golden tests for Meta/FAIR separation, researcher moves, multiple parents,
  multi-organization papers, and unresolved aliases.
- Implement CSRankings as a disabled-by-default academic provider with synthetic fixtures.
  Do not redistribute transformed data until its CC BY-NC-ND licensing question is
  resolved. (Verified: the CSRankings project as a whole is CC BY-NC-ND 4.0 — NonCommercial
  **and** NoDerivatives — while its underlying DBLP data is separately ODC-BY; its
  faculty→institution map is manually curated, not algorithmic. See
  `RESEARCH_PRIOR_WORK.md` §2A.)

### 3. Decouple corpus discovery from organizations — DONE (via the arXiv-spine pivot)

Achieved, but by a different route than originally planned here: rather than broadening the
OpenAlex-fetch predicate, `corpus.source: arxiv_snapshot` (see `HANDOFF.md`) made the corpus a
broad `cs.* OR stat.ML` / date-range ingest independent of any institution list, with
organizations layered on afterward as a separate membership overlay — OpenAlex-institution
evidence (`s15_enrich_openalex.py` → `affiliations.parquet` → `units.py`) plus a curated
author-id roster for registry-invisible neolabs (`org_rosters.yaml`, `s14_rosters.py`). The
corpus is now 271,366 papers (2025–2026); org attribution remains **partial** (OpenAlex
matches 90.2%, but only ~33k papers carry institution evidence) — that gap is item #1 above,
not this one. `HANDOFF.md` gotcha #8 has the full caveat.

### 4. Complete one-space embeddings

`s03` now addresses Semantic Scholar by arXiv → DOI → MAG id, which recovers papers whose
OpenAlex DOI S2 does not index (measured: +57% of MAG-bearing dropped rows, including the
canonical "Attention Is All You Need"). `on_uncovered: drop` still removes whatever remains
unresolved. Close the rest with a `specter2_local` backend using the same SPECTER2
model/adapter as the fetched vectors, then embed missing papers locally. Validate with a
sample of papers available through both paths before assuming the two sources are
numerically compatible. If compatibility cannot be shown, embed the entire corpus locally.

### 5. Validate the planar hierarchy

Semantic-zoom regions now come from Leiden over the planar substrate (2D-layout kNN
adjacency, 768-D cosine weights); the fused semantic/citation graph remains selectable for
comparison. Regions are contiguous on screen and topic purity is measured against OpenAlex
labels, but OpenAlex topics are weak ground truth. Do not mistake this for topic
correctness. Build a versioned human-reviewed benchmark and track:

- neighborhood precision against human-reviewed related-paper sets;
- community coherence and coverage at each zoom band;
- hierarchy stability across rebuilds;
- label specificity, duplication, and human ratings.

**Switch Louvain → Leiden — DONE (2026-07).** `s06` now defaults to Leiden
(`leidenalg`/`igraph`); Louvain stays selectable. On the 28k-paper corpus this was measured
against, internally disconnected zoom cells dropped 14.5% → 10.5% and the hierarchy resolves
more/finer communities (6,732 → 7,359 regions) with strict nesting intact. **The corpus has
since grown to 271,366 papers via the arXiv-spine pivot (see item #3) — this disconnection
rate has not been re-measured at the new scale.** **Remaining:** the residual disconnection
comes from this stage's *semantic* post-processing (coarsen/merge/fill + embedding fallback)
re-merging graph-disjoint groups — the raw Leiden split itself is 0% disconnected.
A **connectivity-aware post-processing pass** (split any cell whose induced subgraph is
disconnected, or prefer graph-adjacent merges) would drive this toward 0, but trades against
the branch-target and strict-nesting invariants, so it needs its own design. See
`RESEARCH_PRIOR_WORK.md` §1.4 and recommendation #1.

**Cross-check labels with a content-only signal.** A WizMap-style tile t-TF-IDF or
hierarchical BERTopic pass (both need no citation data) gives an independent second opinion to
validate the citation-community labels against. See `RESEARCH_PRIOR_WORK.md` §1.3.

## P1: reproducibility and quality gates

- Hierarchical organization search, direct/rollup counts, and organization-scoped
  researcher browsing are now implemented (from affiliation evidence, without schema-v2).
  Still to add on top: breadcrumbs, confidence disclosure, and the confidence-tiered
  inclusive mode once the v2 artifacts exist.
- Add official-page adapters incrementally for reviewed departments, labs, and corporate
  research units; every source row must be mapped, ignored with a reason, or unresolved.
- Commit a Python lockfile intentionally and choose one dependency declaration instead of
  maintaining both `pyproject.toml` and `pipeline/requirements.txt` by hand.
- Add a redistributable sample bundle or release download. A fresh clone currently cannot
  render without API access and a full pipeline run.
- Add artifact-contract tests covering dense IDs, row alignment, edge bounds, hierarchy
  parentage/coverage, finite normalized vectors, and manifest metadata. (Org sub-unit
  attribution now has golden tests in `pipeline/tests/test_directory.py`; the frontend
  loader still validates dense IDs, row alignment, and edge bounds at load.)
- Frontend e2e tests exist (`web/e2e/`, Playwright desktop+mobile): load failure, search +
  selection, org drill-down, org-scoped researchers, and date presets. Still to add:
  selection-focus assertions, citation-mode toggles, and keyboard-only navigation.
- Add CI for Python tests, frontend build/typecheck, and the Playwright e2e suite. Add an
  open-source license before presenting the repository as distributable open source.

## P2: scale beyond the in-memory bundle

The ~17 MB static bundle is appropriate now. Past roughly 100k papers:

- ingest the OpenAlex snapshot and bulk embedding datasets instead of per-paper APIs;
- fit projection/layout on stable landmarks and transform/aligned-update the remainder;
- emit spatial point/label tiles and lazy paper/neighbor/edge partitions;
- serve immutable, content-addressed versions so a client never mixes artifacts from two
  builds;
- add a query service only where static partitions cannot answer the workflow.

## UX follow-ups

- **Ultra-fine "micro-cluster" labels** — DONE (option 1, design in `Design.md` §5). The
  hierarchy now recurses to 11 bands (`min_cluster_size: 8`, `min_tile_points: 3`) and `s07`
  names small leaf communities from their shared title n-gram. Follow-up: a human-reviewed
  spot check of leaf-label quality (some 3-paper groups will still get weak names), and
  tuning `_LEAF_MAX_GROUP` / band step if the deep bands feel too sparse or too dense.
- Evaluate an optional cached LLM naming pass against the deterministic topic+c-TF-IDF
  baseline; key it by a community membership hash and retain reproducible fallback labels.
  This is now the **highest-value upgrade for the leaf bands** — deterministic shared-phrase
  names ship today; an LLM pass would sharpen them ("Sim-to-Real RL for Legged Locomotion").
- Add deep links for view state, filters, and selection.
- Consider surfacing the remaining verified UI/UX findings from the 2026-07 audit not yet
  addressed (details-panel tab keyboard semantics, arXiv iframe fallback, zero-results map
  overlay, bundle-load spinner, retryable error screen).

---

## Testing expectations for any change
- `uv run pytest` stays green (add tests for new pipeline logic; existing tests cover
  abstract reconstruction, corpus identifiers, and projector normalization).
- `cd web && npx tsc -b && npm run build` stays clean.
- After pipeline changes, rerun affected stages and **look at the map in a browser** — the
  previous agent caught 4 real bugs (DOI mangling, topic-id parsing, zstd Arrow, label
  culling) *only* by rendering it, not from tests. Don't skip the visual check.

---

## 2026-08-23 — the bundle is publishable

The work that made this hostable and visitable is done (D49-D58). A visit costs **5.7 MB across
22 requests** where it cost ~143 MB, and no artifact exceeds GitHub's 100 MB per-file limit.

What changed, in order of size: citation edges became zoom tiers plus per-paper shards (87 MB
-> 10 KB at the home view, D53); titles became node shards fetched for what is on screen
(31.1 MB -> 0.13-2.07 MB, D55); search moved onto a token index that also fixed a correctness
bug — results used to depend on which title chunks had downloaded (D54); `orgs.json` shed the
directory's membership (5.05 -> 0.67 MB, D50). The app deploys separately from its data behind
one environment variable (D52).

Strategically the next lever is the same one three times over: **`authors.arrow` is the last
big eager stream** (~14.4 MB), and the name-token index that would replace it is the pattern
already proven twice. After that, publishing is a decision rather than a project — the script
is written and dry-run-verified, waiting on a go-ahead — and the 2026 affiliation gap is the
only remaining *data* deficit, now unblocked by a working GPU.
