// Extract "Figure 1" (or "Table 1") from an arXiv PDF and return the crop rectangle for it,
// entirely client-side via pdf.js. arXiv serves PDFs with `access-control-allow-origin: *`,
// so this needs no backend.
//
// Approach — anchor on the CAPTION, not on embedded images:
//   1. Read the text layer of the first few pages; find the earliest real caption matching
//      "Figure 1" / "Fig. 1" / "Table 1" (a caption starts a text block, so we ignore
//      mid-line body-text mentions like "...see Table 1 for...").
//   2. The caption's bounding box tells us the column and the split point. By convention a
//      FIGURE sits ABOVE its caption and a TABLE sits ABOVE its caption too in most arXiv
//      papers, but tables are also frequently captioned on top; we crop the block adjacent
//      to the caption within its column, biased above, which captures the common cases.
//   3. Return { pageNumber, rect } in PDF user-space; the caller renders that page and crops.
//
// This targets the paper's actual labeled Figure 1 (a raster OR vector figure, or a table),
// which "grab the first embedded image" cannot do — most ML tables and many figures are
// vector, and the first bitmap is often a logo.

export interface FigureCrop {
  pageNumber: number;
  // Crop rectangle in PDF user-space (origin bottom-left, as pdf.js reports text positions).
  rect: { x: number; y: number; width: number; height: number };
  label: string; // e.g. "Figure 1"
}

// A caption line: "Figure 1:", "Figure 1.", "Fig. 1 ", "Table 1:" — case-insensitive, and
// must be followed by a separator/space so "Figure 10" doesn't match "Figure 1".
const CAPTION_RE = /^(figure|fig\.?|table)\s*1\s*([.:]\s|\s|$)/i;

interface TextItem {
  str: string;
  // pdf.js transform: [a, b, c, d, e, f]; e = x, f = y (user space, bottom-left origin).
  transform: number[];
  width: number;
  height: number;
}

interface Caption {
  pageNumber: number;
  isTable: boolean;
  x: number; // caption baseline left
  y: number; // caption baseline
  pageWidth: number;
  pageHeight: number;
}

// Find the first text item whose OWN string starts with "Figure 1"/"Table 1". The `^`
// anchor is the real signal: pdf.js emits a caption's leading run as its own item starting
// with the label, whereas a body-text mention ("...see Table 1...") lives inside an item
// that starts with other words, so it won't match. (An earlier line-start heuristic based
// on previous-item geometry wrongly rejected real captions — pdf.js often reports the
// caption item's x equal to the previous item's right edge.)
function findFirstCaption(
  items: TextItem[],
  pageNumber: number,
  pageWidth: number,
  pageHeight: number,
): Caption | null {
  for (const it of items) {
    const s = (it.str || "").trim();
    if (s && CAPTION_RE.test(s)) {
      return {
        pageNumber,
        isTable: /^table/i.test(s),
        x: it.transform[4],
        y: it.transform[5],
        pageWidth,
        pageHeight,
      };
    }
  }
  return null;
}

// A generous SEARCH band above the caption, within its column, to render and then trim by
// pixel analysis. Wider/taller than the figure on purpose — findInkBounds() tightens it to
// the actual figure box. Estimates the column from the caption's x (two-column arXiv puts a
// right-column caption past mid-page; single-column spans the text width).
function searchBand(cap: Caption): FigureCrop["rect"] {
  const { x, y, pageWidth, pageHeight } = cap;
  const margin = 0.08 * pageWidth;
  const midX = pageWidth / 2;
  const isRightColumn = x > midX;
  const colLeft = isRightColumn ? midX + margin * 0.25 : margin;
  const colRight = isRightColumn
    ? pageWidth - margin
    : x > margin * 1.5 && x < midX
      ? midX - margin * 0.25
      : pageWidth - margin;
  const left = Math.min(x - 4, colLeft);
  const width = Math.max(colRight - left, 0.3 * pageWidth);
  // Start the band ABOVE the caption's own text — the caption baseline is `y`, and its glyphs
  // rise ~1 line above it. If the band included the caption, the upward ink scan would grab
  // the caption strip and stop at the gap between caption and figure. A ~1.4-line skip clears
  // the caption (and any 2-line caption's first line is fine — we want the figure, not text).
  const captionSkip = 0.026 * pageHeight; // ≈ one text line on a US-letter page
  const bottom = y + captionSkip;
  const maxHeight = 0.72 * pageHeight; // generous: trimmed by ink analysis
  const top = Math.min(bottom + maxHeight, pageHeight - margin * 0.4);
  return { x: left, y: bottom, width, height: top - bottom };
}

