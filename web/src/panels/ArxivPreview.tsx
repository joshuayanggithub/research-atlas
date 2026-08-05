// Paper preview. arXiv sends X-Frame-Options / CSP frame-ancestors, so its PDF cannot be
// embedded in an <iframe> (the previous approach stayed blank). Instead we render the PDF's
// FIRST PAGE to a canvas with pdf.js — arXiv serves the PDF with `access-control-allow-origin: *`
// so a cross-origin fetch + client-side render works with no backend. That first page shows
// the title, authors, and usually the opening figure/teaser. When there is no arXiv PDF, or a
// render fails, we fall back to a rich text card (TLDR + abstract from Semantic Scholar, which
// is also CORS-enabled) so the tab always shows something useful.

import {
  ExternalLink,
  FileSearch,
  FileText,
  LoaderCircle,
  RotateCw,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  arxivPath,
  cachedResolve,
  dropCached,
  fetchAbstract,
  type Resolved,
} from "./resolveArxiv";

type LookupState =
  | { status: "resolving" }
  | { status: "ready"; info: Resolved }
  | { status: "error"; message: string };

// Render the first page of an arXiv PDF into the given canvas via pdf.js. The worker is
// loaded from the bundled dist so there's no CDN dependency.
async function renderFirstPage(
  pdfUrl: string,
  canvas: HTMLCanvasElement,
  signal: AbortSignal,
): Promise<void> {
  const pdfjs = await import("pdfjs-dist");
  const worker = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default;

  const task = pdfjs.getDocument({ url: pdfUrl });
  signal.addEventListener("abort", () => task.destroy(), { once: true });
  const doc = await task.promise;
  // Always release the parsed document + worker buffers, even on success — otherwise every
  // paper preview leaks a PDFDocumentProxy for the session.
  try {
    if (signal.aborted) return;
    const page = await doc.getPage(1);
    if (signal.aborted) return;

    const cssWidth = canvas.clientWidth || 380;
    const unscaled = page.getViewport({ scale: 1 });
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const scale = (cssWidth / unscaled.width) * dpr;
    const viewport = page.getViewport({ scale });
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no 2d context");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    canvas.style.height = `${viewport.height / dpr}px`;
    await page.render({ canvasContext: ctx, viewport }).promise;
  } finally {
    doc.destroy();
  }
}

