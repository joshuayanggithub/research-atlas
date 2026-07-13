# Research Visualizer

An interactive 2D **map of CS/AI research**. Each paper is a point; semantically similar
papers sit near each other; citations are directed edges; and the map supports
**Google-Maps-style semantic zoom** — broad fields when zoomed out, ML topics mid-zoom,
fine subtopics zoomed in. Filter by **organization**, **author**, and **date**; inspect a
paper's **citations** and **related works**.

See **`Design.md`** (why) and **`Features.md`** (what).

## How it works

An offline **Python pipeline** (`pipeline/`) turns OpenAlex works + Semantic Scholar
SPECTER2 embeddings into a small **static artifact bundle**; a **React + deck.gl** app
(`web/`) renders it. No backend — the browser loads pre-baked Arrow/JSON files.

```
OpenAlex + Semantic Scholar  ──►  pipeline (s00…s11)  ──►  web/public/data/*  ──►  deck.gl app
```

## Quick start

### 1. Build the data (pipeline)

Requires Python 3.11 (managed here via [`uv`](https://github.com/astral-sh/uv)).

```bash
uv venv --python 3.11
uv pip install -r pipeline/requirements.txt
# Optional but recommended: local embedding fallback (pulls torch)
uv pip install "sentence-transformers>=3.0" "torch>=2.2"

cp .env.example .env          # set OPENALEX_MAILTO; optional S2_API_KEY

# Run the whole pipeline (fetch → embed → project → cluster → label → emit)
uv run python -m pipeline.run_all
# …or resume from a stage:   uv run python -m pipeline.run_all --from s04
# …or run a subset:          uv run python -m pipeline.run_all --only s06,s07
```

Configuration (corpus orgs, date range, cap, embedding backend, zoom bands) lives in
**`config.yaml`**. Artifacts are emitted to `web/public/data/`.

### 2. Run the app (web)

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

## Repo layout

```
config.yaml              # single source of truth for the pipeline
pipeline/
  run_all.py             # stage orchestrator (typer CLI)
  config.py              # typed config loader (config.yaml + .env)
  common/                # schema (the artifact contract), abstract, openalex client, io, fused-sim
  embedding/             # swappable embedding backends (specter2_s2, scincl_local)
  stages/                # s00_resolve_orgs … s11_emit
  tests/                 # unit tests (abstract reconstruction, …)
web/
  src/
    data/                # artifact loader + TS types (mirror of schema.py)
    state/               # zustand store
    map/                 # deck.gl MapView + layers (points, labels, edges) + zoom/colors
    filters/             # org / author / date filters + GPU filter mask
    panels/              # details, related works, legend, search
  public/data/           # ← pipeline output (git-ignored)
```

## Pipeline stages

| Stage | Does | Emits |
|---|---|---|
| s00 | resolve org names → OpenAlex institution ids | `orgs_resolved.json` |
| s01 | fetch CS works (institutions × field × dates) | `works_raw.jsonl` |
| s02 | reconstruct abstracts, dedupe, assign node_id | `corpus.parquet` |
| s03 | embed (SPECTER2 fetch / SciNCL fallback), L2-normalize | `embeddings.npy` |
| s04 | openTSNE 768→2D, freeze projector | `coords2d.npy`, `projector.pkl` |
| s05 | UMAP→10D + HDBSCAN clustering | `cluster_assign.npy` |
| s06 | quadtree semantic-zoom hierarchy | `tiles.json` |
| s07 | c-TF-IDF + OpenAlex topic labels | `clusters.json`, `labels.json` |
| s09 | intra-corpus citation edges | `edges.npz` |
| s08 | fused text+citation kNN neighbors | `neighbors.npz` |
| s10 | org / author / topic indexes | `orgs.json`, `authors.arrow`, `topics.json` |
| s11 | assemble bundle + manifest | `web/public/data/*` |

## Testing

```bash
uv run pytest            # pipeline unit tests
cd web && npx tsc -b     # frontend typecheck
```
