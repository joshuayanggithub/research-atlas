// CS-topic filter over the taxonomy baked into the corpus. Enriched OpenAlex builds provide
// human-readable subfields/topics; the arXiv snapshot currently provides category codes.
//   - Subfield: 11 coarse CS areas (Artificial Intelligence, Computer Vision, …) as toggles.
//   - Topic: type-ahead over the ~288 fine topics.
// Both feed store filters (subfieldIds / topicIds); useFilterMask ANDs them with org/author/
// date and GPU-culls non-matching papers. A paper passes if its subfield is selected (when
// any) AND its topic is selected (when any).

import { useMemo, useState } from "react";
import { X } from "lucide-react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";

interface TopicOption {
  id: number;
  name: string;
}

export function TopicFilter({ ds }: { ds: Dataset }) {
  const filters = useStore((s) => s.filters);
  const setSubfieldIds = useStore((s) => s.setSubfieldIds);
  const setTopicIds = useStore((s) => s.setTopicIds);
  const [query, setQuery] = useState("");
  const usesArxivCategories = ds.manifest.corpus.field.startsWith("arxiv:");

  // Only offer subfields/topics that actually occur in the corpus points, so the controls
  // never list an empty facet. Names come from topics.json.
  const { subfields, topics } = useMemo(() => {
    const presentSub = new Set<number>(ds.points.subfieldId);
    const presentTopic = new Set<number>(ds.points.topicId);
    const subfields: TopicOption[] = [];
    const topics: TopicOption[] = [];
    for (const node of ds.topics.nodes) {
      if (node.level === "subfield" && presentSub.has(node.id)) subfields.push({ id: node.id, name: node.name });
      else if (node.level === "topic" && presentTopic.has(node.id)) topics.push({ id: node.id, name: node.name });
    }
    subfields.sort((a, b) => a.name.localeCompare(b.name));
    return { subfields, topics };
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

      {/* Coarse subfields as toggle chips. */}
      <div className="chips topic-subfields">
        {subfields.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`chip ${filters.subfieldIds.includes(s.id) ? "active" : ""}`}
            aria-pressed={filters.subfieldIds.includes(s.id)}
            onClick={() => toggleSubfield(s.id)}
          >
            {s.name}
          </button>
        ))}
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
