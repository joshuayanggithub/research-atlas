// Powerful date filter: month-granularity dual-handle range, a publication histogram so
// users can see where papers concentrate, quick presets, and an exact label. Filtering
// runs on the GPU (DataFilterExtension month channel), so dragging never recomputes CPU
// masks and stays smooth across the whole corpus.

import { useMemo } from "react";
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
  const { bins, maxBin } = useMemo(() => {
    const b = new Int32Array(maxMonth + 1);
    const mi = ds.points.monthIndex;
    const match = filter?.matchValue;
    for (let i = 0; i < ds.points.count; i++) {
      if (orgAuthorActive && match && match[i] === 0) continue;
      const m = mi[i];
      if (m >= 0 && m <= maxMonth) b[m]++;
    }
    let mx = 1;
    for (const v of b) if (v > mx) mx = v;
    return { bins: b, maxBin: mx };
  }, [ds, maxMonth, filter, orgAuthorActive]);

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

      {/* Histogram + single dual-handle slider stacked as one control: the thumbs ride the
          bottom of the distribution and the selected span is highlighted across both. */}
      <div className="date-control">
        <div className="date-histogram" aria-hidden="true">
          {Array.from(bins).map((count, m) => {
            const selected = m >= filters.monthMin && m <= filters.monthMax;
            return (
              <span
                key={m}
                className={`hist-bar ${selected ? "in" : "out"}`}
                style={{ height: `${Math.max(2, (count / maxBin) * 100)}%` }}
              />
            );
          })}
        </div>
        <DualRangeSlider
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
