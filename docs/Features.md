# Features

What the Research Visualizer does today. See `Design.md` for how and why, and the
**Feature → test coverage** table at the bottom: every feature there is pinned to the
test(s) that guard it. **Do not delete a listed test without removing its feature row and
saying so in the commit** — the table exists so protection for a shipped feature is never
silently dropped.

## The map

- **Semantic map of 271,366 recent CS/ML papers** from arXiv (`cs.* OR stat.ML`, 2025 through
  2026-08-13), all with local SPECTER2 proximity-adapter vectors. Each paper is a point;
  **nearby points are semantically similar** (openTSNE projection of the embeddings).
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
- **Point size** scales gently with citation count when the corpus has provider-backed
  counts. The current arXiv-only preview uses a uniform base size rather than inventing
  influence from missing data.
- **Hover a point** for an instant preview card (title, authors, year, venue, citations)
  pulled straight from memory — no click, no fetch.

## Semantic zoom (the headline feature)

- Zoom behaves like Google Maps for research topics:
  - **Zoomed out** → broad research areas (vision, privacy, language, robotics, systems,
    theory, HCI, etc.).
  - **Mid zoom** → **topics** within a field (e.g. self-supervised learning, world models).
  - **Zoomed in** → **fine subtopics** as differentiating phrases.
  - **Zoomed in all the way** → **micro-clusters of just a few papers**, named by the phrase
    their titles share — 11 nested zoom bands (89,083 regions in the current build).
- Regions come from **Leiden community detection over a planar substrate**: adjacency is the
  2D layout's kNN graph, edge weights are 768-D embedding cosine. This guarantees each region
  is **one contiguous area of the map** (not scattered across it), which is what makes topic
  groupings read as coherent rather than looking like unrelated papers lumped together.
  Children are strict subsets of their parent, so zoom levels can never contradict.
- Labels **declutter in screen space on the CPU**: coarse/important labels win scarce screen
  space when zoomed out; finer labels reveal themselves as you zoom in.
- Label text combines a discriminative **taxonomy signal** with detailed **c-TF-IDF
  phrases** mined from representative titles and abstracts, avoiding repeated ancestor and
  sibling names. Enriched corpora use OpenAlex topics; the current arXiv-only preview uses
  primary category codes (`cs.CV`, `cs.CL`, etc.) until enrichment supplies that taxonomy.
  Embedded MathML/LaTeX markup is stripped structurally so formula markup cannot become a
  label.
- **Restriction-aware labels**: whenever the view is restricted — an organization/author
  filter, or a single-paper selection (which shows only that paper + its citation network) —
  semantic-zoom labels over now-empty regions disappear, so topic names describe only what is
  actually visible rather than blanketing the whole map. Clearing the restriction restores
  all labels.

## Filtering

- **Organization drill-down** — browsable roots are separated into **Companies**,
  **Independent labs**, and **Universities & research institutions**. Roots and every nested
  unit are alphabetized; child labels carry their kind (school, department, institute, lab,
  division, or site). Seed orgs include Google, Google DeepMind,
  Amazon, OpenAI, NVIDIA, Allen Institute for AI, Meta, Microsoft Research; academia: UC
  Berkeley, CMU, Stanford, MIT) expand into evidence-backed **departments and labs** (e.g.
  CMU → Robotics Institute / LTI / Machine Learning Department; Meta → FAIR / Reality Labs /
  Meta AI; MIT → CSAIL / RLE). Sub-units are derived from raw affiliation strings, so counts
  are real, not inferred; selecting a parent includes its whole rollup, selecting a child
  includes only that unit's papers. Rollup vs direct counts are shown so a parent aggregate
  is never mistaken for a specific lab.
- **Curated neolabs** — organizations without reliable OpenAlex/ROR institution records can
  appear through reviewed, date-bound OpenAlex author rosters. Redwood Research is the first
  seed. These entries live in the separate **Independent labs** group and carry a visible
  `roster` marker so author-roster attribution is not confused with structured institutional
  affiliation.
- **Filter by any institution** — beyond the featured orgs, the search box spans **every
  university, company, and lab in the corpus** (~3,575 institutions with ≥3 papers).
- **Organization-scoped researchers** — expand a unit's "Top researchers" to see its most
  prolific authors and add them to the author filter.
- **Author** — type-ahead search over every author in the corpus; select one or more to
  spotlight their papers.
- **CS topic/category** — enriched builds expose OpenAlex subfields and fine topics; the
  current arXiv preview exposes searchable arXiv category codes. A paper passes when its
  selected taxonomy values match; non-matching papers are GPU-culled like the other facets.
