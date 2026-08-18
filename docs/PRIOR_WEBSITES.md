# Prior websites — similar apps and what Research Atlas takes from them

Live products/projects in the same space as Research Atlas: visualizing, relating, and
organizing academic papers or researchers. Each entry notes what it does, roughly how it
works, and where Research Atlas follows or diverges. For the deeper literature survey behind
the pipeline choices (embeddings, layout, semantic zoom, org attribution) see
`RESEARCH_PRIOR_WORK.md`; this file is specifically about *comparable end-user apps*.

Where an implementation is public, a shallow local clone is kept under the gitignored
`.prior-work-repos/` directory for source-level inspection. Repository contents are reference
material under their upstream licenses, not vendored dependencies of Research Atlas. The
local `README.md` in that directory records the exact checked revisions.

### Local source checkout inventory

These shallow clones were checked on 2026-08-14. A missing root license means **inspect only**
until upstream licensing is clarified; it does not imply permission to copy or redistribute.

| Local directory | Upstream repository | Checked commit | License observed locally |
| --- | --- | --- | --- |
| `csrankings` | [emeryberger/CSRankings](https://github.com/emeryberger/CSRankings) | `1e0df689858a` | CC BY-NC-ND 4.0 for the project; its README separately identifies DBLP-derived data as ODC-By |
| `arxiv-bib-overlay` | [mattbierbaum/arxiv-bib-overlay](https://github.com/mattbierbaum/arxiv-bib-overlay) | `d17c50a58a8e` | MIT |
| `arxiv-browse` | [arXiv/arxiv-browse](https://github.com/arXiv/arxiv-browse) | `203a5a94d797` | MIT |
| `arxiv-readability-archived` | [arXiv/zzzArchived_arxiv-readability](https://github.com/arXiv/zzzArchived_arxiv-readability) | `20dac4540aaf` | MIT-style license; archived upstream |
| `ar5iv` | [dginev/ar5iv](https://github.com/dginev/ar5iv) | `5b8cc36b9e8c` | MIT |
| `ar5iv-css` | [dginev/ar5iv-css](https://github.com/dginev/ar5iv-css) | `86db59f5c31a` | MIT |
| `core-eprints-recommender` | [oacore/eprints-recommender](https://github.com/oacore/eprints-recommender) | `098b265e178d` | No root license found—reference only |
| `core-recommender-bundle` | [oacore/recommender-bundle](https://github.com/oacore/recommender-bundle) | `82bb40b01b20` | MIT |
| `paperswithcode-data` | [paperswithcode/paperswithcode-data](https://github.com/paperswithcode/paperswithcode-data) | `58acefc0c9fe` | README declares data CC BY-SA 4.0 |
| `paperswithcode-extractor` | [paperswithcode/sota-extractor](https://github.com/paperswithcode/sota-extractor) | `e6374da129ce` | Apache-2.0 code; README declares included data CC BY-SA 4.0 |
| `huggingface-hub` | [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) | `72b5d66c38a8` | Apache-2.0 |
| `replicate-cog` | [replicate/cog](https://github.com/replicate/cog) | `79dce1d3ca62` | Apache-2.0 |
| `scite-zotero-plugin` | [scitedotai/scite-zotero-plugin](https://github.com/scitedotai/scite-zotero-plugin) | `0dc5ce377e2f` | No root license found—reference only |
| `influencemap` | [csmetrics/influencemap](https://github.com/csmetrics/influencemap) | `6ae3191a7253` | No root license found—reference only |
| `dagshub-papers-with-everything` | [DagsHub/papers-with-everything](https://github.com/DagsHub/papers-with-everything) | `75f4eaf532c2` | No root license found—reference only |

---

## CSRankings — https://github.com/emeryberger/CSRankings

**What it is.** A metrics-based ranking of CS institutions (and faculty) by publication
volume in top, area-specific venues. Not a map — a set of ranked tables you filter by CS
area (AI, Systems, Theory, …) and region.

**How it works.** Two halves — a curated roster and a deliberately citation-free scoring
formula:

1. **Roster.** A **manually curated** faculty→institution mapping. Researchers are added
   through a self-service submission form (with a batch mode for a whole department) and
   matched to their **DBLP** author entry; moves/retirements are tracked (an affiliation
   update when someone changes institutions, a `Remove` that records the industry destination,
   with old entries archived rather than deleted). This roster — not any text-parsing — is the
   source of truth for who belongs to which institution, refreshed quarterly as
   `csrankings-[a-z].csv`.
2. **Scoring (from the CSRankings FAQ).** For each faculty member it counts publications in a
   fixed list of **top, area-specific conferences** (selective venues chosen as a proxy for
   impact, so *no citation counts are used at all* — the FAQ notes citation data "change
   rapidly … and can be gamed"). Papers must be ≥6 pages. Credit is **fractionally split**:
   a paper with N authors gives each author `1/N` ("adjusted count", so each paper is counted
   exactly once total). An institution's score per area is the sum of its faculty's adjusted
   counts; its overall score is the **geometric mean across areas** —
   `averageCount = (∏(adjustedCountᵢ + 1))^(1/n)` — which normalizes for differing area sizes
   and publication rates. There is **no embedding, no clustering, no map**; "relatedness" is
   only the venue + ACM-SIG area taxonomy.

**Relevance to Research Atlas.** This is the strongest precedent for the **author-roster
approach to organization membership** we've been designing for neolabs: CSRankings proves
that *curated person→org rosters joined against a publication index* is the state-of-practice
way to attribute papers to institutions, precisely because affiliation strings are
unreliable. We borrow the pattern (roster keyed on author id) but diverge on curation source
— CSRankings types faculty by hand; we seed rosters from an org's own papers' OpenAlex author
ids and registry signals. **Licensing caveat:** the project is **CC BY-NC-ND 4.0**
(NonCommercial + NoDerivatives), so its rosters can be treated as *reference claims* but not
redistributed/transformed into our bundle; only the DBLP layer beneath is ODC-BY.

---

## Connected Papers — https://www.connectedpapers.com/about

**What it is.** A visual tool that, from a single seed paper, builds a **graph of the most
related prior and derivative work** — the canonical "explore around one paper" experience.
Self-described as "a visual tool to help researchers … find and explore papers relevant to
their field of work."

**How it works.** The pipeline, per their published method, is:

1. **Candidate + similarity.** Starting from the seed, it scans the surrounding citation
   neighborhood (over a Semantic Scholar / arXiv-scale corpus) and scores every candidate by a
   **similarity metric based on co-citation and bibliographic coupling** — two papers are
   similar if they *share references* (coupling) and if they are *cited together* by later
   papers (co-citation). Crucially this is **not** the literal citation edges among the shown
   papers: a strongly-connected pair "does not necessarily cite each other," which is what lets
   the graph surface foundational or parallel work that never directly cite-links.
2. **Selection.** It keeps the few dozen most-similar papers to the seed (not the whole
   neighborhood) — a bounded local set, recomputed fresh for each seed.
3. **Layout.** Those papers are placed with a **force-directed graph layout** where
   **proximity encodes similarity** (similar papers pull together, dissimilar repel) and
   **node size encodes citation count, color encodes publication year** — so influential and
   recent papers read at a glance. Edges are drawn between the most-similar pairs.

There is no persistent global corpus and no topic hierarchy; each graph is a small,
on-demand computation around one seed.

**Relevance to Research Atlas.** Connected Papers is the closest analog to our **selection +
related-works** experience: select a paper, see its neighborhood, follow the graph. Two
deliberate divergences: (1) Connected Papers is **per-seed and ephemeral** — it computes a
fresh local graph on demand and has no global map; Research Atlas renders **one persistent
global map** of the whole corpus and treats a selection as a *filter* into it. (2) Its
relatedness is citation-coupling only; ours is a **fused text (SPECTER2) + citation**
similarity, so neighbors are found even with sparse citation data. The graphs align with the
`RESEARCH_PRIOR_WORK.md` finding that **citation+content hybrids beat pure-citation**
clustering.

---

## Litmaps — https://www.litmaps.com/

**What it is.** A collaborative literature-review workspace that grows an editable paper
collection from one or more seed papers. It combines citation-network discovery, a visual
map, annotations/tags, bibliography import/export, sharing, and recurring alerts for newly
published matches. It is closer to a **saved research project** than Connected Papers' one-off
seed graph.

**How it works.** Litmaps separates the papers a user has accepted into a map from the
candidate papers it recommends:

1. **Seed and iterate.** Start from papers found by keyword, author, DOI, PubMed ID, or arXiv
   ID, or import an existing PDF/reference-manager collection. Recommendations remain
   visually distinct until the user saves them. Adding useful candidates changes the input
   set, and refreshing reruns discovery around the larger collection.
2. **Choose a retrieval signal.** The default **Shared Citations & References** method ranks
   papers connected to the input set through references, citations, and co-citations. Two
   alternatives search for common author collaborations or semantically similar title and
   abstract text. The text option deliberately reaches relevant but uncited/disconnected
   work, while citation search supplies explainable paths back to the seeds.
3. **Use an analytical rather than embedding-defined layout.** Nodes are papers and lines are
   citation/reference links, but the user chooses map axes from publication date, citation
   count, reference count, recency-adjusted citation **Momentum**, and within-map citation
   **Connectivity**. Standard, ring, side-by-side, and author-grouped views distinguish
   accepted inputs from suggestions. Saved papers can also be manually positioned, labeled,
   recolored, annotated, and have citation edges corrected.
4. **Persist the review.** Tags organize topics; maps can be shared; Zotero and common
   bibliography formats can be imported/exported; and a monitor reruns the discovery query
   as the literature changes. This turns graph exploration into a repeatable research-review
   workflow rather than a visualization endpoint.
5. **Merge several open metadata indexes.** Litmaps documents a 270M+ paper catalogue updated
   weekly from **Crossref, Semantic Scholar, and OpenAlex**. It attempts to consolidate
   preprint/published versions and selects the most recent version with the best metadata.
   Litmaps explicitly warns that missing open metadata, provider lag, unresolved references,
   and imperfect version deduplication still cause missing papers or citation edges. The
   aggregate Litmaps catalogue is a product feature, not advertised as a bulk dataset for
   downstream replication.

**Relevance to Research Atlas.** The strongest ideas to borrow are the explicit distinction
between **saved/accepted papers and candidates**, multi-seed iterative discovery, explainable
citation paths beside semantic recommendations, editable collections, and monitors for new
papers. Its Momentum and within-map Connectivity measures are useful paper-detail or sorting
signals, but should not become spatial axes in our primary view: Research Atlas uses a stable
global semantic layout so distance retains the same meaning across searches and users. Its
three-provider merge also reinforces our arXiv-spine/OpenAlex-supplement design while showing
why source provenance, version aliases, and citation freshness must remain visible.

---

## arXiv Bibliographic Explorer and its providers — https://info.arxiv.org/labs/showcase.html#arxiv-bibliographic-explorer

**What it is.** An arXiv Labs experiment that inserts references and forward citations into
an arXiv abstract page, making a paper's first-hop citation tree navigable without leaving
arXiv. The [MIT-licensed implementation](https://github.com/mattbierbaum/arxiv-bib-overlay)
is especially useful as a catalogue of bibliographic providers and as a precedent for
showing **both directions with source attribution**.

**How it works (historical implementation).** The TypeScript/React overlay extracts the
arXiv ID, lazily queries a selected third-party provider in the browser, translates each
provider's response into one common paper/reference/citation model, and renders side-by-side
lists. Results can be sorted, paged, filtered, followed to arXiv/DOI/journal/provider pages,
or exported as formatted citations. Provider and enabled/disabled preferences are persisted;
requests pass through a small client-side rate limiter; domain-specific providers are only
offered for matching arXiv categories. The design notes explicitly call out provider load,
page performance, citation-metric harms, and the reading-privacy leak created by direct
browser calls to third parties.

The repository is a **design reference, not a current ingestion implementation**: its latest
commit is from 2019, it uses obsolete endpoint versions, and one implemented adapter is
disabled. The checked-in source names these providers:

| Provider | Explorer role and coverage | Research Atlas assessment |
| --- | --- | --- |
| [Semantic Scholar](https://www.semanticscholar.org/product/api) | General-purpose references and citations for all categories; the old overlay also exposed its influential-citation flag. The current S2 Academic Graph offers paper/reference/citation APIs and downloadable snapshot datasets. | The only provider in this list that is both cross-disciplinary and architecturally capable of supplementing our entire CS corpus in bulk. Keep it as an optional citation-edge source once dataset/API access is available, with source-specific freshness and licensing recorded. |
| [Prophy](https://www.prophy.ai/) | General-purpose paper, author, reference, and citation data; enabled in the checked-in overlay configuration. Its current product advertises a large cross-disciplinary graph and commercial integrations. | Useful evidence that another broad graph exists, but public bulk access, licensing, reproducibility, and pricing are not documented clearly enough to make it a pipeline dependency without vendor confirmation. |
| [NASA ADS](https://ui.adsabs.harvard.edu/help/api/) | References, forward citations, metrics, and text-similar papers, restricted by the overlay to `astro-ph`, `cond-mat`, and `gr-qc`. ADS is authoritative in astronomy/astrophysics and related physics, but explicitly less complete outside its curated core. | Excellent validation or fallback for relevant physics categories; not a source for comprehensive CS/AI citation edges. |
| [INSPIRE HEP](https://inspirehep.net/) | References, citing papers, citation summaries, and self-citation-aware metrics for high-energy physics and closely related categories. The adapter exists but is disabled in the repository's active provider list. | High-quality specialist source, but INSPIRE says papers and citations outside HEP are usually not covered. It is irrelevant to most of our CS spine except cross-listed physics papers. |
| arXiv API + Crossref | Used by the overlay to fetch canonical metadata and format/export a citation by arXiv ID or DOI, rather than to supply the forward/backward citation graph. | Continue using identifiers and canonical metadata for matching/version linkage; do not mistake citation formatting for citation-edge coverage. |

**Relevance to Research Atlas.** Borrow the normalized provider-adapter boundary, explicit
provider selector/provenance, two-column incoming/outgoing evidence view, category-aware
fallbacks, deferred loading, and honest empty/incomplete states. Do **not** reproduce its
per-page fan-out architecture for millions of papers. Bulk snapshots should populate our
local edge table, then provider APIs should patch only recent or missing records. This also
means NASA ADS and INSPIRE are targeted validators, not alternatives to a global OpenAlex or
Semantic Scholar graph.

---

## Complete arXiv Labs showcase review — https://info.arxiv.org/labs/showcase.html

The showcase is broader than Bibliographic Explorer and its providers. It lists thirteen
separate projects spanning discovery, readable papers, code/data, runnable demos, media, and
reproducibility. This index records **every project listed on the showcase page reviewed on
2026-08-14**; projects with a full entry elsewhere in this document link to that analysis.

| Project | What it does and how it works | Relevance to Research Atlas |
| --- | --- | --- |
| [arXiv Bibliographic Explorer](https://github.com/mattbierbaum/arxiv-bib-overlay) | A lazy browser overlay normalizing references and forward citations from selectable external providers into one two-column interface. See the detailed entry above. | Use its provider-adapter boundary, direction labels, provenance, and evidence lists—not its per-page API fan-out. |
| [ar5iv](https://github.com/dginev/ar5iv) | Converts arXiv LaTeX sources to semantic HTML5 with LaTeXML, including MathML, making papers responsive, accessible, and machine-readable. The original arXiv Readability pilot repository was archived in 2024; ar5iv and arXiv's native HTML Papers work carry the idea forward. | HTML is a substantially better source than PDF for section-aware abstract/full-text extraction, reference parsing, figure/table association, accessibility, and deep links. Prefer official arXiv HTML when present, then ar5iv, then PDF parsing—with conversion provenance and failure status retained. |
| [CORE Recommender](https://core.ac.uk/services/recommender) | A plugin for repositories and article pages that recommends freely readable papers from CORE's aggregation of institutional repositories, preprint servers, and journals. It accepts an identifier and/or metadata/full text, uses content-based vector similarity plus metadata signals, returns accessible candidates, and learns from explicit user feedback. CORE also offers APIs and bulk full-text/metadata datasets. | A useful open-access full-text supplement and precedent for cold-start recommendations. Evaluate CORE coverage against our arXiv spine before ingestion; do not conflate its repository records with canonical paper versions. |
| [arXiv Links to Code & Data / Papers with Code](https://github.com/paperswithcode/paperswithcode-data) | Adds paper→code/data/results links sourced from Papers with Code. Its downloadable CC-BY-SA data includes papers/abstracts, code links, evaluation tables, methods, and datasets and is documented as regenerated daily. | Strong candidate for a separate `artifacts` relation keyed by arXiv ID/DOI. Code, dataset, task, benchmark, and result links should enrich a paper detail panel, not affect the citation count. Preserve artifact source, license, and last-seen timestamp. |
| [Connected Papers](https://www.connectedpapers.com/about) | Builds a bounded seed graph using co-citation and bibliographic coupling; proximity represents similarity rather than literal citations. See the detailed entry above. | Retain as the main precedent for an explainable local related-work lens over our persistent global map. |
| [Litmaps](https://www.litmaps.com/) | Grows a saved multi-seed literature review through citation/reference/co-citation, common-author, or similar-text discovery; supports editable maps, monitoring, and collaboration. See the detailed entry above. | Borrow accepted-versus-candidate state, iterative discovery, and alerts; keep semantic geography stable rather than remapping onto user-selected analytical axes. |
| [Hugging Face Spaces](https://huggingface.co/docs/hub/spaces) | Hosts runnable Gradio, Docker, or static-HTML demos in Git-backed repositories that rebuild on commit. Hub metadata and parsed links connect Spaces, models, and datasets to paper pages; arXiv exposes associated demos directly from the abstract page. | Add typed paper→model/dataset/demo links and show author-owned versus community artifacts. A runnable demo is valuable evidence of usability, but not evidence that results reproduce or that the artifact is maintained. |
| [IArxiv](https://arxiv.org/abs/2002.02460) | Ranks each day's arXiv feed for an individual. Its published method fits LDA topics over a field, represents papers and users as topic-mixture vectors, initializes preferences from the user's prior papers, and updates them from opened papers; ranking is the user–paper vector similarity. | Precedent for personalized feeds and recurring alerts, but not for our global layout: per-user relevance belongs in ranking/highlighting. Modern paper embeddings can replace LDA while preserving an inspectable preference profile and explicit opt-in interaction tracking. |
| [Scite Smart Citations](https://scite.ai/) | Extracts the in-text citation statement, surrounding context, and paper section, then classifies each edge as **supporting**, **contrasting**, or **mentioning** with a learned model. Its API can retrieve DOI-level citing/cited edges and classifications, but access is restricted and coverage depends on resolvable references/full text. | The best precedent for an evidence-rich edge detail panel. Treat citation intent as uncertain enrichment with model/confidence/provenance—not as ground truth or as the base edge source. It requires full text and is therefore a later pipeline layer. |
| [ScienceCast](https://www.sciencecast.org/) | Links arXiv/bioRxiv papers to short author-uploaded video presentations and currently generates brief audio summaries or editable presentation slides from a preprint link. Casts form a category-browsable media stream around the manuscript. | Model media as typed external artifacts. Author-created and AI-generated media need distinct labels; neither should be mixed into semantic similarity without evaluation. |
| [Replicate](https://replicate.com/docs/arxiv) | Lets an author package and publish an executable ML model, associate its model page with a paper URL, and expose a generated web form and cloud API for predictions. Models are versioned deployable programs, commonly containerized with Cog, rather than merely source-code links. | Another paper→executable-demo source. Record the exact model/version, code and paper URLs, license, hardware/cost, and last successful health check so a transient hosted endpoint is not presented as permanently reproducible. |
| [Influence Flower](https://influencemap.cmlab.dev/) | Aggregates bidirectional paper citation flow into paper/author/institution/venue/topic petals. See the detailed entry below. | Borrow the organization/researcher citation lens, anchored time comparisons, self-citation controls, and evidence drill-down. |
| [DagsHub](https://dagshub.com/blog/dagshub-integration-with-arxiv/) | Links an arXiv paper to a reproducible data-science repository. Git versions code, DVC/Data Engine versions data, and MLflow binds experiment parameters, metrics, artifacts, and the exact commit; the arXiv integration adds reciprocal paper/project links. | Richer reproducibility evidence than a GitHub URL alone. Store paper→project links plus versioned data/model/experiment metadata when available, while distinguishing an author implementation from an independent reproduction. |

**Cross-project pattern.** arXiv remains the canonical paper/identifier surface while Labs
integrations attach independently maintained capabilities around it. For Research Atlas this
suggests a typed, provenance-bearing artifact layer—`citation`, `recommendation`, `code`,
`dataset`, `model`, `demo`, `media`, and `reproduction`—instead of forcing every relationship
into the paper similarity or citation graph. Integrations must fail independently so a stale
demo or unavailable vendor API never prevents the core arXiv paper from loading.

---

## alphaXiv Researcher Map — https://www.alphaxiv.org/researchers/map

**What it is.** An **interactive field map of researchers** (not papers): "Explore the
researchers on alphaXiv on an interactive field map — drag to pan, scroll to zoom, hover for
details." Same Google-Maps interaction grammar as our tool, but the *entities plotted are
people*, positioned by research field.

**How it works (observed — mechanism not published).** The interaction is the same
Google-Maps grammar as ours: a single pannable/zoomable 2D canvas (drag to pan, scroll to
zoom, hover a point for a detail popover). Each point is a **researcher**, and points are
positioned so that researchers working in similar fields sit near each other — i.e. a
2D similarity/embedding layout, but over *authors* rather than papers. The natural way to
build such a layout (and what the "field map" framing implies) is to embed each researcher
from their body of work — e.g. aggregate the embeddings of their papers into an author
vector, then project to 2D — but alphaXiv does not publish the embedding model or projection,
so this is inference, not a sourced claim. Context: alphaXiv is a paper-discussion/annotation
platform layered on arXiv, so the map is a discovery surface over its researcher profiles
rather than a standalone product.

**Relevance to Research Atlas.** It validates the **pan/zoom/hover map UX for scholarly
discovery** and shows the *author-as-node* framing as an alternative axis to our
*paper-as-node* map. Our design keeps papers as the primary nodes (with authors/orgs as
*filters* over that map) because the semantic-zoom topic hierarchy — fields → topics →
micro-clusters — is defined over papers, not people. alphaXiv's researcher map is a useful
reference for a possible future "researcher/org lens" view layered on the same substrate.

---

## Influence Map / Influence Flower — https://influencemap.cmlab.dev/

**What it is.** An interactive, **ego-centric view of bidirectional citation influence**.
The selected entity sits at the centre of a flower and its strongest influencing/influenced
entities form the petals. Unlike a conventional paper citation graph, either side can be a
paper collection, author, venue, institution, or topic. The project was introduced in the
VAST 2019 paper [*Influence Flowers of Academic
Entities*](https://arxiv.org/abs/1907.12748); the current public system and
[repository](https://github.com/csmetrics/influencemap) have migrated from Microsoft Academic
Graph to the OpenAlex 2025-05-30 snapshot.

**How it works.** It converts paper-level citation edges into an aggregate, typed first-hop
influence profile:

1. **Curate the centre.** Search can select one or several papers, authors, institutions,
   conferences, or journals. Every entity is reduced to its associated set of papers: an
   author is all their papers, a venue is its proceedings/articles, and an ad-hoc project is
   a user-curated paper set. Multiple search results can be merged and renamed to repair
   split identities or construct a lab/project that the source graph does not model directly.
2. **Aggregate citation flow.** If paper B cites paper A, influence flows from A to B — the
   reverse of the citation arrow. For centre-paper indicator/association matrix `A`, citation
   matrix `C`, and an outer entity type, the paper describes the aggregate as
   `S = A C Aᵀ`. A **blue** petal means the outer entity influenced the centre (the centre
   cited it); a **red** petal means the centre influenced the outer entity (it cited the
   centre). Scores are divided across the entities attached to the **cited** paper so one
   citation remains roughly one unit of influence instead of being multiplied by a large
   author/topic/institution list. The choice was selected empirically over eight candidate
   normalisations, not presented as ground truth.
3. **Select and encode petals.** The default flower keeps 25 outer entities (maximum 50),
   selected by the larger of their incoming and outgoing scores so a strong one-way relation
   is not hidden by a combined ranking. Edge width is log-scaled influence strength; node size
   is total two-way influence; node colour is the blue↔red direction ratio. Petals can be
   sorted by direction ratio, influence into the centre, influence out of the centre, or total
   influence. Self-citations can be excluded, and co-contributors are marked/filtered to
   expose influence beyond an author's collaborators or an institution's own papers.
4. **Compare time without layout drift.** A full-period **anchor flower** fixes alter
   selection, order, position, and maximum sizes in grey. A selected sub-period is overlaid in
   colour relative to that anchor, so changes reflect citation flow rather than a newly
   computed layout. Clicking through to details exposes the papers behind an aggregate edge;
   a curated gallery provides a browse-first entry point alongside search.
5. **Serve a precomputed graph.** The current build converts the OpenAlex snapshot into
   MAG-shaped paper/entity/reference tables, then builds compact bidirectional binary
   adjacency indexes (entity↔paper and paper↔citing/cited paper). The documented full build
   is about 88 GB. A dedicated scoring service walks those indexes for a requested flower,
   while OpenSearch handles typeahead/entity names and paper-title lookup. This replaces the
   original paper's Elasticsearch-heavy relational plan, where an uncached venue example
   required tens of thousands of queries; the core scalability lesson is still to materialise
   adjacency once and aggregate locally rather than fan out to a bibliographic API per click.

**Relevance to Research Atlas.** Influence Map is the strongest precedent for an
**organization/researcher citation lens** over our paper graph. We should borrow its explicit
two-way semantics, evidence drill-down, self-citation/co-contributor controls, ability to
define an ego as a curated paper set, and stable anchor when a date filter changes. This would
fit as a selected paper/author/org panel or overlay: for a lab, show which topics and groups it
builds on versus which ones build on it. It should **not** replace the main map. Influence Map
is citation-only, first-hop, and deliberately omits alter↔alter edges; Research Atlas is a
persistent global semantic space whose proximity also works for uncited new papers. Its
current site also warns that OpenAlex snapshot lag and entity-resolution errors can leave
papers/authors missing, so its reported influence is only as complete as the underlying
citation graph — the same provenance issue our citation pipeline must surface.

---

## Cross-cutting takeaways for Research Atlas

- **Two organizing axes exist in the wild** — by *paper* (Connected Papers, us) and by
  *researcher* (alphaXiv). We commit to paper-as-node because semantic zoom is a
  paper-topic hierarchy; author/org are filters, not the substrate.
- **Roster-based org attribution is the proven path** (CSRankings), not text parsing —
  directly supporting the neolab author-roster design over document/affiliation-string
  extraction.
- **Global map vs. per-seed graph** is the main UX fork: Connected Papers recomputes a local
  graph per seed; we render one persistent corpus map and make selection a filter, which is
  what makes cross-topic browsing and semantic zoom possible.
- **Discovery is iterative, not one-shot:** Litmaps shows the value of keeping a user's
  accepted paper set separate from candidates, rerunning hybrid discovery as that set grows,
  and monitoring it for new work.
- **Citation providers are complementary, not interchangeable:** the Bibliographic Explorer
  routes by domain because ADS and INSPIRE can be excellent within their curated fields but
  cannot cover a CS-wide corpus; global bulk data and specialist validation are separate
  pipeline roles.
- **Paper-adjacent artifacts need their own typed graph:** the full arXiv Labs catalogue links
  papers to code, datasets, models, demos, media, and reproductions. These relations should
  carry provenance and lifecycle state rather than masquerading as citations or semantic
  similarity.
- **A local graph can aggregate papers into typed entities:** Influence Map shows that the
  same citation edges can answer author↔author, institution↔topic, venue↔author, or curated
  project questions without replacing papers as the canonical graph nodes.
- **Direction and time need stable visual semantics:** separate “influenced by” from
  “influencing,” expose the papers behind every aggregate, and keep positions/scales anchored
  when comparing date windows so recomputation is not mistaken for change.
- **Similarity signal:** hybrid text+citation (ours) generalizes past pure co-citation
  (Connected Papers) when citation data is sparse — consistent with the survey in
  `RESEARCH_PRIOR_WORK.md`.

## COMET / Telescope (cometadata.org) — surveyed 2026-08-17

**What it is.** COMET ("Richer Metadata. Together.") is a metadata-enrichment initiative that
produces *open* scholarly metadata — author affiliations, funding entities, software repository
links, preprint↔published matching — and gives the outputs away (datasets CC0, code MIT).
**Telescope** (`cometadata/telescope-ui` + `telescope-index`) is their frontend for exploring
arXiv works enriched with that data, indexed into Typesense.

**Why it matters here.** It is the closest thing to a sibling project: same corpus (arXiv), same
instinct that the interesting product is the *enrichment layer* rather than the papers. It differs
in the primary axis — Telescope is search/index-first over enriched metadata, Research Atlas is a
spatial semantic map. Their enrichment outputs are directly ingestible by us (see
`RESEARCH_PRIOR_WORK.md` §2Z: their arXiv author-affiliation dataset closes our task #9).

**Mechanism worth borrowing.** Their affiliation pipeline — dots.ocr for PDF→markdown, off-policy
distillation from a frontier teacher with rejection sampling against hand annotations, curriculum
ordering by student surprisal, an 8B LoRA student doing the volume inference, then a separate
string→ROR linking step. Full write-up in `RESEARCH_PRIOR_WORK.md` §2Z.

**Licensing.** Datasets CC0; repos MIT. Safe to ingest and to build on, with attribution.

**Upstream.** <https://github.com/cometadata> · <https://www.cometadata.org/> ·
<https://huggingface.co/cometadata>
