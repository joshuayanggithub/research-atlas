// What the two panes cannot show: the numbers, and the shared papers themselves.
//
// The intersection lives here rather than as a third colour on the map. For two authors it is
// often a handful of co-authored papers — a list enumerates those far better than a hue
// applied to four dots, which was the reasoning that turned this design from an overlay into
// split panes (docs/COMPARE_SPEC.md).

import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { useStore } from "../state/store";
import type { Dataset } from "../data/types";
import { useCompareMask } from "../filters/useCompareMask";
import { useTitles } from "../data/useTitles";
import { PaperTitle } from "./PaperTitle";
import { PaperYear } from "./PaperYear";

const SHARED_LIMIT = 50;

export function CompareResults({ ds }: { ds: Dataset }) {
  const compare = useStore((s) => s.compare);
  const selectNode = useStore((s) => s.selectNode);
  const mask = useCompareMask(ds, compare.a, compare.b);
  const shown = mask ? mask.shared.slice(0, SHARED_LIMIT) : [];
  // Only the rows this list renders.
  useTitles(shown);

  // On a narrow screen the panel is a bottom sheet, and at full height it covered pane B
  // completely — the second half of a split screen is not optional. It therefore starts
  // collapsed there, showing the counts (the headline) with the list one tap away.
  const [expanded, setExpanded] = useState(true);
  useEffect(() => {
    const narrow = window.matchMedia("(max-width: 820px)");
    const apply = () => setExpanded(!narrow.matches);
    apply();
    narrow.addEventListener("change", apply);
    return () => narrow.removeEventListener("change", apply);
  }, []);

  if (!mask || !compare.a || !compare.b) return null;
  const { counts, pending } = mask;

  // A count is only a count once both sides have resolved. Reporting "0 shared papers" while
  // an org shard is still in flight is the bug D51 fixed for the filter bar, and it would be
  // worse here: "these two have nothing in common" is a claim people would believe.
  const num = (v: number) =>
    pending ? <span className="count-skeleton" role="img" aria-label="Counting" /> : v.toLocaleString();

  const orgSide = compare.a.kind === "org" || compare.b.kind === "org";

  return (
    <section
      className={`panel compare-results ${expanded ? "expanded" : "collapsed"}`}
      aria-label="Comparison"
    >
      <button
        type="button"
        className="compare-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        <ChevronRight size={13} aria-hidden="true" className="compare-caret" />
        <h3>Comparison</h3>
      </button>

      <div className="compare-counts">
        <div className="compare-count">
          <span className="compare-tag compare-tag-a">A</span>
          <strong>{num(counts.a)}</strong>
          <span className="subtle">{compare.a.label}</span>
        </div>
        <div className="compare-count">
          <span className="compare-tag compare-tag-b">B</span>
          <strong>{num(counts.b)}</strong>
          <span className="subtle">{compare.b.label}</span>
        </div>
        <div className="compare-count compare-count-both">
          <span className="compare-tag compare-tag-both">both</span>
          <strong>{num(counts.both)}</strong>
          <span className="subtle">
            {compare.a.kind === "author" && compare.b.kind === "author"
              ? "co-authored"
              : "jointly affiliated"}
          </span>
        </div>
      </div>

      {expanded && orgSide && (
        // Where the number is, not in a footnote: an institutional comparison is only as good
        // as the affiliation data behind it.
        <p className="compare-caveat subtle">
          Institutional overlap counts papers we could attribute to both. Attribution is
          incomplete for very recent work (~6% for 2026) and depends on curated name matching
          for companies, so this understates rather than overstates.
        </p>
      )}

      {expanded && !pending && counts.both === 0 && (
        <p className="compare-empty subtle">
          No papers in common — a real result, not a missing one.
        </p>
      )}

      {expanded && shown.length > 0 && (
        <>
          <div className="panel-section-head">
            <h4>Shared papers</h4>
            <span>
              {counts.both > SHARED_LIMIT
                ? `top ${SHARED_LIMIT} of ${counts.both.toLocaleString()} by citations`
                : `${counts.both.toLocaleString()}`}
            </span>
          </div>
          <ol className="compare-shared">
            {shown.map((node) => (
              <li key={node}>
                <button type="button" onClick={() => selectNode(node)}>
                  <span className="paper-row-title">
                    <PaperTitle title={ds.papers[node]?.title} node={node} />
                  </span>
                  <span className="paper-row-meta subtle">
                    <PaperYear paper={ds.papers[node]} /> ·{" "}
                    {ds.points.citedByCount[node]?.toLocaleString() ?? 0} cites
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
