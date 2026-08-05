# Features

What the Research Visualizer does today. See `Design.md` for how and why, and the
**Feature → test coverage** table at the bottom: every feature there is pinned to the
test(s) that guard it. **Do not delete a listed test without removing its feature row and
saying so in the commit** — the table exists so protection for a shipped feature is never
silently dropped.

## The map

- **Semantic map of ~71,800 CS papers** (OpenAlex field 17, 2015–2026) across 12 seed
  organizations — the papers with a real SPECTER2 vector, out of ~89k fetched (see Design.md
  for the SPECTER2-only policy). Each paper is a point; **nearby points are semantically
  similar** (position from SPECTER2 embeddings projected to 2D with openTSNE).
- **GPU rendering** via deck.gl handles the full corpus at interactive framerates on an
  orthographic (map-style) canvas with smooth pan/zoom.
- **Level of detail (LOD) with guaranteed no overlap** — at the zoomed-out home view the
  corpus is far too dense to read (measured: ~90% of points sit within 2px of a neighbor).
  A pipeline stage (s12) assigns each paper a **reveal level** via greedy spatial thinning:
  level 0 is a sparse, evenly-separated set of the most-cited papers, and each deeper level
  admits ~4× more while maintaining a minimum on-screen separation — so **no two visible
  points ever overlap at any zoom**. The map draws a point only when its reveal level ≤ the
  active zoom level; citation edges are gated the same way, so the edge web thins with the
  points. A selection or active filter forces the full set so nothing relevant is hidden.
  This is the whole point of the tool: **you preview a chosen region/zoom, never the entire
  corpus at once.**
- **Point color** encodes CS **subfield** by default; switchable to **organization** or
  **recency** (publication year) via the legend.
- **Point size** scales gently with citation count (a log ramp), so influential papers stand
  out without swamping their neighbors.
- **Hover a point** for an instant preview card (title, authors, year, venue, citations)
  pulled straight from memory — no click, no fetch.

## Semantic zoom (the headline feature)

- Zoom behaves like Google Maps for research topics:
  - **Zoomed out** → broad research areas (vision, privacy, language, robotics, systems,
    theory, HCI, etc.).
  - **Mid zoom** → **topics** within a field (e.g. self-supervised learning, world models).
  - **Zoomed in** → **fine subtopics** as differentiating phrases.
  - **Zoomed in all the way** → **micro-clusters of just a few papers**, named by the phrase
    their titles share — 11 nested zoom bands (~24,600 regions) in total.
- Regions come from **Leiden community detection over a planar substrate**: adjacency is the
  2D layout's kNN graph, edge weights are 768-D embedding cosine. This guarantees each region
  is **one contiguous area of the map** (not scattered across it), which is what makes topic
  groupings read as coherent rather than looking like unrelated papers lumped together.
  Children are strict subsets of their parent, so zoom levels can never contradict.
- Labels **declutter in screen space on the CPU**: coarse/important labels win scarce screen
  space when zoomed out; finer labels reveal themselves as you zoom in.
- Label text combines discriminative **OpenAlex topic names** with detailed **c-TF-IDF
  phrases** mined from representative titles and abstracts, avoiding repeated ancestor and
  sibling names. Embedded MathML/LaTeX markup is stripped structurally so formula markup can
  never become a label.
- **Filter-aware labels**: when an organization or author filter is active, only labels whose
  region still contains matching papers remain, so topic names and point colors describe the
  filtered subset rather than the whole map.

## Filtering

- **Organization drill-down** — featured seed orgs (industry: Google, Google DeepMind,
  Amazon, OpenAI, NVIDIA, Allen Institute for AI, Meta, Microsoft Research; academia: UC
  Berkeley, CMU, Stanford, MIT) expand into evidence-backed **departments and labs** (e.g.
  CMU → Robotics Institute / LTI / Machine Learning Department; Meta → FAIR / Reality Labs /
  Meta AI; MIT → CSAIL / RLE). Sub-units are derived from raw affiliation strings, so counts
  are real, not inferred; selecting a parent includes its whole rollup, selecting a child
  includes only that unit's papers. Rollup vs direct counts are shown so a parent aggregate
  is never mistaken for a specific lab.
- **Filter by any institution** — beyond the featured orgs, the search box spans **every
  university, company, and lab in the corpus** (~3,575 institutions with ≥3 papers).
- **Organization-scoped researchers** — expand a unit's "Top researchers" to see its most
  prolific authors and add them to the author filter.
