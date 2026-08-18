// A single dual-handle range slider: one track, two draggable thumbs, and a highlighted
// selected span between them. Replaces two stacked <input type=range> so the date filter
// reads as one control. Values are integers in [min, max]; the whole thing is keyboard
// operable (each thumb is a focusable slider role with arrow-key support).

import { useCallback, useRef } from "react";

interface Props {
  min: number;
  max: number;
  low: number;
  high: number;
  onChange: (low: number, high: number) => void;
  ariaLabelLow?: string;
  ariaLabelHigh?: string;
  formatValue?: (v: number) => string; // for aria-valuetext
  className?: string;
}

export function DualRangeSlider({
  min,
  max,
  low,
  high,
  onChange,
  ariaLabelLow = "Range start",
  ariaLabelHigh = "Range end",
  formatValue,
  className = "",
}: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const dragging = useRef<null | "low" | "high">(null);
  const span = Math.max(1, max - min);
  const pct = (v: number) => ((v - min) / span) * 100;

  // Map a clientX to the nearest integer value on the track.
  const valueAt = useCallback(
    (clientX: number): number => {
      const el = trackRef.current;
      if (!el) return low;
      const rect = el.getBoundingClientRect();
      const t = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      return Math.round(min + t * span);
    },
    [low, min, span],
  );

  const startDrag = (thumb: "low" | "high") => (e: React.PointerEvent) => {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    dragging.current = thumb;
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragging.current) return;
    const v = valueAt(e.clientX);
    if (dragging.current === "low") onChange(Math.min(v, high), high);
    else onChange(low, Math.max(v, low));
  };

  const endDrag = (e: React.PointerEvent) => {
    if (dragging.current && e.currentTarget.hasPointerCapture?.(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    dragging.current = null;
  };

  // Click/drag anywhere on the track moves the nearer thumb. When this slider overlays a
  // histogram, the bars become the range-selection surface rather than a second control.
  const onTrackPointerDown = (e: React.PointerEvent) => {
    if (dragging.current) return;
    const v = valueAt(e.clientX);
    const thumb = Math.abs(v - low) <= Math.abs(v - high) ? "low" : "high";
    e.currentTarget.setPointerCapture(e.pointerId);
    dragging.current = thumb;
    if (thumb === "low") onChange(Math.min(v, high), high);
    else onChange(low, Math.max(v, low));
  };

  const keyStep = (thumb: "low" | "high") => (e: React.KeyboardEvent) => {
    let delta = 0;
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") delta = -1;
    else if (e.key === "ArrowRight" || e.key === "ArrowUp") delta = 1;
    else if (e.key === "Home") delta = thumb === "low" ? min - low : min - high;
    else if (e.key === "End") delta = thumb === "low" ? max - low : max - high;
    else return;
    e.preventDefault();
    if (thumb === "low") onChange(Math.max(min, Math.min(low + delta, high)), high);
    else onChange(low, Math.min(max, Math.max(high + delta, low)));
  };

  return (
    <div
      className={`dual-slider ${className}`.trim()}
      ref={trackRef}
      onPointerDown={onTrackPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
    >
      <div className="dual-slider-track" />
      <div
        className="dual-slider-range"
        style={{ left: `${pct(low)}%`, right: `${100 - pct(high)}%` }}
      />
      <div
        className="dual-slider-thumb"
        role="slider"
        tabIndex={0}
        aria-label={ariaLabelLow}
        aria-valuemin={min}
        aria-valuemax={high}
        aria-valuenow={low}
        aria-valuetext={formatValue?.(low)}
        // When the thumbs coincide, raise the one nearer the max end so it stays grabbable
        // to drag back toward the min (and vice-versa) — otherwise a collapsed range traps
        // the lower thumb under the higher one.
        style={{ left: `${pct(low)}%`, zIndex: low >= (min + max) / 2 ? 3 : 2 }}
        onPointerDown={startDrag("low")}
        onKeyDown={keyStep("low")}
      />
      <div
        className="dual-slider-thumb"
        role="slider"
        tabIndex={0}
        aria-label={ariaLabelHigh}
        aria-valuemin={low}
        aria-valuemax={max}
        aria-valuenow={high}
        aria-valuetext={formatValue?.(high)}
        style={{ left: `${pct(high)}%`, zIndex: low >= (min + max) / 2 ? 2 : 3 }}
        onPointerDown={startDrag("high")}
        onKeyDown={keyStep("high")}
      />
    </div>
  );
}
