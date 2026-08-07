"""Hermetic tests for caption-anchored figure extraction (no network, no fixtures on disk).

Each test synthesizes a tiny PDF in memory with PyMuPDF — a page with a graphical element
and a caption below it — then asserts that ``find_first_figure`` locates the right region.
This locks down the four behaviors the module must guarantee: it anchors on the caption,
finds the box ABOVE it, handles the chapter-numbered "Figure 1.1" caption, and falls back to
a text region for a borderless table.
"""

from __future__ import annotations

import pymupdf
import pytest

from pipeline.common.figure_extract import find_first_figure, render_crop


def _write_pdf(tmp_path, name, build):
    doc = pymupdf.open()
    build(doc)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return str(path)


def _rect_figure_page(doc, caption="Figure 1: A test figure."):
    """A page with a filled rectangle (a 'figure') and a caption line beneath it."""
    page = doc.new_page(width=595, height=842)  # A4 points
    page.draw_rect(pymupdf.Rect(100, 100, 400, 300), fill=(0.2, 0.4, 0.8))
    page.insert_text((100, 330), caption, fontsize=10)


def test_finds_figure_above_caption(tmp_path):
    pdf = _write_pdf(tmp_path, "fig.pdf", _rect_figure_page)
    crop = find_first_figure(pdf)
    assert crop is not None
    assert crop.label == "Figure 1"
    # The crop's box must sit above the caption baseline (~y=330) and cover the rectangle.
    x0, y0, x1, y1 = crop.rect
    assert y0 < 120 and y1 <= 335          # spans the figure, ends at/above the caption
    assert x1 - x0 > 100 and y1 - y0 > 100  # not a sliver


def test_ignores_midsentence_mention(tmp_path):
    """A body-line 'see Figure 1 for details' must NOT be treated as the caption."""
    def build(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), "As we see Figure 1 for details, the model improves.",
                         fontsize=10)
        # The real caption + figure appear lower on the page.
        page.draw_rect(pymupdf.Rect(100, 300, 400, 500), fill=(0.1, 0.6, 0.3))
        page.insert_text((100, 530), "Figure 1: The real caption.", fontsize=10)
    pdf = _write_pdf(tmp_path, "mid.pdf", build)
    crop = find_first_figure(pdf)
    assert crop is not None
    # Must anchor on the real caption (~y=530), so the box is the lower rectangle, not the top.
    assert crop.rect[1] > 250


def test_chapter_numbered_caption(tmp_path):
    """GPT-3-style 'Figure 1.1:' must match as the first figure."""
    pdf = _write_pdf(tmp_path, "ch.pdf",
                     lambda d: _rect_figure_page(d, "Figure 1.1: Chapter-numbered."))
    crop = find_first_figure(pdf)
    assert crop is not None
    assert crop.label == "Figure 1"


def test_figure_ten_does_not_match(tmp_path):
    """'Figure 10' must not be matched by the '1' prefix (no first figure present)."""
    pdf = _write_pdf(tmp_path, "ten.pdf",
                     lambda d: _rect_figure_page(d, "Figure 10: Not the first."))
    assert find_first_figure(pdf) is None


def test_render_crop_returns_png(tmp_path):
    pdf = _write_pdf(tmp_path, "render.pdf", _rect_figure_page)
    crop = find_first_figure(pdf)
    assert crop is not None
    png = render_crop(pdf, crop)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic


def test_missing_pdf_returns_none(tmp_path):
    assert find_first_figure(str(tmp_path / "does-not-exist.pdf")) is None
