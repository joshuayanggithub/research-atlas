// CS-topic filter over the taxonomy baked into the corpus. Enriched OpenAlex builds provide
// human-readable subfields/topics; the arXiv snapshot currently provides category codes.
//   - Subfield: 11 coarse CS areas (Artificial Intelligence, Computer Vision, …) as toggles.
//   - Topic: type-ahead over the ~288 fine topics.
// Both feed store filters (subfieldIds / topicIds); useFilterMask ANDs them with org/author/
// date and GPU-culls non-matching papers. A paper passes if its subfield is selected (when
// any) AND its topic is selected (when any).

import { useMemo, useState } from "react";
import { ChevronRight, X } from "lucide-react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";

interface TopicOption {
  id: number;
  name: string;
  count: number;
  parent?: number | null;
}

interface FieldGroup {
  id: number;
  name: string;
  count: number;
  subfields: TopicOption[];
}

export function TopicFilter({ ds }: { ds: Dataset }) {
  const filters = useStore((s) => s.filters);
  const setSubfieldIds = useStore((s) => s.setSubfieldIds);
  const setTopicIds = useStore((s) => s.setTopicIds);
  const [query, setQuery] = useState("");
  // null = nothing chosen yet (the largest field shows); -1 = user closed it.
  const [expanded, setExpanded] = useState<number | null>(null);
  const usesArxivCategories = ds.manifest.corpus.field.startsWith("arxiv:");

  // Only offer subfields/topics that actually occur in the corpus points, so the controls
  // never list an empty facet. Names come from topics.json.
  // Grouped by FIELD, because 243 subfields in one flat alphabetical list is not a control:
  // it opened with "Rehabilitation", "Renewable Energy" and "Reproductive Medicine" on a map
  // that is 70% computer science. topics.json has always carried a 26-entry `field` level and
  // a parent link on every subfield; nothing used them.
  //
  // Counts come from the artifact (s10), not from ds.points: the browser can only count points
  // it has downloaded, and reveal-level tiles are importance-ordered, so an in-browser count
  // would rank facets by which tiles had arrived and keep changing as more did.
  const { fields, topics } = useMemo(() => {
    const presentTopic = new Set<number>(ds.points.topicId);
    const topics: TopicOption[] = [];
    const subByField = new Map<number, TopicOption[]>();
    const fieldNodes: TopicOption[] = [];
    for (const node of ds.topics.nodes) {
      if (node.level === "field") {
        fieldNodes.push({ id: node.id, name: node.name, count: node.count ?? 0 });
      } else if (node.level === "subfield") {
        const parent = node.parent ?? -1;
        const list = subByField.get(parent) ?? [];
        list.push({ id: node.id, name: node.name, count: node.count ?? 0, parent });
        subByField.set(parent, list);
      } else if (node.level === "topic" && presentTopic.has(node.id)) {
        topics.push({ id: node.id, name: node.name, count: node.count ?? 0 });
      }
    }
    const fields: FieldGroup[] = fieldNodes
      .map((f) => ({
        id: f.id,
        name: f.name,
        count: f.count,
        subfields: (subByField.get(f.id) ?? []).sort((a, b) => b.count - a.count),
      }))
      .filter((f) => f.subfields.length > 0)
      .sort((a, b) => b.count - a.count);
    return { fields, topics };
  }, [ds]);

  const topicName = useMemo(() => new Map(topics.map((t) => [t.id, t.name])), [topics]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < 2) return [];
    return topics.filter((t) => t.name.toLowerCase().includes(q)).slice(0, 8);
  }, [query, topics]);

  const toggleSubfield = (id: number) => {
    setSubfieldIds(
      filters.subfieldIds.includes(id)
        ? filters.subfieldIds.filter((x) => x !== id)
        : [...filters.subfieldIds, id],
    );
  };

  const chooseTopic = (id: number) => {
    if (!filters.topicIds.includes(id)) setTopicIds([...filters.topicIds, id]);
    setQuery("");
  };

  return (
    <div className="filter-section">
      <h4>{usesArxivCategories ? "arXiv category" : "CS topic"}</h4>

      {/* Fields, largest first; a field's subfields appear when it is opened. The biggest
          field starts open so the panel is useful without a click, and a field holding a
          selected subfield opens itself so an active filter is never hidden. */}
      <div className="topic-fields">
        {fields.map((f) => {
          const selectedHere = f.subfields.filter((s) => filters.subfieldIds.includes(s.id)).length;
          const open = expanded === f.id || (expanded === null && f === fields[0]) || selectedHere > 0;
          return (
            <div className="topic-field" key={f.id}>
              <button
                type="button"
                className={`topic-field-head ${open ? "open" : ""}`}
                aria-expanded={open}
                onClick={() => setExpanded(open && expanded === f.id ? -1 : f.id)}
              >
                <ChevronRight size={13} aria-hidden="true" className="topic-caret" />
                <span className="topic-field-name">{f.name}</span>
                {selectedHere > 0 && <span className="topic-field-active">{selectedHere}</span>}
                <span className="count">{f.count.toLocaleString()}</span>
              </button>
              {open && (
                <div className="chips topic-subfields">
                  {f.subfields.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      className={`chip ${filters.subfieldIds.includes(s.id) ? "active" : ""}`}
                      aria-pressed={filters.subfieldIds.includes(s.id)}
                      onClick={() => toggleSubfield(s.id)}
                    >
                      {s.name}
                      <span className="count">{s.count.toLocaleString()}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Fine-topic type-ahead. */}
      <div className="autocomplete-field">
        <input
          className="author-input"
          aria-label="Search CS topics"
          placeholder={usesArxivCategories ? "Search category codes…" : "Search fine topics…"}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {matches.length > 0 && (
          <ul className="autocomplete" role="listbox">
            {matches.map((t) => (
              <li key={t.id} role="option" aria-selected={false}>
                <button type="button" onClick={() => chooseTopic(t.id)}>
                  {t.name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Selected fine topics as removable chips. */}
      {filters.topicIds.length > 0 && (
        <div className="chips">
          {filters.topicIds.map((id) => (
            <button
              key={id}
              type="button"
              className="chip active"
              aria-label={`Remove ${topicName.get(id) ?? "topic"}`}
              onClick={() => setTopicIds(filters.topicIds.filter((x) => x !== id))}
            >
              {topicName.get(id) ?? `Topic ${id}`}
              <X size={12} aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