// Given a rendered band (device pixels, caption at the BOTTOM edge), find the tight bounding
// box of the figure/table by ink analysis. Papers put a whitespace gap between a figure and
// whatever sits above it (body text, a section header, the page title), so:
//   - scan rows upward from the bottom; accumulate the figure while rows have ink, and STOP
//     at the first sustained whitespace gap — that excludes the ICLR-style header / title;
//   - then trim left/right to the inked columns so a narrow figure isn't boxed in whitespace.
// Returns bounds in device px within the band, or null if the band is essentially empty.
interface PxBounds { top: number; bottom: number; left: number; right: number; }
function findInkBounds(data: Uint8ClampedArray, w: number, h: number): PxBounds | null {
  // "Ink" = a pixel darker than near-white. arXiv pages are white; figures/tables/text ink.
  const isInk = (i: number) => data[i] < 245 || data[i + 1] < 245 || data[i + 2] < 245;

  const rowInk = new Float32Array(h);
  const colInk = new Float32Array(w);
  for (let yy = 0; yy < h; yy++) {
    let count = 0;
    const base = yy * w * 4;
    for (let xx = 0; xx < w; xx++) {
      if (isInk(base + xx * 4)) {
        count++;
        colInk[xx]++;
      }
    }
    rowInk[yy] = count / w; // fraction of the row that is inked
  }

  const ROW_INK_MIN = 0.006; // a row with less ink than this counts as blank
  const GAP_ROWS = Math.max(6, Math.round(h * 0.035)); // sustained-blank run that ends a block

  // Walk up from the bottom (caption side). Skip a small initial blank margin, take the
  // inked block, stop at the first GAP_ROWS-long blank run above it.
  let bottom = h - 1;
  while (bottom > 0 && rowInk[bottom] < ROW_INK_MIN) bottom--;
  if (bottom <= 0) return null;
  let top = bottom;
  let blank = 0;
  for (let yy = bottom; yy >= 0; yy--) {
    if (rowInk[yy] < ROW_INK_MIN) {
      blank++;
      if (blank >= GAP_ROWS) break;
    } else {
      blank = 0;
      top = yy;
    }
  }

  // Trim horizontally to inked columns within [top, bottom].
  let left = 0;
  while (left < w - 1 && colInk[left] < 1) left++;
  let right = w - 1;
  while (right > left && colInk[right] < 1) right--;

  if (right - left < 8 || bottom - top < 8) return null;
  // Small padding so we don't clip antialiased edges.
  const pad = Math.round(h * 0.008);
  return {
    top: Math.max(0, top - pad),
    bottom: Math.min(h - 1, bottom + pad),
    left: Math.max(0, left - pad),
    right: Math.min(w - 1, right + pad),
  };
}

/**
 * Locate Figure 1 / Table 1 in a PDF and render its crop into `canvas`, opening the ~2MB PDF
 * ONCE (find + render share the parsed doc). Prefers a figure over a table (figures usually
 * appear first and convey the gist) and the earliest page. Returns the found crop, or null if
 * no caption was located in the first `maxPages` pages (caller then renders nothing).
 */
export async function extractFirstFigure(
  pdfUrl: string,
  canvas: HTMLCanvasElement,
  signal: AbortSignal,
  maxPages = 8,
): Promise<FigureCrop | null> {
  const pdfjs = await import("pdfjs-dist");
  const worker = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default;

  const task = pdfjs.getDocument({ url: pdfUrl });
  signal.addEventListener("abort", () => task.destroy(), { once: true });
  const doc = await task.promise;
  try {
    // Pass 1: find the caption. Keep the pdf.js page around for the winner so render reuses it.
    let chosen: { cap: Caption; label: string } | null = null;
    let firstTable: Caption | null = null;
    const pages = Math.min(maxPages, doc.numPages);
    for (let p = 1; p <= pages && !chosen; p++) {
      if (signal.aborted) return null;
      const page = await doc.getPage(p);
      const vp = page.getViewport({ scale: 1 });
      const cap = findFirstCaption((await page.getTextContent()).items as TextItem[], p, vp.width, vp.height);
      if (cap && !cap.isTable) chosen = { cap, label: "Figure 1" };
      else if (cap && !firstTable) firstTable = cap;
    }
    if (!chosen && firstTable) chosen = { cap: firstTable, label: "Table 1" };
    if (!chosen || signal.aborted) return null;

    const { cap, label } = chosen;
    const band = searchBand(cap);

    // Pass 2: render the (generous) search band to an OFFSCREEN canvas, detect the figure's
    // tight pixel bounds by ink analysis (excludes the header/title above the real figure),
    // then blit just that region into the display canvas at high resolution. The display
    // canvas has no inline width, so CSS width:100% scales the crisp bitmap to the panel.
    const page = await doc.getPage(cap.pageNumber);
    const RENDER_WIDTH = 900; // logical px; ample for a widened panel on hi-dpi screens
    const scale = (RENDER_WIDTH / band.width) * Math.min(window.devicePixelRatio || 1, 2);
    const full = page.getViewport({ scale });
    const offsetX = band.x * scale;
    const pageHeightUser = page.getViewport({ scale: 1 }).height;
    const offsetY = (pageHeightUser - (band.y + band.height)) * scale;

    const bandW = Math.ceil(band.width * scale);
    const bandH = Math.ceil(band.height * scale);
    const off = document.createElement("canvas");
    off.width = bandW;
    off.height = bandH;
    const offCtx = off.getContext("2d", { willReadFrequently: true });
    if (!offCtx) throw new Error("no 2d context");
    // White backdrop so transparent PDF pixels read as page-white for the ink test.
    offCtx.fillStyle = "#ffffff";
    offCtx.fillRect(0, 0, bandW, bandH);
    await page.render({
      canvasContext: offCtx,
      viewport: full,
      transform: [1, 0, 0, 1, -offsetX, -offsetY],
    }).promise;
    if (signal.aborted) return null;

    const bounds =
      findInkBounds(offCtx.getImageData(0, 0, bandW, bandH).data, bandW, bandH) ?? {
        top: 0,
        bottom: bandH - 1,
        left: 0,
        right: bandW - 1,
      };
    const cw = bounds.right - bounds.left + 1;
    const ch = bounds.bottom - bounds.top + 1;

    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no 2d context");
    canvas.width = cw;
    canvas.height = ch;
    ctx.drawImage(off, bounds.left, bounds.top, cw, ch, 0, 0, cw, ch);

    return { pageNumber: cap.pageNumber, rect: band, label };
  } finally {
    doc.destroy();
  }
}