export function ArxivPreview({
  arxivId,
  doi,
  title,
}: {
  arxivId: string | null;
  doi: string | null;
  title: string;
}) {
  const [attempt, setAttempt] = useState(0);
  const [lookup, setLookup] = useState<LookupState>({ status: "resolving" });
  const [pageState, setPageState] = useState<"idle" | "rendering" | "done" | "failed">("idle");
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Step 1: resolve arXiv id + abstract/TLDR (shared resolver — honors a local id, else S2).
  useEffect(() => {
    let active = true;
    setLookup({ status: "resolving" });
    cachedResolve(arxivId, doi, title)
      .then((info) => active && setLookup({ status: "ready", info }))
      .catch((error: unknown) =>
        active &&
        setLookup({
          status: "error",
          message: error instanceof Error ? error.message : "Paper lookup failed",
        }),
      );
    return () => {
      active = false;
    };
  }, [attempt, arxivId, doi, title]);

  const resolvedArxiv = lookup.status === "ready" ? lookup.info.arxivId : null;

  // Step 2: render the first PDF page once we have an arXiv id.
  useEffect(() => {
    if (!resolvedArxiv || !canvasRef.current) return;
    const controller = new AbortController();
    setPageState("rendering");
    renderFirstPage(
      `https://arxiv.org/pdf/${arxivPath(resolvedArxiv)}`,
      canvasRef.current,
      controller.signal,
    )
      .then(() => !controller.signal.aborted && setPageState("done"))
      .catch(() => !controller.signal.aborted && setPageState("failed"));
    return () => controller.abort();
  }, [resolvedArxiv, attempt]);

  // If the PDF render failed and we skipped S2 (direct arXiv id), lazily fetch abstract text
  // so the fallback card still has something to show.
  useEffect(() => {
    if (pageState !== "failed" || lookup.status !== "ready" || lookup.info.abstract || lookup.info.tldr) {
      return;
    }
    let active = true;
    fetchAbstract(resolvedArxiv ?? "")
      .then((info) => {
        if (!active || (!info.abstract && !info.tldr)) return;
        setLookup((s) =>
          s.status === "ready"
            ? { status: "ready", info: { ...s.info, tldr: info.tldr, abstract: info.abstract } }
            : s,
        );
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [pageState, lookup, resolvedArxiv]);

  const searchUrl = `https://arxiv.org/search/?query=${encodeURIComponent(title)}&searchtype=title`;
  const retry = () => {
    dropCached(arxivId, doi, title);
    setPageState("idle");
    setAttempt((v) => v + 1);
  };

  const abstractText =
    lookup.status === "ready" ? lookup.info.tldr ?? lookup.info.abstract : null;
  const abstractIsTldr = lookup.status === "ready" && !!lookup.info.tldr;

  return (
    <section className="arxiv-preview" aria-labelledby="paper-preview-heading">
      <div className="panel-section-head">
        <h4 id="paper-preview-heading">Paper preview</h4>
        <span>{resolvedArxiv ? `arXiv ${resolvedArxiv}` : "arXiv"}</span>
      </div>

      {lookup.status === "resolving" && (
        <div className="arxiv-status" role="status">
          <LoaderCircle className="spin" size={18} aria-hidden="true" />
          Finding the paper
        </div>
      )}

      {lookup.status === "error" && (
        <>
          <div className="arxiv-status">
            <FileSearch size={18} aria-hidden="true" />
            <span><strong>{lookup.message}</strong></span>
          </div>
          <div className="arxiv-actions">
            <button type="button" onClick={retry}>
              Retry <RotateCw size={13} aria-hidden="true" />
            </button>
            <a href={searchUrl} target="_blank" rel="noreferrer">
              Search arXiv <ExternalLink size={13} aria-hidden="true" />
            </a>
          </div>
        </>
      )}

      {lookup.status === "ready" && (
        <>
          {resolvedArxiv && (
            <div className="arxiv-actions">
              <a href={`https://arxiv.org/abs/${arxivPath(resolvedArxiv)}`} target="_blank" rel="noreferrer">
                Abstract <ExternalLink size={13} aria-hidden="true" />
              </a>
              <a href={`https://arxiv.org/pdf/${arxivPath(resolvedArxiv)}`} target="_blank" rel="noreferrer">
                PDF <FileText size={13} aria-hidden="true" />
              </a>
            </div>
          )}

          {/* First-page render (hidden until it succeeds; failure falls through to text). */}
          {resolvedArxiv && pageState !== "failed" && (
            <div className="arxiv-page-shell">
              {pageState === "rendering" && (
                <div className="arxiv-loading" role="status">
                  <LoaderCircle className="spin" size={18} aria-hidden="true" />
                  Rendering first page
                </div>
              )}
              <canvas ref={canvasRef} className="arxiv-page-canvas" aria-label={`First page of ${title}`} />
            </div>
          )}

          {/* Text preview: shown when there's no arXiv PDF or the render failed. */}
          {(!resolvedArxiv || pageState === "failed") &&
            (abstractText ? (
              <div className="arxiv-abstract">
                <span className="arxiv-abstract-tag">{abstractIsTldr ? "TL;DR" : "Abstract"}</span>
                <p>{abstractText}</p>
                {pageState === "failed" && resolvedArxiv && (
                  <button type="button" className="arxiv-retry-inline" onClick={retry}>
                    Retry first-page render <RotateCw size={12} aria-hidden="true" />
                  </button>
                )}
              </div>
            ) : (
              <div className="arxiv-status">
                <FileSearch size={18} aria-hidden="true" />
                <span><strong>No preview available for this paper</strong></span>
              </div>
            ))}

          {!resolvedArxiv && (
            <div className="arxiv-actions">
              <a href={searchUrl} target="_blank" rel="noreferrer">
                Search arXiv <ExternalLink size={13} aria-hidden="true" />
              </a>
            </div>
          )}
        </>
      )}
    </section>
  );
}
