// Extract "Figure 1" AND "Table 1" from an arXiv PDF and render each crop, entirely
// client-side via pdf.js. arXiv serves PDFs with `access-control-allow-origin: *`, so this
// needs no backend. Runs on demand when a paper is selected.
//
// Method — the PDFFigures 2.0 / PyMuPDF pattern, ported to pdf.js's operator list (the
// accurate replacement for the old ink-density scan, which grabbed page headers and body
// text):
//   1. Find the caption ("Figure 1:" / "Table 1:") in the text layer — its page, position.
//   2. Reconstruct the page's GRAPHICAL geometry from the operator list: walk fnArray tracking
//      the transform stack (CTM), take the bounding box of every path-construction op and every
//      image op, then CLUSTER boxes that touch/overlap into figure-sized regions (the way
//      PyMuPDF's cluster_drawings joins vector paths into one diagram box).
//   3. Choose the cluster directly ABOVE the caption, overlapping its column, largest+closest.
//   4. Borderless tables have no paths/images, so fall back to the contiguous TEXT block above
//      the caption (row lines stacked over it), stopping at the first large vertical gap.
//   5. Render that user-space rect to the crop canvas.
//
// Coordinates: pdf.js operator-list geometry and text transforms are BOTH in PDF user space
// (origin bottom-left, y increases UP), at scale 1 — no viewport flip. "Above the caption"
// therefore means a larger y than the caption baseline.

export interface FigureCrop {
  pageNumber: number;
  rect: { x: number; y: number; width: number; height: number }; // PDF user space
  label: string; // "Figure 1" | "Table 1"
}

// "Figure 1:" / "Fig. 1" / "Table 1." / chapter-numbered "Figure 1.1:" — a separator must
// follow so "Figure 10" / "Figure 2.x" don't match. Anchored so a mid-sentence "see Table 1"
// (inside an item starting with other words) doesn't match.
const CAPTION_RE = /^(figure|fig\.?|table)\s*1(\.\d+)?\s*([.:]\s|\s|$)/i;

interface TextItem {
  str: string;
  transform: number[]; // [a,b,c,d,e,f]; e=x, f=y (user space)
  width: number;
  height: number;
}

interface Caption {
  x: number; // baseline left
  y: number; // baseline (user space, y-up)
  width: number;
  isTable: boolean;
}

interface Box {
  x0: number; y0: number; x1: number; y1: number;
  kind: "path" | "image" | "text";
}

// --- affine helpers (pdf.js transform arrays: [a,b,c,d,e,f]) ---
function mul(a: number[], b: number[]): number[] {
  return [
    a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
    a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
    a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5],
  ];
}
function applyPt(m: number[], x: number, y: number): [number, number] {
  return [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
}

// The first caption ("Figure 1"/"Table 1") on a page whose own text item starts with the
// label. Returns the FIRST figure and the FIRST table found (either may be null).
function findCaptions(items: TextItem[]): { figure: Caption | null; table: Caption | null } {
  let figure: Caption | null = null;
  let table: Caption | null = null;
  for (const it of items) {
    const s = (it.str || "").trim();
    if (!s || !CAPTION_RE.test(s)) continue;
    const cap: Caption = { x: it.transform[4], y: it.transform[5], width: it.width || 300, isTable: /^table/i.test(s) };
    if (cap.isTable) { if (!table) table = cap; }
    else if (!figure) figure = cap;
  }
  return { figure, table };
}

// Bounding boxes of all path + image ops on a page, in user space. `OPS` is pdfjs.OPS.
async function graphicBoxes(page: any, OPS: any): Promise<Box[]> {
  const ops = await page.getOperatorList();
  let ctm = [1, 0, 0, 1, 0, 0];
  const stack: number[][] = [];
  const out: Box[] = [];
  for (let i = 0; i < ops.fnArray.length; i++) {
    const fn = ops.fnArray[i];
    const args = ops.argsArray[i];
    if (fn === OPS.save) stack.push(ctm.slice());
    else if (fn === OPS.restore) ctm = stack.pop() || [1, 0, 0, 1, 0, 0];
    else if (fn === OPS.transform) ctm = mul(ctm, args);
    else if (fn === OPS.constructPath) {
      const coords: number[] | undefined = args[1];
      if (!coords) continue;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (let k = 0; k + 1 < coords.length; k += 2) {
        const [X, Y] = applyPt(ctm, coords[k], coords[k + 1]);
        if (X < x0) x0 = X; if (Y < y0) y0 = Y; if (X > x1) x1 = X; if (Y > y1) y1 = Y;
      }
      if (x1 > x0 && y1 > y0) out.push({ x0, y0, x1, y1, kind: "path" });
    } else if (
      fn === OPS.paintImageXObject || fn === OPS.paintInlineImageXObject ||
      fn === OPS.paintImageXObjectRepeat
    ) {
      const c = [applyPt(ctm, 0, 0), applyPt(ctm, 1, 0), applyPt(ctm, 0, 1), applyPt(ctm, 1, 1)];
      const xs = c.map((p) => p[0]);
      const ys = c.map((p) => p[1]);
      out.push({ x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys), kind: "image" });
    }
  }
  return out;
}

