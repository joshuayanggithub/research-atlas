// Map display controls and the active point-color legend.

import { Network } from "lucide-react";
import type { Dataset } from "../data/types";
import { useStore, type ColorMode } from "../state/store";
import { ORG_COLORS } from "../map/colors";
import { rootOrgKeys } from "../filters/orgHierarchy";

export function Legend({ ds }: { ds: Dataset }) {
  const colorMode = useStore((s) => s.colorMode);
  const setColorMode = useStore((s) => s.setColorMode);
  const showCitationEdges = useStore((s) => s.showCitationEdges);
  const setShowCitationEdges = useStore((s) => s.setShowCitationEdges);

  // Subfield legend: derive present subfields + their color from points.arrow.
  const subfieldColors = new Map<number, [number, number, number]>();
  const subfieldNames = new Map<number, string>();
  for (const n of ds.topics.nodes) {
    if (n.level === "subfield") subfieldNames.set(n.id, n.name);
  }
  const pts = ds.points;
  for (let i = 0; i < pts.count && subfieldColors.size < 20; i++) {
    const s = pts.subfieldId[i];
    if (!subfieldColors.has(s)) subfieldColors.set(s, [pts.r[i], pts.g[i], pts.b[i]]);
  }

  return (
    <div className="legend">
      <div className="edge-map-control">
        <div className="edge-map-head">
          <span>
            <Network size={14} aria-hidden="true" />
            Citation edges
          </span>
          {ds.manifest.corpus.citation_graph_source ? (
            <label className="switch">
              <input
                type="checkbox"
                checked={showCitationEdges}
                onChange={(event) => setShowCitationEdges(event.target.checked)}
                aria-label="Show citation edges on map"
              />
              <span className="switch-track" aria-hidden="true" />
            </label>
          ) : (
            <span className="subtle">unavailable</span>
          )}
        </div>
        {/* Phrased as influence rather than graph direction: "outgoing/incoming" tells you
            which way an arrow points, not what it means. With a paper selected these same
            colours tint the connected papers themselves (usePointsLayer). */}
        {ds.manifest.corpus.citation_graph_source && showCitationEdges && (
          <div className="edge-key">
            <span><i className="edge-key-line global" /> citations</span>
            <span><i className="edge-key-line outgoing" /> influenced this ← references</span>
            <span><i className="edge-key-line incoming" /> influenced by this → citations</span>
            {/* No arrowhead in the swatch: similarity is symmetric, and the map draws it
                without one for the same reason. */}
            <span><i className="edge-key-line similar" /> similar work (no citation)</span>
          </div>
        )}
      </div>
      <div className="color-by-label subtle">Color by</div>
      <div className="seg small" role="group" aria-label="Color points by">
        {(
          [
            ["subfield", "Subfield"],
            ["org", "Organization"],
            ["recency", "Recency"],
          ] as [ColorMode, string][]
        ).map(([m, label]) => (
          <button
            type="button"
            key={m}
            className={colorMode === m ? "active" : ""}
            aria-pressed={colorMode === m}
            onClick={() => setColorMode(m)}
          >
            {label}
          </button>
        ))}
      </div>
      {colorMode === "subfield" && (
        <div className="legend-items">
          {Array.from(subfieldColors)
            .filter(([s]) => subfieldNames.has(s))
            .map(([s, c]) => (
              <div key={s} className="legend-item">
                <span className="swatch" style={{ background: `rgb(${c[0]},${c[1]},${c[2]})` }} />
                {subfieldNames.get(s)}
              </div>
            ))}
        </div>
      )}
      {colorMode === "org" && (
        <div className="legend-items">
          {rootOrgKeys(ds).map((k, i) => (
            <div key={k} className="legend-item">
              <span
                className="swatch"
                style={{ background: `rgb(${ORG_COLORS[i % ORG_COLORS.length].join(",")})` }}
              />
              {ds.orgs.institutions[k].display_name}
            </div>
          ))}
        </div>
      )}
      {colorMode === "recency" && (
        <div className="legend-items">
          <div className="legend-item">
            <span className="swatch" style={{ background: "rgb(70,90,160)" }} /> older
          </div>
          <div className="legend-item">
            <span className="swatch" style={{ background: "rgb(250,180,60)" }} /> newer
          </div>
        </div>
      )}
    </div>
  );
}
