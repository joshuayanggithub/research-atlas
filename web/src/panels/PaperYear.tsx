// A paper's publication year, or an honest placeholder while it is still downloading.
//
// The sibling of PaperTitle, for the same reason. A year arrives either with the paper's point
// tile / position shard (per-paper, trickling in over minutes on a slow link) or with
// papers-index.arrow (all N at once, ~2.6 MB). Until one of those lands the row knows nothing
// about the date — and rendering that as an em dash is a claim, not a gap: measured on an
// author with 58 papers, 47 rows asserted a missing year for roughly two minutes before
// quietly turning into real dates.
//
// `dateAvailable` is what separates the two cases, exactly as `citationCountAvailable` does for
// a zero citation count (D29/D39).

import type { PaperMeta } from "../data/types";

export function PaperYear({ paper }: { paper: PaperMeta | undefined }) {
  const year = paper?.publicationDate?.slice(0, 4);
  if (year) return <>{year}</>;
  // Not loaded yet — shimmer at roughly the width of a four-digit year.
  if (!paper?.dateAvailable) {
    return <span className="year-skeleton" role="img" aria-label="Year loading" />;
  }
  // Known to have no date.
  return <>—</>;
}
