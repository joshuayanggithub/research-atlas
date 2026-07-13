// Year range filter. Drives the GPU DataFilterExtension year channel (no CPU recompute),
// so dragging is smooth even at tens of thousands of points.

import { useStore } from "../state/store";
import type { Dataset } from "../data/types";

export function DateRangeSlider({ ds }: { ds: Dataset }) {
  const filters = useStore((s) => s.filters);
  const setYearRange = useStore((s) => s.setYearRange);

  const min = parseInt(ds.manifest.corpus.date_from.slice(0, 4));
  const max = parseInt(ds.manifest.corpus.date_to.slice(0, 4));

  return (
    <div className="filter-section">
      <h4>
        Years <span className="subtle">{filters.yearMin}–{filters.yearMax}</span>
      </h4>
      <div className="range-row">
        <input
          type="range"
          min={min}
          max={max}
          value={filters.yearMin}
          onChange={(e) =>
            setYearRange(Math.min(+e.target.value, filters.yearMax), filters.yearMax)
          }
        />
        <input
          type="range"
          min={min}
          max={max}
          value={filters.yearMax}
          onChange={(e) =>
            setYearRange(filters.yearMin, Math.max(+e.target.value, filters.yearMin))
          }
        />
      </div>
    </div>
  );
}
