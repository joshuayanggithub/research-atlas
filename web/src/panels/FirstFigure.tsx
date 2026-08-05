// Inline "gist" thumbnail on the details card: the paper's Figure 1 (or Table 1), cropped
// from the arXiv PDF client-side (see figureExtract.ts). Shows immediately on select, above
// the Citations/Paper tabs, so you grasp the paper without opening the Paper tab.
//
// The arXiv id comes from the corpus (arxiv_id, populated in the pipeline from Semantic
// Scholar during embedding — S2 is CORS-blocked at runtime, so we resolve it offline) or a
// DOI that encodes it. Renders nothing when neither yields an id or no Figure 1 is located —
// it's a bonus visual, so it stays silent; the Paper tab has the full fallback.

import { LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { extractFirstFigure } from "./figureExtract";
import { arxivPath, localArxivId } from "./resolveArxiv";

// Ids with no locatable figure — skip the PDF fetch on re-select (the browser HTTP-caches
// the PDF itself for ids that do have one).
const noFigure = new Set<string>();

type State =
  | { status: "loading" }
  | { status: "done"; label: string }
  | { status: "none" };

export function FirstFigure({
  arxivId,
  doi,
}: {
  arxivId: string | null;
  doi: string | null;
}) {
  const id = localArxivId(arxivId, doi);
  const [state, setState] = useState<State>(id ? { status: "loading" } : { status: "none" });
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!id) return; // detail not in yet, or paper has no arXiv id — a later render retries.
    if (noFigure.has(id)) {
      setState({ status: "none" });
      return;
    }
    setState({ status: "loading" });
    const controller = new AbortController();
    const pdfUrl = `https://arxiv.org/pdf/${arxivPath(id)}`;

    // The canvas mounts only after a non-"none" state commits; rAF fires after paint, when
    // the canvas is in the DOM, so its ref is ready for pdf.js.
    const raf = requestAnimationFrame(() => {
      if (controller.signal.aborted) return;
      const target = canvasRef.current;
      if (!target) return;
      extractFirstFigure(pdfUrl, target, controller.signal)
        .then((crop) => {
          if (controller.signal.aborted) return;
          if (!crop) {
            noFigure.add(id);
            setState({ status: "none" });
          } else {
            setState({ status: "done", label: crop.label });
          }
        })
        .catch(() => {
          if (!controller.signal.aborted) setState({ status: "none" });
        });
    });

    return () => {
      controller.abort();
      cancelAnimationFrame(raf);
    };
  }, [id]);

  if (state.status === "none") return null;

  return (
    <div className="first-figure" aria-label="Paper first figure">
      {state.status === "loading" && (
        <div className="first-figure-loading" role="status">
          <LoaderCircle className="spin" size={16} aria-hidden="true" />
          Finding first figure…
        </div>
      )}
      <canvas
        ref={canvasRef}
        className="first-figure-canvas"
        style={{ display: state.status === "done" ? "block" : "none" }}
      />
      {state.status === "done" && <span className="first-figure-tag">{state.label}</span>}
    </div>
  );
}
