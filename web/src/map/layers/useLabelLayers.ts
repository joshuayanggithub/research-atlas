// Semantic-zoom labels. We declutter on the CPU with a greedy screen-space algorithm:
// project each candidate label to pixels, place them highest-priority-first, and skip any
// whose box overlaps an already-placed label. This is deterministic, works in an
// OrthographicView (deck.gl's GPU CollisionFilterExtension is unreliable in non-geo
// views — it culls all instances), and is cheap at our label count (~200).
//
// Coarse/high-count labels have higher priority, so they win when zoomed out; as you zoom
// in, fine labels' screen positions spread apart, stop overlapping, and appear.

import { TextLayer } from "@deck.gl/layers";
import type { Viewport } from "@deck.gl/core";
import type { Label, LevelBand } from "../../data/types";
import { bandForZoom, labelSizeForBand, visibleLabelLevels } from "../zoom";

interface Args {
  labels: Label[];
  levels: LevelBand[];
  zoom: number;
  base: number; // fit zoom; pipeline band offsets are measured from this
  viewport: Viewport | null;
}

interface PlacedLabel extends Label {
  size: number;
}

// Rough px width of a label at a given font size (avg glyph ~0.55em).
function labelWidth(text: string, size: number): number {
  return text.length * size * 0.55;
}

export function useLabelLayers({ labels, levels, zoom, base, viewport }: Args) {
  if (!viewport) return [];

  const visible = visibleLabelLevels(zoom, levels, base);
  const currentBand = bandForZoom(zoom, levels, base);

  // Candidate labels = those in visible bands, sorted by priority (desc).
  const candidates = labels
    .filter((l) => visible.has(l.level))
    .sort((a, b) => b.priority - a.priority);

  // Greedy screen-space placement.
  const placedBoxes: [number, number, number, number][] = []; // x0,y0,x1,y1 in px
  const placed: PlacedLabel[] = [];
  const seenText = new Set<string>(); // avoid repeating the same label (e.g. many "AI" tiles)
  const PAD = 6;

  for (const lb of candidates) {
    // Skip a text we've already placed at this or a coarser band (dedupe repeats).
    if (seenText.has(lb.text)) continue;

    const size = labelSizeForBand(lb.level, currentBand);
    const [px, py] = viewport.project([lb.x, lb.y]) as [number, number];
    const w = labelWidth(lb.text, size) / 2 + PAD;
    const h = size / 2 + PAD;
    const box: [number, number, number, number] = [px - w, py - h, px + w, py + h];

    // Skip labels projected outside the viewport.
    if (px < 0 || py < 0 || px > viewport.width || py > viewport.height) continue;

    const overlaps = placedBoxes.some(
      (b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1],
    );
    if (overlaps) continue;

    placedBoxes.push(box);
    placed.push({ ...lb, size });
    seenText.add(lb.text);
  }

  // One TextLayer for all placed labels (sizes vary per-datum via getSize accessor).
  return [
    new TextLayer<PlacedLabel>({
      id: "labels",
      data: placed,
      getPosition: (d) => [d.x, d.y] as [number, number],
      getText: (d) => d.text,
      getSize: (d) => d.size,
      sizeUnits: "pixels",
      getColor: [237, 240, 247, 255],
      outlineColor: [8, 10, 16, 255],
      outlineWidth: 3,
      fontSettings: { sdf: true },
      fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
      getTextAnchor: "middle",
      getAlignmentBaseline: "center",
      billboard: true,
      background: true,
      getBackgroundColor: [10, 12, 18, 190],
      backgroundPadding: [6, 3],
      updateTriggers: {
        // Re-place whenever the set of placed labels changes.
        getText: [placed.map((p) => p.id).join(","), currentBand],
        getSize: [currentBand],
      },
    }),
  ];
}
