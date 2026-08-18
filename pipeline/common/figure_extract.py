"""Caption-anchored Figure 1 / Table 1 extraction from a PDF, via PyMuPDF.

This is the offline half of the "first figure at a glance" feature. Semantic Scholar
extracts figures with PDFFigures 2.0 (Clark & Divvala) — a *layout-structure* method that
anchors on the caption and finds the graphical region adjacent to it, rather than doing
computer vision on rendered pixels. We reproduce that pattern in pure Python with PyMuPDF,
which exposes the needed primitives natively:

    - ``page.find_tables()``   → table bounding boxes (the "Table 1" case, gridded figures)
    - ``page.cluster_drawings()`` → vector-figure boxes (most ML plots/diagrams are vector)
    - ``page.get_image_info()``   → raster-figure boxes (photos, screenshots)

Algorithm (mirrors PDFFigures 2.0):
    1. Find the earliest "Figure 1" / "Table 1" caption in the text layer (page + bbox).
    2. Gather candidate graphical boxes on that page from the three sources above.
    3. Pick a figure directly ABOVE its caption. For tables, first search BELOW the caption
       (the conventional table layout), then fall back above for unusual templates.
    4. Render that box (plus the caption strip, for context) to a PNG.

Prefers a figure over a table (figures usually come first and convey the gist), earliest
page. Returns ``None`` when no caption is located — the caller then bakes nothing and the
frontend falls back to the client-side pdf.js path.

This runs OFFLINE in the pipeline (PyMuPDF is Python-only). The rendered crops are baked into
the artifact bundle and served statically, so the browser never parses a PDF for papers that
have a baked crop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

# "Figure 1:" / "Fig. 1" / "Table 1." and the chapter-numbered "Figure 1.1:" (GPT-3-style).
# Figures sometimes omit punctuation ("Figure 1 A comparison"), but tables require ':' or
# '.' so a leading prose sentence such as "Table 1 reports our results" is not mistaken for
# the caption. The number must be 1 or 1.<n>, so "Figure 10" does not match.
_CAPTION_RE = re.compile(
    r"^(?:(?:figure|fig\.?)\s*1(?:\.\d+)?\s*(?:[.:]\s|\s|$)|"
    r"table\s*1(?:\.\d+)?\s*(?:[.:]\s|$))",
    re.IGNORECASE,
)

# Candidate-box filters. A real figure/table is not a sliver.
_MIN_W = 40.0
_MIN_H = 20.0


@dataclass(frozen=True)
class FigureCrop:
    page_number: int          # 0-based page index
    rect: tuple[float, float, float, float]  # crop rect in PDF points (x0,y0,x1,y1)
    label: str                # "Figure 1" | "Table 1"
    source: str               # "table" | "vector" | "raster" — which primitive found the box


@dataclass(frozen=True)
class _Caption:
    page_number: int
    rect: pymupdf.Rect
    is_table: bool


def _find_caption(doc: pymupdf.Document, max_pages: int) -> _Caption | None:
    """Earliest Figure 1 / Table 1 caption. Prefers a figure; falls back to a table."""
    fig: _Caption | None = None
    tbl: _Caption | None = None
    for pno in range(min(max_pages, doc.page_count)):
        page = doc[pno]
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"]).strip()
                if not text or not _CAPTION_RE.match(text):
                    continue
                # Keep the whole caption block, not merely its first line. Multi-line table
                # captions otherwise make the apparent caption→table gap look 40–60pt larger
                # and cause the first header rows to be missed.
                cap = _Caption(pno, pymupdf.Rect(block["bbox"]),
                               text.lower().startswith("table"))
                if not cap.is_table and fig is None:
                    fig = cap
                elif cap.is_table and tbl is None:
                    tbl = cap
        if fig is not None:  # a figure on an earlier page always wins
            break
    return fig or tbl


def _candidate_boxes(page: pymupdf.Page) -> list[tuple[str, pymupdf.Rect]]:
    """All graphical candidate boxes on the page, tagged by which primitive found them."""
    out: list[tuple[str, pymupdf.Rect]] = []
    # Tables (best for the Table-1 case and gridded figures).
    try:
        for t in page.find_tables().tables:
            out.append(("table", pymupdf.Rect(t.bbox)))
    except Exception:  # noqa: BLE001 - find_tables can raise on odd content; skip it
        pass
    # Vector-drawing clusters (ML plots/diagrams — the common arXiv figure).
    try:
        for r in page.cluster_drawings():
            out.append(("vector", pymupdf.Rect(r)))
    except Exception:  # noqa: BLE001
        pass
    # Raster images.
    try:
        for info in page.get_image_info():
            out.append(("raster", pymupdf.Rect(info["bbox"])))
    except Exception:  # noqa: BLE001
        pass
    return out


def _text_block_above(page: pymupdf.Page, cap: pymupdf.Rect) -> pymupdf.Rect | None:
    """Fallback for borderless tables: the contiguous text region directly above the caption.

    A borderless table (no ruling lines, e.g. GLUE's Table 1) is invisible to find_tables()
    and is not a drawing/image, so no graphical candidate exists. But its rows are text
    blocks stacked just above the caption. Walk blocks upward from the caption within its
    column, joining them while the vertical gap stays small, and stop at the first large gap
    (which separates the table from the body paragraph / section heading above it).
    """
    col_lo, col_hi = cap.x0 - 20, cap.x1 + 20
    blocks = []
    for b in page.get_text("dict")["blocks"]:
        if "lines" not in b:
            continue
        r = pymupdf.Rect(b["bbox"])
        if r.y1 > cap.y0 + 2:                     # above the caption only
            continue
        if min(r.x1, col_hi) - max(r.x0, col_lo) <= 0:  # in the caption's column
            continue
        blocks.append(r)
    if not blocks:
        return None
    blocks.sort(key=lambda r: r.y1, reverse=True)  # nearest-to-caption first, going up
    merged = blocks[0]
    prev_top = blocks[0].y0
    gap_limit = max(18.0, cap.height * 2.5)
    for r in blocks[1:]:
        if prev_top - r.y1 > gap_limit:            # sustained whitespace ⇒ end of the table
            break
        merged |= r
        prev_top = r.y0
    if merged.height < _MIN_H or merged.width < _MIN_W:
        return None
    return merged


def _text_block_below(page: pymupdf.Page, cap: pymupdf.Rect) -> pymupdf.Rect | None:
    """Contiguous table text directly below an above-table caption.

    Tables conventionally put their caption above the header. PyMuPDF often detects only
    isolated ruling-line fragments (RLM Table 1 is split into two small drawing clusters),
    while the text blocks cover the complete header and every row. Merge those blocks until
    a real whitespace break separates the table from the following prose.
    """
    col_lo, col_hi = cap.x0 - 20, cap.x1 + 20
    blocks = []
    for b in page.get_text("dict")["blocks"]:
        if "lines" not in b:
            continue
        r = pymupdf.Rect(b["bbox"])
        if r.y0 < cap.y1 - 2:                     # below the complete caption block only
            continue
        if min(r.x1, col_hi) - max(r.x0, col_lo) <= 0:
            continue
        blocks.append(r)
    if not blocks:
        return None
    blocks.sort(key=lambda r: r.y0)               # nearest-to-caption first, going down
    gap_limit = 18.0
    # Templates commonly leave a little more padding after the caption than between rows.
    if blocks[0].y0 - cap.y1 > 30.0:
        return None
    merged = blocks[0]
    prev_bottom = blocks[0].y1
    for r in blocks[1:]:
        if r.y0 - prev_bottom > gap_limit:        # whitespace ⇒ following body prose
            break
        merged |= r
        prev_bottom = max(prev_bottom, r.y1)
    if merged.height < _MIN_H or merged.width < _MIN_W:
        return None
    return merged


def _pick_box_below(
    cands: list[tuple[str, pymupdf.Rect]], cap: pymupdf.Rect
) -> tuple[str, pymupdf.Rect] | None:
    """Graphical table candidate directly below an above-table caption."""
    col_lo, col_hi = cap.x0 - 20, cap.x1 + 20
    best: tuple[str, pymupdf.Rect] | None = None
    best_score = -1.0
    for src, r in cands:
        if r.width < _MIN_W or r.height < _MIN_H or r.y0 < cap.y1 - 5:
            continue
        overlap = min(r.x1, col_hi) - max(r.x0, col_lo)
        if overlap <= 0:
            continue
        gap = r.y0 - cap.y1
        score = r.width * r.height - gap * 50.0
        if score > best_score:
            best, best_score = (src, r), score
    return best


def _pick_box(
    cands: list[tuple[str, pymupdf.Rect]], cap: pymupdf.Rect
) -> tuple[str, pymupdf.Rect] | None:
    """Choose the candidate directly ABOVE the caption, overlapping its column, largest.

    Scoring mirrors PDFFigures 2.0's region selection: prefer a large box close to the
    caption. A figure sits above its caption; horizontal overlap with the caption's column
    disambiguates a two-column page.
    """
    col_lo, col_hi = cap.x0 - 20, cap.x1 + 20
    best: tuple[str, pymupdf.Rect] | None = None
    best_score = -1.0
    for src, r in cands:
        if r.width < _MIN_W or r.height < _MIN_H:
            continue
        if r.y1 > cap.y0 + 5:                     # must be above the caption baseline
            continue
        overlap = min(r.x1, col_hi) - max(r.x0, col_lo)
        if overlap <= 0:                          # not in the caption's column
            continue
        gap = cap.y0 - r.y1                        # vertical distance caption ← box
        score = r.width * r.height - gap * 50.0    # big + close wins
        if score > best_score:
            best, best_score = (src, r), score
    return best


def find_first_figure(
    pdf_path: str, max_pages: int = 8
) -> FigureCrop | None:
    """Locate Figure 1 / Table 1 in ``pdf_path``; return its crop rect or None.

    Pure detection (no rendering) so it is cheap to unit-test and to run over a corpus.
    Use :func:`render_crop` to rasterize the returned rect.
    """
    try:
        doc = pymupdf.open(pdf_path)
    except Exception:  # noqa: BLE001 - unreadable/corrupt PDF → no figure
        return None
    try:
        cap = _find_caption(doc, max_pages)
        if cap is None:
            return None
        label = "Table 1" if cap.is_table else "Figure 1"
        page = doc[cap.page_number]
        candidates = _candidate_boxes(page)
        table_below = _text_block_below(page, cap.rect) if cap.is_table else None
        if cap.is_table and table_below is not None:
            # Text spans the whole table more reliably than find_tables()/drawing clusters,
            # which frequently return only its ruled subsections.
            chosen = ("text", table_below)
        elif cap.is_table:
            chosen = _pick_box_below(candidates, cap.rect)
        else:
            chosen = _pick_box(candidates, cap.rect)
        if chosen is None and cap.is_table:
            # Unusual template with the caption below the table.
            chosen = _pick_box(candidates, cap.rect)
            if chosen is None:
                box = _text_block_above(page, cap.rect)
                chosen = ("text", box) if box is not None else None
        if chosen is None:
            return None
        src, box = chosen
        # Include the caption strip in the crop for context.
        if cap.is_table and box.y0 >= cap.rect.y1 - 5:
            crop = pymupdf.Rect(
                min(box.x0, cap.rect.x0), cap.rect.y0,
                max(box.x1, cap.rect.x1), box.y1,
            )
        else:
            crop = pymupdf.Rect(
                min(box.x0, cap.rect.x0), box.y0,
                max(box.x1, cap.rect.x1), cap.rect.y1,
            )
        return FigureCrop(cap.page_number, tuple(crop), label, src)
    finally:
        doc.close()


def render_crop(pdf_path: str, crop: FigureCrop, scale: float = 2.0) -> bytes | None:
    """Render a located crop to PNG bytes at ``scale``× (2× ≈ crisp on hi-dpi)."""
    try:
        doc = pymupdf.open(pdf_path)
    except Exception:  # noqa: BLE001
        return None
    try:
        page = doc[crop.page_number]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                              clip=pymupdf.Rect(crop.rect))
        return pix.tobytes("png")
    except Exception:  # noqa: BLE001
        return None
    finally:
        doc.close()