// Join boxes that touch/overlap (within `pad`) into clusters — turns a diagram's hundreds of
// vector strokes into one figure box, like PyMuPDF's cluster_drawings.
function clusterBoxes(boxes: Box[], pad = 6): Box[] {
  const out = boxes.map((b) => ({ ...b }));
  let merged = true;
  while (merged) {
    merged = false;
    for (let i = 0; i < out.length; i++) {
      for (let j = i + 1; j < out.length; j++) {
        const a = out[i];
        const b = out[j];
        if (a.x0 - pad <= b.x1 && b.x0 - pad <= a.x1 && a.y0 - pad <= b.y1 && b.y0 - pad <= a.y1) {
          a.x0 = Math.min(a.x0, b.x0); a.y0 = Math.min(a.y0, b.y0);
          a.x1 = Math.max(a.x1, b.x1); a.y1 = Math.max(a.y1, b.y1);
          if (b.kind === "image") a.kind = "image";
          out.splice(j, 1); merged = true; j--;
        }
      }
    }
  }
  return out;
}

// The graphic cluster directly ABOVE the caption, overlapping its column, largest+closest.
function pickBox(boxes: Box[], cap: Caption): Box | null {
  const colLo = cap.x - 30;
  const colHi = cap.x + cap.width + 30;
  let best: Box | null = null;
  let bestScore = -Infinity;
  for (const r of boxes) {
    const w = r.x1 - r.x0;
    const h = r.y1 - r.y0;
    if (w < 40 || h < 20) continue;
    if (r.y0 < cap.y - 4) continue; // must sit above the caption baseline (user space y-up)
    const overlap = Math.min(r.x1, colHi) - Math.max(r.x0, colLo);
    if (overlap <= 0) continue;
    const gap = r.y0 - cap.y; // vertical distance caption → box bottom
    const score = w * h - gap * 50; // big + close wins
    if (score > bestScore) { bestScore = score; best = r; }
  }
  return best;
}

// Borderless-table fallback: the contiguous block of TEXT lines directly above the caption
// (invisible to path/image detection). Walk text items above the caption in its column, join
// while the vertical gap stays small, stop at the first large gap.
function textBlockAbove(items: TextItem[], cap: Caption): Box | null {
  const colLo = cap.x - 30;
  const colHi = cap.x + cap.width + 30;
  const above = items
    .map((it) => ({ x: it.transform[4], y: it.transform[5], w: it.width || 0, h: it.height || 8 }))
    .filter((t) => t.y > cap.y + 2 && Math.min(t.x + t.w, colHi) - Math.max(t.x, colLo) > 0)
    .sort((a, b) => a.y - b.y); // nearest-to-caption first, going up
  if (!above.length) return null;
  const gapLimit = Math.max(18, (above[0].h || 8) * 3);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  let prevY = cap.y;
  for (const t of above) {
    if (t.y - prevY > gapLimit) break; // sustained whitespace → top of the table
    x0 = Math.min(x0, t.x); y0 = Math.min(y0, t.y);
    x1 = Math.max(x1, t.x + t.w); y1 = Math.max(y1, t.y + t.h);
    prevY = t.y;
  }
  if (x1 - x0 < 40 || y1 - y0 < 20) return null;
  return { x0, y0, x1, y1, kind: "text" };
}

