// A paper's title, or an honest placeholder while it is still downloading.
//
// Titles stream in as 17 chunks after first paint (D23/D30), so for the first stretch of a
// session a paper legitimately has no title yet. Rendering that as an empty string makes the
// interface look broken — a blank details heading, an unlabelled hover card, a citation row with
// nothing in it — and rendering it as "(untitled)" is worse, because that is a claim about the
// paper. This distinguishes "still loading" from "genuinely has no title", the same way the
// citation panel distinguishes missing data from a real zero (D29/D39).

import { usePapersReady } from "../data/usePapersReady";

export function PaperTitle({ title, className }: { title: string | undefined; className?: string }) {
  const papersReady = usePapersReady();
  if (title) return <span className={className}>{title}</span>;
  if (papersReady) {
    return (
      <span className={className}>
        <span className="subtle">(untitled)</span>
      </span>
    );
  }
  return (
    <span className={className}>
      {/* aria-label rather than visible text: a screen reader should hear "title loading", but
          sighted users get the shimmer, which reads as "pending" without adding noise. */}
      <span className="title-skeleton" role="img" aria-label="Title loading" />
    </span>
  );
}

/** Plain-string form for attributes (aria-label, tooltip title) that cannot hold an element. */
export function paperTitleText(title: string | undefined, papersReady: boolean): string {
  return title || (papersReady ? "(untitled)" : "Loading title…");
}
