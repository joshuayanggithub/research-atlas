# Features

What the Research Visualizer does today (MVP). See `Design.md` for how and why.

## The map

- **Semantic map of ~39k CS papers** (OpenAlex field 17, 2020–2026) from 7 seed
  organizations. Each paper is a point; **nearby points are semantically similar**
  (position from SPECTER2 embeddings projected to 2D with openTSNE).
- **GPU rendering** via deck.gl handles the full corpus at interactive framerates on an
  orthographic (map-style) canvas with smooth pan/zoom.
- **Point color** encodes CS **subfield** by default; switchable to **organization** or
  **recency** (publication year) via the legend.
- **Point size** scales with citation count, so influential papers stand out.

## Semantic zoom (the headline feature)

- Zoom behaves like Google Maps for research topics:
  - **Zoomed out** → broad CS **subfields** (Artificial Intelligence, Computer Vision,
    Systems, Theory, Networks, HCI, …).
  - **Mid zoom** → **topics** within a field (e.g. self-supervised learning, world models).
  - **Zoomed in** → **fine subtopics** as differentiating phrases (e.g.
    "action-conditioned world models").
- Labels are drawn from a **strictly nested quadtree hierarchy**, so a fine label always
  lives inside its coarse parent's region — no contradictory labels across zoom levels.
- Labels **declutter live on the GPU**: coarse/important labels win scarce screen space
  when zoomed out; finer labels reveal themselves as you zoom in.
- Label text is a hybrid of curated **OpenAlex topic names** and **c-TF-IDF key phrases**
  mined from each region's papers.

## Filtering

- **Organization** — toggle any of the seed orgs (industry: Google DeepMind, Meta AI/FAIR,
  Microsoft Research; academia: UC Berkeley, CMU, Stanford, MIT). Matching papers stay
  vivid; non-matches **dim** (default, preserving spatial context) or **hide** (toggle).
  Multiple orgs union; combines with other filters.
- **Author** — type-ahead search over every author in the corpus (ranked by paper count);
  select one or more to spotlight their papers.
- **Date range** — dual-handle year slider (2020–2026); filtering is applied on the GPU so
  dragging is instant even across the whole corpus.
- Filters **compose** (organization AND author AND date). "Clear" resets them.

## Selection, citations & related works

- **Click a paper** to open a details card: title, authors, date, venue, citation count,
  and a direct link to the paper (DOI / arXiv).
- **Citation edges** are drawn only for the selected paper (never the whole graph):
  directed arcs for papers it **cites**, papers that **cite it**, or **both** (toggle).
  Arc color encodes direction.
- **Related works** panel lists the most similar papers using a **fused text + citation**
  similarity (embedding cosine blended with co-citation + bibliographic coupling). Click a
  related paper to jump to it.

## Search

- **Title search** with type-ahead; selecting a result selects and focuses that paper.

## Data & provenance

- A top-bar summary shows corpus size, date span, and which embedding backend produced the
  map. All data is pre-baked into a static bundle (`web/public/data/`) — the app runs with
  no backend.

## Not yet (planned)

- University→department→lab granularity (currently org = OpenAlex institution level).
- Streaming/tiling for corpora beyond ~100k points; live "embed my own abstract" search;
  incremental nightly corpus updates; a dedicated local citation-subgraph panel.
