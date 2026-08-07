# Prior websites — similar apps and what Research Atlas takes from them

Live products/projects in the same space as Research Atlas: visualizing, relating, and
organizing academic papers or researchers. Each entry notes what it does, roughly how it
works, and where Research Atlas follows or diverges. For the deeper literature survey behind
the pipeline choices (embeddings, layout, semantic zoom, org attribution) see
`RESEARCH_PRIOR_WORK.md`; this file is specifically about *comparable end-user apps*.

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
- **Similarity signal:** hybrid text+citation (ours) generalizes past pure co-citation
  (Connected Papers) when citation data is sparse — consistent with the survey in
  `RESEARCH_PRIOR_WORK.md`.