- **Date range** — the month-granularity **publication histogram is itself a draggable range
  brush**: its two handles and selected interval sit directly on the bars, rather than in a
  second slider below them. Presets (All, Last 12mo, Last 24mo, current year) and a live
  "papers in range" count remain. Runs on the GPU, so dragging is instant.
- **Reading list** — import your own library and see what you have read against the map.
  Accepts **CSL JSON** (what Zotero, Mendeley and Paperpile all export) or BibTeX; a bare
  CSL-JSON array works, and `tools/zotero_export.py` additionally preserves your collection
  names as separate toggles. Matching runs in the browser, identifier-first (arXiv id, then
  DOI, then normalised title), so nothing about your reading history leaves the machine. The
  match count is stated plainly — entries outside a CS/AI arXiv corpus are reported, not
  silently dropped. Each list becomes a chip that composes with every other facet, so the list
  view and the topic label describe your library too. The import survives a reload and is
  discarded automatically when a corpus rebuild renumbers papers.
- Filters **compose** (organization AND author AND topic AND date). "Clear" resets them.
- **Any active org/author filter hides non-matching papers completely** — they are
  GPU-culled (not drawn, not hoverable/clickable) and their citation edges are dropped, so a
  filtered view shows *only* the matching set with no dimmed backdrop. (This is unconditional;
  there is no dim-vs-hide toggle.) The same holds for a single-paper selection, which shows
  only that paper and its citation network.

## Selection, citations & related works

- **Click a paper** to open a details card: title, authors, date, venue, citation count, and
  a direct link (DOI / arXiv). The card is **resizable** — drag its left edge to widen it
  (e.g. to read a figure at full size); double-click the handle to reset. The width persists
  across selections and reloads. (Desktop only; on mobile the card is a full-width bottom
  sheet.) Author names, venue, and ids are **fetched on demand** for
  the selected paper (sharded per node); the resident index holds title, year, citations,
  and author ids for all papers. Consequence: hover cards and citation/related **list rows**
  show *title · year · citations* (author names/venue appear on the selected-paper card),
  keeping the initial download light at any corpus size.
- **First figure and table at a glance.** On the details card the paper's **Figure 1** and
  **Table 1** are shown inline when present — so you get the gist
  immediately without opening the Paper tab. There are **two sources**, tried in order:
  1. **Pipeline-baked crop (preferred).** An offline stage (s13) extracts the figure with
     **PyMuPDF** — the same caption-anchored, layout-structure method Semantic Scholar uses
     (PDFFigures 2.0): anchor on the "Figure 1:" / "Table 1:" caption, then take the
     figure box directly above a figure caption. Table captions are normally above their
     tables, so table extraction walks downward through contiguous rows and stops at the
     whitespace before body prose; it falls back above for unusual templates. It also uses
     `find_tables` (tables), `cluster_drawings` (vector
     figures — most ML diagrams), or `get_image_info` (raster), with a text-block fallback
     for borderless tables. The crop is rendered to a PNG, sharded by node id, and served
     statically — so it appears **instantly with no PDF parse in the browser**. The resident
     papers index carries a `has_figure` flag so the card fetches a crop only when one exists.
  2. **Client-side fallback (pdf.js).** For papers without a baked crop, the browser locates
     and crops both Figure 1 and Table 1 from the CORS-open arXiv PDF at runtime using the
     same direction-aware caption logic. The arXiv id is resolved **in the pipeline** (from Semantic Scholar during
     embedding, since S2 is CORS-blocked in the browser) and baked into the corpus.

  Silently hidden for non-arXiv papers or when neither crop is located. Baking
  is optional (`figures.enabled`); a bundle built without it still works via the fallback.
- **On selection, irrelevant papers are fully hidden** (culled on the GPU — not drawn, not
  hoverable), so the selected paper and its citation network are the only things on the map.
  Deselecting restores the full map. The connected papers **stay hoverable** — hovering one
  during a selection shows the same preview card (title · year · citations).
- **Relevance slider (Connected-Papers-style).** With a paper selected, a slider on the
  Citations tab **gradually filters its citation network by relevance**. The score is computed
  on selection like Connected Papers — `|shared references| + |shared citers|` (bibliographic
  coupling + co-citation) per connected paper, normalized to [0,1]. At 0 the whole network
  shows; dragging up progressively hides the least-related papers (points GPU-culled and their
  edges dropped together). Resets on each new selection.
