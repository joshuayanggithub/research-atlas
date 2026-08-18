# Prior work — embedding/organizing papers & categorizing research by org

Findings from a deep-research pass (2026-07) surveying prior work behind the two hardest
parts of Research Atlas: (1) embedding papers and laying them out with a multi-scale
semantic-zoom topic hierarchy, and (2) categorizing works by organization / lab / author.
It pairs a state-of-the-art survey with concrete, adopt-now recommendations for *this*
pipeline. Companion to `Design.md` (why the current choices) and `ROADMAP.md` (what to
build next); the org-directory target is in `ORGANIZATION_DIRECTORY.md`.

## How to read confidence

- **[VERIFIED]** — survived 3-vote adversarial verification against a primary source
  (each claim independently checked by three skeptical verifiers; killed only if ≥2 refute).
- **[SOURCED]** — extracted verbatim from an authoritative primary source (official docs or
  a peer-reviewed / arXiv paper) and confirmed by the same 3-vote pass.
- **[REFUTED]** — failed verification; recorded so we don't repeat it.

Method: 5 search angles → 25 primary sources → 117 candidate claims → adversarial
verification. Topic 1 and Topic 2 were verified in two passes. Where a recommendation is our
own inference rather than a directly sourced fact, it says so.

---

## TOPIC 1 — Embedding, layout, and semantic zoom

### 1.1 Scientific document embeddings — SPECTER2 is well-justified