- **Author** — type-ahead search over every author in the corpus; select one or more to
  spotlight their papers.
- **Date range** — a month-granularity **dual-handle slider** over a **publication
  histogram**, plus presets (All, Last 12mo, Last 24mo, current year) and a live "papers in
  range" count. Runs on the GPU, so dragging is instant.
- Filters **compose** (organization AND author AND date). "Clear" resets them.

## Selection, citations & related works

- **Click a paper** to open a details card: title, authors, date, venue, citation count, and
  a direct link (DOI / arXiv).
- **On selection, irrelevant papers are fully hidden** (culled on the GPU — not drawn, not
  hoverable), so the selected paper and its citation network are the only things on the map.
  Deselecting restores the full map.
- **Citation importance is visualized in rank order.** On both the map edges and the
  details-panel citation graph, each linked paper's **edge width, opacity, arrowhead size,
  and node/endpoint size scale with its importance** (a blend of citation magnitude and rank
  position), so the strongest references and citers read as the boldest, largest links —
  in-citations and out-citations each self-scale independently.
- The normal map view shows a deterministic, zoom-adaptive sample of **directed citation
  edges by default**; an **Edges** toggle hides or restores them.
- A dedicated **citation explorer** separates **References**, **Cited by**, and **Both**,
  with a compact directed neighborhood and searchable, clickable paper lists.
- The details panel has **Citations** and **Paper** tabs. Paper view renders the **first page
  of the arXiv PDF** via pdf.js; if no arXiv ID is stored it resolves one via Semantic
  Scholar, falling back to a TL;DR/abstract card.
- **Related works** panel lists the most similar papers using a **fused text + citation**
  similarity (semantic neighbors, direct citations, co-citations, shared references).

## Search

- **Title search** with type-ahead; selecting a result selects and focuses that paper.
- Title and author search support keyboard selection.
- On small screens, filters open as a drawer and paper details use a bottom sheet.

## Data & provenance

- The paper corpus comes from OpenAlex; SPECTER2 vectors from Semantic Scholar, addressed by
  **arXiv → DOI → MAG id** so landmark papers whose OpenAlex DOI S2 does not index (e.g.
  "Attention Is All You Need") are still recovered.
- A top-bar summary shows corpus size, date span, and embedding backend. All data is
  pre-baked into a static bundle (`web/public/data/`) — the app runs with no backend.

## Not yet (planned)

- **Fetch-on-demand tiling** so corpus size stops gating the initial download. The bundle is
  currently downloaded in full on load; realizing "load only the chosen region/topic/range"
  for an unbounded (1M+) corpus requires pre-tiling papers by region/zoom and fetching only
  visible tiles. (The LOD reveal above is the visual half of this; tiling is the delivery
  half.) This also subsumes true global "no node overlap at any zoom".
- Decoupling corpus discovery from org selection so **any** organization is queryable without
  re-fetching, and deeper org nesting / temporal membership / cross-institution identity.
