// Inline "gist" thumbnails on the details card: the paper's Figure 1 AND Table 1 (each shown
// when present), so you grasp the paper without opening the Paper tab.
//
// Figure source, in order:
//   1. A crop the pipeline baked offline (PyMuPDF, stage s13), served as a static PNG —
//      instant, no PDF parse. Used when the manifest carries a `figures` block and this
//      paper's index row has `hasFigure`. (The bake produces one crop, the figure.)
//   2. Client-side extraction from the arXiv PDF via pdf.js (figureExtract.extractFigures).
//
// Table source: ALWAYS the client-side extractor — the baked path only has the figure, so
// even a baked-figure paper still runs pdf.js to pull Table 1. This is why ViT (baked figure)
// shows its Table 1 too. When there's no baked figure, one extractor call renders both.
//
// Renders nothing when neither a figure nor a table is found.

import { LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { extractFigures } from "./figureExtract";
import { arxivPath, localArxivId } from "./resolveArxiv";

// Ids with no figure AND no table (client-side path) — skip the PDF fetch on re-select.
const noExtract = new Set<string>();

interface State {
  // The figure image: a baked PNG url, a client-rendered canvas, or none.
  figure: "baked" | "canvas" | "none";
  bakedUrl: string | null;
  table: "canvas" | "none";
  loading: boolean;
}

export function FirstFigure({
  arxivId,
  doi,
  bakedUrl,
}: {
  arxivId: string | null;
  doi: string | null;
  bakedUrl: string | null;
}) {
  const id = localArxivId(arxivId, doi);
  const [state, setState] = useState<State>({
    figure: bakedUrl ? "baked" : "none",
    bakedUrl,
    table: "none",
    loading: !!id, // we still probe the PDF for a table (and the figure when not baked)
  });
  const figCanvas = useRef<HTMLCanvasElement>(null);
  const tblCanvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    setState({ figure: bakedUrl ? "baked" : "none", bakedUrl, table: "none", loading: !!id });
    if (!id) return; // detail not in yet, or no arXiv id — a later render retries.
    if (noExtract.has(id) && bakedUrl) return; // known: no client-extractable crops, keep baked figure
    const controller = new AbortController();
    const pdfUrl = `https://arxiv.org/pdf/${arxivPath(id)}`;

    // On the frame after paint (canvases mounted), extract from the PDF. We ask for the TABLE
    // always, and the FIGURE only when it wasn't baked — so a baked-figure paper still gets its
    // table, and a non-baked paper gets both from one open of the PDF.
    const raf = requestAnimationFrame(() => {
      if (controller.signal.aborted) return;
      extractFigures(
        pdfUrl,
        { figure: bakedUrl ? null : figCanvas.current, table: tblCanvas.current },
        controller.signal,
      )
        .then((res) => {
          if (controller.signal.aborted) return;
          const gotFigure = bakedUrl ? true : !!res.figure;
          const gotTable = !!res.table;
          if (!gotFigure && !gotTable) {
            noExtract.add(id);
            setState({ figure: "none", bakedUrl, table: "none", loading: false });
          } else {
            setState({
              figure: bakedUrl ? "baked" : res.figure ? "canvas" : "none",
              bakedUrl,
              table: gotTable ? "canvas" : "none",
              loading: false,
            });
          }
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          // Extraction failed: keep a baked figure if we have one, else show nothing.
          setState({ figure: bakedUrl ? "baked" : "none", bakedUrl, table: "none", loading: false });
        });
    });

    return () => {
      controller.abort();
      cancelAnimationFrame(raf);
    };
  }, [id, bakedUrl]);

  // A baked crop that 404s (bundle/disk mismatch): drop to the client-side figure extractor.
  const onBakedError = () => {
    if (id) setState((s) => ({ ...s, figure: "none", bakedUrl: null, loading: true }));
    else setState((s) => ({ ...s, figure: "none" }));
  };

  const hasFigure = state.figure !== "none";
  const hasTable = state.table !== "none";
  if (!state.loading && !hasFigure && !hasTable) return null;

  return (
    <div className="first-figure" aria-label="Paper first figure and table">
      {state.loading && (
        <div className="first-figure-loading" role="status">
          <LoaderCircle className="spin" size={16} aria-hidden="true" />
          Finding first figure &amp; table…
        </div>
      )}

      {/* Figure: baked PNG or client-rendered canvas. Canvas is always mounted (hidden until
          a crop lands) so the extractor's ref is available. */}
      <div className="first-figure-crop" style={{ display: hasFigure ? "block" : "none" }}>
        {state.figure === "baked" && state.bakedUrl ? (
          <img className="first-figure-canvas" src={state.bakedUrl} alt="Paper first figure" onError={onBakedError} />
        ) : (
          <canvas ref={figCanvas} className="first-figure-canvas" />
        )}
        <span className="first-figure-tag">Figure 1</span>
      </div>

      {/* Table: always client-rendered. */}
      <div className="first-figure-crop" style={{ display: hasTable ? "block" : "none" }}>
        <canvas ref={tblCanvas} className="first-figure-canvas" />
        <span className="first-figure-tag">Table 1</span>
      </div>
    </div>
  );
}