- **[VERIFIED] The SPECTER → SPECTER2 lineage is the domain-appropriate, citation-aware
  choice, and it embeds from title+abstract alone at inference.** SPECTER (Cohan et al.,
  ACL 2020, [arXiv:2004.07180](https://arxiv.org/abs/2004.07180)) pretrains on the citation
  graph as a relatedness signal but needs **no citations at inference** — important because
  Research Atlas ingests papers with and without citation data. SPECTER2 / SciRepEval (Singh
  et al., EMNLP 2023, [arXiv:2211.13308](https://arxiv.org/abs/2211.13308)) found SPECTER and
  SciNCL fail to generalize across task *formats*, and fixes this with task-specific
  **adapters** (proximity, retrieval, classification, regression), beating the
  single-embedding SOTA by ~2 points.
  - **Adopt-now:** for a similarity-layout map, the **"proximity" adapter is the correct
    one** — which is what the pipeline's `specter_v2` Semantic Scholar fetch returns. Keep it.
- **[VERIFIED] SciNCL is the one alternative worth A/B-testing.** (Ostendorff et al., EMNLP
  2022, [arXiv:2202.06671](https://arxiv.org/abs/2202.06671)) Samples *continuous* nearest
  neighbors over citation-graph embeddings instead of discrete citation links; beats SPECTER
  on 9/12 SciDocs metrics (~81.8 vs ~80.0 avg). Drop-in title+abstract embedder. **Caveat
  (our own, from `HANDOFF.md` gotcha #6):** never *mix* SPECTER2 and SciNCL vectors in one
  space — evaluate as a full replacement, not a fill.
- **[VERIFIED] Do not swap to a general-purpose embedder (E5/GTE/BGE) without task-matched
  evaluation.** MTEB (Muennighoff et al., EACL 2023,
  [arXiv:2210.07316](https://arxiv.org/abs/2210.07316)) states verbatim that *"no particular
  text embedding method dominates across all tasks."* A top-retrieval model is not
  automatically a top-**clustering/layout** model — which is our actual objective. Any swap
  must be evaluated on 2D-layout / cluster-coherence, not a generic leaderboard rank.

### 1.2 Layout / dimensionality reduction — openTSNE is defensible

- **[VERIFIED] openTSNE scales and uniquely supports incremental layout.** Parallel FIt-SNE
  benchmarked to ~1M points, ~2× faster than UMAP at scale; `transform()` /
  `prepare_partial()` embed **new papers against a frozen existing layout**
  ([opentsne docs](https://opentsne.readthedocs.io/en/latest/)). This is exactly the
  stability property the pipeline relies on by freezing `projector.pkl`. Keep it.
- **[VERIFIED] PaCMAP is the alternative to benchmark for the zoomed-out "fields" view.**
  (Wang et al., JMLR 2021, [20-1061](https://jmlr.org/papers/v22/20-1061.html)) Recognized DR
  method aiming to preserve both local and global structure.
- **[REFUTED] There is *not* a proven "fundamental" global-vs-local tradeoff in DR.** (vote
  1-2) Per Kobak & Linderman, **initialization dominates** (PCA/Laplacian init). So don't
  over-index on "PaCMAP preserves global structure better" — benchmark both **with good
  init** on the real corpus before switching.

### 1.3 Semantic zoom — two near-exact blueprints exist

- **[VERIFIED] WizMap is the closest published blueprint for the zoom pillar.** (Wang et al.,
  ACL 2023 demo, [arXiv:2306.09328](https://arxiv.org/abs/2306.09328)) Builds a **quadtree
  over the 2D points** and applies **tile-based t-TF-IDF** to per-tile meta-documents,
  traversed bottom-up, then *"maps pre-computed embedding summaries to a suitable granularity
  level and dynamically shows them as users zoom"* — Google-Maps-style, our exact UX. Renders
  *millions* of points **entirely client-side in WebGL, no backend**, validating the
  static-bundle + deck.gl architecture at scale.
  - **Adopt-now:** **t-TF-IDF over spatial tiles is a simpler labeling path** than
    nested-community labels — usable as a complementary cross-check (MIT-licensed). WizMap
    also does **KDE over a 200×200 grid** to auto-label dense regions — a way to place labels
    on density peaks, not only community centroids. (Authors note t-TF-IDF is *"sensitive to
    tile-size selection."*)
- **[VERIFIED, w/ caveat] Embedding Atlas (Apple, 2025) validates scale, not the zoom
  pillar.** ([machinelearning.apple.com](https://machinelearning.apple.com/research/embedding-atlas))
  A single WebGL/WebGPU scatter view renders **4M points @ 60fps, 10M+ @ 25fps** — confirms a
  deck.gl frontend can hold a large corpus. **But** it produces a **flat cluster list, no
  hierarchy/zoom** (hierarchical clustering called "future work"). (No-hierarchy detail was a
  2-1 split; it uses WebGPU not deck.gl, so FPS figures are directional.)
- **[VERIFIED, medium — single hobby source] Academic-Atlas is a live peer project doing our
  exact pattern.** ([github.com/jscmp4/Academic-Atlas](https://github.com/jscmp4/Academic-Atlas))
  deck.gl + **semantic zoom as progressive field→subfield→topic label reveal**. Two pragmatic
  decisions worth noting: (a) it **does not project the full corpus** — downsamples 455M
  OpenAlex papers to **~50K high-impact (≥500 citations) → 157 clusters** for the rendered
  map; (b) it **prefers OpenAlex's topic hierarchy for labels** (human-readable), using
  TF-IDF only to dedupe. It uses a *general-purpose* embedder (all-MiniLM-L6-v2) + UMAP +
  BERTopic — the opposite embedder choice from ours (evidence that both routes ship).
- **[VERIFIED] BERTopic is a fully content-based nested-label route.**
  ([BERTopic docs](https://maartengr.github.io/BERTopic/getting_started/hierarchicaltopics/hierarchicaltopics.html))
  Agglomerative (ward) clustering over per-topic c-TF-IDF with cosine distance, yielding a
  **keyword label at every merge level** (parent nodes re-sum bag-of-words). A ready-made
  alternative/complement to our citation-community labels; needs no citation data.

### 1.4 Citation-based organization — the fused graph is validated; Louvain is the weak link

- **[VERIFIED] Fusing citation + content beats pure-citation clustering — validating the
  fused semantic+citation graph.** Boyack & Klavans (JASIST 2010,
  [asi.21419](https://onlinelibrary.wiley.com/doi/abs/10.1002/asi.21419)) on 2.1M articles: a
  citation+text hybrid *"improves upon the bibliographic coupling results in all respects."*
  Corroborated by a MIT QSS study
  ([qss 1/4/1570](https://direct.mit.edu/qss/article/1/4/1570/96116)) finding a 50/50
  direct-citation+text hybrid most accurate.
- **[REFUTED] Do not rely on a fixed pure-citation ranking.** (vote 1-2) The specific claim
  "bibliographic coupling > co-citation > direct citation" failed and was later revised
  (Klavans & Boyack 2017 upgraded direct citation). The **hybrid-beats-pure** conclusion
  still holds; the ordering among pure methods does not.
- **🔑 [VERIFIED] Replace Louvain with Leiden for zoom-band community detection — highest-value
  adopt-now fix.** Traag, Waltman & van Eck, *"From Louvain to Leiden"* (Nature Sci Rep 2019,
  [s41598-019-41695-z](https://www.nature.com/articles/s41598-019-41695-z)): Louvain yields
  *"up to 25% of communities badly connected and up to 16% disconnected,"* and *"communities
  may even be disconnected, especially when running the algorithm iteratively."* Our
  `s06_hierarchy` runs Louvain **nested/iteratively** — precisely the failure regime. Leiden
  **guarantees connected communities** and is near-drop-in (`leidenalg` / `igraph`). Directly
  advances ROADMAP P0 #5 ("validate the fused graph and semantic hierarchy"). *Minor nuance:
  the paper's "iteratively" means re-running Louvain to refine one partition, related to but
  not identical to building a nested multi-resolution hierarchy — but the disconnection
  failure mode still applies.*

---

## TOPIC 2 — Categorizing works by organization, lab, author

> **Verification status:** all 23 Topic-2 claims below passed the same 3-vote adversarial
> verification as Topic 1 — **23/23 confirmed, 0 refuted, 0 votes against**. They are marked
> **[VERIFIED]**.

### 2Z. How everyone else actually gets affiliations (survey, 2026-08-17)

Added after D35 established that **no metadata API carries affiliations for arXiv preprints** —
OpenAlex, Semantic Scholar, arXiv's own API and DataCite all return zero for the papers we lack.
The literature splits the job into two problems that are usually conflated:

**(1) EXTRACTION — obtaining a raw affiliation string at all.** This is our blocker; arXiv does
not require an affiliation at submission, so nothing downstream can inherit one.

| Approach | Who does it | Measured quality | Notes |
|---|---|---|---|
| Manual roster keyed to an author id | **CSRankings** (`faculty-affiliations.csv`, keyed to DBLP names, updated by pull request) | human-level | The same shape as our `org_rosters.yaml` / s14. Precise, unbounded labour. |
| Publisher metadata | Crossref / DataCite | n/a | Only helps once a preprint is published: **1.7%** of our post-2021 unaffiliated papers have a non-arXiv DOI. |
| PDF layout parsing | **GROBID** | **~80% precision / ~50% recall** on arXiv affiliations (COMET's measurement) — strong on author *names*, weak on affiliations | Apache-2.0. The obvious route, and measurably not good enough alone. |
| LaTeX source | **unarXive** (1.9M sources) | n/a | Higher fidelity than PDF for structure, but affiliations are not a first-class field in the release. |
| Closed LLM over full documents | Epoch AI (prompted LLM for structured metadata); COMET's Claude baseline | "quite well" | COMET priced full-arXiv extraction at **$60k–$100k**. |
| **Distilled small open model** | **COMET** — Qwen3-8B LoRA distilled from GLM-4.5-Air, teacher–student with curriculum by model surprisal | **91% precision / 81% recall** on affiliations (97% / 86% on authors) | State of the art for arXiv, open weights. |

**(2) LINKING — mapping a raw string to a canonical organisation (ROR).** Solved, several ways,
all open. Benchmarked in *From raw affiliations to organization identifiers* (arXiv:2505.07577):

| System | Approach | Precision / Recall / F1 (AffRoDB) |
|---|---|---|
| **AffRo** | rule-based keyword framework: preprocess → match → disambiguate | **0.965 / 0.910 / 0.937** |
| **OpenAlex ROR predictor** | ensemble of two models trained on historical MAG data + synthetic strings | 0.914 / 0.929 / 0.921 |
| **S2AFF** (Allen AI) | NER-parse into main/child/address → Jaccard retrieval of top-100 → LightGBM pairwise re-rank | 0.964 / 0.846 / 0.901 |
| **AffilGood** (SIRIS) | modular span → language → translate → NER → link → geocode; multilingual XLM-R fine-tunes | — (not in this table) |
| **ROR affiliation API** | curated registry + algorithmic matching, free | — (ROR reports no public figure) |

**The punchline: the extraction half is already done and given away.** COMET ran their distilled
model over the whole arXiv corpus and released the output as **CC0**:
`cometadata/arxiv-author-affiliations-matched-ror-ids` on HuggingFace — **2,799,088 papers keyed
on `arxiv_id`**, per-author `{name, affiliations: [{affiliation, ror_id}]}`, **2.44 GB**,
**12.1M affiliation entries of which 9.2M (75.8%) carry a ROR id at ~97% matching precision**.
Verified by sampling rows from the datasets server, not just reading the card.

**How COMET's pipeline actually works** (worth borrowing; six stages):

1. **PDF → markdown** with **dots.ocr**, a vision-language OCR model, over ~2.8M arXiv PDFs.
   This is the expensive step; it ran on **Marlowe, Stanford's GPU instrument**.
2. **Ground truth**: 2,491 hand-annotated arXiv papers (1,400 train / 1,092 test), released CC0.
3. **Off-policy distillation with rejection sampling.** A large teacher (**GLM-4.5-Air**) produces
   several extraction rollouts per paper *including reasoning traces*; only rollouts whose answer
   matches the annotation are kept. The training set is therefore teacher reasoning that
   provably reached the right answer — no label noise inherited from the teacher. They ran this
   with several teachers and published each rollout set (GLM-4.5-Air, GLM-4.6, Claude Sonnet 4,
   gpt-oss-120b, Gemini 2.5 Flash, Qwen3-235B).
4. **Curriculum by surprisal.** Kept examples are ordered by the *student's own* surprisal — how
   unlikely each example is under its current predictions — and trained easiest-first. Free
   difficulty ranking: the student is its own oracle, no extra annotation.
5. **Student**: **Qwen3-8B + LoRA**, supervised fine-tuning on that filtered, ordered set.
   Result **91% P / 81% R** on affiliations (**97% / 86%** on authors); the model card reports
   **F1 83.37**. GROBID on the same task: ~80% P / ~50% R.
6. **Inference at scale**: vLLM serving the LoRA on H100s, emitting JSON per paper. Then a
   separate **string → ROR linking** step, using a matcher designed by Crossref's Dominika
   Tkaczyk, at **~97% precision** — 9.2M of 12.1M affiliations matched.

The shape of the idea: **use the expensive model once, not 2.8M times.** A frontier model buys
training signal; an 8B model does the volume. Extraction and linking stay separate, so the
linking half reuses Crossref/ROR work instead of being reinvented.

**Consequence for us.** Our 1,000,490 papers are a subset of arXiv, so #9 reduces from "buy
86–289 GB of requester-pays S3 egress and run GROBID for weeks" to **a 2.44 GB download and a
join on `arxiv_id`**. Their snapshot is December 2025, so our ~118k 2026 papers fall outside it;
the model is open-weight, so that tail can be run locally on a 3090 if it matters.

**Citing it / is there a paper?** No peer-reviewed paper. The citable artifact is the Zenodo
record **10.5281/zenodo.18663775** — *"COMET arXiv preprint author affiliation extraction and ROR
ID matching results"*, **Parth Sarin (Stanford)** and **Adam Buttrick (California Digital
Library / ROR)**, published 2026-02-16, CC0, 2.4 GB JSONL. Methods are written up in the blog
post; code is `cometadata/arxiv-preprint-parsing` (MIT). Worth noting one author works at ROR
itself, which is why the linking half is as strong as it is.

**Extending past their December-2025 snapshot.** Their run covers **870,385 of our 1,000,490
papers (87%)**. Of the **730,148** papers we are missing affiliations for, **608,578 (83%) fall
inside their snapshot** and need only a join; the remaining **121,570** are 2025-12 or later.
Doing that tail ourselves is cheaper than COMET's own run for two reasons:

- **PDFs are free.** `gs://arxiv-dataset` on Google Cloud Storage is a public mirror of all arXiv
  PDFs (~1.1 TB, weekly updates) — no requester-pays S3 bill. COMET's own repo pulls from GCS or
  Kaggle. 121,570 papers ≈ 180-240 GB, streamable (fetch → extract → delete) against 262 GB free.
- **The OCR stage is skippable for us.** COMET ran dots.ocr over everything because 2.8M papers
  span 1991-2025 including scanned and unusual PDFs. We need only recent CS papers, which
  essentially all carry an embedded text layer — and **PyMuPDF is already a dependency** (s13
  figure extraction). Affiliations live on page 1, so this is `page.get_text()` on one page
  rather than a vision model over ten. That removes the largest GPU cost outright.

What remains is the 8B student itself (short page-1 inputs, so vLLM batching makes this order
hours, not days, on a single 24 GB card) plus free ROR linking. **Blocker as of 2026-08-17: this
box's GPU is unusable** — `nvidia-smi` reports a driver/library mismatch (kernel module
580.159.03 vs NVML 580.173) and `torch.cuda.is_available()` is `False`. Clearing it without a
reboot needs root and an idle GPU:
`sudo rmmod nvidia_uvm nvidia_drm nvidia_modeset nvidia && sudo modprobe nvidia`.

Sensible order: **ingest their dataset first** (free, one join, the large majority of the win),
measure the coverage jump, *then* decide whether the 2026 tail earns GPU time — those papers are
also the least-cited, so their marginal value to an influence-weighted map is lowest.

**Licences.** COMET dataset CC0; GROBID Apache-2.0; S2AFF and AffilGood open; ROR data CC0.
CSRankings is CC BY-NC-ND — usable as a reference, not ingestible.

Sources: <https://www.cometadata.org/blog/unlocking-author-affiliation-metadata-for-all-of-arxiv/>,
<https://huggingface.co/datasets/cometadata/arxiv-author-affiliations-matched-ror-ids>,
<https://arxiv.org/abs/2505.07577>, <https://github.com/allenai/S2AFF>,
<https://github.com/sirisacademic/affilgood>, <https://ror.readme.io/docs/matching>,
<https://help.openalex.org/hc/en-us/articles/24831328396311-Institutions-and-Raw-Affiliation-String-Parsing>,
<https://github.com/emeryberger/CSrankings>, <https://github.com/IllDepence/unarXive>.

### 2A. Existing structures you can ingest (instead of hand-building)

| Source | Sub-institution (dept/lab) granularity | Hierarchy support | License | Automated ingestion |
|---|---|---|---|---|
| **ROR** | Institutes & **labs in scope**; **schools/departments explicitly out of scope**; some divisions/centers exist as child records | ✅ 5 rel. types (Parent/Child/Related/Successor/Predecessor); **array → multi-parent DAG**; multi-level | **CC0** (public domain) | Open API + full dump |
| **OpenAlex institutions** | **ROR-backed 1:1** → operates at ROR level (e.g. "MIT"), **not** dept/lab | ✅ `lineage` (ancestor IDs), `roles`, `type` | CC0 | API `lineage`/`ror` filters |
| **CSRankings** | Faculty→institution, **manually curated** | Institution + CS area | ⚠️ **CC BY-NC-ND 4.0** (whole project); underlying **DBLP is ODC-BY** | Quarterly `csrankings-[a-z].csv`; not algorithmic |
| **GRID** | (superseded) | — | Sunset 2021 → ROR | Use **ROR** (maintained successor) |
| **Wikidata** | *not assessed this pass* | — | CC0 | — (open question) |

Key facts (all **[VERIFIED]** — 3-vote confirmed):

- **ROR models hierarchy explicitly and is CC0.** Five relationship types stored as an
  **array**, so an org can have multiple parents/children (a DAG, matching `orgs.json`'s
  parent/children model); supports multi-level parent→child→grandchild "family trees."
  ([ROR relationships](https://ror.readme.io/docs/relationships),
  [ROR FAQ](https://ror.org/about/faqs/))
- **But ROR deliberately stops above departments:** *"not focused on capturing all
  subdivisions… such as a university's schools or departments."* Per AffRo, **only ~0.06% of
  ROR records are university-child departments.** → **Dept/lab granularity cannot be sourced
  from ROR/OpenAlex; it must be built.** This *validates* the hand-curated
  `pipeline/directory/units.py` approach as state-of-practice, not a shortcut.
- **OpenAlex institution = ROR entry, 1:1.** Its `lineage` gives university→system hierarchy
  but **not** intra-university structure — consistent with the observation that broad Meta
  IDs establish "Meta," not "FAIR."
  ([OpenAlex Institution object](https://developers.openalex.org/api-entities/institutions/institution-object))
- **CSRankings licensing:** the **whole project is CC BY-NC-ND 4.0** (NonCommercial +
  NoDerivatives) — constrains redistributing/transforming its faculty-affiliation data; only
  the DBLP layer beneath is ODC-BY. Confirms the existing decision to keep CSRankings disabled
  in redistributable builds and treat its rosters as *claims*. Its faculty→institution map is
  **manually curated** (submission form validating DBLP/homepage/Scholar/ORCID, quarterly
  processing) — no automated affiliation linking to borrow.
  ([CSRankings README](https://github.com/emeryberger/CSRankings))

### 2B. Algorithms to build/fill what's missing

**Affiliation string → institution (entity linking):**

- **OpenAlex** uses a **deep-learning parser** at **~0.92 recall / 0.93 precision** on the
  AffilGood benchmark. **Failure mode: the model hasn't been retrained since April 2023**, so
  newer institutions need a 3-step fallback (DL parse → monthly rule-based string matching →
  ROR's own matcher). The **model + code + training data are open** and self-hostable.
  ([OpenAlex affiliation parsing](https://help.openalex.org/hc/en-us/articles/24831328396311-Institutions-and-Raw-Affiliation-String-Parsing))
- **AffRo (OpenAIRE, 2025, [arXiv:2505.07577](https://arxiv.org/abs/2505.07577)) beats both
  OpenAlex and S2AFF** on the expert-curated AffRoDB (**AffRo F1 ~0.937 vs OpenAlex 0.921 vs
  S2AFF 0.901**). It's **production-deployed in the OpenAIRE Graph**, a transparent 3-phase
  pipeline (preprocess → cosine+Levenshtein match → city/country disambiguation), and ships
  **AffRoDB under CC0 on Zenodo** — a reusable, permissively-licensed eval set to test *our*
  affiliation linking. Notably AffRo also **maps up to the parent institution** because ROR
  lacks departments — the same reality this project hit.
- **ROR's own matcher** is moving to a new "single search" strategy (more accurate, fewer
  false positives), default for `?affiliation` queries in Q1 2026. Caveat: *any individual
  string may match worse* than the old multisearch — evaluate on datasets, not single strings.
  ([ROR affiliation-matching blog](https://ror.org/blog/2025-12-02-announcing-a-new-affiliation-matching-strategy/))

**Author name disambiguation:**

- **S2AND** (JCDL 2021, [arXiv:2103.07534](https://arxiv.org/abs/2103.07534)) unifies 8 AND
  datasets; its open model **cuts error >50% in B³ F1 vs Semantic Scholar's production
  algorithm**. Failure mode: **models trained on one dataset generalize poorly** — train on
  the union. Fully open (`github.com/allenai/S2AND`). The reusable route if we move beyond
  OpenAlex author IDs.
- **ID-registry approach** (ORCID / OpenAlex author IDs / Google Scholar IDs) sidesteps
  model-based disambiguation where coverage exists.

**Detecting labs/groups *below* the institution (the hard part):**

- **Co-authorship community detection** is the automatable signal: within a single CS domain,
  co-authorship networks *"exhibit small communities within the influential authors of a
  particular domain"* ([arXiv:2409.00081](https://arxiv.org/pdf/2409.00081v2)) — communities
  cluster around PIs, approximating research groups. But the same source warns it's
  fragmented and field-dependent.
- **Two failure modes for any affiliation-based attribution:** publication-time affiliations
  are often unavailable, and **authors change institutions over time** — so org attribution
  should anchor to the affiliation *as stated on that paper*. The paper-id-keyed
  `affiliations.parquet` already does this.

**Bottom line:** *Institution-level* attribution is fully automatable (OpenAlex/ROR/AffRo,
all CC0-friendly). *Department/lab-level* is **not** reliably sourceable or fully automatable
today — ROR excludes it, OpenAlex inherits that, CSRankings is manual + license-restricted.
The realistic automation frontier is **affiliation-string matching + co-authorship community
detection to *propose* sub-units, with human curation to confirm** — a semi-automated version
of the current `units.py`.

---

## Prioritized recommendations (mapped to the pipeline)

1. **🔑 Swap Louvain → Leiden in `s06_hierarchy`** [VERIFIED] — **DONE (2026-07).** Now the
   default (`hierarchy.method: "leiden"`, reference `leidenalg`/`igraph`
   `RBConfigurationVertexPartition`); Louvain stays selectable for comparison. Measured on the
   live 28k corpus: internally-disconnected zoom cells dropped **14.5% → 10.5%**, and the
   hierarchy resolves more/finer communities (**6,732 → 7,359 regions**), with all
   strict-nesting/exact-partition invariants intact. **Nuance the rebuild surfaced:** Leiden's
   "guaranteed connected" property holds at the *split* step (raw detector output: 0
   disconnected), but this stage's semantic post-processing (`_coarsen_groups`,
   `_merge_small_groups`, `_fill_to_target`, embedding fallback) re-introduces some
   disconnection by merging graph-disjoint-but-embedding-close groups. Driving disconnection
   to ~0 needs a connectivity-aware post-processing pass — a follow-up with real tradeoffs
   against the branch-target/nesting invariants, not a mechanical change. Tracked in ROADMAP
   P0 #5.
2. **Add a t-TF-IDF (WizMap-style) or hierarchical-BERTopic label cross-check** [VERIFIED].
   Cheap, content-only second opinion to validate citation-community labels against.
3. **Benchmark, don't assume, before any embedder/DR swap** [VERIFIED]. Keep
   SPECTER2-proximity as default; if evaluating, A/B on layout/cluster coherence (not generic
   leaderboards), with PCA init for the DR comparison.
4. **Evaluate AffRo (or the self-hosted OpenAlex parser) against current OpenAlex matching**
   [VERIFIED]. AffRo's +1.6 F1 and CC0 AffRoDB benchmark give a concrete way to measure org
   attribution and catch the "OpenAlex model stale since April 2023" gap.
5. **Keep hand-curating dept/lab units — it's correct, not a hack** [VERIFIED]. Sub-institution
   granularity is unsourceable from registries. To *scale* curation, add **co-authorship
   community detection to auto-propose** candidate groups for human confirmation.
6. **Lean on ROR (CC0) for the org-hierarchy backbone; keep CSRankings out of redistributable
   builds** [VERIFIED]. ROR's Parent/Child/Related array is a ready DAG for `orgs.json`;
   CSRankings' CC BY-NC-ND constrains redistribution.
7. **Consider downsample-by-citation for tractability at scale** [VERIFIED, medium]. If the
   corpus outgrows full openTSNE projection in a static bundle, Academic-Atlas's ≥N-citations
   sampling is a proven tactic — but weigh against the max-zoom "micro-cluster" pillar, which
   downsampling thins out.

## Open questions this research did not close

- **Wikidata** for org hierarchy/labs (not assessed) — likely CC0 and richer on sub-units
  than ROR; worth a targeted follow-up.
- Whether swapping the embedder/DR *measurably* changes the CS/AI layout — needs a benchmark
  on the actual ~28k corpus, not literature.
- Whether **full-text** embedding (vs title+abstract) materially improves fine-grained
  (max-zoom) separation.

## Primary sources

**Topic 1:** SPECTER [2004.07180](https://arxiv.org/abs/2004.07180) · SPECTER2/SciRepEval
[2211.13308](https://arxiv.org/abs/2211.13308) · SciNCL
[2202.06671](https://arxiv.org/abs/2202.06671) · MTEB
[2210.07316](https://arxiv.org/abs/2210.07316) · WizMap
[2306.09328](https://arxiv.org/abs/2306.09328) ·
[Embedding Atlas](https://machinelearning.apple.com/research/embedding-atlas) ·
[Academic-Atlas](https://github.com/jscmp4/Academic-Atlas) ·
[openTSNE](https://opentsne.readthedocs.io/en/latest/) · PaCMAP
[JMLR 20-1061](https://jmlr.org/papers/v22/20-1061.html) ·
[BERTopic hierarchical](https://maartengr.github.io/BERTopic/getting_started/hierarchicaltopics/hierarchicaltopics.html) ·
Boyack & Klavans [asi.21419](https://onlinelibrary.wiley.com/doi/abs/10.1002/asi.21419) ·
[QSS large-scale science models](https://direct.mit.edu/qss/article/1/4/1570/96116) ·
Louvain→Leiden [s41598-019-41695-z](https://www.nature.com/articles/s41598-019-41695-z) ·
[Connected Papers about](https://www.connectedpapers.com/about)

**Topic 2:**
[OpenAlex Institution object](https://developers.openalex.org/api-entities/institutions/institution-object) ·
[OpenAlex affiliation parsing](https://help.openalex.org/hc/en-us/articles/24831328396311-Institutions-and-Raw-Affiliation-String-Parsing) ·
[OpenAlex author disambiguation](https://help.openalex.org/hc/en-us/articles/24347048891543-Author-disambiguation) ·
[ROR FAQ](https://ror.org/about/faqs/) · [ROR relationships](https://ror.readme.io/docs/relationships) ·
[ROR affiliation matching](https://ror.org/blog/2025-12-02-announcing-a-new-affiliation-matching-strategy/) ·
AffRo [2505.07577](https://arxiv.org/abs/2505.07577) · S2AND
[2103.07534](https://arxiv.org/abs/2103.07534) · co-authorship communities
[2409.00081](https://arxiv.org/pdf/2409.00081v2) ·
[CSRankings](https://github.com/emeryberger/CSRankings)