- **Selection labels track the visible set.** Semantic-zoom labels re-place to the selected
  paper's citation network on selection (a prior bug reused the previous view's label
  positions; fixed by keying the label layer's positional accessors on the placed set).
- **Citation importance is visualized in rank order.** On both the map edges and the
  details-panel citation graph, each linked paper's **edge width, opacity, arrowhead size,
  and endpoint-ring size + outline thickness scale with its importance relative to the
  selected paper** (a blend of citation magnitude and rank position), so the strongest
  references and citers read as the boldest, largest links and rings — in-citations and
  out-citations each self-scale independently.
- The normal map view shows a deterministic, zoom-adaptive sample of **directed citation
  edges by default**; an **Edges** toggle hides or restores them.
- A dedicated **citation explorer** separates **References**, **Cited by**, and **Both**,
  with a compact directed neighborhood and searchable, clickable paper lists.
- The details panel has **Citations** and **Paper** tabs. Paper view renders the **first page
  of the arXiv PDF** via pdf.js; if no arXiv ID is stored it resolves one via Semantic
  Scholar, falling back to a TL;DR/abstract card.
- **Related works** panel lists the most similar papers using a **fused text + citation**
  similarity (semantic neighbors, direct citations, co-citations, shared references). The
  neighbor data is **fetched on demand** — sharded by node id, so selecting a paper loads
  only its shard (~540KB) rather than the whole ~9MB (→50MB at 390k) neighbor table up
  front. Shards are cached, so re-selecting nearby papers is instant.

## Search

- **Combined paper and map-label search** with type-ahead. Paper results select and focus a
  paper; map-label results pan and zoom to the named semantic region and emphasize its label.
- Paper, map-label, and author search support keyboard selection.
- **Citation arrows cost what you can see, not what exists.** The graph arrives as zoom tiers
  (an edge loads with the points that make it drawable) plus a per-paper shard on selection, so
  the home view downloads 10 KB of edges instead of 87 MB — and a selected paper's counts still
  come from its complete network, not from whatever happened to be on screen (D53).
- **The app can be deployed independently of its data.** `VITE_DATA_BASE` points the browser
  at whatever origin hosts the artifact bundle, so GitHub Pages serves the app shell and the
  1,303-file bundle lives in object storage; `tools/publish_artifacts.sh` pushes it pre-gzipped
  under an immutable versioned path (D52).
- **Any of the 10,475 institutions can be filtered without paying for all of them.** Only the
  curated browse tree's membership ships up front; a directory institution's papers are fetched
  when it is selected, one ~47 KB request (D50). This took `orgs.json` from 5.05 MB to 0.67 MB
  gzipped and the pre-paint budget from ~7.2 MB to 3.2 MB.
- **A filter's match count is never reported before it is known.** Selecting an organization or
  author whose membership is still being fetched shows a placeholder where the number goes,
  rather than a "0" that turns into 16,844 a second later (D51).
- **The hover card names the authors.** Hovering a paper on the map shows its authors under
  the title, fetched from the paper's detail shard (cached per 2,048-row block, so the rest of
  a region is free). They appear only once loaded — never as a blank line — and the card is
  never captioned with a previously hovered paper's authors.
- **A paper's year is never guessed.** Dates come from the resident papers index as soon as it
  lands rather than waiting for each paper's own tile, and a date that has not arrived shows a
  shimmer instead of an em dash, so "still downloading" cannot be misread as "no publication
  date" (D49).
- **Author search becomes usable while it is still loading.** The name index arrives as 12
  chunks (13.5 MB gzipped in total, 1.18 MB for the first) instead of one 21.2 MB file, and
  matching runs against whatever has arrived — so a name typed a second after load can already
  hit, and the dropdown says the index is still streaming when it has nothing to match yet.
- On small screens, filters open as a drawer and paper details use a bottom sheet.

## Initial download (on-demand data delivery)

The app has no backend — it downloads a static bundle — so keeping the *initial* download
small as the corpus grows is a real constraint. What loads up front vs on demand:

- **On demand** (fetched per selection, sharded by node id, cached): related-works
  neighbors, and per-paper detail (author names, venue, ids, full date).
- **Not shipped to the browser at all**: `clusters.json` (the per-region array — the
  frontend never read it; the zoom levels it carried are in the manifest).
- **Resident** (needed for all-paper interactions): point tiles by zoom, the papers index
  (title + year + citations + author_ids), the author index (names), and the citation edge
  list (adjacency drives citation LOD gating + the explorer; ~11MB at 390k, kept whole).

