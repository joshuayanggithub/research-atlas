// Citation-count range filter.
//
// Citation counts are extremely skewed — in the 2025-2026 corpus most papers sit at 0 while
// the top paper is in the tens of thousands — so a linear track would spend ~99% of its
// length on a range nobody wants and make "0 .. 50" unclickable. The slider therefore runs on
// a LOG scale: evenly spaced steps map to exponentially growing citation counts, which keeps
// the low end (where the mass of the corpus lives) actually selectable.

import { useEffect, useMemo, useRef, useState } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";
import { DualRangeSlider } from "./DualRangeSlider";

// Track resolution. More steps = finer control; 48 keeps every step ≥1 citation apart at the
// low end for a corpus topping out in the tens of thousands.
const STEPS = 48;

export function CitationFilter({ ds }: { ds: Dataset }) {
  const citeMin = useStore((s) => s.filters.citeMin);
  const citeMax = useStore((s) => s.filters.citeMax);
  const setCitationRange = useStore((s) => s.setCitationRange);

  const maxCites = useMemo(() => {
    let m = 0;
    const c = ds.points.citedByCount;
    for (let i = 0; i < c.length; i++) if (c[i] > m) m = c[i];
    return m;
  }, [ds.points.citedByCount]);

  // log1p/expm1 keep 0 an exact fixed point, so step 0 really means "0 citations".
  const span = Math.log1p(maxCites) || 1;
  const stepToCites = (step: number) =>
    step <= 0 ? 0 : Math.round(Math.expm1((step / STEPS) * span));
  // The TRACK POSITION is authoritative, not the citation value derived from it. step->cites
  // is lossy at the low end (with a corpus max in the thousands, steps 1-2 both round to 0
  // citations), so re-deriving the thumb from the stored value made it snap back to 0 on every
  // nudge. Keeping the step in local state makes the thumb move one step per keypress.
  const [steps, setSteps] = useState<[number, number]>([0, STEPS]);
  // What this component last wrote to the store. The sync effect below needs to tell an
  // EXTERNAL reset apart from a range this slider itself just produced — see below.
  const lastPushed = useRef<{ min: number; max: number | null }>({ min: 0, max: null });

  // Follow the store when it is reset from outside (sidebar "Clear").
  //
  // The guard matters: the low end of a log track is lossy, so several leading steps all round
  // to 0 citations (steps 0-1 at a 51,361 max; 0-2 at 7,243). Dragging the low thumb into that
  // zone therefore stores `citeMin=0, citeMax=null` — byte-identical to the cleared state — and
  // without this check the effect fired and snapped the thumb back to the left edge, so the
  // minimum handle appeared frozen. Ignoring values we just wrote makes the effect respond only
  // to a genuine outside reset.
  useEffect(() => {
    if (citeMin === lastPushed.current.min && citeMax === lastPushed.current.max) return;
    if (citeMin === 0 && citeMax === null) setSteps([0, STEPS]);
  }, [citeMin, citeMax]);

  const [lowStep, highStep] = steps;

  // Committing to the store is EXPENSIVE: citeMin/citeMax are dependencies of useFilterMask,
  // which rebuilds a 912,429-entry match mask, and that also changes the filter object identity
  // so the date histogram re-bins another 912k. DualRangeSlider fires onChange on every
  // pointermove, so an undebounced drag was doing ~110M JS ops/sec and the thumb crawled.
  //
  // The thumb itself stays perfectly smooth because `steps` is local state, updated every move.
  // Only the map-wide filter is throttled, with a trailing commit so the final position always
  // lands even if the user stops mid-interval.
  const commitTimer = useRef<number | null>(null);
  const pendingCommit = useRef<{ min: number; max: number | null } | null>(null);
  const COMMIT_MS = 120;

  const flushCommit = () => {
    if (commitTimer.current !== null) {
      window.clearTimeout(commitTimer.current);
      commitTimer.current = null;
    }
    const next = pendingCommit.current;
    if (!next) return;
    pendingCommit.current = null;
    lastPushed.current = next;
    setCitationRange(next.min, next.max);
  };

  useEffect(() => () => {
    if (commitTimer.current !== null) window.clearTimeout(commitTimer.current);
  }, []);

  const onChange = (low: number, high: number) => {
    setSteps([low, high]);
    // Snapping the top of the track back to `null` keeps "no upper bound" distinct from
    // "capped at today's most-cited paper", which would silently drop papers after a rebuild.
    pendingCommit.current = {
      min: stepToCites(low),
      max: high >= STEPS ? null : stepToCites(high),
    };
    if (commitTimer.current === null) {
      commitTimer.current = window.setTimeout(flushCommit, COMMIT_MS);
    }
  };

  const active = citeMin > 0 || citeMax !== null;
  const label = `${citeMin.toLocaleString()} – ${
    citeMax === null ? "any" : citeMax.toLocaleString()
  }`;

  return (
    <div className="filter-section">
      <div className="section-head">
        <h4>
          Citations <span className="subtle">{label}</span>
        </h4>
        {active && (
          <button
            type="button"
            className="text-btn"
            onClick={() => {
              // Drop any throttled commit still in flight so it cannot land after the reset.
              if (commitTimer.current !== null) window.clearTimeout(commitTimer.current);
              commitTimer.current = null;
              pendingCommit.current = null;
              setSteps([0, STEPS]);
              lastPushed.current = { min: 0, max: null };
              setCitationRange(0, null);
            }}
          >
            Reset
          </button>
        )}
      </div>
      <DualRangeSlider
        min={0}
        max={STEPS}
        low={lowStep}
        high={highStep}
        onChange={onChange}
        ariaLabelLow="Minimum citations"
        ariaLabelHigh="Maximum citations"
        formatValue={(step) => (step >= STEPS ? "any" : stepToCites(step).toLocaleString())}
      />
    </div>
  );
}
