// Powerful date filter: a publication histogram with an integrated month-granularity range
// brush, quick presets, and an exact label. Filtering
// runs on the GPU (DataFilterExtension month channel), so dragging never recomputes CPU
// masks and stays smooth across the whole corpus.

import { useMemo } from "react";
import { UNLOADED_LEVEL } from "../data/loadArtifacts";
import { usePointTilesEpoch } from "../data/usePapersReady";
import { useStore } from "../state/store";
import { useFilterMask } from "./useFilterMask";
import { DualRangeSlider } from "./DualRangeSlider";
import type { Dataset } from "../data/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function labelForMonth(fromYear: number, monthIndex: number): string {
  const y = fromYear + Math.floor(monthIndex / 12);
  const m = ((monthIndex % 12) + 12) % 12;
  return `${MONTHS[m]} ${y}`;
}

export function DateRangeSlider({ ds }: { ds: Dataset }) {
  const filters = useStore((s) => s.filters);
  const setMonthRange = useStore((s) => s.setMonthRange);
  const filter = useFilterMask(ds, filters);

  const fromYear = parseInt(ds.manifest.corpus.date_from.slice(0, 4));
  const toYear = parseInt(ds.manifest.corpus.date_to.slice(0, 4));
  const toMonth = parseInt(ds.manifest.corpus.date_to.slice(5, 7)) || 12;
  const maxMonth = (toYear - fromYear) * 12 + (toMonth - 1);

  // Histogram of paper counts per month. When an org/author filter is active it reflects
  // ONLY the matching subset, so the histogram + range count agree with what the map shows
  // (rather than misleadingly counting the whole corpus). Date is excluded here — the
  // slider itself is the date control.
  const orgAuthorActive = !!filter?.anyOrgAuthorActive;
  const tilesEpoch = usePointTilesEpoch();
  const bins = useMemo(() => {
    // With NO org/author filter the true distribution comes from the manifest, computed by s11
    // over the whole corpus. Deriving it from loaded points instead is not just incomplete, it is
    // BIASED: reveal levels are ordered by importance, so the sparse early years live in the
    // deepest tiles and are the last to arrive. At the home view that showed "0 selected" for
    // 1991-2014, a range that genuinely holds 88,061 papers.
    const preset = ds.manifest.month_histogram;
    if (!orgAuthorActive && preset && preset.length > 0) {
      const b = new Int32Array(maxMonth + 1);
      for (let m = 0; m <= maxMonth && m < preset.length; m++) b[m] = preset[m];
      return b;
    }
    const b = new Int32Array(maxMonth + 1);
    const mi = ds.points.monthIndex;
    const reveal = ds.points.revealLevel;
    const match = filter?.matchValue;
    for (let i = 0; i < ds.points.count; i++) {
      // Skip papers whose point tile has not arrived. emptyPoints zeroes monthIndex, so an
      // unloaded paper reads as the FIRST month of the corpus: at startup ~925,000 unplaced
      // papers stacked into a single 1991 bar and every real bar was flattened against it.
      // This is the histogram's version of the placeholder-as-fact bug (D39/D41).
      if (reveal[i] === UNLOADED_LEVEL) continue;
      if (orgAuthorActive && match && match[i] === 0) continue;
      const m = mi[i];
      if (m >= 0 && m <= maxMonth) b[m]++;
    }
    return b;
    // Depend on the MASK, not the whole filter object: the filter identity changes on every
    // citation/topic/org tweak, and re-binning 912k points for a change the histogram does not
    // even reflect was a large part of the drag lag.
    // tilesEpoch: the bins are only as complete as the tiles that have landed, so re-bin as
    // more arrive rather than freezing on the eager set.
  }, [ds, maxMonth, filter?.matchValue, orgAuthorActive, tilesEpoch]);

  // Group months into DISPLAY bars. The corpus spans 1991-2026 = 428 months, and the sidebar
  // histogram is ~260px wide: one bar per month gives each 0.61px with a 1px gap between them,
  // so the gaps are wider than the bars and the distribution simply vanishes. Group to the
  // smallest NATURAL unit (month, quarter, half, year, ...) that keeps bars readable. The range
  // control underneath stays month-granular — this changes what is drawn, not what is selectable.
  const { bars, monthsPerBar } = useMemo(() => {
    const NATURAL = [1, 3, 6, 12, 24, 60];
    const TARGET_BARS = 56;
    const step = NATURAL.find((n) => Math.ceil((maxMonth + 1) / n) <= TARGET_BARS) ?? 60;
    const out: { start: number; end: number; count: number }[] = [];
    for (let m = 0; m <= maxMonth; m += step) {
      const end = Math.min(m + step - 1, maxMonth);
      let count = 0;
      for (let k = m; k <= end; k++) count += bins[k] ?? 0;
      out.push({ start: m, end, count });
    }
    return { bars: out, monthsPerBar: step };
  }, [bins, maxMonth]);
  const maxBar = useMemo(() => bars.reduce((m, b) => (b.count > m ? b.count : m), 1), [bars]);
  const unit = monthsPerBar === 1 ? "mo"
    : monthsPerBar === 3 ? "quarter"
      : monthsPerBar === 12 ? "yr"
        : `${monthsPerBar}mo`;

  // Count inside the selected window (updates live as the user drags).
  const inRange = useMemo(() => {
    let sum = 0;
    for (let m = filters.monthMin; m <= filters.monthMax; m++) sum += bins[m] ?? 0;
    return sum;
  }, [bins, filters.monthMin, filters.monthMax]);

  const presets: { label: string; min: number; max: number }[] = [
    { label: "All", min: 0, max: maxMonth },
    { label: "Last 12mo", min: Math.max(0, maxMonth - 11), max: maxMonth },
    { label: "Last 24mo", min: Math.max(0, maxMonth - 23), max: maxMonth },
    { label: `${toYear}`, min: (toYear - fromYear) * 12, max: maxMonth },
  ];
  const activePreset = presets.find((p) => p.min === filters.monthMin && p.max === filters.monthMax);

  return (
    <div className="filter-section">
      <h4>
        Dates{" "}
        <span className="subtle">
          {labelForMonth(fromYear, filters.monthMin)} – {labelForMonth(fromYear, filters.monthMax)}
        </span>
      </h4>

      {/* Without a stated maximum the bars are a shape with no units — you can see that later
          years are taller but not what any of it means. One peak label turns it into a scale. */}
      {/* Labels the gridline the bars rise to, so "how tall is tall" has an answer. The
          half-height line sits at 25% of the peak because the bars use a sqrt scale. */}
      <div className="hist-scale subtle">
        <span>
          {maxBar.toLocaleString()}/{unit} peak · log
        </span>
        <span>{inRange.toLocaleString()} selected</span>
      </div>

      {/* The histogram is the range control: its selected brush and two handles live directly
          on the bars, so dragging the distribution updates the map's time filter. */}
      <div className="date-control">
        <div className="date-histogram" role="group" aria-label="Publication date range histogram">
          {bars.map((bar) => {
            // A display bar is selected when the brush overlaps ANY of its months, so the
            // highlight still tracks a month-granular selection under a yearly bar.
            const selected = bar.end >= filters.monthMin && bar.start <= filters.monthMax;
            const from = labelForMonth(fromYear, bar.start);
            const to = labelForMonth(fromYear, bar.end);
            return (
              <span
                key={bar.start}
                className={`hist-bar ${selected ? "in" : "out"}`}
                title={`${from}${from === to ? "" : ` – ${to}`}: ${bar.count.toLocaleString()} papers`}
                // LOG, not sqrt. Across 1991-2026 arXiv CS output grows by roughly three orders
                // of magnitude, and sqrt puts the early 1990s at ~3% height — present in the DOM,
                // invisible on screen, which is what "the y axis is screwed up" describes. log1p
                // spans decades legibly and is the standard choice for a growth distribution;
                // the axis says "log" so the height is not mistaken for a linear reading.
                style={{ height: `${Math.max(2, (Math.log1p(bar.count) / Math.log1p(maxBar)) * 100)}%` }}
              />
            );
          })}
          <DualRangeSlider
            className="histogram-range-selector"
            min={0}
            max={maxMonth}
            low={filters.monthMin}
            high={filters.monthMax}
            onChange={setMonthRange}
            ariaLabelLow="Start month"
            ariaLabelHigh="End month"
            formatValue={(v) => labelForMonth(fromYear, v)}
          />
        </div>
      </div>

      <div className="date-presets">
        {presets.map((p) => (
          <button
            type="button"
            key={p.label}
            className={`date-preset ${activePreset?.label === p.label ? "active" : ""}`}
            aria-pressed={activePreset?.label === p.label}
            onClick={() => setMonthRange(p.min, p.max)}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="date-count subtle">
        {inRange.toLocaleString()} papers in range
        {orgAuthorActive ? " (filtered)" : ""}
      </div>
    </div>
  );
}
