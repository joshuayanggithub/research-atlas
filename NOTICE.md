# Third-party notices

Research Atlas is MIT-licensed (see `LICENSE`). It builds on data sources and
open-source dependencies with their own terms; the notable ones:

## Data sources

- **OpenAlex** (works, citations, topics, institutions) — CC0. The corpus spine.
- **Semantic Scholar** (SPECTER2 embeddings) — used via the public API.
- **arXiv** — PDFs and metadata are fetched from arXiv for figure/table extraction and
  date resolution, under arXiv's API Terms of Use (polite rate limits; we link back to
  arXiv rather than redistributing full text).

## Dependency licensing note — PyMuPDF (AGPL-3.0)

The pipeline's offline figure-extraction stage (`pipeline/stages/s13_figures.py`,
`pipeline/common/figure_extract.py`) uses **PyMuPDF**, which is licensed **AGPL-3.0**.

This project is MIT-licensed. That is consistent because:

- PyMuPDF is used **offline, in the data pipeline**, to render figure/table crop images.
  Research Atlas ships the resulting **images**, not the PyMuPDF library, and the web app
  never imports or serves PyMuPDF.
- The MIT license covers **this project's own source code**. It does not relicense
  PyMuPDF; PyMuPDF remains AGPL-3.0 for anyone who obtains and runs it.

If you redistribute or host a modified version of the **pipeline** (the part that runs
PyMuPDF) as a network service, AGPL-3.0's source-sharing obligations apply to that use of
PyMuPDF. The runtime web app (which does client-side figure extraction via pdf.js, Apache-2.0)
is unaffected. If AGPL is undesirable in your deployment, swap the s13 extractor for
PDFFigures 2.0 (Apache-2.0) — the pipeline seam is extractor-agnostic (see
`docs/DESIGN_DECISIONS.md` D11).
