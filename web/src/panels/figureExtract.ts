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

// Turn a caption position into a crop rect for the figure/table body. The body is the block
// directly ABOVE the caption, within the caption's column. We estimate column width from the
// caption's x (single- vs two-column) and cap the block height so we don't grab the whole
// page. These are heuristics tuned on arXiv two-column + single-column layouts.
function cropRectFor(cap: Caption): FigureCrop["rect"] {
  const { x, y, pageWidth, pageHeight } = cap;
  const margin = 0.08 * pageWidth; // typical page margin
  const midX = pageWidth / 2;
  // Column bounds: if the caption starts left of center it's the left column (or full width
  // for single-column); we detect two-column by whether a caption ever starts right of center.
  const isRightColumn = x > midX;
  const colLeft = isRightColumn ? midX + margin * 0.25 : margin;
  const colRight = isRightColumn ? pageWidth - margin : (x > margin * 1.5 && x < midX ? midX - margin * 0.25 : pageWidth - margin);
  const left = Math.min(x - 4, colLeft);
  const width = Math.max(colRight - left, 0.3 * pageWidth);
  // Figure body sits above the caption baseline. Take a generous band up-page, capped.
  const bottom = y + 2; // just above the caption line
  const maxHeight = 0.55 * pageHeight;
  const top = Math.min(bottom + maxHeight, pageHeight - margin * 0.5);
  return { x: left, y: bottom, width, height: top - bottom };
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
  cssWidth: number,
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
    const rect = cropRectFor(cap);

    // Pass 2: render just the crop region of that page.
    const page = await doc.getPage(cap.pageNumber);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const scale = (cssWidth / rect.width) * dpr;
    const full = page.getViewport({ scale });
    const offsetX = rect.x * scale;
    // pdf.js device y grows downward; crop top in user space = rect.y + rect.height.
    const pageHeightUser = page.getViewport({ scale: 1 }).height;
    const offsetY = (pageHeightUser - (rect.y + rect.height)) * scale;

    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no 2d context");
    canvas.width = Math.ceil(rect.width * scale);
    canvas.height = Math.ceil(rect.height * scale);
    canvas.style.width = `${(rect.width * scale) / dpr}px`;
    canvas.style.height = `${(rect.height * scale) / dpr}px`;
    await page.render({
      canvasContext: ctx,
      viewport: full,
      transform: [1, 0, 0, 1, -offsetX, -offsetY],
    }).promise;

    return { pageNumber: cap.pageNumber, rect, label };
  } finally {
    doc.destroy();
  }
}
