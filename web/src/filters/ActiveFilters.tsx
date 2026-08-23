// One place that answers "what am I looking at?".
//
// The facets are spread across the UI by necessity — orgs and categories in the left sidebar,
// authors in their own panel, dates and citations further down — so with several active there
// was no single view of the current constraint, and no quick way to drop just one of them.
// This bar sits under the top bar and lists every active facet as a removable chip, with the
// resulting paper count. It does not replace any control; it summarises them and can remove.

import { X } from "lucide-react";
import { useMemo } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";
import { UNLOADED_LEVEL } from "../data/loadArtifacts";
import { useAuthorInfo } from "../data/useAuthorLookup";
import { useFilterMask } from "./useFilterMask";

interface Chip {
  key: string;
  /** Facet name, e.g. "Org" — kept short; the value carries the meaning. */
  kind: string;
  label: string;
  remove: () => void;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthLabel = (fromYear: number, m: number) =>
  `${MONTHS[((m % 12) + 12) % 12]} ${fromYear + Math.floor(m / 12)}`;

export function ActiveFilters({ ds }: { ds: Dataset }) {
  const filters = useStore((s) => s.filters);
  const toggleOrg = useStore((s) => s.toggleOrg);
  const setAuthors = useStore((s) => s.setAuthors);
  const setSubfieldIds = useStore((s) => s.setSubfieldIds);
  const setLabelIds = useStore((s) => s.setLabelIds);
  const setTopicIds = useStore((s) => s.setTopicIds);
  const setReadingLists = useStore((s) => s.setReadingLists);
  const setCitationRange = useStore((s) => s.setCitationRange);
  const setMonthRange = useStore((s) => s.setMonthRange);
  const clearFilters = useStore((s) => s.clearFilters);

  // Authors are only needed to name the chips, and they are already loaded by the time an
  // author filter can exist — so this never triggers the 55.9 MB fetch on its own.
  // Names for the selected authors only — the full index is no longer downloaded (D59).
  const authorInfo = useAuthorInfo(filters.authorIds);
  const authors = [...authorInfo.values()];
  const filter = useFilterMask(ds, filters);

  const fromYear = parseInt(ds.manifest.corpus.date_from.slice(0, 4));
  const toYear = parseInt(ds.manifest.corpus.date_to.slice(0, 4));
  const toMonth = parseInt(ds.manifest.corpus.date_to.slice(5, 7)) || 12;
  const maxMonth = (toYear - fromYear) * 12 + (toMonth - 1);
  const dateActive = filters.monthMin !== 0 || filters.monthMax !== maxMonth;

  const subfieldName = useMemo(() => {
    const m = new Map<number, string>();
    for (const nd of ds.topics.nodes) if (nd.level === "subfield") m.set(nd.id, nd.name);
    return m;
  }, [ds]);
  const topicName = useMemo(() => {
    const m = new Map<number, string>();
    for (const nd of ds.topics.nodes) if (nd.level === "topic") m.set(nd.id, nd.name);
    return m;
  }, [ds]);

  const chips: Chip[] = [];

  for (const key of filters.orgKeys) {
    chips.push({
      key: `org-${key}`,
      kind: "Org",
      label: ds.orgs.institutions[key]?.display_name ?? key,
      remove: () => toggleOrg(key),
    });
  }

  // Author ids are selected as a whole identity (several rows share one name), so collapse
  // them back to one chip per person rather than showing the same name repeatedly.
  const authorById = useMemo(() => new Map(authors.map((a) => [a.authorId, a])), [authors]);
  const seenAuthor = new Set<string>();
  for (const id of filters.authorIds) {
    const name = authorById.get(id)?.name;
    if (!name || seenAuthor.has(name)) continue;
    seenAuthor.add(name);
    chips.push({
      key: `author-${name}`,
      kind: "Author",
      label: name,
      remove: () =>
        setAuthors(filters.authorIds.filter((x) => authorById.get(x)?.name !== name)),
    });
  }

  // Region labels read as the most concrete facet ("cs.CV: Gaussian Splatting"), so they come
  // before the coarser category/topic chips.
  const labelText = useMemo(
    () => new Map(ds.labels.labels.map((l) => [l.id, l.text])),
    [ds],
  );
  for (const id of filters.labelIds) {
    chips.push({
      key: `label-${id}`,
      kind: "Region",
      label: labelText.get(id) ?? `#${id}`,
      remove: () => setLabelIds(filters.labelIds.filter((x) => x !== id)),
    });
  }

  // Reading-list chips come first: "my library" is the strongest framing of a view, and the
  // other facets read as narrowing it.
  for (const name of filters.readingLists) {
    chips.unshift({
      key: `reading-${name}`,
      kind: "Read",
      label: name,
      remove: () => setReadingLists(filters.readingLists.filter((n) => n !== name)),
    });
  }

  for (const id of filters.subfieldIds) {
    chips.push({
      key: `sub-${id}`,
      kind: "Category",
      label: subfieldName.get(id) ?? `#${id}`,
      remove: () => setSubfieldIds(filters.subfieldIds.filter((x) => x !== id)),
    });
  }
  for (const id of filters.topicIds) {
    chips.push({
      key: `topic-${id}`,
      kind: "Topic",
      label: topicName.get(id) ?? `#${id}`,
      remove: () => setTopicIds(filters.topicIds.filter((x) => x !== id)),
    });
  }
  if (filters.citeMin > 0 || filters.citeMax !== null) {
    chips.push({
      key: "cites",
      kind: "Citations",
      label: `${filters.citeMin.toLocaleString()}–${
        filters.citeMax === null ? "any" : filters.citeMax.toLocaleString()
      }`,
      remove: () => setCitationRange(0, null),
    });
  }
  if (dateActive) {
    chips.push({
      key: "date",
      kind: "Dates",
      label: `${monthLabel(fromYear, filters.monthMin)} – ${monthLabel(fromYear, filters.monthMax)}`,
      remove: () => setMonthRange(0, maxMonth),
    });
  }

  if (chips.length === 0) return null;

  const total = ds.points.count;
  // matchValue covers org/author/category/topic/citations; the date range is applied on the
  // GPU, so count it here to report the number the map actually shows.
  // Two numbers, not one. `matched` is how many papers pass the filters; `drawn` is how many
  // of those the map can actually show right now, because point coordinates arrive as
  // progressive tiles (D23) and a paper whose tile has not landed has no position yet.
  //
  // Reporting only `matched` is how a 17-paper reading list looked like a 6-paper one: the bar
  // claimed 17, the map drew 6, and nothing said the difference was still downloading.
  const { matched, drawn } = (() => {
    if (!filter) return { matched: total, drawn: total };
    const { monthIndex, revealLevel } = ds.points;
    let k = 0;
    let d = 0;
    for (let i = 0; i < total; i++) {
      if (filter.matchValue[i] !== 1) continue;
      const m = monthIndex[i];
      if (m < filters.monthMin || m > filters.monthMax) continue;
      k++;
      if (revealLevel[i] !== UNLOADED_LEVEL) d++;
    }
    return { matched: k, drawn: d };
  })();

  return (
    <div className="active-filters" role="region" aria-label="Active filters">
      <span className="active-filters-count">
        {/* A count is only a count once the selection's membership has arrived. Reporting the
            in-flight state as "0 of 1,000,490" claimed the filter matched nothing, a second
            before it matched 16,844. */}
        {filter?.pending ? (
          <strong className="count-skeleton" role="img" aria-label="Counting matches" />
        ) : (
          <strong>{matched.toLocaleString()}</strong>
        )}
        <span className="subtle"> of {total.toLocaleString()} papers</span>
        {/* Only while it is actually true: a filter whose papers have all arrived says nothing
            extra, so this never becomes background noise. */}
        {drawn < matched && (
          <span className="subtle loading-note" title="Point positions stream in progressively">
            {" "}· {drawn.toLocaleString()} on the map so far
          </span>
        )}
      </span>
      <div className="active-filters-chips">
        {chips.map((c) => (
          <button
            key={c.key}
            type="button"
            className="active-chip"
            title={`Remove ${c.kind}: ${c.label}`}
            aria-label={`Remove ${c.kind} filter ${c.label}`}
            onClick={c.remove}
          >
            <span className="active-chip-kind">{c.kind}</span>
            <span className="active-chip-label">{c.label}</span>
            <X size={11} aria-hidden="true" />
          </button>
        ))}
      </div>
      <button type="button" className="text-btn active-filters-clear" onClick={clearFilters}>
        Clear all
      </button>
    </div>
  );
}
