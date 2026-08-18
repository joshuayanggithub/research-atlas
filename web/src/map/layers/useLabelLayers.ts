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
  // When an org/author filter is active, only labels whose region still contains matching
  // papers are relevant (a topic name over an empty region is misleading). null = no filter.
  relevantLabelIds: Set<number> | null;
  // A search-selected label wins collision ordering so it remains visible after navigation.
  focusedLabelId: number | null;
  onHover?: (labelId: number | null, x: number, y: number) => void;
  // Clicking a label navigates to that region, the same way choosing it from search does.
  onClick?: (label: Label) => void;
}

interface PlacedLabel extends Label {
  size: number;
}

// Rough px width of a label at a given font size (avg glyph ~0.55em).
function labelWidth(text: string, size: number): number {
  return text.length * size * 0.55;
}

export function useLabelLayers({
  labels,
  levels,
  zoom,
  base,
  viewport,
  relevantLabelIds,
  focusedLabelId,
  onHover,
  onClick,
}: Args) {
  if (!viewport) return [];

  const visible = visibleLabelLevels(zoom, levels, base, relevantLabelIds !== null);
  const currentBand = bandForZoom(zoom, levels, base);

  // Candidate labels = those in visible bands (and, when filtering, still populated by
  // matching papers), sorted by priority (desc).
  const candidates = labels
    .filter((l) => visible.has(l.level) && (!relevantLabelIds || relevantLabelIds.has(l.id)))
    .sort((a, b) => {
      if (a.id === focusedLabelId) return -1;
      if (b.id === focusedLabelId) return 1;
      // Coarser bands first when a filter opened up the finer ones, so the broad names still
      // claim the prime space and the specific ones fill in around them.
      if (relevantLabelIds && a.level !== b.level) return a.level - b.level;
      return b.priority - a.priority;
    });

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

    // Drop a label whose box leaves the viewport. Clamping it back inside would move the text
    // away from the region it names, which is worse than not drawing it — and with finer bands
    // now offered under a filter there is always another candidate for that space.
    if (box[0] < 0 || box[1] < 0 || box[2] > viewport.width || box[3] > viewport.height) continue;

    const overlaps = placedBoxes.some(
      (b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1],
    );
    if (overlaps) continue;

    placedBoxes.push(box);
    placed.push({ ...lb, size });
    seenText.add(lb.text);
  }

  // deck.gl diffs a TextLayer's data by index and, for text glyph layout, caches per-datum
  // buffers keyed by updateTriggers. When the placed set changes (e.g. a selection restricts
  // which labels are relevant) without a matching trigger, a row can keep the PREVIOUS datum's
  // laid-out text/position — labels show the names that were at that slot before. So the
  // trigger below is keyed on the exact placed set (ids) + band, forcing every positional
  // accessor to re-run whenever the set changes. The layer id stays STABLE so deck.gl updates
  // in place (no per-frame teardown/flicker during zoom).
  const placedKey = placed.map((p) => p.id).join(",");

  // One TextLayer for all placed labels (sizes vary per-datum via getSize accessor).
  return [
    new TextLayer<PlacedLabel>({
      id: "labels",
      data: placed,
      getPosition: (d) => [d.x, d.y] as [number, number],
      getText: (d) => d.text,
      getSize: (d) => d.size,
      sizeUnits: "pixels",
      getColor: (d) =>
        d.id === focusedLabelId ? [246, 173, 85, 255] : [237, 240, 247, 255],
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
      pickable: !!onHover || !!onClick,
      onHover: onHover
        ? (info) => onHover(info.index >= 0 ? placed[info.index].id : null, info.x, info.y)
        : undefined,
      // Return true so the click stops here: DeckGL's top-level handler treats an unhandled
      // click as "empty space" and clears the paper selection.
      onClick: onClick
        ? (info) => {
            if (info.index < 0) return false;
            onClick(placed[info.index]);
            return true;
          }
        : undefined,
      updateTriggers: {
        // Re-evaluate all positional accessors whenever the placed set or band changes.
        getPosition: [placedKey, currentBand],
        getText: [placedKey, currentBand],
        getSize: [placedKey, currentBand],
        getColor: [placedKey, focusedLabelId],
      },
    }),
  ];
}