- Complete embedding coverage (`specter2_local` for the ~19% S2 still can't vector); live
  "embed my own abstract" search; incremental nightly updates; a human-reviewed
  topic/neighborhood quality benchmark.

---

## Feature → test coverage

Each feature that has automated protection is mapped to the test(s) guarding it. When you
change or remove a test, update this table and call it out in the commit — a shipped feature
should never lose its guard silently. Run `python -m pytest pipeline/tests -q` (unit) and
`cd web && npx playwright test` (e2e).

| Feature | Guarding test(s) | File |
|---|---|---|
| Contiguous semantic-zoom regions (planar substrate) | `test_planar_regions_are_spatially_contiguous`, `test_planar_graph_only_connects_spatial_neighbors_with_semantic_weights` | `pipeline/tests/test_hierarchy.py` |
| Nested regions strictly partition their parent | `test_graph_hierarchy_children_partition_their_parent`, `test_curated_child_units_are_subsets_of_parent` | `test_hierarchy.py`, `test_org_index.py` |
| Leiden yields internally connected communities | `test_leiden_communities_are_internally_connected` | `pipeline/tests/test_hierarchy.py` |
| Fused graph keeps citations + strongest neighbor weight | `test_fused_graph_keeps_direct_citations_and_strongest_neighbor_weight` | `pipeline/tests/test_hierarchy.py` |
| Labels combine topic + specific phrase; leaf micro-cluster naming | `test_label_combines_topic_with_specific_community_phrase`, `test_leaf_phrase_*` (3) | `pipeline/tests/test_labels.py` |
| MathML/markup never becomes a label | `test_clean_strips_mathml_so_attribute_names_cannot_become_labels`, `test_markup_tokens_and_acronyms_are_normalized` | `pipeline/tests/test_labels.py` |
| SPECTER2 arXiv→DOI→MAG addressing (recovers landmark papers) | `test_s2_addressing_routes_include_mag_fallback`, `test_arxiv_extraction`, `test_clean_doi_*` | `pipeline/tests/test_corpus.py` |
| Abstract reconstruction from inverted index | `test_basic_reconstruction`, `test_out_of_order_index`, `test_gap_in_positions_is_skipped`, `test_duplicate_words_multiple_positions`, `test_missing_and_empty`, `test_embed_text_composition` | `pipeline/tests/test_abstract.py` |
| Fused text + citation related-works ranking | `test_reference_sets_preserve_citation_direction`, `test_citation_candidates_include_direct_coupling_and_co_citation`, `test_fused_ranking_can_introduce_a_non_text_citation_candidate` | `pipeline/tests/test_fused_similarity.py` |
| Org drill-down: dept/lab attribution, no cross-org leakage | `test_fair_is_separated_from_generic_meta`, `test_facebook_ai_without_research_is_meta_ai_not_fair`, `test_no_cross_org_leakage`, `test_cmu_specific_unit_wins_over_school`, +6 more | `pipeline/tests/test_directory.py` |
| Org affiliation evidence scoped per authorship | `test_org_affiliation_evidence_is_scoped_per_authorship`, `test_org_affiliation_evidence_empty_without_map` | `pipeline/tests/test_corpus.py` |
| Curated roots + full directory split; no duplication | `test_curated_root_and_directory_split`, `test_curated_institution_not_duplicated_in_directory`, `test_directory_entry_falls_back_to_id_without_registry` | `pipeline/tests/test_org_index.py` |
| Frozen projector keeps new points in the same map space | `test_frozen_projector_reuses_fit_normalization_for_new_points`, `test_frozen_projector_rejects_non_2d_fit_coordinates` | `pipeline/tests/test_project.py` |
| Reveal-level thinning: total coverage, overlap-free per zoom, importance-ordered, deterministic | `test_cumulative_levels_are_overlap_free`, `test_every_paper_gets_a_level`, `test_most_important_papers_reveal_first`, `test_deterministic`, `test_ties_broken_by_index_not_random`, +2 | `pipeline/tests/test_thinning.py` |
| Map loads, corpus summary, filled canvas | `loads the map, corpus summary, and a filled canvas` | `web/e2e/app.spec.ts` |
| Title search → select → details | `title search selects a paper and opens details` | `web/e2e/app.spec.ts` |
| Hover preview tooltip | `hovering a point shows a preview tooltip` | `web/e2e/app.spec.ts` |
| Org drill-down UI + scoped researchers + directory search | `expands a university into evidence-backed departments/labs`, `selecting a lab reveals org-scoped researchers`, `org search surfaces a child unit and its parent`, `org search finds a non-curated corpus institution` | `web/e2e/app.spec.ts` |
| Date presets + dual-handle slider update in-range count | `a preset narrows the corpus and updates the in-range count`, `the single dual-handle slider narrows the range by dragging` | `web/e2e/app.spec.ts` |
| Graceful bundle-missing error | `shows a clear error when the bundle is missing (route-mocked 404)` | `web/e2e/app.spec.ts` |

### Coverage gaps (features without a dedicated automated test)

These are shipped but currently guarded only by manual/visual verification. Adding tests
here is welcome; **removing the feature should still update this file.**

- **Reveal-level render gate + fade/shrink + citation importance ordering** — the *pipeline*
  thinning that produces reveal levels is fully tested (`test_thinning.py`); the *frontend*
  math (`lodRamp`, `importanceWeight`, the zoom→level mapping) is centralized in
  `web/src/map/importance.ts` and `usePointsLayer.ts`, ready to unit-test, but the web app
  has **no JS unit-test runner** yet (only Playwright e2e + Python pytest). Adding vitest +
  a test for that module is the natural next guard; until then the frontend half is verified
  visually.
- **Selection culls irrelevant papers** (`usePointsLayer.ts` filter channel) — no e2e assert
  that non-connected points stop rendering/picking; verified visually.
- **t-SNE island separation** (`projector.exaggeration`) — no numeric guard on inter-cluster
  whitespace; verified via a one-off occupancy measurement.