// Locate one caption's crop rect (graphic cluster above it, else text-block fallback).
function locateCrop(cap: Caption, boxes: Box[], items: TextItem[]): Box | null {
  const box = pickBox(boxes, cap) ?? (cap.isTable ? textBlockAbove(items, cap) : null);
  if (!box) return null;
  // Include the caption strip beneath the box, for context.
  return { x0: Math.min(box.x0, cap.x), y0: cap.y - 4, x1: Math.max(box.x1, cap.x + cap.width), y1: box.y1, kind: box.kind };
}

async function renderRect(page: any, box: Box, canvas: HTMLCanvasElement): Promise<void> {
  const RENDER_WIDTH = 900; // logical px; ample for a widened panel on hi-dpi screens
  const w = box.x1 - box.x0;
  const scale = (RENDER_WIDTH / Math.max(w, 1)) * Math.min(window.devicePixelRatio || 1, 2);
  const full = page.getViewport({ scale });
  const pageH = page.getViewport({ scale: 1 }).height;
  const offsetX = box.x0 * scale;
  // user-space y-up → device y-down: device top = (pageH - box.y1)
  const offsetY = (pageH - box.y1) * scale;
  const cw = Math.ceil((box.x1 - box.x0) * scale);
  const ch = Math.ceil((box.y1 - box.y0) * scale);
  canvas.width = cw;
  canvas.height = ch;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no 2d context");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, cw, ch);
  await page.render({
    canvasContext: ctx,
    viewport: full,
    transform: [1, 0, 0, 1, -offsetX, -offsetY],
  }).promise;
}

export interface ExtractResult {
  figure: FigureCrop | null;
  table: FigureCrop | null;
}

/**
 * Locate Figure 1 AND Table 1 in a PDF and render each into its canvas (pass a canvas for the
 * one(s) you want; null skips rendering that crop). Opens the PDF once. Returns which crops
 * were found so the caller can hide empty slots.
 */
export async function extractFigures(
  pdfUrl: string,
  canvases: { figure: HTMLCanvasElement | null; table: HTMLCanvasElement | null },
  signal: AbortSignal,
  maxPages = 10,
): Promise<ExtractResult> {
  const pdfjs = await import("pdfjs-dist");
  const worker = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
  const OPS = (pdfjs as any).OPS;

  const task = pdfjs.getDocument({ url: pdfUrl });
  signal.addEventListener("abort", () => task.destroy(), { once: true });
  const doc = await task.promise;
  const result: ExtractResult = { figure: null, table: null };
  try {
    let figCap: { page: number; cap: Caption } | null = null;
    let tblCap: { page: number; cap: Caption } | null = null;
    const pages = Math.min(maxPages, doc.numPages);
    for (let p = 1; p <= pages; p++) {
      if (signal.aborted) return result;
      const page = await doc.getPage(p);
      const items = (await page.getTextContent()).items as TextItem[];
      const { figure, table } = findCaptions(items);
      if (figure && !figCap) figCap = { page: p, cap: figure };
      if (table && !tblCap) tblCap = { page: p, cap: table };
      if (figCap && tblCap) break;
    }

    // Render each requested + found crop.
    for (const which of ["figure", "table"] as const) {
      const found = which === "figure" ? figCap : tblCap;
      const canvas = canvases[which];
      if (!found || !canvas || signal.aborted) continue;
      const page = await doc.getPage(found.page);
      const items = (await page.getTextContent()).items as TextItem[];
      const boxes = clusterBoxes(await graphicBoxes(page, OPS));
      const crop = locateCrop(found.cap, boxes, items);
      if (!crop) continue;
      await renderRect(page, crop, canvas);
      const rect = { x: crop.x0, y: crop.y0, width: crop.x1 - crop.x0, height: crop.y1 - crop.y0 };
      result[which] = { pageNumber: found.page, rect, label: which === "figure" ? "Figure 1" : "Table 1" };
    }
    return result;
  } finally {
    doc.destroy();
  }
}