Net: the 72k bundle's initial load is ~22MB (was ~29MB); projected ~117MB uncompressed at
390k — roughly 40–50MB over the wire with gzip, since titles/JSON compress well. The floor
at large N is titles + author names, which must be resident for client-side search; going
beyond that would require server-side search, which the no-backend design trades away.

## Data & provenance

- The pipeline supports OpenAlex ingestion and comprehensive recent-arXiv bulk ingestion.
  The latter streams Cornell's weekly metadata snapshot, then applies resumable OAI-PMH
  upserts by arXiv id; the current ignored bundle has all 271,366 qualifying
  2025+2026 `cs.* OR stat.ML` papers through 2026-08-13 with abstracts.
- A resumable arXiv→OpenAlex enrichment stage bulk-resolves up to 100 exact identifiers per
  request and checkpoints both matches and negative route attempts. It supplements structured
  author/institution identities, affiliations, venue/identifier gaps, topics, and secondary
  citation provenance without replacing arXiv identity, text, v1 date, or categories.
- Embeddings can come from Semantic Scholar (addressed by **arXiv → DOI → MAG id**) or be
  generated locally with the official SPECTER2 proximity adapter. Local inference has durable
  row checkpoints and achieved 100% coverage on the 271k arXiv corpus.
- A top-bar summary shows corpus size, date span, and embedding backend. All data is
  pre-baked into a static bundle (`web/public/data/`) — the app runs with no backend.
- **Provider-backed citations (pipeline implemented).** `s16_apply_openalex_citations` uses
  the completed exact OpenAlex crosswalk locally: each matched paper gets OpenAlex's current
  count, while only exact corpus source→target links render in the map/explorer. A paper without
  a provider match still reads “citation count unavailable,” never “0 citations.” Details name
  the active count provider. The optional S2AG bulk job is retained for non-additive
  reconciliation. The checked-in preview remains semantic-only until the local stage and
  downstream rebuild are run.

## Not yet (planned)

- **Fetch-on-demand tiling** so corpus size stops gating the initial download. The bundle is
  currently downloaded in full on load; realizing "load only the chosen region/topic/range"
  for an unbounded (1M+) corpus requires pre-tiling papers by region/zoom and fetching only
  visible tiles. (The LOD reveal above is the visual half of this; tiling is the delivery
  half.) This also subsumes true global "no node overlap at any zoom".
- Decoupling corpus discovery from org selection so **any** organization is queryable without
  re-fetching, and deeper org nesting / temporal membership / cross-institution identity.
