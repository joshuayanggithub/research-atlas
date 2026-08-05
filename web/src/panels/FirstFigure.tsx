// Inline "gist" thumbnail on the details card: the paper's Figure 1 (or Table 1), cropped
// from the arXiv PDF client-side (see firstFigure.ts). Shows immediately on select, above
// the Citations/Paper tabs, so you grasp the paper without opening the Paper tab.
//
// Renders nothing when the paper has no arXiv id or no locatable Figure 1 — it's a bonus
// visual, so it stays silent rather than showing an error; the Paper tab still has the full
// first-page render and text fallback.

import { LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { extractFirstFigure } from "./figureExtract";

function canonicalArxivId(rawId: string): string {
  return rawId
    .trim()
    .replace(/^arxiv:\s*/i, "")
    .replace(/^https?:\/\/(?:www\.)?arxiv\.org\/(?:abs|pdf)\//i, "")
    .replace(/\.pdf$/i, "");
}

// Many OpenAlex records leave arxiv_id null but carry a DOI that encodes it, e.g.
// 10.48550/arXiv.2010.11929 → 2010.11929. Recover the id from either source so the figure
// shows for the large set of arXiv papers whose id only lives in the DOI.
function arxivFrom(arxivId: string | null, doi: string | null): string | null {
  if (arxivId) return canonicalArxivId(arxivId);
  if (doi) {
    const m = doi.match(/arxiv\.([^/\s]+)$/i);
    if (m) return m[1];
  }
  return null;
}

function arxivPath(id: string): string {
  return id.split("/").map(encodeURIComponent).join("/");
}

// Remember which arXiv ids have no locatable figure, so re-selecting them skips the PDF
// fetch entirely (the browser HTTP-caches the PDF itself for ids that do have one).
const noFigure = new Set<string>();

type State =
  | { status: "loading" }
  | { status: "done"; label: string }
  | { status: "none" }; // no figure — render nothing

export function FirstFigure({
  arxivId,
  doi,
}: {
  arxivId: string | null;
  doi: string | null;
}) {
  const id = arxivFrom(arxivId, doi);
  // Detail loads asynchronously (per-node shard fetch), so on the first render arxivId/doi
  // are both null. Starting in "none" and never re-entering "loading" would sticky-hide the
  // figure for the whole selection. Instead we hold in "none" (render nothing) until id
  // materializes; the effect below then sets loading and kicks off extraction.
  const [state, setState] = useState<State>(id ? { status: "loading" } : { status: "none" });
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!id) return; // wait for detail to arrive; a later render with a real id will re-run.
    if (noFigure.has(id)) {
      setState({ status: "none" });
      return;
    }
    setState({ status: "loading" });
    const controller = new AbortController();
    const pdfUrl = `https://arxiv.org/pdf/${arxivPath(id)}`;

    // The canvas is only in the DOM after the "loading" state commits, so wait a frame for
    // React to mount it before capturing the ref. (Without this the ref is null when the
    // previous render was "none".)
    const start = () => {
      if (controller.signal.aborted) return;
      const target = canvasRef.current;
      if (!target) return; // element still not mounted; the following render will retry.
      const cssWidth = target.parentElement?.clientWidth || 360;

      extractFirstFigure(pdfUrl, target, cssWidth, controller.signal)
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
    };
    // Wait for the "loading" state to commit and the canvas to mount before capturing the
    // ref. rAF fires after paint, when the DOM is definitely up to date.
    const raf = requestAnimationFrame(start);

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
