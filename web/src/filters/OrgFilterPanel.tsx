// Organization filter as a searchable drill-down hierarchy:
//   Root org (University / Company)  ->  Department / Lab  ->  researchers
// Selecting a parent includes its whole rollup (deduplicated); selecting a child includes
// only that unit's evidence-backed papers. Direct vs rollup counts are shown so a parent
// aggregate is never mistaken for a specific lab. Units come from raw-affiliation evidence
// (see docs/ORGANIZATION_DIRECTORY.md); a parent match never implies a child.

import { useMemo, useState } from "react";
import { ChevronRight, Users, X } from "lucide-react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";
import { buildOrgTree, searchDirectory, topAuthorsInNodes, type OrgNode } from "./orgHierarchy";

export function OrgFilterPanel({ ds }: { ds: Dataset }) {
  const filters = useStore((s) => s.filters);
  const toggleOrg = useStore((s) => s.toggleOrg);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const tree = useMemo(() => buildOrgTree(ds), [ds]);
  const industry = tree.filter((n) => n.inst.kind === "industry");
  const academic = tree.filter((n) => n.inst.kind !== "industry");

  // Every other institution in the corpus (universities, companies, labs) — searchable but
  // not part of the curated browse tree. Only computed when there's a query.
  const directory = useMemo(() => searchDirectory(ds, query), [ds, query]);
  const directoryTotal = useMemo(
    () => Object.values(ds.orgs.institutions).filter((i) => i.parent === null).length,
    [ds],
  );

  const q = query.trim().toLowerCase();
  const matchNode = (n: OrgNode): boolean =>
    !q ||
    n.inst.display_name.toLowerCase().includes(q) ||
    n.children.some((c) => c.inst.display_name.toLowerCase().includes(q));

  const toggleExpand = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const renderUnit = (node: OrgNode, isChild: boolean) => {
    const { key, inst } = node;
    const active = filters.orgKeys.includes(key);
    const hasChildren = node.children.length > 0;
    const isOpen = expanded.has(key) || (q.length > 0 && matchNode(node));
    // When searching, hide a child row that doesn't match unless its parent name matched.
    const childVisible = (c: OrgNode) =>
      !q || c.inst.display_name.toLowerCase().includes(q) ||
      inst.display_name.toLowerCase().includes(q);

    return (
      <div key={key} className={`org-row-group ${isChild ? "child" : ""}`}>
        <div className="org-row">
          {hasChildren ? (
            <button
              type="button"
              className={`org-expand ${isOpen ? "open" : ""}`}
              aria-label={isOpen ? `Collapse ${inst.display_name}` : `Expand ${inst.display_name}`}
              aria-expanded={isOpen}
              onClick={() => toggleExpand(key)}
            >
              <ChevronRight size={14} aria-hidden="true" />
            </button>
          ) : (
            <span className="org-expand-spacer" aria-hidden="true" />
          )}
          <button
            type="button"
            className={`chip org-chip ${active ? "active" : ""}`}
            aria-pressed={active}
            onClick={() => toggleOrg(key)}
            title={
              isChild
                ? `${inst.direct_count.toLocaleString()} papers in this corpus whose affiliation names this unit`
                : hasChildren
                  ? `${inst.count.toLocaleString()} papers in this corpus (rollup incl. departments/labs)`
                  : `${inst.count.toLocaleString()} papers in this corpus`
            }
          >
            <span className="org-name">{inst.display_name}</span>
            <span className="count">{inst.count.toLocaleString()}</span>
          </button>
        </div>
        {hasChildren && isOpen && (
          <div className="org-children">
            {node.children.filter(childVisible).map((c) => renderUnit(c, true))}
          </div>
        )}
        {active && <OrgAuthors ds={ds} node={node} />}
      </div>
    );
  };

  const renderGroup = (title: string, nodes: OrgNode[]) => {
    const visible = nodes.filter(matchNode);
    if (visible.length === 0) return null;
    return (
      <div className="org-group">
        <div className="group-title">{title}</div>
        <div className="org-tree">{visible.map((n) => renderUnit(n, false))}</div>
      </div>
    );
  };

  return (
    <div className="filter-section">
      <div className="section-head">
        <h4>Organizations</h4>
      </div>
      <input
        className="org-search"
        aria-label="Search organizations, departments, and labs"
        placeholder="Search org, department, lab..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {/* Always-visible active selections — so an org picked via search (e.g. a directory
          institution) stays removable after the query is cleared, not stranded off-panel. */}
      {filters.orgKeys.length > 0 && (
        <div className="org-selected" aria-label="Selected organizations">
          {filters.orgKeys.map((key) => {
            const inst = ds.orgs.institutions[key];
            if (!inst) return null;
            return (
              <button
                type="button"
                key={key}
                className="chip active org-selected-chip"
                aria-label={`Remove ${inst.display_name}`}
                onClick={() => toggleOrg(key)}
              >
                <span className="org-name">{inst.display_name}</span>
                <X size={11} aria-hidden="true" />
              </button>
            );
          })}
        </div>
      )}
      <p className="org-hint subtle">
        Featured orgs drill into departments/labs. Search to filter by any of the
        {" "}{directoryTotal.toLocaleString()} institutions in the corpus.
      </p>
      {renderGroup("Featured · Industry", industry)}
      {renderGroup("Featured · Academia", academic)}
      {q.length >= 2 && (
        <div className="org-group">
          <div className="group-title">
            In corpus{directory.length ? ` · ${directory.length}${directory.length >= 40 ? "+" : ""}` : ""}
          </div>
          {directory.length === 0 ? (
            <p className="org-hint subtle" style={{ margin: 0 }}>No other institutions match “{query}”.</p>
          ) : (
            <>
              <div className="org-tree">{directory.map((n) => renderUnit(n, false))}</div>
              <p className="org-hint subtle" style={{ marginTop: 8 }}>
                Counts are papers <em>in this map</em> (CS, co-authored with a featured org),
                not the institution's full output. Large orgs may appear as several regional
                OpenAlex entities.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// Organization-scoped researcher browsing: the top authors within the selected unit's
// papers. Clicking a researcher adds them to the author filter to explore their work.
function OrgAuthors({ ds, node }: { ds: Dataset; node: OrgNode }) {
  const [open, setOpen] = useState(false);
  const filters = useStore((s) => s.filters);
  const setAuthors = useStore((s) => s.setAuthors);
  const authors = useMemo(
    () => (open ? topAuthorsInNodes(ds, node.inst.node_ids, 12) : []),
    [open, ds, node.inst.node_ids],
  );

  const addAuthor = (authorId: number) => {
    if (!filters.authorIds.includes(authorId)) setAuthors([...filters.authorIds, authorId]);
  };

  return (
    <div className="org-authors">
      <button
        type="button"
        className="org-authors-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Users size={12} aria-hidden="true" />
        {open ? "Hide researchers" : "Top researchers"}
      </button>
      {open && (
        <ul className="org-author-list">
          {authors.map((a) => {
            const selected = filters.authorIds.includes(a.authorId);
            return (
              <li key={a.authorId}>
                <button
                  type="button"
                  className={selected ? "selected" : ""}
                  onClick={() => addAuthor(a.authorId)}
                  title={selected ? "Already in author filter" : "Add to author filter"}
                >
                  <span>{a.name}</span>
                  <span className="count">{a.count}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
