// A sidebar block that starts closed.
//
// The sidebar rendered every panel expanded at once — Compare (with a three-line explanation)
// held the top slot above the filters, and Organizations, categories, authors, citations,
// dates, the reading list and the legend all followed at full height. Everything competed for
// attention and nothing could be scanned. Blocks that are not corpus filters now collapse, so
// the sidebar opens as a short list of headings the reader can choose from.
//
// Collapsed means UNMOUNTED, not hidden: the reading list and the legend do real work on mount
// (parsing an imported library, subscribing to edge state), and a closed drawer should not pay
// for it.

import { ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";

export function CollapsibleSection({
  title,
  badge,
  defaultOpen = false,
  children,
}: {
  title: string;
  /** Short active-state marker, e.g. the number of selections. Omitted when inactive. */
  badge?: string | number | null;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`filter-section collapsible ${open ? "open" : ""}`}>
      {/* Heading wraps the button (the standard accordion pattern) so the block keeps a real
          heading in the accessibility tree and stays reachable by role. */}
      <h4 className="collapsible-head">
        <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <ChevronRight size={13} className="collapsible-chev" aria-hidden="true" />
          <span>{title}</span>
          {badge !== undefined && badge !== null && badge !== "" && (
            <span className="collapsible-badge">{badge}</span>
          )}
        </button>
      </h4>
      {open && <div className="collapsible-body">{children}</div>}
    </section>
  );
}
