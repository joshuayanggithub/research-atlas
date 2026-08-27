import { useEffect, useState } from "react";
import { loadDataset, type LoadProgress } from "./data/loadArtifacts";
import { useStore } from "./state/store";
import { MapView } from "./map/MapView";
import { OrgFilterPanel } from "./filters/OrgFilterPanel";
import { CompareSetup } from "./filters/ComparePanel";
import { CompareResults } from "./panels/CompareResults";
import { AuthorFilter } from "./filters/AuthorFilter";
import { CitationFilter } from "./filters/CitationFilter";
import { ActiveFilters } from "./filters/ActiveFilters";
import { CollapsibleSection } from "./filters/CollapsibleSection";
import { useUrlSync } from "./state/useUrlSync";
import { ShareLinkButton } from "./panels/ShareLinkButton";
import { ReadingListPanel } from "./filters/ReadingListPanel";
import { LoadingStatus } from "./panels/LoadingStatus";
import { TopicFilter } from "./filters/TopicFilter";
import { DateRangeSlider } from "./filters/DateRangeSlider";
import { DetailsPanel } from "./panels/DetailsPanel";
import { AuthorPanel } from "./panels/AuthorPanel";
import { PaperListPanel } from "./panels/PaperListPanel";
import { Legend } from "./panels/Legend";
import { SearchBox } from "./panels/SearchBox";

// Who made this. Change these two lines to change the credit everywhere it appears.
const AUTHOR_NAME = "Joshua Yang";
const AUTHOR_GITHUB = "joshuayanggithub";

export default function App() {
  const dataset = useStore((s) => s.dataset);
  const loading = useStore((s) => s.loading);
  const error = useStore((s) => s.error);
  const setDataset = useStore((s) => s.setDataset);
  const setError = useStore((s) => s.setError);
  const clearFilters = useStore((s) => s.clearFilters);
  const filters = useStore((s) => s.filters);
  const compare = useStore((s) => s.compare);
  const selectedNode = useStore((s) => s.selectedNode);
  const [filtersOpen, setFiltersOpen] = useState(() =>
    window.matchMedia("(min-width: 721px)").matches,
  );

  const [progress, setProgress] = useState<LoadProgress | null>(null);

  // Above every early return: hooks must run in the same order on every render.
  useUrlSync(dataset);

  useEffect(() => {
    loadDataset(setProgress)
      .then(setDataset)
      .catch((e) => setError(String(e)));
  }, [setDataset, setError]);

  useEffect(() => {
    const query = window.matchMedia("(min-width: 721px)");
    const onChange = (event: MediaQueryListEvent) => setFiltersOpen(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (selectedNode !== null && window.innerWidth <= 720) setFiltersOpen(false);
  }, [selectedNode]);

  if (error) {
    return (
      <div className="fullscreen center">
        <div className="error">
          <h2>Failed to load data</h2>
          <p>{error}</p>
          <p className="subtle">
            Run the pipeline first: <code>uv run python -m pipeline.run_all</code>
          </p>
        </div>
      </div>
    );
  }

  if (loading || !dataset) {
    // Real progress, not an animation: the percentage is uncompressed bytes received against
    // the exact sizes s11 recorded in the manifest, so it neither races ahead nor parks at 99%.
    const pct = Math.round((progress?.pct ?? 0) * 100);
    const mb = (n: number) => (n / 1048576).toFixed(0);
    return (
      <div className="fullscreen center">
        <div className="loading-panel">
          <div className="loading">Loading the research map…</div>
          <div
            className="loading-bar"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            aria-label="Loading the research map"
          >
            <div className="loading-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <div className="loading-meta subtle">
            <span>{progress?.detail ?? "starting"}</span>
            <span>
              {progress && progress.totalBytes > 0
                ? `${pct}% · ${mb(progress.loadedBytes)}/${mb(progress.totalBytes)} MB`
                : `${pct}%`}
            </span>
          </div>
        </div>
      </div>
    );
  }

  const m = dataset.manifest;
  const fromYear = parseInt(m.corpus.date_from.slice(0, 4));
  const toYear = parseInt(m.corpus.date_to.slice(0, 4));
  const toMonth = parseInt(m.corpus.date_to.slice(5, 7)) || 12;
  const fullMaxMonth = (toYear - fromYear) * 12 + (toMonth - 1);
  const filtersActive =
    filters.orgKeys.length > 0 ||
    filters.authorIds.length > 0 ||
    filters.subfieldIds.length > 0 ||
    filters.topicIds.length > 0 ||
    filters.citeMin > 0 ||
    filters.citeMax !== null ||
    filters.monthMin !== 0 ||
    filters.monthMax !== fullMaxMonth;

  return (
    <div className="app">
      <main className="map-shell" aria-label="Research map">
        <MapView ds={dataset} />
      </main>

      <header className="topbar">
        <div className="title">
          <strong>Research Atlas</strong>
          <span className="subtle">
            {m.corpus.count.toLocaleString()} papers · {m.corpus.date_from.slice(0, 4)}–
            {m.corpus.date_to.slice(0, 4)} · {m.embedding.backend}
          </span>
          {/* Author credit. Its own class rather than `subtle`, which the mobile header hides
              to stay compact — a credit that disappears on a phone is not a credit. */}
          <a
            className="byline"
            href={`https://github.com/${AUTHOR_GITHUB}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            by {AUTHOR_NAME}
          </a>
        </div>
        <SearchBox ds={dataset} />
        <button
          type="button"
          className="mobile-filter-toggle"
          aria-controls="filters-panel"
          aria-expanded={filtersOpen}
          onClick={() => setFiltersOpen((open) => !open)}
        >
          Filters
        </button>
      </header>

      <ActiveFilters ds={dataset} />
      <LoadingStatus />

      <aside
        id="filters-panel"
        className={`sidebar ${filtersOpen ? "open" : ""}`}
        aria-label="Research filters"
      >
        <div className="sidebar-head">
          <h3>Filters</h3>
          <div className="sidebar-actions">
            <ShareLinkButton />
            {filtersActive && (
              <button type="button" className="text-btn" onClick={clearFilters}>
                Clear
              </button>
            )}
            <button
              type="button"
              className="mobile-sidebar-close"
              aria-label="Close filters"
              onClick={() => setFiltersOpen(false)}
            >
              Close
            </button>
          </div>
        </div>
        {/* Corpus filters first and expanded — they are what the sidebar is for. Everything
            below the rule is a tool or a reference, not a filter, and starts collapsed: Compare
            used to hold the top slot with a three-line explanation above the facets, and the
            reading list opened the panel with a paragraph about Zotero. */}
        <OrgFilterPanel ds={dataset} />
        <TopicFilter ds={dataset} />
        <AuthorFilter ds={dataset} />
        <CitationFilter ds={dataset} />
        <DateRangeSlider ds={dataset} />

        <div className="sidebar-rule" role="separator" aria-hidden="true">
          <span>Tools</span>
        </div>
        <CollapsibleSection
          title="Compare two"
          badge={[compare.a, compare.b].filter(Boolean).length || null}
        >
          <CompareSetup ds={dataset} />
        </CollapsibleSection>
        <CollapsibleSection
          title="Reading list"
          badge={filters.readingLists.length || null}
        >
          <ReadingListPanel ds={dataset} />
        </CollapsibleSection>
        <CollapsibleSection title="Legend & colours">
          <Legend ds={dataset} />
        </CollapsibleSection>
      </aside>

      <PaperListPanel ds={dataset} />
      <DetailsPanel ds={dataset} />
      <AuthorPanel ds={dataset} />
      <CompareResults ds={dataset} />
    </div>
  );
}
