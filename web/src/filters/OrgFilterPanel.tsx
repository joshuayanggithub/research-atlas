// Organization filter: toggle chips for each org (grouped by kind). Selecting orgs dims
// (or hides) non-matching points on the map. Also exposes the dim/hide toggle.

import type { Dataset } from "../data/types";
import { useStore } from "../state/store";

export function OrgFilterPanel({ ds }: { ds: Dataset }) {
  const filters = useStore((s) => s.filters);
  const toggleOrg = useStore((s) => s.toggleOrg);
  const orgDisplayMode = useStore((s) => s.orgDisplayMode);
  const setOrgDisplayMode = useStore((s) => s.setOrgDisplayMode);

  const insts = ds.orgs.institutions;
  const keys = Object.keys(insts);
  const industry = keys.filter((k) => insts[k].kind === "industry");
  const academic = keys.filter((k) => insts[k].kind !== "industry");

  const renderGroup = (title: string, groupKeys: string[]) => (
    <div className="org-group">
      <div className="group-title">{title}</div>
      <div className="chips">
        {groupKeys.map((k) => {
          const active = filters.orgKeys.includes(k);
          return (
            <button
              key={k}
              className={`chip ${active ? "active" : ""}`}
              onClick={() => toggleOrg(k)}
              title={`${insts[k].count.toLocaleString()} papers`}
            >
              {insts[k].display_name}
              <span className="count">{insts[k].count.toLocaleString()}</span>
            </button>
          );
        })}
      </div>
    </div>
  );

  return (
    <div className="filter-section">
      <div className="section-head">
        <h4>Organizations</h4>
        {filters.orgKeys.length > 0 && (
          <div className="seg small">
            <button
              className={orgDisplayMode === "dim" ? "active" : ""}
              onClick={() => setOrgDisplayMode("dim")}
            >
              dim
            </button>
            <button
              className={orgDisplayMode === "hide" ? "active" : ""}
              onClick={() => setOrgDisplayMode("hide")}
            >
              hide
            </button>
          </div>
        )}
      </div>
      {renderGroup("Industry", industry)}
      {renderGroup("Academia", academic)}
    </div>
  );
}
