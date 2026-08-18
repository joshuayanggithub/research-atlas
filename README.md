# Research Atlas

A 2D map of CS/AI research papers. Each paper is a point, papers with similar
titles/abstracts sit near each other, and citations are drawn as directed edges. Zooming
works like a map: broad fields when zoomed out, specific topics in the middle, and
micro-clusters of a few papers at the deepest zoom.

![The map with topic labels and citation edges](docs/screenshots/01-home.png)

Select a paper to see its citation network, related work, and — for arXiv papers — its
Figure 1 and Table 1 pulled straight from the PDF. A slider filters the network down to the
most related papers.

![A selected paper with its citation network, first figure, and first table](docs/screenshots/02-selection.png)

## Research Taste

Being a good researcher means having good ["research taste"](https://www.lesswrong.com/posts/Ldrss6o3tiKT6NdMm/my-research-process-understanding-and-cultivating-research), a set of good intuitions and judgements for tackling questions that are both interesting to you and worthwhile. 

This includes the ability to scope the bigger picture of a field: what problems are important, what approaches have been tried? While we can automate research experiments, [taste is something unique to yourself and not something AI can do well](https://www.lesswrong.com/posts/Mxsy7wYvsCRv5dGrw/tastybench-toward-measuring-research-taste-in-llm). 

The ability to build a Strategic Big Picture is the purpose of this project. 

## Use Cases
See (usecases.md)

1. Understanding Individual Researchers (advisors, recruitment, etc): It allows one to view the research interest of a singular researchers at specific company, organization, neolab, or university by filtering across time periods and viewing all their published research, as well as the specific topics that research centers around.

2. Organization Research Trends: It allows one to understand the general research trends of groups of reserachers in specific time periods (10 years ago or contemporary day) by visualizing what topics their published work centers around. By groups this includes groups of researchers in any granularity. This includes - Universities (Berkeley), University Departments (CMU's MLD or RI) and individual Research Labs/Groups within (Berkley's BAIR, CMU's BIG lab, Biorobotics lab) - Companies (Amazon, Google) and individual research Groups (FAIR, Meta Superintelligence, Amazon FAR, Amazon AGI, Google Deepmind, Google Brain, etc) - NeoLabs (Redwood Research, Anthropic, OpenAI, Deepseek, Kimi, Minimax).

3. Finding similar work: We can visualize the relations and similarities given a specific research work to other research works based on the topic and semantic similarity of written content.

4. Visualizing how fields evolve: By clustering groups of related research work together into common labels, we can view from different time periods what topic were relevant in what times and how they evolved.

## Previous Approaches

See (docs/prior_websites)


## Quick start

### 1. Build the data

Requires Python 3.11 (via [`uv`](https://github.com/astral-sh/uv)).

```bash
uv venv --python 3.11
uv pip install -r pipeline/requirements.txt
# optional local embedding fallback (pulls torch):
uv pip install "sentence-transformers>=3.0" "torch>=2.2"

cp .env.example .env          # set OPENALEX_MAILTO; OPENALEX_API_KEY recommended

uv run python -m pipeline.run_all              # full run
uv run python -m pipeline.run_all --from s04   # resume from a stage
uv run python -m pipeline.run_all --only s06,s07
```

`config.yaml` holds everything configurable (corpus scope, date range, embedding backend,
zoom bands, figure baking). Output lands in `web/public/data/`.

### 2. Run the app

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

A fresh clone has no data bundle (it's gitignored — it's large and regenerable), so run the
pipeline once first, or the app shows "Failed to load data."

### 3. (Optional) See your own reading against the map

Import your library and the map filters to the papers you have actually read, so the areas you
have worked through — and the ones you never have — become visible. Any **CSL JSON** export
works (Zotero, Mendeley and Paperpile all offer one), as does BibTeX. To keep your Zotero
collection names as separate toggles:

```bash
# reads a local Zotero database, or Zotero 7's local API, or the hosted API with a read-only key
python3 tools/zotero_export.py --list                      # what collections can I see?
python3 tools/zotero_export.py -o reading-list.json        # default: "1. Finished", "2. Understood"
ZOTERO_API_KEY=... python3 tools/zotero_export.py -o reading-list.json   # library on another machine
```

Then **Filters → Reading list → Import**. Matching runs entirely in the browser (arXiv id, then
DOI, then title), so your reading history never leaves your machine, and the panel reports how
many entries matched rather than quietly dropping the rest.

## Pipeline stages

| Stage | Does |
|---|---|
| s00 | resolve org names → OpenAlex institution ids |
| s01 | fetch CS works (field × dates, optionally org-gated) |
| s02 | reconstruct abstracts, dedupe, assign node ids, arXiv-preferred dates |
| s03 | embed (SPECTER2 via Semantic Scholar, SciNCL fallback) |
| s04 | openTSNE 768→2D, freeze the projector |
| s05 | UMAP→10D + HDBSCAN clustering |
| s09 / s08 | citation edges / fused semantic + citation neighbor graph |
| s06 / s07 | nested Leiden communities / topic + phrase labels |
| s12 | reveal levels for overlap-free zoom + on-demand tiles |
| s10 | org / author / topic indexes |
| s13 | (optional) bake Figure 1 crops from arXiv PDFs |
| s11 | assemble the static bundle + manifest |

## Repo layout

```
config.yaml            # single source of truth for the pipeline
pipeline/
  run_all.py           # stage orchestrator (typer CLI)
  common/              # artifact schema, abstract reconstruction, OpenAlex client, figure extract
  embedding/           # swappable backends (specter2_s2, scincl_local)
  directory/           # curated org/department/lab units
  stages/              # s00 … s16
  tests/               # pytest
tools/
  zotero_export.py     # export your Zotero collections to an importable reading list
web/
  src/
    data/              # artifact loader + TS types (mirror of schema.py)
    state/             # zustand store
    map/               # deck.gl MapView + layers (points, edges, labels) + relevance/scores
    filters/           # org / author / topic / date / reading list + GPU filter mask
    panels/            # details, citations, figure/table, arXiv preview, related works
  public/data/         # ← pipeline output (gitignored)
```

## Testing

```bash
uv run pytest                    # pipeline unit tests
cd web && npx tsc -b             # frontend typecheck
cd web && npm run test:e2e       # Playwright (desktop + mobile)
```

## Data & licensing

Papers, citations, and topics come from [OpenAlex](https://openalex.org) (CC0); SPECTER2
embeddings from [Semantic Scholar](https://www.semanticscholar.org); figure/table crops and
dates from [arXiv](https://arxiv.org) under its API terms. This project is MIT-licensed (see
[`LICENSE`](LICENSE)); the offline figure stage uses PyMuPDF (AGPL-3.0) — see
[`NOTICE.md`](NOTICE.md) for what that means.

## Status

The checked-in config builds a demonstrator. Known limitations and next work are tracked in
[`docs/TODO.md`](docs/TODO.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md); if you're picking the
project up, start with [`docs/HANDOFF.md`](docs/HANDOFF.md).
