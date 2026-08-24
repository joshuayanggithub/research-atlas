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
import { buildOrgTree, searchDirectory, topDirectoryOrgs, type OrgNode } from "./orgHierarchy";

/** What the membership evidence actually is, for the badge tooltip. */
function provenanceTitle(methods: string[]): string {
  if (methods.includes("affiliation_name")) {
    return "Membership from the affiliation text on the papers themselves (curated name "
      + "matching — ROR does not resolve company affiliations)";
  }
  return `Membership from reviewed author roster (${methods.join(", ")})`;
}

export function OrgFilterPanel({ ds }: { ds: Dataset }) {
  const filters = useStore((s) => s.filters);
  const toggleOrg = useStore((s) => s.toggleOrg);
  const compareWith = useStore((s) => s.compareWith);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const tree = useMemo(() => buildOrgTree(ds), [ds]);
  const industry = tree.filter((n) => n.inst.kind === "industry");
  const neolabs = tree.filter((n) => n.inst.kind === "neolab");
  const academic = tree.filter((n) => n.inst.kind !== "industry" && n.inst.kind !== "neolab");

  // Every other institution in the corpus (universities, companies, labs) — searchable but
  // not part of the curated browse tree. Only computed when there's a query.
  const directory = useMemo(() => searchDirectory(ds, query), [ds, query]);
  const directoryTotal = useMemo(
    () => Object.values(ds.orgs.institutions).filter((i) => i.parent === null).length,
    [ds],
  );
  // Largest non-curated institutions, so the directory is browsable without a query.
  const topDirectory = useMemo(() => topDirectoryOrgs(ds, 10), [ds]);

  const q = query.trim().toLowerCase();
  const matchNode = (n: OrgNode): boolean =>
    !q ||
    n.inst.display_name.toLowerCase().includes(q) ||
    n.children.some(matchNode);

  const toggleExpand = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const unitKind = (value: string) => value.replace(/_/g, " ");

  const renderUnit = (node: OrgNode, depth = 0) => {
    const { key, inst } = node;
    const active = filters.orgKeys.includes(key);
    const hasChildren = node.children.length > 0;
    const isOpen = expanded.has(key) || (q.length > 0 && matchNode(node));
    // When searching, hide a child row that doesn't match unless its parent name matched.
    const childVisible = (c: OrgNode) =>
      !q || matchNode(c) ||
      inst.display_name.toLowerCase().includes(q);

    return (
      <div key={key} className={`org-row-group ${depth > 0 ? "child" : "root"}`}>
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
              depth > 0
                ? `${inst.direct_count.toLocaleString()} papers in this corpus whose affiliation names this unit`
                : hasChildren
                  ? `${inst.count.toLocaleString()} papers in this corpus (rollup incl. departments/labs)`
                  : `${inst.count.toLocaleString()} papers in this corpus`
            }
          >
            <span className="org-name">{inst.display_name}</span>
            {depth > 0 && inst.unit_type !== "organization" && (
              <span className="org-unit-kind">{unitKind(inst.unit_type)}</span>
            )}
            {inst.membership_methods.length > 0 && (
              // The badge names the ACTUAL evidence. It used to read "roster" for anything
              // with a provenance, which became a false claim once organizations could be
              // attributed from affiliation strings: Anthropic's papers come from what authors
              // wrote on the paper, not from a reviewed membership list.
              <span className="org-source-badge" title={provenanceTitle(inst.membership_methods)}>
                {inst.membership_methods.includes("affiliation_name") ? "affiliation" : "roster"}
              </span>
            )}
            <span className="count">{inst.count.toLocaleString()}</span>
          </button>
        </div>
        {hasChildren && isOpen && (
          <div className="org-children">
            {node.children.filter(childVisible).map((c) => renderUnit(c, depth + 1))}
          </div>
        )}
        {active && <OrgAuthors node={node} />}
      </div>
    );
  };

  const renderGroup = (title: string, nodes: OrgNode[]) => {
    const visible = nodes.filter(matchNode);
    if (visible.length === 0) return null;
    return (
      <div className="org-group">
        <div className="group-title">{title} · {visible.length}</div>
        <div className="org-tree">{visible.map((n) => renderUnit(n))}</div>
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
              <span className="org-selected-row" key={key}>
                <button
                  type="button"
                  className="chip active org-selected-chip"
                  aria-label={`Remove ${inst.display_name}`}
                  onClick={() => toggleOrg(key)}
                >
                  <span className="org-name">{inst.display_name}</span>
                  <X size={11} aria-hidden="true" />
                </button>
                {/* Same entry point as the author panel: compare from what is already open. */}
                <button
                  type="button"
                  className="text-btn"
                  aria-label={`Compare ${inst.display_name} with another organization or author`}
                  onClick={() =>
                    compareWith({ kind: "org", keys: [key], label: inst.display_name })}
                >
                  Compare
                </button>
              </span>
            );
          })}
        </div>
      )}
      <p className="org-hint subtle">
        Browse organizations and their named units. Search to filter by any of the
        {" "}{directoryTotal.toLocaleString()} institutions in the corpus.
      </p>
      {renderGroup("Companies", industry)}
      {renderGroup("Independent labs", neolabs)}
      {renderGroup("Universities & research institutions", academic)}
      {/* The directory was reachable only by typing a name you already knew, which hid the
          largest institutions in the corpus behind a guess. Show the biggest when idle. */}
      {q.length < 2 && (
        <div className="org-group">
          <div className="group-title">Largest in corpus</div>
          <div className="org-tree">{topDirectory.map((n) => renderUnit(n))}</div>
          <p className="org-hint subtle" style={{ marginTop: 8 }}>
            Counts are papers <em>in this map</em> (CS, co-authored with a featured org), not
            the institution's full output.
          </p>
        </div>
      )}
      {q.length >= 2 && (
        <div className="org-group">
          <div className="group-title">
            In corpus{directory.length ? ` · ${directory.length}${directory.length >= 40 ? "+" : ""}` : ""}
          </div>
          {directory.length === 0 ? (
            <p className="org-hint subtle" style={{ margin: 0 }}>No other institutions match “{query}”.</p>
          ) : (
            <>
              <div className="org-tree">{directory.map((n) => renderUnit(n))}</div>
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
function OrgAuthors({ node }: { node: OrgNode }) {
  const [open, setOpen] = useState(false);
  const filters = useStore((s) => s.filters);
  const setAuthors = useStore((s) => s.setAuthors);
  // Precomputed by s10 (Institution.top_authors), for the browse-tree units only.
  const precomputed = node.inst.top_authors ?? [];
  // The in-browser fallback (count author_ids across the org's node_ids) is gone. It had
  // already stopped working when per-paper author_ids left the resident index in D30, and the
  // ids it scanned are no longer shipped for directory institutions either — s10 precomputes
  // top_authors for the browse tree instead. An org without them now says so rather than
  // rendering a confidently empty list.
  const authors = useMemo(
    () => (open
      ? precomputed.slice(0, 12).map((a) => ({ authorId: a.author_id, name: a.name, count: a.count }))
      : []),
    [open, precomputed],
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
      {open && authors.length === 0 && (
        <p className="org-hint subtle" style={{ margin: "4px 0 0" }}>
          Top researchers are precomputed for the organizations in the browse tree; this
          institution does not have them.
        </p>
      )}
      {open && authors.length > 0 && (
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
