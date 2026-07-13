// Semantic-zoom labels. One TextLayer per band, all sharing a single CollisionFilter
// group so labels declutter against each other on the GPU every frame. Coarse/important
// labels get higher collision priority, so they win scarce screen space when zoomed out;
// as you zoom in, finer labels stop colliding and appear (Google-Maps behavior).

import { TextLayer } from "@deck.gl/layers";
import { CollisionFilterExtension } from "@deck.gl/extensions";
import type { Label, LevelBand } from "../../data/types";
import { bandForZoom, labelSizeForBand, visibleLabelLevels } from "../zoom";

interface Args {
  labels: Label[];
  levels: LevelBand[];
  zoom: number;
}

export function useLabelLayers({ labels, levels, zoom }: Args) {
  const visible = visibleLabelLevels(zoom, levels);
  const currentBand = bandForZoom(zoom, levels);

  // Group labels by band so each layer can size independently, but all share one
  // collision group ("labels") so cross-band overlaps are resolved together.
  const byBand = new Map<number, Label[]>();
  for (const lb of labels) {
    if (!visible.has(lb.level)) continue;
    (byBand.get(lb.level) ?? byBand.set(lb.level, []).get(lb.level)!).push(lb);
  }

  const layers = [];
  for (const [band, group] of byBand) {
    const size = labelSizeForBand(band, currentBand);
    layers.push(
      new TextLayer({
        id: `labels-${band}`,
        data: group,
        getPosition: (d: Label) => [d.x, d.y] as [number, number],
        getText: (d: Label) => d.text,
        getSize: size,
        sizeUnits: "pixels",
        getColor: [235, 238, 245, 255],
        outlineColor: [8, 10, 16, 255],
        outlineWidth: 3,
        fontSettings: { sdf: true },
        fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
        fontWeight: band <= 1 ? 700 : 500,
        getTextAnchor: "middle",
        getAlignmentBaseline: "center",
        billboard: true,
        characterSet: "auto",
        // Collision: higher priority wins. priority already encodes coarseness + size.
        extensions: [new CollisionFilterExtension()],
        collisionEnabled: true,
        collisionGroup: "labels",
        getCollisionPriority: (d: Label) => d.priority,
        collisionTestProps: { sizeScale: 2 },
        updateTriggers: {
          getSize: [size],
        },
      }),
    );
  }
  return layers;
}