- Expand the completed arXiv→OpenAlex enrichment beyond its current 90.2% exact-match coverage;
  run the implemented S2AG citation stage and rebuild the static bundle; live "embed my own
  abstract" search; and a human-reviewed topic/neighborhood quality benchmark.

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
| Bulk arXiv v1-date/category normalization + OAI page parsing | `test_uses_first_version_date_not_identifier_month`, `test_normalize_includes_cross_listed_cs_category`, `test_oai_page_parses_upsert_and_resumption_token` | `pipeline/tests/test_arxiv_ingest.py` |
| arXiv-spine OpenAlex enrichment preserves canonical truth, exact IDs, affiliations, cursor paging | `test_extracts_arxiv_id_*`, `test_normalize_keeps_*`, `test_materialize_preserves_*`, `test_list_works_cursors_*` | `pipeline/tests/test_openalex_enrichment.py` |
| OpenAlex citation materialization exposes exact-match totals and only deduplicated internal arrows | `test_materialize_uses_openalex_totals_and_only_exact_internal_references` | `pipeline/tests/test_openalex_citations.py` |
| S2AG citation scan keeps external citation totals while emitting only deduplicated internal arrows | `test_stream_keeps_external_counts_*`, `test_materialize_marks_unmatched_*` | `pipeline/tests/test_s2_citations.py` |
| Local SPECTER2 row-checkpoint safety | `test_checkpoint_resumes_only_matching_corpus` | `pipeline/tests/test_specter2_local.py` |
| Figure/table extraction: direction-aware captions, prose rejection, borderless tables, PNG render | `test_finds_figure_above_caption`, `test_ignores_*`, `test_borderless_table_below_multiline_caption_stops_before_body`, `extracts a borderless Table 1 below its real caption`, +4 | `pipeline/tests/test_figure_extract.py`, `web/e2e/app.spec.ts` |
| Abstract reconstruction from inverted index | `test_basic_reconstruction`, `test_out_of_order_index`, `test_gap_in_positions_is_skipped`, `test_duplicate_words_multiple_positions`, `test_missing_and_empty`, `test_embed_text_composition` | `pipeline/tests/test_abstract.py` |
| Fused text + citation related-works ranking | `test_reference_sets_preserve_citation_direction`, `test_citation_candidates_include_direct_coupling_and_co_citation`, `test_fused_ranking_can_introduce_a_non_text_citation_candidate` | `pipeline/tests/test_fused_similarity.py` |
| Org drill-down: dept/lab attribution, no cross-org leakage | `test_fair_is_separated_from_generic_meta`, `test_facebook_ai_without_research_is_meta_ai_not_fair`, `test_no_cross_org_leakage`, `test_cmu_specific_unit_wins_over_school`, +6 more | `pipeline/tests/test_directory.py` |
| Org affiliation evidence scoped per authorship | `test_org_affiliation_evidence_is_scoped_per_authorship`, `test_org_affiliation_evidence_empty_without_map` | `pipeline/tests/test_corpus.py` |
| Curated roots + full directory split; no duplication | `test_curated_root_and_directory_split`, `test_curated_institution_not_duplicated_in_directory`, `test_directory_entry_falls_back_to_id_without_registry` | `pipeline/tests/test_org_index.py` |
| Curated neolab roster: exact author join, temporal bounds, multi-org claims, provenance | `test_roster_exact_join_preserves_evidence_and_date_bounds`, `test_one_author_may_have_reviewed_claims_at_multiple_orgs`, `test_roster_backed_neolab_is_a_curated_root_with_provenance` | `pipeline/tests/test_rosters.py`, `pipeline/tests/test_org_index.py` |
| Frozen projector keeps new points in the same map space | `test_frozen_projector_reuses_fit_normalization_for_new_points`, `test_frozen_projector_rejects_non_2d_fit_coordinates` | `pipeline/tests/test_project.py` |
| Reveal-level thinning: total coverage, overlap-free per zoom, importance-ordered, deterministic | `test_cumulative_levels_are_overlap_free`, `test_every_paper_gets_a_level`, `test_most_important_papers_reveal_first`, `test_deterministic`, `test_ties_broken_by_index_not_random`, +2 | `pipeline/tests/test_thinning.py` |
| Map loads, corpus summary, filled canvas | `loads the map, corpus summary, and a filled canvas` | `web/e2e/app.spec.ts` |
| Title search → select → details | `title search selects a paper and opens details` | `web/e2e/app.spec.ts` |
| Map-label search → semantic-region navigation | `label search navigates to a named semantic region` | `web/e2e/app.spec.ts` |
| Missing citation metadata is never rendered as zero | `missing citation metadata is not presented as zero citations` | `web/e2e/app.spec.ts` |
| Hover preview tooltip | `hovering a point shows a preview tooltip` | `web/e2e/app.spec.ts` |
| Organized recursive org drill-down + roster-backed neolabs + scoped researchers + directory search | `shows roster-backed neolabs with provenance and filters their papers`, `expands a university into evidence-backed departments/labs`, `selecting a lab reveals org-scoped researchers`, `org search surfaces a child unit and its parent`, `org search finds a non-curated corpus institution` | `web/e2e/app.spec.ts` |
| Date presets + draggable histogram range update in-range count | `a preset narrows the corpus and updates the in-range count`, `dragging the publication histogram narrows the range` | `web/e2e/app.spec.ts` |
| Author search → filter, chunked index, resolved OpenAlex profile | `selecting an author filters the map and names them in the filter bar` | `web/e2e/app.spec.ts` |
| Reading-list import: parse, match by id/DOI/title, filter, report non-matches | `importing a reading list filters the map to those papers` | `web/e2e/app.spec.ts` |
| Graceful bundle-missing error | `shows a clear error when the bundle is missing (route-mocked 404)` | `web/e2e/app.spec.ts` |

### Coverage gaps (features without a dedicated automated test)

These are shipped but currently guarded only by manual/visual verification. Adding tests
here is welcome; **removing the feature should still update this file.**

- **Clicking a point on the map canvas** — the test exists (`clicking a point on the map canvas
  selects that paper`) but ships as `test.skip` on this machine. Without a GPU, deck.gl
  rasterises ~900k points through SwiftShader on the main thread, and a canvas click is
  move+down+up with each step waiting on that thread: one `page.mouse.click` did not return
  inside 540 s, even with an author filter leaving 7 visible points. Hover — one event — does
  complete, and the hover test exercises the same deck.gl picking path. Un-skip on a GPU box
  (D33).

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
