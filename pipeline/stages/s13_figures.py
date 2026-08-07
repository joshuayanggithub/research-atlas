"""s13: Bake "first figure" crops (Figure 1 / Table 1) from arXiv PDFs, offline.

For each paper with an arXiv id, download the PDF (politely — arXiv's Terms of Use cap
requests at 1/3s with no parallelism), locate Figure 1 / Table 1 with PyMuPDF
(``common.figure_extract``, the PDFFigures-2.0 caption-anchored pattern), render the crop to
a PNG, and write it under ``web/public/data/figures/<shard>/<node_id>.png``. This is the
offline half of the feature: the frontend serves these baked crops on demand and only falls
back to client-side pdf.js for papers without one.

Disabled unless ``figures.enabled``. Because it downloads a PDF per paper, a full-corpus pass
is a multi-hour polite batch; ``figures.max_papers`` caps a run so a sample can be baked
without the whole thing. Idempotent: a crop that already exists on disk is skipped, so a
capped run resumes where it left off.

Reads:  data/interim/corpus_active.parquet  (node_id, arxiv_id, cited_by_count)
Writes: web/public/data/figures/<node_id // FIGURE_SHARD_SIZE>/<node_id>.png
        data/interim/figures_index.json      { "node_ids": [...] }  (consumed by s11)
"""

from __future__ import annotations

import json
import time

import httpx
import polars as pl

from pipeline.common import log
from pipeline.common import schema as S
from pipeline.common.figure_extract import find_first_figure, render_crop
from pipeline.config import (
    CACHE_DIR, CORPUS_ACTIVE, INTERIM_DIR, WEB_DATA_DIR, Config, ensure_dirs, load_config,
)

OUT_INDEX = INTERIM_DIR / "figures_index.json"
PDF_CACHE = CACHE_DIR / "arxiv_pdf"
_UA = "research-atlas/1.0 (offline figure extraction; mailto:%s)"


def _arxiv_pdf_url(arxiv_id: str) -> str:
    # arXiv ids may carry a version suffix already; the bare id resolves to the latest.
    return f"https://arxiv.org/pdf/{arxiv_id}"


def _download_pdf(client: httpx.Client, arxiv_id: str) -> str | None:
    """Fetch the PDF to the on-disk cache; return the path, or None on failure."""
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    safe = arxiv_id.replace("/", "_")
    path = PDF_CACHE / f"{safe}.pdf"
    if path.exists() and path.stat().st_size > 0:
        return str(path)
    try:
        resp = client.get(_arxiv_pdf_url(arxiv_id), follow_redirects=True, timeout=60.0)
        if resp.status_code != 200 or not resp.content:
            log.warn(f"arxiv {arxiv_id}: HTTP {resp.status_code}")
            return None
        path.write_bytes(resp.content)
        return str(path)
    except Exception as e:  # noqa: BLE001 - network hiccup: skip this paper, keep going
        log.warn(f"arxiv {arxiv_id}: download failed ({e})")
        return None


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s13_figures")

    fc = cfg.figures
    if not fc.enabled:
        log.info("figures.enabled = false → skipping (frontend uses client-side fallback)")
        # Still write an (empty) index so s11 has a deterministic input.
        if not OUT_INDEX.exists():
            OUT_INDEX.write_text(json.dumps({"node_ids": []}))
        return str(OUT_INDEX)

    corpus = pl.read_parquet(CORPUS_ACTIVE)
    # Papers with an arXiv id, most-cited first (bake the high-value figures first so a
    # capped run covers the papers users are most likely to open).
    have = (
        corpus.filter(pl.col("arxiv_id").is_not_null() & (pl.col("arxiv_id") != ""))
        .select(["node_id", "arxiv_id", "cited_by_count"])
        .sort("cited_by_count", descending=True)
    )
    total_arxiv = have.height
    if fc.max_papers > 0:
        have = have.head(fc.max_papers)
    log.info(f"{total_arxiv} papers with an arXiv id; processing {have.height} "
             f"(delay={fc.request_delay}s, cap={fc.max_papers or 'none'})")

    figure_dir = WEB_DATA_DIR / S.FIGURES_DIR
    have_figure: list[int] = []
    # Pre-seed with any crops already on disk (idempotent resume).
    rows = list(zip(have["node_id"].to_list(), have["arxiv_id"].to_list()))

    headers = {"User-Agent": _UA % (cfg.secrets.openalex_mailto or "unknown")}
    n_ok = n_none = n_fail = 0
    with httpx.Client(headers=headers) as client:
        for i, (node_id, arxiv_id) in enumerate(rows):
            out_path = WEB_DATA_DIR / S.figure_path(node_id)
            if out_path.exists() and out_path.stat().st_size > 0:
                have_figure.append(node_id)
                n_ok += 1
                continue

            pdf_path = _download_pdf(client, arxiv_id)
            # Respect arXiv's rate limit only when we actually hit the network.
            if pdf_path is not None:
                pass
            time.sleep(fc.request_delay)
            if pdf_path is None:
                n_fail += 1
                continue

            crop = find_first_figure(pdf_path, max_pages=fc.max_pages)
            if crop is None:
                n_none += 1
                continue
            png = render_crop(pdf_path, crop, scale=fc.scale)
            if not png:
                n_none += 1
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(png)
            have_figure.append(node_id)
            n_ok += 1

            if (i + 1) % 50 == 0:
                log.info(f"  {i + 1}/{len(rows)}  ok={n_ok} none={n_none} fail={n_fail}")

    OUT_INDEX.write_text(json.dumps({"node_ids": sorted(have_figure)}))
    log.info(f"baked {len(have_figure)} crops → {figure_dir} "
             f"(ok={n_ok} no-figure={n_none} download-fail={n_fail}); index → {OUT_INDEX}")
    return str(OUT_INDEX)


if __name__ == "__main__":
    run()
