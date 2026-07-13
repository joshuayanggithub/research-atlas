// Color-mode switch + a legend of subfields (or orgs) with their palette colors.

import type { Dataset } from "../data/types";
import { useStore, type ColorMode } from "../state/store";
import { ORG_COLORS } from "../map/colors";

export function Legend({ ds }: { ds: Dataset }) {
  const colorMode = useStore((s) => s.colorMode);
  const setColorMode = useStore((s) => s.setColorMode);

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
      <div className="seg small">
        {(["subfield", "org", "recency"] as ColorMode[]).map((m) => (
          <button key={m} className={colorMode === m ? "active" : ""} onClick={() => setColorMode(m)}>
            {m}
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
          {Object.keys(ds.orgs.institutions).map((k, i) => (
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
